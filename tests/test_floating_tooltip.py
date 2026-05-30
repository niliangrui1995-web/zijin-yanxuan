# -*- coding: utf-8 -*-

from PyQt6.QtCore import QPoint, Qt
from PyQt6.QtGui import QImage

from ui.components.tooltip_popup import (
    FloatingToolTip,
    current_floating_tooltip,
    hide_floating_tooltip,
    show_floating_tooltip,
)
from ui.theme import theme_manager
from ui.theme_tokens import build_ui_tokens


def test_floating_tooltip_uses_translucent_frameless_popup(qt_application):
    tooltip = FloatingToolTip()
    try:
        assert tooltip.objectName() == "floatingTooltip"
        assert tooltip.testAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        assert tooltip.testAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        assert tooltip.testAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        assert tooltip.windowFlags() & Qt.WindowType.ToolTip
        assert tooltip.windowFlags() & Qt.WindowType.FramelessWindowHint
    finally:
        tooltip.deleteLater()


def test_floating_tooltip_keeps_outer_corners_transparent(qt_application):
    tooltip = FloatingToolTip()
    try:
        tooltip.set_text("corner transparency check", rich_text=False)
        tooltip.resize(220, 72)
        image = QImage(tooltip.size(), QImage.Format.Format_ARGB32_Premultiplied)
        image.fill(Qt.GlobalColor.transparent)

        tooltip.render(image)

        assert image.pixelColor(0, 0).alpha() == 0
        assert image.pixelColor(image.width() - 1, 0).alpha() == 0
        assert image.pixelColor(0, image.height() - 1).alpha() == 0
        assert image.pixelColor(image.width() - 1, image.height() - 1).alpha() == 0
    finally:
        tooltip.deleteLater()


def test_floating_tooltip_reapplies_theme_token_colors(qt_application):
    original_theme = theme_manager.current_theme_name
    dark_name = next(name for name, theme in theme_manager.THEMES.items() if theme.get("appearance") == "dark")
    light_name = next(name for name, theme in theme_manager.THEMES.items() if theme.get("appearance") == "light")
    tooltip = FloatingToolTip()

    try:
        theme_manager._current_name = dark_name
        tooltip.apply_theme()
        dark_fill = tooltip._fill_color.getRgb()
        dark_text_style = tooltip._label.styleSheet()

        theme_manager._current_name = light_name
        tooltip.apply_theme()
        light_fill = tooltip._fill_color.getRgb()
        light_text_style = tooltip._label.styleSheet()

        assert dark_fill != light_fill
        assert dark_text_style != light_text_style
    finally:
        theme_manager._current_name = original_theme
        tooltip.deleteLater()


def test_floating_tooltip_uses_theme_tooltip_font_size(qt_application):
    tooltip = FloatingToolTip()
    try:
        expected_size = build_ui_tokens()["tooltip"]["font_size"]

        assert tooltip._label.font().pointSize() == expected_size
        assert f"font-size: {expected_size}px;" in tooltip._label.styleSheet()
    finally:
        tooltip.deleteLater()


def test_show_floating_tooltip_reuses_custom_popup(qt_application):
    hide_floating_tooltip()

    assert show_floating_tooltip("hello tooltip", QPoint(30, 30), rich_text=False) is True
    tooltip = current_floating_tooltip()

    try:
        assert isinstance(tooltip, FloatingToolTip)
        assert tooltip.isVisible()
    finally:
        hide_floating_tooltip()
