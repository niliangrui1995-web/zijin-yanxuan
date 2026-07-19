# -*- coding: utf-8 -*-
from __future__ import annotations

from contextlib import suppress
from functools import partial

import pandas as pd

from app.services.kline_data_service import KlineDataService
from app.services.ui_market_calendar_service import MarketCalendar
from app.services.ui_task_lifecycle_service import (
    invoke_with_cancellation,
    raise_if_cancelled,
)
from ui.kline_chart_payload import merge_kline_context
from ui.kline_window_runtime import _is_current_request
from ui.kline_window_state import current_kline_open_context


def merge_asian_context_payload(vcp_data: dict, stock_payload: dict, refresh_header_context):
    track = str(stock_payload.get("track", "") or "").strip()
    market = str(stock_payload.get("market", "") or "").strip()
    currency = str(stock_payload.get("currency", "") or "").strip()
    if not any((track, market, currency)):
        return

    merge_kline_context(
        vcp_data,
        {
            "赛道": track,
            "track": track,
            "市场": market,
            "market": market,
            "货币": currency,
            "currency": currency,
        },
        overwrite=True,
    )
    refresh_header_context()


def _load_asian_backfill(
    cancellation_token,
    *,
    request_name: str,
    request_code: str,
    context,
    fetch_single_kline,
):
    raise_if_cancelled(cancellation_token)
    stock_payload = invoke_with_cancellation(
        fetch_single_kline,
        cancellation_token,
        request_name,
        request_code,
        period="1y",
    )
    raise_if_cancelled(cancellation_token)

    def loader(_path, _code):
        return stock_payload

    data_result = KlineDataService(None, asian_stock_loader=loader).load(
        context,
        cancellation_token=cancellation_token,
    )
    return stock_payload, data_result.frame


def _apply_asian_backfill_result(result, *, window, request_code: str, request_generation: int) -> None:
    with suppress(RuntimeError):
        if not _is_current_request(window, request_code, request_generation):
            return
        stock_payload, frame = result or (None, None)
        if not stock_payload or frame is None or frame.empty:
            window._set_status_message("当前标的暂无历史日线数据", tone="warning")
            return
        merge_asian_context_payload(window.vcp_data, stock_payload, window._refresh_header_context)
        window._set_status_message(f"已回源载入 · {len(frame)} 条日线", tone="success")
        window._render_chart(frame, loading=False)


def _report_asian_backfill_error(
    error_msg,
    *,
    window,
    request_code: str,
    request_generation: int,
) -> None:
    with suppress(RuntimeError):
        if _is_current_request(window, request_code, request_generation):
            window._set_status_message(f"历史日线拉取失败: {error_msg}", tone="error")


def schedule_asian_history_backfill(
    window,
    *,
    task_manager,
    fetch_single_kline,
    submit_owned_task=None,
):
    del task_manager
    if getattr(window, "_closing", False):
        return
    controller = getattr(window, "_load_controller", None)
    identity = getattr(controller, "current_identity", None)
    if controller is None or not controller.is_current(identity):
        return
    request_code = identity.code
    request_name = str(getattr(window, "name", "") or "").strip()
    request_generation = identity.generation
    context = current_kline_open_context(window)

    window._set_status_message("本地缓存缺少该标的，正在单独补拉历史日线...", tone="loading")
    if submit_owned_task is None:
        from ui.kline_window_runtime import _submit_owned_window_task

        submit_owned_task = _submit_owned_window_task
    submit_owned_task(
        window,
        "asian_history_backfill",
        partial(
            _load_asian_backfill,
            request_name=request_name,
            request_code=request_code,
            context=context,
            fetch_single_kline=fetch_single_kline,
        ),
        partial(
            _apply_asian_backfill_result,
            window=window,
            request_code=request_code,
            request_generation=request_generation,
        ),
        controller.task_id("asian-history", identity=identity),
        120.0,
        on_error=partial(
            _report_asian_backfill_error,
            window=window,
            request_code=request_code,
            request_generation=request_generation,
        ),
        identity=identity,
    )


def build_asian_rt_df(df_today: pd.DataFrame | None) -> pd.DataFrame | None:
    if df_today is None or df_today.empty:
        return None

    idx = pd.to_datetime(df_today.index).normalize()
    if idx.tz is not None:
        idx = idx.tz_localize(None)

    rt_df = pd.DataFrame(index=idx)
    rt_df["open"] = df_today["Open"].values.astype(float)
    rt_df["high"] = df_today["High"].values.astype(float)
    rt_df["low"] = df_today["Low"].values.astype(float)
    rt_df["close"] = df_today["Close"].values.astype(float)
    if "Volume" in df_today.columns:
        rt_df["volume"] = df_today["Volume"].values.astype(float)
    else:
        rt_df["volume"] = 0.0
    return rt_df


def _coerce_trade_date(raw_value):
    if not raw_value:
        return None
    try:
        return pd.Timestamp(raw_value).date()
    except (TypeError, ValueError):
        return None


def build_asian_rt_quote(code: str, quote: dict, *, market: str, latest_trade_date) -> dict | None:
    if latest_trade_date is None or not quote:
        return None

    df_today = quote.get("df_today")
    if df_today is not None and not df_today.empty:
        try:
            last_row = df_today.iloc[-1]
            last_dt = pd.Timestamp(last_row.name)
            if last_dt.tzinfo is not None:
                last_dt = last_dt.tz_localize(None)
            if last_dt.date() == latest_trade_date:
                return {
                    "date": latest_trade_date.strftime("%Y-%m-%d"),
                    "open": float(last_row.get("Open", 0) or 0),
                    "high": float(last_row.get("High", 0) or 0),
                    "low": float(last_row.get("Low", 0) or 0),
                    "close": float(last_row.get("Close", 0) or 0),
                    "volume": float(last_row.get("Volume", 0) or 0),
                }
        except (IndexError, KeyError, TypeError, ValueError):
            pass

    rt_close = float(quote.get("close", 0) or 0)
    rt_open = float(quote.get("open", rt_close) or 0)
    if rt_close <= 0 or rt_open <= 0:
        return None

    quote_trade_date = _coerce_trade_date(quote.get("date"))
    if quote_trade_date is None:
        return None
    if quote_trade_date > latest_trade_date:
        quote_trade_date = latest_trade_date
    if not MarketCalendar.is_trade_day(quote_trade_date, market=market):
        return None

    rt_high = float(quote.get("high", max(rt_open, rt_close)) or 0)
    rt_low = float(quote.get("low", min(rt_open, rt_close)) or 0)
    if rt_high <= 0:
        rt_high = max(rt_open, rt_close)
    if rt_low <= 0:
        rt_low = min(rt_open, rt_close)

    return {
        "date": quote_trade_date.strftime("%Y-%m-%d"),
        "open": rt_open,
        "high": rt_high,
        "low": rt_low,
        "close": rt_close,
        "volume": float(quote.get("volume", 0) or 0),
    }


def apply_asian_live_quote(df: pd.DataFrame, quote: dict, *, market: str) -> pd.DataFrame:
    merged_df = df.copy()
    df_today = quote.get("df_today")
    if df_today is not None and not df_today.empty:
        rt_df = build_asian_rt_df(df_today)
        if rt_df is not None and not rt_df.empty:
            merged_df.index = pd.to_datetime(merged_df.index).normalize()
            overlap_mask = merged_df.index.isin(rt_df.index)
            merged_df = pd.concat([merged_df[~overlap_mask], rt_df]).sort_index()
            merged_df = merged_df[~merged_df.index.duplicated(keep="last")]

    latest_trade_date = MarketCalendar.get_latest_trade_date(market)
    if latest_trade_date is None or merged_df.empty:
        return merged_df

    last_date = pd.Timestamp(merged_df.index[-1]).date()
    rt_close = quote.get("close")
    rt_open = quote.get("open")
    rt_high = quote.get("high")
    rt_low = quote.get("low")

    quote_trade_date = _coerce_trade_date(quote.get("date"))
    if quote_trade_date is None and df_today is not None and not df_today.empty:
        try:
            last_dt = pd.Timestamp(df_today.index[-1])
            if getattr(last_dt, "tzinfo", None) is not None:
                last_dt = last_dt.tz_localize(None)
            quote_trade_date = last_dt.date()
        except (IndexError, TypeError, ValueError):
            quote_trade_date = None

    if rt_close is None or quote_trade_date is None:
        return merged_df

    if quote_trade_date == last_date:
        if rt_open:
            merged_df.iloc[-1, merged_df.columns.get_loc("open")] = float(rt_open)
        if rt_high:
            merged_df.iloc[-1, merged_df.columns.get_loc("high")] = max(
                float(merged_df.iloc[-1]["high"]),
                float(rt_high),
            )
        if rt_low:
            merged_df.iloc[-1, merged_df.columns.get_loc("low")] = min(
                float(merged_df.iloc[-1]["low"]),
                float(rt_low),
            )
        merged_df.iloc[-1, merged_df.columns.get_loc("close")] = float(rt_close)
        return merged_df

    allow_append = MarketCalendar.is_quote_refresh_time(market) or quote_trade_date == latest_trade_date

    if quote_trade_date > last_date and quote_trade_date <= latest_trade_date and allow_append:
        rt_close_val = float(rt_close) if rt_close else 0.0
        if rt_close_val <= 0:
            return merged_df

        sim_open = float(rt_open) if rt_open else rt_close_val
        sim_high = float(rt_high) if rt_high else max(sim_open, rt_close_val)
        sim_low = float(rt_low) if rt_low else min(sim_open, rt_close_val)
        new_row = pd.DataFrame(
            {
                "open": [sim_open],
                "high": [sim_high],
                "low": [sim_low],
                "close": [rt_close_val],
                "volume": [0.0],
            },
            index=[pd.Timestamp(quote_trade_date)],
        )
        merged_df = pd.concat([merged_df, new_row])

    return merged_df
