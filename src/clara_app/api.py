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
TASK_READ_TIMEOUT = 120.0  # adding or reopening a task can have Clara choose its reminders
PROJECT_READ_TIMEOUT = 300.0  # files to read (PDF, a .zip), a GitHub repository to download
_KEEP = object()  # update_conversation: leave the project as it is

INSTRUCTIONS = (
    "You are talking through Clara's desktop app, a small chat window. "
    "Markdown is displayed, but keep answers short and conversational. "
    "The user can attach files (PDF, code, Markdown, text): their content comes in the message, each inside "
    '<document name="..." type="..."> tags. Refer to them by name. '
    "To quiz the user or to collect several answers at once, call the qcm tool: the app shows it as a form "
    "(radio buttons, check boxes or a text box) and their answers come back in their next message. "
    "Write mathematical formulas in LaTeX: $...$ inline and $$...$$ on their own lines (they are typeset, in "
    "answers and in the QCM)."
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

    # -- the model --------------------------------------------------------------------------- #

    def models(self) -> dict:
        """The models this user may choose here (`models`: `ref`, `name`, `provider_label`, `weight`, the credits a
        token costs), the server's own (`default`), what they chose (`choices`, by surface) and the model in use
        (`current`). An administrator selects the models; there may be none."""
        return self._call("GET", "/v1/models", params=self._identity()).json()

    def choose_model(self, ref: str | None) -> dict:
        """Choose the model Clara answers this user with in the app (None: the server's own)."""
        return self._call("PUT", "/v1/models/choice", json={**self._identity(), "model": ref}).json()

    # -- the to-do list ---------------------------------------------------------------------- #

    def tasks(self, status: str = "open") -> dict:
        """This user's tasks (`status`: open, done or all), the same on every client of theirs: `tasks` (`id`, `title`,
        `description`, `status`, `due_at`, `reminders_sent`, `next_reminder`, `reminders`...) and `max_reminders`."""
        return self._call("GET", "/v1/tasks", params={**self._identity(), "status": status}).json()

    def task(self, task_id: int) -> dict:
        return self._call("GET", f"/v1/tasks/{int(task_id)}", params=self._identity()).json()

    def add_task(self, title: str, description: str = "", due: str | None = None, reminders: list[str] | None = None) -> dict:
        """A task for this user. `due` and `reminders` are ISO 8601 times with their offset; without any reminder
        Clara picks them (that can take a moment)."""
        body = {
            **self._identity(), "user_name": self.config.user_name or None, "title": title, "description": description,
            "due": due, "reminders": reminders or [],
        }
        return self._call("POST", "/v1/tasks", read=TASK_READ_TIMEOUT, json=body).json()

    def change_task(self, task_id: int, **fields: object) -> dict:
        """Change a task: `title`, `description`, `due` (None: no deadline), `reminders` (ISO times; [] stops them),
        `status` ("done" or "open"; reopening has Clara pick the reminders)."""
        return self._call(
            "PATCH", f"/v1/tasks/{int(task_id)}", read=TASK_READ_TIMEOUT, json={**self._identity(), **fields}
        ).json()

    def delete_task(self, task_id: int) -> dict:
        return self._call("DELETE", f"/v1/tasks/{int(task_id)}", params=self._identity()).json()

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

    # -- who you are, what Clara remembers, the files she writes ---------------------------------- #

    def me(self) -> dict:
        """The signed-in user: `name`, `is_admin`, `person`, `accounts`, `usage`... (a user's token only)."""
        return self._call("GET", "/v1/auth/me").json()

    def facts(self) -> list[dict]:
        """What Clara remembers about this person: `id`, `text`."""
        return self._call("GET", "/v1/memory/facts", accept=(404,), params=self._identity()).json().get("facts", [])

    def add_fact(self, text: str) -> dict:
        return self._call("POST", "/v1/memory/facts", json={**self._identity(), "text": text}).json()

    def delete_fact(self, fact_id: int) -> None:
        self._call("DELETE", f"/v1/memory/facts/{int(fact_id)}", params=self._identity())

    def markdown_files(self) -> list[dict]:
        """The files Clara wrote for this person: `id`, `name`, `size`, `updated_at`."""
        return self._call("GET", "/v1/markdown-files", params=self._identity()).json()["files"]

    def markdown_file(self, file_id: int) -> dict:
        """One of them, with its `text`."""
        return self._call("GET", f"/v1/markdown-files/{int(file_id)}", params=self._identity()).json()

    def delete_markdown_file(self, file_id: int) -> None:
        self._call("DELETE", f"/v1/markdown-files/{int(file_id)}", params=self._identity())

    # -- the account ------------------------------------------------------------------------------- #

    def sessions(self) -> list[dict]:
        """Where this user is signed in: `id`, `surface`, `device`, `address`, `created_at`, `last_used_at`, `current`."""
        return self._call("GET", "/v1/auth/sessions").json()["sessions"]

    def delete_session(self, session_id: str) -> None:
        self._call("DELETE", f"/v1/auth/sessions/{quote(str(session_id), safe='')}")

    def change_password(self, current: str, new: str) -> dict:
        return self._call("POST", "/v1/auth/password", json={"current_password": current, "new_password": new}).json()

    def link_code(self) -> dict:
        """A code, valid for 10 minutes, that proves control of this account to another client."""
        return self._call("POST", "/v1/accounts/link-code", json=self._identity()).json()

    # -- administration (an administrator's token) --------------------------------------------------- #

    def admin_users(self) -> list[dict]:
        return self._call("GET", "/v1/admin/users").json()["users"]

    def admin_add_user(self, name: str, password: str | None, admin: bool, discord_id: str | None) -> dict:
        body = {"name": name, "password": password or None, "admin": admin, "discord_id": discord_id}
        return self._call("POST", "/v1/admin/users", json=body).json()

    def admin_change_user(self, name: str, **change: object) -> dict:
        """`password`, `generate_password`, `admin`, `disabled`, `token_limit`, `follow_default_limit`."""
        return self._call("PATCH", f"/v1/admin/users/{quote(name, safe='')}", json=change).json()

    def admin_sign_out_user(self, name: str) -> dict:
        return self._call("POST", f"/v1/admin/users/{quote(name, safe='')}/sign-out", json={}).json()

    def admin_remove_user(self, name: str) -> None:
        self._call("DELETE", f"/v1/admin/users/{quote(name, safe='')}")

    def admin_limits(self) -> dict:
        return self._call("GET", "/v1/admin/limits").json()

    def admin_set_default_limit(self, tokens: int) -> dict:
        return self._call("PUT", "/v1/admin/limits/default", json={"tokens": tokens}).json()

    def admin_catalog(self, refresh: bool = False) -> dict:
        return self._call("GET", "/v1/admin/catalog", read=60.0, params={"refresh": "true"} if refresh else {}).json()

    def admin_change_catalog(self, refs: list[str], **change: object) -> dict:
        return self._call("PATCH", "/v1/admin/catalog", json={"refs": refs, **change}).json()

    def admin_set_discord_model(self, model: str | None) -> dict:
        return self._call("PUT", "/v1/admin/catalog/discord", json={"model": model}).json()

    def admin_status(self) -> dict:
        return self._call("GET", "/v1/admin/status").json()

    def admin_models(self) -> dict:
        return self._call("GET", "/v1/admin/models", read=60.0).json()

    def admin_people(self) -> list[dict]:
        return self._call("GET", "/v1/admin/people").json()["people"]

    def admin_person_facts(self, person: int) -> dict:
        return self._call("GET", f"/v1/admin/people/{int(person)}/facts").json()

    def admin_add_person_fact(self, person: int, text: str) -> dict:
        return self._call("POST", f"/v1/admin/people/{int(person)}/facts", json={"text": text}).json()

    def admin_delete_person_fact(self, person: int, fact: int) -> None:
        self._call("DELETE", f"/v1/admin/people/{int(person)}/facts/{int(fact)}")

    def admin_set_relation(self, person: int, relation: int | None) -> dict:
        return self._call("PATCH", f"/v1/admin/people/{int(person)}", json={"relation": relation}).json()

    def admin_footprint(self, person: int) -> dict:
        return self._call("GET", f"/v1/admin/people/{int(person)}/footprint").json()

    def admin_command(self, line: str) -> dict:
        """Run a server console command: `output`, `quit`."""
        return self._call("POST", "/v1/admin/command", read=120.0, json={"line": line}).json()

    def admin_commands(self) -> list[dict]:
        return self._call("GET", "/v1/admin/commands").json()

    # -- Discord (an administrator's token) -------------------------------------------------------------- #

    def discord(self) -> dict:
        """`bot` (state, user, guilds...), `default_chime`, `spaces` (the servers) and `accounts` (signed in)."""
        return self._call("GET", "/v1/admin/discord").json()

    def discord_act(self, action: str) -> dict:
        """`start`, `stop` or `restart` the bot built into the server."""
        return self._call("POST", f"/v1/admin/discord/{quote(action, safe='')}", read=60.0, json={}).json()

    def discord_default_chime(self, chime: bool) -> dict:
        return self._call("PATCH", "/v1/admin/spaces", json={"default_chime": chime}).json()

    def discord_space_chime(self, space: str, chime: bool | None) -> dict:
        return self._call("PATCH", f"/v1/admin/spaces/{quote(space, safe='')}", json={"chime": chime}).json()

    def discord_sign_out(self, discord_user: str) -> None:
        self._call("DELETE", f"/v1/admin/discord/accounts/{quote(str(discord_user), safe='')}")

    def discord_sign_in(self, discord_user: str, user: str) -> dict:
        return self._call("POST", "/v1/admin/discord/accounts", json={"user_id": discord_user, "user": user}).json()

    def discord_members(self, query: str) -> dict:
        return self._call("GET", "/v1/admin/discord/members", params={"q": query}).json()

    # -- integrations: connected accounts, repositories, folders, requests for permission ------------------ #

    def integrations(self) -> dict:
        """`types` (what is on), `accounts`, `resources` (with `effective` permissions), `roots`, `pending`, `settings`."""
        return self._call("GET", "/v1/integrations", params=self._identity()).json()

    def set_approval_notify_after(self, seconds: int | None) -> dict:
        """After how long an unanswered request is pushed to the other devices (0: never; None: the server's default)."""
        return self._call("PUT", "/v1/integrations/settings", json={**self._identity(), "approval_notify_after": seconds}).json()

    def connect_github(self, token: str) -> dict:
        return self._call("POST", "/v1/integrations/github", json={**self._identity(), "token": token}).json()

    def google_start(self) -> dict:
        """`url`: where to send the person to connect their Google Drive (the browser comes back to the server)."""
        return self._call("POST", "/v1/integrations/google/start", json=self._identity()).json()

    def disconnect_account(self, account: int) -> dict:
        return self._call("DELETE", f"/v1/integrations/accounts/{int(account)}", params=self._identity()).json()

    def set_account_levels(self, account: int, levels: dict) -> dict:
        return self._call("PATCH", f"/v1/integrations/accounts/{int(account)}", json={**self._identity(), "levels": levels}).json()

    def browse_github(self, account: int | None, query: str = "") -> dict:
        params = {**self._identity(), "q": query, **({"account": int(account)} if account else {})}
        return self._call("GET", "/v1/integrations/browse/github", read=30.0, params=params).json()

    def browse_drive(self, account: int | None, folder: str = "root", query: str = "") -> dict:
        params = {**self._identity(), "folder": folder, "q": query, **({"account": int(account)} if account else {})}
        return self._call("GET", "/v1/integrations/browse/drive", read=30.0, params=params).json()

    def browse_server(self, path: str = "") -> dict:
        return self._call("GET", "/v1/integrations/browse/server", params={**self._identity(), "path": path}).json()

    def add_resource(self, kind: str, **fields: object) -> dict:
        """Add a repository (`repo`, `ref`, `account`), a Drive folder or file (`file_id`, `account`), a folder of the
        server (`path`) or of this computer (`device`, `alias`, `label`)."""
        return self._call("POST", "/v1/integrations/resources", read=30.0, json={**self._identity(), "kind": kind, **fields}).json()

    def update_resource(self, resource: int, **fields: object) -> dict:
        return self._call("PATCH", f"/v1/integrations/resources/{int(resource)}", json={**self._identity(), **fields}).json()

    def delete_resource(self, resource: int) -> dict:
        return self._call("DELETE", f"/v1/integrations/resources/{int(resource)}", params=self._identity()).json()

    def attachments(self, project: int | None = None, conversation: str | None = None) -> dict:
        """What is attached to a project, or to a conversation: `attachments`, and the `inherited` ones."""
        params = {**self._identity(), **({"project": int(project)} if project else {"conversation": conversation})}
        return self._call("GET", "/v1/integrations/attachments", params=params).json()

    def attach(self, resource: int, project: int | None = None, conversation: str | None = None, levels: dict | None = None) -> dict:
        body = {**self._identity(), "resource": int(resource), **({"project": int(project)} if project else {"conversation": conversation})}
        if levels is not None:
            body["levels"] = levels
        return self._call("PUT", "/v1/integrations/attachments", json=body).json()

    def detach(self, attachment: int) -> dict:
        return self._call("DELETE", f"/v1/integrations/attachments/{int(attachment)}", params=self._identity()).json()

    def approvals(self, conversation: str | None = None) -> list[dict]:
        """The requests for permission that wait for an answer, the oldest first."""
        params = {**self._identity(), "status": "pending", **({"conversation": conversation} if conversation else {})}
        return list(reversed(self._call("GET", "/v1/approvals", params=params).json()["approvals"]))

    def decide_approval(self, approval: int, approve: bool, remember: str = "") -> dict:
        """Approve (the action runs now) or deny a request."""
        body = {**self._identity(), "approve": approve, "remember": remember}
        return self._call("POST", f"/v1/approvals/{int(approval)}/decide", read=150.0, json=body).json()

    def computer_jobs(self, device: str) -> list[dict]:
        """What Clara asked of this computer's folders (each given out once): `id`, `op`, `alias`, `args`."""
        params = {**self._identity(), "device": device}
        return self._call("GET", "/v1/integrations/jobs", params=params).json()["jobs"]

    def finish_job(self, job: int, ok: bool, text: str) -> None:
        self._call("POST", f"/v1/integrations/jobs/{int(job)}/result", read=60.0, json={**self._identity(), "ok": ok, "text": text})

    # -- administration of the integrations --------------------------------------------------------------- #

    def admin_integrations(self) -> dict:
        return self._call("GET", "/v1/admin/integrations").json()

    def admin_set_integrations(self, **change: object) -> dict:
        return self._call("PUT", "/v1/admin/integrations", json=change).json()

    def admin_integrations_log(self, before: int | None = None) -> list[dict]:
        params = {"limit": 50, **({"before": before} if before else {})}
        return self._call("GET", "/v1/admin/integrations/log", params=params).json()["entries"]
