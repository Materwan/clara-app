"""Start with Windows: a value in HKCU\\Software\\Microsoft\\Windows\\CurrentVersion\\Run."""

from __future__ import annotations

import sys
from pathlib import Path

RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
VALUE_NAME = "ClaraApp"
BACKGROUND_FLAG = "--background"  # started by Windows: stay in the tray, do not open the window


def available() -> bool:
    return sys.platform == "win32"


def command() -> str:
    """The command line Windows runs at login (no console window: pythonw, not python)."""
    exe = Path(sys.executable)
    if getattr(sys, "frozen", False):  # a packaged build
        return f'"{exe}" {BACKGROUND_FLAG}'
    windowless = exe.with_name("pythonw.exe")
    return f'"{windowless if windowless.exists() else exe}" -m clara_app {BACKGROUND_FLAG}'


def is_enabled(key_path: str = RUN_KEY) -> bool:
    if not available():
        return False
    import winreg

    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path) as key:
            winreg.QueryValueEx(key, VALUE_NAME)
        return True
    except FileNotFoundError:
        return False


def set_enabled(enabled: bool, key_path: str = RUN_KEY) -> None:
    if not available():
        return
    import winreg

    if enabled:
        with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, key_path, 0, winreg.KEY_SET_VALUE) as key:
            winreg.SetValueEx(key, VALUE_NAME, 0, winreg.REG_SZ, command())
        return
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path, 0, winreg.KEY_SET_VALUE) as key:
            winreg.DeleteValue(key, VALUE_NAME)
    except FileNotFoundError:
        pass

