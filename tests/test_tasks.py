"""The to-do list in the app: the API, the Tasks page, and how it is opened."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from conftest import wait_until
from PySide6.QtCore import QDateTime
from PySide6.QtWidgets import QMessageBox

from clara_app.api import ApiError, ClaraApi
from clara_app.chat_window import ChatWindow
from clara_app.tasks_page import ID, TasksPage, local, summary, to_iso, to_qt

ID_DEPTH = ID + 2  # where the list keeps how deep a task is

TOMORROW = (datetime.now(timezone.utc) + timedelta(days=1)).replace(hour=9, minute=0, second=0, microsecond=0)


def make_dialog(qt, config) -> TasksPage:
    dialog = TasksPage(lambda: config, ClaraApi)
    dialog.show()
    return dialog


def titles(dialog: TasksPage) -> list[str]:
    return [dialog.list.item(row).text().split("\n")[0] for row in range(dialog.list.count())]


def patches(state) -> list[dict]:
    return [body for method, _, body in state.task_requests if method == "PATCH"]


# --- the API ---------------------------------------------------------------------------------------------


def test_tasks_through_the_api(config, server):
    _, state = server
    api = ClaraApi(config)
    assert api.tasks("all") == {"tasks": [], "max_reminders": 10}
    due = (TOMORROW + timedelta(days=2)).isoformat()
    made = api.add_task("Send the invoice", "to ACME", due, [TOMORROW.isoformat()])
    assert (made["title"], made["description"], made["reminders"]) == (
        "Send the invoice", "to ACME", [TOMORROW.isoformat(timespec="seconds")],
    )
    method, _, body = state.task_requests[-1]
    assert (method, body["surface"], body["user_id"], body["user_name"]) == ("POST", "app", "tester", "Tess")
    assert api.task(made["id"])["title"] == "Send the invoice"
    assert [t["title"] for t in api.tasks()["tasks"]] == ["Send the invoice"]
    assert api.change_task(made["id"], title="Send it", due=None)["due_at"] is None
    assert api.change_task(made["id"], status="done")["status"] == "done" and api.tasks()["tasks"] == []
    assert [t["status"] for t in api.tasks("done")["tasks"]] == ["done"]
    api.delete_task(made["id"])
    assert api.tasks("all")["tasks"] == []


def test_a_task_the_server_refuses_is_explained(config, server):
    api = ClaraApi(config)
    with pytest.raises(ApiError, match="needs a title"):
        api.add_task("  ")
    with pytest.raises(ApiError, match="No such task"):
        api.task(99)


# --- the words ---------------------------------------------------------------------------------------------


def test_a_task_is_summed_up_with_what_was_sent_and_what_comes_next():
    task = {"status": "open", "reminders_sent": 2, "next_reminder": TOMORROW.isoformat(), "due_at": None}
    assert summary(task) == f"2 reminders sent · next reminder {local(TOMORROW.isoformat())}"
    assert summary({**task, "reminders_sent": 1, "next_reminder": None}) == "1 reminder sent · no reminder to come"
    assert summary({**task, "status": "done"}) == "done · 2 reminders sent"
    late = summary({**task, "due_at": (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()})
    assert "overdue, was due" in late
    assert "· due " in summary({**task, "due_at": (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()})


def test_a_moment_goes_to_the_form_and_back_on_the_computers_clock():
    text = "2026-10-05T07:30:00+00:00"
    moment = to_qt(text)
    assert isinstance(moment, QDateTime) and moment.toString("yyyy-MM-dd HH:mm") == local(text)
    assert datetime.fromisoformat(to_iso(moment)) == datetime.fromisoformat(text)  # the same instant, minutes kept


# --- the dialog ----------------------------------------------------------------------------------------------


def test_the_dialog_lists_the_tasks_and_shows_one_in_the_form(qt, config, server):
    _, state = server
    state.add_task("Taxes", "Gather papers", None, [TOMORROW.isoformat(), (TOMORROW + timedelta(days=3)).isoformat()], sent=2)
    state.add_task("Old", status="done")
    dialog = make_dialog(qt, config)
    wait_until(lambda: dialog.list.count() == 1)  # "To do" is the filter at first
    assert titles(dialog) == ["Taxes"] and "2 reminders sent" in dialog.list.item(0).text()
    assert dialog.count.text() == "1 open task"
    dialog.list.setCurrentRow(0)
    assert dialog.title.text() == "Taxes" and dialog.description.toPlainText() == "Gather papers"
    assert dialog.reminders.count() == 2 and not dialog.has_due.isChecked()
    assert "2 reminders sent (at most 10" in dialog.info.text() and "Next reminder:" in dialog.info.text()
    dialog.filter.setCurrentIndex(1)  # Done
    assert titles(dialog) == ["Old"]
    dialog.filter.setCurrentIndex(2)  # All
    assert sorted(titles(dialog)) == ["Old", "Taxes"]
    dialog.shutdown()


def test_a_new_task_without_reminders_is_left_to_clara(qt, config, server):
    _, state = server
    dialog = make_dialog(qt, config)
    wait_until(lambda: "No task yet" in dialog.count.text())
    dialog.start_new()
    assert not dialog.save_button.isEnabled() and "Clara chooses" in dialog.hint.text()
    dialog.title.setText("Send the invoice")
    dialog.description.setPlainText("to ACME")
    dialog.has_due.setChecked(True)
    dialog.save()
    assert dialog.status.text() == "Clara is choosing the reminders…"
    wait_until(lambda: state.tasks and dialog.task is not None)
    body = next(body for method, _, body in state.task_requests if method == "POST")
    assert (body["title"], body["description"], body["reminders"]) == ("Send the invoice", "to ACME", [])
    assert body["due"] is not None and datetime.fromisoformat(body["due"]).utcoffset() is not None  # with its offset
    wait_until(lambda: dialog.status.text() == "Saved." and titles(dialog) == ["Send the invoice"])
    assert dialog.reminders.count() == 1  # what Clara chose
    dialog.shutdown()


def test_a_new_task_with_the_reminders_the_person_chose(qt, config, server):
    _, state = server
    dialog = make_dialog(qt, config)
    dialog.start_new()
    dialog.title.setText("Call the dentist")
    dialog.reminder_time.setDateTime(to_qt((TOMORROW + timedelta(hours=1)).isoformat()))
    dialog.add_reminder()
    dialog.reminder_time.setDateTime(to_qt((TOMORROW + timedelta(days=1)).isoformat()))
    dialog.add_reminder()
    assert dialog.reminders.count() == 2
    dialog.reminders.setCurrentRow(1)
    dialog.remove_reminder()
    assert dialog.reminders.count() == 1
    dialog.save()
    wait_until(lambda: state.tasks)
    body = next(body for method, _, body in state.task_requests if method == "POST")
    assert len(body["reminders"]) == 1 and datetime.fromisoformat(body["reminders"][0]) == TOMORROW + timedelta(hours=1)
    dialog.shutdown()


def test_only_what_was_changed_is_sent(qt, config, server):
    _, state = server
    due = (TOMORROW + timedelta(days=2)).isoformat(timespec="seconds")
    state.add_task("Taxes", "Gather papers", due, [TOMORROW.isoformat()])
    dialog = make_dialog(qt, config)
    wait_until(lambda: dialog.list.count() == 1)
    dialog.list.setCurrentRow(0)
    assert dialog.has_due.isChecked()
    dialog.title.setText("Taxes 2025")
    dialog.save()
    wait_until(lambda: state.tasks[1]["title"] == "Taxes 2025")
    assert set(patches(state)[-1]) == {"surface", "user_id", "title", "description"}  # not the deadline, nor the reminders
    wait_until(lambda: dialog.status.text() == "Saved.")
    dialog.has_due.setChecked(False)
    dialog.reminders.setCurrentRow(0)
    dialog.remove_reminder()
    dialog.save()
    wait_until(lambda: state.tasks[1]["due_at"] is None and state.tasks[1]["reminders"] == [])
    assert patches(state)[-1]["due"] is None and patches(state)[-1]["reminders"] == []
    dialog.shutdown()


def test_a_task_is_marked_done_reopened_and_deleted(qt, config, server, monkeypatch):
    _, state = server
    state.add_task("Pay rent", reminders=[TOMORROW.isoformat()])
    dialog = make_dialog(qt, config)
    wait_until(lambda: dialog.list.count() == 1)
    dialog.list.setCurrentRow(0)
    assert dialog.done_button.text() == "Mark as done"
    dialog.done_button.click()
    wait_until(lambda: state.tasks[1]["status"] == "done" and dialog.task and dialog.task["status"] == "done")
    wait_until(lambda: dialog.done_button.text() == "Reopen" and not dialog.add_reminder_button.isEnabled())
    assert "not reminded" in dialog.hint.text()
    dialog.done_button.click()
    wait_until(lambda: state.tasks[1]["status"] == "open" and dialog.task["status"] == "open")
    assert dialog.reminders.count() == 1 and dialog.add_reminder_button.isEnabled()  # Clara chose again
    monkeypatch.setattr(QMessageBox, "question", lambda *args, **kwargs: QMessageBox.StandardButton.No)
    dialog.delete()
    assert state.tasks
    monkeypatch.setattr(QMessageBox, "question", lambda *args, **kwargs: QMessageBox.StandardButton.Yes)
    dialog.delete()
    wait_until(lambda: not state.tasks and dialog.task is None and dialog.status.text() == "Deleted.")
    assert dialog.status.text() == "Deleted." and titles(dialog) == []
    dialog.shutdown()


def test_the_dialog_follows_what_clara_changes_meanwhile(qt, config, server):
    _, state = server
    state.add_task("Chore", reminders=[TOMORROW.isoformat()])
    dialog = make_dialog(qt, config)
    wait_until(lambda: dialog.list.count() == 1)
    dialog.list.setCurrentRow(0)
    assert dialog.reminders.count() == 1
    moved = (TOMORROW + timedelta(days=4)).isoformat(timespec="seconds")
    later = (TOMORROW + timedelta(days=9)).isoformat(timespec="seconds")
    state.tasks[1].update(reminders=[moved, later], next_reminder=moved, reminders_sent=1)
    dialog.reload()
    wait_until(lambda: dialog.reminders.count() == 2)  # nothing was being edited: the form follows
    assert "1 reminder sent" in dialog.info.text()
    dialog.title.setText("Chore, typed but not saved")
    state.tasks[1].update(reminders=[moved], reminders_sent=2)
    dialog.reload()
    wait_until(lambda: "2 reminders sent" in dialog.info.text())
    assert dialog.title.text() == "Chore, typed but not saved"  # being edited: what was typed stays
    state.tasks.clear()
    dialog.reload()
    wait_until(lambda: dialog.task is None)
    dialog.shutdown()


def test_a_failure_is_shown_in_the_dialog(qt, config, server):
    dialog = make_dialog(qt, config)
    dialog.start_new()
    dialog.title.setText("x")
    config.token = "nope"  # the server will refuse it
    dialog.save()
    wait_until(lambda: "401" in dialog.status.text())
    assert dialog.status.property("tone") == "bad"
    dialog.shutdown()


# --- how it is opened -------------------------------------------------------------------------------------


def test_the_window_shows_the_tasks_page_and_keeps_it(qt, config, server):
    window = ChatWindow(lambda: config, ClaraApi)
    window.show()
    window.open_tasks()
    first = window.shell.pages["tasks"]
    assert isinstance(first, TasksPage) and window.shell.current == "tasks" and first.isVisible()
    window.go("chat")
    window.open_tasks()
    assert window.shell.pages["tasks"] is first  # the same page again, not a second one
    window.quit_for_good()
    window.close()


def test_without_a_server_the_window_asks_for_the_settings_first(qt, config):
    from dataclasses import replace

    window = ChatWindow(lambda: replace(config, token=""), ClaraApi)
    asked = []
    window.settings_requested.connect(lambda: asked.append(True))
    window.open_tasks()
    assert asked == [True] and window.shell.current == "chat"
    window.quit_for_good()
    window.close()


# --- sub tasks ---------------------------------------------------------------------------------------------


def test_a_sub_task_is_sent_with_the_task_it_is_part_of(config, server):
    _, state = server
    api = ClaraApi(config)
    parent = api.add_task("Move house", due=(TOMORROW + timedelta(days=9)).isoformat())
    sub = api.add_task("Pack", "Books", None, [TOMORROW.isoformat()], parent["id"])
    assert state.task_requests[-1][2]["parent_id"] == parent["id"] and sub["parent_id"] == parent["id"]
    api.add_task("Plain")
    assert "parent_id" not in state.task_requests[-1][2]


def test_the_list_draws_the_sub_tasks_under_their_task(qt, config, server):
    _, state = server
    parent = state.add_task("Move house", due=(TOMORROW + timedelta(days=9)).isoformat())
    first = state.add_task("Pack", parent_id=parent["id"])
    state.add_task("Sort the shelves", parent_id=first["id"])
    state.add_task("Book the movers", status="done", parent_id=parent["id"])
    state.add_task("Dentist")
    dialog = make_dialog(qt, config)
    wait_until(lambda: dialog.list.count() == 5)
    assert titles(dialog) == ["Move house", "Pack", "Sort the shelves", "Book the movers", "Dentist"]
    assert [dialog.list.item(row).data(ID_DEPTH) for row in range(5)] == [0, 1, 2, 1, 0]
    assert "1/2 sub tasks" in dialog.list.item(0).text()
    dialog.filter.setCurrentIndex(1)  # Done: the done sub task of a task still to do is listed on its own
    assert titles(dialog) == ["Book the movers"]
    dialog.shutdown()


def test_a_sub_task_is_added_from_its_task_within_the_deadline(qt, config, server):
    _, state = server
    due = TOMORROW + timedelta(days=9)
    parent = state.add_task("Move house", due=due.isoformat())
    dialog = make_dialog(qt, config)
    wait_until(lambda: dialog.list.count() == 1)
    dialog.list.setCurrentRow(0)
    assert dialog.sub_button.isEnabled()
    dialog.start_sub()
    assert dialog.task is None and dialog.parent_task["id"] == parent["id"]
    assert "Part of “Move house”" in dialog.info.text() and "cannot be after" in dialog.info.text()
    assert dialog.due.maximumDateTime() == to_qt(due.isoformat())  # the form will not take a later one
    dialog.title.setText("Pack the books")
    dialog.add_reminder()
    dialog.save()
    wait_until(lambda: any(m == "POST" for m, _, _ in state.task_requests))
    body = [b for m, _, b in state.task_requests if m == "POST"][-1]
    assert body["parent_id"] == parent["id"] and body["title"] == "Pack the books"
    wait_until(lambda: dialog.list.count() == 2)
    assert titles(dialog) == ["Move house", "Pack the books"]
    dialog.shutdown()


def test_finishing_or_deleting_a_task_with_sub_tasks_asks_first(qt, config, server, monkeypatch):
    _, state = server
    parent = state.add_task("Move house")
    state.add_task("Pack", parent_id=parent["id"])
    dialog = make_dialog(qt, config)
    wait_until(lambda: dialog.list.count() == 2)
    dialog.list.setCurrentRow(0)
    asked = []
    monkeypatch.setattr(QMessageBox, "question", lambda *args: asked.append(args[2]) or QMessageBox.StandardButton.No)
    dialog.toggle_done()
    assert "1 sub task still to do" in asked[-1] and not patches(state)
    dialog.delete()
    assert "its 1 sub task" in asked[-1] and not [r for r in state.task_requests if r[0] == "DELETE"]
    dialog.shutdown()
