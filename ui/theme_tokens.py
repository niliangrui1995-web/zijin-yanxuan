# -*- coding: utf-8 -*-
"""统一视觉 token 层。

把主题色、字号、圆角、间距、控件密度、表格密度和状态色统一收口到这里，
避免每个组件自己写一套尺寸和状态映射。
"""

from __future__ import annotations

from ui.theme import theme_manager


def _normalize_density(density: str | None = None) -> str:
    from core.app_config import app_config

    mode = density or app_config.table_density
    return mode if mode in ("紧凑", "舒适") else "舒适"


def _is_dark_theme(theme: dict) -> bool:
    return theme.get("name") == "墨渊"


def _hex_to_rgba(color: str, alpha: float) -> str:
    if not isinstance(color, str):
        return str(color)
    color = color.strip()
    if not color.startswith("#"):
        return color

    raw = color.lstrip("#")
    if len(raw) == 3:
        raw = "".join(ch * 2 for ch in raw)
    if len(raw) != 6:
        return color

    r = int(raw[0:2], 16)
    g = int(raw[2:4], 16)
    b = int(raw[4:6], 16)
    return f"rgba({r}, {g}, {b}, {alpha:.2f})"


def _build_state_tones(theme: dict, *, is_dark: bool) -> dict:
    neutral_bg = theme["BG_BUTTON"] if is_dark else theme["BG_ELEVATED"]
    neutral_border = theme["BORDER_DEFAULT"]

    def tone(color_key: str, *, fg: str | None = None, bg_alpha: float = 0.12, border_alpha: float = 0.20) -> dict:
        color = theme[color_key]
        return {
            "bg": _hex_to_rgba(color, bg_alpha),
            "fg": fg or color,
            "border": _hex_to_rgba(color, border_alpha),
        }

    return {
        "neutral": {
            "bg": neutral_bg,
            "fg": theme["TEXT_SECONDARY"],
            "border": neutral_border,
        },
        "info": tone("COLOR_INFO", fg=theme["TEXT_PRIMARY"] if is_dark else theme["COLOR_INFO"], bg_alpha=0.10, border_alpha=0.18),
        "loading": tone("COLOR_WARNING", bg_alpha=0.12, border_alpha=0.22),
        "success": tone("COLOR_SUCCESS", bg_alpha=0.12, border_alpha=0.22),
        "warning": tone("COLOR_WARNING", bg_alpha=0.12, border_alpha=0.22),
        "error": tone("COLOR_ERROR", bg_alpha=0.12, border_alpha=0.22),
        "offline": {
            "bg": _hex_to_rgba(theme["TEXT_MUTED"], 0.10) if is_dark else theme["BG_ELEVATED"],
            "fg": theme["TEXT_SECONDARY"],
            "border": neutral_border,
        },
    }


def build_ui_tokens(theme: dict | None = None, density: str | None = None) -> dict:
    """构建统一 UI token。

    density 当前复用“表格密度”设置，后续可以自然扩展成全局视觉密度模式。
    """

    theme = theme or theme_manager.current_theme
    mode = _normalize_density(density)
    compact = mode == "紧凑"
    is_dark = _is_dark_theme(theme)

    font = {
        "family": '"Microsoft YaHei UI", "Segoe UI", sans-serif',
        "mono_family": '"Consolas", "Courier New", monospace',
        "size_xs": 11,
        "size_sm": 12,
        "size_md": 13,
        "size_lg": 14,
        "size_xl": 16,
        "weight_medium": 500,
        "weight_semibold": 600,
        "weight_bold": 700,
    }

    radius = {
        "xs": 4,
        "sm": 6,
        "md": 8,
        "lg": 12,
        "xl": 14,
        "pill": 12,
    }

    space = {
        "2xs": 4,
        "xs": 6,
        "sm": 8,
        "md": 10,
        "lg": 12,
        "xl": 14,
        "2xl": 16,
    }

    control = {
        "button_height": 30 if compact else 32,
        "input_height": 30 if compact else 32,
        "toolbar_button_height": 38 if compact else 40,
        "button_padding_x": 12 if compact else 14,
        "tab_padding_y": 7 if compact else 8,
        "tab_padding_x": 12 if compact else 16,
        "segment_height": 26 if compact else 28,
    }

    table = {
        "row_height_base": 30,
        "row_height_delta": 6,
        "cell_padding_y": 4 if compact else 7,
        "cell_padding_x": 10 if compact else 12,
        "header_padding_y": 5 if compact else 6,
        "header_padding_x": 9 if compact else 10,
        "header_font_size": 11 if compact else 12,
        "focus_radius": radius["xs"],
    }

    shell = {
        "titlebar_height": 38 if compact else 40,
        "window_button_width": 44 if compact else 46,
        "system_button_width": 44 if compact else 46,
        "status_height": 32 if compact else 34,
        "status_pill_min_height": 24 if compact else 26,
        "status_pill_padding_x": 10 if compact else 12,
    }

    surface = {
        "canvas": theme["BG_CANVAS"],
        "panel": theme["BG_CARD"],
        "elevated": theme["BG_ELEVATED"],
        "soft": theme["BG_BUTTON"],
        "hover": theme["BG_HOVER"],
        "glass": theme["BG_GLASS"],
        "chrome": theme["BG_TITLEBAR"],
        "statusbar": theme["BG_STATUSBAR"],
        "input": theme["BG_INPUT"],
        "selection": theme["SELECTION_BG"],
        "selection_hover": theme["SELECTION_HOVER_BG"],
    }

    border = {
        "default": theme["BORDER_DEFAULT"],
        "subtle": theme["BORDER_SUBTLE"],
        "strong": theme["BORDER_STRONG"],
        "accent": theme["BORDER_BRAND"],
    }

    text = {
        "primary": theme["TEXT_PRIMARY"],
        "secondary": theme["TEXT_SECONDARY"],
        "muted": theme["TEXT_MUTED"],
        "disabled": theme["TEXT_DISABLED"],
        "header": theme["TEXT_HEADER"],
        "bright": theme["TEXT_BRIGHT"],
        "accent": theme["TEXT_ON_ACCENT"],
    }

    return {
        "theme": theme,
        "density": mode,
        "is_dark": is_dark,
        "font": font,
        "radius": radius,
        "space": space,
        "control": control,
        "table": table,
        "shell": shell,
        "surface": surface,
        "border": border,
        "text": text,
        "state": _build_state_tones(theme, is_dark=is_dark),
    }


def get_state_tone(tone: str, theme: dict | None = None, density: str | None = None) -> dict:
    tokens = build_ui_tokens(theme, density)
    return tokens["state"].get(tone, tokens["state"]["neutral"])
