"""The frame of the window, as on the web site: a rail of Prussian blue on the left (Clara's portrait, a new chat, the
pages you work in, your conversations, and you), and the page beside it with its header. The pages of settings (Memory,
Account, Discord, Admin) are reached through you, at the bottom of the rail, and share a bar of tabs under the header.

Below `NARROW` pixels the rail slides over the page instead of sitting beside it, as on a phone."""

from __future__ import annotations

from typing import Callable

from PySide6.QtCore import QEasingCurve, QEvent, QPropertyAnimation, QRect, Qt, Signal
from PySide6.QtGui import QKeyEvent, QMouseEvent
from PySide6.QtWidgets import (
    QButtonGroup,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QStackedWidget,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from . import APP_NAME
from .icons import bind_icon, icon, portrait_label
from .theme import THEME, tone
from .widgets import NAV, Page, avatar

RAIL_WIDTH = 276
NARROW = 780  # below this width the rail is a drawer

WORK = (("chat", "Chat", "chat"), ("projects", "Projects", "folder"), ("tasks", "Tasks", "tasks"), ("files", "Files", "file"))
SETTINGS = (("memory", "Memory", "memory"), ("account", "Account", "user"), ("discord", "Discord", "bot"), ("admin", "Admin", "admin"))
ADMIN_ONLY = ("discord", "admin")


class Scrim(QWidget):
    clicked = Signal()

    def __init__(self, parent: QWidget):
        super().__init__(parent)
        self.setObjectName("scrim")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.hide()

    def mousePressEvent(self, event: QMouseEvent) -> None:
        self.clicked.emit()


class Shell(QWidget):
    new_chat_requested = Signal()
    page_changed = Signal(str)

    def __init__(self, history: QWidget, factories: dict[str, Callable[[], Page]]):
        super().__init__()
        self._factories = factories
        self.pages: dict[str, Page] = {}
        self.current = ""
        self.is_admin = False
        self.narrow = False
        self._rail_open = False
        self._actions: list[QWidget] = []

        # ---- the rail -------------------------------------------------------------------------------
        self.rail = QFrame(self)
        self.rail.setObjectName("rail")
        self.rail.setFixedWidth(RAIL_WIDTH)
        self.rail.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        brand = QPushButton(f"  {APP_NAME}")
        brand.setFlat(True)
        brand.setCursor(Qt.CursorShape.PointingHandCursor)
        brand.setStyleSheet('QPushButton { border: none; background: transparent; text-align: left; font-family: "Epilogue"; '
                            "font-weight: 600; font-size: 22px; padding: 2px 4px; } QPushButton:hover { background: transparent; border: none; }")
        brand.clicked.connect(lambda: self.show_page("chat"))
        brand_icon = portrait_label(30)
        head = QHBoxLayout()
        head.setContentsMargins(6, 2, 4, 0)
        head.setSpacing(0)
        head.addWidget(brand_icon)
        head.addWidget(brand, 1)
        self.close_rail = QToolButton()
        self.close_rail.setToolTip("Close the navigation")
        bind_icon(self.close_rail, "close", "rail_muted", 18)
        self.close_rail.clicked.connect(lambda: self.set_rail_open(False))
        self.close_rail.hide()
        head.addWidget(self.close_rail)

        self.new_chat = QPushButton("  New chat")
        self.new_chat.setCursor(Qt.CursorShape.PointingHandCursor)
        self.new_chat.setToolTip("Start a new conversation (the others stay in the list)")
        bind_icon(self.new_chat, "plus", "rail_text", 18)
        self.new_chat.clicked.connect(self.new_chat_requested)

        self.nav_buttons: dict[str, QPushButton] = {}
        self._group = QButtonGroup(self)  # the pages you work in, and you
        self._tab_group = QButtonGroup(self)  # the tabs of the settings
        nav = QVBoxLayout()
        nav.setSpacing(1)
        for key, text, glyph in WORK:
            nav.addWidget(self._nav_button(key, text, glyph))

        self.me = QPushButton()
        self.me.setObjectName("me")
        self.me.setCheckable(True)
        self.me.setMinimumHeight(54)
        self.me.setCursor(Qt.CursorShape.PointingHandCursor)
        self.me.setToolTip("Your settings: memory, account")
        self.me.clicked.connect(lambda: self.show_page("account"))
        self._me_row = QHBoxLayout(self.me)
        self._me_row.setContentsMargins(6, 4, 8, 4)
        self._me_row.setSpacing(10)
        self._avatar = avatar("?", 34)
        self._avatar.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self._me_name = QLabel("")
        self._me_name.setStyleSheet("font-weight: 700;")
        self._me_status = QLabel("")
        self._me_status.setProperty("tone", "muted")
        self._me_status.setStyleSheet("font-size: 12px;")
        who = QVBoxLayout()
        who.setSpacing(0)
        who.addWidget(self._me_name)
        who.addWidget(self._me_status)
        self._me_row.addWidget(self._avatar)
        self._me_row.addLayout(who, 1)
        chevron = QLabel()
        chevron.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self._chevron = chevron
        self._me_row.addWidget(chevron)
        for widget in (self._me_name, self._me_status):
            widget.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self._group.addButton(self.me)
        foot = QFrame()
        foot.setObjectName("rail-foot")
        foot_row = QVBoxLayout(foot)
        foot_row.setContentsMargins(8, 8, 8, 10)
        foot_row.addWidget(self.me)

        rail_layout = QVBoxLayout(self.rail)
        rail_layout.setContentsMargins(12, 14, 12, 0)
        rail_layout.setSpacing(0)
        rail_layout.addLayout(head)
        rail_layout.addSpacing(14)
        rail_layout.addWidget(self.new_chat)
        rail_layout.addSpacing(10)
        rail_layout.addLayout(nav)
        rail_layout.addSpacing(10)
        rail_layout.addWidget(history, 1)
        rail_layout.addWidget(foot)
        foot.setContentsMargins(0, 0, 0, 0)

        # ---- the page ----------------------------------------------------------------------------------
        self.content = QWidget(self)
        self.content.setObjectName("page")
        self.content.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.menu_button = QToolButton()
        self.menu_button.setToolTip("Open the navigation")
        bind_icon(self.menu_button, "menu", "muted", 20)
        self.menu_button.clicked.connect(NAV.menu_requested)
        self.menu_button.hide()
        self.title = QLabel("")
        self.title.setProperty("heading", "page")
        self.title.setTextFormat(Qt.TextFormat.PlainText)
        self.title.setMinimumWidth(40)
        self.title.setSizePolicy(self.title.sizePolicy().horizontalPolicy(), self.title.sizePolicy().verticalPolicy())
        self.actions_row = QHBoxLayout()
        self.actions_row.setSpacing(6)
        header = QFrame()
        header.setObjectName("page-head")
        header.setFixedHeight(58)
        head_row = QHBoxLayout(header)
        head_row.setContentsMargins(16, 0, 18, 0)
        head_row.setSpacing(8)
        head_row.addWidget(self.menu_button)
        head_row.addWidget(self.title, 1)
        head_row.addLayout(self.actions_row)

        self.settings_bar = QFrame()
        self.settings_bar.setObjectName("settings-bar")
        bar = QHBoxLayout(self.settings_bar)
        bar.setContentsMargins(14, 0, 14, 0)
        bar.setSpacing(2)
        self.tabs: dict[str, QPushButton] = {}
        for key, text, glyph in SETTINGS:
            tab = QPushButton(f" {text}")
            tab.setObjectName("settings-tab")
            tab.setCheckable(True)
            tab.setCursor(Qt.CursorShape.PointingHandCursor)
            bind_icon(tab, glyph, "muted", 17)
            tab.clicked.connect(lambda _=False, k=key: self.show_page(k))
            self._tab_group.addButton(tab)
            self.tabs[key] = tab
            bar.addWidget(tab)
        bar.addStretch(1)
        self.settings_bar.hide()

        self.stack = QStackedWidget()
        column = QVBoxLayout(self.content)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(0)
        column.addWidget(header)
        column.addWidget(self.settings_bar)
        column.addWidget(self.stack, 1)

        self.scrim = Scrim(self)
        self.scrim.clicked.connect(lambda: self.set_rail_open(False))
        self._slide = QPropertyAnimation(self.rail, b"geometry", self)
        self._slide.setDuration(180)
        self._slide.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._slide.finished.connect(self._slid)
        NAV.menu_requested.connect(lambda: self.set_rail_open(not self._rail_open))
        THEME.changed.connect(self._restyle)
        self._restyle()
        self.set_user("", "")
        self.set_admin(False)

    # -- the rail ---------------------------------------------------------------------------------------- #

    def _nav_button(self, key: str, text: str, glyph: str) -> QPushButton:
        button = QPushButton(f"  {text}")
        button.setObjectName("nav")
        button.setCheckable(True)
        button.setCursor(Qt.CursorShape.PointingHandCursor)
        bind_icon(button, glyph, "rail_muted", 19)
        button.clicked.connect(lambda _=False, k=key: self.show_page(k))
        self._group.addButton(button)
        self.nav_buttons[key] = button
        return button

    def _restyle(self) -> None:
        self._chevron.setPixmap(icon("chevron", "rail_muted", 16).pixmap(16, 16))

    def set_user(self, name: str, detail: str) -> None:
        """Who is signed in, shown at the bottom of the rail."""
        self._avatar.setText((name or "?").strip()[:1].upper() or "?")
        self._me_name.setText(name or APP_NAME)

    def set_status(self, text: str, state: str | None) -> None:
        """What the server is doing, under the name."""
        self._me_status.setText(text)
        tone(self._me_status, {"running": "ok", "down": "bad", "stopping": "warn"}.get(state or "", "muted"))
        self._me_status.setStyleSheet("font-size: 12px;")

    def set_admin(self, admin: bool) -> None:
        self.is_admin = admin
        for key in ADMIN_ONLY:
            self.tabs[key].setVisible(admin)
        if not admin and self.current in ADMIN_ONLY:
            self.show_page("account")

    # -- pages --------------------------------------------------------------------------------------------- #

    def page(self, name: str) -> Page:
        """A page, made the first time it is asked for."""
        if name not in self.pages:
            page = self._factories[name]()
            self.pages[name] = page
            page.title_changed.connect(lambda text, p=page: self._retitle(p, text))
            self.stack.addWidget(page)
        return self.pages[name]

    def _retitle(self, page: Page, text: str) -> None:
        if self.pages.get(self.current) is page:
            self.title.setText(text)

    def show_page(self, name: str) -> None:
        if name in ADMIN_ONLY and not self.is_admin:
            name = "account"
        page = self.page(name)
        for widget in self._actions:  # the last page's buttons leave the header (the page keeps them)
            self.actions_row.removeWidget(widget)
            widget.hide()
        self._actions = page.actions()
        for widget in self._actions:
            self.actions_row.addWidget(widget)
            widget.show()
        self.current = name
        self.stack.setCurrentWidget(page)
        self.title.setText(page.page_title)
        settings = name in self.tabs
        self.settings_bar.setVisible(settings)
        if name in self.tabs:
            self.tabs[name].setChecked(True)
            self.me.setChecked(True)
        elif name in self.nav_buttons:
            self.nav_buttons[name].setChecked(True)
        self.set_rail_open(False)
        page.activated()
        self.page_changed.emit(name)

    # -- the rail as a drawer ------------------------------------------------------------------------------ #

    def set_rail_open(self, opened: bool) -> None:
        if not self.narrow:
            return
        if opened == self._rail_open and self.rail.isVisible() == opened:
            return
        self._rail_open = opened
        height = self.height()
        shown, hidden = QRect(0, 0, RAIL_WIDTH, height), QRect(-RAIL_WIDTH, 0, RAIL_WIDTH, height)
        self.rail.raise_() if opened else None
        if opened:
            self.scrim.setGeometry(self.rect())
            self.scrim.show()
            self.scrim.raise_()
            self.rail.raise_()
            self.rail.show()
        self._slide.stop()
        self._slide.setStartValue(self.rail.geometry() if self.rail.isVisible() else hidden)
        self._slide.setEndValue(shown if opened else hidden)
        self._slide.start()
        if opened:
            self.rail.setFocus()

    def _slid(self) -> None:
        if self.narrow and not self._rail_open:
            self.rail.hide()
            self.scrim.hide()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        narrow = self.width() < NARROW
        if narrow != self.narrow:
            self.narrow = narrow
            self._rail_open = False
            self._slide.stop()
            self.menu_button.setVisible(narrow)
            self.close_rail.setVisible(narrow)
            self.scrim.hide()
            NAV.narrow_changed.emit(narrow)
        height = self.height()
        if narrow:
            self.content.setGeometry(0, 0, self.width(), height)
            self.scrim.setGeometry(self.rect())
            if self._rail_open:
                self.rail.setGeometry(0, 0, RAIL_WIDTH, height)
                self.rail.show()
            else:
                self.rail.setGeometry(-RAIL_WIDTH, 0, RAIL_WIDTH, height)
                self.rail.hide()
        else:
            self.rail.setGeometry(0, 0, RAIL_WIDTH, height)
            self.rail.show()
            self.content.setGeometry(RAIL_WIDTH, 0, self.width() - RAIL_WIDTH, height)

    def keyPressEvent(self, event: QKeyEvent) -> None:
        if event.key() == Qt.Key.Key_Escape and self._rail_open:
            self.set_rail_open(False)
            return
        super().keyPressEvent(event)

    def changeEvent(self, event: QEvent) -> None:
        super().changeEvent(event)
