# -*- coding: utf-8 -*-
"""Worker threads and shared cache state for Asian market tab."""

from __future__ import annotations

import concurrent.futures
import datetime
import json
import os
import time

import yfinance as yf
from PyQt6.QtCore import QThread, pyqtSignal

from core.logger import get_logger
from vcp.constants import CACHE_DIR
from vcp.fetchers.yf_session import build_yf_session

log = get_logger(__name__)

JSON_CACHE = os.path.join(CACHE_DIR, "asian_klines_latest.json")
RT_JSON_CACHE = os.path.join(CACHE_DIR, "asian_rt_latest.json")
GLOBAL_ASIAN_RT_CACHE: dict[str, dict] = {}

_USE_CF_PROXY = True


def is_cf_proxy_enabled() -> bool:
    return _USE_CF_PROXY


def set_cf_proxy_enabled(enabled: bool) -> None:
    global _USE_CF_PROXY
    _USE_CF_PROXY = bool(enabled)


class AsianMarketWorker(QThread):
    progress = pyqtSignal(str)
    result_ready = pyqtSignal(dict)

    def __init__(self, codes):
        super().__init__()
        self.codes = codes
        self._is_running = True

    def stop(self):
        self._is_running = False

    def trigger_refresh(self):
        self._force_refresh = True

    def run(self):
        while self._is_running:
            now = datetime.datetime.now()
            time_num = now.hour * 100 + now.minute
            is_trading_hours = (now.weekday() < 5) and (800 <= time_num <= 1635)
            is_manual_refresh = getattr(self, '_force_refresh', False)

            if not is_trading_hours and not is_manual_refresh:
                self.progress.emit("🌙 休市休眠中 (按刷新键可强拉)...")
                time.sleep(1)
                continue
            try:
                self.progress.emit(f"[{now.strftime('%H:%M:%S')}] 拉取最新报价中...")
                updates = {}
                yf_session = build_yf_session(is_cf_proxy_enabled())

                def _fetch(code):
                    ticker = yf.Ticker(code, session=yf_session)
                    fast_info = ticker.fast_info
                    df = ticker.history(period="2mo", interval="1d", timeout=20)
                    if not df.empty:
                        close_price = float(fast_info.get("lastPrice") or df.iloc[-1]['Close'])
                        day_open = float(fast_info.get("open") or df.iloc[-1]['Open'])
                        day_high = float(fast_info.get("dayHigh") or df.iloc[-1]['High'])
                        day_low = float(fast_info.get("dayLow") or df.iloc[-1]['Low'])
                        prev_close = float(fast_info.get("previousClose", 0))
                        if prev_close <= 0:
                            prev_close = float(df.iloc[-2]['Close']) if len(df) >= 2 else float(df.iloc[-1]['Close'])

                        pct = 0.0
                        if prev_close > 0:
                            pct = ((close_price / prev_close) - 1.0) * 100.0

                        def get_past_pct(days_ago):
                            if len(df) > days_ago:
                                past_close = float(df.iloc[-(days_ago + 1)]['Close'])
                                if past_close > 0:
                                    return ((close_price / past_close) - 1.0) * 100.0
                            return 0.0

                        pct_5 = get_past_pct(5)
                        pct_10 = get_past_pct(10)
                        pct_20 = get_past_pct(20)
                        quote_date = None
                        try:
                            last_idx = df.index[-1]
                            if getattr(last_idx, "tzinfo", None) is not None:
                                last_idx = last_idx.tz_localize(None)
                            quote_date = str(last_idx)[:10]
                        except Exception:
                            quote_date = None

                        GLOBAL_ASIAN_RT_CACHE[code] = {
                            "date": quote_date,
                            "close": close_price,
                            "open": day_open,
                            "high": day_high,
                            "low": day_low,
                            "pct": pct,
                            "pct_5": pct_5,
                            "pct_10": pct_10,
                            "pct_20": pct_20,
                            "currency": fast_info.get('currency', 'USD'),
                            "df_today": df,
                        }
                        return code, GLOBAL_ASIAN_RT_CACHE[code]
                    return code, None

                executor = concurrent.futures.ThreadPoolExecutor(max_workers=6)
                futures = {
                    executor.submit(_fetch, code): code
                    for code in self.codes
                }
                try:
                    for future in concurrent.futures.as_completed(futures, timeout=60):
                        if not self._is_running:
                            executor.shutdown(wait=False, cancel_futures=True)
                            break
                        try:
                            code, payload = future.result(timeout=30)
                            if payload:
                                updates[code] = payload
                        except (concurrent.futures.TimeoutError, Exception) as exc:
                            log.debug(f"[AsianTab] 单票拉取超时/异常: {futures.get(future, '?')}: {exc}")
                except concurrent.futures.TimeoutError:
                    log.warning("[AsianTab] 本轮拉取整体超时(60s)，跳过剩余标的")
                finally:
                    executor.shutdown(wait=False, cancel_futures=True)

                if self._is_running and updates:
                    self.result_ready.emit(updates)
                    ok_msg = f"✅ [{datetime.datetime.now().strftime('%H:%M:%S')}] 亚洲市场 YF 外网接口直连成功 (获取 {len(updates)} 支最新报价)"
                    self.progress.emit(ok_msg)
                    log.info(f"[AsianTab] {ok_msg}")

            except Exception as exc:
                err_str = str(exc)
                if "Too Many Requests" in err_str or "Rate limited" in err_str or "429" in err_str:
                    err_hint = "Yahoo接口被限流(429)！如果开启了VPN请尝试切换节点；如果没开请开启VPN。"
                elif "Timeout" in err_str or "Max retries" in err_str or "unreachable" in err_str.lower() or "Connection" in err_str:
                    err_hint = "连接YF失败！外网接口严重依赖梯子，请检查 VPN 是否已开启（建议开启全局模式）。"
                elif "NoneType" in err_str and "subscriptable" in err_str:
                    err_hint = "请求代理/CF隧道遇到墙阻断或空响应，未能获取合法数据，请尝试开启VPN全局代理并关闭CF节点。"
                else:
                    err_hint = f"YF拉取遭遇异常: {err_str}"
                msg = f"❌ 外网断开: {err_hint}"
                self.progress.emit(msg)
                log.error(f"[AsianTab] {msg} | Native Error: {exc}")

            for _ in range(120 * 10):
                if not self._is_running:
                    return
                if getattr(self, '_force_refresh', False):
                    self._force_refresh = False
                    break
                time.sleep(0.1)


class AsianCacheFetcherThread(QThread):
    finished_sig = pyqtSignal(bool, str)

    def run(self):
        try:
            from vcp.fetchers.asian_kline_fetcher import (
                fetch_all_asian_klines,
                fetch_single_kline,
                filter_asian_tickers,
                save_kline_data,
            )

            target_map = filter_asian_tickers()
            target_tickers = set(target_map.values())
            ticker_to_name = {ticker: name for name, ticker in target_map.items()}

            def _to_map(rows):
                out = {}
                for row in rows or []:
                    ticker = str((row or {}).get("ticker", "")).strip()
                    if ticker and ticker not in out:
                        out[ticker] = row
                return out

            data = fetch_all_asian_klines(max_workers=4, use_cf_proxy=is_cf_proxy_enabled())
            if not data:
                self.finished_sig.emit(False, "❌ 盘后缓存全量拉取失败")
                return

            row_map = _to_map(data)
            missing = sorted(target_tickers - set(row_map.keys()))

            if missing:
                log.warning(f"[AsianTab] 盘后全量抓取缺失 {len(missing)} 支，启动单票补抓: {missing}")
                for ticker in list(missing):
                    name = ticker_to_name.get(ticker, ticker)
                    try:
                        one = fetch_single_kline(name, ticker, period="1y", use_cf_proxy=is_cf_proxy_enabled())
                        if one:
                            row_map[ticker] = one
                    except Exception as exc:
                        log.warning(f"[AsianTab] 单票补抓失败 {ticker}: {exc}")
                missing = sorted(target_tickers - set(row_map.keys()))

            reused = []
            if missing and os.path.exists(JSON_CACHE):
                try:
                    with open(JSON_CACHE, 'r', encoding='utf-8') as f:
                        old_raw = json.load(f)
                    old_map = _to_map(old_raw.get("stocks", []))
                    for ticker in list(missing):
                        if ticker in old_map:
                            row_map[ticker] = old_map[ticker]
                            reused.append(ticker)
                    missing = sorted(target_tickers - set(row_map.keys()))
                    if reused:
                        log.warning(f"[AsianTab] 已从旧缓存回填 {len(reused)} 支缺失标的: {sorted(reused)}")
                except Exception as exc:
                    log.warning(f"[AsianTab] 旧缓存回填失败: {exc}")

            if missing:
                msg = f"⚠ 盘后同步部分失败：仍缺失 {len(missing)} 只({', '.join(missing)})，已保留旧缓存"
                log.warning(f"[AsianTab] {msg}")
                self.finished_sig.emit(False, msg)
                return

            final_data = list(row_map.values())
            final_data.sort(key=lambda item: (item.get("market", ""), item.get("name", "")))
            save_kline_data(final_data)
            if reused:
                self.finished_sig.emit(True, f"✅ 16:30 盘后自动同步完成（含旧缓存回填 {len(reused)} 只）")
            else:
                self.finished_sig.emit(True, "✅ 16:30 盘后自动同步完成！")
        except Exception as exc:
            self.finished_sig.emit(False, f"❌ 盘后拉取异常: {exc}")
