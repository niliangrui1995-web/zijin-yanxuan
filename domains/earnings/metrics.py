"""Pure earnings metric helpers with deferred numeric-library loading."""

from __future__ import annotations

import importlib
from collections.abc import Callable
from dataclasses import dataclass
from functools import lru_cache
from typing import Any


@lru_cache(maxsize=1)
def _numeric_modules() -> tuple[Any, Any]:
    """Load dataframe dependencies only when a metric is actually calculated."""
    return importlib.import_module("numpy"), importlib.import_module("pandas")


@dataclass(frozen=True)
class _SingleQuarterMetrics:
    current_single: float
    last_single: float
    yoy_base_single: float
    last_single_basis: str


type _ProfitGetter = Callable[[str, str], tuple[float, bool]]
type _SingleQuarterMetricResult = tuple[_SingleQuarterMetrics | None, str | None]


def _parse_amount(value):
    """把财务字段统一转成元，兼容字符串中的 万/亿 单位。"""
    np, pd = _numeric_modules()
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


def _cumulative_single_quarter_metrics(
    target_est_cum_profit: float,
    current_dates: tuple[str, str],
    yoy_dates: tuple[str, str],
    get_cum_profit_with_quick: _ProfitGetter,
) -> _SingleQuarterMetricResult:
    np, pd = _numeric_modules()
    current_cum, current_quick = get_cum_profit_with_quick(current_dates[0], "本期累计基数")
    previous_cum, previous_quick = get_cum_profit_with_quick(current_dates[1], "上一季基数")
    if pd.isna(current_cum) or pd.isna(previous_cum):
        return None, "缺记录"

    yoy_current_cum, _ = get_cum_profit_with_quick(yoy_dates[0], "去年同期基数")
    yoy_previous_cum, _ = get_cum_profit_with_quick(yoy_dates[1], "去年同期基数")
    yoy_base_single = (
        yoy_current_cum - yoy_previous_cum if pd.notna(yoy_current_cum) and pd.notna(yoy_previous_cum) else np.nan
    )

    return _SingleQuarterMetrics(
        current_single=target_est_cum_profit - current_cum,
        last_single=current_cum - previous_cum,
        yoy_base_single=yoy_base_single,
        last_single_basis=_basis_from_quick_flags(current_quick, previous_quick),
    ), None


def _q4_single_quarter_metrics(
    year: int, target_est_cum_profit: float, get_cum_profit_with_quick: _ProfitGetter
) -> _SingleQuarterMetricResult:
    return _cumulative_single_quarter_metrics(
        target_est_cum_profit,
        (f"{year}-09-30", f"{year}-06-30"),
        (f"{year - 1}-12-31", f"{year - 1}-09-30"),
        get_cum_profit_with_quick,
    )


def _q3_single_quarter_metrics(
    year: int, target_est_cum_profit: float, get_cum_profit_with_quick: _ProfitGetter
) -> _SingleQuarterMetricResult:
    return _cumulative_single_quarter_metrics(
        target_est_cum_profit,
        (f"{year}-06-30", f"{year}-03-31"),
        (f"{year - 1}-09-30", f"{year - 1}-06-30"),
        get_cum_profit_with_quick,
    )


def _q2_single_quarter_metrics(
    year: int, target_est_cum_profit: float, get_cum_profit_with_quick: _ProfitGetter
) -> _SingleQuarterMetricResult:
    np, pd = _numeric_modules()
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

    return _SingleQuarterMetrics(
        current_single=target_est_cum_profit - q1_cum,
        last_single=q1_cum,
        yoy_base_single=yoy_base_single,
        last_single_basis=_basis_from_quick_flags(q1_quick),
    ), None


def _q1_single_quarter_metrics(
    year: int, target_est_cum_profit: float, get_cum_profit_with_quick: _ProfitGetter
) -> _SingleQuarterMetricResult:
    np, pd = _numeric_modules()
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

    return _SingleQuarterMetrics(
        current_single=target_est_cum_profit,
        last_single=last_q4_cum - last_q3_cum,
        yoy_base_single=yoy_base_single,
        last_single_basis=_basis_from_quick_flags(q4_quick, q3_quick),
    ), None


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
    get_cum_profit_with_quick: _ProfitGetter,
) -> tuple[_SingleQuarterMetrics, str | None]:
    np, _pd = _numeric_modules()
    resolver = _SINGLE_QUARTER_METRIC_RESOLVERS.get(month)
    if resolver is None:
        return _SingleQuarterMetrics(np.nan, np.nan, np.nan, "财报"), None

    metrics, error = resolver(year, target_est_cum_profit, get_cum_profit_with_quick)
    if metrics is None:
        return _SingleQuarterMetrics(np.nan, np.nan, np.nan, "财报"), error
    return metrics, error
