"""The look of the app: the same tokens as Clara's web site (chalk and Prussian-blue ink, the rail as the negative, one
violet signal), as a palette and a style sheet for Qt, light or dark.

`THEME` is the one place that knows the colours: widgets read `THEME.t["name"]`, and anything drawn by hand (an icon)
listens to `THEME.changed`. Everything else is styled by the application's style sheet, by object name and by
properties (`tone`, `kind`...), so a change of theme is one new style sheet.
"""

from __future__ import annotations

import tempfile
from pathlib import Path
from string import Template

from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtGui import QColor, QFont, QFontDatabase, QGuiApplication, QPalette
from PySide6.QtWidgets import QApplication, QWidget

FONT_DIR = Path(__file__).parent / "fonts"
BODY_FAMILY = "Hanken Grotesk"
DISPLAY_FAMILY = "Epilogue"

LIGHT = {
    "bg": "#eceFea", "panel": "#f6f8f4", "panel_2": "#e2e8e4", "raised": "#ffffff",
    "hover": "rgba(16, 41, 74, 0.07)", "select": "rgba(16, 41, 74, 0.11)",
    "line": "#c9d3d6", "line_strong": "#6f8494",
    "text": "#10294a", "text_2": "#2c4663", "muted": "#51687b",
    "primary": "#10294a", "primary_hover": "#1d3f6b", "primary_ink": "#eceFea",
    "accent": "#a987f1", "accent_hover": "#9a74ea", "accent_ink": "#1a1033", "accent_text": "#5a3bc4", "accent_soft": "#e6defa",
    "ok": "#2f6b4b", "ok_soft": "#dcece3", "danger": "#a8321f", "danger_soft": "#f6dfda", "warn": "#8a5a00",
    "code_bg": "#e3e9e6", "code_line": "#cdd7d6", "scrim": "rgba(8, 19, 31, 0.5)",
    "rail_bg": "#0d2036", "rail_text": "#e3eae8", "rail_text_2": "#c3d0d3", "rail_muted": "#8fa6b8",
    "rail_hover": "rgba(227, 234, 232, 0.09)", "rail_line": "rgba(227, 234, 232, 0.14)", "rail_signal": "#b79bf5",
    "rail_field": "rgba(227, 234, 232, 0.08)",
}
DARK = {
    "bg": "#0a1a2b", "panel": "#102338", "panel_2": "#173049", "raised": "#173049",
    "hover": "rgba(230, 237, 234, 0.08)", "select": "rgba(230, 237, 234, 0.12)",
    "line": "#24405a", "line_strong": "#5e7a92",
    "text": "#e6edea", "text_2": "#c3d0d3", "muted": "#8ea4b3",
    "primary": "#e6edea", "primary_hover": "#ffffff", "primary_ink": "#0a1a2b",
    "accent": "#a987f1", "accent_hover": "#b99bf5", "accent_ink": "#1a1033", "accent_text": "#c3a8ff", "accent_soft": "#2b2350",
    "ok": "#7cc49c", "ok_soft": "#173627", "danger": "#ff8f7c", "danger_soft": "#3d2320", "warn": "#e0b25c",
    "code_bg": "#08141f", "code_line": "#1c3249", "scrim": "rgba(0, 0, 0, 0.6)",
    "rail_bg": "#06111d", "rail_text": "#e3eae8", "rail_text_2": "#c3d0d3", "rail_muted": "#8fa6b8",
    "rail_hover": "rgba(227, 234, 232, 0.09)", "rail_line": "rgba(227, 234, 232, 0.14)", "rail_signal": "#b79bf5",
    "rail_field": "rgba(227, 234, 232, 0.08)",
}
MODES = ("auto", "light", "dark")

STYLE = Template(
    """
QWidget { color: $text; font-family: "$body"; }
QMainWindow, QDialog, QMessageBox, QStackedWidget, #page, #scroll-body { background: $bg; }
QToolTip { background: $raised; color: $text; border: 1px solid $line_strong; padding: 4px 8px; border-radius: 6px; }
QLabel { background: transparent; }
QLabel[tone="muted"] { color: $muted; }
QLabel[tone="bad"] { color: $danger; }
QLabel[tone="ok"] { color: $ok; }
QLabel[tone="warn"] { color: $warn; }
QLabel[heading="page"] { font-family: "$display"; font-weight: 600; font-size: 18px; }
QLabel[heading="section"] { font-family: "$display"; font-weight: 600; font-size: 16px; }
QLabel[heading="hero"] { font-family: "$display"; font-weight: 600; font-size: 40px; }
QLabel[heading="small"] { font-weight: 600; }

QLineEdit, QPlainTextEdit, QTextEdit, QTextBrowser, QSpinBox, QDoubleSpinBox, QDateTimeEdit, QComboBox {
  background: $raised; color: $text; border: 1px solid $line_strong; border-radius: 6px; padding: 6px 10px;
  selection-background-color: $accent; selection-color: $accent_ink;
}
QLineEdit:focus, QPlainTextEdit:focus, QTextEdit:focus, QSpinBox:focus, QDateTimeEdit:focus, QComboBox:focus { border: 1px solid $text; }
QLineEdit:disabled, QPlainTextEdit:disabled, QComboBox:disabled, QSpinBox:disabled, QDateTimeEdit:disabled { color: $muted; background: $panel_2; }
QComboBox::drop-down { border: none; width: 24px; }
QSpinBox::up-button, QSpinBox::down-button, QDoubleSpinBox::up-button, QDoubleSpinBox::down-button { width: 0; border: none; }
QComboBox QAbstractItemView { background: $raised; border: 1px solid $line_strong; border-radius: 6px; selection-background-color: $select; selection-color: $text; outline: 0; padding: 4px; }
QComboBox[flat="true"] { border: none; background: transparent; color: $muted; padding: 4px 8px; font-weight: 600; }
QComboBox[flat="true"]:hover { background: $hover; color: $text; }
QComboBox[flat="true"]:focus { border: none; background: $hover; color: $text; }

QPushButton { background: transparent; color: $text; border: 1px solid $line_strong; border-radius: 8px; padding: 7px 14px; font-weight: 600; }
QPushButton:hover { background: $hover; border-color: $text_2; }
QPushButton:pressed { background: $select; }
QPushButton:disabled { color: $muted; border-color: $line; }
QPushButton:focus { border-color: $text; }
QPushButton[kind="primary"] { background: $primary; color: $primary_ink; border-color: $primary; }
QPushButton[kind="primary"]:hover { background: $primary_hover; border-color: $primary_hover; }
QPushButton[kind="primary"]:disabled { background: $panel_2; color: $muted; border-color: $panel_2; }
QPushButton[kind="danger"] { color: $danger; border-color: $danger; }
QPushButton[kind="danger"]:hover { background: $danger_soft; }
QPushButton[kind="ghost"] { border-color: transparent; }
QPushButton[kind="ghost"]:hover { background: $hover; }
QPushButton[kind="send"] { background: $accent; color: $accent_ink; border: none; border-radius: 8px; padding: 0; }
QPushButton[kind="send"]:hover { background: $accent_hover; }
QPushButton[kind="send"]:disabled { background: $accent_soft; color: $muted; }
QPushButton[kind="stop"] { background: $text; color: $bg; border: none; border-radius: 8px; padding: 0; }
QPushButton[kind="chip"] { background: $panel_2; border: none; border-radius: 6px; padding: 4px 8px; font-weight: 500; }
QToolButton { background: transparent; border: none; border-radius: 8px; padding: 6px; color: $muted; }
QToolButton:hover { background: $hover; color: $text; }
QToolButton:checked { background: $select; color: $text; }
QToolButton:disabled { color: $line_strong; }
QToolButton::menu-indicator { image: none; }
QCheckBox, QRadioButton { spacing: 8px; background: transparent; }
QCheckBox::indicator, QRadioButton::indicator { width: 16px; height: 16px; border: 1px solid $line_strong; background: $raised; }
QCheckBox::indicator { border-radius: 4px; }
QCheckBox::indicator:hover, QRadioButton::indicator:hover { border-color: $text; }
QCheckBox::indicator:checked { background: $primary; border-color: $primary; image: url($check); }
QRadioButton::indicator { border-radius: 9px; }
QRadioButton::indicator:checked { border-color: $primary; background: qradialgradient(cx:0.5, cy:0.5, radius:0.5, fx:0.5, fy:0.5, stop:0 $primary, stop:0.45 $primary, stop:0.55 $raised, stop:1 $raised); }

QListWidget, QTreeWidget, QTableWidget { background: transparent; border: none; outline: 0; }
QListWidget::item { padding: 6px 10px; border-radius: 8px; }
QListWidget::item:hover { background: $hover; }
QListWidget::item:selected { background: $select; color: $text; }
QListWidget[plain="true"] { background: $raised; border: 1px solid $line; border-radius: 8px; }
QHeaderView::section { background: transparent; color: $muted; border: none; border-bottom: 1px solid $line_strong; padding: 8px 10px; font-weight: 600; text-align: left; }
QTableWidget { gridline-color: $line; }
QTableWidget::item { padding: 6px 10px; border-bottom: 1px solid $line; }
QTableWidget::item:selected { background: $select; color: $text; }

QScrollArea { border: none; background: transparent; }
QProgressBar { background: $line; border: none; border-radius: 4px; }
QProgressBar::chunk { background: $text; border-radius: 4px; }
QProgressBar[over="true"]::chunk { background: $danger; }
QScrollBar:vertical { background: transparent; width: 12px; margin: 2px; }
QScrollBar::handle:vertical { background: $line_strong; border-radius: 4px; min-height: 28px; margin: 0 2px; }
QScrollBar::handle:vertical:hover { background: $muted; }
QScrollBar:horizontal { background: transparent; height: 12px; margin: 2px; }
QScrollBar::handle:horizontal { background: $line_strong; border-radius: 4px; min-width: 28px; margin: 2px 0; }
QScrollBar::add-line, QScrollBar::sub-line { height: 0; width: 0; }
QScrollBar::add-page, QScrollBar::sub-page { background: transparent; }

QMenu { background: $raised; color: $text; border: 1px solid $line_strong; border-radius: 8px; padding: 4px; }
QMenu::item { padding: 7px 28px 7px 12px; border-radius: 6px; }
QMenu::item:selected { background: $select; }
QMenu::separator { height: 1px; background: $line; margin: 4px 8px; }

QGroupBox { border: 1px solid $line; border-radius: 8px; margin-top: 14px; padding: 16px 12px 10px 12px; font-weight: 600; }
QGroupBox::title { subcontrol-origin: margin; left: 12px; padding: 0 4px; }

QFrame#panel { background: transparent; border: 1px solid $line; border-top: 2px solid $text; border-radius: 4px 4px 8px 8px; }
QFrame#panel[danger="true"] { border-top-color: $danger; }
QFrame#panel-sep { background: $line; max-height: 1px; min-height: 1px; border: none; }
QFrame#notice { background: $panel_2; border-radius: 4px; }
QFrame#notice[tone="bad"] { background: $danger_soft; }
QLabel#badge { background: $panel_2; color: $text_2; border-radius: 4px; padding: 1px 7px; font-weight: 600; font-size: 12px; }
QLabel#badge[kind="admin"] { background: $primary; color: $primary_ink; }
QLabel#badge[kind="ok"] { background: $ok_soft; color: $ok; }
QLabel#badge[kind="off"] { background: $danger_soft; color: $danger; }

QFrame#page-head { background: $bg; border-bottom: 1px solid $line; }
QFrame#settings-bar { background: $bg; border-bottom: 1px solid $line; }
QPushButton#settings-tab { border: none; border-bottom: 2px solid transparent; border-radius: 0; color: $muted; padding: 10px 12px; background: transparent; }
QPushButton#settings-tab:hover { color: $text; background: transparent; }
QPushButton#settings-tab:checked { color: $text; border-bottom: 2px solid $accent; }
QPushButton#segment { border: none; border-radius: 6px; color: $muted; padding: 6px 14px; background: transparent; }
QPushButton#segment:checked { background: $primary; color: $primary_ink; }
QFrame#segmented { background: $hover; border-radius: 8px; }

QFrame#bubble[role="user"] { background: $accent_soft; border-radius: 12px; border-bottom-right-radius: 4px; }
QFrame#bubble[role="note"] { background: transparent; }
QFrame#bubble[role="error"] { background: $danger_soft; border-radius: 4px; }
QFrame#bubble[role="error"] QLabel { color: $danger; }
QFrame#bubble[role="clara"] { background: transparent; }
QLabel#notes { color: $muted; font-size: 12px; }

QFrame#composer { background: $raised; border: 1px solid $line_strong; border-radius: 12px; }
QFrame#composer[focused="true"] { border: 1px solid $text; }
QFrame#composer QPlainTextEdit { border: none; background: transparent; padding: 6px 8px; }

QFrame#qcm { background: transparent; border: 1px solid $line_strong; border-top: 2px solid $text; border-radius: 4px 4px 8px 8px; }
QLabel#qcm-title { font-family: "$display"; font-weight: 600; font-size: 16px; }
QLabel#qcm-question { font-weight: 600; }
QFrame#qcm-option { border: 1px solid $line_strong; border-radius: 8px; background: transparent; }
QFrame#qcm-option:hover { background: $hover; }
QFrame#qcm-option[picked="true"] { border: 1px solid $text; background: $select; }
QFrame#qcm-option[state="right"] { background: $ok_soft; border: 1px solid $ok; }
QFrame#qcm-option[state="wrong"] { background: $danger_soft; border: 1px solid $danger; }
QFrame#qcm-option[state="missed"] { border: 1px dashed $ok; }
QFrame#qcm-explain { background: $panel_2; border-radius: 4px; }
QLabel#qcm-explain { background: $panel_2; border-radius: 4px; padding: 5px 8px; }
QLabel#qcm-letter, QLabel#qcm-hint, QLabel#qcm-progress { color: $muted; font-weight: 600; }
QLabel#qcm-score { color: $text; font-weight: 600; }
QLabel#qcm-verdict[ok="true"] { color: $ok; font-weight: 600; }
QLabel#qcm-verdict[ok="false"] { color: $danger; font-weight: 600; }

QFrame#card { background: transparent; border: 1px solid $line; border-top: 2px solid $text; border-radius: 4px 4px 8px 8px; }
QFrame#card:hover { background: $hover; border-top-color: $accent; }

QFrame#rail { background: $rail_bg; }
QFrame#rail QLabel { color: $rail_text; }
QFrame#rail QLabel[tone="muted"] { color: $rail_muted; }
QFrame#rail QLabel[tone="ok"] { color: #7cc49c; }
QFrame#rail QLabel[tone="warn"] { color: #e0b25c; }
QFrame#rail QLabel[tone="bad"] { color: #ff8f7c; }
QFrame#rail QLabel[group="title"] { color: $rail_muted; font-size: 12px; font-weight: 600; padding: 12px 8px 2px 8px; }
QFrame#rail QLineEdit { background: $rail_field; border: 1px solid $rail_line; color: $rail_text; border-radius: 8px; padding: 6px 10px; }
QFrame#rail QLineEdit:focus { border: 1px solid $rail_text; }
QFrame#rail QPushButton { color: $rail_text; border-color: $rail_line; background: transparent; }
QFrame#rail QPushButton:hover { background: $rail_text; color: $rail_bg; border-color: $rail_text; }
QFrame#rail QToolButton { color: $rail_muted; }
QFrame#rail QToolButton:hover { background: $rail_hover; color: $rail_text; }
QFrame#rail QPushButton#nav { border: none; border-left: 3px solid transparent; border-radius: 8px; text-align: left; padding: 8px 12px; color: $rail_text_2; font-weight: 600; }
QFrame#rail QPushButton#nav:hover { background: $rail_hover; color: $rail_text; }
QFrame#rail QPushButton#nav:checked { background: $rail_hover; color: $rail_text; border-left: 3px solid $rail_signal; border-top-left-radius: 0; border-bottom-left-radius: 0; }
QFrame#rail QListWidget { background: transparent; color: $rail_text_2; }
QFrame#rail QListWidget::item { color: $rail_text_2; padding: 7px 10px; }
QFrame#rail QListWidget::item:hover { background: $rail_hover; color: $rail_text; }
QFrame#rail QListWidget::item:selected { background: $rail_hover; color: $rail_text; }
QFrame#rail QScrollBar::handle:vertical { background: $rail_line; }
QFrame#rail-foot { border-top: 1px solid $rail_line; }
QPushButton#me { border: none; border-radius: 8px; text-align: left; padding: 6px 8px; background: transparent; }
QPushButton#me:hover { background: $rail_hover; color: $rail_text; border: none; }
QPushButton#me:checked { background: $rail_hover; }
QWidget#scrim { background: $scrim; }
QLabel#avatar { background: $accent; color: $accent_ink; border-radius: 8px; font-family: "$display"; font-weight: 600; }
QFrame#rail QLabel#avatar { background: $rail_signal; color: $accent_ink; }
"""
)


def _color(value: str) -> QColor:
    """A colour from `#rrggbb` or `rgba(r, g, b, a)`."""
    if value.startswith("rgba"):
        r, g, b, a = [part.strip() for part in value[value.index("(") + 1 : value.rindex(")")].split(",")]
        return QColor(int(r), int(g), int(b), int(float(a) * 255))
    return QColor(value)


class ThemeManager(QObject):
    """The colours in use. `changed` is emitted when the light or dark theme (or the preference) changes."""

    changed = Signal()

    def __init__(self):
        super().__init__()
        self.preference = "auto"
        self.mode = "light"
        self.t = dict(LIGHT)

    def qcolor(self, name: str) -> QColor:
        return _color(self.t[name])

    def system_dark(self) -> bool:
        hints = QGuiApplication.styleHints()
        return hints is not None and hints.colorScheme() == Qt.ColorScheme.Dark

    def resolve(self) -> str:
        return ("dark" if self.system_dark() else "light") if self.preference == "auto" else self.preference

    def apply(self, preference: str | None = None, app: QApplication | None = None) -> None:
        """Use the theme `preference` ("auto" follows Windows) and tell everyone that listens."""
        if preference is not None:
            self.preference = preference if preference in MODES else "auto"
        app = app or QApplication.instance()
        self.mode = self.resolve()
        self.t = dict(DARK if self.mode == "dark" else LIGHT)
        if app is None:
            return
        app.setStyle("Fusion")
        app.setPalette(self.palette())
        app.setStyleSheet(STYLE.substitute(body=BODY_FAMILY, display=DISPLAY_FAMILY, check=self._check_mark(), **self.t))
        self.changed.emit()

    def _check_mark(self) -> str:
        """The tick of a ticked box, drawn in the colour of the theme in a file the style sheet can point to."""
        from .icons import svg

        folder = Path(tempfile.gettempdir()) / "clara-app"
        folder.mkdir(exist_ok=True)
        target = folder / f"check-{self.mode}.svg"
        target.write_bytes(svg("check", self.t["primary_ink"], 2.6))
        return target.as_posix()

    def palette(self) -> QPalette:
        t, q = self.t, self.qcolor
        palette = QPalette()
        for role, name in (
            (QPalette.ColorRole.Window, "bg"), (QPalette.ColorRole.WindowText, "text"), (QPalette.ColorRole.Base, "raised"),
            (QPalette.ColorRole.AlternateBase, "panel_2"), (QPalette.ColorRole.Text, "text"), (QPalette.ColorRole.Button, "bg"),
            (QPalette.ColorRole.ButtonText, "text"), (QPalette.ColorRole.PlaceholderText, "muted"),
            (QPalette.ColorRole.ToolTipBase, "raised"), (QPalette.ColorRole.ToolTipText, "text"),
            (QPalette.ColorRole.Highlight, "accent"), (QPalette.ColorRole.HighlightedText, "accent_ink"),
            (QPalette.ColorRole.Link, "accent_text"), (QPalette.ColorRole.LinkVisited, "accent_text"),
            (QPalette.ColorRole.Mid, "line_strong"), (QPalette.ColorRole.Midlight, "line"), (QPalette.ColorRole.Light, "panel"),
            (QPalette.ColorRole.Dark, "text_2"), (QPalette.ColorRole.Shadow, "text"),
        ):
            palette.setColor(role, q(name))
        for role in (QPalette.ColorRole.Text, QPalette.ColorRole.WindowText, QPalette.ColorRole.ButtonText):
            palette.setColor(QPalette.ColorGroup.Disabled, role, q("muted"))
        del t
        return palette


THEME = ThemeManager()


def load_fonts() -> None:
    """Register the fonts that ship with the app (the site's two) and make the body font the application's."""
    for path in sorted(FONT_DIR.glob("*.ttf")):
        QFontDatabase.addApplicationFont(str(path))


def install(app: QApplication, preference: str = "auto") -> None:
    """Fonts, palette and style sheet for the whole application; follows Windows while the preference is "auto"."""
    load_fonts()
    font = QFont(BODY_FAMILY)
    font.setPixelSize(14)
    app.setFont(font)
    hints = QGuiApplication.styleHints()
    if hints is not None:
        hints.colorSchemeChanged.connect(lambda *_: THEME.apply() if THEME.preference == "auto" else None)
    THEME.apply(preference, app)


def tone(widget: QWidget, name: str | None) -> None:
    """Colour a label by its meaning ("muted", "bad", "ok", "warn"; None: as the text), through the style sheet."""
    widget.setProperty("tone", name or "")
    widget.style().unpolish(widget)
    widget.style().polish(widget)


def set_property(widget: QWidget, name: str, value: object) -> None:
    """Set a style-sheet property and have the widget look again."""
    widget.setProperty(name, value)
    widget.style().unpolish(widget)
    widget.style().polish(widget)
