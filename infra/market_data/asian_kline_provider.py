# -*- coding: utf-8 -*-
from __future__ import annotations

from vcp.fetchers import asian_kline_fetcher as _legacy_fetcher


def fetch_single_kline(
    name: str,
    ticker: str,
    period: str = "1y",
    session=None,
):
    kwargs = {"period": period}
    if session is not None:
        kwargs["session"] = session
    return _legacy_fetcher.fetch_single_kline(name, ticker, **kwargs)


def filter_asian_tickers(market_filter: str | None = None) -> dict[str, str]:
    return _legacy_fetcher.filter_asian_tickers(market_filter)


def find_asian_track(ticker: str) -> str:
    return _legacy_fetcher._find_track(ticker)


def sync_asian_kline_cache(
    market_filter: str | None = None,
    single_ticker: str | None = None,
    max_workers: int = 6,
    period: str = "1y",
    output_dir: str | None = None,
    time_budget_sec: float | int | None = None,
    cancellation_token=None,
):
    cancellation_checkpoint = None
    if cancellation_token is not None:
        cancellation_checkpoint = cancellation_token.raise_if_cancelled
        remaining = cancellation_token.remaining_seconds()
        if remaining is not None:
            time_budget_sec = remaining if time_budget_sec is None else min(float(time_budget_sec), remaining)
    return _legacy_fetcher.sync_asian_kline_cache(
        market_filter=market_filter,
        single_ticker=single_ticker,
        max_workers=max_workers,
        period=period,
        output_dir=output_dir,
        time_budget_sec=time_budget_sec,
        cancellation_checkpoint=cancellation_checkpoint,
    )


__all__ = [
    "fetch_single_kline",
    "filter_asian_tickers",
    "find_asian_track",
    "sync_asian_kline_cache",
]
