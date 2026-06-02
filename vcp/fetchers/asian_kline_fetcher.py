# -*- coding: utf-8 -*-
"""亚洲寡头 250 日 K 线数据拉取器。

从 industry_dict.py 自动读取 VANGUARD_TICKERS，
筛选出亚洲市场标的（.TW / .TWO / .KS / .T / .HK），
按市场免费源拉取约 250 个交易日的 OHLCV 日线数据，
输出 JSON 文件供前端看板渲染 K 线图。

用法：
    python asian_kline_fetcher.py              # 拉取全部亚洲标的
    python asian_kline_fetcher.py --market JP   # 只拉日本
    python asian_kline_fetcher.py --ticker 8035.T  # 只拉单只
"""

import argparse
import importlib.util
import json
import logging
import math
import os
import re
import sys
import time
from datetime import date, datetime, timedelta

from infra.http_safety import requests_get_https
from vcp.fetchers.asian_kline_cache import (
    _latest_cache_path as _latest_cache_path,
)
from vcp.fetchers.asian_kline_cache import (
    _load_cached_row_map,
    _resolve_cache_output_dir,
    _rows_to_map,
    save_kline_data,
)
from vcp.fetchers.yf_session import (
    build_yf_session,
    get_yf_rate_limit_status,
    is_yf_rate_limit_error,
    mark_yf_rate_limited,
)

# Why: 行业字典暂未收入本项目工程，通过项目根目录向上推导兄弟目录，避免硬编码特定机器的绝对路径
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_PIPELINE_DIR = os.path.join(os.path.dirname(_PROJECT_ROOT), "每日战报", "每日战报")
_PIPELINE_INDUSTRY_DICT = os.path.join(_PIPELINE_DIR, "industry_dict.py")


def _load_industry_module():
    module = sys.modules.get("industry_dict")
    if module is not None:
        return module
    if not os.path.isfile(_PIPELINE_INDUSTRY_DICT):
        return None
    try:
        spec = importlib.util.spec_from_file_location("_zijin_external_industry_dict", _PIPELINE_INDUSTRY_DICT)
        if spec is None or spec.loader is None:
            return None
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    except (AttributeError, ImportError, OSError, RuntimeError, TypeError, ValueError):
        return None


_industry_module = _load_industry_module()
OLIGARCH_DICT = getattr(_industry_module, "OLIGARCH_DICT", {}) if _industry_module is not None else {}
VANGUARD_TICKERS = getattr(_industry_module, "VANGUARD_TICKERS", {}) if _industry_module is not None else {}

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)


def _is_tls_verification_error(exc: BaseException) -> bool:
    error_text = str(exc or "")
    return (
        exc.__class__.__name__ == "SSLError"
        or "SSLCertVerificationError" in error_text
        or "CERTIFICATE_VERIFY_FAILED" in error_text
    )


# ===================================================================
# 亚洲市场后缀映射（与 p6_mapper.py 保持一致）
# ===================================================================
ASIAN_MARKET_MAP = {
    ".TW": "台湾",
    ".TWO": "台湾上柜",
    ".KS": "韩国",
    ".T": "日本",
    ".HK": "香港",
}

# Why: 命令行参数用的简写 → 后缀映射
MARKET_SHORTCUT = {
    "TW": (".TW", ".TWO"),
    "KR": (".KS",),
    "JP": (".T",),
    "HK": (".HK",),
}

# Why: 亚洲页需要优先跟踪本地挂牌代码，避免被 ADR/海外替代代码稀释。
# 这里仅覆盖“亚洲看板”的取数池，不影响每日战报主工程里的全局 ticker 定义。
ASIAN_LOCAL_TICKER_OVERRIDES = {
    "TSMC": "2330.TW",
    "TUC": "6274.TWO",
    "Shin-Etsu": "4063.T",
    "SUMCO": "3436.T",
    "SCREEN Holdings": "7735.T",
    "Nidec": "6594.T",
    "AMADA": "6113.T",
    "Union Tool": "6278.T",
    "Ushio": "6925.T",
    "Accretech": "7729.T",
    "MJC": "6871.T",
    "Fujikura": "5803.T",
    "SKC": "011790.KS",
    "Murata": "6981.T",
}

# Why: 亚洲页允许补充少量本地维护的赛道归属，避免上游产业字典尚未同步时出现“未知赛道”。
ASIAN_LOCAL_TRACK_OVERRIDES = {
    "6274.TWO": "高频PCB与覆铜板材料",
    "8035.T": "前道晶圆设备与量测",
    "4063.T": "关键晶圆材料与特种工艺",
    "3436.T": "关键晶圆材料与特种工艺",
    "7735.T": "AI PCB设备与关键耗材",
    "6594.T": "AI PCB设备与关键耗材",
    "6113.T": "AI PCB设备与关键耗材",
    "6278.T": "AI PCB设备与关键耗材",
    "6925.T": "AI PCB设备与关键耗材",
    "7729.T": "半导体测试设备与探针卡",
    "6871.T": "半导体测试设备与探针卡",
    "5802.T": "光芯片与硅光",
    "5803.T": "光通信无源器件与精密零部件",
    "011790.KS": "IC载板与封装材料",
    "6981.T": "数据中心电力与配电",
}

_ALNUM_RE = re.compile(r"[a-z0-9]+")
_NUMERIC_TOKEN_RE = re.compile(r"[-+]?\d+(?:,\d{3})*(?:\.\d+)?")
_YJ_JWT_TOKEN_RE = re.compile(r'jwtToken\\":\\"([^\\]+)\\"')
_MARKET_CURRENCY_MAP = {
    ".TW": "TWD",
    ".TWO": "TWD",
    ".KS": "KRW",
    ".T": "JPY",
    ".HK": "HKD",
}
_EMPTY_NUMERIC_MARKERS = {"", "-", "--", "---", "N/A", "n/a", "null", "None"}
_JP_HISTORY_PAGE_SIZE = 20
_KR_HISTORY_PAGE_SIZE = 20


def _ensure_industry_mappings_loaded() -> None:
    global OLIGARCH_DICT, VANGUARD_TICKERS

    if OLIGARCH_DICT and VANGUARD_TICKERS:
        return

    industry_module = _load_industry_module()
    if industry_module is None:
        return

    real_oligarch = getattr(industry_module, "OLIGARCH_DICT", None)
    real_tickers = getattr(industry_module, "VANGUARD_TICKERS", None)
    if real_oligarch:
        OLIGARCH_DICT = real_oligarch
    if real_tickers:
        VANGUARD_TICKERS = real_tickers


def _get_asian_source_tickers() -> dict[str, str]:
    _ensure_industry_mappings_loaded()
    tickers = dict(VANGUARD_TICKERS)
    tickers.update(ASIAN_LOCAL_TICKER_OVERRIDES)
    return tickers


def _get_market_suffix(ticker: str) -> str | None:
    """提取 ticker 的市场后缀，无后缀返回 None。"""
    for suffix in ASIAN_MARKET_MAP:
        if ticker.endswith(suffix):
            return suffix
    return None


def _get_market_name(ticker: str) -> str:
    """获取 ticker 对应的市场中文名。"""
    suffix = _get_market_suffix(ticker)
    return ASIAN_MARKET_MAP.get(suffix, "未知")


def filter_asian_tickers(market_filter: str | None = None) -> dict[str, str]:
    """从 VANGUARD_TICKERS 中筛选亚洲市场标的。

    Args:
        market_filter: 可选市场简写（TW/KR/JP/HK），None 表示全部亚洲
    Returns:
        {公司名: ticker} 字典
    """
    result = {}

    # Why: 确定要筛选的后缀范围
    if market_filter:
        target_suffixes = MARKET_SHORTCUT.get(market_filter.upper())
        if not target_suffixes:
            logging.error(f"未知市场简写: {market_filter}，支持: TW/KR/JP/HK")
            return {}
    else:
        target_suffixes = tuple(ASIAN_MARKET_MAP.keys())

    for name, ticker in _get_asian_source_tickers().items():
        if any(ticker.endswith(s) for s in target_suffixes):
            result[name] = ticker

    return result


def _find_track(ticker: str) -> str:
    """反查 ticker 所属的赛道名称。"""
    _ensure_industry_mappings_loaded()
    if ticker in ASIAN_LOCAL_TRACK_OVERRIDES:
        return ASIAN_LOCAL_TRACK_OVERRIDES[ticker]

    # Why: 从 VANGUARD_TICKERS 找到公司名，再从 OLIGARCH_DICT 反查赛道
    company_name = None
    for name, tk in _get_asian_source_tickers().items():
        if tk == ticker:
            company_name = name
            break

    if not company_name:
        return "未知赛道"

    def _normalize_text(value: str) -> str:
        return "".join(_ALNUM_RE.findall(str(value or "").lower()))

    def _build_acronym(value: str) -> str:
        tokens = _ALNUM_RE.findall(str(value or ""))
        if len(tokens) <= 1:
            return ""
        return "".join(token[0] for token in tokens).lower()

    name_lower = company_name.lower()
    name_normalized = _normalize_text(company_name)
    ticker_prefix = str(ticker or "").split(".")[0].lower()
    for track, companies in OLIGARCH_DICT.items():
        for comp in companies:
            comp_lower = comp.lower()
            # Why: 三层匹配——完整包含 > 英文前缀 > 公司名是赛道成员子串
            if name_lower in comp_lower or comp_lower.startswith(name_lower):
                return track
            eng_prefix = comp.split("(")[0].strip()
            eng_prefix_lower = eng_prefix.lower()
            if eng_prefix_lower == name_lower:
                return track
            if name_normalized and _normalize_text(comp) == name_normalized:
                return track
            if name_normalized and _normalize_text(eng_prefix) == name_normalized:
                return track
            if name_lower and _build_acronym(comp) == name_lower:
                return track
            if name_lower and _build_acronym(eng_prefix) == name_lower:
                return track
            if ticker_prefix and _build_acronym(comp) == ticker_prefix:
                return track
            if ticker_prefix and _build_acronym(eng_prefix) == ticker_prefix:
                return track
    return "未知赛道"


def _build_sync_target_map(
    market_filter: str | None = None,
    single_ticker: str | None = None,
) -> dict[str, str]:
    """构建严格同步时的目标股票池。"""
    if single_ticker:
        single_ticker = str(single_ticker).strip()
        if not single_ticker:
            return {}

        for name, ticker in _get_asian_source_tickers().items():
            if ticker == single_ticker:
                return {name: single_ticker}
        return {single_ticker: single_ticker}

    return filter_asian_tickers(market_filter)


def _to_float(value) -> float | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        number = float(value)
        return number if math.isfinite(number) else None

    text = str(value).strip()
    if text in _EMPTY_NUMERIC_MARKERS:
        return None

    match = _NUMERIC_TOKEN_RE.search(text.replace("%", ""))
    if not match:
        return None

    try:
        number = float(match.group(0).replace(",", ""))
        return number if math.isfinite(number) else None
    except (TypeError, ValueError):
        return None


def _normalize_iso_date(raw_value) -> str | None:
    text = str(raw_value or "").strip()
    if not text:
        return None

    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%Y/%m/%d %H:%M:%S", "%Y%m%d"):
        try:
            return datetime.strptime(text, fmt).date().isoformat()
        except ValueError:
            continue
    return None


def _normalize_roc_date(raw_value) -> str | None:
    text = str(raw_value or "").strip()
    if not text:
        return None

    parts = text.split("/")
    if len(parts) != 3:
        return _normalize_iso_date(text)

    try:
        year = int(parts[0]) + 1911
        month = int(parts[1])
        day = int(parts[2])
        return date(year, month, day).isoformat()
    except ValueError:
        return None


def _date_from_iso(raw_value: str | None) -> date | None:
    if not raw_value:
        return None
    try:
        return datetime.strptime(raw_value, "%Y-%m-%d").date()
    except ValueError:
        return None


def _last_kline_date(row: dict | None) -> date | None:
    klines = (row or {}).get("klines") or []
    if not klines:
        return None
    return _date_from_iso(str((klines[-1] or {}).get("date") or ""))


def _market_latest_dates(row_map: dict[str, dict]) -> dict[str, date]:
    latest_by_market: dict[str, date] = {}
    for row in row_map.values():
        market = str((row or {}).get("market") or "").strip()
        last_date = _last_kline_date(row)
        if not market or last_date is None:
            continue
        if market not in latest_by_market or last_date > latest_by_market[market]:
            latest_by_market[market] = last_date
    return latest_by_market


def _find_stale_kline_tickers(
    row_map: dict[str, dict],
    target_tickers: set[str],
) -> list[str]:
    latest_by_market = _market_latest_dates(row_map)
    stale: list[str] = []
    for ticker in sorted(target_tickers & set(row_map.keys())):
        row = row_map.get(ticker) or {}
        market = str(row.get("market") or "").strip()
        last_date = _last_kline_date(row)
        latest_date = latest_by_market.get(market)
        if last_date is None or (latest_date is not None and last_date < latest_date):
            stale.append(ticker)
    return stale


def _drop_stale_kline_rows(
    row_map: dict[str, dict],
    target_tickers: set[str],
) -> list[str]:
    stale = _find_stale_kline_tickers(row_map, target_tickers)
    for ticker in stale:
        row_map.pop(ticker, None)
    return stale


def _resolve_period_window(period: str) -> tuple[date, date, int]:
    end_date = datetime.now().date()
    text = str(period or "1y").strip().lower()
    match = re.fullmatch(r"(\d+)(d|mo|y)", text)
    if not match:
        return end_date - timedelta(days=366), end_date, 260

    count = max(int(match.group(1)), 1)
    unit = match.group(2)
    if unit == "d":
        return end_date - timedelta(days=count), end_date, max(count, 5)
    if unit == "mo":
        return end_date - timedelta(days=count * 31), end_date, count * 25
    return end_date - timedelta(days=count * 366), end_date, count * 260


def _iter_month_starts(start_date: date, end_date: date):
    cursor = date(start_date.year, start_date.month, 1)
    last_month = date(end_date.year, end_date.month, 1)
    while cursor <= last_month:
        yield cursor
        if cursor.month == 12:
            cursor = date(cursor.year + 1, 1, 1)
        else:
            cursor = date(cursor.year, cursor.month + 1, 1)


def _extract_yj_history_value(values: list, index: int) -> float | None:
    if index >= len(values):
        return None
    cell = values[index]
    if isinstance(cell, dict):
        return _to_float(cell.get("value"))
    return _to_float(cell)


def _finalize_klines(raw_rows: list[dict], *, start_date: date, end_date: date) -> list[dict]:
    deduped: dict[str, dict] = {}
    for row in raw_rows:
        iso_date = str((row or {}).get("date") or "").strip()
        row_date = _date_from_iso(iso_date)
        if row_date is None or row_date < start_date or row_date > end_date:
            continue

        close_price = _to_float(row.get("close"))
        if close_price is None:
            continue

        open_price = _to_float(row.get("open"))
        high_price = _to_float(row.get("high"))
        low_price = _to_float(row.get("low"))
        volume = int(round(_to_float(row.get("volume")) or 0.0))

        if open_price is None:
            open_price = close_price
        if high_price is None:
            high_price = max(open_price, close_price)
        if low_price is None:
            low_price = min(open_price, close_price)

        deduped[iso_date] = {
            "date": iso_date,
            "open": round(float(open_price), 2),
            "high": round(float(high_price), 2),
            "low": round(float(low_price), 2),
            "close": round(float(close_price), 2),
            "volume": volume,
        }

    return [deduped[iso_date] for iso_date in sorted(deduped)]


def _fetch_tw_history_twse(
    ticker: str,
    http_session,
    *,
    start_date: date,
    end_date: date,
) -> list[dict]:
    base_code = str(ticker or "").split(".")[0].strip()
    if not base_code:
        return []

    rows: list[dict] = []
    for month_start in _iter_month_starts(start_date, end_date):
        url = (
            "https://www.twse.com.tw/exchangeReport/STOCK_DAY"
            f"?response=json&date={month_start.strftime('%Y%m01')}&stockNo={base_code}"
        )
        try:
            response = requests_get_https(
                url,
                session=http_session,
                headers={"User-Agent": "Mozilla/5.0", "Referer": "https://www.twse.com.tw/"},
                timeout=20,
            )
            payload = response.json()
        except Exception as exc:
            if _is_tls_verification_error(exc):
                logging.warning("TWSE TLS verification failed for %s: %s", ticker, exc)
                return []
            raise
        if str(payload.get("stat") or "").upper() != "OK":
            continue

        for raw in payload.get("data") or []:
            iso_date = _normalize_roc_date(raw[0] if len(raw) > 0 else "")
            if not iso_date:
                continue
            rows.append(
                {
                    "date": iso_date,
                    "open": _to_float(raw[3] if len(raw) > 3 else None),
                    "high": _to_float(raw[4] if len(raw) > 4 else None),
                    "low": _to_float(raw[5] if len(raw) > 5 else None),
                    "close": _to_float(raw[6] if len(raw) > 6 else None),
                    "volume": _to_float(raw[1] if len(raw) > 1 else None),
                }
            )
    return rows


def _fetch_tw_history_tpex(
    ticker: str,
    http_session,
    *,
    start_date: date,
    end_date: date,
) -> list[dict]:
    base_code = str(ticker or "").split(".")[0].strip()
    if not base_code:
        return []

    rows: list[dict] = []
    for month_start in _iter_month_starts(start_date, end_date):
        response = requests_get_https(
            "https://www.tpex.org.tw/www/en-us/afterTrading/tradingStock",
            session=http_session,
            params={"code": base_code, "date": month_start.strftime("%Y/%m/01")},
            headers={
                "User-Agent": "Mozilla/5.0",
                "Referer": "https://www.tpex.org.tw/en-us/mainboard/trading/info/stock-pricing.html",
            },
            timeout=20,
        )
        payload = response.json()
        if str(payload.get("stat") or "").lower() != "ok":
            continue

        table = (payload.get("tables") or [{}])[0]
        for raw in table.get("data") or []:
            iso_date = _normalize_iso_date(raw[0] if len(raw) > 0 else "")
            if not iso_date:
                continue
            rows.append(
                {
                    "date": iso_date,
                    "open": _to_float(raw[3] if len(raw) > 3 else None),
                    "high": _to_float(raw[4] if len(raw) > 4 else None),
                    "low": _to_float(raw[5] if len(raw) > 5 else None),
                    "close": _to_float(raw[6] if len(raw) > 6 else None),
                    # TPEX historical page exposes trade units; convert back to shares.
                    "volume": (_to_float(raw[1] if len(raw) > 1 else None) or 0.0) * 1000.0,
                }
            )
    return rows


def _fetch_kr_history_naver(
    ticker: str,
    http_session,
    *,
    start_date: date,
    end_date: date,
) -> list[dict]:
    base_code = str(ticker or "").split(".")[0].strip()
    if not base_code:
        return []

    rows: list[dict] = []
    page = 1
    while True:
        response = requests_get_https(
            f"https://m.stock.naver.com/api/stock/{base_code}/price?page={page}",
            session=http_session,
            headers={
                "User-Agent": "Mozilla/5.0",
                "Referer": "https://m.stock.naver.com/",
            },
            timeout=20,
        )
        payload = response.json() or []
        if not payload:
            break

        oldest_row_date: date | None = None
        for item in payload:
            iso_date = _normalize_iso_date(item.get("localTradedAt"))
            row_date = _date_from_iso(iso_date)
            if row_date is None:
                continue
            oldest_row_date = row_date if oldest_row_date is None else min(oldest_row_date, row_date)
            rows.append(
                {
                    "date": iso_date,
                    "open": _to_float(item.get("openPrice")),
                    "high": _to_float(item.get("highPrice")),
                    "low": _to_float(item.get("lowPrice")),
                    "close": _to_float(item.get("closePrice")),
                    "volume": _to_float(item.get("accumulatedTradingVolume")),
                }
            )

        if len(payload) < _KR_HISTORY_PAGE_SIZE or (oldest_row_date and oldest_row_date < start_date):
            break
        page += 1
    return rows


def _fetch_jp_history_yahoo_japan(
    ticker: str,
    http_session,
    *,
    start_date: date,
    end_date: date,
) -> list[dict]:
    base_code = str(ticker or "").split(".")[0].strip()
    if not base_code:
        return []

    history_url = f"https://finance.yahoo.co.jp/quote/{base_code}.T/history"
    response = requests_get_https(
        history_url,
        session=http_session,
        headers={"User-Agent": "Mozilla/5.0", "Referer": history_url},
        timeout=20,
    )
    token_match = _YJ_JWT_TOKEN_RE.search(response.text)
    if not token_match:
        return []

    jwt_token = token_match.group(1)
    rows: list[dict] = []
    page = 1
    while True:
        api_response = requests_get_https(
            "https://finance.yahoo.co.jp/bff-quote-stocks/v1/ajax/history/price",
            session=http_session,
            params={
                "code": f"{base_code}.T",
                "fromDate": start_date.strftime("%Y%m%d"),
                "toDate": end_date.strftime("%Y%m%d"),
                "timeFrameId": "d",
                "page": page,
            },
            headers={
                "User-Agent": "Mozilla/5.0",
                "Referer": history_url,
                "x-jwt-token": jwt_token,
            },
            timeout=20,
        )
        payload = api_response.json()
        history = ((payload.get("response") or {}).get("history") or {}).get("histories") or []
        if not history:
            break

        oldest_row_date: date | None = None
        for item in history:
            iso_date = _normalize_iso_date(item.get("date"))
            row_date = _date_from_iso(iso_date)
            if row_date is None:
                continue
            oldest_row_date = row_date if oldest_row_date is None else min(oldest_row_date, row_date)
            values = item.get("values") or []
            rows.append(
                {
                    "date": iso_date,
                    "open": _extract_yj_history_value(values, 0),
                    "high": _extract_yj_history_value(values, 1),
                    "low": _extract_yj_history_value(values, 2),
                    "close": _extract_yj_history_value(values, 3),
                    "volume": _extract_yj_history_value(values, 4),
                }
            )

        if len(history) < _JP_HISTORY_PAGE_SIZE or (oldest_row_date and oldest_row_date < start_date):
            break
        page += 1
    return rows


def _fetch_yfinance_history_rows(
    ticker: str,
    http_session,
    *,
    start_date: date,
    end_date: date,
) -> list[dict]:
    try:
        import yfinance as yf
    except (ImportError, ModuleNotFoundError):
        return []

    end_exclusive = end_date + timedelta(days=1)
    frame = yf.Ticker(ticker, session=http_session).history(
        start=start_date.isoformat(),
        end=end_exclusive.isoformat(),
        auto_adjust=False,
    )
    if frame is None or frame.empty:
        return []

    rows: list[dict] = []
    for index, item in frame.iterrows():
        row_date = getattr(index, "date", lambda: None)()
        if row_date is None:
            row_date = _date_from_iso(str(index)[:10])
        if row_date is None:
            continue

        rows.append(
            {
                "date": row_date.isoformat(),
                "open": _to_float(item.get("Open")),
                "high": _to_float(item.get("High")),
                "low": _to_float(item.get("Low")),
                "close": _to_float(item.get("Close")),
                "volume": _to_float(item.get("Volume")),
            }
        )
    return rows


def _fetch_hk_history_tencent(
    ticker: str,
    http_session,
    *,
    start_date: date,
    end_date: date,
    target_rows: int,
) -> list[dict]:
    base_code = str(ticker or "").split(".")[0].strip().zfill(5)
    if not base_code:
        return []

    row_count = min(max(target_rows + 30, 120), 800)
    symbol = f"hk{base_code}"
    response = requests_get_https(
        f"https://web.ifzq.gtimg.cn/appstock/app/hkfqkline/get?param={symbol},day,,,{row_count},qfq",
        session=http_session,
        headers={
            "User-Agent": "Mozilla/5.0",
            "Referer": "https://stockapp.finance.qq.com/",
        },
        timeout=20,
    )
    payload = response.json()
    market_data = (payload.get("data") or {}).get(symbol) or {}
    history = market_data.get("qfqday") or market_data.get("day") or []

    rows: list[dict] = []
    for item in history:
        if len(item) < 6:
            continue
        iso_date = _normalize_iso_date(item[0])
        if not iso_date:
            continue
        rows.append(
            {
                "date": iso_date,
                "open": _to_float(item[1]),
                "close": _to_float(item[2]),
                "high": _to_float(item[3]),
                "low": _to_float(item[4]),
                "volume": _to_float(item[5]),
            }
        )
    return rows


def _fetch_market_history_rows(
    ticker: str,
    http_session,
    *,
    start_date: date,
    end_date: date,
    target_rows: int,
) -> tuple[list[dict], str]:
    suffix = _get_market_suffix(ticker)
    if suffix == ".TW":
        rows = _fetch_tw_history_twse(ticker, http_session, start_date=start_date, end_date=end_date)
        if rows:
            return rows, "twse_stock_day"
        return (
            _fetch_yfinance_history_rows(ticker, http_session, start_date=start_date, end_date=end_date),
            "yfinance_history",
        )
    if suffix == ".TWO":
        return (
            _fetch_tw_history_tpex(ticker, http_session, start_date=start_date, end_date=end_date),
            "tpex_trading_stock",
        )
    if suffix == ".KS":
        return (
            _fetch_kr_history_naver(ticker, http_session, start_date=start_date, end_date=end_date),
            "naver_history",
        )
    if suffix == ".T":
        rows = _fetch_jp_history_yahoo_japan(ticker, http_session, start_date=start_date, end_date=end_date)
        if rows:
            return rows, "yj_history"
        return (
            _fetch_yfinance_history_rows(ticker, http_session, start_date=start_date, end_date=end_date),
            "yfinance_history",
        )
    if suffix == ".HK":
        return (
            _fetch_hk_history_tencent(
                ticker,
                http_session,
                start_date=start_date,
                end_date=end_date,
                target_rows=target_rows,
            ),
            "tencent_hk_qfq",
        )
    return [], "unsupported"


def fetch_single_kline(
    name: str,
    ticker: str,
    period: str = "1y",
    use_cf_proxy: bool = False,
    session=None,
) -> dict | None:
    """拉取单只标的的 K 线数据。

    Returns:
        {
            "name": "Tokyo Electron",
            "ticker": "8035.T",
            "market": "日本",
            "track": "前道晶圆设备与量测",
            "currency": "JPY",
            "klines": [
                {"date": "2025-04-01", "open": 100, "high": 105, "low": 98, "close": 103, "volume": 12345},
                ...
            ]
        }
    """
    rate_limit_status = get_yf_rate_limit_status()
    if rate_limit_status["active"]:
        logging.warning(
            "⚠️ %s(%s): Yahoo Finance 冷却中，跳过请求 (剩余 %.0fs)",
            name,
            ticker,
            rate_limit_status["remaining_sec"],
        )
        return None

    try:
        ticker = str(ticker or "").strip().upper()
        http_session = session or build_yf_session(use_cf_proxy)
        start_date, end_date, target_rows = _resolve_period_window(period)
        raw_rows, source = _fetch_market_history_rows(
            ticker,
            http_session,
            start_date=start_date,
            end_date=end_date,
            target_rows=target_rows,
        )
        klines = _finalize_klines(raw_rows, start_date=start_date, end_date=end_date)

        if not klines:
            logging.warning(f"⚠️ {name}({ticker}): 无数据")
            return None

        suffix = _get_market_suffix(ticker)
        currency = _MARKET_CURRENCY_MAP.get(suffix, "N/A")

        return {
            "name": name,
            "ticker": ticker,
            "market": _get_market_name(ticker),
            "track": _find_track(ticker),
            "currency": currency,
            "source": source,
            "kline_count": len(klines),
            "klines": klines,
        }

    except Exception as e:
        if is_yf_rate_limit_error(e):
            remaining_sec = mark_yf_rate_limited(e)
            logging.warning(f"⚠️ {name}({ticker}): Yahoo Finance 限流，冷却 {remaining_sec:.0f}s — {e}")
            return None
        if isinstance(
            e,
            (
                AttributeError,
                KeyError,
                OSError,
                RuntimeError,
                TypeError,
                ValueError,
                json.JSONDecodeError,
            ),
        ):
            logging.error(f"❌ {name}({ticker}): 拉取失败 — {e}")
            return None
        raise


def fetch_all_asian_klines(
    market_filter: str | None = None,
    single_ticker: str | None = None,
    max_workers: int = 6,
    period: str = "1y",
    use_cf_proxy: bool = False,
) -> list[dict]:
    """并发拉取亚洲寡头 K 线数据。

    Args:
        market_filter: 市场筛选（TW/KR/JP/HK）
        single_ticker: 只拉单只
        max_workers: 并发线程数（别太高，免费上游会限速）
        period: 时间窗口参数，默认 1y（约 250 个交易日）
    """
    # Why: 支持单只调试模式
    if single_ticker:
        name = None
        for n, tk in _get_asian_source_tickers().items():
            if tk == single_ticker:
                name = n
                break
        if not name:
            logging.error(f"ticker {single_ticker} 不在 VANGUARD_TICKERS 中")
            return []
        tickers = {name: single_ticker}
    else:
        tickers = filter_asian_tickers(market_filter)

    if not tickers:
        logging.error("没有找到符合条件的亚洲标的")
        return []

    logging.info(f"📊 开始拉取 {len(tickers)} 只亚洲标的的 K 线数据 (period={period}, workers={max_workers})")

    results = []
    failed = []
    yf_session = build_yf_session(use_cf_proxy)

    for name, ticker in tickers.items():
        if get_yf_rate_limit_status()["active"]:
            logging.warning("⚠️ Yahoo Finance 已进入冷却，提前结束本轮亚洲 K 线抓取")
            break
        time.sleep(0.3)
        try:
            data = fetch_single_kline(
                name,
                ticker,
                period=period,
                use_cf_proxy=use_cf_proxy,
                session=yf_session,
            )
            if data:
                results.append(data)
                logging.info(f"  ✅ {name}({ticker}) [{data['market']}]: {data['kline_count']} 根K线")
            else:
                failed.append(f"{name}({ticker})")
        except Exception as e:
            failed.append(f"{name}({ticker})")
            if is_yf_rate_limit_error(e):
                remaining_sec = mark_yf_rate_limited(e)
                logging.warning(f"  ⚠️ {name}({ticker}): Yahoo Finance 限流，冷却 {remaining_sec:.0f}s — {e}")
                break
            if isinstance(e, (AttributeError, KeyError, OSError, RuntimeError, TypeError, ValueError)):
                logging.error(f"  ❌ {name}({ticker}): {e}")
                continue
            raise

    # Why: 按市场分组排序，便于前端渲染
    results.sort(key=lambda x: (x["market"], x["name"]))

    logging.info(
        f"\n📊 拉取完成: 成功 {len(results)}/{len(tickers)}"
        + (f"，失败 {len(failed)}: {', '.join(failed)}" if failed else "")
    )

    return results


def sync_asian_kline_cache(
    market_filter: str | None = None,
    single_ticker: str | None = None,
    max_workers: int = 6,
    period: str = "1y",
    use_cf_proxy: bool = False,
    output_dir: str | None = None,
) -> tuple[bool, str, dict]:
    """严格同步亚洲 K 线缓存。

    统一规则：
    1. 先按目标池做全量拉取；
    2. 对缺失票做单票补抓；
    3. 仍缺失时尝试从旧缓存回填；
    4. 若最终仍缺票，则拒绝覆盖 latest 缓存。
    """
    target_map = _build_sync_target_map(market_filter=market_filter, single_ticker=single_ticker)
    if not target_map:
        message = "没有找到符合条件的亚洲标的"
        return (
            False,
            message,
            {
                "target_count": 0,
                "written_count": 0,
                "single_recovered": [],
                "reused": [],
                "missing": [],
            },
        )

    ticker_to_name = {ticker: name for name, ticker in target_map.items()}
    target_tickers = set(target_map.values())
    output_dir = _resolve_cache_output_dir(output_dir)

    data = fetch_all_asian_klines(
        market_filter=market_filter,
        single_ticker=single_ticker,
        max_workers=max_workers,
        period=period,
        use_cf_proxy=use_cf_proxy,
    )
    if not data:
        try:
            old_map = _load_cached_row_map(output_dir)
        except (FileNotFoundError, PermissionError, OSError, TypeError, ValueError, json.JSONDecodeError) as exc:
            logging.warning(f"⚠️ 旧缓存回填失败: {exc}")
            old_map = {}

        preserved = sorted(target_tickers & set(old_map.keys()))
        missing = sorted(target_tickers - set(old_map.keys()))
        if preserved and not missing:
            message = "亚洲 K 线远端拉取失败，已保留现有缓存"
            return (
                True,
                message,
                {
                    "target_count": len(target_tickers),
                    "written_count": len(preserved),
                    "single_recovered": [],
                    "reused": preserved,
                    "missing": [],
                    "cache_preserved": True,
                },
            )

        message = "亚洲 K 线缓存全量拉取失败"
        return (
            False,
            message,
            {
                "target_count": len(target_tickers),
                "written_count": 0,
                "single_recovered": [],
                "reused": preserved,
                "missing": missing or sorted(target_tickers),
                "cache_preserved": False,
            },
        )

    row_map = _rows_to_map(data)
    stale_tickers = _drop_stale_kline_rows(row_map, target_tickers)
    if stale_tickers:
        logging.warning(f"⚠️ 全量抓取发现 K 线日期落后 {len(stale_tickers)} 只，按缺失处理: {stale_tickers}")
    missing = sorted(target_tickers - set(row_map.keys()))
    single_recovered: list[str] = []

    if missing:
        logging.warning(f"⚠️ 全量抓取缺失 {len(missing)} 只，开始单票补抓: {missing}")
        rescue_session = build_yf_session(use_cf_proxy)
        for ticker in list(missing):
            name = ticker_to_name.get(ticker, ticker)
            try:
                if get_yf_rate_limit_status()["active"]:
                    logging.warning("⚠️ Yahoo Finance 冷却中，停止单票补抓")
                    break
                one = fetch_single_kline(
                    name,
                    ticker,
                    period=period,
                    use_cf_proxy=use_cf_proxy,
                    session=rescue_session,
                )
                if one:
                    row_map[ticker] = one
                    single_recovered.append(ticker)
            except Exception as exc:
                if is_yf_rate_limit_error(exc):
                    remaining_sec = mark_yf_rate_limited(exc)
                    logging.warning(f"⚠️ 单票补抓触发 Yahoo Finance 限流 {ticker}: 冷却 {remaining_sec:.0f}s — {exc}")
                    break
                if isinstance(exc, (AttributeError, KeyError, OSError, RuntimeError, TypeError, ValueError)):
                    logging.warning(f"⚠️ 单票补抓失败 {ticker}: {exc}")
                    continue
                raise
        missing = sorted(target_tickers - set(row_map.keys()))
        rescue_stale = _drop_stale_kline_rows(row_map, target_tickers)
        if rescue_stale:
            stale_tickers = sorted(set(stale_tickers) | set(rescue_stale))
            logging.warning(f"⚠️ 单票补抓后仍有 K 线日期落后，拒绝写入: {rescue_stale}")
            missing = sorted(target_tickers - set(row_map.keys()))

    reused: list[str] = []
    if missing:
        try:
            old_map = _load_cached_row_map(output_dir)
            for ticker in list(missing):
                if ticker in old_map:
                    row_map[ticker] = old_map[ticker]
                    reused.append(ticker)
            reused_stale = _drop_stale_kline_rows(row_map, target_tickers)
            if reused_stale:
                stale_tickers = sorted(set(stale_tickers) | set(reused_stale))
                reused = [ticker for ticker in reused if ticker not in set(reused_stale)]
                logging.warning(f"⚠️ 旧缓存回填包含落后 K 线，已拒绝: {reused_stale}")
            if reused:
                logging.warning(f"⚠️ 已从旧缓存回填 {len(reused)} 只: {sorted(reused)}")
            missing = sorted(target_tickers - set(row_map.keys()))
        except (FileNotFoundError, PermissionError, OSError, TypeError, ValueError, json.JSONDecodeError) as exc:
            logging.warning(f"⚠️ 旧缓存回填失败: {exc}")

    if missing:
        message = f"亚洲 K 线缓存同步失败，仍缺失 {len(missing)} 只({', '.join(missing)})，未覆盖现有缓存"
        return (
            False,
            message,
            {
                "target_count": len(target_tickers),
                "written_count": 0,
                "single_recovered": single_recovered,
                "reused": reused,
                "stale": stale_tickers,
                "missing": missing,
            },
        )

    final_data = list(row_map.values())
    final_data.sort(key=lambda item: (item.get("market", ""), item.get("name", "")))
    save_kline_data(final_data, output_dir)

    parts = [f"亚洲 K 线缓存同步完成，共 {len(final_data)} 只"]
    if single_recovered:
        parts.append(f"单票补抓 {len(single_recovered)} 只")
    if reused:
        parts.append(f"旧缓存回填 {len(reused)} 只")
    message = "，".join(parts)
    return (
        True,
        message,
        {
            "target_count": len(target_tickers),
            "written_count": len(final_data),
            "single_recovered": single_recovered,
            "reused": reused,
            "stale": stale_tickers,
            "missing": [],
        },
    )


def main():
    parser = argparse.ArgumentParser(description="亚洲寡头 250 日 K 线数据拉取器")
    parser.add_argument(
        "--market",
        choices=["TW", "KR", "JP", "HK"],
        help="只拉指定市场（TW=台湾 KR=韩国 JP=日本 HK=香港）",
    )
    parser.add_argument(
        "--ticker",
        help="只拉单只标的（如 8035.T）",
    )
    parser.add_argument(
        "--period",
        default="1y",
        help="K 线周期（默认 1y ≈ 250 个交易日）",
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=6,
        help="并发线程数（默认 6，太高会触发上游限速）",
    )
    parser.add_argument(
        "--output-dir",
        help="输出目录（默认：亚洲寡头行情/data/）",
    )
    parser.add_argument(
        "--strict-sync",
        action="store_true",
        help="严格同步模式：缺票时拒绝覆盖 latest 缓存",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="只列出目标标的，不实际拉数据",
    )

    args = parser.parse_args()

    # Why: dry-run 模式方便确认标的列表是否正确
    if args.dry_run:
        tickers = filter_asian_tickers(args.market)
        print(f"\n📋 目标标的 ({len(tickers)} 只):\n")
        for name, ticker in sorted(tickers.items(), key=lambda x: x[1]):
            print(f"  {ticker:>12s}  {name:<20s}  [{_get_market_name(ticker)}] {_find_track(ticker)}")
        return

    if args.strict_sync:
        success, message, _report = sync_asian_kline_cache(
            market_filter=args.market,
            single_ticker=args.ticker,
            max_workers=args.workers,
            period=args.period,
            output_dir=args.output_dir,
        )
        if success:
            logging.info(message)
            return
        logging.error(message)
        raise SystemExit(1)

    data = fetch_all_asian_klines(
        market_filter=args.market,
        single_ticker=args.ticker,
        max_workers=args.workers,
        period=args.period,
    )

    if data:
        save_kline_data(data, args.output_dir)


if __name__ == "__main__":
    main()
