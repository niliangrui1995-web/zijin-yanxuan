# -*- coding: utf-8 -*-
"""Foreign block-trade orchestration behind a narrow application facade."""

from __future__ import annotations

import datetime
import json
import time
from typing import cast

import pandas as pd

from core.logger import get_logger
from core.task_errors import UserFacingTaskError
from infra.market_data.foreign_block_provider import fetch_block_trades, fetch_trade_calendar
from infra.tasks import ProcessExecutionError, ProcessTimeoutError
from infra.tasks.lifecycle import TaskCancelledError, TaskDeadlineExceeded

log = get_logger(__name__)

FOREIGN_KEYWORDS = ("高盛", "摩根大通", "摩根士丹利", "瑞银", "法巴", "渣打", "野村", "汇丰", "星展", "大和")
BLOCK_TRADE_CHUNK_TIMEOUT = 15
BLOCK_TRADE_CALENDAR_TIMEOUT = 10
BLOCK_TRADE_MAX_RETRIES = 2
BLOCK_TRADE_TOTAL_TIMEOUT = 90
BLOCK_TRADE_TIMEOUT_USER_MESSAGE = (
    f"抓取超时：{BLOCK_TRADE_TOTAL_TIMEOUT}秒内未拿到完整结果。通常是当前网络较慢，"
    "或 VPN/代理影响了国内数据源；可稍后重试，必要时临时关闭 VPN 后再刷新。"
)
_FETCH_ERRORS = (json.JSONDecodeError, OSError, RuntimeError, ProcessExecutionError, TypeError, ValueError)
_filter_rows_to_ai_chain_codes = None


def _raise_if_cancelled(cancellation_token=None) -> None:
    if cancellation_token is not None:
        cancellation_token.raise_if_cancelled()


def _cooperative_wait(cancellation_token, seconds: float) -> None:
    if cancellation_token is None:
        time.sleep(seconds)
        return
    if cancellation_token.wait(seconds):
        cancellation_token.raise_if_cancelled()


def _cancellable_items(values, cancellation_token=None):
    for value in values:
        _raise_if_cancelled(cancellation_token)
        yield value


def _resolve_filter_rows_to_ai_chain_codes():
    global _filter_rows_to_ai_chain_codes
    if _filter_rows_to_ai_chain_codes is None:
        from app.services.ui_industry_chain_service import filter_rows_to_ai_chain_codes

        _filter_rows_to_ai_chain_codes = filter_rows_to_ai_chain_codes
    return _filter_rows_to_ai_chain_codes


def filter_foreign_block_rows_to_ai_chain(row_data: list[dict]) -> list[dict]:
    try:
        return _resolve_filter_rows_to_ai_chain_codes()(row_data, code_keys=("代码", "证券代码"))
    except (FileNotFoundError, OSError, RuntimeError, TypeError, ValueError) as exc:
        log.warning(f"[外资大宗] AI产业链股票池不可用，已按空股票池处理: {exc}")
        return []


def _parse_numeric_trade_date(text: str):
    formats = {
        13: {"unit": "ms"},
        10: {"unit": "s"},
        8: {"format": "%Y%m%d"},
    }
    options = formats.get(len(text))
    if options is None:
        return pd.NaT
    if "unit" in options:
        return pd.to_datetime(int(text), unit=options["unit"], errors="coerce")
    return pd.to_datetime(text, format=options["format"], errors="coerce")


def normalize_trade_date_value(value) -> str:
    if value is None or pd.isna(value):
        return ""
    if isinstance(value, (datetime.date, datetime.datetime, pd.Timestamp)):
        return pd.Timestamp(value).strftime("%Y-%m-%d")

    text = str(value).strip()
    if not text or text.lower() in {"nan", "nat", "none"}:
        return ""
    if text.isdigit():
        parsed = _parse_numeric_trade_date(text)
        if pd.notna(parsed):
            return parsed.strftime("%Y-%m-%d")
        return text
    parsed = pd.to_datetime(text, errors="coerce")
    return parsed.strftime("%Y-%m-%d") if pd.notna(parsed) else text


def normalize_trade_date_series(series: pd.Series) -> pd.Series:
    return cast(pd.Series, series.apply(normalize_trade_date_value))


def should_include_foreign_block_row(buyer, seller) -> bool:
    buyer_text = str(buyer) if pd.notna(buyer) else ""
    seller_text = str(seller) if pd.notna(seller) else ""
    return any(keyword in buyer_text or keyword in seller_text for keyword in FOREIGN_KEYWORDS)


def foreign_block_direction(buyer, seller) -> str:
    buyer_text = str(buyer) if pd.notna(buyer) else ""
    seller_text = str(seller) if pd.notna(seller) else ""
    buy_foreign = any(keyword in buyer_text for keyword in FOREIGN_KEYWORDS)
    sell_foreign = any(keyword in seller_text for keyword in FOREIGN_KEYWORDS)
    if buy_foreign and sell_foreign:
        return "外资对倒"
    if buy_foreign:
        return "外资买入"
    if sell_foreign:
        return "外资卖出"
    return "--"


def build_foreign_block_trade_rows(records: list[dict]) -> tuple[list[dict], int]:
    if not records:
        return [], 0
    frame = pd.DataFrame(records)
    if frame.empty:
        return [], 0
    if "交易日期" in frame.columns:
        trade_dates = cast(pd.Series, frame.loc[:, "交易日期"])
        frame.loc[:, "交易日期"] = normalize_trade_date_series(trade_dates)
    frame = cast(
        pd.DataFrame,
        frame.groupby(
            ["交易日期", "证券代码", "买方营业部", "卖方营业部", "证券简称"],
            as_index=False,
        ).agg({"收盘价": "first", "成交价": "mean", "折溢率": "mean", "成交量": "sum", "成交额": "sum"}),
    )
    frame = frame.sort_values("证券代码", ascending=True)
    frame = frame.sort_values("交易日期", ascending=False, kind="stable")

    rows = []
    for _, record in frame.iterrows():
        close_price = _safe_float(record.get("收盘价", 0))
        trade_price = _safe_float(record.get("成交价", 0))
        premium = _safe_float(record.get("折溢率", 0))
        volume = _safe_float(record.get("成交量", 0))
        amount = _safe_float(record.get("成交额", 0))
        buyer = str(record.get("买方营业部", ""))
        seller = str(record.get("卖方营业部", ""))
        rows.append(
            {
                "代码": str(record.get("证券代码", "")).zfill(6),
                "名称": str(record.get("证券简称", "")),
                "现价": "--",
                "涨幅%": "--",
                "市值": "--",
                "交易日期": str(record.get("交易日期", "")),
                "交易详情": foreign_block_direction(buyer, seller),
                "当日收盘价": f"{close_price:.2f}" if close_price else "--",
                "成交价格": f"{trade_price:.2f}" if trade_price else "--",
                "折/溢价率(%)": f"{premium * 100:.2f}%",
                "成交数量(万股)": f"{volume / 10000.0:.2f}",
                "成交金额(万元)": f"{amount / 10000.0:.2f}",
                "买方营业部": buyer,
                "卖方营业部": seller,
            }
        )
    return filter_foreign_block_rows_to_ai_chain(rows), len(frame)


def _safe_float(value) -> float:
    try:
        return 0.0 if pd.isna(value) else float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def _cache_aggregation(frame: pd.DataFrame) -> dict[str, str]:
    candidates = (
        ("收盘价", "first"),
        ("成交价格", "mean"),
        ("成交价", "mean"),
        ("折溢率", "mean"),
        ("成交量", "sum"),
        ("成交金额", "sum"),
        ("成交额", "sum"),
    )
    return {source: method for source, method in candidates if source in frame.columns}


def _build_foreign_block_cache_row(record: pd.Series) -> dict:
    buyer = str(record.get("买方营业部", ""))
    seller = str(record.get("卖方营业部", ""))
    close_price = _safe_float(record.get("收盘价", 0))
    trade_price = _safe_float(record.get("成交价格", record.get("成交价", 0)))
    amount = _safe_float(record.get("成交金额", record.get("成交额", 0)))
    return {
        "代码": str(record.get("证券代码", "")).zfill(6),
        "名称": str(record.get("证券简称", "")),
        "现价": "--",
        "涨幅%": "--",
        "市值": "--",
        "交易日期": str(record.get("交易日期", "")),
        "交易详情": foreign_block_direction(buyer, seller),
        "当日收盘价": f"{close_price:.2f}" if close_price else "--",
        "成交价格": f"{trade_price:.2f}" if trade_price else "--",
        "折/溢价率(%)": f"{_safe_float(record.get('折溢率', 0)) * 100:.2f}%",
        "成交数量(万股)": f"{_safe_float(record.get('成交量', 0)) / 10000.0:.2f}",
        "成交金额(万元)": f"{amount:.2f}",
        "买方营业部": buyer,
        "卖方营业部": seller,
    }


def build_foreign_block_cache_rows(records: list[dict], *, cancellation_token=None) -> list[dict]:
    if not records:
        return []
    frame = pd.DataFrame(records)
    if "交易日期" in frame.columns:
        trade_dates = cast(pd.Series, frame.loc[:, "交易日期"])
        frame.loc[:, "交易日期"] = normalize_trade_date_series(trade_dates)
    required = ["交易日期", "证券代码", "买方营业部", "卖方营业部", "证券简称"]
    missing = [name for name in required if name not in frame.columns]
    if missing:
        raise ValueError(f"foreign block records missing columns: {missing}")

    aggregation = _cache_aggregation(frame)
    if not aggregation:
        raise ValueError("foreign block records missing numeric columns")
    grouped = cast(pd.DataFrame, frame.groupby(required, as_index=False).agg(aggregation))
    grouped = grouped.sort_values("证券代码", ascending=True)
    grouped = grouped.sort_values("交易日期", ascending=False, kind="stable")

    rows = [
        _build_foreign_block_cache_row(record)
        for _, record in _cancellable_items(grouped.iterrows(), cancellation_token)
    ]
    return filter_foreign_block_rows_to_ai_chain(rows)


def _deadline(cancellation_token=None) -> float:
    deadline = time.monotonic() + BLOCK_TRADE_TOTAL_TIMEOUT
    if cancellation_token is None:
        return deadline
    remaining = cancellation_token.remaining_seconds()
    return min(deadline, time.monotonic() + remaining) if remaining is not None else deadline


def _resolve_start_date(end_dt, days_to_fetch: int, deadline: float, cancellation_token=None):
    _raise_if_cancelled(cancellation_token)
    remaining = max(0.1, deadline - time.monotonic())
    try:
        raw_dates = fetch_trade_calendar(
            timeout=min(BLOCK_TRADE_CALENDAR_TIMEOUT, remaining),
            cancellation_token=cancellation_token,
        )
        dates = [pd.to_datetime(value).date() for value in raw_dates]
    except (TaskCancelledError, TaskDeadlineExceeded):
        raise
    except ProcessTimeoutError:
        log.warning("[大宗交易] 获取交易日历超时，回退到自然日估算")
        return end_dt - datetime.timedelta(days=int(days_to_fetch * 1.5))
    except _FETCH_ERRORS as exc:
        log.warning(f"[大宗交易] 获取交易日历失败，使用自然日估算: {exc}")
        return end_dt - datetime.timedelta(days=int(days_to_fetch * 1.5))
    _raise_if_cancelled(cancellation_token)
    past_dates = [value for value in dates if value <= end_dt.date()]
    start_date = past_dates[-days_to_fetch] if len(past_dates) >= days_to_fetch else None
    start_date = start_date or (past_dates[0] if past_dates else end_dt.date() - datetime.timedelta(days=days_to_fetch))
    return datetime.datetime.combine(start_date, datetime.time())


def _chunk_windows(start_dt, end_dt, *, latest_first: bool) -> list[tuple]:
    windows = []
    cursor = start_dt
    while cursor < end_dt:
        chunk_end = min(cursor + datetime.timedelta(days=15), end_dt)
        windows.append((cursor, chunk_end))
        cursor = chunk_end + datetime.timedelta(days=1)
    return list(reversed(windows)) if latest_first else windows


def _chunk_key(window) -> str:
    return f"{window[0].strftime('%Y%m%d')}-{window[1].strftime('%Y%m%d')}"


def _fetch_chunk(window, deadline: float, cancellation_token=None) -> tuple[list[dict], str]:
    timed_out = False
    for attempt in range(BLOCK_TRADE_MAX_RETRIES):
        _raise_if_cancelled(cancellation_token)
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return [], "timeout"
        try:
            records = fetch_block_trades(
                window[0].strftime("%Y%m%d"),
                window[1].strftime("%Y%m%d"),
                timeout=min(BLOCK_TRADE_CHUNK_TIMEOUT, remaining),
                cancellation_token=cancellation_token,
            )
            rows = [
                row
                for row in _cancellable_items(records, cancellation_token)
                if should_include_foreign_block_row(row.get("买方营业部"), row.get("卖方营业部"))
            ]
            return rows, "ok"
        except (TaskCancelledError, TaskDeadlineExceeded):
            raise
        except ProcessTimeoutError:
            timed_out = True
            log.warning(f"[大宗交易] {_chunk_key(window)} 请求超时")
        except _FETCH_ERRORS as exc:
            log.warning(f"[大宗交易] {_chunk_key(window)} 第{attempt + 1}次失败: {exc}")
        if attempt < BLOCK_TRADE_MAX_RETRIES - 1:
            _cooperative_wait(cancellation_token, 1)
    return [], "timeout" if timed_out else "failed"


def _fetch_records(*, days_to_fetch: int, cancellation_token=None, latest_first: bool) -> dict:
    end_dt = datetime.datetime.now()
    deadline = _deadline(cancellation_token)
    start_dt = _resolve_start_date(end_dt, days_to_fetch, deadline, cancellation_token)
    windows = _chunk_windows(start_dt, end_dt, latest_first=latest_first)
    records, timeout_chunks, failed_chunks = [], [], []
    finished_chunks = 0
    for index, window in enumerate(windows):
        _raise_if_cancelled(cancellation_token)
        if time.monotonic() >= deadline:
            timeout_chunks.extend(_chunk_key(item) for item in windows[index:])
            break
        chunk_rows, status = _fetch_chunk(window, deadline, cancellation_token)
        if status == "ok":
            records.extend(chunk_rows)
            finished_chunks += 1
        elif status == "timeout":
            timeout_chunks.append(_chunk_key(window))
        else:
            failed_chunks.append(_chunk_key(window))
    if not records and failed_chunks and finished_chunks == 0:
        raise UserFacingTaskError(
            "抓取失败：本轮未拿到有效结果。通常是国内数据源响应慢或网络较差；可稍后重试。",
            "大宗交易抓取失败：所有分段均未返回有效结果。",
        )
    return {"records": records, "timeout_chunks": timeout_chunks, "failed_chunks": failed_chunks}


def fetch_foreign_block_records(*, days_to_fetch: int, cancellation_token=None) -> dict:
    return _fetch_records(
        days_to_fetch=days_to_fetch,
        cancellation_token=cancellation_token,
        latest_first=True,
    )


def fetch_foreign_block_payload(days_to_fetch: int, cancellation_token=None) -> dict:
    payload = _fetch_records(
        days_to_fetch=days_to_fetch,
        cancellation_token=cancellation_token,
        latest_first=False,
    )
    _raise_if_cancelled(cancellation_token)
    rows, grouped_count = build_foreign_block_trade_rows(payload["records"])
    return {**payload, "row_data": rows, "grouped_count": grouped_count}


def format_incomplete_message(timeout_chunks, failed_chunks) -> str:
    parts = []
    if timeout_chunks:
        parts.append(f"{len(timeout_chunks)} 个区间超时")
    if failed_chunks:
        parts.append(f"{len(failed_chunks)} 个区间失败")
    return "" if not parts else "；" + "，".join(parts) + "，结果可能不完整"


__all__ = [
    "BLOCK_TRADE_TOTAL_TIMEOUT",
    "BLOCK_TRADE_TIMEOUT_USER_MESSAGE",
    "FOREIGN_KEYWORDS",
    "build_foreign_block_cache_rows",
    "build_foreign_block_trade_rows",
    "fetch_foreign_block_payload",
    "fetch_foreign_block_records",
    "filter_foreign_block_rows_to_ai_chain",
    "foreign_block_direction",
    "format_incomplete_message",
    "normalize_trade_date_series",
    "normalize_trade_date_value",
    "should_include_foreign_block_row",
]
