"""The application: the tray icon, the window, and the reminder listener, wired together."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from PySide6.QtCore import QObject
from PySide6.QtWidgets import QApplication, QDialog

from . import APP_NAME, autostart, config as config_module
from .api import ClaraApi
from .chat_window import ChatWindow
from .config import Config
from .settings_dialog import SettingsDialog
from .status import CHANGED, STATUS, from_server
from .tray import Tray
from .workers import ReminderWorker

LATE_SECONDS = 120  # a reminder shown this long after it fired is announced as missed


def describe_reminder(event: dict, now: datetime | None = None) -> tuple[str, str, str]:
    """`(title, text, detail)` of a reminder event, for a notification. The text is what Clara wrote for it
    (the reminder's own text if she could not)."""
    now = now or datetime.now(timezone.utc)
    missed = (now - datetime.fromisoformat(event["fired_at"])).total_seconds() > LATE_SECONDS
    title = f"{APP_NAME} reminder" + (" (missed)" if missed else "")
    details = []
    if missed:
        due = datetime.fromisoformat(event["due_at"]).astimezone().strftime("%Y-%m-%d %H:%M")
        details.append(f"It was due {due}.")
    if event.get("from"):
        details.append(f"Set by {event['from']}.")
    return title, str(event.get("message") or event["text"]), " ".join(details)


class ClaraApplication(QObject):
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
        self.listener: ReminderWorker | None = None
        self.server_state: str | None = None  # "running", "stopping", "down"; None until the server answers

        self.window = ChatWindow(lambda: self.config, api_factory)
        self.window.settings_requested.connect(self.open_settings)
        self.tray = Tray()
        self.tray.toggle_requested.connect(self.window.toggle)
        self.tray.open_requested.connect(self.window.bring_to_front)
        self.tray.settings_requested.connect(self.open_settings)
        self.tray.autostart_toggled.connect(self._set_autostart)
        self.tray.quit_requested.connect(self.quit)

    # -- life cycle ------------------------------------------------------------------------ #

    def start(self, background: bool = False) -> None:
        """Show the tray icon. The window opens too, unless Windows started the app at login."""
        self.tray.set_autostart_checked(autostart.is_enabled())
        self.tray.show()
        if not self.config.ready:
            self.open_settings(first_run=True)
        self.start_listener()
        if not background or not self.config.ready:
            self.window.bring_to_front()

    def quit(self) -> None:
        self.stop_listener()
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
        self.listener.connection.connect(self._connection)
        self.listener.server.connect(self._server_said)
        self.listener.start()

    def stop_listener(self) -> None:
        listener, self.listener = self.listener, None
        if listener is not None:
            listener.stop()
            listener.wait(3000)
            listener.deleteLater()

    def _connection(self, online: bool) -> None:
        if not online:  # the server cannot be reached (or the connection broke without a word)
            self._set_server_state("down")

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
