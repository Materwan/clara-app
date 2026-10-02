"""The chat window: the conversation, a box to write in, and a few buttons."""

from __future__ import annotations

import re
from typing import Callable

from PySide6.QtCore import QEvent, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QGuiApplication, QKeyEvent
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from . import APP_NAME
from .api import ClaraApi
from .chat_view import CLARA, ERROR, NOTE, USER, ChatView, MessageBubble
from .config import Config
from .icon import make_icon
from .workers import ChatWorker, TaskWorker

FLUSH_MS = 40  # streamed text is drawn at most this often
MAX_INPUT_LINES = 5
GREETING = "Hi! Ask me anything."

_MARKDOWN_SPECIAL = re.compile(r"([\\`*_{}\[\]()#+\-.!|<>~&])")


def literal(text: str) -> str:
    """`text` as it must be written in Markdown to be displayed exactly as typed."""
    escaped = _MARKDOWN_SPECIAL.sub(r"\\\1", text)
    return escaped.replace("\r\n", "\n").replace("\n", "  \n")  # two spaces: a line break, not a space


class InputBox(QPlainTextEdit):
    """Enter sends, Shift+Enter starts a new line. Grows with its text, up to a few lines."""

    submitted = Signal()

    def __init__(self):
        super().__init__()
        self.setPlaceholderText("Write to Clara…   (Enter sends, Shift+Enter for a new line)")
        self.setTabChangesFocus(True)
        self.textChanged.connect(self._fit)
        self._fit()

    def _fit(self) -> None:
        lines = max(1, min(MAX_INPUT_LINES, round(self.document().size().height())))
        metrics = self.fontMetrics()
        margin = int(self.document().documentMargin() * 2) + 2 * self.frameWidth()
        self.setFixedHeight(metrics.lineSpacing() * lines + margin + 4)

    def keyPressEvent(self, event: QKeyEvent) -> None:
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter) and not (
            event.modifiers() & Qt.KeyboardModifier.ShiftModifier
        ):
            self.submitted.emit()
            return
        super().keyPressEvent(event)


class ChatWindow(QMainWindow):
    settings_requested = Signal()

    def __init__(self, get_config: Callable[[], Config], api_factory: Callable[[Config], ClaraApi] = ClaraApi):
        super().__init__()
        self._get_config = get_config
        self._api_factory = api_factory
        self._worker: ChatWorker | None = None
        self._reset_task: TaskWorker | None = None
        self._reply: MessageBubble | None = None
        self._reply_text = ""
        self._quitting = False
        self._placed = False

        self.setWindowTitle(APP_NAME)
        self.setWindowIcon(make_icon())
        self.resize(460, 640)
        self.setMinimumSize(QSize(340, 360))

        title = QLabel(f"<b>{APP_NAME}</b>")
        self.status = QLabel("")
        self.status.setStyleSheet("color: gray;")
        self.new_chat_button = QPushButton("New chat")
        self.new_chat_button.setToolTip("Start a fresh conversation (Clara keeps what she knows about you)")
        self.new_chat_button.clicked.connect(self.new_chat)
        self.settings_button = QPushButton("Settings")
        self.settings_button.clicked.connect(self.settings_requested)
        header = QHBoxLayout()
        header.addWidget(title)
        header.addWidget(self.status, 1)
        header.addWidget(self.new_chat_button)
        header.addWidget(self.settings_button)

        self.view = ChatView()
        self.input = InputBox()
        self.input.submitted.connect(self.send)
        self.send_button = QPushButton("Send")
        self.send_button.setDefault(True)
        self.send_button.clicked.connect(self._send_or_stop)
        row = QHBoxLayout()
        row.addWidget(self.input, 1)
        row.addWidget(self.send_button, 0, Qt.AlignmentFlag.AlignBottom)

        body = QWidget()
        layout = QVBoxLayout(body)
        layout.setContentsMargins(10, 8, 10, 10)
        layout.addLayout(header)
        layout.addWidget(self.view, 1)
        layout.addLayout(row)
        self.setCentralWidget(body)

        self._flush_timer = QTimer(self)
        self._flush_timer.setSingleShot(True)
        self._flush_timer.setInterval(FLUSH_MS)
        self._flush_timer.timeout.connect(self._flush)

        self.view.add(NOTE, GREETING)
        self.set_state(None)

    # -- state ------------------------------------------------------------------------ #

    @property
    def busy(self) -> bool:
        return self._worker is not None

    def set_state(self, state: str | None) -> None:
        """What the status line says about the server: "running", "stopping", "down", or None (not known yet)."""
        text, color = {
            "running": ("● Clara is running", "#1b7f3b"),
            "stopping": ("◐ Clara is stopping", "#b26a00"),
            "down": ("○ Clara is not running", "#b3261e"),
            None: ("", "gray"),
        }[state]
        self.status.setText(text)
        self.status.setStyleSheet(f"color: {color};")

    def _set_busy(self, busy: bool) -> None:
        self.send_button.setText("Stop" if busy else "Send")

    # -- showing the window --------------------------------------------------------------- #

    def bring_to_front(self) -> None:
        if not self._placed:  # the first time: near the notification area, where the user clicked
            self._placed = True
            area = QGuiApplication.primaryScreen().availableGeometry()
            self.move(area.right() - self.width() - 16, area.bottom() - self.height() - 16)
        self.setWindowState(self.windowState() & ~Qt.WindowState.WindowMinimized)
        self.show()
        self.raise_()
        self.activateWindow()
        self.input.setFocus()

    def toggle(self) -> None:
        if self.isVisible() and not self.isMinimized() and self.isActiveWindow():
            self.hide()
        else:
            self.bring_to_front()

    def quit_for_good(self) -> None:
        """From now on closing the window really closes it."""
        self._quitting = True
        self.cancel()
        if self._reset_task is not None:
            self._reset_task.wait(5000)

    def closeEvent(self, event) -> None:
        if self._quitting:
            super().closeEvent(event)
        else:  # the app lives in the notification area
            event.ignore()
            self.hide()

    def changeEvent(self, event) -> None:
        super().changeEvent(event)
        if event.type() == QEvent.Type.ActivationChange and self.isActiveWindow():
            self.input.setFocus()

    # -- talking ------------------------------------------------------------------------------ #

    def _send_or_stop(self) -> None:
        if self.busy:
            self.cancel()
        else:
            self.send()

    def send(self, text: str | None = None) -> None:
        text = (self.input.toPlainText() if text is None else text).strip()
        if not text or self.busy or self._reset_task is not None:
            return
        config = self._get_config()
        if not config.ready:
            self.settings_requested.emit()
            return
        self.input.clear()
        self.view.add(USER, literal(text))
        self._reply = self.view.add(CLARA, "")
        self._reply_text = ""
        self._worker = ChatWorker(self._api_factory(config), text, self)
        self._worker.token.connect(self._on_token)
        self._worker.answered.connect(self._on_answered)
        self._worker.failed.connect(self._on_failed)
        self._worker.finished.connect(self._worker_finished)
        self._set_busy(True)
        self._worker.start()

    def cancel(self) -> None:
        """Stop the reply being written; what has arrived is kept."""
        worker = self._worker
        if worker is not None:
            worker.cancel()
            worker.wait(5000)
            self._worker_finished()

    def _on_token(self, text: str) -> None:
        self._reply_text += text
        if not self._flush_timer.isActive():
            self._flush_timer.start()

    def _flush(self) -> None:
        if self._reply is not None:
            self._reply.set_text(self._reply_text)

    def _on_answered(self) -> None:
        self._flush()

    def _on_failed(self, message: str) -> None:
        self._flush()
        if self._reply is not None and not self._reply_text:
            self.view.remove(self._reply)
        self._reply = None
        self.view.add(ERROR, literal(message))

    def _worker_finished(self) -> None:
        worker, self._worker = self._worker, None
        if worker is None:
            return
        self._flush_timer.stop()
        self._flush()
        if self._reply is not None and not self._reply_text:
            self.view.remove(self._reply)  # cancelled before the first word
        self._reply = None
        self._set_busy(False)
        worker.deleteLater()

    def new_chat(self) -> None:
        """Forget the conversation on the server (what Clara knows about you stays), then clear the view."""
        config = self._get_config()
        if self._reset_task is not None:
            return
        self.cancel()
        self.new_chat_button.setEnabled(False)
        api = self._api_factory(config)
        self._reset_task = TaskWorker(api.new_thread, self)
        self._reset_task.done.connect(self._on_reset)
        self._reset_task.start()

    def _on_reset(self, error: str) -> None:
        task, self._reset_task = self._reset_task, None
        self.new_chat_button.setEnabled(True)
        if task is not None:
            task.wait(2000)
            task.deleteLater()
        if error:
            self.view.add(ERROR, literal(error))
            return
        self.view.clear()
        self.view.add(NOTE, "New conversation. " + GREETING)

    # -- reminders ----------------------------------------------------------------------------- #

    def add_reminder(self, text: str, detail: str = "") -> None:
        """Keep a trace of a reminder in the conversation."""
        self.view.add(NOTE, f"⏰ **{literal(text)}**" + (f"  \n{literal(detail)}" if detail else ""))
