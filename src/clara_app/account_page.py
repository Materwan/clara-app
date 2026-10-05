"""The Account page: who you are, how the app looks, what you used today, the model Clara answers you with, when a
finished task notifies you, your password, the devices you are signed in on, and the connection to the server."""

from __future__ import annotations

from typing import Callable

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QLineEdit,
    QProgressBar,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from .api import ApiError, ClaraApi
from .config import Config
from .theme import THEME, tone
from .widgets import (
    Calls,
    Column,
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
    date_time,
    divider,
    label,
    token_count,
)

MAX_NOTIFY_AFTER = 7 * 86400  # seconds: what the server accepts
DEFAULT_NOTIFY_AFTER = 120  # shown when the user picks a delay of their own


def cost(model: dict) -> str:
    """What a token of a model costs, in words: "0.4 credits per token"."""
    weight = f"{model['weight']:.3f}".rstrip("0").rstrip(".")
    return f"{weight} {'credit' if model['weight'] == 1 else 'credits'} per token"


def device_name(device: str | None, surface: str) -> str:
    text = (device or "").strip()
    return text[:60] if text else {"web": "Web browser", "app": "Desktop app", "cli": "Terminal", "console": "Console"}.get(surface, surface)


def load_everything(api: ClaraApi) -> dict:
    """What the page shows, read from the server; what the token cannot read is None (a shared client token is not a user)."""
    data: dict = {}
    for key, call in (("me", api.me), ("sessions", api.sessions), ("settings", api.settings), ("models", api.models)):
        try:
            data[key] = call()
        except ApiError as error:
            data[key] = None
            data[f"{key}_error"] = str(error)
    return data


class AccountPage(Page, Calls):
    page_title = "Account"

    def __init__(self, get_config: Callable[[], Config], api_factory: Callable[[Config], ClaraApi], host=None):
        super().__init__()
        self._init_calls(get_config, api_factory)
        self._host = host
        self.data: dict = {}
        self.column = Column()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.column)
        self.status = StatusLine()
        self.model_box: QComboBox | None = None
        self.notify_mode: QComboBox | None = None
        self.notify_seconds: QSpinBox | None = None
        self.notify_save = None
        self._notify_original: int | None = None
        self._model_original: str | None = None

    def activated(self) -> None:
        if self._get_config().ready:
            self._call(load_everything, self._loaded)
        else:
            self._draw()

    def shutdown(self) -> None:
        self.stop_calls()

    def _loaded(self, data: object, error: str) -> None:
        self.data = data if isinstance(data, dict) and not error else {}
        if error:
            self.status.say(error, bad=True)
        self._draw()
        me = self.data.get("me")
        if self._host is not None and isinstance(me, dict):
            self._host.shell.set_admin(bool(me.get("is_admin")))

    # -- drawing -------------------------------------------------------------------------------------- #

    def _draw(self) -> None:
        self.column.clear(keep=(self.status,))
        config = self._get_config()
        me = self.data.get("me")
        self.column.add(self._profile(me, config))
        if me is None and config.ready and self.data:
            self.column.add(Notice("You are signed in with a shared client token, not as a user: usage, password and devices are "
                                   "managed for users. Sign in with your user name and password under Connection to see them."))
        self.column.add(self._appearance())
        if isinstance(me, dict) and me.get("usage"):
            self.column.add(self._usage(me))
        if self.data.get("models"):
            self.column.add(self._model(self.data["models"]))
        if self.data.get("settings"):
            self.column.add(self._notifications(self.data["settings"]))
        if isinstance(me, dict):
            self.column.add(self._password())
        if self.data.get("sessions"):
            self.column.add(self._devices(self.data["sessions"]))
        self.column.add(self._connection(config))
        self.column.add(self.status)
        self.column.finish()

    def _profile(self, me: dict | None, config: Config) -> Panel:
        panel = Panel()
        row = QHBoxLayout()
        row.setSpacing(16)
        name = (me or {}).get("person", {}).get("name") if me else None
        name = name or (me or {}).get("name") or config.user_name or config.user_id or "You"
        row.addWidget(avatar(name, 56), 0, Qt.AlignmentFlag.AlignTop)
        who = QVBoxLayout()
        who.setSpacing(4)
        head = QHBoxLayout()
        head.addWidget(label(name, heading="section"))
        if me and me.get("is_admin"):
            head.addWidget(badge("Administrator", "admin"))
        head.addStretch(1)
        who.addLayout(head)
        signed = (me or {}).get("name") or config.user_id
        who.addWidget(label(f"Signed in as {signed}." + (" Clara knows you on these accounts:" if me and me.get("accounts") else ""), tone_="muted", wrap=True))
        if me and me.get("accounts"):
            accounts = QHBoxLayout()
            for account in me["accounts"]:
                accounts.addWidget(badge(account))
            accounts.addStretch(1)
            who.addLayout(accounts)
        row.addLayout(who, 1)
        row.addWidget(button("Sign out", "", self.sign_out), 0, Qt.AlignmentFlag.AlignTop)
        panel.add(row)
        return panel

    def sign_out(self) -> None:
        if not confirm(self, "Sign out", "Forget the sign-in on this computer? You will be asked for your password again.", "Sign out"):
            return
        if self._host is not None:
            self._host.sign_out()

    def _appearance(self) -> Panel:
        panel = Panel("Appearance", "Light, dark, or the way Windows is set. Remembered by the app.")
        choice = Segmented([("auto", "Auto"), ("light", "Light"), ("dark", "Dark")], THEME.preference)
        choice.chosen.connect(self._theme)
        holder = QHBoxLayout()
        holder.addWidget(choice)
        holder.addStretch(1)
        panel.add(holder)
        return panel

    def _theme(self, preference: str) -> None:
        if self._host is not None:
            self._host.set_theme(preference)

    def _usage(self, me: dict) -> Panel:
        usage = me["usage"]
        panel = Panel("Usage today")
        limit = usage.get("limit")
        used = usage.get("used", 0)
        if limit:
            panel.add(label(f"{token_count(used)} / {token_count(limit)} credits", heading="small"))
            bar = QProgressBar()
            bar.setRange(0, 1000)
            bar.setValue(min(1000, int(used * 1000 / limit)))
            bar.setTextVisible(False)
            bar.setFixedHeight(8)
            if used >= limit:
                bar.setProperty("over", True)
            panel.add(bar)
            left = usage.get("remaining")
            note = f"{token_count(left)} credits left today. " if left is not None else ""
            panel.add(label(f"{note}The day starts again at {date_time(usage.get('resets_at'))}.", tone_="muted", wrap=True))
        else:
            panel.add(label(f"{token_count(used)} credits used today.", heading="small"))
            panel.add(label("As an administrator you have no limit." if me.get("is_admin") else "You have no daily limit.", tone_="muted"))
        return panel

    def _model(self, info: dict) -> Panel:
        panel = Panel("Model", "Every token Clara reads or writes costs credits: bigger models cost more per token, so a small one lets you talk longer within your daily limit.")
        offered = info.get("models") or []
        default = info.get("default") or {}
        if not offered:
            name = default.get("name", "the server's model")
            panel.add(label(f"Clara answers you with {name}" + (f" ({cost(default)})" if default else "") + ". An administrator has not offered other models yet.", wrap=True))
            return panel
        box = self.model_box = QComboBox()
        box.addItem(f"Like the server: {default.get('name', '')} ({cost(default)})" if default else "Like the server", None)
        for model in offered:
            box.addItem(f"{model['name']} ({model['provider_label']}), {cost(model)}", model["ref"])
        own = (info.get("choices") or {}).get("app")
        self._model_original = own if any(m["ref"] == own for m in offered) else None
        box.setCurrentIndex(max(0, box.findData(self._model_original)))
        box.activated.connect(lambda index, b=box: self._choose_model(b.itemData(index)))
        panel.add(label("The model Clara answers you with in this app (the web site and the terminal have their own).", tone_="muted", wrap=True))
        panel.add(box)
        return panel

    def _choose_model(self, ref: str | None) -> None:
        def chosen(_result: object, error: str) -> None:
            self.status.say(error or "Saved.", bad=bool(error))
            if not error and self._host is not None:
                self._host.refresh_models()

        self._call(lambda api: api.choose_model(ref), chosen)

    def _notifications(self, settings: dict) -> Panel:
        panel = Panel("Notifications", "A task that takes at least this long (an answer, a long job in the console) notifies you on your devices when it is done.")
        own = settings.get("notify_after")
        self._notify_original = own
        mode = self.notify_mode = QComboBox()
        mode.addItem("Like the server", "default")
        mode.addItem("Never", "never")
        mode.addItem("After a delay of my own", "after")
        mode.setCurrentIndex(mode.findData("default" if own is None else "never" if own == 0 else "after"))
        seconds = self.notify_seconds = QSpinBox()
        seconds.setRange(1, MAX_NOTIFY_AFTER)
        seconds.setSuffix(" s")
        seconds.setValue(own or settings.get("notify_after_default") or DEFAULT_NOTIFY_AFTER)
        seconds.setEnabled(mode.currentData() == "after")
        mode.currentIndexChanged.connect(lambda *_: seconds.setEnabled(mode.currentData() == "after"))

        def save() -> None:
            choice = mode.currentData()
            value = None if choice == "default" else 0 if choice == "never" else seconds.value()

            def saved(_result: object, error: str) -> None:
                self.status.say(error or "Saved.", bad=bool(error))
                if not error:
                    self._notify_original = value

            self._call(lambda api: api.set_notify_after(value), saved)

        row = QHBoxLayout()
        row.addWidget(mode, 1)
        row.addWidget(seconds)
        self.notify_save = button("Save", "primary", save)
        row.addWidget(self.notify_save)
        panel.add(row)
        return panel

    def _password(self) -> Panel:
        panel = Panel("Password", "Changing it signs you out on your other devices.")
        fields = []
        for placeholder in ("Current password", "New password (10 characters or more)", "Repeat the new password"):
            field = QLineEdit()
            field.setEchoMode(QLineEdit.EchoMode.Password)
            field.setPlaceholderText(placeholder)
            fields.append(field)
            panel.add(field)

        def change() -> None:
            current, new, again = (field.text() for field in fields)
            if new != again:
                self.status.say("The two new passwords are not the same.", bad=True)
                return

            def changed(_result: object, error: str) -> None:
                self.status.say(error or "Password changed. Your other devices are signed out.", bad=bool(error))
                if not error:
                    for field in fields:
                        field.clear()

            self._call(lambda api: api.change_password(current, new), changed)

        panel.add(button("Change password", "primary", change))
        return panel

    def _devices(self, sessions: list[dict]) -> Panel:
        panel = Panel("Your devices", "Where you are signed in. A device unused for 90 days is signed out.")
        panel.body.setSpacing(0)
        others = [s for s in sessions if not s.get("current")]
        for position, session in enumerate(sessions):
            if position:
                panel.add(divider())
            row = QWidget()
            line = QHBoxLayout(row)
            line.setContentsMargins(0, 8, 0, 8)
            text = QVBoxLayout()
            text.setSpacing(0)
            head = QHBoxLayout()
            head.addWidget(label(device_name(session.get("device"), session["surface"]), heading="small"))
            if session.get("current"):
                head.addWidget(badge("This device", "ok"))
            head.addStretch(1)
            text.addLayout(head)
            where = f", from {session['address']}" if session.get("address") else ""
            text.addWidget(label(f"{session['surface']}{where}. Signed in {date_time(session.get('created_at'))}, last used {ago(session.get('last_used_at'))}.", tone_="muted", wrap=True))
            line.addLayout(text, 1)
            if not session.get("current"):
                line.addWidget(button("Sign out", "danger", lambda _=False, s=session: self._sign_out_device(s)))
            panel.add(row)
        if others:
            panel.add(button("Sign out the other devices", "danger", self._sign_out_others))
        return panel

    def _sign_out_device(self, session: dict) -> None:
        self._call(lambda api: api.delete_session(session["id"]), lambda _r, error: self._after_device(error))

    def _sign_out_others(self) -> None:
        others = [s for s in self.data.get("sessions") or [] if not s.get("current")]
        if not others or not confirm(self, "Sign out", f"Sign out {len(others)} other device(s)? This one stays signed in.", "Sign out", True):
            return

        def run(api: ClaraApi) -> None:
            for session in others:
                api.delete_session(session["id"])

        self._call(run, lambda _r, error: self._after_device(error))

    def _after_device(self, error: str) -> None:
        self.status.say(error or "Signed out.", bad=bool(error))
        self.activated()

    def _connection(self, config: Config) -> Panel:
        panel = Panel("Connection", "The Clara server this app talks to, and who you are on it.")
        panel.add(label(f"{config.url}  ·  {config.user_id or 'no user yet'}", selectable=True))
        panel.add(button("Change the connection…", "", lambda: self._host and self._host.settings_requested.emit()))
        return panel
