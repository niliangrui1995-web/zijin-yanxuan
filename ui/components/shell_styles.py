# -*- coding: utf-8 -*-
from __future__ import annotations

from ui.theme_tokens import build_ui_tokens


def titlebar_shell_style(theme: dict) -> str:
    tokens = build_ui_tokens(theme)
    return f"""
        QWidget#customTitleBar {{
            background-color: {theme["BG_TITLEBAR"]};
            border-bottom: 1px solid {theme["TITLEBAR_BORDER"]};
        }}
        QLabel#titleBarBrand {{
            color: {theme.get("TITLEBAR_BRAND_TEXT", theme["BRAND_PRIMARY"])};
            font-size: {tokens["font"]["size_md"]}px;
            font-weight: {tokens["font"]["weight_bold"]};
            font-family: {tokens["font"]["family"]};
            background: transparent;
            padding-right: 6px;
        }}
        QFrame#titleBarSeparator {{
            color: {theme["BORDER_STRONG"]};
        }}
    """


def titlebar_button_style(theme: dict, color: str, hover_bg: str, *, font_size: int | None = None) -> str:
    tokens = build_ui_tokens(theme)
    font_size = font_size or tokens["font"]["size_sm"]
    return f"""
        QPushButton {{
            background: transparent;
            color: {color};
            border: none;
            font-size: {font_size}px;
            font-weight: bold;
            padding: 0 {tokens["space"]["xl"]}px;
            min-height: {tokens["shell"]["titlebar_height"]}px;
            max-height: {tokens["shell"]["titlebar_height"]}px;
        }}
        QPushButton:hover {{
            background-color: {hover_bg};
        }}
    """


def system_button_style(theme: dict, text_color: str, hover_bg: str) -> str:
    tokens = build_ui_tokens(theme)
    return f"""
        QToolButton {{
            background: transparent;
            color: {text_color};
            border: none;
            font-size: {max(16, tokens["font"]["size_md"])}px;
            font-family: "Segoe UI Emoji", "Segoe UI Symbol", "Microsoft YaHei UI";
            font-weight: {tokens["font"]["weight_medium"]};
            padding: 0;
            min-height: {tokens["shell"]["titlebar_height"]}px;
            max-height: {tokens["shell"]["titlebar_height"]}px;
        }}
        QToolButton:hover {{
            background-color: {hover_bg};
        }}
        QToolButton::menu-indicator {{
            image: none;
            width: 0px;
        }}
    """


def standalone_tabbar_qss(theme: dict, *, compact: bool = False) -> str:
    tokens = build_ui_tokens(theme)
    tab_gap = 2 if compact else max(3, tokens["shell"]["toolbar_group_gap"])
    tab_padding_x = max(12, tokens["control"]["tab_padding_x"] + (0 if compact else 2))
    tab_radius = max(8, tokens["radius"]["md"])
    surface = tokens["surface"]
    border = tokens["border"]
    tab_active_border = theme.get("TAB_ACTIVE_BORDER", theme["BORDER_BRAND"])
    tab_active_top = theme.get("TAB_ACTIVE_TOP", "transparent")
    tab_active_indicator = theme.get("TAB_ACTIVE_INDICATOR", tab_active_top)
    if theme.get("TAB_ACTIVE_INDICATOR_SIDE") == "bottom":
        tab_active_indicator_rule = (
            f"border-top: 1px solid {tab_active_border};\n"
            f"            border-bottom: 2px solid {tab_active_indicator};"
        )
    else:
        tab_active_indicator_rule = f"border-top: 2px solid {tab_active_top};"
    return f"""
        QTabBar {{
            background: transparent;
            border: none;
        }}
        QTabBar::tab {{
            background: {surface["toolbar_chip"]};
            color: {theme["TAB_TEXT"]};
            padding: {tokens["control"]["tab_padding_y"]}px {tab_padding_x}px;
            margin: 0 {tab_gap}px 0 0;
            border: 1px solid {border["subtle"]};
            border-top: 2px solid transparent;
            font-size: {tokens["font"]["size_sm"]}px;
            font-weight: {tokens["font"]["weight_semibold"]};
            min-height: {tokens["shell"]["tabbar_height"]}px;
            min-width: 44px;
            icon-size: {tokens["icon"]["chrome_size"]}px;
            border-radius: {tab_radius}px;
            font-family: {tokens["font"]["family"]};
        }}
        QTabBar QToolButton {{
            background: {surface["toolbar_chip"]};
            color: {theme["TEXT_SECONDARY"]};
            border: 1px solid {border["subtle"]};
            border-radius: {tokens["radius"]["sm"]}px;
            min-width: 18px;
            max-width: 18px;
            margin: 1px 1px;
        }}
        QTabBar QToolButton:hover {{
            color: {theme["TEXT_PRIMARY"]};
            border-color: {border["strong"]};
            background: {theme["BG_HOVER"]};
        }}
        QTabBar::tab:selected {{
            color: {theme.get("TAB_ACTIVE_TEXT", theme["TEXT_PRIMARY"])};
            background: {theme.get("TAB_ACTIVE_BG", theme["BRAND_SUBTLE"])};
            border-color: {tab_active_border};
            {tab_active_indicator_rule}
        }}
        QTabBar::tab:hover:!selected {{
            color: {theme["TAB_TEXT_HOVER"]};
            background: {theme["TAB_HOVER_BG"]};
            border-color: {border["strong"]};
        }}
    """


def nav_group_button_qss(theme: dict) -> str:
    tokens = build_ui_tokens(theme)
    surface = tokens["surface"]
    border = tokens["border"]
    return f"""
        QPushButton {{
            background: {surface["toolbar_chip"]};
            color: {theme["TEXT_SECONDARY"]};
            border: 1px solid {border["subtle"]};
            border-radius: {tokens["radius"]["pill"]}px;
            padding: 0 {tokens["space"]["lg"]}px;
            min-height: {tokens["control"]["segment_height"]}px;
            font-size: {tokens["font"]["size_sm"]}px;
            font-weight: {tokens["font"]["weight_semibold"]};
            outline: none;
        }}
        QPushButton:hover {{
            color: {theme["TEXT_PRIMARY"]};
            border-color: {border["strong"]};
            background: {theme["BG_HOVER"]};
        }}
        QPushButton:checked {{
            color: {theme.get("TAB_ACTIVE_TEXT", theme["TEXT_PRIMARY"])};
            border-color: {theme.get("TAB_ACTIVE_BORDER", theme["BORDER_BRAND"])};
            background: {theme.get("TAB_ACTIVE_BG", theme["BRAND_SUBTLE"])};
        }}
    """


def titlebar_sync_button_qss(theme: dict) -> str:
    tokens = build_ui_tokens(theme)
    primary_gradient_start = theme.get("PRIMARY_GRADIENT_START", theme["BRAND_PRIMARY"])
    primary_gradient_end = theme.get("PRIMARY_GRADIENT_END", theme.get("BRAND_PRESSED", theme["BRAND_DEEP"]))
    primary_hover_start = theme.get("PRIMARY_HOVER_GRADIENT_START", theme["BRAND_HOVER"])
    primary_hover_end = theme.get("PRIMARY_HOVER_GRADIENT_END", theme["BRAND_PRIMARY"])
    primary_button_text = theme.get("PRIMARY_BUTTON_TEXT", theme["TEXT_ON_ACCENT"])
    primary_button_border = theme.get("PRIMARY_BUTTON_BORDER", theme["BRAND_DEEP"])
    primary_button_pressed_bg = theme.get("PRIMARY_BUTTON_PRESSED_BG", theme.get("BRAND_PRESSED", theme["BRAND_DEEP"]))
    return f"""
        QPushButton {{
            background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                stop:0 {primary_gradient_start}, stop:1 {primary_gradient_end});
            color: {primary_button_text};
            border: 1px solid {primary_button_border};
            border-radius: {tokens["radius"]["pill"]}px;
            padding: 0 {tokens["space"]["xl"]}px;
            min-height: {tokens["control"]["toolbar_button_height"]}px;
            font-size: {tokens["font"]["size_sm"]}px;
            font-weight: {tokens["font"]["weight_bold"]};
            outline: none;
        }}
        QPushButton:hover {{
            background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                stop:0 {primary_hover_start}, stop:1 {primary_hover_end});
        }}
        QPushButton:pressed {{
            background: {primary_button_pressed_bg};
        }}
    """


def titlebar_secondary_button_qss(theme: dict) -> str:
    tokens = build_ui_tokens(theme)
    surface = tokens["surface"]
    border = tokens["border"]
    return f"""
        QPushButton {{
            background: {surface["toolbar_chip"]};
            color: {theme["TEXT_SECONDARY"]};
            border: 1px solid {border["subtle"]};
            border-radius: {tokens["radius"]["pill"]}px;
            padding: 0 {tokens["space"]["lg"]}px;
            min-height: {tokens["control"]["toolbar_button_height"]}px;
            font-size: {tokens["font"]["size_sm"]}px;
            font-weight: {tokens["font"]["weight_semibold"]};
            outline: none;
        }}
        QPushButton:hover {{
            background: {theme["BG_HOVER"]};
            color: {theme["TEXT_PRIMARY"]};
            border: 1px solid {border["focus"]};
        }}
        QPushButton:pressed {{
            background: {theme["BG_BUTTON_HOVER"]};
        }}
    """
