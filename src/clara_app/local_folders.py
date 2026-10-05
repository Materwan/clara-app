"""The folders of this computer that Clara may work in, and the code that works in them for her.

The person adds a folder here (Integrations page); it gets an *alias*, and the server only ever knows that alias and the
id of this computer. When Clara wants to read or change something there, the server hands the app a *job* ("write
notes.md in the folder docs"); the app does it, and only in a folder it was told about, at a path that stays inside it.
So even a server that was taken over cannot reach the rest of this disk: the list of folders lives here.

Qt-free: the app runs `run_jobs` on a worker thread.
"""

from __future__ import annotations

import json
import os
import re
import socket
import uuid
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath, PureWindowsPath

from .api import ApiError, ClaraApi
from .config import config_dir
from .documents import DocumentError, read_document

REGISTRY_FILE = "computer-folders.json"
LIST_LIMIT = 300
READ_LINES = 400  # lines a read gives at once
READ_CHARS = 40_000
SEARCH_MATCHES = 60
SEARCH_LINE = 240
SEARCH_FILE_BYTES = 2_000_000
SEARCH_FILES = 5_000
MAX_WRITE = 1_000_000
RESULT_LIMIT = 100_000
IGNORED_DIRS = frozenset({".git", ".hg", ".svn", "node_modules", "__pycache__", ".venv", "venv", ".mypy_cache", ".pytest_cache"})
BINARY = frozenset(
    "png jpg jpeg gif bmp ico webp mp3 wav mp4 mkv avi mov exe dll so bin zip 7z rar gz tar iso woff woff2 ttf otf sqlite db".split()
)


class FolderError(Exception):
    """A job that cannot be done: the message says why (it goes back to Clara as it is)."""


@dataclass
class FolderRegistry:
    """What this computer is called to the server, and the folders it lets Clara into (alias -> path)."""

    path: Path
    device: str = ""
    name: str = ""
    folders: dict[str, str] = field(default_factory=dict)

    @classmethod
    def load(cls, path: Path | None = None) -> FolderRegistry:
        path = path or config_dir() / REGISTRY_FILE
        registry = cls(path)
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            registry.device = str(data.get("device", ""))
            registry.name = str(data.get("name", ""))
            registry.folders = {str(k): str(v) for k, v in dict(data.get("folders", {})).items()}
        except (OSError, ValueError, TypeError):
            pass
        if not registry.device:
            registry.device = uuid.uuid4().hex[:16]
            registry.name = socket.gethostname()
            registry.save()
        return registry

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            json.dumps({"device": self.device, "name": self.name, "folders": self.folders}, indent=2), encoding="utf-8"
        )

    def add(self, folder: str | Path) -> str:
        """Let Clara work in this folder; returns its alias (the folder's name, made unique)."""
        real = Path(folder).resolve()
        if not real.is_dir():
            raise FolderError(f"{folder} is not a folder.")
        for alias, existing in self.folders.items():
            if Path(existing).resolve() == real:
                return alias
        base = re.sub(r"[^\w.-]+", "-", real.name).strip("-") or "folder"
        alias, number = base, 2
        while alias in self.folders:
            alias, number = f"{base}-{number}", number + 1
        self.folders[alias] = str(real)
        self.save()
        return alias

    def remove(self, alias: str) -> None:
        if self.folders.pop(alias, None) is not None:
            self.save()

    def base(self, alias: str) -> Path:
        folder = self.folders.get(alias)
        if folder is None:
            raise FolderError("That folder is not on this computer any more (it was removed from the Clara app).")
        base = Path(folder).resolve()
        if not base.is_dir():
            raise FolderError(f"The folder {folder} does not exist on this computer any more.")
        return base


def relative(path: object) -> str:
    """A path inside a folder as Clara wrote it, made safe: "" is the folder itself."""
    text = str(path or "").strip().replace("\\", "/")
    if text in ("", ".", "/"):
        return ""
    if PureWindowsPath(text).drive or PurePosixPath(text).is_absolute():
        raise FolderError("Give a path inside the folder, not an absolute one.")
    parts = [part for part in text.split("/") if part not in ("", ".")]
    if ".." in parts or any("\x00" in part for part in parts):
        raise FolderError("A path may not go up (..).")
    return "/".join(parts)


def inside(base: Path, path: object) -> tuple[Path, str]:
    """(the resolved path, its clean relative form); it must stay inside `base` (links that leave it are refused)."""
    rel = relative(path)
    try:
        resolved = (base / rel).resolve() if rel else base
    except (OSError, RuntimeError):
        raise FolderError(f"{rel}: not a usable path.") from None
    if resolved != base and not resolved.is_relative_to(base):
        raise FolderError(f"{rel}: that leads outside the folder.")
    return resolved, rel


def _text_of(path: Path) -> str:
    if path.suffix.lower().lstrip(".") in BINARY:
        raise FolderError(f"{path.name} is not a text file.")
    if path.suffix.lower() == ".pdf":
        try:
            return read_document(path).text
        except DocumentError as error:
            raise FolderError(str(error)) from None
    data = path.read_bytes()
    if b"\x00" in data[:8192]:
        raise FolderError(f"{path.name} is not a text file.")
    try:
        return data.decode("utf-8").removeprefix("﻿")
    except UnicodeDecodeError:
        return data.decode("cp1252", errors="replace")


def _lines(name: str, text: str, start: int, end: int | None) -> str:
    lines = text.splitlines()
    start = max(1, start)
    if start > len(lines):
        return f"{name} has only {len(lines)} lines."
    last = min(len(lines), end if end else start + READ_LINES - 1, start + READ_LINES - 1)
    out, used = [], 0
    for number in range(start, last + 1):
        line = f"{number:>5}  {lines[number - 1]}"
        if used + len(line) > READ_CHARS and out:
            last = number - 1
            break
        out.append(line)
        used += len(line) + 1
    header = f"{name}, lines {start}-{last} of {len(lines)}" + (f" (read on with start_line={last + 1})" if last < len(lines) else "")
    return header + ":\n" + "\n".join(out)


class LocalFolders:
    """Does the jobs of the server inside the folders of a registry."""

    def __init__(self, registry: FolderRegistry):
        self.registry = registry

    def run(self, alias: str, op: str, args: dict) -> str:
        base = self.registry.base(alias)
        handler = getattr(self, f"_{op}", None)
        if handler is None or op.startswith("_"):
            raise FolderError(f"This computer does not do {op}.")
        try:
            result = handler(base, args)
        except FolderError:
            raise
        except OSError as error:
            raise FolderError(f"{error.strerror or error}") from None
        return result if len(result) <= RESULT_LIMIT else result[: RESULT_LIMIT - 1] + "…"

    def _list(self, base: Path, args: dict) -> str:
        path, rel = inside(base, args.get("path"))
        if not path.is_dir():
            raise FolderError(f"{rel or '/'} is not a folder.")
        entries = sorted(path.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower()))
        lines = []
        for entry in entries[:LIST_LIMIT]:
            try:
                lines.append(f"{entry.name}/" if entry.is_dir() else f"{entry.name} ({entry.stat().st_size:,} bytes)")
            except OSError:
                lines.append(entry.name)
        if len(entries) > LIST_LIMIT:
            lines.append(f"[{len(entries) - LIST_LIMIT} more]")
        return "\n".join(lines) or "(empty)"

    def _read(self, base: Path, args: dict) -> str:
        path, rel = inside(base, args.get("path"))
        if not path.is_file():
            raise FolderError(f"{rel or '/'} is not a file.")
        end = int(args["end_line"]) if args.get("end_line") else None
        return _lines(rel, _text_of(path), int(args.get("start_line") or 1), end)

    def _search(self, base: Path, args: dict) -> str:
        path, _ = inside(base, args.get("path"))
        query = str(args.get("query") or "").strip()
        if not query:
            raise FolderError("query is empty.")
        try:
            pattern = re.compile(query if args.get("regex") else re.escape(query), re.IGNORECASE)
        except re.error as error:
            raise FolderError(f"not a valid regular expression ({error}).") from None
        matches, files, scanned = [], 0, 0
        for folder, dirs, names in os.walk(path):
            dirs[:] = sorted(d for d in dirs if d not in IGNORED_DIRS)
            for name in sorted(names):
                file = Path(folder, name)
                if file.is_symlink() or file.suffix.lower().lstrip(".") in BINARY:
                    continue
                scanned += 1
                if scanned > SEARCH_FILES:
                    break
                try:
                    if file.stat().st_size > SEARCH_FILE_BYTES:
                        continue
                    text = _text_of(file)
                except (OSError, FolderError):
                    continue
                short = file.relative_to(base).as_posix()
                found = [
                    f"{short}:{n}: {line.strip()[:SEARCH_LINE]}"
                    for n, line in enumerate(text.splitlines(), 1) if pattern.search(line)
                ][: SEARCH_MATCHES - len(matches)]
                if found:
                    files += 1
                    matches += found
                if len(matches) >= SEARCH_MATCHES:
                    break
            if len(matches) >= SEARCH_MATCHES or scanned > SEARCH_FILES:
                break
        if not matches:
            return f"No match for {query!r}."
        note = f"\n[only the first {SEARCH_MATCHES} shown]" if len(matches) >= SEARCH_MATCHES else ""
        return f"Matches in {files} file{'s' if files != 1 else ''}:\n" + "\n".join(matches) + note

    def _write(self, base: Path, args: dict) -> str:
        mode = str(args.get("mode") or "create")
        if mode not in ("create", "overwrite", "append"):
            raise FolderError("mode must be one of: create, overwrite, append.")
        content = args.get("content")
        if not isinstance(content, str) or len(content) > MAX_WRITE:
            raise FolderError(f"content must be text of at most {MAX_WRITE:,} characters.")
        path, rel = inside(base, args.get("path"))
        if not rel:
            raise FolderError("Give the path of the file to write.")
        if path.is_dir():
            raise FolderError(f"{rel} is a folder.")
        exists = path.exists()
        if mode == "create" and exists:
            raise FolderError(f"{rel} already exists: use mode overwrite or append.")
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a" if mode == "append" else "w", encoding="utf-8", newline="") as handle:
            handle.write(content)
        verb = {"create": "Created", "overwrite": "Replaced" if exists else "Created", "append": "Added to"}[mode]
        return f"{verb} {rel} ({len(content):,} characters)."

    def _delete(self, base: Path, args: dict) -> str:
        path, rel = inside(base, args.get("path"))
        if not rel:
            raise FolderError("The folder itself cannot be deleted.")
        if path.is_symlink() or path.is_file():
            path.unlink()
            return f"Deleted {rel}."
        if path.is_dir():
            try:
                path.rmdir()
            except OSError:
                raise FolderError(f"{rel} is a folder with something in it: delete its files first.") from None
            return f"Deleted the empty folder {rel}."
        raise FolderError(f"No such file: {rel}.")

    def _move(self, base: Path, args: dict) -> str:
        source, rel = inside(base, args.get("path"))
        dest, to = inside(base, args.get("dest"))
        if not rel or not to:
            raise FolderError("Give the path to move and where to.")
        if not source.exists():
            raise FolderError(f"No such file: {rel}.")
        if dest.exists():
            raise FolderError(f"{to} already exists: nothing was moved.")
        dest.parent.mkdir(parents=True, exist_ok=True)
        source.rename(dest)
        return f"Moved {rel} to {to}."


def run_jobs(api: ClaraApi, folders: LocalFolders) -> int:
    """Fetch what Clara asked of this computer, do it, and say how it went. Returns how many jobs were done."""
    done = 0
    for job in api.computer_jobs(folders.registry.device):
        try:
            text, ok = folders.run(str(job.get("alias", "")), str(job.get("op", "")), dict(job.get("args") or {})), True
        except FolderError as error:
            text, ok = str(error), False
        except Exception as error:  # a job must never take the app down
            text, ok = f"The app could not do it: {error}", False
        try:
            api.finish_job(int(job["id"]), ok, text)
        except ApiError:
            continue  # the server will give the job up by itself
        done += 1
    return done
