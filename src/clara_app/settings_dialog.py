"""The connection settings: which server, who you are, and the token that proves it. Signing in with a password turns it
into a token (the password itself is never kept). Everything else (the model, notifications, the theme) is on the
Account page."""

from __future__ import annotations

from dataclasses import replace

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
)

from .api import ClaraApi
from .config import Config, url_hint
from .theme import tone
from .workers import LoginWorker, ProbeWorker


class SettingsDialog(QDialog):
    def __init__(self, config: Config, parent=None, first_run: bool = False):
        super().__init__(parent)
        self.setWindowTitle("Clara · Connection")
        self.setMinimumWidth(460)
        self._config = config
        self._probe: ProbeWorker | None = None
        self._login: LoginWorker | None = None
        self._then_accept = False

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

        form = QFormLayout()
        form.setVerticalSpacing(10)
        form.addRow("Server", self.url)
        form.addRow("User name", self.user_id)
        form.addRow("Password", self.password)
        form.addRow("Sign-in token", self.token)
        form.addRow("Your name", self.user_name)

        self.url_note = QLabel("")
        self.url_note.setWordWrap(True)
        tone(self.url_note, "warn")

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
        self.save_button = buttons.button(QDialogButtonBox.StandardButton.Save)
        self.save_button.setProperty("kind", "primary")

        layout = QVBoxLayout(self)
        layout.setSpacing(12)
        if first_run:
            intro = QLabel("Welcome! Tell the app which Clara server to talk to, and sign in.")
            intro.setWordWrap(True)
            layout.addWidget(intro)
        layout.addLayout(form)
        layout.addWidget(self.url_note)
        layout.addLayout(test_row)
        layout.addWidget(buttons)
        for field in (self.url, self.token, self.user_id, self.password):
            field.textChanged.connect(self._update_save)
        self._update_save()

    def config(self) -> Config:
        """The settings as typed."""
        return replace(
            self._config,
            url=self.url.text().strip(),
            token=self.token.text().strip(),
            user_id=self.user_id.text().strip(),
            user_name=self.user_name.text().strip(),
        )

    def _say(self, text: str, kind: str | None = None) -> None:
        self.verdict.setText(text)
        tone(self.verdict, kind)

    def accept(self) -> None:
        """Save. A password typed is first exchanged for a token (and then forgotten)."""
        if self.password.text():
            self._then_accept = True
            self._sign_in()
        else:
            super().accept()

    def _sign_in(self) -> None:
        self.test.setEnabled(False)
        self.save_button.setEnabled(False)
        self._say("Signing in…")
        self._login = LoginWorker(self.url.text(), self.user_id.text(), self.password.text(), self)
        self._login.signed_in.connect(self._signed_in)
        self._login.failed.connect(self._sign_in_failed)
        self._login.start()

    def _signed_in(self, token: str, name: str) -> None:
        self.token.setText(token)
        self.user_id.setText(name)  # as the server spells it
        self.password.clear()
        self.test.setEnabled(True)
        if self._then_accept:
            self._then_accept = False
            super().accept()
        else:
            self._run_probe()

    def _sign_in_failed(self, message: str) -> None:
        self._then_accept = False
        self.test.setEnabled(True)
        self._update_save()
        self._say(message, "bad")

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
        self._say("Connecting…")
        self._probe = ProbeWorker(ClaraApi(self.config()), self)
        self._probe.result.connect(self._probed)
        self._probe.start()

    def _probed(self, ok: bool, message: str) -> None:
        self.test.setEnabled(True)
        self._say(message, "ok" if ok else "bad")

    def done(self, result: int) -> None:
        for worker in (self._probe, self._login):
            if worker is not None and worker.isRunning():
                worker.wait(15_000)
        super().done(result)
