from __future__ import annotations

import time

import pandas as pd

from core.logger import get_logger
from domains.market_calendar import MarketCalendar
from domains.scan.indicator_service import IndicatorService
from vcp.realtime_quote_runtime import RealtimeQuoteRuntime

_log = get_logger(__name__)


class RealtimeQuoteProvider:
    """实时行情运行时与盘中图表服务。"""

    def __init__(self, provider, *, logger=None) -> None:
        self.provider = provider
        self._log = logger or _log

    def archive_runtime(self, runtime):
        if runtime is None:
            return
        provider = self.provider
        stats = self._runtime_snapshot(runtime)
        if not stats:
            return
        provider._rt_runtime_last_success_at = max(
            float(getattr(provider, "_rt_runtime_last_success_at", 0) or 0),
            float(stats.get("last_success_at") or 0),
        )
        provider._rt_runtime_reconnect_archived += int(stats.get("reconnect_count") or 0)

    @staticmethod
    def _runtime_is_alive(runtime) -> bool:
        if runtime is None:
            return False
        try:
            return bool(runtime.is_alive())
        except (AttributeError, RuntimeError, TypeError, ValueError):
            return False

    @staticmethod
    def _runtime_snapshot(runtime) -> dict:
        if runtime is None:
            return {}
        try:
            return dict(runtime.snapshot() or {})
        except (AttributeError, RuntimeError, TypeError, ValueError):
            return {}

    def _clear_retired_runtime_if_stopped(self, runtime) -> None:
        provider = self.provider
        with provider._rt_runtime_lock:
            is_current = getattr(provider, "_rt_runtime_retiring", None) is runtime
            if is_current and not self._runtime_is_alive(runtime):
                provider._rt_runtime_retiring = None

    def _retire_runtime(self, runtime) -> dict:
        if runtime is None:
            return {}
        runtime_stats = self._runtime_snapshot(runtime)
        self.archive_runtime(runtime)
        try:
            runtime.close()
        except (AttributeError, OSError, RuntimeError, TypeError, ValueError) as exc:
            self._log.debug(f"[实时行情] 关闭旧运行时失败: {exc}")
        finally:
            self._clear_retired_runtime_if_stopped(runtime)
        return runtime_stats

    def _owner_thread_alive(self, runtime_stats: dict, runtime) -> bool:
        explicit = runtime_stats.get("owner_thread_alive")
        if explicit is None:
            explicit = runtime_stats.get("worker_alive")
        return self._runtime_is_alive(runtime) if explicit is None else bool(explicit)

    def ensure_runtime(self):
        provider = self.provider
        now = time.time()
        if now < float(getattr(provider, "_rt_runtime_cooldown_until", 0) or 0):
            remaining = max(1, int(provider._rt_runtime_cooldown_until - now))
            raise TimeoutError(f"实时行情冷却中，剩余 {remaining}s")

        with provider._rt_runtime_lock:
            retiring = getattr(provider, "_rt_runtime_retiring", None)
            if retiring is not None:
                if self._runtime_is_alive(retiring):
                    raise RuntimeError("实时行情运行时正在重置")
                provider._rt_runtime_retiring = None
            runtime = provider._rt_runtime
            if runtime is not None and self._runtime_is_alive(runtime):
                return runtime
            if runtime is not None:
                self.archive_runtime(runtime)
                provider._rt_runtime = None
            runtime = RealtimeQuoteRuntime(provider, self._log)
            provider._rt_runtime = runtime
            return runtime

    def reset_runtime(
        self,
        reason: str = "",
        *,
        log_warning: bool = True,
        penalize_server: bool = True,
    ):
        provider = self.provider
        runtime_stats = {}
        with provider._rt_runtime_lock:
            runtime = provider._rt_runtime
            if runtime is not None:
                provider._rt_runtime = None
                provider._rt_runtime_retiring = runtime

        runtime_stats = self._retire_runtime(runtime)

        failed_server = runtime_stats.get("server")
        if penalize_server and failed_server:
            provider._deprioritize_server(failed_server, reason)
        if reason:
            provider._rt_runtime_last_error = reason
            if log_warning:
                self._log.warning(f"[实时行情] {reason}")

    def register_success(self):
        provider = self.provider
        provider._rt_runtime_consecutive_failures = 0
        provider._rt_runtime_last_error = ""
        provider._rt_runtime_cooldown_until = 0.0
        provider._rt_runtime_last_success_at = max(
            float(getattr(provider, "_rt_runtime_last_success_at", 0) or 0),
            time.time(),
        )

    def enter_cooldown(self, reason: str, cooldown_sec: float | None = None):
        provider = self.provider
        cooldown_sec = float(cooldown_sec) if cooldown_sec is not None else float(provider._rt_runtime_cooldown_sec)
        provider._rt_runtime_cooldown_until = time.time() + cooldown_sec
        provider._rt_runtime_last_error = reason
        self.reset_runtime(reason, log_warning=False)
        self._log.error(f"[实时行情] 进入冷却 {int(cooldown_sec)}s: {reason}")

    def register_failure(self, reason: str):
        provider = self.provider
        provider._rt_runtime_consecutive_failures += 1
        provider._rt_runtime_last_error = reason
        if provider._rt_runtime_consecutive_failures >= provider._rt_runtime_failure_threshold:
            self.enter_cooldown(reason)
            return
        self.reset_runtime(reason)

    def submit_request(self, params_list, timeout_sec: float):
        provider = self.provider
        runtime = self.ensure_runtime()
        quotes = runtime.request(params_list, timeout_sec)
        runtime_stats = runtime.snapshot()
        provider._rt_runtime_last_success_at = max(
            float(getattr(provider, "_rt_runtime_last_success_at", 0) or 0),
            float(runtime_stats.get("last_success_at") or 0),
        )
        return quotes

    def get_runtime_stats(self) -> dict:
        provider = self.provider
        with provider._rt_runtime_lock:
            runtime = provider._rt_runtime
            retiring = getattr(provider, "_rt_runtime_retiring", None)

        observed_runtime = runtime if runtime is not None else retiring
        runtime_stats = self._runtime_snapshot(observed_runtime)
        owner_thread_alive = self._owner_thread_alive(runtime_stats, observed_runtime)
        return {
            "inflight": int(runtime_stats.get("inflight") or 0),
            "last_success_at": max(
                float(getattr(provider, "_rt_runtime_last_success_at", 0) or 0),
                float(runtime_stats.get("last_success_at") or 0),
            ),
            "consecutive_failures": int(getattr(provider, "_rt_runtime_consecutive_failures", 0) or 0),
            "reconnect_count": int(getattr(provider, "_rt_runtime_reconnect_archived", 0) or 0)
            + int(runtime_stats.get("reconnect_count") or 0),
            "cooldown_until": float(getattr(provider, "_rt_runtime_cooldown_until", 0) or 0),
            "worker_alive": owner_thread_alive,
            "owner_thread_alive": owner_thread_alive,
            "reset_in_progress": bool(retiring is not None and self._runtime_is_alive(retiring)),
            "last_error": getattr(provider, "_rt_runtime_last_error", ""),
        }

    def protect_against_thread_anomaly(self, pytdx_thread_count: int, threshold: int | None = None) -> bool:
        provider = self.provider
        threshold = int(threshold or provider._rt_runtime_thread_threshold)
        if pytdx_thread_count <= threshold:
            return False
        reason = f"pytdx 线程异常: {pytdx_thread_count}>{threshold}"
        self.enter_cooldown(reason)
        return True

    def build_realtime_df(self, code, quote):
        provider = self.provider
        hist_df = provider.get_data(code)
        if hist_df is None or len(hist_df) < 10:
            return None
        if quote.get("close", 0) <= 0 or quote.get("open", 0) <= 0:
            return None

        slice_df = hist_df.iloc[-260:]
        combined = slice_df.copy(deep=True)
        rt_date_str = quote.get("date")
        if rt_date_str:
            rt_date = pd.Timestamp(rt_date_str)
        else:
            try:
                trade_dt = MarketCalendar.get_latest_trade_date()
                if trade_dt:
                    rt_date = pd.Timestamp(trade_dt)
                else:
                    rt_date = pd.Timestamp(MarketCalendar.today("CN"))
            except (AttributeError, RuntimeError, TypeError, ValueError) as exc:
                self._log.debug(f"[盘中K线] 获取最近交易日失败: {exc}")
                rt_date = pd.Timestamp(MarketCalendar.today("CN"))

        now = MarketCalendar.now("CN")
        hour, minute = now.hour, now.minute
        ratio = 1.0
        if MarketCalendar.is_market_active():
            if 9 <= hour <= 11:
                passed = (
                    (minute - 30)
                    if hour == 9
                    else (30 + minute if hour == 10 else (120 if minute > 30 else 90 + minute))
                )
            elif 13 <= hour < 15:
                passed = 120 + (hour - 13) * 60 + minute
            elif hour == 15:
                passed = 240
            else:
                passed = 120
            passed = max(1, passed)
            ratio = 240.0 / passed

        scaled_quote = dict(quote)
        if ratio > 1.05 and "volume" in scaled_quote:
            scaled_quote["volume"] = float(scaled_quote["volume"] or 0) * ratio
        if ratio > 1.05 and "amount" in scaled_quote:
            scaled_quote["amount"] = float(scaled_quote["amount"] or 0) * ratio

        if rt_date in slice_df.index:
            cols = [col for col in ["open", "high", "low", "close", "volume", "amount"] if col in combined.columns]
            today_row = pd.DataFrame(
                {col: [scaled_quote.get(col, combined.loc[rt_date, col])] for col in cols},
                index=[rt_date],
            )
            combined.update(today_row)
        else:
            new_row = pd.DataFrame([scaled_quote], index=[rt_date])
            combined = pd.concat([combined, new_row])

        if hasattr(combined, "attrs"):
            combined.attrs.pop("vcp_indicators_ready", None)
            combined.attrs.pop("vcp_core_ready", None)
            combined.attrs.pop("vcp_chart_ready", None)
        return IndicatorService.calculate_indicators(combined)
