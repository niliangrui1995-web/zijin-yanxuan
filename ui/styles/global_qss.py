# -*- coding: utf-8 -*-
"""紫金研选 — 全局 QSS 样式表（动态化主题版）

根据当前激活的主题 token 字典动态生成 QSS 字符串。
为什么动态生成而不是硬编码？因为用户可以切换主题，
QSS 必须跟着变——就像换一套衣服，每件都要配套。
"""

from ui.styles.global_qss_sections import (
    build_button_qss,
    build_dialog_log_scrollbar_qss,
    build_form_progress_qss,
    build_toolbar_status_qss,
)
from ui.theme import theme_manager
from ui.theme_tokens import _theme_cache_signature, build_ui_tokens, invalidate_ui_token_cache

_GLOBAL_QSS_CACHE_MAX_SIZE = 8
_GLOBAL_QSS_CACHE: dict[tuple[int, str, tuple[tuple[str, str], ...]], str] = {}


def _global_qss_cache_key(theme: dict, density: str) -> tuple[int, str, tuple[tuple[str, str], ...]]:
    return (id(theme), density, _theme_cache_signature(theme))


def invalidate_global_qss_cache() -> None:
    _GLOBAL_QSS_CACHE.clear()
    invalidate_ui_token_cache()


def _global_qss_tokens(theme: dict | None = None, density: str | None = None) -> tuple:
    if theme is None:
        theme = theme_manager.current_theme
    t = theme
    ui = build_ui_tokens(theme, density)
    font = ui["font"]
    radius = ui["radius"]
    space = ui["space"]
    control = ui["control"]
    table = ui["table"]
    shell = ui["shell"]
    surface = ui["surface"]
    border = ui["border"]
    text = ui["text"]
    tab_active_bg = t.get("TAB_ACTIVE_BG", t["BRAND_SUBTLE"])
    tab_active_border = t.get("TAB_ACTIVE_BORDER", t["BORDER_BRAND"])
    tab_active_text = t.get("TAB_ACTIVE_TEXT", t["TEXT_PRIMARY"])
    tab_active_top = t.get("TAB_ACTIVE_TOP", "transparent")
    tab_active_indicator = t.get("TAB_ACTIVE_INDICATOR", tab_active_top)
    if t.get("TAB_ACTIVE_INDICATOR_SIDE") == "bottom":
        tab_active_indicator_rule = (
            f"border-top: 1px solid {tab_active_border};\n"
            f"    border-bottom: 2px solid {tab_active_indicator};"
        )
    else:
        tab_active_indicator_rule = f"border-top: 2px solid {tab_active_top};"
    primary_gradient_start = t.get("PRIMARY_GRADIENT_START", t["BRAND_PRIMARY"])
    primary_gradient_end = t.get("PRIMARY_GRADIENT_END", t.get("BRAND_PRESSED", t["BRAND_DEEP"]))
    primary_hover_start = t.get("PRIMARY_HOVER_GRADIENT_START", t["BRAND_HOVER"])
    primary_hover_end = t.get("PRIMARY_HOVER_GRADIENT_END", t["BRAND_PRIMARY"])
    primary_button_text = t.get("PRIMARY_BUTTON_TEXT", t["TEXT_ON_ACCENT"])
    primary_button_border = t.get("PRIMARY_BUTTON_BORDER", "transparent")
    primary_button_pressed_bg = t.get("PRIMARY_BUTTON_PRESSED_BG", t.get("BRAND_PRESSED", t["BRAND_DEEP"]))
    segment_active_bg = t.get("SEGMENT_ACTIVE_BG", t["BRAND_PRIMARY"])
    segment_active_border = t.get("SEGMENT_ACTIVE_BORDER", t["BRAND_HOVER"])
    segment_active_text = t.get("SEGMENT_ACTIVE_TEXT", t["TEXT_ON_ACCENT"])
    progress_gradient_start = t.get("PROGRESS_GRADIENT_START", primary_gradient_start)
    progress_gradient_mid = t.get("PROGRESS_GRADIENT_MID", primary_hover_start)
    progress_gradient_end = t.get("PROGRESS_GRADIENT_END", primary_gradient_end)
    error_hover = t.get("COLOR_ERROR_HOVER", t["BRAND_HOVER"])
    text_on_danger = t.get("TEXT_ON_DANGER", t["TEXT_ON_ACCENT"])
    input_selection_bg = t.get("INPUT_SELECTION_BG", t["SELECTION_BG"])
    info_badge_text = t.get("INFO_BADGE_FG", t["TEXT_PRIMARY"])
    accent_border = t.get("ACCENT_BORDER", t["BORDER_BRAND"])
    scrollbar_handle = t.get("SCROLLBAR_HANDLE", t["BORDER_DEFAULT"])
    scrollbar_hover = t.get("SCROLLBAR_HANDLE_HOVER", t["TEXT_MUTED"])
    scrollbar_pressed = t.get("SCROLLBAR_HANDLE_PRESSED", t["BRAND_PRIMARY"])
    sidebar_end = t.get("BG_SIDEBAR_END", t["BG_INPUT"])
    toolbar_end = t.get("BG_TOOLBAR_END", surface["toolbar"])
    toolbar_background = (
        f"qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 {surface['toolbar']}, stop:1 {toolbar_end})"
        if toolbar_end != surface["toolbar"]
        else surface["toolbar"]
    )
    depth_line = t.get("KLINE_DEPTH_LINE", "rgba(255, 255, 255, 0.05)" if ui["is_dark"] else "rgba(15, 23, 42, 0.04)")
    panel_depth_bg = (
        f"qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 {surface['panel']}, stop:1 {surface['elevated']})"
        if ui["is_dark"]
        else surface["panel"]
    )
    return (
        theme,
        t,
        ui,
        font,
        radius,
        space,
        control,
        table,
        shell,
        surface,
        border,
        text,
        tab_active_bg,
        tab_active_border,
        tab_active_text,
        tab_active_indicator_rule,
        primary_gradient_start,
        primary_gradient_end,
        primary_hover_start,
        primary_hover_end,
        primary_button_text,
        primary_button_border,
        primary_button_pressed_bg,
        segment_active_bg,
        segment_active_border,
        segment_active_text,
        progress_gradient_start,
        progress_gradient_mid,
        progress_gradient_end,
        error_hover,
        text_on_danger,
        input_selection_bg,
        info_badge_text,
        accent_border,
        scrollbar_handle,
        scrollbar_hover,
        scrollbar_pressed,
        sidebar_end,
        toolbar_background,
        depth_line,
        panel_depth_bg,
    )


def generate_global_qss(theme: dict | None = None, density: str | None = None) -> str:
    """根据主题 token 字典生成完整的全局 QSS 字符串"""
    (
        theme,
        t,
        ui,
        font,
        radius,
        space,
        control,
        table,
        shell,
        surface,
        border,
        text,
        tab_active_bg,
        tab_active_border,
        tab_active_text,
        tab_active_indicator_rule,
        primary_gradient_start,
        primary_gradient_end,
        primary_hover_start,
        primary_hover_end,
        primary_button_text,
        primary_button_border,
        primary_button_pressed_bg,
        segment_active_bg,
        segment_active_border,
        segment_active_text,
        progress_gradient_start,
        progress_gradient_mid,
        progress_gradient_end,
        error_hover,
        text_on_danger,
        input_selection_bg,
        info_badge_text,
        accent_border,
        scrollbar_handle,
        scrollbar_hover,
        scrollbar_pressed,
        sidebar_end,
        toolbar_background,
        depth_line,
        panel_depth_bg,
    ) = _global_qss_tokens(theme, density)
    if theme is None:
        theme = theme_manager.current_theme
    cache_key = _global_qss_cache_key(theme, ui["density"])
    cached = _GLOBAL_QSS_CACHE.get(cache_key)
    if cached is not None:
        return cached

    qss = f"""
/* ═══════════════════════════════════════════
   紫金研选 - 全局主题 QSS (动态生成)
   当前主题: {t.get("name", "未知")}
   ═══════════════════════════════════════════ */

/* --- 全局窗口基底 --- */
QMainWindow, QWidget {{
    background-color: {t["BG_CANVAS"]};
    color: {text["primary"]};
    font-family: {font["family"]};
    font-size: {font["size_md"]}px;
}}

QWidget#mainWindowFrame {{
    background-color: {t["BG_CANVAS"]};
    border: 1px solid {shell["inner_border_color"]};
    border-radius: {radius["lg"]}px;
}}

/* --- 左侧面板 --- */
QWidget#leftPanel {{
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
        stop:0 {t["BG_SIDEBAR"]}, stop:1 {sidebar_end});
    border-right: none;
}}

/* ═══════════════════════════════════════════
   QTabWidget - 现代标签页
   ═══════════════════════════════════════════ */
QTabWidget::pane {{
    background: {panel_depth_bg};
    border: 1px solid {depth_line};
    border-radius: {radius["lg"]}px;
    top: -1px;
}}
QTabBar {{
    background: transparent;
}}
QTabBar::tab {{
    background: {surface["toolbar_chip"]};
    color: {t["TEXT_MUTED"]};
    padding: {control["tab_padding_y"]}px {control["tab_padding_x"]}px;
    margin-right: {space["xs"]}px;
    font-size: {font["size_md"]}px;
    font-weight: {font["weight_medium"]};
    border: 1px solid {border["subtle"]};
    border-top: 2px solid transparent;
    border-top-left-radius: {radius["md"]}px;
    border-top-right-radius: {radius["md"]}px;
    min-width: 50px;
}}
QTabBar::tab:hover {{
    color: {t["TEXT_PRIMARY"]};
    background: {t["TAB_HOVER_BG"]};
    border-color: {border["strong"]};
}}
QTabBar::tab:selected {{
    color: {tab_active_text};
    font-weight: {font["weight_semibold"]};
    background: {tab_active_bg};
    border-color: {tab_active_border};
    {tab_active_indicator_rule}
}}

/* ═══════════════════════════════════════════
   QTableWidget - 数据表格
   ═══════════════════════════════════════════ */
QTableView {{
    background-color: {t["BG_TABLE_BASE"]};
    alternate-background-color: {t["BG_TABLE_ALT_ROW"]};
    color: {t["TEXT_PRIMARY"]};
    gridline-color: transparent;
    border: none;
    font-family: {font["mono_family"]};
    font-size: {font["size_md"]}px;
    selection-background-color: {table["selected_bg"]};
    selection-color: {t["TEXT_BRIGHT"]};
    outline: none;
}}
QTableView::item {{
    padding: {table["cell_padding_y"]}px {table["cell_padding_x"]}px;
    border-bottom: 1px solid {t["BORDER_SUBTLE"]};
    border-right: 1px solid transparent;
}}
QTableView::item:hover {{
    background-color: {t["BG_TABLE_HOVER"]};
}}
QTableView::item:selected {{
    background-color: {table["selected_bg"]};
    color: {t["TEXT_BRIGHT"]};
    border-bottom: 1px solid {t["BORDER_SUBTLE"]};
    border-right: 1px solid transparent;
}}
QTableView::item:selected:hover {{
    background-color: {table["selected_hover_bg"]};
}}
QTableView::item:focus {{
    outline: none;
    border-bottom: 1px solid {t["BORDER_SUBTLE"]};
    border-right: 1px solid transparent;
}}
QTableView::item:selected:focus {{
    background-color: {table["selected_bg"]};
    color: {t["TEXT_BRIGHT"]};
    outline: none;
    border-bottom: 1px solid {t["BORDER_SUBTLE"]};
    border-right: 1px solid transparent;
}}
QTableView:focus {{
    border: 1px solid {border["focus"]};
    border-radius: {table["focus_radius"]}px;
}}
QTableCornerButton::section {{
    background-color: {surface["elevated"]};
    border: none;
    border-bottom: 1px solid {border["default"]};
    border-right: 1px solid {border["subtle"]};
}}

/* 表头 */
QHeaderView::section {{
    background-color: {surface["toolbar"]};
    color: {text["header"]};
    font-family: {font["family"]};
    font-size: {table["header_font_size"]}px;
    font-weight: {font["weight_semibold"]};
    letter-spacing: 0px;
    padding: {table["header_padding_y"]}px {table["header_padding_x"]}px;
    min-height: {table["header_min_height"]}px;
    border: none;
    border-bottom: 1px solid {border["strong"]};
    border-right: 1px solid transparent;
}}
QHeaderView::section:hover {{
    background-color: {surface["elevated"]};
    color: {text["primary"]};
}}
QHeaderView::section:pressed {{
    background-color: {table["sorted_header_bg"]};
}}
QHeaderView::down-arrow {{ image: none; width: 0; }}
QHeaderView::up-arrow {{ image: none; width: 0; }}

"""
    section_context = {
        "t": t,
        "font": font,
        "radius": radius,
        "space": space,
        "control": control,
        "table": table,
        "shell": shell,
        "surface": surface,
        "border": border,
        "text": text,
        "primary_gradient_start": primary_gradient_start,
        "primary_gradient_end": primary_gradient_end,
        "primary_hover_start": primary_hover_start,
        "primary_hover_end": primary_hover_end,
        "primary_button_text": primary_button_text,
        "primary_button_border": primary_button_border,
        "primary_button_pressed_bg": primary_button_pressed_bg,
        "segment_active_bg": segment_active_bg,
        "segment_active_border": segment_active_border,
        "segment_active_text": segment_active_text,
        "progress_gradient_start": progress_gradient_start,
        "progress_gradient_mid": progress_gradient_mid,
        "progress_gradient_end": progress_gradient_end,
        "input_selection_bg": input_selection_bg,
        "info_badge_text": info_badge_text,
        "accent_border": accent_border,
        "scrollbar_handle": scrollbar_handle,
        "scrollbar_hover": scrollbar_hover,
        "scrollbar_pressed": scrollbar_pressed,
        "toolbar_background": toolbar_background,
        "depth_line": depth_line,
        "error_hover": error_hover,
        "text_on_danger": text_on_danger,
    }
    qss += build_button_qss(section_context)
    qss += build_form_progress_qss(section_context)
    qss += build_toolbar_status_qss(section_context)
    qss += build_dialog_log_scrollbar_qss(section_context)
    if len(_GLOBAL_QSS_CACHE) >= _GLOBAL_QSS_CACHE_MAX_SIZE and cache_key not in _GLOBAL_QSS_CACHE:
        _GLOBAL_QSS_CACHE.clear()
    _GLOBAL_QSS_CACHE[cache_key] = qss
    return qss


# 为了向后兼容，保持 GLOBAL_QSS 变量名可用
# 避免模块导入时生成整份 QSS，主题应用路径会按需调用 generate_global_qss()
GLOBAL_QSS = ""


theme_manager.sig_theme_changed.connect(invalidate_global_qss_cache)
