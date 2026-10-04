"""Sending files to a project (Qt-free): single files, or the text files of a folder.

The server reads what it is given (text, code, PDF, Word, .zip) and leaves out what is not text; a folder is
first sorted here, so that dependencies and build output (node_modules, .venv...) are not even sent.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from .api import ClaraApi

BATCH_BYTES = 6_000_000  # bytes of files per request (base64 makes them a third bigger)
BATCH_FILES = 200
MAX_FILE_BYTES = 30_000_000
FILTER = "Files (*.*);;Text and code (*.txt *.md *.py *.c *.cpp *.h *.js *.ts *.json);;PDF (*.pdf);;Word (*.docx);;Archives (*.zip)"

# What a folder holds that is not worth sending (the server leaves it out too: see its ingest.py)
IGNORED_DIRS = frozenset({
    ".git", ".hg", ".svn", "node_modules", "bower_components", "__pycache__", ".venv", "venv", ".tox", ".nox",
    ".mypy_cache", ".pytest_cache", ".ruff_cache", ".cache", ".gradle", ".idea", ".next", ".nuxt", ".svelte-kit",
    ".parcel-cache", ".turbo", "dist", "build", "target", "out", "coverage", "htmlcov", ".terraform", ".dart_tool",
    "Pods", "DerivedData", ".eggs",
})
IGNORED_FILES = frozenset({
    "package-lock.json", "yarn.lock", "pnpm-lock.yaml", "poetry.lock", "Cargo.lock", "composer.lock", "Gemfile.lock",
    "uv.lock", "Pipfile.lock", "go.sum", ".DS_Store", "Thumbs.db", "desktop.ini",
})
IGNORED_SUFFIXES = (".min.js", ".min.css", ".map", ".pyc", ".pyo", ".lock")
BINARY = frozenset(
    "png jpg jpeg gif bmp ico webp tif tiff psd heic avif mp3 wav ogg flac m4a aac mp4 mkv avi mov webm wmv exe dll so "
    "dylib bin o a lib obj class jar war apk msi dmg iso woff woff2 ttf otf eot 7z rar gz tgz bz2 xz tar zst sqlite "
    "sqlite3 db pkl pickle npy npz pt pth onnx h5 parquet doc xls xlsx ppt pptx odt ods odp key pages numbers".split()
)


@dataclass(frozen=True)
class Entry:
    path: str  # where it goes in the project
    source: Path  # where it is on this computer
    size: int


@dataclass
class Sent:
    """What sending files did."""

    added: int = 0
    skipped: list[dict] = field(default_factory=list)  # `path`, `reason` (the first ones)
    skipped_count: int = 0
    project: dict | None = None  # the project as the server describes it after the last batch
    error: str = ""  # why it stopped before the end


def left_out(path: str) -> str:
    """Why a file of a folder is not sent, or ""."""
    parts = path.split("/")
    for folder in parts[:-1]:
        if folder in IGNORED_DIRS or folder.endswith(".egg-info"):
            return f"in {folder}/"
    name = parts[-1]
    if name in IGNORED_FILES or name.lower().endswith(IGNORED_SUFFIXES):
        return "generated file"
    _, dot, ext = name.rpartition(".")
    if dot and ext.lower() in BINARY:
        return "not a text file"
    return ""


def file_entries(paths: list[str]) -> tuple[list[Entry], list[dict]]:
    """Files chosen one by one: each goes at the top of the project, under its name."""
    entries, skipped = [], []
    for name in paths:
        path = Path(name)
        try:
            size = path.stat().st_size
        except OSError as error:
            skipped.append({"path": path.name, "reason": f"cannot be read ({error.strerror or error})"})
            continue
        if size > MAX_FILE_BYTES:
            skipped.append({"path": path.name, "reason": f"too big ({size // 1_000_000} MB)"})
        else:
            entries.append(Entry(path.name, path, size))
    return entries, skipped


def folder_entries(folder: str | Path) -> tuple[list[Entry], list[dict]]:
    """The files of a folder worth sending, under the folder's name; the others with the reason."""
    root = Path(folder)
    entries, skipped = [], []
    for directory, folders, names in os.walk(root):
        folders[:] = sorted(f for f in folders if f not in IGNORED_DIRS and not f.endswith(".egg-info"))
        for name in sorted(names):
            source = Path(directory) / name
            path = "/".join((root.name, *source.relative_to(root).parts))
            reason = left_out(path)
            if not reason:
                try:
                    size = source.stat().st_size
                except OSError:
                    reason = "cannot be read"
                else:
                    if size > MAX_FILE_BYTES:
                        reason = f"too big ({size // 1_000_000} MB)"
            if reason:
                skipped.append({"path": path, "reason": reason})
            else:
                entries.append(Entry(path, source, size))
    return entries, skipped


def batches(entries: list[Entry]) -> list[list[Entry]]:
    """The entries in groups small enough for one request each."""
    groups: list[list[Entry]] = []
    current: list[Entry] = []
    size = 0
    for entry in entries:
        if current and (len(current) >= BATCH_FILES or size + entry.size > BATCH_BYTES):
            groups.append(current)
            current, size = [], 0
        current.append(entry)
        size += entry.size
    if current:
        groups.append(current)
    return groups


def send(
    api: ClaraApi, project_id: int, entries: list[Entry], progress: Callable[[int, int], None] | None = None
) -> Sent:
    """Send the entries in batches (blocking: run it off the UI thread). `progress(sent, total)` after each."""
    sent = Sent()
    done = 0
    for group in batches(entries):
        files = []
        for entry in group:
            try:
                files.append((entry.path, entry.source.read_bytes()))
            except OSError as error:
                sent.skipped.append({"path": entry.path, "reason": f"cannot be read ({error.strerror or error})"})
                sent.skipped_count += 1
        if files:
            try:
                result = api.upload_files(project_id, files)
            except Exception as error:
                sent.error = str(error)
                return sent
            sent.added += len(result.get("added", [])) + len(result.get("replaced", []))
            sent.skipped += result.get("skipped", [])
            sent.skipped_count += int(result.get("skipped_count", len(result.get("skipped", []))))
            sent.project = result.get("project", sent.project)
        done += len(group)
        if progress is not None:
            progress(done, len(entries))
    return sent


def size_text(characters: int) -> str:
    if characters < 1_000:
        return f"{characters} chars"
    if characters < 1_000_000:
        return f"{characters / 1_000:.0f} k chars"
    return f"{characters / 1_000_000:.1f} M chars"
