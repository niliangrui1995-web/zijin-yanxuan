# -*- coding: utf-8 -*-
from __future__ import annotations

import json
from datetime import datetime

import pandas as pd

from core.market_calendar import MarketCalendar
from core.task_manager import task_manager
from ui.tabs.asian_market_workers import fetch_asian_realtime_quote


def normalize_daily_df_index(df, *, logger):
    """统一到按交易日去重的 DatetimeIndex，避免同一天重复 K 线。"""
    if df is None or len(df) == 0:
        return df

    try:
        normalized = df.copy()
        idx = pd.to_datetime(normalized.index, errors="coerce")
        valid_mask = ~idx.isna()
        if not valid_mask.all():
            normalized = normalized.loc[valid_mask].copy()
            idx = idx[valid_mask]
        if len(idx) == 0:
            return normalized
        if isinstance(idx, pd.DatetimeIndex) and idx.tz is not None:
            idx = idx.tz_localize(None)
        normalized.index = idx.normalize()
        normalized = normalized[~normalized.index.duplicated(keep="last")].sort_index()
        return normalized
    except (TypeError, ValueError, KeyError) as exc:
        logger.debug(f"[K线] 日线索引归一化失败: {exc}")
        return df


def _resolve_quote_trade_date(
    *,
    market: str,
    raw_quote_date,
    last_date,
    latest_trade_date,
) -> object:
    quote_trade_date = None
    if raw_quote_date:
        try:
            quote_trade_date = pd.Timestamp(raw_quote_date).date()
        except (TypeError, ValueError):
            quote_trade_date = None

    if quote_trade_date is None:
        if (
            market == "CN"
            and latest_trade_date is not None
            and MarketCalendar.is_quote_refresh_time(market)
            and last_date < latest_trade_date
        ):
            quote_trade_date = latest_trade_date
        else:
            quote_trade_date = last_date

    if latest_trade_date is None or quote_trade_date is None:
        return last_date
    if not MarketCalendar.is_trade_day(quote_trade_date, market=market):
        return last_date
    if quote_trade_date > latest_trade_date:
        return latest_trade_date
    return quote_trade_date


def _merge_cn_realtime_bar(df, quote, *, target_trade_date):
    fresh_df = df.copy()
    rt_open = float(quote.get("open", 0) or 0)
    if rt_open <= 0 or target_trade_date is None:
        return fresh_df

    rt_close = float(quote.get("close", 0) or 0)
    rt_high = float(quote.get("high", 0) or 0)
    rt_low = float(quote.get("low", 0) or 0)
    rt_vol = float(quote.get("volume", 0) or 0)

    last_date = pd.Timestamp(fresh_df.index[-1]).date()
    quote_trade_date = _resolve_quote_trade_date(
        market="CN",
        raw_quote_date=quote.get("date"),
        last_date=last_date,
        latest_trade_date=target_trade_date,
    )

    if quote_trade_date == last_date:
        fresh_df.iloc[-1, fresh_df.columns.get_loc("open")] = rt_open
        if rt_high > 0:
            fresh_df.iloc[-1, fresh_df.columns.get_loc("high")] = max(float(fresh_df.iloc[-1]["high"]), rt_high)
        if rt_low > 0:
            fresh_df.iloc[-1, fresh_df.columns.get_loc("low")] = min(float(fresh_df.iloc[-1]["low"]), rt_low)
        if rt_close > 0:
            fresh_df.iloc[-1, fresh_df.columns.get_loc("close")] = rt_close
        if "volume" in fresh_df.columns:
            fresh_df.iloc[-1, fresh_df.columns.get_loc("volume")] = rt_vol
        return fresh_df

    if (
        quote_trade_date > last_date
        and quote_trade_date <= target_trade_date
        and MarketCalendar.is_quote_refresh_time("CN")
        and rt_close > 0
    ):
        prev_row = fresh_df.iloc[-1]
        tol = 1e-8
        same_as_prev = (
            abs(float(prev_row.get("open", 0)) - rt_open) <= tol
            and abs(float(prev_row.get("high", 0)) - rt_high) <= tol
            and abs(float(prev_row.get("low", 0)) - rt_low) <= tol
            and abs(float(prev_row.get("close", 0)) - rt_close) <= tol
        )
        if not same_as_prev:
            sim_high = rt_high if rt_high > 0 else max(rt_open, rt_close)
            sim_low = rt_low if rt_low > 0 else min(rt_open, rt_close)
            new_row = pd.DataFrame(
                {
                    "open": [rt_open],
                    "high": [sim_high],
                    "low": [sim_low],
                    "close": [rt_close],
                    "volume": [rt_vol],
                },
                index=[pd.Timestamp(quote_trade_date)],
            )
            fresh_df = fresh_df[fresh_df.index != pd.Timestamp(quote_trade_date)]
            fresh_df = pd.concat([fresh_df, new_row])

    return fresh_df


def load_and_draw(window):
    """异步加载 K 线数据并渲染 ECharts。"""
    if "." in window.code:
        window._load_asian_chart()
        return

    local_df = window.data_provider.get_data(window.code)
    if local_df is not None and len(local_df) >= 60:
        window._render_chart(local_df, loading=True)
    else:
        window._set_status_message("正在同步完整日线数据...", tone="loading")

    def _bg_fetch():
        quote_to_apply = None
        target_trade_date = window._get_cn_target_trade_date()

        normalized_local_df = normalize_daily_df_index(window.data_provider.get_data(window.code), logger=window._log)
        last_local_date = None
        if normalized_local_df is not None and not normalized_local_df.empty:
            last_local_date = pd.Timestamp(normalized_local_df.index[-1]).date()

        need_sync = (
            target_trade_date is None
            or last_local_date is None
            or last_local_date < target_trade_date
        )

        if need_sync:
            try:
                fresh_df = window.data_provider.get_data_fresh_for_chart(window.code, force_sync=True)
            except TypeError:
                fresh_df = window.data_provider.get_data_fresh_for_chart(window.code)
            fresh_df = normalize_daily_df_index(fresh_df, logger=window._log)
        else:
            fresh_df = normalized_local_df

        if (
            not getattr(window.data_provider, "_offline", False)
            and target_trade_date is not None
            and MarketCalendar.is_quote_refresh_time("CN")
        ):
            last_dt = None
            if fresh_df is not None and not fresh_df.empty:
                last_dt = pd.Timestamp(fresh_df.index[-1]).date()

            already_has_latest = last_dt is not None and last_dt >= target_trade_date
            if not already_has_latest:
                try:
                    quotes = window.data_provider.fetch_realtime_quotes_batch([window.code])
                except (AttributeError, KeyError, OSError, RuntimeError, TypeError, ValueError) as exc:
                    window._log.warning(f"[K线] {window.code} 实时行情合并失败: {exc}")
                else:
                    quote_to_apply = quotes.get(window.code) if quotes else None

        return fresh_df, quote_to_apply, target_trade_date

    def _on_fetch_success(result):
        try:
            if not result:
                return

            fresh_df, quote_to_apply, target_trade_date = result
            if fresh_df is None or len(fresh_df) == 0:
                window._set_status_message("未获取到可用日线数据，请检查网络后重试", tone="error")
                return

            fresh_df = normalize_daily_df_index(fresh_df, logger=window._log)
            if quote_to_apply is not None:
                fresh_df = _merge_cn_realtime_bar(
                    fresh_df,
                    quote_to_apply,
                    target_trade_date=target_trade_date,
                )
                fresh_df = normalize_daily_df_index(fresh_df, logger=window._log)

            window._render_chart(fresh_df, loading=False)
        except RuntimeError:
            return

    task_manager.run_in_background(
        _bg_fetch,
        on_success=_on_fetch_success,
        task_id=f"kline_{window.code}",
    )


def poll_rt_update(window):
    """定时器回调：拉取最新实时报价，通过 JS 增量更新最后一根 K 线。"""
    market = window._get_market()
    if not MarketCalendar.is_quote_refresh_time(market):
        if window._rt_timer:
            window._rt_timer.stop()
            window._log.debug(f"[K线] {window.code} 已收盘，停止实时刷新")
        return

    try:
        if market != "CN":
            quote = window._build_asian_rt_quote()
            if quote is None:
                quote = fetch_asian_realtime_quote(window.code)
            if quote is not None:
                refresh_last_bar(window, quote)
            return

        quotes = window.data_provider.fetch_realtime_quotes_batch([window.code])
        if quotes and window.code in quotes:
            refresh_last_bar(window, quotes[window.code])
    except (AttributeError, ImportError, KeyError, OSError, RuntimeError, TypeError, ValueError) as exc:
        window._log.warning(f"[K线] {window.code} 实时刷新异常: {exc}")


def refresh_last_bar(window, quote):
    """通过 JS 注入实现无闪烁增量更新最后一根 K 线。"""
    if window.df is None or len(window.df) == 0:
        return

    rt_close = float(quote.get("close", 0) or 0)
    rt_open = float(quote.get("open", 0) or 0)
    rt_high = float(quote.get("high", 0) or 0)
    rt_low = float(quote.get("low", 0) or 0)
    rt_vol = float(quote.get("volume", 0) or 0)
    if rt_close <= 0 or rt_open <= 0:
        return

    market = window._get_market()
    latest_trade_date = MarketCalendar.get_latest_trade_date(market)
    last_date = pd.Timestamp(window.df.index[-1]).date()
    rt_date = _resolve_quote_trade_date(
        market=market,
        raw_quote_date=quote.get("date"),
        last_date=last_date,
        latest_trade_date=latest_trade_date,
    )

    if last_date >= rt_date:
        window.df.iloc[-1, window.df.columns.get_loc("open")] = rt_open
        if rt_high > 0:
            window.df.iloc[-1, window.df.columns.get_loc("high")] = max(
                float(window.df.iloc[-1, window.df.columns.get_loc("high")]),
                rt_high,
            )
        if rt_low > 0:
            window.df.iloc[-1, window.df.columns.get_loc("low")] = min(
                float(window.df.iloc[-1, window.df.columns.get_loc("low")]),
                rt_low,
            )
        window.df.iloc[-1, window.df.columns.get_loc("close")] = rt_close
        if "volume" in window.df.columns:
            window.df.iloc[-1, window.df.columns.get_loc("volume")] = rt_vol
    else:
        sim_high = rt_high if rt_high > 0 else max(rt_open, rt_close)
        sim_low = rt_low if rt_low > 0 else min(rt_open, rt_close)
        new_row = pd.DataFrame(
            {
                "open": [rt_open],
                "high": [sim_high],
                "low": [sim_low],
                "close": [rt_close],
                "volume": [rt_vol],
            },
            index=[pd.Timestamp(rt_date)],
        )
        window.df = pd.concat([window.df, new_row])

    rt_json = json.dumps(
        {
            "date": pd.Timestamp(rt_date).strftime("%Y-%m-%d"),
            "open": rt_open,
            "high": float(window.df.iloc[-1]["high"]),
            "low": float(window.df.iloc[-1]["low"]),
            "close": rt_close,
            "vol": rt_vol,
        }
    )
    window.browser.page().runJavaScript(f"window.updateLastBar({rt_json})")

    pre_close = rt_open
    if len(window.df) >= 2:
        pre_close = float(window.df.iloc[-2]["close"])

    pct = ((rt_close - pre_close) / pre_close * 100) if pre_close > 0 else 0
    sign = "+" if rt_close >= pre_close else ""
    now_str = datetime.now().strftime("%H:%M:%S")
    window._set_status_message(
        f"实时更新 {now_str} · {rt_close:.2f} · {sign}{pct:.2f}% · 成交量 {rt_vol / 10000:.0f}万",
        tone="realtime",
    )
