"""The application: the tray icon, the window, and the listener of reminders and notifications, wired
together."""

from __future__ import annotations

import time
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from PySide6.QtCore import QObject, QTimer
from PySide6.QtWidgets import QApplication, QDialog

from . import APP_NAME, autostart
from . import config as config_module
from .api import ClaraApi
from .chat_window import ChatWindow
from .config import Config
from .local_folders import FolderRegistry, LocalFolders, run_jobs
from .settings_dialog import SettingsDialog
from .status import CHANGED, STATUS, from_server
from .theme import install as install_theme
from .tray import Tray
from .widgets import Calls
from .workers import ReminderWorker

LATE_SECONDS = 120  # a reminder shown this long after it fired is announced as missed
APPROVAL_NOTICE_SECONDS = 12.0  # a click on a notification this soon after a request for permission opens the requests
APPROVALS_REFRESH_MS = 60_000  # the count of requests waiting is read again this often, whatever was heard


def describe_reminder(event: dict, now: datetime | None = None) -> tuple[str, str, str]:
    """`(title, text, detail)` of a reminder event, for a notification. The text is what Clara wrote for it
    (the reminder's own text if she could not). A reminder is always the user's own."""
    now = now or datetime.now(timezone.utc)
    missed = (now - datetime.fromisoformat(event["fired_at"])).total_seconds() > LATE_SECONDS
    title = f"{APP_NAME} reminder" + (" (missed)" if missed else "")
    detail = ""
    if missed:
        due = datetime.fromisoformat(event["due_at"]).astimezone().strftime("%Y-%m-%d %H:%M")
        detail = f"It was due {due}."
    return title, str(event.get("message") or event["text"]), detail


def describe_notification(event: dict, now: datetime | None = None) -> tuple[str, str, str]:
    """`(title, text, detail)` of a notification event (from Clara, the server, or another client)."""
    now = now or datetime.now(timezone.utc)
    title = event.get("title") or APP_NAME
    detail = ""
    if (now - datetime.fromisoformat(event["sent_at"])).total_seconds() > LATE_SECONDS:
        sent = datetime.fromisoformat(event["sent_at"]).astimezone().strftime("%Y-%m-%d %H:%M")
        detail = f"Sent {sent}."
    return title, str(event["text"]), detail


def describe_approval(event: dict) -> tuple[str, str]:
    """`(title, text)` of the notification for a request for permission nobody answered in time."""
    where = event.get("resource") or ""
    return "Clara needs your permission", "\n".join(part for part in (str(event.get("summary") or event.get("text") or ""), f"On {where}" if where else "") if part)


class ClaraApplication(QObject, Calls):
    def __init__(
        self,
        qt: QApplication,
        config_path: Path | None = None,
        api_factory: Callable[[Config], ClaraApi] = ClaraApi,
    ):
        super().__init__()
        self.qt = qt
        self._config_path = config_path
        self._api_factory = api_factory
        self.config = config_module.load(config_path)
        self._init_calls(lambda: self.config, api_factory)
        self._approval_notice_at = 0.0  # when the last request for permission was announced
        self._jobs_running = False
        self._jobs_again = False
        self.approvals_timer = QTimer(self)
        self.approvals_timer.setInterval(APPROVALS_REFRESH_MS)
        self.approvals_timer.timeout.connect(self.refresh_approvals)
        self.listener: ReminderWorker | None = None
        self.server_state: str | None = None  # "running", "stopping", "down"; None until the server answers

        install_theme(qt, self.config.theme)
        self.window = ChatWindow(lambda: self.config, api_factory)
        self.window.settings_requested.connect(self.open_settings)
        self.window.theme_chosen.connect(self._theme_chosen)
        self.window.signed_out.connect(self.sign_out)
        self.tray = Tray()
        self.tray.toggle_requested.connect(self.window.toggle)
        self.tray.open_requested.connect(self.window.bring_to_front)
        self.tray.messageClicked.connect(self._notice_clicked)  # (the click also opens the window: open_requested)
        self.window.approvals_changed.connect(self.refresh_approvals)
        self.tray.settings_requested.connect(self.open_account)
        self.tray.tasks_requested.connect(self.open_tasks)
        self.tray.autostart_toggled.connect(self._set_autostart)
        self.tray.quit_requested.connect(self.quit)

    # -- life cycle ------------------------------------------------------------------------ #

    def start(self, background: bool = False) -> None:
        """Show the tray icon. The window opens too, unless Windows started the app at login."""
        self.tray.set_autostart_checked(autostart.is_enabled())
        self.tray.show()
        if not self.config.ready:
            self.open_settings(first_run=True)
        else:
            self.window.start_history()
        self.start_listener()
        if not background or not self.config.ready:
            self.window.bring_to_front()

    def quit(self) -> None:
        self.stop_listener()
        self.approvals_timer.stop()
        self.stop_calls()
        self.window.quit_for_good()
        self.tray.hide()
        self.window.close()
        self.qt.quit()

    # -- settings ------------------------------------------------------------------------------ #

    def open_settings(self, first_run: bool = False) -> None:
        dialog = SettingsDialog(self.config, self.window if self.window.isVisible() else None, first_run=first_run)
        dialog.setWindowIcon(self.window.windowIcon())
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.config = dialog.config()
            config_module.save(self.config, self._config_path)
            self.start_listener()  # the server or the token may have changed
            self.window.start_history()  # and so may the user, and their conversations

    def open_account(self) -> None:
        """Your settings: the Account page (the connection to the server is changed from there)."""
        if not self.config.ready:
            self.open_settings(first_run=True)
            return
        self.window.bring_to_front()
        self.window.go("account")

    def sign_out(self) -> None:
        """Forget the token on this computer and ask to sign in again."""
        self.config = replace(self.config, token="")
        config_module.save(self.config, self._config_path)
        self.stop_listener()
        self.window.shell.set_admin(False)
        self.open_settings(first_run=True)

    def _theme_chosen(self, preference: str) -> None:
        self.config = replace(self.config, theme=preference)
        config_module.save(self.config, self._config_path)

    def open_tasks(self) -> None:
        """The to-do list, in the window."""
        self.window.bring_to_front()
        self.window.open_tasks()

    def _set_autostart(self, enabled: bool) -> None:
        try:
            autostart.set_enabled(enabled)
        except OSError as error:
            self.tray.notify(APP_NAME, f"Could not change the start-up setting: {error}")
            self.tray.set_autostart_checked(autostart.is_enabled())

    # -- reminders ------------------------------------------------------------------------------- #

    def start_listener(self) -> None:
        self.stop_listener()
        self.server_state = None
        self.window.set_state(None)
        self.tray.set_state(None)
        if not self.config.ready:
            return
        config = self.config
        self.listener = ReminderWorker(lambda: self._api_factory(config), parent=self)
        self.listener.reminder.connect(self.on_reminder)
        self.listener.notification.connect(self.on_notification)
        self.listener.approval.connect(self.on_approval)
        self.listener.approval_resolved.connect(self.on_approval_resolved)
        self.listener.job.connect(lambda _event: self.do_jobs())
        self.listener.connection.connect(self._connection)
        self.listener.server.connect(self._server_said)
        self.listener.rejected.connect(self._token_rejected)
        self.listener.start()

    def stop_listener(self) -> None:
        listener, self.listener = self.listener, None
        if listener is not None:
            listener.stop()
            listener.wait(3000)
            listener.deleteLater()

    def _token_rejected(self, reason: str) -> None:
        """The server no longer knows this sign-in: ask for the password instead of retrying with it."""
        self.tray.notify(APP_NAME, reason)
        self.sign_out()

    def _connection(self, online: bool) -> None:
        if not online:  # the server cannot be reached (or the connection broke without a word)
            self._set_server_state("down")
            return
        self.refresh_approvals()  # what was asked while the app was off is waiting
        self.do_jobs()
        self.approvals_timer.start()

    def _server_said(self, state: str) -> None:
        self._set_server_state(from_server(state))

    def _set_server_state(self, state: str) -> None:
        """Show the state of the server everywhere, and say so when it changes."""
        before, self.server_state = self.server_state, state
        if state == before:
            return
        self.window.set_state(state)
        self.tray.set_state(state)
        if not (state == "running" and before is None):  # nothing to say when the app finds all well
            self.tray.notify(APP_NAME, CHANGED[state])

    def on_reminder(self, event: dict) -> None:
        title, text, detail = describe_reminder(event)
        status = STATUS[self.server_state or "running"]  # a reminder reached us, so the server is there
        self.tray.notify(title, "\n".join(part for part in (text, detail, status) if part))
        self.window.add_reminder(text, detail)

    # -- requests for permission ------------------------------------------------------------------ #

    def on_approval(self, event: dict) -> None:
        """A request nobody answered in time reached this device: announce it, and show it in the list of the rail."""
        title, text = describe_approval(event)
        self._approval_notice_at = time.monotonic()
        self.tray.notify(title, text)
        self.refresh_approvals()

    def on_approval_resolved(self, _event: dict) -> None:
        self.refresh_approvals()

    def refresh_approvals(self) -> None:
        if not self.config.ready:
            self.window.shell.set_approvals(0)
            return
        self._call(lambda api: api.approvals(), lambda result, error: None if error else self.window.shell.set_approvals(len(list(result))))  # type: ignore[arg-type]

    def _notice_clicked(self) -> None:
        """A click on a notification that announced a request for permission: the requests."""
        if time.monotonic() - self._approval_notice_at < APPROVAL_NOTICE_SECONDS:
            self.window.show_approvals()

    # -- folders of this computer ------------------------------------------------------------------ #

    def do_jobs(self) -> None:
        """Clara asked something of a folder of this computer: do it, here, and say how it went."""
        if not self.config.ready:
            return
        registry = FolderRegistry.load()
        if not registry.folders:
            return
        if self._jobs_running:
            self._jobs_again = True
            return
        self._jobs_running = True
        self._call(lambda api: run_jobs(api, LocalFolders(registry)), self._jobs_done)

    def _jobs_done(self, _result: object, _error: str) -> None:
        self._jobs_running = False
        if self._jobs_again:
            self._jobs_again = False
            self.do_jobs()

    def on_notification(self, event: dict) -> None:
        """Pop it up, unless it is about the conversation the user is looking at right now."""
        title, text, detail = describe_notification(event)
        watching = self.window.isVisible() and self.window.isActiveWindow()
        if not (watching and event.get("conversation") == self.window.conversation):
            self.tray.notify(title, "\n".join(part for part in (text, detail) if part))
        self.window.add_notification(title, text, detail)
