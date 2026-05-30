# -*- coding: utf-8 -*-
"""Custom translucent tooltip popup used instead of native tooltip rendering."""

from __future__ import annotations

import re

from PyQt6.QtCore import QPoint, QRect, QSize, Qt
from PyQt6.QtGui import QColor, QFont, QPainter, QPen
from PyQt6.QtWidgets import QApplication, QFrame, QLabel, QVBoxLayout, QWidget

from ui.theme import theme_manager
from ui.theme_tokens import build_ui_tokens

_TOOLTIP_MARGIN = 6
_MAX_TEXT_WIDTH = 560
_floating_tooltip: "FloatingToolTip | None" = None


def _qcolor_from_css(value: str | QColor, fallback: str = "#111827") -> QColor:
    if isinstance(value, QColor):
        return QColor(value)

    text = str(value or "").strip()
    if not text:
        return QColor(fallback)

    rgba_match = re.fullmatch(
        r"rgba\(\s*(\d{1,3})\s*,\s*(\d{1,3})\s*,\s*(\d{1,3})\s*,\s*([0-9]*\.?[0-9]+)\s*\)",
        text,
        re.IGNORECASE,
    )
    if rgba_match:
        red, green, blue = (int(rgba_match.group(index)) for index in range(1, 4))
        alpha_raw = float(rgba_match.group(4))
        alpha = int(round(alpha_raw * 255)) if alpha_raw <= 1 else int(round(alpha_raw))
        return QColor(red, green, blue, max(0, min(255, alpha)))

    rgb_match = re.fullmatch(
        r"rgb\(\s*(\d{1,3})\s*,\s*(\d{1,3})\s*,\s*(\d{1,3})\s*\)",
        text,
        re.IGNORECASE,
    )
    if rgb_match:
        return QColor(*(int(rgb_match.group(index)) for index in range(1, 4)))

    color = QColor(text)
    return color if color.isValid() else QColor(fallback)


class FloatingToolTip(QFrame):
    def __init__(self, parent: QWidget | None = None):
        super().__init__(
            parent,
            Qt.WindowType.ToolTip
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.NoDropShadowWindowHint,
        )
        self.setObjectName("floatingTooltip")
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setAutoFillBackground(False)

        self._radius = 10
        self._fill_color = QColor(15, 18, 30, 242)
        self._border_color = QColor(255, 255, 255, 42)
        self._shadow_color = QColor(0, 0, 0, 54)

        self._label = QLabel(self)
        self._label.setObjectName("floatingTooltipLabel")
        self._label.setWordWrap(True)
        self._label.setMaximumWidth(_MAX_TEXT_WIDTH)
        self._label.setTextInteractionFlags(Qt.TextInteractionFlag.NoTextInteraction)
        self._label.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 14, 18, 14)
        layout.setSpacing(0)
        layout.addWidget(self._label)
        self.apply_theme()

    def apply_theme(self) -> None:
        tokens = build_ui_tokens(theme_manager.current_theme)
        tooltip = tokens["tooltip"]

        self._fill_color = _qcolor_from_css(tooltip["bg"])
        self._border_color = _qcolor_from_css(tooltip["border"])
        self._shadow_color = _qcolor_from_css(tooltip["shadow"], fallback="#000000")
        self._radius = int(tooltip["radius"])
        self._label.setMaximumWidth(int(tooltip["max_width"]))
        self.layout().setContentsMargins(
            int(tooltip["padding_x"]),
            int(tooltip["padding_y"]),
            int(tooltip["padding_x"]),
            int(tooltip["padding_y"]),
        )

        font = QFont()
        font.setFamilies(tokens["font"].get("family_names") or ["Microsoft YaHei UI", "Segoe UI"])
        font.setPointSize(int(tooltip["font_size"]))
        self._label.setFont(font)
        self._label.setStyleSheet(
            "QLabel#floatingTooltipLabel {"
            " background: transparent;"
            f" color: {tooltip['text']};"
            f" font-size: {int(tooltip['font_size'])}px;"
            f" font-weight: {tokens['font']['weight_medium']};"
            " border: none;"
            " padding: 0px;"
            "}"
        )
        self._apply_text_width()
        self.update()

    def set_text(self, text: str, *, rich_text: bool | None = None) -> None:
        value = str(text or "").strip()
        if rich_text is None:
            rich_text = value.lower().startswith("<qt")
        self._label.setTextFormat(Qt.TextFormat.RichText if rich_text else Qt.TextFormat.PlainText)
        self._label.setText(value)
        self._apply_text_width()

    def _apply_text_width(self) -> None:
        tokens = build_ui_tokens(theme_manager.current_theme)
        max_width = int(tokens["tooltip"]["max_width"])
        self._label.setMinimumWidth(0)
        self._label.setMaximumWidth(max_width)
        if self._label.textFormat() == Qt.TextFormat.RichText:
            return

        text = self._label.text()
        if not text:
            return
        longest_line = max((self._label.fontMetrics().horizontalAdvance(line) for line in text.splitlines()), default=0)
        if longest_line > max_width:
            self._label.setMinimumWidth(max_width)

    def show_at(self, global_pos: QPoint) -> None:
        self.apply_theme()
        self.adjustSize()
        hint = self.sizeHint()
        self.resize(hint)
        self.move(self._clamped_position(global_pos, hint))
        self.show()
        self.raise_()

    def _clamped_position(self, global_pos: QPoint, hint: QSize) -> QPoint:
        tooltip = build_ui_tokens(theme_manager.current_theme)["tooltip"]
        offset = QPoint(int(tooltip["offset_x"]), int(tooltip["offset_y"]))
        anchor = QPoint(global_pos) + offset
        screen = QApplication.screenAt(global_pos) or QApplication.primaryScreen()
        if screen is None:
            return anchor

        bounds = screen.availableGeometry().adjusted(4, 4, -4, -4)
        x = min(max(anchor.x(), bounds.left()), max(bounds.left(), bounds.right() - hint.width()))
        y = anchor.y()
        if y + hint.height() > bounds.bottom():
            y = global_pos.y() - hint.height() - offset.y()
        y = min(max(y, bounds.top()), max(bounds.top(), bounds.bottom() - hint.height()))
        return QPoint(x, y)

    def paintEvent(self, event) -> None:
        tooltip = build_ui_tokens(theme_manager.current_theme)["tooltip"]
        margin = int(tooltip.get("margin", _TOOLTIP_MARGIN))
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)

        rect = QRect(self.rect()).adjusted(margin, margin, -margin, -margin)
        shadow_alpha = max(1, self._shadow_color.alpha())
        for spread, alpha_scale in ((4, 0.34), (2, 0.50), (1, 0.66)):
            shadow = QColor(self._shadow_color)
            shadow.setAlpha(max(1, min(255, int(shadow_alpha * alpha_scale))))
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(shadow)
            painter.drawRoundedRect(
                rect.adjusted(-spread, -spread, spread, spread),
                self._radius + spread,
                self._radius + spread,
            )

        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(self._fill_color)
        painter.drawRoundedRect(rect, self._radius, self._radius)

        painter.setPen(QPen(self._border_color, 1))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRoundedRect(rect.adjusted(0, 0, -1, -1), self._radius, self._radius)
        painter.end()
        super().paintEvent(event)


def _tooltip_instance() -> FloatingToolTip | None:
    global _floating_tooltip

    app = QApplication.instance()
    if app is None:
        return None
    if _floating_tooltip is None:
        _floating_tooltip = FloatingToolTip()
        app.aboutToQuit.connect(hide_floating_tooltip)
    return _floating_tooltip


def show_floating_tooltip(
    text: str,
    global_pos: QPoint,
    *,
    owner: QWidget | None = None,
    rich_text: bool | None = None,
) -> bool:
    value = str(text or "").strip()
    if not value:
        hide_floating_tooltip()
        return False

    tooltip = _tooltip_instance()
    if tooltip is None:
        return False

    tooltip.set_text(value, rich_text=rich_text)
    tooltip.show_at(QPoint(global_pos))
    return True


def hide_floating_tooltip() -> None:
    if _floating_tooltip is not None:
        try:
            _floating_tooltip.hide()
        except RuntimeError:
            pass


def current_floating_tooltip() -> FloatingToolTip | None:
    return _floating_tooltip
