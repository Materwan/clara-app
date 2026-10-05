"""The window: the frame of the web site (a rail with Clara's portrait, a new chat, the pages you work in, your
conversations grouped by day and by project, and you), the chat, and the other pages beside it.

The window talks to the server for the chat and the conversations; the pages are made when they are first opened and
talk to it themselves. A project is chosen by the conversations (a project's group in the rail), and a new chat in a
project starts from the project's page."""

from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path
from typing import Callable

from PySide6.QtCore import QEvent, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QDragEnterEvent, QDropEvent, QGuiApplication, QKeyEvent
from PySide6.QtWidgets import (
    QComboBox,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QInputDialog,
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
from .approvals import ApprovalCard, ApprovalsDialog
from .chat_view import CLARA, ERROR, NOTE, USER, ChatView, MessageBubble, tool_note
from .config import Config
from .documents import FILTER, MAX_TOTAL_CHARS, Document, compose, split_message, total_chars
from .history import HistoryPanel, display_title
from .icon import make_icon
from .icons import bind_icon
from .qcm import QcmCard, display_answers
from .shell import Shell
from .theme import THEME, set_property
from .widgets import Page
from .workers import CallWorker, ChatWorker, DocumentWorker

FLUSH_MS = 40  # streamed text is drawn at most this often
MAX_INPUT_LINES = 6
REMINDER_DUE = "[Reminder due] "  # what the server puts in a conversation when a reminder set there comes due
WINDOW_SIZE = QSize(1120, 740)
SYNC_MS = 30_000  # the web site shares our conversations and projects: look again this often while the window shows

_MARKDOWN_SPECIAL = re.compile(r"([\\`*_{}\[\]()#+\-.!|<>~&])")


def literal(text: str) -> str:
    """`text` as it must be written in Markdown to be displayed exactly as typed."""
    escaped = _MARKDOWN_SPECIAL.sub(r"\\\1", text)
    return escaped.replace("\r\n", "\n").replace("\n", "  \n")  # two spaces: a line break, not a space


def greeting(now: datetime | None = None) -> str:
    hour = (now or datetime.now()).hour
    return "Hello" if hour < 5 else "Good morning" if hour < 12 else "Good afternoon" if hour < 18 else "Good evening"


class InputBox(QPlainTextEdit):
    """Enter sends, Shift+Enter starts a new line. Grows with its text, up to a few lines."""

    submitted = Signal()
    focus_changed = Signal(bool)

    def __init__(self):
        super().__init__()
        self.setPlaceholderText("Message Clara")
        self.setTabChangesFocus(True)
        self.textChanged.connect(self._fit)
        self._fit()

    def _fit(self) -> None:
        lines = max(1, min(MAX_INPUT_LINES, round(self.document().size().height())))
        metrics = self.fontMetrics()
        margin = int(self.document().documentMargin() * 2) + 2 * self.frameWidth()
        self.setFixedHeight(metrics.lineSpacing() * lines + margin + 14)

    def keyPressEvent(self, event: QKeyEvent) -> None:
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter) and not (
            event.modifiers() & Qt.KeyboardModifier.ShiftModifier
        ):
            self.submitted.emit()
            return
        super().keyPressEvent(event)

    def focusInEvent(self, event) -> None:
        super().focusInEvent(event)
        self.focus_changed.emit(True)

    def focusOutEvent(self, event) -> None:
        super().focusOutEvent(event)
        self.focus_changed.emit(False)


class ChatPage(Page):
    """The conversation and the box to write in."""

    page_title = "New chat"

    def __init__(self, window: "ChatWindow"):
        super().__init__()
        self.window_ = window
        self.connections_button = QToolButton()
        self.connections_button.setToolTip("Connections: what Clara can reach in this conversation (GitHub, Google Drive, folders)")
        bind_icon(self.connections_button, "plug", "muted", 20)
        self.connections_button.clicked.connect(window.show_connections)
        self.more_button = QToolButton()
        self.more_button.setToolTip("Conversation actions")
        bind_icon(self.more_button, "more", "muted", 20)
        self.more_button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.more_button.clicked.connect(window.show_conversation_menu)
        self.more_button.hide()

    def actions(self) -> list[QWidget]:
        return [self.connections_button, self.more_button]

    def activated(self) -> None:
        self.window_.input.setFocus()


class ChatWindow(QMainWindow):
    settings_requested = Signal()  # the connection must be set up (or changed)
    theme_chosen = Signal(str)  # "auto", "light" or "dark"
    signed_out = Signal()  # the user asked to forget the sign-in on this computer
    approvals_changed = Signal()  # a request for permission was answered here: the count in the rail is read again

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
        self._stamp: str | None = None  # when the conversation shown was last written in, as read: it may go on elsewhere
        self._restamp = False  # our own answer just ended: the next list tells when the conversation was last written in
        self._reply: MessageBubble | None = None
        self._reply_text = ""
        self._quitting = False
        self._placed = False
        self.documents: list[Document] = []  # attached to the next message
        self._readers: list[DocumentWorker] = []  # files being read
        self.project: int | None = None  # where the next new chat goes (None: in none)
        self.project_names: dict[int, str] = {}
        self.server_state: str | None = None
        self.models: dict | None = None  # what the server offers to choose from, once known

        self.setWindowTitle(APP_NAME)
        self.setWindowIcon(make_icon())
        self.resize(WINDOW_SIZE)
        self.setMinimumSize(QSize(380, 480))

        # ---- the chat page ------------------------------------------------------------------------------
        self.view = ChatView()
        self.input = InputBox()
        self.input.submitted.connect(self.send)
        self.attach_button = QToolButton()
        self.attach_button.setToolTip("Attach documents: PDF, code (Python, C…), Markdown, text. You can also drop files here.")
        bind_icon(self.attach_button, "clip", "muted", 20)
        self.attach_button.clicked.connect(self.choose_documents)
        self.doc_info = QPushButton("")
        self.doc_info.hide()
        self.model_box = QComboBox()
        self.model_box.setProperty("flat", True)
        self.model_box.setToolTip("The model Clara answers you with here (an administrator chooses which are offered)")
        self.model_box.hide()
        self.model_box.activated.connect(self._model_chosen)
        self.send_button = QPushButton()
        self.send_button.setProperty("kind", "send")
        self.send_button.setFixedSize(40, 40)
        self.send_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.send_button.setToolTip("Send")
        bind_icon(self.send_button, "send", "accent_ink", 20)
        self.send_button.clicked.connect(self._send_or_stop)

        self.attachments = QWidget()  # one removable chip per attached document
        self._chips = QHBoxLayout(self.attachments)
        self._chips.setContentsMargins(4, 0, 4, 0)
        self._chips.addStretch(1)
        self.attachments.hide()

        self.composer = QFrame()
        self.composer.setObjectName("composer")
        box = QVBoxLayout(self.composer)
        box.setContentsMargins(10, 8, 10, 8)
        box.setSpacing(4)
        bar = QHBoxLayout()
        bar.setSpacing(6)
        bar.addWidget(self.attach_button)
        bar.addStretch(1)
        bar.addWidget(self.model_box)
        bar.addWidget(self.send_button)
        box.addWidget(self.attachments)
        box.addWidget(self.input)
        box.addLayout(bar)
        self.input.focus_changed.connect(lambda focused: set_property(self.composer, "focused", focused))

        self.chat_page = ChatPage(self)
        page = QVBoxLayout(self.chat_page)
        page.setContentsMargins(0, 0, 0, 0)
        page.setSpacing(0)
        page.addWidget(self.view, 1)
        wrap = QHBoxLayout()
        wrap.setContentsMargins(24, 0, 24, 18)
        frame = QWidget()
        frame.setMaximumWidth(920)
        inner = QVBoxLayout(frame)
        inner.setContentsMargins(0, 0, 0, 0)
        inner.addWidget(self.composer)
        wrap.addStretch(1)
        wrap.addWidget(frame, 100)
        wrap.addStretch(1)
        page.addLayout(wrap)

        # ---- the rail's list, and the frame ------------------------------------------------------------------
        self.history = HistoryPanel()
        self.history.chosen.connect(self.open_conversation)
        self.history.project_opened.connect(self.open_project)
        self.history.search_changed.connect(self.refresh_history)
        self.history.rename_requested.connect(self.rename_conversation)
        self.history.pin_requested.connect(self.pin_conversation)
        self.history.move_requested.connect(self.move_conversation)
        self.history.delete_requested.connect(self.delete_conversation)

        self.shell = Shell(self.history, self._factories())
        self.shell.new_chat_requested.connect(self._new_chat_clicked)
        self.shell.page_changed.connect(self._page_changed)
        self.shell.approvals_requested.connect(self.show_approvals)
        self.setCentralWidget(self.shell)
        self.shell.show_page("chat")
        self.setAcceptDrops(True)

        self._flush_timer = QTimer(self)
        self._flush_timer.setSingleShot(True)
        self._flush_timer.setInterval(FLUSH_MS)
        self._flush_timer.timeout.connect(self._flush)
        THEME.changed.connect(self._set_busy_icon)
        self._sync_timer = QTimer(self)
        self._sync_timer.setInterval(SYNC_MS)
        self._sync_timer.timeout.connect(self.sync)
        self._sync_timer.start()

        self._show_welcome()
        self.set_state(None)
        self.set_user_label()

    def _factories(self) -> dict[str, Callable[[], Page]]:
        """How each page other than the chat is made, the first time it is opened."""
        from .account_page import AccountPage
        from .admin_page import AdminPage
        from .discord_page import DiscordPage
        from .files_page import FilesPage
        from .integrations_page import IntegrationsPage
        from .memory_page import MemoryPage
        from .projects_page import ProjectsPage
        from .tasks_page import TasksPage

        args = (self._get_config, self._api_factory, self)
        return {
            "chat": lambda: self.chat_page,
            "projects": lambda: ProjectsPage(*args),
            "tasks": lambda: TasksPage(*args),
            "files": lambda: FilesPage(*args),
            "memory": lambda: MemoryPage(*args),
            "account": lambda: AccountPage(*args),
            "integrations": lambda: IntegrationsPage(*args),
            "discord": lambda: DiscordPage(*args),
            "admin": lambda: AdminPage(*args),
        }

    # -- state ------------------------------------------------------------------------ #

    @property
    def busy(self) -> bool:
        return self._worker is not None

    def config(self) -> Config:
        return self._get_config()

    def display_name(self) -> str:
        config = self._get_config()
        return config.user_name or config.user_id or APP_NAME

    def set_user_label(self) -> None:
        self.shell.set_user(self.display_name(), "")

    def set_state(self, state: str | None) -> None:
        """What the status line says about the server: "running", "stopping", "down", or None (not known yet)."""
        self.server_state = state
        text = {"running": "Clara is running", "stopping": "Clara is stopping", "down": "Clara is not running"}.get(state or "", "")
        self.shell.set_status(text, state)

    def _set_busy(self, busy: bool) -> None:
        self.send_button.setProperty("kind", "stop" if busy else "send")
        self.send_button.style().unpolish(self.send_button)
        self.send_button.style().polish(self.send_button)
        self.send_button.setToolTip("Stop the answer" if busy else "Send")
        self._set_busy_icon()
        # the answer is kept on the server only once it is complete: no leaving it halfway
        self.shell.new_chat.setEnabled(not busy)
        self.model_box.setEnabled(not busy)  # the answer belongs to the conversation shown
        self.history.set_locked(busy)

    def _set_busy_icon(self) -> None:
        busy = self.busy
        from .icons import icon

        self.send_button.setIcon(icon("stop" if busy else "send", "bg" if busy else "accent_ink", 20))
        self.send_button.setIconSize(QSize(20, 20))

    # -- showing the window --------------------------------------------------------------- #

    def bring_to_front(self) -> None:
        if not self._placed:  # the first time: in the middle of the screen
            self._placed = True
            area = QGuiApplication.primaryScreen().availableGeometry()
            size = self.size().boundedTo(area.size())
            self.resize(size)
            self.move(area.center().x() - size.width() // 2, area.center().y() - size.height() // 2)
        self.setWindowState(self.windowState() & ~Qt.WindowState.WindowMinimized)
        self.show()
        self.raise_()
        self.activateWindow()
        if self.shell.current == "chat":
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
        for page in self.shell.pages.values():
            page.shutdown()
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
            self.sync()
            if self.shell.current == "chat":
                self.input.setFocus()

    # -- going to a page -------------------------------------------------------------------- #

    def go(self, name: str) -> None:
        """Show a page (`chat`, `projects`, `tasks`, `files`, `memory`, `account`, `discord`, `admin`)."""
        self.shell.show_page(name)

    def _page_changed(self, name: str) -> None:
        if name == "chat":
            self.input.setFocus()

    def open_tasks(self) -> None:
        if not self._get_config().ready:
            self.settings_requested.emit()
            return
        self.go("tasks")

    def open_projects(self) -> None:
        if not self._get_config().ready:
            self.settings_requested.emit()
            return
        self.go("projects")

    def open_project(self, project: int) -> None:
        self.go("projects")
        self.shell.pages["projects"].show_project(project)

    def chat_in_project(self, project: int) -> None:
        """A new chat in a project (from its page)."""
        if self.busy:
            return
        self.go("chat")
        self.new_chat(project)

    def projects_changed(self) -> None:
        self.refresh_projects()

    def set_theme(self, preference: str) -> None:
        """Light, dark or the way Windows is set (kept by the app's settings)."""
        THEME.apply(preference)
        self.theme_chosen.emit(preference)

    def sign_out(self) -> None:
        self.signed_out.emit()

    def _new_chat_clicked(self) -> None:
        self.go("chat")
        self.new_chat()

    # -- talking ------------------------------------------------------------------------------ #

    def _send_or_stop(self) -> None:
        if self.busy:
            self.cancel()
        else:
            self.send()

    def send(self, text: str | None = None, direct: bool = False) -> None:
        """Send the text of the box with the attached documents, or, given a text, that text. `direct`: only that
        text (the answers of a QCM): the box and the documents waiting are left alone."""
        text = (self.input.toPlainText() if text is None else text).strip()
        if not (text or (self.documents and not direct)) or self.busy:
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
        documents, self.documents = ([], self.documents) if direct else (self.documents, [])
        self._refresh_attachments()
        if not direct:
            self.input.clear()
        shown = [literal(display_answers(text))] if text else []
        shown += [f"📎 {literal(document.name)} ({document.size})" for document in documents]
        self.view.add(USER, "  \n".join(shown))
        self._reply = self.view.add(CLARA, "")
        self._reply_text = ""
        self._worker = ChatWorker(
            self._api_factory(config), compose(text, documents), self.conversation, self, project=self.project
        )
        self._worker.token.connect(self._on_token)
        self._worker.qcm.connect(self._on_qcm)
        self._worker.approval.connect(self._on_approval)
        self._worker.tool.connect(self._on_tool)
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

    def _on_tool(self, event: dict) -> None:
        note = tool_note(event)
        if note and self._reply is not None:
            self._reply.add_note(*note)

    def _on_qcm(self, form: dict) -> None:
        self._flush()
        self._add_qcm(form)

    def _on_approval(self, approval: dict) -> None:
        """Clara asked for a permission and went on: the request, with its buttons, in the conversation."""
        self._flush()
        self._add_approval(approval)

    def _add_approval(self, approval: dict) -> None:
        if any(getattr(card, "approval", {}).get("id") == approval.get("id") for card in self.view.cards):
            return
        card = ApprovalCard(approval, self._get_config, self._api_factory)
        card.decided.connect(self._approval_answered)
        self.view.add_card(card)

    def _approval_answered(self, _done: dict) -> None:
        """Answered: Clara goes on by herself on the server (a follow-up turn), so read the conversation again for a while."""
        self.approvals_changed.emit()
        self._watch_follow_up()

    def _watch_follow_up(self) -> None:
        timer = getattr(self, "_follow_timer", None)
        if timer is None:
            timer = self._follow_timer = QTimer(self)
            timer.setInterval(3000)
            timer.timeout.connect(self._follow_tick)
        self._follow_ticks = 0
        timer.start()

    def _follow_tick(self) -> None:
        self._follow_ticks += 1
        if self._follow_ticks > 20:
            self._follow_timer.stop()
            return
        self.sync()

    def _load_approvals(self, conversation: str) -> None:
        """The requests of the conversation shown that still wait (they are not in its messages)."""
        def found(result: object, error: str) -> None:
            if error or conversation != self.conversation:
                return
            for approval in list(result):  # type: ignore[arg-type]
                self._add_approval(approval)

        self._call(lambda api: api.approvals(conversation), found)

    def show_approvals(self) -> None:
        """Every request waiting for an answer, in a list."""
        dialog = ApprovalsDialog(self._get_config, self._api_factory, self)
        dialog.changed.connect(self._approval_answered)
        dialog.exec()
        self.approvals_changed.emit()

    def show_connections(self) -> None:
        """What is connected to the conversation shown: attach a repository, a Drive folder, a folder."""
        if not self._get_config().ready:
            self.settings_requested.emit()
            return
        if self.conversation is None:  # a new chat: its first message creates it, under this name
            self.conversation = new_conversation(self._get_config().user_id)
        from .integrations_page import ConnectionsDialog

        heading = "This conversation is in a project: what is connected to the project is available here too." if self.project else ""
        ConnectionsDialog({"conversation": self.conversation}, self._get_config, self._api_factory, self, heading).exec()

    def _add_qcm(self, form: dict, answers: list | None = None) -> None:
        """A form of Clara's in the conversation: to fill in, or, with the answers the user gave, answered."""
        card = QcmCard(form, answers)
        if answers is None:
            card.submitted.connect(lambda text, shown=card: self._submit_qcm(shown, text))
        self.view.add_card(card)

    def _submit_qcm(self, card: QcmCard, text: str) -> None:
        if self.busy:
            card.warn("Wait for Clara to finish, then send your answers.")
            return
        self.send(text, direct=True)
        if self.busy:  # sent
            card.lock()
        else:
            card.warn("The answers could not be sent just now: try again.")

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
        elif self._reply is not None:
            self._reply.settle()  # a text with formulas is now typeset
        self._reply = None
        self._set_busy(False)
        worker.deleteLater()
        self._restamp = True
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
        self.chat_page.set_title(display_title(info) if info else "New chat")
        self.chat_page.more_button.setVisible(info is not None)

    def _show_welcome(self) -> None:
        name = self.project_names.get(self.project) if self.project is not None else None
        title = f"{greeting()}, {self.display_name()}"
        text = f"A new chat in {name}: Clara can use its files and instructions." if name else "What would you like to talk about?"
        self.view.set_welcome(title, text)

    def start_history(self) -> None:
        """When the app starts or its settings change: a new chat, then the last conversation as soon as the
        list of conversations arrives."""
        self.new_chat()
        self._open_latest = True
        self.refresh_history()
        self.refresh_projects()
        self.refresh_identity()
        self.refresh_models()
        self.set_user_label()

    def refresh_identity(self) -> None:
        """Who the server says is signed in: an administrator sees the pages of administration."""

        def known(me: object, error: str) -> None:
            admin = bool(not error and isinstance(me, dict) and me.get("is_admin"))
            self.shell.set_admin(admin)

        if self._get_config().ready:
            self._call(lambda api: api.me(), known)

    def sync(self) -> None:
        """What the web site changed meanwhile: the conversations and the projects, read again while the window is
        in front (and the conversation shown, when it was gone on with elsewhere: see `_listed`)."""
        if not self.isVisible() or self.isMinimized() or self.busy:
            return
        self.refresh_history()
        self.refresh_projects()

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
            self.history.show_conversations(conversations, self.conversation, projects=self.project_names)
            self._show_title()
            self._follow(self.history.info(self.conversation))
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

    def _follow(self, info: dict | None) -> None:
        """The conversation shown, as the list now has it: read again if it was written in elsewhere (the web
        site, say) since we showed it. Never while an answer is being written, or one is being opened."""
        if info is None or self.busy or self._opening is not None:
            return
        if self._restamp:  # our own answer: that is the state we show
            self._restamp = False
            self._stamp = info.get("updated_at")
        elif self._stamp and info.get("updated_at") != self._stamp:
            self.open_conversation(info["id"], reload=True)

    def open_conversation(self, conversation: str, reload: bool = False) -> None:
        """Show a conversation of the list, to read it or to go on with it (`reload`: again, where the window is)."""
        if not reload and self.shell.current != "chat":
            self.go("chat")
        if self.busy or conversation == self.conversation and self._opening is None and not reload:
            return
        self._opening = conversation
        self.history.set_current(conversation)
        self._call(
            lambda api: api.messages(conversation),
            lambda shown, error: self._opened(conversation, shown, error, quiet=reload),
        )

    def _opened(self, conversation: str, shown: object, error: str, quiet: bool = False) -> None:
        if conversation != self._opening:  # another one was asked for meanwhile, or a new chat
            return
        self._opening = None
        if not error and not isinstance(shown, dict):
            error = "the Clara server sent no conversation"
        if error:
            self.history.set_current(self.conversation)
            if not quiet:  # read again in the background: the next look tries once more
                self.view.add(ERROR, literal(f"Could not open the conversation: {error}"))
            return
        self.conversation = conversation
        info = self.history.info(conversation) or {}
        self.project = info.get("project")
        self._stamp = shown.get("updated_at") or info.get("updated_at")
        self.view.clear()
        if shown.get("summary"):
            self.view.add(NOTE, "**Earlier in this conversation** (summary)  \n" + literal(shown["summary"]))
        elif shown.get("earlier"):
            self.view.add(NOTE, "Older messages of this conversation are not shown.")
        for message in shown.get("messages", []):
            if message["role"] == "user" and message["content"].startswith(REMINDER_DUE):
                self.add_reminder(message["content"][len(REMINDER_DUE):])  # Clara's announcement follows
            elif message["role"] == "user":
                text, names = split_message(display_answers(message["content"]))
                lines = [literal(text)] if text else []
                self.view.add(USER, "  \n".join(lines + [f"📎 {literal(name)}" for name in names]))
            else:
                if message["content"]:
                    self.view.add(CLARA, message["content"])
                for form in message.get("qcm") or []:
                    self._add_qcm(form, form.get("answers"))
        if not shown.get("messages") and not shown.get("summary"):
            self._show_welcome()
        self._load_approvals(conversation)
        self.history.set_current(self.conversation)
        self._show_title()
        self.input.setFocus()

    def new_chat(self, project: int | None = None) -> None:
        """An empty chat: the conversation shown stays in the list, the new one joins it with its first message
        (in `project`, when one is given)."""
        if self.busy:
            return
        self._opening = None
        self._open_latest = False
        self.conversation = None
        self._stamp = None
        self.project = project
        self.view.clear()
        self._show_welcome()
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

    def show_conversation_menu(self) -> None:
        """The "…" of the page header: the menu of the conversation shown."""
        if self.conversation is None or self.history.info(self.conversation) is None:
            return
        menu = self.history.menu_for(self.conversation)
        button = self.chat_page.more_button
        menu.exec(button.mapToGlobal(button.rect().bottomLeft()))
        menu.deleteLater()

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

    def move_conversation(self, conversation: str) -> None:
        """Put a conversation in another project, or in none."""
        info = self.history.info(conversation) or {}
        names = ["No project", *self.project_names.values()]
        ids: list[int | None] = [None, *self.project_names]
        current = ids.index(info.get("project")) if info.get("project") in ids else 0
        name, accepted = QInputDialog.getItem(
            self, "Move to a project", f"Move “{display_title(info)}” to:", names, current, False
        )
        if not accepted:
            return
        target = ids[names.index(name)]
        if target == info.get("project"):
            return

        def moved(_result: object, error: str) -> None:
            if error:
                self.history.show_error(error)
            elif conversation == self.conversation:
                self.project = target  # the conversation shown goes with it
            self.refresh_history()

        self._call(lambda api: api.update_conversation(conversation, project=target), moved)

    # -- projects ------------------------------------------------------------------------------ #

    def refresh_projects(self) -> None:
        """Fetch the user's projects again, to name the groups of the rail."""
        if self._get_config().ready:
            self._call(lambda api: api.projects(), self._projects_listed)

    def _projects_listed(self, projects: object, error: str) -> None:
        if error or not isinstance(projects, list):
            return
        self.project_names = {project["id"]: project["name"] for project in projects}
        if self.project not in self.project_names:  # deleted meanwhile
            self.project = None
        self.history.show_conversations(
            list(self.history.conversations.values()), self.conversation, projects=self.project_names
        )
        if not self.view.bubbles:
            self._show_welcome()

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

    # -- the model ------------------------------------------------------------------------------ #

    def refresh_models(self) -> None:
        """The models an administrator offers: when there are some, a quiet picker appears in the box."""
        if self._get_config().ready:
            self._call(lambda api: api.models(), self._models_listed)

    def _models_listed(self, info: object, error: str) -> None:
        if error or not isinstance(info, dict):
            return
        self.models = info
        offered = info.get("models") or []
        self.model_box.blockSignals(True)
        self.model_box.clear()
        default = info.get("default") or {}
        self.model_box.addItem(f"Server default: {default.get('name', '')}".strip(": "), None)
        for model in offered:
            self.model_box.addItem(f"{model['name']} ({model['provider_label']})", model["ref"])
        own = (info.get("choices") or {}).get("app")
        self.model_box.setCurrentIndex(max(0, self.model_box.findData(own)))
        self.model_box.blockSignals(False)
        self.model_box.setVisible(bool(offered))

    def _model_chosen(self, index: int) -> None:
        ref = self.model_box.itemData(index)
        self._call(lambda api: api.choose_model(ref), lambda _r, error: self.history.show_error(error) if error else None)

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
            chip.setProperty("kind", "chip")
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
            self.go("chat")
            self.attach(files)

    # -- reminders and notifications -------------------------------------------------------------- #

    def add_reminder(self, text: str, detail: str = "") -> None:
        """Keep a trace of a reminder in the conversation."""
        self.view.add(NOTE, f"⏰ **{literal(text)}**" + (f"  \n{literal(detail)}" if detail else ""))

    def add_notification(self, title: str, text: str, detail: str = "") -> None:
        """Keep a trace of a notification in the conversation."""
        head = f"**{literal(title)}**: " if title else ""
        self.view.add(NOTE, f"🔔 {head}{literal(text)}" + (f"  \n{literal(detail)}" if detail else ""))
