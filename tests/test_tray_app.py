"""The tray icon, the single-instance guard, start with Windows, and the application wiring."""

from __future__ import annotations

import subprocess
import sys
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from conftest import wait_until
from PySide6.QtWidgets import QSystemTrayIcon

from clara_app import autostart
from clara_app.app import ClaraApplication, describe_notification, describe_reminder
from clara_app.config import Config, load, save
from clara_app.single import SingleInstance
from clara_app.tray import Tray
from clara_app.workers import ReminderWorker
from clara_app.api import ClaraApi


class TestTray:
    def test_left_click_toggles_and_double_click_opens(self, qt):
        tray = Tray()
        seen = []
        tray.toggle_requested.connect(lambda: seen.append("toggle"))
        tray.open_requested.connect(lambda: seen.append("open"))
        tray.activated.emit(QSystemTrayIcon.ActivationReason.Trigger)
        tray.activated.emit(QSystemTrayIcon.ActivationReason.DoubleClick)
        tray.activated.emit(QSystemTrayIcon.ActivationReason.Context)  # right click: the menu, nothing else
        assert seen == ["toggle", "open"]

    def test_the_menu_offers_open_tasks_settings_autostart_and_quit(self, qt):
        tray = Tray()
        labels = [a.text() for a in tray.contextMenu().actions() if not a.isSeparator()]
        assert labels == ["Open Clara", "Tasks…", "Settings…", "Start with Windows", "Quit"]
        seen = []
        tray.open_requested.connect(lambda: seen.append("open"))
        tray.tasks_requested.connect(lambda: seen.append("tasks"))
        tray.settings_requested.connect(lambda: seen.append("settings"))
        tray.quit_requested.connect(lambda: seen.append("quit"))
        for action in tray.contextMenu().actions():
            if not action.isSeparator() and action.text() != "Start with Windows":
                action.trigger()
        assert seen == ["open", "tasks", "settings", "quit"]

    def test_clicking_a_notification_opens_the_app(self, qt):
        tray = Tray()
        seen = []
        tray.open_requested.connect(lambda: seen.append("open"))
        tray.messageClicked.emit()
        assert seen == ["open"]

    def test_notify_does_not_fail(self, qt):
        Tray().notify("Clara reminder", "Dentist")

    def test_the_autostart_entry_follows_the_menu_without_looping(self, qt):
        tray = Tray()
        toggled = []
        tray.autostart_toggled.connect(toggled.append)
        tray.set_autostart_checked(True)  # showing the state is not a request
        assert toggled == []
        actions = {a.text(): a for a in tray.contextMenu().actions()}
        if actions["Start with Windows"].isEnabled():
            actions["Start with Windows"].trigger()
            assert toggled == [False]

    def test_the_tooltip_says_when_the_server_is_unreachable(self, qt):
        tray = Tray()
        tray.set_state("down")
        assert tray.toolTip() == "Clara is not running"
        tray.set_state("stopping")
        assert tray.toolTip() == "Clara is stopping"
        tray.set_state("running")
        assert tray.toolTip() == "Clara is running"


class TestSingleInstance:
    SECOND_COPY = "; ".join(
        [
            "import os",
            "os.environ['QT_QPA_PLATFORM'] = 'offscreen'",
            "from PySide6.QtWidgets import QApplication",
            "from clara_app.single import SingleInstance",
            "qt = QApplication([])",
            "print('started' if SingleInstance({name!r}).acquire() else 'refused')",
        ]
    )

    def test_a_second_copy_asks_the_first_to_show_itself_and_leaves(self, qt):
        name = f"clara-app-test-{uuid.uuid4().hex}"
        first = SingleInstance(name)
        shown = []
        assert first.acquire()
        first.show_requested.connect(lambda: shown.append(True))

        second = subprocess.Popen(
            [sys.executable, "-c", self.SECOND_COPY.format(name=name)], stdout=subprocess.PIPE, text=True
        )
        wait_until(lambda: shown and second.poll() is not None, timeout=20)  # a real second process
        assert second.communicate()[0].strip() == "refused"
        assert shown == [True]

    def test_after_the_first_copy_is_gone_the_name_is_free_again(self, qt):
        name = f"clara-app-test-{uuid.uuid4().hex}"
        first = SingleInstance(name)
        assert first.acquire()
        first._server.close()
        assert SingleInstance(name).acquire()


@pytest.mark.skipif(sys.platform != "win32", reason="the Windows registry")
class TestAutostart:
    KEY = r"Software\clara-app-tests\Run"

    @pytest.fixture(autouse=True)
    def clean(self):
        import winreg

        yield
        for path in (self.KEY, r"Software\clara-app-tests"):
            try:
                winreg.DeleteKey(winreg.HKEY_CURRENT_USER, path)
            except FileNotFoundError:
                pass

    def test_the_command_starts_the_app_in_the_background_without_a_console(self):
        command = autostart.command()
        assert command.startswith('"') and "-m clara_app --background" in command
        assert "pythonw" in command.lower() or "python" in command.lower()

    def test_enable_then_disable(self):
        assert not autostart.is_enabled(self.KEY)
        autostart.set_enabled(True, self.KEY)
        assert autostart.is_enabled(self.KEY)
        autostart.set_enabled(False, self.KEY)
        assert not autostart.is_enabled(self.KEY)
        autostart.set_enabled(False, self.KEY)  # already off: fine


class TestReminderWorker:
    def test_reminders_arrive_and_the_worker_reconnects(self, qt, config, server):
        _, state = server
        state.reminders = [{"type": "reminder", "id": 1, "text": "Dentist"}, {"type": "other"}]
        state.hold_reminders = False  # each connection ends after its events, like a restarting server
        got, states = [], []
        worker = ReminderWorker(lambda: ClaraApi(config), pause=0.05)
        worker.reminder.connect(got.append)
        worker.connection.connect(states.append)
        worker.start()
        wait_until(lambda: len(got) >= 2)  # the stream ended, it came back
        worker.stop()
        assert worker.wait(5000)
        assert [e["text"] for e in got][:2] == ["Dentist", "Dentist"]
        assert state.reminder_connections >= 2
        assert True in states and False in states

    def test_a_server_that_is_down_is_retried(self, qt, config):
        config.url = "http://127.0.0.1:1"
        states = []
        worker = ReminderWorker(lambda: ClaraApi(config), pause=0.05)
        worker.connection.connect(states.append)
        worker.start()
        wait_until(lambda: states.count(False) >= 2, timeout=15)
        worker.stop()
        assert worker.wait(5000)

    def test_stop_ends_a_worker_blocked_on_a_quiet_stream(self, qt, config, server):
        _, state = server
        state.reminders = []
        worker = ReminderWorker(lambda: ClaraApi(config), pause=30)
        worker.start()
        wait_until(lambda: state.reminder_connections >= 1)
        worker.stop()
        assert worker.wait(5000)


class TestNotifications:
    def test_the_worker_listens_as_this_user_and_passes_notifications_on(self, qt, config, server):
        _, state = server
        state.reminders = [{"type": "notification", "id": 2, "title": "Done", "text": "Build finished"}]
        got = []
        worker = ReminderWorker(lambda: ClaraApi(config), pause=30)
        worker.notification.connect(got.append)
        worker.start()
        wait_until(lambda: got)
        worker.stop()
        assert worker.wait(5000)
        assert got[0]["text"] == "Build finished"
        assert state.stream_paths[0] == "/v1/notifications/stream?surface=app&user_id=tester"

    def test_describe_notification(self):
        stamp = datetime(2026, 10, 2, 10, 0, tzinfo=timezone.utc)
        event = {"type": "notification", "title": "", "text": "Done", "sent_at": stamp.isoformat()}
        assert describe_notification(event, stamp) == ("Clara", "Done", "")
        title, _, detail = describe_notification({**event, "title": "Build"}, stamp + timedelta(hours=3))
        assert title == "Build" and detail.startswith("Sent ")

    def test_the_tray_opens_the_tasks_over_the_window(self, qt, tmp_path, config):
        path = tmp_path / "config.json"
        save(config, path)
        application = ClaraApplication(qt, config_path=path)
        opened = []
        application.window.bring_to_front = lambda: opened.append("window")
        application.window.open_tasks = lambda: opened.append("tasks")
        application.tray.tasks_requested.emit()
        assert opened == ["window", "tasks"]  # the window first: the dialog belongs to it
        application.quit()

    def test_a_task_reminder_pops_up_with_the_title_of_its_task(self, qt, tmp_path, config):
        path = tmp_path / "config.json"
        save(config, path)
        application = ClaraApplication(qt, config_path=path)
        shown = []
        application.tray.notify = lambda title, text: shown.append((title, text))
        now = datetime.now(timezone.utc).isoformat()
        application.on_notification(
            {"type": "notification", "title": "Task: Taxes", "text": "Your taxes are due in two days.", "source": "tasks", "sent_at": now}
        )
        assert shown == [("Task: Taxes", "Your taxes are due in two days.")]
        application.quit()

    def test_a_notification_pops_up_and_is_noted(self, qt, tmp_path, config):
        path = tmp_path / "config.json"
        save(config, path)
        application = ClaraApplication(qt, config_path=path)
        shown = []
        application.tray.notify = lambda title, text: shown.append((title, text))
        now = datetime.now(timezone.utc).isoformat()
        application.on_notification({"type": "notification", "title": "Answer ready", "text": "Done.", "sent_at": now})
        assert shown == [("Answer ready", "Done.")]
        assert "Answer ready" in application.window.view.texts()[-1][1]
        application.quit()

    def test_one_about_the_conversation_in_front_of_the_user_is_only_noted(self, qt, tmp_path, config, monkeypatch):
        path = tmp_path / "config.json"
        save(config, path)
        application = ClaraApplication(qt, config_path=path)
        shown = []
        application.tray.notify = lambda title, text: shown.append(title)
        window = application.window
        monkeypatch.setattr(window, "isVisible", lambda: True)
        monkeypatch.setattr(window, "isActiveWindow", lambda: True)
        window.conversation = "app:tester:1"
        now = datetime.now(timezone.utc).isoformat()
        event = {"type": "notification", "title": "Answer ready", "text": "Done.", "sent_at": now}
        application.on_notification({**event, "conversation": "app:tester:1"})
        assert shown == []
        application.on_notification({**event, "conversation": "app:tester:2"})  # another one: it pops up
        assert shown == ["Answer ready"]
        application.quit()


class TestServerSignal:
    def test_the_worker_passes_on_what_the_server_says(self, qt, config, server):
        _, state = server
        state.server_scripts = [["running", "stopping", "stopped"]]
        said = []
        worker = ReminderWorker(lambda: ClaraApi(config), pause=30)
        worker.server.connect(said.append)
        worker.start()
        wait_until(lambda: said == ["running", "stopping", "stopped"])
        worker.stop()
        assert worker.wait(5000)


class TestDescribeReminder:
    def event(self, **fields):
        stamp = datetime(2026, 10, 2, 10, 0, tzinfo=timezone.utc).isoformat()
        return {"type": "reminder", "text": "Dentist", "due_at": stamp, "fired_at": stamp, "from": "Alice", **fields}

    def test_on_time(self):
        now = datetime(2026, 10, 2, 10, 0, 3, tzinfo=timezone.utc)
        assert describe_reminder(self.event(), now) == ("Clara reminder", "Dentist", "")

    def test_missed(self):
        title, text, detail = describe_reminder(self.event(), datetime(2026, 10, 2, 16, 0, tzinfo=timezone.utc))
        assert title == "Clara reminder (missed)" and "It was due" in detail

    def test_the_message_clara_wrote_comes_first(self):
        now = datetime(2026, 10, 2, 10, 0, 3, tzinfo=timezone.utc)
        assert describe_reminder(self.event(message="Alice, your dentist is waiting!"), now)[1] == "Alice, your dentist is waiting!"
        assert describe_reminder(self.event(message=None), now)[1] == "Dentist"

    def test_without_author(self):
        now = datetime(2026, 10, 2, 10, 0, 1, tzinfo=timezone.utc)
        assert describe_reminder(self.event(**{"from": None}), now)[2] == ""


class TestApplication:
    def make(self, qt, tmp_path, config):
        path = tmp_path / "config.json"
        save(config, path)
        return ClaraApplication(qt, config_path=path)

    def test_a_reminder_pops_a_notification_and_a_note(self, qt, tmp_path, config):
        application = self.make(qt, tmp_path, config)
        shown = []
        application.tray.notify = lambda title, text: shown.append((title, text))
        now = datetime.now(timezone.utc)
        application.on_reminder(
            {"type": "reminder", "text": "Dentist", "due_at": now.isoformat(), "fired_at": now.isoformat(), "from": "Alice"}
        )
        assert shown == [("Clara reminder", "Dentist\nClara is running")]  # and the state of the server
        assert "Dentist" in application.window.view.texts()[-1][1]
        application.quit()

    def test_the_notification_shows_what_clara_wrote_and_the_state_of_the_server(self, qt, tmp_path, config):
        application = self.make(qt, tmp_path, config)
        shown = []
        application.tray.notify = lambda title, text: shown.append((title, text))
        now = datetime.now(timezone.utc).isoformat()
        event = {"type": "reminder", "text": "Dentist", "message": "Your dentist is waiting!", "due_at": now, "fired_at": now}
        application.on_reminder(event)  # nothing heard from the server yet: the reminder came, so it is there
        assert shown[-1][1].endswith("Clara is running")
        application._server_said("stopping")
        shown.clear()
        application.on_reminder(event)
        assert shown == [("Clara reminder", "Your dentist is waiting!\nClara is stopping")]
        assert "Your dentist is waiting" in application.window.view.texts()[-1][1]
        application.quit()

    def test_the_state_of_the_server_is_shown_and_said_when_it_changes(self, qt, tmp_path, config):
        application = self.make(qt, tmp_path, config)
        shown = []
        application.tray.notify = lambda title, text: shown.append((title, text))

        application._server_said("running")  # finding all well: nothing to say
        assert shown == [] and application.window.status.text() == "● Clara is running"
        application._server_said("running")
        application._server_said("stopping")
        assert shown == [("Clara", "Clara is stopping: she finishes what is running and takes nothing new.")]
        assert application.window.status.text() == "◐ Clara is stopping"
        application._server_said("stopped")
        application._connection(False)  # the stream ends right after: already known
        assert shown[-1] == ("Clara", "Clara is not running.") and len(shown) == 2
        assert application.window.status.text() == "○ Clara is not running"
        assert application.tray.toolTip() == "Clara is not running"
        application._server_said("running")
        assert shown[-1] == ("Clara", "Clara is running again.") and len(shown) == 3
        application.quit()

    def test_a_server_that_is_not_there_at_the_start_is_said(self, qt, tmp_path, config):
        config.url = "http://127.0.0.1:1"
        application = self.make(qt, tmp_path, config)
        shown = []
        application.tray.notify = lambda title, text: shown.append((title, text))
        application.start(background=True)
        wait_until(lambda: shown, timeout=15)
        assert shown == [("Clara", "Clara is not running.")]
        assert application.server_state == "down"
        application.quit()

    def test_the_whole_story_the_server_stops_then_comes_back(self, qt, tmp_path, config, server, monkeypatch):
        from clara_app import workers

        monkeypatch.setattr(workers, "RECONNECT_SECONDS", 0.05)
        _, state = server
        state.server_scripts = [["running", "stopping", "stopped"], ["running"]]
        application = self.make(qt, tmp_path, config)
        shown = []
        application.tray.notify = lambda title, text: shown.append(text)
        application.start(background=True)
        wait_until(lambda: len(shown) >= 3)
        assert shown[:3] == [
            "Clara is stopping: she finishes what is running and takes nothing new.",
            "Clara is not running.",
            "Clara is running again.",
        ]
        assert application.server_state == "running" and "running" in application.window.status.text()
        application.quit()

    def test_started_by_windows_it_stays_in_the_tray(self, qt, tmp_path, config, server):
        application = self.make(qt, tmp_path, config)
        application.start(background=True)
        assert not application.window.isVisible()
        wait_until(lambda: "running" in application.window.status.text())
        application.quit()

    def test_started_by_hand_it_opens_the_window(self, qt, tmp_path, config, server):
        application = self.make(qt, tmp_path, config)
        application.start(background=False)
        assert application.window.isVisible()
        application.quit()

    def test_without_a_token_the_settings_come_first_and_the_window_opens_anyway(self, qt, tmp_path, config, monkeypatch):
        application = self.make(qt, tmp_path, Config(url=config.url, token="", user_id="tester"))
        opened = []
        monkeypatch.setattr(application, "open_settings", lambda first_run=False: opened.append(first_run))
        application.start(background=True)
        assert opened == [True]
        assert application.window.isVisible()  # nothing could be listened to: do not hide it
        assert application.listener is None
        application.quit()

    def test_new_settings_are_saved_and_restart_the_listener(self, qt, tmp_path, config, server, monkeypatch):
        from PySide6.QtWidgets import QDialog

        application = self.make(qt, tmp_path, config)
        application.start(background=True)
        first = application.listener
        monkeypatch.setattr(
            "clara_app.app.SettingsDialog.exec", lambda self: (setattr(self, "_done", True), QDialog.DialogCode.Accepted)[1]
        )
        monkeypatch.setattr(
            "clara_app.app.SettingsDialog.config", lambda self: Config(config.url, config.token, "someone-else", "Else")
        )
        application.open_settings()
        assert application.config.user_id == "someone-else"
        assert load(tmp_path / "config.json", env={}).user_name == "Else"
        assert application.listener is not None and application.listener is not first
        application.quit()

    def test_at_the_start_the_last_conversation_is_shown(self, qt, tmp_path, config, server):
        _, state = server
        state.add_conversation("app:tester:old", ("old question", "old answer"), updated_at="2026-09-01T10:00:00+00:00")
        state.add_conversation("app:tester:new", ("Tea timer", "Set."), updated_at="2026-10-02T10:00:00+00:00")
        state.add_conversation(
            "app:tester:pin", ("pinned", "ok"), updated_at="2026-08-01T10:00:00+00:00", pinned=True
        )
        application = self.make(qt, tmp_path, config)
        application.start(background=True)
        window = application.window
        wait_until(lambda: window.conversation == "app:tester:new")
        assert [text for role, text in window.view.texts() if role != "note"] == ["Tea timer", "Set."]
        application.quit()

    def test_quit_stops_everything(self, qt, tmp_path, config, server):
        application = self.make(qt, tmp_path, config)
        application.start(background=False)
        application.quit()
        assert application.listener is None and not application.window.isVisible()


def test_the_icon_is_the_picture_of_clara_and_turns_grey_when_the_server_is_down(qt):
    from PySide6.QtCore import QSize

    from clara_app.icon import ICON_FILE, make_icon

    assert ICON_FILE.is_file()
    online, offline = make_icon(True), make_icon(False)
    assert not online.isNull() and QSize(32, 32) in online.availableSizes()
    up, down = online.pixmap(32).toImage(), offline.pixmap(32).toImage()
    colourful = max(abs(up.pixelColor(x, y).red() - up.pixelColor(x, y).blue()) for x in range(32) for y in range(32))
    grey = max(abs(down.pixelColor(x, y).red() - down.pixelColor(x, y).blue()) for x in range(32) for y in range(32))
    assert colourful > 20 and grey == 0
