# -*- coding: utf-8 -*-
"""Worker threads and shared cache state for Asian market tab."""

from __future__ import annotations

import concurrent.futures
import datetime
import json
import os
import re
import threading
import time

import yfinance as yf
from PyQt6.QtCore import QThread, pyqtSignal

from app.services import CACHE_DIR, build_yf_session, sync_asian_kline_cache
from core.logger import get_logger
from domains.market_calendar import MarketCalendar

log = get_logger(__name__)

JSON_CACHE = os.path.join(CACHE_DIR, "asian_klines_latest.json")
RT_JSON_CACHE = os.path.join(CACHE_DIR, "asian_rt_latest.json")
GLOBAL_ASIAN_RT_CACHE: dict[str, dict] = {}
_ASIAN_MARKET_CODES = ("TW", "HK", "T", "KS")
_PE_REFRESH_INTERVAL_SEC = 12 * 60 * 60

_USE_CF_PROXY = True
_EMPTY_NUMERIC_MARKERS = {"", "-", "--", "---", "—", "－", "None", "null"}
_NUMERIC_TOKEN_RE = re.compile(r"-?\d+(?:\.\d+)?")
_DEFAULT_HTTP_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/135.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
}


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


def _to_float(value) -> float | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        try:
            parsed = float(value)
        except (TypeError, ValueError):
            return None
        return parsed if parsed == parsed else None

    text = str(value).strip()
    if text in _EMPTY_NUMERIC_MARKERS:
        return None
    compact = (
        text.replace(",", "")
        .replace("¥", "")
        .replace("￥", "")
        .replace("원", "")
        .replace("%", "")
    )
    match = _NUMERIC_TOKEN_RE.search(compact)
    if not match:
        return None
    try:
        return float(match.group(0))
    except (TypeError, ValueError):
        return None


def _first_book_price(raw_value) -> float | None:
    for chunk in str(raw_value or "").split("_"):
        price = _to_float(chunk)
        if price is not None and price > 0:
            return price
    return None


def _normalize_trade_date(raw_value) -> str | None:
    text = str(raw_value or "").strip()
    if not text:
        return None
    for fmt in (
        "%Y-%m-%d",
        "%Y/%m/%d",
        "%Y%m%d",
        "%Y-%m-%dT%H:%M:%S%z",
        "%Y-%m-%dT%H:%M:%S.%f%z",
        "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%dT%H:%M:%S.%f",
    ):
        try:
            return datetime.datetime.strptime(text, fmt).date().isoformat()
        except ValueError:
            continue
    return None


def _pick_twse_price(info: dict, prev_close: float | None) -> tuple[float | None, str]:
    for key, quality in (("z", "last"), ("pz", "match")):
        price = _to_float(info.get(key))
        if price is not None and price > 0:
            return price, quality

    bid_price = _first_book_price(info.get("b"))
    ask_price = _first_book_price(info.get("a"))
    if bid_price is not None and ask_price is not None:
        return round((bid_price + ask_price) / 2.0, 4), "indicative_mid"
    if bid_price is not None:
        return bid_price, "bid_only"
    if ask_price is not None:
        return ask_price, "ask_only"

    open_price = _to_float(info.get("o"))
    if open_price is not None and open_price > 0:
        return open_price, "open_fallback"
    if prev_close is not None and prev_close > 0:
        return prev_close, "prev_close_fallback"
    return None, "missing"


def _fetch_tw_realtime_quote(code: str, http_session) -> dict | None:
    suffix = str(code or "").split(".")[-1].upper()
    base_code = str(code or "").split(".")[0].strip()
    if not base_code:
        return None

    market_prefix = "otc" if suffix == "TWO" else "tse"
    url = (
        "https://mis.twse.com.tw/stock/api/getStockInfo.jsp"
        f"?ex_ch={market_prefix}_{base_code}.tw&json=1&delay=0"
    )
    response = http_session.get(
        url,
        headers={**_DEFAULT_HTTP_HEADERS, "Referer": "https://mis.twse.com.tw/"},
        timeout=15,
    )
    data = response.json()
    info = (data.get("msgArray") or [{}])[0]
    if not info:
        return None

    prev_close = _to_float(info.get("y"))
    close_price, quote_quality = _pick_twse_price(info, prev_close)
    if close_price is None or close_price <= 0:
        return None

    open_price = _to_float(info.get("o")) or prev_close or close_price
    high_price = _to_float(info.get("h")) or max(open_price or 0.0, close_price)
    low_price = _to_float(info.get("l")) or min(open_price or close_price, close_price)
    quote_date = _normalize_trade_date(info.get("d") or info.get("^"))
    volume = _to_float(info.get("v")) or 0.0
    return {
        "date": quote_date,
        "close": close_price,
        "open": open_price,
        "high": high_price,
        "low": low_price,
        "volume": volume,
        "previous_close": prev_close,
        "currency": "TWD",
        "source": "twse_mis",
        "quote_quality": quote_quality,
    }


def _fetch_kr_realtime_quote(code: str, http_session) -> dict | None:
    base_code = str(code or "").split(".")[0].strip()
    if not base_code:
        return None

    url = f"https://polling.finance.naver.com/api/realtime/domestic/stock/{base_code}"
    response = http_session.get(
        url,
        headers={**_DEFAULT_HTTP_HEADERS, "Referer": "https://finance.naver.com/"},
        timeout=15,
    )
    data = response.json()
    info = (data.get("datas") or [{}])[0]
    if not info:
        return None

    close_price = _to_float(info.get("closePriceRaw") or info.get("closePrice"))
    if close_price is None or close_price <= 0:
        return None

    ratio = _to_float(info.get("fluctuationsRatioRaw") or info.get("fluctuationsRatio"))
    diff_value = _to_float(
        info.get("compareToPreviousClosePriceRaw") or info.get("compareToPreviousClosePrice")
    )
    direction_code = str((info.get("compareToPreviousPrice") or {}).get("code") or "").strip()
    prev_close = None
    if diff_value is not None:
        sign = -1.0 if direction_code in {"4", "5"} or (ratio is not None and ratio < 0) else 1.0
        prev_close = close_price - sign * diff_value
    if prev_close is None and ratio is not None and abs(1.0 + ratio / 100.0) > 1e-9:
        prev_close = close_price / (1.0 + ratio / 100.0)

    return {
        "date": _normalize_trade_date(info.get("localTradedAt")),
        "close": close_price,
        "open": _to_float(info.get("openPriceRaw") or info.get("openPrice")) or close_price,
        "high": _to_float(info.get("highPriceRaw") or info.get("highPrice")) or close_price,
        "low": _to_float(info.get("lowPriceRaw") or info.get("lowPrice")) or close_price,
        "volume": _to_float(
            info.get("accumulatedTradingVolumeRaw") or info.get("accumulatedTradingVolume")
        ) or 0.0,
        "previous_close": prev_close,
        "currency": ((info.get("currencyType") or {}).get("code") or "KRW"),
        "source": "naver_realtime",
        "quote_quality": "last",
    }


def _fetch_jp_realtime_quote(code: str, http_session) -> dict | None:
    base_code = str(code or "").split(".")[0].strip()
    if not base_code:
        return None

    url = f"https://finance.yahoo.co.jp/quote/{base_code}.T"
    response = http_session.get(url, headers=_DEFAULT_HTTP_HEADERS, timeout=15)
    html = response.text
    prefix = "__PRELOADED_STATE__ = "
    start = html.find(prefix)
    if start < 0:
        return None
    end = html.find("</script>", start)
    if end < 0:
        return None

    payload = json.loads(html[start + len(prefix):end].strip())
    board = (payload.get("mainStocksPriceBoard") or {}).get("priceBoard") or {}
    detail = (payload.get("mainStocksDetail") or {}).get("detail") or {}
    page_info = payload.get("pageInfo") or {}

    close_price = _to_float(board.get("price"))
    if close_price is None or close_price <= 0:
        return None

    quote_date = None
    current_ms = _to_float(page_info.get("currentDateTime"))
    if current_ms is not None and current_ms > 0:
        quote_date = (
            datetime.datetime.fromtimestamp(
                current_ms / 1000.0,
                tz=datetime.timezone.utc,
            )
            .astimezone(datetime.timezone(datetime.timedelta(hours=9)))
            .date()
            .isoformat()
        )

    low_price = _to_float(detail.get("lowPrice"))
    high_price = _to_float(detail.get("highPrice"))

    return {
        "date": quote_date or MarketCalendar.now("T").date().isoformat(),
        "close": close_price,
        "open": _to_float(detail.get("openPrice")) or close_price,
        "high": high_price or close_price,
        "low": low_price or close_price,
        "volume": _to_float(detail.get("volume")) or 0.0,
        "previous_close": _to_float(detail.get("previousPrice")),
        "currency": "JPY",
        "source": "yj_finance_page",
        "quote_quality": "free_delayed",
    }


def _fetch_yfinance_realtime_quote(code: str, yf_session) -> dict | None:
    ticker = yf.Ticker(code, session=yf_session)
    fast_info = ticker.fast_info
    df = ticker.history(period="5d", interval="1d", timeout=15)
    if df.empty:
        return None

    close_price = _to_float(fast_info.get("lastPrice")) or float(df.iloc[-1]["Close"])
    if close_price is None or close_price <= 0:
        return None

    prev_close = _to_float(fast_info.get("previousClose"))
    if prev_close is None or prev_close <= 0:
        prev_close = float(df.iloc[-2]["Close"]) if len(df) >= 2 else float(df.iloc[-1]["Close"])

    quote_date = None
    try:
        last_idx = df.index[-1]
        if getattr(last_idx, "tzinfo", None) is not None:
            last_idx = last_idx.tz_localize(None)
        quote_date = str(last_idx)[:10]
    except (AttributeError, IndexError, TypeError, ValueError):
        quote_date = None

    return {
        "date": quote_date,
        "close": close_price,
        "open": _to_float(fast_info.get("open")) or float(df.iloc[-1]["Open"]),
        "high": _to_float(fast_info.get("dayHigh")) or float(df.iloc[-1]["High"]),
        "low": _to_float(fast_info.get("dayLow")) or float(df.iloc[-1]["Low"]),
        "volume": _to_float(fast_info.get("lastVolume")) or float(df.iloc[-1].get("Volume", 0) or 0),
        "previous_close": prev_close,
        "currency": fast_info.get("currency", "USD"),
        "source": "yfinance",
        "quote_quality": "last",
        "df_today": df,
    }


def fetch_asian_realtime_quote(
    code: str,
    *,
    use_cf_proxy: bool | None = None,
    yf_session=None,
):
    normalized_code = str(code or "").strip().upper()
    if not normalized_code or "." not in normalized_code:
        return None

    session = yf_session or build_yf_session(is_cf_proxy_enabled() if use_cf_proxy is None else use_cf_proxy)
    suffix = normalized_code.split(".")[-1]

    try:
        if suffix in {"TW", "TWO"}:
            quote = _fetch_tw_realtime_quote(normalized_code, session)
        elif suffix == "KS":
            quote = _fetch_kr_realtime_quote(normalized_code, session)
        elif suffix == "T":
            quote = _fetch_jp_realtime_quote(normalized_code, session)
        else:
            quote = None
    except (
        AttributeError,
        KeyError,
        OSError,
        RuntimeError,
        TypeError,
        ValueError,
        json.JSONDecodeError,
    ) as exc:
        log.debug(f"[AsianTab] 替代实时源拉取失败 {normalized_code}: {exc}")
        quote = None

    if quote:
        return quote

    try:
        return _fetch_yfinance_realtime_quote(normalized_code, session)
    except (
        AttributeError,
        KeyError,
        OSError,
        RuntimeError,
        TypeError,
        ValueError,
    ) as exc:
        log.debug(f"[AsianTab] yfinance 实时回退失败 {normalized_code}: {exc}")
        return None


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

    def _fetch_single_code(self, code: str, yf_session, info_session):
        ticker = yf.Ticker(code, session=yf_session)
        realtime_quote = fetch_asian_realtime_quote(code, yf_session=yf_session)
        fast_info = ticker.fast_info
        df = ticker.history(period="2mo", interval="1d", timeout=15)
        if df.empty:
            return code, None

        close_price = _to_float((realtime_quote or {}).get("close")) or _to_float(fast_info.get("lastPrice"))
        if close_price is None or close_price <= 0:
            close_price = float(df.iloc[-1]["Close"])

        day_open = _to_float((realtime_quote or {}).get("open")) or _to_float(fast_info.get("open"))
        if day_open is None or day_open <= 0:
            day_open = float(df.iloc[-1]["Open"])

        day_high = _to_float((realtime_quote or {}).get("high")) or _to_float(fast_info.get("dayHigh"))
        if day_high is None or day_high <= 0:
            day_high = float(df.iloc[-1]["High"])

        day_low = _to_float((realtime_quote or {}).get("low")) or _to_float(fast_info.get("dayLow"))
        if day_low is None or day_low <= 0:
            day_low = float(df.iloc[-1]["Low"])

        prev_close = _to_float((realtime_quote or {}).get("previous_close")) or _to_float(
            fast_info.get("previousClose")
        )
        if prev_close is None or prev_close <= 0:
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

        quote_date = (realtime_quote or {}).get("date")
        if not quote_date:
            try:
                last_idx = df.index[-1]
                if getattr(last_idx, "tzinfo", None) is not None:
                    last_idx = last_idx.tz_localize(None)
                quote_date = str(last_idx)[:10]
            except (AttributeError, IndexError, TypeError, ValueError):
                quote_date = None

        cached_payload = GLOBAL_ASIAN_RT_CACHE.get(code, {}) or {}
        pe_value = cached_payload.get("pe")
        pe_source = cached_payload.get("pe_source", "")
        pe_updated_at = float(cached_payload.get("pe_updated_at", 0.0) or 0.0)
        if (time.time() - pe_updated_at) >= _PE_REFRESH_INTERVAL_SEC:
            try:
                info_ticker = ticker if info_session is yf_session else yf.Ticker(code, session=info_session)
                info = info_ticker.info
                trailing_pe = self._normalize_pe(info.get("trailingPE"))
                forward_pe = self._normalize_pe(info.get("forwardPE"))
                if trailing_pe is not None:
                    pe_value = trailing_pe
                    pe_source = "trailing"
                elif forward_pe is not None:
                    pe_value = forward_pe
                    pe_source = "forward"
                else:
                    pe_value = None
                    pe_source = ""
                pe_updated_at = time.time()
            except (
                AttributeError,
                KeyError,
                OSError,
                RuntimeError,
                TypeError,
                ValueError,
            ) as exc:
                log.debug(f"[AsianTab] PE 拉取失败 {code}: {exc}")

        payload = {
            "date": quote_date,
            "close": close_price,
            "open": day_open,
            "high": day_high,
            "low": day_low,
            "volume": _to_float((realtime_quote or {}).get("volume")) or 0.0,
            "previous_close": prev_close,
            "pct": pct,
            "pct_5": _past_pct(5),
            "pct_10": _past_pct(10),
            "pct_20": _past_pct(20),
            "currency": (realtime_quote or {}).get("currency") or fast_info.get("currency", "USD"),
            "pe": pe_value,
            "pe_source": pe_source,
            "pe_updated_at": pe_updated_at,
            "source": (realtime_quote or {}).get("source", "yfinance"),
            "quote_quality": (realtime_quote or {}).get("quote_quality", ""),
            "df_today": df,
        }
        GLOBAL_ASIAN_RT_CACHE[code] = payload
        return code, payload

    @staticmethod
    def _normalize_pe(value):
        try:
            pe_value = float(value)
        except (TypeError, ValueError):
            return None
        return pe_value if pe_value > 0 else None

    def _fetch_updates(self) -> dict:
        updates = {}
        yf_session = build_yf_session(is_cf_proxy_enabled())
        info_session = build_yf_session(False) if is_cf_proxy_enabled() else yf_session
        codes = [str(code).strip() for code in self.codes if str(code).strip()]
        if not codes:
            return updates

        executor = concurrent.futures.ThreadPoolExecutor(max_workers=4)
        futures = {
            executor.submit(self._fetch_single_code, code, yf_session, info_session): code
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
