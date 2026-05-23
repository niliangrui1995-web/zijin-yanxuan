# -*- coding: utf-8 -*-
"""Small inline SVG icon helpers for chrome controls and status glyphs."""

from __future__ import annotations

from PyQt6.QtCore import QByteArray, Qt
from PyQt6.QtGui import QColor, QIcon, QPainter, QPixmap
from PyQt6.QtSvg import QSvgRenderer


_PATHS = {
    "minimize": '<path d="M5 12.5H19"/>',
    "maximize": '<path d="M7 7H17V17H7Z"/>',
    "restore": '<path d="M9 6H18V15"/><path d="M6 9H15V18H6Z"/>',
    "close": '<path d="M7 7L17 17"/><path d="M17 7L7 17"/>',
    "gear": (
        '<path d="M12 8.2A3.8 3.8 0 1 1 12 15.8A3.8 3.8 0 0 1 12 8.2Z"/>'
        '<path d="M12 3.8V6.0M12 18.0V20.2M4.9 7.0L6.8 8.1M17.2 15.9L19.1 17.0'
        'M4.9 17.0L6.8 15.9M17.2 8.1L19.1 7.0M3.8 12H6.0M18.0 12H20.2"/>'
    ),
    "check": '<path d="M6.5 12.5L10.2 16L17.8 8"/>',
    "hourglass": '<path d="M8 5H16M8 19H16M9 5C9 9 15 9 15 12C15 15 9 15 9 19M15 5C15 9 9 9 9 12C9 15 15 15 15 19"/>',
    "alert": '<path d="M12 4.8L20 18H4Z"/><path d="M12 9V13"/><path d="M12 16H12.01"/>',
}


def _svg(name: str, color: str, *, stroke_width: float = 1.55) -> bytes:
    path = _PATHS.get(name, _PATHS["gear"])
    stroke = QColor(color if color else "#9AA8BF").name()
    return (
        '<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" '
        'viewBox="0 0 24 24" fill="none" stroke="'
        + stroke
        + '" stroke-width="'
        + f"{stroke_width:.2f}"
        + '" stroke-linecap="round" stroke-linejoin="round">'
        + path
        + "</svg>"
    ).encode("utf-8")


def svg_icon(name: str, color: str, *, size: int = 18, stroke_width: float = 1.55) -> QIcon:
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.GlobalColor.transparent)

    renderer = QSvgRenderer(QByteArray(_svg(name, color, stroke_width=stroke_width)))
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    renderer.render(painter)
    painter.end()
    return QIcon(pixmap)


def set_button_svg_icon(button, name: str, color: str, *, size: int = 18) -> None:
    button.setText("")
    button.setIcon(svg_icon(name, color, size=size))
    button.setIconSize(button.iconSize().expandedTo(pixmap_size(size)))


def pixmap_size(size: int):
    from PyQt6.QtCore import QSize

    value = max(8, int(size or 18))
    return QSize(value, value)
