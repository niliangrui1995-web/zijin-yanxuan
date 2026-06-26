from __future__ import annotations

import re
import sys
import threading
import time

from PyQt6.QtCore import QObject, QTimer, pyqtSlot

from app.services.central_quote_polling_service import CentralQuotePoller
from app.services.ui_market_calendar_service import MarketCalendar
from app.services.ui_quote_service import publish_rt_quotes
from app.services.ui_task_service import CENTRAL_QUOTES_POLL
from app.services.ui_task_service import background_job_runner as task_manager
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
_FALLBACK_PRESSURE_MIN_ELAPSED_MS = 20000.0
_FALLBACK_PRESSURE_SOURCE_TOKENS = ("sina", "tencent", "fallback", "offline", "stale")


class CentralQuotesService(QObject):
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
        self._code_supplier = code_supplier
        self._missing_code_supplier_warned = False
        self._closed = False

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._trigger_fetch)
        self._timer.start(_A_SHARE_POLL_INTERVAL_MS)

        self._is_fetching = False
        self._fetch_start_time = 0.0
        self._fetch_warned_slow = False
        self._fetch_codes_count = 0
        self._fetch_generation = 0
        self._off_market_snapshot_emitted = False
        self._opening_warmup_signature: tuple[str, ...] = ()
        self._opening_warmup_cursor = 0
        self._fallback_pressure_signature: tuple[str, ...] = ()
        self._fallback_pressure_cursor = 0
        self._last_fallback_pressure_log_at = 0.0
        self._last_central_quote_request_stats: dict = {}

        self._consecutive_failures = 0
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

    def _get_missing_finance_codes(self, codes: set[str]) -> list[str]:
        return self._poller.missing_finance_codes(codes)

    def _fetch_quote_payload(self, codes: set[str]) -> dict:
        return self._poller.fetch_payload(codes)

    def _reset_failures(self):
        self._consecutive_failures = 0

    def _record_failure(self, reason: str):
        self._consecutive_failures += 1
        log.warning(f"[报价站] 抓取失败({self._consecutive_failures}/{self._FAILURE_THRESHOLD}): {reason}")
        if self._consecutive_failures < self._FAILURE_THRESHOLD:
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

            frame = frames.get(thread.ident)
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
        if self._is_fetching or inflight > 0:
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

    def _get_quote_request_stats(self) -> dict:
        stats_getter = getattr(self.data_provider, "get_quote_request_stats", None)
        if not callable(stats_getter):
            return {}
        try:
            stats = stats_getter() or {}
        except (AttributeError, RuntimeError, TypeError, ValueError):
            return {}
        return stats if isinstance(stats, dict) else {}

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
        heavy_network = (
            pending >= _FALLBACK_PRESSURE_MIN_PENDING
            or (requested >= _FALLBACK_PRESSURE_MIN_PENDING and cache_hits <= max(1, requested // 10))
            or (elapsed_ms >= _FALLBACK_PRESSURE_MIN_ELAPSED_MS and pending >= 40)
        )
        if not (recent_enough and fallback_or_degraded and heavy_network):
            return False, ""

        layer_text = "/".join(source_layers) if source_layers else status or "fallback"
        return True, f"{label} fallback pressure pending={pending}/{requested} cache={cache_hits} source={layer_text}"

    def _recent_quote_fallback_pressure(self, *, now: float | None = None) -> tuple[bool, str]:
        now = time.time() if now is None else float(now)
        candidates = (
            ("central", self._last_central_quote_request_stats),
            ("provider", self._get_quote_request_stats()),
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
        cooldown_candidates.append(getattr(self.data_provider, "_rt_eastmoney_cooldown_until", 0.0))

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

    def _run_maintenance(
        self,
        active_codes_count: int | None = None,
        *,
        quote_refreshable: bool | None = None,
        market_status: str | None = None,
    ):
        stats = self._poller.compact_runtime_caches()

        total_threads, pytdx_threads = self._collect_thread_health()
        if self._poller.protect_against_thread_anomaly(pytdx_threads):
            self._circuit_breaker_cooldown = max(self._circuit_breaker_cooldown, self._COOLDOWN_TICKS)

        if self._tick_count % self._heartbeat_every_ticks != 0:
            return

        runtime_stats = stats.get("rt_runtime", {}) if isinstance(stats, dict) else {}
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
        rt_quote_cache_size = stats.get("rt_quote_cache_size", 0) if isinstance(stats, dict) else 0
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
            stats.get("history_symbol_count", 0) if isinstance(stats, dict) else 0,
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
            return

        log.info(
            "[报价站] 心跳 "
            f"标的={active_codes_count if active_codes_count is not None else '-'} "
            f"实时缓存={rt_cache_text} "
            f"历史缓存={stats.get('history_symbol_count', 0) if isinstance(stats, dict) else 0} "
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

    def _emit_off_market_snapshot(self, codes: set[str]):
        if self._off_market_snapshot_emitted or not codes or self.data_provider is None:
            return

        try:
            payload = self._fetch_quote_payload(codes)
            quotes = payload.get("quotes") or {}
        except (AttributeError, ConnectionError, OSError, RuntimeError, TimeoutError, TypeError, ValueError) as exc:
            log.warning(f"[报价站] 盘后离线快照构建失败: {exc}")
            return

        self._off_market_snapshot_emitted = True
        has_valid = any(float(quote.get("close", 0) or 0) > 0 for quote in quotes.values())
        if has_valid:
            self.publish_external_quotes(
                quotes,
                source="central_quotes.off_market",
                require_valid=True,
            )

    @pyqtSlot()
    def _trigger_fetch(self):
        self._trigger_fetch_for_reason("timer")

    def _trigger_fetch_for_reason(self, reason: str = "timer"):
        if self._closed:
            return

        self._ensure_timer_running()
        self._tick_count += 1
        quote_refreshable = MarketCalendar.is_quote_refresh_time()
        market_status = self._market_status_text()
        self._observe_quote_window(quote_refreshable)
        if quote_refreshable:
            self._off_market_snapshot_emitted = False

        codes = self._get_all_active_codes()
        self._run_maintenance(
            active_codes_count=len(codes) if codes else 0,
            quote_refreshable=quote_refreshable,
            market_status=market_status,
        )
        if not codes:
            return

        if not quote_refreshable:
            self._emit_off_market_snapshot(codes)
            return

        if not self._poller.is_online():
            return

        if self._is_fetching:
            batch_timeout_sec = float(getattr(self.data_provider, "_rt_api_call_timeout_sec", 8.0) or 8.0)
            batch_size = int(getattr(self.data_provider, "_rt_quote_batch_size", 20) or 20)
            codes_count = int(self._fetch_codes_count or len(codes) or 0)
            expected_batches = max(1, (codes_count + batch_size - 1) // batch_size)
            slow_threshold = max(20.0, expected_batches * batch_timeout_sec + 4.0)
            if (
                not self._fetch_warned_slow
                and self._fetch_start_time > 0
                and (time.time() - self._fetch_start_time) > slow_threshold
            ):
                self._fetch_warned_slow = True
                log.warning(
                    f"[报价站] 单次抓取耗时过长({time.time() - self._fetch_start_time:.1f}s)，"
                    "继续等待当前单飞行任务结束"
                )
            return

        codes = self._opening_warmup_codes(codes, market_status=market_status)
        provider_stats = self._poller.get_runtime_stats()
        if time.time() < float(provider_stats.get("cooldown_until") or 0):
            self._circuit_breaker_cooldown = max(self._circuit_breaker_cooldown, self._COOLDOWN_TICKS)
            return

        codes = self._fallback_pressure_codes(codes, provider_stats=provider_stats, market_status=market_status)
        code_signature = tuple(sorted(codes))
        now = time.time()
        if reason == "cache_reload":
            self._post_cache_reload_signature = code_signature
            self._post_cache_reload_quiet_until = now + _POST_CACHE_RELOAD_DEDUP_WINDOW_SEC
        elif (
            self._post_cache_reload_signature
            and code_signature == self._post_cache_reload_signature
            and now < self._post_cache_reload_quiet_until
        ):
            log.debug(f"[报价站] F5后窗口内跳过重复行情轮询: reason={reason} codes={len(codes)}")
            return

        if self._circuit_breaker_cooldown > 0:
            self._circuit_breaker_cooldown -= 1
            if self._circuit_breaker_cooldown == 0:
                log.info("[报价站] 冷却结束，恢复轮询")
            return

        self._is_fetching = True
        self._fetch_start_time = time.time()
        self._fetch_warned_slow = False
        self._fetch_codes_count = len(codes)
        self._fetch_generation += 1
        fetch_token = self._fetch_generation

        def _bg_task():
            return self._fetch_quote_payload(codes)

        def _on_result(payload):
            if fetch_token != self._fetch_generation:
                return

            elapsed_ms = max(0.0, (time.time() - self._fetch_start_time) * 1000.0)
            self._is_fetching = False
            self._fetch_start_time = 0.0
            self._fetch_warned_slow = False
            self._fetch_codes_count = 0
            if self._closed:
                return

            payload = payload or {}
            quotes = payload.get("quotes") or {}
            provider_stats = payload.get("provider_stats") or {}
            quote_request_stats = payload.get("quote_request_stats") or {}
            self._last_central_quote_request_stats = (
                dict(quote_request_stats) if isinstance(quote_request_stats, dict) else {}
            )
            cooldown_until = float(provider_stats.get("cooldown_until") or 0)
            has_valid = any(float(quote.get("close", 0) or 0) > 0 for quote in quotes.values())
            has_live_source = any(
                str(quote.get("source") or "").lower() in {"eastmoney", "sina", "tencent"} for quote in quotes.values()
            )
            provider_failed = (not has_live_source) and (
                (not has_valid)
                or int(provider_stats.get("consecutive_failures") or 0) > 0
                or time.time() < cooldown_until
            )

            if provider_failed:
                self._record_failure(provider_stats.get("last_error") or "提供方返回离线兜底快照")
            else:
                self._reset_failures()

            record_metric(
                "quote_refresh_batch_size",
                len(codes),
                unit="count",
                tags={"valid_quotes": str(bool(has_valid)).lower()},
            )
            record_metric(
                "quote_refresh_ms",
                elapsed_ms,
                unit="ms",
                tags={"valid_quotes": str(bool(has_valid)).lower()},
            )
            emit_structured_log(
                "quotes.refresh.completed",
                batch_size=len(codes),
                elapsed_ms=round(elapsed_ms, 3),
                valid_quotes=bool(has_valid),
                provider_failed=bool(provider_failed),
            )

            if has_valid:
                self.publish_external_quotes(
                    quotes,
                    source="central_quotes.realtime",
                    require_valid=True,
                )

        def _on_error(err_msg):
            if fetch_token != self._fetch_generation:
                return

            self._is_fetching = False
            self._fetch_start_time = 0.0
            self._fetch_warned_slow = False
            self._fetch_codes_count = 0
            if self._closed:
                return

            self._record_failure(err_msg or "后台抓取异常")
            log.error(f"[报价站] 后台抓取异常: {err_msg}")

        task_manager.run_in_background(
            _bg_task,
            on_success=_on_result,
            on_error=_on_error,
            task_id=CENTRAL_QUOTES_POLL,
        )

    def shutdown(self):
        self._closed = True
        self._timer.stop()
        self._fetch_generation += 1
