"""The chat window: the conversation, a box to write in, documents to attach, a few buttons, and the list of
conversations at its side (hidden until ☰ is clicked)."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Callable

from PySide6.QtCore import QEvent, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QDragEnterEvent, QDropEvent, QGuiApplication, QKeyEvent
from PySide6.QtWidgets import (
    QFileDialog,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from . import APP_NAME
from .api import ClaraApi, new_conversation
from .chat_view import CLARA, ERROR, NOTE, USER, ChatView, MessageBubble
from .config import Config
from .documents import FILTER, MAX_TOTAL_CHARS, Document, compose, split_message, total_chars
from .history import SIDEBAR_WIDTH, HistoryPanel, display_title
from .icon import make_icon
from .workers import CallWorker, ChatWorker, DocumentWorker

FLUSH_MS = 40  # streamed text is drawn at most this often
MAX_INPUT_LINES = 5
GREETING = "Hi! Ask me anything."
REMINDER_DUE = "[Reminder due] "  # what the server puts in a conversation when a reminder set there comes due

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
        self._calls: list[CallWorker] = []  # every other call to the server under way
        self.conversation: str | None = None  # the one shown; None: a new chat, which its first message creates
        self._opening: str | None = None  # the conversation being fetched to be shown
        self._listing = False  # the list of conversations is being fetched...
        self._list_again = False  # ...and must be fetched again after that
        self._open_latest = False  # show the last conversation once the list arrives (when the app starts)
        self._titling: set[str] = set()  # conversations Clara is writing a title for
        self._reply: MessageBubble | None = None
        self._reply_text = ""
        self._quitting = False
        self._placed = False
        self.documents: list[Document] = []  # attached to the next message
        self._readers: list[DocumentWorker] = []  # files being read

        self.setWindowTitle(APP_NAME)
        self.setWindowIcon(make_icon())
        self.resize(460, 640)
        self.setMinimumSize(QSize(340, 360))

        self.history_button = QToolButton()
        self.history_button.setText("☰")
        self.history_button.setCheckable(True)
        self.history_button.setToolTip("Your conversations")
        self.history_button.toggled.connect(self.show_history)
        title = QLabel(f"<b>{APP_NAME}</b>")
        self.status = QLabel("")
        self.status.setStyleSheet("color: gray;")
        self.new_chat_button = QPushButton("New chat")
        self.new_chat_button.setToolTip("Start a new conversation (the others stay in the list ☰)")
        self.new_chat_button.clicked.connect(self.new_chat)
        self.settings_button = QPushButton("Settings")
        self.settings_button.clicked.connect(self.settings_requested)
        header = QHBoxLayout()
        header.addWidget(self.history_button)
        header.addWidget(title)
        header.addWidget(self.status, 1)
        header.addWidget(self.new_chat_button)
        header.addWidget(self.settings_button)

        self.view = ChatView()
        self.input = InputBox()
        self.input.submitted.connect(self.send)
        self.attach_button = QToolButton()
        self.attach_button.setText("📎")
        self.attach_button.setToolTip("Attach documents: PDF, code (Python, C…), Markdown, text. You can also drop files here.")
        self.attach_button.clicked.connect(self.choose_documents)
        self.send_button = QPushButton("Send")
        self.send_button.setDefault(True)
        self.send_button.clicked.connect(self._send_or_stop)
        row = QHBoxLayout()
        row.addWidget(self.attach_button, 0, Qt.AlignmentFlag.AlignBottom)
        row.addWidget(self.input, 1)
        row.addWidget(self.send_button, 0, Qt.AlignmentFlag.AlignBottom)

        self.attachments = QWidget()  # one removable chip per attached document
        self._chips = QHBoxLayout(self.attachments)
        self._chips.setContentsMargins(0, 0, 0, 0)
        self._chips.addStretch(1)
        self.attachments.hide()

        self.history = HistoryPanel()
        self.history.hide()
        self.history.chosen.connect(self.open_conversation)
        self.history.new_chat_requested.connect(self.new_chat)
        self.history.search_changed.connect(self.refresh_history)
        self.history.rename_requested.connect(self.rename_conversation)
        self.history.pin_requested.connect(self.pin_conversation)
        self.history.delete_requested.connect(self.delete_conversation)

        chat = QVBoxLayout()
        chat.addLayout(header)
        chat.addWidget(self.view, 1)
        chat.addWidget(self.attachments)
        chat.addLayout(row)
        body = QWidget()
        layout = QHBoxLayout(body)
        layout.setContentsMargins(10, 8, 10, 10)
        layout.addWidget(self.history)
        layout.addLayout(chat, 1)
        self.setCentralWidget(body)
        self.setAcceptDrops(True)

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
        # the answer is kept on the server only once it is complete: no leaving it halfway
        self.new_chat_button.setEnabled(not busy)
        self.history.set_locked(busy)

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
        for worker in [*self._calls, *self._readers]:
            worker.wait(5000)
        for worker in self._calls:
            worker.then = None

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
        if not (text or self.documents) or self.busy:
            return
        if self._readers:
            self.view.add(NOTE, "Still reading the attached documents… send again in a moment.")
            return
        if self._opening is not None:
            self.view.add(NOTE, "Still opening the conversation… send again in a moment.")
            return
        config = self._get_config()
        if not config.ready:
            self.settings_requested.emit()
            return
        if self.conversation is None:
            self.conversation = new_conversation(config.user_id)
        documents, self.documents = self.documents, []
        self._refresh_attachments()
        self.input.clear()
        shown = [literal(text)] if text else []
        shown += [f"📎 {literal(document.name)} ({document.size})" for document in documents]
        self.view.add(USER, "  \n".join(shown))
        self._reply = self.view.add(CLARA, "")
        self._reply_text = ""
        self._worker = ChatWorker(self._api_factory(config), compose(text, documents), self.conversation, self)
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
        worker = self.sender()
        if isinstance(worker, ChatWorker):
            self._ask_title(worker.conversation)

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
        self.refresh_history()  # the conversation is now the last written in (or new in the list)

    # -- the conversations ------------------------------------------------------------------- #

    def _call(self, call: Callable[[ClaraApi], object], then: Callable[[object, str], None]) -> None:
        """Run `call(api)` in the background, then `then(result, error)` here."""
        if self._quitting:
            return
        api = self._api_factory(self._get_config())
        worker = CallWorker(lambda: call(api), then, self)
        worker.done.connect(self._call_done)  # a bound method: Qt runs it on the UI thread
        worker.finished.connect(self._call_finished)
        self._calls.append(worker)
        worker.start()

    def _call_done(self, result: object, error: str) -> None:
        worker = self.sender()
        if not isinstance(worker, CallWorker):
            return
        then, worker.then = worker.then, None  # it refers to the window: no cycle left behind
        if then is not None and not self._quitting:
            then(result, error)

    def _call_finished(self) -> None:
        worker = self.sender()
        if worker in self._calls:
            worker.wait(2000)  # `finished` is sent just before the thread ends
            self._calls.remove(worker)
            worker.deleteLater()

    def _show_title(self) -> None:
        info = self.history.info(self.conversation)
        self.setWindowTitle(f"{APP_NAME} — {display_title(info)}" if info else APP_NAME)

    def start_history(self) -> None:
        """When the app starts or its settings change: a new chat, then the last conversation as soon as the
        list of conversations arrives."""
        self.new_chat()
        self._open_latest = True
        self.refresh_history()

    def show_history(self, shown: bool) -> None:
        """Show or hide the list of conversations; the window grows to its left to make room for it."""
        if shown == self.history.isVisible():
            return
        if self.history_button.isChecked() != shown:
            self.history_button.setChecked(shown)  # comes back here, with nothing left to do
            return
        change = SIDEBAR_WIDTH + 6 if shown else -(SIDEBAR_WIDTH + 6)
        self.history.setVisible(shown)
        self.new_chat_button.setVisible(not shown)  # the list has its own
        if not (self.isMaximized() or self.isFullScreen()):
            geometry = self.geometry()
            left = geometry.left() - change
            screen = self.screen().availableGeometry() if self.screen() else None
            if screen is not None:
                left = max(screen.left(), left)
            self.setGeometry(left, geometry.top(), geometry.width() + change, geometry.height())
        if shown:
            self.refresh_history()

    def refresh_history(self, *_) -> None:
        """Fetch the list of conversations again (what the search box holds)."""
        if not self._get_config().ready:
            return
        if self._listing:
            self._list_again = True
            return
        self._listing = True
        query = self.history.search.text().strip()
        self._call(lambda api: api.conversations(query), self._listed)

    def _listed(self, conversations: object, error: str) -> None:
        self._listing = False
        if error:
            self.history.show_error(error)
        else:
            self.history.show_conversations(conversations, self.conversation)
            self._show_title()
            if self._open_latest and not self.history.search.text().strip():
                self._open_latest = False
                written = any(role == USER for role, _ in self.view.texts())
                untouched = self.conversation is None and not self.busy and not written
                if conversations and untouched:
                    latest = max(conversations, key=lambda info: info["updated_at"])
                    self.open_conversation(latest["id"])
        if self._list_again:
            self._list_again = False
            self.refresh_history()

    def open_conversation(self, conversation: str) -> None:
        """Show a conversation of the list, to read it or to go on with it."""
        if self.busy or conversation == self.conversation and self._opening is None:
            return
        self._opening = conversation
        self.history.set_current(conversation)
        self._call(
            lambda api: api.messages(conversation), lambda shown, error: self._opened(conversation, shown, error)
        )

    def _opened(self, conversation: str, shown: object, error: str) -> None:
        if conversation != self._opening:  # another one was asked for meanwhile, or a new chat
            return
        self._opening = None
        if not error and not isinstance(shown, dict):
            error = "the Clara server sent no conversation"
        if error:
            self.history.set_current(self.conversation)
            self.view.add(ERROR, literal(f"Could not open the conversation: {error}"))
            return
        self.conversation = conversation
        self.view.clear()
        if shown.get("summary"):
            self.view.add(NOTE, "**Earlier in this conversation** (summary)  \n" + literal(shown["summary"]))
        elif shown.get("earlier"):
            self.view.add(NOTE, "Older messages of this conversation are not shown.")
        for message in shown.get("messages", []):
            if message["role"] == "user" and message["content"].startswith(REMINDER_DUE):
                self.add_reminder(message["content"][len(REMINDER_DUE):])  # Clara's announcement follows
            elif message["role"] == "user":
                text, names = split_message(message["content"])
                lines = [literal(text)] if text else []
                self.view.add(USER, "  \n".join(lines + [f"📎 {literal(name)}" for name in names]))
            else:
                self.view.add(CLARA, message["content"])
        if not shown.get("messages") and not shown.get("summary"):
            self.view.add(NOTE, GREETING)
        self.history.set_current(self.conversation)
        self._show_title()
        self.input.setFocus()

    def new_chat(self) -> None:
        """An empty chat: the conversation shown stays in the list, the new one joins it with its first message."""
        if self.busy:
            return
        self._opening = None
        self._open_latest = False
        self.conversation = None
        self.view.clear()
        self.view.add(NOTE, GREETING)
        self.history.set_current(None)
        self._show_title()
        self.input.setFocus()

    def _ask_title(self, conversation: str) -> None:
        """After an answer: Clara titles a conversation that has no title yet (it is listed meanwhile by the
        start of its first message)."""
        info = self.history.info(conversation)
        if conversation in self._titling or (info is not None and info.get("title")):
            return
        self._titling.add(conversation)

        def titled(_title: object, _error: str) -> None:
            self._titling.discard(conversation)
            self.refresh_history()  # without a title (the model failed), it is asked for after the next answer

        self._call(lambda api: api.title(conversation), titled)

    def rename_conversation(self, conversation: str) -> None:
        info = self.history.info(conversation) or {}
        title, accepted = QInputDialog.getText(
            self, "Rename the conversation", "Title (empty: Clara gives it one):", QLineEdit.EchoMode.Normal,
            info.get("title") or display_title(info),
        )
        if accepted:
            self._call(lambda api: api.update_conversation(conversation, title=title.strip()), self._changed)

    def pin_conversation(self, conversation: str, pinned: bool) -> None:
        self._call(lambda api: api.update_conversation(conversation, pinned=pinned), self._changed)

    def _changed(self, _result: object, error: str) -> None:
        if error:
            self.history.show_error(error)
        self.refresh_history()

    def delete_conversation(self, conversation: str) -> None:
        info = self.history.info(conversation) or {}
        answer = QMessageBox.question(
            self,
            "Delete the conversation",
            f"Delete “{display_title(info)}”?\n\nIts messages are erased from the Clara server. What Clara knows "
            "about you stays.",
        )
        if answer != QMessageBox.StandardButton.Yes:
            return

        def deleted(_result: object, error: str) -> None:
            if error:
                self.history.show_error(error)
            elif conversation == self.conversation and not self.busy:
                self.new_chat()
            self.refresh_history()

        self._call(lambda api: api.delete_conversation(conversation), deleted)

    # -- documents ----------------------------------------------------------------------------- #

    def choose_documents(self) -> None:
        paths, _ = QFileDialog.getOpenFileNames(self, "Attach documents", "", FILTER)
        self.attach(paths)

    def attach(self, paths: list[str]) -> None:
        """Read files in the background; each one read joins the next message."""
        attached = {document.path for document in self.documents}
        paths = [path for path in paths if Path(path) not in attached]
        if not paths:
            return
        reader = DocumentWorker(paths, self)
        # Bound methods of the window, not lambdas: Qt then runs them on the UI thread
        reader.loaded.connect(self._document_loaded)
        reader.failed.connect(self._document_failed)
        reader.finished.connect(self._readers_finished)
        self._readers.append(reader)
        self.attach_button.setEnabled(False)
        reader.start()

    def _document_failed(self, message: str) -> None:
        self.view.add(ERROR, literal(message))

    def _document_loaded(self, document: Document) -> None:
        if any(attached.path == document.path for attached in self.documents):
            return
        if total_chars([*self.documents, document]) > MAX_TOTAL_CHARS:
            self.view.add(
                ERROR,
                literal(
                    f"{document.name} does not fit: the documents of one message can hold about "
                    f"{MAX_TOTAL_CHARS:,} characters. Send it in a message of its own, or a part of it."
                ),
            )
            return
        self.documents.append(document)
        self._refresh_attachments()

    def _readers_finished(self) -> None:
        reader = self.sender()
        if reader in self._readers:
            reader.wait(2000)  # `finished` is sent just before the thread ends
            self._readers.remove(reader)
            reader.deleteLater()
        self.attach_button.setEnabled(not self._readers)

    def detach(self, document: Document) -> None:
        self.documents = [attached for attached in self.documents if attached is not document]
        self._refresh_attachments()

    def _refresh_attachments(self) -> None:
        while self._chips.count() > 1:  # the stretch at the end stays
            item = self._chips.takeAt(0)
            if item.widget() is not None:
                item.widget().deleteLater()
        for document in self.documents:
            chip = QPushButton(f"📎 {document.name} · {document.size}  ✕")
            chip.setFlat(True)
            chip.setToolTip(f"{document.path}\nClick to remove it from the message.")
            chip.clicked.connect(lambda _=False, d=document: self.detach(d))
            self._chips.insertWidget(self._chips.count() - 1, chip)
        self.attachments.setVisible(bool(self.documents))

    @staticmethod
    def _files(event: QDragEnterEvent | QDropEvent) -> list[str]:
        mime = event.mimeData()
        if not mime.hasUrls():
            return []
        return [url.toLocalFile() for url in mime.urls() if url.isLocalFile() and Path(url.toLocalFile()).is_file()]

    def dragEnterEvent(self, event: QDragEnterEvent) -> None:
        if self._files(event):
            event.acceptProposedAction()
        else:
            event.ignore()

    def dropEvent(self, event: QDropEvent) -> None:
        files = self._files(event)
        if files:
            event.acceptProposedAction()
            self.attach(files)

    # -- reminders and notifications -------------------------------------------------------------- #

    def add_reminder(self, text: str, detail: str = "") -> None:
        """Keep a trace of a reminder in the conversation."""
        self.view.add(NOTE, f"⏰ **{literal(text)}**" + (f"  \n{literal(detail)}" if detail else ""))

    def add_notification(self, title: str, text: str, detail: str = "") -> None:
        """Keep a trace of a notification in the conversation."""
        head = f"**{literal(title)}**: " if title else ""
        self.view.add(NOTE, f"🔔 {head}{literal(text)}" + (f"  \n{literal(detail)}" if detail else ""))
