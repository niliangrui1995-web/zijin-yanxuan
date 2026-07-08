# -*- coding: utf-8 -*-
from __future__ import annotations

import time
from importlib import import_module

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtWidgets import QLabel, QPushButton, QSizePolicy, QVBoxLayout, QWidget

from app.services.ui_diagnostics_service import ui_stall_span
from core.logger import get_logger
from ui.components.smooth_tab_widget import SmoothTabWidget
from ui.components.vector_icons import tab_svg_icon
from ui.theme_tokens import build_ui_tokens
from ui.workspaces.stock_signal import StockSignal
from ui.workspaces.workspace_facade import WorkspaceFacade

log = get_logger(__name__)


WatchlistTab = None
AsianMarketTab = None
NADailyTab = None
StockCandidateTab = None
AIIndustryChainTab = None
LhbTab = None
RtMonitorTab = None
ScanTab = None
ForeignBlockTradeTab = None
EarningsTab = None
FundHoldingsTab = None
LogTab = None


def _resolve_tab_class(class_name: str, module_name: str):
    tab_class = globals().get(class_name)
    if tab_class is None:
        module = import_module(module_name)
        tab_class = getattr(module, class_name)
        globals()[class_name] = tab_class
    return tab_class


class LazyTabPlaceholder(QWidget):
    """Lightweight first-entry shell for tabs whose real widget is mounted on demand."""

    def __init__(self, title: str, load_callback, parent=None):
        super().__init__(parent)
        self._title_text = str(title or "").strip()
        self._load_callback = load_callback
        self.setObjectName("lazyWorkspaceTabPlaceholder")
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(10)
        layout.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self.lbl_title = QLabel(self._title_text or "页面待加载", self)
        self.lbl_title.setObjectName("lazyWorkspaceTabTitle")
        self.lbl_title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.lbl_title, 0, Qt.AlignmentFlag.AlignCenter)

        self.lbl_detail = QLabel("首次进入时加载，主工作台已先响应。", self)
        self.lbl_detail.setObjectName("lazyWorkspaceTabDetail")
        self.lbl_detail.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.lbl_detail.setWordWrap(True)
        layout.addWidget(self.lbl_detail, 0, Qt.AlignmentFlag.AlignCenter)

        self.btn_load = QPushButton("立即加载", self)
        self.btn_load.setObjectName("lazyWorkspaceTabLoad")
        self.btn_load.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_load.clicked.connect(self._handle_load)
        layout.addWidget(self.btn_load, 0, Qt.AlignmentFlag.AlignCenter)

        self.apply_theme()

    def set_loading(self) -> None:
        self.lbl_detail.setText("正在挂载页面，请稍候...")
        self.btn_load.setEnabled(False)
        self.btn_load.setText("加载中")

    def set_error(self, message: str) -> None:
        self.lbl_detail.setText(str(message or "").strip() or "页面加载失败，请重试。")
        self.btn_load.setEnabled(True)
        self.btn_load.setText("重试")

    def _handle_load(self) -> None:
        if callable(self._load_callback):
            self._load_callback()

    def apply_theme(self) -> None:
        tokens = build_ui_tokens()
        theme = tokens["theme"]
        primary_gradient_start = theme.get("PRIMARY_GRADIENT_START", theme["BRAND_PRIMARY"])
        primary_gradient_end = theme.get("PRIMARY_GRADIENT_END", theme.get("BRAND_PRESSED", theme["BRAND_DEEP"]))
        primary_hover_start = theme.get("PRIMARY_HOVER_GRADIENT_START", theme["BRAND_HOVER"])
        primary_hover_end = theme.get("PRIMARY_HOVER_GRADIENT_END", theme["BRAND_PRIMARY"])
        primary_button_text = theme.get("PRIMARY_BUTTON_TEXT", theme["TEXT_ON_ACCENT"])
        primary_button_border = theme.get("PRIMARY_BUTTON_BORDER", theme["BRAND_DEEP"])
        self.setStyleSheet(
            f"""
            QWidget#lazyWorkspaceTabPlaceholder {{
                background: {theme["BG_GLASS"]};
            }}
            QLabel#lazyWorkspaceTabTitle {{
                color: {theme["TEXT_PRIMARY"]};
                font-size: {tokens["font"]["size_xl"]}px;
                font-weight: {tokens["font"]["weight_bold"]};
            }}
            QLabel#lazyWorkspaceTabDetail {{
                color: {theme["TEXT_SECONDARY"]};
                font-size: {tokens["font"]["size_sm"]}px;
            }}
            QPushButton#lazyWorkspaceTabLoad {{
                background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                    stop:0 {primary_gradient_start}, stop:1 {primary_gradient_end});
                color: {primary_button_text};
                border: 1px solid {primary_button_border};
                border-radius: {tokens["radius"]["pill"]}px;
                padding: 0 {tokens["space"]["xl"]}px;
                min-height: {tokens["control"]["toolbar_button_height"]}px;
                font-size: {tokens["font"]["size_sm"]}px;
                font-weight: {tokens["font"]["weight_bold"]};
            }}
            QPushButton#lazyWorkspaceTabLoad:disabled {{
                background: {theme["BG_BUTTON"]};
                color: {theme["TEXT_MUTED"]};
                border-color: {theme["BORDER_DEFAULT"]};
            }}
            QPushButton#lazyWorkspaceTabLoad:hover:!disabled {{
                background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                    stop:0 {primary_hover_start}, stop:1 {primary_hover_end});
            }}
            """
        )


def _resolve_workspace_facade(workspace) -> WorkspaceFacade:
    facade = getattr(workspace, "_workspace_facade", None)
    if facade is None:
        facade = WorkspaceFacade(workspace)
        setattr(workspace, "_workspace_facade", facade)
    return facade


class ClassicWorkspace(QWidget):
    mode = "classic"
    BACKGROUND_PREWARM_DELAY_MS = 350
    BACKGROUND_PREWARM_INTERVAL_MS = 260
    CONTEXT_PREWARM_PRIORITY = ("ai_industry_chain", "na_daily")
    BACKGROUND_PREWARM_KEYS = frozenset()
    RESTORE_LAST_TAB_DELAY_MS = 2500
    COPY_HOOK_REFRESH_DELAY_MS = 240
    STARTUP_TRANSITION_SUSPEND_MS = 60_000
    STARTUP_RAW_TAB_SWITCH_GUARD_MS = 60_000
    FIRST_VISIBLE_TAB_WORK_DELAY_MS = 1800
    LHB_FIRST_VISIBLE_POOL_DELAY_MS = 5000
    SHELL_GROUP_REBUILD_LOAD_DELAY_MS = 120
    SHELL_GROUP_REBUILD_ACTIVATION_DELAY_MS = 250
    WATCHLIST_TAB_SWITCH_INDICATOR_DELAY_MS = FIRST_VISIBLE_TAB_WORK_DELAY_MS
    INTERACTIVE_LOAD_REASONS = frozenset(
        {
            "placeholder_action",
            "tab_switch",
            "user",
            "restore_last_tab",
            "shell_nav",
            "command",
            "stock_signal_source",
        }
    )
    PROBE_LOAD_REASONS = frozenset({"perf_memory_probe", "perf_memory_probe_cycle"})
    CONTROLLED_STARTUP_PROBE_DEFER_KEYS = frozenset(
        {
            "watchlist",
            "lhb",
            "asian_market",
            "na_daily",
            "stock_candidates",
            "ai_industry_chain",
            "rt_monitor",
            "scan",
            "foreign_block",
            "earnings",
            "fund_holdings",
        }
    )

    def __init__(
        self,
        data_provider,
        engine,
        host=None,
        parent=None,
        *,
        background_prewarm: bool = True,
        watchlist_startup_tasks: bool = True,
        controlled_startup_probe_guard: bool = False,
    ):
        super().__init__(parent)
        self.data_provider = data_provider
        self.engine = engine
        self.host = host
        self._stock_detail_dialogs = {}
        watchlist_kwargs = {} if watchlist_startup_tasks else {"startup_tasks_enabled": False}
        self._controlled_startup_probe_guard = bool(controlled_startup_probe_guard)
        self._startup_guard_started_at = time.perf_counter()
        self._startup_last_allowed_index = -1
        self._startup_suppressed_tab_switch_keys: set[str] = set()
        self._pending_tab_activation_reasons: dict[int, str] = {}
        self._shell_group_rebuild_quiet_until = 0.0

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self.tabs = SmoothTabWidget(self)
        self.tabs.setDocumentMode(True)
        self.tabs.setTransitionEnabled(True)
        self.tabs.suspendTransitionsFor(self.STARTUP_TRANSITION_SUSPEND_MS)
        layout.addWidget(self.tabs, 1)

        self._tab_specs = self._build_tab_specs(watchlist_kwargs)

        self._tabs_by_key = {}
        self._lazy_loading_keys: set[str] = set()
        self._background_prewarm_queue: list[str] = []
        self._background_prewarm_started = False
        self._pending_restore_index: int | None = None
        self._restore_last_tab_timer: QTimer | None = None
        self._last_system_log_shell_nav_load_at = 0.0
        self._copy_hook_refresh_queued = False
        self._workspace_event_bus = None
        self._workspace_events_connected = False
        self._workspace_icon_tokens = build_ui_tokens()["icon"]
        self._mount_initial_tabs()
        self._workspace_facade = WorkspaceFacade(self)
        self.tabs.currentChanged.connect(self._on_current_tab_changed)
        self._connect_workspace_events()
        if background_prewarm:
            QTimer.singleShot(self.BACKGROUND_PREWARM_DELAY_MS, self._start_background_tab_prewarm)

    def _build_tab_specs(self, watchlist_kwargs: dict) -> list[dict]:
        return [
            {
                "key": "watchlist",
                "title": "关注池",
                "group": "主工作台",
                "group_order": 10,
                "attr": "tab_watchlist",
                "factory": self._tab_factory(
                    "WatchlistTab",
                    "ui.tabs.watchlist_tab",
                    self.data_provider,
                    self,
                    **watchlist_kwargs,
                ),
                "widget": None,
                "loaded": False,
            },
            {
                "key": "lhb",
                "title": "龙虎榜",
                "group": "主工作台",
                "group_order": 15,
                "attr": "tab_lhb",
                "factory": self._tab_factory(
                    "LhbTab", "ui.tabs.lhb_tab", self.data_provider, self, autoload_pool=False
                ),
                "widget": None,
                "loaded": False,
            },
            {
                "key": "asian_market",
                "title": "亚洲寡头",
                "group": "主工作台",
                "group_order": 20,
                "attr": "tab_asian_market",
                "factory": self._tab_factory("AsianMarketTab", "ui.tabs.asian_market_tab", self.data_provider, self),
                "widget": None,
                "loaded": False,
            },
            {
                "key": "na_daily",
                "title": "北美战报",
                "group": "主工作台",
                "group_order": 30,
                "attr": "tab_na_daily",
                "factory": self._tab_factory("NADailyTab", "ui.tabs.na_daily_tab", self.data_provider, self),
                "widget": None,
                "loaded": False,
            },
            {
                "key": "stock_candidates",
                "title": "综合候选",
                "group": "主工作台",
                "group_order": 32,
                "attr": "tab_stock_candidates",
                "factory": self._tab_factory(
                    "StockCandidateTab", "ui.tabs.stock_candidate_tab", self.data_provider, self
                ),
                "widget": None,
                "loaded": False,
            },
            {
                "key": "ai_industry_chain",
                "title": "AI产业链",
                "group": "情报源",
                "group_order": 15,
                "attr": "tab_ai_industry_chain",
                "factory": self._tab_factory(
                    "AIIndustryChainTab", "ui.tabs.ai_industry_chain_tab", self.data_provider, self
                ),
                "widget": None,
                "loaded": False,
            },
            {
                "key": "rt_monitor",
                "title": "盘中监控",
                "group": "主工作台",
                "group_order": 50,
                "attr": "tab_rt",
                "factory": self._tab_factory(
                    "RtMonitorTab", "ui.tabs.rt_monitor_tab", self.data_provider, self.engine, self
                ),
                "widget": None,
                "loaded": False,
            },
            {
                "key": "scan",
                "title": "VCP扫描",
                "group": "情报源",
                "group_order": 10,
                "attr": "tab_scan",
                "factory": self._tab_factory("ScanTab", "ui.tabs.scan_tab", self.data_provider, self.engine, self),
                "widget": None,
                "loaded": False,
            },
            {
                "key": "foreign_block",
                "title": "大宗交易",
                "group": "情报源",
                "group_order": 20,
                "attr": "tab_foreign_block",
                "factory": self._tab_factory(
                    "ForeignBlockTradeTab", "ui.tabs.foreign_block_trade_tab", self.data_provider, self
                ),
                "widget": None,
                "loaded": False,
            },
            {
                "key": "earnings",
                "title": "业绩异动",
                "group": "情报源",
                "group_order": 30,
                "attr": "tab_earnings",
                "factory": self._tab_factory("EarningsTab", "ui.tabs.earnings_tab", self.data_provider, self),
                "widget": None,
                "loaded": False,
            },
            {
                "key": "fund_holdings",
                "title": "基金持仓",
                "group": "情报源",
                "group_order": 40,
                "attr": "tab_fund_holdings",
                "factory": self._tab_factory(
                    "FundHoldingsTab", "ui.tabs.fund_holdings_tab", self.data_provider, self, autoload=False
                ),
                "widget": None,
                "loaded": False,
            },
            {
                "key": "system_log",
                "title": "系统日志",
                "group": "系统",
                "group_order": 10,
                "attr": "tab_log",
                "factory": self._tab_factory("LogTab", "ui.tabs.log_tab", self),
                "widget": None,
                "loaded": False,
            },
        ]

    def _tab_factory(self, class_name: str, module_name: str, *args, **kwargs):
        def _create(**runtime_kwargs):
            tab_class = _resolve_tab_class(class_name, module_name)
            call_kwargs = {**kwargs, **runtime_kwargs}
            return tab_class(*args, **call_kwargs)

        return _create

    def _mount_initial_tabs(self) -> None:
        icon_tokens = self._workspace_icon_tokens
        for spec in self._tab_specs:
            key = str(spec.get("key") or "").strip()
            widget = self._create_placeholder_tab(spec)
            setattr(widget, "workspace_key", key)
            spec["widget"] = widget
            spec["loaded"] = False
            setattr(self, spec["attr"], None)
            self.tabs.addTab(
                widget,
                tab_svg_icon(
                    key=key,
                    label=spec["title"],
                    color=icon_tokens["muted"],
                    size=icon_tokens["chrome_size"],
                    stroke_width=icon_tokens["stroke_width"],
                ),
                spec["title"],
            )

    def _create_real_tab(self, spec: dict, reason: str = "user"):
        factory = spec.get("factory")
        if not callable(factory):
            raise TypeError(f"missing tab factory: {spec.get('key')}")
        runtime_kwargs = {}
        key = str(spec.get("key") or "").strip()
        reason_text = str(reason or "").strip()
        first_visible_load = reason_text in self.INTERACTIVE_LOAD_REASONS
        if key == "watchlist" and reason_text == "background_prewarm":
            runtime_kwargs["startup_indicator_refresh_enabled"] = False
        elif key == "watchlist" and first_visible_load:
            runtime_kwargs["startup_indicator_refresh_delay_ms"] = self.WATCHLIST_TAB_SWITCH_INDICATOR_DELAY_MS
            runtime_kwargs["startup_followup_refresh_enabled"] = False
        elif first_visible_load and key == "lhb":
            runtime_kwargs["initial_load_delay_ms"] = self.LHB_FIRST_VISIBLE_POOL_DELAY_MS
        elif first_visible_load and key == "fund_holdings":
            runtime_kwargs["initial_load_delay_ms"] = self.FIRST_VISIBLE_TAB_WORK_DELAY_MS
        elif first_visible_load and key in {"scan", "foreign_block"}:
            runtime_kwargs["initial_cache_load_delay_ms"] = self.FIRST_VISIBLE_TAB_WORK_DELAY_MS
        elif first_visible_load and key in {"asian_market"}:
            runtime_kwargs["local_cache_delay_ms"] = self.FIRST_VISIBLE_TAB_WORK_DELAY_MS
        elif first_visible_load and key in {"ai_industry_chain", "na_daily", "stock_candidates", "earnings"}:
            runtime_kwargs["runtime_start_delay_ms"] = self.FIRST_VISIBLE_TAB_WORK_DELAY_MS
        elif key == "foreign_block" and reason_text not in self.INTERACTIVE_LOAD_REASONS:
            runtime_kwargs["autoload"] = False
        return factory(**runtime_kwargs)

    def _create_placeholder_tab(self, spec: dict) -> LazyTabPlaceholder:
        key = str(spec.get("key") or "").strip()
        title = str(spec.get("title") or key or "").strip()
        return LazyTabPlaceholder(
            title,
            lambda key=key: self.ensure_tab_loaded(key, reason="placeholder_action"),
            parent=self,
        )

    def _spec_for_key_or_index(self, key_or_index):
        if isinstance(key_or_index, int):
            if 0 <= key_or_index < len(self._tab_specs):
                return self._tab_specs[key_or_index]
            return None

        key = str(key_or_index or "").strip()
        if not key:
            return None
        for spec in self._tab_specs:
            if str(spec.get("key") or "").strip() == key:
                return spec
        return None

    def get_loaded_tab(self, key: str):
        return self._tabs_by_key.get(str(key or "").strip())

    def should_defer_probe_tab_load(self, key: str, *, reason: str = "perf_memory_probe") -> bool:
        key_text = str(key or "").strip()
        reason_text = str(reason or "").strip()
        return (
            bool(self._controlled_startup_probe_guard)
            and reason_text in self.PROBE_LOAD_REASONS
            and key_text in self.CONTROLLED_STARTUP_PROBE_DEFER_KEYS
        )

    def ensure_tab_loaded(self, key_or_index, reason: str = "user"):
        spec = self._spec_for_key_or_index(key_or_index)
        if spec is None:
            return None

        key = str(spec.get("key") or "").strip()
        with ui_stall_span("ClassicWorkspace.ensure_tab_loaded", tab=key, signal=reason):
            return self._ensure_tab_loaded_impl(spec, key, reason)

    def _ensure_tab_loaded_impl(self, spec: dict, key: str, reason: str = "user"):
        if spec.get("loaded"):
            return spec.get("widget")

        placeholder = spec.get("widget")
        if isinstance(placeholder, LazyTabPlaceholder):
            placeholder.set_loading()

        try:
            widget = self._create_real_tab(spec, reason=reason)
        except Exception as exc:
            self._lazy_loading_keys.discard(key)
            if isinstance(placeholder, LazyTabPlaceholder):
                placeholder.set_error(str(exc))
            log.error(f"[Workspace] lazy tab load failed key={key} reason={reason}: {exc}", exc_info=True)
            return None

        index = self._tab_index_for_key(key)
        if index < 0:
            self._lazy_loading_keys.discard(key)
            return widget

        load_reason = str(reason or "")
        setattr(widget, "workspace_key", key)
        setattr(widget, "_workspace_load_reason", load_reason)
        setattr(
            widget,
            "_workspace_noninteractive_loaded",
            load_reason not in self.INTERACTIVE_LOAD_REASONS,
        )
        current_index = self.tabs.currentIndex()
        previous_blocked = self.tabs.blockSignals(True)
        old_widget = spec.get("widget")
        try:
            self.tabs.removeTab(index)
            icon_tokens = self._workspace_icon_tokens
            self.tabs.insertTab(
                index,
                widget,
                tab_svg_icon(
                    key=key,
                    label=spec.get("title", ""),
                    color=icon_tokens["muted"],
                    size=icon_tokens["chrome_size"],
                    stroke_width=icon_tokens["stroke_width"],
                ),
                spec.get("title", ""),
            )
            if 0 <= current_index < self.tabs.count():
                self.tabs.setCurrentIndex(current_index)
        finally:
            self.tabs.blockSignals(previous_blocked)

        if old_widget is not None and old_widget is not widget:
            old_widget.deleteLater()

        spec["widget"] = widget
        spec["loaded"] = True
        self._tabs_by_key[key] = widget
        setattr(self, spec["attr"], widget)
        if key == "system_log" and load_reason == "shell_nav":
            self._last_system_log_shell_nav_load_at = time.perf_counter()
        self._lazy_loading_keys.discard(key)
        ensure_polished = getattr(widget, "ensurePolished", None)
        if callable(ensure_polished):
            QTimer.singleShot(0, ensure_polished)
        QTimer.singleShot(250, lambda widget=widget: setattr(widget, "_workspace_load_reason", ""))
        self._notify_tab_loaded(key, widget)
        if self.tabs.currentWidget() is widget:
            self._startup_last_allowed_index = index
            self._notify_tab_activated(key, widget)
        return widget

    def _on_current_tab_changed(self, index: int) -> None:
        spec = self._spec_for_key_or_index(index)
        key = str((spec or {}).get("key") or "").strip()
        with ui_stall_span("ClassicWorkspace._on_current_tab_changed", tab=key, signal="currentChanged"):
            if spec is None:
                return
            reason = self._take_tab_activation_reason(index)
            if spec.get("loaded"):
                widget = spec.get("widget")
                if widget is not None:
                    self._startup_last_allowed_index = index
                    self._notify_tab_activated(key, widget)
                return
            if not key or key in self._lazy_loading_keys:
                return
            if self._should_suppress_startup_tab_switch(key, reason):
                self._restore_startup_allowed_tab_after_suppressed_switch(key)
                return
            self._queue_lazy_tab_load(spec, key, reason=reason or "tab_switch", index=index)

    def _take_tab_activation_reason(self, index: int) -> str:
        return self._pending_tab_activation_reasons.pop(int(index), "tab_switch")

    def _is_startup_raw_tab_switch_guard_active(self) -> bool:
        return (time.perf_counter() - self._startup_guard_started_at) * 1000.0 < self.STARTUP_RAW_TAB_SWITCH_GUARD_MS

    def _should_suppress_startup_tab_switch(self, key: str, reason: str) -> bool:
        return (
            bool(key)
            and str(reason or "").strip() == "tab_switch"
            and self._is_startup_raw_tab_switch_guard_active()
        )

    def _queue_lazy_tab_load(self, spec: dict, key: str, *, reason: str, index: int | None = None) -> bool:
        if not key or key in self._lazy_loading_keys:
            return False
        self._lazy_loading_keys.add(key)
        if isinstance(index, int) and 0 <= index < self.tabs.count():
            self._startup_last_allowed_index = index
        placeholder = spec.get("widget")
        if isinstance(placeholder, LazyTabPlaceholder):
            placeholder.set_loading()
        QTimer.singleShot(
            self._lazy_tab_load_delay_ms(reason),
            lambda key=key, reason=reason: self.ensure_tab_loaded(key, reason=reason),
        )
        return True

    def prepare_shell_group_rebuild_navigation(self, *, interval_ms: int = 0) -> None:
        try:
            interval = max(0, int(interval_ms or 0))
        except (TypeError, ValueError):
            interval = 0
        if interval <= 0:
            return
        self._shell_group_rebuild_quiet_until = max(
            float(getattr(self, "_shell_group_rebuild_quiet_until", 0.0) or 0.0),
            time.perf_counter() + interval / 1000.0,
        )

    def _is_shell_group_rebuild_quiet_window(self) -> bool:
        return time.perf_counter() < float(getattr(self, "_shell_group_rebuild_quiet_until", 0.0) or 0.0)

    def _lazy_tab_load_delay_ms(self, reason: str) -> int:
        if str(reason or "").strip() == "shell_nav" and self._is_shell_group_rebuild_quiet_window():
            return self.SHELL_GROUP_REBUILD_LOAD_DELAY_MS
        return 0

    def _activation_callback_delay_ms(self) -> int:
        if self._is_shell_group_rebuild_quiet_window():
            return self.SHELL_GROUP_REBUILD_ACTIVATION_DELAY_MS
        return 0

    def _restore_startup_allowed_tab_after_suppressed_switch(self, key: str) -> None:
        restore_index = self._startup_last_allowed_index
        if not (0 <= restore_index < self.tabs.count()):
            return
        if restore_index == self.tabs.currentIndex():
            return
        if key not in self._startup_suppressed_tab_switch_keys:
            self._startup_suppressed_tab_switch_keys.add(key)
            log.info(
                "[Workspace] suppress startup raw tab switch key=%s restore_index=%s",
                key,
                restore_index,
            )

        def _restore() -> None:
            if not (0 <= restore_index < self.tabs.count()):
                return
            if self.tabs.currentIndex() == restore_index:
                return
            self._pending_tab_activation_reasons[restore_index] = "startup_guard_restore"
            self.tabs.setCurrentIndex(restore_index)

        QTimer.singleShot(0, _restore)

    def activate_tab(self, index: int, *, reason: str = "user") -> bool:
        try:
            target_index = int(index)
        except (TypeError, ValueError):
            return False
        if not (0 <= target_index < self.tabs.count()):
            return False

        reason_text = str(reason or "").strip() or "user"
        if self.tabs.currentIndex() == target_index:
            spec = self._spec_for_key_or_index(target_index)
            key = str((spec or {}).get("key") or "").strip()
            self._pending_tab_activation_reasons.pop(target_index, None)
            if spec is None or not key:
                return True
            if spec.get("loaded"):
                widget = spec.get("widget")
                if widget is not None:
                    self._startup_last_allowed_index = target_index
                    self._notify_tab_activated(key, widget)
                return True
            self._queue_lazy_tab_load(spec, key, reason=reason_text, index=target_index)
            return True

        self._pending_tab_activation_reasons[target_index] = reason_text
        self.tabs.setCurrentIndex(target_index)
        return True

    def _connect_workspace_events(self) -> None:
        try:
            from app.services.ui_event_service import domain_events as event_bus

            event_bus.sig_ai_industry_chain_updated.connect(self._on_ai_industry_chain_source_updated)
            event_bus.sig_fund_holdings_updated.connect(self._on_fund_holdings_source_updated)
            self._workspace_event_bus = event_bus
            self._workspace_events_connected = True
        except (AttributeError, ImportError, OSError, RuntimeError, TypeError, ValueError) as exc:
            log.debug(f"[Workspace] skip workspace event wiring: {exc}")

    def _disconnect_workspace_events(self) -> None:
        event_bus = getattr(self, "_workspace_event_bus", None)
        if event_bus is None or not getattr(self, "_workspace_events_connected", False):
            return
        try:
            event_bus.sig_ai_industry_chain_updated.disconnect(self._on_ai_industry_chain_source_updated)
        except (AttributeError, RuntimeError, TypeError, ValueError):
            pass
        try:
            event_bus.sig_fund_holdings_updated.disconnect(self._on_fund_holdings_source_updated)
        except (AttributeError, RuntimeError, TypeError, ValueError):
            pass
        self._workspace_events_connected = False

    def _on_ai_industry_chain_source_updated(self, *_args) -> None:
        _resolve_workspace_facade(self).refresh_tabs_after_ai_industry_chain_update()
        self.prime_stock_context_snapshots(force=True, include_lhb=False)

    def _on_fund_holdings_source_updated(self, *_args) -> None:
        self.prime_stock_context_snapshots(force=True, include_lhb=False)

    def _start_background_tab_prewarm(self) -> None:
        if self._background_prewarm_started:
            return
        self._background_prewarm_started = True
        self.prime_stock_context_snapshots(include_lhb=False)

        prewarm_keys = set(self.BACKGROUND_PREWARM_KEYS)
        unloaded_keys = [
            str(spec.get("key") or "").strip()
            for spec in self._tab_specs
            if not spec.get("loaded")
            and str(spec.get("key") or "").strip()
            and str(spec.get("key") or "").strip() in prewarm_keys
        ]
        current_spec = self._spec_for_key_or_index(self.tabs.currentIndex())
        current_key = str((current_spec or {}).get("key") or "").strip()
        lead_key = current_key if current_key in prewarm_keys else ""
        if lead_key in unloaded_keys:
            unloaded_keys.remove(lead_key)
            unloaded_keys.insert(0, lead_key)
        priority_insert_at = 1 if lead_key and unloaded_keys[:1] == [lead_key] else 0
        for priority_key in reversed(self.CONTEXT_PREWARM_PRIORITY):
            if priority_key not in unloaded_keys:
                continue
            unloaded_keys.remove(priority_key)
            unloaded_keys.insert(priority_insert_at, priority_key)
        self._background_prewarm_queue = unloaded_keys
        self._prewarm_next_tab()

    def _prewarm_next_tab(self) -> None:
        with ui_stall_span("ClassicWorkspace._prewarm_next_tab", signal="background_prewarm"):
            while self._background_prewarm_queue:
                key = self._background_prewarm_queue.pop(0)
                spec = self._spec_for_key_or_index(key)
                if spec is None or spec.get("loaded"):
                    continue

                widget = self.ensure_tab_loaded(key, reason="background_prewarm")
                if widget is not None:
                    self._prime_tab_runtime(widget)
                break

            if self._background_prewarm_queue:
                QTimer.singleShot(self.BACKGROUND_PREWARM_INTERVAL_MS, self._prewarm_next_tab)

    def _prime_tab_runtime(self, widget) -> None:
        for method_name in (
            "prime_background_load",
            "_ensure_runtime_started",
            "_ensure_initial_load_started",
            "_ensure_pool_bootstrap_started",
        ):
            method = getattr(widget, method_name, None)
            if not callable(method):
                continue
            try:
                method()
            except (AttributeError, OSError, RuntimeError, TypeError, ValueError) as exc:
                log.warning(f"[Workspace] prime tab runtime failed {widget.__class__.__name__}.{method_name}: {exc}")
            return

    def _schedule_workspace_table_copy_hooks(self) -> None:
        host = self.host or self.window()
        install_hooks = getattr(host, "install_workspace_table_copy_hooks", None)
        if not callable(install_hooks) or self._copy_hook_refresh_queued:
            return
        self._copy_hook_refresh_queued = True

        def _install_hooks() -> None:
            self._copy_hook_refresh_queued = False
            try:
                install_hooks()
            except (AttributeError, RuntimeError, TypeError, ValueError) as exc:
                log.warning(f"[Workspace] table copy hook install failed: {exc}")

        QTimer.singleShot(self.COPY_HOOK_REFRESH_DELAY_MS, _install_hooks)

    def _notify_tab_loaded(self, _key: str, _widget) -> None:
        self._schedule_workspace_table_copy_hooks()
        host = self.host or self.window()
        if str(_key or "").strip() == "rt_monitor":
            restore_rt_cache = getattr(host, "restore_pending_rt_cache", None)
            if callable(restore_rt_cache):
                QTimer.singleShot(0, restore_rt_cache)

    def _notify_tab_activated(self, _key: str, widget) -> None:
        callback = getattr(widget, "on_workspace_tab_activated", None)
        if callable(callback):
            def _run_if_current(widget=widget, callback=callback) -> None:
                current_widget = getattr(self.tabs, "currentWidget", None)
                if callable(current_widget):
                    try:
                        if current_widget() is not widget:
                            return
                    except (AttributeError, RuntimeError, TypeError, ValueError):
                        return
                callback()

            QTimer.singleShot(self._activation_callback_delay_ms(), _run_if_current)

    def tab_specs(self) -> list[dict]:
        return list(self._tab_specs)

    def nav_groups(self) -> list[str]:
        return _resolve_workspace_facade(self).nav_groups()

    def tab_indices_by_group(self) -> dict[str, list[int]]:
        return _resolve_workspace_facade(self).tab_indices_by_group()

    def restore_last_tab(self, index: int):
        if 0 <= index < self.tabs.count():
            self.activate_tab(index, reason="restore_last_tab")

    def schedule_restore_last_tab(self, index: int, *, delay_ms: int | None = None) -> None:
        if not isinstance(index, int) or index < 0:
            return
        self._pending_restore_index = index
        try:
            delay = self.RESTORE_LAST_TAB_DELAY_MS if delay_ms is None else max(0, int(delay_ms))
        except (TypeError, ValueError):
            return

        previous_timer = self._restore_last_tab_timer
        if previous_timer is not None:
            previous_timer.stop()
            previous_timer.deleteLater()

        timer = QTimer(self)
        timer.setSingleShot(True)
        self._restore_last_tab_timer = timer

        def _restore() -> None:
            if self._restore_last_tab_timer is timer:
                self._restore_last_tab_timer = None
            self.restore_last_tab(index)
            timer.deleteLater()

        timer.timeout.connect(_restore)
        timer.start(delay)

    def current_tab_index(self) -> int:
        return self.tabs.currentIndex()

    def get_tab(self, key: str):
        return self.ensure_tab_loaded(key)

    def iter_tabs(self) -> list:
        return list(self._tabs_by_key.values())

    def get_realtime_quote_codes(self) -> set[str]:
        return _resolve_workspace_facade(self).get_realtime_quote_codes()

    def get_scan_results(self) -> list[dict]:
        return _resolve_workspace_facade(self).get_scan_results()

    def get_rt_table(self):
        return _resolve_workspace_facade(self).get_rt_table()

    def iter_tables(self) -> list:
        return _resolve_workspace_facade(self).iter_tables()

    def iter_refreshable_tabs(self) -> list:
        return _resolve_workspace_facade(self).iter_refreshable_tabs()

    def refresh_all_tabs_after_f5(self, *, skip_cache_reload_tabs: bool = False) -> None:
        _resolve_workspace_facade(self).refresh_all_tabs_after_f5(skip_cache_reload_tabs=skip_cache_reload_tabs)

    def refresh_all_tabs_after_f5_scheduled(
        self,
        *,
        on_finished=None,
        interval_ms: int = 0,
        skip_cache_reload_tabs: bool = False,
    ) -> bool:
        return _resolve_workspace_facade(self).refresh_all_tabs_after_f5_scheduled(
            on_finished=on_finished,
            interval_ms=interval_ms,
            skip_cache_reload_tabs=skip_cache_reload_tabs,
        )

    def refresh_tabs_after_ai_industry_chain_update(self) -> dict[str, bool]:
        return _resolve_workspace_facade(self).refresh_tabs_after_ai_industry_chain_update()

    def refresh_information_sources_after_f5(self) -> dict[str, bool]:
        return _resolve_workspace_facade(self).refresh_information_sources_after_f5()

    def refresh_information_sources_after_f5_scheduled(
        self,
        *,
        on_finished=None,
        interval_ms: int = 2500,
        frame_budget_ms: int = 4,
    ) -> bool:
        return _resolve_workspace_facade(self).refresh_information_sources_after_f5_scheduled(
            on_finished=on_finished,
            interval_ms=interval_ms,
            frame_budget_ms=frame_budget_ms,
        )

    def select_scan_row(self, index: int) -> bool:
        return _resolve_workspace_facade(self).select_scan_row(index)

    def is_rt_monitor_running(self) -> bool:
        return _resolve_workspace_facade(self).is_rt_monitor_running()

    def toggle_rt_monitor(self) -> bool:
        return _resolve_workspace_facade(self).toggle_rt_monitor()

    def run_incremental_scan(self) -> bool:
        return _resolve_workspace_facade(self).run_incremental_scan()

    def open_scan_settings(self) -> bool:
        return _resolve_workspace_facade(self).open_scan_settings()

    def refresh_lhb_history(self) -> bool:
        return _resolve_workspace_facade(self).refresh_lhb_history()

    def run_fund_holdings_sync(self) -> bool:
        return _resolve_workspace_facade(self).run_fund_holdings_sync()

    def run_fund_holdings_auto_sync_after_f5(self) -> bool:
        return _resolve_workspace_facade(self).run_fund_holdings_auto_sync_after_f5()

    def select_code_row(self, code: str, preferred_tab_index: int | None = None) -> bool:
        return _resolve_workspace_facade(self).select_code_row(code, preferred_tab_index)

    def refresh_watchlist_names(self, code2name: dict[str, str]) -> bool:
        return _resolve_workspace_facade(self).refresh_watchlist_names(code2name)

    def schedule_watchlist_special_quotes(self, task_manager) -> None:
        _resolve_workspace_facade(self).schedule_watchlist_special_quotes(task_manager)

    def run_post_online_refresh(self, task_manager) -> None:
        _resolve_workspace_facade(self).run_post_online_refresh(task_manager)

    def auto_start_rt_monitor(self) -> bool:
        return _resolve_workspace_facade(self).auto_start_rt_monitor()

    def collect_watchlist_radar_data(
        self,
        *,
        include_cache_fallback: bool = False,
        include_source_cache_fallback: bool | None = None,
        target_codes=None,
        allow_lhb_cache_compute: bool = False,
    ) -> tuple[dict, dict, dict, dict, dict, dict | None]:
        return _resolve_workspace_facade(self).collect_watchlist_radar_data(
            include_cache_fallback=include_cache_fallback,
            include_source_cache_fallback=include_source_cache_fallback,
            target_codes=target_codes,
            allow_lhb_cache_compute=allow_lhb_cache_compute,
        )

    def collect_stock_signals(self) -> list[StockSignal]:
        return _resolve_workspace_facade(self).collect_stock_signals()

    def collect_stock_context(
        self,
        *,
        include_cache_fallback: bool = True,
        include_source_cache_fallback: bool | None = None,
        allow_lhb_cache_compute: bool = False,
        allow_async_snapshot_refresh: bool = True,
    ) -> dict[str, list[StockSignal]]:
        return _resolve_workspace_facade(self).collect_stock_context(
            include_cache_fallback=include_cache_fallback,
            include_source_cache_fallback=include_source_cache_fallback,
            allow_lhb_cache_compute=allow_lhb_cache_compute,
            allow_async_snapshot_refresh=allow_async_snapshot_refresh,
        )

    def prime_stock_context_snapshots(
        self,
        *,
        force: bool = False,
        include_fund: bool = True,
        include_lhb: bool = True,
    ) -> bool:
        return _resolve_workspace_facade(self).prime_stock_context_snapshots(
            force=force,
            include_fund=include_fund,
            include_lhb=include_lhb,
        )

    def open_security_detail(self, code: str, context=None):
        code_text = str(code or "").strip()
        if not code_text:
            return False

        context = context if isinstance(context, dict) else {}
        name = str(context.get("name") or context.get("名称") or "").strip()
        if not name:
            code2name = getattr(self.data_provider, "code2name", {}) or {}
            name = str(code2name.get(code_text, "") or "").strip()

        tab_titles = {
            str(spec.get("key") or "").strip(): str(spec.get("title") or "").strip() for spec in self.tab_specs()
        }
        signals = self.collect_stock_context().get(code_text, [])
        detail_context = context.get("vcp_data")
        detail_context = dict(detail_context) if isinstance(detail_context, dict) else {}

        from ui.components.stock_detail_dialog import StockDetailDialog

        detail_dialogs = getattr(self, "_stock_detail_dialogs", None)
        if detail_dialogs is None:
            detail_dialogs = {}
            setattr(self, "_stock_detail_dialogs", detail_dialogs)

        existing_dialog = detail_dialogs.get(code_text)
        if existing_dialog is not None:
            try:
                if existing_dialog.isVisible():
                    existing_dialog.raise_()
                    existing_dialog.activateWindow()
                    return True
            except RuntimeError:
                pass
            detail_dialogs.pop(code_text, None)

        dialog = StockDetailDialog(
            code_text,
            name,
            signals,
            tab_titles=tab_titles,
            activate_callback=self._activate_stock_signal_source,
            context=detail_context,
            parent=self.window(),
        )
        detail_dialogs[code_text] = dialog
        dialog.destroyed.connect(lambda _obj=None, key=code_text: detail_dialogs.pop(key, None))
        dialog.show()
        try:
            dialog.raise_()
            dialog.activateWindow()
        except RuntimeError:
            pass
        return True

    def _tab_index_for_key(self, key: str) -> int:
        key_text = str(key or "").strip()
        if not key_text:
            return -1
        for index, spec in enumerate(self.tab_specs()):
            if str(spec.get("key") or "").strip() == key_text:
                return index
        return -1

    def _activate_stock_signal_source(self, signal: StockSignal) -> bool:
        code_text = signal.normalized_code()
        if not code_text:
            return False

        source_index = ClassicWorkspace._tab_index_for_key(self, signal.source_tab)
        if source_index >= 0:
            activate_tab = getattr(self, "activate_tab", None)
            if callable(activate_tab):
                activate_tab(source_index, reason="stock_signal_source")
            else:
                self.tabs.setCurrentIndex(source_index)
            tab = self.get_tab(signal.source_tab)
            select_code_row = getattr(tab, "select_code_row", None)
            if callable(select_code_row):
                return bool(select_code_row(code_text))

        return self.select_code_row(code_text, preferred_tab_index=source_index if source_index >= 0 else None)

    def shutdown(self):
        restore_timer = getattr(self, "_restore_last_tab_timer", None)
        if restore_timer is not None:
            restore_timer.stop()
            restore_timer.deleteLater()
            self._restore_last_tab_timer = None

        disconnect_events = getattr(self, "_disconnect_workspace_events", None)
        if callable(disconnect_events):
            disconnect_events()
        for tab in self.iter_tabs():
            shutdown = getattr(tab, "shutdown", None)
            if not callable(shutdown):
                continue
            try:
                shutdown()
            except (AttributeError, OSError, RuntimeError, TypeError, ValueError) as exc:
                log.warning(f"[Workspace] {tab.__class__.__name__} shutdown failed: {exc}")
