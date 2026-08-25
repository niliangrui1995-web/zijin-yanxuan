from __future__ import annotations

import importlib
import json
import os
import time
from collections.abc import Mapping
from datetime import datetime, timedelta
from functools import lru_cache
from pathlib import Path
from threading import Lock, RLock

import requests

from core.logger import get_logger
from core.runtime_paths import PROJECT_ROOT

# Metric helpers are intentionally re-exported from this legacy module.
from domains.earnings.metrics import (  # noqa: F401
    _SINGLE_QUARTER_METRIC_RESOLVERS,
    _basis_from_quick_flags,
    _compute_single_quarter_metrics,
    _cumulative_single_quarter_metrics,
    _parse_amount,
    _ProfitGetter,
    _q1_single_quarter_metrics,
    _q2_single_quarter_metrics,
    _q3_single_quarter_metrics,
    _q4_single_quarter_metrics,
    _select_profit_columns,
    _SingleQuarterMetricResult,
    _SingleQuarterMetrics,
)
from domains.industry_chain.pool_service import normalize_ai_chain_code
from domains.market_calendar import MarketCalendar
from infra.http_safety import requests_get_https
from infra.tasks.lifecycle import raise_if_cancelled as _raise_if_cancelled

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


ak = _LazyModule("akshare")
np = _LazyModule("numpy")
pd = _LazyModule("pandas")

EARNINGS_QOQ_MIN_PCT = 30.0
EARNINGS_FORMAL_REPORT_RETRY_BUDGET_SECONDS = 60.0
AI_CHAIN_BSE_EARNINGS_ENABLED_CODES = frozenset({"920045"})
_EASTMONEY_BSE_QUICK_REPORT_URL = "https://datacenter.eastmoney.com/securities/api/data/v1/get"
_EASTMONEY_BSE_QUICK_REPORT_ALLOWED_HOSTS = frozenset({"datacenter.eastmoney.com"})
_THS_FINANCIAL_BENEFIT_ALLOWED_HOSTS = frozenset({"basic.10jqka.com.cn"})
_EASTMONEY_BSE_QUICK_REPORT_TIMEOUT = (5, 15)
_BSE_QUICK_REPORT_COLUMNS = ["股票代码", "股票简称", "公告日期", "净利润-净利润"]

_POOL_CACHE = {}
_THS_FINANCIAL_BENEFIT_CACHE = {}
_EARNINGS_CACHE_LOCK = RLock()
_THS_FINANCIAL_BENEFIT_CACHE_TTL_SEC = 30 * 60
_THS_FINANCIAL_BENEFIT_FALLBACK_TTL_SEC = 6 * 60 * 60
_THS_NO_LAST_SUCCESS_BASIS = "N/A（本轮未取得同花顺历史底稿，未使用替代来源）"
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
_BSE_QUICK_REPORT_FETCH_ERRORS = _AKSHARE_FETCH_ERRORS + (json.JSONDecodeError, requests.RequestException)
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


def resolve_legacy_earnings_cache_path(cache_file: str | os.PathLike[str]) -> str:
    path = Path(cache_file).expanduser()
    if not path.is_absolute():
        path = Path(PROJECT_ROOT) / path
    return str(path.resolve())


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


def _fetch_enabled_ai_chain_bse_quick_report_rows(report_date: str, stock_codes: set[str]) -> pd.DataFrame:
    """Fetch the allowlisted AI-chain BSE quick-report rows outside AkShare's BSE exclusion."""
    enabled_codes = sorted(
        {
            normalize_ai_chain_code(code)
            for code in stock_codes or set()
            if normalize_ai_chain_code(code) in AI_CHAIN_BSE_EARNINGS_ENABLED_CODES
        }
    )
    if not enabled_codes:
        return pd.DataFrame(columns=_BSE_QUICK_REPORT_COLUMNS)

    report_date_text = str(report_date or "").strip()
    if len(report_date_text) != 8 or not report_date_text.isdigit():
        raise ValueError(f"业绩快报报告期格式错误: {report_date!r}")
    report_date_ymd = f"{report_date_text[:4]}-{report_date_text[4:6]}-{report_date_text[6:]}"

    rows = []
    for code in enabled_codes:
        params = {
            "sortColumns": "UPDATE_DATE,SECURITY_CODE",
            "sortTypes": "-1,-1",
            "pageSize": "10",
            "pageNumber": "1",
            "reportName": "RPT_FCI_PERFORMANCEE",
            "columns": "ALL",
            "filter": (
                f'(SECURITY_CODE="{code}")(TRADE_MARKET_CODE="069001017")'
                f"(REPORT_DATE='{report_date_ymd}')"
            ),
        }
        response = requests_get_https(
            _EASTMONEY_BSE_QUICK_REPORT_URL,
            params=params,
            timeout=_EASTMONEY_BSE_QUICK_REPORT_TIMEOUT,
            allowed_hosts=_EASTMONEY_BSE_QUICK_REPORT_ALLOWED_HOSTS,
            allow_reserved_tun_for_allowed_hosts=True,
        )
        if int(getattr(response, "status_code", 0) or 0) != 200:
            raise RuntimeError(f"东财北交所业绩快报 HTTP 状态异常: {getattr(response, 'status_code', None)}")
        payload = response.json()
        if (
            isinstance(payload, Mapping)
            and payload.get("success") is False
            and str(payload.get("code") or "").strip() == "9201"
        ):
            continue
        result = payload.get("result") if isinstance(payload, Mapping) else None
        source_rows = result.get("data") if isinstance(result, Mapping) else None
        if not isinstance(source_rows, list):
            raise ValueError("东财北交所业绩快报返回结构异常")

        for source_row in source_rows:
            if not isinstance(source_row, Mapping):
                continue
            row_code = normalize_ai_chain_code(source_row.get("SECURITY_CODE"))
            if row_code != code:
                continue
            if str(source_row.get("TRADE_MARKET_CODE") or "").strip() != "069001017":
                continue
            if str(source_row.get("REPORT_DATE") or "").strip()[:10] != report_date_ymd:
                continue
            rows.append(
                {
                    "股票代码": row_code,
                    "股票简称": str(source_row.get("SECURITY_NAME_ABBR") or "").strip(),
                    "公告日期": source_row.get("NOTICE_DATE") or source_row.get("UPDATE_DATE"),
                    "净利润-净利润": source_row.get("PARENT_NETPROFIT"),
                }
            )

    return pd.DataFrame(rows, columns=_BSE_QUICK_REPORT_COLUMNS)


def _merge_quick_report_profit_rows(quick_profit_map: dict[str, float], df_quick_report: pd.DataFrame) -> None:
    if (
        df_quick_report.empty
        or "股票代码" not in df_quick_report.columns
        or "净利润-净利润" not in df_quick_report.columns
    ):
        return

    df_work = df_quick_report.copy()
    if "公告日期" in df_work.columns:
        df_work["公告日期"] = pd.to_datetime(df_work["公告日期"], errors="coerce")
        df_work = df_work.sort_values(by="公告日期", ascending=True, na_position="first")
    for _, row in df_work.iterrows():
        code = normalize_ai_chain_code(row.get("股票代码"))
        if not code:
            continue
        profit = _parse_amount(row.get("净利润-净利润", np.nan))
        if pd.notna(profit):
            # 同一只股票若存在多次快报修订，保留最新一次公告的净利润。
            quick_profit_map[code] = float(profit)


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
    with _EARNINGS_CACHE_LOCK:
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
    cached_df = df.copy()
    with _EARNINGS_CACHE_LOCK:
        if (
            cache_key not in _THS_FINANCIAL_BENEFIT_CACHE
            and len(_THS_FINANCIAL_BENEFIT_CACHE) >= _THS_FINANCIAL_BENEFIT_CACHE_MAX_ENTRIES
        ):
            excess = len(_THS_FINANCIAL_BENEFIT_CACHE) - _THS_FINANCIAL_BENEFIT_CACHE_MAX_ENTRIES + 1
            for old_key, _old_value in sorted(_THS_FINANCIAL_BENEFIT_CACHE.items(), key=lambda item: item[1][0])[:excess]:
                _THS_FINANCIAL_BENEFIT_CACHE.pop(old_key, None)
        _THS_FINANCIAL_BENEFIT_CACHE[cache_key] = (time.time(), cached_df)
    return cached_df.copy()


def _build_ths_source_gap(symbol: str, error: object, *, last_success_basis: str) -> dict[str, object]:
    return {
        "source": "同花顺历史底稿",
        "symbol": str(symbol or "").strip().zfill(6),
        "retryable": True,
        "last_success_basis": str(last_success_basis or _THS_NO_LAST_SUCCESS_BASIS).strip(),
        "error": str(error or "unknown_error").strip() or "unknown_error",
    }


def _ths_source_gap_from_frame(frame) -> dict[str, object] | None:
    attrs = getattr(frame, "attrs", {})
    raw_gap = attrs.get("earnings_source_gap") if isinstance(attrs, Mapping) else None
    return dict(raw_gap) if isinstance(raw_gap, Mapping) else None


def _with_ths_source_gap(result: dict, source_gap: dict[str, object] | None) -> dict:
    if source_gap:
        result["source_gap"] = dict(source_gap)
    return result


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
        response = requests_get_https(
            url,
            headers=headers,
            timeout=_THS_REQUEST_TIMEOUT,
            allowed_hosts=_THS_FINANCIAL_BENEFIT_ALLOWED_HOSTS,
            allow_reserved_tun_for_allowed_hosts=True,
        )
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


def _ak_fetch_pool_label(function_name: str) -> str:
    for marker, label in (
        ("yjyg", "【业绩预告池】"),
        ("yjbb", "【正式财报池】"),
        ("yjkb", "【业绩快报池】"),
        ("financial_benefit", "【同花顺历史底稿】"),
    ):
        if marker in function_name:
            return label
    return "未知金矿"


def safe_ak_fetch(fetch_func, *args, max_elapsed_sec: float | None = None, **kwargs):
    """带退避的强力护甲 + 大白话进度解说"""
    retries = 3
    delay = 2.0
    started_at = time.monotonic()

    # 翻译文言文函数名
    fname = fetch_func.__name__
    func_cn = _ak_fetch_pool_label(fname)
    is_ths_financial = "financial_benefit" in fname

    # 提取报备日期供打印
    param_str = kwargs.get("date", kwargs.get("symbol", "全局获取"))

    # ==== 极速内存缓存过滤（仅针对大池子，同花顺个股不管） ====
    if "financial_benefit" not in fname:
        cache_key = f"{fname}_{param_str}"
        with _EARNINGS_CACHE_LOCK:
            cached = _POOL_CACHE.get(cache_key)
            if cached is not None:
                cached_time, cached_df = cached
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
                logger.info(f"[业绩引擎] {func_cn} ({param_str}) 拉取完成")
                with _EARNINGS_CACHE_LOCK:
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
                        f"[业绩引擎] {func_cn} ({param_str}) 已耗时 {elapsed_sec:.0f}s，"
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
                            f"[业绩引擎] {func_cn} ({param_str}) 连续失败，回退使用 {int(age_sec)}s 前缓存: {e}"
                        )
                        stale_df.attrs["earnings_source_gap"] = _build_ths_source_gap(
                            kwargs.get("symbol") or param_str,
                            e,
                            last_success_basis=f"同花顺历史底稿内存缓存（{max(0, int(age_sec))}秒前成功）",
                        )
                        return stale_df
                logger.error(f"[业绩引擎] {func_cn} ({param_str}) 重试 {retries} 次后仍失败: {e}")
                raise e

            if max_elapsed_sec is not None and elapsed_sec + delay >= float(max_elapsed_sec):
                if not is_ths_financial:
                    logger.warning(
                        f"[业绩引擎] {func_cn} ({param_str}) 本轮重试预算不足，"
                        "停止本池重试并保留后续自动重试机会"
                    )
                raise EarningsUpstreamDegraded(func_cn, param_str, elapsed_sec, e) from e

            logger.warning(f"[业绩引擎] {func_cn} 请求失败({e})，{delay:.0f}s 后第 {i + 2} 次重试")
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


def _call_engine_stage(engine, method_name: str, cancellation_token, *args, **kwargs):
    _raise_if_cancelled(cancellation_token)
    method = getattr(engine, method_name)
    if cancellation_token is not None:
        kwargs["cancellation_token"] = cancellation_token
    result = method(*args, **kwargs)
    _raise_if_cancelled(cancellation_token)
    return result


def _collect_guidance_pools(engine, report_dates, target_date, cancellation_token=None):
    rows = []
    critical = False
    for report_date in report_dates:
        try:
            rows.extend(
                _call_engine_stage(
                    engine,
                    "_collect_guidance_candidates",
                    cancellation_token,
                    report_date,
                    target_date,
                )
            )
        except _AKSHARE_FETCH_ERRORS as exc:
            _raise_if_cancelled(cancellation_token)
            logger.error(f"[业绩引擎] 业绩预告({report_date})拉取失败: {exc}")
            critical = True
    return rows, critical


def _collect_formal_pool(engine, report_date, target_date, degraded, cancellation_token=None):
    if degraded:
        logger.warning(f"[业绩引擎] 【正式财报池】 ({report_date}) 本轮已降级，跳过并等待下次例行扫描重试")
        return [], True, [], True
    try:
        rows = _call_engine_stage(
            engine,
            "_collect_report_candidates",
            cancellation_token,
            report_date,
            target_date,
            fetch_func=ak.stock_yjbb_em,
            date_col="最新公告日期",
            data_type="财报",
            tone="正式出炉",
            max_fetch_elapsed_sec=EARNINGS_FORMAL_REPORT_RETRY_BUDGET_SECONDS,
        )
        return rows, False, [], False
    except EarningsUpstreamDegraded as exc:
        logger.warning(f"[业绩引擎] 财报({report_date})本轮降级: {exc}")
        return [], True, [exc.to_dict()], True
    except _AKSHARE_FETCH_ERRORS as exc:
        _raise_if_cancelled(cancellation_token)
        logger.error(f"[业绩引擎] 财报({report_date})拉取失败: {exc}")
        return [], True, [], False


def _collect_quick_pool(engine, report_date, target_date, cancellation_token=None):
    rows = []
    critical = False
    try:
        rows.extend(
            _call_engine_stage(
                engine,
                "_collect_report_candidates",
                cancellation_token,
                report_date,
                target_date,
                fetch_func=ak.stock_yjkb_em,
                date_col="公告日期",
                data_type="快报",
                tone="快报速递",
            )
        )
    except _AKSHARE_FETCH_ERRORS as exc:
        _raise_if_cancelled(cancellation_token)
        logger.error(f"[业绩引擎] 业绩快报({report_date})拉取失败: {exc}")
        critical = True

    try:
        rows.extend(
            _call_engine_stage(
                engine,
                "_collect_enabled_ai_chain_bse_quick_report_candidates",
                cancellation_token,
                report_date,
                target_date,
            )
        )
    except _BSE_QUICK_REPORT_FETCH_ERRORS as exc:
        _raise_if_cancelled(cancellation_token)
        logger.error(f"[业绩引擎] 北交所业绩快报补充({report_date})拉取失败: {exc}")
        critical = True
    return rows, critical


def _collect_daily_candidates_pipeline(engine, report_dates, target_date, cancellation_token=None):
    rows, critical = _collect_guidance_pools(engine, report_dates, target_date, cancellation_token)
    degradations = []
    formal_degraded = False
    for report_date in report_dates:
        formal_rows, formal_error, new_degradations, formal_degraded = _collect_formal_pool(
            engine,
            report_date,
            target_date,
            formal_degraded,
            cancellation_token,
        )
        quick_rows, quick_error = _collect_quick_pool(engine, report_date, target_date, cancellation_token)
        rows.extend(formal_rows)
        rows.extend(quick_rows)
        degradations.extend(new_degradations)
        critical = critical or formal_error or quick_error
    return rows, critical, degradations


def _submit_candidate_futures(engine, executor, candidates, cancellation_token=None):
    futures = {}
    for candidate in candidates:
        _raise_if_cancelled(cancellation_token)
        kwargs = {"cancellation_token": cancellation_token} if cancellation_token is not None else {}
        future = executor.submit(engine._check_surprise_candidate, candidate, **kwargs)
        futures[future] = candidate
    return futures


def _log_candidate_progress(cand, processed_count: int, total_pending: int, *, source_gap: dict[str, object] | None = None) -> None:
    code = cand["股票代码"]
    source_gap_suffix = ""
    if source_gap is not None:
        source_gap_suffix = "（同花顺历史底稿数据源缺口，可重试）"
    if (total_pending >= 50 and processed_count % 20 == 0) or (10 < total_pending < 50 and processed_count % 10 == 0):
        logger.info(f"[业绩引擎] 验证进度 {processed_count}/{total_pending}{source_gap_suffix}")
    elif 0 < total_pending <= 10:
        logger.info(f"[业绩引擎] 验证 {processed_count}/{total_pending}: {code} {cand.get('股票名称', '')}{source_gap_suffix}")


def _candidate_source_gap(candidate: dict, result: dict) -> dict[str, object] | None:
    raw_gap = result.get("source_gap")
    if not isinstance(raw_gap, Mapping):
        return None
    symbol = str(candidate.get("股票代码") or raw_gap.get("symbol") or "").strip().zfill(6)
    return {
        "source": str(raw_gap.get("source") or "同花顺历史底稿").strip(),
        "symbol": symbol,
        "stock_name": str(candidate.get("股票名称") or "").strip(),
        "report_date": str(candidate.get("报告期") or "").strip(),
        "data_type": str(candidate.get("数据类型") or "").strip(),
        "retryable": bool(raw_gap.get("retryable")),
        "last_success_basis": str(raw_gap.get("last_success_basis") or _THS_NO_LAST_SUCCESS_BASIS).strip(),
        "error": str(raw_gap.get("error") or result.get("error") or "unknown_error").strip(),
    }


def _validated_candidate(engine, future, failed_candidate, processed_count, total_pending, cancellation_token=None):
    try:
        cand, fingerprint, result = future.result()
        _raise_if_cancelled(cancellation_token)
        source_gap = _candidate_source_gap(cand, result)
        _log_candidate_progress(cand, processed_count, total_pending, source_gap=source_gap)
        if result.get("error") is not None or not engine._surprise_result_passes_threshold(result):
            return None, source_gap
        cand.update(result)
        cand["揭晓日"] = str(cand.get("公告日期") or cand.get("源公告日期") or "").strip()
        cand["发现时间"] = MarketCalendar.now("CN").isoformat(timespec="seconds")
        return (cand, fingerprint), source_gap
    except _EARNINGS_COMPUTE_ERRORS as exc:
        _raise_if_cancelled(cancellation_token)
        logger.debug(f"[业绩引擎] {failed_candidate.get('股票代码', '?')} 并发计算异常: {exc}")
        return None, None


def _process_pending_candidates_pipeline(engine, candidates, cancellation_token=None):
    if not candidates:
        return [], False, []
    import concurrent.futures

    valid_records = []
    fingerprints = []
    source_gaps = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=3) as executor:
        future_map = _submit_candidate_futures(engine, executor, candidates, cancellation_token)
        for processed_count, future in enumerate(concurrent.futures.as_completed(future_map), start=1):
            _raise_if_cancelled(cancellation_token)
            validated, source_gap = _validated_candidate(
                engine,
                future,
                future_map[future],
                processed_count,
                len(candidates),
                cancellation_token,
            )
            if source_gap is not None:
                source_gaps.append(source_gap)
            if validated is not None:
                record, fingerprint = validated
                valid_records.append(record)
                fingerprints.append(fingerprint)
    _raise_if_cancelled(cancellation_token)
    engine.local_records.extend(valid_records)
    engine.seen_fingerprints.update(fingerprints)
    return valid_records, bool(valid_records), source_gaps


def _prepare_daily_scan(engine, target_date, cancellation_token=None, stock_codes: set[str] | None = None):
    report_dates = current_active_report_dates()
    candidates, critical, degradations = engine._collect_daily_surprise_candidates(
        report_dates,
        target_date,
        cancellation_token=cancellation_token,
    )
    candidates.sort(key=lambda item: not item["is_koufei"])
    universe_codes = engine._resolve_stock_universe_codes(cancellation_token=cancellation_token)
    if stock_codes is not None:
        requested_codes = {
            code
            for raw_code in stock_codes
            if (code := normalize_ai_chain_code(raw_code))
        }
        universe_codes = requested_codes if universe_codes is None else universe_codes & requested_codes
    pending = engine._pending_surprise_candidates(
        candidates,
        universe_codes,
        cancellation_token=cancellation_token,
    )
    if pending:
        logger.info(f"[业绩引擎] 初筛完成，{len(pending)} 只待深度验证")
    return pending, critical, degradations


def _commit_daily_scan(engine, target_date, valid_records, new_found, critical, cancellation_token=None):
    _raise_if_cancelled(cancellation_token)
    if valid_records:
        if cancellation_token is None:
            engine._inject_sectors(valid_records)
        else:
            engine._inject_sectors(valid_records, cancellation_token=cancellation_token)
    _raise_if_cancelled(cancellation_token)
    sync_advanced = target_date > engine.last_sync_date and not critical
    if sync_advanced:
        engine.last_sync_date = target_date
    # 每轮结束都落盘扫描状态：失败不能只留在内存中，否则重启后会被误判为正常完成。
    engine._save_cache()
    return sync_advanced


def _source_gap_error_summary(source_gaps: list[dict[str, object]]) -> str:
    if not source_gaps:
        return ""
    ordered_gaps = sorted(source_gaps, key=lambda item: (str(item.get("symbol") or ""), str(item.get("report_date") or "")))
    identities = "、".join(
        " ".join(part for part in (str(item.get("symbol") or "").strip(), str(item.get("stock_name") or "").strip()) if part)
        or "未知股票"
        for item in ordered_gaps
    )
    bases = "；".join(
        f"{str(item.get('symbol') or '未知股票').strip()}={str(item.get('last_success_basis') or _THS_NO_LAST_SUCCESS_BASIS).strip()}"
        for item in ordered_gaps
    )
    return f"同花顺历史底稿数据源缺口：{identities}；可重试；最后成功依据：{bases}"


def _finish_daily_scan_state(engine, target_date, started_at, valid_records, critical, degradations, source_gaps) -> None:
    source_gaps = sorted(
        (dict(item) for item in source_gaps if isinstance(item, Mapping)),
        key=lambda item: (str(item.get("symbol") or ""), str(item.get("report_date") or "")),
    )
    if degradations:
        error_parts = [
            f"{item.get('pool')}({item.get('report_date')}): {item.get('error')}" for item in degradations
        ]
    else:
        error_parts = []
    source_gap_summary = _source_gap_error_summary(source_gaps)
    if source_gap_summary:
        error_parts.append(source_gap_summary)
    error_text = "; ".join(error_parts) or ("provider_fetch_failed" if critical else "")
    engine.last_scan_result = {
        "status": "degraded" if critical else "success",
        "target_publish_date": target_date,
        "started_at": started_at,
        "finished_at": MarketCalendar.now("CN").isoformat(timespec="seconds"),
        "records": int(len(valid_records)),
        "degradations": degradations,
        "source_gaps": source_gaps,
        "source_gap_count": int(len(source_gaps)),
        "retryable": bool(critical),
        "error": error_text,
    }


def _fetch_daily_surprises_pipeline(
    engine,
    target_publish_date=None,
    cancellation_token=None,
    stock_codes: set[str] | None = None,
):
    _raise_if_cancelled(cancellation_token)
    target_date = target_publish_date or MarketCalendar.today("CN").strftime("%Y-%m-%d")
    started_at = MarketCalendar.now("CN").isoformat(timespec="seconds")
    engine.last_scan_result = {"status": "running", "target_publish_date": target_date, "started_at": started_at}
    logger.info(f"[业绩引擎] 扫描目标日期: {target_date}")
    pending, critical, degradations = _prepare_daily_scan(
        engine,
        target_date,
        cancellation_token,
        stock_codes=stock_codes,
    )
    valid_records, new_found, source_gaps = engine._process_pending_surprise_candidates(
        pending,
        cancellation_token=cancellation_token,
    )
    critical = critical or bool(source_gaps)
    _finish_daily_scan_state(engine, target_date, started_at, valid_records, critical, degradations, source_gaps)
    _commit_daily_scan(engine, target_date, valid_records, new_found, critical, cancellation_token)
    if valid_records:
        return pd.DataFrame(valid_records).sort_values(
            by=["揭晓日", "环比增速_百分比"],
            ascending=[False, False],
        )
    return pd.DataFrame()


class EarningsEngine:
    def __init__(
        self,
        cache_file="data/earnings_state.json",
        keep_days=30,
        stock_universe_provider=None,
        stock_context_provider=None,
    ):
        self.cache_file = resolve_legacy_earnings_cache_path(cache_file)
        self.keep_days = keep_days
        self.stock_universe_provider = stock_universe_provider
        self.stock_context_provider = stock_context_provider
        self.seen_fingerprints = set()
        self.local_records = []
        self.last_sync_date = MarketCalendar.today("CN").strftime("%Y-%m-%d")
        self.last_scan_result: dict[str, object] = {}
        self.ai_chain_bse_backfilled_codes: set[str] = set()
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
    def _get_next_trade_date(cls, target_date: datetime.date) -> datetime.date:
        cursor = target_date + timedelta(days=1)
        for _ in range(40):
            if cursor.weekday() >= 5:
                cursor += timedelta(days=1)
                continue
            # 若日历已就绪，精准校验是否为节假日；若未就绪，普通工作日即为交易日
            try:
                trade_dates = getattr(MarketCalendar, "_trade_dates", None)
                if trade_dates:
                    if cursor.isoformat() in trade_dates:
                        return cursor
                else:
                    return cursor
            except (AttributeError, TypeError):
                return cursor
            cursor += timedelta(days=1)
        return target_date + timedelta(days=1)

    @classmethod
    def _resolve_allowed_publish_dates(cls, target_publish_date: str, data_type: str) -> set[str]:
        allowed_dates = {target_publish_date}
        if data_type not in {"预告", "财报", "快报"}:
            return allowed_dates

        try:
            cur_dt = datetime.strptime(target_publish_date, "%Y-%m-%d").date()
        except (TypeError, ValueError):
            return allowed_dates

        next_trade_dt = cls._get_next_trade_date(cur_dt)
        step = cur_dt
        while step <= next_trade_dt:
            allowed_dates.add(step.strftime("%Y-%m-%d"))
            step += timedelta(days=1)

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
        from infra.storage.data_store import data_store

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
                    ai_chain_bse_backfilled_codes=data.get("ai_chain_bse_backfilled_codes", []),
                    last_scan_result=data.get("last_scan_result", {}),
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
            raw_last_scan_result = data.get("last_scan_result")
            self.last_scan_result = dict(raw_last_scan_result) if isinstance(raw_last_scan_result, Mapping) else {}
            raw_backfilled_codes = data.get("ai_chain_bse_backfilled_codes", [])
            if isinstance(raw_backfilled_codes, (list, tuple, set)):
                self.ai_chain_bse_backfilled_codes = {
                    code
                    for raw_code in raw_backfilled_codes
                    if (code := normalize_ai_chain_code(raw_code)) in AI_CHAIN_BSE_EARNINGS_ENABLED_CODES
                }
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
                f"[业绩引擎] 已加载近 {self.keep_days} 天 {len(self.local_records)} 条记录，"
                f"上次同步: {self.last_sync_date}"
            )
        else:
            os.makedirs(os.path.dirname(self.cache_file), exist_ok=True)

    def _save_cache(self):
        """持久化所有追溯到的记录（写入 SQLite）"""
        try:
            from infra.storage.data_store import data_store

            data_store.save_earnings_state(
                self.last_sync_date,
                list(self.seen_fingerprints),
                self.local_records,
                ai_chain_bse_backfilled_codes=sorted(self.ai_chain_bse_backfilled_codes),
                last_scan_result=getattr(self, "last_scan_result", {}),
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
            _merge_quick_report_profit_rows(quick_profit_map, df_kb)
            cache[report_date] = quick_profit_map

        normalized_target_code = normalize_ai_chain_code(target_code)
        enabled_codes = self._enabled_ai_chain_bse_quick_report_codes()
        if normalized_target_code in enabled_codes and normalized_target_code not in cache[report_date]:
            try:
                df_bse = _fetch_enabled_ai_chain_bse_quick_report_rows(report_date, {normalized_target_code})
            except _BSE_QUICK_REPORT_FETCH_ERRORS as exc:
                logger.debug(f"[业绩引擎] 北交所快报回填抓取失败({report_date}): {exc}")
            else:
                _merge_quick_report_profit_rows(cache[report_date], df_bse)

        return cache[report_date].get(normalized_target_code, np.nan)

    def _inject_sectors(self, records: list, *, cancellation_token=None) -> list:
        _raise_if_cancelled(cancellation_token)
        if not records:
            return records
        provider = getattr(self, "stock_context_provider", None)
        try:
            context_map = dict(provider() or {}) if callable(provider) else {}
        except (FileNotFoundError, ImportError, OSError, RuntimeError, TypeError, ValueError) as e:
            logger.error(f"[业绩引擎] AI产业链细分板块数据加载失败: {e}")
            context_map = {}

        _raise_if_cancelled(cancellation_token)
        for rec in records:
            _raise_if_cancelled(cancellation_token)
            code = self._record_stock_code(rec)
            rec["所属行业与概念"] = context_map.get(code, "--")
        return records

    def _resolve_stock_universe_codes(self, *, cancellation_token=None) -> set[str] | None:
        _raise_if_cancelled(cancellation_token)
        provider = getattr(self, "stock_universe_provider", None)
        if not callable(provider):
            return None
        try:
            values = provider()
        except (FileNotFoundError, ImportError, OSError, RuntimeError, TypeError, ValueError) as exc:
            logger.warning(f"[业绩引擎] AI产业链股票池不可用，按空股票池处理: {exc}")
            return set()
        _raise_if_cancelled(cancellation_token)
        codes = set()
        for value in values:
            _raise_if_cancelled(cancellation_token)
            code = normalize_ai_chain_code(value)
            if code:
                codes.add(code)
        return codes

    def _enabled_ai_chain_bse_quick_report_codes(self, *, cancellation_token=None) -> set[str]:
        universe_codes = self._resolve_stock_universe_codes(cancellation_token=cancellation_token)
        if universe_codes is None:
            return set()
        return set(AI_CHAIN_BSE_EARNINGS_ENABLED_CODES & universe_codes)

    def get_pending_ai_chain_bse_backfill_codes(self, *, cancellation_token=None) -> set[str]:
        """Return newly eligible AI-chain BSE codes that need one bounded historical scan."""
        enabled_codes = self._enabled_ai_chain_bse_quick_report_codes(cancellation_token=cancellation_token)
        persisted_codes = {
            code
            for raw_code in getattr(self, "ai_chain_bse_backfilled_codes", set())
            if (code := normalize_ai_chain_code(raw_code)) in AI_CHAIN_BSE_EARNINGS_ENABLED_CODES
        }
        active_completed_codes = persisted_codes & enabled_codes
        if active_completed_codes != persisted_codes:
            self.ai_chain_bse_backfilled_codes = active_completed_codes
            self._save_cache()
        return enabled_codes - active_completed_codes

    def mark_ai_chain_bse_backfill_completed(self, stock_codes, *, cancellation_token=None) -> bool:
        """Persist successful bounded-backfill completion for currently eligible BSE codes."""
        _raise_if_cancelled(cancellation_token)
        enabled_codes = self._enabled_ai_chain_bse_quick_report_codes(cancellation_token=cancellation_token)
        existing_codes = {
            code
            for raw_code in getattr(self, "ai_chain_bse_backfilled_codes", set())
            if (code := normalize_ai_chain_code(raw_code)) in enabled_codes
        }
        completed_codes = {
            code
            for raw_code in stock_codes or set()
            if (code := normalize_ai_chain_code(raw_code)) in enabled_codes
        }
        updated_codes = existing_codes | completed_codes
        if updated_codes == existing_codes:
            return False
        self.ai_chain_bse_backfilled_codes = updated_codes
        self._save_cache()
        return True

    @staticmethod
    def _record_stock_code(record: dict) -> str:
        if not isinstance(record, dict):
            return ""
        return normalize_ai_chain_code(record.get("股票代码") or record.get("代码") or record.get("stock_code"))

    def _filter_records_to_stock_universe(self, records: list[dict], *, cancellation_token=None) -> list[dict]:
        allowed_codes = self._resolve_stock_universe_codes(cancellation_token=cancellation_token)
        if allowed_codes is None:
            return list(records or [])
        filtered = []
        for record in records or []:
            _raise_if_cancelled(cancellation_token)
            if self._record_stock_code(record) in allowed_codes:
                filtered.append(record)
        return filtered

    def get_cached_record_rows(self, *, cancellation_token=None) -> list[dict]:
        """Return cached rows without importing the dataframe stack."""
        records = self._filter_records_to_stock_universe(
            self.local_records,
            cancellation_token=cancellation_token,
        )
        if not records:
            return []

        for record in records:
            _raise_if_cancelled(cancellation_token)
            self._normalize_record_dates(record, str(record.get("公告日期", "") or ""))
        if cancellation_token is None:
            self._inject_sectors(records)
        else:
            self._inject_sectors(records, cancellation_token=cancellation_token)

        def _sort_key(record: dict) -> tuple[str, float]:
            try:
                qoq = float(record.get("环比增速_百分比", 0.0) or 0.0)
            except (TypeError, ValueError):
                qoq = 0.0
            return str(record.get("揭晓日", "") or ""), qoq

        _raise_if_cancelled(cancellation_token)
        return sorted((dict(record) for record in records), key=_sort_key, reverse=True)

    def get_cached_records(self, *, cancellation_token=None) -> pd.DataFrame:
        """从长线账本中读取出所有还在存续期内的好股"""
        records = self.get_cached_record_rows(cancellation_token=cancellation_token)
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

    def _collect_guidance_candidates(
        self,
        report_date: str,
        target_publish_date: str,
        *,
        cancellation_token=None,
    ) -> list[dict]:
        _raise_if_cancelled(cancellation_token)
        df_yg = safe_ak_fetch(ak.stock_yjyg_em, date=report_date)
        _raise_if_cancelled(cancellation_token)
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
            _raise_if_cancelled(cancellation_token)
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
        cancellation_token=None,
    ) -> list[dict]:
        _raise_if_cancelled(cancellation_token)
        df_report = safe_ak_fetch(fetch_func, date=report_date, max_elapsed_sec=max_fetch_elapsed_sec)
        _raise_if_cancelled(cancellation_token)
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
            _raise_if_cancelled(cancellation_token)
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

    def _collect_enabled_ai_chain_bse_quick_report_candidates(
        self,
        report_date: str,
        target_publish_date: str,
        *,
        cancellation_token=None,
    ) -> list[dict]:
        _raise_if_cancelled(cancellation_token)
        enabled_codes = self._enabled_ai_chain_bse_quick_report_codes(cancellation_token=cancellation_token)
        if not enabled_codes:
            return []

        df_report = _fetch_enabled_ai_chain_bse_quick_report_rows(report_date, enabled_codes)
        _raise_if_cancelled(cancellation_token)
        if df_report.empty:
            return []

        df_target = self._filter_candidates_by_publish_date(
            df_report,
            "公告日期",
            target_publish_date,
            "快报",
        )
        candidates = []
        for _, row in df_target.iterrows():
            _raise_if_cancelled(cancellation_token)
            candidate = self._build_report_candidate(
                row,
                report_date=report_date,
                target_publish_date=target_publish_date,
                data_type="快报",
                date_col="公告日期",
                tone="快报速递",
            )
            if candidate is not None:
                candidates.append(candidate)
        return candidates

    def _collect_daily_surprise_candidates(
        self,
        report_dates: list[str],
        target_publish_date: str,
        *,
        cancellation_token=None,
    ) -> tuple[list[dict], bool, list[dict[str, object]]]:
        return _collect_daily_candidates_pipeline(
            self,
            report_dates,
            target_publish_date,
            cancellation_token,
        )

    def _pending_surprise_candidates(
        self,
        candidates: list[dict],
        stock_universe_codes: set[str] | None,
        *,
        cancellation_token=None,
    ) -> list[dict]:
        pending_candidates = []
        for cand in candidates:
            _raise_if_cancelled(cancellation_token)
            code = cand["股票代码"]
            is_standard_a_share_code = code.startswith(("0", "3", "6"))
            is_enabled_ai_chain_bse_code = (
                stock_universe_codes is not None
                and code in stock_universe_codes
                and code in AI_CHAIN_BSE_EARNINGS_ENABLED_CODES
            )
            if not (is_standard_a_share_code or is_enabled_ai_chain_bse_code):
                continue
            if stock_universe_codes is not None and code not in stock_universe_codes:
                continue
            fingerprint = self._build_fingerprint(code, cand["报告期"], cand["数据类型"])
            if fingerprint in self.seen_fingerprints:
                continue
            pending_candidates.append(cand)
        return pending_candidates

    def _check_surprise_candidate(self, cand: dict, *, cancellation_token=None) -> tuple[dict, str, dict]:
        _raise_if_cancelled(cancellation_token)
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
            cancellation_token=cancellation_token,
        )
        _raise_if_cancelled(cancellation_token)
        return cand, fingerprint, result

    @staticmethod
    def _surprise_result_passes_threshold(result: dict) -> bool:
        yoy_pct = result.get("同比增速_百分比", -1)
        return (
            result.get("环比增速_百分比", -1) >= EARNINGS_QOQ_MIN_PCT
            and result.get("单季净利润_新增", -1) > 0
            and yoy_pct > 0
        )

    def _process_pending_surprise_candidates(
        self,
        pending_candidates: list[dict],
        *,
        cancellation_token=None,
    ) -> tuple[list[dict], bool, list[dict[str, object]]]:
        return _process_pending_candidates_pipeline(
            self,
            pending_candidates,
            cancellation_token,
        )

    def fetch_daily_surprises(
        self,
        target_publish_date: str = None,
        *,
        cancellation_token=None,
        stock_codes: set[str] | None = None,
    ) -> pd.DataFrame:
        return _fetch_daily_surprises_pipeline(
            self,
            target_publish_date,
            cancellation_token,
            stock_codes=stock_codes,
        )

    def compute_single_quarter_qoq(
        self,
        target_code: str,
        target_est_cum_profit: float,
        report_date: str,
        is_koufei: bool = True,
        must_wait_ths: bool = False,
        cancellation_token=None,
    ) -> dict:
        _raise_if_cancelled(cancellation_token)
        try:
            df_fin = safe_ak_fetch(ak.stock_financial_benefit_ths, symbol=target_code)
        except _EARNINGS_COMPUTE_ERRORS as e:
            _raise_if_cancelled(cancellation_token)
            logger.error(f"[业绩预告] 获取失败: {e}")
            return {
                "error": "THS_SOURCE_GAP",
                "source_gap": _build_ths_source_gap(
                    target_code,
                    e,
                    last_success_basis=_THS_NO_LAST_SUCCESS_BASIS,
                ),
            }

        source_gap = _ths_source_gap_from_frame(df_fin)
        if source_gap:
            # 旧底稿仅用于说明最后成功依据，不能作为本轮计算输入，否则会把待重试候选写入 seen。
            return {"error": "THS_SOURCE_GAP", "source_gap": source_gap}
        try:
            _raise_if_cancelled(cancellation_token)
            if df_fin.empty:
                return _with_ths_source_gap({"error": "无历史"}, source_gap)
            df_fin["报告期"] = pd.to_datetime(df_fin["报告期"])
            df_fin = df_fin.sort_values(by="报告期", ascending=False)
            # --- 核心拦截：如果强制要求纯粹的扣非财报，抛弃之前传进来的虚假预估值，直接从底层提 ---
            if must_wait_ths:
                cols = [c for c in df_fin.columns if "扣除" in c]
                if not cols:
                    return _with_ths_source_gap({"error": "无找点字段"}, source_gap)
                match_current = df_fin[df_fin["报告期"] == pd.to_datetime(report_date)]
                if match_current.empty:
                    return _with_ths_source_gap({"error": "THS_PENDING"}, source_gap)
                real_val = match_current.iloc[0][cols[0]]
                if pd.isna(real_val):
                    return _with_ths_source_gap({"error": "THS_PENDING"}, source_gap)

                target_est_cum_profit = _parse_amount(real_val)
                if pd.isna(target_est_cum_profit):
                    return _with_ths_source_gap({"error": "THS_PENDING"}, source_gap)
                is_koufei = True
            cols = _select_profit_columns(df_fin.columns, is_koufei)
            if not cols:
                return _with_ths_source_gap({"error": "无利润字段"}, source_gap)
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
                _raise_if_cancelled(cancellation_token)
                value = get_cum_profit(target_date)
                if pd.notna(value):
                    return value, False

                quick_report_period = pd.to_datetime(target_date).strftime("%Y%m%d")
                quick_report_cum = self._get_quick_report_cum_profit(target_code, quick_report_period)
                _raise_if_cancelled(cancellation_token)
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
                return _with_ths_source_gap({"error": metrics_error}, source_gap)
            current_single = metrics.current_single
            last_single = metrics.last_single
            yoy_base_single = metrics.yoy_base_single
            last_single_basis = metrics.last_single_basis

            if pd.isna(current_single) or pd.isna(last_single):
                return _with_ths_source_gap({"error": "空值"}, source_gap)
            if last_single == 0:
                return _with_ths_source_gap({"error": "基数0"}, source_gap)

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
            return _with_ths_source_gap(result, source_gap)
        except _EARNINGS_COMPUTE_ERRORS as e:
            _raise_if_cancelled(cancellation_token)
            logger.error(f"[业绩预告] 获取失败: {e}")
            return {"error": "抛锚"}
