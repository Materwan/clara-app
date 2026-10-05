"""Requests for permission: when Clara wants to do something that needs your say (replace a file, delete, commit to the
main branch…) she goes on with something else and the request waits. A card shows it, with Approve and Deny; the same
card is in the conversation, in the Integrations page and in the list the tray notification opens. Answering runs the
action on the server (or, for a folder of this computer, on the app)."""

from __future__ import annotations

from typing import Callable

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QDialog, QFrame, QHBoxLayout, QLabel, QMenu, QToolButton, QVBoxLayout, QWidget

from .api import ClaraApi
from .config import Config
from .icons import bind_icon, icon
from .widgets import Calls, EmptyState, Notice, badge, button, label

LEVEL_WORDS = {"read": "Look", "write": "Add or change", "destructive": "Replace or delete"}
LEVEL_KINDS = {"read": "", "write": "ask", "destructive": "off"}


def level_word(level: str) -> str:
    return LEVEL_WORDS.get(level, level)


class ApprovalCard(QFrame, Calls):
    """One request. `decided(approval)` once it was answered here, or found answered somewhere else."""

    decided = Signal(dict)

    def __init__(self, approval: dict, get_config: Callable[[], Config], api_factory: Callable[[Config], ClaraApi], parent: QWidget | None = None):
        super().__init__(parent)
        self._init_calls(get_config, api_factory)
        self.approval = approval
        self.setObjectName("approval")
        outer = QVBoxLayout(self)
        outer.setContentsMargins(16, 12, 16, 12)
        outer.setSpacing(6)

        head = QHBoxLayout()
        head.setSpacing(8)
        shield = QLabel()
        shield.setPixmap(icon("shield", "accent_text", 20).pixmap(20, 20))
        head.addWidget(shield)
        head.addWidget(label("Clara asks for your permission", heading="small"), 1)
        head.addWidget(badge(level_word(approval.get("level", "")), LEVEL_KINDS.get(approval.get("level", ""), "")))
        outer.addLayout(head)
        summary = label(approval.get("summary", ""), wrap=True, selectable=True)
        summary.setObjectName("approval-summary")
        outer.addWidget(summary)
        outer.addWidget(label(f"On {approval.get('resource') or 'a resource'}. Nothing is done until you approve.", tone_="muted", wrap=True))
        if approval.get("reason"):
            reason = label(f"Clara says: “{approval['reason']}”", wrap=True)
            reason.setObjectName("approval-reason")
            outer.addWidget(reason)

        self.approve_button = button("Approve", "primary", lambda: self._decide(True))
        self.deny_button = button("Deny", "", lambda: self._decide(False))
        self.more_button = QToolButton()
        self.more_button.setToolTip("More ways to approve")
        self.more_button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        bind_icon(self.more_button, "more", "muted", 20)
        menu = QMenu(self.more_button)
        menu.addAction("Approve, and do not ask again in this conversation", lambda: self._decide(True, "conversation"))
        menu.addAction("Approve, and do not ask again for this resource", lambda: self._decide(True, "resource"))
        self.more_button.setMenu(menu)
        self.actions = QWidget()
        row = QHBoxLayout(self.actions)
        row.setContentsMargins(0, 4, 0, 0)
        row.setSpacing(8)
        row.addWidget(self.approve_button)
        row.addWidget(self.deny_button)
        row.addWidget(self.more_button)
        row.addStretch(1)
        outer.addWidget(self.actions)
        self.status = label("", wrap=True)
        self.status.hide()
        outer.addWidget(self.status)

    def _decide(self, approve: bool, remember: str = "") -> None:
        self.actions.setEnabled(False)
        self._call(lambda api: api.decide_approval(self.approval["id"], approve, remember), self._decided)

    def _decided(self, result: object, error: str) -> None:
        if error:
            if "already" in error.lower() or "409" in error:  # answered somewhere else a moment ago
                self._settle("Already answered.", {**self.approval, "status": "answered"})
                return
            self.actions.setEnabled(True)
            self.status.setText(error)
            self.status.setProperty("tone", "bad")
            self.status.style().unpolish(self.status)
            self.status.style().polish(self.status)
            self.status.show()
            return
        done = dict(result)  # type: ignore[arg-type]
        text = (
            "Denied. Nothing was done." if done.get("status") == "denied"
            else f"Approved, but it failed: {done.get('result', '')}" if done.get("status") == "failed"
            else f"Approved and done. {done.get('result', '')}".strip()
        )
        self._settle(text, done)

    def _settle(self, text: str, done: dict) -> None:
        self.actions.hide()
        self.setProperty("settled", True)
        self.style().unpolish(self)
        self.style().polish(self)
        self.status.setText(text)
        self.status.show()
        self.decided.emit(done)

    def shutdown(self) -> None:
        self.stop_calls()


class ApprovalsDialog(QDialog, Calls):
    """Every request that waits for an answer."""

    changed = Signal()  # one was answered: the badge of the rail counts again

    def __init__(self, get_config: Callable[[], Config], api_factory: Callable[[Config], ClaraApi], parent: QWidget | None = None):
        super().__init__(parent)
        self._init_calls(get_config, api_factory)
        self.setWindowTitle("Waiting for your permission")
        self.setMinimumWidth(560)
        self.cards: list[ApprovalCard] = []
        self.layout_ = QVBoxLayout(self)
        self.layout_.setContentsMargins(20, 18, 20, 16)
        self.layout_.setSpacing(12)
        self.layout_.addWidget(label("Waiting for your permission", heading="section"))
        self.notice = Notice("")
        self.notice.hide()
        self.layout_.addWidget(self.notice)
        self.list = QVBoxLayout()
        self.list.setSpacing(12)
        self.layout_.addLayout(self.list)
        close = QHBoxLayout()
        close.addStretch(1)
        close.addWidget(button("Close", "", self.accept))
        self.layout_.addLayout(close)
        self.refresh()

    def refresh(self) -> None:
        self._call(lambda api: api.approvals(), self._listed)

    def _listed(self, result: object, error: str) -> None:
        if error:
            self.notice.say(error, bad=True)
            return
        self.notice.say("")
        for card in self.cards:
            card.shutdown()
            card.hide()
            card.deleteLater()
        while self.list.count():
            item = self.list.takeAt(0)
            if item.widget() is not None:
                item.widget().hide()
                item.widget().deleteLater()
        self.cards = []
        found = list(result)  # type: ignore[arg-type]
        if not found:
            self.list.addWidget(EmptyState("Nothing is waiting for you", "Requests Clara makes show up here."))
            return
        for approval in found:
            where = label(f"In {approval.get('conversation', '')}", tone_="muted")
            card = ApprovalCard(approval, self._get_config, self._api_factory)
            card.decided.connect(lambda _done: self.changed.emit())
            self.cards.append(card)
            self.list.addWidget(where)
            self.list.addWidget(card)

    def done(self, code: int) -> None:
        for card in self.cards:
            card.shutdown()
        self.stop_calls()
        super().done(code)

    def keyPressEvent(self, event) -> None:
        if event.key() == Qt.Key.Key_Escape:
            self.reject()
            return
        super().keyPressEvent(event)
