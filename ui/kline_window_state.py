# -*- coding: utf-8 -*-
"""Small state initializer for one K-line window."""

from __future__ import annotations

from app.services.kline_open_context import KlineNavItem, KlineOpenContext
from ui.kline_load_controller import KlineLoadController
from ui.kline_runtime_lifecycle import KLineRuntimeLifecycleController


def _navigation_rows(context: KlineOpenContext) -> list[dict]:
    return [
        {
            "代码": item.code,
            "名称": item.name,
            "__source_tab_key": item.source_tab_key,
            "__source_tab_index": item.source_tab_index,
        }
        for item in context.navigation
    ]


def _navigation_items(rows) -> tuple[KlineNavItem, ...]:
    return tuple(
        KlineNavItem(
            code=str(row.get("代码") or row.get("code") or "").strip(),
            name=str(row.get("名称") or row.get("name") or "").strip(),
            source_tab_key=str(row.get("__source_tab_key") or "").strip(),
            source_tab_index=row.get("__source_tab_index", -1),
        )
        for row in (rows or [])
        if isinstance(row, dict)
    )


def current_kline_open_context(window) -> KlineOpenContext:
    existing = getattr(window, "_open_context", None)
    code = str(getattr(window, "code", "") or "").strip()
    if isinstance(existing, KlineOpenContext) and existing.code == code:
        return existing
    navigation = _navigation_items(getattr(window, "code_list", None))
    current_idx = int(getattr(window, "current_idx", 0) or 0)
    current = navigation[current_idx] if 0 <= current_idx < len(navigation) else None
    context = KlineOpenContext(
        code=code,
        name=str(getattr(window, "name", "") or code).strip(),
        vcp_data=dict(getattr(window, "vcp_data", None) or {}),
        navigation=navigation,
        current_idx=current_idx,
        source_tab_key=current.source_tab_key if current is not None else "",
        source_tab_index=current.source_tab_index if current is not None else -1,
    )
    window._open_context = context
    return context


def _resolve_open_values(
    *,
    code,
    name,
    vcp_data,
    code_list,
    current_idx,
    open_context: KlineOpenContext | None,
) -> tuple[str, str, dict, list, int, bool]:
    if open_context is not None:
        return (
            open_context.code,
            open_context.name,
            open_context.mutable_vcp_data(),
            _navigation_rows(open_context),
            open_context.current_idx,
            True,
        )
    normalized_code = str(code or "").strip()
    normalized_name = str(name or normalized_code).strip() or normalized_code
    return (
        normalized_code,
        normalized_name,
        dict(vcp_data or {}),
        list(code_list or []),
        int(current_idx or 0),
        False,
    )


def _initialize_render_lifecycle(window) -> None:
    window._load_controller = KlineLoadController()
    window._runtime_lifecycle = KLineRuntimeLifecycleController()
    window._active_load_identity = None
    window._active_kline_task_tickets = set()
    window._running_kline_task_submission = None
    window._pending_kline_task_submission = None
    window._render_generation = 0
    window._snapshot_version = 0
    window._pending_prepared_render = None
    window._last_prepared_render = None
    window._pending_frame = None
    window._snapshot_inflight = None
    window._snapshot_inflight_browser = None
    window._snapshot_inflight_epoch = None
    window._snapshot_render_deadline = None
    window._render_commit_timer = None
    window._render_watchdog_timer = None
    window._snapshot_render_query_pending = False
    window._fallback_snapshot_key = None
    window.df = None
    window._history_frame = None


def _initialize_browser_runtime(window) -> None:
    window.browser = None
    window._chart_shell_html = None
    window._shell_loaded = False
    window._browser_epoch = 0
    window._last_shell_load_epoch = -1
    window._last_shell_load_ok = None
    window._visibility_epoch = 0
    window._runtime_active = True
    window._latest_rt_quote = None
    window._last_rt_quote_fingerprint = None
    window._rt_prepare_inflight = False
    window._rt_prepare_owner = None
    window._pending_chart_status = None
    window._rt_timer = None


def _initialize_native_window_state(window) -> None:
    window._native_window_effects_applied = False
    window._snap_threshold = 15
    window._snapping_to_main_window = False
    window._magnetically_attached = False
    window._fullscreen_geometry = None
    window._header_resize_timer = None
    window._header_resize_pending = False


def _assign_open_values(
    window,
    *,
    main_window,
    code,
    name,
    data_provider,
    vcp_data,
    code_list,
    current_idx,
    open_context: KlineOpenContext | None,
) -> None:
    code, name, vcp_data, code_list, current_idx, context_resolved = _resolve_open_values(
        code=code,
        name=name,
        vcp_data=vcp_data,
        code_list=code_list,
        current_idx=current_idx,
        open_context=open_context,
    )
    window.main_window = main_window
    window.code = code
    window.name = name
    window.data_provider = data_provider
    window.vcp_data = vcp_data
    window.code_list = code_list
    window.current_idx = current_idx
    window._open_context = open_context
    window._open_context_resolved = context_resolved


def _preserved_lease_resources(window):
    return (
        getattr(window, "browser", None),
        getattr(window, "_rt_timer", None),
        getattr(window, "_render_commit_timer", None),
        getattr(window, "_render_watchdog_timer", None),
    )


def _reset_lease_render_state(window, *, render_commit_timer, render_watchdog_timer) -> None:
    # A pooled window's previous owner lifecycle is permanently closed during
    # closeEvent.  Let the next lease create a fresh group before submitting
    # history work; otherwise every task is rejected as ``owner_shutdown``.
    window._task_lifecycle = None
    window._runtime_lifecycle = KLineRuntimeLifecycleController()
    window._active_load_identity = None
    window._active_kline_task_tickets = set()
    window._running_kline_task_submission = None
    window._pending_kline_task_submission = None
    window._render_generation = -1
    window._snapshot_version = 0
    window._pending_prepared_render = None
    window._last_prepared_render = None
    window._pending_frame = None
    window._snapshot_inflight = None
    window._snapshot_inflight_browser = None
    window._snapshot_inflight_epoch = None
    window._snapshot_render_deadline = None
    window._render_commit_timer = render_commit_timer
    window._render_watchdog_timer = render_watchdog_timer
    window._snapshot_render_query_pending = False
    window._fallback_snapshot_key = None
    window.df = None
    window._history_frame = None


def _reset_lease_runtime_state(window) -> None:
    window._latest_rt_quote = None
    window._last_rt_quote_fingerprint = None
    window._rt_prepare_inflight = False
    window._rt_prepare_owner = None
    window._pending_chart_status = None
    window._runtime_active = True
    window._visibility_epoch = int(getattr(window, "_visibility_epoch", 0) or 0) + 1
    window._fullscreen_geometry = None
    window._magnetically_attached = False
    window._snapping_to_main_window = False
    header_resize_timer = getattr(window, "_header_resize_timer", None)
    if header_resize_timer is not None:
        header_resize_timer.stop()
    window._header_resize_pending = False


def reset_kline_window_lease_state(
    window,
    *,
    main_window,
    code,
    name,
    data_provider,
    vcp_data,
    code_list,
    current_idx,
    open_context: KlineOpenContext | None,
) -> None:
    """Reset business state while preserving the physical WebEngine hierarchy."""
    browser, realtime_timer, render_commit_timer, render_watchdog_timer = _preserved_lease_resources(window)
    if realtime_timer is not None:
        realtime_timer.stop()
    from ui.kline_window_rendering import cancel_snapshot_render_confirmation

    cancel_snapshot_render_confirmation(window)
    window._load_controller.reopen_lease()
    _assign_open_values(
        window,
        main_window=main_window,
        code=code,
        name=name,
        data_provider=data_provider,
        vcp_data=vcp_data,
        code_list=code_list,
        current_idx=current_idx,
        open_context=open_context,
    )
    _reset_lease_render_state(
        window,
        render_commit_timer=render_commit_timer,
        render_watchdog_timer=render_watchdog_timer,
    )
    _reset_lease_runtime_state(window)
    window.browser = browser


def initialize_kline_window_state(
    window,
    *,
    main_window,
    code,
    name,
    data_provider,
    vcp_data,
    code_list,
    current_idx,
    open_context: KlineOpenContext | None,
) -> None:
    """Initialize ownership/lifecycle state without growing the QWidget constructor."""
    from ui.kline_pool_state import initialize_kline_pool_state

    _assign_open_values(
        window,
        main_window=main_window,
        code=code,
        name=name,
        data_provider=data_provider,
        vcp_data=vcp_data,
        code_list=code_list,
        current_idx=current_idx,
        open_context=open_context,
    )
    window._log = None
    initialize_kline_pool_state(window)
    _initialize_render_lifecycle(window)
    _initialize_browser_runtime(window)
    _initialize_native_window_state(window)
