# -*- coding: utf-8 -*-
from __future__ import annotations

from vcp.fetchers import asian_kline_fetcher as asian_fetcher_module
from vcp.fetchers import yf_session as yf_session_module


def build_yf_session(use_cf_proxy: bool = True):
    return yf_session_module.build_yf_session(use_cf_proxy)


def fetch_single_kline(
    name: str,
    ticker: str,
    period: str = "1y",
    use_cf_proxy: bool | None = None,
    session=None,
):
    kwargs = {"period": period}
    if use_cf_proxy is not None:
        kwargs["use_cf_proxy"] = use_cf_proxy
    if session is not None:
        kwargs["session"] = session
    return asian_fetcher_module.fetch_single_kline(name, ticker, **kwargs)


def filter_asian_tickers(market_filter: str | None = None) -> dict[str, str]:
    return asian_fetcher_module.filter_asian_tickers(market_filter)


def find_asian_track(ticker: str) -> str:
    return asian_fetcher_module._find_track(ticker)


def sync_asian_kline_cache(
    market_filter: str | None = None,
    single_ticker: str | None = None,
    max_workers: int = 6,
    period: str = "1y",
    use_cf_proxy: bool = True,
    output_dir: str | None = None,
):
    return asian_fetcher_module.sync_asian_kline_cache(
        market_filter=market_filter,
        single_ticker=single_ticker,
        max_workers=max_workers,
        period=period,
        use_cf_proxy=use_cf_proxy,
        output_dir=output_dir,
    )


__all__ = [
    "build_yf_session",
    "fetch_single_kline",
    "filter_asian_tickers",
    "find_asian_track",
    "sync_asian_kline_cache",
]
