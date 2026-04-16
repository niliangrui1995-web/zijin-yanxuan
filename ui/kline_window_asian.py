# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import os

import pandas as pd

from core.market_calendar import MarketCalendar
from ui.kline_chart_payload import merge_kline_context


def build_asian_df_from_klines(klines, normalize_daily_df_index):
    if not klines:
        return None

    frame = pd.DataFrame(klines)
    if frame.empty:
        return None

    if "date" in frame.columns:
        frame["date"] = pd.to_datetime(frame["date"]).dt.normalize()
        frame.set_index("date", inplace=True)

    for column in ["open", "high", "low", "close", "volume"]:
        if column in frame.columns:
            frame[column] = pd.to_numeric(frame[column], errors="coerce").astype(float)

    return normalize_daily_df_index(frame)


def load_cached_asian_stock(json_cache: str, code: str) -> dict | None:
    if not os.path.exists(json_cache):
        return None

    with open(json_cache, "r", encoding="utf-8") as handle:
        raw = json.load(handle)
    stocks = raw.get("stocks", [])
    return next((stock for stock in stocks if stock.get("ticker") == code), None)


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


def build_asian_history_df(
    stock_payload: dict | None,
    *,
    vcp_data: dict,
    refresh_header_context,
    normalize_daily_df_index,
):
    if not stock_payload:
        return None
    merge_asian_context_payload(vcp_data, stock_payload, refresh_header_context)
    return build_asian_df_from_klines(
        stock_payload.get("klines", []),
        normalize_daily_df_index,
    )


def render_asian_history_payload(window, stock_payload: dict | None) -> bool:
    if not stock_payload:
        window._set_status_message("当前标的暂无历史日线数据", tone="warning")
        return False

    fresh_df = build_asian_history_df(
        stock_payload,
        vcp_data=window.vcp_data,
        refresh_header_context=window._refresh_header_context,
        normalize_daily_df_index=window._normalize_daily_df_index,
    )
    if fresh_df is None or fresh_df.empty:
        window._set_status_message("当前标的暂无历史日线数据", tone="warning")
        return False

    window._set_status_message(f"已回源载入 · {len(fresh_df)} 条日线", tone="success")
    window._render_chart(fresh_df, loading=False)
    return True


def schedule_asian_history_backfill(window, *, task_manager, fetch_single_kline):
    window._set_status_message("本地缓存缺少该标的，正在单独补拉历史日线...", tone="loading")

    def _bg_fetch():
        return fetch_single_kline(window.name, window.code, period="1y")

    def _on_fetch_success(stock_payload):
        try:
            render_asian_history_payload(window, stock_payload)
        except RuntimeError:
            pass

    def _on_fetch_error(error_msg):
        try:
            window._set_status_message(f"历史日线拉取失败: {error_msg}", tone="error")
        except RuntimeError:
            pass

    task_manager.run_in_background(
        _bg_fetch,
        on_success=_on_fetch_success,
        on_error=_on_fetch_error,
        task_id=f"kline_asian_{window.code}",
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

    if (
        quote_trade_date > last_date
        and quote_trade_date <= latest_trade_date
        and MarketCalendar.is_quote_refresh_time(market)
    ):
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
