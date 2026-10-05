"""The Files page: the Markdown files Clara wrote for you, as rows to open, copy, save or delete, as on the web site.
They are kept by the server and are the same there."""

from __future__ import annotations

from pathlib import Path
from typing import Callable

from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import (
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QPlainTextEdit,
    QStackedWidget,
    QTextBrowser,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from .api import ClaraApi
from .config import Config
from .icons import bind_icon
from .widgets import Calls, Column, EmptyState, Page, Panel, Segmented, StatusLine, ago, button, confirm, divider, label

MONO = "font-family: Consolas, monospace; font-weight: 600; font-size: 15px;"


def size_text(size: int) -> str:
    return f"{size:,} characters" if size < 10_000 else f"{size / 1000:,.0f}k characters"


def icon_button(glyph: str, tip: str, slot) -> QToolButton:
    widget = QToolButton()
    widget.setToolTip(tip)
    bind_icon(widget, glyph, "muted", 19)
    widget.clicked.connect(slot)
    return widget


class FilesPage(Page, Calls):
    page_title = "Files"

    def __init__(self, get_config: Callable[[], Config], api_factory: Callable[[Config], ClaraApi], host=None):
        super().__init__()
        self._init_calls(get_config, api_factory)
        self.files: list[dict] = []
        self.file: dict | None = None  # the one shown in the viewer, with its text

        # the viewer: a dialog, as on the web site
        self.viewer = QDialog(self)
        self.viewer.resize(820, 600)
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
        column = QVBoxLayout(self.viewer)
        column.setSpacing(10)
        column.addLayout(head)
        column.addWidget(self.info)
        column.addWidget(self.stack, 1)
        column.addLayout(buttons)

        # the page: a count, and one row for each file
        self.count = label("", tone_="muted")
        self.rows = Panel()
        self.rows.body.setContentsMargins(0, 0, 0, 0)
        self.rows.body.setSpacing(0)
        self.empty = EmptyState("No file yet", "Ask Clara to write one: “write my meeting notes to a file”. It appears here.")
        self.page_column = Column()
        self.page_column.add(label("The Markdown files Clara wrote for you. They are the same on the web site.", tone_="muted", wrap=True))
        self.page_column.add(self.count)
        self.page_column.add(self.rows)
        self.page_column.add(self.empty)
        self.page_column.finish()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.page_column)
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
        self.count.setText(f"{len(self.files)} {'file' if len(self.files) == 1 else 'files'}" if self.files else "")
        body = self.rows.body
        while body.count():
            item = body.takeAt(0)
            if item.widget() is not None:
                item.widget().hide()
                item.widget().deleteLater()
        for position, entry in enumerate(self.files):
            if position:
                body.addWidget(divider())
            body.addWidget(self._row(entry))
        self.rows.setVisible(bool(self.files))
        self.empty.setVisible(not self.files)

    def _row(self, entry: dict) -> QWidget:
        row = QWidget()
        line = QHBoxLayout(row)
        line.setContentsMargins(18, 10, 12, 10)
        line.setSpacing(12)
        glyph = QToolButton()
        glyph.setEnabled(False)
        bind_icon(glyph, "file", "text", 19)
        glyph.setStyleSheet("QToolButton { background: palette(alternate-base); border-radius: 8px; padding: 6px; }")
        name = button(entry["name"], "ghost", lambda _=False, i=entry["id"]: self.open_file(i))
        name.setStyleSheet(f"text-align: left; padding: 4px 6px; {MONO}")
        about = label(f"{ago(entry.get('updated_at'))}, {size_text(entry['size'])}", tone_="muted")
        text = QVBoxLayout()
        text.setSpacing(0)
        text.addWidget(name)
        text.addWidget(about)
        line.addWidget(glyph)
        line.addLayout(text, 1)
        line.addWidget(icon_button("copy", "Copy", lambda _=False, i=entry["id"]: self.open_file(i, then=self.copy)))
        line.addWidget(icon_button("download", "Save as…", lambda _=False, i=entry["id"]: self.open_file(i, then=self.save_as)))
        line.addWidget(icon_button("trash", "Delete…", lambda _=False, i=entry["id"]: self.open_file(i, then=self.delete)))
        return row

    def open_file(self, file_id: int, then: Callable[[], None] | None = None) -> None:
        """Read a file from the server and show it (or, given `then`, do that with it instead of showing it)."""

        def loaded(file: object, error: str) -> None:
            if error:
                self.status.say(error, bad=True)
                return
            self._loaded(file, show=then is None)
            if then is not None:
                then()

        self._call(lambda api: api.markdown_file(file_id), loaded)

    def _loaded(self, file: object, show: bool = True) -> None:
        self.file = file  # type: ignore[assignment]
        text = self.file.get("text", "")
        self.name.setText(self.file["name"])
        self.info.setText(f"Changed {ago(self.file.get('updated_at'))}, {size_text(self.file['size'])}")
        self.preview.setMarkdown(text)
        self.source.setPlainText(text)
        self.status.say("")
        self._enable()
        if show:
            self.viewer.setWindowTitle(self.file["name"])
            self.viewer.open()

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
            self.viewer.hide()
            self.activated()

        self._call(lambda api: api.delete_markdown_file(file_id), deleted)
