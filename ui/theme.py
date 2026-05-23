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
# 月白主题（亮色，参照 Gemini 页面风格）
# ============================================================
THEME_YUEBAI = {
    "name": "月白",
    "appearance": "light",
    # 背景色层级体系（冷白 → 纸白），避免浅色模式变成一整片米色。
    "BG_CANVAS": "#F4F6F9",
    "BG_SIDEBAR": "#E6EDF5",
    "BG_SIDEBAR_END": "#F4F6F9",
    "BG_TABLE_ALT": "#F6F7FA",
    "BG_CARD": "#FFFFFF",
    "BG_HOVER": "rgba(37, 99, 235, 0.06)",
    "BG_INPUT": "#FFFFFF",
    "BG_ELEVATED": "#FFFFFF",
    "BG_TITLEBAR": "#F5F6FA",
    "BG_STATUSBAR": "#F5F6FA",
    "BG_TABLE_BASE": "#FFFFFF",
    "BG_TABLE_ALT_ROW": "#F6F7FA",
    "BG_TABLE_HOVER": "#EEF4FF",
    "BG_BUTTON": "#F5F6FA",
    "BG_BUTTON_HOVER": "#E8F0FF",
    "BG_MENU": "#FFFFFF",
    "BG_GLASS": "rgba(244, 246, 249, 0.97)",
    "BG_TOOLBAR": "#F7FAFC",
    "BG_TOOLBAR_END": "#F4F6F9",
    "BG_TOOLBAR_CARD": "#FFFFFF",
    "BG_TOOLBAR_CHIP": "#F0F4FA",
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
    "BRAND_PRIMARY": "#C81E1E",
    "BRAND_HOVER": "#E23B3B",
    "BRAND_DEEP": "#991B1B",
    "BRAND_PRESSED": "#7F1D1D",
    "BRAND_SUBTLE": "rgba(200, 30, 30, 0.08)",
    # 信息强调色 — 用冷蓝承接焦点、标签和导航，和涨跌红绿分离。
    "ACCENT_PRIMARY": "#2563EB",
    "ACCENT_HOVER": "#3B82F6",
    "ACCENT_DEEP": "#1D4ED8",
    "ACCENT_SUBTLE": "rgba(37, 99, 235, 0.08)",
    "ACCENT_BORDER": "rgba(37, 99, 235, 0.24)",
    "ACCENT_TEXT": "#1E3A8A",
    # 功能色 — 涨跌（白底加深版，确保对比度）
    "COLOR_RISE": "#D92D2D",
    "COLOR_RISE_STRONG": "#B91C1C",
    "COLOR_FALL": "#0F9F6E",
    "COLOR_FALL_STRONG": "#047857",
    "COLOR_FLAT": "#64748B",
    # 功能色 — 状态（不变）
    "COLOR_SUCCESS": "#0F9F6E",
    "COLOR_WARNING": "#B7791F",
    "COLOR_ERROR": "#D92D2D",
    "COLOR_ERROR_HOVER": "#B91C1C",
    "COLOR_INFO": "#2563EB",
    "COLOR_REALTIME": "#0284C7",
    "INFO_BADGE_BG": "rgba(37, 99, 235, 0.08)",
    "INFO_BADGE_BORDER": "rgba(37, 99, 235, 0.18)",
    "INFO_BADGE_FG": "#334155",
    # 边框色 — 白底用深色半透明
    "BORDER_DEFAULT": "rgba(15, 23, 42, 0.05)",
    "BORDER_SUBTLE": "rgba(15, 23, 42, 0.04)",
    "BORDER_STRONG": "rgba(15, 23, 42, 0.09)",
    "BORDER_BRAND": "rgba(200, 30, 30, 0.18)",
    "BORDER_MENU": "#DCE5EF",
    "FOCUS_RING": "rgba(37, 99, 235, 0.30)",
    # 评分着色梯度 — 不变
    "SCORE_EXCELLENT": "#D92D2D",
    "SCORE_GOOD": "#B7791F",
    "SCORE_NORMAL": "#2563EB",
    "SCORE_LOW": "#64748B",
    # 突破状态着色 — 不变
    "STATUS_BREAKOUT": "#D92D2D",
    "STATUS_APPROACHING": "#B7791F",
    "STATUS_VCP": "#2563EB",
    "STATUS_INACTIVE": "#64748B",
    # 滚动条颜色 — 灰色系
    "SCROLLBAR_HANDLE": "rgba(71, 85, 105, 0.22)",
    "SCROLLBAR_HANDLE_HOVER": "rgba(37, 99, 235, 0.30)",
    "SCROLLBAR_HANDLE_PRESSED": "rgba(37, 99, 235, 0.46)",
    # 选中态颜色 - 浅色模式用蓝色轨道表达焦点，避免和涨跌红混淆。
    "SELECTION_BG": "rgba(37, 99, 235, 0.10)",
    "SELECTION_HOVER_BG": "rgba(37, 99, 235, 0.16)",
    "INPUT_SELECTION_BG": "rgba(37, 99, 235, 0.18)",
    "TABLE_SELECTED_RAIL": "#2563EB",
    "TABLE_CURRENT_CELL_BG": "rgba(37, 99, 235, 0.07)",
    "TABLE_CURRENT_CELL_BG_SELECTED": "rgba(37, 99, 235, 0.13)",
    "TABLE_CURRENT_CELL_BORDER": "rgba(37, 99, 235, 0.66)",
    "TABLE_FLASH_ALPHA_SCALE": 0.12,
    "TABLE_FLASH_MAX_ALPHA": 34,
    "TABLE_FLASH_RAIL_ALPHA": 105,
    # 标题栏分隔线
    "TITLEBAR_BORDER": "rgba(15, 23, 42, 0.05)",
    "STATUSBAR_BORDER": "rgba(15, 23, 42, 0.05)",
    # 齿轮菜单选中色
    "MENU_SELECTED_BG": "rgba(37, 99, 235, 0.10)",
    # Splitter
    "SPLITTER_BG": "rgba(71, 85, 105, 0.10)",
    "SPLITTER_HOVER": "rgba(37, 99, 235, 0.20)",
    # Tab
    "TAB_TEXT": "#475569",
    "TAB_TEXT_HOVER": "#111827",
    "TAB_HOVER_BG": "#EEF4FF",
    "TAB_ACTIVE_BG": "#FFFFFF",
    "TAB_ACTIVE_BORDER": "rgba(37, 99, 235, 0.26)",
    "TAB_ACTIVE_TEXT": "#1E3A8A",
    "TAB_ACTIVE_TOP": "#2563EB",
    # 下拉箭头颜色
    "ARROW_COLOR": "#475569",
    # 主题能力 token
    "PRIMARY_GRADIENT_START": "#1D4ED8",
    "PRIMARY_GRADIENT_END": "#2563EB",
    "PRIMARY_HOVER_GRADIENT_START": "#2563EB",
    "PRIMARY_HOVER_GRADIENT_END": "#60A5FA",
    "PRIMARY_BUTTON_TEXT": "#FFFFFF",
    "PRIMARY_BUTTON_BORDER": "rgba(37, 99, 235, 0.30)",
    "PRIMARY_BUTTON_PRESSED_BG": "#1D4ED8",
    "SEGMENT_ACTIVE_BG": "rgba(37, 99, 235, 0.12)",
    "SEGMENT_ACTIVE_BORDER": "#2563EB",
    "SEGMENT_ACTIVE_TEXT": "#1E3A8A",
    "PROGRESS_GRADIENT_START": "#1D4ED8",
    "PROGRESS_GRADIENT_MID": "#2563EB",
    "PROGRESS_GRADIENT_END": "#60A5FA",
    "NETWORK_ONLINE": "#0284C7",
    "NETWORK_OFFLINE": "#D92D2D",
    "NETWORK_BUSY": "#B7791F",
    # K线图专用色 — 白底适配版，所有色值加深确保 WCAG 对比度
    "KLINE_UP_COLOR": "#D92D2D",
    "KLINE_DOWN_COLOR": "#0F9F6E",
    "KLINE_MA10": "#111827",
    "KLINE_MA20": "#2563EB",
    "KLINE_MA50": "#EA580C",
    "KLINE_MA150": "#7C3AED",
    "KLINE_MA200": "#C81E1E",
    "KLINE_VOL_MA20": "#B7791F",
    "KLINE_GRID_LINE": "rgba(71, 85, 105, 0.10)",
    "KLINE_AXIS_LINE": "rgba(15, 23, 42, 0.10)",
    "KLINE_AXIS_LABEL": "#475569",
    "KLINE_POINTER_BG": "#94A3B8",
    "KLINE_VCP_STAR": "#B7791F",
    "KLINE_VCP_LINE": "rgba(183, 121, 31, 0.92)",
    "KLINE_VCP_LINE_SOFT": "rgba(183, 121, 31, 0.62)",
    "KLINE_VCP_AREA": "rgba(37, 99, 235, 0.08)",
    "KLINE_VCP_GUIDE": "rgba(183, 121, 31, 0.46)",
    "KLINE_VCP_BREAKOUT_BG": "rgba(183, 121, 31, 0.14)",
    "KLINE_BG_CANVAS": "#FFFFFF",
    "KLINE_BG_TOOLBAR": "#FFFFFF",
    "KLINE_WIDGET_BG": "#FFFFFF",
    "KLINE_WIDGET_TEXT": "#111827",
    "KLINE_TOOLBAR_BG": "#FFFFFF",
    "KLINE_TOOLBAR_BORDER": "rgba(15, 23, 42, 0.05)",
    "KLINE_SUMMARY_BG": "#FFFFFF",
    "KLINE_INFO_COLOR": "#475569",
    "KLINE_BTN_BORDER": "#D6E0EA",
    "KLINE_BTN_HOVER_BG": "#EAF2FC",
    "KLINE_BTN_HOVER_TEXT": "#111827",
    "KLINE_BTN_DISABLED_TEXT": "#94A3B8",
    "KLINE_BTN_DISABLED_BORDER": "rgba(71, 85, 105, 0.12)",
    "KLINE_CHART_BG": "#FFFFFF",
    "KLINE_NAV_BG": "#F0F4FA",
    "KLINE_BADGE_BG": "rgba(37, 99, 235, 0.08)",
    "KLINE_BADGE_FG": "#334155",
    "KLINE_SUMMARY_BORDER": "rgba(15, 23, 42, 0.05)",
    "KLINE_TOOLTIP_BG": "rgba(17, 24, 39, 0.94)",
    "KLINE_TOOLTIP_TEXT": "#F8FAFC",
    "KLINE_MACD_DIFF": "#B7791F",
    "KLINE_MACD_DEA": "#2563EB",
    "KLINE_CROSSHAIR_LINE": "rgba(71, 85, 105, 0.56)",
    "KLINE_DATAZOOM_BG": "#F0F4FA",
    "KLINE_DATAZOOM_FILL": "rgba(37, 99, 235, 0.14)",
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
    "BG_CANVAS": "#06070B",
    "BG_SIDEBAR": "#06070B",
    "BG_SIDEBAR_END": "#0E111D",
    "BG_TABLE_ALT": "#0F1220",
    "BG_CARD": "#121422",
    "BG_HOVER": "rgba(110, 123, 255, 0.08)",
    "BG_INPUT": "#0C0F18",
    "BG_ELEVATED": "#171A2A",
    "BG_TITLEBAR": "#06070B",
    "BG_STATUSBAR": "#06070B",
    "BG_TABLE_BASE": "#0A0D15",
    "BG_TABLE_ALT_ROW": "#0F1220",
    "BG_TABLE_HOVER": "#171B2D",
    "BG_BUTTON": "#151827",
    "BG_BUTTON_HOVER": "#1B2034",
    "BG_MENU": "#121422",
    "BG_GLASS": "rgba(18, 20, 34, 0.96)",
    "BG_TOOLBAR": "#0B0D15",
    "BG_TOOLBAR_END": "#121422",
    "BG_TOOLBAR_CARD": "#121422",
    "BG_TOOLBAR_CHIP": "#151827",
    # 文字色
    "TEXT_PRIMARY": "#F2F6FF",
    "TEXT_SECONDARY": "#B7C2D8",
    "TEXT_MUTED": "#8290AA",
    "TEXT_DISABLED": "#5F6D86",
    "TEXT_HEADER": "#9AA8BF",
    "TEXT_BRIGHT": "#FAFCFF",
    "TEXT_ON_ACCENT": "#FFFFFF",
    "TEXT_ON_DANGER": "#FFFFFF",
    # 品牌色（金色）
    "BRAND_PRIMARY": "#B91C1C",
    "BRAND_HOVER": "#DC2626",
    "BRAND_DEEP": "#7F1D1D",
    "BRAND_PRESSED": "#5F1717",
    "BRAND_SUBTLE": "rgba(185, 28, 28, 0.14)",
    # 交互强调色（冷紫）
    "ACCENT_PRIMARY": "#6E7BFF",
    "ACCENT_HOVER": "#8C96FF",
    "ACCENT_DEEP": "#5660D8",
    "ACCENT_SUBTLE": "rgba(110, 123, 255, 0.13)",
    "ACCENT_BORDER": "rgba(110, 123, 255, 0.28)",
    "ACCENT_TEXT": "#DCE1FF",
    # 功能色 — 涨跌
    "COLOR_RISE": "#FF5A5F",
    "COLOR_RISE_STRONG": "#FF4248",
    "COLOR_FALL": "#22C55E",
    "COLOR_FALL_STRONG": "#16A34A",
    "COLOR_FLAT": "#CBD5E8",
    # 功能色 — 状态
    "COLOR_SUCCESS": "#2DD4BF",
    "COLOR_WARNING": "#F59E0B",
    "COLOR_ERROR": "#EF4444",
    "COLOR_ERROR_HOVER": "#F87171",
    "COLOR_INFO": "#55B7FF",
    "COLOR_REALTIME": "#55B7FF",
    "INFO_BADGE_BG": "rgba(110, 123, 255, 0.08)",
    "INFO_BADGE_BORDER": "rgba(110, 123, 255, 0.20)",
    "INFO_BADGE_FG": "#B7C2D8",
    # 边框色
    "BORDER_DEFAULT": "rgba(110, 123, 255, 0.12)",
    "BORDER_SUBTLE": "rgba(110, 123, 255, 0.08)",
    "BORDER_STRONG": "rgba(110, 123, 255, 0.24)",
    "BORDER_BRAND": "rgba(185, 28, 28, 0.36)",
    "BORDER_MENU": "#33405A",
    "FOCUS_RING": "rgba(110, 123, 255, 0.34)",
    # 评分着色梯度
    "SCORE_EXCELLENT": "#FF5A5F",
    "SCORE_GOOD": "#F59E0B",
    "SCORE_NORMAL": "#55B7FF",
    "SCORE_LOW": "#8290AA",
    # 突破状态着色
    "STATUS_BREAKOUT": "#FF4248",
    "STATUS_APPROACHING": "#F59E0B",
    "STATUS_VCP": "#6E7BFF",
    "STATUS_INACTIVE": "#8290AA",
    # 滚动条颜色
    "SCROLLBAR_HANDLE": "rgba(130, 144, 170, 0.26)",
    "SCROLLBAR_HANDLE_HOVER": "rgba(110, 123, 255, 0.34)",
    "SCROLLBAR_HANDLE_PRESSED": "rgba(110, 123, 255, 0.58)",
    # 选中态颜色
    "SELECTION_BG": "rgba(110, 123, 255, 0.10)",
    "SELECTION_HOVER_BG": "rgba(110, 123, 255, 0.16)",
    "INPUT_SELECTION_BG": "rgba(110, 123, 255, 0.24)",
    "TABLE_SELECTED_RAIL": "#6E7BFF",
    "TABLE_HOVER_RAIL": "#6E7BFF",
    "TABLE_CURRENT_CELL_BG": "rgba(110, 123, 255, 0.08)",
    "TABLE_CURRENT_CELL_BG_SELECTED": "rgba(110, 123, 255, 0.14)",
    "TABLE_CURRENT_CELL_BORDER": "rgba(110, 123, 255, 0.72)",
    "TABLE_FLASH_ALPHA_SCALE": 0.16,
    "TABLE_FLASH_MAX_ALPHA": 42,
    "TABLE_FLASH_RAIL_ALPHA": 120,
    # 标题栏 / 状态栏
    "TITLEBAR_BORDER": "rgba(110, 123, 255, 0.12)",
    "STATUSBAR_BORDER": "rgba(110, 123, 255, 0.12)",
    # 菜单 / 分隔 / 当前标签
    "MENU_SELECTED_BG": "rgba(110, 123, 255, 0.12)",
    "SPLITTER_BG": "rgba(96, 115, 148, 0.14)",
    "SPLITTER_HOVER": "rgba(110, 123, 255, 0.22)",
    "TAB_TEXT": "#93A0B8",
    "TAB_TEXT_HOVER": "#F2F6FF",
    "TAB_HOVER_BG": "#171B2D",
    "TAB_ACTIVE_BG": "rgba(110, 123, 255, 0.15)",
    "TAB_ACTIVE_BORDER": "rgba(110, 123, 255, 0.42)",
    "TAB_ACTIVE_TEXT": "#DCE1FF",
    "TAB_ACTIVE_TOP": "#6E7BFF",
    # 下拉箭头颜色
    "ARROW_COLOR": "#8290AA",
    # 主题能力 token
    "PRIMARY_GRADIENT_START": "#5660D8",
    "PRIMARY_GRADIENT_END": "#6E7BFF",
    "PRIMARY_HOVER_GRADIENT_START": "#6E7BFF",
    "PRIMARY_HOVER_GRADIENT_END": "#8C96FF",
    "PRIMARY_BUTTON_TEXT": "#FFFFFF",
    "PRIMARY_BUTTON_BORDER": "rgba(110, 123, 255, 0.40)",
    "PRIMARY_BUTTON_PRESSED_BG": "#5660D8",
    "SEGMENT_ACTIVE_BG": "rgba(110, 123, 255, 0.15)",
    "SEGMENT_ACTIVE_BORDER": "#6E7BFF",
    "SEGMENT_ACTIVE_TEXT": "#DCE1FF",
    "PROGRESS_GRADIENT_START": "#5660D8",
    "PROGRESS_GRADIENT_MID": "#6E7BFF",
    "PROGRESS_GRADIENT_END": "#8C96FF",
    "NETWORK_ONLINE": "#55B7FF",
    "NETWORK_OFFLINE": "#EF4444",
    "NETWORK_BUSY": "#F59E0B",
    # K线图专用色
    "KLINE_UP_COLOR": "#FF5A5F",
    "KLINE_DOWN_COLOR": "#22C55E",
    "KLINE_MA10": "rgba(210, 219, 235, 0.82)",
    "KLINE_MA20": "rgba(85, 183, 255, 0.82)",
    "KLINE_MA50": "rgba(215, 172, 69, 0.42)",
    "KLINE_MA150": "rgba(110, 123, 255, 0.34)",
    "KLINE_MA200": "rgba(255, 138, 143, 0.36)",
    "KLINE_VOL_MA20": "#E9C867",
    "KLINE_GRID_LINE": "rgba(96, 115, 148, 0.06)",
    "KLINE_AXIS_LINE": "#42516E",
    "KLINE_AXIS_LABEL": "#8290AA",
    "KLINE_POINTER_BG": "rgba(55, 63, 133, 0.88)",
    "KLINE_VCP_STAR": "#FFD700",
    "KLINE_VCP_LINE": "rgba(215, 172, 69, 0.95)",
    "KLINE_VCP_LINE_SOFT": "rgba(215, 172, 69, 0.72)",
    "KLINE_VCP_AREA": "rgba(110, 123, 255, 0.11)",
    "KLINE_VCP_GUIDE": "rgba(215, 172, 69, 0.62)",
    "KLINE_VCP_BREAKOUT_BG": "rgba(215, 172, 69, 0.18)",
    "KLINE_BG_CANVAS": "#06070B",
    "KLINE_BG_TOOLBAR": "#0B0D15",
    "KLINE_WIDGET_BG": "#121422",
    "KLINE_WIDGET_TEXT": "#F2F6FF",
    "KLINE_TOOLBAR_BG": "#0B0D15",
    "KLINE_TOOLBAR_BORDER": "rgba(110, 123, 255, 0.12)",
    "KLINE_SUMMARY_BG": "#121422",
    "KLINE_INFO_COLOR": "#B7C2D8",
    "KLINE_BTN_BORDER": "rgba(110, 123, 255, 0.24)",
    "KLINE_BTN_HOVER_BG": "#171B2D",
    "KLINE_BTN_HOVER_TEXT": "#F2F6FF",
    "KLINE_BTN_DISABLED_TEXT": "#5F6D86",
    "KLINE_BTN_DISABLED_BORDER": "rgba(110, 123, 255, 0.12)",
    "KLINE_CHART_BG": "#06070B",
    "KLINE_NAV_BG": "#151827",
    "KLINE_BADGE_BG": "rgba(110, 123, 255, 0.08)",
    "KLINE_BADGE_FG": "#B7C2D8",
    "KLINE_SUMMARY_BORDER": "rgba(110, 123, 255, 0.12)",
    "KLINE_TOOLTIP_BG": "rgba(13, 20, 36, 0.94)",
    "KLINE_TOOLTIP_TEXT": "#F3F7FF",
    "KLINE_MACD_DIFF": "#D7AC45",
    "KLINE_MACD_DEA": "#55B7FF",
    "KLINE_CROSSHAIR_LINE": "rgba(110, 123, 255, 0.40)",
    "KLINE_DATAZOOM_BG": "#121422",
    "KLINE_DATAZOOM_FILL": "rgba(110, 123, 255, 0.11)",
    "KLINE_DATAZOOM_HANDLE": "#607394",
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
