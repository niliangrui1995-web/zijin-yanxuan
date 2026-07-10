from __future__ import annotations

import importlib
import json
import os
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from functools import lru_cache
from threading import Lock

import requests

from core.ai_industry_chain_pool import (
    load_cached_ai_industry_chain_context_map,
    load_cached_ai_industry_chain_stock_codes,
    normalize_ai_chain_code,
)
from core.logger import get_logger
from domains.market_calendar import MarketCalendar
from infra.http_safety import requests_get_https

logger = get_logger()


class _LazyModule:
    """Load a heavy optional dependency on first attribute access."""

    def __init__(self, module_name: str, *, before_load=None):
        self._module_name = module_name
        self._before_load = before_load
        self._module = None
        self._lock = Lock()

    def _load(self):
        if self._module is None:
            with self._lock:
                if self._module is None:
                    if self._before_load is not None:
                        self._before_load()
                    self._module = importlib.import_module(self._module_name)
        return self._module

    def __getattr__(self, name: str):
        return getattr(self._load(), name)


_TQDM_PATCH_LOCK = Lock()
_TQDM_PATCHED = False


def _install_silent_tqdm() -> None:
    """Install the AkShare progress hook only when AkShare is actually used."""
    global _TQDM_PATCHED

    if _TQDM_PATCHED:
        return
    with _TQDM_PATCH_LOCK:
        if _TQDM_PATCHED:
            return
        try:
            tqdm_module = importlib.import_module("tqdm")
            tqdm_class = tqdm_module.tqdm
            if not getattr(tqdm_class, "_vcp_earnings_silent", False):
                original_init = tqdm_class.__init__
                original_update = tqdm_class.update

                def _silent_tqdm_init(self, *args, **kwargs):
                    kwargs["disable"] = True
                    original_init(self, *args, **kwargs)
                    self._my_n = 0

                def _my_tqdm_update(self, n=1):
                    original_update(self, n)
                    self._my_n += n
                    total = getattr(self, "total", None) or "?"
                    if self._my_n % 5 == 0 or self._my_n == total:
                        logger.info(f"[业绩引擎] 分页抓取中 {self._my_n}/{total}")

                tqdm_class.__init__ = _silent_tqdm_init
                tqdm_class.update = _my_tqdm_update
                tqdm_class._vcp_earnings_silent = True
        except (AttributeError, ImportError, RuntimeError, TypeError, ValueError) as exc:
            logger.debug(f"[tqdm补丁] tqdm 劫持失败（非致命）: {exc}")
        _TQDM_PATCHED = True


ak = _LazyModule("akshare", before_load=_install_silent_tqdm)
np = _LazyModule("numpy")
pd = _LazyModule("pandas")

EARNINGS_QOQ_MIN_PCT = 30.0
EARNINGS_FORMAL_REPORT_RETRY_BUDGET_SECONDS = 60.0

_POOL_CACHE = {}
_THS_FINANCIAL_BENEFIT_CACHE = {}
_THS_FINANCIAL_BENEFIT_CACHE_TTL_SEC = 30 * 60
_THS_FINANCIAL_BENEFIT_FALLBACK_TTL_SEC = 6 * 60 * 60
_THS_REQUEST_TIMEOUT = (5, 15)
_THS_MAX_RESPONSE_CHARS = 2_000_000
_THS_MAX_FLASHDATA_CHARS = 2_000_000
_THS_MAX_TITLE_ROWS = 512
_THS_MAX_SECTION_ROWS = 512
_THS_MAX_SECTION_COLUMNS = 128
_THS_FINANCIAL_BENEFIT_CACHE_MAX_ENTRIES = 512
_AKSHARE_FETCH_ERRORS = (
    AttributeError,
    ConnectionError,
    KeyError,
    OSError,
    RuntimeError,
    TimeoutError,
    TypeError,
    ValueError,
    IndexError,
)
_EARNINGS_CACHE_ERRORS = (
    AttributeError,
    ImportError,
    OSError,
    RuntimeError,
    TypeError,
    ValueError,
    json.JSONDecodeError,
)
_EARNINGS_COMPUTE_ERRORS = _AKSHARE_FETCH_ERRORS + (ArithmeticError,)


@dataclass(frozen=True)
class _SingleQuarterMetrics:
    current_single: float
    last_single: float
    yoy_base_single: float
    last_single_basis: str


class EarningsUpstreamDegraded(RuntimeError):
    def __init__(self, func_cn: str, param_str: str, elapsed_sec: float, original_error: Exception):
        self.func_cn = str(func_cn or "").strip()
        self.param_str = str(param_str or "").strip()
        self.elapsed_sec = float(elapsed_sec or 0.0)
        self.original_error = original_error
        super().__init__(
            f"{self.func_cn}({self.param_str}) upstream degraded after {self.elapsed_sec:.1f}s: {original_error}"
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "pool": self.func_cn,
            "report_date": self.param_str,
            "elapsed_sec": round(self.elapsed_sec, 3),
            "error": str(self.original_error or "").strip(),
        }

@lru_cache(maxsize=1)
def _akshare_ths_headers() -> dict[str, str]:
    _install_silent_tqdm()
    try:
        module = importlib.import_module("akshare.stock_fundamental.stock_finance_ths")
        headers = getattr(module, "headers")
        return dict(headers)
    except (AttributeError, ImportError):
        return {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/114.0.0.0 Safari/537.36"
        )
        }


def _parse_amount(value):
    """把财务字段统一转成元，兼容字符串中的 万/亿 单位。"""
    if pd.isna(value):
        return np.nan
    value_str = str(value).strip()
    if not value_str:
        return np.nan

    multiplier = 1.0
    if "万" in value_str:
        multiplier = 10000.0
        value_str = value_str.replace("万", "")
    elif "亿" in value_str:
        multiplier = 100000000.0
        value_str = value_str.replace("亿", "")

    digits = "".join(filter(lambda x: x.isdigit() or x in ".-", value_str))
    if not digits or digits in (".", "-", "-."):
        return np.nan
    try:
        return float(digits) * multiplier
    except (ValueError, TypeError):
        return np.nan


def _select_profit_columns(columns, is_koufei: bool) -> list:
    if is_koufei:
        candidate_groups = (
            lambda col: "扣除" in str(col),
            lambda col: "归属于母公司" in str(col) or "归属" in str(col),
            lambda col: "净利润" in str(col),
        )
    else:
        candidate_groups = (
            lambda col: "归属于母公司" in str(col) or "归属" in str(col),
            lambda col: "净利润" in str(col) and "扣除" not in str(col),
            lambda col: "净利润" in str(col),
        )

    for matcher in candidate_groups:
        cols = [col for col in columns if matcher(col)]
        if cols:
            return cols
    return []


def _basis_from_quick_flags(*quick_flags: bool) -> str:
    return "快报净利润回填" if any(quick_flags) else "财报"


def _q4_single_quarter_metrics(
    year: int,
    target_est_cum_profit: float,
    get_cum_profit_with_quick: Callable[[str, str], tuple[float, bool]],
) -> tuple[_SingleQuarterMetrics | None, str | None]:
    q3_date, q2_date = f"{year}-09-30", f"{year}-06-30"
    last_q4_date, last_q3_date = f"{year - 1}-12-31", f"{year - 1}-09-30"
    q3_cum, q3_quick = get_cum_profit_with_quick(q3_date, "本期累计基数")
    q2_cum, q2_quick = get_cum_profit_with_quick(q2_date, "上一季基数")
    if pd.isna(q3_cum) or pd.isna(q2_cum):
        return None, "缺记录"

    yoy_base_single = np.nan
    ly_q4_cum, _ = get_cum_profit_with_quick(last_q4_date, "去年同期基数")
    ly_q3_cum, _ = get_cum_profit_with_quick(last_q3_date, "去年同期基数")
    if pd.notna(ly_q4_cum) and pd.notna(ly_q3_cum):
        yoy_base_single = ly_q4_cum - ly_q3_cum

    return (
        _SingleQuarterMetrics(
            current_single=target_est_cum_profit - q3_cum,
            last_single=q3_cum - q2_cum,
            yoy_base_single=yoy_base_single,
            last_single_basis=_basis_from_quick_flags(q3_quick, q2_quick),
        ),
        None,
    )


def _q3_single_quarter_metrics(
    year: int,
    target_est_cum_profit: float,
    get_cum_profit_with_quick: Callable[[str, str], tuple[float, bool]],
) -> tuple[_SingleQuarterMetrics | None, str | None]:
    q2_date, q1_date = f"{year}-06-30", f"{year}-03-31"
    last_q3_date, last_q2_date = f"{year - 1}-09-30", f"{year - 1}-06-30"
    q2_cum, q2_quick = get_cum_profit_with_quick(q2_date, "本期累计基数")
    q1_cum, q1_quick = get_cum_profit_with_quick(q1_date, "上一季基数")
    if pd.isna(q2_cum) or pd.isna(q1_cum):
        return None, "缺记录"

    yoy_base_single = np.nan
    ly_q3_cum, _ = get_cum_profit_with_quick(last_q3_date, "去年同期基数")
    ly_q2_cum, _ = get_cum_profit_with_quick(last_q2_date, "去年同期基数")
    if pd.notna(ly_q3_cum) and pd.notna(ly_q2_cum):
        yoy_base_single = ly_q3_cum - ly_q2_cum

    return (
        _SingleQuarterMetrics(
            current_single=target_est_cum_profit - q2_cum,
            last_single=q2_cum - q1_cum,
            yoy_base_single=yoy_base_single,
            last_single_basis=_basis_from_quick_flags(q2_quick, q1_quick),
        ),
        None,
    )


def _q2_single_quarter_metrics(
    year: int,
    target_est_cum_profit: float,
    get_cum_profit_with_quick: Callable[[str, str], tuple[float, bool]],
) -> tuple[_SingleQuarterMetrics | None, str | None]:
    q1_date = f"{year}-03-31"
    last_q2_date, last_q1_date = f"{year - 1}-06-30", f"{year - 1}-03-31"
    q1_cum, q1_quick = get_cum_profit_with_quick(q1_date, "上一季基数")
    if pd.isna(q1_cum):
        return None, "缺记录"

    yoy_base_single = np.nan
    ly_q2_cum, _ = get_cum_profit_with_quick(last_q2_date, "去年同期基数")
    ly_q1_cum, _ = get_cum_profit_with_quick(last_q1_date, "去年同期基数")
    if pd.notna(ly_q2_cum) and pd.notna(ly_q1_cum):
        yoy_base_single = ly_q2_cum - ly_q1_cum

    return (
        _SingleQuarterMetrics(
            current_single=target_est_cum_profit - q1_cum,
            last_single=q1_cum,
            yoy_base_single=yoy_base_single,
            last_single_basis=_basis_from_quick_flags(q1_quick),
        ),
        None,
    )


def _q1_single_quarter_metrics(
    year: int,
    target_est_cum_profit: float,
    get_cum_profit_with_quick: Callable[[str, str], tuple[float, bool]],
) -> tuple[_SingleQuarterMetrics | None, str | None]:
    last_q4_date, last_q3_date = f"{year - 1}-12-31", f"{year - 1}-09-30"
    last_q1_date = f"{year - 1}-03-31"
    last_q4_cum, q4_quick = get_cum_profit_with_quick(last_q4_date, "上一季基数")
    last_q3_cum, q3_quick = get_cum_profit_with_quick(last_q3_date, "上一季基数")
    if pd.isna(last_q4_cum) or pd.isna(last_q3_cum):
        return None, "缺记录"

    yoy_base_single = np.nan
    ly_q1_cum, _ = get_cum_profit_with_quick(last_q1_date, "去年同期基数")
    if pd.notna(ly_q1_cum):
        yoy_base_single = ly_q1_cum

    return (
        _SingleQuarterMetrics(
            current_single=target_est_cum_profit,
            last_single=last_q4_cum - last_q3_cum,
            yoy_base_single=yoy_base_single,
            last_single_basis=_basis_from_quick_flags(q4_quick, q3_quick),
        ),
        None,
    )


_SINGLE_QUARTER_METRIC_RESOLVERS = {
    12: _q4_single_quarter_metrics,
    9: _q3_single_quarter_metrics,
    6: _q2_single_quarter_metrics,
    3: _q1_single_quarter_metrics,
}


def _compute_single_quarter_metrics(
    year: int,
    month: int,
    target_est_cum_profit: float,
    get_cum_profit_with_quick: Callable[[str, str], tuple[float, bool]],
) -> tuple[_SingleQuarterMetrics, str | None]:
    resolver = _SINGLE_QUARTER_METRIC_RESOLVERS.get(month)
    if resolver is None:
        return _SingleQuarterMetrics(np.nan, np.nan, np.nan, "财报"), None

    metrics, error = resolver(year, target_est_cum_profit, get_cum_profit_with_quick)
    if metrics is None:
        return _SingleQuarterMetrics(np.nan, np.nan, np.nan, "财报"), error
    return metrics, error


def _preview_remote_text(raw_text, limit: int = 120) -> str:
    preview = " ".join((raw_text or "").split())
    if not preview:
        return "<empty>"
    if len(preview) > limit:
        return f"{preview[:limit]}..."
    return preview


def _ths_financial_benefit_cache_key(symbol: str, indicator: str) -> str:
    return f"{str(symbol).zfill(6)}::{indicator}"


def _get_cached_ths_financial_benefit(
    symbol: str,
    indicator: str,
    *,
    max_age_sec: int,
) -> tuple[pd.DataFrame | None, float | None]:
    cache_key = _ths_financial_benefit_cache_key(symbol, indicator)
    cached = _THS_FINANCIAL_BENEFIT_CACHE.get(cache_key)
    if cached is None:
        return None, None

    cached_time, cached_df = cached
    age_sec = time.time() - cached_time
    if age_sec > max_age_sec:
        return None, None
    return cached_df.copy(), age_sec


def _set_cached_ths_financial_benefit(symbol: str, indicator: str, df: pd.DataFrame) -> pd.DataFrame:
    cache_key = _ths_financial_benefit_cache_key(symbol, indicator)
    if cache_key not in _THS_FINANCIAL_BENEFIT_CACHE and len(_THS_FINANCIAL_BENEFIT_CACHE) >= _THS_FINANCIAL_BENEFIT_CACHE_MAX_ENTRIES:
        excess = len(_THS_FINANCIAL_BENEFIT_CACHE) - _THS_FINANCIAL_BENEFIT_CACHE_MAX_ENTRIES + 1
        for old_key, _old_value in sorted(_THS_FINANCIAL_BENEFIT_CACHE.items(), key=lambda item: item[1][0])[:excess]:
            _THS_FINANCIAL_BENEFIT_CACHE.pop(old_key, None)
    cached_df = df.copy()
    _THS_FINANCIAL_BENEFIT_CACHE[cache_key] = (time.time(), cached_df)
    return cached_df.copy()


def _format_ths_payload_error(symbol: str, response_text: str, detail: str, status_code: int | None = None) -> str:
    parts = [f"symbol={str(symbol).zfill(6)}"]
    if status_code is not None:
        parts.append(f"status={status_code}")
    parts.append(f"len={len(response_text or '')}")
    parts.append(f"preview={_preview_remote_text(response_text)}")
    parts.append(detail)
    return "THS 返回异常: " + ", ".join(parts)


def _raise_if_ths_text_too_large(
    symbol: str,
    text: str,
    *,
    limit: int,
    label: str,
    response_text: str,
    status_code: int | None = None,
) -> None:
    if len(text or "") > limit:
        raise ValueError(
            _format_ths_payload_error(
                symbol,
                response_text,
                f"{label} too large: len={len(text or '')}, limit={limit}",
                status_code,
            )
        )


def _validate_ths_financial_shape(symbol: str, section_key: str, title_data: list, header_row: list, data_rows: list) -> None:
    if len(title_data) > _THS_MAX_TITLE_ROWS:
        raise ValueError(f"THS 数据结构异常: symbol={symbol}, title rows exceed limit")
    if len(data_rows) > _THS_MAX_SECTION_ROWS:
        raise ValueError(f"THS 数据结构异常: symbol={symbol}, {section_key} rows exceed limit")
    if len(header_row) > _THS_MAX_SECTION_COLUMNS:
        raise ValueError(f"THS 数据结构异常: symbol={symbol}, {section_key} columns exceed limit")
    for row in data_rows:
        if not isinstance(row, list):
            raise ValueError(f"THS 数据结构异常: symbol={symbol}, {section_key} 行不是列表")
        if len(row) > _THS_MAX_SECTION_COLUMNS:
            raise ValueError(f"THS 数据结构异常: symbol={symbol}, {section_key} row columns exceed limit")


def _fetch_stock_financial_benefit_ths(symbol: str, indicator: str = "按报告期") -> pd.DataFrame:
    indicator_map = {
        "按报告期": "report",
        "按单季度": "simple",
        "按年度": "year",
    }
    section_key = indicator_map.get(indicator)
    if section_key is None:
        raise ValueError(f"不支持的同花顺利润表口径: {indicator}")

    cached_df, _ = _get_cached_ths_financial_benefit(
        symbol,
        indicator,
        max_age_sec=_THS_FINANCIAL_BENEFIT_CACHE_TTL_SEC,
    )
    if cached_df is not None:
        return cached_df

    symbol = str(symbol).zfill(6)
    url = f"https://basic.10jqka.com.cn/api/stock/finance/{symbol}_benefit.json"
    headers = _akshare_ths_headers()
    headers.setdefault("Accept", "application/json, text/plain, */*")
    headers.setdefault("Referer", f"https://basic.10jqka.com.cn/new/{symbol}/finance.html")

    try:
        response = requests_get_https(url, headers=headers, timeout=_THS_REQUEST_TIMEOUT)
    except requests.RequestException as exc:
        raise RuntimeError(f"THS 请求失败: symbol={symbol}, {exc}") from exc

    response_text = response.text or ""
    if response.status_code != 200:
        raise RuntimeError(
            _format_ths_payload_error(
                symbol,
                response_text,
                "HTTP 状态异常",
                response.status_code,
            )
        )
    _raise_if_ths_text_too_large(
        symbol,
        response_text,
        limit=_THS_MAX_RESPONSE_CHARS,
        label="response",
        response_text=response_text,
        status_code=response.status_code,
    )

    stripped_text = response_text.strip()
    if not stripped_text:
        raise ValueError(_format_ths_payload_error(symbol, response_text, "响应体为空", response.status_code))
    if stripped_text[0] not in "{[":
        raise ValueError(_format_ths_payload_error(symbol, response_text, "返回内容不是 JSON", response.status_code))

    try:
        payload = json.loads(stripped_text)
    except json.JSONDecodeError as exc:
        raise ValueError(
            _format_ths_payload_error(symbol, response_text, f"外层 JSON 解析失败: {exc}", response.status_code)
        ) from exc

    flash_data = payload.get("flashData")
    if not flash_data:
        raise ValueError(_format_ths_payload_error(symbol, response_text, "缺少 flashData", response.status_code))
    if not isinstance(flash_data, str):
        raise ValueError(_format_ths_payload_error(symbol, response_text, "flashData 类型异常", response.status_code))
    _raise_if_ths_text_too_large(
        symbol,
        flash_data,
        limit=_THS_MAX_FLASHDATA_CHARS,
        label="flashData",
        response_text=response_text,
        status_code=response.status_code,
    )

    try:
        data_json = json.loads(flash_data)
    except json.JSONDecodeError as exc:
        raise ValueError(f"THS flashData 解析失败: symbol={symbol}, {exc}") from exc

    title_data = data_json.get("title") or []
    section_data = data_json.get(section_key) or []
    if not isinstance(title_data, list) or not title_data:
        raise ValueError(f"THS 数据结构异常: symbol={symbol}, title 缺失")
    if not isinstance(section_data, list):
        raise ValueError(f"THS 数据结构异常: symbol={symbol}, {section_key} 不是列表")
    if not section_data:
        return _set_cached_ths_financial_benefit(symbol, indicator, pd.DataFrame(columns=["报告期"]))

    header_row = section_data[0]
    data_rows = section_data[1:]
    if not isinstance(header_row, list):
        raise ValueError(f"THS 数据结构异常: symbol={symbol}, {section_key} 表头缺失")
    _validate_ths_financial_shape(symbol, section_key, title_data, header_row, data_rows)

    df_index = [item[0] if isinstance(item, list) else item for item in title_data]
    if data_rows and len(df_index[1:]) != len(data_rows):
        raise ValueError(f"THS 数据结构异常: symbol={symbol}, title={len(df_index[1:])}, rows={len(data_rows)}")

    temp_df = pd.DataFrame(data_rows, columns=header_row, index=df_index[1:])
    temp_df = temp_df.T
    temp_df.reset_index(inplace=True)
    temp_df.rename(columns={"index": "报告期"}, inplace=True)
    return _set_cached_ths_financial_benefit(symbol, indicator, temp_df)


def safe_ak_fetch(fetch_func, *args, max_elapsed_sec: float | None = None, **kwargs):
    """带退避的强力护甲 + 大白话进度解说"""
    retries = 3
    delay = 2.0
    started_at = time.monotonic()

    # 翻译文言文函数名
    fname = fetch_func.__name__
    func_cn = "未知金矿"
    if "yjyg" in fname:
        func_cn = "【业绩预告池】"
    elif "yjbb" in fname:
        func_cn = "【正式财报池】"
    elif "yjkb" in fname:
        func_cn = "【业绩快报池】"
    elif "financial_benefit" in fname:
        func_cn = "【同花顺历史底稿】"
    is_ths_financial = "financial_benefit" in fname

    # 提取报备日期供打印
    param_str = kwargs.get("date", kwargs.get("symbol", "全局获取"))

    # ==== 极速内存缓存过滤（仅针对大池子，同花顺个股不管） ====
    if "financial_benefit" not in fname:
        cache_key = f"{fname}_{param_str}"
        if cache_key in _POOL_CACHE:
            cached_time, cached_df = _POOL_CACHE[cache_key]
            if time.time() - cached_time < 600:  # 10 分钟 TTL，足以覆盖一次深度扫描
                # 过滤掉冗杂的打卡日志，保持清爽
                return cached_df.copy()
    # ==========================================================

    for i in range(retries):
        try:
            # 只有抓历史底稿时过于频繁，为了不刷屏少打印开始。大型池子打印。
            if not is_ths_financial:
                logger.info(f"[业绩引擎] 拉取 {func_cn} ({param_str})...")

            if fetch_func is ak.stock_financial_benefit_ths:
                res = _fetch_stock_financial_benefit_ths(*args, **kwargs)
            else:
                res = fetch_func(*args, **kwargs)

            if not is_ths_financial:
                logger.info(f"[业绩引擎] ✅ {func_cn} ({param_str}) 拉取完成")
                _POOL_CACHE[f"{fname}_{param_str}"] = (time.time(), res.copy() if not res.empty else res)

            return res

        except _AKSHARE_FETCH_ERRORS as e:
            error_msg = str(e)
            if "NoneType" in error_msg or "not subscriptable" in error_msg:
                if not is_ths_financial:
                    logger.info(f"[业绩引擎] {func_cn} ({param_str}) 暂无数据，跳过")
                return pd.DataFrame()

            elapsed_sec = time.monotonic() - started_at
            if max_elapsed_sec is not None and elapsed_sec >= float(max_elapsed_sec):
                if not is_ths_financial:
                    logger.warning(
                        f"[业绩引擎] ⚠️ {func_cn} ({param_str}) 已耗时 {elapsed_sec:.0f}s，"
                        "停止本池重试并保留后续自动重试机会"
                    )
                raise EarningsUpstreamDegraded(func_cn, param_str, elapsed_sec, e) from e

            if i == retries - 1:
                if fetch_func is ak.stock_financial_benefit_ths:
                    indicator = kwargs.get("indicator", "按报告期")
                    stale_df, age_sec = _get_cached_ths_financial_benefit(
                        param_str,
                        indicator,
                        max_age_sec=_THS_FINANCIAL_BENEFIT_FALLBACK_TTL_SEC,
                    )
                    if stale_df is not None:
                        logger.warning(
                            f"[业绩引擎] ⚠️ {func_cn} ({param_str}) 连续失败，回退使用 {int(age_sec)}s 前缓存: {e}"
                        )
                        return stale_df
                logger.error(f"[业绩引擎] ❌ {func_cn} ({param_str}) 重试 {retries} 次后仍失败: {e}")
                raise e

            if max_elapsed_sec is not None and elapsed_sec + delay >= float(max_elapsed_sec):
                if not is_ths_financial:
                    logger.warning(
                        f"[业绩引擎] ⚠️ {func_cn} ({param_str}) 本轮重试预算不足，"
                        "停止本池重试并保留后续自动重试机会"
                    )
                raise EarningsUpstreamDegraded(func_cn, param_str, elapsed_sec, e) from e

            logger.warning(f"[业绩引擎] ⚠️ {func_cn} 请求失败({e})，{delay:.0f}s 后第 {i + 2} 次重试")
            time.sleep(delay)
            delay *= 1.5


def current_active_report_dates() -> list:
    now = MarketCalendar.now("CN")
    year = now.year
    month = now.month
    dates = []
    if 1 <= month <= 4:
        dates.extend([f"{year - 1}1231", f"{year}0331"])
    elif 7 <= month <= 8:
        dates.append(f"{year}0630")
    elif month == 10:
        dates.append(f"{year}0930")
    return dates if dates else [f"{year - 1}1231", f"{year}0331", f"{year}0630", f"{year}0930"]


class EarningsEngine:
    def __init__(
        self,
        cache_file="data/earnings_state.json",
        keep_days=30,
        stock_universe_provider=load_cached_ai_industry_chain_stock_codes,
        stock_context_provider=load_cached_ai_industry_chain_context_map,
    ):
        if not os.path.isabs(cache_file):
            root_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            cache_file = os.path.join(root_dir, cache_file)

        self.cache_file = cache_file
        self.keep_days = keep_days
        self.stock_universe_provider = stock_universe_provider
        self.stock_context_provider = stock_context_provider
        self.seen_fingerprints = set()
        self.local_records = []
        self.last_sync_date = MarketCalendar.today("CN").strftime("%Y-%m-%d")
        self.last_scan_result: dict[str, object] = {}
        self._quick_report_profit_cache = {}
        self._load_cache()

    @staticmethod
    def _build_fingerprint(code: str, report_date: str, data_type: str) -> str:
        return f"SHOCK_{str(code).zfill(6)}_{report_date}_{data_type}"

    @staticmethod
    def _normalize_publish_date(raw_value) -> str:
        raw_text = str(raw_value or "").strip()
        return raw_text[:10] if raw_text else ""

    @staticmethod
    def _next_calendar_date(publish_date: str) -> str | None:
        try:
            cursor = datetime.strptime(publish_date, "%Y-%m-%d").date()
        except ValueError:
            return None
        return (cursor + timedelta(days=1)).strftime("%Y-%m-%d")

    @classmethod
    def _resolve_allowed_publish_dates(cls, target_publish_date: str, data_type: str) -> set[str]:
        allowed_dates = {target_publish_date}
        if data_type not in {"预告", "财报", "快报"}:
            return allowed_dates

        next_calendar_date = cls._next_calendar_date(target_publish_date)
        if next_calendar_date:
            allowed_dates.add(next_calendar_date)
        return allowed_dates

    @classmethod
    def _filter_candidates_by_publish_date(
        cls,
        df: pd.DataFrame,
        date_col: str,
        target_publish_date: str,
        data_type: str,
    ) -> pd.DataFrame:
        if df.empty or date_col not in df.columns:
            return df.iloc[0:0]

        allowed_dates = cls._resolve_allowed_publish_dates(target_publish_date, data_type)
        normalized_dates = df[date_col].astype(str).str[:10]
        return df[normalized_dates.isin(allowed_dates)]

    def _record_to_fingerprint(self, record: dict):
        code = str(record.get("股票代码") or record.get("代码") or "").zfill(6)
        report_date = str(record.get("报告期", "") or "")
        data_type = str(record.get("数据类型") or record.get("类型") or "")
        if not code or not report_date or not data_type:
            return None
        return self._build_fingerprint(code, report_date, data_type)

    @staticmethod
    def _record_capture_time(record: dict, fallback: str = "") -> str:
        return str(record.get("发现时间") or record.get("discovered_at") or fallback or "").strip()

    @staticmethod
    def _record_reveal_date(record: dict) -> str:
        return str(record.get("揭晓日") or record.get("公告日期") or record.get("源公告日期") or "").strip()

    @classmethod
    def _normalize_record_dates(cls, record: dict, fallback_capture_time: str = "") -> bool:
        capture_time = cls._record_capture_time(record, fallback_capture_time)
        reveal_date = cls._record_reveal_date(record)
        old_discovered_at = record.get("发现时间")
        old_reveal_date = record.get("揭晓日")
        if capture_time:
            record["发现时间"] = capture_time
        if reveal_date:
            record["揭晓日"] = reveal_date
        return old_discovered_at != record.get("发现时间") or old_reveal_date != record.get("揭晓日")

    def _prune_retryable_seen_fingerprints(self) -> bool:
        """
        清理可重试的旧预告指纹：
        - 当前活跃报告期内的“预告”指纹
        - 但本地有效记录里并没有对应落表结果
        这类指纹通常来自旧逻辑下的“缺记录/空值”等失败计算，不应永久阻断重试。
        """
        active_report_dates = set(current_active_report_dates())
        persisted_success = {fp for fp in (self._record_to_fingerprint(r) for r in self.local_records) if fp}

        cleaned = 0
        kept = set()
        for fp in self.seen_fingerprints:
            parts = fp.split("_", 3)
            if len(parts) != 4 or parts[0] != "SHOCK":
                kept.add(fp)
                continue

            _, code, report_date, data_type = parts
            if report_date in active_report_dates and fp not in persisted_success:
                cleaned += 1
                continue
            kept.add(fp)

        if cleaned:
            self.seen_fingerprints = kept
            logger.info(f"[业绩引擎] 清理 {cleaned} 条过期预告指纹")
            return True
        return False

    def _load_cache(self):
        """恢复全天候账本。清理超过 `keep_days` 天的老账"""
        from core.data_store import data_store

        data = data_store.load_earnings_state()
        state_updated_at = ""
        try:
            row = data_store.fetch_one("SELECT updated_at FROM kv_store WHERE key = ?", ("earnings_state",), default={})
            state_updated_at = str((row or {}).get("updated_at") or "").strip()
        except (AttributeError, RuntimeError, TypeError, ValueError):
            state_updated_at = ""

        # 向下兼容：首次启动如果 SQLite 无数据但旧 JSON 存在，自动迁入
        if not data and os.path.exists(self.cache_file):
            try:
                with open(self.cache_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                logger.info("[业绩引擎] 检测到旧 JSON 缓存，自动迁入 SQLite")
                data_store.save_earnings_state(
                    data.get("last_sync_date", ""),
                    data.get("seen", []),
                    data.get("records", []),
                )
                # 旧文件重命名为 .migrated 保留 30 天后由 DataStore 自动清理
                try:
                    migrated_path = self.cache_file + ".migrated"
                    os.rename(self.cache_file, migrated_path)
                    logger.info(f"[业绩引擎] 旧 JSON 已重命名为 {migrated_path}，30 天后自动清理")
                except OSError as _e:
                    logger.debug(f"[业绩引擎] 旧 JSON 缓存重命名失败: {_e}")
            except _EARNINGS_CACHE_ERRORS as e:
                logger.error(f"[业绩引擎] 旧 JSON 迁入失败: {e}")

        if data:
            self.last_sync_date = data.get("last_sync_date", self.last_sync_date)
            self.seen_fingerprints = set(data.get("seen", []))
            all_records = data.get("records", [])

            # 清理过期数据保障性能（只保留距离今天内 N 天的数据）
            valid_records = []
            cache_changed = False
            today_dt = MarketCalendar.now("CN")
            for r in all_records:
                # 强力清真过滤：剔除最新单季扣非利润为负或为 0，环比增速不足 30%，或同比为负的垃圾股
                if (
                    float(r.get("单季净利润_新增", 0.0)) <= 0
                    or float(r.get("环比增速_百分比", 0.0)) < EARNINGS_QOQ_MIN_PCT
                ):
                    continue
                # 同比必须为正（即去年同期对比必须是增长的），否则说明公司在走下坡路
                if float(r.get("同比增速_百分比", -1.0)) <= 0:
                    continue

                r_date = self._normalize_publish_date(self._record_reveal_date(r)) or r.get("公告日期", "")
                try:
                    r_dt = datetime.strptime(r_date, "%Y-%m-%d")
                    if (today_dt - r_dt).days <= self.keep_days:
                        cache_changed = self._normalize_record_dates(r, state_updated_at) or cache_changed
                        valid_records.append(r)
                except (ValueError, TypeError):
                    pass

            self.local_records = valid_records
            cache_changed = self._prune_retryable_seen_fingerprints() or cache_changed
            if cache_changed:
                self._save_cache()
            logger.info(
                f"[业绩引擎] 💾 已加载近 {self.keep_days} 天 {len(self.local_records)} 条记录，"
                f"上次同步: {self.last_sync_date}"
            )
        else:
            os.makedirs(os.path.dirname(self.cache_file), exist_ok=True)

    def _save_cache(self):
        """持久化所有追溯到的记录（写入 SQLite）"""
        try:
            from core.data_store import data_store

            data_store.save_earnings_state(
                self.last_sync_date,
                list(self.seen_fingerprints),
                self.local_records,
            )
        except (AttributeError, OSError, RuntimeError, TypeError, ValueError) as e:
            logger.error(f"[业绩引擎] SQLite 持久化失败: {e}")

    def _get_quick_report_cum_profit(self, target_code: str, report_date: str) -> float:
        """
        当正式财报尚未落到同花顺历史底稿时，尝试用同报告期的业绩快报净利润回填累计值。
        注意：快报口径只有“净利润-净利润”，并不提供扣非净利润字段。
        """
        cache = getattr(self, "_quick_report_profit_cache", None)
        if cache is None:
            cache = {}
            self._quick_report_profit_cache = cache

        if report_date not in cache:
            quick_profit_map = {}
            try:
                df_kb = safe_ak_fetch(ak.stock_yjkb_em, date=report_date)
            except _AKSHARE_FETCH_ERRORS as _e:
                logger.debug(f"[业绩引擎] 快报回填抓取失败({report_date}): {_e}")
                df_kb = pd.DataFrame()

            if not df_kb.empty and "股票代码" in df_kb.columns and "净利润-净利润" in df_kb.columns:
                df_work = df_kb.copy()
                if "公告日期" in df_work.columns:
                    df_work["公告日期"] = pd.to_datetime(df_work["公告日期"], errors="coerce")
                    df_work = df_work.sort_values(by="公告日期", ascending=True, na_position="first")
                for _, row in df_work.iterrows():
                    code = str(row.get("股票代码", "")).zfill(6)
                    if not code:
                        continue
                    profit = _parse_amount(row.get("净利润-净利润", np.nan))
                    if pd.notna(profit):
                        # 同一只股票若存在多次快报修订，保留最新一次公告的净利润。
                        quick_profit_map[code] = float(profit)
            cache[report_date] = quick_profit_map

        return cache[report_date].get(str(target_code).zfill(6), np.nan)

    def _inject_sectors(self, records: list) -> list:
        if not records:
            return records
        provider = getattr(self, "stock_context_provider", load_cached_ai_industry_chain_context_map)
        try:
            context_map = dict(provider() or {}) if callable(provider) else {}
        except (FileNotFoundError, ImportError, OSError, RuntimeError, TypeError, ValueError) as e:
            logger.error(f"[业绩引擎] AI产业链细分板块数据加载失败: {e}")
            context_map = {}

        for rec in records:
            code = self._record_stock_code(rec)
            rec["所属行业与概念"] = context_map.get(code, "--")
        return records

    def _resolve_stock_universe_codes(self) -> set[str] | None:
        provider = getattr(self, "stock_universe_provider", None)
        if not callable(provider):
            return None
        try:
            return {code for code in (normalize_ai_chain_code(value) for value in provider()) if code}
        except (FileNotFoundError, ImportError, OSError, RuntimeError, TypeError, ValueError) as exc:
            logger.warning(f"[业绩引擎] AI产业链股票池不可用，按空股票池处理: {exc}")
            return set()

    @staticmethod
    def _record_stock_code(record: dict) -> str:
        if not isinstance(record, dict):
            return ""
        return normalize_ai_chain_code(record.get("股票代码") or record.get("代码") or record.get("stock_code"))

    def _filter_records_to_stock_universe(self, records: list[dict]) -> list[dict]:
        allowed_codes = self._resolve_stock_universe_codes()
        if allowed_codes is None:
            return list(records or [])
        return [record for record in (records or []) if self._record_stock_code(record) in allowed_codes]

    def get_cached_record_rows(self) -> list[dict]:
        """Return cached rows without importing the dataframe stack."""
        records = self._filter_records_to_stock_universe(self.local_records)
        if not records:
            return []

        for record in records:
            self._normalize_record_dates(record, str(record.get("公告日期", "") or ""))
        self._inject_sectors(records)

        def _sort_key(record: dict) -> tuple[str, float]:
            try:
                qoq = float(record.get("环比增速_百分比", 0.0) or 0.0)
            except (TypeError, ValueError):
                qoq = 0.0
            return str(record.get("揭晓日", "") or ""), qoq

        return sorted((dict(record) for record in records), key=_sort_key, reverse=True)

    def get_cached_records(self) -> pd.DataFrame:
        """从长线账本中读取出所有还在存续期内的好股"""
        records = self.get_cached_record_rows()
        if records:
            return pd.DataFrame(records)
        return pd.DataFrame()

    @staticmethod
    def _resolve_guidance_est_profit(row) -> tuple[float, str]:
        est_profit = pd.to_numeric(row.get("预测数值", np.nan), errors="coerce")
        target_metric = str(row.get("预测指标", ""))

        # 向下兼容处理旧版接口逻辑
        if pd.isna(est_profit) and "预计净利润-下限" in row:
            v_min = pd.to_numeric(row.get("预计扣非净利润-下限", np.nan), errors="coerce")
            v_max = pd.to_numeric(row.get("预计扣非净利润-上限", np.nan), errors="coerce")
            if pd.notna(v_min) and pd.notna(v_max):
                est_profit = (v_min + v_max) / 2
            elif pd.notna(v_min):
                est_profit = v_min
            elif pd.notna(v_max):
                est_profit = v_max
            target_metric = "扣非"

            if pd.isna(est_profit):
                v_min = pd.to_numeric(row.get("预计净利润-下限", np.nan), errors="coerce")
                v_max = pd.to_numeric(row.get("预计净利润-上限", np.nan), errors="coerce")
                if pd.notna(v_min) and pd.notna(v_max):
                    est_profit = (v_min + v_max) / 2
                target_metric = "净利润"

        return est_profit, target_metric

    def _build_guidance_candidate(self, row, report_date: str, target_publish_date: str) -> dict | None:
        est_profit, target_metric = self._resolve_guidance_est_profit(row)
        if pd.isna(est_profit):
            return None

        is_koufei = "扣非" in target_metric or "扣除非经常性损益" in target_metric
        # 严格卡口：只要拿不到扣非数据就直接丢弃，不允许用归母净利润混进来
        if not is_koufei:
            return None

        source_publish_date = self._normalize_publish_date(row.get("公告日期")) or target_publish_date
        return {
            "股票代码": str(row["股票代码"]).zfill(6),
            "股票名称": row.get("股票简称", ""),
            "报告期": report_date,
            "数据类型": "预告",
            "基调": row.get("预告类型", ""),
            "累计期末利润估算_元": float(est_profit),
            "公告日期": target_publish_date,
            "源公告日期": source_publish_date,
            "is_koufei": is_koufei,
        }

    def _build_report_candidate(
        self,
        row,
        *,
        report_date: str,
        target_publish_date: str,
        data_type: str,
        date_col: str,
        tone: str,
    ) -> dict | None:
        est_profit = pd.to_numeric(row.get("净利润-净利润", np.nan), errors="coerce")
        if pd.isna(est_profit):
            return None

        source_publish_date = self._normalize_publish_date(row.get(date_col)) or target_publish_date
        return {
            "股票代码": str(row["股票代码"]).zfill(6),
            "股票名称": row.get("股票简称", ""),
            "报告期": report_date,
            "数据类型": data_type,
            "基调": tone,
            "累计期末利润估算_元": float(est_profit),
            "公告日期": target_publish_date,
            "源公告日期": source_publish_date,
            "is_koufei": False,
        }

    def _collect_guidance_candidates(self, report_date: str, target_publish_date: str) -> list[dict]:
        df_yg = safe_ak_fetch(ak.stock_yjyg_em, date=report_date)
        if df_yg.empty or "公告日期" not in df_yg.columns:
            return []

        df_target = self._filter_candidates_by_publish_date(
            df_yg,
            "公告日期",
            target_publish_date,
            "预告",
        )
        candidates = []
        for _, row in df_target.iterrows():
            candidate = self._build_guidance_candidate(row, report_date, target_publish_date)
            if candidate is not None:
                candidates.append(candidate)
        return candidates

    def _collect_report_candidates(
        self,
        report_date: str,
        target_publish_date: str,
        *,
        fetch_func,
        date_col: str,
        data_type: str,
        tone: str,
        max_fetch_elapsed_sec: float | None = None,
    ) -> list[dict]:
        df_report = safe_ak_fetch(fetch_func, date=report_date, max_elapsed_sec=max_fetch_elapsed_sec)
        if df_report.empty or date_col not in df_report.columns:
            return []

        df_target = self._filter_candidates_by_publish_date(
            df_report,
            date_col,
            target_publish_date,
            data_type,
        )
        candidates = []
        for _, row in df_target.iterrows():
            candidate = self._build_report_candidate(
                row,
                report_date=report_date,
                target_publish_date=target_publish_date,
                data_type=data_type,
                date_col=date_col,
                tone=tone,
            )
            if candidate is not None:
                candidates.append(candidate)
        return candidates

    def _collect_daily_surprise_candidates(
        self,
        report_dates: list[str],
        target_publish_date: str,
    ) -> tuple[list[dict], bool, list[dict[str, object]]]:
        all_candidates = []
        has_critical_error = False
        degradations: list[dict[str, object]] = []

        for report_date in report_dates:
            try:
                all_candidates.extend(self._collect_guidance_candidates(report_date, target_publish_date))
            except _AKSHARE_FETCH_ERRORS as e:
                logger.error(f"[业绩引擎] 业绩预告({report_date})拉取失败: {e}")
                has_critical_error = True

        formal_report_pool_degraded = False
        for report_date in report_dates:
            if formal_report_pool_degraded:
                has_critical_error = True
                logger.warning(
                    f"[业绩引擎] ⚠️ 【正式财报池】 ({report_date}) 本轮已降级，跳过并等待下次例行扫描重试"
                )
            else:
                try:
                    all_candidates.extend(
                        self._collect_report_candidates(
                            report_date,
                            target_publish_date,
                            fetch_func=ak.stock_yjbb_em,
                            date_col="最新公告日期",
                            data_type="财报",
                            tone="正式出炉",
                            max_fetch_elapsed_sec=EARNINGS_FORMAL_REPORT_RETRY_BUDGET_SECONDS,
                        )
                    )
                except EarningsUpstreamDegraded as e:
                    logger.warning(f"[业绩引擎] ⚠️ 财报({report_date})本轮降级: {e}")
                    degradations.append(e.to_dict())
                    has_critical_error = True
                    formal_report_pool_degraded = True
                except _AKSHARE_FETCH_ERRORS as e:
                    logger.error(f"[业绩引擎] 财报({report_date})拉取失败: {e}")
                    has_critical_error = True

            try:
                all_candidates.extend(
                    self._collect_report_candidates(
                        report_date,
                        target_publish_date,
                        fetch_func=ak.stock_yjkb_em,
                        date_col="公告日期",
                        data_type="快报",
                        tone="快报速递",
                    )
                )
            except _AKSHARE_FETCH_ERRORS as e:
                logger.error(f"[业绩引擎] 业绩快报({report_date})拉取失败: {e}")
                has_critical_error = True

        return all_candidates, has_critical_error, degradations

    def _pending_surprise_candidates(
        self,
        candidates: list[dict],
        stock_universe_codes: set[str] | None,
    ) -> list[dict]:
        pending_candidates = []
        for cand in candidates:
            code = cand["股票代码"]
            if not (code.startswith("0") or code.startswith("3") or code.startswith("6")):
                continue
            if stock_universe_codes is not None and code not in stock_universe_codes:
                continue
            fingerprint = self._build_fingerprint(code, cand["报告期"], cand["数据类型"])
            if fingerprint in self.seen_fingerprints:
                continue
            pending_candidates.append(cand)
        return pending_candidates

    def _check_surprise_candidate(self, cand: dict) -> tuple[dict, str, dict]:
        code = cand["股票代码"]
        report_date = cand["报告期"]
        data_type = cand["数据类型"]
        is_koufei = cand.pop("is_koufei", True)
        must_wait_ths = data_type in ["财报", "快报"]
        fingerprint = self._build_fingerprint(code, report_date, data_type)

        result = self.compute_single_quarter_qoq(
            code,
            cand["累计期末利润估算_元"],
            report_date,
            is_koufei,
            must_wait_ths,
        )
        return cand, fingerprint, result

    @staticmethod
    def _surprise_result_passes_threshold(result: dict) -> bool:
        yoy_pct = result.get("同比增速_百分比", -1)
        return (
            result.get("环比增速_百分比", -1) >= EARNINGS_QOQ_MIN_PCT
            and result.get("单季净利润_新增", -1) > 0
            and yoy_pct > 0
        )

    def _process_pending_surprise_candidates(self, pending_candidates: list[dict]) -> tuple[list[dict], bool]:
        valid_records = []
        new_found_flag = False
        total_pending = len(pending_candidates)
        if total_pending == 0:
            return valid_records, new_found_flag

        # 加入并发线程池（同花顺反爬较严，保守开 3 个线程刚刚好）
        import concurrent.futures

        processed_count = 0
        with concurrent.futures.ThreadPoolExecutor(max_workers=3) as executor:
            future_to_candidate = {
                executor.submit(self._check_surprise_candidate, candidate): candidate for candidate in pending_candidates
            }
            for future in concurrent.futures.as_completed(future_to_candidate):
                processed_count += 1
                failed_candidate = future_to_candidate[future]
                try:
                    cand, fingerprint, result = future.result()
                    code = cand["股票代码"]

                    # --- 节奏感极强的白话心跳 ---
                    if total_pending >= 50 and processed_count % 20 == 0:
                        logger.info(f"[业绩引擎] 验证进度 {processed_count}/{total_pending}")
                    elif 10 < total_pending < 50 and processed_count % 10 == 0:
                        logger.info(f"[业绩引擎] 验证进度 {processed_count}/{total_pending}")
                    elif 0 < total_pending <= 10:
                        logger.info(
                            f"[业绩引擎] 验证 {processed_count}/{total_pending}: {code} {cand.get('股票名称', '')}"
                        )

                    error_code = result.get("error")
                    if error_code in ["THS_PENDING", "抛锚"]:
                        continue

                    if error_code is not None:
                        continue

                    # 三重硬门槛：① 单季利润为正 ② 环比>=30% ③ 同比为正（扣非同比增长）
                    if self._surprise_result_passes_threshold(result):
                        cand.update(result)
                        capture_time = MarketCalendar.now("CN").isoformat(timespec="seconds")
                        cand["揭晓日"] = str(cand.get("公告日期") or cand.get("源公告日期") or "").strip()
                        cand["发现时间"] = capture_time
                        valid_records.append(cand)
                        self.local_records.append(cand)
                        self.seen_fingerprints.add(fingerprint)
                        new_found_flag = True
                except _EARNINGS_COMPUTE_ERRORS as _e:
                    logger.debug(f"[业绩引擎] {failed_candidate.get('股票代码', '?')} 并发计算异常: {_e}")

        return valid_records, new_found_flag

    def fetch_daily_surprises(self, target_publish_date: str = None) -> pd.DataFrame:
        if target_publish_date is None:
            target_publish_date = MarketCalendar.today("CN").strftime("%Y-%m-%d")

        started_at = MarketCalendar.now("CN").isoformat(timespec="seconds")
        self.last_scan_result = {
            "status": "running",
            "target_publish_date": target_publish_date,
            "started_at": started_at,
        }
        logger.info(f"[业绩引擎] 扫描目标日期: {target_publish_date}")

        sync_date_advanced = False
        should_advance_sync_date = False
        if target_publish_date > self.last_sync_date:
            should_advance_sync_date = True

        report_dates = current_active_report_dates()
        all_candidates, has_critical_error, degradations = self._collect_daily_surprise_candidates(
            report_dates,
            target_publish_date,
        )

        # 强制将携带真实扣非数值的记录排在前面，以防同日被互斥锁误杀
        all_candidates.sort(key=lambda x: not x["is_koufei"])

        stock_universe_codes = self._resolve_stock_universe_codes()

        # 初筛：把根本不用查水表的股票直接踢掉，算出真实的待审名单
        pending_candidates = self._pending_surprise_candidates(all_candidates, stock_universe_codes)

        total_pending = len(pending_candidates)
        if total_pending > 0:
            logger.info(f"[业绩引擎] 🔍 初筛完成，{total_pending} 只待深度验证")

        valid_records, new_found_flag = self._process_pending_surprise_candidates(pending_candidates)

        # 致命判断：本轮雷达扫描如果没有遭遇伤筋动骨的异常断连，才允许它推移游标。
        if should_advance_sync_date and not has_critical_error:
            self.last_sync_date = target_publish_date
            sync_date_advanced = True

        if new_found_flag or sync_date_advanced:
            self._save_cache()

        status = "degraded" if has_critical_error else "success"
        error_text = ""
        if degradations:
            error_text = "; ".join(
                f"{item.get('pool')}({item.get('report_date')}): {item.get('error')}" for item in degradations
            )
        elif has_critical_error:
            error_text = "provider_fetch_failed"
        self.last_scan_result = {
            "status": status,
            "target_publish_date": target_publish_date,
            "started_at": started_at,
            "finished_at": MarketCalendar.now("CN").isoformat(timespec="seconds"),
            "records": int(len(valid_records)),
            "degradations": degradations,
            "error": error_text,
        }

        if valid_records:
            # === 补齐 AI 产业链细分板块与备注 ===
            self._inject_sectors(valid_records)

            return pd.DataFrame(valid_records).sort_values(by=["揭晓日", "环比增速_百分比"], ascending=[False, False])
        return pd.DataFrame()

    def compute_single_quarter_qoq(
        self,
        target_code: str,
        target_est_cum_profit: float,
        report_date: str,
        is_koufei: bool = True,
        must_wait_ths: bool = False,
    ) -> dict:
        try:
            df_fin = safe_ak_fetch(ak.stock_financial_benefit_ths, symbol=target_code)
            if df_fin.empty:
                return {"error": "无历史"}
            df_fin["报告期"] = pd.to_datetime(df_fin["报告期"])
            df_fin = df_fin.sort_values(by="报告期", ascending=False)

            # --- 核心拦截：如果强制要求纯粹的扣非财报，抛弃之前传进来的虚假预估值，直接从底层提 ---
            if must_wait_ths:
                cols = [c for c in df_fin.columns if "扣除" in c]
                if not cols:
                    return {"error": "无找点字段"}
                match_current = df_fin[df_fin["报告期"] == pd.to_datetime(report_date)]
                if match_current.empty:
                    return {"error": "THS_PENDING"}
                real_val = match_current.iloc[0][cols[0]]
                if pd.isna(real_val):
                    return {"error": "THS_PENDING"}

                target_est_cum_profit = _parse_amount(real_val)
                if pd.isna(target_est_cum_profit):
                    return {"error": "THS_PENDING"}
                is_koufei = True

            cols = _select_profit_columns(df_fin.columns, is_koufei)
            if not cols:
                return {"error": "无利润字段"}
            kf_col = cols[0]

            df_fin["累计扣非_元"] = df_fin[kf_col].apply(_parse_amount)

            r_datetime = pd.to_datetime(report_date)
            year, month = r_datetime.year, r_datetime.month

            # 【性能优化】将 dataframe 的查找时间复杂度从 O(N) 降维到 O(1) 的 Hash 查找
            df_fin.set_index("报告期", inplace=True)

            def get_cum_profit(target_date):
                td = pd.to_datetime(target_date)
                if td in df_fin.index:
                    return df_fin.at[td, "累计扣非_元"]
                return np.nan

            def get_cum_profit_with_quick(target_date, basis_desc):
                value = get_cum_profit(target_date)
                if pd.notna(value):
                    return value, False

                quick_report_period = pd.to_datetime(target_date).strftime("%Y%m%d")
                quick_report_cum = self._get_quick_report_cum_profit(target_code, quick_report_period)
                if pd.notna(quick_report_cum):
                    logger.info(
                        f"[业绩引擎] {target_code} 缺 {target_date} 财报，"
                        f"回退用快报 {quick_report_period} 估算{basis_desc}"
                    )
                    return quick_report_cum, True
                return np.nan, False

            metrics, metrics_error = _compute_single_quarter_metrics(
                year,
                month,
                target_est_cum_profit,
                get_cum_profit_with_quick,
            )
            if metrics_error is not None:
                return {"error": metrics_error}
            current_single = metrics.current_single
            last_single = metrics.last_single
            yoy_base_single = metrics.yoy_base_single
            last_single_basis = metrics.last_single_basis

            if pd.isna(current_single) or pd.isna(last_single):
                return {"error": "空值"}
            if last_single == 0:
                return {"error": "基数0"}

            qoq = (current_single - last_single) / abs(last_single) * 100

            # 计算单季度同比增速：当季扣非 vs 去年同季度扣非
            yoy = np.nan
            if pd.notna(yoy_base_single) and yoy_base_single != 0:
                yoy = (current_single - yoy_base_single) / abs(yoy_base_single) * 100

            result = {
                "单季净利润_新增": current_single,
                "单季净利润_上期": last_single,
                "单季净利润_去年同期": yoy_base_single if pd.notna(yoy_base_single) else 0.0,
                "环比增速_百分比": round(qoq, 2),
                "同比增速_百分比": round(yoy, 2) if pd.notna(yoy) else 0.0,
                "error": None,
            }
            if last_single_basis != "财报":
                result["上季基数口径"] = last_single_basis
            return result
        except _EARNINGS_COMPUTE_ERRORS as e:
            logger.error(f"[业绩预告] 获取失败: {e}")
            return {"error": "抛锚"}
