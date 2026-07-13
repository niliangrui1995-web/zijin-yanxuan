# -*- coding: utf-8 -*-
"""Worker threads and shared cache state for Asian market tab."""

from __future__ import annotations

import concurrent.futures
import datetime
import threading
import time

from PyQt6.QtCore import QThread, pyqtSignal

import app.services.asian_market_quote_service as quote_service
from app.services.asian_market_cache_service import (
    ASIAN_KLINE_CACHE as JSON_CACHE,
)
from app.services.asian_market_cache_service import (
    ASIAN_REALTIME_CACHE as RT_JSON_CACHE,
)
from app.services.asian_market_cache_service import (
    write_realtime_quote_cache,
)
from app.services.ui_market_calendar_service import MarketCalendar
from app.services.ui_task_lifecycle_service import (
    CancellationToken,
    TaskCancelledError,
    TaskDeadlineExceeded,
)
from core.logger import get_logger

__all__ = ["JSON_CACHE", "RT_JSON_CACHE", "requests"]

log = get_logger(__name__)

GLOBAL_ASIAN_RT_CACHE: dict[str, dict] = {}
_ASIAN_MARKET_CODES = ("TW", "HK", "T", "KS")
_YF_FETCH_MAX_WORKERS = 2
_FETCH_UPDATES_TIMEOUT_SEC = 45
_FETCH_TIMEOUT_MARKET_BACKOFF_SEC = 30 * 60
_FETCH_TIMEOUT_CYCLE_BACKOFF_SEC = 30 * 60
_OPTIONAL_NETWORK_MIN_REMAINING_SEC = 25
_SOURCE_PAYLOAD_CODE_BACKOFF_SEC = 10 * 60

AsianRealtimePayloadError = quote_service.AsianRealtimePayloadError
build_yf_session = quote_service.build_yf_session
get_yf_rate_limit_status = quote_service.get_yf_rate_limit_status
is_yf_rate_limit_error = quote_service.is_yf_rate_limit_error
mark_yf_rate_limited = quote_service.mark_yf_rate_limited
requests = quote_service.requests_module
yf = quote_service.yf

_fetch_tw_realtime_quote = quote_service.fetch_tw_realtime_quote
_fetch_hk_realtime_quote = quote_service.fetch_hk_realtime_quote
_fetch_kr_realtime_quote = quote_service.fetch_kr_realtime_quote
_fetch_jp_realtime_quote = quote_service.fetch_jp_realtime_quote
_fetch_yfinance_realtime_quote = quote_service.fetch_yfinance_realtime_quote
_fetch_twse_pe = quote_service.fetch_twse_pe
_fetch_tpex_pe = quote_service.fetch_tpex_pe
_fetch_kr_naver_pe = quote_service.fetch_kr_naver_pe
_fetch_jp_yahoo_pe = quote_service.fetch_jp_yahoo_pe
_fetch_jp_kabutan_pe = quote_service.fetch_jp_kabutan_pe
_parse_jp_realtime_page = quote_service.parse_jp_realtime_page
_parse_jp_yahoo_pe_from_html = quote_service.parse_jp_yahoo_pe_from_html
_to_float = quote_service.to_float
_round_pct = quote_service.round_pct
_format_cooldown_eta = quote_service.format_cooldown_eta


def sync_asian_kline_cache(*args, **kwargs):
    from app.services.asian_market_service import sync_asian_kline_cache as _sync_asian_kline_cache

    return _sync_asian_kline_cache(*args, **kwargs)


def infer_asian_markets(codes) -> list[str]:
    markets: list[str] = []
    for raw_code in codes or []:
        code = str(raw_code or "").strip()
        if not code:
            continue
        market = MarketCalendar.normalize_market(MarketCalendar.infer_market(code))
        if market not in _ASIAN_MARKET_CODES or market in markets:
            continue
        markets.append(market)
    return markets or list(_ASIAN_MARKET_CODES)


def is_asian_quote_refresh_time(codes) -> bool:
    return any(MarketCalendar.is_quote_refresh_time(market) for market in infer_asian_markets(codes))


def _asian_quote_fetch_priority(code: str) -> int:
    suffix = str(code or "").strip().upper().split(".")[-1]
    return {
        "HK": 0,
        "KS": 1,
        "TW": 2,
        "TWO": 2,
        "T": 3,
    }.get(suffix, 4)


def _asian_market_suffix(code: str) -> str:
    return str(code or "").strip().upper().split(".")[-1]


def _asian_quote_market(code: str) -> str:
    return MarketCalendar.normalize_market(MarketCalendar.infer_market(code))


def _is_code_quote_refresh_time(code: str) -> bool:
    market = _asian_quote_market(code)
    if market not in _ASIAN_MARKET_CODES:
        return True
    return MarketCalendar.is_quote_refresh_time(market)


def _filter_open_market_codes(codes) -> tuple[list[str], list[str]]:
    open_codes: list[str] = []
    closed_markets: set[str] = set()
    for code in codes or []:
        if _is_code_quote_refresh_time(code):
            open_codes.append(code)
            continue
        closed_markets.add(_asian_quote_market(code))
    return open_codes, sorted(closed_markets)


def _seconds_until_monotonic(deadline: float | None) -> float | None:
    if deadline is None:
        return None
    return float(deadline) - time.monotonic()


def _has_optional_network_budget(deadline: float | None) -> bool:
    remaining = _seconds_until_monotonic(deadline)
    return remaining is None or remaining >= _OPTIONAL_NETWORK_MIN_REMAINING_SEC


def save_global_asian_rt_cache() -> None:
    try:
        write_realtime_quote_cache(GLOBAL_ASIAN_RT_CACHE)
    except (PermissionError, OSError, TypeError, ValueError) as exc:
        log.error(f"[AsianTab] persist RT cache failed: {exc}")


def _fetch_asian_pe_fallback(code: str, http_session) -> tuple[float | None, str]:
    return quote_service.fetch_asian_pe_fallback(
        code,
        http_session,
        rate_limit_status=get_yf_rate_limit_status,
        twse_fetcher=_fetch_twse_pe,
        tpex_fetcher=_fetch_tpex_pe,
        kr_fetcher=_fetch_kr_naver_pe,
        jp_yahoo_fetcher=_fetch_jp_yahoo_pe,
        jp_kabutan_fetcher=_fetch_jp_kabutan_pe,
    )


def fetch_asian_realtime_quote(
    code: str,
    *,
    yf_session=None,
    allow_yfinance_fallback: bool = True,
    raise_on_source_payload_error: bool = False,
):
    return quote_service.fetch_asian_realtime_quote(
        code,
        yf_session=yf_session,
        allow_yfinance_fallback=allow_yfinance_fallback,
        raise_on_source_payload_error=raise_on_source_payload_error,
        yf_module=yf,
        rate_limit_status=get_yf_rate_limit_status,
        rate_limit_error=is_yf_rate_limit_error,
        mark_rate_limited=mark_yf_rate_limited,
        tw_fetcher=_fetch_tw_realtime_quote,
        hk_fetcher=_fetch_hk_realtime_quote,
        kr_fetcher=_fetch_kr_realtime_quote,
        jp_fetcher=_fetch_jp_realtime_quote,
        yf_fetcher=_fetch_yfinance_realtime_quote,
    )


class AsianMarketWorker(QThread):
    progress = pyqtSignal(str)
    result_ready = pyqtSignal(dict)

    def __init__(self, codes):
        super().__init__()
        self.codes = list(codes or [])
        self._is_running = True
        self._pause_mode = False
        self._manual_refresh_requested = False
        self._cycle_done = threading.Event()
        self._cycle_done.set()
        self._last_status = ""
        self._market_backoff_until: dict[str, float] = {}
        self._code_backoff_until: dict[str, float] = {}
        self._backoff_lock = threading.Lock()
        self._timeout_backoff_until = 0.0
        self._fetch_deadline_monotonic: float | None = None
        self._last_fetch_timed_out = False
        self._last_fetch_source_degraded = False

    def stop(self):
        self._is_running = False
        self._manual_refresh_requested = False
        self._cycle_done.set()

    def pause_for_cache_sync(self):
        self._pause_mode = True

    def resume_auto_refresh(self):
        self._pause_mode = False

    def trigger_refresh(self):
        self._manual_refresh_requested = True

    def wait_for_cycle_idle(self, timeout_sec: float = 30.0) -> bool:
        return self._cycle_done.wait(timeout_sec)

    def _emit_status_once(self, message: str):
        if message != self._last_status:
            self._last_status = message
            self.progress.emit(message)

    def _sleep_with_break(self, seconds: float) -> bool:
        deadline = time.time() + seconds
        while self._is_running and time.time() < deadline:
            time.sleep(0.1)
        return self._is_running

    def _handle_optional_yahoo_error(self, code: str, exc: Exception, context: str) -> bool:
        if is_yf_rate_limit_error(exc):
            remaining_sec = mark_yf_rate_limited(exc)
            log.warning(
                "[AsianTab] %s 触发 Yahoo Finance 限流 %s: %s | 冷却 %s",
                context,
                code,
                exc,
                _format_cooldown_eta(remaining_sec),
            )
            return True
        if isinstance(
            exc,
            (
                AttributeError,
                KeyError,
                OSError,
                RuntimeError,
                TypeError,
                ValueError,
            ),
        ):
            log.debug(f"[AsianTab] {context}失败 {code}: {exc}")
            return False
        raise exc

    def _prune_market_backoff(self, now_ts: float | None = None) -> None:
        now = time.time() if now_ts is None else float(now_ts)
        self._market_backoff_until = {
            market: until_ts
            for market, until_ts in self._market_backoff_until.items()
            if float(until_ts or 0.0) > now
        }

    def _prune_code_backoff(self, now_ts: float | None = None) -> None:
        now = time.time() if now_ts is None else float(now_ts)
        with self._backoff_lock:
            self._code_backoff_until = {
                code: until_ts
                for code, until_ts in self._code_backoff_until.items()
                if float(until_ts or 0.0) > now
            }

    def _mark_market_backoff(self, market: str, *, now_ts: float | None = None) -> None:
        normalized_market = str(market or "").strip().upper()
        if not normalized_market:
            return
        now = time.time() if now_ts is None else float(now_ts)
        self._market_backoff_until[normalized_market] = now + _FETCH_TIMEOUT_MARKET_BACKOFF_SEC

    def _mark_code_backoff(self, code: str, *, now_ts: float | None = None) -> None:
        normalized_code = str(code or "").strip().upper()
        if not normalized_code:
            return
        now = time.time() if now_ts is None else float(now_ts)
        with self._backoff_lock:
            self._code_backoff_until[normalized_code] = now + _SOURCE_PAYLOAD_CODE_BACKOFF_SEC

    def _mark_timeout_backoff(self, *, now_ts: float | None = None, duration_sec: float | None = None) -> None:
        now = time.time() if now_ts is None else float(now_ts)
        duration = float(duration_sec or _FETCH_TIMEOUT_CYCLE_BACKOFF_SEC)
        self._timeout_backoff_until = max(
            float(self._timeout_backoff_until or 0.0),
            now + duration,
        )

    def defer_auto_refresh(self, seconds: float, reason: str = "") -> None:
        duration = max(0.0, float(seconds or 0.0))
        if duration <= 0:
            return
        self._mark_timeout_backoff(duration_sec=duration)
        if reason:
            log.info("[AsianTab] Auto refresh deferred for %.0fs: %s", duration, reason)

    def _timeout_backoff_remaining(self, *, now_ts: float | None = None) -> float:
        now = time.time() if now_ts is None else float(now_ts)
        remaining = float(self._timeout_backoff_until or 0.0) - now
        if remaining <= 0:
            self._timeout_backoff_until = 0.0
            return 0.0
        return remaining

    def _clear_timeout_backoff(self) -> None:
        self._timeout_backoff_until = 0.0

    def _is_market_backoff_active(self, code: str, *, now_ts: float | None = None) -> bool:
        market = _asian_market_suffix(code)
        if not market:
            return False
        now = time.time() if now_ts is None else float(now_ts)
        return float(self._market_backoff_until.get(market, 0.0) or 0.0) > now

    def _is_code_backoff_active(self, code: str, *, now_ts: float | None = None) -> bool:
        normalized_code = str(code or "").strip().upper()
        if not normalized_code:
            return False
        now = time.time() if now_ts is None else float(now_ts)
        with self._backoff_lock:
            return float(self._code_backoff_until.get(normalized_code, 0.0) or 0.0) > now

    def _mark_source_payload_degraded(self) -> None:
        with self._backoff_lock:
            self._last_fetch_source_degraded = True

    def _source_payload_degraded(self) -> bool:
        with self._backoff_lock:
            return bool(self._last_fetch_source_degraded)

    def _fetch_yahoo_enrichment(self, code: str, yf_session, *, allow_network: bool = True):
        return quote_service.fetch_yahoo_enrichment(
            code,
            yf_session,
            allow_network=allow_network,
            yf_module=yf,
            rate_limit_status=get_yf_rate_limit_status,
            error_handler=self._handle_optional_yahoo_error,
        )

    def _refresh_pe_if_needed(
        self,
        code: str,
        *,
        ticker,
        info_session,
        pe_value,
        pe_source: str,
        pe_updated_at: float,
        allow_optional_network: bool = True,
    ):
        return quote_service.refresh_pe_if_needed(
            code,
            ticker=ticker,
            info_session=info_session,
            pe_value=pe_value,
            pe_source=pe_source,
            pe_updated_at=pe_updated_at,
            allow_optional_network=allow_optional_network,
            yf_module=yf,
            rate_limit_status=get_yf_rate_limit_status,
            quote_refresh_time=MarketCalendar.is_quote_refresh_time,
            fallback_fetcher=_fetch_asian_pe_fallback,
            error_handler=self._handle_optional_yahoo_error,
            now=time.time,
        )

    def _fetch_single_code(self, code: str, yf_session, info_session):
        deadline = getattr(self, "_fetch_deadline_monotonic", None)
        remaining = _seconds_until_monotonic(deadline)
        if remaining is not None and remaining <= 0:
            return code, None
        try:
            payload = quote_service.fetch_normalized_asian_quote(
                code,
                yf_session=yf_session,
                info_session=info_session,
                cached_payload=GLOBAL_ASIAN_RT_CACHE.get(code, {}) or {},
                allow_optional_network=_has_optional_network_budget(deadline),
                realtime_fetcher=fetch_asian_realtime_quote,
                enrichment_fetcher=self._fetch_yahoo_enrichment,
                pe_refresher=self._refresh_pe_if_needed,
                error_handler=self._handle_optional_yahoo_error,
            )
        except AsianRealtimePayloadError as exc:
            self._mark_code_backoff(code)
            self._mark_source_payload_degraded()
            log.debug(
                "[AsianTab] 替代实时源不可解析 %s: %s，已跳过单票回退并短退避 %s",
                code,
                exc,
                _format_cooldown_eta(_SOURCE_PAYLOAD_CODE_BACKOFF_SEC),
            )
            return code, None
        if payload is None:
            return code, None
        GLOBAL_ASIAN_RT_CACHE[code] = payload
        return code, payload

    def _fetch_updates(self, *, open_markets_only: bool = False) -> dict:
        updates = {}
        yf_session = build_yf_session()
        info_session = yf_session
        raw_codes = list(dict.fromkeys(str(code).strip() for code in self.codes if str(code).strip()))
        if not raw_codes:
            return updates
        closed_markets: list[str] = []
        if open_markets_only:
            raw_codes, closed_markets = _filter_open_market_codes(raw_codes)
            if closed_markets:
                log.info("[AsianTab] Skip closed markets in auto refresh: %s", ",".join(closed_markets))
            if not raw_codes:
                return updates
        now_ts = time.time()
        self._prune_market_backoff(now_ts)
        self._prune_code_backoff(now_ts)
        skipped_markets = sorted(
            {_asian_market_suffix(code) for code in raw_codes if self._is_market_backoff_active(code, now_ts=now_ts)}
        )
        skipped_codes = sorted({code for code in raw_codes if self._is_code_backoff_active(code, now_ts=now_ts)})
        eligible_codes = [
            code
            for code in raw_codes
            if not self._is_market_backoff_active(code, now_ts=now_ts)
            and not self._is_code_backoff_active(code, now_ts=now_ts)
        ]
        if skipped_markets:
            log.info("[AsianTab] Skip markets in short backoff: %s", ",".join(skipped_markets))
        if skipped_codes:
            log.debug("[AsianTab] Skip codes in source-payload backoff: %s", ",".join(skipped_codes))
        if not eligible_codes:
            return updates
        codes = [
            code
            for _idx, code in sorted(
                enumerate(eligible_codes),
                key=lambda item: (_asian_quote_fetch_priority(item[1]), item[0]),
            )
        ]

        max_workers = max(1, min(_YF_FETCH_MAX_WORKERS, len(codes)))
        deadline = time.monotonic() + _FETCH_UPDATES_TIMEOUT_SEC
        previous_deadline = getattr(self, "_fetch_deadline_monotonic", None)
        self._fetch_deadline_monotonic = deadline
        self._last_fetch_timed_out = False
        with self._backoff_lock:
            self._last_fetch_source_degraded = False
        timed_out = False
        cancelled = False
        executor = concurrent.futures.ThreadPoolExecutor(max_workers=max_workers)
        futures = {executor.submit(self._fetch_single_code, code, yf_session, info_session): code for code in codes}
        try:
            for future in concurrent.futures.as_completed(futures, timeout=_FETCH_UPDATES_TIMEOUT_SEC):
                if not self._is_running:
                    cancelled = True
                    break
                code = futures[future]
                try:
                    result_code, payload = future.result(timeout=1)
                    if payload:
                        updates[result_code] = payload
                except Exception as exc:
                    if is_yf_rate_limit_error(exc):
                        remaining_sec = mark_yf_rate_limited(exc)
                        log.warning(
                            "[AsianTab] future 结果触发 Yahoo Finance 限流 %s: %s | 冷却 %s",
                            code,
                            exc,
                            _format_cooldown_eta(remaining_sec),
                        )
                        continue
                    if isinstance(
                        exc,
                        (
                            concurrent.futures.CancelledError,
                            AttributeError,
                            KeyError,
                            OSError,
                            RuntimeError,
                            TypeError,
                            ValueError,
                        ),
                    ):
                        log.debug(f"[AsianTab] 单票拉取失败 {code}: {exc}")
                        continue
                    raise
        except concurrent.futures.TimeoutError:
            timed_out = True
            self._last_fetch_timed_out = True
            unfinished_markets = sorted(
                {_asian_market_suffix(code) for future, code in futures.items() if not future.done()}
            )
            for market in unfinished_markets:
                self._mark_market_backoff(market)
            self._mark_timeout_backoff()
            if unfinished_markets:
                log.warning("[AsianTab] Timeout degraded markets: %s", ",".join(unfinished_markets))
            log.warning(
                "[AsianTab] 本轮亚洲报价抓取达到 %s 秒上限，已取消未完成请求并启用短暂市场降级",
                _FETCH_UPDATES_TIMEOUT_SEC,
            )
        finally:
            for future in futures:
                if not future.done():
                    future.cancel()
            executor.shutdown(wait=not (timed_out or cancelled or not self._is_running), cancel_futures=True)
            self._fetch_deadline_monotonic = previous_deadline

        return updates

    def run(self):
        while self._is_running:
            auto_refresh_allowed = is_asian_quote_refresh_time(self.codes)
            manual_refresh = self._manual_refresh_requested

            if self._pause_mode and not manual_refresh:
                self._emit_status_once("亚洲市场后台刷新已暂停，等待缓存同步完成")
                if not self._sleep_with_break(0.5):
                    return
                continue

            if not auto_refresh_allowed and not manual_refresh:
                self._emit_status_once("盘后静默中，可点击刷新亚洲市场")
                if not self._sleep_with_break(1.0):
                    return
                continue

            rate_limit_status = get_yf_rate_limit_status()
            if rate_limit_status["active"]:
                self._emit_status_once(
                    "Yahoo Finance 限流冷却中，约 "
                    f"{_format_cooldown_eta(rate_limit_status['remaining_sec'])} 后重试，仍尝试交易所实时报价"
                )
            else:
                self._last_status = ""

            if not manual_refresh:
                timeout_backoff_remaining = self._timeout_backoff_remaining()
                if timeout_backoff_remaining > 0:
                    self._emit_status_once(
                        "亚洲市场后台刷新已短暂降级，约 "
                        f"{_format_cooldown_eta(timeout_backoff_remaining)} 后重试"
                    )
                    if not self._sleep_with_break(min(30.0, timeout_backoff_remaining)):
                        return
                    continue

            self._cycle_done.clear()
            try:
                now = MarketCalendar.now("CN")
                self.progress.emit(f"[{now.strftime('%H:%M:%S')}] 正在拉取亚洲市场最新报价...")
                updates = self._fetch_updates(open_markets_only=not manual_refresh)
                if manual_refresh and updates and not getattr(self, "_last_fetch_timed_out", False):
                    self._clear_timeout_backoff()
                if self._is_running and updates:
                    save_global_asian_rt_cache()
                    source_payload_degraded = self._source_payload_degraded()
                    defer_ui_update = (
                        getattr(self, "_last_fetch_timed_out", False) or source_payload_degraded
                    ) and not manual_refresh
                    if not defer_ui_update:
                        self.result_ready.emit(updates)
                    message = (
                        f"[{datetime.datetime.now().strftime('%H:%M:%S')}] 亚洲市场报价更新完成，获取 {len(updates)} 只"
                    )
                    if defer_ui_update:
                        reason = (
                            "source payload degraded"
                            if source_payload_degraded and not getattr(self, "_last_fetch_timed_out", False)
                            else "timed out"
                        )
                        message = (
                            f"[{datetime.datetime.now().strftime('%H:%M:%S')}] "
                            f"Asian market quote refresh {reason}; cached {len(updates)} updates and deferred UI repaint"
                        )
                    self.progress.emit(message)
                    log.info(f"[AsianTab] {message}")
            except Exception as exc:
                error_text = str(exc)
                if is_yf_rate_limit_error(exc):
                    remaining_sec = mark_yf_rate_limited(exc)
                    hint = (
                        "Yahoo Finance 返回 429，已进入冷却，约 "
                        f"{_format_cooldown_eta(remaining_sec)} 后重试，当前沿用本地缓存"
                    )
                    self.progress.emit(hint)
                    log.warning(f"[AsianTab] {hint} | Native Error: {exc}")
                elif isinstance(exc, (AttributeError, KeyError, OSError, RuntimeError, TypeError, ValueError)):
                    if "Timeout" in error_text or "Connection" in error_text or "Max retries" in error_text:
                        hint = "连接 Yahoo Finance 失败，请检查外网或代理"
                    elif "NoneType" in error_text and "subscriptable" in error_text:
                        hint = "上游返回了空响应，请切换网络后重试"
                    else:
                        hint = f"亚洲行情拉取异常: {error_text}"
                    self.progress.emit(hint)
                    log.error(f"[AsianTab] {hint} | Native Error: {exc}")
                else:
                    raise
            finally:
                self._manual_refresh_requested = False
                self._cycle_done.set()

            if not self._is_running:
                return

            if not auto_refresh_allowed:
                continue

            if not self._sleep_with_break(120.0):
                return


class AsianCacheFetcherThread(QThread):
    def __init__(self, parent=None, *, cancellation_token: CancellationToken | None = None):
        super().__init__(parent)
        self.cancellation_token = cancellation_token or CancellationToken.with_timeout(15 * 60.0)
        self.result_success = False
        self.result_message = ""

    def requestInterruption(self) -> None:
        self.cancellation_token.cancel("thread_interrupted")
        super().requestInterruption()

    def cancel(self, reason: str = "cancelled") -> None:
        self.cancellation_token.cancel(reason)
        super().requestInterruption()

    def run(self):
        try:
            self.cancellation_token.raise_if_cancelled()
            success, message, _report = sync_asian_kline_cache(
                max_workers=3,
                period="1y",
                cancellation_token=self.cancellation_token,
            )
            self.cancellation_token.raise_if_cancelled()
            self.result_success = bool(success)
            self.result_message = str(message or "")
        except (TaskCancelledError, TaskDeadlineExceeded) as exc:
            self.result_success = False
            self.result_message = f"盘后拉取已取消: {exc}"
        except (AttributeError, ImportError, OSError, RuntimeError, TypeError, ValueError) as exc:
            self.result_success = False
            self.result_message = f"盘后拉取异常: {exc}"
