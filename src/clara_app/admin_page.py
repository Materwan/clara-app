"""The Admin page (administrators): users, the models people may choose, the server, what Clara knows about people, and
the server's console. The same tools as on the web site; every call runs off the UI thread."""

from __future__ import annotations

from typing import Callable

from PySide6.QtCore import QTimer, Qt
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDoubleSpinBox,
    QFormLayout,
    QHBoxLayout,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QPlainTextEdit,
    QSpinBox,
    QStackedWidget,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from .account_page import cost
from .api import ClaraApi
from .config import Config
from .icons import bind_icon
from .widgets import (
    Calls,
    Column,
    EmptyState,
    Notice,
    Page,
    Panel,
    Segmented,
    StatusLine,
    ago,
    avatar,
    badge,
    button,
    confirm,
    divider,
    duration,
    fit_table,
    key_values,
    label,
    make_table,
    parse_credits,
    secret_dialog,
    ask_text,
    token_count,
)

TABS = (("users", "Users"), ("models", "Models"), ("server", "Server"), ("people", "People & memory"), ("console", "Console"))
STATUS_MS = 5000
SHOWN_MODELS = 300


class Tab(Column, Calls):
    """One section of the page: a scrolling column that talks to the server."""

    def __init__(self, get_config, api_factory, host, wide: bool = True):
        Column.__init__(self, wide=wide)
        self._init_calls(get_config, api_factory)
        self._host = host
        self.status = StatusLine()

    def activated(self) -> None:
        """The tab is shown: read it."""

    def deactivated(self) -> None:
        """The tab is hidden: stop whatever refreshes it."""

    def say(self, result: object, error: str, done: str = "Saved.") -> bool:
        """Show what a change answered; True when it worked."""
        self.status.say(error or done, bad=bool(error))
        return not error


# ---- users ------------------------------------------------------------------------------------------------------


class AddUserDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Add a user")
        self.setMinimumWidth(420)
        self.name = QLineEdit()
        self.name.setPlaceholderText("Letters, digits, dots, dashes and underscores")
        self.password = QLineEdit()
        self.password.setPlaceholderText("Leave empty to generate one (10 characters or more)")
        self.admin = QCheckBox("Administrator")
        self.discord = QLineEdit()
        self.discord.setPlaceholderText("Discord user id (optional)")
        form = QFormLayout()
        form.addRow("User name", self.name)
        form.addRow("Password", self.password)
        form.addRow("", self.admin)
        form.addRow("Discord account", self.discord)
        self.add = button("Add user", "primary", self.accept)
        row = QHBoxLayout()
        row.addStretch(1)
        row.addWidget(button("Cancel", "", self.reject))
        row.addWidget(self.add)
        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(label("With a Discord account, Clara answers them there right away (no /login) and the user starts with what "
                               "she already knows from it.", tone_="muted", wrap=True))
        layout.addLayout(row)

    def values(self) -> tuple[str, str | None, bool, str | None]:
        return self.name.text().strip(), self.password.text() or None, self.admin.isChecked(), self.discord.text().strip() or None


class UsersTab(Tab):
    def __init__(self, *args):
        super().__init__(*args)
        self.users: list[dict] = []
        self.default_limit: int | None = None
        self.count = label("", tone_="muted")
        self.default_button = button("Default limit: none", "", self.set_default_limit)
        self.default_button.setToolTip("Credits a day for users who have no limit of their own")
        row = QHBoxLayout()
        row.addWidget(self.count, 1)
        row.addWidget(self.default_button)
        row.addWidget(button("Add user", "primary", self.add_user))
        holder = QWidget()
        holder.setLayout(row)
        row.setContentsMargins(0, 0, 0, 0)
        self.panel = Panel()
        self.panel.body.setContentsMargins(0, 0, 0, 0)
        self.column.addWidget(holder)
        self.column.addWidget(self.panel)
        self.column.addWidget(self.status)
        self.finish()

    def activated(self) -> None:
        self._call(lambda api: (api.admin_limits(), api.admin_users()), self._loaded)

    def _loaded(self, result: object, error: str) -> None:
        if error:
            self.status.say(error, bad=True)
            return
        limits, self.users = result  # type: ignore[misc]
        self.default_limit = limits.get("default") or None
        self.default_button.setText(f"Default limit: {token_count(self.default_limit)} a day" if self.default_limit else "Default limit: none")
        self.count.setText(f"{len(self.users)} {'person' if len(self.users) == 1 else 'people'} can sign in")
        self._draw()

    def _draw(self) -> None:
        body = self.panel.body
        while body.count():
            item = body.takeAt(0)
            if item.widget() is not None:
                item.widget().hide()
                item.widget().deleteLater()
        table = make_table(["User", "Role", "Person", "Devices", "Credits today", "Last sign-in", ""], 0)
        table.setRowCount(len(self.users))
        for row, user in enumerate(self.users):
            name = QWidget()
            line = QHBoxLayout(name)
            line.setContentsMargins(10, 4, 4, 4)
            line.addWidget(avatar(user["name"], 28))
            texts = QVBoxLayout()
            texts.setSpacing(0)
            texts.addWidget(label(user["name"], heading="small"))
            discord = ", ".join(a.get("discord_name") or str(a["user_id"]) for a in user.get("discord_accounts", []))
            if discord:
                texts.addWidget(label(f"Discord: {discord}", tone_="muted"))
            line.addLayout(texts, 1)
            table.put(row, 0, name)
            role = badge("Disabled", "off") if user.get("disabled") else badge("Administrator", "admin") if user["is_admin"] else badge("User")
            table.put(row, 1, role)
            table.put(row, 2, label(f"{user['person']['name']} (#{user['person']['id']})" if user.get("person") else "None"))
            table.put(row, 3, label(str(user["sessions"] + len(user.get("signed_in_accounts", [])))))
            table.put(row, 4, label(self._usage_text(user)))
            table.put(row, 5, label(ago(user.get("last_login_at"))))
            more = QToolButton()
            more.setToolTip("Actions")
            bind_icon(more, "more", "muted", 20)
            more.clicked.connect(lambda _=False, u=user, b=more: self._menu(u, b))
            table.put(row, 6, more)
        fit_table(table)
        body.addWidget(table)

    @staticmethod
    def _usage_text(user: dict) -> str:
        usage = user["usage"]
        if not usage.get("limit"):
            return f"{token_count(usage['used'])}  ·  no limit"
        return f"{token_count(usage['used'])} / {token_count(usage['limit'])}" + ("" if usage.get("own_limit") is not None else "  ·  default")

    def _menu(self, user: dict, anchor: QWidget) -> None:
        menu = QMenu(self)
        menu.addAction("Generate a new password", lambda: self.reset(user, True))
        menu.addAction("Set a password…", lambda: self.reset(user, False))
        menu.addAction("Remove administrator rights" if user["is_admin"] else "Make administrator", lambda: self.change(user, admin=not user["is_admin"]))
        menu.addAction("Set the daily credit limit…", lambda: self.set_limit(user))
        if user["usage"].get("own_limit") is not None:
            menu.addAction("Use the default credit limit", lambda: self.change(user, follow_default_limit=True))
        menu.addAction("Enable" if user.get("disabled") else "Disable", lambda: self.change(user, disabled=not user.get("disabled")))
        menu.addSeparator()
        for account in user.get("discord_accounts", []):
            menu.addAction(f"Sign out of Discord: {account.get('discord_name') or account['user_id']}",
                           lambda a=account: self.sign_out_discord(user, a))
        menu.addAction("Sign out everywhere", lambda: self.sign_out(user))
        menu.addSeparator()
        menu.addAction("Remove user…", lambda: self.remove(user))
        menu.exec(anchor.mapToGlobal(anchor.rect().bottomLeft()))
        menu.deleteLater()

    def change(self, user: dict, **change: object) -> None:
        self._call(lambda api: api.admin_change_user(user["name"], **change), lambda r, e: (self.say(r, e), self.activated()))

    def set_limit(self, user: dict) -> None:
        own = user["usage"].get("own_limit")
        value = ask_text(self, f"Daily credit limit for {user['name']}", "Credits a day (500000, 500k, 2m, or off)",
                         "" if own is None else "off" if own == 0 else str(own), "Save",
                         hint="Administrators have no limit whatever is set here. Empty: follow the default.")
        if value is None:
            return
        if not value.strip():
            return self.change(user, follow_default_limit=True)
        credits = parse_credits(value)
        if credits is None:
            self.status.say("Not a number of credits: try 500000, 500k, 2m or off.", bad=True)
            return
        self.change(user, token_limit=credits)

    def set_default_limit(self) -> None:
        value = ask_text(self, "Default daily credit limit", "Credits a day (500000, 500k, 2m, or off)",
                         str(self.default_limit) if self.default_limit else "off", "Save",
                         hint="For every user who has no limit of their own. Administrators never have one.")
        if value is None:
            return
        credits = parse_credits(value)
        if credits is None:
            self.status.say("Not a number of credits: try 500000, 500k, 2m or off.", bad=True)
            return
        self._call(lambda api: api.admin_set_default_limit(credits), lambda r, e: (self.say(r, e), self.activated()))

    def reset(self, user: dict, generate: bool) -> None:
        if generate:
            if not confirm(self, "New password", f"Generate a new password for {user['name']}? They are signed out everywhere.", "Generate"):
                return
            change: dict = {"generate_password": True}
        else:
            value = ask_text(self, f"Password for {user['name']}", "New password (10 characters or more)", "", "Set password", True,
                             "They are signed out everywhere.")
            if not value:
                return
            change = {"password": value}

        def done(result: object, error: str) -> None:
            if self.say(result, error, "Password set."):
                if generate and isinstance(result, dict):
                    secret_dialog(self, f"Password for {user['name']}", "Give it to them; they can change it on the Account page.", result["password"])
            self.activated()

        self._call(lambda api: api.admin_change_user(user["name"], **change), done)

    def sign_out(self, user: dict) -> None:
        def done(result: object, error: str) -> None:
            self.say(result, error, f"{(result or {}).get('signed_out', 0)} device(s) signed out.")  # type: ignore[union-attr]
            self.activated()

        self._call(lambda api: api.admin_sign_out_user(user["name"]), done)

    def sign_out_discord(self, user: dict, account: dict) -> None:
        name = account.get("discord_name") or account["user_id"]
        if confirm(self, "Sign out of Discord", f"Sign {name} out of {user['name']}? Clara stops answering them on Discord until they sign in again.", "Sign out", True):
            self._call(lambda api: api.discord_sign_out(str(account["user_id"])), lambda r, e: (self.say(r, e, "Signed out."), self.activated()))

    def remove(self, user: dict) -> None:
        if confirm(self, "Remove user", f"{user['name']} will no longer be able to sign in. Their memories and conversations are kept "
                                        "(erase them under People & memory).", "Remove", True):
            self._call(lambda api: api.admin_remove_user(user["name"]), lambda r, e: (self.say(r, e, "User removed."), self.activated()))

    def add_user(self) -> None:
        dialog = AddUserDialog(self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        name, password, admin, discord = dialog.values()

        def done(result: object, error: str) -> None:
            if self.say(result, error, "User added.") and isinstance(result, dict) and result.get("password"):
                secret_dialog(self, f"{result['user']['name']} was added", "Give them this password; they can change it on the Account page.", result["password"])
            self.activated()

        self._call(lambda api: api.admin_add_user(name, password, admin, discord), done)


# ---- models ---------------------------------------------------------------------------------------------------------


class ModelsTab(Tab):
    def __init__(self, *args):
        super().__init__(*args)
        self.data: dict | None = None
        self.head = Panel("Models for users", "Users see only the models selected below, each with its cost. A token (read or written) costs the model's weight in credits.")
        self.default_text = QLineEdit()
        self.default_text.setReadOnly(True)
        self.discord = QComboBox()
        self.discord.activated.connect(self._discord_chosen)
        form = QFormLayout()
        form.addRow("Server default (for users who chose none)", self.default_text)
        form.addRow("Discord", self.discord)
        self.head.add(form)
        self.problems = QVBoxLayout()
        problems = QWidget()
        problems.setLayout(self.problems)
        self.search = QLineEdit()
        self.search.setClearButtonEnabled(True)
        self.search.setPlaceholderText("Search models")
        self.search.textChanged.connect(lambda *_: self._timer.start())
        self.provider = QComboBox()
        self.provider.activated.connect(lambda *_: self._draw())
        self.provider.setMinimumWidth(170)
        self.count = label("", tone_="muted")
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(200)
        self._timer.timeout.connect(self._draw)
        bar = QHBoxLayout()
        bar.addWidget(self.search, 1)
        bar.addWidget(self.provider)
        bar2 = QHBoxLayout()
        bar2.addWidget(self.count, 1)
        bar2.addWidget(button("Select shown", "", lambda: self._bulk(True)))
        bar2.addWidget(button("Unselect shown", "", lambda: self._bulk(False)))
        bar2.addWidget(button("Refresh", "", lambda: self.load(True)))
        row1, row2 = QWidget(), QWidget()
        row1.setLayout(bar)
        row2.setLayout(bar2)
        for row in (bar, bar2):
            row.setContentsMargins(0, 0, 0, 0)
        self.table_panel = Panel()
        self.table_panel.body.setContentsMargins(0, 0, 0, 0)
        for widget in (self.head, problems, row1, row2, self.table_panel, self.status):
            self.column.addWidget(widget)
        self.finish()

    def activated(self) -> None:
        self.load()

    def load(self, force: bool = False) -> None:
        self._call(lambda api: api.admin_catalog(force), self._loaded)

    def _loaded(self, data: object, error: str) -> None:
        if error:
            self.status.say(error, bad=True)
            return
        self.data = data  # type: ignore[assignment]
        self._draw_head()
        self._draw()

    def _change(self, refs: list[str], **change: object) -> None:
        def done(data: object, error: str) -> None:
            if error:
                self.status.say(error, bad=True)
            else:
                self.data = data  # type: ignore[assignment]
            self._draw_head()
            self._draw()

        self._call(lambda api: api.admin_change_catalog(refs, **change), done)

    def _draw_head(self) -> None:
        data = self.data or {}
        by_ref = {m["ref"]: m for m in data.get("models", [])}
        default = by_ref.get(data.get("default"))
        self.default_text.setText(f"{data.get('default', '')}" + (f", {cost(default)}" if default else ""))
        self.discord.blockSignals(True)
        self.discord.clear()
        self.discord.addItem(f"Server default: {data.get('default', '')}", None)
        for model in data.get("models", []):
            self.discord.addItem(f"{model['ref']}, {cost(model)}", model["ref"])
        chosen = data.get("discord")
        self.discord.setCurrentIndex(max(0, self.discord.findData(chosen) if chosen in by_ref else 0))
        self.discord.blockSignals(False)
        while self.problems.count():
            item = self.problems.takeAt(0)
            if item.widget() is not None:
                item.widget().deleteLater()
        providers = {p["id"]: p["label"] for p in data.get("providers", [])}
        for pid, why in (data.get("problems") or {}).items():
            self.problems.addWidget(Notice(f"{providers.get(pid, pid)} did not answer: {why}", bad=True))
        keep = self.provider.currentData()
        self.provider.blockSignals(True)
        self.provider.clear()
        self.provider.addItem("All providers", "")
        for p in data.get("providers", []):
            if p.get("usable"):
                self.provider.addItem(p["label"], p["id"])
        self.provider.setCurrentIndex(max(0, self.provider.findData(keep)))
        self.provider.blockSignals(False)

    def _discord_chosen(self, index: int) -> None:
        model = self.discord.itemData(index)
        self._call(lambda api: api.admin_set_discord_model(model), lambda r, e: self.say(r, e, "Discord now answers with that model." if model else "Discord follows the server's model."))

    def _shown(self) -> list[dict]:
        needle = self.search.text().strip().lower()
        provider = self.provider.currentData()
        return [m for m in (self.data or {}).get("models", [])
                if (not provider or m["provider"] == provider) and (not needle or needle in m["ref"].lower() or needle in m["provider_label"].lower())]

    def _bulk(self, enabled: bool) -> None:
        refs = [m["ref"] for m in self._shown()]
        if refs:
            self._change(refs, enabled=enabled)

    def _draw(self) -> None:
        body = self.table_panel.body
        while body.count():
            item = body.takeAt(0)
            if item.widget() is not None:
                item.widget().hide()
                item.widget().deleteLater()
        if not self.data:
            return
        rows = self._shown()
        everything = self.data.get("models", [])
        selected = sum(1 for m in everything if m["enabled"])
        self.count.setText(f"{selected} of {len(everything)} selected" + (f", {len(rows)} shown" if len(rows) != len(everything) else ""))
        if not rows:
            body.addWidget(EmptyState("No model matches." if everything else "No provider offers a model."))
            return
        table = make_table(["Offered", "Model", "Provider", "Size", "Credits per token"], 1)
        shown = rows[:SHOWN_MODELS]
        table.setRowCount(len(shown))
        for row, model in enumerate(shown):
            box = QCheckBox()
            box.setChecked(model["enabled"])
            box.toggled.connect(lambda on, m=model: self._change([m["ref"]], enabled=on))
            holder = QWidget()
            QHBoxLayout(holder).addWidget(box, 0, Qt.AlignmentFlag.AlignCenter)
            table.put(row, 0, holder)
            name = QWidget()
            line = QHBoxLayout(name)
            line.setContentsMargins(6, 2, 6, 2)
            line.addWidget(label(model["name"], heading="small"))
            if not model.get("listed"):
                line.addWidget(badge("not offered", "off"))
            if model["ref"] == self.data.get("default"):
                line.addWidget(badge("server default", "ok"))
            line.addStretch(1)
            table.put(row, 1, name)
            table.put(row, 2, label(model["provider_label"]))
            size = model.get("size_b")
            table.put(row, 3, label(f"{size:.2f}".rstrip("0").rstrip(".") + "B" if size else "unknown"))
            weight = QDoubleSpinBox()
            weight.setDecimals(3)
            weight.setRange(0.01, 1000)
            weight.setValue(float(model["weight"]))
            weight.editingFinished.connect(lambda w=weight, m=model: self._change([m["ref"]], weight=w.value()) if abs(w.value() - m["weight"]) > 1e-9 else None)
            cell = QWidget()
            line = QHBoxLayout(cell)
            line.setContentsMargins(6, 2, 6, 2)
            line.addWidget(weight)
            if model.get("auto_weight"):
                weight.setToolTip("From the model's size" if model.get("size_b") else "Its size is unknown")
            else:
                weight.setToolTip("Set by hand")
                line.addWidget(button("Use the size", "ghost", lambda _=False, m=model: self._change([m["ref"]], auto_weight=True)))
            line.addStretch(1)
            table.put(row, 4, cell)
        fit_table(table)
        body.addWidget(table)
        if len(rows) > SHOWN_MODELS:
            body.addWidget(label(f"  {len(rows) - SHOWN_MODELS} more: search to find them.", tone_="muted"))


# ---- the server ------------------------------------------------------------------------------------------------------


class ServerTab(Tab):
    def __init__(self, *args):
        super().__init__(*args)
        self.controls = Panel("Language model", "The server's own model: used by everybody who chose none, and for summaries and titles. "
                                                "Changes apply at once, without a restart, and everybody is told.")
        self.provider = QComboBox()
        self.model = QComboBox()
        self.provider.activated.connect(lambda *_: self._run(f"/provider {self.provider.currentData()}"))
        self.model.activated.connect(lambda *_: self._run(f"/model {self.model.currentData()}"))
        form = QFormLayout()
        form.addRow("Provider", self.provider)
        form.addRow("Model", self.model)
        self.controls.add(form)
        self.stats = Panel("Status", "Refreshed every 5 seconds.")
        self.stats_holder = QVBoxLayout()
        self.stats.add(self.stats_holder)
        self.danger = Panel("Stop the server", "Running answers finish first and every client is told. If a service manager restarts Clara, it comes back.", danger=True)
        self.danger.add(button("Stop…", "danger", self.stop))
        for widget in (self.controls, self.stats, self.danger, self.status):
            self.column.addWidget(widget)
        self.finish()
        self._drawn = ""
        self._timer = QTimer(self)
        self._timer.setInterval(STATUS_MS)
        self._timer.timeout.connect(self.load)

    def activated(self) -> None:
        self._drawn = ""
        self.load()
        self._timer.start()

    def deactivated(self) -> None:
        self._timer.stop()

    def load(self) -> None:
        self._call(lambda api: api.admin_status(), self._loaded)

    def _loaded(self, status: object, error: str) -> None:
        if error or not isinstance(status, dict):
            return
        while self.stats_holder.count():
            item = self.stats_holder.takeAt(0)
            if item.widget() is not None:
                item.widget().deleteLater()
        tailscale = status.get("tailscale", {})
        self.stats_holder.addWidget(key_values([
            ("Running for", "Stopping…" if status.get("stopping") else duration(status["uptime_seconds"])),
            ("Answers", f"{status['turns']['running']} running, {status['turns']['since_start']} since start"),
            ("Tokens read", f"{status['tokens']['prompt']:,}"),
            ("Tokens written", f"{status['tokens']['completion']:,}"),
            ("Memory", f"{status['people']} people, {status['facts']} facts"),
            ("Listening on", str(status["listen"])),
            ("Tailscale", "Off" if tailscale.get("mode") == "off" else tailscale.get("url") or f"Unavailable: {tailscale.get('problem') or 'starting…'}"),
        ]))
        key = status["provider"]["id"] + status["model"]
        if key == self._drawn:
            return
        self._drawn = key
        self._call(lambda api: api.admin_models(), lambda models, error: self._fill(status, models if not error else {"models": [], "model": status["model"]}))

    def _fill(self, status: dict, models: dict) -> None:
        self.provider.blockSignals(True)
        self.provider.clear()
        for p in status.get("providers", []):
            self.provider.addItem(f"{p['label']} ({p['id']})" + ("" if p.get("usable") else ", no key"), p["id"])
        self.provider.setCurrentIndex(max(0, self.provider.findData(status["provider"]["id"])))
        self.provider.blockSignals(False)
        names = models.get("models") or []
        names = names if status["model"] in names else [status["model"], *names]
        self.model.blockSignals(True)
        self.model.clear()
        for name in names:
            self.model.addItem(name, name)
        self.model.setCurrentIndex(max(0, self.model.findData(status["model"])))
        self.model.blockSignals(False)

    def _run(self, line: str) -> None:
        def done(result: object, error: str) -> None:
            first = str((result or {}).get("output", "")).split("\n")[0]  # type: ignore[union-attr]
            self.status.say(error or first, bad=bool(error))
            self._drawn = ""
            self.load()

        self._call(lambda api: api.admin_command(line), done)

    def stop(self) -> None:
        if confirm(self, "Stop the server", "Running answers finish first; new questions are refused. Nobody can use Clara until it is started again.", "Stop the server", True):
            self._run("/stop")


# ---- people and memory ---------------------------------------------------------------------------------------------


class PeopleTab(Tab):
    def __init__(self, *args):
        super().__init__(*args)
        self.people: list[dict] = []
        self.person: dict | None = None
        self.list = QListWidget()
        self.list.setFixedWidth(300)
        self.list.currentItemChanged.connect(self._picked)
        self.detail = QVBoxLayout()
        self.detail.setSpacing(14)
        holder = QWidget()
        holder.setLayout(self.detail)
        row = QHBoxLayout()
        row.setSpacing(18)
        left = Panel("People")
        left.body.setContentsMargins(6, 6, 6, 6)
        left.add(self.list)
        row.addWidget(left, 0, Qt.AlignmentFlag.AlignTop)
        row.addWidget(holder, 1, Qt.AlignmentFlag.AlignTop)
        top = QWidget()
        top.setLayout(row)
        row.setContentsMargins(0, 0, 0, 0)
        self.column.addWidget(top)
        self.column.addWidget(self.status)
        self.finish()
        self._placeholder()

    def _clear_detail(self) -> None:
        while self.detail.count():
            item = self.detail.takeAt(0)
            if item.widget() is not None:
                item.widget().hide()
                item.widget().deleteLater()

    def _placeholder(self) -> None:
        self._clear_detail()
        self.detail.addWidget(EmptyState("Pick someone", "Their facts, accounts and the tools to link or erase them appear here."))

    def activated(self) -> None:
        self._call(lambda api: api.admin_people(), self._loaded)

    def _loaded(self, people: object, error: str) -> None:
        if error:
            self.status.say(error, bad=True)
            return
        self.people = list(people)  # type: ignore[arg-type]
        wanted = (self.person or {}).get("id")
        self.list.blockSignals(True)
        self.list.clear()
        chosen = None
        for person in self.people:
            relation = "no relationship" if person.get("relation") is None else f"relationship {person['relation']}/100"
            item = QListWidgetItem(f"{person['name']}{'  · can sign in' if person.get('user') else ''}\n{person['facts']} facts, {relation}")
            item.setData(256, person["id"])
            self.list.addItem(item)
            if person["id"] == wanted:
                chosen = item
        self.list.blockSignals(False)
        if chosen is not None:
            self.list.setCurrentItem(chosen)
        elif wanted is not None:
            self.person = None
            self._placeholder()

    def _picked(self, item: QListWidgetItem | None, _previous=None) -> None:
        if item is None:
            return
        self.person = next((p for p in self.people if p["id"] == item.data(256)), None)
        if self.person:
            self._call(lambda api: api.admin_person_facts(self.person["id"]), lambda body, e: self._show(body) if not e else self.status.say(e, bad=True))

    def _show(self, body: object) -> None:
        person = self.person
        if person is None or not isinstance(body, dict):
            return
        self._clear_detail()
        facts = Panel(person["name"], "Accounts: " + (", ".join(person.get("accounts", [])) or "none"))
        facts.body.setSpacing(0)
        if not body["facts"]:
            facts.add(label("No facts yet.", tone_="muted"))
        for position, fact in enumerate(body["facts"]):
            if position:
                facts.add(divider())
            line = QWidget()
            row = QHBoxLayout(line)
            row.setContentsMargins(0, 6, 0, 6)
            row.addWidget(label(fact["text"], wrap=True, selectable=True), 1)
            forget = QToolButton()
            bind_icon(forget, "trash", "muted", 18)
            forget.clicked.connect(lambda _=False, f=fact: self._call(lambda api: api.admin_delete_person_fact(person["id"], f["id"]), lambda r, e: (self.say(r, e, "Deleted."), self._refresh())))
            row.addWidget(forget)
            facts.add(line)
        add = QLineEdit()
        add.setPlaceholderText("Add a fact")
        add.setMaxLength(300)
        add.returnPressed.connect(lambda: self._add_fact(add))
        row = QHBoxLayout()
        row.addWidget(add, 1)
        row.addWidget(button("Add fact", "", lambda: self._add_fact(add)))
        facts.add(row)
        self.detail.addWidget(facts)

        relation = Panel("Relationship", "0 to 100. It sets Clara's tone with them on every surface, and she moves it herself when they are friendly or rude.")
        spin = QSpinBox()
        spin.setRange(0, 100)
        spin.setSpecialValueText("none")
        spin.setValue(person["relation"] if person.get("relation") is not None else 0)
        row = QHBoxLayout()
        row.addWidget(spin)
        row.addWidget(button("Save", "", lambda: self._set_relation(spin.value() if spin.value() or person.get("relation") is not None else 0)))
        row.addWidget(button("Reset", "", lambda: self._set_relation(None)))
        row.addStretch(1)
        relation.add(row)
        self.detail.addWidget(relation)

        tools = Panel()
        row = QHBoxLayout()
        row.addWidget(button("Link an account…", "", self.link))
        row.addStretch(1)
        row.addWidget(button("Erase this person…", "danger", self.erase))
        tools.add(row)
        self.detail.addWidget(tools)

    def _refresh(self) -> None:
        self.activated()
        if self.person:
            self._call(lambda api: api.admin_person_facts(self.person["id"]), lambda body, e: self._show(body) if not e else None)

    def _add_fact(self, field: QLineEdit) -> None:
        text, person = field.text().strip(), self.person
        if text and person:
            self._call(lambda api: api.admin_add_person_fact(person["id"], text), lambda r, e: (self.say(r, e, "Added."), self._refresh()))

    def _set_relation(self, value: int | None) -> None:
        person = self.person
        if person:
            self._call(lambda api: api.admin_set_relation(person["id"], value), lambda r, e: (self.say(r, e, f"Relationship: {(r or {}).get('relation')}/100." if value is not None else "Relationship reset."), self._refresh()))  # type: ignore[union-attr]

    def link(self) -> None:
        person = self.person
        value = ask_text(self, f"Link an account to {person['name']}", "Account (surface:user, e.g. discord:1234)", "", "Link",
                         hint="If that account already has its own memories they are merged into this person, which cannot be undone.") if person else None
        if value:
            self._call(lambda api: api.admin_command(f"/link {value.strip()} {person['id']}"), lambda r, e: (self.say(None, e, str((r or {}).get("output", "")).split("\n")[0]), self._refresh()))  # type: ignore[union-attr]

    def erase(self) -> None:
        person = self.person
        if person is None:
            return

        def footprint(found: object, error: str) -> None:
            if error:
                self.status.say(error, bad=True)
                return
            text = f"{found['accounts']} account(s), {found['facts']} fact(s), {found['messages']} message(s) in {found['conversations']} conversation(s)"  # type: ignore[index]
            if confirm(self, f"Erase {person['name']}?", f"This erases {text}, and their login if any. There is no undo. (The traffic log is not erased.)", "Erase for good", True):
                def erased(r: object, e: str) -> None:
                    self.say(None, e, str((r or {}).get("output", "")).split("\n")[0])  # type: ignore[union-attr]
                    self.person = None
                    self._placeholder()
                    self.activated()

                self._call(lambda api: api.admin_command(f"/forget-person {person['id']} confirm"), erased)

        self._call(lambda api: api.admin_footprint(person["id"]), footprint)


# ---- the console -----------------------------------------------------------------------------------------------------


class ConsoleTab(Tab):
    def __init__(self, *args):
        super().__init__(*args)
        self.out = QPlainTextEdit()
        self.out.setReadOnly(True)
        self.out.setMinimumHeight(380)
        mono = QFont("Consolas")
        mono.setPixelSize(13)
        self.out.setFont(mono)
        self.input = QLineEdit()
        self.input.setPlaceholderText("/help")
        self.input.setFont(mono)
        self.input.returnPressed.connect(self.run)
        self.input.installEventFilter(self)
        self.history: list[str] = []
        self.at = 0
        row = QHBoxLayout()
        row.addWidget(label(">", heading="small"))
        row.addWidget(self.input, 1)
        row.addWidget(button("Run", "primary", self.run))
        panel = Panel()
        panel.add(self.out)
        panel.add(row)
        self.column.addWidget(label("The same commands as the server's own console and clara-admin. Passwords made by /user are shown here only once.", tone_="muted", wrap=True))
        self.column.addWidget(panel)
        self.finish()
        self._print("Type /help to see the commands.")

    def eventFilter(self, watched, event) -> bool:
        from PySide6.QtCore import QEvent

        if watched is getattr(self, "input", None) and event.type() == QEvent.Type.KeyPress and getattr(self, "history", None):
            if event.key() == Qt.Key.Key_Up:
                self.at = max(0, self.at - 1)
                self.input.setText(self.history[self.at])
                return True
            if event.key() == Qt.Key.Key_Down:
                self.at = min(len(self.history), self.at + 1)
                self.input.setText(self.history[self.at] if self.at < len(self.history) else "")
                return True
        return super().eventFilter(watched, event)

    def activated(self) -> None:
        self.input.setFocus()

    def _print(self, text: str) -> None:
        self.out.appendPlainText(text)

    def run(self) -> None:
        line = self.input.text().strip()
        if not line:
            return
        self.history.append(line)
        self.at = len(self.history)
        self.input.clear()
        self._print("> " + line)

        def done(result: object, error: str) -> None:
            if error:
                self._print(error)
            elif isinstance(result, dict):
                if result.get("output"):
                    self._print(result["output"])
                if result.get("quit"):
                    self._print("(this console closes only the page; use /stop to stop the server)")

        self._call(lambda api: api.admin_command(line), done)


# ---- the page ----------------------------------------------------------------------------------------------------------


class AdminPage(Page, Calls):
    page_title = "Administration"

    def __init__(self, get_config: Callable[[], Config], api_factory: Callable[[Config], ClaraApi], host=None):
        super().__init__()
        self._init_calls(get_config, api_factory)
        classes = {"users": UsersTab, "models": ModelsTab, "server": ServerTab, "people": PeopleTab, "console": ConsoleTab}
        self.tabs: dict[str, Tab] = {key: cls(get_config, api_factory, host) for key, cls in classes.items()}
        self.stack = QStackedWidget()
        for tab in self.tabs.values():
            self.stack.addWidget(tab)
        self.segmented = Segmented(list(TABS), "users")
        self.segmented.chosen.connect(self.show_tab)
        self.current = "users"
        top = QHBoxLayout()
        top.setContentsMargins(24, 16, 24, 0)
        top.addWidget(self.segmented)
        top.addStretch(1)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addLayout(top)
        layout.addWidget(self.stack, 1)

    def activated(self) -> None:
        self.show_tab(self.current)

    def show_tab(self, key: str) -> None:
        self.tabs[self.current].deactivated()
        self.current = key
        self.segmented.select(key)
        self.stack.setCurrentWidget(self.tabs[key])
        self.tabs[key].activated()

    def hideEvent(self, event) -> None:
        self.tabs[self.current].deactivated()
        super().hideEvent(event)

    def shutdown(self) -> None:
        for tab in self.tabs.values():
            tab.deactivated()
            tab.stop_calls()
