"""The conversation, as on the web site: a wide column in the middle, your messages in a violet-tinted bubble on the
right, Clara's plain on the left beside her portrait, with a quiet line under a reply that says what she used."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QScrollArea, QVBoxLayout, QWidget

from . import mathview
from .icons import portrait_label
from .widgets import label

USER, CLARA, NOTE, ERROR = "user", "clara", "note", "error"
COLUMN = 920  # the widest the conversation gets, in pixels
USER_SHARE = 0.78  # widest a bubble of the user gets, as a share of the column
STICK_MARGIN = 24  # pixels from the bottom within which the view keeps following new text
PORTRAIT = 30

# What Clara used to answer, in words: tool name -> (what she did, the argument that says what about)
TOOL_NOTES = {
    "remember": ("Remembered", "fact"),
    "forget": ("Forgot a fact", ""),
    "recall_facts": ("Looked in memory for", "query"),
    "about_person": ("Read what she knows about", "name"),
    "web_search": ("Searched the web for", "query"),
    "web_fetch": ("Read a page", "url"),
    "remind": ("Set a reminder", "text"),
    "list_reminders": ("Checked your reminders", ""),
    "cancel_reminder": ("Cancelled a reminder", ""),
    "notify": ("Notified you", "text"),
    "add_task": ("Added a task", "title"),
    "list_tasks": ("Read your tasks", ""),
    "update_task": ("Updated a task", "title"),
    "delete_task": ("Deleted a task", ""),
    "create_markdown_file": ("Wrote", "name"),
    "edit_markdown_file": ("Edited", "name"),
    "append_markdown_file": ("Added to", "name"),
    "read_markdown_file": ("Read", "name"),
    "list_markdown_files": ("Listed your files", ""),
    "list_project_files": ("Listed the project's files", ""),
    "read_project_file": ("Read", "path"),
    "search_project": ("Searched the project for", "query"),
}
SILENT_TOOLS = {"qcm", "adjust_relation"}  # the form is its own card; the relationship is not shown here


def tool_note(event: dict) -> tuple[str, str] | None:
    """`(what she did, about what)` for a `tool` event of the stream, or None for a tool that leaves no note."""
    name = str(event.get("name") or "")
    if not name or name in SILENT_TOOLS:
        return None
    what, key = TOOL_NOTES.get(name, (name.replace("_", " "), ""))
    arguments = event.get("arguments")
    detail = arguments.get(key) if key and isinstance(arguments, dict) else ""
    detail = detail.strip() if isinstance(detail, str) else ""
    return what, (detail[:79] + "…" if len(detail) > 80 else detail)


class MessageBubble(QFrame):
    def __init__(self, role: str, text: str = ""):
        super().__init__()
        self.setObjectName("bubble")
        self.setProperty("role", role)
        self.role = role
        self.text = text
        self.notes: list[tuple[str, str]] = []  # what Clara used, for a reply
        self.label = QLabel()
        self.label.setTextFormat(Qt.TextFormat.MarkdownText)
        self.label.setWordWrap(True)
        self.label.setOpenExternalLinks(True)
        self.label.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse | Qt.TextInteractionFlag.LinksAccessibleByMouse
        )
        self.math: mathview.MathView | None = None  # shows the text instead of the label when it has formulas
        self._notes_label = QLabel()
        self._notes_label.setObjectName("notes")
        self._notes_label.setWordWrap(True)
        self._notes_label.hide()
        if role == CLARA:
            self.label.setStyleSheet("font-size: 15px;")
            self._text_column = QVBoxLayout()
            self._text_column.setContentsMargins(0, 3, 0, 0)
            self._text_column.setSpacing(6)
            self._text_column.addWidget(self.label)
            self._text_column.addWidget(self._notes_label)
            row = QHBoxLayout(self)
            row.setContentsMargins(0, 0, 0, 0)
            row.setSpacing(12)
            row.addWidget(portrait_label(PORTRAIT), 0, Qt.AlignmentFlag.AlignTop)
            row.addLayout(self._text_column, 1)
            self._layout = self._text_column
        else:
            padding = 4 if role == NOTE else 10
            self._layout = QVBoxLayout(self)
            self._layout.setContentsMargins(padding + 6, padding, padding + 6, padding)
            self._layout.addWidget(self.label)
        self.set_text(text)
        self.settle()

    def set_text(self, text: str) -> None:
        self.text = text
        if self.math is not None:
            self.math.set_text(text)
        else:
            self.label.setText(text if text else "…")  # a reply still being awaited shows as "…"

    def add_note(self, what: str, detail: str = "") -> None:
        """Under a reply: one more thing she did to answer."""
        self.notes.append((what, detail))
        self._notes_label.setText("      ".join(f"<b>{w}</b> {d}".strip() for w, d in self.notes))
        self._notes_label.show()

    def settle(self) -> None:
        """Clara's text is complete (or was read back): when it holds formulas a web view typesets it. While it
        streams in it stays a label, with the formulas as source: the page would be loaded at each piece."""
        if self.role != CLARA or self.math is not None or not mathview.available() or not mathview.has_math(self.text):
            return
        self.math = mathview.MathView(self.text, parent=self)
        self._layout.replaceWidget(self.label, self.math)
        self.label.hide()


class ChatView(QScrollArea):
    def __init__(self):
        super().__init__()
        self.setWidgetResizable(True)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        outer = QWidget()
        outer.setObjectName("scroll-body")
        sides = QHBoxLayout(outer)
        sides.setContentsMargins(24, 20, 24, 12)
        self._inner = QWidget()
        self._inner.setMaximumWidth(COLUMN)
        self._column = QVBoxLayout(self._inner)
        self._column.setContentsMargins(0, 0, 0, 0)
        self._column.setSpacing(24)
        self._column.addStretch(1)
        sides.addStretch(1)
        sides.addWidget(self._inner, 100)
        sides.addStretch(1)
        self.setWidget(outer)
        self.bubbles: list[MessageBubble] = []
        self.cards: list[QWidget] = []  # forms (QCM) shown between the bubbles
        self._rows: dict[QWidget, QHBoxLayout] = {}
        self._follow = True

        # what shows when the conversation is empty: Clara's face, a greeting, and what she can do here
        self.welcome = QWidget(self._inner)
        welcome = QVBoxLayout(self.welcome)
        welcome.setContentsMargins(0, 60, 0, 0)
        welcome.setSpacing(8)
        welcome.addWidget(portrait_label(68), 0, Qt.AlignmentFlag.AlignHCenter)
        welcome.addSpacing(10)
        self.welcome_title = label("", heading="hero")
        self.welcome_title.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        self.welcome_text = label("", tone_="muted", wrap=True)
        self.welcome_text.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        welcome.addWidget(self.welcome_title)
        welcome.addWidget(self.welcome_text)
        self._column.insertWidget(0, self.welcome)
        self.verticalScrollBar().rangeChanged.connect(self._range_changed)
        self.verticalScrollBar().valueChanged.connect(self._value_changed)

    # -- content ------------------------------------------------------------------- #

    def set_welcome(self, title: str, text: str = "") -> None:
        self.welcome_title.setText(title)
        self.welcome_text.setText(text)
        self._sync_welcome()

    def _sync_welcome(self) -> None:
        self.welcome.setVisible(not (self.bubbles or self.cards))

    def add(self, role: str, text: str = "") -> MessageBubble:
        """Append a bubble and return it, so that a reply can be extended while it streams in."""
        bubble = MessageBubble(role, text)
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        if role == USER:
            row.addStretch(1)
        row.addWidget(bubble, 1 if role != USER else 0)
        if role == USER:
            pass
        elif role == NOTE:
            row.addStretch(0)
        self._column.insertLayout(self._column.count() - 1, row)
        self.bubbles.append(bubble)
        self._rows[bubble] = row
        self._limit_width(bubble)
        self._follow = True
        self._sync_welcome()
        return bubble

    def add_card(self, card: QWidget) -> QWidget:
        """Append a widget of Clara's (a QCM form) in a row of its own, beside the width of her text."""
        row = QHBoxLayout()
        row.setContentsMargins(PORTRAIT + 12, 0, 0, 0)
        row.addWidget(card)
        row.addStretch(1)
        self._column.insertLayout(self._column.count() - 1, row)
        self.cards.append(card)
        self._rows[card] = row
        self._limit_card(card)
        self._follow = True
        self._sync_welcome()
        return card

    def remove(self, bubble: MessageBubble) -> None:
        self._take(bubble)
        self.bubbles.remove(bubble)
        self._sync_welcome()

    def _take(self, widget: QWidget) -> None:
        row = self._rows.pop(widget)
        self._column.removeItem(row)
        widget.hide()  # gone now, not only when Qt gets round to deleting it
        widget.deleteLater()
        row.deleteLater()

    def clear(self) -> None:
        for bubble in list(self.bubbles):
            self.remove(bubble)
        for card in self.cards:
            self._take(card)
        self.cards.clear()
        self._sync_welcome()

    def texts(self) -> list[tuple[str, str]]:
        return [(bubble.role, bubble.text) for bubble in self.bubbles]

    # -- layout -------------------------------------------------------------------- #

    def _column_width(self) -> int:
        return min(max(self.viewport().width() - 48, 240), COLUMN)

    def _limit_width(self, bubble: MessageBubble) -> None:
        width = self._column_width()
        bubble.setMaximumWidth(int(width * USER_SHARE) if bubble.role == USER else width)

    def _limit_card(self, card: QWidget) -> None:
        card.setMaximumWidth(max(self._column_width() - PORTRAIT - 12, 200))

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        for bubble in self.bubbles:
            self._limit_width(bubble)
        for card in self.cards:
            self._limit_card(card)

    # -- following new text ----------------------------------------------------------- #

    def _value_changed(self, value: int) -> None:
        self._follow = value >= self.verticalScrollBar().maximum() - STICK_MARGIN

    def _range_changed(self, _low: int, high: int) -> None:
        if self._follow:
            self.verticalScrollBar().setValue(high)
