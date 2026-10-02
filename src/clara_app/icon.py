"""Clara's icon, drawn in code (no image files to ship)."""

from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QIcon, QLinearGradient, QPainter, QPixmap

SIZES = (16, 24, 32, 48, 64, 128, 256)
ACCENT = QColor("#6d5dfc")
ACCENT_DARK = QColor("#3b2fc9")
OFFLINE = QColor("#8a8f98")


def _draw(size: int, connected: bool) -> QPixmap:
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHints(QPainter.RenderHint.Antialiasing | QPainter.RenderHint.TextAntialiasing)
    gradient = QLinearGradient(QPointF(0, 0), QPointF(size, size))
    gradient.setColorAt(0, ACCENT if connected else OFFLINE.lighter(115))
    gradient.setColorAt(1, ACCENT_DARK if connected else OFFLINE.darker(120))
    painter.setBrush(gradient)
    painter.setPen(Qt.PenStyle.NoPen)
    margin = size * 0.04
    painter.drawEllipse(QRectF(margin, margin, size - 2 * margin, size - 2 * margin))
    font = QFont("Segoe UI")
    font.setBold(True)
    font.setPixelSize(max(8, round(size * 0.62)))
    painter.setFont(font)
    painter.setPen(QColor("white"))
    painter.drawText(QRectF(0, -size * 0.03, size, size), Qt.AlignmentFlag.AlignCenter, "C")
    painter.end()
    return pixmap


def make_icon(connected: bool = True) -> QIcon:
    """The icon in every size Windows may ask for; grey when the server cannot be reached."""
    icon = QIcon()
    for size in SIZES:
        icon.addPixmap(_draw(size, connected))
    return icon
