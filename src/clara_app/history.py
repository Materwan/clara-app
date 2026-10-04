"""The list of conversations at the side of the window, as in other chat apps: a new chat, a search, the
conversations (pinned ones first, then by day), and a menu to rename, pin, move to a project or delete one.

The panel only shows and asks: the window talks to the server and gives it the list again.
"""

from __future__ import annotations

from datetime import date, datetime

from PySide6.QtCore import QPoint, Qt, QTimer, Signal
from PySide6.QtGui import QFont
from PySide6.QtWidgets import QLabel, QLineEdit, QListWidget, QListWidgetItem, QMenu, QPushButton, QVBoxLayout, QWidget

from .documents import preview

SIDEBAR_WIDTH = 230
SEARCH_DELAY_MS = 300  # the search waits for the user to stop typing
UNTITLED = "New conversation"
PINNED = "Pinned"
ID = Qt.ItemDataRole.UserRole  # the conversation of an item (None: a heading)


def display_title(info: dict) -> str:
    """What the list says of a conversation: its title, else the start of its first message."""
    return info.get("title") or preview(info.get("preview") or "") or UNTITLED


def group_of(updated_at: str, today: date | None = None) -> str:
    """The heading a conversation last written in at `updated_at` goes under."""
    today = today or date.today()
    age = (today - datetime.fromisoformat(updated_at).astimezone().date()).days
    if age <= 0:
        return "Today"
    if age == 1:
        return "Yesterday"
    if age < 7:
        return "Previous 7 days"
    if age < 30:
        return "Previous 30 days"
    return "Older"


class HistoryPanel(QWidget):
    chosen = Signal(str)
    new_chat_requested = Signal()
    search_changed = Signal(str)  # once the user has stopped typing
    rename_requested = Signal(str)
    pin_requested = Signal(str, bool)
    move_requested = Signal(str)  # to another project, or out of its own
    delete_requested = Signal(str)

    def __init__(self):
        super().__init__()
        self.conversations: dict[str, dict] = {}  # the ones listed, by id
        self.setFixedWidth(SIDEBAR_WIDTH)

        self.new_chat_button = QPushButton("+  New chat")
        self.new_chat_button.setToolTip("Start a new conversation (the others stay in this list)")
        self.new_chat_button.clicked.connect(self.new_chat_requested)
        self.search = QLineEdit()
        self.search.setPlaceholderText("🔍 Search")
        self.search.setClearButtonEnabled(True)
        self._search_timer = QTimer(self)
        self._search_timer.setSingleShot(True)
        self._search_timer.setInterval(SEARCH_DELAY_MS)
        self._search_timer.timeout.connect(self._search_now)
        self.search.textChanged.connect(self._search_timer.start)

        self.list = QListWidget()
        self.list.setToolTip("Right-click a conversation to rename, pin, move or delete it")
        self.list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.list.setTextElideMode(Qt.TextElideMode.ElideRight)  # a long title ends with "…"
        self.list.setWordWrap(False)
        self.list.setStyleSheet("QListWidget::item { padding: 4px 6px; }")
        self.list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.list.customContextMenuRequested.connect(self._menu)
        self.list.itemClicked.connect(self._clicked)
        self.list.itemActivated.connect(self._clicked)
        self.note = QLabel("")  # nothing yet, nothing found, the server could not be reached
        self.note.setWordWrap(True)
        self.note.setStyleSheet("color: gray;")
        self.note.hide()

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 6, 0)
        layout.addWidget(self.new_chat_button)
        layout.addWidget(self.search)
        layout.addWidget(self.list, 1)
        layout.addWidget(self.note)

    # -- what is shown ------------------------------------------------------------------- #

    def show_conversations(self, conversations: list[dict], current: str | None, today: date | None = None) -> None:
        """Show the list the server gave (already in order: pinned first, then the last written in)."""
        self.conversations = {info["id"]: info for info in conversations}
        self.list.clear()
        heading = None
        for info in conversations:
            group = PINNED if info.get("pinned") else group_of(info["updated_at"], today)
            if group != heading:
                heading = group
                self._add_heading(group)
            item = QListWidgetItem(display_title(info))
            item.setData(ID, info["id"])
            item.setToolTip(display_title(info))
            self.list.addItem(item)
        self.set_current(current)
        if conversations:
            self.note.hide()
        else:
            self._say("Nothing found." if self.search.text().strip() else "No conversation yet.")

    def _add_heading(self, text: str) -> None:
        item = QListWidgetItem(text.upper())
        item.setFlags(Qt.ItemFlag.NoItemFlags)  # neither chosen nor selected
        font = QFont(item.font())
        font.setBold(True)
        font.setPointSizeF(max(6.0, font.pointSizeF() * 0.9))
        item.setFont(font)
        item.setForeground(Qt.GlobalColor.gray)
        item.setData(ID, None)
        self.list.addItem(item)

    def show_error(self, message: str) -> None:
        self._say(message)

    def _say(self, text: str) -> None:
        self.note.setText(text)
        self.note.show()

    def set_current(self, conversation: str | None) -> None:
        """Select the conversation shown in the window (none: a new chat)."""
        self.list.clearSelection()
        for row in range(self.list.count()):
            item = self.list.item(row)
            if conversation is not None and item.data(ID) == conversation:
                self.list.setCurrentItem(item)
                return
        self.list.setCurrentRow(-1)

    def info(self, conversation: str | None) -> dict | None:
        return self.conversations.get(conversation) if conversation else None

    def items(self) -> list[tuple[str | None, str]]:
        """`(conversation or None for a heading, text)` of every line, for tests."""
        return [(self.list.item(row).data(ID), self.list.item(row).text()) for row in range(self.list.count())]

    def set_locked(self, locked: bool) -> None:
        """While Clara writes, no other conversation can be opened (the answer would be lost)."""
        self.list.setEnabled(not locked)
        self.new_chat_button.setEnabled(not locked)

    # -- what the user does -------------------------------------------------------------- #

    def _search_now(self) -> None:
        self.search_changed.emit(self.search.text().strip())

    def _clicked(self, item: QListWidgetItem) -> None:
        conversation = item.data(ID)
        if conversation:
            self.chosen.emit(conversation)

    def _menu(self, position: QPoint) -> None:
        item = self.list.itemAt(position)
        conversation = item.data(ID) if item is not None else None
        if not conversation or not self.list.isEnabled():
            return
        menu = self.menu_for(conversation)
        menu.exec(self.list.viewport().mapToGlobal(position))
        menu.deleteLater()

    def menu_for(self, conversation: str) -> QMenu:
        menu = QMenu(self)
        pinned = bool(self.conversations.get(conversation, {}).get("pinned"))
        menu.addAction("Rename…", lambda: self.rename_requested.emit(conversation))
        menu.addAction("Unpin" if pinned else "Pin", lambda: self.pin_requested.emit(conversation, not pinned))
        menu.addAction("Move to a project…", lambda: self.move_requested.emit(conversation))
        menu.addSeparator()
        menu.addAction("Delete…", lambda: self.delete_requested.emit(conversation))
        return menu
