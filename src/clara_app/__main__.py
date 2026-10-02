"""`clara-app` or `python -m clara_app`."""

from __future__ import annotations

import sys

from PySide6.QtWidgets import QApplication

from . import APP_NAME
from .autostart import BACKGROUND_FLAG
from .icon import make_icon


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv if argv is None else argv)
    background = BACKGROUND_FLAG in argv
    if sys.platform == "win32":  # its own identity for notifications and the taskbar
        try:
            import ctypes

            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("Clara.DesktopApp")
        except Exception:
            pass

    qt = QApplication([arg for arg in argv if arg != BACKGROUND_FLAG])
    qt.setApplicationName(APP_NAME)
    qt.setWindowIcon(make_icon())
    qt.setQuitOnLastWindowClosed(False)  # closing the window leaves the app in the notification area

    from .single import SingleInstance

    guard = SingleInstance()
    if not guard.acquire():  # already running: it was asked to show its window
        return 0

    from .app import ClaraApplication

    application = ClaraApplication(qt)
    guard.show_requested.connect(application.window.bring_to_front)
    application.start(background=background)
    return qt.exec()


if __name__ == "__main__":
    raise SystemExit(main())
