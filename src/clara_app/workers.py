"""Background threads: one for each reply being written, one listening for reminders.

The UI thread never waits for the network. Workers talk back through Qt signals, which Qt delivers
on the UI thread.
"""

from __future__ import annotations

import threading
from typing import Callable

from PySide6.QtCore import QThread, Signal

from .api import ApiError, ClaraApi, EventStream

RECONNECT_SECONDS = 5.0


class ChatWorker(QThread):
    """Runs one turn: `token` for each piece of the answer, then `answered`, or `failed` with a reason."""

    token = Signal(str)
    answered = Signal()
    failed = Signal(str)

    def __init__(self, api: ClaraApi, message: str, parent=None):
        super().__init__(parent)
        self._api, self._message = api, message
        self._stream: EventStream | None = None
        self._cancelled = False

    def cancel(self) -> None:
        self._cancelled = True
        stream = self._stream
        if stream is not None:
            stream.close()

    def run(self) -> None:
        try:
            self._stream = self._api.chat(self._message)
            if self._cancelled:
                self._stream.close()
                return
            for event in self._stream:
                kind = event.get("type")
                if kind == "token":
                    self.token.emit(event["text"])
                elif kind == "error":
                    self.failed.emit(str(event.get("message", "the server reported an error")))
                    return
                elif kind == "done":
                    break
            if not self._cancelled:
                self.answered.emit()
        except ApiError as error:
            if not self._cancelled:
                self.failed.emit(str(error))


class ReminderWorker(QThread):
    """Holds the reminder stream open and emits `reminder` for each one; reconnects when it drops.
    `connection` says whether the server can be reached, `server` what it says it is doing
    ("running", "stopping" or "stopped")."""

    reminder = Signal(dict)
    connection = Signal(bool)
    server = Signal(str)

    def __init__(self, api_factory: Callable[[], ClaraApi], pause: float | None = None, parent=None):
        super().__init__(parent)
        self._api_factory = api_factory
        self._pause = RECONNECT_SECONDS if pause is None else pause
        self._stop = threading.Event()
        self._stream: EventStream | None = None

    def stop(self) -> None:
        self._stop.set()
        stream = self._stream
        if stream is not None:
            stream.close()

    def run(self) -> None:
        while not self._stop.is_set():
            try:
                self._stream = self._api_factory().reminders()
                if self._stop.is_set():  # stop() came while connecting, before it could close the stream
                    self._stream.close()
                    return
                self.connection.emit(True)
                for event in self._stream:
                    kind = event.get("type")
                    if kind == "reminder":
                        self.reminder.emit(event)
                    elif kind == "server":
                        self.server.emit(str(event.get("state", "")))
            except ApiError:
                pass  # server down, token refused...: try again; what was missed arrives then
            if not self._stop.is_set():
                self.connection.emit(False)
            self._stream = None
            self._stop.wait(self._pause)


class ProbeWorker(QThread):
    """Checks a configuration against the server: `result(ok, message)`."""

    result = Signal(bool, str)

    def __init__(self, api: ClaraApi, parent=None):
        super().__init__(parent)
        self._api = api

    def run(self) -> None:
        try:
            info = self._api.health()
        except ApiError as error:
            self.result.emit(False, str(error))
            return
        self.result.emit(True, f"Connected: {info.get('model', '?')} ({info.get('provider', '?')})")


class TaskWorker(QThread):
    """Runs a blocking call off the UI thread: `done(error)`, with an empty error on success."""

    done = Signal(str)

    def __init__(self, task: Callable[[], object], parent=None):
        super().__init__(parent)
        self._task = task

    def run(self) -> None:
        try:
            self._task()
        except ApiError as error:
            self.done.emit(str(error))
            return
        self.done.emit("")
