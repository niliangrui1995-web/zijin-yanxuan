# -*- coding: utf-8 -*-
from __future__ import annotations

import time
from functools import wraps
from importlib import import_module

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtWidgets import QLabel, QPushButton, QSizePolicy, QVBoxLayout, QWidget

from app.services.stock_context_model_service import StockContextSnapshot, StockSignal
from app.services.stock_context_query_service import GENERAL_STOCK_CONTEXT_SOURCE_KEYS
from app.services.ui_diagnostics_service import ui_stall_span
from core.logger import get_logger
from ui.components.smooth_tab_widget import SmoothTabWidget
from ui.components.vector_icons import tab_svg_icon
from ui.theme_tokens import build_ui_tokens
from ui.workspaces.background_tab_preload import (
    BackgroundTabPreloadCoordinator,
    cancel_background_tab_preload,
)
from ui.workspaces.tab_registry import (
    INTERACTIVE_TAB_LOAD_REASONS,
    PROBE_TAB_LOAD_REASONS,
    TAB_DEFINITIONS,
    TabConstructorProfile,
    TabDefinition,
    TabLoadReason,
    TabRuntimeDelayPolicy,
    get_tab_definition,
    is_interactive_tab_load_reason,
    normalize_tab_load_reason,
    startup_tab_keys,
    widget_prewarm_tab_keys,
)
from ui.workspaces.workspace_facade import WorkspaceFacade

log = get_logger(__name__)


WatchlistTab = None
AsianMarketTab = None
NADailyTab = None
StockCandidateTab = None
AIIndustryChainTab = None
LhbTab = None
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


def _tab_factory_for_definition(workspace, definition: TabDefinition, watchlist_kwargs: dict):
    def _create(**runtime_kwargs):
        runtime_kwargs = dict(runtime_kwargs)
        construction_parent = runtime_kwargs.pop("_workspace_parent_override", workspace)
        profile = definition.constructor_profile
        args = (workspace.data_provider, construction_parent)
        kwargs = definition.constructor_default_kwargs()
        if profile is TabConstructorProfile.WATCHLIST:
            kwargs.update(watchlist_kwargs)
        elif profile is TabConstructorProfile.SCAN:
            args = (workspace.data_provider, workspace.engine, construction_parent)
        elif profile is TabConstructorProfile.WORKSPACE_PARENT:
            args = (construction_parent,)
        elif profile not in {
            TabConstructorProfile.DATA_PROVIDER_PARENT,
            TabConstructorProfile.LHB,
            TabConstructorProfile.FUND_HOLDINGS,
        }:
            raise ValueError(f"unsupported tab constructor profile: {profile}")
        factory = workspace._tab_factory(definition.class_name, definition.module_name, *args, **kwargs)
        return factory(**runtime_kwargs)

    return _create


def _watchlist_runtime_kwargs(definition: TabDefinition, reason_text: str, first_visible_load: bool, workspace) -> dict:
    if reason_text == TabLoadReason.BACKGROUND_PREWARM.value:
        return {"startup_indicator_refresh_enabled": False}
    if not first_visible_load:
        return definition.noninteractive_default_kwargs()
    return {
        "startup_indicator_refresh_delay_ms": workspace.WATCHLIST_TAB_SWITCH_INDICATOR_DELAY_MS,
        "startup_followup_refresh_enabled": False,
    }


def _first_visible_runtime_delay_ms(policy: TabRuntimeDelayPolicy, reason_text: str, workspace) -> int:
    if policy is TabRuntimeDelayPolicy.LHB_POOL:
        return (
            workspace.LHB_SHELL_NAV_POOL_DELAY_MS
            if reason_text == TabLoadReason.SHELL_NAV.value
            else workspace.LHB_FIRST_VISIBLE_POOL_DELAY_MS
        )
    if policy is TabRuntimeDelayPolicy.SHELL_HEAVY and reason_text == TabLoadReason.SHELL_NAV.value:
        return workspace.SHELL_NAV_HEAVY_TAB_WORK_DELAY_MS
    return workspace.FIRST_VISIBLE_TAB_WORK_DELAY_MS


def _runtime_kwargs_for_definition(
    definition: TabDefinition,
    reason_text: str,
    first_visible_load: bool,
    workspace,
) -> dict:
    policy = definition.runtime_delay_policy
    if policy is TabRuntimeDelayPolicy.WATCHLIST:
        return _watchlist_runtime_kwargs(definition, reason_text, first_visible_load, workspace)
    if not first_visible_load:
        return definition.noninteractive_default_kwargs()
    kwarg = str(definition.runtime_delay_kwarg or "").strip()
    if not kwarg or policy is TabRuntimeDelayPolicy.NONE:
        return {}
    return {kwarg: _first_visible_runtime_delay_ms(policy, reason_text, workspace)}


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

        self.lbl_detail = QLabel("首屏就绪后会按依赖顺序后台加载；点击可立即优先打开。", self)
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

    def set_waiting_for_runtime(self) -> None:
        self.lbl_detail.setText("数据服务正在初始化，页面会在就绪后自动加载。")
        self.btn_load.setEnabled(False)
        self.btn_load.setText("准备中")

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


def _workspace_stock_context_snapshots_settled(workspace) -> bool:
    return bool(_resolve_workspace_facade(workspace).stock_context_snapshots_settled())


def _cancel_workspace_stock_context_snapshots(workspace, *, reason: str) -> bool:
    return bool(_resolve_workspace_facade(workspace).cancel_stock_context_snapshots(reason=reason))


def _handle_startup_cache_bootstrap_ready(workspace, *_args) -> None:
    if getattr(workspace, "_shutting_down", False):
        return
    workspace._startup_cache_bootstrap_ready = True
    coordinator = getattr(workspace, "_background_preload_coordinator", None)
    priority_pending = bool(getattr(coordinator, "_priority_reasons", None))
    if workspace._initial_real_tab_activated or priority_pending:
        workspace._start_background_tab_prewarm()


def _shutdown_workspace_facade(workspace) -> None:
    facade = getattr(workspace, "_workspace_facade", None)
    shutdown = getattr(facade, "shutdown", None)
    if not callable(shutdown):
        return
    try:
        shutdown(timeout_ms=750)
    except (AttributeError, OSError, RuntimeError, TypeError, ValueError) as exc:
        log.warning(f"[Workspace] stock context shutdown failed: {exc}")


def _workspace_is_stopping(workspace) -> bool:
    return bool(getattr(workspace, "_shutting_down", False))


def _prefer_tab_load_reason(current: object, incoming: object) -> str:
    current_text = normalize_tab_load_reason(current)
    incoming_text = normalize_tab_load_reason(incoming)
    if current_text in INTERACTIVE_TAB_LOAD_REASONS:
        return current_text
    if incoming_text in INTERACTIVE_TAB_LOAD_REASONS:
        return incoming_text
    return incoming_text or current_text


def _load_queued_tab(workspace, key: str, reason: str) -> None:
    if _workspace_is_stopping(workspace):
        workspace._lazy_loading_keys.discard(key)
        return
    if key not in workspace._lazy_loading_keys:
        return
    workspace.ensure_tab_loaded(key, reason=reason)


def _tab_runtime_dependencies_ready(workspace, spec: dict) -> bool:
    profile = str(spec.get("constructor_profile") or "").strip()
    if profile == TabConstructorProfile.WORKSPACE_PARENT.value:
        return True
    if workspace.data_provider is None:
        return False
    return profile != TabConstructorProfile.SCAN.value or workspace.engine is not None


def _defer_tab_until_runtime_ready(workspace, spec: dict, key: str, reason: str, placeholder) -> bool:
    if spec.get("loaded") or _tab_runtime_dependencies_ready(workspace, spec):
        return False
    workspace._lazy_loading_keys.add(key)
    workspace._runtime_pending_tab_loads[key] = _prefer_tab_load_reason(
        workspace._runtime_pending_tab_loads.get(key),
        reason,
    )
    if isinstance(placeholder, LazyTabPlaceholder):
        placeholder.set_waiting_for_runtime()
    return True


def _attach_workspace_runtime_services(workspace, *, data_provider=None, engine=None) -> None:
    if data_provider is not None:
        workspace.data_provider = data_provider
    if engine is not None:
        workspace.engine = engine
    for key, reason in tuple(workspace._runtime_pending_tab_loads.items()):
        spec = workspace._spec_for_key_or_index(key)
        if spec is None or not _tab_runtime_dependencies_ready(workspace, spec):
            continue
        workspace._runtime_pending_tab_loads.pop(key, None)
        QTimer.singleShot(0, lambda key=key, reason=reason: _load_queued_tab(workspace, key, reason))
    start_prewarm = getattr(workspace, "_start_background_tab_prewarm", None)
    if callable(start_prewarm):
        start_prewarm()


def _cancel_pending_startup_restore_for_user(workspace, reason: str) -> None:
    reason_text = normalize_tab_load_reason(reason)
    if reason_text not in INTERACTIVE_TAB_LOAD_REASONS or reason_text == TabLoadReason.RESTORE_LAST_TAB.value:
        return
    restore_timer = getattr(workspace, "_restore_last_tab_timer", None)
    if restore_timer is not None:
        restore_timer.stop()
        if workspace._restore_last_tab_timer is restore_timer:
            workspace._restore_last_tab_timer = None
        restore_timer.deleteLater()
    callback = getattr(getattr(workspace, "host", None), "cancel_pending_workspace_activation", None)
    if callable(callback):
        callback(workspace)
    coordinator = getattr(workspace, "_background_preload_coordinator", None)
    discard_restore = getattr(coordinator, "discard_priority_reason", None)
    if callable(discard_restore):
        discard_restore(TabLoadReason.RESTORE_LAST_TAB.value)


def _pause_interactive_handoff_for_visible_watchlist(
    workspace,
    key: str,
    reason: object,
) -> bool:
    if key != "watchlist":
        return False
    coordinator = getattr(workspace, "_background_preload_coordinator", None)
    pause = getattr(coordinator, "pause_interactive_handoff_for_watchlist", None)
    return bool(callable(pause) and pause(reason))


def _clear_runtime_pending_tab_loads(workspace) -> None:
    pending = getattr(workspace, "_runtime_pending_tab_loads", None)
    if pending is not None:
        pending.clear()


def _capture_workspace_stock_context(
    workspace,
    *,
    include_rps_bundle: bool = True,
    sources=None,
    target_codes=None,
) -> StockContextSnapshot:
    facade = _resolve_workspace_facade(workspace)
    if sources is None and target_codes is None:
        return (
            facade.capture_stock_context_snapshot()
            if include_rps_bundle
            else facade.capture_stock_context_snapshot(include_rps_bundle=False)
        )
    return facade.capture_stock_context_snapshot(
        include_rps_bundle=include_rps_bundle,
        sources=sources,
        target_codes=target_codes,
    )


def _publish_workspace_stock_context_index(workspace, index) -> int:
    return _resolve_workspace_facade(workspace).publish_stock_context_signal_index(index)


def _published_workspace_stock_context_signals(workspace, code: str):
    return _resolve_workspace_facade(workspace).get_published_stock_context_signals(code)


def _security_detail_name(workspace, code_text: str, context: dict) -> str:
    name = str(context.get("name") or context.get("名称") or "").strip()
    if name:
        return name
    code2name = getattr(workspace.data_provider, "code2name", {}) or {}
    return str(code2name.get(code_text, "") or "").strip()


def _security_detail_inputs(workspace, code_text: str, context) -> tuple[str, dict[str, str], dict]:
    context = context if isinstance(context, dict) else {}
    name = _security_detail_name(workspace, code_text, context)
    specs = getattr(workspace, "tab_specs")()
    tab_titles = {
        str(spec.get("key") or "").strip(): str(spec.get("title") or "").strip()
        for spec in specs
    }
    detail_context = context.get("vcp_data")
    return name, tab_titles, dict(detail_context) if isinstance(detail_context, dict) else {}


def _initialize_workspace_runtime_state(workspace, *, controlled_startup_probe_guard: bool) -> None:
    workspace._stock_detail_dialogs = {}
    workspace._stock_detail_refresh_slots = {}
    workspace._controlled_startup_probe_guard = bool(controlled_startup_probe_guard)
    workspace._startup_guard_started_at = time.perf_counter()
    workspace._startup_last_allowed_index = -1
    workspace._startup_suppressed_tab_switch_keys = set()
    workspace._pending_tab_activation_reasons = {}
    workspace._shell_group_rebuild_quiet_until = 0.0
    workspace._tabs_by_key, workspace._runtime_pending_tab_loads = {}, {}
    workspace._lazy_loading_keys = set()
    workspace._background_prewarm_queue = []
    workspace._background_prewarm_started = False
    workspace._background_prewarm_finished = False
    workspace._background_prewarm_finished_at = 0.0
    workspace._background_prewarm_enabled = False
    workspace._background_prewarm_timer = None
    workspace._background_prewarm_active_key = ""
    workspace._background_prewarm_active_widget = None
    workspace._background_prewarm_active_started_at = 0.0
    workspace._background_preload_staging_host = None
    workspace._background_prewarm_start_order = []
    workspace._background_prewarm_completion_order = []
    workspace._background_prewarm_failures = {}
    workspace._background_prewarm_timeouts = []
    workspace._initial_real_tab_activated = False
    workspace._startup_cache_bootstrap_required = bool(
        getattr(getattr(workspace, "host", None), "_startup_enabled", False)
    )
    workspace._startup_cache_bootstrap_ready = not workspace._startup_cache_bootstrap_required
    workspace._restore_last_tab_timer = None
    workspace._last_shell_nav_load_at = 0.0
    workspace._last_system_log_shell_nav_load_at = 0.0
    workspace._copy_hook_refresh_queued = False
    workspace._workspace_event_bus = None
    workspace._workspace_events_connected = False
    workspace._workspace_icon_tokens = build_ui_tokens()["icon"]


def _activate_existing_stock_detail(dialog) -> bool:
    if dialog is None:
        return False
    try:
        if not dialog.isVisible():
            return False
        dialog.raise_()
        dialog.activateWindow()
        return True
    except RuntimeError:
        return False


def _disconnect_stock_detail_refresh(slot) -> None:
    if slot is None:
        return
    from app.services.ui_event_service import domain_events

    try:
        domain_events.sig_stock_context_snapshot_updated.disconnect(slot)
    except (TypeError, RuntimeError):
        pass


def _attach_stock_detail_refresh(workspace, code_text: str, dialog, detail_dialogs: dict) -> None:
    from app.services.ui_event_service import domain_events

    refresh_slots = getattr(workspace, "_stock_detail_refresh_slots", None)
    if refresh_slots is None:
        refresh_slots = {}
        workspace._stock_detail_refresh_slots = refresh_slots
    _disconnect_stock_detail_refresh(refresh_slots.pop(code_text, None))

    def _refresh_detail(*_args, key=code_text, target_dialog=dialog) -> None:
        if _workspace_is_stopping(workspace) or detail_dialogs.get(key) is not target_dialog:
            return
        updated = workspace.collect_stock_context(
            target_codes={key},
            sources=GENERAL_STOCK_CONTEXT_SOURCE_KEYS,
        ).get(key, [])
        update_signals = getattr(target_dialog, "update_signals", None)
        if callable(update_signals):
            update_signals(updated)

    def _cleanup_detail(_obj=None, key=code_text, target_dialog=dialog) -> None:
        _disconnect_stock_detail_refresh(_refresh_detail)
        if refresh_slots.get(key) is _refresh_detail:
            refresh_slots.pop(key, None)
        if detail_dialogs.get(key) is target_dialog:
            detail_dialogs.pop(key, None)

    refresh_slots[code_text] = _refresh_detail
    domain_events.sig_stock_context_snapshot_updated.connect(_refresh_detail)
    dialog.destroyed.connect(_cleanup_detail)


def _show_stock_detail_dialog(dialog) -> None:
    dialog.show()
    try:
        dialog.raise_()
        dialog.activateWindow()
    except RuntimeError:
        pass


def _clear_workspace_pending_state(workspace) -> None:
    for attr in ("_background_prewarm_queue", "_lazy_loading_keys", "_pending_tab_activation_reasons"):
        value = getattr(workspace, attr, None)
        if value is not None:
            value.clear()
    _clear_runtime_pending_tab_loads(workspace)
    prewarm_timer = getattr(workspace, "_background_prewarm_timer", None)
    if prewarm_timer is not None:
        prewarm_timer.stop()
    coordinator = getattr(workspace, "_background_preload_coordinator", None)
    if coordinator is not None:
        cancel_background_tab_preload(coordinator)
    workspace._background_prewarm_active_key = ""
    workspace._background_prewarm_active_widget = None
    workspace._background_prewarm_active_started_at = 0.0
    workspace._background_prewarm_enabled = False
    workspace._background_prewarm_started = False
    workspace._background_prewarm_finished_at = 0.0
    workspace._copy_hook_refresh_queued = False
    restore_timer = getattr(workspace, "_restore_last_tab_timer", None)
    if restore_timer is None:
        return
    restore_timer.stop()
    restore_timer.deleteLater()
    workspace._restore_last_tab_timer = None


def _shutdown_stock_detail_dialogs(workspace) -> None:
    refresh_slots = getattr(workspace, "_stock_detail_refresh_slots", {})
    for slot in tuple(refresh_slots.values()):
        _disconnect_stock_detail_refresh(slot)
    refresh_slots.clear()
    detail_dialogs = getattr(workspace, "_stock_detail_dialogs", {})
    for dialog in tuple(detail_dialogs.values()):
        close = getattr(dialog, "close", None)
        if not callable(close):
            continue
        try:
            close()
        except RuntimeError:
            pass
    detail_dialogs.clear()


def _shutdown_loaded_workspace_tabs(workspace) -> None:
    for tab in workspace.iter_tabs():
        shutdown = getattr(tab, "shutdown", None)
        if not callable(shutdown):
            continue
        try:
            shutdown()
        except (AttributeError, OSError, RuntimeError, TypeError, ValueError) as exc:
            log.warning(f"[Workspace] {tab.__class__.__name__} shutdown failed: {exc}")


def _configure_loaded_tab_widget(workspace, widget, key: str, load_reason: str) -> None:
    setattr(widget, "workspace_key", key)
    setattr(widget, "_workspace_load_reason", load_reason)
    setattr(
        widget,
        "_workspace_noninteractive_loaded",
        load_reason not in workspace.INTERACTIVE_LOAD_REASONS,
    )


def _prepare_workspace_preload_repaint_guard(workspace, widget, load_reason: str) -> None:
    load_reason_text = normalize_tab_load_reason(load_reason)
    if load_reason_text not in {
        TabLoadReason.BACKGROUND_PREWARM.value,
        TabLoadReason.RESTORE_LAST_TAB.value,
    }:
        return
    current_widget = workspace.tabs.currentWidget()
    seen: set[int] = set()
    for candidate in (current_widget, widget):
        if candidate is None or id(candidate) in seen:
            continue
        seen.add(id(candidate))
        prepare_guard = getattr(candidate, "prepare_workspace_preload_repaint_guard", None)
        if not callable(prepare_guard):
            continue
        try:
            prepare_guard(load_reason=load_reason_text)
        except (AttributeError, RuntimeError, TypeError, ValueError) as exc:
            log.debug("skip workspace preload repaint guard: %s", exc)


def _replace_workspace_placeholder(
    workspace,
    spec: dict,
    key: str,
    index: int,
    widget,
    *,
    load_reason: str = "",
) -> None:
    _prepare_workspace_preload_repaint_guard(workspace, widget, load_reason)
    current_index = workspace.tabs.currentIndex()
    previous_blocked = workspace.tabs.blockSignals(True)
    old_widget = spec.get("page_widget") or spec.get("widget")
    try:
        icon_tokens = workspace._workspace_icon_tokens
        workspace.tabs.insertTab(
            index,
            widget,
            tab_svg_icon(
                key=str(spec.get("icon_key") or key),
                label=spec.get("title", ""),
                color=icon_tokens["muted"],
                size=icon_tokens["chrome_size"],
                stroke_width=icon_tokens["stroke_width"],
            ),
            spec.get("title", ""),
        )
        if old_widget is not None and workspace.tabs.currentWidget() is old_widget:
            workspace.tabs.setCurrentIndex(index)
        workspace.tabs.removeTab(index + 1)
        if old_widget is not workspace.tabs.currentWidget() and 0 <= current_index < workspace.tabs.count():
            workspace.tabs.setCurrentIndex(index if current_index == index else current_index)
    finally:
        workspace.tabs.blockSignals(previous_blocked)

    spec["page_widget"] = widget
    spec["mounted"] = True
    if old_widget is not None and old_widget is not widget:
        old_widget.deleteLater()


def _register_loaded_workspace_tab(
    workspace,
    spec: dict,
    key: str,
    index: int,
    widget,
    load_reason: str,
    *,
    mounted: bool = True,
) -> None:
    spec["widget"] = widget
    spec["loaded"] = True
    spec["mounted"] = bool(mounted)
    workspace._tabs_by_key[key] = widget
    setattr(workspace, spec["attr"], widget)
    workspace._mark_system_log_shell_nav(key, load_reason)
    workspace._lazy_loading_keys.discard(key)
    QTimer.singleShot(250, lambda widget=widget: setattr(widget, "_workspace_load_reason", ""))
    if mounted:
        workspace._schedule_workspace_table_copy_hooks()
    if load_reason == TabLoadReason.RESTORE_LAST_TAB.value:
        started_at = float(getattr(getattr(workspace, "host", None), "_launch_started_at", 0.0) or 0.0)
        if started_at > 0:
            workspace._initial_tab_ready_elapsed_ms = (time.perf_counter() - started_at) * 1000.0
    if workspace.tabs.currentWidget() is widget:
        workspace._startup_last_allowed_index = index
        coordinator = getattr(workspace, "_background_preload_coordinator", None)
        defer_activation = getattr(coordinator, "defers_interactive_activation", None)
        activation_deferred = bool(callable(defer_activation) and defer_activation(key))
        if not activation_deferred:
            workspace._notify_tab_activated(key, widget)
        if load_reason in INTERACTIVE_TAB_LOAD_REASONS:
            callback = getattr(workspace, "_on_initial_real_tab_activated", None)
            if callable(callback):
                callback()


def _should_stage_background_tab_mount(workspace, key: str, load_reason: str) -> bool:
    return bool(
        load_reason == TabLoadReason.BACKGROUND_PREWARM.value
        and key == "watchlist"
        and key == str(getattr(workspace, "_background_prewarm_active_key", "") or "")
    )


def _ensure_background_preload_staging_host(workspace):
    host = getattr(workspace, "_background_preload_staging_host", None)
    if host is None:
        host = QWidget(workspace, Qt.WindowType.Tool)
        host.setObjectName("backgroundPreloadStagingHost")
        host.hide()
        workspace._background_preload_staging_host = host
    runtime_host = workspace.host or workspace.window()
    host._workspace = workspace
    for name in (
        "central_quotes_svc",
        "na_daily_service",
        "asian_market_service",
        "earnings_refresh_service",
        "_is_closing",
    ):
        if runtime_host is not None and hasattr(runtime_host, name):
            setattr(host, name, getattr(runtime_host, name))
    return host


def _stage_loaded_workspace_tab(workspace, widget) -> None:
    host = _ensure_background_preload_staging_host(workspace)
    widget.hide()
    widget.setParent(host)


def _install_staged_tab_copy_hooks(workspace, widget) -> bool:
    iter_tables = getattr(widget, "iter_tables", None)
    if not callable(iter_tables):
        return True
    try:
        tables = list(iter_tables() or [])
    except (AttributeError, RuntimeError, TypeError, ValueError):
        return False
    if not tables:
        return True
    runtime_host = workspace.host or workspace.window()
    install_hooks = getattr(runtime_host, "install_workspace_table_copy_hooks", None)
    if not callable(install_hooks):
        return False
    try:
        install_hooks(tables=tables)
    except (AttributeError, RuntimeError, TypeError, ValueError) as exc:
        log.warning(f"[Workspace] staged table copy hook install failed: {exc}")
        return False
    return True


def _mount_loaded_workspace_tab(workspace, spec: dict, key: str, index: int):
    widget = spec.get("widget")
    if widget is None or spec.get("mounted", True) is not False:
        return widget
    copy_hooks_ready = _install_staged_tab_copy_hooks(workspace, widget)
    prepare_reveal = getattr(widget, "prepare_workspace_preload_reveal", None)
    if callable(prepare_reveal):
        prepare_reveal()
    _replace_workspace_placeholder(workspace, spec, key, index, widget)
    if not copy_hooks_ready:
        workspace._schedule_workspace_table_copy_hooks(delay_ms=0)
    setattr(widget, "_workspace_preload_staged", False)
    return widget


def _skip_if_workspace_stopping(default=None):
    def _decorate(method):
        @wraps(method)
        def _guarded(workspace, *args, **kwargs):
            if _workspace_is_stopping(workspace):
                return default
            return method(workspace, *args, **kwargs)

        return _guarded

    return _decorate


class _ClassicWorkspaceLifecycleMixin:
    def closeEvent(self, event):
        self.shutdown()
        super().closeEvent(event)

    def deleteLater(self):
        self.shutdown()
        super().deleteLater()


class ClassicWorkspace(_ClassicWorkspaceLifecycleMixin, QWidget):
    mode = "classic"
    capture_stock_context_snapshot = _capture_workspace_stock_context
    publish_stock_context_signal_index = _publish_workspace_stock_context_index
    get_published_stock_context_signals = _published_workspace_stock_context_signals
    _shutting_down = False
    BACKGROUND_PREWARM_DELAY_MS = 350
    BACKGROUND_PREWARM_INTERVAL_MS = 260
    BACKGROUND_PREWARM_POLL_INTERVAL_MS = 80
    BACKGROUND_PREWARM_STEP_TIMEOUT_MS = 190_000
    BACKGROUND_PREWARM_CANCEL_SETTLEMENT_TIMEOUT_MS = 5_000
    BACKGROUND_PREWARM_CANCEL_BLOCKED_POLL_INTERVAL_MS = 500
    STARTUP_TAB_LOAD_ORDER = startup_tab_keys()
    # QWidget construction remains on the GUI thread.  Each real tab then
    # hydrates only its local/cache startup data while the coordinator keeps a
    # single active preload step.
    BACKGROUND_PREWARM_KEYS = widget_prewarm_tab_keys()
    RESTORE_LAST_TAB_DELAY_MS = 400
    COPY_HOOK_REFRESH_DELAY_MS = 240
    STARTUP_TRANSITION_SUSPEND_MS = 60_000
    STARTUP_RAW_TAB_SWITCH_GUARD_MS = 60_000
    FIRST_VISIBLE_TAB_WORK_DELAY_MS = 1800
    LHB_FIRST_VISIBLE_POOL_DELAY_MS = 5000
    LHB_SHELL_NAV_POOL_DELAY_MS = 12000
    SHELL_NAV_HEAVY_TAB_WORK_DELAY_MS = 12000
    SHELL_GROUP_REBUILD_LOAD_DELAY_MS = 120
    SHELL_GROUP_REBUILD_ACTIVATION_DELAY_MS = 250
    WATCHLIST_TAB_SWITCH_INDICATOR_DELAY_MS = FIRST_VISIBLE_TAB_WORK_DELAY_MS
    SNAPSHOT_TRANSITION_SKIP_PAIRS = frozenset(
        {("lhb", "asian_market")}
        | {
            ("watchlist", definition.key)
            for definition in TAB_DEFINITIONS
            if definition.key != "watchlist"
        }
        | {
            (definition.key, "watchlist")
            for definition in TAB_DEFINITIONS
            if definition.key != "watchlist"
        }
    )
    INTERACTIVE_LOAD_REASONS = INTERACTIVE_TAB_LOAD_REASONS
    PROBE_LOAD_REASONS = PROBE_TAB_LOAD_REASONS
    CONTROLLED_STARTUP_PROBE_DEFER_KEYS = frozenset(
        definition.key for definition in TAB_DEFINITIONS if definition.key != "system_log"
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
        _initialize_workspace_runtime_state(
            self,
            controlled_startup_probe_guard=controlled_startup_probe_guard,
        )
        # Keep preload scheduling outside the already broad workspace widget.
        self._background_preload_coordinator = BackgroundTabPreloadCoordinator(self, enabled=background_prewarm)
        watchlist_kwargs = {} if watchlist_startup_tasks else {"startup_tasks_enabled": False}

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self.tabs = SmoothTabWidget(self)
        self.tabs.setDocumentMode(True)
        self.tabs.setTransitionEnabled(True)
        self.tabs.setSnapshotTransitionSkipPairs(self.SNAPSHOT_TRANSITION_SKIP_PAIRS)
        self.tabs.suspendTransitionsFor(self.STARTUP_TRANSITION_SUSPEND_MS)
        layout.addWidget(self.tabs, 1)

        self._tab_specs = self._build_tab_specs(watchlist_kwargs)
        self._mount_initial_tabs()
        self._workspace_facade = WorkspaceFacade(self)
        self.tabs.currentChanged.connect(self._on_current_tab_changed)
        self._connect_workspace_events()

    def _build_tab_specs(self, watchlist_kwargs: dict) -> list[dict]:
        specs: list[dict] = []
        for definition in sorted(TAB_DEFINITIONS, key=lambda item: item.stack_order):
            spec = definition.runtime_spec_metadata()
            spec.update(
                {
                    "factory": _tab_factory_for_definition(self, definition, watchlist_kwargs),
                    "widget": None,
                    "page_widget": None,
                    "loaded": False,
                    "mounted": False,
                }
            )
            specs.append(spec)
        return specs

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
            spec["page_widget"] = widget
            spec["loaded"] = False
            spec["mounted"] = False
            setattr(self, spec["attr"], None)
            self.tabs.addTab(
                widget,
                tab_svg_icon(
                    key=str(spec.get("icon_key") or key),
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
        key = str(spec.get("key") or "").strip()
        definition = get_tab_definition(key)
        if definition is None:
            raise KeyError(f"unknown tab registry key: {key}")
        reason_text = normalize_tab_load_reason(reason)
        first_visible_load = reason_text in self.INTERACTIVE_LOAD_REASONS
        runtime_kwargs = _runtime_kwargs_for_definition(definition, reason_text, first_visible_load, self)
        if _should_stage_background_tab_mount(self, key, reason_text):
            runtime_kwargs["_workspace_parent_override"] = _ensure_background_preload_staging_host(self)
        return factory(**runtime_kwargs)

    def _create_placeholder_tab(self, spec: dict) -> LazyTabPlaceholder:
        key = str(spec.get("key") or "").strip()
        title = str(spec.get("title") or key or "").strip()
        return LazyTabPlaceholder(
            title,
            lambda key=key: self.activate_tab(
                self._tab_index_for_key(key),
                reason=TabLoadReason.PLACEHOLDER_ACTION.value,
            ),
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

    def _mount_preloaded_tab(self, key: str):
        spec = self._spec_for_key_or_index(key)
        if spec is None or not spec.get("loaded"):
            return None
        index = self._tab_index_for_key(key)
        if index < 0:
            return spec.get("widget")
        return _mount_loaded_workspace_tab(self, spec, str(key or "").strip(), index)

    attach_runtime_services = _attach_workspace_runtime_services

    def should_defer_probe_tab_load(self, key: str, *, reason: str = "perf_memory_probe") -> bool:
        key_text = str(key or "").strip()
        reason_text = normalize_tab_load_reason(reason)
        return (
            bool(self._controlled_startup_probe_guard)
            and reason_text in self.PROBE_LOAD_REASONS
            and key_text in self.CONTROLLED_STARTUP_PROBE_DEFER_KEYS
        )

    @_skip_if_workspace_stopping()
    def ensure_tab_loaded(self, key_or_index, reason: str = "user"):
        spec = self._spec_for_key_or_index(key_or_index)
        if spec is None:
            return None

        key = str(spec.get("key") or "").strip()
        if _defer_tab_until_runtime_ready(self, spec, key, normalize_tab_load_reason(reason), spec.get("widget")):
            return None
        if spec.get("loaded") and self._defer_interactive_activation_until_preload_ready(key, reason):
            return spec.get("widget")
        with ui_stall_span("ClassicWorkspace.ensure_tab_loaded", tab=key, signal=reason):
            return self._ensure_tab_loaded_impl(spec, key, reason)

    def _defer_interactive_activation_until_preload_ready(self, key: str, reason: object) -> bool:
        if not is_interactive_tab_load_reason(reason):
            return False
        coordinator = getattr(self, "_background_preload_coordinator", None)
        defers_activation = getattr(coordinator, "defers_interactive_activation", None)
        if not callable(defers_activation):
            return False
        if not defers_activation(key):
            active_key = str(getattr(self, "_background_prewarm_active_key", "") or "")
            prioritize = getattr(coordinator, "prioritize", None)
            if key == active_key and callable(prioritize):
                prioritize(key, reason)
        if not defers_activation(key):
            return False
        prioritize = getattr(coordinator, "prioritize", None)
        if callable(prioritize):
            prioritize(key, reason)
        return True

    def _mark_system_log_shell_nav(self, key: str, reason: str) -> None:
        if normalize_tab_load_reason(reason) != TabLoadReason.SHELL_NAV.value:
            return
        loaded_at = time.perf_counter()
        self._last_shell_nav_load_at = loaded_at
        if key == "system_log":
            self._last_system_log_shell_nav_load_at = loaded_at

    def _ensure_tab_loaded_impl(self, spec: dict, key: str, reason: str = "user"):
        load_reason = normalize_tab_load_reason(reason)
        if spec.get("loaded"):
            self._mark_system_log_shell_nav(key, load_reason)
            widget = spec.get("widget")
            if spec.get("mounted", True) is False and load_reason in self.INTERACTIVE_LOAD_REASONS:
                index = self._tab_index_for_key(key)
                if index >= 0:
                    widget = _mount_loaded_workspace_tab(self, spec, key, index)
            ClassicWorkspace._promote_loaded_tab_to_interactive(widget, load_reason)
            return widget

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

        _configure_loaded_tab_widget(self, widget, key, load_reason)
        if _should_stage_background_tab_mount(self, key, load_reason):
            _stage_loaded_workspace_tab(self, widget)
            setattr(widget, "_workspace_preload_staged", True)
            _register_loaded_workspace_tab(
                self,
                spec,
                key,
                index,
                widget,
                load_reason,
                mounted=False,
            )
        else:
            _replace_workspace_placeholder(
                self,
                spec,
                key,
                index,
                widget,
                load_reason=load_reason,
            )
            _register_loaded_workspace_tab(self, spec, key, index, widget, load_reason)
        return widget

    @_skip_if_workspace_stopping()
    def _on_current_tab_changed(self, index: int) -> None:
        spec = self._spec_for_key_or_index(index)
        key = str((spec or {}).get("key") or "").strip()
        with ui_stall_span("ClassicWorkspace._on_current_tab_changed", tab=key, signal="currentChanged"):
            if spec is None:
                return
            reason = self._take_tab_activation_reason(index)
            _cancel_pending_startup_restore_for_user(self, reason)
            _pause_interactive_handoff_for_visible_watchlist(self, key, reason)
            self._mark_system_log_shell_nav(key, reason)
            if spec.get("loaded"):
                widget = spec.get("widget")
                if widget is not None:
                    if self._defer_interactive_activation_until_preload_ready(key, reason):
                        return
                    widget = _mount_loaded_workspace_tab(self, spec, key, index)
                    ClassicWorkspace._promote_loaded_tab_to_interactive(widget, reason)
                    self._startup_last_allowed_index = index
                    self._notify_tab_activated(key, widget)
                return
            if not key:
                return
            if self._should_suppress_startup_tab_switch(key, reason):
                self._restore_startup_allowed_tab_after_suppressed_switch(key)
                return
            self._request_interactive_tab_load(
                spec,
                key,
                reason=reason or TabLoadReason.TAB_SWITCH.value,
                index=index,
            )

    def _take_tab_activation_reason(self, index: int) -> str:
        return self._pending_tab_activation_reasons.pop(int(index), TabLoadReason.TAB_SWITCH.value)

    @staticmethod
    def _promote_loaded_tab_to_interactive(widget, reason: object) -> bool:
        reason_text = normalize_tab_load_reason(reason)
        if widget is None or reason_text not in INTERACTIVE_TAB_LOAD_REASONS:
            return False
        try:
            setattr(widget, "_workspace_load_reason", reason_text)
            setattr(widget, "_workspace_noninteractive_loaded", False)
        except (AttributeError, RuntimeError, TypeError, ValueError):
            return False
        return True

    def _is_startup_raw_tab_switch_guard_active(self) -> bool:
        return (time.perf_counter() - self._startup_guard_started_at) * 1000.0 < self.STARTUP_RAW_TAB_SWITCH_GUARD_MS

    def _should_suppress_startup_tab_switch(self, key: str, reason: str) -> bool:
        return (
            bool(key)
            and normalize_tab_load_reason(reason) == TabLoadReason.TAB_SWITCH.value
            and self._is_startup_raw_tab_switch_guard_active()
        )

    @_skip_if_workspace_stopping(False)
    def _request_interactive_tab_load(
        self,
        spec: dict,
        key: str,
        *,
        reason: str,
        index: int | None = None,
    ) -> bool:
        coordinator = getattr(self, "_background_preload_coordinator", None)
        prioritize = getattr(coordinator, "prioritize", None)
        if callable(prioritize) and prioritize(key, reason):
            if isinstance(index, int) and 0 <= index < self.tabs.count():
                self._startup_last_allowed_index = index
            placeholder = spec.get("widget")
            if isinstance(placeholder, LazyTabPlaceholder):
                placeholder.set_loading()
            return True
        return self._queue_lazy_tab_load(spec, key, reason=reason, index=index)

    @_skip_if_workspace_stopping(False)
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
            lambda key=key, reason=reason: _load_queued_tab(self, key, reason),
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
        if normalize_tab_load_reason(reason) == TabLoadReason.SHELL_NAV.value and self._is_shell_group_rebuild_quiet_window():
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
            if _workspace_is_stopping(self):
                return
            if not (0 <= restore_index < self.tabs.count()):
                return
            if self.tabs.currentIndex() == restore_index:
                return
            self._pending_tab_activation_reasons[restore_index] = TabLoadReason.STARTUP_GUARD_RESTORE.value
            self.tabs.setCurrentIndex(restore_index)

        QTimer.singleShot(0, _restore)

    @_skip_if_workspace_stopping(False)
    def activate_tab(self, index: int, *, reason: str = "user") -> bool:
        try:
            target_index = int(index)
        except (TypeError, ValueError):
            return False
        if not (0 <= target_index < self.tabs.count()):
            return False

        reason_text = normalize_tab_load_reason(reason) or TabLoadReason.USER.value
        _cancel_pending_startup_restore_for_user(self, reason_text)
        spec = self._spec_for_key_or_index(target_index)
        key = str((spec or {}).get("key") or "").strip()
        if self.tabs.currentIndex() == target_index:
            self._pending_tab_activation_reasons.pop(target_index, None)
            if spec is None or not key:
                return True
            _pause_interactive_handoff_for_visible_watchlist(self, key, reason_text)
            if spec.get("loaded"):
                self._mark_system_log_shell_nav(key, reason_text)
                widget = spec.get("widget")
                if widget is not None:
                    if self._defer_interactive_activation_until_preload_ready(key, reason_text):
                        return True
                    widget = _mount_loaded_workspace_tab(self, spec, key, target_index)
                    ClassicWorkspace._promote_loaded_tab_to_interactive(widget, reason_text)
                    self._startup_last_allowed_index = target_index
                    self._notify_tab_activated(key, widget)
                return True
            self._request_interactive_tab_load(spec, key, reason=reason_text, index=target_index)
            return True

        if spec is not None and key and spec.get("loaded") and spec.get("mounted", True) is False:
            if not self._defer_interactive_activation_until_preload_ready(key, reason_text):
                _mount_loaded_workspace_tab(self, spec, key, target_index)
        if (
            key in {"watchlist", "lhb"}
            and reason_text == TabLoadReason.SHELL_NAV.value
            and spec is not None
            and spec.get("loaded")
        ):
            prepare_guard = getattr(spec.get("widget"), "prepare_shell_nav_repaint_guard", None)
            if callable(prepare_guard):
                try:
                    prepare_guard()
                except (AttributeError, RuntimeError, TypeError, ValueError) as exc:
                    log.debug("skip %s shell-nav repaint guard: %s", key, exc)
        self._pending_tab_activation_reasons[target_index] = reason_text
        self.tabs.setCurrentIndex(target_index)
        return True

    def _connect_workspace_events(self) -> None:
        try:
            from app.services.ui_event_service import domain_events as event_bus

            event_bus.sig_ai_industry_chain_updated.connect(self._on_ai_industry_chain_source_updated)
            event_bus.sig_fund_holdings_updated.connect(self._on_fund_holdings_source_updated)
            event_bus.sig_cache_bootstrap_ready.connect(self._on_startup_cache_bootstrap_ready)
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
        try:
            event_bus.sig_cache_bootstrap_ready.disconnect(self._on_startup_cache_bootstrap_ready)
        except (AttributeError, RuntimeError, TypeError, ValueError):
            pass
        self._workspace_events_connected = False

    @_skip_if_workspace_stopping()
    def _on_ai_industry_chain_source_updated(self, *_args) -> None:
        _resolve_workspace_facade(self).refresh_tabs_after_ai_industry_chain_update()

    @_skip_if_workspace_stopping()
    def _on_fund_holdings_source_updated(self, *_args) -> None:
        self.prime_stock_context_snapshots(force=True, include_lhb=False)

    _on_startup_cache_bootstrap_ready = _handle_startup_cache_bootstrap_ready

    def _on_initial_real_tab_activated(self) -> None:
        self._initial_real_tab_activated = True
        self._start_background_tab_prewarm()

    def _start_background_tab_prewarm(self) -> None:
        if getattr(self, "_shutting_down", False):
            return
        if getattr(self, "_startup_cache_bootstrap_required", False) and not getattr(
            self,
            "_startup_cache_bootstrap_ready",
            False,
        ):
            return
        coordinator = getattr(self, "_background_preload_coordinator", None)
        if coordinator is not None:
            coordinator.start()

    def _prewarm_next_tab(self) -> None:
        coordinator = getattr(self, "_background_preload_coordinator", None)
        if coordinator is not None:
            coordinator.advance()

    def _prime_tab_runtime(self, widget) -> bool:
        for method_name in (
            "prime_background_load",
            "prime_startup_state",
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
                return False
            return True
        return True

    def background_preload_status(self) -> dict:
        status = self._background_preload_coordinator.status()
        status["startup_cache_bootstrap_required"] = self._startup_cache_bootstrap_required
        status["startup_cache_bootstrap_ready"] = self._startup_cache_bootstrap_ready
        return status

    @_skip_if_workspace_stopping()
    def _schedule_workspace_table_copy_hooks(self, *, delay_ms: int | None = None) -> None:
        host = self.host or self.window()
        install_hooks = getattr(host, "install_workspace_table_copy_hooks", None)
        if not callable(install_hooks) or self._copy_hook_refresh_queued:
            return
        self._copy_hook_refresh_queued = True

        def _install_hooks() -> None:
            self._copy_hook_refresh_queued = False
            if _workspace_is_stopping(self):
                return
            try:
                install_hooks()
            except (AttributeError, RuntimeError, TypeError, ValueError) as exc:
                log.warning(f"[Workspace] table copy hook install failed: {exc}")

        delay = self.COPY_HOOK_REFRESH_DELAY_MS if delay_ms is None else max(0, int(delay_ms))
        if delay == 0:
            _install_hooks()
            return
        QTimer.singleShot(delay, _install_hooks)

    @_skip_if_workspace_stopping()
    def _notify_tab_activated(self, _key: str, widget) -> None:
        callback = getattr(widget, "on_workspace_tab_activated", None)
        if not callable(callback):
            callback = getattr(widget, "_ensure_runtime_started", None)
        if callable(callback):
            def _run_if_current(widget=widget, callback=callback) -> None:
                if _workspace_is_stopping(self):
                    return
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

    def tab_indices_by_group(self) -> dict[str, list[int]]:
        return _resolve_workspace_facade(self).tab_indices_by_group()

    @_skip_if_workspace_stopping()
    def restore_last_tab(self, key_or_index: str | int):
        spec = self._spec_for_key_or_index(key_or_index)
        if spec is None:
            return
        key = str(spec.get("key") or "").strip()
        index = self._tab_index_for_key(key)
        if 0 <= index < self.tabs.count():
            self.activate_tab(index, reason=TabLoadReason.RESTORE_LAST_TAB.value)

    @_skip_if_workspace_stopping()
    def schedule_restore_last_tab(self, key_or_index: str | int, *, delay_ms: int | None = None) -> None:
        if isinstance(key_or_index, int):
            if key_or_index < 0:
                return
        elif isinstance(key_or_index, str):
            key_or_index = key_or_index.strip()
            if not key_or_index:
                return
        else:
            return
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
            if self._restore_last_tab_timer is not timer:
                timer.deleteLater()
                return
            if _workspace_is_stopping(self):
                self._restore_last_tab_timer = None
                timer.deleteLater()
                return
            self._restore_last_tab_timer = None
            self.restore_last_tab(key_or_index)
            timer.deleteLater()

        timer.timeout.connect(_restore)
        timer.start(delay)

    def current_tab_index(self) -> int:
        return self.tabs.currentIndex()

    def current_tab_key(self) -> str:
        spec = self._spec_for_key_or_index(self.tabs.currentIndex())
        return str((spec or {}).get("key") or "").strip()

    def get_tab(self, key: str):
        return self.ensure_tab_loaded(key)

    def iter_tabs(self) -> list:
        return list(self._tabs_by_key.values())

    def get_realtime_quote_codes(self) -> set[str]:
        return _resolve_workspace_facade(self).get_realtime_quote_codes()

    def get_scan_results(self) -> list[dict]:
        return _resolve_workspace_facade(self).get_scan_results()

    def iter_tables(self) -> list:
        return _resolve_workspace_facade(self).iter_tables()

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

    def run_post_online_refresh(self, task_manager) -> None:
        _resolve_workspace_facade(self).run_post_online_refresh(task_manager)

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

    def collect_stock_context(
        self,
        *,
        include_cache_fallback: bool = True,
        include_source_cache_fallback: bool | None = None,
        allow_lhb_cache_compute: bool = False,
        allow_async_snapshot_refresh: bool = True,
        capture_snapshot: bool = False,
        include_rps_bundle: bool = True,
        target_codes=None,
        sources=None,
    ) -> dict[str, list[StockSignal]] | StockContextSnapshot:
        options = dict(
            include_cache_fallback=include_cache_fallback,
            include_source_cache_fallback=include_source_cache_fallback,
            allow_lhb_cache_compute=allow_lhb_cache_compute,
            allow_async_snapshot_refresh=allow_async_snapshot_refresh,
            target_codes=target_codes,
            sources=sources,
        )
        if capture_snapshot:
            options["capture_snapshot"] = True
            if not include_rps_bundle:
                options["include_rps_bundle"] = False
        return _resolve_workspace_facade(self).collect_stock_context(**options)

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

    stock_context_snapshots_settled = _workspace_stock_context_snapshots_settled
    cancel_stock_context_snapshots = _cancel_workspace_stock_context_snapshots

    def open_security_detail(self, code: str, context=None):
        code_text = str(code or "").strip()
        if not code_text:
            return False

        name, tab_titles, detail_context = _security_detail_inputs(self, code_text, context)
        signals = self.collect_stock_context(
            target_codes={code_text},
            sources=GENERAL_STOCK_CONTEXT_SOURCE_KEYS,
        ).get(code_text, [])

        from ui.components.stock_detail_dialog import StockDetailDialog

        detail_dialogs = getattr(self, "_stock_detail_dialogs", None)
        if detail_dialogs is None:
            detail_dialogs = {}
            setattr(self, "_stock_detail_dialogs", detail_dialogs)

        existing_dialog = detail_dialogs.get(code_text)
        if _activate_existing_stock_detail(existing_dialog):
            return True
        if existing_dialog is not None and detail_dialogs.get(code_text) is existing_dialog:
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
        _attach_stock_detail_refresh(self, code_text, dialog, detail_dialogs)
        _show_stock_detail_dialog(dialog)
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
                activate_tab(source_index, reason=TabLoadReason.STOCK_SIGNAL_SOURCE.value)
            else:
                self.tabs.setCurrentIndex(source_index)
            tab = self.get_tab(signal.source_tab)
            select_code_row = getattr(tab, "select_code_row", None)
            if callable(select_code_row):
                return bool(select_code_row(code_text))

        return self.select_code_row(code_text, preferred_tab_index=source_index if source_index >= 0 else None)

    def shutdown(self):
        if getattr(self, "_shutting_down", False):
            return
        self._shutting_down = True
        staging_host = getattr(self, "_background_preload_staging_host", None)
        if staging_host is not None:
            setattr(staging_host, "_is_closing", True)
        _clear_workspace_pending_state(self)
        disconnect_events = getattr(self, "_disconnect_workspace_events", None)
        if callable(disconnect_events):
            disconnect_events()
        _shutdown_stock_detail_dialogs(self)
        _shutdown_workspace_facade(self)
        _shutdown_loaded_workspace_tabs(self)
