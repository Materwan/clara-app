"""The Clara server, seen from the app: a small blocking HTTP client (Qt-free).

It is meant to be called from worker threads (see workers.py), never from the UI thread.
"""

from __future__ import annotations

import base64
import functools
import json
import socket
import ssl
import uuid
from typing import Iterator
from urllib.parse import quote

import httpx

from . import SURFACE
from .config import Config

CHAT_READ_TIMEOUT = 300.0  # a model can think for a long while before its first token
REMINDER_READ_TIMEOUT = 60.0  # the server sends a keepalive every 15 s
PROJECT_READ_TIMEOUT = 300.0  # files to read (PDF, a .zip), a GitHub repository to download
_KEEP = object()  # update_conversation: leave the project as it is

INSTRUCTIONS = (
    "You are talking through Clara's desktop app, a small chat window. "
    "Markdown is displayed, but keep answers short and conversational. "
    "The user can attach files (PDF, code, Markdown, text): their content comes in the message, each inside "
    '<document name="..." type="..."> tags. Refer to them by name. '
    "To quiz the user or to collect several answers at once, call the qcm tool: the app shows it as a form "
    "(radio buttons, check boxes or a text box) and their answers come back in their next message."
)


def new_conversation(user_id: str) -> str:
    """The id of a new conversation of this user: the server lists it from its first message on."""
    return f"{SURFACE}:{user_id}:{uuid.uuid4().hex[:12]}"


def _path(conversation: str) -> str:
    return "/v1/conversations/" + quote(conversation, safe=":")


def _project(project_id: int) -> str:
    return f"/v1/projects/{int(project_id)}"


class ApiError(Exception):
    """The server could not be reached, or refused the request. The message is fit to show."""


@functools.cache
def _tls() -> ssl.SSLContext:
    """Built once: creating it costs about half a second, and every request makes a new client."""
    return httpx.create_ssl_context()


def _detail(response: httpx.Response) -> str:
    try:
        body = response.json()
        return str(body.get("detail", body)) if isinstance(body, dict) else str(body)
    except ValueError:
        return response.text.strip()[:300] or response.reason_phrase


SIGNED_OUT = "You were signed out of the Clara server. Open Settings and enter your password again."


def _refused(response: httpx.Response, user_token: bool) -> str:
    """What to tell the user about an error answer."""
    if response.status_code == 401 and user_token:
        return SIGNED_OUT
    return f"Clara server: {_detail(response)} (HTTP {response.status_code})"


def login(url: str, user: str, password: str, timeout: float = 15.0) -> dict:
    """Sign in with a password: `{"token", "user": {"name", ...}}`. The server gives a token bound to this user
    on the surface of the app; the password is not kept."""
    base = url.strip().rstrip("/")
    try:
        response = httpx.post(
            base + "/v1/auth/login",
            json={"username": user.strip(), "password": password, "surface": SURFACE, "device": socket.gethostname()},
            timeout=timeout, verify=_tls(),
        )
    except httpx.ConnectError:
        raise ApiError(f"Cannot reach the Clara server at {base}.") from None
    except httpx.HTTPError as error:
        raise ApiError(f"The Clara server did not answer: {error}") from None
    if response.is_error:
        raise ApiError(_detail(response))
    return response.json()


class EventStream:
    """The Server-Sent Events of a response. Iterating yields the decoded events; `close()` (from any
    thread) ends the connection, which ends the iteration."""

    _closed = False

    def __init__(self, client: httpx.Client, response: httpx.Response):
        self._client, self._response = client, response

    def __iter__(self) -> Iterator[dict]:
        try:
            for line in self._response.iter_lines():
                if line.startswith("data: "):
                    yield json.loads(line[6:])
        except (httpx.HTTPError, OSError) as error:
            if not self._closed:
                raise ApiError(f"The connection to the Clara server broke: {error}") from None
        finally:
            self.close()

    def close(self) -> None:
        self._closed = True
        for closable in (self._response, self._client):
            try:
                closable.close()
            except Exception:
                pass


class ClaraApi:
    def __init__(self, config: Config):
        self.config = config
        self.base = config.url.strip().rstrip("/")
        self._headers = {"Authorization": f"Bearer {config.token.strip()}"}
        self._user_token = config.token.strip().startswith("clu_")  # from a password login (else a shared client token)

    # -- plumbing ------------------------------------------------------------------ #

    def _identity(self) -> dict:
        return {"surface": SURFACE, "user_id": self.config.user_id}

    def _client(self, read: float) -> httpx.Client:
        return httpx.Client(
            base_url=self.base, headers=self._headers, timeout=httpx.Timeout(10.0, read=read), verify=_tls()
        )

    def _open(self, method: str, path: str, read: float, **options) -> EventStream:
        client = self._client(read)
        try:
            response = client.send(client.build_request(method, path, **options), stream=True)
            if response.is_error:
                response.read()
                raise ApiError(_refused(response, self._user_token))
        except httpx.ConnectError:
            client.close()
            raise ApiError(f"Cannot reach the Clara server at {self.base}.") from None
        except httpx.HTTPError as error:
            client.close()
            raise ApiError(f"The Clara server did not answer: {error}") from None
        except ApiError:
            client.close()
            raise
        return EventStream(client, response)

    def _call(
        self, method: str, path: str, accept: tuple[int, ...] = (), read: float = 15.0, **options
    ) -> httpx.Response:
        try:
            with self._client(read) as client:
                response = client.request(method, path, **options)
        except httpx.ConnectError:
            raise ApiError(f"Cannot reach the Clara server at {self.base}.") from None
        except httpx.HTTPError as error:
            raise ApiError(f"The Clara server did not answer: {error}") from None
        if response.is_error and response.status_code not in accept:
            raise ApiError(_refused(response, self._user_token))
        return response

    # -- the server ---------------------------------------------------------------- #

    def health(self) -> dict:
        """`{"status", "provider", "model"}` (no token needed), then checks that the token is accepted."""
        info = self._call("GET", "/health").json()
        # 401 if the token is wrong; 404 only means the server does not know this person yet
        self._call("GET", "/v1/memory/facts", accept=(404,), params=self._identity())
        return info

    def chat(self, message: str, conversation: str, project: int | None = None) -> EventStream:
        """Events of one turn of `conversation`: `token`, `usage`, `done`, `error`... A new conversation goes in
        `project` (one that exists stays in its own)."""
        body = {
            **self._identity(),
            "user_name": self.config.user_name or None,
            "message": message,
            "conversation": conversation,
            "instructions": INSTRUCTIONS,
        }
        if project:
            body["project"] = project
        return self._open("POST", "/v1/chat/stream", CHAT_READ_TIMEOUT, json=body)

    # -- the conversations ------------------------------------------------------------ #

    def conversations(self, query: str = "", project: int | str | None = None) -> list[dict]:
        """This user's conversations in the app, pinned first, then the last written in: `id`, `title`,
        `pinned`, `updated_at`, `preview`, `project`... `query` keeps those whose title or messages contain it;
        `project` those of a project ("none": those in no project; None: all of them)."""
        params = {**self._identity(), **({"q": query.strip()} if query.strip() else {})}
        if project is not None:
            params["project"] = str(project)
        return self._call("GET", "/v1/conversations", params=params).json()["conversations"]

    def messages(self, conversation: str) -> dict:
        """A conversation to show again: its last `messages` (`role`, `content`), the `summary` of older ones
        that are no longer kept, and whether some are left out (`earlier`)."""
        return self._call("GET", _path(conversation) + "/messages", params=self._identity()).json()

    def title(self, conversation: str) -> str:
        """Clara's title for the conversation, written now if it has none yet."""
        return self._call("POST", _path(conversation) + "/title", json=self._identity()).json()["title"]

    def update_conversation(
        self, conversation: str, title: str | None = None, pinned: bool | None = None, project: object = _KEEP
    ) -> dict:
        """Rename (an empty title: none) and/or pin a conversation, and/or move it to a project (None: out of
        its project)."""
        body = {**self._identity(), "title": title, "pinned": pinned}
        if project is not _KEEP:
            body["project"] = project
        return self._call("PATCH", _path(conversation), json=body).json()

    def delete_conversation(self, conversation: str) -> None:
        """Erase a conversation from the server (the facts Clara knows are kept)."""
        self._call("DELETE", _path(conversation), params=self._identity())

    def reminders(self) -> EventStream:
        """This user's reminders as they come due and notifications as they are sent, the ones missed since
        this client last connected first, and what the server is doing."""
        return self._open("GET", "/v1/notifications/stream", REMINDER_READ_TIMEOUT, params=self._identity())

    def settings(self) -> dict:
        """This user's settings on the server: `notify_after` (seconds a task takes before it notifies them when
        done; 0: never; None: not set), `notify_after_default` and `notify_after_effective`."""
        return self._call("GET", "/v1/settings", params=self._identity()).json()

    def set_notify_after(self, seconds: int | None) -> dict:
        """How long a task takes before this user is notified when it is done (0: never; None: the server's
        default). Returns the settings."""
        body = {**self._identity(), "user_name": self.config.user_name or None, "notify_after": seconds}
        return self._call("PATCH", "/v1/settings", json=body).json()

    # -- projects --------------------------------------------------------------------------- #

    def projects(self) -> list[dict]:
        """This user's projects (the same on every client of theirs): `id`, `name`, `description`, `files`,
        `size`, `conversations`, `context`..."""
        return self._call("GET", "/v1/projects", params=self._identity()).json()["projects"]

    def project(self, project_id: int) -> dict:
        """A project, with its `sources` (GitHub repositories) and its `file_list` (`path`, `size`, `source`)."""
        return self._call("GET", _project(project_id), params=self._identity()).json()

    def create_project(self, name: str, description: str = "", instructions: str = "") -> dict:
        body = {
            **self._identity(), "user_name": self.config.user_name or None,
            "name": name, "description": description, "instructions": instructions,
        }
        return self._call("POST", "/v1/projects", json=body).json()

    def update_project(
        self, project_id: int, name: str | None = None, description: str | None = None, instructions: str | None = None
    ) -> dict:
        body = {**self._identity(), "name": name, "description": description, "instructions": instructions}
        return self._call("PATCH", _project(project_id), json=body).json()

    def delete_project(self, project_id: int) -> dict:
        """Delete a project and its files; its conversations stay, in no project."""
        return self._call("DELETE", _project(project_id), params=self._identity()).json()

    def upload_files(self, project_id: int, files: list[tuple[str, bytes]]) -> dict:
        """Add files to a project, `(path in the project, bytes)`; the server reads them (PDF, Word, .zip...).
        `added`, `replaced`, `skipped` (`path`, `reason`), `skipped_count` and the `project`."""
        body = {
            **self._identity(),
            "files": [{"path": path, "data": base64.b64encode(data).decode("ascii")} for path, data in files],
        }
        return self._call("POST", _project(project_id) + "/files", read=PROJECT_READ_TIMEOUT, json=body).json()

    def file(self, project_id: int, path: str) -> dict:
        params = {**self._identity(), "path": path}
        return self._call("GET", _project(project_id) + "/file", params=params).json()

    def remove_file(self, project_id: int, path: str, folder: bool = False) -> dict:
        params = {**self._identity(), "path": path, **({"folder": "true"} if folder else {})}
        return self._call("DELETE", _project(project_id) + "/files", params=params).json()

    def add_repository(self, project_id: int, repo: str, ref: str = "") -> dict:
        """Download a GitHub repository's text files into the project (the server does it)."""
        body = {**self._identity(), "repo": repo, "ref": ref}
        return self._call("POST", _project(project_id) + "/github", read=PROJECT_READ_TIMEOUT, json=body).json()

    def sync_repository(self, project_id: int, source_id: int) -> dict:
        path = f"{_project(project_id)}/sources/{int(source_id)}/sync"
        return self._call("POST", path, read=PROJECT_READ_TIMEOUT, json=self._identity()).json()

    def remove_repository(self, project_id: int, source_id: int) -> dict:
        path = f"{_project(project_id)}/sources/{int(source_id)}"
        return self._call("DELETE", path, params=self._identity()).json()

    def notify(self, text: str, title: str = "", targets: list[str] | None = None) -> dict:
        """Send this user a notification, on the surfaces in `targets` (empty: on all of theirs)."""
        body = {**self._identity(), "text": text, "title": title, "targets": targets or []}
        return self._call("POST", "/v1/notifications", json=body).json()
