"""The Integrations page: the accounts you connect (GitHub, Google Drive), the repositories, Drive folders and folders you
add (also folders of this computer, which only this app can reach), and what Clara may do with each: look, add or change,
replace or delete, each one allowed, asked to you, or never. The same panel attaches them to a project or a conversation
(`ConnectionsDialog`). The administrator's switches and the log are at the bottom, for administrators."""

from __future__ import annotations

from typing import Callable

from PySide6.QtCore import QTimer, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QStackedWidget,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from .api import ClaraApi
from .approvals import ApprovalCard
from .config import Config
from .icons import bind_icon, icon
from .local_folders import FolderError, FolderRegistry
from .widgets import (
    Calls,
    Column,
    EmptyState,
    Notice,
    Page,
    Panel,
    StatusLine,
    ago,
    ask_text,
    badge,
    button,
    confirm,
    divider,
    label,
)

LEVELS = (
    ("read", "Look", "List, read and search."),
    ("write", "Add or change", "New files, a commit on a branch, a pull request, an issue."),
    ("destructive", "Replace or delete", "Overwrite, delete, move, commit to the main branch, merge, close."),
)
DECISIONS = (("allow", "Allow"), ("ask", "Ask me"), ("deny", "Never"))
OPENNESS = {"deny": 0, "ask": 1, "allow": 2}
DEFAULT_LEVELS = {"read": "allow", "write": "ask", "destructive": "ask"}
SHORT = {"read": "Look", "write": "Change", "destructive": "Replace"}
DECISION_KINDS = {"allow": "ok", "ask": "ask", "deny": "off"}
KINDS = {
    "github_repo": ("branch", "GitHub repository"),
    "drive_folder": ("cloud", "Drive folder"),
    "drive_file": ("cloud", "Drive file"),
    "server_path": ("server", "Folder on the server"),
    "computer_path": ("laptop", "Folder on a computer"),
}
ACCOUNTS = {"github": ("branch", "GitHub"), "gdrive": ("cloud", "Google Drive")}
NOTIFY_CHOICES = (("", "The server's default"), ("0", "Never"), ("30", "30 seconds"), ("60", "1 minute"), ("300", "5 minutes"), ("900", "15 minutes"), ("3600", "1 hour"))
GOOGLE_POLL_MS = 2500
GOOGLE_POLLS = 70


def clear_layout(layout) -> None:
    while layout.count():
        item = layout.takeAt(0)
        if item.widget() is not None:
            item.widget().hide()
            item.widget().deleteLater()
        elif item.layout() is not None:
            clear_layout(item.layout())


def level_badges(effective: dict) -> QWidget:
    """What Clara may do, level by level, as a row of badges."""
    row = QWidget()
    line = QHBoxLayout(row)
    line.setContentsMargins(0, 0, 0, 0)
    line.setSpacing(6)
    for level, _name, _help in LEVELS:
        decision = effective.get(level) or DEFAULT_LEVELS[level]
        text = dict(DECISIONS).get(decision, decision).lower()
        line.addWidget(badge(f"{SHORT[level]} · {text}", DECISION_KINDS.get(decision, "")))
    line.addStretch(1)
    return row


def list_row(glyph: str, title: str, subtitle: str = "", extra: QWidget | None = None, actions: list[QWidget] | None = None, tag: QWidget | None = None) -> QWidget:
    row = QWidget()
    line = QHBoxLayout(row)
    line.setContentsMargins(18, 10, 12, 10)
    line.setSpacing(12)
    mark = QLabel()
    mark.setPixmap(icon(glyph, "text_2", 20).pixmap(20, 20))
    line.addWidget(mark)
    text = QVBoxLayout()
    text.setSpacing(2)
    head = QHBoxLayout()
    head.setSpacing(8)
    head.addWidget(label(title, heading="small"))
    if tag is not None:
        head.addWidget(tag)
    head.addStretch(1)
    text.addLayout(head)
    if subtitle:
        text.addWidget(label(subtitle, tone_="muted", wrap=True))
    if extra is not None:
        text.addWidget(extra)
    line.addLayout(text, 1)
    for widget in actions or []:
        line.addWidget(widget)
    return row


def tool_button(glyph: str, tip: str, slot: Callable[[], object], role: str = "muted") -> QToolButton:
    widget = QToolButton()
    widget.setToolTip(tip)
    bind_icon(widget, glyph, role, 18)
    widget.clicked.connect(lambda _=False: slot())
    return widget


class LevelsDialog(QDialog):
    """Three choices, one per level of action. `inherit`: the text of the choice that leaves a level to the layer above
    (none: every level has a value). `ceiling`: the most the administrator lets through."""

    def __init__(self, title: str, intro: str, levels: dict, inherit: str = "", ceiling: dict | None = None, parent: QWidget | None = None):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setMinimumWidth(480)
        self.boxes: dict[str, QComboBox] = {}
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 18, 20, 16)
        layout.setSpacing(10)
        layout.addWidget(label(title, heading="section"))
        if intro:
            layout.addWidget(label(intro, tone_="muted", wrap=True))
        for level, name, help_text in LEVELS:
            box = QComboBox()
            if inherit:
                box.addItem(inherit, "")
            for value, text in DECISIONS:
                box.addItem(text, value)
                limit = (ceiling or {}).get(level)
                if limit is not None and OPENNESS[value] > OPENNESS[limit]:
                    item = box.model().item(box.count() - 1)
                    item.setEnabled(False)
                    item.setText(f"{text} (the administrator does not allow it)")
            current = levels.get(level) or ("" if inherit else DEFAULT_LEVELS[level])
            box.setCurrentIndex(max(0, box.findData(current)))
            self.boxes[level] = box
            layout.addWidget(label(name, heading="small"))  # name, what it covers, then the choice: stacked, so that nothing is clipped
            layout.addWidget(label(help_text, tone_="muted", wrap=True))
            layout.addWidget(box)
            layout.addSpacing(6)
        actions = QHBoxLayout()
        actions.addStretch(1)
        actions.addWidget(button("Cancel", "", self.reject))
        actions.addWidget(button("Save", "primary", self.accept))
        layout.addLayout(actions)

    def levels(self) -> dict:
        return {level: box.currentData() for level, box in self.boxes.items() if box.currentData()}


class AddResourceDialog(QDialog, Calls):
    """Add a repository, a Drive folder or file, a folder of the server, or a folder of this computer."""

    def __init__(self, data: dict, get_config: Callable[[], Config], api_factory: Callable[[Config], ClaraApi], parent: QWidget | None = None):
        super().__init__(parent)
        self._init_calls(get_config, api_factory)
        self.data = data
        self.did_add = False
        self.setWindowTitle("Add")
        self.setMinimumWidth(560)
        self.setMinimumHeight(440)
        types = {t["id"]: t for t in data["types"]}

        def usable(kind: str) -> bool:
            return bool(types.get(kind, {}).get("enabled") and types.get(kind, {}).get("available"))

        self.choices = [
            (key, text) for key, text, kind in (
                ("github_repo", "GitHub repository", "github"), ("drive_folder", "Google Drive folder", "gdrive"),
                ("drive_file", "Google Drive file", "gdrive"), ("server_path", "Folder on the server", "server"),
                ("computer_path", "Folder on this computer", "computer"),
            ) if usable(kind)
        ]
        self.kind = QComboBox()
        for key, text in self.choices:
            self.kind.addItem(text, key)
        self.stack = QStackedWidget()
        self.status = StatusLine()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 18, 20, 16)
        layout.setSpacing(10)
        layout.addWidget(label("Add", heading="section"))
        layout.addWidget(self.kind)
        layout.addWidget(self.stack, 1)
        layout.addWidget(self.status)
        close = QHBoxLayout()
        close.addStretch(1)
        close.addWidget(button("Close", "", self.accept))
        layout.addLayout(close)
        self._pages: dict[str, QWidget] = {}
        self._drive_here = {"id": "root", "name": "My Drive", "parent": None}
        self.kind.currentIndexChanged.connect(lambda *_: self._show())
        self._show()

    # -- the form of each kind ------------------------------------------------------------------------ #

    def _show(self) -> None:
        key = self.kind.currentData()
        if key is None:
            self.stack.addWidget(EmptyState("Nothing to add", "Ask the administrator to turn an integration on, and connect an account."))
            return
        if key not in self._pages:
            builder = {"github_repo": self._github_page, "drive_folder": self._drive_page, "drive_file": self._drive_page,
                       "server_path": self._server_page, "computer_path": self._computer_page}[key]
            self._pages[key] = builder(key)
            self.stack.addWidget(self._pages[key])
        self.stack.setCurrentWidget(self._pages[key])
        self.status.say("")

    def _accounts(self, kind: str) -> list[dict]:
        return [a for a in self.data["accounts"] if a["kind"] == kind and a["status"] == "ok"]

    def _account_combo(self, kind: str) -> QComboBox:
        box = QComboBox()
        for account in self._accounts(kind):
            box.addItem(account["label"], account["id"])
        return box

    def _need_account(self, text: str) -> QWidget:
        page = QWidget()
        column = QVBoxLayout(page)
        column.addWidget(label(text, wrap=True))
        column.addStretch(1)
        return page

    def _github_page(self, _key: str) -> QWidget:
        if not self._accounts("github"):
            return self._need_account("Connect a GitHub account first.")
        page = QWidget()
        column = QVBoxLayout(page)
        column.setContentsMargins(0, 0, 0, 0)
        self.gh_account = self._account_combo("github")
        self.gh_search = QLineEdit()
        self.gh_search.setClearButtonEnabled(True)
        self.gh_search.setPlaceholderText("Search your repositories")
        self.gh_list = QListWidget()
        self.gh_typed = QLineEdit()
        self.gh_typed.setPlaceholderText("owner/name or a github.com address")
        self.gh_branch = QLineEdit()
        self.gh_branch.setPlaceholderText("Branch (optional)")
        self.gh_list.itemClicked.connect(lambda item: self.gh_typed.setText(item.data(256)))
        self.gh_timer = QTimer(self)
        self.gh_timer.setSingleShot(True)
        self.gh_timer.setInterval(250)
        self.gh_timer.timeout.connect(self._github_find)
        self.gh_search.textChanged.connect(lambda *_: self.gh_timer.start())
        self.gh_account.currentIndexChanged.connect(lambda *_: self._github_find())
        self.gh_add = button("Add", "primary", self._github_add)
        if self.gh_account.count() > 1:
            column.addWidget(self.gh_account)
        column.addWidget(self.gh_search)
        column.addWidget(self.gh_list, 1)
        row = QHBoxLayout()
        row.addWidget(self.gh_typed, 2)
        row.addWidget(self.gh_branch, 1)
        column.addLayout(row)
        add = QHBoxLayout()
        add.addStretch(1)
        add.addWidget(self.gh_add)
        column.addLayout(add)
        self._github_find()
        return page

    def _github_find(self) -> None:
        account, query = self.gh_account.currentData(), self.gh_search.text().strip()

        def found(result: object, error: str) -> None:
            if error:
                self.status.say(error, bad=True)
                return
            self.gh_list.clear()
            for repo in dict(result).get("repos", []):  # type: ignore[arg-type]
                item = QListWidgetItem(f"{repo['full_name']}  ·  {'Private' if repo.get('private') else 'Public'}"
                                       + (f"  ·  {repo['description']}" if repo.get("description") else ""))
                item.setData(256, repo["full_name"])
                self.gh_list.addItem(item)

        self._call(lambda api: api.browse_github(account, query), found)

    def _github_add(self) -> None:
        repo = self.gh_typed.text().strip()
        if not repo:
            self.status.say("Choose a repository or type one.", bad=True)
            return
        account, ref = self.gh_account.currentData(), self.gh_branch.text().strip()
        self._add("github_repo", repo=repo, ref=ref, account=account)

    def _drive_page(self, key: str) -> QWidget:
        if not self._accounts("gdrive"):
            return self._need_account("Connect a Google Drive account first.")
        page = QWidget()
        column = QVBoxLayout(page)
        column.setContentsMargins(0, 0, 0, 0)
        self.dr_kind = key
        self.dr_account = self._account_combo("gdrive")
        self.dr_search = QLineEdit()
        self.dr_search.setClearButtonEnabled(True)
        self.dr_search.setPlaceholderText("Search by name")
        self.dr_where = label("", tone_="muted")
        self.dr_list = QListWidget()
        self.dr_list.itemDoubleClicked.connect(self._drive_open)
        self.dr_timer = QTimer(self)
        self.dr_timer.setSingleShot(True)
        self.dr_timer.setInterval(300)
        self.dr_timer.timeout.connect(lambda: self._drive_show(self._drive_here["id"]))
        self.dr_search.textChanged.connect(lambda *_: self.dr_timer.start())
        self.dr_account.currentIndexChanged.connect(lambda *_: self._drive_show("root"))
        self.dr_up = button("Up", "", lambda: self._drive_show(self._drive_here.get("parent") or "root"))
        self.dr_use = button("Use this folder" if key == "drive_folder" else "Use the selected file", "primary", self._drive_use)
        if self.dr_account.count() > 1:
            column.addWidget(self.dr_account)
        column.addWidget(self.dr_search)
        column.addWidget(self.dr_where)
        column.addWidget(self.dr_list, 1)
        row = QHBoxLayout()
        row.addWidget(self.dr_up)
        row.addStretch(1)
        row.addWidget(self.dr_use)
        column.addLayout(row)
        column.addWidget(label("Double-click a folder to open it.", tone_="muted"))
        self._drive_show("root")
        return page

    def _drive_show(self, folder: str) -> None:
        account, query = self.dr_account.currentData(), self.dr_search.text().strip()

        def shown(result: object, error: str) -> None:
            if error:
                self.status.say(error, bad=True)
                return
            found = dict(result)  # type: ignore[arg-type]
            if found.get("folder"):
                self._drive_here = {"id": found["folder"]["id"], "name": found["folder"]["name"], "parent": found.get("parent")}
            self.dr_where.setText("Search results" if query else f"In {self._drive_here['name']}")
            self.dr_up.setVisible(not query and bool(self._drive_here.get("parent")))
            self.dr_list.clear()
            for entry in found.get("items", []):
                if self.dr_kind == "drive_file" and entry["folder"] and query:
                    continue
                item = QListWidgetItem(("📁 " if entry["folder"] else "📄 ") + entry["name"])
                item.setData(256, entry)
                self.dr_list.addItem(item)

        self._call(lambda api: api.browse_drive(account, folder, query), shown)

    def _drive_open(self, item: QListWidgetItem) -> None:
        entry = item.data(256)
        if entry["folder"]:
            self.dr_search.blockSignals(True)
            self.dr_search.clear()
            self.dr_search.blockSignals(False)
            self._drive_show(entry["id"])
        elif self.dr_kind == "drive_file":
            self._add("drive_file", account=self.dr_account.currentData(), file_id=entry["id"])

    def _drive_use(self) -> None:
        account = self.dr_account.currentData()
        if self.dr_kind == "drive_folder":
            self._add("drive_folder", account=account, file_id=self._drive_here["id"])
            return
        item = self.dr_list.currentItem()
        if item is None or item.data(256)["folder"]:
            self.status.say("Select a file.", bad=True)
            return
        self._add("drive_file", account=account, file_id=item.data(256)["id"])

    def _server_page(self, _key: str) -> QWidget:
        page = QWidget()
        column = QVBoxLayout(page)
        column.setContentsMargins(0, 0, 0, 0)
        self.sv_where = label("Folders the administrator allows", tone_="muted")
        self.sv_list = QListWidget()
        self.sv_list.itemDoubleClicked.connect(lambda item: self._server_show(item.data(256)))
        self.sv_path = ""
        self.sv_parent = ""
        self.sv_up = button("Up", "", lambda: self._server_show(self.sv_parent))
        self.sv_use = button("Use this folder", "primary", self._server_use)
        column.addWidget(self.sv_where)
        column.addWidget(self.sv_list, 1)
        row = QHBoxLayout()
        row.addWidget(self.sv_up)
        row.addStretch(1)
        row.addWidget(self.sv_use)
        column.addLayout(row)
        column.addWidget(label("Double-click a folder to open it.", tone_="muted"))
        self._server_show("")
        return page

    def _server_show(self, path: str) -> None:
        def shown(result: object, error: str) -> None:
            if error:
                self.status.say(error, bad=True)
                return
            found = dict(result)  # type: ignore[arg-type]
            self.sv_path, self.sv_parent = found.get("path", ""), found.get("parent") or ""
            self.sv_where.setText(self.sv_path or "Folders the administrator allows")
            self.sv_up.setVisible(bool(self.sv_path))
            self.sv_use.setVisible(bool(self.sv_path))
            self.sv_list.clear()
            for name in found.get("dirs", []):
                full = name if found.get("roots") else f"{self.sv_path}{chr(92) if chr(92) in self.sv_path else '/'}{name}"
                item = QListWidgetItem("📁 " + name)
                item.setData(256, full)
                self.sv_list.addItem(item)

        self._call(lambda api: api.browse_server(path), shown)

    def _server_use(self) -> None:
        if self.sv_path:
            self._add("server_path", path=self.sv_path)

    def _computer_page(self, _key: str) -> QWidget:
        page = QWidget()
        column = QVBoxLayout(page)
        column.setContentsMargins(0, 0, 0, 0)
        column.addWidget(label("Choose a folder of this computer. Clara can then read and work in it, only inside that folder, "
                               "and only while this app is running. The folders you add are listed in the app only: the server "
                               "never learns where they are.", wrap=True))
        column.addWidget(button("Choose a folder…", "primary", self._computer_choose))
        column.addStretch(1)
        return page

    def _computer_choose(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "Choose a folder Clara may work in")
        if folder:
            self.add_computer_folder(folder)

    def add_computer_folder(self, folder: str) -> None:
        try:
            registry = FolderRegistry.load()
            alias = registry.add(folder)
        except (FolderError, OSError) as error:
            self.status.say(str(error), bad=True)
            return
        self._add("computer_path", device=registry.device, alias=alias, label=f"{alias} on {registry.name}")

    # -- adding ------------------------------------------------------------------------------------------ #

    def _add(self, kind: str, **fields: object) -> None:
        self.status.say("Adding…")

        def added(_result: object, error: str) -> None:
            if error:
                self.status.say(error, bad=True)
                return
            self.did_add = True
            self.status.say("Added.")

        self._call(lambda api: api.add_resource(kind, **fields), added)

    def done(self, code: int) -> None:
        self.stop_calls()
        super().done(code)


class ConnectionsDialog(QDialog, Calls):
    """What is attached to a project, or to a conversation (and, for a conversation, what its project gives it)."""

    def __init__(self, target: dict, get_config: Callable[[], Config], api_factory: Callable[[Config], ClaraApi], parent: QWidget | None = None, heading: str = ""):
        super().__init__(parent)
        self._init_calls(get_config, api_factory)
        self.target = target  # {"project": id} or {"conversation": id}
        self.found: dict = {"attachments": [], "inherited": []}
        self.known: list[dict] = []
        self.setWindowTitle("Connections")
        self.setMinimumWidth(600)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(20, 18, 20, 16)
        outer.setSpacing(10)
        outer.addWidget(label("Connections", heading="section"))
        outer.addWidget(label(heading or ("What Clara can reach in every conversation of this project." if "project" in target
                                           else "What Clara can reach in this conversation, besides what its project gives her."), tone_="muted", wrap=True))
        self.notice = Notice("")
        self.notice.hide()
        outer.addWidget(self.notice)
        self.panel = Panel()
        self.panel.body.setContentsMargins(0, 0, 0, 0)
        self.panel.body.setSpacing(0)
        outer.addWidget(self.panel)
        row = QHBoxLayout()
        row.addWidget(button("Attach…", "primary", self._attach))
        row.addStretch(1)
        row.addWidget(button("Close", "", self.accept))
        outer.addLayout(row)
        self.reload()

    def reload(self) -> None:
        self._call(lambda api: (api.attachments(**self.target), api.integrations()), self._loaded)

    def _loaded(self, result: object, error: str) -> None:
        if error:
            self.notice.say(error, bad=True)
            return
        self.notice.say("")
        self.found, overview = result  # type: ignore[misc]
        self.known = overview["types"]
        self._draw(overview)

    def _draw(self, overview: dict) -> None:
        clear_layout(self.panel.body)
        rows = [(item, True) for item in self.found.get("inherited", [])] + [(item, False) for item in self.found.get("attachments", [])]
        if not rows:
            self.panel.body.addWidget(EmptyState("Nothing connected here yet", "Attach a repository, a Drive folder or a folder."))
            return
        for position, (item, inherited) in enumerate(rows):
            if position:
                self.panel.body.addWidget(divider())
            resource = item["resource"]
            glyph, kind = KINDS.get(resource["kind"], ("file", resource["kind"]))
            actions = [tool_button("shield", "What Clara may do here", lambda i=item: self._levels(i))]
            if not inherited:
                actions.append(tool_button("close", "Detach", lambda i=item: self._detach(i)))
            self.panel.body.addWidget(list_row(glyph, resource["label"], kind, level_badges(item["effective"]), actions,
                                               badge("From the project") if inherited else None))

    def _levels(self, item: dict) -> None:
        resource = item["resource"]
        ceiling = next((t.get("ceiling", {}) for t in self.known if t["id"] == resource["type"]), {})
        dialog = LevelsDialog(f"{resource['label']}: here",
                              "For every conversation of this project." if "project" in self.target
                              else "For this conversation only. “Same as the resource” follows its own permissions.",
                              item["levels"], "Same as the resource", ceiling, self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        levels = dialog.levels()
        self._call(lambda api: api.attach(resource["id"], levels=levels, **self.target), lambda _r, e: self._after(e))

    def _detach(self, item: dict) -> None:
        self._call(lambda api: api.detach(item["attachment"]), lambda _r, e: self._after(e))

    def _after(self, error: str) -> None:
        if error:
            self.notice.say(error, bad=True)
            return
        self.reload()

    def _attach(self) -> None:
        def listed(result: object, error: str) -> None:
            if error:
                self.notice.say(error, bad=True)
                return
            attached = {i["resource"]["id"] for i in self.found.get("attachments", []) + self.found.get("inherited", [])}
            free = [r for r in dict(result)["resources"] if r["id"] not in attached]  # type: ignore[arg-type]
            if not free:
                self.notice.say("Everything you added is attached already. Add more in Integrations." if dict(result)["resources"]  # type: ignore[arg-type]
                                else "You have not added anything yet: add a repository or a folder in Integrations.")
                return
            chooser = QDialog(self)
            chooser.setWindowTitle("Attach")
            chooser.setMinimumWidth(420)
            column = QVBoxLayout(chooser)
            column.addWidget(label("Attach", heading="section"))
            choices = QListWidget()
            for resource in free:
                entry = QListWidgetItem(f"{resource['label']}  ·  {KINDS.get(resource['kind'], ('', resource['kind']))[1]}")
                entry.setData(256, resource["id"])
                choices.addItem(entry)
            choices.itemDoubleClicked.connect(lambda _item: chooser.accept())
            column.addWidget(choices)
            row = QHBoxLayout()
            row.addStretch(1)
            row.addWidget(button("Cancel", "", chooser.reject))
            row.addWidget(button("Attach", "primary", chooser.accept))
            column.addLayout(row)
            if chooser.exec() == QDialog.DialogCode.Accepted and choices.currentItem() is not None:
                chosen = choices.currentItem().data(256)
                self._call(lambda api: api.attach(chosen, **self.target), lambda _r, e: self._after(e))

        self._call(lambda api: api.integrations(), listed)

    def done(self, code: int) -> None:
        self.stop_calls()
        super().done(code)


class IntegrationsPage(Page, Calls):
    page_title = "Integrations"

    def __init__(self, get_config: Callable[[], Config], api_factory: Callable[[Config], ClaraApi], host=None):
        super().__init__()
        self._init_calls(get_config, api_factory)
        self.host = host
        self.data: dict | None = None
        self.pending: list[dict] = []
        self.is_admin = False
        self._timer: QTimer | None = None
        self._polls = 0
        self._before = ""
        self.status = StatusLine()
        self.approvals_panel = Panel("Waiting for your permission", "Clara asked, and carries on with other work until you answer.")
        self.approvals_panel.hide()
        self.accounts_panel = Panel("Accounts", "What you connected is kept encrypted on the server and is never shown again.")
        self.resources_panel = Panel("Repositories and folders", "What you can attach to a project or a conversation.")
        self.settings_panel = Panel("When you do not answer")
        self.admin_panel = Panel("Administration", "Which integrations are on, how far people may open them up, the folders of the server they may pick from, and a log of what Clara did.")
        self.admin_panel.hide()
        self.notify_box = QComboBox()
        for value, text in NOTIFY_CHOICES:
            self.notify_box.addItem(text, value)
        self.notify_box.activated.connect(self._notify_chosen)
        self.settings_panel.add(label("A request waits in its conversation. If you have not answered, it is sent to your other devices so that you can answer there.", tone_="muted", wrap=True))
        row = QHBoxLayout()
        row.addWidget(label("Send it to my other devices after"))
        row.addWidget(self.notify_box)
        row.addStretch(1)
        self.settings_panel.add(row)

        column = Column(wide=True)
        column.add(label("Connect your GitHub and Google Drive accounts, add folders, then attach them to a project or a conversation: Clara can then read and work "
                         "on them. You decide what she may do. She asks you before anything that replaces or deletes, and goes on with something else while she waits.",
                         tone_="muted", wrap=True))
        for panel in (self.approvals_panel, self.accounts_panel, self.resources_panel, self.settings_panel, self.admin_panel):
            column.add(panel)
        column.add(self.status)
        column.finish()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(column)

    def activated(self) -> None:
        if self._get_config().ready:
            self.refresh()

    def shutdown(self) -> None:
        if self._timer is not None:
            self._timer.stop()
        self.stop_calls()

    def refresh(self) -> None:
        self._call(lambda api: (api.integrations(), api.approvals()), self._loaded)

    def _loaded(self, result: object, error: str) -> None:
        if error:
            self.status.say(error, bad=True)
            return
        self.data, self.pending = result  # type: ignore[misc]
        self.status.say("")
        self._draw_approvals()
        self._draw_accounts()
        self._draw_resources()
        current = self.data["settings"].get("approval_notify_after")
        self.notify_box.setCurrentIndex(max(0, self.notify_box.findData("" if current is None else str(current))))
        self._call(lambda api: api.me(), self._me)

    def _me(self, result: object, error: str) -> None:
        admin = bool(not error and dict(result).get("is_admin"))  # type: ignore[arg-type]
        if admin and not self.is_admin:
            self.is_admin = True
            self.admin_panel.show()
        if admin:
            self._call(lambda api: (api.admin_integrations(), api.admin_integrations_log()), self._admin_loaded)

    # ---- waiting requests ----
    def _draw_approvals(self) -> None:
        self.approvals_panel.setVisible(bool(self.pending))
        clear_layout(self.approvals_panel.body)
        for approval in self.pending:
            self.approvals_panel.body.addWidget(label(f"In {approval.get('conversation', '')}", tone_="muted"))
            card = ApprovalCard(approval, self._get_config, self._api_factory)
            card.decided.connect(lambda _done: self.refresh())
            self.approvals_panel.body.addWidget(card)

    # ---- accounts ----
    def _types(self) -> dict[str, dict]:
        return {t["id"]: t for t in (self.data or {}).get("types", [])}

    def _draw_accounts(self) -> None:
        assert self.data is not None
        clear_layout(self.accounts_panel.body)
        self.accounts_panel.body.setContentsMargins(0, 0, 0, 0)
        self.accounts_panel.body.setSpacing(0)
        types = self._types()
        bar = QHBoxLayout()
        bar.setContentsMargins(18, 10, 18, 10)
        github, drive = types.get("github", {}), types.get("gdrive", {})
        self.connect_github_button = button("Connect GitHub", "", self.connect_github)
        self.connect_google_button = button("Connect Google Drive", "", self.connect_google)
        bar.addWidget(self.connect_github_button)
        bar.addWidget(self.connect_google_button)
        bar.addStretch(1)
        self.accounts_panel.body.addLayout(bar)
        # shown or hidden once they are in the page: a widget with no parent that is shown becomes a window of its own
        self.connect_github_button.setVisible(bool(github.get("enabled") and github.get("available")))
        self.connect_google_button.setVisible(bool(drive.get("enabled") and drive.get("available")))
        if drive.get("enabled") and not drive.get("available"):
            holder = QWidget()
            inner = QVBoxLayout(holder)
            inner.setContentsMargins(18, 0, 18, 10)
            inner.addWidget(Notice("Google Drive is not set up on this server: the administrator has to put a Google client in the server's .env (GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET)."))
            self.accounts_panel.body.addWidget(holder)
        accounts = self.data["accounts"]
        if not accounts:
            self.accounts_panel.body.addWidget(EmptyState("No account yet", "Connect GitHub with a token, or Google Drive with your Google account."))
            return
        for account in accounts:
            self.accounts_panel.body.addWidget(divider())
            glyph, name = ACCOUNTS.get(account["kind"], ("link", account["kind"]))
            needs = account["status"] != "ok"
            actions: list[QWidget] = []
            if needs:
                actions.append(button("Reconnect", "", self.connect_github if account["kind"] == "github" else self.connect_google))
            actions.append(tool_button("shield", "Permissions", lambda a=account: self.account_levels(a)))
            actions.append(tool_button("trash", "Disconnect", lambda a=account: self.disconnect(a), "danger"))
            self.accounts_panel.body.addWidget(list_row(
                glyph, f"{account['label']}", f"{name} · default for what you add from it", level_badges({**DEFAULT_LEVELS, **account["levels"]}), actions,
                badge("Connect it again", "off") if needs else badge("Connected", "ok")))

    def connect_github(self) -> None:
        token = ask_text(self, "Connect GitHub", "Personal access token", "", "Connect", password=True,
                         hint="Make a fine-grained token at github.com/settings/personal-access-tokens, with access to the repositories Clara may use. "
                              "“Contents: read and write” lets her commit; add “Pull requests” and “Issues” for those too.")
        if not token or not token.strip():
            return
        self._call(lambda api: api.connect_github(token.strip()),
                   lambda result, error: self.status.say(error, bad=True) if error else (self.status.say(f"GitHub connected as {dict(result)['label']}."), self.refresh()))  # type: ignore[arg-type]

    def connect_google(self) -> None:
        def started(result: object, error: str) -> None:
            if error:
                self.status.say(error, bad=True)
                return
            QDesktopServices.openUrl(QUrl(dict(result)["url"]))  # type: ignore[arg-type]
            self.status.say("Finish in your browser: this page updates when you are back.")
            self._before = repr([(a["id"], a["status"]) for a in (self.data or {}).get("accounts", [])])
            self._polls = 0
            if self._timer is None:
                self._timer = QTimer(self)
                self._timer.setInterval(GOOGLE_POLL_MS)
                self._timer.timeout.connect(self._poll_google)
            self._timer.start()

        self._call(lambda api: api.google_start(), started)

    def _poll_google(self) -> None:
        self._polls += 1

        def seen(result: object, error: str) -> None:
            if error or self._timer is None:
                return
            now = repr([(a["id"], a["status"]) for a in dict(result)["accounts"]])  # type: ignore[arg-type]
            if now != self._before or self._polls > GOOGLE_POLLS:
                self._timer.stop()
                self.refresh()

        self._call(lambda api: api.integrations(), seen)

    def account_levels(self, account: dict) -> None:
        ceiling = self._types().get("github" if account["kind"] == "github" else "gdrive", {}).get("ceiling", {})
        dialog = LevelsDialog(f"Permissions of {account['label']}",
                              "What Clara may do with the repositories and folders you add from this account, unless one of them says otherwise.",
                              account["levels"], "", ceiling, self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            levels = dialog.levels()
            self._call(lambda api: api.set_account_levels(account["id"], levels), lambda _r, e: self._saved(e))

    def disconnect(self, account: dict) -> None:
        if not confirm(self, "Disconnect", f"Disconnect {account['label']}? The repositories and folders you added from it are removed from your projects and conversations. "
                                           f"Nothing is deleted on {ACCOUNTS.get(account['kind'], ('', 'the other side'))[1]}.", "Disconnect", danger=True):
            return
        self._call(lambda api: api.disconnect_account(account["id"]), lambda _r, e: self._saved(e))

    def _saved(self, error: str) -> None:
        if error:
            self.status.say(error, bad=True)
            return
        self.status.say("Saved.")
        self.refresh()

    # ---- resources ----
    def _draw_resources(self) -> None:
        assert self.data is not None
        clear_layout(self.resources_panel.body)
        self.resources_panel.body.setContentsMargins(0, 0, 0, 0)
        self.resources_panel.body.setSpacing(0)
        types = self._types()
        usable = any(t.get("enabled") and t.get("available") for t in types.values())
        bar = QHBoxLayout()
        bar.setContentsMargins(18, 10, 18, 10)
        self.add_button = button("Add…", "primary", self.add_resource)
        self.add_button.setEnabled(usable)
        bar.addWidget(self.add_button)
        bar.addStretch(1)
        self.resources_panel.body.addLayout(bar)
        resources = self.data["resources"]
        if not resources:
            self.resources_panel.body.addWidget(EmptyState("Nothing added yet", "Add a repository, a Drive folder or a folder, then attach it where you want Clara to use it."))
            return
        here = FolderRegistry.load()
        for resource in resources:
            self.resources_panel.body.addWidget(divider())
            glyph, kind = KINDS.get(resource["kind"], ("file", resource["kind"]))
            account = next((a for a in self.data["accounts"] if a["id"] == resource.get("account")), None)
            used = resource.get("attachments", 0)
            sub = kind + (f" · {account['label']}" if account else "")
            if resource["kind"] == "computer_path":
                mine = resource["locator"].get("device") == here.device
                sub += " · this computer" if mine else " · another computer"
            sub += f" · used in {used} place{'s' if used != 1 else ''}" if used else " · not used yet"
            self.resources_panel.body.addWidget(list_row(
                glyph, resource["label"], sub, level_badges(resource["effective"]),
                [tool_button("shield", "Permissions", lambda r=resource: self.resource_levels(r)),
                 tool_button("trash", "Remove", lambda r=resource: self.remove_resource(r), "danger")]))

    def add_resource(self) -> None:
        if self.data is None:
            return
        dialog = AddResourceDialog(self.data, self._get_config, self._api_factory, self)
        dialog.exec()
        if dialog.did_add:
            self.refresh()

    def resource_levels(self, resource: dict) -> None:
        ceiling = self._types().get(resource["type"], {}).get("ceiling", {})
        dialog = LevelsDialog(f"Permissions of {resource['label']}",
                              "What Clara may do here. “Same as the account” follows the account's default. A project or a conversation can still decide otherwise.",
                              resource["levels"], "Same as the account", ceiling, self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            levels = dialog.levels()
            self._call(lambda api: api.update_resource(resource["id"], levels=levels), lambda _r, e: self._saved(e))

    def remove_resource(self, resource: dict) -> None:
        if not confirm(self, "Remove", f"Remove {resource['label']}? Clara can no longer reach it from the projects and conversations it was attached to. Nothing is deleted there.", "Remove", danger=True):
            return
        if resource["kind"] == "computer_path":
            registry = FolderRegistry.load()
            if resource["locator"].get("device") == registry.device:
                registry.remove(resource["locator"].get("alias", ""))
        self._call(lambda api: api.delete_resource(resource["id"]), lambda _r, e: self._saved(e))

    # ---- settings ----
    def _notify_chosen(self) -> None:
        value = self.notify_box.currentData()
        seconds = None if value == "" else int(value)
        self._call(lambda api: api.set_approval_notify_after(seconds), lambda _r, e: self._saved(e) if e else self.status.say("Saved."))

    # ---- administrator ----
    def _admin_loaded(self, result: object, error: str) -> None:
        if error:
            return
        policy, entries = result  # type: ignore[misc]
        clear_layout(self.admin_panel.body)
        for kind in policy["types"]:
            row = QHBoxLayout()
            on = QCheckBox(kind["name"])
            on.setChecked(bool(policy["enabled"].get(kind["id"])))
            on.toggled.connect(lambda checked, k=kind["id"]: self._admin_save(enabled={k: checked}))
            row.addWidget(on, 1)
            if not kind["available"]:
                row.addWidget(label("Not available here", tone_="muted"))
            for level, _name, _help in LEVELS:
                box = QComboBox()
                for value, text in (("", "No limit"), ("ask", "Always ask"), ("deny", "Never")):
                    box.addItem(f"{SHORT[level]}: {text}", value)
                box.setCurrentIndex(max(0, box.findData(policy["ceiling"].get(kind["id"], {}).get(level, ""))))
                box.activated.connect(lambda _i, b=box, k=kind["id"], lv=level, pol=policy: self._admin_ceiling(pol, k, lv, b.currentData()))
                row.addWidget(box)
            self.admin_panel.body.addLayout(row)
        self.admin_panel.body.addWidget(divider())
        self.admin_panel.body.addWidget(label("Folders of the server people may pick from", heading="small"))
        for root in policy["roots"]:
            line = QHBoxLayout()
            line.addWidget(label(root), 1)
            line.addWidget(tool_button("trash", "Remove", lambda r=root, pol=policy: self._admin_save(roots=[x for x in pol["roots"] if x != r]), "danger"))
            self.admin_panel.body.addLayout(line)
        add = QHBoxLayout()
        self.root_field = QLineEdit()
        self.root_field.setPlaceholderText("A folder on the server, e.g. /srv/clara-files")
        add.addWidget(self.root_field, 1)
        add.addWidget(button("Allow this folder", "", lambda pol=policy: self._admin_add_root(pol)))
        self.admin_panel.body.addLayout(add)
        self.admin_panel.body.addWidget(divider())
        self.admin_panel.body.addWidget(label("Log", heading="small"))
        if not entries:
            self.admin_panel.body.addWidget(label("Nothing yet.", tone_="muted"))
        for entry in entries[:30]:
            self.admin_panel.body.addWidget(label(f"{ago(entry['at'])} · {entry.get('person') or '–'} · {entry['summary']} · {entry['outcome']}", wrap=True))

    def _admin_add_root(self, policy: dict) -> None:
        text = self.root_field.text().strip()
        if text:
            self._admin_save(roots=[*policy["roots"], text])

    def _admin_ceiling(self, policy: dict, kind: str, level: str, value: str) -> None:
        ceiling = dict(policy["ceiling"].get(kind, {}))
        if value:
            ceiling[level] = value
        else:
            ceiling.pop(level, None)
        self._admin_save(ceiling={kind: ceiling})

    def _admin_save(self, **change: object) -> None:
        self._call(lambda api: api.admin_set_integrations(**change), lambda _r, e: self._saved(e))
