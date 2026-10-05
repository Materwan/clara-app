"""The conversation: a scrolling column of message bubbles whose text is rendered as Markdown."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QScrollArea, QVBoxLayout, QWidget

USER, CLARA, NOTE, ERROR = "user", "clara", "note", "error"
BUBBLE_SHARE = 0.84  # widest a bubble gets, as a share of the window
STICK_MARGIN = 24  # pixels from the bottom within which the view keeps following new text

STYLE = """
QFrame#bubble[role="user"] { background: #3d5afe; border-radius: 12px; }
QFrame#bubble[role="user"] QLabel { color: white; }
QFrame#bubble[role="clara"] { background: palette(alternate-base); border: 1px solid palette(midlight); border-radius: 12px; }
QFrame#bubble[role="clara"] QLabel { color: palette(text); }
QFrame#bubble[role="note"] { background: transparent; }
QFrame#bubble[role="note"] QLabel { color: palette(placeholder-text); font-size: 12px; }
QFrame#bubble[role="error"] { background: #fdecea; border: 1px solid #f5c2bd; border-radius: 12px; }
QFrame#bubble[role="error"] QLabel { color: #8a1c13; }
"""


class MessageBubble(QFrame):
    def __init__(self, role: str, text: str = ""):
        super().__init__()
        self.setObjectName("bubble")
        self.setProperty("role", role)
        self.role = role
        self.text = text
        self.label = QLabel()
        self.label.setTextFormat(Qt.TextFormat.MarkdownText)
        self.label.setWordWrap(True)
        self.label.setOpenExternalLinks(True)
        self.label.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse | Qt.TextInteractionFlag.LinksAccessibleByMouse
        )
        padding = 4 if role == NOTE else 10
        layout = QVBoxLayout(self)
        layout.setContentsMargins(padding + 2, padding, padding + 2, padding)
        layout.addWidget(self.label)
        self.set_text(text)

    def set_text(self, text: str) -> None:
        self.text = text
        self.label.setText(text if text else "…")  # a reply still being awaited shows as "…"


class ChatView(QScrollArea):
    def __init__(self):
        super().__init__()
        self.setWidgetResizable(True)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setStyleSheet(STYLE)
        self._inner = QWidget()
        self._column = QVBoxLayout(self._inner)
        self._column.setContentsMargins(12, 12, 12, 12)
        self._column.setSpacing(8)
        self._column.addStretch(1)
        self.setWidget(self._inner)
        self.bubbles: list[MessageBubble] = []
        self.cards: list[QWidget] = []  # forms (QCM) shown between the bubbles
        self._rows: dict[QWidget, QHBoxLayout] = {}
        self._follow = True
        self.verticalScrollBar().rangeChanged.connect(self._range_changed)
        self.verticalScrollBar().valueChanged.connect(self._value_changed)

    # -- content ------------------------------------------------------------------- #

    def add(self, role: str, text: str = "") -> MessageBubble:
        """Append a bubble and return it, so that a reply can be extended while it streams in."""
        bubble = MessageBubble(role, text)
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        if role in (USER, NOTE):
            row.addStretch(1)
        row.addWidget(bubble)
        if role in (CLARA, ERROR, NOTE):
            row.addStretch(1)
        self._column.insertLayout(self._column.count() - 1, row)
        self.bubbles.append(bubble)
        self._rows[bubble] = row
        self._limit_width(bubble)
        self._follow = True
        return bubble

    def add_card(self, card: QWidget) -> QWidget:
        """Append a widget of Clara's (a QCM form) in a row of its own, as wide as a bubble."""
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.addWidget(card)
        row.addStretch(1)
        self._column.insertLayout(self._column.count() - 1, row)
        self.cards.append(card)
        self._rows[card] = row
        self._limit_card(card)
        self._follow = True
        return card

    def remove(self, bubble: MessageBubble) -> None:
        self._take(bubble)
        self.bubbles.remove(bubble)

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

    def texts(self) -> list[tuple[str, str]]:
        return [(bubble.role, bubble.text) for bubble in self.bubbles]

    # -- layout -------------------------------------------------------------------- #

    def _limit_width(self, bubble: MessageBubble) -> None:
        width = max(self.viewport().width(), 240)
        bubble.setMaximumWidth(max(width - 40, 200) if bubble.role == NOTE else int(width * BUBBLE_SHARE))

    def _limit_card(self, card: QWidget) -> None:
        card.setMaximumWidth(max(int(max(self.viewport().width(), 240) * BUBBLE_SHARE), 200))

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
