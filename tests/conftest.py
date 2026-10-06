"""Shared test tools: Qt without a screen, and a small fake Clara server that speaks the real protocol."""

from __future__ import annotations

import base64
import gc
import json
import os
import threading
import time
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, unquote, urlsplit

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QTWEBENGINE_CHROMIUM_FLAGS", "--disable-gpu")  # no GPU needed to typeset a formula

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
        self.extra_events: list[dict] = []  # events sent after the tokens of an answer (a `qcm`...)
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
        self.notify_after: int | None = None  # the user's setting (None: not set, the server's 120 s applies)
        self.settings_patches: list[dict] = []  # bodies of PATCH /v1/settings
        # the models an administrator offers (`ref`, `name`, `provider_label`, `weight`); none: the server's own only
        self.offered_models: list[dict] = []
        self.model_choice: str | None = None  # what the user chose for the app
        self.model_requests: list[dict] = []  # bodies of PUT /v1/models/choice
        self.models_known = True  # False: the server has no /v1/models (an older one)
        # projects: id -> {"id", "name", "description", "instructions", ...}; their files: id -> {path: text}
        self.projects: dict[int, dict] = {}
        self.project_files: dict[int, dict[str, str]] = {}
        self.project_sources: dict[int, list[dict]] = {}
        self.uploads: list[list[str]] = []  # the paths of each upload request
        self.project_requests: list[tuple[str, str]] = []  # (method, path) of each /v1/projects request
        self.tasks: dict[int, dict] = {}  # the to-do list, as the server describes each task
        self.task_requests: list[tuple[str, str, dict]] = []  # (method, path, body) of each /v1/tasks request
        # any other route: (method, path or "prefix/") -> a payload, (status, payload), or a function of (body, query, path)
        self.routes: dict[tuple[str, str], object] = {}
        self.requests: list[tuple[str, str, dict]] = []  # (method, path, body) of each request a route answered

    def add_task(self, title: str, description: str = "", due: str | None = None, reminders: list[str] | None = None,
                 sent: int = 0, status: str = "open", parent_id: int | None = None) -> dict:
        """A task as the server describes it. Without reminders Clara picks tomorrow at 09:00 (UTC)."""
        task_id = max(self.tasks, default=0) + 1
        picked = reminders or [(datetime.now(timezone.utc) + timedelta(days=1)).replace(
            hour=9, minute=0, second=0, microsecond=0).isoformat(timespec="seconds")]
        queue = sorted(picked) if status == "open" else []
        self.tasks[task_id] = {
            "id": task_id, "title": title, "description": description, "status": status, "due_at": due,
            "reminders_sent": sent, "max_reminders": 10, "next_reminder": queue[0] if queue else None,
            "reminders": queue, "timezone": "UTC", "targets": [], "created_at": now(), "updated_at": now(),
            "done_at": now() if status == "done" else None, "parent_id": parent_id,
        }
        return self.tasks[task_id]

    def described(self, task: dict) -> dict:
        """The task with what the server adds from the others: how many sub tasks it has, and its limit."""
        kids = [t for t in self.tasks.values() if t.get("parent_id") == task["id"]]
        parent = self.tasks.get(task.get("parent_id"))
        return {**task, "subtasks": {"total": len(kids), "done": sum(k["status"] == "done" for k in kids)},
                "due_limit": parent.get("due_at") if parent else None}

    def add_project(self, name: str, **files: str) -> dict:
        project_id = max(self.projects, default=0) + 1
        self.projects[project_id] = {"id": project_id, "name": name, "description": "", "instructions": "",
                                     "created_at": now(), "updated_at": now()}
        self.project_files[project_id] = dict(files)
        self.project_sources[project_id] = []
        return self.describe_project(project_id)

    def describe_project(self, project_id: int) -> dict:
        files = self.project_files[project_id]
        size = sum(len(text) for text in files.values())
        return {
            **self.projects[project_id], "files": len(files), "size": size,
            "conversations": sum(1 for c in self.conversations.values() if c.get("project") == project_id),
            "context": {"tokens": size // 4, "window": 32768, "percent": 0.1, "inline": True, "inline_percent": 40},
            "limits": {"size": 20_000_000, "files": 5000},
            "sources": self.project_sources[project_id],
            "file_list": [{"path": path, "kind": "", "size": len(text), "source": None, "added_at": now()}
                          for path, text in sorted(files.items())],
        }

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

    def listed(self, query: str = "", project: str | None = None) -> list[dict]:
        found = [
            info for info in self.conversations.values()
            if (project is None or (project == "none" and not info.get("project")) or str(info.get("project")) == project)
            and (not query or query.lower() in info["title"].lower()
                 or any(query.lower() in m["content"].lower() for m in self.messages.get(info["id"], [])))
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

    def models_payload(self) -> dict:
        state = self.state
        default = {"ref": "local:fake-model", "name": state.model, "provider_label": "Local host", "weight": 0.4}
        chosen = next((m for m in state.offered_models if m["ref"] == state.model_choice), None)
        return {
            "surface": "app", "models": state.offered_models, "default": default,
            "choices": {"app": chosen["ref"]} if chosen else {}, "current": chosen or default, "surfaces": ["app"],
        }

    def settings_payload(self) -> dict:
        own = self.state.notify_after
        return {"notify_after": own, "notify_after_default": 120, "notify_after_effective": 120 if own is None else own}

    def routed(self, method: str, body: dict | None = None) -> bool:
        """Answer from `state.routes` when the test set a route for this request."""
        parts = urlsplit(self.path)
        query = {key: values[0] for key, values in parse_qs(parts.query).items()}
        for (verb, prefix), answer in self.state.routes.items():
            if verb == method and (parts.path == prefix or (prefix.endswith("/") and parts.path.startswith(prefix))):
                self.state.requests.append((method, parts.path, body or {}))
                payload = answer(body or {}, query, parts.path) if callable(answer) else answer
                status, payload = payload if isinstance(payload, tuple) else (200, payload)
                self.reply(status, payload)
                return True
        return False

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

    def project_route(self, method: str, body: dict | None = None) -> None:
        """The /v1/projects routes, as the real server answers them (enough of it for the app)."""
        state = self.state
        parts = urlsplit(self.path)
        query = {key: values[0] for key, values in parse_qs(parts.query).items()}
        segments = parts.path.strip("/").split("/")[2:]  # after v1/projects
        state.project_requests.append((method, self.path))
        if not segments:
            if method == "GET":
                return self.reply(200, {"projects": [state.describe_project(i) for i in state.projects]})
            project = state.add_project(body["name"])
            state.projects[project["id"]].update(description=body.get("description", ""), instructions=body.get("instructions", ""))
            return self.reply(201, state.describe_project(project["id"]))
        project_id = int(segments[0])
        if project_id not in state.projects:
            return self.reply(404, {"detail": "No such project of yours"})
        rest = segments[1:]
        files = state.project_files[project_id]
        if not rest:
            if method == "PATCH":
                for key in ("name", "description", "instructions"):
                    if body.get(key) is not None:
                        state.projects[project_id][key] = body[key]
            elif method == "DELETE":
                for info in state.conversations.values():
                    if info.get("project") == project_id:
                        info["project"] = None
                state.projects.pop(project_id)
                return self.reply(200, {"ok": True, "conversations_moved": 0})
            return self.reply(200, state.describe_project(project_id))
        if rest == ["files"] and method == "POST":
            added, skipped = [], []
            state.uploads.append([item["path"] for item in body["files"]])
            for item in body["files"]:
                text = base64.b64decode(item["data"]).decode("utf-8", "replace")
                if text.strip():
                    files[item["path"]] = text
                    added.append(item["path"])
                else:
                    skipped.append({"path": item["path"], "reason": "empty"})
            return self.reply(200, {"added": added, "replaced": [], "skipped": skipped, "skipped_count": len(skipped),
                                    "project": state.describe_project(project_id)})
        if rest == ["files"] and method == "DELETE":
            files.pop(query["path"], None)
            return self.reply(200, {"removed": 1, "project": state.describe_project(project_id)})
        if rest == ["file"]:
            return self.reply(200, {"path": query["path"], "kind": "", "size": len(files[query["path"]]),
                                    "source": None, "content": files[query["path"]]})
        if rest == ["github"]:
            name = body["repo"].split("/")[-1]
            source = {"id": len(state.project_sources[project_id]) + 1, "kind": "github", "repo": body["repo"],
                      "ref": body.get("ref", ""), "folder": name, "commit": "abc1234", "synced_at": now(), "files": 1,
                      "size": 7, "skipped": 0, "problem": ""}
            state.project_sources[project_id].append(source)
            files[f"{name}/README.md"] = "# Hello"
            return self.reply(200, {"added": [f"{name}/README.md"], "replaced": [], "skipped": [], "skipped_count": 0,
                                    "project": state.describe_project(project_id)})
        if len(rest) >= 2 and rest[0] == "sources":
            sources = state.project_sources[project_id]
            if method == "DELETE":
                state.project_sources[project_id] = [s for s in sources if s["id"] != int(rest[1])]
                return self.reply(200, {"removed": 1, "project": state.describe_project(project_id)})
            return self.reply(200, {"added": ["x"], "replaced": [], "skipped": [], "skipped_count": 0,
                                    "project": state.describe_project(project_id)})
        return self.reply(404, {"detail": "no such route"})

    def task_route(self, method: str, body: dict | None = None) -> None:
        """The /v1/tasks routes, as the real server answers them (enough of it for the app)."""
        state = self.state
        parts = urlsplit(self.path)
        query = {key: values[0] for key, values in parse_qs(parts.query).items()}
        segments = parts.path.strip("/").split("/")[2:]  # after v1/tasks
        state.task_requests.append((method, self.path, body or {}))
        if not segments:
            if method == "GET":
                status = query.get("status", "open")
                found = [t for t in state.tasks.values() if status == "all" or t["status"] == status]
                return self.reply(200, {"tasks": [state.described(t) for t in found], "max_reminders": 10})
            if not body["title"].strip():
                return self.reply(422, {"detail": "A task needs a title."})
            return self.reply(201, state.add_task(
                body["title"], body["description"], body["due"], body["reminders"], parent_id=body.get("parent_id")))
        task = state.tasks.get(int(segments[0]))
        if task is None:
            return self.reply(404, {"detail": "No such task of yours."})
        if method == "DELETE":
            state.tasks.pop(task["id"])
            return self.reply(200, {"deleted": task["id"]})
        if method == "PATCH":
            for key in ("title", "description"):
                if body.get(key) is not None:
                    task[key] = body[key]
            if "due" in body:
                task["due_at"] = body["due"]
            if body.get("status") == "done":
                task.update(status="done", reminders=[], next_reminder=None, done_at=now())
            elif body.get("status") == "open" and task["status"] == "done":
                again = state.add_task(task["title"], task["description"], task["due_at"], body.get("reminders"))
                state.tasks.pop(again["id"])
                task.update(status="open", reminders=again["reminders"], next_reminder=again["next_reminder"], done_at=None)
            elif body.get("reminders") is not None:
                task.update(reminders=sorted(body["reminders"]), next_reminder=min(body["reminders"], default=None))
        return self.reply(200, task)

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
        if self.routed("GET"):
            return
        if self.path.startswith("/v1/memory/facts"):
            return self.reply(404, {"detail": "Nobody known as app:tester"})
        if self.path.split("?")[0] == "/v1/settings":
            return self.reply(200, self.settings_payload())
        if self.path.split("?")[0] == "/v1/models":
            if not self.state.models_known:
                return self.reply(404, {"detail": "Not Found"})
            return self.reply(200, self.models_payload())
        if self.path.startswith("/v1/projects"):
            return self.project_route("GET")
        if self.path.startswith("/v1/tasks"):
            return self.task_route("GET")
        if self.path.split("?")[0] == "/v1/conversations":
            query = {key: values[0] for key, values in parse_qs(urlsplit(self.path).query).items()}
            self.state.list_requests.append(query)
            return self.reply(200, {"conversations": self.state.listed(query.get("q", ""), query.get("project"))})
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
            if self.routed("DELETE"):
                return
            if self.path.startswith("/v1/projects"):
                return self.project_route("DELETE")
            if self.path.startswith("/v1/tasks"):
                return self.task_route("DELETE")
            self.state.deleted.append(self.path)
            conversation, _, _ = self.conversation_route()
            self.state.conversations.pop(conversation, None)
            self.reply(200, {"deleted_messages": len(self.state.messages.pop(conversation, []))})

    def do_PUT(self):
        length = int(self.headers.get("Content-Length") or 0)
        body = json.loads(self.rfile.read(length) or b"{}")
        if not self.authorised():
            return
        if self.routed("PUT", body):
            return
        if self.path == "/v1/models/choice":
            self.state.model_requests.append(body)
            if body["model"] is not None and not any(m["ref"] == body["model"] for m in self.state.offered_models):
                return self.reply(422, {"detail": f"{body['model']} is not one of the models you may choose."})
            self.state.model_choice = body["model"]
            return self.reply(200, self.models_payload())
        self.reply(404, {"detail": "no such route"})

    def do_PATCH(self):
        length = int(self.headers.get("Content-Length") or 0)
        body = json.loads(self.rfile.read(length) or b"{}")
        if not self.authorised():
            return
        if self.routed("PATCH", body):
            return
        if self.path.startswith("/v1/projects"):
            return self.project_route("PATCH", body)
        if self.path.startswith("/v1/tasks"):
            return self.task_route("PATCH", body)
        if self.path == "/v1/settings":
            self.state.settings_patches.append(body)
            self.state.notify_after = body["notify_after"]
            return self.reply(200, self.settings_payload())
        conversation, _, _ = self.conversation_route()
        info = self.state.conversations.get(conversation)
        if info is None:
            return self.reply(404, {"detail": "No such conversation of yours"})
        self.state.patches.append((conversation, body))
        if body.get("title") is not None:
            info["title"], info["titled_by"] = body["title"], "person" if body["title"] else ""
        if body.get("pinned") is not None:
            info["pinned"] = body["pinned"]
        if "project" in body:
            info["project"] = body["project"]
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
        if self.routed("POST", body):
            return
        if self.path.startswith("/v1/projects"):
            return self.project_route("POST", body)
        if self.path.startswith("/v1/tasks"):
            return self.task_route("POST", body)
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
            for extra in self.state.extra_events:
                self.event(extra)
            conversation = body.get("conversation") or ""
            if conversation not in self.state.conversations:
                self.state.add_conversation(conversation)
                self.state.conversations[conversation]["preview"] = body["message"][:300]
                self.state.conversations[conversation]["project"] = body.get("project")
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
