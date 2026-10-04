"""The connection settings: server, token, who you are."""

from __future__ import annotations

from dataclasses import replace

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
)

from .api import ClaraApi
from .config import Config, url_hint
from .workers import CallWorker, LoginWorker, ProbeWorker

MAX_NOTIFY_AFTER = 7 * 86400  # seconds: what the server accepts
DEFAULT_NOTIFY_AFTER = 120  # shown when the user picks a delay of their own


class SettingsDialog(QDialog):
    def __init__(self, config: Config, parent=None, first_run: bool = False):
        super().__init__(parent)
        self.setWindowTitle("Clara · Settings")
        self.setMinimumWidth(420)
        self._config = config
        self._probe: ProbeWorker | None = None
        self._login: LoginWorker | None = None
        self._calls: list[CallWorker] = []  # the settings being read or saved on the server
        self._then_accept = False
        self._notify_loaded = False  # the user's setting was read from the server (and may be saved)
        self._notify_original: int | None = None  # what the server had: None (its default), 0 (never) or seconds

        self.url = QLineEdit(config.url)
        self.url.setPlaceholderText("http://127.0.0.1:8765, or https://<machine>.<tailnet>.ts.net")
        self.user_id = QLineEdit(config.user_id)
        self.user_id.setPlaceholderText("your user name on the server")
        self.password = QLineEdit()
        self.password.setEchoMode(QLineEdit.EchoMode.Password)
        self.password.setPlaceholderText("only to sign in: not kept" if config.token else "your password")
        self.token = QLineEdit(config.token)
        self.token.setEchoMode(QLineEdit.EchoMode.Password)
        self.token.setPlaceholderText("filled in by signing in (or a client token of CLARA_TOKENS)")
        self.user_name = QLineEdit(config.user_name)
        self.user_name.setPlaceholderText("how Clara should call you (optional)")

        # How long a task takes before the user is notified when it is done: kept by the server, for every client
        self.notify_mode = QComboBox()
        self.notify_mode.addItem("Like the server", "default")
        self.notify_mode.addItem("Never", "never")
        self.notify_mode.addItem("After a delay of my own", "after")
        self.notify_seconds = QSpinBox()
        self.notify_seconds.setRange(1, MAX_NOTIFY_AFTER)
        self.notify_seconds.setSuffix(" s")
        self.notify_seconds.setValue(DEFAULT_NOTIFY_AFTER)
        self.notify_mode.currentIndexChanged.connect(self._update_notify)
        notify_row = QHBoxLayout()
        notify_row.addWidget(self.notify_mode, 1)
        notify_row.addWidget(self.notify_seconds)
        self._set_notify_enabled(False)  # until the server has said what it is

        form = QFormLayout()
        form.addRow("Server", self.url)
        form.addRow("User name", self.user_id)
        form.addRow("Password", self.password)
        form.addRow("Sign-in token", self.token)
        form.addRow("Your name", self.user_name)
        form.addRow("Notify when a task is done", notify_row)

        self.url_note = QLabel("")
        self.url_note.setWordWrap(True)
        self.url_note.setStyleSheet("color: #9a6700;")

        self.test = QPushButton("Test connection")
        self.test.clicked.connect(self._run_probe)
        self.verdict = QLabel("")
        self.verdict.setWordWrap(True)
        self.verdict.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        test_row = QHBoxLayout()
        test_row.addWidget(self.test)
        test_row.addWidget(self.verdict, 1)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        if first_run:
            intro = QLabel("Welcome! Tell the app which Clara server to talk to.")
            intro.setWordWrap(True)
            layout.addWidget(intro)
        layout.addLayout(form)
        layout.addWidget(self.url_note)
        layout.addLayout(test_row)
        layout.addWidget(buttons)
        self.save_button = buttons.button(QDialogButtonBox.StandardButton.Save)
        for field in (self.url, self.token, self.user_id, self.password):
            field.textChanged.connect(self._update_save)
        self._update_save()
        self._load_notify()

    def config(self) -> Config:
        """The settings as typed."""
        return replace(
            self._config,
            url=self.url.text().strip(),
            token=self.token.text().strip(),
            user_id=self.user_id.text().strip(),
            user_name=self.user_name.text().strip(),
        )

    def accept(self) -> None:
        """Save. A password typed is first exchanged for a token (and then forgotten)."""
        if self.password.text():
            self._then_accept = True
            self._sign_in()
        else:
            self._save_notify()

    # -- when a finished task notifies the user ------------------------------------------------------ #

    def _notify_value(self) -> int | None:
        """The setting as chosen: None (the server's delay), 0 (never) or seconds."""
        mode = self.notify_mode.currentData()
        return None if mode == "default" else 0 if mode == "never" else self.notify_seconds.value()

    def _set_notify_enabled(self, enabled: bool) -> None:
        self.notify_mode.setEnabled(enabled)
        self.notify_seconds.setEnabled(enabled and self.notify_mode.currentData() == "after")

    def _update_notify(self) -> None:
        self._set_notify_enabled(self._notify_loaded)

    def _run_call(self, call, then) -> None:
        """Run a call to the server off the UI thread; `then(result, error)` runs on it."""
        worker = CallWorker(call, then, self)
        worker.done.connect(self._call_done)  # a bound method: Qt runs it on the UI thread
        self._calls.append(worker)
        worker.start()

    def _call_done(self, result: object, error: str) -> None:
        worker = self.sender()
        if isinstance(worker, CallWorker) and worker.then is not None:
            then, worker.then = worker.then, None
            then(result, error)

    def _load_notify(self) -> None:
        """Read the setting from the server (when there is a token to ask with)."""
        config = self.config()
        if not (config.url and config.token and config.user_id):
            return
        self._run_call(ClaraApi(config).settings, self._notify_read)

    def _notify_read(self, settings: object, error: str) -> None:
        if error or not isinstance(settings, dict):
            return  # the setting stays out of reach: the connection can still be saved
        own = settings.get("notify_after")
        self._notify_original = own
        self.notify_mode.setCurrentIndex(self.notify_mode.findData("default" if own is None else "never" if own == 0 else "after"))
        if own:
            self.notify_seconds.setValue(own)
        self._notify_loaded = True
        self._update_notify()

    def _save_notify(self) -> None:
        """Save the setting if it was read and changed, then close; a refusal is shown and keeps the dialog open."""
        value = self._notify_value()
        if not self._notify_loaded or value == self._notify_original:
            return super().accept()
        self.test.setEnabled(False)
        self.save_button.setEnabled(False)
        self.verdict.setStyleSheet("")
        self.verdict.setText("Saving…")
        self._run_call(lambda: ClaraApi(self.config()).set_notify_after(value), self._notify_saved)

    def _notify_saved(self, _: object, error: str) -> None:
        self.test.setEnabled(True)
        self._update_save()
        if error:
            self.verdict.setStyleSheet("color: #b3261e;")
            self.verdict.setText(f"The notification delay was not saved: {error}")
            return
        super().accept()

    def _sign_in(self) -> None:
        self.test.setEnabled(False)
        self.save_button.setEnabled(False)
        self.verdict.setStyleSheet("")
        self.verdict.setText("Signing in…")
        self._login = LoginWorker(self.url.text(), self.user_id.text(), self.password.text(), self)
        self._login.signed_in.connect(self._signed_in)
        self._login.failed.connect(self._sign_in_failed)
        self._login.start()

    def _signed_in(self, token: str, name: str) -> None:
        self.token.setText(token)
        self.user_id.setText(name)  # as the server spells it
        self.password.clear()
        self.test.setEnabled(True)
        if not self._notify_loaded:  # now there is a token to ask the server with
            self._load_notify()
        if self._then_accept:
            self._then_accept = False
            self._save_notify()
        else:
            self._run_probe()

    def _sign_in_failed(self, message: str) -> None:
        self._then_accept = False
        self.test.setEnabled(True)
        self._update_save()
        self.verdict.setStyleSheet("color: #b3261e;")
        self.verdict.setText(message)

    def _update_save(self) -> None:
        config = self.config()
        self.save_button.setEnabled(bool(config.url and config.user_id and (config.token or self.password.text())))
        hint = url_hint(self.url.text())
        self.url_note.setText(hint)
        self.url_note.setVisible(bool(hint))

    def _run_probe(self) -> None:
        if self.password.text():  # sign in first; the connection is tested once it worked
            self._then_accept = False
            return self._sign_in()
        self.test.setEnabled(False)
        self.verdict.setStyleSheet("")
        self.verdict.setText("Connecting…")
        self._probe = ProbeWorker(ClaraApi(self.config()), self)
        self._probe.result.connect(self._probed)
        self._probe.start()

    def _probed(self, ok: bool, message: str) -> None:
        self.test.setEnabled(True)
        self.verdict.setStyleSheet("color: #1b7f3b;" if ok else "color: #b3261e;")
        self.verdict.setText(message)

    def done(self, result: int) -> None:
        for worker in (self._probe, self._login, *self._calls):
            if worker is not None and worker.isRunning():
                worker.wait(15_000)
        super().done(result)
