"""The Discord page (administrators): the bot built into the server, the Discord servers it is in (and where Clara may
chime in), and the Discord accounts signed in as Clara users."""

from __future__ import annotations

import json
from typing import Callable

from PySide6.QtCore import QTimer, QUrl
from PySide6.QtGui import QDesktopServices, QGuiApplication
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QHBoxLayout,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QVBoxLayout,
    QWidget,
)

from .api import ClaraApi
from .config import Config
from .widgets import (
    Calls,
    Column,
    EmptyState,
    Notice,
    Page,
    Panel,
    StatusLine,
    badge,
    button,
    confirm,
    duration,
    fit_table,
    key_values,
    label,
    make_table,
)

STATES = {
    "running": ("Running", "ok"),
    "starting": ("Connecting…", ""),
    "stopped": ("Stopped", ""),
    "error": ("Stopped after an error", "off"),
    "no-token": ("No token", "off"),
    "unavailable": ("Not installed", "off"),
}
REFRESH_MS = 5000


class SignInDialog(QDialog, Calls):
    """Sign a Discord account in as a Clara user, with no password."""

    def __init__(self, get_config, api_factory, users: list[dict], parent=None, user: str | None = None):
        super().__init__(parent)
        self._init_calls(get_config, api_factory)
        self.setWindowTitle("Sign in a Discord account")
        self.setMinimumWidth(460)
        self.chosen: dict | None = None
        self.query = QLineEdit()
        self.query.setClearButtonEnabled(True)
        self.query.setPlaceholderText("Name, or Discord user id")
        self.results = QListWidget()
        self.results.setFixedHeight(130)
        self.results.itemClicked.connect(self._pick)
        self.note = label("Search the bot's servers by name, or paste a Discord user id.", tone_="muted", wrap=True)
        self.user = QComboBox()
        for entry in users:
            self.user.addItem(f"{entry['name']} ({entry['person']['name']})" if entry.get("person") else entry["name"], entry["name"])
        if user:
            self.user.setCurrentIndex(max(0, self.user.findData(user)))
            self.user.setEnabled(False)
        self.warning = Notice("", bad=False)
        self.warning.hide()
        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.setInterval(250)
        self.timer.timeout.connect(self._search)
        self.query.textChanged.connect(lambda *_: self.timer.start())
        self.sign_in = button("Sign in", "primary", self.accept)
        self.sign_in.setEnabled(False)
        row = QHBoxLayout()
        row.addStretch(1)
        row.addWidget(button("Cancel", "", self.reject))
        row.addWidget(self.sign_in)
        layout = QVBoxLayout(self)
        layout.addWidget(label("Discord account", heading="small"))
        layout.addWidget(self.query)
        layout.addWidget(self.note)
        layout.addWidget(self.results)
        layout.addWidget(label("Clara user", heading="small"))
        layout.addWidget(self.user)
        layout.addWidget(self.warning)
        layout.addWidget(label("No password is asked: Clara answers them on Discord right away. What she already knows from this "
                               "Discord account joins the user's memories.", tone_="muted", wrap=True))
        layout.addLayout(row)
        self.user.currentIndexChanged.connect(lambda *_: self._explain())
        self._search()

    def _search(self) -> None:
        text = self.query.text().strip()
        digits = text.removeprefix("discord:").strip("<@!>")
        self.chosen = None
        self.sign_in.setEnabled(False)
        if digits.isdigit() and 1 <= len(digits) <= 20:  # a pasted id is taken as it is
            self.chosen = {"user_id": digits, "display_name": f"Discord id {digits}"}
            self.sign_in.setEnabled(True)
            self.note.setText(f"Discord id {digits}")
            self.results.clear()
            return
        if not text:
            self.results.clear()
            return

        def found(result: object, error: str) -> None:
            if error or not isinstance(result, dict):
                self.note.setText(error)
                return
            self.results.clear()
            for member in result.get("members", []):
                item = QListWidgetItem(f"{member.get('display_name') or member['user_id']}  @{member.get('name', '')}"
                                       + (f"   (signed in as {member['user']})" if member.get("user") else ""))
                item.setData(256, member)
                self.results.addItem(item)
            if not result.get("running"):
                self.note.setText("The bot is not running: paste their Discord user id.")
            elif not result.get("members"):
                self.note.setText("Nobody by that name in the bot's servers. A Discord user id works too.")
            else:
                self.note.setText("Pick one:")

        self._call(lambda api: api.discord_members(text), found)

    def _pick(self, item: QListWidgetItem) -> None:
        self.chosen = item.data(256)
        self.sign_in.setEnabled(True)
        self.note.setText(f"{self.chosen.get('display_name')}, id {self.chosen['user_id']}")
        self._explain()

    def _explain(self) -> None:
        now = (self.chosen or {}).get("user")
        target = self.user.currentData()
        self.warning.setVisible(bool(now and now != target))
        if now and now != target:
            self.warning.say(f"This account is signed in as {now} now: it will be {target}'s instead ({now} keeps their memories).")

    def done(self, result: int) -> None:
        self.stop_calls()
        super().done(result)


class DiscordPage(Page, Calls):
    page_title = "Discord"

    def __init__(self, get_config: Callable[[], Config], api_factory: Callable[[Config], ClaraApi], host=None):
        super().__init__()
        self._init_calls(get_config, api_factory)
        self._host = host
        self._busy = False
        self._drawn = ""
        self.found: dict = {}
        self.column = Column(wide=True)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.column)
        self.status = StatusLine()
        self._timer = QTimer(self)
        self._timer.setInterval(REFRESH_MS)
        self._timer.timeout.connect(lambda: None if self._busy else self.reload())

    def activated(self) -> None:
        self._drawn = ""
        self.reload()
        self._timer.start()

    def hideEvent(self, event) -> None:
        self._timer.stop()
        super().hideEvent(event)

    def shutdown(self) -> None:
        self._timer.stop()
        self.stop_calls()

    def reload(self) -> None:
        if self._get_config().ready:
            self._call(lambda api: api.discord(), self._loaded)

    def _loaded(self, found: object, error: str) -> None:
        if error or not isinstance(found, dict):
            self.status.say(error, bad=True)
            if not self.found:
                self._draw_error(error)
            return
        self.found = found
        self.status.say("")
        key = json.dumps(found, sort_keys=True, default=str)
        if key != self._drawn:  # redrawn only when something changed: a menu being used is not reset
            self._drawn = key
            self._draw()

    def _draw_error(self, error: str) -> None:
        self.column.clear(keep=(self.status,))
        self.column.add(Notice(error or "The Discord page needs an administrator's sign-in.", bad=True))
        self.column.finish()

    def _draw(self) -> None:
        self.column.clear(keep=(self.status,))
        self.column.add(label("Clara's Discord bot runs inside this server. People talk to her once they have an account "
                              "(/register or /login on Discord), and get their reminders as private messages.", tone_="muted", wrap=True))
        self.column.add(self._bot_panel(self.found["bot"]))
        self.column.add(self._servers_panel(self.found))
        self.column.add(self._accounts_panel(self.found.get("accounts", [])))
        self.column.add(self.status)
        self.column.finish()

    # ---- the bot ----

    def _act(self, action: str) -> None:
        if action == "stop" and not confirm(
            self, "Stop the bot",
            "Clara stops answering on Discord until the bot is started again (or the server restarts with AUTO_START_DISCORD_BOT on).",
            "Stop the bot", True,
        ):
            return
        self._busy = True

        def done(result: object, error: str) -> None:
            self._busy = False
            text = error or str((result or {}).get("output", "")).removeprefix("Discord bot: ")  # type: ignore[union-attr]
            self.status.say(text, bad=bool(error))
            self._drawn = ""
            self.reload()

        self._call(lambda api: api.discord_act(action), done)

    def _bot_panel(self, bot: dict) -> Panel:
        state, kind = STATES.get(bot["state"], (bot["state"], ""))
        panel = Panel()
        head = QHBoxLayout()
        title = QVBoxLayout()
        top = QHBoxLayout()
        top.addWidget(label("Bot", heading="section"))
        top.addWidget(badge(state, kind))
        top.addStretch(1)
        title.addLayout(top)
        title.addWidget(label("Starts with the server (AUTO_START_DISCORD_BOT=true)." if bot.get("auto_start")
                              else "Does not start with the server (AUTO_START_DISCORD_BOT is off): start it here or with /discord start.",
                              tone_="muted", wrap=True))
        head.addLayout(title, 1)
        live = bot["state"] in ("running", "starting")
        usable = bot.get("available") and bot.get("token_set")
        if not live:
            start = button("Start", "primary", lambda: self._act("start"))
            start.setEnabled(bool(usable) and not self._busy)
            head.addWidget(start)
        else:
            head.addWidget(button("Restart", "", lambda: self._act("restart")))
            head.addWidget(button("Stop", "danger", lambda: self._act("stop")))
        panel.add(head)
        if not bot.get("available"):
            panel.add(Notice("discord.py is not installed on this server: pip install clara-server[discord], then restart it.", bad=True))
        elif not bot.get("token_set"):
            panel.add(Notice("Put the bot's token in the server's .env (DISCORD_BOT_TOKEN), then restart the server. It is never shown here.", bad=True))
        if bot.get("last_error"):
            panel.add(Notice(bot["last_error"], bad=True))
        panel.add(key_values([
            ("Discord account", bot.get("user") or "–"),
            ("Servers", str(bot.get("guilds")) if bot["state"] == "running" else "–"),
            ("Latency", f"{bot['latency_ms']} ms" if bot.get("latency_ms") is not None else "–"),
            ("Running for", duration(bot["uptime_seconds"]) if bot.get("uptime_seconds") is not None else "–"),
        ], 4))
        if bot.get("invite_url"):
            row = QHBoxLayout()
            row.addWidget(button("Invite to a server", "", lambda: QDesktopServices.openUrl(QUrl(bot["invite_url"]))))
            row.addWidget(button("Copy the link", "ghost", lambda: QGuiApplication.clipboard().setText(bot["invite_url"])))
            row.addStretch(1)
            panel.add(row)
        return panel

    # ---- the servers ----

    def _servers_panel(self, found: dict) -> Panel:
        panel = Panel("Servers", "Clara always answers when she is mentioned or replied to. With chime in she may also answer a message "
                                 "that was not for her, when she has something to add (a model call for every message).")
        default = QCheckBox("Chime in by default (servers with no choice of their own)")
        default.setChecked(bool(found.get("default_chime")))
        default.toggled.connect(lambda on: self._save(lambda api: api.discord_default_chime(on)))
        panel.add(default)
        spaces = found.get("spaces", [])
        if not spaces:
            panel.add(EmptyState("No server yet", "Invite the bot to a server: it appears here once the bot is running."))
            return panel
        table = make_table(["Server", "Chime in", ""], 0)
        table.setRowCount(len(spaces))
        for row, space in enumerate(spaces):
            table.put(row, 0, self._two_lines(space.get("name") or space["id"], space["id"]))
            choice = QComboBox()
            for value, text in (("default", f"Default ({'on' if found.get('default_chime') else 'off'})"), ("on", "On"), ("off", "Off")):
                choice.addItem(text, value)
            current = space.get("chime")
            choice.setCurrentIndex(choice.findData("default" if current is None else "on" if current else "off"))
            choice.activated.connect(lambda _i, c=choice, s=space: self._save(
                lambda api: api.discord_space_chime(s["id"], {"default": None, "on": True, "off": False}[c.currentData()])))
            table.put(row, 1, choice)
            table.put(row, 2, badge("Bot present", "ok") if space.get("present") else badge("Bot gone", "off"))
        fit_table(table)
        panel.add(table)
        return panel

    @staticmethod
    def _two_lines(first: str, second: str) -> QWidget:
        holder = QWidget()
        column = QVBoxLayout(holder)
        column.setContentsMargins(8, 2, 8, 2)
        column.setSpacing(0)
        column.addWidget(label(first, heading="small"))
        column.addWidget(label(second, tone_="muted"))
        return holder

    def _save(self, call: Callable[[ClaraApi], object]) -> None:
        def saved(_result: object, error: str) -> None:
            self.status.say(error or "Saved.", bad=bool(error))
            self._drawn = ""
            self.reload()

        self._call(call, saved)

    # ---- the accounts ----

    def _accounts_panel(self, accounts: list[dict]) -> Panel:
        panel = Panel("Signed-in accounts", "Discord accounts that may talk to Clara, and the Clara user each one is signed in as.")
        panel.add(button("Sign in an account…", "", self.sign_in_account))
        if not accounts:
            panel.add(EmptyState("Nobody yet", "People sign in on Discord with /register or /login, or you sign them in here."))
            return panel
        table = make_table(["Discord account", "Clara user", "Person", ""], 0)
        table.setRowCount(len(accounts))
        for row, account in enumerate(accounts):
            table.put(row, 0, self._two_lines(account.get("discord_name") or "Unknown name", str(account["user_id"])))
            table.put(row, 1, label(account.get("user", ""), wrap=False))
            table.put(row, 2, label(account.get("person") or "–"))
            table.put(row, 3, button("Sign out", "danger", lambda _=False, a=account: self._sign_out(a)))
        fit_table(table)
        panel.add(table)
        return panel

    def _sign_out(self, account: dict) -> None:
        name = account.get("discord_name") or account["user_id"]
        if confirm(self, "Sign out", f"Sign {name} out of Clara on Discord? They can sign in again with /login.", "Sign out", True):
            self._save(lambda api: api.discord_sign_out(str(account["user_id"])))

    def sign_in_account(self) -> None:
        def users_listed(users: object, error: str) -> None:
            if error:
                self.status.say(error, bad=True)
                return
            active = [u for u in users if not u.get("disabled")]  # type: ignore[union-attr]
            if not active:
                self.status.say("Add a user first (Admin, Users).", bad=True)
                return
            dialog = SignInDialog(self._get_config, self._api_factory, active, self)
            if dialog.exec() == QDialog.DialogCode.Accepted and dialog.chosen:
                member, user = dialog.chosen, dialog.user.currentData()
                self._save(lambda api: api.discord_sign_in(str(member["user_id"]), user))

        self._call(lambda api: api.admin_users(), users_listed)
