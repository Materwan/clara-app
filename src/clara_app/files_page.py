"""The Files page: the Markdown files Clara wrote for you (notes, summaries, a README), to read, copy, save or delete.
They are kept by the server and are the same on the web site."""

from __future__ import annotations

from pathlib import Path
from typing import Callable

from PySide6.QtCore import Qt
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import (
    QFileDialog,
    QHBoxLayout,
    QListWidget,
    QListWidgetItem,
    QPlainTextEdit,
    QStackedWidget,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from .api import ClaraApi
from .config import Config
from .widgets import Calls, EmptyState, Page, Segmented, StatusLine, ago, button, confirm, label

ID = Qt.ItemDataRole.UserRole


def size_text(size: int) -> str:
    return f"{size:,} characters" if size < 10_000 else f"{size / 1000:,.0f}k characters"


class FilesPage(Page, Calls):
    page_title = "Files"

    def __init__(self, get_config: Callable[[], Config], api_factory: Callable[[Config], ClaraApi], host=None):
        super().__init__()
        self._init_calls(get_config, api_factory)
        self.files: list[dict] = []
        self.file: dict | None = None  # the one shown, with its text

        self.list = QListWidget()
        self.list.setFixedWidth(280)
        self.list.currentItemChanged.connect(self._picked)
        self.name = label("", heading="section")
        self.info = label("", tone_="muted")
        self.mode = Segmented([("preview", "Preview"), ("source", "Markdown")], "preview")
        self.mode.chosen.connect(self._mode)
        self.preview = QTextBrowser()
        self.preview.setOpenExternalLinks(True)
        self.source = QPlainTextEdit()
        self.source.setReadOnly(True)
        self.source.setLineWrapMode(QPlainTextEdit.LineWrapMode.WidgetWidth)
        self.source.setStyleSheet("font-family: Consolas, monospace;")
        self.stack = QStackedWidget()
        self.stack.addWidget(self.preview)
        self.stack.addWidget(self.source)
        self.copy_button = button("Copy", "", self.copy)
        self.save_button = button("Save as…", "", self.save_as)
        self.delete_button = button("Delete…", "danger", self.delete)
        self.status = StatusLine()

        head = QHBoxLayout()
        head.addWidget(self.name, 1)
        head.addWidget(self.mode)
        buttons = QHBoxLayout()
        buttons.addWidget(self.status, 1)
        buttons.addWidget(self.copy_button)
        buttons.addWidget(self.save_button)
        buttons.addWidget(self.delete_button)
        self.detail = QWidget()
        right = QVBoxLayout(self.detail)
        right.setContentsMargins(0, 0, 0, 0)
        right.setSpacing(8)
        right.addLayout(head)
        right.addWidget(self.info)
        right.addWidget(self.stack, 1)
        right.addLayout(buttons)

        self.empty = EmptyState("No file yet", "Ask Clara to write one: “write my meeting notes to a file”. It appears here.")
        self.body = QWidget()
        body = QHBoxLayout(self.body)
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(18)
        body.addWidget(self.list)
        body.addWidget(self.detail, 1)

        self.pages = QStackedWidget()
        self.pages.addWidget(self.body)
        self.pages.addWidget(self.empty)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 18, 24, 16)
        layout.addWidget(label("The Markdown files Clara wrote for you. They are the same on the web site.", tone_="muted"))
        layout.addWidget(self.pages, 1)
        self._enable()

    def activated(self) -> None:
        if self._get_config().ready:
            self._call(lambda api: api.markdown_files(), self._listed)

    def shutdown(self) -> None:
        self.stop_calls()

    def _listed(self, files: object, error: str) -> None:
        if error:
            self.status.say(error, bad=True)
            return
        self.files = list(files)  # type: ignore[arg-type]
        wanted = (self.file or {}).get("id")
        self.list.blockSignals(True)
        self.list.clear()
        chosen = None
        for entry in self.files:
            item = QListWidgetItem(f"{entry['name']}\n{ago(entry.get('updated_at'))}, {size_text(entry['size'])}")
            item.setData(ID, entry["id"])
            self.list.addItem(item)
            if entry["id"] == wanted:
                chosen = item
        self.list.blockSignals(False)
        self.pages.setCurrentWidget(self.body if self.files else self.empty)
        if self.files:
            self.list.setCurrentItem(chosen or self.list.item(0))
        else:
            self.file = None

    def _picked(self, item: QListWidgetItem | None, _previous=None) -> None:
        if item is None:
            return
        file_id = item.data(ID)
        self._call(lambda api: api.markdown_file(file_id), self._loaded)

    def _loaded(self, file: object, error: str) -> None:
        if error:
            self.status.say(error, bad=True)
            return
        self.file = file  # type: ignore[assignment]
        text = self.file.get("text", "")
        self.name.setText(self.file["name"])
        self.info.setText(f"Changed {ago(self.file.get('updated_at'))}, {size_text(self.file['size'])}")
        self.preview.setMarkdown(text)
        self.source.setPlainText(text)
        self.status.say("")
        self._enable()

    def _mode(self, key: str) -> None:
        self.stack.setCurrentWidget(self.preview if key == "preview" else self.source)

    def _enable(self) -> None:
        shown = self.file is not None
        for widget in (self.copy_button, self.save_button, self.delete_button):
            widget.setEnabled(shown)

    def copy(self) -> None:
        if self.file:
            QGuiApplication.clipboard().setText(self.file.get("text", ""))
            self.status.say("Copied.")

    def save_as(self) -> None:
        if not self.file:
            return
        path, _ = QFileDialog.getSaveFileName(self, "Save the file", self.file["name"], "Markdown (*.md);;All files (*)")
        if path:
            try:
                Path(path).write_text(self.file.get("text", ""), encoding="utf-8")
                self.status.say(f"Saved to {path}.")
            except OSError as error:
                self.status.say(f"Could not save: {error}", bad=True)

    def delete(self) -> None:
        if not self.file or not confirm(self, "Delete the file", f"Delete {self.file['name']}? Clara can no longer read it.", "Delete", True):
            return
        file_id = self.file["id"]

        def deleted(_result: object, error: str) -> None:
            if error:
                self.status.say(error, bad=True)
                return
            self.file = None
            self.activated()

        self._call(lambda api: api.delete_markdown_file(file_id), deleted)
