# -*- coding: utf-8 -*-
from __future__ import annotations

import time
from collections.abc import Mapping

from PyQt6.QtCore import QObject, QTimer, pyqtSignal

from app.services.ui_diagnostics_service import ui_stall_span
from core.logger import get_logger
from ui.workspaces.background_preload_receipt import BackgroundPreloadCancellationReceipt
from ui.workspaces.tab_registry import (
    TabConstructorProfile,
    TabLoadReason,
    is_interactive_tab_load_reason,
    normalize_tab_load_reason,
    preload_dependencies_for,
)

log = get_logger(__name__)


def _priority_runtime_ready(workspace) -> bool:
    return bool(
        not getattr(workspace, "_shutting_down", False)
        and workspace._background_prewarm_enabled
        and not workspace._background_prewarm_finished
    )


def _priority_target_pending(workspace, key: str) -> bool:
    return bool(
        key == str(workspace._background_prewarm_active_key or "")
        or key in workspace._background_prewarm_queue
    )


def _normalized_priority_request(workspace, key: object, reason: object) -> tuple[str, str] | None:
    key_text = str(key or "").strip()
    reason_text = normalize_tab_load_reason(reason)
    if not key_text or not is_interactive_tab_load_reason(reason_text):
        return None
    if not _priority_runtime_ready(workspace):
        return None
    if workspace._background_prewarm_started:
        target_available = _priority_target_pending(workspace, key_text)
    else:
        target_available = bool(
            key_text in workspace.BACKGROUND_PREWARM_KEYS
            and workspace._spec_for_key_or_index(key_text) is not None
        )
    if not target_available:
        return None
    return key_text, reason_text


def _resume_startup_lazy_handoff(coordinator, key: object, reason: object) -> tuple[str, str] | None:
    workspace = coordinator.workspace
    key_text = str(key or "").strip()
    reason_text = normalize_tab_load_reason(reason)
    handoff_keys = list(coordinator._startup_lazy_handoff_keys)
    if (
        not key_text
        or not is_interactive_tab_load_reason(reason_text)
        or key_text not in handoff_keys
        or getattr(workspace, "_shutting_down", False)
        or not workspace._background_prewarm_enabled
        or not workspace._background_prewarm_finished
    ):
        return None

    workspace._background_prewarm_queue = [
        candidate for candidate in handoff_keys if not _preload_key_ready(workspace, candidate)
    ]
    if key_text not in workspace._background_prewarm_queue:
        return None
    workspace._background_prewarm_finished = False
    workspace._background_prewarm_finished_at = 0.0
    coordinator._completion_scope = "interactive_resume"
    coordinator._startup_lazy_handoff_keys.clear()
    coordinator._interactive_handoff_targets.add(key_text)
    return key_text, reason_text


def _preload_key_ready(workspace, key: str) -> bool:
    spec = workspace._spec_for_key_or_index(key)
    if spec is None or not spec.get("loaded"):
        return False
    widget = spec.get("widget")
    return bool(widget is not None and getattr(widget, "_workspace_background_preload_ready", False))


def _unready_startup_keys(workspace, *, exclude: set[str] | None = None) -> list[str]:
    excluded = exclude or set()
    return [
        key
        for key in workspace.STARTUP_TAB_LOAD_ORDER
        if key not in excluded and not _preload_key_ready(workspace, key)
    ]


def _ordered_unready_dependency_closure(workspace, key: str) -> tuple[str, ...]:
    closure: set[str] = set()

    def _visit(candidate: str) -> None:
        if candidate in closure or _preload_key_ready(workspace, candidate):
            return
        closure.add(candidate)
        for dependency in preload_dependencies_for(candidate):
            _visit(dependency)

    _visit(key)
    active_key = str(workspace._background_prewarm_active_key or "")
    pending_keys = set(workspace._background_prewarm_queue)
    if active_key:
        pending_keys.add(active_key)
    return tuple(
        candidate
        for candidate in workspace.STARTUP_TAB_LOAD_ORDER
        if candidate in closure and candidate in pending_keys
    )


def _move_priority_closure_to_front(workspace, closure: tuple[str, ...]) -> None:
    queued_closure = [key for key in closure if key in workspace._background_prewarm_queue]
    if not queued_closure:
        return
    prioritized = set(queued_closure)
    workspace._background_prewarm_queue = queued_closure + [
        queued_key
        for queued_key in workspace._background_prewarm_queue
        if queued_key not in prioritized
    ]


def _configured_preload_queue(workspace) -> list[str]:
    allowed_keys = set(workspace.BACKGROUND_PREWARM_KEYS)
    available_keys: set[str] = set()
    for spec in workspace._tab_specs:
        key = str(spec.get("key") or "").strip()
        if key in allowed_keys:
            available_keys.add(key)
    return [key for key in workspace.STARTUP_TAB_LOAD_ORDER if key in available_keys]


def _apply_prestart_priority_closures(coordinator) -> None:
    workspace = coordinator.workspace
    for key in coordinator._prestart_priority_order:
        closure = _ordered_unready_dependency_closure(workspace, key)
        coordinator._priority_closures[key] = closure
        coordinator._priority_boosted_keys.update(closure)
        _move_priority_closure_to_front(workspace, closure)
    coordinator._prestart_priority_order.clear()


def _initial_preload_delay_ms(coordinator) -> int:
    workspace = coordinator.workspace
    if (
        workspace._background_prewarm_queue
        and workspace._background_prewarm_queue[0] in coordinator._priority_boosted_keys
    ):
        return 0
    return workspace.BACKGROUND_PREWARM_DELAY_MS


def _next_step_delay_ms(coordinator) -> int:
    queue = coordinator.workspace._background_prewarm_queue
    if queue and queue[0] in coordinator._priority_boosted_keys:
        return 0
    return coordinator.workspace.BACKGROUND_PREWARM_INTERVAL_MS


def _ordinary_step_interaction_quiet_delay_ms(coordinator) -> int:
    """Return the remaining quiet time before a non-user-requested GUI step.

    Widget construction cannot leave the GUI thread.  A normal queued preload
    must therefore yield to a just-finished tab navigation; an active user
    target and its dependency closure still bypass this delay for its required
    first frame.
    """
    if coordinator._priority_reasons:
        return 0
    workspace = coordinator.workspace
    try:
        quiet_until = float(getattr(workspace, "_background_prewarm_interaction_quiet_until", 0.0) or 0.0)
    except (TypeError, ValueError):
        return 0
    remaining_ms = (quiet_until - time.perf_counter()) * 1000.0
    return max(0, int(remaining_ms + 0.999))


def _watchlist_is_current(coordinator) -> bool:
    """Read only the current page; it remains valid even when the queue is empty."""
    workspace = coordinator.workspace
    spec_getter = getattr(workspace, "_spec_for_key_or_index", None)
    if not callable(spec_getter):
        return False
    try:
        watchlist_spec = spec_getter("watchlist") or {}
        watchlist = watchlist_spec.get("widget")
        return bool(watchlist is not None and workspace.tabs.currentWidget() is watchlist)
    except (AttributeError, RuntimeError, TypeError, ValueError):
        return False


def _foreground_watchlist_hold_reason(coordinator) -> str:
    """Return a foreground-priority hold reason without touching the live layout.

    The Watchlist opts in because it is the primary working surface.  Once its
    own cache-only preload has completed, constructing another hidden QWidget
    can still cause native MainWindow activation/layout invalidations on
    Windows.  Those invalidations are unrelated to Watchlist data, but they
    force an expensive full viewport paint.  A deliberate user request keeps
    its existing priority path and is never held here.
    """
    if coordinator._priority_reasons or not _watchlist_is_current(coordinator):
        return ""
    workspace = coordinator.workspace
    try:
        watchlist_spec = workspace._spec_for_key_or_index("watchlist") or {}
        watchlist = watchlist_spec.get("widget")
    except (AttributeError, RuntimeError, TypeError, ValueError):
        return ""
    hold_reader = getattr(watchlist, "should_hold_background_prewarm", None)
    if not callable(hold_reader):
        return ""
    try:
        return "watchlist_active" if hold_reader() else ""
    except (AttributeError, RuntimeError, TypeError, ValueError):
        return ""


def _visible_watchlist_prewarm_hold_reason(coordinator) -> str:
    """Hold ordinary queued staging only after Watchlist's first frame is ready."""
    if not bool(getattr(coordinator.workspace, "_background_prewarm_queue", ())):
        return ""
    return _foreground_watchlist_hold_reason(coordinator)


def _interactive_handoff_start_delay_ms(workspace, reason: object) -> int:
    """Reuse shell-nav's quiet window before constructing a cold staged target.

    Group navigation can be reversed on the next event-loop turn.  Starting a
    priority preload at zero delay races that return: QWidget construction then
    blocks the Watchlist reveal even though the target is no longer wanted.
    Only shell navigation has the existing quiet-window contract; direct user
    activation remains immediate.
    """
    reason_text = normalize_tab_load_reason(reason)
    if reason_text != TabLoadReason.SHELL_NAV.value:
        return 0
    delay_reader = getattr(workspace, "_lazy_tab_load_delay_ms", None)
    if not callable(delay_reader):
        return 0
    try:
        return max(0, int(delay_reader(reason_text) or 0))
    except (AttributeError, RuntimeError, TypeError, ValueError):
        return 0


def _preload_start_blocked(workspace) -> bool:
    return bool(
        getattr(workspace, "_shutting_down", False)
        or workspace._background_prewarm_started
        or not workspace._background_prewarm_enabled
        or workspace._background_prewarm_finished
    )


def _preload_runtime_ready(workspace) -> bool:
    coordinator = getattr(workspace, "_background_preload_coordinator", None)
    priority_pending = bool(getattr(coordinator, "_priority_reasons", None))
    return bool(
        (workspace._initial_real_tab_activated or priority_pending)
        and workspace.data_provider is not None
        and workspace.engine is not None
    )


def _schedule_preload(coordinator, delay_ms: int) -> bool:
    workspace = coordinator.workspace
    if getattr(workspace, "_shutting_down", False) or not workspace._background_prewarm_enabled:
        return False
    coordinator.timer.start(max(0, int(delay_ms)))
    return True


def _pop_next_step(workspace) -> tuple[str, Mapping] | None:
    while workspace._background_prewarm_queue:
        key = workspace._background_prewarm_queue.pop(0)
        spec = workspace._spec_for_key_or_index(key)
        if spec is not None:
            return key, spec
    return None


def _preload_dependencies_ready(workspace, spec: Mapping) -> bool:
    profile = str(spec.get("constructor_profile") or "").strip()
    if profile == TabConstructorProfile.WORKSPACE_PARENT.value:
        return True
    if workspace.data_provider is None:
        return False
    return profile != TabConstructorProfile.SCAN.value or workspace.engine is not None


def _active_step_timed_out(workspace) -> bool:
    elapsed_ms = (
        time.perf_counter() - float(workspace._background_prewarm_active_started_at or 0.0)
    ) * 1000.0
    return elapsed_ms >= workspace.BACKGROUND_PREWARM_STEP_TIMEOUT_MS


def _cancellation_settlement_timed_out(coordinator) -> bool:
    started_at = float(coordinator._cancel_settlement_started_at or 0.0)
    if started_at <= 0.0:
        return False
    elapsed_ms = (time.perf_counter() - started_at) * 1000.0
    return elapsed_ms >= coordinator.workspace.BACKGROUND_PREWARM_CANCEL_SETTLEMENT_TIMEOUT_MS


def _receipt_status(receipt) -> dict:
    if receipt is None:
        return {}
    status = getattr(receipt, "status", None)
    if not callable(status):
        return {"accepted": False, "settled": False}
    try:
        result = status()
    except (AttributeError, OSError, RuntimeError, TypeError, ValueError):
        return {"accepted": False, "settled": False}
    return dict(result) if isinstance(result, Mapping) else {"accepted": False, "settled": False}


def _loaded_preload_keys(workspace, planned_order: list[str]) -> list[str]:
    loaded: list[str] = []
    for spec in workspace._tab_specs:
        key = str(spec.get("key") or "").strip()
        if spec.get("loaded") and key in planned_order:
            loaded.append(key)
    return loaded


def _ready_preload_keys(workspace, planned_order: list[str]) -> list[str]:
    return [key for key in planned_order if _preload_key_ready(workspace, key)]


def _priority_closure_status(coordinator) -> dict[str, list[str]]:
    return {
        key: list(closure)
        for key, closure in coordinator._priority_closures.items()
    }


def _shutdown_receipt_statuses(coordinator) -> list[dict]:
    return [_receipt_status(receipt) for receipt in coordinator._shutdown_cancel_receipts]


def _all_receipts_settled(receipt_statuses: list[dict]) -> bool:
    return all(bool(status.get("settled")) for status in receipt_statuses)


def _missing_preload_dependencies(workspace, key: str) -> tuple[str, ...]:
    return tuple(
        dependency
        for dependency in preload_dependencies_for(key)
        if not _preload_key_ready(workspace, dependency)
    )


def _pending_preload_dependencies(workspace, dependencies: tuple[str, ...]) -> tuple[str, ...]:
    pending = set(workspace._background_prewarm_queue)
    active_key = str(workspace._background_prewarm_active_key or "")
    return tuple(
        dependency
        for dependency in dependencies
        if dependency in pending or dependency == active_key
    )


def _requeue_dependency_step(coordinator, key: str) -> None:
    workspace = coordinator.workspace
    workspace._background_prewarm_queue.insert(0, key)
    closure = _ordered_unready_dependency_closure(workspace, key)
    coordinator._priority_boosted_keys.update(closure)
    _move_priority_closure_to_front(workspace, closure)
    _schedule_preload(coordinator, workspace.BACKGROUND_PREWARM_POLL_INTERVAL_MS)


def _clear_cancellation_state(coordinator) -> None:
    coordinator._cancelling_key = ""
    coordinator._cancel_receipt = None
    coordinator._cancel_terminal_status = ""
    coordinator._cancel_terminal_detail = ""
    coordinator._cancel_settlement_started_at = 0.0


def _record_step_terminal(workspace, key: str, widget, status: str, detail: str) -> None:
    workspace._background_prewarm_completion_order.append(key)
    if status == "ready" and widget is not None:
        setattr(widget, "_workspace_background_preload_ready", True)
        workspace._background_prewarm_failures.pop(key, None)
        workspace._background_prewarm_timeouts = [
            candidate for candidate in workspace._background_prewarm_timeouts if candidate != key
        ]
        return
    workspace._background_prewarm_failures[key] = str(detail or status)
    if status == "timeout" and key not in workspace._background_prewarm_timeouts:
        workspace._background_prewarm_timeouts.append(key)


def _promote_priority_widget(coordinator, key: str, widget, priority_reason: str) -> None:
    workspace = coordinator.workspace
    if not priority_reason or widget is None or workspace.tabs.currentWidget() is not widget:
        return
    workspace._promote_loaded_tab_to_interactive(widget, priority_reason)
    workspace._startup_last_allowed_index = workspace._tab_index_for_key(key)
    workspace._notify_tab_activated(key, widget)
    if not workspace._initial_real_tab_activated:
        if priority_reason == TabLoadReason.RESTORE_LAST_TAB.value:
            launch_started_at = float(
                getattr(getattr(workspace, "host", None), "_launch_started_at", 0.0) or 0.0
            )
            if launch_started_at > 0.0:
                workspace._initial_tab_ready_elapsed_ms = (
                    time.perf_counter() - launch_started_at
                ) * 1000.0
        on_initial_activation = getattr(workspace, "_on_initial_real_tab_activated", None)
        if callable(on_initial_activation):
            on_initial_activation()


def _finish_all_preloads(coordinator) -> None:
    workspace = coordinator.workspace
    if workspace._background_prewarm_finished:
        return
    workspace._background_prewarm_finished = True
    workspace._background_prewarm_finished_at = time.perf_counter()
    host = workspace.host or workspace.window()
    refresh_supplier = getattr(host, "_refresh_central_quote_code_supplier", None)
    if callable(refresh_supplier):
        try:
            refresh_supplier()
        except (AttributeError, RuntimeError, TypeError, ValueError) as exc:
            log.warning(f"[Workspace] refresh quote universe after preload failed: {exc}")
    log.info(
        "[Workspace] background tab preload finished scope=%s completed=%s "
        "startup_lazy_handoff=%s failures=%s",
        coordinator._completion_scope,
        len(workspace._background_prewarm_completion_order),
        len(coordinator._startup_lazy_handoff_keys),
        sorted(workspace._background_prewarm_failures),
    )


def _record_visible_watchlist_terminal(
    coordinator,
    completed_key: str,
    terminal_status: str,
    terminal_detail: str = "",
) -> None:
    """Record Watchlist preload state without mistaking hidden staging for a first frame."""
    workspace = coordinator.workspace
    if completed_key != "watchlist":
        return
    current_state = str(
        getattr(workspace, "_background_prewarm_visible_watchlist_state", "pending") or "pending"
    )
    if current_state != "pending":
        return
    spec = workspace._spec_for_key_or_index("watchlist")
    widget = (spec or {}).get("widget")
    try:
        mounted_current = bool(
            (spec or {}).get("mounted")
            and widget is not None
            and workspace.tabs.currentWidget() is widget
        )
    except (AttributeError, RuntimeError, TypeError):
        mounted_current = False
    state = (
        "ready"
        if terminal_status == "ready" and mounted_current
        else ("staged_ready" if terminal_status == "ready" else "terminal_failed")
    )
    workspace._background_prewarm_visible_watchlist_state = state
    workspace._background_prewarm_visible_watchlist_at = time.perf_counter()
    workspace._background_prewarm_visible_watchlist_detail = (
        "" if state == "ready" else str(terminal_detail or terminal_status or "terminal_failed")
    )
    log.info(
        "[Workspace] background preload Watchlist terminal state=%s; continue hidden staging remaining=%s",
        state,
        len(workspace._background_prewarm_queue),
    )


def _handoff_after_interactive_target_terminal(
    coordinator,
    completed_key: str,
    terminal_status: str,
) -> bool:
    if completed_key not in coordinator._interactive_handoff_targets:
        return False
    coordinator._interactive_handoff_targets.discard(completed_key)
    if coordinator._interactive_handoff_targets or coordinator._priority_reasons:
        return False

    # All WIDGET_PREWARM pages now stage outside the live QTabWidget, so a
    # satisfied interactive target must not terminate the remaining hidden
    # queue.  It simply resumes ordinary all-planned staging.
    del terminal_status
    return False


def _finish_visible_watchlist_resume_handoff(coordinator) -> None:
    """Drop superseded priority work and resume normal hidden staging."""
    coordinator._watchlist_resume_pause_requested = False
    coordinator._priority_reasons.clear()
    coordinator._prestart_priority_order.clear()
    coordinator._priority_closures.clear()
    coordinator._priority_boosted_keys.clear()
    coordinator._interactive_handoff_targets.clear()


def cancel_background_tab_preload(coordinator) -> None:
    coordinator.timer.stop()
    coordinator.workspace._background_prewarm_foreground_hold_reason = ""
    coordinator._priority_reasons.clear()
    coordinator._interactive_handoff_targets.clear()
    coordinator._watchlist_resume_pause_requested = False
    coordinator._foreground_hold_active_key = ""
    coordinator._foreground_hold_mode = ""
    coordinator._foreground_hold_requeued_keys.clear()
    receipt = coordinator._cancel_receipt
    if receipt is None and coordinator.workspace._background_prewarm_active_widget is not None:
        receipt = coordinator._cancel_active_widget(reason="owner_shutdown")
    if receipt is not None:
        coordinator._shutdown_cancel_receipts.append(receipt)
    _clear_cancellation_state(coordinator)
    coordinator._cancellation_blocked = False
    coordinator._active_step_count = 0


class BackgroundTabPreloadCoordinator(QObject):
    """Serially construct and hydrate workspace tabs after first paint."""

    stepStarted = pyqtSignal(str)

    def __init__(self, workspace, *, enabled: bool) -> None:
        super().__init__(workspace)
        self.workspace = workspace
        workspace._background_prewarm_enabled = bool(enabled)
        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.timeout.connect(self.advance)
        self._priority_reasons: dict[str, str] = {}
        self._prestart_priority_order: list[str] = []
        self._priority_closures: dict[str, tuple[str, ...]] = {}
        self._priority_boosted_keys: set[str] = set()
        self._promoted_order: list[str] = []
        self._cancelling_key = ""
        self._cancel_receipt = None
        self._cancel_terminal_status = ""
        self._cancel_terminal_detail = ""
        self._cancel_settlement_started_at = 0.0
        self._cancellation_blocked = False
        self._cancellation_timeouts: dict[str, str] = {}
        self._dependency_failures: dict[str, str] = {}
        self._active_step_count = 0
        self._max_concurrent_steps = 0
        self._shutdown_cancel_receipts: list[object] = []
        self._completion_scope = "all_planned"
        self._startup_lazy_handoff_keys: list[str] = []
        self._interactive_handoff_targets: set[str] = set()
        self._watchlist_resume_pause_requested = False
        self._foreground_hold_active_key = ""
        self._foreground_hold_mode = ""
        self._foreground_hold_count = 0
        self._foreground_hold_requeued_keys: set[str] = set()
        workspace._background_prewarm_timer = self.timer

    def start(self) -> None:
        workspace = self.workspace
        if _preload_start_blocked(workspace) or not _preload_runtime_ready(workspace):
            return
        workspace._background_prewarm_started = True
        workspace._background_prewarm_queue = _configured_preload_queue(workspace)
        self._completion_scope = "all_planned"
        self._startup_lazy_handoff_keys.clear()
        self._interactive_handoff_targets.clear()
        self._watchlist_resume_pause_requested = False
        self._foreground_hold_active_key = ""
        self._foreground_hold_mode = ""
        self._foreground_hold_count = 0
        self._foreground_hold_requeued_keys.clear()
        workspace._background_prewarm_foreground_hold_reason = ""
        workspace._background_prewarm_visible_watchlist_state = "pending"
        workspace._background_prewarm_visible_watchlist_at = 0.0
        workspace._background_prewarm_visible_watchlist_detail = ""
        _apply_prestart_priority_closures(self)
        _schedule_preload(self, _initial_preload_delay_ms(self))

    def advance(self) -> None:
        workspace = self.workspace
        if getattr(workspace, "_shutting_down", False) or workspace._background_prewarm_finished:
            workspace._background_prewarm_queue.clear()
            return
        with ui_stall_span("ClassicWorkspace._prewarm_next_tab", signal="background_prewarm"):
            if self._cancellation_blocked:
                self._poll_cancelled_step()
                return
            if workspace._background_prewarm_active_key:
                if self._cancelling_key:
                    self._poll_cancelled_step()
                    return
                if self._foreground_hold_active_key:
                    if _watchlist_is_current(self):
                        workspace._background_prewarm_foreground_hold_reason = "watchlist_active"
                        _schedule_preload(
                            self,
                            max(1, int(workspace.BACKGROUND_PREWARM_WATCHLIST_HOLD_POLL_MS)),
                        )
                        return
                    if not self.resume_foreground_hold_after_watchlist():
                        _schedule_preload(self, workspace.BACKGROUND_PREWARM_POLL_INTERVAL_MS)
                        return
                self._poll_active_step()
                return
            self._discard_stale_priority_requests()
            quiet_delay_ms = _ordinary_step_interaction_quiet_delay_ms(self)
            if quiet_delay_ms > 0:
                workspace._background_prewarm_foreground_hold_reason = ""
                _schedule_preload(self, quiet_delay_ms)
                return
            hold_reason = _visible_watchlist_prewarm_hold_reason(self)
            if hold_reason:
                previous_reason = str(
                    getattr(workspace, "_background_prewarm_foreground_hold_reason", "") or ""
                )
                workspace._background_prewarm_foreground_hold_reason = hold_reason
                if previous_reason != hold_reason:
                    log.info(
                        "[Workspace] pause ordinary background preload while Watchlist remains active"
                    )
                _schedule_preload(
                    self,
                    max(1, int(workspace.BACKGROUND_PREWARM_WATCHLIST_HOLD_POLL_MS)),
                )
                return
            workspace._background_prewarm_foreground_hold_reason = ""
            self._start_next_step()

    def status(self) -> dict:
        workspace = self.workspace
        planned_order = list(workspace.STARTUP_TAB_LOAD_ORDER)
        loaded_keys = _loaded_preload_keys(workspace, planned_order)
        shutdown_receipts = _shutdown_receipt_statuses(self)
        return {
            "enabled": bool(workspace._background_prewarm_enabled),
            "started": bool(workspace._background_prewarm_started),
            "finished": bool(workspace._background_prewarm_finished),
            "completion_scope": self._completion_scope,
            "visible_watchlist_state": str(
                getattr(workspace, "_background_prewarm_visible_watchlist_state", "pending") or "pending"
            ),
            "visible_watchlist_at": float(
                getattr(workspace, "_background_prewarm_visible_watchlist_at", 0.0) or 0.0
            ),
            "visible_watchlist_detail": str(
                getattr(workspace, "_background_prewarm_visible_watchlist_detail", "") or ""
            ),
            "startup_lazy_handoff_keys": list(self._startup_lazy_handoff_keys),
            "startup_lazy_handoff_count": len(self._startup_lazy_handoff_keys),
            "interactive_handoff_targets": sorted(self._interactive_handoff_targets),
            "watchlist_resume_pause_requested": self._watchlist_resume_pause_requested,
            "foreground_hold_reason": str(
                getattr(workspace, "_background_prewarm_foreground_hold_reason", "") or ""
            ),
            "foreground_hold_active_key": self._foreground_hold_active_key,
            "foreground_hold_mode": self._foreground_hold_mode,
            "foreground_hold_count": self._foreground_hold_count,
            "foreground_hold_requeued_keys": sorted(self._foreground_hold_requeued_keys),
            "planned_order": planned_order,
            "planned_count": len(planned_order),
            "start_order": list(workspace._background_prewarm_start_order),
            "completion_order": list(workspace._background_prewarm_completion_order),
            "ready_keys": _ready_preload_keys(workspace, planned_order),
            "loaded_keys": loaded_keys,
            "loaded_count": len(loaded_keys),
            "active_key": str(workspace._background_prewarm_active_key or ""),
            "remaining_keys": list(workspace._background_prewarm_queue),
            "failures": dict(workspace._background_prewarm_failures),
            "timeouts": list(workspace._background_prewarm_timeouts),
            "promoted_order": list(self._promoted_order),
            "pending_priority_keys": list(self._priority_reasons),
            "priority_closures": _priority_closure_status(self),
            "dependency_failures": dict(self._dependency_failures),
            "cancelling_key": self._cancelling_key,
            "cancel_receipt": _receipt_status(self._cancel_receipt),
            "cancellation_settlement_timeout_ms": (
                workspace.BACKGROUND_PREWARM_CANCEL_SETTLEMENT_TIMEOUT_MS
            ),
            "cancellation_blocked_poll_interval_ms": (
                workspace.BACKGROUND_PREWARM_CANCEL_BLOCKED_POLL_INTERVAL_MS
            ),
            "cancellation_timeouts": dict(self._cancellation_timeouts),
            "cancellation_timeout_keys": list(self._cancellation_timeouts),
            "cancellation_blocked": self._cancellation_blocked,
            "blocked_reason": "cancellation_timeout" if self._cancellation_blocked else "",
            "active_step_count": self._active_step_count,
            "max_concurrent_steps": self._max_concurrent_steps,
            "timer_active": bool(self.timer.isActive()),
            "interaction_quiet_remaining_ms": _ordinary_step_interaction_quiet_delay_ms(self),
            "shutdown_cancel_receipts": shutdown_receipts,
            "shutdown_cancellation_settled": _all_receipts_settled(shutdown_receipts),
        }

    def prioritize(self, key: str, reason: object) -> bool:
        """Move an interactive request to the next serial preload slot."""
        workspace = self.workspace
        if self._foreground_hold_active_key:
            self.resume_foreground_hold_after_watchlist()
        request = _resume_startup_lazy_handoff(self, key, reason)
        if request is None:
            request = _normalized_priority_request(workspace, key, reason)
        if request is None:
            return False

        key_text, reason_text = request
        self._watchlist_resume_pause_requested = False
        self._discard_stale_priority_requests(keep_key=key_text)
        if self._completion_scope == "interactive_resume":
            active_key = str(workspace._background_prewarm_active_key or "")
            self._priority_reasons.clear()
            self._priority_closures.clear()
            self._priority_boosted_keys.clear()
            self._interactive_handoff_targets = {key_text}
            workspace._background_prewarm_queue = _unready_startup_keys(
                workspace,
                exclude={active_key} if active_key else None,
            )
        active_key = str(workspace._background_prewarm_active_key or "")
        self._priority_reasons[key_text] = reason_text
        if key_text not in self._promoted_order:
            self._promoted_order.append(key_text)
        workspace._lazy_loading_keys.discard(key_text)
        if not workspace._background_prewarm_started:
            self._prestart_priority_order = [
                queued_key for queued_key in self._prestart_priority_order if queued_key != key_text
            ]
            self._prestart_priority_order.append(key_text)
            start_preload = getattr(workspace, "_start_background_tab_prewarm", None)
            if callable(start_preload):
                start_preload()
            return True
        closure = _ordered_unready_dependency_closure(workspace, key_text)
        self._priority_closures[key_text] = closure
        self._priority_boosted_keys.update(closure)
        _move_priority_closure_to_front(workspace, closure)
        if self._cancellation_blocked or not active_key:
            _schedule_preload(self, _interactive_handoff_start_delay_ms(workspace, reason_text))
        return True

    def _discard_stale_priority_requests(self, *, keep_key: str = "") -> list[str]:
        """Demote requests whose tab is no longer the current user target."""
        workspace = self.workspace
        current_key = ""
        try:
            current_spec = workspace._spec_for_key_or_index(workspace.tabs.currentIndex())
            current_key = str((current_spec or {}).get("key") or "").strip()
        except (AttributeError, RuntimeError, TypeError, ValueError):
            current_key = ""
        allowed_key = str(keep_key or current_key or "").strip()
        stale_keys = [
            key
            for key in self._priority_reasons
            if key != allowed_key
        ]
        if not stale_keys:
            return []
        stale_set = set(stale_keys)
        for key in stale_keys:
            self._priority_reasons.pop(key, None)
            self._priority_closures.pop(key, None)
            self._interactive_handoff_targets.discard(key)
        self._prestart_priority_order = [
            key for key in self._prestart_priority_order if key not in stale_set
        ]
        pending_keys = set(workspace._background_prewarm_queue)
        workspace._background_prewarm_queue = [
            key for key in workspace.STARTUP_TAB_LOAD_ORDER if key in pending_keys
        ]
        self._priority_boosted_keys = {
            dependency
            for closure in self._priority_closures.values()
            for dependency in closure
        }
        for closure in self._priority_closures.values():
            _move_priority_closure_to_front(workspace, closure)
        return stale_keys

    def defers_interactive_activation(self, key: str) -> bool:
        key_text = str(key or "").strip()
        return bool(
            key_text
            and key_text in self._priority_reasons
            and key_text == str(self.workspace._background_prewarm_active_key or "")
        )

    def discard_priority_reason(self, reason: object) -> list[str]:
        """Remove superseded startup priorities without touching real user requests."""
        reason_text = normalize_tab_load_reason(reason)
        discarded = [
            key
            for key, pending_reason in self._priority_reasons.items()
            if pending_reason == reason_text
        ]
        if not discarded:
            return []
        for key in discarded:
            self._priority_reasons.pop(key, None)
            self._priority_closures.pop(key, None)
        discarded_set = set(discarded)
        self._prestart_priority_order = [
            key for key in self._prestart_priority_order if key not in discarded_set
        ]
        pending_keys = set(self.workspace._background_prewarm_queue)
        self.workspace._background_prewarm_queue = [
            key
            for key in self.workspace.STARTUP_TAB_LOAD_ORDER
            if key in pending_keys
        ]
        self._priority_boosted_keys = {
            dependency
            for closure in self._priority_closures.values()
            for dependency in closure
        }
        for closure in self._priority_closures.values():
            _move_priority_closure_to_front(self.workspace, closure)
        return discarded

    def pause_interactive_handoff_for_watchlist(self, reason: object) -> bool:
        """Protect visible Watchlist from an already active hidden preload step."""
        if not is_interactive_tab_load_reason(reason):
            return False
        workspace = self.workspace
        if workspace._background_prewarm_finished or not _preload_key_ready(
            workspace,
            "watchlist",
        ):
            return False

        self._discard_stale_priority_requests()
        self._priority_reasons.clear()
        self._prestart_priority_order.clear()
        self._priority_closures.clear()
        self._priority_boosted_keys.clear()
        self._interactive_handoff_targets.clear()
        active_key = str(workspace._background_prewarm_active_key or "")
        if active_key and active_key != "watchlist":
            return self._hold_active_step_for_visible_watchlist()

        _finish_visible_watchlist_resume_handoff(self)
        return True

    def resume_foreground_hold_after_watchlist(self) -> bool:
        """Release an active cooperative hold as soon as the user leaves Watchlist."""
        key = str(self._foreground_hold_active_key or "")
        if not key:
            return False
        if _watchlist_is_current(self):
            return False
        if self._foreground_hold_mode == "cancelling":
            _schedule_preload(self, 0)
            return True
        if self._foreground_hold_mode != "paused":
            return False
        widget = self.workspace._background_prewarm_active_widget
        resume = getattr(widget, "resume_background_preload", None)
        try:
            resumed = bool(callable(resume) and resume())
        except (AttributeError, OSError, RuntimeError, TypeError, ValueError) as exc:
            log.warning("[Workspace] resume foreground-held preload failed key=%s: %s", key, exc)
            resumed = False
        if not resumed:
            return False
        self._foreground_hold_active_key = ""
        self._foreground_hold_mode = ""
        self.workspace._background_prewarm_foreground_hold_reason = ""
        _schedule_preload(self, 0)
        return True

    def _hold_active_step_for_visible_watchlist(self) -> bool:
        workspace = self.workspace
        key = str(workspace._background_prewarm_active_key or "")
        if not key:
            return False
        if self._foreground_hold_active_key == key:
            return True
        widget = workspace._background_prewarm_active_widget
        pause = getattr(widget, "pause_background_preload", None)
        try:
            paused = bool(callable(pause) and pause())
        except (AttributeError, OSError, RuntimeError, TypeError, ValueError) as exc:
            log.warning("[Workspace] pause active background preload failed key=%s: %s", key, exc)
            paused = False
        self._foreground_hold_active_key = key
        self._foreground_hold_count += 1
        workspace._background_prewarm_foreground_hold_reason = "watchlist_active"
        if paused:
            self._foreground_hold_mode = "paused"
            _schedule_preload(
                self,
                max(1, int(workspace.BACKGROUND_PREWARM_WATCHLIST_HOLD_POLL_MS)),
            )
            return True

        self._foreground_hold_mode = "cancelling"
        self._begin_foreground_hold_cancellation()
        return True

    def _begin_foreground_hold_cancellation(self) -> None:
        workspace = self.workspace
        key = str(workspace._background_prewarm_active_key or "")
        if not key or self._cancelling_key:
            return
        self._cancelling_key = key
        self._cancel_terminal_status = "foreground_hold"
        self._cancel_terminal_detail = "watchlist_visible"
        self._cancel_settlement_started_at = time.perf_counter()
        self._cancel_receipt = self._cancel_active_widget(reason="watchlist_visible")
        self._poll_cancelled_step()

    def _park_cancelled_step_for_foreground_hold(self) -> None:
        workspace = self.workspace
        key = str(workspace._background_prewarm_active_key or "")
        if key and not _preload_key_ready(workspace, key):
            queued_keys = set(workspace._background_prewarm_queue)
            queued_keys.add(key)
            workspace._background_prewarm_queue = [
                candidate
                for candidate in workspace.STARTUP_TAB_LOAD_ORDER
                if candidate in queued_keys and not _preload_key_ready(workspace, candidate)
            ]
            for closure in self._priority_closures.values():
                _move_priority_closure_to_front(workspace, closure)
            self._foreground_hold_requeued_keys.add(key)

        workspace._background_prewarm_active_key = ""
        workspace._background_prewarm_active_widget = None
        workspace._background_prewarm_active_started_at = 0.0
        self._active_step_count = max(0, self._active_step_count - 1)
        self._foreground_hold_active_key = ""
        self._foreground_hold_mode = ""
        _clear_cancellation_state(self)
        if _watchlist_is_current(self):
            workspace._background_prewarm_foreground_hold_reason = "watchlist_active"
            _schedule_preload(
                self,
                max(1, int(workspace.BACKGROUND_PREWARM_WATCHLIST_HOLD_POLL_MS)),
            )
            return
        workspace._background_prewarm_foreground_hold_reason = ""
        _schedule_preload(self, 0)

    def _poll_active_step(self) -> None:
        workspace = self.workspace
        if self._cancelling_key:
            self._poll_cancelled_step()
            return
        try:
            completed = self._widget_preload_complete(workspace._background_prewarm_active_widget)
        except (AttributeError, OSError, RuntimeError, TypeError, ValueError) as exc:
            self._fail_active_step(str(exc))
            return
        if completed:
            self._complete_active_step()
            return
        if _active_step_timed_out(workspace):
            timeout_ms = workspace.BACKGROUND_PREWARM_STEP_TIMEOUT_MS
            self._begin_step_cancellation(
                "timeout",
                f"cache-only preload exceeded {timeout_ms}ms",
                reason="step_timeout",
            )
            return
        _schedule_preload(self, workspace.BACKGROUND_PREWARM_POLL_INTERVAL_MS)

    def _start_next_step(self) -> None:
        step = _pop_next_step(self.workspace)
        if step is None:
            _finish_all_preloads(self)
            return
        key, spec = step
        if not _preload_dependencies_ready(self.workspace, spec):
            self.workspace._background_prewarm_queue.insert(0, key)
            _schedule_preload(self, self.workspace.BACKGROUND_PREWARM_POLL_INTERVAL_MS)
            return
        missing_dependencies = _missing_preload_dependencies(self.workspace, key)
        if missing_dependencies:
            unresolved = _pending_preload_dependencies(self.workspace, missing_dependencies)
            if unresolved:
                _requeue_dependency_step(self, key)
                return
            self._fail_dependency_blocked_step(key, spec, missing_dependencies)
            return
        self._activate_step(key, spec)

    def _fail_dependency_blocked_step(
        self,
        key: str,
        spec: Mapping,
        missing_dependencies: tuple[str, ...],
    ) -> None:
        detail = "dependencies_not_ready:" + ",".join(missing_dependencies)
        self._dependency_failures[key] = detail
        self.workspace._background_prewarm_start_order.append(key)
        _record_step_terminal(self.workspace, key, None, "dependency_failed", detail)
        priority_reason = self._priority_reasons.pop(key, "")
        self._priority_closures.pop(key, None)
        self._priority_boosted_keys.discard(key)
        placeholder = spec.get("widget")
        set_error = getattr(placeholder, "set_error", None)
        if priority_reason and callable(set_error):
            set_error("上游数据未就绪，综合数据加载已安全停止。")
        log.error(
            "[Workspace] background preload dependency gate rejected key=%s missing=%s",
            key,
            missing_dependencies,
        )
        if _handoff_after_interactive_target_terminal(
            self,
            key,
            "dependency_failed",
        ):
            return
        _schedule_preload(self, _next_step_delay_ms(self))

    def _activate_step(self, key: str, spec: Mapping) -> None:
        workspace = self.workspace
        self._dependency_failures.pop(key, None)
        resumed_from_foreground_hold = key in self._foreground_hold_requeued_keys
        self._foreground_hold_requeued_keys.discard(key)
        if not resumed_from_foreground_hold:
            workspace._background_prewarm_start_order.append(key)
        workspace._background_prewarm_active_key = key
        workspace._background_prewarm_active_widget = None
        workspace._background_prewarm_active_started_at = time.perf_counter()
        if not resumed_from_foreground_hold:
            self.stepStarted.emit(key)
        self._active_step_count += 1
        self._max_concurrent_steps = max(self._max_concurrent_steps, self._active_step_count)
        with ui_stall_span(
            "BackgroundTabPreloadCoordinator.construct_and_stage_tab",
            tab=key,
            signal="background_prewarm",
        ):
            widget = spec.get("widget") if spec.get("loaded") else workspace.ensure_tab_loaded(
                key,
                reason=TabLoadReason.BACKGROUND_PREWARM.value,
            )
        if widget is None:
            self._fail_active_step("tab construction failed")
            return
        workspace._background_prewarm_active_widget = widget
        with ui_stall_span(
            "BackgroundTabPreloadCoordinator.prime_tab_runtime",
            tab=key,
            signal="background_prewarm",
        ):
            primed = workspace._prime_tab_runtime(widget)
        if not primed:
            self._fail_active_step("startup prime failed")
            return
        try:
            completed = self._widget_preload_complete(widget)
        except (AttributeError, OSError, RuntimeError, TypeError, ValueError) as exc:
            self._fail_active_step(str(exc))
            return
        if completed:
            self._complete_active_step()
            return
        _schedule_preload(self, workspace.BACKGROUND_PREWARM_POLL_INTERVAL_MS)

    @staticmethod
    def _widget_preload_complete(widget) -> bool:
        if widget is None:
            return False
        checker = getattr(widget, "is_background_preload_complete", None)
        if callable(checker) and not checker():
            return False

        snapshot_prime = getattr(widget, "prime_workspace_background_snapshot", None)
        snapshot_complete = getattr(widget, "is_workspace_background_snapshot_complete", None)
        if not callable(snapshot_prime) or not callable(snapshot_complete):
            return True
        if not snapshot_prime():
            return False
        return bool(snapshot_complete())

    def _complete_active_step(self) -> None:
        self._finish_active_step_and_continue("ready")

    def _finish_active_step_and_continue(self, status: str, detail: str = "") -> None:
        completed_key = str(self.workspace._background_prewarm_active_key or "")
        self._finish_step(status, detail)
        if self._watchlist_resume_pause_requested:
            _finish_visible_watchlist_resume_handoff(self)
        _handoff_after_interactive_target_terminal(self, completed_key, status)
        _record_visible_watchlist_terminal(self, completed_key, status, detail)
        _schedule_preload(self, _next_step_delay_ms(self))

    def _fail_active_step(self, detail: str) -> None:
        if self.workspace._background_prewarm_active_widget is None:
            self._finish_active_step_and_continue("failed", detail)
            return
        self._begin_step_cancellation("failed", detail, reason="step_failed")

    def _begin_step_cancellation(self, status: str, detail: str, *, reason: str) -> None:
        workspace = self.workspace
        key = str(workspace._background_prewarm_active_key or "")
        if not key or self._cancelling_key:
            return
        self._cancelling_key = key
        self._cancel_terminal_status = str(status or "failed")
        self._cancel_terminal_detail = str(detail or status or "failed")
        self._cancel_settlement_started_at = time.perf_counter()
        workspace._background_prewarm_failures[key] = self._cancel_terminal_detail
        if status == "timeout" and key not in workspace._background_prewarm_timeouts:
            workspace._background_prewarm_timeouts.append(key)
        self._cancel_receipt = self._cancel_active_widget(reason=reason)
        self._poll_cancelled_step()

    def _cancel_active_widget(self, *, reason: str):
        widget = self.workspace._background_prewarm_active_widget
        cancel = getattr(widget, "cancel_background_preload", None)
        if not callable(cancel):
            return BackgroundPreloadCancellationReceipt(accepted=False)
        try:
            receipt = cancel(reason=reason)
        except (AttributeError, OSError, RuntimeError, TypeError, ValueError) as exc:
            log.warning(f"[Workspace] background preload cancellation failed: {exc}")
            return BackgroundPreloadCancellationReceipt(accepted=False)
        is_settled = getattr(receipt, "is_settled", None)
        return receipt if callable(is_settled) else BackgroundPreloadCancellationReceipt(accepted=False)

    def _poll_cancelled_step(self) -> None:
        receipt = self._cancel_receipt
        receipt_status = _receipt_status(receipt)
        accepted = bool(receipt_status.get("accepted"))
        is_settled = getattr(receipt, "is_settled", None)
        try:
            settled = bool(accepted and callable(is_settled) and is_settled())
        except (AttributeError, OSError, RuntimeError, TypeError, ValueError):
            settled = False
        if not settled:
            if _cancellation_settlement_timed_out(self):
                self._block_on_cancellation_timeout()
                return
            _schedule_preload(self, self.workspace.BACKGROUND_PREWARM_POLL_INTERVAL_MS)
            return
        self._cancellation_blocked = False
        if (
            self._foreground_hold_mode == "cancelling"
            and self._foreground_hold_active_key == str(self.workspace._background_prewarm_active_key or "")
        ):
            self._park_cancelled_step_for_foreground_hold()
            return
        status = self._cancel_terminal_status
        detail = self._cancel_terminal_detail
        self._finish_active_step_and_continue(status, detail)

    def _block_on_cancellation_timeout(self) -> None:
        workspace = self.workspace
        retry_ms = workspace.BACKGROUND_PREWARM_CANCEL_BLOCKED_POLL_INTERVAL_MS
        if self._cancellation_blocked:
            _schedule_preload(self, retry_ms)
            return
        key = str(self._cancelling_key or workspace._background_prewarm_active_key or "")
        timeout_ms = workspace.BACKGROUND_PREWARM_CANCEL_SETTLEMENT_TIMEOUT_MS
        receipt_status = _receipt_status(self._cancel_receipt)
        accepted = bool(receipt_status.get("accepted"))
        detail = (
            f"cancellation_timeout: physical termination was not confirmed within {timeout_ms}ms "
            f"(accepted={accepted})"
        )
        self._cancellation_timeouts[key] = detail
        self._cancellation_blocked = True
        log.error(
            "[Workspace] background preload fail-closed key=%s reason=cancellation_timeout receipt=%s",
            key,
            receipt_status,
        )
        _schedule_preload(self, retry_ms)

    def _finish_step(self, status: str, detail: str = "") -> None:
        workspace = self.workspace
        key = str(workspace._background_prewarm_active_key or "")
        widget = workspace._background_prewarm_active_widget
        priority_reason = self._priority_reasons.pop(key, "")
        if not key:
            return
        _record_step_terminal(workspace, key, widget, status, detail)
        spec = workspace._spec_for_key_or_index(key)
        mount = getattr(workspace, "_mount_preloaded_tab", None)
        target_index = workspace._tab_index_for_key(key)
        if key == "lhb" and priority_reason == TabLoadReason.SHELL_NAV.value:
            prepare_guard = getattr(widget, "prepare_shell_nav_repaint_guard", None)
            if callable(prepare_guard):
                try:
                    prepare_guard()
                except (AttributeError, RuntimeError, TypeError, ValueError) as exc:
                    log.debug("skip LHB staged shell-nav repaint guard: %s", exc)
        if (
            spec is not None
            and callable(mount)
            and target_index >= 0
            and workspace.tabs.currentIndex() == target_index
        ):
            widget = mount(key)
            workspace._background_prewarm_active_widget = widget
        _promote_priority_widget(self, key, widget, priority_reason)
        workspace._background_prewarm_active_key = ""
        workspace._background_prewarm_active_widget = None
        workspace._background_prewarm_active_started_at = 0.0
        self._active_step_count = max(0, self._active_step_count - 1)
        self._priority_boosted_keys.discard(key)
        self._priority_closures.pop(key, None)
        _clear_cancellation_state(self)
