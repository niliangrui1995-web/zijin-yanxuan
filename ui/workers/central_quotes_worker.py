from __future__ import annotations

import re
import sys
import threading
import time

from PyQt6.QtCore import QObject, QTimer, pyqtSlot

from core.event_bus import event_bus
from core.logger import get_logger
from core.market_calendar import MarketCalendar
from core.task_manager import task_manager

log = get_logger(__name__)
_A_SHARE_CODE_RE = re.compile(r"^\d{6}$")


class CentralQuotesService(QObject):
    """
    统一的中央实时报价广播站。

    目标：
    1. 同一时刻只允许一个抓取任务在飞。
    2. 盘后不再发起任何 pytdx 实时拉取。
    3. 分钟级输出运行时健康日志，发现线程异常时主动熔断。
    """

    def __init__(self, main_window, data_provider):
        super().__init__(main_window)
        self.main_window = main_window
        self.data_provider = data_provider
        self._closed = False

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._trigger_fetch)
        self._timer.start(10000)

        self._is_fetching = False
        self._fetch_start_time = 0.0
        self._fetch_warned_slow = False
        self._fetch_generation = 0
        self._off_market_snapshot_emitted = False

        self._consecutive_failures = 0
        self._circuit_breaker_cooldown = 0
        self._FAILURE_THRESHOLD = 3
        self._COOLDOWN_TICKS = 30  # 10s * 30 = 5 minutes

        self._tick_count = 0
        self._heartbeat_every_ticks = 6
        self._last_heartbeat_signature = None
        self._last_heartbeat_logged_at = 0.0

    def _get_all_active_codes(self) -> set[str]:
        codes = set()
        mw = self.main_window

        def _normalize_a_code(code):
            code = str(code).strip()
            return code if _A_SHARE_CODE_RE.match(code) else None

        def _extract(model_data):
            for row in model_data:
                code = _normalize_a_code(row.get("代码"))
                if code:
                    codes.add(code)

        if hasattr(mw, "tab_scan") and mw.tab_scan.source_model:
            _extract(mw.tab_scan.source_model.row_data)

        if hasattr(mw, "tab_rt") and mw.tab_rt.source_model:
            _extract(mw.tab_rt.source_model.row_data)

        if hasattr(mw, "tab_watchlist") and getattr(mw.tab_watchlist, "model", None):
            _extract(mw.tab_watchlist.model.row_data)

        if hasattr(mw, "tab_foreign_block") and hasattr(mw.tab_foreign_block, "_block_trade_codes"):
            for code in mw.tab_foreign_block._block_trade_codes:
                normalized = _normalize_a_code(code)
                if normalized:
                    codes.add(normalized)

        if hasattr(mw, "tab_na_daily") and getattr(mw.tab_na_daily, "model", None):
            _extract(mw.tab_na_daily.model.row_data)

        if hasattr(mw, "tab_earnings") and getattr(mw.tab_earnings, "model", None):
            _extract(mw.tab_earnings.model.row_data)

        if hasattr(mw, "tab_lhb") and getattr(mw.tab_lhb, "model", None):
            _extract(mw.tab_lhb.model.row_data)

        return codes

    def _reset_failures(self):
        self._consecutive_failures = 0

    def _record_failure(self, reason: str):
        self._consecutive_failures += 1
        log.warning(
            f"[报价站] 抓取失败({self._consecutive_failures}/{self._FAILURE_THRESHOLD}): {reason}"
        )
        if self._consecutive_failures < self._FAILURE_THRESHOLD:
            return

        self._circuit_breaker_cooldown = max(self._circuit_breaker_cooldown, self._COOLDOWN_TICKS)
        log.error("[报价站] 连续失败达到阈值，进入 5 分钟冷却")

        if self.data_provider is not None:
            try:
                self.data_provider._enter_realtime_cooldown(
                    f"报价站连续失败达到阈值：{reason}",
                    cooldown_sec=300,
                )
            except Exception as exc:
                log.debug(f"[报价站] 触发 provider 冷却失败: {exc}")

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

    def _run_maintenance(self, active_codes_count: int | None = None):
        stats = {}
        if self.data_provider is not None:
            try:
                stats = self.data_provider.compact_runtime_caches()
            except Exception as exc:
                log.debug(f"[报价站] 运行时缓存清理失败: {exc}")

        total_threads, pytdx_threads = self._collect_thread_health()
        if self.data_provider is not None:
            try:
                if self.data_provider.protect_against_thread_anomaly(pytdx_threads):
                    self._circuit_breaker_cooldown = max(self._circuit_breaker_cooldown, self._COOLDOWN_TICKS)
            except Exception as exc:
                log.debug(f"[报价站] pytdx 线程防护失败: {exc}")

        if self._tick_count % self._heartbeat_every_ticks != 0:
            return

        runtime_stats = stats.get("rt_runtime", {}) if isinstance(stats, dict) else {}
        last_success_at = float(runtime_stats.get("last_success_at") or 0)
        last_success_text = (
            time.strftime("%H:%M:%S", time.localtime(last_success_at))
            if last_success_at > 0
            else "-"
        )
        cooldown_until = float(runtime_stats.get("cooldown_until") or 0)
        cooldown_left = max(0, int(cooldown_until - time.time()))
        now_ts = time.time()
        heartbeat_signature = (
            active_codes_count if active_codes_count is not None else "-",
            stats.get("rt_quote_cache_size", 0) if isinstance(stats, dict) else 0,
            stats.get("history_symbol_count", 0) if isinstance(stats, dict) else 0,
            runtime_stats.get("inflight", 0),
            last_success_text,
            runtime_stats.get("consecutive_failures", 0),
            runtime_stats.get("reconnect_count", 0),
            cooldown_left,
            runtime_stats.get("worker_alive", False),
            total_threads,
            pytdx_threads,
        )
        should_log = MarketCalendar.is_quote_refresh_time()
        if not should_log:
            signature_changed = heartbeat_signature != self._last_heartbeat_signature
            interval_reached = (now_ts - self._last_heartbeat_logged_at) >= 1800
            should_log = signature_changed or interval_reached

        if not should_log:
            return

        log.info(
            "[报价站] 心跳 "
            f"标的={active_codes_count if active_codes_count is not None else '-'} "
            f"实时缓存={stats.get('rt_quote_cache_size', 0) if isinstance(stats, dict) else 0} "
            f"历史缓存={stats.get('history_symbol_count', 0) if isinstance(stats, dict) else 0} "
            f"飞行中={runtime_stats.get('inflight', 0)} "
            f"上次成功={last_success_text} "
            f"连败={runtime_stats.get('consecutive_failures', 0)} "
            f"重连={runtime_stats.get('reconnect_count', 0)} "
            f"冷却剩余={cooldown_left}s "
            f"工作线程存活={runtime_stats.get('worker_alive', False)} "
            f"总线程={total_threads} "
            f"pytdx线程={pytdx_threads}"
        )
        self._last_heartbeat_signature = heartbeat_signature
        self._last_heartbeat_logged_at = now_ts

    def _emit_off_market_snapshot(self, codes: set[str]):
        if self._off_market_snapshot_emitted or not codes or self.data_provider is None:
            return

        try:
            quotes = self.data_provider.fetch_realtime_quotes_batch(list(codes))
        except Exception as exc:
            log.warning(f"[报价站] 盘后离线快照构建失败: {exc}")
            return

        self._off_market_snapshot_emitted = True
        has_valid = any(float(quote.get("close", 0) or 0) > 0 for quote in quotes.values())
        if has_valid:
            event_bus.sig_rt_quotes.emit(quotes)

    @pyqtSlot()
    def _trigger_fetch(self):
        if self._closed:
            return

        self._tick_count += 1
        quote_refreshable = MarketCalendar.is_quote_refresh_time()
        if quote_refreshable:
            self._off_market_snapshot_emitted = False

        codes = self._get_all_active_codes()
        self._run_maintenance(active_codes_count=len(codes) if codes else 0)
        if not codes:
            return

        if not quote_refreshable:
            self._emit_off_market_snapshot(codes)
            return

        if not self.data_provider or not self.data_provider.is_online():
            return

        if self._is_fetching:
            batch_timeout_sec = float(getattr(self.data_provider, "_rt_api_call_timeout_sec", 8.0) or 8.0)
            expected_batches = max(1, (len(codes) + 79) // 80)
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

        if self._circuit_breaker_cooldown > 0:
            self._circuit_breaker_cooldown -= 1
            if self._circuit_breaker_cooldown == 0:
                log.info("[报价站] 冷却结束，恢复轮询")
            return

        provider_stats = self.data_provider.get_realtime_runtime_stats()
        if time.time() < float(provider_stats.get("cooldown_until") or 0):
            self._circuit_breaker_cooldown = max(self._circuit_breaker_cooldown, self._COOLDOWN_TICKS)
            return

        self._is_fetching = True
        self._fetch_start_time = time.time()
        self._fetch_warned_slow = False
        self._fetch_generation += 1
        fetch_token = self._fetch_generation

        def _bg_task():
            quotes = self.data_provider.fetch_realtime_quotes_batch(list(codes))
            return {
                "quotes": quotes,
                "provider_stats": self.data_provider.get_realtime_runtime_stats(),
            }

        def _on_result(payload):
            if fetch_token != self._fetch_generation:
                return

            self._is_fetching = False
            self._fetch_start_time = 0.0
            self._fetch_warned_slow = False
            if self._closed:
                return

            payload = payload or {}
            quotes = payload.get("quotes") or {}
            provider_stats = payload.get("provider_stats") or {}
            cooldown_until = float(provider_stats.get("cooldown_until") or 0)
            provider_failed = (
                int(provider_stats.get("consecutive_failures") or 0) > 0
                or time.time() < cooldown_until
            )

            if provider_failed:
                self._record_failure(provider_stats.get("last_error") or "提供方返回离线兜底快照")
            else:
                self._reset_failures()

            has_valid = any(float(quote.get("close", 0) or 0) > 0 for quote in quotes.values())
            if has_valid:
                event_bus.sig_rt_quotes.emit(quotes)

        def _on_error(err_msg):
            if fetch_token != self._fetch_generation:
                return

            self._is_fetching = False
            self._fetch_start_time = 0.0
            self._fetch_warned_slow = False
            if self._closed:
                return

            self._record_failure(err_msg or "后台抓取异常")
            log.error(f"[报价站] 后台抓取异常: {err_msg}")

        task_manager.run_in_background(
            _bg_task,
            on_success=_on_result,
            on_error=_on_error,
            task_id="central_quotes",
        )

    def shutdown(self):
        self._closed = True
        self._timer.stop()
        self._fetch_generation += 1
