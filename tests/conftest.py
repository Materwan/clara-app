"""Shared test tools: Qt without a screen, and a small fake Clara server that speaks the real protocol."""

from __future__ import annotations

import gc
import json
import os
import threading
import time
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, unquote, urlsplit

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from clara_app.config import Config  # noqa: E402

TOKEN = "app-token"
USER_TOKEN = "clu_fake-token"  # what a password login gives
PASSWORD = "right-password-1"


@pytest.fixture(scope="session")
def qt():
    app = QApplication.instance() or QApplication([])
    app.setQuitOnLastWindowClosed(False)
    return app


@pytest.fixture(autouse=True)
def collect_garbage():
    """Free the windows of a test here, on the Qt thread. Left to the garbage collector, a window in a reference
    cycle could be freed at any moment, maybe on a worker thread, which Qt does not allow (it aborts)."""
    yield
    gc.collect()


def wait_until(condition, timeout: float = 5.0) -> None:
    """Let Qt deliver signals until `condition()` holds."""
    deadline = time.monotonic() + timeout
    while not condition():
        if time.monotonic() > deadline:
            raise AssertionError("condition not reached in time")
        QApplication.processEvents()
        time.sleep(0.005)


class State:
    def __init__(self):
        self.chat_bodies: list[dict] = []
        self.deleted: list[str] = []
        self.reply = ["Hello ", "**world**"]  # tokens of the next answer
        self.chat_error: str | None = None  # an `error` event instead of an answer
        self.hold = threading.Event()  # set: the answer stops after its first token and waits
        self.reminders: list[dict] = []  # sent to each connection of the reminder stream, then it ends
        self.reminder_connections = 0
        self.hold_reminders = True  # keep each reminder stream open, as the real server does
        # what each connection of the reminder stream hears about the server (default: it is running);
        # a "stopped" ends that connection, as it does on the real server
        self.server_scripts: list[list[str]] = []
        self.model = "fake-model"
        self.stream_paths: list[str] = []  # the event stream as asked for, with its query
        self.notifications: list[dict] = []  # bodies of POST /v1/notifications
        # the conversations, as the server lists them (`id`, `title`, `pinned`, `updated_at`, `preview`)
        self.conversations: dict[str, dict] = {}
        self.messages: dict[str, list[dict]] = {}  # of each conversation: `role`, `content`
        self.summaries: dict[str, str] = {}
        self.title = "Clara's title"  # what Clara titles a conversation (None: the model fails)
        self.title_requests: list[str] = []
        self.patches: list[tuple[str, dict]] = []
        self.list_requests: list[dict] = []  # the query of each GET /v1/conversations
        self.hold_messages = threading.Event()  # set: GET .../messages waits until it is cleared
        self.signed_out = False  # the token a password login gave is no longer accepted
        self.logins: list[dict] = []  # bodies of POST /v1/auth/login

    def add_conversation(self, conversation: str, *exchanges: tuple[str, str], title: str = "",
                         updated_at: str = "", pinned: bool = False) -> None:
        self.conversations[conversation] = {
            "id": conversation, "title": title, "titled_by": "clara" if title else "", "pinned": pinned,
            "created_at": updated_at or now(), "updated_at": updated_at or now(),
            "preview": exchanges[0][0][:300] if exchanges else "",
        }
        self.messages[conversation] = [
            {"role": role, "content": content} for question, answer in exchanges
            for role, content in (("user", question), ("assistant", answer))
        ]

    def listed(self, query: str = "") -> list[dict]:
        found = [
            info for info in self.conversations.values()
            if not query or query.lower() in info["title"].lower()
            or any(query.lower() in m["content"].lower() for m in self.messages.get(info["id"], []))
        ]
        found.sort(key=lambda info: info["updated_at"], reverse=True)
        found.sort(key=lambda info: not info["pinned"])
        return found


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


class Handler(BaseHTTPRequestHandler):
    state: State

    def log_message(self, *args):
        pass

    def reply(self, status, payload):
        data = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def authorised(self) -> bool:
        accepted = [TOKEN] + ([] if self.state.signed_out else [USER_TOKEN])
        if self.headers.get("Authorization") in [f"Bearer {token}" for token in accepted]:
            return True
        self.reply(401, {"detail": "Missing or invalid token"})
        return False

    def event(self, payload):
        self.wfile.write(f"event: {payload['type']}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n".encode())
        self.wfile.flush()

    def stream_headers(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.end_headers()

    def conversation_route(self) -> tuple[str, str, dict]:
        """`(conversation, what follows it, query)` of a /v1/conversations/... path."""
        parts = urlsplit(self.path)
        rest = unquote(parts.path[len("/v1/conversations/"):])
        for suffix in ("/messages", "/title"):
            if rest.endswith(suffix):
                return rest[: -len(suffix)], suffix, parse_qs(parts.query)
        return rest, "", parse_qs(parts.query)

    def do_GET(self):
        if self.path == "/health":
            return self.reply(200, {"status": "ok", "provider": "local", "model": self.state.model})
        if not self.authorised():
            return
        if self.path.startswith("/v1/memory/facts"):
            return self.reply(404, {"detail": "Nobody known as app:tester"})
        if self.path.split("?")[0] == "/v1/conversations":
            query = {key: values[0] for key, values in parse_qs(urlsplit(self.path).query).items()}
            self.state.list_requests.append(query)
            return self.reply(200, {"conversations": self.state.listed(query.get("q", ""))})
        if self.path.startswith("/v1/conversations/"):
            conversation, suffix, _ = self.conversation_route()
            while self.state.hold_messages.is_set():
                time.sleep(0.01)
            if suffix != "/messages" or conversation not in self.state.conversations:
                return self.reply(404, {"detail": "No such conversation of yours"})
            return self.reply(200, {
                **self.state.conversations[conversation],
                "summary": self.state.summaries.get(conversation, ""),
                "earlier": False,
                "messages": self.state.messages[conversation],
            })
        if self.path.split("?")[0] in ("/v1/reminders/stream", "/v1/notifications/stream"):
            self.state.reminder_connections += 1
            self.state.stream_paths.append(self.path)
            self.stream_headers()
            self.wfile.write(b": keepalive\n\n")
            for reminder in self.state.reminders:
                self.event(reminder)
            states = self.state.server_scripts.pop(0) if self.state.server_scripts else ["running"]
            for name in states:
                self.event({"type": "server", "state": name})
            if "stopped" in states:
                return
            try:
                while self.state.hold_reminders:  # until the client goes away
                    time.sleep(0.02)
                    self.wfile.write(b": keepalive\n\n")
                    self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError, OSError):
                pass
            return
        self.reply(404, {"detail": "no such route"})

    def do_DELETE(self):
        if self.authorised():
            self.state.deleted.append(self.path)
            conversation, _, _ = self.conversation_route()
            self.state.conversations.pop(conversation, None)
            self.reply(200, {"deleted_messages": len(self.state.messages.pop(conversation, []))})

    def do_PATCH(self):
        length = int(self.headers.get("Content-Length") or 0)
        body = json.loads(self.rfile.read(length) or b"{}")
        if not self.authorised():
            return
        conversation, _, _ = self.conversation_route()
        info = self.state.conversations.get(conversation)
        if info is None:
            return self.reply(404, {"detail": "No such conversation of yours"})
        self.state.patches.append((conversation, body))
        if body.get("title") is not None:
            info["title"], info["titled_by"] = body["title"], "person" if body["title"] else ""
        if body.get("pinned") is not None:
            info["pinned"] = body["pinned"]
        self.reply(200, info)

    def do_POST(self):
        length = int(self.headers.get("Content-Length") or 0)
        body = json.loads(self.rfile.read(length) or b"{}")
        if self.path == "/v1/auth/login":
            self.state.logins.append(body)
            if body.get("password") != PASSWORD:
                return self.reply(401, {"detail": "Wrong user name or password"})
            return self.reply(200, {"token": USER_TOKEN, "user": {"name": body["username"].lower()}, "surface": "app"})
        if not self.authorised():
            return
        if self.path == "/v1/notifications":
            self.state.notifications.append(body)
            return self.reply(201, {"id": len(self.state.notifications), "targets": body.get("targets", [])})
        if self.path.startswith("/v1/conversations/"):
            conversation, _, _ = self.conversation_route()
            self.state.title_requests.append(conversation)
            info = self.state.conversations.get(conversation)
            if info is None:
                return self.reply(404, {"detail": "No such conversation of yours"})
            if self.state.title is None:
                return self.reply(502, {"detail": "The language model failed"})
            if not info["title"]:
                info["title"], info["titled_by"] = self.state.title, "clara"
            return self.reply(200, {"id": conversation, "title": info["title"]})
        if self.path != "/v1/chat/stream":
            return self.reply(404, {"detail": "no such route"})
        self.state.chat_bodies.append(body)
        self.stream_headers()
        self.event({"type": "turn", "id": "t1"})
        try:
            if self.state.chat_error:
                return self.event({"type": "error", "message": self.state.chat_error})
            for index, piece in enumerate(self.state.reply):
                self.event({"type": "token", "text": piece})
                if index == 0 and self.state.hold.is_set():
                    for _ in range(500):  # the client may go away meanwhile
                        time.sleep(0.01)
                        self.wfile.write(b": keepalive\n\n")
                        self.wfile.flush()
            conversation = body.get("conversation") or ""
            if conversation not in self.state.conversations:
                self.state.add_conversation(conversation)
                self.state.conversations[conversation]["preview"] = body["message"][:300]
            self.state.conversations[conversation]["updated_at"] = now()
            self.state.messages[conversation] += [
                {"role": "user", "content": body["message"]},
                {"role": "assistant", "content": "".join(self.state.reply)},
            ]
            self.event({"type": "done", "reply": "".join(self.state.reply), "usage": {}})
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass


@pytest.fixture
def server():
    state = State()
    handler = type("BoundHandler", (Handler,), {"state": state})
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    httpd.daemon_threads = True
    threading.Thread(target=httpd.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True).start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}", state
    httpd.shutdown()
    httpd.server_close()


@pytest.fixture
def config(server) -> Config:
    url, _ = server
    return Config(url=url, token=TOKEN, user_id="tester", user_name="Tess")
