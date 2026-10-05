"""The month calendar of the Tasks page, as on the web site: each day shows the deadlines and the reminders still to come,
and the day you click lists them below the month."""

from __future__ import annotations

import calendar
from datetime import date, datetime

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QMouseEvent
from PySide6.QtWidgets import QFrame, QGridLayout, QHBoxLayout, QLabel, QPushButton, QSizePolicy, QToolButton, QVBoxLayout, QWidget

from .icons import bind_icon
from .widgets import Panel, button, label

CHIPS = 2  # events written in a day before "+ n more"


def day_of(text: str) -> date:
    return datetime.fromisoformat(text).astimezone().date()


def events_of(tasks: list[dict]) -> dict[date, list[dict]]:
    """The deadlines and the reminders to come of the tasks, by the day they fall on: `time`, `title`, `kind`, `id`, `done`."""
    found: dict[date, list[dict]] = {}
    for task in tasks:
        done = task["status"] == "done"
        if task.get("due_at"):
            found.setdefault(day_of(task["due_at"]), []).append({"time": task["due_at"], "title": task["title"], "kind": "due", "id": task["id"], "done": done})
        if not done:
            for at in task.get("reminders", []):
                found.setdefault(day_of(at), []).append({"time": at, "title": task["title"], "kind": "reminder", "id": task["id"], "done": False})
    for events in found.values():
        events.sort(key=lambda event: event["time"])
    return found


class DayCell(QFrame):
    clicked = Signal()

    def __init__(self):
        super().__init__()
        self.setObjectName("cal-cell")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setMinimumHeight(92)

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()
        super().mousePressEvent(event)


class CalendarView(QWidget):
    task_clicked = Signal(int)

    def __init__(self):
        super().__init__()
        today = date.today()
        self.month = today.replace(day=1)
        self.selected = today
        self.tasks: list[dict] = []
        self.cells: dict[date, DayCell] = {}

        previous = QToolButton()
        previous.setToolTip("Previous month")
        bind_icon(previous, "back", "muted", 18)
        previous.clicked.connect(lambda: self.move(-1))
        following = QToolButton()
        following.setToolTip("Next month")
        bind_icon(following, "chevron", "muted", 18)
        following.clicked.connect(lambda: self.move(1))
        self.title = label("", heading="section")
        self.title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        bar = QHBoxLayout()
        bar.setContentsMargins(14, 10, 14, 10)
        bar.addWidget(previous)
        bar.addWidget(self.title, 1)
        bar.addWidget(following)
        bar.addWidget(button("Today", "", self.today))

        self.grid = QGridLayout()
        self.grid.setSpacing(0)
        self.grid.setContentsMargins(0, 0, 0, 0)
        panel = Panel()
        panel.body.setContentsMargins(0, 0, 0, 0)
        panel.body.setSpacing(0)
        panel.add(bar)
        holder = QWidget()
        holder.setLayout(self.grid)
        panel.add(holder)
        self.agenda = Panel("")
        self.agenda_title = label("", heading="section")
        self.agenda.body.setSpacing(2)
        column = QVBoxLayout(self)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(16)
        column.addWidget(panel)
        column.addWidget(self.agenda)
        self.redraw()

    def set_tasks(self, tasks: list[dict]) -> None:
        self.tasks = tasks
        self.redraw()

    def move(self, months: int) -> None:
        index = self.month.year * 12 + self.month.month - 1 + months
        self.month = date(index // 12, index % 12 + 1, 1)
        self.redraw()

    def today(self) -> None:
        self.selected = date.today()
        self.month = self.selected.replace(day=1)
        self.redraw()

    def select(self, day: date) -> None:
        self.selected = day
        if (day.year, day.month) != (self.month.year, self.month.month):
            self.month = day.replace(day=1)
        self.redraw()

    def redraw(self) -> None:
        while self.grid.count():
            item = self.grid.takeAt(0)
            if item.widget() is not None:
                item.widget().hide()
                item.widget().deleteLater()
        self.cells = {}
        self.title.setText(self.month.strftime("%B %Y"))
        for column, name in enumerate(calendar.day_abbr):
            head = label(name, tone_="muted")
            head.setAlignment(Qt.AlignmentFlag.AlignCenter)
            head.setMinimumHeight(30)
            self.grid.addWidget(head, 0, column)
        events = events_of(self.tasks)
        today = date.today()
        for row, week in enumerate(calendar.Calendar(0).monthdatescalendar(self.month.year, self.month.month), 1):
            for column, day in enumerate(week):
                cell = DayCell()
                cell.setProperty("other", day.month != self.month.month)
                cell.setProperty("selected", day == self.selected)
                cell.setProperty("today", day == today)
                inside = QVBoxLayout(cell)
                inside.setContentsMargins(6, 4, 6, 4)
                inside.setSpacing(2)
                number = QLabel(str(day.day))
                number.setObjectName("cal-num")
                number.setProperty("today", day == today)
                number.setAlignment(Qt.AlignmentFlag.AlignCenter)
                number.setFixedSize(26, 26)
                inside.addWidget(number, 0, Qt.AlignmentFlag.AlignLeft)
                todays = events.get(day, [])
                for event in todays[:CHIPS]:
                    chip = QLabel(f"{datetime.fromisoformat(event['time']).astimezone():%H:%M} {event['title']}")
                    chip.setObjectName("cal-chip")
                    chip.setProperty("kind", event["kind"])
                    chip.setProperty("done", event["done"])
                    chip.setToolTip(event["title"])
                    chip.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)  # a long title is cut, it does not widen the day
                    chip.setMinimumWidth(0)
                    inside.addWidget(chip)
                if len(todays) > CHIPS:
                    inside.addWidget(label(f"+ {len(todays) - CHIPS} more", tone_="muted"))
                inside.addStretch(1)
                cell.clicked.connect(lambda _=False, d=day: self.select(d))
                self.cells[day] = cell
                self.grid.addWidget(cell, row, column)
        for column in range(7):
            self.grid.setColumnStretch(column, 1)
        self._draw_agenda(events.get(self.selected, []))

    def _draw_agenda(self, events: list[dict]) -> None:
        body = self.agenda.body
        while body.count():
            item = body.takeAt(0)
            if item.widget() is not None:
                item.widget().hide()
                item.widget().deleteLater()
        body.addWidget(label(f"{self.selected:%A %d %B %Y}", heading="section"))
        if not events:
            body.addWidget(label("Nothing planned this day.", tone_="muted"))
            return
        for event in events:
            moment = datetime.fromisoformat(event["time"]).astimezone()
            what = "Due" if event["kind"] == "due" else "Reminder"
            row = QPushButton(f"{moment:%H:%M}    {event['title']}      {what}")
            row.setProperty("kind", "ghost")
            row.setCursor(Qt.CursorShape.PointingHandCursor)
            row.setStyleSheet("text-align: left; padding: 8px 6px;")
            row.clicked.connect(lambda _=False, i=event["id"]: self.task_clicked.emit(i))
            body.addWidget(row)

