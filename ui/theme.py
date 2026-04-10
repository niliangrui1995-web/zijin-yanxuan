# -*- coding: utf-8 -*-
"""
ui/theme.py
紫金研选 — 双主题色彩系统（墨渊 / 月白）

所有 UI 组件应引用此文件中的常量或调用 ThemeManager 获取当前主题色值。
为什么用单例+信号？因为主题切换需要通知所有已创建的组件刷新样式，
就像广播电台——发一次信号，所有收音机同时收到。
"""
from PyQt6.QtCore import QObject, pyqtSignal, QSettings, QTimer
from datetime import datetime as _datetime

# ============================================================
# 墨渊主题（暗色，即当前默认主题）
# ============================================================
THEME_MOYUAN = {
    "name": "墨渊",

    # 背景色层级体系（深 → 浅）
    "BG_CANVAS": "#0F1117",
    "BG_SIDEBAR": "#161B26",
    "BG_TABLE_ALT": "#161B26",
    "BG_CARD": "#1A1F2E",
    "BG_HOVER": "#1C212B",
    "BG_INPUT": "#0D1117",
    "BG_ELEVATED": "#252A36",
    "BG_TITLEBAR": "#0A0C10",
    "BG_STATUSBAR": "#0A0C10",
    "BG_TABLE_BASE": "#12141A",
    "BG_TABLE_ALT_ROW": "#1E293B",
    "BG_TABLE_HOVER": "rgba(239, 68, 68, 0.08)",
    "BG_BUTTON": "#1F2937",
    "BG_BUTTON_HOVER": "#374151",
    "BG_MENU": "#151820",
    "BG_GLASS": "rgba(18, 20, 26, 0.92)",
    "BG_MODULE_CARD": "#1E293B",

    # 文字色
    "TEXT_PRIMARY": "#E2E8F0",
    "TEXT_SECONDARY": "#A0AEC0",
    "TEXT_MUTED": "#718096",
    "TEXT_DISABLED": "#4B5563",
    "TEXT_HEADER": "#718096",
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
    "BORDER_DEFAULT": "rgba(255, 255, 255, 0.05)",
    "BORDER_SUBTLE": "rgba(255, 255, 255, 0.03)",
    "BORDER_STRONG": "#374151",
    "BORDER_BRAND": "rgba(239, 68, 68, 0.15)",
    "BORDER_MENU": "#252A36",

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
    "SELECTION_BG": "rgba(239, 68, 68, 0.18)",
    "SELECTION_HOVER_BG": "rgba(239, 68, 68, 0.08)",

    # 标题栏分隔线颜色
    "TITLEBAR_BORDER": "rgba(139, 92, 246, 0.12)",
    "STATUSBAR_BORDER": "rgba(59, 130, 246, 0.3)",

    # 齿轮菜单选中色
    "MENU_SELECTED_BG": "rgba(139, 92, 246, 0.5)",

    # Splitter
    "SPLITTER_BG": "rgba(239, 68, 68, 0.08)",
    "SPLITTER_HOVER": "rgba(239, 68, 68, 0.25)",

    # Tab
    "TAB_TEXT": "#6B7280",
    "TAB_TEXT_HOVER": "#D1D5DB",
    "TAB_HOVER_BG": "rgba(255,255,255,0.04)",

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
    "KLINE_VCP_LINE": "#FFD700",
    "KLINE_VCP_AREA": "rgba(51, 153, 255, 0.1)",
}

# ============================================================
# 月白主题（亮色，参照 Gemini 页面风格）
# ============================================================
THEME_YUEBAI = {
    "name": "月白",

    # 背景色层级体系（浅 → 白）
    "BG_CANVAS": "#F7F3EC",
    "BG_SIDEBAR": "#EEE6DA",
    "BG_TABLE_ALT": "#F3EEE6",
    "BG_CARD": "#FFFDF8",
    "BG_HOVER": "#E8DFD1",
    "BG_INPUT": "#FFFCF7",
    "BG_ELEVATED": "#FFFCF7",
    "BG_TITLEBAR": "#EEE7DB",
    "BG_STATUSBAR": "#EEE7DB",
    "BG_TABLE_BASE": "#FAF7F2",
    "BG_TABLE_ALT_ROW": "#F3EEE6",
    "BG_TABLE_HOVER": "#EEE7DB",
    "BG_BUTTON": "#F0E8DD",
    "BG_BUTTON_HOVER": "#E6DCCD",
    "BG_MENU": "#FFFCF7",
    "BG_GLASS": "rgba(247, 243, 236, 0.96)",
    "BG_MODULE_CARD": "#FFFCF7",

    # 文字色 — 白底黑字，严格遵循 WCAG 4.5:1
    "TEXT_PRIMARY": "#0F172A",
    "TEXT_SECONDARY": "#475569",
    "TEXT_MUTED": "#64748B",
    "TEXT_DISABLED": "#94A3B8",
    "TEXT_HEADER": "#475569",
    "TEXT_BRIGHT": "#0F172A",

    # 品牌色 — 不变
    "BRAND_PRIMARY": "#EF4444",
    "BRAND_HOVER": "#F87171",
    "BRAND_DEEP": "#B91C1C",
    "BRAND_SUBTLE": "rgba(239, 68, 68, 0.08)",

    # 功能色 — 涨跌（白底加深版，确保对比度）
    "COLOR_RISE": "#DC2626",
    "COLOR_RISE_STRONG": "#B91C1C",
    "COLOR_FALL": "#16A34A",
    "COLOR_FALL_STRONG": "#15803D",
    "COLOR_FLAT": "#6B7280",

    # 功能色 — 状态（不变）
    "COLOR_SUCCESS": "#10B981",
    "COLOR_WARNING": "#F59E0B",
    "COLOR_ERROR": "#EF4444",
    "COLOR_INFO": "#3B82F6",

    # 边框色 — 白底用深色半透明
    "BORDER_DEFAULT": "rgba(93, 78, 55, 0.10)",
    "BORDER_SUBTLE": "rgba(93, 78, 55, 0.05)",
    "BORDER_STRONG": "#D8CDBE",
    "BORDER_BRAND": "rgba(239, 68, 68, 0.12)",
    "BORDER_MENU": "#E4D8C8",

    # 评分着色梯度 — 不变
    "SCORE_EXCELLENT": "#FF4757",
    "SCORE_GOOD": "#F59E0B",
    "SCORE_NORMAL": "#3A82F6",
    "SCORE_LOW": "#8E8E93",

    # 突破状态着色 — 不变
    "STATUS_BREAKOUT": "#E85D5D",
    "STATUS_APPROACHING": "#FFD60A",
    "STATUS_VCP": "#3A82F6",
    "STATUS_INACTIVE": "#8E8E93",

    # 滚动条颜色 — 灰色系
    "SCROLLBAR_HANDLE": "rgba(0, 0, 0, 0.12)",
    "SCROLLBAR_HANDLE_HOVER": "rgba(0, 0, 0, 0.25)",

    # 选中态颜色 - 增强透明度或使用更深的底色使得在月白(白底)上移动时能明显看出悬停状态
    "SELECTION_BG": "rgba(239, 68, 68, 0.15)",     # 加深选中色
    "SELECTION_HOVER_BG": "#F4EDE4",               # 暖白悬停色，和米白表格及顶栏更统一


    # 标题栏分隔线
    "TITLEBAR_BORDER": "rgba(93, 78, 55, 0.10)",
    "STATUSBAR_BORDER": "rgba(93, 78, 55, 0.10)",

    # 齿轮菜单选中色
    "MENU_SELECTED_BG": "rgba(239, 68, 68, 0.12)",

    # Splitter
    "SPLITTER_BG": "rgba(0, 0, 0, 0.06)",
    "SPLITTER_HOVER": "rgba(0, 0, 0, 0.15)",

    # Tab
    "TAB_TEXT": "#6B7280",
    "TAB_TEXT_HOVER": "#0F172A",
    "TAB_HOVER_BG": "rgba(93, 78, 55, 0.06)",

    # 下拉箭头颜色
    "ARROW_COLOR": "#64748B",

    # K线图专用色 — 白底适配版，所有色值加深确保 WCAG 对比度
    "KLINE_UP_COLOR": "#DC2626",
    "KLINE_DOWN_COLOR": "#16A34A",
    "KLINE_MA10": "#1E293B",
    "KLINE_MA20": "#2563EB",
    "KLINE_MA50": "#EA580C",
    "KLINE_MA150": "#9333EA",
    "KLINE_MA200": "#DC2626",
    "KLINE_VOL_MA20": "#B45309",
    "KLINE_GRID_LINE": "rgba(93,78,55,0.06)",
    "KLINE_AXIS_LINE": "#D8CDBE",
    "KLINE_AXIS_LABEL": "#64748B",
    "KLINE_POINTER_BG": "#94A3B8",
    "KLINE_VCP_STAR": "#D97706",
    "KLINE_VCP_LINE": "#B45309",
    "KLINE_VCP_AREA": "rgba(37, 99, 235, 0.08)",
}


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
        self._settings = QSettings("VCPHunter", "ThemeManager")
        # 从持久化配置恢复上次选择的主题，默认"墨渊"
        saved = self._settings.value("current_theme", "墨渊")
        self._current_name = saved if saved in self.THEMES else "墨渊"

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
        self._current_name = name
        self._settings.setValue("current_theme", name)
        self._settings.sync()
        self.sig_theme_changed.emit(name)

    def is_dark(self) -> bool:
        """当前是否为暗色主题"""
        return self._current_name == "墨渊"

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
FONT_FAMILY = '"Microsoft YaHei UI", "Inter", "Segoe UI", sans-serif'
FONT_MONO = '"JetBrains Mono", "Cascadia Code", "Consolas", monospace'
FONT_SIZE_NORMAL = 13
FONT_SIZE_SMALL = 12
FONT_SIZE_HEADER = 11
FONT_SIZE_TITLE = 15


def apply_rise_fall_color(pct_val: float) -> str:
    """根据涨跌幅返回颜色字符串（动态取当前主题）"""
    t = theme_manager.current_theme
    if pct_val > 5:
        return t["COLOR_RISE_STRONG"]
    elif pct_val > 0:
        return t["COLOR_RISE"]
    elif pct_val < -5:
        return t["COLOR_FALL_STRONG"]
    elif pct_val < 0:
        return t["COLOR_FALL"]
    return t["COLOR_FLAT"]


def apply_score_color(score_val: float) -> str:
    """根据评分返回颜色字符串（动态取当前主题）"""
    t = theme_manager.current_theme
    if score_val >= 90:
        return t["SCORE_EXCELLENT"]
    elif score_val >= 80:
        return t["SCORE_GOOD"]
    elif score_val >= 60:
        return t["SCORE_NORMAL"]
    return t["SCORE_LOW"]
