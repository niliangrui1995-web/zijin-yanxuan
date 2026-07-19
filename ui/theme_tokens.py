# -*- coding: utf-8 -*-
"""统一视觉 token 层。

把主题色、字号、圆角、间距、控件密度、表格密度和状态色统一收口到这里，
避免每个组件自己写一套尺寸和状态映射。
"""

from __future__ import annotations

from ui.theme import theme_manager

_UI_TOKEN_CACHE_MAX_SIZE = 16
_UI_TOKEN_CACHE: dict[tuple[int, str, tuple[tuple[str, str], ...]], dict] = {}


def _theme_cache_signature(theme: dict) -> tuple[tuple[str, str], ...]:
    return tuple(sorted((str(key), repr(value)) for key, value in theme.items()))


def _ui_token_cache_key(theme: dict, density: str) -> tuple[int, str, tuple[tuple[str, str], ...]]:
    return (id(theme), density, _theme_cache_signature(theme))


def invalidate_ui_token_cache() -> None:
    _UI_TOKEN_CACHE.clear()


def _normalize_density(density: str | None = None) -> str:
    from app.services.ui_config_service import app_config

    mode = density or app_config.table_density
    return mode if mode in ("紧凑", "舒适") else "舒适"


def _is_dark_theme(theme: dict) -> bool:
    return theme.get("appearance") == "dark"


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
        "info": tone(
            "COLOR_INFO",
            fg=theme.get("INFO_BADGE_FG", theme["TEXT_PRIMARY"] if is_dark else theme["COLOR_INFO"]),
            bg_alpha=0.08,
            border_alpha=0.18,
        ),
        "loading": tone(
            "COLOR_INFO",
            fg=theme.get("INFO_BADGE_FG", theme["TEXT_PRIMARY"] if is_dark else theme["COLOR_INFO"]),
            bg_alpha=0.08,
            border_alpha=0.20,
        ),
        "success": tone("COLOR_SUCCESS", bg_alpha=0.12, border_alpha=0.22),
        "warning": tone("COLOR_WARNING", bg_alpha=0.12, border_alpha=0.22),
        "error": tone("COLOR_ERROR", bg_alpha=0.12, border_alpha=0.22),
        "cached": tone(
            "COLOR_INFO",
            fg=theme.get("INFO_BADGE_FG", theme["TEXT_PRIMARY"] if is_dark else theme["COLOR_INFO"]),
            bg_alpha=0.08,
            border_alpha=0.18,
        ),
        "realtime": tone("COLOR_REALTIME", fg=theme["COLOR_REALTIME"], bg_alpha=0.10, border_alpha=0.24),
        "stale": tone("COLOR_WARNING", fg=theme["COLOR_WARNING"], bg_alpha=0.08, border_alpha=0.18),
        "focus": tone("COLOR_INFO", fg=theme["COLOR_INFO"], bg_alpha=0.10, border_alpha=0.28),
        "offline": {
            "bg": _hex_to_rgba(theme["TEXT_MUTED"], 0.10) if is_dark else theme["BG_ELEVATED"],
            "fg": theme["TEXT_SECONDARY"],
            "border": neutral_border,
        },
    }


def _build_font_tokens() -> dict:
    return {
        "family": '"Microsoft YaHei UI", "Microsoft YaHei", "Segoe UI", sans-serif',
        "family_names": ["Microsoft YaHei UI", "Microsoft YaHei", "Segoe UI", "SimSun"],
        "mono_family": (
            '"JetBrains Mono", "Cascadia Mono", "Consolas", "Segoe UI Historic", '
            '"Microsoft YaHei UI", "Microsoft YaHei", "Segoe UI", monospace'
        ),
        "mono_family_names": [
            "JetBrains Mono",
            "Cascadia Mono",
            "Consolas",
            "Segoe UI Historic",
            "Microsoft YaHei UI",
            "Microsoft YaHei",
            "Segoe UI",
            "SimSun",
        ],
        "size_xs": 11,
        "size_sm": 12,
        "size_md": 13,
        "size_lg": 14,
        "size_xl": 16,
        "weight_medium": 500,
        "weight_semibold": 600,
        "weight_bold": 700,
    }


def _build_radius_tokens() -> dict:
    return {
        "xs": 4,
        "sm": 6,
        "md": 8,
        "lg": 14,
        "xl": 16,
        "pill": 14,
    }


def _build_space_tokens(*, compact: bool) -> dict:
    return {
        "2xs": 4,
        "xs": 6,
        "sm": 8,
        "md": 10 if compact else 14,
        "lg": 12 if compact else 14,
        "xl": 14 if compact else 16,
        "2xl": 16 if compact else 20,
    }


def _build_control_tokens(*, compact: bool) -> dict:
    return {
        "button_height": 30 if compact else 36,
        "input_height": 30 if compact else 34,
        "toolbar_button_height": 32 if compact else 38,
        "button_padding_x": 12 if compact else 16,
        "toolbar_chip_height": 20 if compact else 24,
        "toolbar_chip_padding_x": 8 if compact else 12,
        "tab_padding_y": 3 if compact else 5,
        "tab_padding_x": 9 if compact else 13,
        "segment_height": 26 if compact else 30,
    }


def _build_table_tokens(theme: dict, radius: dict, *, compact: bool, is_dark: bool) -> dict:
    return {
        "row_height_base": 32,
        "row_height_delta": 4,
        "cell_padding_y": 4 if compact else 8,
        "cell_padding_x": 10 if compact else 12,
        "header_padding_y": 3 if compact else 5,
        "header_padding_x": 9 if compact else 12,
        "header_font_size": 11 if compact else 12,
        "header_min_height": 28 if compact else 30,
        "focus_radius": radius["xs"],
        "selected_bg": theme.get("TABLE_SELECTION_BG", theme["SELECTION_BG"]),
        "selected_hover_bg": theme.get("TABLE_SELECTION_HOVER_BG", theme["SELECTION_HOVER_BG"]),
        "selected_rail_width": 3 if compact else 4,
        "selected_rail_color": theme.get("TABLE_SELECTED_RAIL", theme["BRAND_PRIMARY"]),
        "hover_rail_width": 3,
        "hover_rail_color": theme.get("TABLE_HOVER_RAIL", theme.get("ACCENT_PRIMARY", theme["COLOR_INFO"])),
        "current_cell_bg": theme.get(
            "TABLE_CURRENT_CELL_BG", _hex_to_rgba(theme["BRAND_PRIMARY"], 0.10 if is_dark else 0.06)
        ),
        "current_cell_bg_selected": theme.get(
            "TABLE_CURRENT_CELL_BG_SELECTED", _hex_to_rgba(theme["BRAND_PRIMARY"], 0.16 if is_dark else 0.10)
        ),
        "current_cell_border": theme.get("TABLE_CURRENT_CELL_BORDER", theme["BRAND_DEEP"]),
        "sorted_column_bg": _hex_to_rgba(theme["COLOR_INFO"], 0.08 if is_dark else 0.06),
        "sorted_header_bg": _hex_to_rgba(theme["COLOR_INFO"], 0.14 if is_dark else 0.08),
        "status_pill_radius": radius["sm"],
        "numeric_heat_max_alpha": 40 if is_dark else 32,
        "accent_rail_width": 3,
        "accent_rail_alpha": 210 if is_dark else 180,
        "accent_rail_bg_alpha": 28 if is_dark else 22,
        "flash_alpha_scale": float(theme.get("TABLE_FLASH_ALPHA_SCALE", 0.24)),
        "flash_max_alpha": int(theme.get("TABLE_FLASH_MAX_ALPHA", 76)),
        "flash_duration_ms": 500,
        "flash_rail_width": 3,
        "flash_rail_alpha": int(theme.get("TABLE_FLASH_RAIL_ALPHA", 120 if is_dark else 105)),
    }


def _build_shell_tokens(theme: dict, radius: dict, *, compact: bool, is_dark: bool) -> dict:
    return {
        "titlebar_height": 38 if compact else 42,
        "window_button_width": 44 if compact else 48,
        "system_button_width": 44 if compact else 48,
        "status_height": 32 if compact else 36,
        "status_pill_min_height": 24 if compact else 28,
        "status_pill_padding_x": 10 if compact else 12,
        "toolbar_min_height": 46 if compact else 56,
        "toolbar_padding_x": 8 if compact else 12,
        "toolbar_padding_y": 6 if compact else 8,
        "toolbar_section_gap": 6 if compact else 10,
        "toolbar_group_gap": 3 if compact else 6,
        "toolbar_card_radius": radius["lg"],
        "tabbar_height": 26 if compact else 30,
        "inner_border_color": "rgba(255, 255, 255, 0.08)" if is_dark else "rgba(15, 23, 42, 0.08)",
        "window_shadow_blur": 30 if compact else 36,
        "window_shadow_offset_y": 10 if compact else 12,
        "window_shadow_alpha": 0.45 if is_dark else 0.20,
    }


def build_ui_tokens(theme: dict | None = None, density: str | None = None) -> dict:
    """构建统一 UI token。

    density 当前复用“表格密度”设置，后续可以自然扩展成全局视觉密度模式。
    """

    theme = theme or theme_manager.current_theme
    mode = _normalize_density(density)
    cache_key = _ui_token_cache_key(theme, mode)
    cached = _UI_TOKEN_CACHE.get(cache_key)
    if cached is not None:
        return cached

    compact = mode == "紧凑"
    is_dark = _is_dark_theme(theme)

    font = _build_font_tokens()
    radius = _build_radius_tokens()
    space = _build_space_tokens(compact=compact)
    control = _build_control_tokens(compact=compact)

    table = _build_table_tokens(theme, radius, compact=compact, is_dark=is_dark)
    shell = _build_shell_tokens(theme, radius, compact=compact, is_dark=is_dark)

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
        "toolbar": theme.get("BG_TOOLBAR", theme["BG_ELEVATED"] if is_dark else theme["BG_CARD"]),
        "toolbar_card": theme.get(
            "BG_TOOLBAR_CARD", theme["BG_CARD"] if is_dark else _hex_to_rgba(theme["TEXT_PRIMARY"], 0.02)
        ),
        "toolbar_chip": theme.get(
            "BG_TOOLBAR_CHIP", theme["BG_BUTTON"] if is_dark else _hex_to_rgba(theme["TEXT_PRIMARY"], 0.05)
        ),
        "panel_alt": theme["BG_TABLE_ALT_ROW"],
        "overlay": _hex_to_rgba(theme["BG_ELEVATED"], 0.94 if is_dark else 0.98),
        "chart_panel": theme["BG_CARD"],
        "selection": theme["SELECTION_BG"],
        "selection_hover": theme["SELECTION_HOVER_BG"],
    }

    border = {
        "default": theme["BORDER_DEFAULT"],
        "subtle": theme["BORDER_SUBTLE"],
        "strong": theme["BORDER_STRONG"],
        "accent": theme["BORDER_BRAND"],
        "focus": theme.get("FOCUS_RING") or _hex_to_rgba(theme["COLOR_INFO"], 0.32 if is_dark else 0.24),
    }

    motion = {
        "fast": 120,
        "base": 180,
        "slow": 260,
        "pulse": 1500,
        "quote_pulse_flash": 80,
        "quote_pulse_decay": 200,
    }

    skeleton = {
        "base": _hex_to_rgba(theme["TEXT_PRIMARY"], 0.08 if is_dark else 0.06),
        "shine": _hex_to_rgba(theme.get("ACCENT_PRIMARY", theme["COLOR_INFO"]), 0.22 if is_dark else 0.14),
        "line": _hex_to_rgba(theme.get("ACCENT_PRIMARY", theme["COLOR_INFO"]), 0.12 if is_dark else 0.08),
        "duration": 1400,
        "row_height": 12,
        "row_gap": 10,
        "radius": radius["xs"],
    }

    icon = {
        "chrome_size": 16,
        "status_size": 16,
        "stroke_width": 1.40,
        "muted": theme["TEXT_MUTED"],
        "strong": theme["TEXT_PRIMARY"],
    }

    status_glyph = {
        "online": {
            "shape": "circle",
            "symbol": "check",
            "color": theme.get("NETWORK_ONLINE", theme["COLOR_REALTIME"]),
        },
        "busy": {
            "shape": "hexagon",
            "symbol": "hourglass",
            "color": theme.get("NETWORK_BUSY", theme["COLOR_WARNING"]),
        },
        "offline": {
            "shape": "triangle",
            "symbol": "alert",
            "color": theme.get("NETWORK_OFFLINE", theme["COLOR_ERROR"]),
        },
    }

    z_index = {
        "base": 0,
        "toolbar": 10,
        "overlay": 20,
        "menu": 30,
        "modal": 40,
    }

    chart = {
        "panel_bg": surface["chart_panel"],
        "toolbar_bg": surface["toolbar"],
        "grid_line": theme["KLINE_GRID_LINE"],
        "axis_line": theme["KLINE_AXIS_LINE"],
        "axis_label": theme["KLINE_AXIS_LABEL"],
        "crosshair_bg": theme["KLINE_POINTER_BG"],
        "vcp_star": theme["KLINE_VCP_STAR"],
    }

    calendar = {
        "cell_fill": surface["input"],
        "selected_color": theme.get("ACCENT_PRIMARY", theme["COLOR_INFO"]),
        "today_color": theme["COLOR_INFO"],
        "non_trade_color": theme["COLOR_ERROR"],
        "selected_bg_alpha": 42 if is_dark else 28,
        "selected_border_alpha": 184 if is_dark else 150,
        "today_bg_alpha": 16 if is_dark else 10,
        "today_border_alpha": 148 if is_dark else 116,
        "today_dot_alpha": 220 if is_dark else 180,
        "non_trade_text_alpha": 208 if is_dark else 180,
        "marker_super_giant": theme.get("CALENDAR_SUPER_GIANT_MARKER", theme["COLOR_WARNING"]),
        "marker_strategic_giant": theme.get(
            "CALENDAR_STRATEGIC_GIANT_MARKER", theme.get("ACCENT_PRIMARY", theme["COLOR_INFO"])
        ),
        "marker_normal": theme.get("CALENDAR_NORMAL_MARKER", theme["COLOR_INFO"]),
        "marker_super_giant_alpha": 230 if is_dark else 214,
        "marker_strategic_giant_alpha": 220 if is_dark else 204,
        "marker_normal_alpha": 205 if is_dark else 192,
    }

    tooltip = {
        "bg": theme.get("TOOLTIP_BG", "rgba(15, 18, 30, 0.95)" if is_dark else "rgba(255, 255, 255, 0.98)"),
        "border": theme.get("TOOLTIP_BORDER", "rgba(255, 255, 255, 0.16)" if is_dark else "rgba(15, 23, 42, 0.12)"),
        "text": theme.get("TOOLTIP_TEXT", theme.get("TEXT_BRIGHT", theme["TEXT_PRIMARY"]) if is_dark else theme["TEXT_PRIMARY"]),
        "shadow": theme.get("TOOLTIP_SHADOW", "rgba(0, 0, 0, 0.26)" if is_dark else "rgba(15, 23, 42, 0.14)"),
        "radius": radius["md"],
        "padding_x": 18,
        "padding_y": 14,
        "font_size": font["size_xl"],
        "max_width": 560,
        "offset_x": 14,
        "offset_y": 18,
        "margin": 6,
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

    tokens = {
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
        "motion": motion,
        "skeleton": skeleton,
        "icon": icon,
        "status_glyph": status_glyph,
        "z_index": z_index,
        "chart": chart,
        "calendar": calendar,
        "tooltip": tooltip,
        "text": text,
        "state": _build_state_tones(theme, is_dark=is_dark),
    }
    if len(_UI_TOKEN_CACHE) >= _UI_TOKEN_CACHE_MAX_SIZE and cache_key not in _UI_TOKEN_CACHE:
        _UI_TOKEN_CACHE.clear()
    _UI_TOKEN_CACHE[cache_key] = tokens
    return tokens


def get_state_tone(tone: str, theme: dict | None = None, density: str | None = None) -> dict:
    tokens = build_ui_tokens(theme, density)
    return tokens["state"].get(tone, tokens["state"]["neutral"])


theme_manager.sig_theme_changed.connect(invalidate_ui_token_cache)
