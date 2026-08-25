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
import subprocess
import sys
import time
from datetime import date, datetime, timedelta
from functools import partial
from urllib.parse import urlencode

from domains.market_calendar import MarketCalendar
from infra.http_safety import ensure_https_request, requests_get_https
from infra.tasks.lifecycle import (
    bounded_io_timeout,
    raise_if_cancelled,
    reraise_task_cancellation,
    wait_with_cancellation,
)
from infra.tasks.owner_lifecycle import invoke_with_cancellation
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
    "2330.TW": "\u5148\u8fdb\u5236\u7a0b\u4ee3\u5de5",
    "6274.TWO": "高频PCB与覆铜板材料",
    "8035.T": "前道晶圆设备与量测",
    "4063.T": "关键晶圆材料与特种工艺",
    "3436.T": "关键晶圆材料与特种工艺",
    "7735.T": "AI PCB设备与关键耗材",
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
ASIAN_EXCLUDED_TICKERS = {"6594.T"}
ASIAN_EXCLUDED_COMPANIES = {"Nidec"}

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
_SYSTEM_CURL_PATH = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32", "curl.exe")
_SYSTEM_CURL_MAX_OUTPUT_BYTES = 1024 * 1024
_TWSE_ALLOWED_HOSTS = frozenset({"www.twse.com.tw"})
_TPEX_ALLOWED_HOSTS = frozenset({"www.tpex.org.tw"})
_NAVER_STOCK_ALLOWED_HOSTS = frozenset({"m.stock.naver.com"})
_YAHOO_JAPAN_ALLOWED_HOSTS = frozenset({"finance.yahoo.co.jp"})
_TENCENT_HK_ALLOWED_HOSTS = frozenset({"web.ifzq.gtimg.cn"})


def _history_request_timeout(cancellation_token=None, default_seconds: float = 20.0) -> float:
    return bounded_io_timeout(default_seconds, cancellation_token)


def _response_json(response, cancellation_token=None):
    payload = response.json()
    raise_if_cancelled(cancellation_token)
    return payload


def _response_text(response, cancellation_token=None) -> str:
    text = response.text
    raise_if_cancelled(cancellation_token)
    return text


def _system_curl_timeout_seconds(timeout) -> int:
    if isinstance(timeout, tuple):
        timeout = max(timeout)
    try:
        seconds = float(timeout)
    except (TypeError, ValueError):
        seconds = 20.0
    return max(1, min(30, math.ceil(seconds)))


def _fetch_official_json_via_system_curl(
    url: str,
    *,
    allowed_hosts,
    headers: dict[str, str] | None,
    timeout,
    cancellation_token=None,
) -> dict | None:
    """经 Windows Schannel 严格验签请求交易所官方 JSON，失败时不降级证书校验。"""
    ensure_https_request(
        url,
        allowed_hosts=allowed_hosts,
        allow_reserved_tun_for_allowed_hosts=True,
    )
    raise_if_cancelled(cancellation_token)
    if not os.path.isfile(_SYSTEM_CURL_PATH):
        return None

    header_args: list[str] = []
    for name, value in (headers or {}).items():
        header_name = str(name)
        header_value = str(value)
        if any(marker in header_name or marker in header_value for marker in ("\r", "\n", "\x00")):
            raise ValueError("system curl headers must not contain control characters")
        header_args.extend(("--header", f"{header_name}: {header_value}"))

    timeout_seconds = _system_curl_timeout_seconds(timeout)
    command = [
        _SYSTEM_CURL_PATH,
        "--disable",
        "--silent",
        "--show-error",
        "--fail",
        "--proto",
        "=https",
        "--tlsv1.2",
        "--max-time",
        str(timeout_seconds),
        "--max-filesize",
        str(_SYSTEM_CURL_MAX_OUTPUT_BYTES),
        *header_args,
        url,
    ]
    try:
        completed = subprocess.run(
            command,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout_seconds + 2,
            check=False,
            shell=False,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        logging.warning("Official system curl request failed: %s", exc)
        return None
    raise_if_cancelled(cancellation_token)
    if completed.returncode != 0:
        logging.warning("Official system curl request failed with exit code %s", completed.returncode)
        return None
    try:
        payload = json.loads(completed.stdout)
    except (TypeError, ValueError, json.JSONDecodeError):
        logging.warning("Official system curl response was not valid JSON")
        return None
    return payload if isinstance(payload, dict) else None


class _SystemCurlJsonResponse:
    def __init__(self, payload: dict):
        self._payload = payload
        self.status_code = 200

    def json(self) -> dict:
        return self._payload


class _SystemCurlJsonSession:
    def __init__(self, allowed_hosts, cancellation_token=None):
        self._allowed_hosts = allowed_hosts
        self._cancellation_token = cancellation_token

    def get(self, url, *, params=None, headers=None, timeout=20, **_kwargs):
        request_url = str(url)
        if params:
            separator = "&" if "?" in request_url else "?"
            request_url = f"{request_url}{separator}{urlencode(params, doseq=True)}"
        payload = _fetch_official_json_via_system_curl(
            request_url,
            allowed_hosts=self._allowed_hosts,
            headers=headers,
            timeout=timeout,
            cancellation_token=self._cancellation_token,
        )
        if payload is None:
            raise OSError("official system curl history request failed")
        return _SystemCurlJsonResponse(payload)


def _deadline_from_time_budget(time_budget_sec: float | int | None) -> float | None:
    if time_budget_sec is None:
        return None
    try:
        budget = float(time_budget_sec)
    except (TypeError, ValueError):
        return None
    if budget <= 0:
        return time.monotonic()
    return time.monotonic() + budget


def _deadline_exceeded(deadline: float | None, cancellation_checkpoint=None) -> bool:
    _check_cancellation(cancellation_checkpoint)
    return deadline is not None and time.monotonic() >= deadline


def _check_cancellation(cancellation_checkpoint=None) -> None:
    if cancellation_checkpoint is not None:
        cancellation_checkpoint()


def _remaining_time_budget(deadline: float | None) -> float | None:
    if deadline is None:
        return None
    return max(0.0, deadline - time.monotonic())


def _time_budget_exhausted_result(
    target_tickers: set[str],
    output_dir: str,
    expected_latest_dates: dict[str, date] | None = None,
) -> tuple[bool, str, dict]:
    try:
        old_map = _load_cached_row_map(output_dir)
    except (FileNotFoundError, PermissionError, OSError, TypeError, ValueError, json.JSONDecodeError) as exc:
        logging.warning("Asian kline cache preserve failed after time budget: %s", exc)
        old_map = {}

    stale_tickers = _find_stale_kline_tickers(old_map, target_tickers, expected_latest_dates)
    preserved = sorted((target_tickers & set(old_map.keys())) - set(stale_tickers))
    missing = sorted(target_tickers - set(preserved))
    if preserved and not missing:
        return (
            True,
            "Asian kline sync time budget exhausted; kept existing cache",
            {
                "target_count": len(target_tickers),
                "written_count": len(preserved),
                "single_recovered": [],
                "reused": preserved,
                "stale": [],
                "missing": [],
                "cache_preserved": True,
                "time_budget_exhausted": True,
            },
        )

    return (
        False,
        "Asian kline sync time budget exhausted before cache was complete",
        {
            "target_count": len(target_tickers),
            "written_count": 0,
            "single_recovered": [],
            "reused": preserved,
            "stale": stale_tickers,
            "missing": missing,
            "cache_preserved": False,
            "time_budget_exhausted": True,
        },
    )


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
    excluded_companies = {name.lower() for name in ASIAN_EXCLUDED_COMPANIES}
    excluded_tickers = {ticker.upper() for ticker in ASIAN_EXCLUDED_TICKERS}
    return {
        name: ticker
        for name, ticker in tickers.items()
        if str(name).strip().lower() not in excluded_companies
        and str(ticker).strip().upper() not in excluded_tickers
    }


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


def _expected_market_latest_dates(target_tickers: set[str]) -> dict[str, date]:
    markets = {MarketCalendar.infer_market(ticker) for ticker in target_tickers}
    return {
        market: MarketCalendar.get_latest_completed_trade_date(market)
        for market in sorted(markets & {"TW", "HK", "T", "KS"})
    }


def _find_stale_kline_tickers(
    row_map: dict[str, dict],
    target_tickers: set[str],
    expected_latest_dates: dict[str, date] | None = None,
) -> list[str]:
    latest_by_market = _market_latest_dates(row_map)
    expected_latest_dates = expected_latest_dates or {}
    stale: list[str] = []
    for ticker in sorted(target_tickers & set(row_map.keys())):
        row = row_map.get(ticker) or {}
        market = str(row.get("market") or "").strip()
        last_date = _last_kline_date(row)
        latest_date = latest_by_market.get(market)
        expected_date = expected_latest_dates.get(MarketCalendar.infer_market(ticker))
        if last_date is None or (latest_date is not None and last_date < latest_date) or (
            expected_date is not None and last_date < expected_date
        ):
            stale.append(ticker)
    return stale


def _drop_stale_kline_rows(
    row_map: dict[str, dict],
    target_tickers: set[str],
    expected_latest_dates: dict[str, date] | None = None,
) -> list[str]:
    stale = _find_stale_kline_tickers(row_map, target_tickers, expected_latest_dates)
    for ticker in stale:
        row_map.pop(ticker, None)
    return stale


def _load_sync_cache_snapshot(output_dir: str) -> tuple[dict[str, dict], str | None]:
    try:
        return _load_cached_row_map(output_dir), None
    except FileNotFoundError:
        return {}, None
    except (PermissionError, OSError, TypeError, ValueError, json.JSONDecodeError) as exc:
        logging.warning("Asian kline cache snapshot read failed: %s", exc)
        return {}, str(exc)


def _merge_kline_history(fetched_row: dict, cached_row: dict | None) -> dict:
    """用最新源覆盖同日记录，同时保留旧快照中未被本次短窗口返回的历史。"""
    fetched_klines = list((fetched_row or {}).get("klines") or [])
    cached_klines = list((cached_row or {}).get("klines") or [])
    if not cached_klines:
        return dict(fetched_row)
    if not fetched_klines:
        return dict(cached_row)

    merged_klines: dict[str, dict] = {}
    for kline in [*cached_klines, *fetched_klines]:
        kline_date = str((kline or {}).get("date") or "").strip()
        if _date_from_iso(kline_date) is not None:
            merged_klines[kline_date] = dict(kline)
    if not merged_klines:
        return dict(cached_row)

    merged_row = dict(cached_row)
    merged_row.update(fetched_row)
    merged_row["klines"] = [merged_klines[kline_date] for kline_date in sorted(merged_klines)]
    merged_row["kline_count"] = len(merged_row["klines"])
    return merged_row


def _merge_rows_with_cached_history(row_map: dict[str, dict], cached_map: dict[str, dict]) -> dict[str, dict]:
    return {
        ticker: _merge_kline_history(row, cached_map.get(ticker))
        for ticker, row in row_map.items()
    }


def _final_sync_rows(
    row_map: dict[str, dict],
    cached_map: dict[str, dict],
    target_tickers: set[str],
    *,
    scoped_sync: bool,
) -> list[dict]:
    final_map = dict(row_map)
    if scoped_sync:
        final_map = {ticker: row for ticker, row in cached_map.items() if ticker not in target_tickers}
        final_map.update(row_map)
    final_data = list(final_map.values())
    final_data.sort(key=lambda item: (item.get("market", ""), item.get("name", "")))
    return final_data


def _empty_fetch_sync_result(
    target_tickers: set[str],
    cached_map: dict[str, dict],
    expected_latest_dates: dict[str, date],
) -> tuple[bool, str, dict]:
    stale_tickers = _find_stale_kline_tickers(cached_map, target_tickers, expected_latest_dates)
    preserved = sorted((target_tickers & set(cached_map.keys())) - set(stale_tickers))
    missing = sorted(target_tickers - set(preserved))
    if preserved and not missing:
        return (
            True,
            "亚洲 K 线远端拉取失败，已保留现有缓存",
            {
                "target_count": len(target_tickers),
                "written_count": len(preserved),
                "single_recovered": [],
                "reused": preserved,
                "missing": [],
                "cache_preserved": True,
            },
        )
    return (
        False,
        f"亚洲 K 线缓存全量拉取失败，仍缺失 {len(missing)} 只，未覆盖现有缓存",
        {
            "target_count": len(target_tickers),
            "written_count": 0,
            "single_recovered": [],
            "reused": preserved,
            "stale": stale_tickers,
            "missing": missing,
            "cache_preserved": False,
        },
    )


def _find_market_wide_missing_tail_sessions(
    row_map: dict[str, dict],
    cached_map: dict[str, dict],
    target_tickers: set[str],
    expected_latest_dates: dict[str, date],
) -> list[str]:
    """识别所有同市场标的共同缺失的近期交易日，防止尾日存在却中间断柱。"""
    tickers_by_market: dict[str, list[str]] = {}
    for ticker in target_tickers:
        market = MarketCalendar.infer_market(ticker)
        if market in expected_latest_dates:
            tickers_by_market.setdefault(market, []).append(ticker)

    missing_sessions: list[str] = []
    for market, tickers in tickers_by_market.items():
        if len(tickers) < 2:
            continue
        cached_dates = [_last_kline_date(cached_map.get(ticker)) for ticker in tickers]
        latest_cached_date = max((item for item in cached_dates if item is not None), default=None)
        expected_date = expected_latest_dates[market]
        if latest_cached_date is None or latest_cached_date >= expected_date:
            continue
        available_dates = {
            _date_from_iso(str((kline or {}).get("date") or ""))
            for ticker in tickers
            for kline in (row_map.get(ticker) or {}).get("klines") or []
        }
        cursor = latest_cached_date + timedelta(days=1)
        while cursor <= expected_date:
            try:
                is_trade_day = MarketCalendar.is_trade_day(cursor, market=market)
            except (AttributeError, OSError, RuntimeError, TypeError, ValueError) as exc:
                logging.warning("Asian market calendar check failed for %s: %s", market, exc)
                break
            if is_trade_day and cursor not in available_dates:
                missing_sessions.append(f"{market}:{cursor.isoformat()}")
            cursor += timedelta(days=1)
    return missing_sessions


def _rescue_missing_kline_rows(
    row_map: dict[str, dict],
    missing: list[str],
    ticker_to_name: dict[str, str],
    *,
    period: str,
    deadline: float | None,
    cancellation_checkpoint,
    cancellation_token,
) -> list[str]:
    recovered: list[str] = []
    rescue_session = build_yf_session()
    for ticker in missing:
        _check_cancellation(cancellation_checkpoint)
        raise_if_cancelled(cancellation_token)
        if _deadline_exceeded(deadline, cancellation_checkpoint):
            logging.warning("Asian kline sync time budget exhausted; stop single-symbol rescue")
            break
        try:
            one = invoke_with_cancellation(
                fetch_single_kline,
                cancellation_token,
                ticker_to_name.get(ticker, ticker),
                ticker,
                period=period,
                session=rescue_session,
            )
            if one:
                row_map[ticker] = one
                recovered.append(ticker)
        except Exception as exc:
            reraise_task_cancellation(exc)
            if is_yf_rate_limit_error(exc):
                logging.warning("单票补抓遇到上游限流 %s: %s", ticker, exc)
                continue
            if isinstance(exc, (AttributeError, KeyError, OSError, RuntimeError, TypeError, ValueError)):
                logging.warning("单票补抓失败 %s: %s", ticker, exc)
                continue
            raise
    return recovered


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


def _finalize_klines(
    raw_rows: list[dict],
    *,
    start_date: date,
    end_date: date,
    cancellation_token=None,
) -> list[dict]:
    deduped: dict[str, dict] = {}
    for row in raw_rows:
        raise_if_cancelled(cancellation_token)
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
    cancellation_token=None,
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
                timeout=_history_request_timeout(cancellation_token),
                allowed_hosts=_TWSE_ALLOWED_HOSTS,
                allow_reserved_tun_for_allowed_hosts=True,
            )
            payload = _response_json(response, cancellation_token)
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
    cancellation_token=None,
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
            timeout=_history_request_timeout(cancellation_token),
            allowed_hosts=_TPEX_ALLOWED_HOSTS,
            allow_reserved_tun_for_allowed_hosts=True,
        )
        payload = _response_json(response, cancellation_token)
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


def _fetch_tw_history_twse_system_curl(
    ticker: str,
    _http_session,
    *,
    start_date: date,
    end_date: date,
    cancellation_token=None,
) -> list[dict]:
    session = _SystemCurlJsonSession({"www.twse.com.tw"}, cancellation_token)
    return _fetch_tw_history_twse(
        ticker,
        session,
        start_date=start_date,
        end_date=end_date,
        cancellation_token=cancellation_token,
    )


def _fetch_tw_history_tpex_system_curl(
    ticker: str,
    _http_session,
    *,
    start_date: date,
    end_date: date,
    cancellation_token=None,
) -> list[dict]:
    session = _SystemCurlJsonSession({"www.tpex.org.tw"}, cancellation_token)
    return _fetch_tw_history_tpex(
        ticker,
        session,
        start_date=start_date,
        end_date=end_date,
        cancellation_token=cancellation_token,
    )


def _fetch_kr_history_naver(
    ticker: str,
    http_session,
    *,
    start_date: date,
    end_date: date,
    cancellation_token=None,
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
            timeout=_history_request_timeout(cancellation_token),
            allowed_hosts=_NAVER_STOCK_ALLOWED_HOSTS,
            allow_reserved_tun_for_allowed_hosts=True,
        )
        payload = _response_json(response, cancellation_token) or []
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
    cancellation_token=None,
) -> list[dict]:
    base_code = str(ticker or "").split(".")[0].strip()
    if not base_code:
        return []

    history_url = f"https://finance.yahoo.co.jp/quote/{base_code}.T/history"
    response = requests_get_https(
        history_url,
        session=http_session,
        headers={"User-Agent": "Mozilla/5.0", "Referer": history_url},
        timeout=_history_request_timeout(cancellation_token),
        allowed_hosts=_YAHOO_JAPAN_ALLOWED_HOSTS,
        allow_reserved_tun_for_allowed_hosts=True,
    )
    token_match = _YJ_JWT_TOKEN_RE.search(_response_text(response, cancellation_token))
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
            timeout=_history_request_timeout(cancellation_token),
            allowed_hosts=_YAHOO_JAPAN_ALLOWED_HOSTS,
            allow_reserved_tun_for_allowed_hosts=True,
        )
        payload = _response_json(api_response, cancellation_token)
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
    cancellation_token=None,
) -> list[dict]:
    rate_limit_status = get_yf_rate_limit_status()
    if rate_limit_status["active"]:
        logging.warning(
            "⚠️ %s: Yahoo Finance 回退源冷却中，跳过回退请求 (剩余 %.0fs)",
            ticker,
            rate_limit_status["remaining_sec"],
        )
        return []
    try:
        import yfinance as yf
    except (ImportError, ModuleNotFoundError):
        return []
    end_exclusive = end_date + timedelta(days=1)
    try:
        frame = yf.Ticker(ticker, session=http_session).history(
            start=start_date.isoformat(),
            end=end_exclusive.isoformat(),
            auto_adjust=False,
            timeout=_history_request_timeout(cancellation_token),
        )
    except Exception as exc:
        if not is_yf_rate_limit_error(exc):
            raise
        remaining_sec = mark_yf_rate_limited(exc)
        logging.warning(f"⚠️ {ticker}: Yahoo Finance 回退源限流，冷却 {remaining_sec:.0f}s — {exc}")
        return []
    raise_if_cancelled(cancellation_token)
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
    cancellation_token=None,
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
        timeout=_history_request_timeout(cancellation_token),
        allowed_hosts=_TENCENT_HK_ALLOWED_HOSTS,
        allow_reserved_tun_for_allowed_hosts=True,
    )
    payload = _response_json(response, cancellation_token)
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


def _call_history_source(cancellation_token, fetcher, *args, **kwargs):
    return invoke_with_cancellation(fetcher, cancellation_token, *args, **kwargs)


def _taiwan_history_source_or_empty(call, source_name: str, fetcher, *args, **kwargs) -> list[dict]:
    try:
        return call(fetcher, *args, **kwargs)
    except Exception as exc:
        reraise_task_cancellation(exc)
        if _is_tls_verification_error(exc) or isinstance(exc, (ConnectionError, OSError, TimeoutError)):
            logging.warning("%s history source failed for %s: %s", source_name, args[0], exc)
            return []
        raise


def _fetch_market_history_rows(
    ticker: str,
    http_session,
    *,
    start_date: date,
    end_date: date,
    target_rows: int,
    cancellation_token=None,
) -> tuple[list[dict], str]:
    call = partial(_call_history_source, cancellation_token)
    args = (ticker, http_session)
    window = {"start_date": start_date, "end_date": end_date}
    suffix = _get_market_suffix(ticker)
    if suffix == ".TW":
        rows = _taiwan_history_source_or_empty(call, "TWSE", _fetch_tw_history_twse, *args, **window)
        if rows:
            return rows, "twse_stock_day"
        rows = _taiwan_history_source_or_empty(
            call,
            "TWSE system curl",
            _fetch_tw_history_twse_system_curl,
            *args,
            **window,
        )
        return (rows, "twse_stock_day_system_curl") if rows else (call(_fetch_yfinance_history_rows, *args, **window), "yfinance_history")
    if suffix == ".TWO":
        rows = _taiwan_history_source_or_empty(call, "TPEX", _fetch_tw_history_tpex, *args, **window)
        if rows:
            return rows, "tpex_trading_stock"
        rows = _taiwan_history_source_or_empty(
            call,
            "TPEX system curl",
            _fetch_tw_history_tpex_system_curl,
            *args,
            **window,
        )
        return (rows, "tpex_trading_stock_system_curl") if rows else (call(_fetch_yfinance_history_rows, *args, **window), "yfinance_history")
    if suffix == ".KS":
        return call(_fetch_kr_history_naver, *args, **window), "naver_history"
    if suffix == ".T":
        rows = call(_fetch_jp_history_yahoo_japan, *args, **window)
        return (rows, "yj_history") if rows else (
            call(_fetch_yfinance_history_rows, *args, **window),
            "yfinance_history",
        )
    if suffix == ".HK":
        rows = call(_fetch_hk_history_tencent, *args, target_rows=target_rows, **window)
        return rows, "tencent_hk_qfq"
    return [], "unsupported"


def fetch_single_kline(
    name: str,
    ticker: str,
    period: str = "1y",
    session=None,
    *,
    cancellation_token=None,
) -> dict | None:
    """拉取单只亚洲标的的日线、市场、货币与赛道快照。"""
    try:
        raise_if_cancelled(cancellation_token)
        ticker = str(ticker or "").strip().upper()
        http_session = session or build_yf_session()
        start_date, end_date, target_rows = _resolve_period_window(period)
        raw_rows, source = _fetch_market_history_rows(
            ticker,
            http_session,
            start_date=start_date,
            end_date=end_date,
            target_rows=target_rows,
            cancellation_token=cancellation_token,
        )
        klines = _finalize_klines(
            raw_rows,
            start_date=start_date,
            end_date=end_date,
            cancellation_token=cancellation_token,
        )

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
        reraise_task_cancellation(e)
        if is_yf_rate_limit_error(e):
            logging.warning(f"⚠️ {name}({ticker}): 上游请求限流 — {e}")
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
    time_budget_sec: float | int | None = None,
    cancellation_checkpoint=None,
    cancellation_token=None,
) -> list[dict]:
    """按市场/单票筛选拉取亚洲寡头 K 线数据。"""
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
    yf_session = build_yf_session()
    deadline = _deadline_from_time_budget(time_budget_sec)
    for name, ticker in tickers.items():
        raise_if_cancelled(cancellation_token)
        if _deadline_exceeded(deadline, cancellation_checkpoint):
            logging.warning(
                "Asian kline fetch time budget exhausted; stop early after %s/%s",
                len(results),
                len(tickers),
            )
            break
        wait_with_cancellation(0.3, cancellation_token)
        try:
            data = invoke_with_cancellation(
                fetch_single_kline,
                cancellation_token,
                name,
                ticker,
                period=period,
                session=yf_session,
            )
            if data:
                results.append(data)
                logging.info(f"  ✅ {name}({ticker}) [{data['market']}]: {data['kline_count']} 根K线")
            else:
                failed.append(f"{name}({ticker})")
        except Exception as e:
            reraise_task_cancellation(e)
            failed.append(f"{name}({ticker})")
            if is_yf_rate_limit_error(e):
                logging.warning(f"  ⚠️ {name}({ticker}): 上游请求限流 — {e}")
                continue
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
    output_dir: str | None = None,
    time_budget_sec: float | int | None = None,
    cancellation_checkpoint=None,
    cancellation_token=None,
) -> tuple[bool, str, dict]:
    """全量拉取、单票补抓、旧缓存回填；仍缺票时拒绝覆盖 latest。"""
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
    expected_latest_dates = _expected_market_latest_dates(target_tickers)
    output_dir = _resolve_cache_output_dir(output_dir)
    cached_map, cache_read_error = _load_sync_cache_snapshot(output_dir)
    scoped_sync = market_filter is not None or single_ticker is not None
    if scoped_sync and (cache_read_error is not None or not cached_map):
        return (
            False,
            "亚洲 K 线局部同步缺少可保留的共享缓存，未执行覆盖写入",
            {
                "target_count": len(target_tickers),
                "written_count": 0,
                "single_recovered": [],
                "reused": [],
                "missing": sorted(target_tickers),
                "cache_preserved": False,
            },
        )
    deadline = _deadline_from_time_budget(time_budget_sec)
    _check_cancellation(cancellation_checkpoint)
    raise_if_cancelled(cancellation_token)

    data = fetch_all_asian_klines(
        market_filter=market_filter,
        single_ticker=single_ticker,
        max_workers=max_workers,
        period=period,
        time_budget_sec=_remaining_time_budget(deadline),
        cancellation_checkpoint=cancellation_checkpoint,
        cancellation_token=cancellation_token,
    )
    if _deadline_exceeded(deadline, cancellation_checkpoint):
        return _time_budget_exhausted_result(target_tickers, output_dir, expected_latest_dates)
    if not data:
        return _empty_fetch_sync_result(target_tickers, cached_map, expected_latest_dates)

    row_map = _merge_rows_with_cached_history(_rows_to_map(data), cached_map)
    stale_tickers = _drop_stale_kline_rows(row_map, target_tickers, expected_latest_dates)
    if stale_tickers:
        logging.warning(f"⚠️ 全量抓取发现 K 线日期落后 {len(stale_tickers)} 只，按缺失处理: {stale_tickers}")
    missing = sorted(target_tickers - set(row_map.keys()))
    single_recovered: list[str] = []

    if missing:
        logging.warning(f"⚠️ 全量抓取缺失 {len(missing)} 只，开始单票补抓: {missing}")
        single_recovered = _rescue_missing_kline_rows(
            row_map,
            missing,
            ticker_to_name,
            period=period,
            deadline=deadline,
            cancellation_checkpoint=cancellation_checkpoint,
            cancellation_token=cancellation_token,
        )
        if _deadline_exceeded(deadline, cancellation_checkpoint):
            return _time_budget_exhausted_result(target_tickers, output_dir, expected_latest_dates)
        row_map = _merge_rows_with_cached_history(row_map, cached_map)
        missing = sorted(target_tickers - set(row_map.keys()))
        rescue_stale = _drop_stale_kline_rows(row_map, target_tickers, expected_latest_dates)
        if rescue_stale:
            stale_tickers = sorted(set(stale_tickers) | set(rescue_stale))
            logging.warning(f"⚠️ 单票补抓后仍有 K 线日期落后，拒绝写入: {rescue_stale}")
            missing = sorted(target_tickers - set(row_map.keys()))

    reused: list[str] = []
    if missing:
        for ticker in missing:
            _check_cancellation(cancellation_checkpoint)
            if ticker in cached_map:
                row_map[ticker] = cached_map[ticker]
                reused.append(ticker)
        reused_stale = _drop_stale_kline_rows(row_map, target_tickers, expected_latest_dates)
        if reused_stale:
            stale_tickers = sorted(set(stale_tickers) | set(reused_stale))
            reused = [ticker for ticker in reused if ticker not in set(reused_stale)]
            logging.warning(f"⚠️ 旧缓存回填包含落后 K 线，已拒绝: {reused_stale}")
        if reused:
            logging.warning(f"⚠️ 已从旧缓存回填 {len(reused)} 只: {sorted(reused)}")
        missing = sorted(target_tickers - set(row_map.keys()))
        if _deadline_exceeded(deadline, cancellation_checkpoint):
            return _time_budget_exhausted_result(target_tickers, output_dir, expected_latest_dates)

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

    missing_tail_sessions = _find_market_wide_missing_tail_sessions(
        row_map,
        cached_map,
        target_tickers,
        expected_latest_dates,
    )
    if missing_tail_sessions:
        return (
            False,
            f"亚洲 K 线缓存同步失败，检测到市场级中间缺柱 {', '.join(missing_tail_sessions)}，未覆盖现有缓存",
            {
                "target_count": len(target_tickers),
                "written_count": 0,
                "single_recovered": single_recovered,
                "reused": reused,
                "stale": stale_tickers,
                "missing": [],
                "market_wide_missing_sessions": missing_tail_sessions,
            },
        )
    if _deadline_exceeded(deadline, cancellation_checkpoint):
        return _time_budget_exhausted_result(target_tickers, output_dir, expected_latest_dates)
    _check_cancellation(cancellation_checkpoint)
    final_data = _final_sync_rows(row_map, cached_map, target_tickers, scoped_sync=scoped_sync)
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
        "--time-budget-sec",
        type=float,
        default=None,
        help="Optional wall-clock budget for best-effort background sync",
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
            time_budget_sec=args.time_budget_sec,
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
        time_budget_sec=args.time_budget_sec,
    )

    if data:
        save_kline_data(data, args.output_dir)


if __name__ == "__main__":
    main()
