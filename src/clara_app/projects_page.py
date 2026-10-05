"""The Projects page: make, edit and delete projects, and fill them with files, folders and GitHub repositories.

A project is kept by the server and belongs to the user, so it is the same here and on the web site. Every call
runs off the UI thread (workers.py); the page only shows what the server answers.
"""

from __future__ import annotations

from typing import Callable

from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtGui import QMouseEvent
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QStackedWidget,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from .api import ClaraApi
from .config import Config
from .projects import FILTER, Entry, Sent, file_entries, folder_entries, send, size_text
from .icons import bind_icon, portrait_label
from .theme import tone
from .widgets import Page, Panel, ago, button as push, divider, label
from .workers import CallWorker

ID = Qt.ItemDataRole.UserRole
FILES_SHOWN = 2_000
QUIET_REASONS = ("generated file", "not a text file")  # left out as expected: not worth a message


def plural(count: int, word: str) -> str:
    return f"{count:,} {word}{'' if count == 1 else 's'}"


class UploadWorker(QThread):
    """Finds the files (a folder can take a while), then sends them: `progress(sent, total)`, then `sent(Sent)`."""

    progress = Signal(int, int)
    sent = Signal(object)

    def __init__(self, api: ClaraApi, project_id: int, find: Callable[[], tuple[list[Entry], list[dict]]], parent=None):
        super().__init__(parent)
        self._api, self._project_id, self._find = api, project_id, find

    def run(self) -> None:
        try:
            entries, skipped = self._find()
        except OSError as error:
            self.sent.emit(Sent(error=str(error)))
            return
        result = send(self._api, self._project_id, entries, self.progress.emit)
        result.skipped = skipped + result.skipped
        result.skipped_count += len(skipped)
        self.sent.emit(result)


class ProjectCard(QFrame):
    """A project as the web site shows it: its name, what it is about, and how big it is. A click opens it."""

    clicked = Signal()

    def __init__(self, project: dict):
        super().__init__()
        self.setObjectName("card")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setMinimumHeight(140)
        column = QVBoxLayout(self)
        column.setContentsMargins(18, 16, 18, 14)
        column.setSpacing(8)
        head = QHBoxLayout()
        head.setSpacing(10)
        glyph = QToolButton()
        glyph.setEnabled(False)
        bind_icon(glyph, "folder", "text", 20)
        glyph.setStyleSheet("QToolButton { background: palette(alternate-base); border-radius: 8px; padding: 6px; }")
        head.addWidget(glyph)
        head.addWidget(label(project["name"], heading="section"), 1)
        column.addLayout(head)
        about = label(project.get("description") or "No description", tone_="" if project.get("description") else "muted", wrap=True)
        about.setMaximumHeight(60)
        column.addWidget(about, 1)
        column.addWidget(label(f"{plural(project['files'], 'file')}   {plural(project['conversations'], 'chat')}   updated {ago(project.get('updated_at'))}", tone_="muted"))

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()
        super().mousePressEvent(event)


class RepositoryDialog(QDialog):
    """Which GitHub repository to add, and which branch."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Add a GitHub repository")
        self.repo = QLineEdit()
        self.repo.setPlaceholderText("owner/name or https://github.com/owner/name")
        self.ref = QLineEdit()
        self.ref.setPlaceholderText("default branch")
        form = QFormLayout()
        form.addRow("Repository:", self.repo)
        form.addRow("Branch, tag or commit:", self.ref)
        note = QLabel(
            "The Clara server downloads its text files (not its history); sync it to get its latest version. "
            "Private repositories need a GITHUB_TOKEN on the server."
        )
        note.setWordWrap(True)
        tone(note, "muted")
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self._accept)
        buttons.rejected.connect(self.reject)
        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(note)
        layout.addWidget(buttons)
        self.resize(460, 0)

    def _accept(self) -> None:
        if self.repo.text().strip():
            self.accept()


class ProjectsPage(Page):
    changed = Signal()  # a project was made, renamed or deleted: the window lists them again
    chat_requested = Signal(int)  # "New chat in this project"

    page_title = "Projects"

    def __init__(
        self,
        get_config: Callable[[], Config],
        api_factory: Callable[[Config], ClaraApi] = ClaraApi,
        host=None,
        select: int | None = None,
    ):
        super().__init__()
        self._host = host
        self._get_config, self._api_factory = get_config, api_factory
        self._select = select
        self.project: dict | None = None  # the one shown, as the server describes it
        self._calls: list[CallWorker] = []
        self._upload: UploadWorker | None = None

        intro = QLabel(
            "A project keeps files, folders and GitHub repositories with instructions of its own: every "
            "conversation of the project can use them. They are the same on the web site."
        )
        intro.setWordWrap(True)
        tone(intro, "muted")

        self.list = QListWidget()  # the projects as the server lists them; the cards are drawn from it
        self.list.hide()
        self.list.currentItemChanged.connect(self._picked)
        self.new_button = push("New project", "primary", self.create)
        self.delete_button = push("Delete…", "danger", self.delete)
        self._cards: list[ProjectCard] = []
        self._opening_detail = False  # the next project loaded is opened on its own page

        self.name = QLineEdit()
        self.name.setMaxLength(100)
        self.description = QPlainTextEdit()
        self.description.setPlaceholderText("What is it about?")
        self.description.setFixedHeight(76)
        self.instructions = QPlainTextEdit()
        self.instructions.setPlaceholderText("How Clara should work in this project: language, conventions, focus…")
        self.instructions.setFixedHeight(120)
        for field in (self.name, self.description, self.instructions):
            field.textChanged.connect(self._edited)
        self.save_button = push("Save", "primary", self.save)
        self.chat_button = QPushButton("New chat in this project")
        self.chat_button.clicked.connect(lambda: self.project and self.chat_requested.emit(self.project["id"]))
        form = QFormLayout()
        form.addRow("Name:", self.name)
        form.addRow("Description:", self.description)
        form.addRow("Instructions:", self.instructions)
        self.chat_button.setProperty("kind", "primary")
        self.connections_button = QPushButton("Connections…")
        self.connections_button.setToolTip("What Clara can reach in every conversation of this project: GitHub, Google Drive, folders")
        self.connections_button.clicked.connect(self.show_connections)
        edit_row = QHBoxLayout()
        edit_row.addStretch(1)
        edit_row.addWidget(self.save_button)

        self.summary = QLabel("")
        self.summary.setWordWrap(True)
        self.add_files_button = QPushButton("Add files…")
        self.add_files_button.setToolTip("Text, code, Markdown, PDF, Word, .zip")
        self.add_files_button.clicked.connect(self.add_files)
        self.add_folder_button = QPushButton("Add a folder…")
        self.add_folder_button.setToolTip("Its text files; dependencies and build output are left out")
        self.add_folder_button.clicked.connect(self.add_folder)
        self.github_button = QPushButton("GitHub…")
        self.github_button.setToolTip("Download a GitHub repository's text files")
        self.github_button.clicked.connect(self.add_repository)
        add_row = QHBoxLayout()
        for button in (self.add_files_button, self.add_folder_button, self.github_button):
            add_row.addWidget(button)
        add_row.addStretch(1)

        self.sources = QListWidget()
        self.sources.setFixedHeight(64)
        self.sources.currentItemChanged.connect(lambda *_: self._enable())
        self.sync_button = QPushButton("Sync")
        self.sync_button.setToolTip("Download the repository again")
        self.sync_button.clicked.connect(self.sync)
        self.remove_source_button = QPushButton("Remove")
        self.remove_source_button.setToolTip("Remove the repository and its files")
        self.remove_source_button.clicked.connect(self.remove_source)
        sources_row = QHBoxLayout()
        sources_row.addWidget(self.sources, 1)
        source_buttons = QVBoxLayout()
        source_buttons.addWidget(self.sync_button)
        source_buttons.addWidget(self.remove_source_button)
        source_buttons.addStretch(1)
        sources_row.addLayout(source_buttons)
        self.sources_box = QWidget()
        self.sources_box.setLayout(sources_row)
        sources_row.setContentsMargins(0, 0, 0, 0)

        self.filter = QLineEdit()
        self.filter.setPlaceholderText("🔍 Filter files")
        self.filter.setClearButtonEnabled(True)
        self.filter.textChanged.connect(self._show_files)
        self.files = QListWidget()
        self.files.setToolTip("Double-click a file to read it")
        self.files.itemDoubleClicked.connect(self.view_file)
        self.files.currentItemChanged.connect(lambda *_: self._enable())
        self.remove_file_button = QPushButton("Remove file")
        self.remove_file_button.clicked.connect(self.remove_file)
        self.status = QLabel("")
        self.status.setWordWrap(True)
        tone(self.status, "muted")
        files_box = Panel("Files", "Text, code, PDF and Word files, folders, and GitHub repositories Clara reads in this project.")
        files_layout = files_box.body
        files_layout.addWidget(self.summary)
        files_layout.addLayout(add_row)
        files_layout.addWidget(self.sources_box)
        files_layout.addWidget(self.filter)
        files_layout.addWidget(self.files, 1)
        bottom = QHBoxLayout()
        bottom.addWidget(self.status, 1)
        bottom.addWidget(self.remove_file_button)
        files_layout.addLayout(bottom)

        # the page of one project: a way back, what it is about, its chats, and its files
        self.back_button = QToolButton()
        self.back_button.setToolTip("All projects")
        bind_icon(self.back_button, "back", "muted", 20)
        self.back_button.clicked.connect(self.close_project)
        self.project_title = label("", heading="page")
        head = QHBoxLayout()
        head.setSpacing(10)
        head.addWidget(self.back_button)
        head.addWidget(self.project_title, 1)
        head.addWidget(self.connections_button)
        head.addWidget(self.chat_button)
        head.addWidget(self.delete_button)
        about = Panel("About", "Its name, what it is for, and how Clara should work in it.")
        about.add(form)
        about.add(edit_row)
        self.chats_panel = Panel("Conversations")
        self.chats_panel.body.setSpacing(0)
        self.chats_panel.body.setContentsMargins(0, 0, 0, 6)
        left = QVBoxLayout()
        left.setSpacing(16)
        left.addWidget(about)
        left.addWidget(self.chats_panel)
        left.addStretch(1)
        columns = QHBoxLayout()
        columns.setSpacing(18)
        columns.addLayout(left, 1)
        columns.addWidget(files_box, 1)
        self.detail = QWidget()
        detail = QVBoxLayout(self.detail)
        detail.setContentsMargins(24, 18, 24, 16)
        detail.setSpacing(16)
        detail.addLayout(head)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        inside = QWidget()
        inside.setObjectName("scroll-body")
        inside.setLayout(columns)
        scroll.setWidget(inside)
        detail.addWidget(scroll, 1)

        # the page of all projects: cards
        self.grid = QGridLayout()
        self.grid.setSpacing(16)
        self.empty = QWidget()
        self.empty_box = QVBoxLayout(self.empty)
        grid_page = QWidget()
        grid_column = QVBoxLayout(grid_page)
        grid_column.setContentsMargins(24, 18, 24, 16)
        grid_column.setSpacing(14)
        grid_column.addWidget(intro)
        grid_column.addLayout(self.grid)
        grid_column.addWidget(self.empty)
        grid_column.addStretch(1)
        self.grid_page = QScrollArea()
        self.grid_page.setWidgetResizable(True)
        self.grid_page.setFrameShape(QFrame.Shape.NoFrame)
        grid_page.setObjectName("scroll-body")
        self.grid_page.setWidget(grid_page)

        self.stack = QStackedWidget()
        self.stack.addWidget(self.grid_page)
        self.stack.addWidget(self.detail)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.stack)

        self.new_button_header = push("New project", "primary", self.create)
        self.changed.connect(self._told_host)
        self.chat_requested.connect(self._chat_in)
        self._show(None)
        self.reload()

    def actions(self) -> list[QWidget]:
        return [self.new_button_header]

    def activated(self) -> None:
        self.reload()

    def show_connections(self) -> None:
        """Attach a repository, a Drive folder or a folder to every conversation of the project."""
        if self.project is None:
            return
        from .integrations_page import ConnectionsDialog

        ConnectionsDialog({"project": self.project["id"]}, self._get_config, self._api_factory, self).exec()

    def close_project(self) -> None:
        self.stack.setCurrentWidget(self.grid_page)

    def _open_card(self, project_id: int) -> None:
        self._opening_detail = True
        self.reload(select=project_id)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._layout_cards()

    def _layout_cards(self) -> None:
        columns = max(1, (self.width() - 48 + 16) // 316)
        for index, card in enumerate(self._cards):
            self.grid.addWidget(card, index // columns, index % columns)
        for column in range(columns):
            self.grid.setColumnStretch(column, 1)

    def _told_host(self) -> None:
        if self._host is not None:
            self._host.projects_changed()

    def _chat_in(self, project: int) -> None:
        if self._host is not None:
            self._host.chat_in_project(project)

    def show_project(self, project: int) -> None:
        """Open the page on this project."""
        self.reload(select=project)

    # -- plumbing ------------------------------------------------------------------------- #

    @property
    def busy(self) -> bool:
        return self._upload is not None or any(getattr(worker, "long", False) for worker in self._calls)

    def _call(self, call: Callable[[ClaraApi], object], then: Callable[[object, str], None], long: bool = False) -> None:
        """Run `call(api)` in the background, then `then(result, error)` here."""
        api = self._api_factory(self._get_config())
        worker = CallWorker(lambda: call(api), then, self)
        worker.long = long  # a download or an upload: the actions wait for it
        worker.done.connect(self._call_done)
        worker.finished.connect(self._call_finished)
        self._calls.append(worker)
        worker.start()
        self._enable()

    def _call_done(self, result: object, error: str) -> None:
        worker = self.sender()
        if not isinstance(worker, CallWorker):
            return
        then, worker.then = worker.then, None
        worker.long = False
        if then is not None:
            then(result, error)
        self._enable()

    def _call_finished(self) -> None:
        worker = self.sender()
        if worker in self._calls:
            worker.wait(2000)
            self._calls.remove(worker)
            worker.deleteLater()

    def _say(self, text: str, bad: bool = False) -> None:
        self.status.setText(text)
        tone(self.status, "bad" if bad else "muted")

    def _enable(self) -> None:
        shown = self.project is not None
        busy = self.busy
        self.detail.setEnabled(shown)
        self.delete_button.setEnabled(shown and not busy)
        for button in (self.add_files_button, self.add_folder_button, self.github_button):
            button.setEnabled(shown and not busy)
        source = self.sources.currentItem()
        self.sync_button.setEnabled(source is not None and not busy)
        self.remove_source_button.setEnabled(source is not None and not busy)
        self.remove_file_button.setEnabled(self.files.currentItem() is not None and self.files.currentItem().data(ID) is not None and not busy)
        self.save_button.setEnabled(shown and self._dirty())

    def shutdown(self) -> None:
        for worker in list(self._calls):
            worker.then = None
            worker.wait(5000)
        if self._upload is not None:
            self._upload.wait(5000)

    # -- the list ------------------------------------------------------------------------- #

    def reload(self, select: int | None = None) -> None:
        if select is not None:
            self._select = select
        self._call(lambda api: api.projects(), self._listed)

    def _listed(self, projects: object, error: str) -> None:
        if error:
            self._say(error, bad=True)
            return
        wanted = self._select if self._select is not None else (self.project or {}).get("id")
        self._select = None
        self.list.blockSignals(True)
        self.list.clear()
        chosen = None
        for project in projects:
            item = QListWidgetItem(project["name"])
            item.setData(ID, project["id"])
            item.setToolTip(f"{project['name']}\n{plural(project['files'], 'file')}, {plural(project['conversations'], 'conversation')}")
            self.list.addItem(item)
            if project["id"] == wanted:
                chosen = item
        self.list.blockSignals(False)
        self._draw_cards(projects)
        if not projects:
            self._show(None)
            self._say("No project yet: make one with New project…")
            return
        if chosen is not None:
            self.list.setCurrentItem(chosen)

    def _draw_cards(self, projects: list[dict]) -> None:
        for card in self._cards:
            self.grid.removeWidget(card)
            card.hide()
            card.deleteLater()
        self._cards = []
        for project in projects:
            card = ProjectCard(project)
            card.clicked.connect(lambda _=False, i=project["id"]: self._open_card(i))
            self._cards.append(card)
        while self.empty_box.count():
            item = self.empty_box.takeAt(0)
            if item.widget() is not None:
                item.widget().deleteLater()
        if not projects:
            from .widgets import EmptyState

            self.empty_box.addWidget(EmptyState("No project yet", "Make one with New project, then give it files, folders or a GitHub repository."))
        self.empty.setVisible(not projects)
        self._layout_cards()

    def _picked(self, item: QListWidgetItem | None, _previous=None) -> None:
        if item is None:
            self._show(None)
            return
        project_id = item.data(ID)
        self._call(lambda api: api.project(project_id), self._loaded)

    def _loaded(self, project: object, error: str) -> None:
        if error:
            self._say(error, bad=True)
            return
        self._show(project)
        if self._opening_detail:
            self._opening_detail = False
            self.stack.setCurrentWidget(self.detail)

    def _fill_chats(self, project: dict | None) -> None:
        """The conversations of the project, on its page: a click goes to the chat on that conversation."""
        body = self.chats_panel.body
        while body.count():
            item = body.takeAt(0)
            if item.widget() is not None:
                item.widget().hide()
                item.widget().deleteLater()
        known = self._host.history.conversations.values() if self._host is not None and project is not None else []
        chats = sorted((c for c in known if c.get("project") == (project or {}).get("id")), key=lambda c: c["updated_at"], reverse=True)
        if not chats:
            body.addWidget(label("  No conversation yet. Start one with New chat in this project.", tone_="muted", wrap=True))
            return
        from .history import display_title

        for position, chat in enumerate(chats):
            if position:
                body.addWidget(divider())
            row = push(f"{display_title(chat)}      {ago(chat.get('updated_at'))}", "ghost", lambda _=False, c=chat["id"]: self._host.open_conversation(c))
            row.setStyleSheet("text-align: left; padding: 10px 18px; border-radius: 0;")
            body.addWidget(row)

    def items(self) -> list[str]:
        """The names listed, for tests."""
        return [self.list.item(row).text() for row in range(self.list.count())]

    # -- one project ---------------------------------------------------------------------- #

    def _dirty(self) -> bool:
        project = self.project
        return project is not None and (
            self.name.text().strip() != project["name"]
            or self.description.toPlainText().strip() != project["description"]
            or self.instructions.toPlainText().strip() != project["instructions"]
        ) and bool(self.name.text().strip())

    def _edited(self) -> None:
        self.save_button.setEnabled(self._dirty())

    def _show(self, project: dict | None) -> None:
        """Show a project as the server described it (None: nothing to show)."""
        self.project_title.setText((project or {}).get("name", ""))
        self._fill_chats(project)
        same = project is not None and self.project is not None and project["id"] == self.project["id"]
        self.project = project
        if project is None:
            for field in (self.name, self.description, self.instructions):
                field.blockSignals(True)
                field.clear()
                field.blockSignals(False)
            self.summary.setText("")
            self.sources.clear()
            self.files.clear()
            self.sources_box.hide()
            self.stack.setCurrentWidget(self.grid_page)
            self._enable()
            return
        if not same or not self._dirty():  # what the user is typing is not thrown away by an upload
            for field, value in ((self.name, project["name"]), (self.description, project["description"]),
                                 (self.instructions, project["instructions"])):
                field.blockSignals(True)
                if isinstance(field, QLineEdit):
                    field.setText(value)
                else:
                    field.setPlainText(value)
                field.blockSignals(False)
        if project["files"]:
            context = project["context"]
            how = (
                f"Clara reads all of them with every message ({context['percent']}% of the model's context)."
                if context["inline"]
                else f"Too big to be read whole by the model in use ({context['percent']}% of its context): "
                "Clara searches and reads them when she needs to."
            )
            self.summary.setText(f"{plural(project['files'], 'file')}, {size_text(project['size'])}. {how}")
        else:
            self.summary.setText("No file yet: add text, code, PDF or Word files, a folder, a .zip or a GitHub repository.")
        self.sources.clear()
        for source in project.get("sources", []):
            when = f"synced {source['synced_at'][:16].replace('T', ' ')}" if source.get("synced_at") else "never synced"
            text = f"{source['repo']}{' @ ' + source['ref'] if source.get('ref') else ''} — {plural(source['files'], 'file')}, {when}"
            if source.get("problem"):
                text += f" — {source['problem']}"
            item = QListWidgetItem(text)
            item.setData(ID, source["id"])
            item.setToolTip(text)
            self.sources.addItem(item)
        self.sources_box.setVisible(bool(project.get("sources")))
        self._show_files()
        self._enable()

    def _show_files(self) -> None:
        self.files.clear()
        if self.project is None:
            return
        needle = self.filter.text().strip().lower()
        files = [f for f in self.project.get("file_list", []) if needle in f["path"].lower()]
        for file in files[:FILES_SHOWN]:
            item = QListWidgetItem(f"{file['path']}   ({size_text(file['size'])})")
            item.setData(ID, file["path"])
            self.files.addItem(item)
        if len(files) > FILES_SHOWN:
            more = QListWidgetItem(f"… {len(files) - FILES_SHOWN:,} more: filter to find them")
            more.setFlags(Qt.ItemFlag.NoItemFlags)
            self.files.addItem(more)
        self.filter.setVisible(len(self.project.get("file_list", [])) > 8)
        self._enable()

    def _replaced(self, result: object, error: str, done: str = "") -> None:
        """After a change: the server gives the project back."""
        if error:
            self._say(error, bad=True)
            return
        project = result.get("project", result) if isinstance(result, dict) else None
        if project:
            self._show(project)
        if done:
            self._say(done)

    # -- what the user does ---------------------------------------------------------------- #

    def create(self) -> None:
        name, accepted = QInputDialog.getText(self, "New project", "Name:")
        if not accepted or not name.strip():
            return

        def made(project: object, error: str) -> None:
            if error:
                self._say(error, bad=True)
                return
            self.changed.emit()
            self._opening_detail = True  # a new project opens on its own page
            self.reload(select=project["id"])

        self._call(lambda api: api.create_project(name.strip()), made)

    def save(self) -> None:
        if self.project is None or not self._dirty():
            return
        project_id = self.project["id"]
        values = (self.name.text().strip(), self.description.toPlainText().strip(), self.instructions.toPlainText().strip())

        def saved(project: object, error: str) -> None:
            if error:
                self._say(error, bad=True)
                return
            self.project = None  # shown again as saved
            self._show(project)
            self._say("Saved.")
            self.changed.emit()
            self.reload()

        self._call(lambda api: api.update_project(project_id, *values), saved)

    def delete(self) -> None:
        project = self.project
        if project is None:
            return
        chats = f"\n\nIts {plural(project['conversations'], 'conversation')} stay, in your list of chats." if project["conversations"] else ""
        answer = QMessageBox.question(self, "Delete the project", f"Delete “{project['name']}” and its files?{chats}")
        if answer != QMessageBox.StandardButton.Yes:
            return

        def deleted(_result: object, error: str) -> None:
            if error:
                self._say(error, bad=True)
                return
            self._show(None)
            self._say(f"{project['name']} was deleted.")
            self.changed.emit()
            self.reload()

        self._call(lambda api: api.delete_project(project["id"]), deleted)

    def add_files(self) -> None:
        paths, _ = QFileDialog.getOpenFileNames(self, "Add files to the project", "", FILTER)
        if paths:
            self.upload(lambda: file_entries(paths))

    def add_folder(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "Add a folder to the project")
        if folder:
            self.upload(lambda: folder_entries(folder))

    def upload(self, find: Callable[[], tuple[list[Entry], list[dict]]]) -> None:
        if self.project is None or self.busy:
            return
        api = self._api_factory(self._get_config())
        self._upload = UploadWorker(api, self.project["id"], find, self)
        self._upload.progress.connect(self._progress)
        self._upload.sent.connect(self._sent)
        self._upload.finished.connect(self._upload_finished)
        self._say("Looking at the files…")
        self._upload.start()
        self._enable()

    def _progress(self, sent: int, total: int) -> None:
        self._say(f"Sent {sent:,} of {plural(total, 'file')}…")

    def _sent(self, result: Sent) -> None:
        if result.project:
            self._show(result.project)
        text = f"{plural(result.added, 'file')} added"
        if result.skipped_count:
            text += f", {result.skipped_count:,} left out"
        if result.error:
            self._say(f"{text}. Stopped: {result.error}", bad=True)
        else:
            self._say(text + ".", bad=not result.added and result.skipped_count > 0)
        notable = [s for s in result.skipped if s["reason"] not in QUIET_REASONS and not s["reason"].startswith("in ")]
        if notable:
            box = QMessageBox(QMessageBox.Icon.Information, "Files left out", f"{plural(len(notable), 'file')} could not be added.", parent=self)
            box.setDetailedText("\n".join(f"{s['path']}: {s['reason']}" for s in notable[:300]))
            box.open()  # not exec: the dialog stays usable, and tests are not blocked

    def _upload_finished(self) -> None:
        worker, self._upload = self._upload, None
        if worker is not None:
            worker.wait(2000)
            worker.deleteLater()
        self._enable()

    def add_repository(self) -> None:
        if self.project is None or self.busy:
            return
        dialog = RepositoryDialog(self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        self.download(dialog.repo.text().strip(), dialog.ref.text().strip())

    def download(self, repo: str, ref: str = "") -> None:
        project_id = self.project["id"]
        self._say(f"Downloading {repo}…")

        def added(result: object, error: str) -> None:
            if error:
                self._say(error, bad=True)
                return
            self._replaced(result, "", f"{plural(len(result['added']), 'file')} added from {repo}, {result['skipped_count']:,} left out.")

        self._call(lambda api: api.add_repository(project_id, repo, ref), added, long=True)

    def sync(self) -> None:
        item = self.sources.currentItem()
        if self.project is None or item is None:
            return
        project_id, source_id = self.project["id"], item.data(ID)
        self._say("Downloading the repository again…")

        def synced(result: object, error: str) -> None:
            if error:
                self._say(error, bad=True)
                self._call(lambda api: api.project(project_id), self._loaded)  # it says what went wrong
                return
            self._replaced(result, "", f"Up to date: {plural(len(result['added']), 'file')}.")

        self._call(lambda api: api.sync_repository(project_id, source_id), synced, long=True)

    def remove_source(self) -> None:
        item = self.sources.currentItem()
        if self.project is None or item is None:
            return
        answer = QMessageBox.question(self, "Remove the repository", f"Remove {item.text().split(' — ')[0]} and its files from the project?")
        if answer != QMessageBox.StandardButton.Yes:
            return
        project_id, source_id = self.project["id"], item.data(ID)
        self._call(lambda api: api.remove_repository(project_id, source_id), lambda r, e: self._replaced(r, e, "Removed."))

    def remove_file(self) -> None:
        item = self.files.currentItem()
        if self.project is None or item is None or item.data(ID) is None:
            return
        project_id, path = self.project["id"], item.data(ID)
        self._call(lambda api: api.remove_file(project_id, path), lambda r, e: self._replaced(r, e, f"{path} removed."))

    def view_file(self, item: QListWidgetItem) -> None:
        path = item.data(ID)
        if self.project is None or path is None:
            return
        project_id = self.project["id"]

        def shown(file: object, error: str) -> None:
            if error:
                self._say(error, bad=True)
                return
            viewer = QDialog(self)
            viewer.setWindowTitle(file["path"])
            text = QPlainTextEdit(file["content"])
            text.setReadOnly(True)
            text.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
            buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
            buttons.rejected.connect(viewer.reject)
            layout = QVBoxLayout(viewer)
            layout.addWidget(text)
            layout.addWidget(buttons)
            viewer.resize(760, 560)
            viewer.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
            viewer.open()

        self._call(lambda api: api.file(project_id, path), shown)
