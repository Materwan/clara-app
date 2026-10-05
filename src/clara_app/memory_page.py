"""The Memory page: what Clara remembers about you, to add to or to take away from. The same facts as on the web site,
in the terminal and on Discord: the memory follows the person, whatever the surface."""

from __future__ import annotations

from typing import Callable

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QHBoxLayout, QLabel, QLineEdit, QToolButton, QVBoxLayout, QWidget

from .api import ClaraApi
from .config import Config
from .icons import bind_icon
from .widgets import Calls, Column, EmptyState, Page, Panel, StatusLine, button, divider, label

MAX_FACT = 300


class MemoryPage(Page, Calls):
    page_title = "Memory"

    def __init__(self, get_config: Callable[[], Config], api_factory: Callable[[Config], ClaraApi], host=None):
        super().__init__()
        self._init_calls(get_config, api_factory)
        self.facts: list[dict] = []
        self.loaded = False

        self.add_field = QLineEdit()
        self.add_field.setMaxLength(MAX_FACT)
        self.add_field.setPlaceholderText("Something Clara should remember about you")
        self.add_field.returnPressed.connect(self.add)
        self.add_button = button("Remember", "primary", self.add)
        self.filter = QLineEdit()
        self.filter.setClearButtonEnabled(True)
        self.filter.setPlaceholderText("Search what Clara remembers")
        self.filter.textChanged.connect(self._draw)
        self.count = label("", tone_="muted")
        self.status = StatusLine()
        self.list_panel = Panel()
        self.list_panel.body.setContentsMargins(0, 0, 0, 0)
        self.list_panel.body.setSpacing(0)

        column = Column()
        column.add(label("Clara picks up facts about you as you talk, and uses them in every conversation, on all your devices. "
                         "Add what she should know, or remove what she shouldn't.", tone_="muted", wrap=True))
        add_panel = Panel()
        row = QHBoxLayout()
        row.addWidget(self.add_field, 1)
        row.addWidget(self.add_button)
        add_panel.add(row)
        column.add(add_panel)
        bar = QHBoxLayout()
        bar.addWidget(self.filter, 1)
        bar.addWidget(self.count)
        holder = QWidget()
        holder.setLayout(bar)
        bar.setContentsMargins(0, 0, 0, 0)
        column.add(holder)
        column.add(self.list_panel)
        column.add(self.status)
        column.finish()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(column)

    def activated(self) -> None:
        if self._get_config().ready:
            self._call(lambda api: api.facts(), self._listed)

    def shutdown(self) -> None:
        self.stop_calls()

    def _listed(self, facts: object, error: str) -> None:
        if error:
            self.status.say(error, bad=True)
            return
        self.facts = list(facts)  # type: ignore[arg-type]
        self.loaded = True
        self.status.say("")
        self._draw()

    def _draw(self) -> None:
        body = self.list_panel.body
        while body.count():
            item = body.takeAt(0)
            if item.widget() is not None:
                item.widget().hide()
                item.widget().deleteLater()
        needle = self.filter.text().strip().lower()
        shown = [fact for fact in self.facts if needle in fact["text"].lower()]
        if not self.loaded:
            self.count.setText("")
            return
        self.count.setText(
            f"{len(shown)} of {len(self.facts)}" if needle else f"{len(self.facts)} {'thing' if len(self.facts) == 1 else 'things'}"
        )
        if not self.facts:
            body.addWidget(EmptyState("Clara doesn't know anything about you yet", "Tell her about yourself in a chat, or add something above."))
            return
        if not shown:
            body.addWidget(label("Nothing matches your search.", tone_="muted"))
            return
        for position, fact in enumerate(shown):
            if position:
                body.addWidget(divider())
            body.addWidget(self._row(fact))

    def _row(self, fact: dict) -> QWidget:
        row = QWidget()
        line = QHBoxLayout(row)
        line.setContentsMargins(18, 8, 10, 8)
        line.setSpacing(12)
        dot = QLabel("•")
        dot.setProperty("tone", "muted")
        text = label(fact["text"], wrap=True, selectable=True)
        forget = QToolButton()
        forget.setToolTip("Forget this")
        forget.setCursor(Qt.CursorShape.PointingHandCursor)
        bind_icon(forget, "trash", "muted", 18)
        forget.clicked.connect(lambda _=False, f=fact: self.forget(f))
        line.addWidget(dot)
        line.addWidget(text, 1)
        line.addWidget(forget)
        return row

    def add(self) -> None:
        text = self.add_field.text().strip()
        if not text:
            return

        def added(_result: object, error: str) -> None:
            if error:
                self.status.say(error, bad=True)
                return
            self.add_field.clear()
            self.status.say("Remembered.")
            self.activated()

        self._call(lambda api: api.add_fact(text), added)

    def forget(self, fact: dict) -> None:
        def forgotten(_result: object, error: str) -> None:
            if error:
                self.status.say(error, bad=True)
                return
            self.facts = [other for other in self.facts if other["id"] != fact["id"]]
            self.status.say("Forgotten.")
            self._draw()

        self._call(lambda api: api.delete_fact(fact["id"]), forgotten)
