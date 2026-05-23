# -*- coding: utf-8 -*-
"""Small inline SVG icon helpers for chrome controls and status glyphs."""

from __future__ import annotations

from PyQt6.QtCore import QByteArray, QSize, Qt
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
    "watchlist": '<path d="M5 6.5H19"/><path d="M5 12H15"/><path d="M5 17.5H13"/><path d="M17 14L18.2 16.4L21 16.8L19 18.8L19.5 21.6L17 20.3L14.5 21.6L15 18.8L13 16.8L15.8 16.4Z"/>',
    "trophy": '<path d="M8 5H16V9.5C16 12 14.2 14 12 14C9.8 14 8 12 8 9.5Z"/><path d="M8 7H5.5C5.5 10 6.8 11.4 9 11.6"/><path d="M16 7H18.5C18.5 10 17.2 11.4 15 11.6"/><path d="M12 14V18"/><path d="M8.5 20H15.5"/>',
    "globe": '<path d="M12 4A8 8 0 1 1 12 20A8 8 0 0 1 12 4Z"/><path d="M4.8 9H19.2M4.8 15H19.2"/><path d="M12 4C14 6.2 15 8.8 15 12C15 15.2 14 17.8 12 20"/><path d="M12 4C10 6.2 9 8.8 9 12C9 15.2 10 17.8 12 20"/>',
    "jet": '<path d="M4 14L20 5L15 20L11.8 13.2Z"/><path d="M11.8 13.2L20 5"/><path d="M4 14L10 16"/>',
    "spark": '<path d="M12 4L13.7 9.9L19.5 12L13.7 14.1L12 20L10.3 14.1L4.5 12L10.3 9.9Z"/>',
    "cpu": '<path d="M8 8H16V16H8Z"/><path d="M10 4V7M14 4V7M10 17V20M14 17V20M4 10H7M4 14H7M17 10H20M17 14H20"/>',
    "radar": '<path d="M4 12A8 8 0 0 1 20 12"/><path d="M7 12A5 5 0 0 1 17 12"/><path d="M10 12A2 2 0 0 1 14 12"/><path d="M12 12L18 6"/><path d="M12 12V20"/>',
    "scan": '<path d="M5 8V5H8"/><path d="M16 5H19V8"/><path d="M19 16V19H16"/><path d="M8 19H5V16"/><path d="M7 12H17"/>',
    "blocks": '<path d="M5 5H11V11H5Z"/><path d="M13 5H19V11H13Z"/><path d="M5 13H11V19H5Z"/><path d="M13 13H19V19H13Z"/>',
    "calendar": '<path d="M6 5H18C19.1 5 20 5.9 20 7V18C20 19.1 19.1 20 18 20H6C4.9 20 4 19.1 4 18V7C4 5.9 4.9 5 6 5Z"/><path d="M8 3V7M16 3V7M4 10H20"/>',
    "wallet": '<path d="M5 7H18C19.1 7 20 7.9 20 9V18C20 19.1 19.1 20 18 20H5C3.9 20 3 19.1 3 18V7.8C3 6.8 3.8 6 4.8 6H17"/><path d="M15 13H20"/><path d="M16.8 15.2H17"/>',
    "log": '<path d="M6 4H18V20H6Z"/><path d="M9 8H15M9 12H15M9 16H13"/>',
}


_TAB_ICON_BY_KEY = {
    "watchlist": "watchlist",
    "lhb": "trophy",
    "asian_market": "globe",
    "na_daily": "jet",
    "stock_candidates": "spark",
    "ai_industry_chain": "cpu",
    "rt_monitor": "radar",
    "scan": "scan",
    "foreign_block": "blocks",
    "earnings": "calendar",
    "fund_holdings": "wallet",
    "system_log": "log",
}

_TAB_ICON_BY_LABEL = {
    "关注池": "watchlist",
    "龙虎榜": "trophy",
    "亚洲寡头": "globe",
    "北美战报": "jet",
    "综合候选": "spark",
    "AI产业链": "cpu",
    "盘中监控": "radar",
    "VCP扫描": "scan",
    "大宗交易": "blocks",
    "业绩异动": "calendar",
    "基金持仓": "wallet",
    "系统日志": "log",
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


def set_button_svg_icon(button, name: str, color: str, *, size: int = 18, stroke_width: float = 1.55) -> None:
    button.setText("")
    button.setIcon(svg_icon(name, color, size=size, stroke_width=stroke_width))
    value = max(8, int(size or 18))
    button.setIconSize(QSize(value, value))


def tab_icon_name(key: str = "", label: str = "") -> str:
    key_text = str(key or "").strip()
    if key_text in _TAB_ICON_BY_KEY:
        return _TAB_ICON_BY_KEY[key_text]
    return _TAB_ICON_BY_LABEL.get(str(label or "").strip(), "spark")


def tab_svg_icon(key: str = "", label: str = "", color: str = "", *, size: int = 16, stroke_width: float = 1.40) -> QIcon:
    return svg_icon(tab_icon_name(key, label), color, size=size, stroke_width=stroke_width)
