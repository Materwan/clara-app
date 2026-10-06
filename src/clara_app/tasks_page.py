"""The Tasks page: your to-do list, with the reminders still to come of each task.

The list is kept by the server and belongs to the user, so it is the same here, on the web site, in the terminal and in
every chat with Clara. Clara picks the reminders of a task given none, and moves the next ones each time one is sent:
the numbers shown here change by themselves, so the page reads the list again from time to time. Every call runs off
the UI thread (workers.py); the page only shows what the server answers.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Callable

from PySide6.QtCore import QDate, QDateTime, Qt, QTime, QTimer, Signal
from PySide6.QtCore import QRect, QSize
from PySide6.QtGui import QColor, QFont, QPainter, QPen
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDateTimeEdit,
    QDialog,
    QStackedWidget,
    QStyledItemDelegate,
    QStyleOptionViewItem,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from .api import ClaraApi
from .config import Config
from .calendar_view import CalendarView
from .theme import THEME, tone
from .widgets import Page, Segmented, button as push, label
from .workers import CallWorker

ID = Qt.ItemDataRole.UserRole
REFRESH_MS = 30_000  # reminders are sent while the dialog is open: look again from time to time
FORMAT = "yyyy-MM-dd HH:mm"
FILTERS = (("To do", "open"), ("Done", "done"), ("All", "all"))


def local(text: str) -> str:
    """A moment from the server (ISO, with its offset) as the computer's clock shows it."""
    return datetime.fromisoformat(text).astimezone().strftime("%Y-%m-%d %H:%M")


def plural(count: int, word: str) -> str:
    return f"{count} {word}{'' if count == 1 else 's'}"


def to_qt(text: str) -> QDateTime:
    moment = datetime.fromisoformat(text).astimezone()
    return QDateTime(QDate(moment.year, moment.month, moment.day), QTime(moment.hour, moment.minute))


def to_iso(moment: QDateTime) -> str:
    """The moment as the computer's clock reads it, with the offset the server needs."""
    return moment.toPython().replace(second=0, microsecond=0).astimezone().isoformat(timespec="seconds")


def latest(*moments: str | None) -> str | None:
    """The earliest of some moments from the server (ISO), or None when there is none."""
    given = [m for m in moments if m]
    return min(given, key=datetime.fromisoformat) if given else None


def summary(task: dict) -> str:
    """The second line of a task in the list: what was sent, and what comes next."""
    sent = plural(task["reminders_sent"], "reminder") + " sent"
    progress = task.get("subtasks") or {}
    if progress.get("total"):
        sent = f"{progress['done']}/{progress['total']} sub tasks · {sent}"
    if task["status"] == "done":
        return f"done · {sent}"
    overdue = task.get("due_at") and datetime.fromisoformat(task["due_at"]) < datetime.now().astimezone()
    due = f" · {'overdue, was due' if overdue else 'due'} {local(task['due_at'])}" if task.get("due_at") else ""
    next_reminder = f"next reminder {local(task['next_reminder'])}" if task.get("next_reminder") else "no reminder to come"
    return f"{sent} · {next_reminder}{due}"


class TaskRows(QStyledItemDelegate):
    """Draws a task as the web site does: a round box to tick, its title, and what is known about its reminders."""

    BOX = 22
    INDENT = 28  # per level of sub task

    def sizeHint(self, option: QStyleOptionViewItem, index) -> QSize:
        return QSize(option.rect.width(), 74)

    def box_rect(self, row: QRect, depth: int = 0) -> QRect:
        return QRect(row.left() + 16 + depth * self.INDENT, row.top() + 14, self.BOX, self.BOX)

    def paint(self, painter: QPainter, option: QStyleOptionViewItem, index) -> None:
        t = THEME.t
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = option.rect
        selected = bool(option.state & option.state.State_Selected) if hasattr(option.state, "State_Selected") else False
        hover = bool(option.state & option.state.State_MouseOver) if hasattr(option.state, "State_MouseOver") else False
        if hover or selected:
            painter.fillRect(rect, THEME.qcolor("hover"))
        painter.setPen(QPen(THEME.qcolor("line"), 1))
        painter.drawLine(rect.left(), rect.bottom(), rect.right(), rect.bottom())
        title, _, meta = str(index.data(Qt.ItemDataRole.DisplayRole)).partition("\n")
        done = bool(index.data(ID + 1))
        box = self.box_rect(rect, int(index.data(ID + 2) or 0))
        painter.setPen(QPen(QColor(t["text"] if done else t["line_strong"]), 2))
        painter.setBrush(QColor(t["text"]) if done else Qt.BrushStyle.NoBrush)
        painter.drawEllipse(box)
        if done:
            painter.setPen(QPen(QColor(t["bg"]), 2.2))
            painter.drawLine(box.left() + 6, box.center().y(), box.left() + 10, box.bottom() - 6)
            painter.drawLine(box.left() + 10, box.bottom() - 6, box.right() - 5, box.top() + 7)
        left = box.right() + 14
        font = QFont(option.font)
        font.setPixelSize(16)
        font.setWeight(QFont.Weight.DemiBold)
        font.setStrikeOut(done)
        painter.setFont(font)
        painter.setPen(QColor(t["muted"] if done else t["text"]))
        painter.drawText(QRect(left, rect.top() + 11, rect.right() - left - 12, 24), Qt.AlignmentFlag.AlignVCenter | Qt.TextFlag.TextSingleLine, title)
        small = QFont(option.font)
        small.setPixelSize(13)
        painter.setFont(small)
        painter.setPen(QColor(t["muted"]))
        painter.drawText(QRect(left, rect.top() + 38, rect.right() - left - 12, 22), Qt.AlignmentFlag.AlignVCenter | Qt.TextFlag.TextSingleLine, meta)
        painter.restore()


class TasksPage(Page):
    changed = Signal()  # a task was added, changed or deleted

    page_title = "Tasks"

    def __init__(
        self,
        get_config: Callable[[], Config],
        api_factory: Callable[[Config], ClaraApi] = ClaraApi,
        host=None,
    ):
        super().__init__()
        self._host = host
        self._get_config, self._api_factory = get_config, api_factory
        self.tasks: list[dict] = []  # every task, as the server describes them
        self.task: dict | None = None  # the one shown in the form (None: a new one)
        self.limit = 10  # reminders the server sends for one task at most
        self._calls: list[CallWorker] = []
        self._loading = False  # the form is being filled: its edits are not the user's
        self._changes = 0  # counts the changes made here: a list asked for before the last one is out of date
        self._list_failed = False  # the status line shows that the list could not be read
        self._due_touched = False
        self._reminders_touched = False
        self.parent_task: dict | None = None  # the task a new sub task is made for (None: a main task, or one that exists)
        self._limit: str | None = None  # the latest moment a deadline or reminder of the task in the form may have

        intro = QLabel(
            "Your to-do list, the same on the web site and in every chat with Clara. Each task has reminders: choose "
            "them, or leave it to Clara, who also moves the next ones each time one is sent. A task can be divided "
            "into sub tasks, each with its own reminders and none of them later than the task's deadline."
        )
        intro.setWordWrap(True)
        tone(intro, "muted")

        self.filter = QComboBox()
        for label, status in FILTERS:
            self.filter.addItem(label, status)
        self.filter.currentIndexChanged.connect(lambda *_: self._fill_list())
        self.filter.hide()  # the choice the page keeps; the segments below set it
        self.list = QListWidget()
        self.list.setItemDelegate(TaskRows(self.list))
        self.list.setMouseTracking(True)
        self.list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.list.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.list.currentItemChanged.connect(self._picked)
        self.list.viewport().installEventFilter(self)
        self.new_button = push("New task", "primary", self.start_new)
        self.count = QLabel("")
        tone(self.count, "muted")
        self.calendar = CalendarView()
        self.calendar.task_clicked.connect(self.edit_task)
        self.filters = Segmented([(status, text) for text, status in FILTERS], "open")
        self.filters.chosen.connect(lambda status: self.filter.setCurrentIndex(self.filter.findData(status)))
        self.views = Segmented([("list", "List"), ("calendar", "Calendar")], "list")
        self.views.chosen.connect(self._view)

        self.title = QLineEdit()
        self.title.setMaxLength(200)
        self.title.setPlaceholderText("What has to be done")
        self.description = QPlainTextEdit()
        self.description.setPlaceholderText("Details, if any")
        self.description.setFixedHeight(80)
        self.has_due = QCheckBox("Deadline")
        self.due = QDateTimeEdit()
        self.due.setCalendarPopup(True)
        self.due.setDisplayFormat(FORMAT)
        self.due.setEnabled(False)
        self.has_due.toggled.connect(self._due_toggled)
        self.due.dateTimeChanged.connect(self._due_edited)
        due_row = QHBoxLayout()
        due_row.addWidget(self.has_due)
        due_row.addWidget(self.due, 1)
        self.reminders = QListWidget()
        self.reminders.setFixedHeight(96)
        self.reminders.setToolTip("The reminders to come: select one to remove it")
        self.reminders.currentItemChanged.connect(lambda *_: self._enable())
        self.reminder_time = QDateTimeEdit()
        self.reminder_time.setCalendarPopup(True)
        self.reminder_time.setDisplayFormat(FORMAT)
        self.add_reminder_button = QPushButton("Add")
        self.add_reminder_button.clicked.connect(self.add_reminder)
        self.remove_reminder_button = QPushButton("Remove")
        self.remove_reminder_button.clicked.connect(self.remove_reminder)
        reminder_row = QHBoxLayout()
        reminder_row.addWidget(self.reminder_time, 1)
        reminder_row.addWidget(self.add_reminder_button)
        reminder_row.addWidget(self.remove_reminder_button)
        reminders_box = QVBoxLayout()
        reminders_box.addWidget(self.reminders)
        reminders_box.addLayout(reminder_row)
        self.hint = QLabel("")
        self.hint.setWordWrap(True)
        tone(self.hint, "muted")
        self.info = QLabel("")
        self.info.setWordWrap(True)
        for field in (self.title, self.description):
            field.textChanged.connect(self._enable)

        form = QFormLayout()
        form.addRow("Title:", self.title)
        form.addRow("Description:", self.description)
        form.addRow("", due_row)
        form.addRow("Reminders:", reminders_box)
        self.save_button = push("Save", "primary", self.save)
        self.save_button.setDefault(True)
        self.done_button = push("Mark as done", "", self.toggle_done)
        self.sub_button = push("Add a sub task", "", self.start_sub)
        self.delete_button = push("Delete…", "danger", self.delete)
        buttons = QHBoxLayout()
        buttons.addWidget(self.delete_button)
        buttons.addWidget(self.done_button)
        buttons.addWidget(self.sub_button)
        buttons.addStretch(1)
        buttons.addWidget(self.save_button)
        self.status = QLabel("")
        self.status.setWordWrap(True)
        tone(self.status, "muted")

        # the form is a dialog, as on the web site: opened by a task, or by "New task"
        self.form_dialog = QDialog(self)
        self.form_dialog.setMinimumWidth(520)
        self.detail = QWidget()
        right = QVBoxLayout(self.detail)
        right.setContentsMargins(0, 0, 0, 0)
        right.setSpacing(12)
        right.addLayout(form)
        right.addWidget(self.hint)
        right.addWidget(self.info)
        right.addLayout(buttons)
        outer = QVBoxLayout(self.form_dialog)
        outer.addWidget(self.detail)
        self.form_dialog.finished.connect(self._form_closed)

        self.list_panel = QWidget()
        panel = QVBoxLayout(self.list_panel)
        panel.setContentsMargins(0, 0, 0, 0)
        panel.addWidget(self.list)
        panel.addStretch(1)
        self.empty = QLabel("")
        self.empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        tone(self.empty, "muted")
        self.stack = QStackedWidget()
        self.stack.addWidget(self.list_panel)
        calendar_holder = QWidget()
        calendar_column = QVBoxLayout(calendar_holder)
        calendar_column.setContentsMargins(0, 0, 0, 0)
        calendar_column.addWidget(self.calendar)
        calendar_column.addStretch(1)
        self.stack.addWidget(calendar_holder)

        toolbar = QHBoxLayout()
        toolbar.addWidget(self.filters)
        toolbar.addWidget(self.views)
        toolbar.addStretch(1)
        toolbar.addWidget(self.count)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 18, 24, 16)
        layout.setSpacing(14)
        layout.addWidget(intro)
        layout.addLayout(toolbar)
        layout.addWidget(self.stack, 1)
        layout.addWidget(self.empty)
        layout.addWidget(self.status)
        self.changed.connect(self.form_dialog.hide)

        self._timer = QTimer(self)
        self._timer.setInterval(REFRESH_MS)
        self._timer.timeout.connect(self.reload)
        self._timer.start()
        self._show(None)
        self.reload()

    # -- plumbing ------------------------------------------------------------------------- #

    @property
    def busy(self) -> bool:
        return any(getattr(worker, "saving", False) for worker in self._calls)

    def _call(self, call: Callable[[ClaraApi], object], then: Callable[[object, str], None], saving: bool = False) -> None:
        """Run `call(api)` in the background, then `then(result, error)` here."""
        api = self._api_factory(self._get_config())
        worker = CallWorker(lambda: call(api), then, self)
        worker.saving = saving  # a change is under way: the buttons wait for it
        worker.done.connect(self._call_done)
        worker.finished.connect(self._call_finished)
        self._calls.append(worker)
        worker.start()
        self._enable()

    def _call_done(self, result: object, error: str) -> None:
        worker = self.sender()
        if not isinstance(worker, CallWorker):
            return
        then, worker.then = worker.then, None
        worker.saving = False
        if then is not None:
            then(result, error)
        self._enable()

    def _call_finished(self) -> None:
        worker = self.sender()
        if worker in self._calls:
            worker.wait(2000)
            self._calls.remove(worker)
            worker.deleteLater()

    def _say(self, text: str, bad: bool = False) -> None:
        self.status.setText(text)
        tone(self.status, "bad" if bad else "muted")

    def actions(self) -> list[QWidget]:
        return [self.new_button]

    def eventFilter(self, watched, event) -> bool:
        """A click on the round box of a row ticks the task; a click elsewhere on it opens the task."""
        from PySide6.QtCore import QEvent

        if watched is self.list.viewport() and event.type() == QEvent.Type.MouseButtonRelease and event.button() == Qt.MouseButton.LeftButton:
            item = self.list.itemAt(event.position().toPoint())
            if item is not None:
                task = next((t for t in self.tasks if t["id"] == item.data(ID)), None)
                if task is not None:
                    depth = int(item.data(ID + 2) or 0)
                    box = self.list.itemDelegate().box_rect(self.list.visualItemRect(item), depth).adjusted(-6, -6, 6, 6)
                    if box.contains(event.position().toPoint()):
                        self._show(task)
                        self.toggle_done()
                    else:
                        self.edit_task(task["id"])
        return super().eventFilter(watched, event)

    def _view(self, key: str) -> None:
        self.stack.setCurrentIndex(0 if key == "list" else 1)

    def edit_task(self, task_id: int) -> None:
        """Open a task in the form."""
        task = next((t for t in self.tasks if t["id"] == task_id), None)
        if task is not None:
            self._show(task)
            self.form_dialog.setWindowTitle("Task")
            self.form_dialog.open()

    def _form_closed(self) -> None:
        self.list.clearSelection()

    def activated(self) -> None:
        self.reload()

    def shutdown(self) -> None:
        self._timer.stop()
        for worker in list(self._calls):
            worker.then = None
            worker.wait(5000)

    def _enable(self) -> None:
        busy = self.busy
        task = self.task
        is_open = task is None or task["status"] == "open"
        self.save_button.setEnabled(not busy and bool(self.title.text().strip()))
        self.done_button.setEnabled(task is not None and not busy)
        self.done_button.setText("Reopen" if task is not None and task["status"] == "done" else "Mark as done")
        self.sub_button.setEnabled(task is not None and task["status"] == "open" and not busy)
        self.delete_button.setEnabled(task is not None and not busy)
        self.new_button.setEnabled(not busy)
        for widget in (self.reminder_time, self.add_reminder_button):
            widget.setEnabled(is_open and not busy)
        self.remove_reminder_button.setEnabled(is_open and not busy and self.reminders.currentItem() is not None)
        self.reminders.setEnabled(is_open)

    # -- the list ------------------------------------------------------------------------- #

    def reload(self) -> None:
        if self.busy:
            return
        asked_at = self._changes

        def listed(found: object, error: str) -> None:
            if asked_at == self._changes:  # not if a task was changed meanwhile: that list is out of date
                self._listed(found, error)

        self._call(lambda api: api.tasks("all"), listed)

    def _listed(self, found: object, error: str) -> None:
        if error:
            self._list_failed = True
            self._say(error, bad=True)
            return
        self.tasks = found["tasks"]
        self.limit = found.get("max_reminders", self.limit)
        wanted = self.task["id"] if self.task else None
        self._fill_list(wanted)
        current = next((t for t in self.tasks if t["id"] == wanted), None)
        if wanted is not None and current is None:  # it was deleted elsewhere
            self._show(None)
        elif current is not None:
            if self._clean(current):
                self._show(current)  # Clara may have moved the reminders: nothing here is being edited
            else:
                self.task = current
                self._refresh_info()
        if self._list_failed:  # it can be read again: say nothing more about it
            self._list_failed = False
            self._say("")

    def _fill_list(self, select: int | None = None) -> None:
        status = self.filter.currentData()
        shown = [t for t in self.tasks if status == "all" or t["status"] == status]
        rows = self._rows(status)
        if select is None and self.task:
            select = self.task["id"]
        self.list.blockSignals(True)
        self.list.clear()
        chosen = None
        for task, depth in rows:
            item = QListWidgetItem(f"{task['title']}\n{summary(task)}")
            item.setData(ID, task["id"])
            item.setData(ID + 1, task["status"] == "done")
            item.setData(ID + 2, depth)
            self.list.addItem(item)
            if task["id"] == select:
                chosen = item
        if chosen is not None:
            self.list.setCurrentItem(chosen)
        self.list.blockSignals(False)
        open_count = sum(1 for t in self.tasks if t["status"] == "open")
        self.count.setText(plural(open_count, "open task") if self.tasks else "No task yet")
        self.list.setFixedHeight(74 * len(rows) + 2 if rows else 0)
        shown = rows
        self.empty.setText("" if shown else {"done": "Nothing is done yet. Finished tasks stay here.", "open": "No task yet. Add one with New task, or tell Clara in a chat: “add a task: send the invoice by Friday”."}.get(status, "No task yet."))
        self.empty.setVisible(not shown)
        self.calendar.set_tasks([t for t in self.tasks if status == "all" or t["status"] == status])

    def _kids(self, task_id: int) -> list[dict]:
        return sorted((t for t in self.tasks if t.get("parent_id") == task_id), key=lambda t: t["id"])

    def _below(self, task: dict) -> list[dict]:
        """Its sub tasks, theirs, and so on."""
        found: list[dict] = []
        for kid in self._kids(task["id"]):
            found.append(kid)
            found.extend(self._below(kid))
        return found

    def _rows(self, status: str) -> list[tuple[dict, int]]:
        """What the list draws, in order, with how deep each is: the main tasks that match the filter, each followed
        by its sub tasks."""
        known = {t["id"]: t for t in self.tasks}

        def is_root(task: dict) -> bool:
            parent = known.get(task.get("parent_id"))
            if status == "done":  # a done sub task of a task still to do is listed on its own
                return task["status"] == "done" and (parent is None or parent["status"] != "done")
            if status == "open":
                return task["status"] == "open" and parent is None
            return parent is None

        rows: list[tuple[dict, int]] = []

        def walk(task: dict, depth: int) -> None:
            rows.append((task, depth))
            for kid in self._kids(task["id"]):
                walk(kid, depth + 1)

        for task in self.tasks:
            if is_root(task):
                walk(task, 0)
        return rows

    def _clean(self, task: dict) -> bool:
        """Is the form as the server has the task (nothing typed, nothing chosen)?"""
        return (
            self.task is not None and self.task["id"] == task["id"] and not self._due_touched
            and not self._reminders_touched and self.title.text() == self.task["title"]
            and self.description.toPlainText() == self.task["description"]
        )

    def _picked(self, item: QListWidgetItem | None) -> None:
        if item is None:
            return
        task = next((t for t in self.tasks if t["id"] == item.data(ID)), None)
        if task is not None:
            self._show(task)

    # -- the form ------------------------------------------------------------------------- #

    def start_new(self) -> None:
        self.list.blockSignals(True)
        self.list.setCurrentItem(None)
        self.list.blockSignals(False)
        self._show(None)
        self.form_dialog.setWindowTitle("New task")
        self.form_dialog.open()
        self.title.setFocus()

    def start_sub(self) -> None:
        """Open the form for a new sub task of the task in the form."""
        parent = self.task
        if parent is None or parent["status"] != "open":
            return
        self._show(None, parent)
        self.form_dialog.setWindowTitle("New sub task")
        self.title.setFocus()

    def _apply_limit(self) -> None:
        """Nothing of a sub task may be later than the deadline of the tasks it is part of."""
        top = to_qt(self._limit) if self._limit else QDateTime(QDate(7999, 12, 31), QTime(23, 59))
        self.due.setMaximumDateTime(top)
        self.reminder_time.setMaximumDateTime(top)

    def _show(self, task: dict | None, parent: dict | None = None) -> None:
        self._loading = True
        self.task = task
        self.parent_task = parent
        self._limit = (
            task.get("due_limit") if task else latest(parent.get("due_at"), parent.get("due_limit")) if parent else None
        )
        self._apply_limit()
        self.title.setText(task["title"] if task else "")
        self.description.setPlainText(task["description"] if task else "")
        due = task.get("due_at") if task else None
        self.has_due.setChecked(bool(due))
        self.due.setEnabled(bool(due))
        self.due.setDateTime(to_qt(due) if due else self._tomorrow())
        self.reminders.clear()
        for at in (task or {}).get("reminders", []):
            self._add_item(at)
        self.reminder_time.setDateTime(self._tomorrow())
        self._due_touched = self._reminders_touched = False
        self._loading = False
        self._refresh_info()
        self._enable()

    @staticmethod
    def _tomorrow() -> QDateTime:
        moment = (datetime.now() + timedelta(days=1)).replace(hour=9, minute=0, second=0, microsecond=0)
        return QDateTime(QDate(moment.year, moment.month, moment.day), QTime(9, 0))

    def _refresh_info(self) -> None:
        task = self.task
        above = self.parent_task or (next((t for t in self.tasks if t["id"] == task.get("parent_id")), None) if task else None)
        part_of = ""
        if above is not None:
            part_of = f"Part of “{above['title']}”."
            if self._limit:
                part_of += f" Its deadline and reminders cannot be after {local(self._limit)}."
        if task is None:
            self.info.setText(part_of)
            self.hint.setText(
                f"A new {'sub task' if self.parent_task else 'task'}. Without a reminder, Clara chooses when to remind "
                "you, and moves the next ones each time one is sent."
            )
            return
        lines = [f"{'Done' if task['status'] == 'done' else 'To do'} · {plural(task['reminders_sent'], 'reminder')} "
                 f"sent (at most {self.limit} are sent for a task)"]
        if part_of:
            lines.append(part_of)
        if (task.get("subtasks") or {}).get("total"):
            lines.append(f"{task['subtasks']['done']} of {plural(task['subtasks']['total'], 'sub task')} done.")
        if task["status"] == "open":
            lines.append(f"Next reminder: {local(task['next_reminder'])}" if task["next_reminder"] else "No reminder to come.")
        self.info.setText("\n".join(lines))
        self.hint.setText(
            "A task that is done is not reminded." if task["status"] == "done"
            else "" if self.reminders.count() else "No reminder: Clara will not remind you of this task."
        )

    def _add_item(self, at: str) -> None:
        item = QListWidgetItem(local(at))
        item.setData(ID, at)
        self.reminders.addItem(item)

    def _due_toggled(self, on: bool) -> None:
        self.due.setEnabled(on)
        if not self._loading:
            self._due_touched = True

    def _due_edited(self) -> None:
        if not self._loading:
            self._due_touched = True

    def add_reminder(self) -> None:
        self._add_item(to_iso(self.reminder_time.dateTime()))
        self._reminders_touched = True
        self._refresh_info()

    def remove_reminder(self) -> None:
        row = self.reminders.currentRow()
        if row >= 0:
            self.reminders.takeItem(row)
            self._reminders_touched = True
            self._refresh_info()
            self._enable()

    def _times(self) -> list[str]:
        return [self.reminders.item(i).data(ID) for i in range(self.reminders.count())]

    # -- changing ------------------------------------------------------------------------- #

    def save(self) -> None:
        title = self.title.text().strip()
        if not title:
            return
        due = to_iso(self.due.dateTime()) if self.has_due.isChecked() else None
        task = self.task
        if task is None:
            self._say("Adding…" if self._times() else "Clara is choosing the reminders…")
            parent_id = self.parent_task["id"] if self.parent_task else None
            self._call(
                lambda api: api.add_task(title, self.description.toPlainText(), due, self._times(), parent_id),
                self._saved, True,
            )
            return
        fields: dict = {"title": title, "description": self.description.toPlainText()}
        if self._due_touched:
            fields["due"] = due
        if self._reminders_touched and task["status"] == "open":
            fields["reminders"] = self._times()
        self._call(lambda api: api.change_task(task["id"], **fields), self._saved, True)

    def toggle_done(self) -> None:
        task = self.task
        if task is None:
            return
        reopening = task["status"] == "done"
        unfinished = 0 if reopening else sum(1 for kid in self._below(task) if kid["status"] == "open")
        if unfinished and QMessageBox.question(
            self, "Mark as done", f"“{task['title']}” has {plural(unfinished, 'sub task')} still to do: they will be "
            "marked as done too.", QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        ) != QMessageBox.StandardButton.Yes:
            return
        self._say("Reopening: Clara is choosing the next reminders…" if reopening else "")
        self._call(
            lambda api: api.change_task(task["id"], status="open" if reopening else "done"), self._saved, True
        )

    def delete(self) -> None:
        task = self.task
        if task is None:
            return
        parts = self._below(task)
        also = f", its {plural(len(parts), 'sub task')}" if parts else ""
        answer = QMessageBox.question(
            self, "Delete the task", f"“{task['title']}”{also} and the reminders will be deleted for good.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No, QMessageBox.StandardButton.No,
        )
        if answer == QMessageBox.StandardButton.Yes:
            self._call(lambda api: api.delete_task(task["id"]), self._deleted, True)

    def _saved(self, saved: object, error: str) -> None:
        if error:
            self._say(error, bad=True)
            return
        self._changes += 1
        self.task = saved
        self.tasks = [saved if t["id"] == saved["id"] else t for t in self.tasks]
        if not any(t["id"] == saved["id"] for t in self.tasks):
            self.tasks.append(saved)
        self._fill_list(saved["id"])
        self._show(saved)
        self._say("Saved.")
        self.changed.emit()
        self.reload()  # the server decides the order

    def _deleted(self, _result: object, error: str) -> None:
        if error:
            self._say(error, bad=True)
            return
        self._changes += 1
        gone = {self.task["id"], *(kid["id"] for kid in self._below(self.task))} if self.task else set()
        self.tasks = [t for t in self.tasks if t["id"] not in gone]
        self.task = None
        self._fill_list()
        self._show(None)
        self._say("Deleted.")
        self.changed.emit()
        self.reload()  # the task it was part of may be done now
