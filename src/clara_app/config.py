"""The settings of the app, saved in %APPDATA%\\clara-app\\config.json.

CLARA_URL and CLARA_TOKEN in the environment fill in what the file does not say.
"""

from __future__ import annotations

import getpass
import json
import os
from dataclasses import asdict, dataclass, fields
from pathlib import Path
from typing import Mapping
from urllib.parse import urlsplit

DEFAULT_URL = "http://127.0.0.1:8765"


def config_dir(env: Mapping[str, str] | None = None) -> Path:
    env = os.environ if env is None else env
    base = env.get("APPDATA") or env.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(base) / "clara-app"


@dataclass
class Config:
    url: str = DEFAULT_URL
    token: str = ""
    user_id: str = ""
    user_name: str = ""
    theme: str = "auto"  # "auto" (follow Windows), "light" or "dark"

    @property
    def ready(self) -> bool:
        """Enough to talk to a server."""
        return bool(self.url.strip() and self.token.strip() and self.user_id.strip())


def url_hint(url: str) -> str:
    """A word of advice about the server address typed, or "" when it looks fine."""
    try:
        parts = urlsplit(url.strip())
        host = (parts.hostname or "").lower()
    except ValueError:
        return ""
    if parts.scheme != "http" or not host:
        return ""
    if host.endswith(".ts.net"):
        return "A Tailscale address is served over HTTPS: use https:// (the address clara-server shows in /status)."
    if host not in ("localhost", "127.0.0.1", "::1"):
        return "Plain http: the token crosses the network unencrypted. Prefer an https:// address (Tailscale, a proxy)."
    return ""


def default_user() -> str:
    try:
        return getpass.getuser()
    except Exception:
        return ""


def load(path: Path | None = None, env: Mapping[str, str] | None = None) -> Config:
    """The saved settings; the environment supplies a URL or token the file leaves empty."""
    env = os.environ if env is None else env
    path = path or config_dir(env) / "config.json"
    saved: dict = {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            saved = {f.name: str(data[f.name]) for f in fields(Config) if f.name in data}
    except (OSError, ValueError):
        pass
    config = Config(**saved)
    if not saved.get("url") and env.get("CLARA_URL"):
        config.url = env["CLARA_URL"].strip()
    if not config.token and env.get("CLARA_TOKEN"):
        config.token = env["CLARA_TOKEN"].strip()
    if not config.user_id:
        config.user_id = default_user()
    return config


def save(config: Config, path: Path | None = None) -> None:
    path = path or config_dir() / "config.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(asdict(config), indent=2), encoding="utf-8")
