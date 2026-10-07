"""What the security review found in the app: links, the files that hold the token, and the folders Clara may enter."""

import json
import os
import stat
from pathlib import Path

import pytest

from clara_app.config import Config, load, save
from clara_app.links import is_safe
from clara_app.local_folders import FolderError, FolderRegistry


@pytest.mark.parametrize("url", ["https://example.com/a?b=c", "http://localhost:8765", "mailto:someone@example.com", " https://x.org "])
def test_web_pages_and_mail_may_be_opened(url):
    assert is_safe(url)


@pytest.mark.parametrize("url", [
    "file:///C:/Windows/System32/calc.exe", "smb://attacker/share", "\\\\attacker\\share\\x", "//attacker/x", "ms-msdt:/id PCWDiagnostic",
    "search-ms:query=x&crumb=location:\\\\attacker\\share", "javascript:alert(1)", "data:text/html,x", "https:", "mailto:", "",
    "https://example.com/\nfile:///x", "custom-handler://run",
])
def test_everything_else_is_refused(url):
    assert not is_safe(url)


@pytest.mark.skipif(os.name != "posix", reason="file modes")
def test_the_token_file_is_for_its_owner_only(tmp_path):
    path = tmp_path / "clara-app" / "config.json"
    save(Config(url="https://x", token="clu_secret", user_id="u"), path)
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert load(path, env={}).token == "clu_secret" and not path.with_name("config.json.tmp").exists()


@pytest.mark.skipif(os.name != "posix", reason="file modes")
def test_the_folder_list_is_for_its_owner_only(tmp_path):
    folder = tmp_path / "docs"
    folder.mkdir()
    registry = FolderRegistry.load(tmp_path / "computer-folders.json")
    registry.add(folder)
    assert stat.S_IMODE((tmp_path / "computer-folders.json").stat().st_mode) == 0o600
    assert json.loads((tmp_path / "computer-folders.json").read_text())["folders"]


def test_a_folder_that_is_too_wide_is_refused(tmp_path, monkeypatch):
    registry = FolderRegistry.load(tmp_path / "computer-folders.json")
    home = tmp_path / "home" / "me"
    (home / "work").mkdir(parents=True)
    monkeypatch.setattr(Path, "home", lambda: home)
    for wide in (Path(home.anchor), home, home.parent):
        with pytest.raises(FolderError, match="too wide"):
            registry.add(wide)
    assert registry.add(home / "work") == "work"
