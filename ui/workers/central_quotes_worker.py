from __future__ import annotations

import re
import sys
import threading
import time
from collections.abc import Callable, Iterable
from functools import partial

from PyQt6.QtCore import QObject, QTimer, pyqtSlot

from app.services.central_quote_polling_service import CentralQuotePoller
from app.services.quote_runtime_state import QuoteRuntimeState, QuoteRuntimeStateCompatMixin
from app.services.ui_market_calendar_service import MarketCalendar
from app.services.ui_quote_service import (
    publish_rt_quotes,
    read_provider_health,
    read_realtime_quote_request_policy,
)
from app.services.ui_task_lifecycle_service import TaskLifecycleGroup, invoke_with_cancellation
from app.services.ui_task_service import CENTRAL_QUOTES_POLL, task_registry
from app.services.ui_task_service import (
    background_job_runner as task_manager,
)
from core.global_store import global_store
from core.logger import get_logger
from core.observability import emit_structured_log, record_metric

log = get_logger(__name__)
_A_SHARE_CODE_RE = re.compile(r"^\d{6}$")
_A_SHARE_POLL_INTERVAL_MS = 30000
_A_SHARE_FAILURE_COOLDOWN_SEC = 300
_A_SHARE_HEARTBEAT_INTERVAL_SEC = 60
_POST_CACHE_RELOAD_DEDUP_WINDOW_SEC = 32.0
_RECENT_SUCCESS_GRACE_SEC = max(120, _A_SHARE_HEARTBEAT_INTERVAL_SEC * 2)
_OPENING_WARMUP_STATUSES = frozenset({"开盘集合竞价", "开市前时段"})
_OPENING_WARMUP_FETCH_LIMIT = 40
_FALLBACK_PRESSURE_FETCH_LIMIT = 60
_FALLBACK_PRESSURE_RECENT_SEC = 90.0
_FALLBACK_PRESSURE_MIN_PENDING = 100
_FALLBACK_PRESSURE_MIN_ELAPSED_MS = 10000.0
_FALLBACK_PRESSURE_SKIP_CACHE_RATIO = 0.75
_FALLBACK_PRESSURE_SKIP_LOG_INTERVAL_SEC = 30.0
_FALLBACK_PRESSURE_SOURCE_TOKENS = ("sina", "tencent", "fallback", "offline", "stale")


def _provider_request_stats(provider) -> dict:
    snapshot = read_provider_health(provider)
    if snapshot.request_stats:
        return dict(snapshot.request_stats)
    stats_getter = getattr(provider, "get_quote_request_stats", None)
    if not callable(stats_getter):
        return {}
    try:
        stats = stats_getter() or {}
    except (AttributeError, RuntimeError, TypeError, ValueError):
        return {}
    return stats if isinstance(stats, dict) else {}


def _slow_fetch_threshold(provider, codes_count: int) -> float:
    request_policy = read_realtime_quote_request_policy(provider)
    expected_batches = max(1, (max(0, int(codes_count)) + request_policy.batch_size - 1) // request_policy.batch_size)
    return max(20.0, expected_batches * request_policy.api_call_timeout_sec + 4.0)


def _submit_central_task(service, name, fn, on_success, on_error, task_id, timeout_sec: float) -> None:
    service._task_lifecycle.run_background(
        name,
        fn,
        on_success=on_success,
        on_error=on_error,
        task_id=task_id,
        timeout_sec=timeout_sec,
        runner=task_manager,
    )


def _fetch_quote_payload_timed(
    service,
    codes: set[str],
    timing: dict[str, float],
    cancellation_token=None,
) -> dict:
    timing["worker_started_at"] = time.perf_counter()
    try:
        return invoke_with_cancellation(
            service._fetch_quote_payload,
            cancellation_token,
            codes,
        )
    finally:
        timing["worker_finished_at"] = time.perf_counter()


def _remember_pending_fetch(service, reason: str) -> None:
    if str(reason or "").strip() == "cache_reload":
        service.state.update(pending_reason="cache_reload")


def _schedule_pending_fetch_replay(service) -> None:
    if service._closed or not service.state.pending_reason or service._pending_fetch_timer.isActive():
        return
    service._pending_fetch_timer.start(0)


def _replay_pending_fetch(service) -> None:
    reason = str(service.state.pending_reason or "").strip()
    service.state.update(pending_reason="")
    if service._closed or not reason:
        return
    if reason == "cache_reload":
        service._off_market_snapshot_emitted = False
    service._trigger_fetch_for_reason(reason)


def _hold_fetch_while_inflight(service, reason: str, codes: set[str]) -> None:
    _remember_pending_fetch(service, reason)
    runtime = service.state.read()
    codes_count = int(runtime.codes_count or len(codes) or 0)
    slow_threshold = _slow_fetch_threshold(service.data_provider, codes_count)
    if (
        not runtime.warned_slow
        and runtime.started_at > 0
        and (time.time() - runtime.started_at) > slow_threshold
    ):
        service.state.update(warned_slow=True)
        log.warning(
            f"[报价站] 单次抓取耗时过长({time.time() - runtime.started_at:.1f}s)，"
            "继续等待当前单飞行任务结束"
        )


def _quote_phase_durations_ms(timing: dict[str, float], callback_started_at: float) -> dict[str, float]:
    submitted_at = float(timing.get("submitted_at") or callback_started_at)
    worker_started_at = float(timing.get("worker_started_at") or submitted_at)
    worker_finished_at = float(timing.get("worker_finished_at") or callback_started_at)
    return {
        "submit_queue_ms": max(0.0, (worker_started_at - submitted_at) * 1000.0),
        "worker_ms": max(0.0, (worker_finished_at - worker_started_at) * 1000.0),
        "result_queue_delay_ms": max(0.0, (callback_started_at - worker_finished_at) * 1000.0),
    }


def _record_and_publish_quote_refresh(
    service,
    *,
    codes: set[str],
    quotes: dict,
    has_valid: bool,
    provider_failed: bool,
    elapsed_ms: float,
    reason: str,
    timing: dict[str, float],
    callback_started_at: float,
) -> None:
    phase_ms = _quote_phase_durations_ms(timing, callback_started_at)
    tags = {"valid_quotes": str(bool(has_valid)).lower(), "reason": str(reason or "timer")}
    record_metric("quote_refresh_batch_size", len(codes), unit="count", tags=tags)
    record_metric("quote_refresh_ms", elapsed_ms, unit="ms", tags=tags)
    for metric_name, phase_key in (
        ("quote_submit_queue_ms", "submit_queue_ms"),
        ("quote_worker_ms", "worker_ms"),
        ("quote_result_queue_delay_ms", "result_queue_delay_ms"),
    ):
        record_metric(metric_name, phase_ms[phase_key], unit="ms", tags=tags)

    publish_started_at = time.perf_counter()
    if has_valid:
        service.publish_external_quotes(
            quotes,
            source="central_quotes.realtime",
            require_valid=True,
        )
    publish_ms = max(0.0, (time.perf_counter() - publish_started_at) * 1000.0)
    record_metric("quote_publish_ms", publish_ms, unit="ms", tags=tags)
    emit_structured_log(
        "quotes.refresh.completed",
        batch_size=len(codes),
        elapsed_ms=round(elapsed_ms, 3),
        valid_quotes=bool(has_valid),
        provider_failed=bool(provider_failed),
        publish_ms=round(publish_ms, 3),
        submit_queue_ms=round(phase_ms["submit_queue_ms"], 3),
        worker_ms=round(phase_ms["worker_ms"], 3),
        result_queue_delay_ms=round(phase_ms["result_queue_delay_ms"], 3),
    )


def _has_valid_quotes(quotes: dict) -> bool:
    return any(float(quote.get("close", 0) or 0) > 0 for quote in quotes.values())


def _has_live_quote_source(quotes: dict) -> bool:
    live_sources = {"eastmoney", "sina", "tencent"}
    return any(str(quote.get("source") or "").lower() in live_sources for quote in quotes.values())


def _provider_fetch_failed(provider_stats: dict, *, has_valid: bool, has_live_source: bool) -> bool:
    if has_live_source:
        return False
    if not has_valid or int(provider_stats.get("consecutive_failures") or 0) > 0:
        return True
    return time.time() < float(provider_stats.get("cooldown_until") or 0)


def _quote_result_state(payload: dict) -> tuple[dict, dict, dict, bool, bool]:
    payload = payload or {}
    quotes = payload.get("quotes") or {}
    provider_stats = payload.get("provider_stats") or {}
    quote_request_stats = payload.get("quote_request_stats") or {}
    has_valid = _has_valid_quotes(quotes)
    provider_failed = _provider_fetch_failed(
        provider_stats,
        has_valid=has_valid,
        has_live_source=_has_live_quote_source(quotes),
    )
    return quotes, provider_stats, quote_request_stats, has_valid, provider_failed


def _quote_result_source(quotes: dict, provider_stats: dict) -> str:
    sources = sorted(
        {
            str(quote.get("source") or "").strip()
            for quote in quotes.values()
            if isinstance(quote, dict) and str(quote.get("source") or "").strip()
        }
    )
    if sources:
        return "+".join(sources)
    for key in ("current_source", "last_source", "source"):
        source = str(provider_stats.get(key) or "").strip()
        if source:
            return source
    return ""


def _process_quote_fetch_result(
    service,
    *,
    codes: set[str],
    payload: dict,
    elapsed_ms: float,
    reason: str,
    timing: dict[str, float],
    callback_started_at: float,
) -> None:
    quotes, provider_stats, quote_request_stats, has_valid, provider_failed = _quote_result_state(payload)
    current_source = _quote_result_source(quotes, provider_stats)
    if current_source:
        service.state.update(current_source=current_source)
    service._last_central_quote_request_stats = (
        dict(quote_request_stats) if isinstance(quote_request_stats, dict) else {}
    )
    if provider_failed:
        service._record_failure(provider_stats.get("last_error") or "提供方返回离线兜底快照")
    else:
        service._reset_failures()

    _record_and_publish_quote_refresh(
        service,
        codes=codes,
        quotes=quotes,
        has_valid=has_valid,
        provider_failed=provider_failed,
        elapsed_ms=elapsed_ms,
        reason=reason,
        timing=timing,
        callback_started_at=callback_started_at,
    )


def _publish_off_market_snapshot(service, payload: dict) -> None:
    quotes = (payload or {}).get("quotes") or {}
    current_source = _quote_result_source(quotes, (payload or {}).get("provider_stats") or {})
    if current_source:
        service.state.update(current_source=current_source)
    service._off_market_snapshot_emitted = True
    if not any(float(quote.get("close", 0) or 0) > 0 for quote in quotes.values()):
        return
    service.publish_external_quotes(
        quotes,
        source="central_quotes.off_market",
        require_valid=True,
    )


def _active_code_count(codes) -> int:
    return len(codes) if codes else 0


def _prepare_quote_fetch_cycle(service, reason: str) -> tuple[set[str], str, dict] | None:
    if service._closed:
        return None
    service._ensure_timer_running()
    service._tick_count += 1
    quote_refreshable = MarketCalendar.is_quote_refresh_time()
    market_status = service._market_status_text()
    service._observe_quote_window(quote_refreshable)
    if quote_refreshable:
        service._off_market_snapshot_emitted = False
        if service._off_market_snapshot_fetching:
            service._off_market_snapshot_generation += 1
            service._off_market_snapshot_fetching = False

    codes = service._get_all_active_codes()
    maintenance_stats = service._run_maintenance(
        active_codes_count=_active_code_count(codes),
        quote_refreshable=quote_refreshable,
        market_status=market_status,
    ) or {}
    if not codes:
        return None
    if not quote_refreshable:
        if service._off_market_snapshot_fetching:
            _remember_pending_fetch(service, reason)
        else:
            service._emit_off_market_snapshot(codes)
        return None
    if not service._poller.is_online():
        return None
    if service.state.fetching:
        _hold_fetch_while_inflight(service, reason, codes)
        return None
    return codes, market_status, maintenance_stats


def _should_skip_post_cache_reload_duplicate(service, reason: str, codes: set[str], now: float) -> bool:
    code_signature = tuple(sorted(codes))
    if reason == "cache_reload":
        service._post_cache_reload_signature = code_signature
        service._post_cache_reload_quiet_until = now + _POST_CACHE_RELOAD_DEDUP_WINDOW_SEC
        return False
    if (
        service._post_cache_reload_signature
        and code_signature == service._post_cache_reload_signature
        and now < service._post_cache_reload_quiet_until
    ):
        log.debug(f"[报价站] F5后窗口内跳过重复行情轮询: reason={reason} codes={len(codes)}")
        return True
    return False


def _consume_circuit_breaker_tick(service) -> bool:
    if service._circuit_breaker_cooldown <= 0:
        return False
    service._circuit_breaker_cooldown -= 1
    if service._circuit_breaker_cooldown == 0:
        log.info("[报价站] 冷却结束，恢复轮询")
    return True


def _prepare_realtime_fetch_codes(
    service,
    reason: str,
    codes: set[str],
    market_status: str,
    maintenance_stats: dict,
) -> set[str] | None:
    codes = service._opening_warmup_codes(codes, market_status=market_status)
    provider_stats = service._poller.get_runtime_stats()
    if time.time() < float(provider_stats.get("cooldown_until") or 0):
        service._circuit_breaker_cooldown = max(service._circuit_breaker_cooldown, service._COOLDOWN_TICKS)
        return None
    if service._should_skip_fallback_pressure_fetch(
        codes,
        provider_stats=provider_stats,
        maintenance_stats=maintenance_stats,
        market_status=market_status,
        reason=reason,
    ):
        return None
    codes = service._fallback_pressure_codes(codes, provider_stats=provider_stats, market_status=market_status)
    if _should_skip_post_cache_reload_duplicate(service, reason, codes, time.time()):
        return None
    if _consume_circuit_breaker_tick(service):
        return None
    return codes


def _handle_realtime_fetch_result(
    payload,
    *,
    service,
    codes: set[str],
    reason: str,
    fetch_token: int,
    timing: dict[str, float],
) -> None:
    runtime = service.state.read()
    if fetch_token != runtime.generation:
        return
    callback_started_at = time.perf_counter()
    elapsed_ms = max(0.0, (time.time() - runtime.started_at) * 1000.0)
    updated = service.state.update(
        expected_generation=fetch_token,
        fetching=False,
        started_at=0.0,
        warned_slow=False,
        codes_count=0,
    )
    if updated.generation != fetch_token or service._closed:
        return
    try:
        _process_quote_fetch_result(
            service,
            codes=codes,
            payload=payload,
            elapsed_ms=elapsed_ms,
            reason=reason,
            timing=timing,
            callback_started_at=callback_started_at,
        )
    finally:
        _schedule_pending_fetch_replay(service)


def _handle_realtime_fetch_error(error_message: str, *, service, fetch_token: int) -> None:
    updated = service.state.update(
        expected_generation=fetch_token,
        fetching=False,
        started_at=0.0,
        warned_slow=False,
        codes_count=0,
    )
    if updated.generation != fetch_token or service._closed:
        return
    try:
        service._record_failure(error_message or "后台抓取异常")
        log.error(f"[报价站] 后台抓取异常: {error_message}")
    finally:
        _schedule_pending_fetch_replay(service)


def _run_realtime_fetch(
    cancellation_token,
    *,
    service,
    codes: set[str],
    timing: dict[str, float],
) -> dict:
    return _fetch_quote_payload_timed(
        service,
        codes,
        timing,
        cancellation_token,
    )


def _submit_realtime_fetch(service, codes: set[str], reason: str) -> None:
    runtime = service.state.update(
        fetching=True,
        started_at=time.time(),
        warned_slow=False,
        codes_count=len(codes),
        increments={"generation": 1},
    )
    fetch_token = runtime.generation
    timing = {"submitted_at": time.perf_counter()}
    background = partial(_run_realtime_fetch, service=service, codes=codes, timing=timing)
    on_result = partial(
        _handle_realtime_fetch_result,
        service=service,
        codes=codes,
        reason=reason,
        fetch_token=fetch_token,
        timing=timing,
    )
    on_error = partial(_handle_realtime_fetch_error, service=service, fetch_token=fetch_token)
    _submit_central_task(
        service,
        "realtime_poll",
        background,
        on_result,
        on_error,
        CENTRAL_QUOTES_POLL,
        max(30.0, _slow_fetch_threshold(service.data_provider, len(codes)) + 10.0),
    )


class CentralQuotesService(QuoteRuntimeStateCompatMixin, QObject):
    """
    统一的中央实时报价广播站。

    目标：
    1. 同一时刻只允许一个抓取任务在飞。
    2. 盘后不再发起任何 pytdx 实时拉取。
    3. 分钟级输出运行时健康日志，发现线程异常时主动熔断。
    """

    def __init__(self, main_window, data_provider, code_supplier=None):
        super().__init__(main_window)
        self.data_provider = data_provider
        self._code_supplier: Callable[[], Iterable[object] | None] | None = code_supplier
        self._missing_code_supplier_warned = False
        self._closed = False
        self._task_lifecycle = TaskLifecycleGroup(task_manager)

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._trigger_fetch)
        self._timer.start(_A_SHARE_POLL_INTERVAL_MS)
        self._pending_fetch_timer = QTimer(self)
        self._pending_fetch_timer.setSingleShot(True)
        self._pending_fetch_timer.timeout.connect(lambda: _replay_pending_fetch(self))

        self.state = QuoteRuntimeState()
        self._off_market_snapshot_emitted = False
        self._off_market_snapshot_fetching = False
        self._off_market_snapshot_generation = 0
        self._opening_warmup_signature: tuple[str, ...] = ()
        self._opening_warmup_cursor = 0
        self._fallback_pressure_signature: tuple[str, ...] = ()
        self._fallback_pressure_cursor = 0
        self._last_fallback_pressure_log_at = 0.0
        self._last_fallback_pressure_skip_log_at = 0.0
        self._last_central_quote_request_stats: dict = {}

        self._circuit_breaker_cooldown = 0
        self._FAILURE_THRESHOLD = 3
        self._COOLDOWN_TICKS = max(1, _A_SHARE_FAILURE_COOLDOWN_SEC * 1000 // _A_SHARE_POLL_INTERVAL_MS)

        self._tick_count = 0
        self._heartbeat_every_ticks = max(1, _A_SHARE_HEARTBEAT_INTERVAL_SEC * 1000 // _A_SHARE_POLL_INTERVAL_MS)
        self._last_heartbeat_signature = None
        self._last_heartbeat_logged_at = 0.0
        self._last_quote_refreshable: bool | None = None
        self._post_cache_reload_quiet_until = 0.0
        self._post_cache_reload_signature: tuple[str, ...] = ()
        self._poller = CentralQuotePoller(
            data_provider,
            missing_finance_codes=global_store.get_missing_a_share_finance_codes,
        )

    def set_code_supplier(self, code_supplier) -> None:
        self._code_supplier = code_supplier
        self._missing_code_supplier_warned = False

    @pyqtSlot()
    def refresh_after_cache_reload(self):
        """F5 或本地缓存更新后，立刻重建一次全局报价快照。"""
        self._off_market_snapshot_emitted = False
        if self._closed:
            return
        self._trigger_fetch_for_reason("cache_reload")

    def publish_external_quotes(
        self,
        payload,
        *,
        source: str,
        require_valid: bool = False,
    ) -> dict[str, dict]:
        if self._closed:
            return {}
        return publish_rt_quotes(payload, source=source, require_valid=require_valid)

    def _get_all_active_codes(self) -> set[str]:
        def _normalize_a_code(code):
            code = str(code).strip()
            return code if _A_SHARE_CODE_RE.match(code) else None

        if not callable(self._code_supplier):
            if not self._missing_code_supplier_warned:
                self._missing_code_supplier_warned = True
                log.warning("[报价站] 未注入 code_supplier，跳过本轮行情轮询")
            return set()

        self._missing_code_supplier_warned = False
        codes = set()
        for code in self._code_supplier() or []:
            normalized = _normalize_a_code(code)
            if normalized:
                codes.add(normalized)
        return codes

    def _fetch_quote_payload(self, codes: set[str], *, cancellation_token=None) -> dict:
        return self._poller.fetch_payload(codes, cancellation_token=cancellation_token)

    def _reset_failures(self):
        self.state.update(failure_count=0)

    def _record_failure(self, reason: str):
        failure_count = self.state.update(increments={"failure_count": 1}).failure_count
        log.warning(f"[报价站] 抓取失败({failure_count}/{self._FAILURE_THRESHOLD}): {reason}")
        if failure_count < self._FAILURE_THRESHOLD:
            return

        self._circuit_breaker_cooldown = max(self._circuit_breaker_cooldown, self._COOLDOWN_TICKS)
        log.error("[报价站] 连续失败达到阈值，进入 5 分钟冷却")

        self._poller.enter_realtime_cooldown(
            f"报价站连续失败达到阈值：{reason}",
            cooldown_sec=300,
        )

    def _collect_thread_health(self) -> tuple[int, int]:
        frames = sys._current_frames()
        total_threads = 0
        pytdx_threads = 0

        for thread in threading.enumerate():
            total_threads += 1
            thread_name = (thread.name or "").lower()
            if "pytdx" in thread_name or "tdx" in thread_name:
                pytdx_threads += 1
                continue

            frame = frames.get(thread.ident) if thread.ident is not None else None
            while frame is not None:
                filename = (frame.f_code.co_filename or "").lower()
                if "pytdx" in filename:
                    pytdx_threads += 1
                    break
                frame = frame.f_back

        return total_threads, pytdx_threads

    def _timer_is_active(self) -> bool:
        try:
            return bool(self._timer.isActive())
        except RuntimeError:
            return False

    def _ensure_timer_running(self) -> bool:
        if self._closed:
            return False
        if self._timer_is_active():
            return True
        self._timer.start(_A_SHARE_POLL_INTERVAL_MS)
        log.warning("[报价站] 轮询调度器意外停止，已重新启动")
        return True

    def _observe_quote_window(self, quote_refreshable: bool) -> None:
        quote_refreshable = bool(quote_refreshable)
        previous = self._last_quote_refreshable
        self._last_quote_refreshable = quote_refreshable
        if previous is None:
            if not quote_refreshable:
                log.info("[报价站] 非报价时段，实时拉取暂停；30秒调度保留，下一交易时段自动轮询")
            return
        if previous == quote_refreshable:
            return
        if quote_refreshable:
            log.info("[报价站] 报价窗口恢复，自动拉起实时轮询")
        else:
            log.info("[报价站] 非报价时段，实时拉取暂停；30秒调度保留，下一交易时段自动轮询")

    def _heartbeat_runtime_status(
        self,
        *,
        quote_refreshable: bool,
        market_status: str,
        runtime_stats: dict,
        cooldown_left: int,
        realtime_cache_size: int,
        last_success_age: float | None,
    ) -> tuple[str, str, bool, str]:
        timer_active = self._timer_is_active()
        if self._closed:
            return "closed", "服务已关闭", timer_active, "service_closed"
        if not timer_active:
            return "degraded_scheduler_inactive", "调度器未运行，需恢复后才能自动轮询", timer_active, "scheduler_inactive"
        if not quote_refreshable:
            return "paused_market_closed", "下个交易时段自动轮询", timer_active, "market_closed"
        opening_warmup = str(market_status or "").strip() in _OPENING_WARMUP_STATUSES
        try:
            inflight = int(runtime_stats.get("inflight") or 0)
        except (TypeError, ValueError):
            inflight = 0
        try:
            consecutive_failures = int(runtime_stats.get("consecutive_failures") or 0)
        except (TypeError, ValueError):
            consecutive_failures = 0
        last_error = str(runtime_stats.get("last_error") or "").strip()
        if self.state.fetching or inflight > 0:
            if opening_warmup:
                return "opening_warmup_fetching", "集合竞价限量预热中", timer_active, "opening_warmup"
            return "fetching", "等待当前抓取完成", timer_active, "inflight"
        if cooldown_left > 0 or self._circuit_breaker_cooldown > 0:
            return "cooldown", "冷却结束后自动重试", timer_active, "cooldown"
        if consecutive_failures > 0 or last_error:
            return "degraded_provider_errors", "等待下一轮重试或进入冷却", timer_active, "provider_errors"
        if opening_warmup:
            return "opening_warmup", "集合竞价限量预热，连续竞价后全量轮询", timer_active, "opening_warmup"
        if last_success_age is not None and last_success_age <= _RECENT_SUCCESS_GRACE_SEC:
            return "active_refreshing", "持续30秒调度轮询", timer_active, "recent_success"
        if realtime_cache_size > 0:
            return "ready_with_cache", "等待下一轮刷新推进", timer_active, "cache_present"
        return "waiting_first_refresh", "等待首轮实时刷新", timer_active, "no_success_yet"

    @staticmethod
    def _rt_cache_status_text(cache_size: int, runtime_state: str) -> str:
        if int(cache_size or 0) > 0:
            return str(int(cache_size or 0))
        if runtime_state in {"opening_warmup", "opening_warmup_fetching"}:
            return "首轮预热中(0)"
        if runtime_state in {"fetching", "waiting_first_refresh"}:
            return "首轮待写入(0)"
        return "0"

    @staticmethod
    def _owner_thread_status_text(runtime_stats: dict, owner_thread_alive: bool) -> str:
        if owner_thread_alive:
            return "存活"
        if bool(runtime_stats.get("owner_thread_applicable")):
            return "已停止"
        return "未使用(HTTP行情)"

    def _market_status_text(self) -> str:
        try:
            return str(MarketCalendar.get_market_status("CN") or "").strip() or "-"
        except (AttributeError, OSError, RuntimeError, TypeError, ValueError):
            return "unknown"

    def _opening_warmup_codes(self, codes: set[str], *, market_status: str) -> set[str]:
        if str(market_status or "").strip() not in _OPENING_WARMUP_STATUSES:
            self._opening_warmup_signature = ()
            self._opening_warmup_cursor = 0
            return codes

        limit = max(1, int(_OPENING_WARMUP_FETCH_LIMIT or 1))
        ordered = tuple(sorted(codes))
        if len(ordered) <= limit:
            return codes
        if ordered != self._opening_warmup_signature:
            self._opening_warmup_signature = ordered
            self._opening_warmup_cursor = 0

        start = self._opening_warmup_cursor % len(ordered)
        end = start + limit
        selected = list(ordered[start:end])
        if end > len(ordered):
            selected.extend(ordered[: end - len(ordered)])
        self._opening_warmup_cursor = end % len(ordered)
        log.info(
            f"[报价站] {market_status}冷启动限流，本轮联网 {len(selected)}/{len(ordered)} 只；"
            "连续竞价后恢复全量轮询"
        )
        return set(selected)

    @staticmethod
    def _stats_int(stats: dict, key: str) -> int:
        try:
            return int(stats.get(key) or 0)
        except (TypeError, ValueError):
            return 0

    @staticmethod
    def _stats_float(stats: dict, key: str) -> float:
        try:
            return float(stats.get(key) or 0.0)
        except (TypeError, ValueError):
            return 0.0

    @staticmethod
    def _stats_time(value) -> float:
        if isinstance(value, (int, float)):
            return float(value)
        text = str(value or "").strip()
        if not text:
            return 0.0
        try:
            return time.mktime(time.strptime(text[:19], "%Y-%m-%dT%H:%M:%S"))
        except (TypeError, ValueError):
            return 0.0

    def _quote_stats_fallback_pressure(
        self,
        stats: dict,
        *,
        now: float,
        label: str,
    ) -> tuple[bool, str]:
        if not isinstance(stats, dict) or not stats:
            return False, ""

        requested = self._stats_int(stats, "recent_requested_count")
        pending = self._stats_int(stats, "recent_pending_count")
        cache_hits = self._stats_int(stats, "recent_cache_hit_count")
        elapsed_ms = self._stats_float(stats, "recent_elapsed_ms")
        status = str(stats.get("recent_status") or "").lower()
        source_layers = [
            str(layer or "").strip().lower()
            for layer in (stats.get("recent_source_layers") or [])
            if str(layer or "").strip()
        ]
        ended_at = self._stats_time(stats.get("recent_ended_at_ts") or stats.get("recent_ended_at"))
        recent_enough = ended_at > 0 and 0 <= now - ended_at <= _FALLBACK_PRESSURE_RECENT_SEC
        fallback_or_degraded = "fallback" in status or "partial" in status or any(
            any(token in layer for token in _FALLBACK_PRESSURE_SOURCE_TOKENS) for layer in source_layers
        )
        slow_full_refresh = (
            requested >= _FALLBACK_PRESSURE_MIN_PENDING
            and elapsed_ms >= _FALLBACK_PRESSURE_MIN_ELAPSED_MS
            and cache_hits <= max(1, requested // 2)
        )
        heavy_network = (
            pending >= _FALLBACK_PRESSURE_MIN_PENDING
            or (requested >= _FALLBACK_PRESSURE_MIN_PENDING and cache_hits <= max(1, requested // 10))
            or (elapsed_ms >= _FALLBACK_PRESSURE_MIN_ELAPSED_MS and pending >= 40)
        )
        if not (recent_enough and (fallback_or_degraded or slow_full_refresh) and heavy_network):
            return False, ""

        layer_text = "/".join(source_layers) if source_layers else status or "fallback"
        if slow_full_refresh and not fallback_or_degraded:
            layer_text = f"{layer_text}/slow_full_refresh"
        return True, f"{label} fallback pressure pending={pending}/{requested} cache={cache_hits} source={layer_text}"

    def _recent_quote_fallback_pressure(self, *, now: float | None = None) -> tuple[bool, str]:
        now = time.time() if now is None else float(now)
        candidates = (
            ("central", self._last_central_quote_request_stats),
            ("provider", _provider_request_stats(self.data_provider)),
        )
        for label, stats in candidates:
            pressure, reason = self._quote_stats_fallback_pressure(stats, now=now, label=label)
            if pressure:
                return True, reason
        return False, ""

    def _quote_fallback_cooldown_left(self, provider_stats: dict | None = None, *, now: float | None = None) -> int:
        now = time.time() if now is None else float(now)
        cooldown_candidates = []
        if isinstance(provider_stats, dict):
            cooldown_candidates.append(provider_stats.get("quote_cooldown_until"))
        cooldown_candidates.append(read_provider_health(self.data_provider).eastmoney_cooldown_until)

        cooldown_until = 0.0
        for value in cooldown_candidates:
            try:
                cooldown_until = max(cooldown_until, float(value or 0.0))
            except (TypeError, ValueError):
                continue
        return max(0, int(cooldown_until - now))

    def _fallback_pressure_codes(
        self,
        codes: set[str],
        *,
        provider_stats: dict | None = None,
        market_status: str,
    ) -> set[str]:
        if str(market_status or "").strip() in _OPENING_WARMUP_STATUSES:
            self._fallback_pressure_signature = ()
            self._fallback_pressure_cursor = 0
            return codes

        now = time.time()
        cooldown_left = self._quote_fallback_cooldown_left(provider_stats, now=now)
        recent_pressure, pressure_reason = self._recent_quote_fallback_pressure(now=now)
        if cooldown_left <= 0 and not recent_pressure:
            self._fallback_pressure_signature = ()
            self._fallback_pressure_cursor = 0
            return codes

        ordered = tuple(sorted(codes))
        limit = max(1, int(_FALLBACK_PRESSURE_FETCH_LIMIT or 1))
        if len(ordered) <= limit:
            return codes
        if ordered != self._fallback_pressure_signature:
            self._fallback_pressure_signature = ordered
            self._fallback_pressure_cursor = 0

        start = self._fallback_pressure_cursor % len(ordered)
        end = start + limit
        selected = list(ordered[start:end])
        if end > len(ordered):
            selected.extend(ordered[: end - len(ordered)])
        self._fallback_pressure_cursor = end % len(ordered)

        if (now - self._last_fallback_pressure_log_at) >= _A_SHARE_POLL_INTERVAL_MS / 1000:
            self._last_fallback_pressure_log_at = now
            log.info(
                f"[报价站] 东方财富回退冷却中，自动轮询限量 {len(selected)}/{len(ordered)} 只；"
                f"剩余冷却约 {cooldown_left}s，滚动覆盖以避开盘中扫描重活叠加"
            )
            if cooldown_left <= 0 and pressure_reason:
                log.info(f"[报价站] 最近报价回退压力仍在，继续滚动限量: {pressure_reason}")
        return set(selected)

    def _should_skip_fallback_pressure_fetch(
        self,
        codes: set[str],
        *,
        provider_stats: dict | None,
        maintenance_stats: dict | None,
        market_status: str,
        reason: str,
    ) -> bool:
        if reason != "timer" or str(market_status or "").strip() in _OPENING_WARMUP_STATUSES:
            return False

        if not codes:
            return False

        now = time.time()
        cooldown_left = self._quote_fallback_cooldown_left(provider_stats, now=now)
        recent_pressure, pressure_reason = self._recent_quote_fallback_pressure(now=now)
        if cooldown_left <= 0 and not recent_pressure:
            return False

        stats = maintenance_stats if isinstance(maintenance_stats, dict) else {}
        runtime_stats = stats.get("rt_runtime", {}) if isinstance(stats.get("rt_runtime", {}), dict) else {}
        try:
            cache_size = int(stats.get("rt_quote_cache_size") or 0)
        except (TypeError, ValueError):
            cache_size = 0
        min_cache_size = min(
            len(codes),
            max(1, int(len(codes) * _FALLBACK_PRESSURE_SKIP_CACHE_RATIO)),
        )
        if cache_size < min_cache_size:
            return False

        try:
            last_success_at = float(
                (provider_stats or {}).get("last_success_at")
                or runtime_stats.get("last_success_at")
                or 0.0
            )
        except (TypeError, ValueError):
            last_success_at = 0.0
        if last_success_at <= 0:
            return False
        last_success_age = now - last_success_at
        if last_success_age < 0 or last_success_age > _RECENT_SUCCESS_GRACE_SEC:
            return False

        if (now - self._last_fallback_pressure_skip_log_at) >= _FALLBACK_PRESSURE_SKIP_LOG_INTERVAL_SEC:
            self._last_fallback_pressure_skip_log_at = now
            reason_text = pressure_reason or f"cooldown_left={cooldown_left}s"
            log.info(
                "[报价站] 报价回退压力中，跳过本轮自动联网；"
                f"保留已有实时缓存 {cache_size}/{len(codes)} 只，避免与盘中扫描叠加: {reason_text}"
            )
        return True

    def _run_maintenance(
        self,
        active_codes_count: int | None = None,
        *,
        quote_refreshable: bool | None = None,
        market_status: str | None = None,
    ):
        stats = self._poller.compact_runtime_caches()
        if not isinstance(stats, dict):
            stats = {}

        total_threads, pytdx_threads = self._collect_thread_health()
        if self._poller.protect_against_thread_anomaly(pytdx_threads):
            self._circuit_breaker_cooldown = max(self._circuit_breaker_cooldown, self._COOLDOWN_TICKS)

        if self._tick_count % self._heartbeat_every_ticks != 0:
            return stats

        runtime_stats = stats.get("rt_runtime", {})
        last_success_at = float(runtime_stats.get("last_success_at") or 0)
        last_success_text = time.strftime("%H:%M:%S", time.localtime(last_success_at)) if last_success_at > 0 else "-"
        now_ts = time.time()
        runtime_cooldown_until = float(runtime_stats.get("cooldown_until") or 0)
        cooldown_left = max(
            max(0, int(runtime_cooldown_until - now_ts)),
            self._quote_fallback_cooldown_left(runtime_stats, now=now_ts),
        )
        if quote_refreshable is None:
            quote_refreshable = MarketCalendar.is_quote_refresh_time()
        rt_quote_cache_size = stats.get("rt_quote_cache_size", 0)
        last_success_age = max(0.0, now_ts - last_success_at) if last_success_at > 0 else None
        owner_thread_alive = bool(
            runtime_stats.get("owner_thread_alive", runtime_stats.get("worker_alive", False))
        )
        market_status = str(market_status or self._market_status_text()).strip() or "-"
        runtime_state, next_step, timer_active, activity_basis = self._heartbeat_runtime_status(
            quote_refreshable=bool(quote_refreshable),
            market_status=market_status,
            runtime_stats=runtime_stats,
            cooldown_left=cooldown_left,
            realtime_cache_size=int(rt_quote_cache_size or 0),
            last_success_age=last_success_age,
        )
        rt_cache_text = self._rt_cache_status_text(int(rt_quote_cache_size or 0), runtime_state)
        owner_thread_text = self._owner_thread_status_text(runtime_stats, owner_thread_alive)
        heartbeat_signature = (
            active_codes_count if active_codes_count is not None else "-",
            rt_quote_cache_size,
            stats.get("history_symbol_count", 0),
            runtime_stats.get("inflight", 0),
            last_success_text,
            runtime_stats.get("consecutive_failures", 0),
            runtime_stats.get("reconnect_count", 0),
            cooldown_left,
            market_status,
            runtime_state,
            activity_basis,
            next_step,
            timer_active,
            owner_thread_text,
            total_threads,
            pytdx_threads,
        )
        should_log = bool(quote_refreshable)
        if not should_log:
            signature_changed = heartbeat_signature != self._last_heartbeat_signature
            interval_reached = (now_ts - self._last_heartbeat_logged_at) >= 1800
            should_log = signature_changed or interval_reached

        if not should_log:
            return stats

        log.info(
            "[报价站] 心跳 "
            f"标的={active_codes_count if active_codes_count is not None else '-'} "
            f"实时缓存={rt_cache_text} "
            f"历史缓存={stats.get('history_symbol_count', 0)} "
            f"飞行中={runtime_stats.get('inflight', 0)} "
            f"上次成功={last_success_text} "
            f"连败={runtime_stats.get('consecutive_failures', 0)} "
            f"重连={runtime_stats.get('reconnect_count', 0)} "
            f"冷却剩余={cooldown_left}s "
            f"市场={market_status} "
            f"状态={runtime_state} "
            f"活跃依据={activity_basis} "
            f"下一步={next_step} "
            f"调度器存活={timer_active} "
            f"底层owner线程={owner_thread_text} "
            f"总线程={total_threads} "
            f"pytdx线程={pytdx_threads}"
        )
        self._last_heartbeat_signature = heartbeat_signature
        self._last_heartbeat_logged_at = now_ts
        return stats

    def _emit_off_market_snapshot(self, codes: set[str]):
        if (
            self._off_market_snapshot_emitted
            or self._off_market_snapshot_fetching
            or not codes
            or self.data_provider is None
        ):
            return

        request_codes = set(codes)
        self._off_market_snapshot_fetching = True
        self._off_market_snapshot_generation += 1
        request_generation = self._off_market_snapshot_generation

        def _bg_fetch(cancellation_token):
            return invoke_with_cancellation(
                self._fetch_quote_payload,
                cancellation_token,
                request_codes,
            )

        def _on_result(payload):
            if request_generation != self._off_market_snapshot_generation:
                return
            self._off_market_snapshot_fetching = False
            if self._closed:
                return
            try:
                _publish_off_market_snapshot(self, payload)
            finally:
                _schedule_pending_fetch_replay(self)

        def _on_error(error_message: str):
            if request_generation != self._off_market_snapshot_generation:
                return
            self._off_market_snapshot_fetching = False
            try:
                if not self._closed:
                    log.warning(f"[报价站] 盘后离线快照构建失败: {error_message}")
            finally:
                _schedule_pending_fetch_replay(self)

        _submit_central_task(
            self, "off_market_snapshot", _bg_fetch, _on_result, _on_error,
            task_registry.transient_quotes(f"central_quotes_off_market_snapshot_{request_generation}"),
            max(30.0, _slow_fetch_threshold(self.data_provider, len(request_codes)) + 10.0),
        )

    @pyqtSlot()
    def _trigger_fetch(self):
        self._trigger_fetch_for_reason("timer")

    def _trigger_fetch_for_reason(self, reason: str = "timer"):
        prepared = _prepare_quote_fetch_cycle(self, reason)
        if prepared is None:
            return
        codes, market_status, maintenance_stats = prepared
        ready_codes = _prepare_realtime_fetch_codes(
            self,
            reason,
            codes,
            market_status,
            maintenance_stats,
        )
        if ready_codes is not None:
            _submit_realtime_fetch(self, ready_codes, reason)

    def shutdown(self):
        self._closed = True
        self._timer.stop()
        self._pending_fetch_timer.stop()
        self.state.update(
            fetching=False,
            increments={"generation": 1},
            started_at=0.0,
            pending_reason="",
            warned_slow=False,
            codes_count=0,
        )
        self._off_market_snapshot_generation += 1
        self._off_market_snapshot_fetching = False
        self._task_lifecycle.shutdown(timeout_ms=1_000)
