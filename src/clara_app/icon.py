"""Clara's icon: the picture in `clara.ico` (the same as the web site's favicon), in every size Windows may ask for.
When the server cannot be reached it turns grey. If the file is missing, a plain disc with a "C" is drawn instead."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QIcon, QImage, QLinearGradient, QPainter, QPixmap, qGray

SIZES = (16, 24, 32, 48, 64, 128, 256)
ICON_FILE = Path(__file__).with_name("clara.ico")
ACCENT = QColor("#6d5dfc")
ACCENT_DARK = QColor("#3b2fc9")
OFFLINE = QColor("#8a8f98")
GREY_LIGHTNESS = 0.55  # how much of its brightness the picture keeps when the server is down


@lru_cache(maxsize=1)
def _picture() -> QImage | None:
    """The picture of the icon file, or None when it cannot be read."""
    image = QImage(str(ICON_FILE))
    if image.isNull():
        return None
    return image.convertToFormat(QImage.Format.Format_ARGB32)


def _grey(image: QImage) -> QImage:
    """The same picture without colour, dimmed (its transparency is kept)."""
    grey = QImage(image)
    for y in range(grey.height()):
        for x in range(grey.width()):
            pixel = image.pixelColor(x, y)
            level = round(qGray(pixel.rgb()) * GREY_LIGHTNESS + 255 * (1 - GREY_LIGHTNESS) * 0.25)
            grey.setPixelColor(x, y, QColor(level, level, level, pixel.alpha()))
    return grey


def _scaled(image: QImage, size: int) -> QPixmap:
    return QPixmap.fromImage(
        image.scaled(size, size, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
    )


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


@lru_cache(maxsize=2)
def _pictures(connected: bool) -> tuple[QPixmap, ...]:
    picture = _picture()
    if picture is None:
        return tuple(_draw(size, connected) for size in SIZES)
    image = picture if connected else _grey(picture)
    return tuple(_scaled(image, size) for size in SIZES)


def make_icon(connected: bool = True) -> QIcon:
    """The icon in every size Windows may ask for; grey when the server cannot be reached."""
    icon = QIcon()
    for pixmap in _pictures(connected):
        icon.addPixmap(pixmap)
    return icon
