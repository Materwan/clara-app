"""The Clara server, seen from the app: a small blocking HTTP client (Qt-free).

It is meant to be called from worker threads (see workers.py), never from the UI thread.
"""

from __future__ import annotations

import functools
import json
import ssl
from typing import Iterator

import httpx

from . import SURFACE
from .config import Config

CHAT_READ_TIMEOUT = 300.0  # a model can think for a long while before its first token
REMINDER_READ_TIMEOUT = 60.0  # the server sends a keepalive every 15 s

INSTRUCTIONS = (
    "You are talking through Clara's desktop app, a small chat window. "
    "Markdown is displayed, but keep answers short and conversational."
)


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
                raise ApiError(f"Clara server: {_detail(response)} (HTTP {response.status_code})")
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

    def _call(self, method: str, path: str, accept: tuple[int, ...] = (), **options) -> httpx.Response:
        try:
            with self._client(15.0) as client:
                response = client.request(method, path, **options)
        except httpx.ConnectError:
            raise ApiError(f"Cannot reach the Clara server at {self.base}.") from None
        except httpx.HTTPError as error:
            raise ApiError(f"The Clara server did not answer: {error}") from None
        if response.is_error and response.status_code not in accept:
            raise ApiError(f"Clara server: {_detail(response)} (HTTP {response.status_code})")
        return response

    # -- the server ---------------------------------------------------------------- #

    def health(self) -> dict:
        """`{"status", "provider", "model"}` (no token needed), then checks that the token is accepted."""
        info = self._call("GET", "/health").json()
        # 401 if the token is wrong; 404 only means the server does not know this person yet
        self._call("GET", "/v1/memory/facts", accept=(404,), params=self._identity())
        return info

    def chat(self, message: str) -> EventStream:
        """Events of one turn: `token`, `usage`, `done`, `error`..."""
        body = {
            **self._identity(),
            "user_name": self.config.user_name or None,
            "message": message,
            "conversation": self.config.conversation,
            "instructions": INSTRUCTIONS,
        }
        return self._open("POST", "/v1/chat/stream", CHAT_READ_TIMEOUT, json=body)

    def new_thread(self) -> None:
        """Forget this conversation (the facts Clara knows are kept)."""
        self._call("DELETE", f"/v1/conversations/{self.config.conversation}")

    def reminders(self) -> EventStream:
        """Reminders as they come due, and the ones missed since this client last connected."""
        return self._open("GET", "/v1/reminders/stream", REMINDER_READ_TIMEOUT)
