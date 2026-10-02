"""Shared test tools: Qt without a screen, and a small fake Clara server that speaks the real protocol."""

from __future__ import annotations

import json
import os
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from clara_app.config import Config  # noqa: E402

TOKEN = "app-token"


@pytest.fixture(scope="session")
def qt():
    app = QApplication.instance() or QApplication([])
    app.setQuitOnLastWindowClosed(False)
    return app


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
        if self.headers.get("Authorization") == f"Bearer {TOKEN}":
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

    def do_GET(self):
        if self.path == "/health":
            return self.reply(200, {"status": "ok", "provider": "local", "model": self.state.model})
        if not self.authorised():
            return
        if self.path.startswith("/v1/memory/facts"):
            return self.reply(404, {"detail": "Nobody known as app:tester"})
        if self.path == "/v1/reminders/stream":
            self.state.reminder_connections += 1
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
            self.reply(200, {"deleted_messages": 2})

    def do_POST(self):
        length = int(self.headers.get("Content-Length") or 0)
        body = json.loads(self.rfile.read(length) or b"{}")
        if not self.authorised():
            return
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
