# -*- coding: utf-8 -*-
"""
ui/theme.py
紫金研选 — 多主题色彩系统（月白 / 曜黑）

所有 UI 组件应引用此文件中的常量或调用 ThemeManager 获取当前主题色值。
为什么用单例+信号？因为主题切换需要通知所有已创建的组件刷新样式，
就像广播电台——发一次信号，所有收音机同时收到。
"""

from datetime import datetime as _datetime

from PyQt6.QtCore import QObject, QTimer, pyqtSignal

from app.services.ui_config_service import app_config

DEFAULT_THEME_NAME = "曜黑"
DEFAULT_THEME_MIGRATION_KEY = "default_theme_v2_applied"

# ============================================================
# 月白主题（亮色，冷灰交易终端风格）
# ============================================================
THEME_YUEBAI = {
    "name": "月白",
    "appearance": "light",
    # 背景色层级体系（冷白 → 纸白），避免浅色模式变成一整片米色。
    "BG_CANVAS": "#F3F5F7",
    "BG_SIDEBAR": "#E8EDF2",
    "BG_SIDEBAR_END": "#F3F5F7",
    "BG_TABLE_ALT": "#F7F8FA",
    "BG_CARD": "#FFFFFF",
    "BG_HOVER": "rgba(49, 95, 134, 0.07)",
    "BG_INPUT": "#FFFFFF",
    "BG_ELEVATED": "#FFFFFF",
    "BG_TITLEBAR": "#F5F7F9",
    "BG_STATUSBAR": "#F5F7F9",
    "BG_TABLE_BASE": "#FFFFFF",
    "BG_TABLE_ALT_ROW": "#F7F8FA",
    "BG_TABLE_HOVER": "#EAF1F7",
    "BG_BUTTON": "#F4F6F8",
    "BG_BUTTON_HOVER": "#E8EEF5",
    "BG_MENU": "#FFFFFF",
    "BG_GLASS": "rgba(243, 245, 247, 0.97)",
    "BG_TOOLBAR": "#F7F9FB",
    "BG_TOOLBAR_END": "#F3F5F7",
    "BG_TOOLBAR_CARD": "#FFFFFF",
    "BG_TOOLBAR_CHIP": "#EEF3F7",
    # 文字色 — 白底黑字，严格遵循 WCAG 4.5:1
    "TEXT_PRIMARY": "#111827",
    "TEXT_SECONDARY": "#334155",
    "TEXT_MUTED": "#475569",
    "TEXT_DISABLED": "#94A3B8",
    "TEXT_HEADER": "#475569",
    "TEXT_BRIGHT": "#111827",
    "TEXT_ON_ACCENT": "#FFFFFF",
    "TEXT_ON_DANGER": "#FFFFFF",
    # 品牌色 — 保留 A 股红，但只承担主动作/市场语义。
    "BRAND_PRIMARY": "#B3342B",
    "BRAND_HOVER": "#C94A40",
    "BRAND_DEEP": "#8F2923",
    "BRAND_PRESSED": "#6F211D",
    "BRAND_SUBTLE": "rgba(179, 52, 43, 0.08)",
    # 信息强调色 — 用钢蓝承接焦点、标签和导航，避开 AI 产品常见的高饱和蓝紫。
    "ACCENT_PRIMARY": "#315F86",
    "ACCENT_HOVER": "#3C719E",
    "ACCENT_DEEP": "#244C70",
    "ACCENT_SUBTLE": "rgba(49, 95, 134, 0.08)",
    "ACCENT_BORDER": "rgba(49, 95, 134, 0.24)",
    "ACCENT_TEXT": "#214966",
    # 功能色 — 涨跌（白底加深版，确保对比度）
    "COLOR_RISE": "#F23645",
    "COLOR_RISE_STRONG": "#F23645",
    "COLOR_FALL": "#089981",
    "COLOR_FALL_STRONG": "#089981",
    "COLOR_FLAT": "#64748B",
    # 功能色 — 状态（不变）
    "COLOR_SUCCESS": "#16845D",
    "COLOR_WARNING": "#94661E",
    "COLOR_ERROR": "#C83E36",
    "COLOR_ERROR_HOVER": "#A92E29",
    "COLOR_INFO": "#315F86",
    "COLOR_REALTIME": "#2F7EA6",
    "INFO_BADGE_BG": "rgba(49, 95, 134, 0.08)",
    "INFO_BADGE_BORDER": "rgba(49, 95, 134, 0.18)",
    "INFO_BADGE_FG": "#334155",
    # 边框色 — 白底用深色半透明
    "BORDER_DEFAULT": "rgba(15, 23, 42, 0.05)",
    "BORDER_SUBTLE": "rgba(15, 23, 42, 0.04)",
    "BORDER_STRONG": "rgba(15, 23, 42, 0.09)",
    "BORDER_BRAND": "rgba(179, 52, 43, 0.18)",
    "BORDER_MENU": "#DCE5EF",
    "FOCUS_RING": "rgba(49, 95, 134, 0.30)",
    # 评分着色梯度 — 不变
    "SCORE_EXCELLENT": "#C83E36",
    "SCORE_GOOD": "#94661E",
    "SCORE_NORMAL": "#315F86",
    "SCORE_LOW": "#64748B",
    # 突破状态着色 — 不变
    "STATUS_BREAKOUT": "#C83E36",
    "STATUS_APPROACHING": "#94661E",
    "STATUS_VCP": "#315F86",
    "STATUS_INACTIVE": "#64748B",
    # 滚动条颜色 — 灰色系
    "SCROLLBAR_HANDLE": "rgba(71, 85, 105, 0.22)",
    "SCROLLBAR_HANDLE_HOVER": "rgba(49, 95, 134, 0.30)",
    "SCROLLBAR_HANDLE_PRESSED": "rgba(49, 95, 134, 0.46)",
    # 选中态颜色 - 输入/控件保持蓝色，表格行选中使用低饱和浅红。
    "SELECTION_BG": "rgba(49, 95, 134, 0.10)",
    "SELECTION_HOVER_BG": "rgba(49, 95, 134, 0.16)",
    "INPUT_SELECTION_BG": "rgba(49, 95, 134, 0.18)",
    "TABLE_SELECTION_BG": "rgba(242, 54, 69, 0.10)",
    "TABLE_SELECTION_HOVER_BG": "rgba(242, 54, 69, 0.16)",
    "TABLE_SELECTED_RAIL": "#315F86",
    "TABLE_CURRENT_CELL_BG": "rgba(49, 95, 134, 0.07)",
    "TABLE_CURRENT_CELL_BG_SELECTED": "rgba(49, 95, 134, 0.13)",
    "TABLE_CURRENT_CELL_BORDER": "rgba(49, 95, 134, 0.66)",
    "TABLE_FLASH_ALPHA_SCALE": 0.12,
    "TABLE_FLASH_MAX_ALPHA": 34,
    "TABLE_FLASH_RAIL_ALPHA": 105,
    # 标题栏分隔线
    "TITLEBAR_BORDER": "rgba(15, 23, 42, 0.05)",
    "STATUSBAR_BORDER": "rgba(15, 23, 42, 0.05)",
    # 齿轮菜单选中色
    "MENU_SELECTED_BG": "rgba(49, 95, 134, 0.10)",
    # Splitter
    "SPLITTER_BG": "rgba(71, 85, 105, 0.10)",
    "SPLITTER_HOVER": "rgba(49, 95, 134, 0.20)",
    # Tab
    "TAB_TEXT": "#475569",
    "TAB_TEXT_HOVER": "#111827",
    "TAB_HOVER_BG": "#EAF1F7",
    "TAB_ACTIVE_BG": "#FFFFFF",
    "TAB_ACTIVE_BORDER": "rgba(49, 95, 134, 0.26)",
    "TAB_ACTIVE_TEXT": "#214966",
    "TAB_ACTIVE_TOP": "#315F86",
    # 下拉箭头颜色
    "ARROW_COLOR": "#475569",
    # 主题能力 token
    "PRIMARY_GRADIENT_START": "#244C70",
    "PRIMARY_GRADIENT_END": "#315F86",
    "PRIMARY_HOVER_GRADIENT_START": "#315F86",
    "PRIMARY_HOVER_GRADIENT_END": "#477DA8",
    "PRIMARY_BUTTON_TEXT": "#FFFFFF",
    "PRIMARY_BUTTON_BORDER": "rgba(49, 95, 134, 0.30)",
    "PRIMARY_BUTTON_PRESSED_BG": "#244C70",
    "SEGMENT_ACTIVE_BG": "rgba(49, 95, 134, 0.12)",
    "SEGMENT_ACTIVE_BORDER": "#315F86",
    "SEGMENT_ACTIVE_TEXT": "#214966",
    "PROGRESS_GRADIENT_START": "#244C70",
    "PROGRESS_GRADIENT_MID": "#315F86",
    "PROGRESS_GRADIENT_END": "#477DA8",
    "NETWORK_ONLINE": "#2F7EA6",
    "NETWORK_OFFLINE": "#C83E36",
    "NETWORK_BUSY": "#94661E",
    # K线图专用色 — 白底适配版，所有色值加深确保 WCAG 对比度
    "KLINE_UP_COLOR": "#F23645",
    "KLINE_DOWN_COLOR": "#089981",
    "KLINE_UP_GRADIENT_TOP": "#F23645",
    "KLINE_UP_GRADIENT_BOTTOM": "#F23645",
    "KLINE_UP_BORDER": "#F23645",
    "KLINE_DOWN_GRADIENT_TOP": "#089981",
    "KLINE_DOWN_GRADIENT_BOTTOM": "#089981",
    "KLINE_DOWN_BORDER": "#089981",
    "KLINE_MA10": "#1F2933",
    "KLINE_MA20": "#315F86",
    "KLINE_MA50": "#B36B2C",
    "KLINE_MA150": "#5F6F82",
    "KLINE_MA200": "#B3342B",
    "KLINE_VOL_MA20": "#94661E",
    "KLINE_GRID_LINE": "rgba(71, 85, 105, 0.11)",
    "KLINE_AXIS_LINE": "rgba(15, 23, 42, 0.12)",
    "KLINE_AXIS_LABEL": "#475569",
    "KLINE_POINTER_BG": "#64748B",
    "KLINE_VCP_STAR": "#94661E",
    "KLINE_VCP_LINE": "rgba(148, 102, 30, 0.90)",
    "KLINE_VCP_LINE_SOFT": "rgba(148, 102, 30, 0.62)",
    "KLINE_VCP_AREA": "rgba(49, 95, 134, 0.08)",
    "KLINE_VCP_AREA_TOP": "rgba(49, 95, 134, 0.12)",
    "KLINE_VCP_AREA_BOTTOM": "rgba(49, 95, 134, 0.02)",
    "KLINE_VCP_AREA_BORDER": "rgba(148, 102, 30, 0.30)",
    "KLINE_VCP_GUIDE": "rgba(148, 102, 30, 0.46)",
    "KLINE_VCP_BREAKOUT_BG": "rgba(148, 102, 30, 0.14)",
    "KLINE_MA_RIBBON_UP": "rgba(242, 54, 69, 0.08)",
    "KLINE_MA_RIBBON_DOWN": "rgba(8, 153, 129, 0.08)",
    "KLINE_VOLUME_DRY": "rgba(71, 85, 105, 0.24)",
    "KLINE_VOLUME_SPIKE": "#D0A44E",
    "KLINE_VOLUME_SPIKE_SHADOW": "rgba(208, 164, 78, 0.48)",
    "KLINE_DEPTH_LINE": "rgba(15, 23, 42, 0.04)",
    "KLINE_BG_CANVAS": "#FFFFFF",
    "KLINE_BG_TOOLBAR": "#FFFFFF",
    "KLINE_WIDGET_BG": "#FFFFFF",
    "KLINE_WIDGET_TEXT": "#111827",
    "KLINE_TOOLBAR_BG": "#FFFFFF",
    "KLINE_TOOLBAR_BORDER": "rgba(15, 23, 42, 0.05)",
    "KLINE_SUMMARY_BG": "#FFFFFF",
    "KLINE_INFO_COLOR": "#475569",
    "KLINE_BTN_BORDER": "#D7E0E8",
    "KLINE_BTN_HOVER_BG": "#E8EEF5",
    "KLINE_BTN_HOVER_TEXT": "#111827",
    "KLINE_BTN_DISABLED_TEXT": "#94A3B8",
    "KLINE_BTN_DISABLED_BORDER": "rgba(71, 85, 105, 0.12)",
    "KLINE_CHART_BG": "#FFFFFF",
    "KLINE_NAV_BG": "#EEF3F7",
    "KLINE_BADGE_BG": "rgba(49, 95, 134, 0.08)",
    "KLINE_BADGE_FG": "#334155",
    "KLINE_SUMMARY_BORDER": "rgba(15, 23, 42, 0.05)",
    "KLINE_TOOLTIP_BG": "rgba(17, 24, 39, 0.94)",
    "KLINE_TOOLTIP_TEXT": "#F8FAFC",
    "KLINE_MACD_DIFF": "#94661E",
    "KLINE_MACD_DEA": "#315F86",
    "KLINE_CROSSHAIR_LINE": "rgba(31, 41, 51, 0.78)",
    "KLINE_DATAZOOM_BG": "#EEF3F7",
    "KLINE_DATAZOOM_FILL": "rgba(49, 95, 134, 0.14)",
    "KLINE_DATAZOOM_HANDLE": "#94A3B8",
    "KLINE_FONT_FAMILY": '"Microsoft YaHei UI", "Microsoft YaHei", "Segoe UI", sans-serif',
    "KLINE_MONO_FONT_FAMILY": '"JetBrains Mono", "Cascadia Mono", "Consolas", "Microsoft YaHei UI", monospace',
}

# ============================================================
# 曜黑主题（暗色，金融终端语义）
# ============================================================
THEME_YAOHEI = {
    "name": "曜黑",
    "appearance": "dark",
    # 背景色层级体系（纯黑 → 终端面板）
    "BG_CANVAS": "#000000",
    "BG_SIDEBAR": "#000000",
    "BG_SIDEBAR_END": "#121824",
    "BG_TABLE_ALT": "#000000",
    "BG_CARD": "#121824",
    "BG_HOVER": "#1A2234",
    "BG_INPUT": "#0F1620",
    "BG_ELEVATED": "#1A2234",
    "BG_TITLEBAR": "#000000",
    "BG_STATUSBAR": "#000000",
    "BG_TABLE_BASE": "#000000",
    "BG_TABLE_ALT_ROW": "#000000",
    "BG_TABLE_HOVER": "#1A2234",
    "BG_BUTTON": "#121824",
    "BG_BUTTON_HOVER": "#1A2234",
    "BG_MENU": "#121824",
    "BG_GLASS": "rgba(18, 24, 36, 0.96)",
    "BG_TOOLBAR": "#000000",
    "BG_TOOLBAR_END": "#121824",
    "BG_TOOLBAR_CARD": "#121824",
    "BG_TOOLBAR_CHIP": "#121824",
    # 文字色
    "TEXT_PRIMARY": "#E8EDF3",
    "TEXT_SECONDARY": "#B8C3CF",
    "TEXT_MUTED": "#8794A3",
    "TEXT_DISABLED": "#5E6A76",
    "TEXT_HEADER": "#9BA7B4",
    "TEXT_BRIGHT": "#F7FAFC",
    "TEXT_ON_ACCENT": "#FFFFFF",
    "TEXT_ON_DANGER": "#FFFFFF",
    # 品牌色（低饱和 A 股红）
    "BRAND_PRIMARY": "#B93A32",
    "BRAND_HOVER": "#D14B42",
    "BRAND_DEEP": "#8F2A25",
    "BRAND_PRESSED": "#6F211D",
    "BRAND_SUBTLE": "rgba(185, 58, 50, 0.14)",
    # 交互强调色（钢蓝）
    "ACCENT_PRIMARY": "#4E7FA8",
    "ACCENT_HOVER": "#5C8FB8",
    "ACCENT_DEEP": "#3E6688",
    "ACCENT_SUBTLE": "rgba(78, 127, 168, 0.14)",
    "ACCENT_BORDER": "rgba(78, 127, 168, 0.28)",
    "ACCENT_TEXT": "#C9D8E4",
    # 功能色 — 涨跌
    "COLOR_RISE": "#F23645",
    "COLOR_RISE_STRONG": "#F23645",
    "COLOR_FALL": "#089981",
    "COLOR_FALL_STRONG": "#089981",
    "COLOR_FLAT": "#C4CCD6",
    # 功能色 — 状态
    "COLOR_SUCCESS": "#2AA876",
    "COLOR_WARNING": "#C9913A",
    "COLOR_ERROR": "#E05243",
    "COLOR_ERROR_HOVER": "#F06455",
    "COLOR_INFO": "#4E9CC9",
    "COLOR_REALTIME": "#4E9CC9",
    "INFO_BADGE_BG": "rgba(78, 127, 168, 0.10)",
    "INFO_BADGE_BORDER": "rgba(78, 127, 168, 0.22)",
    "INFO_BADGE_FG": "#B8C3CF",
    # 边框色
    "BORDER_DEFAULT": "rgba(132, 149, 169, 0.14)",
    "BORDER_SUBTLE": "rgba(132, 149, 169, 0.08)",
    "BORDER_STRONG": "rgba(132, 149, 169, 0.24)",
    "BORDER_BRAND": "rgba(185, 58, 50, 0.32)",
    "BORDER_MENU": "#344252",
    "FOCUS_RING": "rgba(78, 127, 168, 0.34)",
    # 评分着色梯度
    "SCORE_EXCELLENT": "#E05243",
    "SCORE_GOOD": "#C9913A",
    "SCORE_NORMAL": "#4E9CC9",
    "SCORE_LOW": "#8794A3",
    # 突破状态着色
    "STATUS_BREAKOUT": "#F06455",
    "STATUS_APPROACHING": "#C9913A",
    "STATUS_VCP": "#4E7FA8",
    "STATUS_INACTIVE": "#8794A3",
    # 滚动条颜色
    "SCROLLBAR_HANDLE": "rgba(135, 148, 163, 0.25)",
    "SCROLLBAR_HANDLE_HOVER": "rgba(78, 127, 168, 0.34)",
    "SCROLLBAR_HANDLE_PRESSED": "rgba(78, 127, 168, 0.52)",
    # 选中态颜色
    "SELECTION_BG": "rgba(78, 127, 168, 0.12)",
    "SELECTION_HOVER_BG": "rgba(78, 127, 168, 0.18)",
    "INPUT_SELECTION_BG": "rgba(78, 127, 168, 0.24)",
    "TABLE_SELECTION_BG": "rgba(242, 54, 69, 0.14)",
    "TABLE_SELECTION_HOVER_BG": "rgba(242, 54, 69, 0.20)",
    "TABLE_SELECTED_RAIL": "#4E7FA8",
    "TABLE_HOVER_RAIL": "#4E7FA8",
    "TABLE_CURRENT_CELL_BG": "rgba(78, 127, 168, 0.09)",
    "TABLE_CURRENT_CELL_BG_SELECTED": "rgba(78, 127, 168, 0.15)",
    "TABLE_CURRENT_CELL_BORDER": "rgba(78, 127, 168, 0.68)",
    "TABLE_FLASH_ALPHA_SCALE": 0.16,
    "TABLE_FLASH_MAX_ALPHA": 42,
    "TABLE_FLASH_RAIL_ALPHA": 120,
    # 标题栏 / 状态栏
    "TITLEBAR_BORDER": "rgba(132, 149, 169, 0.12)",
    "STATUSBAR_BORDER": "rgba(132, 149, 169, 0.12)",
    # 菜单 / 分隔 / 当前标签
    "MENU_SELECTED_BG": "rgba(78, 127, 168, 0.13)",
    "SPLITTER_BG": "rgba(132, 149, 169, 0.14)",
    "SPLITTER_HOVER": "rgba(78, 127, 168, 0.22)",
    "TAB_TEXT": "#93A0AD",
    "TAB_TEXT_HOVER": "#E8EDF3",
    "TAB_HOVER_BG": "#1A2234",
    "TAB_ACTIVE_BG": "rgba(78, 127, 168, 0.16)",
    "TAB_ACTIVE_BORDER": "rgba(78, 127, 168, 0.42)",
    "TAB_ACTIVE_TEXT": "#C9D8E4",
    "TAB_ACTIVE_TOP": "#4E7FA8",
    # 下拉箭头颜色
    "ARROW_COLOR": "#8794A3",
    # 主题能力 token
    "PRIMARY_GRADIENT_START": "#3E6688",
    "PRIMARY_GRADIENT_END": "#4E7FA8",
    "PRIMARY_HOVER_GRADIENT_START": "#4E7FA8",
    "PRIMARY_HOVER_GRADIENT_END": "#5C8FB8",
    "PRIMARY_BUTTON_TEXT": "#FFFFFF",
    "PRIMARY_BUTTON_BORDER": "rgba(78, 127, 168, 0.40)",
    "PRIMARY_BUTTON_PRESSED_BG": "#3E6688",
    "SEGMENT_ACTIVE_BG": "rgba(78, 127, 168, 0.16)",
    "SEGMENT_ACTIVE_BORDER": "#4E7FA8",
    "SEGMENT_ACTIVE_TEXT": "#C9D8E4",
    "PROGRESS_GRADIENT_START": "#3E6688",
    "PROGRESS_GRADIENT_MID": "#4E7FA8",
    "PROGRESS_GRADIENT_END": "#5C8FB8",
    "NETWORK_ONLINE": "#4E9CC9",
    "NETWORK_OFFLINE": "#E05243",
    "NETWORK_BUSY": "#C9913A",
    # K线图专用色
    "KLINE_UP_COLOR": "#F23645",
    "KLINE_DOWN_COLOR": "#089981",
    "KLINE_UP_GRADIENT_TOP": "#F23645",
    "KLINE_UP_GRADIENT_BOTTOM": "#F23645",
    "KLINE_UP_BORDER": "#F23645",
    "KLINE_DOWN_GRADIENT_TOP": "#089981",
    "KLINE_DOWN_GRADIENT_BOTTOM": "#089981",
    "KLINE_DOWN_BORDER": "#089981",
    "KLINE_MA10": "rgba(218, 225, 234, 0.82)",
    "KLINE_MA20": "rgba(87, 145, 179, 0.82)",
    "KLINE_MA50": "rgba(201, 145, 58, 0.62)",
    "KLINE_MA150": "rgba(150, 162, 174, 0.56)",
    "KLINE_MA200": "rgba(199, 107, 91, 0.54)",
    "KLINE_VOL_MA20": "#C9913A",
    "KLINE_GRID_LINE": "rgba(126, 142, 160, 0.08)",
    "KLINE_AXIS_LINE": "#3D4A59",
    "KLINE_AXIS_LABEL": "#8794A3",
    "KLINE_POINTER_BG": "rgba(64, 80, 98, 0.96)",
    "KLINE_VCP_STAR": "#D0A44E",
    "KLINE_VCP_LINE": "rgba(201, 145, 58, 0.90)",
    "KLINE_VCP_LINE_SOFT": "rgba(201, 145, 58, 0.62)",
    "KLINE_VCP_AREA": "rgba(78, 127, 168, 0.10)",
    "KLINE_VCP_AREA_TOP": "rgba(78, 127, 168, 0.12)",
    "KLINE_VCP_AREA_BOTTOM": "rgba(78, 127, 168, 0.02)",
    "KLINE_VCP_AREA_BORDER": "rgba(208, 164, 78, 0.30)",
    "KLINE_VCP_GUIDE": "rgba(201, 145, 58, 0.48)",
    "KLINE_VCP_BREAKOUT_BG": "rgba(201, 145, 58, 0.16)",
    "KLINE_MA_RIBBON_UP": "rgba(242, 54, 69, 0.08)",
    "KLINE_MA_RIBBON_DOWN": "rgba(8, 153, 129, 0.08)",
    "KLINE_VOLUME_DRY": "rgba(126, 142, 160, 0.22)",
    "KLINE_VOLUME_SPIKE": "#D0A44E",
    "KLINE_VOLUME_SPIKE_SHADOW": "rgba(208, 164, 78, 0.56)",
    "KLINE_DEPTH_LINE": "rgba(255, 255, 255, 0.05)",
    "KLINE_BG_CANVAS": "#000000",
    "KLINE_BG_TOOLBAR": "#000000",
    "KLINE_WIDGET_BG": "#121824",
    "KLINE_WIDGET_TEXT": "#E8EDF3",
    "KLINE_TOOLBAR_BG": "#000000",
    "KLINE_TOOLBAR_BORDER": "rgba(132, 149, 169, 0.14)",
    "KLINE_SUMMARY_BG": "#121824",
    "KLINE_INFO_COLOR": "#B8C3CF",
    "KLINE_BTN_BORDER": "rgba(132, 149, 169, 0.24)",
    "KLINE_BTN_HOVER_BG": "#1A2234",
    "KLINE_BTN_HOVER_TEXT": "#E8EDF3",
    "KLINE_BTN_DISABLED_TEXT": "#5E6A76",
    "KLINE_BTN_DISABLED_BORDER": "rgba(132, 149, 169, 0.12)",
    "KLINE_CHART_BG": "#000000",
    "KLINE_NAV_BG": "#121824",
    "KLINE_BADGE_BG": "rgba(78, 127, 168, 0.10)",
    "KLINE_BADGE_FG": "#B8C3CF",
    "KLINE_SUMMARY_BORDER": "rgba(132, 149, 169, 0.12)",
    "KLINE_TOOLTIP_BG": "rgba(12, 18, 26, 0.94)",
    "KLINE_TOOLTIP_TEXT": "#F3F7FA",
    "KLINE_MACD_DIFF": "#C9913A",
    "KLINE_MACD_DEA": "#5791B3",
    "KLINE_CROSSHAIR_LINE": "rgba(232, 237, 243, 0.86)",
    "KLINE_DATAZOOM_BG": "#121824",
    "KLINE_DATAZOOM_FILL": "rgba(78, 127, 168, 0.12)",
    "KLINE_DATAZOOM_HANDLE": "#6F7F90",
}


def _with_alias_tokens(theme: dict) -> dict:
    """为新旧样式系统补齐别名 token，避免局部组件各写各的。"""
    enriched = dict(theme)
    appearance = enriched.get("appearance", "dark")
    enriched.setdefault("BG_BASE", enriched.get("BG_CARD", enriched.get("BG_CANVAS", "")))
    enriched.setdefault("SURFACE_BASE", enriched.get("BG_CANVAS", ""))
    enriched.setdefault("SURFACE_PANEL", enriched.get("BG_CARD", ""))
    enriched.setdefault("SURFACE_ELEVATED", enriched.get("BG_ELEVATED", ""))
    enriched.setdefault("SURFACE_INPUT", enriched.get("BG_INPUT", ""))
    enriched.setdefault(
        "BG_TOOLBAR", enriched.get("BG_ELEVATED", "") if appearance == "dark" else enriched.get("BG_CARD", "")
    )
    enriched.setdefault("ACCENT_PRIMARY", enriched.get("BRAND_PRIMARY", ""))
    enriched.setdefault("ACCENT_HOVER", enriched.get("BRAND_HOVER", ""))
    enriched.setdefault("ACCENT_DEEP", enriched.get("BRAND_DEEP", ""))
    enriched.setdefault("ACCENT_SUBTLE", enriched.get("BRAND_SUBTLE", ""))
    enriched.setdefault("ACCENT_BORDER", enriched.get("BORDER_BRAND", ""))
    enriched.setdefault("ACCENT_TEXT", enriched.get("TEXT_PRIMARY", ""))
    enriched.setdefault("BRAND_PRESSED", enriched.get("BRAND_DEEP", enriched.get("BRAND_PRIMARY", "")))
    enriched.setdefault("STATE_SUCCESS", enriched.get("COLOR_SUCCESS", ""))
    enriched.setdefault("STATE_WARNING", enriched.get("COLOR_WARNING", ""))
    enriched.setdefault("STATE_DANGER", enriched.get("COLOR_ERROR", ""))
    enriched.setdefault("STATE_INFO", enriched.get("COLOR_INFO", ""))
    enriched.setdefault("TEXT_ON_ACCENT", "#FFFFFF")
    enriched.setdefault("TEXT_ON_DANGER", enriched.get("TEXT_ON_ACCENT", "#FFFFFF"))
    enriched.setdefault("FOCUS_RING", "")
    enriched.setdefault("TAB_ACTIVE_BG", enriched.get("BRAND_SUBTLE", ""))
    enriched.setdefault("TAB_ACTIVE_BORDER", enriched.get("BORDER_BRAND", ""))
    enriched.setdefault("TAB_ACTIVE_TEXT", enriched.get("TEXT_PRIMARY", ""))
    enriched.setdefault("TAB_ACTIVE_TOP", "transparent")
    enriched.setdefault("TAB_ACTIVE_INDICATOR", enriched.get("TAB_ACTIVE_TOP", "transparent"))
    enriched.setdefault("TAB_ACTIVE_INDICATOR_SIDE", "top")
    enriched.setdefault("PRIMARY_GRADIENT_START", enriched.get("BRAND_PRIMARY", ""))
    enriched.setdefault("PRIMARY_GRADIENT_END", enriched.get("BRAND_DEEP", enriched.get("BRAND_PRIMARY", "")))
    enriched.setdefault("PRIMARY_HOVER_GRADIENT_START", enriched.get("BRAND_HOVER", enriched.get("BRAND_PRIMARY", "")))
    enriched.setdefault("PRIMARY_HOVER_GRADIENT_END", enriched.get("BRAND_PRIMARY", ""))
    enriched.setdefault("PRIMARY_BUTTON_TEXT", enriched.get("TEXT_ON_ACCENT", "#FFFFFF"))
    enriched.setdefault("PRIMARY_BUTTON_BORDER", "transparent")
    enriched.setdefault(
        "PRIMARY_BUTTON_PRESSED_BG",
        enriched.get("BRAND_PRESSED", enriched.get("BRAND_DEEP", enriched.get("BRAND_PRIMARY", ""))),
    )
    enriched.setdefault("SEGMENT_ACTIVE_BG", enriched.get("BRAND_PRIMARY", ""))
    enriched.setdefault("SEGMENT_ACTIVE_BORDER", enriched.get("BRAND_HOVER", enriched.get("BRAND_PRIMARY", "")))
    enriched.setdefault("SEGMENT_ACTIVE_TEXT", enriched.get("TEXT_ON_ACCENT", "#FFFFFF"))
    enriched.setdefault(
        "PROGRESS_GRADIENT_START", enriched.get("PRIMARY_GRADIENT_START", enriched.get("BRAND_PRIMARY", ""))
    )
    enriched.setdefault(
        "PROGRESS_GRADIENT_MID",
        enriched.get("PRIMARY_HOVER_GRADIENT_START", enriched.get("BRAND_HOVER", enriched.get("BRAND_PRIMARY", ""))),
    )
    enriched.setdefault(
        "PROGRESS_GRADIENT_END",
        enriched.get("PRIMARY_GRADIENT_END", enriched.get("BRAND_DEEP", enriched.get("BRAND_PRIMARY", ""))),
    )
    enriched.setdefault("COLOR_ERROR_HOVER", enriched.get("BRAND_HOVER", enriched.get("COLOR_ERROR", "")))
    enriched.setdefault("COLOR_REALTIME", enriched.get("COLOR_SUCCESS", ""))
    enriched.setdefault("NETWORK_ONLINE", enriched.get("COLOR_REALTIME", enriched.get("COLOR_SUCCESS", "")))
    enriched.setdefault("NETWORK_OFFLINE", enriched.get("COLOR_ERROR", ""))
    enriched.setdefault("NETWORK_BUSY", enriched.get("COLOR_WARNING", ""))
    enriched.setdefault("INPUT_SELECTION_BG", enriched.get("SELECTION_BG", ""))
    enriched.setdefault("SCROLLBAR_HANDLE_PRESSED", enriched.get("BRAND_PRIMARY", ""))
    enriched.setdefault("TABLE_SELECTED_RAIL", enriched.get("BRAND_PRIMARY", ""))
    enriched.setdefault("TABLE_CURRENT_CELL_BG", enriched.get("SELECTION_BG", ""))
    enriched.setdefault(
        "TABLE_CURRENT_CELL_BG_SELECTED", enriched.get("SELECTION_HOVER_BG", enriched.get("SELECTION_BG", ""))
    )
    enriched.setdefault("TABLE_CURRENT_CELL_BORDER", enriched.get("BRAND_DEEP", enriched.get("BRAND_PRIMARY", "")))
    enriched.setdefault("INFO_BADGE_BG", enriched.get("BRAND_SUBTLE", ""))
    enriched.setdefault("INFO_BADGE_BORDER", enriched.get("BORDER_SUBTLE", ""))
    enriched.setdefault("INFO_BADGE_FG", enriched.get("TEXT_PRIMARY", ""))
    enriched.setdefault("TITLEBAR_BRAND_TEXT", enriched.get("BRAND_PRIMARY", enriched.get("TEXT_PRIMARY", "")))
    enriched.setdefault("TITLEBAR_PULSE", enriched.get("BRAND_HOVER", enriched.get("BRAND_PRIMARY", "")))
    enriched.setdefault("STATUS_FLOW_WORKING", enriched.get("BRAND_PRIMARY", ""))
    enriched.setdefault("TABLE_FLASH_ALPHA_SCALE", 0.24)
    enriched.setdefault("TABLE_FLASH_MAX_ALPHA", 76)
    return enriched


THEME_YUEBAI = _with_alias_tokens(THEME_YUEBAI)
THEME_YAOHEI = _with_alias_tokens(THEME_YAOHEI)


class ThemeManager(QObject):
    """主题管理器单例 — 全应用只有一个实例，控制当前激活的主题。

    为什么用单例？就像一个家只有一个主灯开关——
    无论从哪个房间去按，控制的都是同一盏灯。
    """

    # 主题切换时发射此信号，所有监听者重新拉取色值并刷新
    sig_theme_changed = pyqtSignal(str)

    _instance = None

    THEMES = {
        "曜黑": THEME_YAOHEI,
        "月白": THEME_YUEBAI,
    }

    def __new__(cls):
        if cls._instance is None:
            instance = super().__new__(cls)
            # 在 __new__ 阶段就完成 QObject.__init__，避免后续 hasattr 报错
            QObject.__init__(instance)
            instance._initialized = False
            cls._instance = instance
        return cls._instance

    def __init__(self):
        # 防止 __init__ 被多次调用时重复初始化
        if self._initialized:
            return
        self._initialized = True
        self._settings = app_config.section("ui/theme", legacy_scope="ThemeManager")
        # 从持久化配置恢复上次选择的主题；默认切到“曜黑”。
        saved = self._settings.value("current_theme", None)
        if not self._settings.contains(DEFAULT_THEME_MIGRATION_KEY):
            if not saved or saved in {"墨渊", "紫曜"}:
                saved = DEFAULT_THEME_NAME
                self._settings.setValue("current_theme", saved)
            self._settings.setValue(DEFAULT_THEME_MIGRATION_KEY, True)
            self._settings.sync()
        elif not saved:
            saved = DEFAULT_THEME_NAME
        self._current_name = saved if saved in self.THEMES else DEFAULT_THEME_NAME

        # 日夜自动切换：白天月白、晚上曜黑，像手机的自动暗色模式
        self._auto_switch = self._settings.value("auto_switch_theme", False, type=bool)
        self._auto_timer = QTimer()
        self._auto_timer.setInterval(60 * 1000)  # 每 60 秒检查一次
        self._auto_timer.timeout.connect(self._check_auto_switch)
        if self._auto_switch:
            self._auto_timer.start()
            # 启动时立即执行一次，不等 60 秒
            self._check_auto_switch()

    @property
    def current_theme_name(self) -> str:
        return self._current_name

    @property
    def current_theme(self) -> dict:
        return self.THEMES.get(self._current_name, THEME_YAOHEI)

    def get(self, token: str) -> str:
        """获取当前主题的某个 token 值"""
        return self.current_theme.get(token, "")

    def switch_theme(self, name: str):
        """切换主题并广播信号"""
        if name not in self.THEMES:
            return
        if name == self._current_name:
            return
        if self._auto_switch and name not in {"曜黑", "月白"}:
            self.set_auto_switch(False)
        self._current_name = name
        self._settings.setValue("current_theme", name)
        self._settings.sync()
        self.sig_theme_changed.emit(name)

    def is_dark(self) -> bool:
        """当前是否为暗色主题"""
        return self.current_theme.get("appearance") == "dark"

    def theme_names(self) -> list:
        return list(self.THEMES.keys())

    # ======================== 日夜自动切换 ========================

    def is_auto_switch(self) -> bool:
        """是否开启了日夜自动切换"""
        return self._auto_switch

    def set_auto_switch(self, enabled: bool):
        """开关日夜自动切换。像手机的「自动暗色模式」——
        开了以后系统根据时间自己决定用哪个主题，不用你手动切。
        """
        self._auto_switch = enabled
        self._settings.setValue("auto_switch_theme", enabled)
        self._settings.sync()
        if enabled:
            self._auto_timer.start()
            self._check_auto_switch()  # 立即执行一次
        else:
            self._auto_timer.stop()

    def _check_auto_switch(self):
        """根据当前时间决定应该用哪个主题。
        规则：7:00–18:00 → 月白（亮色），其余时段 → 曜黑（暗色）。
        就像太阳升起开灯，太阳落山关灯。
        """
        if not self._auto_switch:
            return
        hour = _datetime.now().hour
        # 白天 7:00 ~ 17:59 用月白，晚上用曜黑
        target = "月白" if 7 <= hour < 18 else "曜黑"
        if target != self._current_name:
            self.switch_theme(target)


# 全局实例 — 任何地方 import 即可使用
theme_manager = ThemeManager()


# ============================================================
# 向后兼容层 — 让老代码 `from ui.theme import COLOR_RISE` 继续工作
# 这些变量在模块加载时指向当前主题的值。
# 注意：这些是"快照"，不会随主题切换实时更新。
# 真正需要动态响应主题切换的地方应该使用 theme_manager.get("COLOR_RISE")
# ============================================================
def _refresh_compat_vars():
    """刷新向后兼容的模块级变量"""
    t = theme_manager.current_theme
    g = globals()
    for key, value in t.items():
        if key != "name":
            g[key] = value


# 首次加载时初始化所有兼容变量
_refresh_compat_vars()

# 当主题切换时自动刷新兼容变量
theme_manager.sig_theme_changed.connect(lambda _: _refresh_compat_vars())


# ============================================================
# 字体（不随主题变化）
# ============================================================
