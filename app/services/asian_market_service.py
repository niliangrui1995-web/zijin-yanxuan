# -*- coding: utf-8 -*-
from __future__ import annotations

from infra.market_data import asian_kline_provider as asian_fetcher_module
from infra.market_data import yfinance_session as yf_session_module


def build_yf_session():
    return yf_session_module.build_yf_session()


def get_yf_rate_limit_status():
    return yf_session_module.get_yf_rate_limit_status()


def is_yf_rate_limit_error(exc):
    return yf_session_module.is_yf_rate_limit_error(exc)


def mark_yf_rate_limited(exc=None, cooldown_sec=None):
    if cooldown_sec is None:
        return yf_session_module.mark_yf_rate_limited(exc)
    return yf_session_module.mark_yf_rate_limited(exc, cooldown_sec=cooldown_sec)


def fetch_single_kline(
    name: str,
    ticker: str,
    period: str = "1y",
    session=None,
    *,
    cancellation_token=None,
):
    return asian_fetcher_module.fetch_single_kline(
        name,
        ticker,
        period=period,
        session=session,
        cancellation_token=cancellation_token,
    )


def filter_asian_tickers(market_filter: str | None = None) -> dict[str, str]:
    return asian_fetcher_module.filter_asian_tickers(market_filter)


def find_asian_track(ticker: str) -> str:
    return asian_fetcher_module.find_asian_track(ticker)


def sync_asian_kline_cache(
    market_filter: str | None = None,
    single_ticker: str | None = None,
    max_workers: int = 6,
    period: str = "1y",
    output_dir: str | None = None,
    time_budget_sec: float | int | None = None,
    cancellation_token=None,
):
    return asian_fetcher_module.sync_asian_kline_cache(
        market_filter=market_filter,
        single_ticker=single_ticker,
        max_workers=max_workers,
        period=period,
        output_dir=output_dir,
        time_budget_sec=time_budget_sec,
        cancellation_token=cancellation_token,
    )


__all__ = [
    "build_yf_session",
    "fetch_single_kline",
    "filter_asian_tickers",
    "find_asian_track",
    "get_yf_rate_limit_status",
    "is_yf_rate_limit_error",
    "mark_yf_rate_limited",
    "sync_asian_kline_cache",
]
