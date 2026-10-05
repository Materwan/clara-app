"""The tasks dialog: your to-do list, with the reminders still to come of each task.

The list is kept by the server and belongs to the user, so it is the same here, on the web site, in the terminal and in
every chat with Clara. Clara picks the reminders of a task given none, and moves the next ones each time one is sent:
the numbers shown here change by themselves, so the dialog reads the list again from time to time. Every call runs off
the UI thread (workers.py); the dialog only shows what the server answers.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Callable

from PySide6.QtCore import QDate, QDateTime, Qt, QTime, QTimer, Signal
from PySide6.QtGui import QBrush, QColor
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDateTimeEdit,
    QDialog,
    QDialogButtonBox,
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


def summary(task: dict) -> str:
    """The second line of a task in the list: what was sent, and what comes next."""
    sent = plural(task["reminders_sent"], "reminder") + " sent"
    if task["status"] == "done":
        return f"done · {sent}"
    overdue = task.get("due_at") and datetime.fromisoformat(task["due_at"]) < datetime.now().astimezone()
    due = f" · {'overdue, was due' if overdue else 'due'} {local(task['due_at'])}" if task.get("due_at") else ""
    next_reminder = f"next reminder {local(task['next_reminder'])}" if task.get("next_reminder") else "no reminder to come"
    return f"{sent} · {next_reminder}{due}"


class TasksDialog(QDialog):
    changed = Signal()  # a task was added, changed or deleted

    def __init__(
        self,
        get_config: Callable[[], Config],
        api_factory: Callable[[Config], ClaraApi] = ClaraApi,
        parent=None,
    ):
        super().__init__(parent)
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
        self.setWindowTitle("Tasks")
        self.resize(820, 560)

        intro = QLabel(
            "Your to-do list, the same on the web site and in every chat with Clara. Each task has reminders: choose "
            "them, or leave it to Clara, who also moves the next ones each time one is sent."
        )
        intro.setWordWrap(True)
        intro.setStyleSheet("color: gray;")

        self.filter = QComboBox()
        for label, status in FILTERS:
            self.filter.addItem(label, status)
        self.filter.currentIndexChanged.connect(lambda *_: self._fill_list())
        self.list = QListWidget()
        self.list.setMinimumWidth(300)
        self.list.setWordWrap(True)  # a task's second line is long: it wraps instead of scrolling sideways
        self.list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.list.currentItemChanged.connect(self._picked)
        self.new_button = QPushButton("New task…")
        self.new_button.clicked.connect(self.start_new)
        self.count = QLabel("")
        self.count.setStyleSheet("color: gray;")
        left = QVBoxLayout()
        left.addWidget(self.filter)
        left.addWidget(self.list, 1)
        left.addWidget(self.count)
        left.addWidget(self.new_button)

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
        self.hint.setStyleSheet("color: gray;")
        self.info = QLabel("")
        self.info.setWordWrap(True)
        for field in (self.title, self.description):
            field.textChanged.connect(self._enable)

        form = QFormLayout()
        form.addRow("Title:", self.title)
        form.addRow("Description:", self.description)
        form.addRow("", due_row)
        form.addRow("Reminders:", reminders_box)
        self.save_button = QPushButton("Save")
        self.save_button.setDefault(True)
        self.save_button.clicked.connect(self.save)
        self.done_button = QPushButton("Mark as done")
        self.done_button.clicked.connect(self.toggle_done)
        self.delete_button = QPushButton("Delete…")
        self.delete_button.clicked.connect(self.delete)
        buttons = QHBoxLayout()
        buttons.addWidget(self.delete_button)
        buttons.addWidget(self.done_button)
        buttons.addStretch(1)
        buttons.addWidget(self.save_button)
        self.status = QLabel("")
        self.status.setWordWrap(True)
        self.status.setStyleSheet("color: gray;")

        self.detail = QWidget()
        right = QVBoxLayout(self.detail)
        right.setContentsMargins(0, 0, 0, 0)
        right.addLayout(form)
        right.addWidget(self.hint)
        right.addWidget(self.info)
        right.addStretch(1)
        right.addLayout(buttons)

        body = QHBoxLayout()
        body.addLayout(left)
        body.addWidget(self.detail, 1)
        close = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        close.rejected.connect(self.reject)
        layout = QVBoxLayout(self)
        layout.addWidget(intro)
        layout.addLayout(body, 1)
        layout.addWidget(self.status)
        layout.addWidget(close)

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
        self.status.setStyleSheet("color: #b3261e;" if bad else "color: gray;")

    def done(self, result: int) -> None:
        self._timer.stop()
        for worker in list(self._calls):
            worker.then = None
            worker.wait(5000)
        super().done(result)

    def _enable(self) -> None:
        busy = self.busy
        task = self.task
        is_open = task is None or task["status"] == "open"
        self.save_button.setEnabled(not busy and bool(self.title.text().strip()))
        self.done_button.setEnabled(task is not None and not busy)
        self.done_button.setText("Reopen" if task is not None and task["status"] == "done" else "Mark as done")
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
        if select is None and self.task:
            select = self.task["id"]
        self.list.blockSignals(True)
        self.list.clear()
        chosen = None
        for task in shown:
            item = QListWidgetItem(f"{task['title']}\n{summary(task)}")
            item.setData(ID, task["id"])
            if task["status"] == "done":
                item.setForeground(QBrush(QColor("gray")))
            self.list.addItem(item)
            if task["id"] == select:
                chosen = item
        if chosen is not None:
            self.list.setCurrentItem(chosen)
        self.list.blockSignals(False)
        open_count = sum(1 for t in self.tasks if t["status"] == "open")
        self.count.setText(plural(open_count, "open task") if self.tasks else "No task yet")

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
        self.title.setFocus()

    def _show(self, task: dict | None) -> None:
        self._loading = True
        self.task = task
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
        if task is None:
            self.info.setText("")
            self.hint.setText(
                "A new task. Without a reminder, Clara chooses when to remind you, and moves the next ones each time "
                "one is sent."
            )
            return
        lines = [f"{'Done' if task['status'] == 'done' else 'To do'} · {plural(task['reminders_sent'], 'reminder')} "
                 f"sent (at most {self.limit} are sent for a task)"]
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
            self._call(
                lambda api: api.add_task(title, self.description.toPlainText(), due, self._times()), self._saved, True
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
        self._say("Reopening: Clara is choosing the next reminders…" if reopening else "")
        self._call(
            lambda api: api.change_task(task["id"], status="open" if reopening else "done"), self._saved, True
        )

    def delete(self) -> None:
        task = self.task
        if task is None:
            return
        answer = QMessageBox.question(
            self, "Delete the task", f"“{task['title']}” and its reminders will be deleted for good.",
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
        gone = self.task["id"] if self.task else None
        self.tasks = [t for t in self.tasks if t["id"] != gone]
        self.task = None
        self._fill_list()
        self._show(None)
        self._say("Deleted.")
        self.changed.emit()
