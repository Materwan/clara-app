"""The conversations in the rail, as on the web site: a search, the conversations outside any project (pinned ones
first, then by day), and under them one group for each project, named after it, with its conversations newest first.
A menu renames, pins, moves to a project or deletes one.

The panel only shows and asks: the window talks to the server and gives it the list again.
"""

from __future__ import annotations

from datetime import date, datetime

from PySide6.QtCore import QPoint, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont
from PySide6.QtWidgets import QLabel, QLineEdit, QListWidget, QListWidgetItem, QMenu, QVBoxLayout, QWidget

from .documents import preview
from .icons import icon
from .theme import THEME

SEARCH_DELAY_MS = 300  # the search waits for the user to stop typing
UNTITLED = "New conversation"
PINNED = "Pinned"
ID = Qt.ItemDataRole.UserRole  # the conversation of an item (None: a heading)
PROJECT = Qt.ItemDataRole.UserRole + 1  # the project a heading names (None: a date heading, or a conversation)


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


def newest(infos: list[dict]) -> str:
    return max(info["updated_at"] for info in infos)


class HistoryPanel(QWidget):
    chosen = Signal(str)
    project_opened = Signal(int)  # a project's name was clicked
    search_changed = Signal(str)  # once the user has stopped typing
    rename_requested = Signal(str)
    pin_requested = Signal(str, bool)
    move_requested = Signal(str)  # to another project, or out of its own
    delete_requested = Signal(str)

    def __init__(self):
        super().__init__()
        self.conversations: dict[str, dict] = {}  # the ones listed, by id
        self._shown: tuple[list[dict], str | None, dict[int, str], date | None] = ([], None, {}, None)

        self.search = QLineEdit()
        self.search.setPlaceholderText("Search conversations")
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
        self.list.setIconSize(QSize(14, 14))
        self.list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.list.customContextMenuRequested.connect(self._menu)
        self.list.itemClicked.connect(self._clicked)
        self.list.itemActivated.connect(self._clicked)
        self.note = QLabel("")  # nothing yet, nothing found, the server could not be reached
        self.note.setWordWrap(True)
        self.note.setProperty("tone", "muted")
        self.note.hide()

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        layout.addWidget(self.search)
        layout.addWidget(self.list, 1)
        layout.addWidget(self.note)
        THEME.changed.connect(self._render)

    # -- what is shown ------------------------------------------------------------------- #

    def show_conversations(
        self, conversations: list[dict], current: str | None, today: date | None = None, projects: dict[int, str] | None = None
    ) -> None:
        """Show the list the server gave (pinned first, then the last written in): the pinned ones and those of no
        project by day, then each project with its own."""
        self.conversations = {info["id"]: info for info in conversations}
        self._shown = (conversations, current, projects if projects is not None else self._shown[2], today)
        self._render()
        if conversations:
            self.note.hide()
        else:
            self._say("Nothing found." if self.search.text().strip() else "No conversation yet.")

    def _render(self) -> None:
        conversations, current, projects, today = self._shown
        self.list.clear()
        by_day: list[dict] = []
        by_project: dict[int, list[dict]] = {}
        for info in conversations:
            if info.get("project") and not info.get("pinned"):
                by_project.setdefault(info["project"], []).append(info)
            else:
                by_day.append(info)
        heading = None
        for info in by_day:
            group = PINNED if info.get("pinned") else group_of(info["updated_at"], today)
            if group != heading:
                heading = group
                self._add_heading(group)
            self._add_conversation(info)
        for project_id, infos in sorted(by_project.items(), key=lambda pair: newest(pair[1]), reverse=True):
            self._add_heading(projects.get(project_id) or "Project", project_id)
            for info in sorted(infos, key=lambda i: i["updated_at"], reverse=True):
                self._add_conversation(info)
        self.set_current(current)

    def _add_conversation(self, info: dict) -> None:
        item = QListWidgetItem(display_title(info))
        item.setData(ID, info["id"])
        item.setToolTip(display_title(info))
        self.list.addItem(item)

    def _add_heading(self, text: str, project: int | None = None) -> None:
        item = QListWidgetItem(text)
        item.setFlags(Qt.ItemFlag.ItemIsEnabled if project else Qt.ItemFlag.NoItemFlags)  # a project's name opens it
        font = QFont(item.font())
        font.setPixelSize(12)
        font.setWeight(QFont.Weight.DemiBold)
        item.setFont(font)
        item.setForeground(QColor(THEME.t["rail_muted"]))
        item.setData(ID, None)
        item.setData(PROJECT, project)
        if project:
            item.setIcon(icon("folder", "rail_muted", 14))
            item.setToolTip(f"Open the project {text}")
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

    # -- what the user does -------------------------------------------------------------- #

    def _search_now(self) -> None:
        self.search_changed.emit(self.search.text().strip())

    def _clicked(self, item: QListWidgetItem) -> None:
        conversation = item.data(ID)
        if conversation:
            self.chosen.emit(conversation)
        elif item.data(PROJECT):
            self.project_opened.emit(item.data(PROJECT))

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
        menu.addAction(icon("edit", "muted", 16), "Rename…", lambda: self.rename_requested.emit(conversation))
        menu.addAction(icon("pin", "muted", 16), "Unpin" if pinned else "Pin to the top", lambda: self.pin_requested.emit(conversation, not pinned))
        menu.addAction(icon("folder", "muted", 16), "Move to a project…", lambda: self.move_requested.emit(conversation))
        menu.addSeparator()
        menu.addAction(icon("trash", "danger", 16), "Delete…", lambda: self.delete_requested.emit(conversation))
        return menu
