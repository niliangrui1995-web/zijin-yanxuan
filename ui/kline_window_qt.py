# -*- coding: utf-8 -*-
"""
K 线图窗口 — ECharts 5.5.0 + QWebEngineView 高性能版
替代旧版 PyQtGraph，实现专业级金融图表体验。

核心特性：
- 三面板布局：K线主图 + 成交量 + MACD
- MA5/10/20/50/150/200 均线系统
- VCP 买点信号覆盖层（箱体 + 金星 + 高点连线）
- 盘中 60 秒增量热更新（无闪烁）
- 十字光标 + 顶部工具栏实时联动
"""

import json
import os as _os

from app.services.asian_market_service import is_yf_rate_limit_error, mark_yf_rate_limited
from app.services.scan_runtime_service import calculate_scan_indicators
from app.services.ui_event_service import domain_events as event_bus
from app.services.ui_market_calendar_service import MarketCalendar
from app.services.ui_task_service import background_job_runner, task_registry
from app.services.ui_watchlist_service import watchlist_vm
from core.logger import get_logger

log = get_logger(__name__)
import pandas as pd
from PyQt6.QtCore import QEvent, Qt, QTimer, QUrl
from PyQt6.QtWebEngineWidgets import QWebEngineView
from PyQt6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QSizePolicy, QVBoxLayout, QWidget

from ui.kline_chart_payload import (
    build_kline_echarts_payload,
    build_kline_html,
    build_kline_market_state,
    build_kline_theme_colors,
    dumps_json_for_script,
)
from ui.kline_window_asian import (
    apply_asian_live_quote,
    build_asian_history_df,
    build_asian_rt_quote,
    load_cached_asian_stock,
    schedule_asian_history_backfill,
)
from ui.kline_window_header import (
    apply_header_badges,
    apply_info_styles,
    apply_qt_theme,
    get_cn_target_trade_date,
    refresh_header_context,
    resolve_vcp_context,
    set_header_badge,
)
from ui.kline_window_runtime import (
    load_and_draw,
    normalize_daily_df_index,
    poll_rt_update,
    refresh_last_bar,
)
from ui.theme import theme_manager
from ui.trade_record_store import load_trade_records_for_security
from ui.window_flags import enable_windows_native_shadow, enable_windows_system_backdrop

# ECharts JS 本地路径（断网也能用）
_ECHARTS_JS_PATH = _os.path.join(
    _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))), "assets", "echarts.min.js"
)


def fetch_single_kline(*args, **kwargs):
    # Avoid importing the full app.services facade during K-line window import.
    from app.services.asian_market_service import fetch_single_kline as _fetch_single_kline

    return _fetch_single_kline(*args, **kwargs)


class KLineChartWindow(QWidget):
    """ECharts 驱动的 K 线图窗口"""

    def __init__(
        self,
        main_window,
        code,
        name,
        data_provider,
        vcp_data=None,
        code_list=None,
        current_idx=0,
        *,
        browser=None,
    ):
        super().__init__()
        self.main_window = main_window
        self.code = code
        self.name = name
        self.data_provider = data_provider
        self._log = log
        self.vcp_data = self._resolve_vcp_context(code, name, vcp_data or {})
        self.code_list = code_list or []
        self.current_idx = current_idx
        self._closing = False
        self._render_generation = 0
        self._native_window_effects_applied = False
        self._snap_threshold = 15
        self._snapping_to_main_window = False
        self._magnetically_attached = False
        self._fullscreen_geometry = None

        # 盘中实时刷新定时器
        self._rt_timer = None
        # 缓存当前展示的 DataFrame（用于增量更新）
        self.df = None

        self.setWindowTitle(f"{name} ({code}) - K线图")
        self.setWindowFlags(self.windowFlags() | Qt.WindowType.FramelessWindowHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.resize(1100, 680)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        event_bus.sig_rt_quotes.connect(self._on_global_rt_quotes)

        # 窗口图标
        from PyQt6.QtGui import QIcon

        icon_path = _os.path.join(_os.path.dirname(_os.path.dirname(__file__)), "bull_icon.ico")
        if _os.path.exists(icon_path):
            self.setWindowIcon(QIcon(icon_path))

        # 外层圆角防锯齿容器
        from PyQt6.QtWidgets import QFrame, QToolButton

        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)

        self.container = QFrame()
        self.container.setObjectName("klineContainer")
        container_layout = QVBoxLayout(self.container)
        container_layout.setContentsMargins(1, 1, 1, 1)
        container_layout.setSpacing(0)

        # 自定义拖拽标题栏
        from ui.components.shared_title_bar import DraggableTitleBar

        self.title_bar = DraggableTitleBar(self)
        self.title_bar.setFixedHeight(34)
        self.title_bar.installEventFilter(self)
        tb_layout = QHBoxLayout(self.title_bar)
        tb_layout.setContentsMargins(12, 0, 8, 0)

        self.title_lbl = QLabel("K线图")
        tb_layout.addWidget(self.title_lbl)
        tb_layout.addStretch()

        self.btn_fullscreen = QToolButton()
        self.btn_fullscreen.setText("□")
        self.btn_fullscreen.setFixedSize(32, 28)
        self.btn_fullscreen.setToolTip("全屏 / 还原 K 线图 (F11)")
        self.btn_fullscreen.setAccessibleName("全屏 / 还原 K 线图")
        self.btn_fullscreen.clicked.connect(self._toggle_fullscreen)
        tb_layout.addWidget(self.btn_fullscreen)

        self.btn_close = QToolButton()
        self.btn_close.setText("✕")
        self.btn_close.setFixedSize(32, 28)
        self.btn_close.setToolTip("关闭 K 线窗口")
        self.btn_close.setAccessibleName("关闭 K 线窗口")
        self.btn_close.clicked.connect(self.close)
        tb_layout.addWidget(self.btn_close)

        container_layout.addWidget(self.title_bar)

        # === 顶部主信息区 ===
        self.header_widget = QWidget()
        self.header_widget.setFixedHeight(64)
        header_layout = QHBoxLayout(self.header_widget)
        header_layout.setContentsMargins(12, 6, 12, 6)
        header_layout.setSpacing(10)

        left_group = QVBoxLayout()
        left_group.setContentsMargins(0, 0, 0, 0)
        left_group.setSpacing(3)

        identity_layout = QHBoxLayout()
        identity_layout.setContentsMargins(0, 0, 0, 0)
        identity_layout.setSpacing(8)
        self.identity_lbl = QLabel()
        identity_layout.addWidget(self.identity_lbl)
        self.market_badge_lbl = QLabel()
        self.market_badge_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        identity_layout.addWidget(self.market_badge_lbl)
        self.session_badge_lbl = QLabel()
        self.session_badge_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        identity_layout.addWidget(self.session_badge_lbl)
        self.feed_badge_lbl = QLabel()
        self.feed_badge_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        identity_layout.addWidget(self.feed_badge_lbl)
        identity_layout.addStretch()
        left_group.addLayout(identity_layout)

        self.info_lbl = QLabel("正在准备图表...")
        self.info_lbl.setMinimumHeight(24)
        self.info_lbl.setAlignment(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft)
        left_group.addWidget(self.info_lbl)
        header_layout.addLayout(left_group, 1)

        right_group = QHBoxLayout()
        right_group.setContentsMargins(0, 0, 0, 0)
        right_group.setSpacing(6)

        self.btn_prev = QPushButton("上一只")
        self.btn_prev.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_prev.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.btn_prev.setToolTip("查看当前列表中的上一只股票")
        self.btn_prev.setAccessibleName("上一只股票")
        self.btn_prev.setAccessibleDescription("切换到当前列表中的上一只股票")
        self.btn_prev.clicked.connect(lambda: self._nav_stock(-1))
        right_group.addWidget(self.btn_prev)

        self.nav_index_lbl = QLabel("-- / --")
        self.nav_index_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        right_group.addWidget(self.nav_index_lbl)

        self.btn_next = QPushButton("下一只")
        self.btn_next.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_next.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.btn_next.setToolTip("查看当前列表中的下一只股票")
        self.btn_next.setAccessibleName("下一只股票")
        self.btn_next.setAccessibleDescription("切换到当前列表中的下一只股票")
        self.btn_next.clicked.connect(lambda: self._nav_stock(1))
        right_group.addWidget(self.btn_next)

        self.btn_fav = QPushButton("加入关注")
        self.btn_fav.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_fav.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.btn_fav.setToolTip("将当前股票加入或移出关注池")
        self.btn_fav.setAccessibleName("切换关注状态")
        self.btn_fav.setAccessibleDescription("将当前股票加入或移出关注池")
        self.btn_fav.clicked.connect(self._toggle_fav)
        right_group.addWidget(self.btn_fav)

        header_layout.addLayout(right_group)

        container_layout.addWidget(self.header_widget)

        # === VCP 摘要带 ===
        self.summary_widget = QWidget()
        self.summary_widget.setFixedHeight(78)
        summary_layout = QHBoxLayout(self.summary_widget)
        summary_layout.setContentsMargins(12, 5, 12, 7)
        summary_layout.setSpacing(7)

        self.summary_cards = []
        self._summary_key_color = ""
        self._summary_value_color = ""
        self._summary_highlight_color = ""
        summary_card_specs = ((160, 20), (260, 48), (180, 24))
        for card_idx in range(3):
            card = QFrame()
            card.setObjectName("klineSummaryCard")
            min_width, stretch = summary_card_specs[card_idx]
            card.setMinimumWidth(min_width)
            card.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            card_layout = QVBoxLayout(card)
            card_layout.setContentsMargins(10, 6, 10, 6)
            card_layout.setSpacing(3)

            title_lbl = QLabel("--")
            title_lbl.setAlignment(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft)
            title_lbl.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
            card_layout.addWidget(title_lbl)

            value_labels = []
            for _row_idx in range(2):
                label = QLabel("--")
                label.setMinimumWidth(0)
                label.setMinimumHeight(18)
                label.setAlignment(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft)
                label.setTextFormat(Qt.TextFormat.RichText)
                label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
                value_labels.append(label)
                card_layout.addWidget(label)

            summary_layout.addWidget(card, stretch)
            self.summary_cards.append(
                {
                    "frame": card,
                    "title": title_lbl,
                    "labels": value_labels,
                }
            )

        container_layout.addWidget(self.summary_widget)

        # === ECharts WebEngine 主图区域 ===
        self.browser = browser or QWebEngineView()
        self.browser.setParent(self.container)
        self._pending_chart_status = None
        try:
            self.browser.loadFinished.connect(self._on_chart_load_finished)
        except (AttributeError, RuntimeError, TypeError):
            pass
        container_layout.addWidget(self.browser)

        main_layout.addWidget(self.container)

        # 快捷键 ←/→ 切换上/下一只股票
        from PyQt6.QtGui import QKeySequence, QShortcut

        QShortcut(QKeySequence(Qt.Key.Key_Left), self, activated=lambda: self._nav_stock(-1))
        QShortcut(QKeySequence(Qt.Key.Key_Right), self, activated=lambda: self._nav_stock(1))
        QShortcut(QKeySequence(Qt.Key.Key_F11), self, activated=self._toggle_fullscreen)
        QShortcut(QKeySequence(Qt.Key.Key_Escape), self, activated=self._leave_fullscreen)
        self._update_nav_buttons()

        # 初始化主题样式（必须在所有控件创建完成后调用）
        self._apply_qt_theme()

        self._check_fav_status()
        self._refresh_header_context()
        QTimer.singleShot(0, self._refresh_header_context)
        self._show_chart_placeholder()
        QTimer.singleShot(0, self._load_and_draw)

        # 监听全局主题切换 → 重新渲染 K 线图
        theme_manager.sig_theme_changed.connect(self._on_theme_changed)

    # ======================== 关注池 ========================
    def _check_fav_status(self):
        try:
            self.is_fav = watchlist_vm.is_in_watchlist(self.code)
            self.btn_fav.setText("已关注" if self.is_fav else "加入关注")
            self.btn_fav.setProperty("watching", bool(self.is_fav))
            self.btn_fav.style().unpolish(self.btn_fav)
            self.btn_fav.style().polish(self.btn_fav)
            self.btn_fav.update()
            if hasattr(self, "summary_cards"):
                self._refresh_header_context()
        except (AttributeError, RuntimeError, TypeError, ValueError) as e:
            log.debug(f"[K线] 检查关注状态失败: {e}")
            self.is_fav = False
            self.btn_fav.setProperty("watching", False)

    def _toggle_fav(self):
        try:
            watchlist_vm.toggle_stock(self.code, self.name, self.vcp_data)
            self._check_fav_status()
        except (AttributeError, RuntimeError, TypeError, ValueError) as e:
            log.debug(f"[K线] 切换关注状态失败: {e}")

    def _set_status_message(self, text: str, tone: str = "info"):
        self._info_tone = tone
        if hasattr(self, "info_lbl"):
            self.info_lbl.setText(str(text or "").strip())
        if hasattr(self, "info_lbl"):
            self._apply_info_styles()
        if hasattr(self, "feed_badge_lbl"):
            self._apply_header_badges()

    def _set_header_badge(self, label: QLabel, text: str, tone_name: str):
        set_header_badge(self, label, text, tone_name)

    def _apply_header_badges(self):
        apply_header_badges(self)

    def _refresh_header_context(self):
        refresh_header_context(self)

    def showEvent(self, event):
        super().showEvent(event)
        if self._native_window_effects_applied:
            return
        self._native_window_effects_applied = True
        enable_windows_native_shadow(self)
        enable_windows_system_backdrop(self, backdrop="mica", dark=theme_manager.is_dark())

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if hasattr(self, "summary_cards"):
            self._refresh_header_context()

    def moveEvent(self, event):
        super().moveEvent(event)
        self._snap_to_main_window_edges()

    def eventFilter(self, obj, event):
        if obj is getattr(self, "title_bar", None) and event.type() == QEvent.Type.MouseButtonDblClick:
            if event.button() == Qt.MouseButton.LeftButton:
                self._toggle_fullscreen()
                event.accept()
                return True
        return super().eventFilter(obj, event)

    def _snap_to_main_window_edges(self) -> None:
        if self._snapping_to_main_window or getattr(self, "_closing", False) or self.isFullScreen():
            return

        main_window = getattr(self, "main_window", None)
        if main_window is None:
            self._set_magnetically_attached(False)
            return

        try:
            if main_window.isMinimized() or main_window.isMaximized():
                self._set_magnetically_attached(False)
                return
            main_geo = main_window.frameGeometry()
            own_geo = self.frameGeometry()
        except (RuntimeError, AttributeError, TypeError):
            self._set_magnetically_attached(False)
            return

        if main_geo.isNull() or own_geo.isNull():
            self._set_magnetically_attached(False)
            return

        threshold = int(getattr(self, "_snap_threshold", 15))
        new_x = own_geo.x()
        new_y = own_geo.y()
        attached = False

        if abs(own_geo.left() - main_geo.right()) <= threshold:
            new_x = main_geo.right() + 1
            attached = True
        elif abs(own_geo.right() - main_geo.left()) <= threshold:
            new_x = main_geo.left() - own_geo.width()
            attached = True
        elif abs(own_geo.left() - main_geo.left()) <= threshold:
            new_x = main_geo.left()
            attached = True
        elif abs(own_geo.right() - main_geo.right()) <= threshold:
            new_x = main_geo.right() - own_geo.width() + 1
            attached = True

        if abs(own_geo.top() - main_geo.bottom()) <= threshold:
            new_y = main_geo.bottom() + 1
            attached = True
        elif abs(own_geo.bottom() - main_geo.top()) <= threshold:
            new_y = main_geo.top() - own_geo.height()
            attached = True
        elif abs(own_geo.top() - main_geo.top()) <= threshold:
            new_y = main_geo.top()
            attached = True
        elif abs(own_geo.bottom() - main_geo.bottom()) <= threshold:
            new_y = main_geo.bottom() - own_geo.height() + 1
            attached = True

        if new_x == own_geo.x() and new_y == own_geo.y():
            self._set_magnetically_attached(attached)
            return

        self._snapping_to_main_window = True
        try:
            self.move(new_x, new_y)
        finally:
            self._snapping_to_main_window = False
        self._set_magnetically_attached(attached)

    def _set_magnetically_attached(self, attached: bool) -> None:
        attached = bool(attached)
        if getattr(self, "_magnetically_attached", False) == attached:
            return
        self._magnetically_attached = attached
        self._apply_qt_theme()
        self._apply_chart_glass_mode()

    def _resolve_vcp_context(self, code: str, name: str, item_data: dict = None) -> dict:
        return resolve_vcp_context(self, code, name, item_data)

    # ======================== 主题切换 ========================
    def _on_theme_changed(self, _theme_name: str):
        """主题切换时同步刷新 Qt 外壳 + WebEngine 图表配色。"""
        self._apply_qt_theme()
        self._refresh_header_context()
        self._apply_chart_theme()

    def _apply_qt_theme(self):
        apply_qt_theme(self)

    def _apply_chart_theme(self, *, animate: bool = True) -> None:
        browser = getattr(self, "browser", None)
        if getattr(self, "_closing", False) or browser is None:
            return

        payload_json = dumps_json_for_script(
            {"theme": build_kline_theme_colors(), "animate": bool(animate)},
        )
        script = (
            "(function(payload) {"
            " if (typeof window.applyTheme !== 'function') return false;"
            " return window.applyTheme(payload);"
            " })(" + payload_json + ");"
        )

        try:
            browser.page().runJavaScript(script)
        except (AttributeError, RuntimeError, TypeError):
            pass

    def _apply_chart_market_state(self) -> None:
        browser = getattr(self, "browser", None)
        if getattr(self, "_closing", False) or browser is None:
            return
        payload_json = dumps_json_for_script(
            {"marketState": build_kline_market_state(self.code)},
        )
        script = (
            "(function(payload) {"
            " if (typeof window.applyMarketState !== 'function') return false;"
            " return window.applyMarketState(payload);"
            " })(" + payload_json + ");"
        )
        try:
            browser.page().runJavaScript(script)
        except (AttributeError, RuntimeError, TypeError):
            pass

    def _apply_chart_glass_mode(self) -> None:
        browser = getattr(self, "browser", None)
        if getattr(self, "_closing", False) or browser is None:
            return
        payload_json = dumps_json_for_script({"enabled": bool(getattr(self, "_magnetically_attached", False))})
        script = (
            "(function(payload) {"
            " if (typeof window.setGlassMode !== 'function') return false;"
            " return window.setGlassMode(payload);"
            " })(" + payload_json + ");"
        )
        try:
            browser.page().runJavaScript(script)
        except (AttributeError, RuntimeError, TypeError):
            pass

    def _toggle_fullscreen(self) -> None:
        if self.isFullScreen():
            self._leave_fullscreen()
        else:
            self._enter_fullscreen()

    def _enter_fullscreen(self) -> None:
        if self.isFullScreen() or getattr(self, "_closing", False):
            return
        self._fullscreen_geometry = self.geometry()
        self._set_magnetically_attached(False)
        self.setMinimumSize(0, 0)
        self.setMaximumSize(16777215, 16777215)
        self.showFullScreen()
        self.btn_fullscreen.setText("▣")
        self.btn_fullscreen.setToolTip("退出全屏 (Esc / F11)")
        self._apply_qt_theme()

    def _leave_fullscreen(self) -> None:
        if not self.isFullScreen():
            return
        self.showNormal()
        geometry = self._fullscreen_geometry
        self._fullscreen_geometry = None
        if geometry is not None and not geometry.isNull():
            self.setGeometry(geometry)
        self.btn_fullscreen.setText("□")
        self.btn_fullscreen.setToolTip("全屏 / 还原 K 线图 (F11)")
        self._apply_qt_theme()

    def _apply_info_styles(
        self, widget_text: str | None = None, info_color: str | None = None, is_dark: bool | None = None
    ):
        apply_info_styles(
            self,
            widget_text=widget_text,
            info_color=info_color,
            is_dark=is_dark,
        )

    def _get_market(self) -> str:
        return MarketCalendar.infer_market(self.code)

    def _get_cn_target_trade_date(self):
        return get_cn_target_trade_date()

    def _build_asian_rt_quote(self):
        from ui.tabs.asian_market_tab import GLOBAL_ASIAN_RT_CACHE

        market = self._get_market()
        latest_trade_date = MarketCalendar.get_latest_trade_date(market)
        quote = GLOBAL_ASIAN_RT_CACHE.get(self.code) or {}
        return build_asian_rt_quote(
            self.code,
            quote,
            market=market,
            latest_trade_date=latest_trade_date,
        )

    def _normalize_daily_df_index(self, df):
        return normalize_daily_df_index(df, logger=self._log)

    # ======================== 数据加载 ========================
    def _load_and_draw(self):
        self._render_generation = int(getattr(self, "_render_generation", 0) or 0) + 1
        load_and_draw(self)

    def _show_chart_placeholder(self):
        if getattr(self, "_closing", False):
            return
        self._set_status_message("正在准备图表...", tone="loading")
        browser = getattr(self, "browser", None)
        if not hasattr(browser, "setHtml"):
            return
        colors = build_kline_theme_colors()
        placeholder = f"""<!doctype html>
<html>
<head>
    <meta charset=\"utf-8\">
    <style>
        html, body {{
            margin: 0;
            width: 100%;
            height: 100%;
            background: {colors["bg_canvas"]};
            color: {colors["text_secondary"]};
            font-family: \"Microsoft YaHei UI\", sans-serif;
        }}
        .stage {{
            height: 100%;
            display: flex;
            align-items: center;
            justify-content: center;
            overflow: hidden;
        }}
        .skeleton {{
            width: min(860px, 82vw);
            height: min(420px, 64vh);
            border: 1px solid {colors["depth_line"]};
            border-radius: 12px;
            background:
                linear-gradient(90deg, transparent 0%, rgba(255,255,255,0.08) 48%, transparent 100%),
                repeating-linear-gradient(0deg, transparent 0 47px, {colors["grid_line"]} 48px),
                repeating-linear-gradient(90deg, transparent 0 63px, {colors["grid_line"]} 64px),
                linear-gradient(180deg, {colors["vcp_area_top"]}, {colors["vcp_area_bottom"]});
            background-size: 220px 100%, auto, auto, auto;
            animation: sweep 1.25s cubic-bezier(0.22, 1, 0.36, 1) infinite;
            box-shadow: 0 16px 36px rgba(0, 0, 0, 0.14);
            position: relative;
        }}
        .skeleton::before {{
            content: "";
            position: absolute;
            left: 6%;
            right: 6%;
            top: 18%;
            bottom: 20%;
            background:
                linear-gradient(135deg, transparent 0 12%, {colors["up_gradient_top"]} 13% 14%, transparent 15% 32%, {colors["down_gradient_top"]} 33% 34%, transparent 35% 54%, {colors["volume_spike"]} 55% 56%, transparent 57%);
            opacity: 0.40;
        }}
        .skeleton::after {{
            content: "";
            position: absolute;
            left: 7%;
            right: 7%;
            bottom: 8%;
            height: 14%;
            background: repeating-linear-gradient(90deg, {colors["volume_dry"]} 0 8px, transparent 8px 18px);
            opacity: 0.58;
        }}
        @keyframes sweep {{
            from {{ background-position: -240px 0, 0 0, 0 0, 0 0; }}
            to {{ background-position: 1000px 0, 0 0, 0 0, 0 0; }}
        }}
    </style>
</head>
<body><div class=\"stage\"><div class=\"skeleton\" aria-label=\"K line loading\"></div></div></body>
</html>"""
        try:
            browser.setHtml(placeholder, QUrl("about:blank"))
        except (AttributeError, RuntimeError, TypeError):
            pass

    def _set_pending_chart_status(self, text: str, tone: str) -> None:
        self._pending_chart_status = (str(text or "").strip(), str(tone or "info").strip() or "info")

    def _finish_pending_chart_status(self) -> None:
        pending = getattr(self, "_pending_chart_status", None)
        if not pending:
            return
        self._pending_chart_status = None
        text, tone = pending
        self._set_status_message(text, tone=tone)

    def _on_chart_load_finished(self, ok: bool) -> None:
        if getattr(self, "_closing", False):
            return
        if ok:
            self._apply_chart_market_state()
            self._apply_chart_glass_mode()
            self._finish_pending_chart_status()
        elif getattr(self, "_pending_chart_status", None):
            self._pending_chart_status = None
            self._set_status_message("图表渲染失败，请重试", tone="error")

    def _load_asian_chart(self):
        """加载亚洲市场（yfinance 缓存）的 K 线数据"""
        from ui.tabs.asian_market_tab import GLOBAL_ASIAN_RT_CACHE, JSON_CACHE
        from ui.tabs.asian_market_workers import fetch_asian_realtime_quote

        df = None
        target_stock = load_cached_asian_stock(JSON_CACHE, self.code)
        if target_stock:
            df = build_asian_history_df(
                target_stock,
                vcp_data=self.vcp_data,
                refresh_header_context=self._refresh_header_context,
                normalize_daily_df_index=self._normalize_daily_df_index,
            )

        if df is None:
            from app.services.ui_task_service import background_job_runner as task_manager

            schedule_asian_history_backfill(
                self,
                task_manager=task_manager,
                fetch_single_kline=fetch_single_kline,
            )
            return

        if df is not None:
            market = self._get_market()
            quote = GLOBAL_ASIAN_RT_CACHE.get(self.code)
            latest_trade_date = MarketCalendar.get_latest_trade_date(market)
            if quote is None and latest_trade_date is not None and not df.empty:
                try:
                    last_date = pd.Timestamp(df.index[-1]).date()
                except (IndexError, TypeError, ValueError):
                    last_date = None
                if last_date is None or last_date < latest_trade_date:
                    try:
                        quote = fetch_asian_realtime_quote(self.code)
                    except Exception as exc:
                        if is_yf_rate_limit_error(exc):
                            remaining_sec = mark_yf_rate_limited(exc)
                            self._log.warning(
                                f"[K线] {self.code} 盘后补足亚洲报价触发 Yahoo Finance 限流，冷却 {remaining_sec:.0f}s: {exc}"
                            )
                        elif isinstance(
                            exc,
                            (AttributeError, ImportError, KeyError, OSError, RuntimeError, TypeError, ValueError),
                        ):
                            self._log.warning(f"[K线] {self.code} 盘后补足亚洲报价失败: {exc}")
                        else:
                            raise
                    if quote:
                        GLOBAL_ASIAN_RT_CACHE[self.code] = quote

            if quote is not None:
                df = apply_asian_live_quote(
                    df,
                    quote,
                    market=market,
                )

            # 统一交由 _render_chart() 去计算指标并生成完整 ECharts，此时必然包含最新日期
            self._render_chart(df, loading=False)
        else:
            self._set_status_message("当前标的暂无历史日线数据", tone="warning")

    # ======================== 图表渲染 ========================
    def _render_chart(self, df, loading=False):
        """将 DataFrame 转换成 ECharts 数据格式并渲染到 WebEngine"""
        if getattr(self, "_closing", False) or getattr(self, "browser", None) is None:
            return
        # 兼容 Polars DataFrame
        if not hasattr(df, "iloc"):
            df = df.to_pandas()

        # 确保有 DatetimeIndex
        if "date" in df.columns and not isinstance(df.index, pd.DatetimeIndex):
            df["date"] = pd.to_datetime(df["date"].astype(str))
            df.set_index("date", inplace=True)
        df = self._normalize_daily_df_index(df)

        if df is None or len(df) < 5:
            if not loading:
                self._set_status_message("历史数据不足，暂无法绘图", tone="warning")
            return

        # 始终要求重算完整指标，避免合并了今天盘中的新K线后由于缺少最新日期的MACD导致JS渲染因含有NaN而雪崩不画K线
        df = calculate_scan_indicators(df)

        if loading:
            self._set_status_message(f"正在绘制本地缓存 · {len(df)} 条日线", tone="loading")
            self._set_pending_chart_status(f"已载入本地缓存 · {len(df)} 条日线", "info")
        else:
            self._set_status_message(f"正在绘制图表 · {len(df)} 条日线", tone="loading")
            self._set_pending_chart_status(f"图表已更新 · {len(df)} 条日线", "success")

        # 截取最后 250 根 K 线
        self.df = df.iloc[-250:].copy()
        for col in ["open", "high", "low", "close", "volume"]:
            if col in self.df.columns:
                self.df[col] = self.df[col].ffill().bfill()

        # 构建 ECharts 数据
        try:
            trade_records = load_trade_records_for_security(self.code, self.name)
        except (OSError, RuntimeError, TypeError, ValueError) as exc:
            self._log.debug(f"[K绾縘 {self.code} 浜ゆ槗璁板綍璇诲彇澶辫触: {exc}")
            trade_records = []

        echarts_data = build_kline_echarts_payload(
            self.df,
            code=self.code,
            name=self.name,
            vcp_data=self.vcp_data,
            trade_records=trade_records,
        )
        self._last_chart_payload_bytes = len(
            json.dumps(echarts_data, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        )
        self._last_chart_points = len(echarts_data.get("dates") or [])

        # 判断是首次加载还是切换股票
        if not loading and not hasattr(self, "_first_render_done"):
            # 首次完整渲染（替换 loading 占位）
            pass

        # 渲染 HTML 到 WebEngine
        html_content = build_kline_html(
            title=f"{self.name} ({self.code}) 日线",
            echarts_data=echarts_data,
            echarts_js_path=_ECHARTS_JS_PATH,
            theme_colors=build_kline_theme_colors(),
        )
        self._last_chart_html_bytes = len(html_content.encode("utf-8"))

        # 用 baseUrl 确保本地 file:// 引用正常
        base_url = QUrl.fromLocalFile(_os.path.dirname(_os.path.abspath(_ECHARTS_JS_PATH)) + "/")
        if getattr(self, "_first_render_done", False):
            self._replace_chart_data_or_reload(
                html_content,
                base_url,
                title=f"{self.name} ({self.code}) 日线",
                echarts_data=echarts_data,
            )
            if not loading:
                self._start_rt_timer()
            return

        self.browser.setHtml(html_content, base_url)
        self._first_render_done = True

        # 启动盘中定时器
        if not loading:
            self._start_rt_timer()

    # ======================== 盘中增量更新 ========================
    def _replace_chart_data_or_reload(self, html_content: str, base_url: QUrl, *, title: str, echarts_data: dict):
        browser = getattr(self, "browser", None)
        if getattr(self, "_closing", False) or browser is None:
            return
        payload_json = dumps_json_for_script(
            {"title": title, "data": echarts_data},
        )
        script = (
            "(function(payload) {"
            " if (typeof window.replaceKlineData !== 'function') return false;"
            " return window.replaceKlineData(payload);"
            " })(" + payload_json + ");"
        )

        def _fallback_if_needed(applied):
            if getattr(self, "_closing", False):
                return
            if applied:
                self._finish_pending_chart_status()
                return
            callback_browser = getattr(self, "browser", None)
            if callback_browser is None:
                return
            try:
                callback_browser.setHtml(html_content, base_url)
            except (AttributeError, RuntimeError, TypeError) as exc:
                self._log.debug(f"[K线] JS增量渲染回退失败: {exc}")

        try:
            browser.page().runJavaScript(script, _fallback_if_needed)
        except (AttributeError, RuntimeError, TypeError) as exc:
            self._log.debug(f"[K线] JS增量渲染不可用: {exc}")
            if getattr(self, "_closing", False):
                return
            fallback_browser = getattr(self, "browser", None)
            if fallback_browser is None:
                return
            try:
                fallback_browser.setHtml(html_content, base_url)
            except (AttributeError, RuntimeError, TypeError) as fallback_exc:
                self._log.debug(f"[K线] HTML回退渲染失败: {fallback_exc}")

    def _start_rt_timer(self):
        """启动盘中实时刷新定时器（60秒间隔），只在交易时段运行"""
        market = self._get_market()
        self._apply_chart_market_state()
        if not MarketCalendar.is_quote_refresh_time(market):
            if self._rt_timer is not None:
                self._rt_timer.stop()
            return
        if market == "CN":
            if self._rt_timer is not None:
                self._rt_timer.stop()
            return

        if self._rt_timer is None:
            self._rt_timer = QTimer(self)
            self._rt_timer.timeout.connect(self._on_rt_timer)
        self._rt_timer.start(60 * 1000)
        log.debug(f"[K线] {self.code} 实时刷新已启动 (60s)")

    def _on_global_rt_quotes(self, quotes: dict):
        if self._get_market() != "CN":
            return
        if not quotes or self.code not in quotes:
            return
        try:
            self._refresh_last_bar(quotes[self.code])
        except (AttributeError, RuntimeError, TypeError, ValueError) as e:
            log.debug(f"[K线] 全局实时行情刷新失败: {e}")

    def _on_rt_timer(self):
        poll_rt_update(self)

    def _refresh_last_bar(self, quote):
        refresh_last_bar(self, quote)

    # ======================== 导航 ========================
    def _nav_stock(self, delta):
        """切换股票：delta=-1 上一只, +1 下一只"""
        if not self.code_list:
            return
        if getattr(self, "_switching", False):
            return
        new_idx = self.current_idx + delta
        if 0 <= new_idx < len(self.code_list):
            self._switch_to_stock(new_idx)

    def _update_nav_buttons(self):
        if not self.code_list:
            self.btn_prev.setEnabled(False)
            self.btn_next.setEnabled(False)
            return
        self.btn_prev.setEnabled(self.current_idx > 0)
        self.btn_next.setEnabled(self.current_idx < len(self.code_list) - 1)

    def _switch_to_stock(self, new_idx):
        """切换到指定索引的股票"""
        self._switching = True
        try:
            # 停止旧定时器
            if self._rt_timer is not None:
                self._rt_timer.stop()

            # 重置状态
            old_code = str(getattr(self, "code", "") or "").strip()
            if old_code:
                for task_key in (
                    task_registry.window(f"kline_{old_code}"),
                    task_registry.window(f"kline_asian_{old_code}"),
                ):
                    try:
                        background_job_runner.abandon(task_key)
                    except (AttributeError, RuntimeError, TypeError, ValueError):
                        pass

            item_data = self.code_list[new_idx]
            self.current_idx = new_idx
            self.code = item_data.get("代码", "")
            self.name = item_data.get("名称", "")
            self.vcp_data = self._resolve_vcp_context(self.code, self.name, item_data)

            title = f"{self.name} ({self.code}) - K线图"
            self.setWindowTitle(title)
            self._refresh_header_context()

            # 同步选中主窗口表格行
            workspace = getattr(self.main_window, "_workspace", None) if self.main_window else None
            if workspace is not None:
                preferred_tab_index = item_data.get("__source_tab_index")
                if not isinstance(preferred_tab_index, int):
                    preferred_tab_index = None
                workspace.select_code_row(self.code, preferred_tab_index=preferred_tab_index)

            self._check_fav_status()
            self._load_and_draw()
        finally:
            self._switching = False
            self._update_nav_buttons()

    # ======================== 资源释放 ========================
    def closeEvent(self, event):
        """窗口关闭时彻底释放 WebEngine 资源，防止内存泄漏"""
        self._closing = True
        # 断开主题切换信号，防止信号调用已销毁的窗口
        try:
            theme_manager.sig_theme_changed.disconnect(self._on_theme_changed)
        except TypeError:
            pass
        try:
            event_bus.sig_rt_quotes.disconnect(self._on_global_rt_quotes)
        except TypeError:
            pass

        # 停止定时器
        if self._rt_timer is not None:
            self._rt_timer.stop()
            self._rt_timer = None

        for task_key in (
            task_registry.window(f"kline_{self.code}"),
            task_registry.window(f"kline_asian_{self.code}"),
        ):
            try:
                background_job_runner.abandon(task_key)
            except (AttributeError, RuntimeError, TypeError, ValueError):
                pass

        self.df = None

        # 释放 WebEngine：关闭阶段不再发起新的页面导航，避免 Qt/WebEngine teardown 竞态。
        browser = getattr(self, "browser", None)
        self.browser = None
        try:
            if browser is not None:
                try:
                    browser.loadFinished.disconnect(self._on_chart_load_finished)
                except (AttributeError, RuntimeError, TypeError):
                    pass
                try:
                    browser.stop()
                except (AttributeError, RuntimeError, TypeError):
                    pass
                try:
                    browser.setUpdatesEnabled(False)
                except (AttributeError, RuntimeError, TypeError):
                    pass
        except (AttributeError, RuntimeError, TypeError) as _e:
            log.debug(f"[K线] WebEngine 释放异常: {_e}")

        log.debug(f"[K线] {self.code} 窗口关闭")
        super().closeEvent(event)
