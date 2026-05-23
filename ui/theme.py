# -*- coding: utf-8 -*-
"""
ui/theme.py
紫金研选 — 多主题色彩系统（墨渊 / 月白 / 紫曜 / 曜黑）

所有 UI 组件应引用此文件中的常量或调用 ThemeManager 获取当前主题色值。
为什么用单例+信号？因为主题切换需要通知所有已创建的组件刷新样式，
就像广播电台——发一次信号，所有收音机同时收到。
"""

from datetime import datetime as _datetime

from PyQt6.QtCore import QObject, QTimer, pyqtSignal

from app.services.ui_config_service import app_config

DEFAULT_THEME_NAME = "紫曜"
DEFAULT_THEME_MIGRATION_KEY = "default_theme_v2_applied"

# ============================================================
# 墨渊主题（暗色，即当前默认主题）
# ============================================================
THEME_MOYUAN = {
    "name": "墨渊",
    "appearance": "dark",
    # 背景色层级体系（深 → 浅）
    "BG_CANVAS": "#0E1116",
    "BG_SIDEBAR": "#131820",
    "BG_TABLE_ALT": "#151B23",
    "BG_CARD": "#171C24",
    "BG_HOVER": "#1B212B",
    "BG_INPUT": "#10151D",
    "BG_ELEVATED": "#1C232E",
    "BG_TITLEBAR": "#0C1016",
    "BG_STATUSBAR": "#0C1016",
    "BG_TABLE_BASE": "#10151D",
    "BG_TABLE_ALT_ROW": "#141B24",
    "BG_TABLE_HOVER": "rgba(148, 163, 184, 0.10)",
    "BG_BUTTON": "#18202A",
    "BG_BUTTON_HOVER": "#202835",
    "BG_MENU": "#11161E",
    "BG_GLASS": "rgba(14, 17, 22, 0.96)",
    # 文字色
    "TEXT_PRIMARY": "#E5E7EB",
    "TEXT_SECONDARY": "#A8B3C2",
    "TEXT_MUTED": "#7B8794",
    "TEXT_DISABLED": "#4B5563",
    "TEXT_HEADER": "#8A96A8",
    "TEXT_BRIGHT": "#F9FAFB",
    # 品牌色
    "BRAND_PRIMARY": "#EF4444",
    "BRAND_HOVER": "#F87171",
    "BRAND_DEEP": "#B91C1C",
    "BRAND_SUBTLE": "rgba(239, 68, 68, 0.12)",
    # 功能色 — 涨跌
    "COLOR_RISE": "#FC8181",
    "COLOR_RISE_STRONG": "#E85D5D",
    "COLOR_FALL": "#68D391",
    "COLOR_FALL_STRONG": "#3CC68A",
    "COLOR_FLAT": "#C9CDD4",
    # 功能色 — 状态
    "COLOR_SUCCESS": "#10B981",
    "COLOR_WARNING": "#F59E0B",
    "COLOR_ERROR": "#EF4444",
    "COLOR_INFO": "#3B82F6",
    # 边框色
    "BORDER_DEFAULT": "rgba(148, 163, 184, 0.12)",
    "BORDER_SUBTLE": "rgba(148, 163, 184, 0.08)",
    "BORDER_STRONG": "#2A3342",
    "BORDER_BRAND": "rgba(239, 68, 68, 0.18)",
    "BORDER_MENU": "#232B36",
    # 评分着色梯度
    "SCORE_EXCELLENT": "#FF4757",
    "SCORE_GOOD": "#F59E0B",
    "SCORE_NORMAL": "#3A82F6",
    "SCORE_LOW": "#8E8E93",
    # 突破状态着色
    "STATUS_BREAKOUT": "#E85D5D",
    "STATUS_APPROACHING": "#FFD60A",
    "STATUS_VCP": "#3A82F6",
    "STATUS_INACTIVE": "#8E8E93",
    # 滚动条颜色
    "SCROLLBAR_HANDLE": "rgba(239, 68, 68, 0.25)",
    "SCROLLBAR_HANDLE_HOVER": "rgba(239, 68, 68, 0.45)",
    # 选中态颜色
    "SELECTION_BG": "rgba(239, 68, 68, 0.14)",
    "SELECTION_HOVER_BG": "rgba(239, 68, 68, 0.08)",
    # 标题栏分隔线颜色
    "TITLEBAR_BORDER": "rgba(148, 163, 184, 0.10)",
    "STATUSBAR_BORDER": "rgba(148, 163, 184, 0.10)",
    # 齿轮菜单选中色
    "MENU_SELECTED_BG": "rgba(239, 68, 68, 0.12)",
    # Splitter
    "SPLITTER_BG": "rgba(239, 68, 68, 0.08)",
    "SPLITTER_HOVER": "rgba(239, 68, 68, 0.25)",
    # Tab
    "TAB_TEXT": "#7B8794",
    "TAB_TEXT_HOVER": "#E5E7EB",
    "TAB_HOVER_BG": "rgba(255,255,255,0.05)",
    # 下拉箭头颜色
    "ARROW_COLOR": "#718096",
    # K线图专用色 — 涨跌、均线、网格、VCP 覆盖层（值与当前硬编码完全一致，保证零变化）
    "KLINE_UP_COLOR": "#F92855",
    "KLINE_DOWN_COLOR": "#00FFFF",
    "KLINE_MA10": "#FFFFFF",
    "KLINE_MA20": "#00A2E8",
    "KLINE_MA50": "#FF9000",
    "KLINE_MA150": "#BF5AF2",
    "KLINE_MA200": "#FF375F",
    "KLINE_VOL_MA20": "#FFD700",
    "KLINE_GRID_LINE": "rgba(255,255,255,0.05)",
    "KLINE_AXIS_LINE": "#444444",
    "KLINE_AXIS_LABEL": "#888888",
    "KLINE_POINTER_BG": "#777777",
    "KLINE_VCP_STAR": "#FFD60A",
    "KLINE_VCP_LINE": "rgba(245, 198, 92, 0.96)",
    "KLINE_VCP_LINE_SOFT": "rgba(245, 198, 92, 0.72)",
    "KLINE_VCP_AREA": "rgba(245, 198, 92, 0.11)",
    "KLINE_VCP_GUIDE": "rgba(245, 198, 92, 0.62)",
    "KLINE_VCP_BREAKOUT_BG": "rgba(245, 198, 92, 0.18)",
}

# ============================================================
# 月白主题（亮色，参照 Gemini 页面风格）
# ============================================================
THEME_YUEBAI = {
    "name": "月白",
    "appearance": "light",
    # 背景色层级体系（冷白 → 纸白），避免浅色模式变成一整片米色。
    "BG_CANVAS": "#F4F7FB",
    "BG_SIDEBAR": "#EAF0F7",
    "BG_TABLE_ALT": "#F7FAFD",
    "BG_CARD": "#FFFFFF",
    "BG_HOVER": "#E8F1FB",
    "BG_INPUT": "#FFFFFF",
    "BG_ELEVATED": "#F8FBFF",
    "BG_TITLEBAR": "#EEF4FA",
    "BG_STATUSBAR": "#EEF4FA",
    "BG_TABLE_BASE": "#FFFFFF",
    "BG_TABLE_ALT_ROW": "#F7FAFD",
    "BG_TABLE_HOVER": "#EDF5FF",
    "BG_BUTTON": "#F3F7FB",
    "BG_BUTTON_HOVER": "#E7EFF8",
    "BG_MENU": "#FFFFFF",
    "BG_GLASS": "rgba(244, 247, 251, 0.97)",
    "BG_TOOLBAR": "#F8FBFF",
    "BG_TOOLBAR_CARD": "#FFFFFF",
    "BG_TOOLBAR_CHIP": "#EEF4FA",
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
    "ACCENT_SUBTLE": "rgba(37, 99, 235, 0.10)",
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
    "INFO_BADGE_BG": "rgba(37, 99, 235, 0.10)",
    "INFO_BADGE_BORDER": "rgba(37, 99, 235, 0.22)",
    "INFO_BADGE_FG": "#1E3A8A",
    # 边框色 — 白底用深色半透明
    "BORDER_DEFAULT": "rgba(71, 85, 105, 0.18)",
    "BORDER_SUBTLE": "rgba(71, 85, 105, 0.10)",
    "BORDER_STRONG": "#D6E0EA",
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
    # 标题栏分隔线
    "TITLEBAR_BORDER": "rgba(71, 85, 105, 0.18)",
    "STATUSBAR_BORDER": "rgba(71, 85, 105, 0.18)",
    # 齿轮菜单选中色
    "MENU_SELECTED_BG": "rgba(37, 99, 235, 0.10)",
    # Splitter
    "SPLITTER_BG": "rgba(71, 85, 105, 0.10)",
    "SPLITTER_HOVER": "rgba(37, 99, 235, 0.20)",
    # Tab
    "TAB_TEXT": "#475569",
    "TAB_TEXT_HOVER": "#111827",
    "TAB_HOVER_BG": "#EAF2FC",
    "TAB_ACTIVE_BG": "#FFFFFF",
    "TAB_ACTIVE_BORDER": "rgba(37, 99, 235, 0.26)",
    "TAB_ACTIVE_TEXT": "#1E3A8A",
    "TAB_ACTIVE_TOP": "#2563EB",
    # 下拉箭头颜色
    "ARROW_COLOR": "#475569",
    # 主题能力 token
    "PRIMARY_GRADIENT_START": "#D92D2D",
    "PRIMARY_GRADIENT_END": "#B91C1C",
    "PRIMARY_HOVER_GRADIENT_START": "#E23B3B",
    "PRIMARY_HOVER_GRADIENT_END": "#C81E1E",
    "PRIMARY_BUTTON_TEXT": "#FFFFFF",
    "PRIMARY_BUTTON_BORDER": "rgba(200, 30, 30, 0.30)",
    "PRIMARY_BUTTON_PRESSED_BG": "#991B1B",
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
    "KLINE_AXIS_LINE": "#D6E0EA",
    "KLINE_AXIS_LABEL": "#475569",
    "KLINE_POINTER_BG": "#94A3B8",
    "KLINE_VCP_STAR": "#B7791F",
    "KLINE_VCP_LINE": "rgba(183, 121, 31, 0.92)",
    "KLINE_VCP_LINE_SOFT": "rgba(183, 121, 31, 0.62)",
    "KLINE_VCP_AREA": "rgba(37, 99, 235, 0.08)",
    "KLINE_VCP_GUIDE": "rgba(183, 121, 31, 0.46)",
    "KLINE_VCP_BREAKOUT_BG": "rgba(183, 121, 31, 0.14)",
    "KLINE_BG_CANVAS": "#F4F7FB",
    "KLINE_BG_TOOLBAR": "#F8FBFF",
    "KLINE_WIDGET_BG": "#F8FBFF",
    "KLINE_WIDGET_TEXT": "#111827",
    "KLINE_TOOLBAR_BG": "#F8FBFF",
    "KLINE_TOOLBAR_BORDER": "rgba(71, 85, 105, 0.18)",
    "KLINE_SUMMARY_BG": "#F8FBFF",
    "KLINE_INFO_COLOR": "#475569",
    "KLINE_BTN_BORDER": "#D6E0EA",
    "KLINE_BTN_HOVER_BG": "#EAF2FC",
    "KLINE_BTN_HOVER_TEXT": "#111827",
    "KLINE_BTN_DISABLED_TEXT": "#94A3B8",
    "KLINE_BTN_DISABLED_BORDER": "rgba(71, 85, 105, 0.12)",
    "KLINE_CHART_BG": "#F8FBFF",
    "KLINE_NAV_BG": "#EEF4FA",
    "KLINE_BADGE_BG": "rgba(37, 99, 235, 0.10)",
    "KLINE_BADGE_FG": "#1E3A8A",
    "KLINE_SUMMARY_BORDER": "rgba(71, 85, 105, 0.12)",
    "KLINE_TOOLTIP_BG": "rgba(17, 24, 39, 0.94)",
    "KLINE_TOOLTIP_TEXT": "#F8FAFC",
    "KLINE_MACD_DIFF": "#B7791F",
    "KLINE_MACD_DEA": "#2563EB",
    "KLINE_CROSSHAIR_LINE": "rgba(71, 85, 105, 0.56)",
    "KLINE_DATAZOOM_BG": "#EEF4FA",
    "KLINE_DATAZOOM_FILL": "rgba(37, 99, 235, 0.14)",
    "KLINE_DATAZOOM_HANDLE": "#94A3B8",
    "KLINE_FONT_FAMILY": '"Microsoft YaHei UI", "Microsoft YaHei", "Segoe UI", sans-serif',
    "KLINE_MONO_FONT_FAMILY": '"JetBrains Mono", "Cascadia Mono", "Consolas", "Microsoft YaHei UI", monospace',
}

# ============================================================
# 紫曜主题（新增暗色，金融终端语义）
# ============================================================
THEME_ZIYAO = {
    "name": "紫曜",
    "appearance": "dark",
    # 背景色层级体系（深蓝黑 → 终端面板）
    "BG_CANVAS": "#090D14",
    "BG_SIDEBAR": "#0F1622",
    "BG_TABLE_ALT": "#151E2D",
    "BG_CARD": "#111827",
    "BG_HOVER": "#1C2940",
    "BG_INPUT": "#0B111B",
    "BG_ELEVATED": "#172033",
    "BG_TITLEBAR": "#0D1420",
    "BG_STATUSBAR": "#0D1420",
    "BG_TABLE_BASE": "#101723",
    "BG_TABLE_ALT_ROW": "#151F31",
    "BG_TABLE_HOVER": "#1C2940",
    "BG_BUTTON": "#182234",
    "BG_BUTTON_HOVER": "#24324A",
    "BG_MENU": "#101827",
    "BG_GLASS": "rgba(9, 13, 20, 0.96)",
    "BG_TOOLBAR": "#101826",
    "BG_TOOLBAR_CARD": "#172033",
    "BG_TOOLBAR_CHIP": "#1A2639",
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
    "INFO_BADGE_BG": "rgba(85, 183, 255, 0.16)",
    "INFO_BADGE_BORDER": "rgba(85, 183, 255, 0.28)",
    "INFO_BADGE_FG": "#F2F6FF",
    # 边框色
    "BORDER_DEFAULT": "rgba(96, 115, 148, 0.34)",
    "BORDER_SUBTLE": "rgba(96, 115, 148, 0.20)",
    "BORDER_STRONG": "#42516E",
    "BORDER_BRAND": "rgba(185, 28, 28, 0.36)",
    "BORDER_MENU": "#33405A",
    "FOCUS_RING": "rgba(185, 28, 28, 0.34)",
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
    "SCROLLBAR_HANDLE_HOVER": "rgba(185, 28, 28, 0.34)",
    "SCROLLBAR_HANDLE_PRESSED": "rgba(185, 28, 28, 0.58)",
    # 选中态颜色
    "SELECTION_BG": "rgba(185, 28, 28, 0.08)",
    "SELECTION_HOVER_BG": "rgba(185, 28, 28, 0.13)",
    "INPUT_SELECTION_BG": "rgba(185, 28, 28, 0.26)",
    "TABLE_SELECTED_RAIL": "#DC2626",
    "TABLE_CURRENT_CELL_BG": "rgba(185, 28, 28, 0.06)",
    "TABLE_CURRENT_CELL_BG_SELECTED": "rgba(185, 28, 28, 0.10)",
    "TABLE_CURRENT_CELL_BORDER": "rgba(220, 38, 38, 0.72)",
    # 标题栏 / 状态栏
    "TITLEBAR_BORDER": "rgba(96, 115, 148, 0.34)",
    "STATUSBAR_BORDER": "rgba(96, 115, 148, 0.34)",
    # 菜单 / 分隔 / 当前标签
    "MENU_SELECTED_BG": "rgba(185, 28, 28, 0.11)",
    "SPLITTER_BG": "rgba(96, 115, 148, 0.14)",
    "SPLITTER_HOVER": "rgba(185, 28, 28, 0.20)",
    "TAB_TEXT": "#93A0B8",
    "TAB_TEXT_HOVER": "#F2F6FF",
    "TAB_HOVER_BG": "#1C2940",
    "TAB_ACTIVE_BG": "rgba(185, 28, 28, 0.14)",
    "TAB_ACTIVE_BORDER": "rgba(220, 38, 38, 0.42)",
    "TAB_ACTIVE_TEXT": "#FFF1F2",
    "TAB_ACTIVE_TOP": "#DC2626",
    # 下拉箭头颜色
    "ARROW_COLOR": "#8290AA",
    # 主题能力 token
    "PRIMARY_GRADIENT_START": "#B91C1C",
    "PRIMARY_GRADIENT_END": "#7F1D1D",
    "PRIMARY_HOVER_GRADIENT_START": "#DC2626",
    "PRIMARY_HOVER_GRADIENT_END": "#991B1B",
    "PRIMARY_BUTTON_TEXT": "#FFF7F7",
    "PRIMARY_BUTTON_BORDER": "rgba(248, 113, 113, 0.40)",
    "PRIMARY_BUTTON_PRESSED_BG": "#5F1717",
    "SEGMENT_ACTIVE_BG": "rgba(185, 28, 28, 0.13)",
    "SEGMENT_ACTIVE_BORDER": "#DC2626",
    "SEGMENT_ACTIVE_TEXT": "#FFF1F2",
    "PROGRESS_GRADIENT_START": "#7F1D1D",
    "PROGRESS_GRADIENT_MID": "#DC2626",
    "PROGRESS_GRADIENT_END": "#F87171",
    "NETWORK_ONLINE": "#55B7FF",
    "NETWORK_OFFLINE": "#EF4444",
    "NETWORK_BUSY": "#F59E0B",
    # K线图专用色
    "KLINE_UP_COLOR": "#FF5A5F",
    "KLINE_DOWN_COLOR": "#22C55E",
    "KLINE_MA10": "#F2F6FF",
    "KLINE_MA20": "#55B7FF",
    "KLINE_MA50": "#D7AC45",
    "KLINE_MA150": "#6E7BFF",
    "KLINE_MA200": "#FF8A8F",
    "KLINE_VOL_MA20": "#E9C867",
    "KLINE_GRID_LINE": "rgba(96, 115, 148, 0.20)",
    "KLINE_AXIS_LINE": "#42516E",
    "KLINE_AXIS_LABEL": "#8290AA",
    "KLINE_POINTER_BG": "#24324A",
    "KLINE_VCP_STAR": "#D7AC45",
    "KLINE_VCP_LINE": "rgba(215, 172, 69, 0.95)",
    "KLINE_VCP_LINE_SOFT": "rgba(215, 172, 69, 0.72)",
    "KLINE_VCP_AREA": "rgba(110, 123, 255, 0.11)",
    "KLINE_VCP_GUIDE": "rgba(215, 172, 69, 0.62)",
    "KLINE_VCP_BREAKOUT_BG": "rgba(215, 172, 69, 0.18)",
    "KLINE_BG_CANVAS": "#090D14",
    "KLINE_BG_TOOLBAR": "#172033",
    "KLINE_WIDGET_BG": "#111827",
    "KLINE_WIDGET_TEXT": "#F2F6FF",
    "KLINE_TOOLBAR_BG": "#172033",
    "KLINE_TOOLBAR_BORDER": "rgba(96, 115, 148, 0.34)",
    "KLINE_SUMMARY_BG": "#101826",
    "KLINE_INFO_COLOR": "#B7C2D8",
    "KLINE_BTN_BORDER": "#42516E",
    "KLINE_BTN_HOVER_BG": "#1C2940",
    "KLINE_BTN_HOVER_TEXT": "#F2F6FF",
    "KLINE_BTN_DISABLED_TEXT": "#5F6D86",
    "KLINE_BTN_DISABLED_BORDER": "rgba(96, 115, 148, 0.24)",
    "KLINE_CHART_BG": "#090D14",
    "KLINE_NAV_BG": "#1A2639",
    "KLINE_BADGE_BG": "rgba(85, 183, 255, 0.16)",
    "KLINE_BADGE_FG": "#DCEEFF",
    "KLINE_SUMMARY_BORDER": "rgba(96, 115, 148, 0.26)",
    "KLINE_TOOLTIP_BG": "rgba(13, 20, 36, 0.94)",
    "KLINE_TOOLTIP_TEXT": "#F3F7FF",
    "KLINE_MACD_DIFF": "#D7AC45",
    "KLINE_MACD_DEA": "#55B7FF",
}

# ============================================================
# 曜黑主题（石墨黑交易工作台）
# ============================================================
THEME_YAOHEI = {
    "name": "曜黑",
    "appearance": "dark",
    # 背景色层级体系：石墨黑为主，不引入紫红底色。
    "BG_CANVAS": "#0A0C0F",
    "BG_SIDEBAR": "#101419",
    "BG_TABLE_ALT": "#11161B",
    "BG_CARD": "#11161B",
    "BG_HOVER": "#1A2027",
    "BG_INPUT": "#0D1116",
    "BG_ELEVATED": "#151B22",
    "BG_TITLEBAR": "#0B0E12",
    "BG_STATUSBAR": "#0B0E12",
    "BG_TABLE_BASE": "#0D1116",
    "BG_TABLE_ALT_ROW": "#10151B",
    "BG_TABLE_HOVER": "rgba(148, 163, 184, 0.08)",
    "BG_BUTTON": "#151B22",
    "BG_BUTTON_HOVER": "#1E2630",
    "BG_MENU": "#0F1318",
    "BG_GLASS": "rgba(10, 12, 15, 0.96)",
    "BG_TOOLBAR": "#0E1217",
    "BG_TOOLBAR_CARD": "#121820",
    "BG_TOOLBAR_CHIP": "#151B22",
    "TEXT_PRIMARY": "#E8EDF2",
    "TEXT_SECONDARY": "#AAB4C0",
    "TEXT_MUTED": "#7F8A96",
    "TEXT_DISABLED": "#4E5965",
    "TEXT_HEADER": "#94A0AD",
    "TEXT_BRIGHT": "#F7FAFC",
    "TEXT_ON_ACCENT": "#FFFFFF",
    "TEXT_ON_DANGER": "#FFFFFF",
    # 红色只承担上涨、风险和主操作按钮。
    "BRAND_PRIMARY": "#C93A3A",
    "BRAND_HOVER": "#E24A4A",
    "BRAND_DEEP": "#9F2424",
    "BRAND_PRESSED": "#781C1C",
    "BRAND_SUBTLE": "rgba(201, 58, 58, 0.10)",
    # 青蓝用于选中、焦点和数据联动；琥珀用于提醒和中性强调。
    "ACCENT_PRIMARY": "#22C7D8",
    "ACCENT_HOVER": "#3DDDEA",
    "ACCENT_DEEP": "#1497A8",
    "ACCENT_SUBTLE": "rgba(34, 199, 216, 0.11)",
    "ACCENT_BORDER": "rgba(34, 199, 216, 0.30)",
    "ACCENT_TEXT": "#BDF7FF",
    "COLOR_RISE": "#E05252",
    "COLOR_RISE_STRONG": "#F05B5B",
    "COLOR_FALL": "#2AC77D",
    "COLOR_FALL_STRONG": "#20A968",
    "COLOR_FLAT": "#AAB4C0",
    "COLOR_SUCCESS": "#21BFAE",
    "COLOR_WARNING": "#D7A13D",
    "COLOR_ERROR": "#E05252",
    "COLOR_ERROR_HOVER": "#F05B5B",
    "COLOR_INFO": "#22C7D8",
    "COLOR_REALTIME": "#22C7D8",
    "INFO_BADGE_BG": "rgba(34, 199, 216, 0.11)",
    "INFO_BADGE_BORDER": "rgba(34, 199, 216, 0.25)",
    "INFO_BADGE_FG": "#DFFBFF",
    "BORDER_DEFAULT": "rgba(139, 151, 166, 0.22)",
    "BORDER_SUBTLE": "rgba(139, 151, 166, 0.12)",
    "BORDER_STRONG": "#2B3540",
    "BORDER_BRAND": "rgba(34, 199, 216, 0.26)",
    "BORDER_MENU": "#27313B",
    "FOCUS_RING": "rgba(34, 199, 216, 0.34)",
    "SCORE_EXCELLENT": "#E05252",
    "SCORE_GOOD": "#D7A13D",
    "SCORE_NORMAL": "#22C7D8",
    "SCORE_LOW": "#7F8A96",
    "STATUS_BREAKOUT": "#E05252",
    "STATUS_APPROACHING": "#D7A13D",
    "STATUS_VCP": "#22C7D8",
    "STATUS_INACTIVE": "#7F8A96",
    "SCROLLBAR_HANDLE": "rgba(139, 151, 166, 0.24)",
    "SCROLLBAR_HANDLE_HOVER": "rgba(34, 199, 216, 0.34)",
    "SCROLLBAR_HANDLE_PRESSED": "rgba(34, 199, 216, 0.52)",
    "SELECTION_BG": "rgba(34, 199, 216, 0.13)",
    "SELECTION_HOVER_BG": "rgba(34, 199, 216, 0.18)",
    "INPUT_SELECTION_BG": "rgba(34, 199, 216, 0.24)",
    "TABLE_SELECTED_RAIL": "#22C7D8",
    "TABLE_CURRENT_CELL_BG": "rgba(34, 199, 216, 0.08)",
    "TABLE_CURRENT_CELL_BG_SELECTED": "rgba(34, 199, 216, 0.16)",
    "TABLE_CURRENT_CELL_BORDER": "rgba(34, 199, 216, 0.72)",
    "TABLE_FLASH_ALPHA_SCALE": 0.16,
    "TABLE_FLASH_MAX_ALPHA": 46,
    "TITLEBAR_BORDER": "rgba(139, 151, 166, 0.18)",
    "TITLEBAR_BRAND_TEXT": "#E8EDF2",
    "STATUSBAR_BORDER": "rgba(139, 151, 166, 0.18)",
    "TITLEBAR_PULSE": "#22C7D8",
    "STATUS_FLOW_WORKING": "#D7A13D",
    "MENU_SELECTED_BG": "rgba(34, 199, 216, 0.12)",
    "SPLITTER_BG": "rgba(139, 151, 166, 0.12)",
    "SPLITTER_HOVER": "rgba(34, 199, 216, 0.26)",
    "TAB_TEXT": "#7F8A96",
    "TAB_TEXT_HOVER": "#DDE6EE",
    "TAB_HOVER_BG": "rgba(232, 237, 242, 0.05)",
    "TAB_ACTIVE_BG": "rgba(34, 199, 216, 0.09)",
    "TAB_ACTIVE_BORDER": "rgba(34, 199, 216, 0.24)",
    "TAB_ACTIVE_TEXT": "#DFFBFF",
    "TAB_ACTIVE_TOP": "transparent",
    "TAB_ACTIVE_INDICATOR": "#22C7D8",
    "TAB_ACTIVE_INDICATOR_SIDE": "bottom",
    "ARROW_COLOR": "#7F8A96",
    "PRIMARY_GRADIENT_START": "#C93A3A",
    "PRIMARY_GRADIENT_END": "#9F2424",
    "PRIMARY_HOVER_GRADIENT_START": "#E24A4A",
    "PRIMARY_HOVER_GRADIENT_END": "#B72E2E",
    "PRIMARY_BUTTON_TEXT": "#FFF7F7",
    "PRIMARY_BUTTON_BORDER": "rgba(240, 91, 91, 0.36)",
    "PRIMARY_BUTTON_PRESSED_BG": "#781C1C",
    "SEGMENT_ACTIVE_BG": "rgba(34, 199, 216, 0.12)",
    "SEGMENT_ACTIVE_BORDER": "#22C7D8",
    "SEGMENT_ACTIVE_TEXT": "#DFFBFF",
    "PROGRESS_GRADIENT_START": "#1497A8",
    "PROGRESS_GRADIENT_MID": "#22C7D8",
    "PROGRESS_GRADIENT_END": "#D7A13D",
    "NETWORK_ONLINE": "#22C7D8",
    "NETWORK_OFFLINE": "#E05252",
    "NETWORK_BUSY": "#D7A13D",
    "KLINE_UP_COLOR": "#E05252",
    "KLINE_DOWN_COLOR": "#2AC77D",
    "KLINE_MA10": "#DDE6EE",
    "KLINE_MA20": "#22C7D8",
    "KLINE_MA50": "#D7A13D",
    "KLINE_MA150": "#9FB3C8",
    "KLINE_MA200": "#F05B5B",
    "KLINE_VOL_MA20": "#D7A13D",
    "KLINE_GRID_LINE": "rgba(139, 151, 166, 0.12)",
    "KLINE_AXIS_LINE": "#2B3540",
    "KLINE_AXIS_LABEL": "#7F8A96",
    "KLINE_POINTER_BG": "#16252B",
    "KLINE_VCP_STAR": "#D7A13D",
    "KLINE_VCP_LINE": "rgba(215, 161, 61, 0.88)",
    "KLINE_VCP_LINE_SOFT": "rgba(215, 161, 61, 0.58)",
    "KLINE_VCP_AREA": "rgba(34, 199, 216, 0.08)",
    "KLINE_VCP_GUIDE": "rgba(215, 161, 61, 0.42)",
    "KLINE_VCP_BREAKOUT_BG": "rgba(215, 161, 61, 0.13)",
    "KLINE_BG_CANVAS": "#0A0C0F",
    "KLINE_BG_TOOLBAR": "#0E1217",
    "KLINE_WIDGET_BG": "#101419",
    "KLINE_WIDGET_TEXT": "#E8EDF2",
    "KLINE_TOOLBAR_BG": "#0E1217",
    "KLINE_TOOLBAR_BORDER": "rgba(139, 151, 166, 0.18)",
    "KLINE_SUMMARY_BG": "#0D1116",
    "KLINE_INFO_COLOR": "#AAB4C0",
    "KLINE_BTN_BORDER": "#2B3540",
    "KLINE_BTN_HOVER_BG": "#1A2027",
    "KLINE_BTN_HOVER_TEXT": "#E8EDF2",
    "KLINE_BTN_DISABLED_TEXT": "#4E5965",
    "KLINE_BTN_DISABLED_BORDER": "rgba(139, 151, 166, 0.12)",
    "KLINE_CHART_BG": "#0A0C0F",
    "KLINE_NAV_BG": "#121820",
    "KLINE_BADGE_BG": "rgba(34, 199, 216, 0.11)",
    "KLINE_BADGE_FG": "#DFFBFF",
    "KLINE_SUMMARY_BORDER": "rgba(139, 151, 166, 0.16)",
    "KLINE_TOOLTIP_BG": "rgba(10, 12, 15, 0.96)",
    "KLINE_TOOLTIP_TEXT": "#E8EDF2",
    "KLINE_MACD_DIFF": "#D7A13D",
    "KLINE_MACD_DEA": "#22C7D8",
    "KLINE_CROSSHAIR_LINE": "rgba(34, 199, 216, 0.62)",
    "KLINE_DATAZOOM_BG": "#0E1217",
    "KLINE_DATAZOOM_FILL": "rgba(34, 199, 216, 0.14)",
    "KLINE_DATAZOOM_HANDLE": "#22C7D8",
    "KLINE_FONT_FAMILY": '"Microsoft YaHei UI", "Microsoft YaHei", "Segoe UI", sans-serif',
    "KLINE_MONO_FONT_FAMILY": '"JetBrains Mono", "Cascadia Mono", "Consolas", "Microsoft YaHei UI", monospace',
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


THEME_MOYUAN = _with_alias_tokens(THEME_MOYUAN)
THEME_YUEBAI = _with_alias_tokens(THEME_YUEBAI)
THEME_ZIYAO = _with_alias_tokens(THEME_ZIYAO)
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
        "墨渊": THEME_MOYUAN,
        "月白": THEME_YUEBAI,
        "紫曜": THEME_ZIYAO,
        "曜黑": THEME_YAOHEI,
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
        # 从持久化配置恢复上次选择的主题；v2 默认切到“紫曜”。
        saved = self._settings.value("current_theme", None)
        if not self._settings.contains(DEFAULT_THEME_MIGRATION_KEY):
            if not saved or saved == "墨渊":
                saved = DEFAULT_THEME_NAME
                self._settings.setValue("current_theme", saved)
            self._settings.setValue(DEFAULT_THEME_MIGRATION_KEY, True)
            self._settings.sync()
        elif not saved:
            saved = DEFAULT_THEME_NAME
        self._current_name = saved if saved in self.THEMES else DEFAULT_THEME_NAME

        # 日夜自动切换：白天月白、晚上墨渊，像手机的自动暗色模式
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
        return self.THEMES.get(self._current_name, THEME_MOYUAN)

    def get(self, token: str) -> str:
        """获取当前主题的某个 token 值"""
        return self.current_theme.get(token, "")

    def switch_theme(self, name: str):
        """切换主题并广播信号"""
        if name not in self.THEMES:
            return
        if name == self._current_name:
            return
        if self._auto_switch and name not in {"墨渊", "月白"}:
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
        规则：7:00–18:00 → 月白（亮色），其余时段 → 墨渊（暗色）。
        就像太阳升起开灯，太阳落山关灯。
        """
        if not self._auto_switch:
            return
        hour = _datetime.now().hour
        # 白天 7:00 ~ 17:59 用月白，晚上用墨渊
        target = "月白" if 7 <= hour < 18 else "墨渊"
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
