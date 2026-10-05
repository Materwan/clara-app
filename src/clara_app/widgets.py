"""Small pieces the pages are made of, in the look of the web site: ruled panels, badges, notices, empty states, a
segmented control, the avatar. And `Calls`, the way every page talks to the server without freezing."""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Callable

from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import (
    QAbstractItemView,
    QButtonGroup,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QTableWidget,
    QVBoxLayout,
    QWidget,
)

from .api import ClaraApi
from .config import Config
from .icons import portrait_label
from .theme import set_property, tone
from .workers import CallWorker

CONTENT_WIDTH = 840  # a page of settings is a column this wide, in the middle


class Nav(QObject):
    """What the page headers and the shell tell each other: the rail is narrow-mode, or asked to open."""

    narrow_changed = Signal(bool)
    menu_requested = Signal()


NAV = Nav()


class Calls:
    """For a widget: run calls to the server off the UI thread, then `then(result, error)` on it.

    The widget must inherit QObject too (it is the parent of the workers) and call `stop_calls()` when it goes."""

    def _init_calls(self, get_config: Callable[[], Config], api_factory: Callable[[Config], ClaraApi]) -> None:
        self._get_config, self._api_factory = get_config, api_factory
        self._calls: list[CallWorker] = []
        self._calls_stopped = False

    def api(self) -> ClaraApi:
        return self._api_factory(self._get_config())

    def _call(self, call: Callable[[ClaraApi], object], then: Callable[[object, str], None] | None = None) -> CallWorker | None:
        if self._calls_stopped:
            return None
        api = self.api()
        worker = CallWorker(lambda: call(api), then or (lambda *_: None), self)  # type: ignore[arg-type]
        worker.done.connect(self._call_done)
        worker.finished.connect(self._call_finished)
        self._calls.append(worker)
        worker.start()
        return worker

    def _call_done(self, result: object, error: str) -> None:
        worker = self.sender()  # type: ignore[attr-defined]
        if not isinstance(worker, CallWorker):
            return
        then, worker.then = worker.then, None  # it refers to the widget: no cycle left behind
        if then is not None and not self._calls_stopped:
            then(result, error)

    def _call_finished(self) -> None:
        worker = self.sender()  # type: ignore[attr-defined]
        if worker in self._calls:
            worker.wait(2000)  # `finished` is sent just before the thread ends
            self._calls.remove(worker)
            worker.deleteLater()

    def stop_calls(self) -> None:
        self._calls_stopped = True
        for worker in list(self._calls):
            worker.then = None
            worker.wait(5000)


def label(text: str = "", *, heading: str = "", tone_: str = "", wrap: bool = False, selectable: bool = False) -> QLabel:
    widget = QLabel(text)
    if heading:
        widget.setProperty("heading", heading)
    if tone_:
        widget.setProperty("tone", tone_)
    widget.setWordWrap(wrap)
    if selectable:
        widget.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
    return widget


def button(text: str, kind: str = "", slot: Callable[..., object] | None = None) -> QPushButton:
    """A push button: kind is "primary", "danger", "ghost" or "" (outlined)."""
    widget = QPushButton(text)
    if kind:
        widget.setProperty("kind", kind)
    widget.setCursor(Qt.CursorShape.PointingHandCursor)
    if slot is not None:
        widget.clicked.connect(slot)
    return widget


def divider() -> QFrame:
    line = QFrame()
    line.setObjectName("panel-sep")
    line.setFrameShape(QFrame.Shape.NoFrame)
    return line


class Panel(QFrame):
    """A ruled section (a rule above, a hairline around), with an optional title and a line that says what it is for."""

    def __init__(self, title: str = "", subtitle: str = "", danger: bool = False):
        super().__init__()
        self.setObjectName("panel")
        if danger:
            self.setProperty("danger", True)
        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(0, 0, 0, 0)
        self._layout.setSpacing(0)
        self.head: QWidget | None = None
        if title:
            self.head = QWidget()
            head = QVBoxLayout(self.head)
            head.setContentsMargins(18, 14, 18, 12)
            head.setSpacing(2)
            self.title = label(title, heading="section")
            head.addWidget(self.title)
            if subtitle:
                head.addWidget(label(subtitle, tone_="muted", wrap=True))
            self._layout.addWidget(self.head)
            self._layout.addWidget(divider())
        self.body = QVBoxLayout()
        self.body.setContentsMargins(18, 14, 18, 16)
        self.body.setSpacing(10)
        holder = QWidget()
        holder.setLayout(self.body)
        self._layout.addWidget(holder)

    def add(self, widget: QWidget | QVBoxLayout | QHBoxLayout) -> None:
        if isinstance(widget, QWidget):
            self.body.addWidget(widget)
        else:
            self.body.addLayout(widget)


def badge(text: str, kind: str = "") -> QLabel:
    widget = QLabel(text)
    widget.setObjectName("badge")
    if kind:
        widget.setProperty("kind", kind)
    widget.setSizePolicy(widget.sizePolicy().horizontalPolicy(), widget.sizePolicy().verticalPolicy())
    return widget


class Notice(QFrame):
    """A line of help, or of trouble, in a tinted box."""

    def __init__(self, text: str = "", bad: bool = False):
        super().__init__()
        self.setObjectName("notice")
        row = QHBoxLayout(self)
        row.setContentsMargins(12, 8, 12, 8)
        self.text = label(text, wrap=True)
        row.addWidget(self.text)
        self.set_bad(bad)

    def set_bad(self, bad: bool) -> None:
        set_property(self, "tone", "bad" if bad else "")
        tone(self.text, "bad" if bad else None)

    def say(self, text: str, bad: bool = False) -> None:
        self.text.setText(text)
        self.set_bad(bad)
        self.setVisible(bool(text))


class StatusLine(QLabel):
    """The line under a page that says what just happened (in the colour of its meaning)."""

    def __init__(self):
        super().__init__("")
        self.setWordWrap(True)
        tone(self, "muted")

    def say(self, text: str, bad: bool = False) -> None:
        self.setText(text)
        tone(self, "bad" if bad else "muted")


class EmptyState(QWidget):
    """Clara's face, what is empty, and what to do about it."""

    def __init__(self, title: str, text: str = ""):
        super().__init__()
        column = QVBoxLayout(self)
        column.setContentsMargins(24, 28, 24, 28)
        column.setSpacing(6)
        column.addWidget(portrait_label(40), 0, Qt.AlignmentFlag.AlignHCenter)
        column.addSpacing(6)
        head = label(title, heading="section")
        head.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        column.addWidget(head)
        if text:
            body = label(text, tone_="muted", wrap=True)
            body.setAlignment(Qt.AlignmentFlag.AlignHCenter)
            column.addWidget(body)


class Segmented(QFrame):
    """A row of exclusive choices (To do / Done / All)."""

    chosen = Signal(str)

    def __init__(self, choices: list[tuple[str, str]], current: str):
        super().__init__()
        self.setObjectName("segmented")
        row = QHBoxLayout(self)
        row.setContentsMargins(3, 3, 3, 3)
        row.setSpacing(2)
        self._group = QButtonGroup(self)
        self._buttons: dict[str, QPushButton] = {}
        for key, text in choices:
            segment = QPushButton(text.replace("&", "&&"))  # an ampersand is not a shortcut here
            segment.setObjectName("segment")
            segment.setCheckable(True)
            segment.setCursor(Qt.CursorShape.PointingHandCursor)
            segment.setChecked(key == current)
            segment.clicked.connect(lambda _=False, k=key: self.chosen.emit(k))
            self._group.addButton(segment)
            self._buttons[key] = segment
            row.addWidget(segment)

    def select(self, key: str) -> None:
        if key in self._buttons:
            self._buttons[key].setChecked(True)


def avatar(name: str, size: int = 34) -> QLabel:
    """The violet badge with the first letter of someone's name."""
    widget = QLabel((name or "?").strip()[:1].upper() or "?")
    widget.setObjectName("avatar")
    widget.setAlignment(Qt.AlignmentFlag.AlignCenter)
    widget.setFixedSize(size, size)
    widget.setStyleSheet(f"font-size: {max(11, size * 2 // 5)}px;")
    return widget


class Column(QScrollArea):
    """A page of settings: a scrolling column of at most `CONTENT_WIDTH`, in the middle."""

    def __init__(self, wide: bool = False):
        super().__init__()
        self.setWidgetResizable(True)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        outer = QWidget()
        outer.setObjectName("scroll-body")
        row = QHBoxLayout(outer)
        row.setContentsMargins(20, 24, 20, 40)
        self.inner = QWidget()
        self.inner.setMaximumWidth(1120 if wide else CONTENT_WIDTH)
        self.column = QVBoxLayout(self.inner)
        self.column.setContentsMargins(0, 0, 0, 0)
        self.column.setSpacing(18)
        row.addStretch(1)
        row.addWidget(self.inner, 100)
        row.addStretch(1)
        self.setWidget(outer)
        self.add_stretch = False

    def add(self, widget: QWidget) -> QWidget:
        self.column.addWidget(widget)
        return widget

    def clear(self, keep: tuple[QWidget, ...] = ()) -> None:
        """Take everything out of the column and delete it, except the widgets in `keep` (which the page puts back)."""
        while self.column.count():
            item = self.column.takeAt(0)
            widget = item.widget()
            if widget is not None and widget not in keep:
                widget.hide()
                widget.deleteLater()

    def finish(self) -> None:
        self.column.addStretch(1)


class Page(QWidget):
    """What the shell shows: a title and some actions for the header, and the page below. Pages that talk to the server
    are also `Calls`."""

    title_changed = Signal(str)
    page_title = ""

    def set_title(self, text: str) -> None:
        self.page_title = text
        self.title_changed.emit(text)

    def actions(self) -> list[QWidget]:
        return []

    def activated(self) -> None:
        """The page is shown (again): read what may have changed."""

    def shutdown(self) -> None:
        """The window closes for good."""


# ---- tables, questions and numbers -------------------------------------------------------------------------------


class Table(QTableWidget):
    def put(self, row: int, column: int, widget: QWidget) -> None:
        """A widget in a cell, at the left and in the middle of the row (a cell widget would fill the whole cell)."""
        holder = QWidget()
        line = QHBoxLayout(holder)
        line.setContentsMargins(8, 2, 8, 2)
        line.addWidget(widget, 0, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        line.addStretch(1)
        self.setCellWidget(row, column, holder)


def make_table(headers: list[str], stretch: int = 0) -> Table:
    """A read-only table whose rows are filled by the page. Column `stretch` takes the room that is left."""
    table = Table(0, len(headers))
    table.setHorizontalHeaderLabels(headers)
    table.verticalHeader().hide()
    table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
    table.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
    table.setShowGrid(False)
    table.setFocusPolicy(Qt.FocusPolicy.NoFocus)
    table.verticalHeader().setDefaultSectionSize(46)
    header = table.horizontalHeader()
    header.setHighlightSections(False)
    header.setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
    header.setSectionResizeMode(stretch, QHeaderView.ResizeMode.Stretch)
    table.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
    table.setProperty("stretch", stretch)
    return table


def fit_table(table: QTableWidget) -> None:
    """Make the table as tall as its rows, so that the page scrolls and the table does not."""
    rows = table.rowCount()
    stretch = int(table.property("stretch") or 0)
    for column in range(table.columnCount()):  # cell widgets do not size their column: give each the room its widest needs
        if column == stretch:
            continue
        wanted = table.horizontalHeader().sectionSizeHint(column)
        for row in range(rows):
            widget = table.cellWidget(row, column)
            if widget is not None:
                wanted = max(wanted, widget.sizeHint().width() + 12)
        table.horizontalHeader().setSectionResizeMode(column, QHeaderView.ResizeMode.Fixed)
        table.setColumnWidth(column, wanted + 8)
    table.setFixedHeight(table.horizontalHeader().height() + 4 + sum(table.rowHeight(row) for row in range(rows)) + (30 if rows == 0 else 0))


def key_values(pairs: list[tuple[str, str]], columns: int = 2) -> QWidget:
    """Small labels over values, in a grid (the status of the server, of the bot)."""
    holder = QWidget()
    grid = QGridLayout(holder)
    grid.setContentsMargins(0, 0, 0, 0)
    grid.setHorizontalSpacing(24)
    grid.setVerticalSpacing(12)
    for index, (name, value) in enumerate(pairs):
        cell = QVBoxLayout()
        cell.setSpacing(1)
        cell.addWidget(label(name, tone_="muted"))
        cell.addWidget(label(value, heading="small", wrap=True, selectable=True))
        grid.addLayout(cell, index // columns, index % columns)
    return holder


def confirm(parent: QWidget | None, title: str, text: str, ok: str = "OK", danger: bool = False) -> bool:
    box = QMessageBox(QMessageBox.Icon.Question, title, text, parent=parent)
    yes = box.addButton(ok, QMessageBox.ButtonRole.AcceptRole)
    if danger:
        yes.setProperty("kind", "danger")
    box.addButton("Cancel", QMessageBox.ButtonRole.RejectRole)
    box.setDefaultButton(box.buttons()[-1])
    box.exec()
    return box.clickedButton() is yes


def ask_text(parent: QWidget | None, title: str, prompt: str, value: str = "", ok: str = "Save", password: bool = False, hint: str = "") -> str | None:
    """A line of text from the user (None: cancelled)."""
    dialog = QDialog(parent)
    dialog.setWindowTitle(title)
    dialog.setMinimumWidth(420)
    field = QLineEdit(value)
    if password:
        field.setEchoMode(QLineEdit.EchoMode.Password)
    form = QFormLayout()
    form.addRow(prompt, field)
    buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel)
    save = buttons.addButton(ok, QDialogButtonBox.ButtonRole.AcceptRole)
    save.setProperty("kind", "primary")
    buttons.accepted.connect(dialog.accept)
    buttons.rejected.connect(dialog.reject)
    layout = QVBoxLayout(dialog)
    layout.addLayout(form)
    if hint:
        layout.addWidget(label(hint, tone_="muted", wrap=True))
    layout.addWidget(buttons)
    field.returnPressed.connect(dialog.accept)
    return field.text() if dialog.exec() == QDialog.DialogCode.Accepted else None


def secret_dialog(parent: QWidget | None, title: str, intro: str, secret: str) -> None:
    """A password handed out once: shown big, with a copy button."""
    dialog = QDialog(parent)
    dialog.setWindowTitle(title)
    dialog.setMinimumWidth(420)
    box = QLabel(secret)
    box.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
    box.setStyleSheet("font-family: Consolas, monospace; font-size: 18px; padding: 10px 12px; border: 1px dashed palette(mid); border-radius: 6px;")
    copy = button("Copy", "", lambda: QGuiApplication.clipboard().setText(secret))
    done = button("Done", "primary", dialog.accept)
    row = QHBoxLayout()
    row.addStretch(1)
    row.addWidget(copy)
    row.addWidget(done)
    layout = QVBoxLayout(dialog)
    layout.addWidget(label(intro, wrap=True))
    layout.addWidget(box)
    layout.addWidget(label("It is shown only now: copy it before closing.", tone_="muted"))
    layout.addLayout(row)
    dialog.exec()


def token_count(tokens: int | float) -> str:
    return f"{int(tokens):,}"


def parse_credits(text: str) -> int | None:
    """What a person typed as a daily limit (`500000`, `500k`, `2m`, `off`) as whole credits (0: no limit), or None."""
    word = re.sub(r"[_,\s]", "", text.strip().lower())
    if word in ("off", "none", "no", "unlimited", "0"):
        return 0
    found = re.fullmatch(r"(\d+(?:\.\d+)?)([km]?)", word)
    if not found:
        return None
    exact = float(found.group(1)) * (1_000_000 if found.group(2) == "m" else 1_000 if found.group(2) == "k" else 1)
    credits = round(exact)
    return credits if abs(exact - credits) < 1e-6 and 1 <= credits <= 10**13 else None


def parse_time(text: str | None) -> datetime | None:
    if not text:
        return None
    try:
        moment = datetime.fromisoformat(text)
    except ValueError:
        return None
    return moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)


def ago(text: str | None) -> str:
    moment = parse_time(text)
    if moment is None:
        return "never"
    seconds = int((datetime.now(timezone.utc) - moment).total_seconds())
    if seconds < 60:
        return "just now"
    for name, size in (("year", 31536000), ("month", 2592000), ("day", 86400), ("hour", 3600), ("minute", 60)):
        if seconds >= size:
            count = seconds // size
            return f"{count} {name}{'' if count == 1 else 's'} ago"
    return "just now"


def date_time(text: str | None) -> str:
    moment = parse_time(text)
    return moment.astimezone().strftime("%Y-%m-%d %H:%M") if moment else "never"


def duration(seconds: float) -> str:
    minutes = int(seconds // 60)
    hours, days = minutes // 60, minutes // 1440
    if days:
        return f"{days}d {hours % 24}h"
    return f"{hours}h {minutes % 60:02d}m" if hours else f"{minutes}m"
