# -*- coding: utf-8 -*-
"""K 线窗口首开阶段协调器。"""

from __future__ import annotations

import math
import time
from contextlib import suppress

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtWidgets import QLabel, QVBoxLayout, QWidget

from ui.kline_load_controller import KLINE_OPEN_STAGE_ORDER
from ui.kline_window_recovery import install_render_process_recovery, uninstall_render_process_recovery

_STAGE_METRICS = {
    "shell_ready": "kline_open_shell_ready_ms",
    "browser_ready": "kline_open_browser_ready_ms",
    "data_ready": "kline_open_data_ready_ms",
    "js_ready": "kline_open_js_ready_ms",
    "chart_ready": "kline_open_chart_ready_ms",
    "first_interaction": "kline_open_first_interaction_ms",
}
_EXPECTED_STAGE_ERRORS = (AttributeError, ImportError, OSError, RuntimeError, TypeError, ValueError)
_BROWSER_PIPELINE_SLICE_MS = 1


class KlineStageTimeline:
    """Commit one-shot stage observations in contract order.

    Data and browser work may finish in parallel. Early observations are kept
    with their real elapsed time, then committed once every preceding stage is
    present so exported timings remain monotonic and fail closed on gaps.
    """

    def __init__(self) -> None:
        self._pending: dict[str, float] = {}
        self._timings: dict[str, float] = {}
        self._observed: dict[str, float] = {}

    @staticmethod
    def _elapsed(value: float) -> float:
        elapsed = float(value)
        if not math.isfinite(elapsed) or elapsed < 0:
            raise ValueError("stage elapsed time must be finite and non-negative")
        return elapsed

    def mark(self, stage: str, *, elapsed_ms: float) -> tuple[str, ...]:
        normalized = str(stage or "").strip()
        if normalized not in KLINE_OPEN_STAGE_ORDER:
            raise ValueError(f"unknown K-line open stage: {normalized or '<blank>'}")
        if normalized in self._observed:
            return ()
        observed = self._elapsed(elapsed_ms)
        self._observed[normalized] = observed
        self._pending[normalized] = observed
        return self._flush()

    def _flush(self) -> tuple[str, ...]:
        committed = []
        previous = next(reversed(self._timings.values()), 0.0)
        for stage in KLINE_OPEN_STAGE_ORDER[len(self._timings):]:
            if stage not in self._pending:
                break
            observed = self._pending.pop(stage)
            previous = max(previous, observed)
            self._timings[stage] = previous
            committed.append(stage)
        return tuple(committed)

    def diagnostics(self) -> dict:
        pending = [stage for stage in KLINE_OPEN_STAGE_ORDER if stage in self._pending]
        return {
            "required_stages": list(KLINE_OPEN_STAGE_ORDER),
            "completed_stages": list(self._timings),
            "pending_stages": pending,
            "timings_ms": dict(self._timings),
            "observed_timings_ms": {
                stage: self._observed[stage]
                for stage in KLINE_OPEN_STAGE_ORDER
                if stage in self._observed
            },
            "complete": len(self._timings) == len(KLINE_OPEN_STAGE_ORDER),
        }


def build_chart_host(window, container_layout, browser=None) -> None:
    """Build the WebEngine-free first-paint host."""
    window.chart_host = QWidget(window.container)
    window.chart_host_layout = QVBoxLayout(window.chart_host)
    window.chart_host_layout.setContentsMargins(0, 0, 0, 0)
    window.chart_host_layout.setSpacing(0)
    window.chart_placeholder = QLabel("正在加载图表组件...")
    window.chart_placeholder.setAlignment(Qt.AlignmentFlag.AlignCenter)
    window.chart_host_layout.addWidget(window.chart_placeholder)
    container_layout.addWidget(window.chart_host, 1)


def _object_property(obj, name: str, default=None):
    try:
        value = obj.property(name)
    except (AttributeError, RuntimeError, TypeError):
        value = getattr(obj, f"_{name}", default)
    return default if value is None else value


def _set_object_property(obj, name: str, value) -> None:
    try:
        obj.setProperty(name, value)
    except (AttributeError, RuntimeError, TypeError):
        setattr(obj, f"_{name}", value)


def _handoff_prewarmed_page(browser, page) -> float:
    started_at = time.perf_counter()
    browser.setPage(page)
    _set_object_property(
        browser,
        "klineShellReady",
        bool(_object_property(page, "klineShellReady", False)),
    )
    _set_object_property(
        browser,
        "klineShellHtmlBytes",
        int(_object_property(page, "klineShellHtmlBytes", 0) or 0),
    )
    return (time.perf_counter() - started_at) * 1000.0


def _attach_browser_hierarchy(window, browser, *, marks: dict[str, float]) -> None:
    marks["host"] = time.perf_counter()
    browser_parent = None
    with suppress(AttributeError, RuntimeError, TypeError):
        browser_parent = browser.parentWidget()
    if browser_parent is not window.chart_host:
        browser.setParent(window.chart_host)
    marks["parent"] = time.perf_counter()
    with suppress(AttributeError, RuntimeError, TypeError):
        browser.installEventFilter(window)
        focus_proxy = browser.focusProxy()
        if focus_proxy is not None:
            focus_proxy.installEventFilter(window)
    marks["filters"] = time.perf_counter()
    with suppress(AttributeError, RuntimeError, TypeError):
        browser.loadFinished.connect(window._on_chart_load_finished)
    marks["signal"] = time.perf_counter()
    browser_layout_index = -1
    with suppress(AttributeError, RuntimeError, TypeError):
        browser_layout_index = window.chart_host_layout.indexOf(browser)
    if browser_layout_index < 0:
        window.chart_host_layout.addWidget(browser)
    browser.setUpdatesEnabled(True)
    marks["layout"] = time.perf_counter()
    window.browser = browser
    window._browser_epoch = int(getattr(window, "_browser_epoch", 0) or 0) + 1
    if not install_render_process_recovery(window, browser):
        raise RuntimeError("renderer termination guard installation failed")
    marks["recovery"] = time.perf_counter()
    marks["hierarchy_ready"] = time.perf_counter()


def _show_browser_surface(window, browser, *, marks: dict[str, float]) -> None:
    window.chart_host.show()
    browser.show()
    marks["visible"] = time.perf_counter()


def _activate_browser_runtime(coordinator, window, browser, marks: dict[str, float]) -> None:
    _show_browser_surface(window, browser, marks=marks)
    coordinator.pending_browser = None
    apply_browser_theme = getattr(window, "_apply_browser_surface_theme", None)
    if callable(apply_browser_theme):
        apply_browser_theme()
    marks["theme"] = time.perf_counter()
    marks["load_queued"] = time.perf_counter()
    coordinator.schedule_shell_load(browser, marks)
    marks["load_scheduled"] = time.perf_counter()
    marks["attach_ready"] = time.perf_counter()


def _discard_chart_placeholder(window) -> float:
    placeholder = window.chart_placeholder
    window.chart_placeholder = None
    if placeholder is not None:
        with suppress(*_EXPECTED_STAGE_ERRORS):
            window.chart_host_layout.removeWidget(placeholder)
            placeholder.hide()
            placeholder.deleteLater()
    return time.perf_counter()


def _attach_elapsed_ms(marks: dict[str, float], end: str, start: str) -> float:
    return round((marks[end] - marks[start]) * 1000.0, 3)


def _browser_attach_diagnostics(coordinator, browser, marks: dict[str, float]) -> dict:
    browser_create_ms = _attach_elapsed_ms(marks, "create_ready", "create_start")
    page_handoff_slice_ms = _attach_elapsed_ms(marks, "handoff_ready", "handoff_start")
    hierarchy_slice_ms = _attach_elapsed_ms(marks, "hierarchy_ready", "attach_start")
    activation_slice_ms = _attach_elapsed_ms(marks, "attach_ready", "activation_start")
    browser_attach_sync_ms = max(hierarchy_slice_ms, activation_slice_ms)
    browser_attach_total_ms = _attach_elapsed_ms(marks, "attach_ready", "attach_start")
    load_shell_dispatch_ms = _attach_elapsed_ms(marks, "load_ready", "load_start")
    max_sync_slice_ms = max(
        browser_create_ms,
        page_handoff_slice_ms,
        browser_attach_sync_ms,
        load_shell_dispatch_ms,
    )
    pipeline_total_ms = _attach_elapsed_ms(marks, "stage", "pipeline_started")
    return {
        "page_reused": bool(_object_property(browser, "klineShellReady", False)),
        "browser_create_ms": browser_create_ms,
        "page_handoff_ms": round(float(coordinator._page_handoff_ms), 3),
        "page_handoff_slice_ms": page_handoff_slice_ms,
        "browser_attach_sync_ms": round(browser_attach_sync_ms, 3),
        "browser_attach_total_ms": browser_attach_total_ms,
        "hierarchy_slice_ms": hierarchy_slice_ms,
        "activation_queue_ms": _attach_elapsed_ms(marks, "activation_start", "hierarchy_ready"),
        "activation_slice_ms": activation_slice_ms,
        "max_sync_slice_ms": round(max_sync_slice_ms, 3),
        "pipeline_total_ms": pipeline_total_ms,
        "host_adopt_ms": _attach_elapsed_ms(marks, "host", "attach_start"),
        "set_parent_ms": _attach_elapsed_ms(marks, "parent", "host"),
        "event_filters_ms": _attach_elapsed_ms(marks, "filters", "parent"),
        "signal_ms": _attach_elapsed_ms(marks, "signal", "filters"),
        "layout_commit_ms": _attach_elapsed_ms(marks, "layout", "signal"),
        "surface_show_ms": _attach_elapsed_ms(marks, "visible", "activation_start"),
        "layout_show_ms": round(
            _attach_elapsed_ms(marks, "layout", "signal")
            + _attach_elapsed_ms(marks, "visible", "activation_start"),
            3,
        ),
        "placeholder_ms": _attach_elapsed_ms(marks, "placeholder", "load_ready"),
        "recovery_ms": _attach_elapsed_ms(marks, "recovery", "layout"),
        "browser_theme_ms": _attach_elapsed_ms(marks, "theme", "visible"),
        "stage_record_ms": _attach_elapsed_ms(marks, "stage", "placeholder"),
        "load_shell_ms": _attach_elapsed_ms(marks, "load_ready", "load_start"),
        "load_shell_deferred": True,
        "load_shell_schedule_ms": _attach_elapsed_ms(marks, "load_scheduled", "load_queued"),
        "load_shell_queue_ms": _attach_elapsed_ms(marks, "load_start", "load_scheduled"),
        "load_shell_dispatch_ms": load_shell_dispatch_ms,
        "load_shell_dispatch_ok": True,
        "total_ms": pipeline_total_ms,
    }


def _failed_browser_attach_diagnostics(coordinator, marks: dict[str, float]) -> dict:
    browser_create_ms = _attach_elapsed_ms(marks, "create_ready", "create_start")
    page_handoff_slice_ms = _attach_elapsed_ms(marks, "handoff_ready", "handoff_start")
    hierarchy_slice_ms = _attach_elapsed_ms(marks, "hierarchy_ready", "attach_start")
    activation_slice_ms = _attach_elapsed_ms(marks, "attach_ready", "activation_start")
    browser_attach_sync_ms = max(hierarchy_slice_ms, activation_slice_ms)
    browser_attach_total_ms = _attach_elapsed_ms(marks, "attach_ready", "attach_start")
    load_shell_dispatch_ms = _attach_elapsed_ms(marks, "load_ready", "load_start")
    max_sync_slice_ms = max(
        browser_create_ms,
        page_handoff_slice_ms,
        browser_attach_sync_ms,
        load_shell_dispatch_ms,
    )
    pipeline_total_ms = _attach_elapsed_ms(marks, "load_ready", "pipeline_started")
    return {
        "browser_create_ms": browser_create_ms,
        "page_handoff_ms": round(float(coordinator._page_handoff_ms), 3),
        "page_handoff_slice_ms": page_handoff_slice_ms,
        "browser_attach_sync_ms": round(browser_attach_sync_ms, 3),
        "browser_attach_total_ms": browser_attach_total_ms,
        "hierarchy_slice_ms": hierarchy_slice_ms,
        "activation_queue_ms": _attach_elapsed_ms(marks, "activation_start", "hierarchy_ready"),
        "activation_slice_ms": activation_slice_ms,
        "max_sync_slice_ms": round(max_sync_slice_ms, 3),
        "pipeline_total_ms": pipeline_total_ms,
        "load_shell_deferred": True,
        "load_shell_schedule_ms": _attach_elapsed_ms(marks, "load_scheduled", "load_queued"),
        "load_shell_queue_ms": _attach_elapsed_ms(marks, "load_start", "load_scheduled"),
        "load_shell_ms": _attach_elapsed_ms(marks, "load_ready", "load_start"),
        "load_shell_dispatch_ms": load_shell_dispatch_ms,
        "load_shell_dispatch_ok": False,
        "total_ms": pipeline_total_ms,
    }


def _emit_browser_attached(coordinator, window, browser) -> None:
    diagnostics = window._browser_attach_diagnostics
    coordinator.emit_structured_log(
        "kline.browser_attached",
        code=str(getattr(window, "code", "") or "").strip(),
        reused=bool(getattr(browser, "property", lambda _name: False)("klineShellReady")),
        attach_ms=diagnostics["total_ms"],
        browser_create_ms=diagnostics["browser_create_ms"],
        page_handoff_ms=diagnostics["page_handoff_ms"],
        browser_attach_sync_ms=diagnostics["browser_attach_sync_ms"],
        browser_attach_total_ms=diagnostics["browser_attach_total_ms"],
        hierarchy_slice_ms=diagnostics["hierarchy_slice_ms"],
        activation_queue_ms=diagnostics["activation_queue_ms"],
        activation_slice_ms=diagnostics["activation_slice_ms"],
        layout_commit_ms=diagnostics["layout_commit_ms"],
        surface_show_ms=diagnostics["surface_show_ms"],
        max_sync_slice_ms=diagnostics["max_sync_slice_ms"],
        pipeline_total_ms=diagnostics["pipeline_total_ms"],
        load_shell_call_ms=diagnostics["load_shell_ms"],
        load_shell_deferred=diagnostics["load_shell_deferred"],
        load_shell_queue_ms=diagnostics["load_shell_queue_ms"],
    )


def _dispose_deferred_page(page) -> bool:
    if page is None:
        return True
    clean = True
    try:
        page.stop()
    except _EXPECTED_STAGE_ERRORS:
        clean = False
    try:
        page.deleteLater()
    except _EXPECTED_STAGE_ERRORS:
        clean = False
    return clean


def _release_or_dispose_pending_page(page) -> bool:
    if page is None:
        return True
    released = False
    with suppress(*_EXPECTED_STAGE_ERRORS):
        from ui.components.kline_window_manager import (
            KLINE_SHELL_HTML_BYTES_PROPERTY,
            browser_has_ready_kline_shell,
            kline_manager,
        )

        released = bool(
            kline_manager.release_page(
                page,
                shell_ready=browser_has_ready_kline_shell(page),
                html_bytes=int(
                    _object_property(page, KLINE_SHELL_HTML_BYTES_PROPERTY, 0) or 0
                ),
            )
        )
    return released or _dispose_deferred_page(page)


def _take_pending_shell_load(coordinator, browser=None):
    pending = coordinator._pending_shell_load
    if pending is None or (browser is not None and pending[0] is not browser):
        return None
    coordinator._pending_shell_load = None
    with suppress(AttributeError, RuntimeError, TypeError):
        coordinator.shell_load_timer.stop()
    return pending


def _browser_pipeline_is_current(coordinator, state) -> bool:
    if state is None or state is not coordinator._active_browser_pipeline:
        return False
    window = coordinator.window
    browser = state.get("browser")
    return bool(
        not window._closing
        and state.get("generation") == coordinator._browser_pipeline_generation
        and state.get("identity") == coordinator._current_load_identity()
        and getattr(window, "browser", None) is None
        and (browser is None or coordinator.pending_browser is browser)
    )


def _attached_browser_pipeline_is_current(coordinator, state) -> bool:
    if state is None or state is not coordinator._active_browser_pipeline:
        return False
    window = coordinator.window
    browser = state.get("browser")
    return bool(
        not window._closing
        and browser is not None
        and state.get("generation") == coordinator._browser_pipeline_generation
        and state.get("identity") == coordinator._current_load_identity()
        and window.browser is browser
        and coordinator.pending_browser is browser
    )


def _can_restart_latest_browser_pipeline(
    coordinator,
    state,
    *,
    attached_browser=None,
    epoch: int | None = None,
) -> bool:
    if state is None or state is not coordinator._active_browser_pipeline:
        return False
    window = coordinator.window
    if (
        window._closing
        or state.get("generation") != coordinator._browser_pipeline_generation
        or state.get("identity") == coordinator._current_load_identity()
    ):
        return False
    if attached_browser is not None:
        return bool(
            window.browser is attached_browser
            and int(getattr(window, "_browser_epoch", -1)) == epoch
        )
    browser = state.get("browser")
    return bool(
        window.browser is None
        and (browser is None or coordinator.pending_browser is browser)
    )


def _cancel_browser_pipeline(coordinator):
    pending_browser, coordinator.pending_browser = coordinator.pending_browser, None
    pending_page, coordinator.pending_page = coordinator.pending_page, None
    pending_shell = _take_pending_shell_load(coordinator)
    if pending_page is None and pending_shell is not None:
        pending_page = pending_shell[2]
    coordinator._active_browser_pipeline = None
    coordinator._browser_pipeline_generation += 1
    for timer in (
        coordinator.browser_timer,
        coordinator.browser_create_timer,
        coordinator.page_handoff_timer,
        coordinator.browser_attach_timer,
        coordinator.browser_activate_timer,
        coordinator.shell_load_timer,
    ):
        with suppress(AttributeError, RuntimeError, TypeError):
            timer.stop()
    return pending_browser, pending_page


def _restart_browser_pipeline_with_latest_identity(coordinator, pending_page) -> bool:
    coordinator.pending_page = pending_page
    if coordinator.initialize_browser(staged=True):
        return True
    coordinator.pending_page = None
    return False


def _abort_stale_browser_pipeline(coordinator, state) -> None:
    restart_latest = _can_restart_latest_browser_pipeline(coordinator, state)
    pending_browser, pending_page = _cancel_browser_pipeline(coordinator)
    coordinator._rollback_browser_attachment(pending_browser)
    if restart_latest and _restart_browser_pipeline_with_latest_identity(
        coordinator,
        pending_page,
    ):
        return
    _release_or_dispose_pending_page(pending_page)


def _fail_pre_shell_pipeline(coordinator, exc: Exception) -> None:
    state = coordinator._active_browser_pipeline
    browser = None if state is None else state.get("browser")
    browser = browser or coordinator.pending_browser
    coordinator._rollback_browser_attachment(browser)
    coordinator._handle_browser_failure(exc)


def _create_browser_slice(coordinator) -> None:
    state = coordinator._active_browser_pipeline
    if not _browser_pipeline_is_current(coordinator, state):
        _abort_stale_browser_pipeline(coordinator, state)
        return
    marks = state["marks"]
    marks["create_start"] = time.perf_counter()
    try:
        browser = coordinator.pending_browser or coordinator.browser_factory(
            coordinator.window.chart_host
        )
        if browser is None:
            raise RuntimeError("browser factory returned no browser")
        coordinator.pending_browser = browser
        state["browser"] = browser
    except _EXPECTED_STAGE_ERRORS as exc:
        marks["create_ready"] = time.perf_counter()
        _fail_pre_shell_pipeline(coordinator, exc)
        return
    marks["create_ready"] = time.perf_counter()
    coordinator.page_handoff_timer.start(_BROWSER_PIPELINE_SLICE_MS)


def _handoff_or_skip_page_slice(coordinator) -> None:
    state = coordinator._active_browser_pipeline
    if not _browser_pipeline_is_current(coordinator, state):
        _abort_stale_browser_pipeline(coordinator, state)
        return
    browser = state["browser"]
    marks = state["marks"]
    marks["handoff_start"] = time.perf_counter()
    try:
        coordinator._page_handoff_ms = 0.0
        if coordinator.pending_page is not None:
            coordinator._page_handoff_ms = _handoff_prewarmed_page(
                browser, coordinator.pending_page
            )
    except _EXPECTED_STAGE_ERRORS as exc:
        marks["handoff_ready"] = time.perf_counter()
        _fail_pre_shell_pipeline(coordinator, exc)
        return
    marks["handoff_ready"] = time.perf_counter()
    coordinator.browser_attach_timer.start(_BROWSER_PIPELINE_SLICE_MS)


def _attach_browser_slice(coordinator) -> None:
    state = coordinator._active_browser_pipeline
    if not _browser_pipeline_is_current(coordinator, state):
        _abort_stale_browser_pipeline(coordinator, state)
        return
    browser = state["browser"]
    try:
        coordinator.attach_browser_hierarchy(browser)
        if coordinator.window.browser is not browser:
            raise RuntimeError("browser attach did not commit")
    except _EXPECTED_STAGE_ERRORS as exc:
        state["marks"].setdefault("attach_ready", time.perf_counter())
        _fail_pre_shell_pipeline(coordinator, exc)
        return
    coordinator.browser_activate_timer.start(_BROWSER_PIPELINE_SLICE_MS)


def _activate_browser_slice(coordinator) -> None:
    state = coordinator._active_browser_pipeline
    if not _attached_browser_pipeline_is_current(coordinator, state):
        browser = None if state is None else state.get("browser")
        epoch = int(getattr(coordinator.window, "_browser_epoch", -1))
        _abort_attached_browser_pipeline(
            coordinator,
            browser,
            coordinator.pending_page,
            state=state,
            epoch=epoch,
        )
        return
    browser = state["browser"]
    try:
        coordinator.activate_browser_runtime(browser)
    except _EXPECTED_STAGE_ERRORS as exc:
        state["marks"].setdefault("attach_ready", time.perf_counter())
        _fail_pre_shell_pipeline(coordinator, exc)


def _abort_attached_browser_pipeline(
    coordinator,
    browser,
    handed_off_page,
    *,
    state,
    epoch: int,
) -> None:
    restart_latest = _can_restart_latest_browser_pipeline(
        coordinator,
        state,
        attached_browser=browser,
        epoch=epoch,
    )
    coordinator._active_browser_pipeline = None
    coordinator._browser_pipeline_generation += 1
    if coordinator.pending_browser is browser:
        coordinator.pending_browser = None
    coordinator._rollback_browser_attachment(browser)
    if restart_latest and _restart_browser_pipeline_with_latest_identity(
        coordinator,
        handed_off_page,
    ):
        return
    _release_or_dispose_pending_page(handed_off_page)


def _resolve_deferred_window_context(window) -> tuple[bool, float]:
    started_at = time.perf_counter()
    context_was_prebuilt = bool(getattr(window, "_open_context_resolved", False))
    if not context_was_prebuilt:
        try:
            window.vcp_data = window._resolve_vcp_context(window.code, window.name, window.vcp_data)
            window._open_context_resolved = True
        except _EXPECTED_STAGE_ERRORS as exc:
            window._log.debug(f"[K线] 延迟补全上下文失败: {exc}")
    return context_was_prebuilt, (time.perf_counter() - started_at) * 1000.0


def _refresh_deferred_window_header(window) -> float:
    started_at = time.perf_counter()
    window._refresh_header_context()
    return (time.perf_counter() - started_at) * 1000.0


def _record_deferred_context_diagnostics(
    coordinator,
    window,
    *,
    context_was_prebuilt: bool,
    context_resolve_ms: float,
    header_refresh_ms: float,
) -> None:
    window._context_diagnostics = {
        "prebuilt": context_was_prebuilt,
        "context_resolve_ms": round(context_resolve_ms, 3),
        "header_refresh_ms": round(header_refresh_ms, 3),
        "total_ms": round(context_resolve_ms + header_refresh_ms, 3),
    }
    tags = {"code": str(window.code or "").strip()}
    with suppress(*_EXPECTED_STAGE_ERRORS):
        coordinator.record_metric("kline_context_resolve_ms", context_resolve_ms, unit="ms", tags=tags)
        coordinator.record_metric("kline_header_refresh_ms", header_refresh_ms, unit="ms", tags=tags)


def _resume_deferred_context_pipeline(coordinator, window) -> None:
    if window.browser is None:
        coordinator.browser_timer.start(coordinator.browser_delay_ms)
        return
    if coordinator._pending_shell_load is None:
        coordinator.record("browser_ready")
        if bool(getattr(window, "_shell_loaded", False)) and bool(
            _object_property(window.browser, "klineShellReady", False)
        ):
            coordinator.record("js_ready")


def _initialize_stage_timers(coordinator, window) -> None:
    coordinator.context_timer = coordinator._new_timer(coordinator.finish_deferred_context)
    coordinator.browser_timer = coordinator._new_timer(
        lambda: coordinator.initialize_browser(staged=True)
    )
    coordinator.browser_create_timer = coordinator._new_timer(lambda: _create_browser_slice(coordinator))
    coordinator.page_handoff_timer = coordinator._new_timer(
        lambda: _handoff_or_skip_page_slice(coordinator)
    )
    coordinator.browser_attach_timer = coordinator._new_timer(lambda: _attach_browser_slice(coordinator))
    coordinator.browser_activate_timer = coordinator._new_timer(lambda: _activate_browser_slice(coordinator))
    coordinator.shell_load_timer = coordinator._new_timer(coordinator._dispatch_shell_load)
    coordinator.initial_load_timer = coordinator._new_timer(window._load_and_draw)
    timer_bindings = {
        "_context_init_timer": "context_timer",
        "_browser_init_timer": "browser_timer",
        "_browser_create_timer": "browser_create_timer",
        "_page_handoff_timer": "page_handoff_timer",
        "_browser_attach_timer": "browser_attach_timer",
        "_browser_activate_timer": "browser_activate_timer",
        "_shell_load_timer": "shell_load_timer",
        "_initial_load_timer": "initial_load_timer",
    }
    for window_name, coordinator_name in timer_bindings.items():
        setattr(window, window_name, getattr(coordinator, coordinator_name))
    window._context_ready = False
    window._load_requested = False
    window.browser = None


class _KLineOpenStageControls:
    def _new_timer(self, callback) -> QTimer:
        timer = QTimer(self.window)
        timer.setSingleShot(True)
        timer.setTimerType(Qt.TimerType.PreciseTimer)
        timer.timeout.connect(callback)
        return timer

    def start(self, browser=None, browser_page=None) -> None:
        self.pending_browser = browser
        self.pending_page = browser_page
        self.context_timer.start(0)

    def stage_diagnostics(self) -> dict:
        return self._stage_timeline.diagnostics()


class KLineOpenStageCoordinator(_KLineOpenStageControls):
    """让轻量壳、WebEngine 与首次绘图分属不同事件循环切片。"""

    def __init__(
        self,
        window,
        *,
        open_started_at: float | None,
        browser_factory,
        record_metric,
        emit_structured_log,
        browser_delay_ms: int,
        initial_load_delay_ms: int,
        defer_initial_load: bool = False,
    ) -> None:
        self.window = window
        self.open_started_at = float(open_started_at) if open_started_at is not None else time.perf_counter()
        self.browser_factory = browser_factory
        self.record_metric = record_metric
        self.emit_structured_log = emit_structured_log
        self.browser_delay_ms = int(browser_delay_ms)
        self.initial_load_delay_ms = int(initial_load_delay_ms)
        self.defer_initial_load = bool(defer_initial_load)
        self.recorded_stages = set()
        self._stage_timeline = KlineStageTimeline()
        self._initial_load_scheduled = False
        self._last_load_identity = None
        self.pending_browser = None
        self.pending_page = None
        self._pending_shell_load = None
        self._page_handoff_ms = 0.0
        self._browser_pipeline_generation = 0
        self._active_browser_pipeline = None
        _initialize_stage_timers(self, window)

    def record(self, stage: str) -> tuple[str, ...]:
        elapsed_ms = max(0.0, (time.perf_counter() - self.open_started_at) * 1000.0)
        committed = self._stage_timeline.mark(stage, elapsed_ms=elapsed_ms)
        code = str(self.window.code or "").strip()
        diagnostics = self._stage_timeline.diagnostics()
        for committed_stage in committed:
            self.recorded_stages.add(committed_stage)
            committed_elapsed = diagnostics["timings_ms"][committed_stage]
            observed_elapsed = diagnostics["observed_timings_ms"][committed_stage]
            with suppress(*_EXPECTED_STAGE_ERRORS):
                self.record_metric(
                    _STAGE_METRICS[committed_stage],
                    committed_elapsed,
                    unit="ms",
                    tags={"code": code},
                )
                self.emit_structured_log(
                    "kline.open_stage",
                    stage=committed_stage,
                    code=code,
                    elapsed_ms=round(committed_elapsed, 3),
                    observed_elapsed_ms=round(observed_elapsed, 3),
                )
        return committed

    def finish_deferred_context(self) -> None:
        window = self.window
        if window._closing or window._context_ready:
            return
        window._context_ready = True
        context_was_prebuilt, context_resolve_ms = _resolve_deferred_window_context(window)
        header_refresh_ms = _refresh_deferred_window_header(window)
        _record_deferred_context_diagnostics(
            self,
            window,
            context_was_prebuilt=context_was_prebuilt,
            context_resolve_ms=context_resolve_ms,
            header_refresh_ms=header_refresh_ms,
        )
        _resume_deferred_context_pipeline(self, window)
        self.schedule_initial_load()

    def initialize_browser(self, *, staged: bool = False) -> bool:
        """Queue the production pipeline, with a synchronous compatibility entry for tests/tools."""
        window = self.window
        if (
            window._closing
            or window.browser is not None
            or self._active_browser_pipeline is not None
        ):
            return False
        self._browser_pipeline_generation += 1
        started_at = time.perf_counter()
        self._active_browser_pipeline = {
            "generation": self._browser_pipeline_generation,
            "identity": self._current_load_identity(),
            "browser": self.pending_browser,
            "marks": {
                "pipeline_started": started_at,
                "started": started_at,
            },
        }
        self.browser_create_timer.start(_BROWSER_PIPELINE_SLICE_MS)
        if not staged:
            for timer, callback in (
                (self.browser_create_timer, lambda: _create_browser_slice(self)),
                (self.page_handoff_timer, lambda: _handoff_or_skip_page_slice(self)),
                (self.browser_attach_timer, lambda: _attach_browser_slice(self)),
                (self.browser_activate_timer, lambda: _activate_browser_slice(self)),
                (self.shell_load_timer, self._dispatch_shell_load),
            ):
                timer.stop()
                callback()
        return True

    def _rollback_browser_attachment(self, browser) -> None:
        if browser is None:
            return
        window = self.window
        uninstall_render_process_recovery(browser)
        if getattr(window, "browser", None) is browser:
            window.browser = None
        with suppress(*_EXPECTED_STAGE_ERRORS):
            browser.loadFinished.disconnect(window._on_chart_load_finished)
        with suppress(*_EXPECTED_STAGE_ERRORS):
            browser.removeEventFilter(window)
            focus_proxy = browser.focusProxy()
            if focus_proxy is not None:
                focus_proxy.removeEventFilter(window)
        with suppress(*_EXPECTED_STAGE_ERRORS):
            window.chart_host_layout.removeWidget(browser)
        with suppress(*_EXPECTED_STAGE_ERRORS):
            browser.stop()
        with suppress(*_EXPECTED_STAGE_ERRORS):
            browser.setUpdatesEnabled(False)
            browser.hide()
        with suppress(*_EXPECTED_STAGE_ERRORS):
            browser.deleteLater()

    def _handle_browser_failure(self, exc: Exception) -> None:
        window = self.window
        _pending_browser, pending_page = _cancel_browser_pipeline(self)
        _release_or_dispose_pending_page(pending_page)
        window._set_status_message("图表组件初始化失败，请重试", tone="error")
        if getattr(window, "chart_placeholder", None) is not None:
            window.chart_placeholder.setText("图表组件初始化失败")
        with suppress(*_EXPECTED_STAGE_ERRORS):
            self.emit_structured_log(
                "kline.browser_init_failed",
                code=str(window.code or "").strip(),
                error=str(exc),
            )

    def schedule_shell_load(self, browser, marks: dict[str, float]) -> None:
        """Dispatch setHtml in its own event-loop slice for the current browser epoch."""
        window = self.window
        now = time.perf_counter()
        pipeline_started = marks.setdefault("pipeline_started", marks.get("started", now))
        marks.setdefault("started", pipeline_started)
        for start_name, ready_name in (
            ("create_start", "create_ready"),
            ("handoff_start", "handoff_ready"),
        ):
            start = marks.setdefault(start_name, now)
            marks.setdefault(ready_name, start)
        attach_start = marks.setdefault("attach_start", now)
        hierarchy_ready = marks.setdefault("hierarchy_ready", attach_start)
        activation_start = marks.setdefault("activation_start", hierarchy_ready)
        marks.setdefault("visible", activation_start)
        marks.setdefault("recovery", marks["visible"])
        marks.setdefault("theme", marks["recovery"])
        marks.setdefault("attach_ready", marks["theme"])
        handed_off_page, self.pending_page = self.pending_page, None
        state = self._active_browser_pipeline
        if state is None:
            self._browser_pipeline_generation += 1
            state = {
                "generation": self._browser_pipeline_generation,
                "identity": self._current_load_identity(),
                "browser": browser,
                "marks": marks,
            }
            self._active_browser_pipeline = state
        self._pending_shell_load = (
            browser,
            int(getattr(window, "_browser_epoch", 0) or 0),
            handed_off_page,
            marks,
            None if state is None else state.get("generation"),
            self._current_load_identity() if state is None else state.get("identity"),
        )
        self.shell_load_timer.start(_BROWSER_PIPELINE_SLICE_MS)

    def _dispatch_shell_load(self) -> None:
        pending, self._pending_shell_load = self._pending_shell_load, None
        if pending is None:
            return
        browser, epoch, handed_off_page, marks, generation, identity = pending
        window = self.window
        if (
            window._closing
            or window.browser is not browser
            or int(getattr(window, "_browser_epoch", -1)) != epoch
            or generation != self._browser_pipeline_generation
            or identity != self._current_load_identity()
        ):
            _abort_attached_browser_pipeline(
                self,
                browser,
                handed_off_page,
                state=self._active_browser_pipeline,
                epoch=epoch,
            )
            return
        marks["load_start"] = time.perf_counter()
        try:
            load_shell = getattr(window, "_load_chart_shell", None)
            loaded = not callable(load_shell) or load_shell() is not False
        except _EXPECTED_STAGE_ERRORS as exc:
            marks["load_ready"] = time.perf_counter()
            self._fail_shell_load(browser, handed_off_page, marks, exc)
            return
        marks["load_ready"] = time.perf_counter()
        if not loaded:
            self._fail_shell_load(
                browser,
                handed_off_page,
                marks,
                RuntimeError("chart shell load was rejected"),
            )
            return
        self._finish_shell_load(
            browser,
            epoch,
            handed_off_page,
            marks,
            generation=generation,
            identity=identity,
        )

    def _finish_shell_load(
        self,
        browser,
        epoch: int,
        handed_off_page,
        marks: dict[str, float],
        *,
        generation: int | None,
        identity,
    ) -> None:
        window = self.window
        if (
            window._closing
            or window.browser is not browser
            or int(getattr(window, "_browser_epoch", -1)) != epoch
            or generation != self._browser_pipeline_generation
            or identity != self._current_load_identity()
        ):
            _abort_attached_browser_pipeline(
                self,
                browser,
                handed_off_page,
                state=self._active_browser_pipeline,
                epoch=epoch,
            )
            return
        marks["placeholder"] = _discard_chart_placeholder(window)
        self.record("browser_ready")
        marks["stage"] = time.perf_counter()
        window._browser_attach_diagnostics = _browser_attach_diagnostics(self, browser, marks)
        self._active_browser_pipeline = None
        _emit_browser_attached(self, window, browser)

    def _fail_shell_load(self, browser, handed_off_page, marks, exc: Exception) -> None:
        self.window._browser_attach_diagnostics = _failed_browser_attach_diagnostics(self, marks)
        self.pending_page = handed_off_page
        self._rollback_browser_attachment(browser)
        self._handle_browser_failure(exc)

    def _browser_pipeline_state(self, browser):
        window = self.window
        if window._closing or browser is None or window.browser is not None:
            return None
        state = self._active_browser_pipeline
        if state is None or state.get("browser") is not browser:
            self._browser_pipeline_generation += 1
            started_at = time.perf_counter()
            state = {
                "generation": self._browser_pipeline_generation,
                "identity": self._current_load_identity(),
                "browser": browser,
                "marks": {
                    "pipeline_started": started_at,
                    "started": started_at,
                    "create_start": started_at,
                    "create_ready": started_at,
                    "handoff_start": started_at,
                    "handoff_ready": started_at,
                },
            }
            self._active_browser_pipeline = state
            self.pending_browser = browser
        return state

    def attach_browser_hierarchy(self, browser) -> None:
        state = self._browser_pipeline_state(browser)
        if state is None:
            return
        window = self.window
        marks = state["marks"]
        marks["attach_start"] = time.perf_counter()
        _attach_browser_hierarchy(window, browser, marks=marks)

    def activate_browser_runtime(self, browser) -> None:
        window = self.window
        state = self._active_browser_pipeline
        if (
            state is None
            or state.get("browser") is not browser
            or window.browser is not browser
        ):
            return
        marks = state["marks"]
        marks["activation_start"] = time.perf_counter()
        _activate_browser_runtime(self, window, browser, marks)

    def attach_browser(self, browser) -> None:
        """Synchronous compatibility entry for tests and recovery tools."""
        self.attach_browser_hierarchy(browser)
        if self.window.browser is browser:
            self.activate_browser_runtime(browser)

    def recover_browser(self, failed_browser) -> bool:
        window = self.window
        if window._closing or window.browser is not failed_browser:
            return False
        pending_browser, pending_page = _cancel_browser_pipeline(self)
        if pending_browser is not None and pending_browser is not failed_browser:
            self._rollback_browser_attachment(pending_browser)
        _release_or_dispose_pending_page(pending_page)
        uninstall_render_process_recovery(failed_browser)
        with suppress(AttributeError, RuntimeError, TypeError):
            failed_browser.stop()
        with suppress(AttributeError, RuntimeError, TypeError):
            window.chart_host_layout.removeWidget(failed_browser)
        with suppress(AttributeError, RuntimeError, TypeError):
            failed_browser.hide()
        with suppress(AttributeError, RuntimeError, TypeError):
            failed_browser.deleteLater()
        window.browser = None
        self.pending_browser = None
        self.pending_page = None
        return self.initialize_browser(staged=True)

    def schedule_initial_load(self) -> None:
        window = self.window
        if window._closing or self._initial_load_scheduled or self.defer_initial_load:
            return
        self._initial_load_scheduled = True
        self.initial_load_timer.start(self.initial_load_delay_ms)

    def _current_load_identity(self):
        controller = getattr(self.window, "_load_controller", None)
        identity = getattr(controller, "current_identity", None)
        if identity is not None:
            return identity
        return (
            str(getattr(self.window, "code", "") or "").strip(),
            int(getattr(self.window, "_render_generation", 0) or 0),
        )

    def begin_chart_load(self) -> bool:
        window = self.window
        if window._closing:
            return False
        identity = self._current_load_identity()
        if identity == self._last_load_identity:
            return False
        self._last_load_identity = identity
        window._load_requested = True
        return True

    def stop(self):
        pending_browser, pending_page = _cancel_browser_pipeline(self)
        for timer in (self.context_timer, self.initial_load_timer):
            with suppress(AttributeError, RuntimeError, TypeError):
                timer.stop()
        return pending_browser, pending_page

    def reset_for_lease(self, open_started_at: float | None) -> None:
        """Reuse the coordinator and its timers for a new physical-window lease."""
        pending_browser, pending_page = self.stop()
        self._rollback_browser_attachment(pending_browser)
        _release_or_dispose_pending_page(pending_page)
        self.open_started_at = (
            float(open_started_at) if open_started_at is not None else time.perf_counter()
        )
        self.recorded_stages.clear()
        self._stage_timeline = KlineStageTimeline()
        self._initial_load_scheduled = False
        self._last_load_identity = None
        self._page_handoff_ms = 0.0
        self.defer_initial_load = False
        self.window._context_ready = False
        self.window._load_requested = False
        self.context_timer.start(0)


def can_begin_chart_load(window) -> bool:
    stages = getattr(window, "_open_stages", None)
    if stages is not None:
        return stages.begin_chart_load()
    if getattr(window, "_closing", False):
        return False
    return True
