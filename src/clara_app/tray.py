"""The icon in the Windows notification area.

Left click shows or hides the window; right click opens a menu. `notify` pops a Windows
notification, and clicking it opens the window.
"""

from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtGui import QAction
from PySide6.QtWidgets import QMenu, QSystemTrayIcon

from . import APP_NAME, autostart
from .icon import make_icon
from .status import STATUS

NOTIFICATION_MS = 10_000


class Tray(QSystemTrayIcon):
    toggle_requested = Signal()  # left click
    open_requested = Signal()  # "Open" or a click on a notification
    settings_requested = Signal()
    tasks_requested = Signal()
    autostart_toggled = Signal(bool)
    quit_requested = Signal()

    def __init__(self, parent=None):
        super().__init__(make_icon(), parent)
        self._state: str | None = None
        self._refresh_tooltip()

        menu = QMenu()
        self._open = QAction(f"Open {APP_NAME}", menu)
        self._open.triggered.connect(self.open_requested)
        menu.addAction(self._open)
        self._tasks = QAction("Tasks…", menu)
        self._tasks.triggered.connect(self.tasks_requested)
        menu.addAction(self._tasks)
        menu.addSeparator()
        self._settings = QAction("Settings…", menu)
        self._settings.triggered.connect(self.settings_requested)
        menu.addAction(self._settings)
        self._autostart = QAction("Start with Windows", menu)
        self._autostart.setCheckable(True)
        self._autostart.setEnabled(autostart.available())
        self._autostart.toggled.connect(self.autostart_toggled)
        menu.addAction(self._autostart)
        menu.addSeparator()
        self._quit = QAction("Quit", menu)
        self._quit.triggered.connect(self.quit_requested)
        menu.addAction(self._quit)
        self.setContextMenu(menu)
        self._menu = menu  # the tray does not own its menu

        self.activated.connect(self._activated)
        self.messageClicked.connect(self.open_requested)

    def _activated(self, reason: QSystemTrayIcon.ActivationReason) -> None:
        if reason == QSystemTrayIcon.ActivationReason.Trigger:  # a single left click
            self.toggle_requested.emit()
        elif reason == QSystemTrayIcon.ActivationReason.DoubleClick:
            self.open_requested.emit()

    def set_autostart_checked(self, enabled: bool) -> None:
        """Show the state without triggering `autostart_toggled`."""
        self._autostart.blockSignals(True)
        self._autostart.setChecked(enabled)
        self._autostart.blockSignals(False)

    def set_state(self, state: str | None) -> None:
        """The state of the server: "running", "stopping", "down" or None (not known yet). The icon is
        grey when Clara is not running."""
        self._state = state
        self.setIcon(make_icon(state != "down"))
        self._refresh_tooltip()

    def _refresh_tooltip(self) -> None:
        self.setToolTip(STATUS.get(self._state, APP_NAME))

    def notify(self, title: str, text: str) -> None:
        self.showMessage(title, text, make_icon(), NOTIFICATION_MS)
