# -*- coding: utf-8 -*-
"""
K 线图窗口 — ECharts 5.5.0 + QWebEngineView 高性能版
替代旧版 PyQtGraph，实现专业级金融图表体验。

核心特性：
- 三面板布局：K线主图 + 成交量 + MACD
- MA10/20/50/150/200 均线系统
- VCP 买点信号覆盖层（箱体 + 金星 + 高点连线）
- 盘中 60 秒增量热更新（无闪烁）
- 十字光标 + 顶部工具栏实时联动
"""

import os as _os
from contextlib import suppress

from PyQt6.QtCore import QEvent, Qt, QTimer
from PyQt6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QSizePolicy, QVBoxLayout, QWidget

from app.services.ui_event_service import domain_events as event_bus
from app.services.ui_market_calendar_service import MarketCalendar
from app.services.ui_task_lifecycle_service import (
    shutdown_task_lifecycle_for_owner,
)
from app.services.ui_watchlist_service import watchlist_vm
from core.logger import get_logger
from core.observability import emit_structured_log, record_metric
from ui.kline_chart_payload import (
    build_kline_market_state,
    build_kline_preheated_shell_html,
    build_kline_shell_html,
    build_kline_theme_colors,
    dumps_json_for_script,
)
from ui.kline_js_readiness import begin_js_readiness_probe, set_shell_ready
from ui.kline_render_bridge import build_reset_lease_script
from ui.kline_webengine_page import stop_webengine_page
from ui.kline_window_header import (
    apply_browser_surface_theme,
    apply_header_badges,
    apply_info_styles,
    apply_qt_theme,
    get_cn_target_trade_date,
    refresh_header_context,
    resolve_vcp_context,
)
from ui.kline_window_pool_lifecycle import KLineWindowPoolLifecycleMixin
from ui.kline_window_recovery import install_render_process_recovery, uninstall_render_process_recovery
from ui.kline_window_rendering import cancel_snapshot_render_confirmation, load_chart_shell
from ui.kline_window_stages import KLineOpenStageCoordinator, build_chart_host, can_begin_chart_load
from ui.kline_window_state import initialize_kline_window_state, reset_kline_window_lease_state
from ui.kline_window_visibility import sync_runtime_visibility
from ui.theme import theme_manager
from ui.window_flags import enable_windows_native_shadow, enable_windows_system_backdrop

__all__ = [
    "KLineChartWindow",
    "build_reset_lease_script",
    "cancel_snapshot_render_confirmation",
    "event_bus",
    "install_render_process_recovery",
    "reset_kline_window_lease_state",
]

log = get_logger(__name__)

# ECharts JS 本地路径（断网也能用）
_ECHARTS_JS_PATH = _os.path.join(
    _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))), "assets", "echarts.min.js"
)
KLINE_INITIAL_LOAD_DELAY_MS = 0
KLINE_BROWSER_ATTACH_DELAY_MS = 0
KLINE_HEADER_RESIZE_COALESCE_MS = 16

# WebEngine、pandas 和指标计算都只在图表阶段需要。保留模块级名称便于测试注入，
# 但不在窗口类首次导入时拉起这些重量级依赖。
QWebEngineView = None


def _create_webengine_view(parent=None):
    browser_class = QWebEngineView
    if browser_class is None:
        from PyQt6.QtWebEngineWidgets import QWebEngineView as browser_class

    try:
        return browser_class(parent)
    except TypeError:
        return browser_class()


def _start_open_stages(window, open_started_at, browser, browser_page, *, defer_initial_load=False):
    stages = KLineOpenStageCoordinator(
        window,
        open_started_at=open_started_at,
        browser_factory=_create_webengine_view,
        record_metric=record_metric,
        emit_structured_log=emit_structured_log,
        browser_delay_ms=KLINE_BROWSER_ATTACH_DELAY_MS,
        initial_load_delay_ms=KLINE_INITIAL_LOAD_DELAY_MS,
        defer_initial_load=defer_initial_load,
    )
    stages.start(browser, browser_page)
    return stages


def _install_kline_shortcuts(window) -> None:
    from PyQt6.QtGui import QKeySequence, QShortcut

    QShortcut(QKeySequence(Qt.Key.Key_Left), window, activated=lambda: window._nav_stock(-1))
    QShortcut(QKeySequence(Qt.Key.Key_Right), window, activated=lambda: window._nav_stock(1))
    QShortcut(QKeySequence(Qt.Key.Key_F11), window, activated=window._toggle_fullscreen)
    QShortcut(QKeySequence(Qt.Key.Key_Escape), window, activated=window._leave_fullscreen)


def _configure_kline_window_shell(window, *, name: str, code: str, pool_shell: bool) -> None:
    from PyQt6.QtGui import QIcon

    window._log = log
    window._pool_shell_mode = bool(pool_shell)
    window._lease_signals_connected = False
    window.setWindowTitle(f"{name} ({code}) - K线图")
    window.setWindowFlags(window.windowFlags() | Qt.WindowType.FramelessWindowHint)
    window.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
    window.resize(1100, 680)
    window.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, not window._pool_shell_mode)
    icon_path = _os.path.join(_os.path.dirname(_os.path.dirname(__file__)), "bull_icon.ico")
    if _os.path.exists(icon_path):
        window.setWindowIcon(QIcon(icon_path))


def build_asian_rt_quote(*args, **kwargs):
    from ui.kline_window_asian import build_asian_rt_quote as _build_asian_rt_quote

    return _build_asian_rt_quote(*args, **kwargs)


def _runtime_helper(name: str):
    from ui import kline_window_runtime

    return getattr(kline_window_runtime, name)


def load_and_draw(*args, **kwargs):
    return _runtime_helper("load_and_draw")(*args, **kwargs)


def poll_rt_update(*args, **kwargs):
    return _runtime_helper("poll_rt_update")(*args, **kwargs)


def prepare_and_render_frame(*args, **kwargs):
    return _runtime_helper("prepare_and_render_frame")(*args, **kwargs)


def refresh_last_bar(*args, **kwargs):
    return _runtime_helper("refresh_last_bar")(*args, **kwargs)


def _abandon_owned_kline_tasks(window, code: str, generation: int) -> None:
    del code, generation
    lifecycle = getattr(window, "_task_lifecycle", None)
    if lifecycle is not None:
        for name in (
            "history_load",
            "render_prepare",
            "realtime_quote",
            "realtime_prepare",
            "asian_history_backfill",
        ):
            lifecycle.cancel(name, reason="symbol_switched")
    _runtime_helper("_discard_pending_owned_window_task")(window)
    _runtime_helper("_clear_realtime_generation_state")(window)


def _shutdown_kline_window_tasks(window) -> bool:
    """Cancel without blocking the GUI; only task-free windows may return to the pool."""
    controller = window._load_controller
    controller.close()
    _runtime_helper("_discard_pending_owned_window_task")(window)
    _runtime_helper("_clear_realtime_generation_state")(window)
    window._runtime_lifecycle.begin_close()
    lifecycle_clean = bool(shutdown_task_lifecycle_for_owner(window, timeout_ms=0))
    active_tickets = getattr(window, "_active_kline_task_tickets", ())
    task_clean = getattr(controller, "running_task", None) is None
    return bool(lifecycle_clean and not active_tickets and task_clean)


def _dispose_unowned_page(page) -> bool:
    if page is None:
        return True
    clean = stop_webengine_page(page)
    try:
        page.deleteLater()
    except (AttributeError, RuntimeError, TypeError):
        clean = False
    return clean


def _release_page_to_pool(page, *, shell_ready: bool, html_bytes: int) -> bool:
    if page is None or not shell_ready:
        return False
    try:
        from ui.components.kline_window_manager import kline_manager

        return bool(
            kline_manager.release_page(
                page,
                shell_ready=True,
                html_bytes=int(html_bytes or 0),
            )
        )
    except (AttributeError, ImportError, RuntimeError, TypeError, ValueError):
        return False


def _release_or_dispose_pending_page(pending_page, *, allow_page_reuse: bool) -> bool:
    if pending_page is None:
        return True
    released = False
    if allow_page_reuse:
        try:
            released = _release_page_to_pool(
                pending_page,
                shell_ready=bool(pending_page.property("klineShellReady")),
                html_bytes=int(pending_page.property("klineShellHtmlBytes") or 0),
            )
        except (AttributeError, RuntimeError, TypeError, ValueError):
            released = False
    return released or _dispose_unowned_page(pending_page)


def _detach_kline_browser(window, browser):
    uninstall_render_process_recovery(browser)
    with suppress(AttributeError, RuntimeError, TypeError):
        browser.loadFinished.disconnect(window._on_chart_load_finished)
    with suppress(AttributeError, RuntimeError, TypeError):
        browser.removeEventFilter(window)
        focus_proxy = browser.focusProxy()
        if focus_proxy is not None:
            focus_proxy.removeEventFilter(window)
    with suppress(AttributeError, RuntimeError, TypeError):
        return browser.page()
    return None


def _browser_shell_is_reusable(window, browser) -> bool:
    try:
        return bool(
            getattr(window, "_shell_loaded", False)
            and getattr(window, "_last_shell_load_ok", None) is True
            and browser.property("klineShellReady")
        )
    except (AttributeError, RuntimeError, TypeError):
        return False


def _release_browser_page(window, browser, page, *, allow_page_reuse: bool) -> bool:
    if not allow_page_reuse or not _browser_shell_is_reusable(window, browser):
        return False
    with suppress(AttributeError, RuntimeError, TypeError):
        from ui.kline_render_bridge import build_runtime_active_script

        browser.page().runJavaScript(build_runtime_active_script(False), lambda _ack: None)
    return _release_page_to_pool(
        page,
        shell_ready=True,
        html_bytes=int(getattr(window, "_last_chart_html_bytes", 0) or 0),
    )


def _stop_browser_and_external_page(browser, page) -> bool:
    try:
        browser.stop()
        browser_stopped = True
    except (AttributeError, RuntimeError, TypeError):
        browser_stopped = False
    try:
        page_owned_by_browser = page is not None and page.parent() is browser
    except (AttributeError, RuntimeError, TypeError):
        page_owned_by_browser = False
    page_clean = True if page_owned_by_browser else _dispose_unowned_page(page)
    return bool(browser_stopped and page_clean)


def _delete_detached_browser(browser) -> bool:
    with suppress(AttributeError, RuntimeError, TypeError):
        browser.setUpdatesEnabled(False)
        browser.hide()
    try:
        browser.deleteLater()
    except (AttributeError, RuntimeError, TypeError):
        return False
    return True


def _dispose_kline_browser(
    window,
    pending_browser,
    pending_page,
    *,
    allow_page_reuse: bool = True,
) -> bool:
    browser = getattr(window, "browser", None) or pending_browser
    window.browser = None
    if browser is None:
        return _release_or_dispose_pending_page(pending_page, allow_page_reuse=allow_page_reuse)
    page = _detach_kline_browser(window, browser)
    extra_pending_page = pending_page if pending_page is not page else None
    page_released = _release_browser_page(
        window,
        browser,
        page,
        allow_page_reuse=allow_page_reuse,
    )
    with suppress(AttributeError, RuntimeError, TypeError):
        window.chart_host_layout.removeWidget(browser)
    resource_clean = page_released or _stop_browser_and_external_page(browser, page)
    pending_page_clean = _release_or_dispose_pending_page(
        extra_pending_page,
        allow_page_reuse=allow_page_reuse,
    )
    browser_deleted = _delete_detached_browser(browser)
    return bool(resource_clean and pending_page_clean and browser_deleted)


def _schedule_header_resize_refresh(window) -> None:
    window._header_resize_pending = True
    timer = getattr(window, "_header_resize_timer", None)
    if (
        getattr(window, "_closing", False)
        or not getattr(window, "_runtime_active", True)
        or (callable(getattr(window, "isHidden", None)) and window.isHidden())
    ):
        if timer is not None:
            timer.stop()
        return
    if timer is None:
        timer = QTimer(window)
        timer.setSingleShot(True)
        timer.timeout.connect(lambda: _flush_header_resize_refresh(window))
        window._header_resize_timer = timer
    if not timer.isActive():
        timer.start(KLINE_HEADER_RESIZE_COALESCE_MS)


def _flush_header_resize_refresh(window) -> None:
    if not getattr(window, "_header_resize_pending", False):
        return
    if (
        getattr(window, "_closing", False)
        or not getattr(window, "_runtime_active", True)
        or (callable(getattr(window, "isHidden", None)) and window.isHidden())
    ):
        return
    window._header_resize_pending = False
    if hasattr(window, "summary_cards"):
        window._refresh_header_context()


def _cancel_header_resize_refresh(window) -> None:
    timer = getattr(window, "_header_resize_timer", None)
    if timer is not None:
        timer.stop()
    window._header_resize_pending = False


class KLineChartWindow(KLineWindowPoolLifecycleMixin, QWidget):
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
        browser_page=None,
        pool_shell: bool = False,
        open_started_at: float | None = None,
        open_context=None,
    ):
        super().__init__()
        initialize_kline_window_state(
            self,
            main_window=main_window,
            code=code,
            name=name,
            data_provider=data_provider,
            vcp_data=vcp_data,
            code_list=code_list,
            current_idx=current_idx,
            open_context=open_context,
        )
        _configure_kline_window_shell(self, name=name, code=code, pool_shell=pool_shell)

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
        # 首帧只放轻量占位壳；WebEngine 的导入、创建和挂接在窗口显示后的阶段定时器中完成。
        build_chart_host(self, container_layout, browser)

        main_layout.addWidget(self.container)

        _install_kline_shortcuts(self)
        self._update_nav_buttons()

        # 初始化主题样式（必须在所有控件创建完成后调用）
        self._apply_qt_theme()

        self._check_fav_status()
        self._refresh_header_context()
        self._set_status_message("正在准备图表...", tone="loading")

        if not self._pool_shell_mode:
            self._connect_lease_signals()

        self._open_stages = _start_open_stages(
            self,
            open_started_at,
            browser,
            browser_page,
            defer_initial_load=self._pool_shell_mode,
        )

    # ======================== 关注池 ========================
    def _check_fav_status(self):
        try:
            self.is_fav = watchlist_vm.is_in_watchlist(self.code)
            self.btn_fav.setText("已关注" if self.is_fav else "加入关注")
            self.btn_fav.setProperty("watching", bool(self.is_fav))
            self.btn_fav.style().unpolish(self.btn_fav)
            self.btn_fav.style().polish(self.btn_fav)
            self.btn_fav.update()
        except (AttributeError, RuntimeError, TypeError, ValueError) as e:
            log.debug(f"[K线] 检查关注状态失败: {e}")
            self.is_fav = False
            self.btn_fav.setProperty("watching", False)
        if hasattr(self, "summary_cards"):
            self._refresh_header_context()

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
            apply_info_styles(self)
        if hasattr(self, "feed_badge_lbl"):
            apply_header_badges(self)

    def _refresh_header_context(self):
        refresh_header_context(self)

    def showEvent(self, event):
        super().showEvent(event)
        self._open_stages.record("shell_ready")
        sync_runtime_visibility(self, hidden=False, minimized=self.isMinimized())
        if getattr(self, "_header_resize_pending", False):
            _schedule_header_resize_refresh(self)
        if self._native_window_effects_applied:
            return
        self._native_window_effects_applied = True
        enable_windows_native_shadow(self)
        enable_windows_system_backdrop(self, backdrop="mica", dark=theme_manager.is_dark())

    def hideEvent(self, event):
        timer = getattr(self, "_header_resize_timer", None)
        if timer is not None:
            timer.stop()
        sync_runtime_visibility(self, hidden=True, minimized=self.isMinimized())
        super().hideEvent(event)

    def changeEvent(self, event):
        super().changeEvent(event)
        if event.type() == QEvent.Type.WindowStateChange:
            sync_runtime_visibility(self, hidden=self.isHidden(), minimized=self.isMinimized())

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if hasattr(self, "summary_cards"):
            _schedule_header_resize_refresh(self)

    def moveEvent(self, event):
        super().moveEvent(event)
        self._snap_to_main_window_edges()

    def eventFilter(self, obj, event):
        if obj is getattr(self, "title_bar", None) and event.type() == QEvent.Type.MouseButtonDblClick:
            if event.button() == Qt.MouseButton.LeftButton:
                self._toggle_fullscreen()
                event.accept()
                return True
        browser = getattr(self, "browser", None)
        focus_proxy = browser.focusProxy() if browser is not None else None
        if obj in (browser, focus_proxy) and event.type() in (
            QEvent.Type.MouseButtonPress,
            QEvent.Type.Wheel,
            QEvent.Type.KeyPress,
        ):
            stages = getattr(self, "_open_stages", None)
            if stages is not None:
                stages.record("first_interaction")
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

    def _apply_browser_surface_theme(self):
        apply_browser_surface_theme(self)

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

        with suppress(AttributeError, RuntimeError, TypeError):
            browser.page().runJavaScript(script)

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
        with suppress(AttributeError, RuntimeError, TypeError):
            browser.page().runJavaScript(script)

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
        with suppress(AttributeError, RuntimeError, TypeError):
            browser.page().runJavaScript(script)

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

    # ======================== 数据加载 ========================
    def _load_and_draw(self):
        if getattr(self, "_closing", False):
            return
        identity = self._load_controller.begin(self.code)
        self._active_load_identity = identity
        self._render_generation = identity.generation
        if can_begin_chart_load(self):
            load_and_draw(self, identity=identity)

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
        browser = getattr(self, "sender", lambda: None)() or getattr(self, "browser", None)
        if browser is not getattr(self, "browser", None):
            return
        self._last_shell_load_epoch = int(getattr(self, "_browser_epoch", 0) or 0)
        self._last_shell_load_ok = bool(ok)
        if ok:
            begin_js_readiness_probe(self, browser, self._browser_epoch)
        else:
            set_shell_ready(browser, False)
            self._shell_loaded = False
            if getattr(self, "_pending_chart_status", None):
                self._pending_chart_status = None
                self._set_status_message("图表渲染失败，请重试", tone="error")

    def _load_chart_shell(self) -> bool:
        shell_builder = (
            build_kline_preheated_shell_html
            if bool(getattr(self, "_pool_shell_mode", False))
            else build_kline_shell_html
        )
        return load_chart_shell(
            self,
            echarts_js_path=_ECHARTS_JS_PATH,
            shell_builder=shell_builder,
            theme_colors=build_kline_theme_colors(),
        )

    # ======================== 图表渲染 ========================
    def _render_chart(self, df, loading=False):
        """后台准备完整快照；GUI 线程只负责最终 applySnapshot。"""
        if df is None or len(df) < 5:
            if not loading:
                self._set_status_message("历史数据不足，暂无法绘图", tone="warning")
            return
        prepare_and_render_frame(self, df, loading=loading)

    # ======================== 盘中增量更新 ========================
    def _start_rt_timer(self):
        """启动盘中实时刷新定时器（60秒间隔），只在交易时段运行"""
        if getattr(self, "_closing", False) or not getattr(self, "_runtime_active", True):
            if self._rt_timer is not None:
                self._rt_timer.stop()
            return
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
            refresh_last_bar(self, quotes[self.code])
        except (AttributeError, RuntimeError, TypeError, ValueError) as e:
            log.debug(f"[K线] 全局实时行情刷新失败: {e}")

    def _on_rt_timer(self):
        poll_rt_update(self)

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

    def _abandon_render_tasks(self, code: str, generation: int):
        _abandon_owned_kline_tasks(self, code, generation)

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
                old_generation = int(getattr(self, "_render_generation", 0) or 0)
                self._abandon_render_tasks(old_code, old_generation)

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
