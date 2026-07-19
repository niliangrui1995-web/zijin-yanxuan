# -*- coding: utf-8 -*-
"""Latest-only render hand-off for K-line windows."""

from __future__ import annotations

import time
from collections.abc import Mapping
from contextlib import suppress

from PyQt6.QtCore import QTimer, QUrl

from core.observability import emit_structured_log
from ui.kline_chart_payload import build_kline_html
from ui.kline_render_bridge import (
    build_apply_snapshot_script,
    build_snapshot_render_state_script,
    prepared_matches_current_load,
    snapshot_ack_is_queued,
    snapshot_ack_matches,
    snapshot_render_ack_is_pending,
    snapshot_render_ack_matches,
)

_EXPECTED_RENDER_ERRORS = (AttributeError, RuntimeError, TypeError, ValueError)
_SHELL_READY_PROPERTY = "klineShellReady"
_SHELL_HTML_BYTES_PROPERTY = "klineShellHtmlBytes"
_RENDER_STATE_POLL_MS = 8
_RENDER_STATE_TIMEOUT_MS = 2_000


def _browser_property(browser, name: str, default=None):
    try:
        value = browser.property(name)
    except _EXPECTED_RENDER_ERRORS:
        value = getattr(browser, f"_{name}", default)
    return default if value is None else value


def _confirm_reused_shell(window, browser, epoch: int) -> None:
    if (
        getattr(window, "_closing", False)
        or getattr(window, "browser", None) is not browser
        or int(getattr(window, "_browser_epoch", -1)) != int(epoch)
    ):
        return
    window._on_chart_load_finished(True)


def load_chart_shell(
    window,
    *,
    echarts_js_path: str,
    shell_builder,
    theme_colors: dict,
) -> bool:
    """Load the same data-free HTML shell for initial attach and one recovery."""
    browser = getattr(window, "browser", None)
    if getattr(window, "_closing", False) or browser is None:
        return False
    base_path = str(echarts_js_path).replace("\\", "/").rsplit("/", 1)[0] + "/"
    base_url = QUrl.fromLocalFile(base_path)
    window._chart_base_url = base_url
    window._chart_echarts_js_path = str(echarts_js_path)
    window._chart_theme_colors = dict(theme_colors)
    window._shell_loaded = False
    if bool(_browser_property(browser, _SHELL_READY_PROPERTY, False)):
        window._last_chart_html_bytes = int(
            _browser_property(browser, _SHELL_HTML_BYTES_PROPERTY, 0) or 0
        )
        epoch = int(getattr(window, "_browser_epoch", 0) or 0)
        QTimer.singleShot(0, lambda: _confirm_reused_shell(window, browser, epoch))
        return True
    html = getattr(window, "_chart_shell_html", None)
    if html is None:
        html = shell_builder(
            title="K线",
            echarts_js_path=echarts_js_path,
            theme_colors=theme_colors,
        )
        window._chart_shell_html = html
    window._last_chart_html_bytes = len(html.encode("utf-8"))
    try:
        browser.setHtml(html, base_url)
    except _EXPECTED_RENDER_ERRORS:
        return False
    return True


def _snapshot_matches_current(window, snapshot) -> bool:
    current = window._load_controller.current_identity
    return bool(
        current is not None
        and snapshot is not None
        and snapshot.window_id == current.window_id
        and snapshot.generation == current.generation
        and snapshot.code == current.code
    )


def _record_snapshot(window, prepared):
    return window._runtime_lifecycle.record_snapshot_json(
        prepared.payload_json,
        window_id=prepared.owner_id,
        code=prepared.code,
        generation=prepared.generation,
        points=prepared.point_count,
        version=prepared.snapshot_version,
    )


def _prepared_frames(prepared):
    try:
        return prepared.take_owned_frames()
    except (AttributeError, RuntimeError, TypeError, ValueError):
        return None


def _cancel_superseded_render(window, snapshot) -> None:
    inflight = getattr(window, "_snapshot_inflight", None)
    if (
        inflight is not None
        and snapshot.window_id == inflight.window_id
        and snapshot.generation == inflight.generation
        and snapshot.version > inflight.version
    ):
        cancel_snapshot_render_confirmation(window)


def _store_prepared_render(window, prepared, snapshot, frames) -> None:
    display_frame, history_frame = frames
    window._snapshot_version = prepared.snapshot_version
    window._pending_frame = (prepared.snapshot_version, display_frame, history_frame)
    window._pending_prepared_render = prepared
    window._last_prepared_render = prepared
    window._last_chart_payload_bytes = len(prepared.payload_json.encode("utf-8"))
    window._last_chart_points = prepared.point_count


def _set_prepared_render_status(window, prepared, *, loading: bool) -> None:
    if prepared.source == "realtime":
        window._set_status_message("正在同步实时行情与指标...", tone="loading")
        window._set_pending_chart_status("实时行情与 MA / VOL-MA20 / MACD 已同步", "realtime")
    elif loading:
        window._set_status_message(f"正在绘制本地缓存 · {prepared.point_count} 条日线", tone="loading")
        window._set_pending_chart_status(f"已载入本地缓存 · {prepared.point_count} 条日线", "info")
    else:
        window._set_status_message(f"正在绘制图表 · {prepared.point_count} 条日线", tone="loading")
        window._set_pending_chart_status(f"图表已更新 · {prepared.point_count} 条日线", "success")


def _record_data_ready(window) -> None:
    stages = getattr(window, "_open_stages", None)
    if stages is not None:
        stages.record("data_ready")


def _emit_render_handoff(
    window, prepared, *, started_at, frame_copy_ms, snapshot_ms, submit_ms, submitted: bool
) -> None:
    emit_structured_log(
        "kline.render_handoff",
        code=prepared.code,
        generation=prepared.generation,
        payload_bytes=window._last_chart_payload_bytes,
        frame_copy_ms=round(frame_copy_ms, 3),
        snapshot_ms=round(snapshot_ms, 3),
        submit_ms=round(submit_ms, 3),
        total_ms=round((time.perf_counter() - started_at) * 1000.0, 3),
        submitted=bool(submitted),
    )


def queue_prepared_render(window, prepared, *, loading: bool) -> bool:
    """Accept only the current generation and retain just its latest complete frame."""
    started_at = time.perf_counter()
    if getattr(window, "_closing", False) or not prepared_matches_current_load(window._load_controller, prepared):
        return False
    if prepared.snapshot_version < int(getattr(window, "_snapshot_version", 0) or 0):
        return False
    frame_copy_started_at = time.perf_counter()
    frames = _prepared_frames(prepared)
    if frames is None:
        return False
    frame_copy_ms = (time.perf_counter() - frame_copy_started_at) * 1000.0
    snapshot_started_at = time.perf_counter()
    snapshot = _record_snapshot(window, prepared)
    if snapshot is None:
        return False
    _cancel_superseded_render(window, snapshot)
    snapshot_ms = (time.perf_counter() - snapshot_started_at) * 1000.0
    _store_prepared_render(window, prepared, snapshot, frames)
    _set_prepared_render_status(window, prepared, loading=loading)
    _record_data_ready(window)
    submit_started_at = time.perf_counter()
    submitted = submit_pending_snapshot(window)
    submit_ms = (time.perf_counter() - submit_started_at) * 1000.0
    _emit_render_handoff(
        window, prepared, started_at=started_at, frame_copy_ms=frame_copy_ms,
        snapshot_ms=snapshot_ms, submit_ms=submit_ms, submitted=submitted,
    )
    return True


def requeue_snapshot(window, snapshot) -> None:
    if not _snapshot_matches_current(window, snapshot):
        return
    window._runtime_lifecycle.record_snapshot_json(
        snapshot.payload_json,
        window_id=snapshot.window_id,
        code=snapshot.code,
        generation=snapshot.generation,
        points=snapshot.points,
        version=snapshot.version,
    )


def _fallback_key(window, snapshot) -> tuple:
    return (
        int(getattr(window, "_browser_epoch", 0) or 0),
        snapshot.window_id,
        snapshot.generation,
        snapshot.code,
        snapshot.version,
    )


def load_controlled_fallback_page(window, snapshot) -> bool:
    """Build one full HTML page only after the current JS submission fails."""
    if getattr(window, "_closing", False) or not _snapshot_matches_current(window, snapshot):
        return False
    browser = getattr(window, "browser", None)
    fallback_key = _fallback_key(window, snapshot)
    if browser is None or getattr(window, "_fallback_snapshot_key", None) == fallback_key:
        return False
    window._fallback_snapshot_key = fallback_key
    try:
        payload = snapshot.payload()
        data = payload.get("data")
        if not isinstance(data, Mapping):
            return False
        title = str(payload.get("title") or data.get("title") or "K线")
        html = build_kline_html(
            title,
            dict(data),
            str(window._chart_echarts_js_path),
            dict(window._chart_theme_colors),
        )
        browser.setHtml(html, window._chart_base_url)
    except _EXPECTED_RENDER_ERRORS:
        return False
    window._shell_loaded = False
    window._last_chart_html_bytes = len(html.encode("utf-8"))
    requeue_snapshot(window, snapshot)
    window._set_status_message("图表脚本提交失败，已切换到受控回退页面", tone="warning")
    return True


def _snapshot_submission_ready(window) -> bool:
    return not (
        getattr(window, "_closing", False)
        or not getattr(window, "_shell_loaded", False)
        or getattr(window, "browser", None) is None
        or getattr(window, "_snapshot_inflight", None) is not None
    )


def _begin_snapshot_confirmation(window, snapshot, browser, epoch: int) -> bool:
    window._snapshot_inflight = snapshot
    window._snapshot_inflight_browser = browser
    window._snapshot_inflight_epoch = epoch
    window._snapshot_render_query_pending = False
    window._snapshot_render_deadline = time.monotonic() + (_RENDER_STATE_TIMEOUT_MS / 1000.0)
    return _start_snapshot_render_watchdog(window)


def _fallback_after_submission_failure(window, snapshot, message: str) -> None:
    _clear_inflight_snapshot(window)
    if not load_controlled_fallback_page(window, snapshot):
        window._set_status_message(message, tone="error")


def _run_snapshot_script(window, snapshot, browser, epoch: int):
    script_started_at = time.perf_counter()
    script = build_apply_snapshot_script(snapshot.payload_json)
    script_build_ms = (time.perf_counter() - script_started_at) * 1000.0
    submit_started_at = time.perf_counter()
    try:
        browser.page().runJavaScript(
            script,
            lambda ack, owned=snapshot, owned_browser=browser, owned_epoch=epoch: handle_snapshot_ack(
                window, owned, ack, browser=owned_browser, epoch=owned_epoch
            ),
        )
    except _EXPECTED_RENDER_ERRORS:
        return None
    return script_build_ms, (time.perf_counter() - submit_started_at) * 1000.0


def _emit_snapshot_submitted(snapshot, *, script_build_ms: float, run_javascript_ms: float) -> None:
    emit_structured_log(
        "kline.snapshot_submitted",
        code=snapshot.code,
        generation=snapshot.generation,
        version=snapshot.version,
        payload_bytes=len(snapshot.payload_json.encode("utf-8")),
        script_build_ms=round(script_build_ms, 3),
        run_javascript_ms=round(run_javascript_ms, 3),
    )


def submit_pending_snapshot(window, snapshot=None) -> bool:
    if not _snapshot_submission_ready(window):
        return False
    snapshot = snapshot or window._runtime_lifecycle.take_pending_submission()
    if not _snapshot_matches_current(window, snapshot):
        return False
    browser = window.browser
    epoch = int(getattr(window, "_browser_epoch", 0) or 0)
    if not _begin_snapshot_confirmation(window, snapshot, browser, epoch):
        _fallback_after_submission_failure(window, snapshot, "图表渲染计时器启动失败，请重试")
        return False
    metrics = _run_snapshot_script(window, snapshot, browser, epoch)
    if metrics is None:
        _fallback_after_submission_failure(window, snapshot, "图表快照提交失败，请重试")
        return False
    _emit_snapshot_submitted(snapshot, script_build_ms=metrics[0], run_javascript_ms=metrics[1])
    return True


def _commit_snapshot_frame(window, snapshot) -> bool:
    pending = getattr(window, "_pending_frame", None)
    current = window._load_controller.current_identity
    if pending is None or current is None or pending[0] != snapshot.version:
        return False
    if not window._load_controller.claim_frame(current):
        return False
    window.df = pending[1]
    window._history_frame = pending[2]
    window._pending_frame = None
    window._pending_prepared_render = None
    return True


def _ack_browser_is_current(window, browser, epoch) -> bool:
    try:
        return bool(
            getattr(window, "browser", None) is browser
            and int(getattr(window, "_browser_epoch", -1)) == int(epoch)
        )
    except (TypeError, ValueError):
        return False


def _clear_inflight_snapshot(window) -> None:
    for name in ("_render_commit_timer", "_render_watchdog_timer"):
        timer = getattr(window, name, None)
        if timer is not None:
            with suppress(*_EXPECTED_RENDER_ERRORS):
                timer.stop()
    window._snapshot_inflight = None
    window._snapshot_inflight_browser = None
    window._snapshot_inflight_epoch = None
    window._snapshot_render_query_pending = False
    window._snapshot_render_deadline = None


def cancel_snapshot_render_confirmation(window) -> None:
    """Cancel both JS acknowledgement phases without destroying reusable timers."""
    _clear_inflight_snapshot(window)


def _snapshot_is_latest_current(window, snapshot) -> bool:
    return bool(
        snapshot.version == getattr(window, "_snapshot_version", None)
        and _snapshot_matches_current(window, snapshot)
    )


def _finish_committed_snapshot(window) -> None:
    window._first_render_done = True
    window._finish_pending_chart_status()
    stages = getattr(window, "_open_stages", None)
    if stages is not None:
        stages.record("chart_ready")
    window._start_rt_timer()
    if getattr(window, "_latest_rt_quote", None) is not None:
        from ui.kline_window_runtime import resume_realtime_updates

        resume_realtime_updates(window)


def _render_commit_timer(window):
    timer = getattr(window, "_render_commit_timer", None)
    if timer is not None:
        return timer
    try:
        timer = QTimer(window)
    except TypeError:
        timer = QTimer()
    timer.setSingleShot(True)
    timer.timeout.connect(lambda: _query_inflight_snapshot_render_state(window))
    window._render_commit_timer = timer
    return timer


def _render_watchdog_timer(window):
    timer = getattr(window, "_render_watchdog_timer", None)
    if timer is not None:
        return timer
    try:
        timer = QTimer(window)
    except TypeError:
        timer = QTimer()
    timer.setSingleShot(True)
    timer.timeout.connect(lambda: _on_snapshot_render_watchdog(window))
    window._render_watchdog_timer = timer
    return timer


def _start_snapshot_render_watchdog(window) -> bool:
    deadline = getattr(window, "_snapshot_render_deadline", None)
    if deadline is None:
        return False
    remaining_ms = max(1, int((float(deadline) - time.monotonic()) * 1000.0))
    try:
        _render_watchdog_timer(window).start(remaining_ms)
    except _EXPECTED_RENDER_ERRORS:
        return False
    return True


def _on_snapshot_render_watchdog(window) -> None:
    snapshot = getattr(window, "_snapshot_inflight", None)
    deadline = getattr(window, "_snapshot_render_deadline", None)
    if snapshot is None or deadline is None:
        return
    if time.monotonic() < float(deadline):
        _start_snapshot_render_watchdog(window)
        return
    _fail_snapshot_render_confirmation(window, snapshot, "图表渲染确认超时，请重试")


def _fail_snapshot_render_confirmation(window, snapshot, message: str) -> None:
    _clear_inflight_snapshot(window)
    latest = _snapshot_is_latest_current(window, snapshot)
    if latest and not load_controlled_fallback_page(window, snapshot):
        window._set_status_message(message, tone="error")
    _submit_next_snapshot(window)


def _schedule_snapshot_render_poll(window, snapshot) -> bool:
    deadline = getattr(window, "_snapshot_render_deadline", None)
    if deadline is None or time.monotonic() >= float(deadline):
        _fail_snapshot_render_confirmation(window, snapshot, "图表渲染确认超时，请重试")
        return False
    try:
        _render_commit_timer(window).start(_RENDER_STATE_POLL_MS)
    except _EXPECTED_RENDER_ERRORS:
        _fail_snapshot_render_confirmation(window, snapshot, "图表渲染确认失败，请重试")
        return False
    return True


def _query_snapshot_render_state(window, snapshot, browser, epoch: int) -> bool:
    if getattr(window, "_snapshot_inflight", None) != snapshot:
        return False
    if not _ack_browser_is_current(window, browser, epoch):
        _clear_inflight_snapshot(window)
        _submit_next_snapshot(window)
        return False
    if not _snapshot_is_latest_current(window, snapshot):
        _clear_inflight_snapshot(window)
        _submit_next_snapshot(window)
        return False
    if bool(getattr(window, "_snapshot_render_query_pending", False)):
        return False
    window._snapshot_render_query_pending = True
    try:
        browser.page().runJavaScript(
            build_snapshot_render_state_script(snapshot),
            lambda ack, owned=snapshot, owned_browser=browser, owned_epoch=epoch: handle_snapshot_render_state(
                window,
                owned,
                ack,
                browser=owned_browser,
                epoch=owned_epoch,
            ),
        )
    except _EXPECTED_RENDER_ERRORS:
        window._snapshot_render_query_pending = False
        _fail_snapshot_render_confirmation(window, snapshot, "图表渲染状态读取失败，请重试")
        return False
    return True


def _query_inflight_snapshot_render_state(window) -> bool:
    snapshot = getattr(window, "_snapshot_inflight", None)
    browser = getattr(window, "_snapshot_inflight_browser", None)
    epoch = getattr(window, "_snapshot_inflight_epoch", None)
    if snapshot is None or browser is None or epoch is None:
        return False
    return _query_snapshot_render_state(window, snapshot, browser, int(epoch))


def _submit_next_snapshot(window) -> None:
    with suppress(*_EXPECTED_RENDER_ERRORS):
        submit_pending_snapshot(window)


def handle_snapshot_ack(window, snapshot, ack, *, browser=None, epoch: int | None = None) -> bool:
    if getattr(window, "_snapshot_inflight", None) != snapshot:
        return False
    owned_browser = browser or getattr(window, "_snapshot_inflight_browser", None)
    owned_epoch = epoch if epoch is not None else getattr(window, "_snapshot_inflight_epoch", None)
    if not _ack_browser_is_current(window, owned_browser, owned_epoch):
        return False
    if not _snapshot_is_latest_current(window, snapshot):
        _clear_inflight_snapshot(window)
        _submit_next_snapshot(window)
        return False
    if snapshot_ack_is_queued(snapshot, ack):
        _clear_inflight_snapshot(window)
        requeue_snapshot(window, snapshot)
        return False
    if not snapshot_ack_matches(snapshot, ack):
        _clear_inflight_snapshot(window)
        if not load_controlled_fallback_page(window, snapshot):
            window._set_status_message("图表快照应用失败，请重试", tone="error")
        _submit_next_snapshot(window)
        return False
    _query_snapshot_render_state(window, snapshot, owned_browser, int(owned_epoch))
    return False


def _accept_render_state_callback(window, snapshot, browser, epoch):
    if getattr(window, "_snapshot_inflight", None) != snapshot:
        return None
    owned_browser = browser or getattr(window, "_snapshot_inflight_browser", None)
    owned_epoch = epoch if epoch is not None else getattr(window, "_snapshot_inflight_epoch", None)
    inflight_browser = getattr(window, "_snapshot_inflight_browser", None)
    inflight_epoch = getattr(window, "_snapshot_inflight_epoch", None)
    if owned_browser is not inflight_browser or owned_epoch != inflight_epoch:
        return None
    window._snapshot_render_query_pending = False
    if not _ack_browser_is_current(window, owned_browser, owned_epoch):
        _clear_inflight_snapshot(window)
        _submit_next_snapshot(window)
        return None
    if not _snapshot_is_latest_current(window, snapshot):
        _clear_inflight_snapshot(window)
        _submit_next_snapshot(window)
        return None
    return owned_browser, owned_epoch


def _commit_rendered_snapshot(window, snapshot) -> bool:
    _clear_inflight_snapshot(window)
    committed = _commit_snapshot_frame(window, snapshot)
    if committed:
        _finish_committed_snapshot(window)
        emit_structured_log(
            "kline.snapshot_rendered",
            code=snapshot.code,
            generation=snapshot.generation,
            version=snapshot.version,
            points=snapshot.points,
        )
    elif not load_controlled_fallback_page(window, snapshot):
        window._set_status_message("图表快照应用失败，请重试", tone="error")
    _submit_next_snapshot(window)
    return committed


def handle_snapshot_render_state(
    window,
    snapshot,
    ack,
    *,
    browser=None,
    epoch: int | None = None,
) -> bool:
    if _accept_render_state_callback(window, snapshot, browser, epoch) is None:
        return False
    if snapshot_render_ack_is_pending(snapshot, ack):
        _schedule_snapshot_render_poll(window, snapshot)
        return False
    if not snapshot_render_ack_matches(snapshot, ack):
        _fail_snapshot_render_confirmation(window, snapshot, "图表渲染确认失败，请重试")
        return False
    return _commit_rendered_snapshot(window, snapshot)
