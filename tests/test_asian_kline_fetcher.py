# -*- coding: utf-8 -*-
from vcp.fetchers import asian_kline_fetcher as fetcher


def test_filter_asian_tickers_prefers_tw_listing_for_tsmc(monkeypatch):
    monkeypatch.setattr(
        fetcher,
        "VANGUARD_TICKERS",
        {
            "TSMC": "TSM",
            "ASE": "3711.TW",
            "NVIDIA": "NVDA",
        },
        raising=False,
    )

    tickers = fetcher.filter_asian_tickers()

    assert tickers["TSMC"] == "2330.TW"
    assert tickers["ASE"] == "3711.TW"
    assert "NVIDIA" not in tickers


def test_find_track_works_with_local_tsmc_tw_override(monkeypatch):
    monkeypatch.setattr(fetcher, "VANGUARD_TICKERS", {"TSMC": "TSM"}, raising=False)
    monkeypatch.setattr(
        fetcher,
        "OLIGARCH_DICT",
        {
            "定制化ASIC与代工": ["TSMC (台积电)"],
        },
        raising=False,
    )

    assert fetcher._find_track("2330.TW") == "定制化ASIC与代工"
