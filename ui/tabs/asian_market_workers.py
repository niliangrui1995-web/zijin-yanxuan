# -*- coding: utf-8 -*-
"""Worker threads and shared cache state for Asian market tab."""

from __future__ import annotations

import concurrent.futures
import datetime
import os
import threading
import time

import yfinance as yf
from PyQt6.QtCore import QThread, pyqtSignal

from core.logger import get_logger
from core.market_calendar import MarketCalendar
from vcp.constants import CACHE_DIR
from vcp.fetchers.yf_session import build_yf_session

log = get_logger(__name__)

JSON_CACHE = os.path.join(CACHE_DIR, "asian_klines_latest.json")
RT_JSON_CACHE = os.path.join(CACHE_DIR, "asian_rt_latest.json")
GLOBAL_ASIAN_RT_CACHE: dict[str, dict] = {}
_ASIAN_MARKET_CODES = ("TW", "HK", "T", "KS")

_USE_CF_PROXY = True


def is_cf_proxy_enabled() -> bool:
    return _USE_CF_PROXY


def set_cf_proxy_enabled(enabled: bool) -> None:
    global _USE_CF_PROXY
    _USE_CF_PROXY = bool(enabled)


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
    return any(
        MarketCalendar.is_quote_refresh_time(market)
        for market in infer_asian_markets(codes)
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

    def _fetch_single_code(self, code: str, yf_session):
        ticker = yf.Ticker(code, session=yf_session)
        fast_info = ticker.fast_info
        df = ticker.history(period="2mo", interval="1d", timeout=15)
        if df.empty:
            return code, None

        close_price = float(fast_info.get("lastPrice") or df.iloc[-1]["Close"])
        day_open = float(fast_info.get("open") or df.iloc[-1]["Open"])
        day_high = float(fast_info.get("dayHigh") or df.iloc[-1]["High"])
        day_low = float(fast_info.get("dayLow") or df.iloc[-1]["Low"])
        prev_close = float(fast_info.get("previousClose", 0))
        if prev_close <= 0:
            prev_close = float(df.iloc[-2]["Close"]) if len(df) >= 2 else float(df.iloc[-1]["Close"])

        pct = 0.0
        if prev_close > 0:
            pct = ((close_price / prev_close) - 1.0) * 100.0

        def _past_pct(days_ago: int) -> float:
            if len(df) <= days_ago:
                return 0.0
            past_close = float(df.iloc[-(days_ago + 1)]["Close"])
            if past_close <= 0:
                return 0.0
            return ((close_price / past_close) - 1.0) * 100.0

        quote_date = None
        try:
            last_idx = df.index[-1]
            if getattr(last_idx, "tzinfo", None) is not None:
                last_idx = last_idx.tz_localize(None)
            quote_date = str(last_idx)[:10]
        except (AttributeError, IndexError, TypeError, ValueError):
            quote_date = None

        payload = {
            "date": quote_date,
            "close": close_price,
            "open": day_open,
            "high": day_high,
            "low": day_low,
            "pct": pct,
            "pct_5": _past_pct(5),
            "pct_10": _past_pct(10),
            "pct_20": _past_pct(20),
            "currency": fast_info.get("currency", "USD"),
            "df_today": df,
        }
        GLOBAL_ASIAN_RT_CACHE[code] = payload
        return code, payload

    def _fetch_updates(self) -> dict:
        updates = {}
        yf_session = build_yf_session(is_cf_proxy_enabled())
        codes = [str(code).strip() for code in self.codes if str(code).strip()]
        if not codes:
            return updates

        executor = concurrent.futures.ThreadPoolExecutor(max_workers=4)
        futures = {
            executor.submit(self._fetch_single_code, code, yf_session): code
            for code in codes
        }
        try:
            for future in concurrent.futures.as_completed(futures, timeout=45):
                if not self._is_running:
                    break
                code = futures[future]
                try:
                    result_code, payload = future.result(timeout=1)
                    if payload:
                        updates[result_code] = payload
                except (
                    concurrent.futures.CancelledError,
                    AttributeError,
                    KeyError,
                    OSError,
                    RuntimeError,
                    TypeError,
                    ValueError,
                ) as exc:
                    log.debug(f"[AsianTab] 单票拉取失败 {code}: {exc}")
        except concurrent.futures.TimeoutError:
            log.warning("[AsianTab] 本轮亚洲报价抓取达到 45 秒上限，等待在途请求收尾")
        finally:
            for future in futures:
                if not future.done():
                    future.cancel()
            executor.shutdown(wait=True, cancel_futures=True)

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

            self._last_status = ""
            self._cycle_done.clear()
            try:
                now = MarketCalendar.now("CN")
                self.progress.emit(f"[{now.strftime('%H:%M:%S')}] 正在拉取亚洲市场最新报价...")
                updates = self._fetch_updates()
                if self._is_running and updates:
                    self.result_ready.emit(updates)
                    message = (
                        f"[{datetime.datetime.now().strftime('%H:%M:%S')}] "
                        f"亚洲市场报价更新完成，获取 {len(updates)} 只"
                    )
                    self.progress.emit(message)
                    log.info(f"[AsianTab] {message}")
            except (AttributeError, KeyError, OSError, RuntimeError, TypeError, ValueError) as exc:
                error_text = str(exc)
                if "Too Many Requests" in error_text or "429" in error_text:
                    hint = "Yahoo Finance 返回 429，请稍后重试或切换网络出口"
                elif "Timeout" in error_text or "Connection" in error_text or "Max retries" in error_text:
                    hint = "连接 Yahoo Finance 失败，请检查外网或代理"
                elif "NoneType" in error_text and "subscriptable" in error_text:
                    hint = "上游返回了空响应，请切换网络后重试"
                else:
                    hint = f"亚洲行情拉取异常: {error_text}"
                self.progress.emit(hint)
                log.error(f"[AsianTab] {hint} | Native Error: {exc}")
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
    def __init__(self, parent=None):
        super().__init__(parent)
        self.result_success = False
        self.result_message = ""

    def run(self):
        try:
            from vcp.fetchers.asian_kline_fetcher import (
                sync_asian_kline_cache,
            )
            success, message, _report = sync_asian_kline_cache(
                max_workers=3,
                period="1y",
                use_cf_proxy=is_cf_proxy_enabled(),
            )
            self.result_success = bool(success)
            self.result_message = str(message or "")
        except (AttributeError, ImportError, OSError, RuntimeError, TypeError, ValueError) as exc:
            self.result_success = False
            self.result_message = f"盘后拉取异常: {exc}"
