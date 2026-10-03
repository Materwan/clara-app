"""The connection settings: server, token, who you are."""

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
from .workers import ProbeWorker


class SettingsDialog(QDialog):
    def __init__(self, config: Config, parent=None, first_run: bool = False):
        super().__init__(parent)
        self.setWindowTitle("Clara · Settings")
        self.setMinimumWidth(420)
        self._config = config
        self._probe: ProbeWorker | None = None

        self.url = QLineEdit(config.url)
        self.url.setPlaceholderText("http://127.0.0.1:8765, or https://<machine>.<tailnet>.ts.net")
        self.token = QLineEdit(config.token)
        self.token.setEchoMode(QLineEdit.EchoMode.Password)
        self.token.setPlaceholderText("a token of CLARA_TOKENS")
        self.user_id = QLineEdit(config.user_id)
        self.user_name = QLineEdit(config.user_name)
        self.user_name.setPlaceholderText("how Clara should call you (optional)")

        form = QFormLayout()
        form.addRow("Server", self.url)
        form.addRow("Token", self.token)
        form.addRow("Your id", self.user_id)
        form.addRow("Your name", self.user_name)

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
        for field in (self.url, self.token, self.user_id):
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

    def _update_save(self) -> None:
        self.save_button.setEnabled(self.config().ready)
        hint = url_hint(self.url.text())
        self.url_note.setText(hint)
        self.url_note.setVisible(bool(hint))

    def _run_probe(self) -> None:
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
        if self._probe is not None and self._probe.isRunning():
            self._probe.wait(15_000)
        super().done(result)
