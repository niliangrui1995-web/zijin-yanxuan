# -*- coding: utf-8 -*-
import importlib
import sys
import types


def _load_fetcher_module(monkeypatch):
    fake_yfinance = types.ModuleType("yfinance")
    fake_yfinance.Ticker = object
    fake_industry = types.ModuleType("industry_dict")
    fake_industry.OLIGARCH_DICT = {}
    fake_industry.VANGUARD_TICKERS = {}
    fake_session = types.ModuleType("vcp.fetchers.yf_session")
    fake_session.build_yf_session = lambda use_cf_proxy=True: object()

    monkeypatch.setitem(sys.modules, "yfinance", fake_yfinance)
    monkeypatch.setitem(sys.modules, "industry_dict", fake_industry)
    monkeypatch.setitem(sys.modules, "vcp.fetchers.yf_session", fake_session)

    sys.modules.pop("vcp.fetchers.asian_kline_fetcher", None)
    return importlib.import_module("vcp.fetchers.asian_kline_fetcher")


def test_filter_asian_tickers_prefers_tw_listing_for_tsmc(monkeypatch):
    fetcher = _load_fetcher_module(monkeypatch)
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
    fetcher = _load_fetcher_module(monkeypatch)
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


def test_sync_asian_kline_cache_refuses_partial_overwrite(monkeypatch):
    fetcher = _load_fetcher_module(monkeypatch)
    monkeypatch.setattr(
        fetcher,
        "filter_asian_tickers",
        lambda market_filter=None: {
            "TSMC": "2330.TW",
            "ASE": "3711.TW",
        },
    )
    monkeypatch.setattr(
        fetcher,
        "fetch_all_asian_klines",
        lambda **kwargs: [
            {
                "name": "ASE",
                "ticker": "3711.TW",
                "market": "台湾",
                "track": "封测",
                "currency": "TWD",
                "kline_count": 2,
                "klines": [{"date": "2026-04-15", "close": 100}, {"date": "2026-04-16", "close": 101}],
            }
        ],
    )
    monkeypatch.setattr(fetcher, "build_yf_session", lambda use_cf_proxy=True: object())
    monkeypatch.setattr(fetcher, "fetch_single_kline", lambda *args, **kwargs: None)
    monkeypatch.setattr(fetcher, "_load_cached_row_map", lambda output_dir=None: {})

    saved_payloads = []
    monkeypatch.setattr(
        fetcher,
        "save_kline_data",
        lambda data, output_dir=None: saved_payloads.append((data, output_dir)) or "ignored.json",
    )

    success, message, report = fetcher.sync_asian_kline_cache(output_dir="cache-dir")

    assert success is False
    assert report["missing"] == ["2330.TW"]
    assert "2330.TW" in message
    assert saved_payloads == []


def test_sync_asian_kline_cache_reuses_previous_snapshot_before_write(monkeypatch):
    fetcher = _load_fetcher_module(monkeypatch)
    monkeypatch.setattr(
        fetcher,
        "filter_asian_tickers",
        lambda market_filter=None: {
            "TSMC": "2330.TW",
            "ASE": "3711.TW",
        },
    )
    monkeypatch.setattr(
        fetcher,
        "fetch_all_asian_klines",
        lambda **kwargs: [
            {
                "name": "ASE",
                "ticker": "3711.TW",
                "market": "台湾",
                "track": "封测",
                "currency": "TWD",
                "kline_count": 2,
                "klines": [{"date": "2026-04-15", "close": 100}, {"date": "2026-04-16", "close": 101}],
            }
        ],
    )
    monkeypatch.setattr(fetcher, "build_yf_session", lambda use_cf_proxy=True: object())
    monkeypatch.setattr(fetcher, "fetch_single_kline", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        fetcher,
        "_load_cached_row_map",
        lambda output_dir=None: {
            "2330.TW": {
                "name": "TSMC",
                "ticker": "2330.TW",
                "market": "台湾",
                "track": "晶圆代工",
                "currency": "TWD",
                "kline_count": 2,
                "klines": [{"date": "2026-04-15", "close": 880}, {"date": "2026-04-16", "close": 888}],
            }
        },
    )

    saved_payloads = []
    monkeypatch.setattr(
        fetcher,
        "save_kline_data",
        lambda data, output_dir=None: saved_payloads.append((data, output_dir)) or "ignored.json",
    )

    success, message, report = fetcher.sync_asian_kline_cache(output_dir="cache-dir")

    assert success is True
    assert report["missing"] == []
    assert report["reused"] == ["2330.TW"]
    assert "旧缓存回填 1 只" in message
    assert len(saved_payloads) == 1
    written_rows, written_output_dir = saved_payloads[0]
    assert written_output_dir == "cache-dir"
    assert sorted(row["ticker"] for row in written_rows) == ["2330.TW", "3711.TW"]


def test_sync_asian_kline_cache_keeps_existing_snapshot_when_full_fetch_is_empty(monkeypatch):
    fetcher = _load_fetcher_module(monkeypatch)
    monkeypatch.setattr(
        fetcher,
        "filter_asian_tickers",
        lambda market_filter=None: {
            "TSMC": "2330.TW",
            "ASE": "3711.TW",
        },
    )
    monkeypatch.setattr(fetcher, "fetch_all_asian_klines", lambda **kwargs: [])
    monkeypatch.setattr(
        fetcher,
        "_load_cached_row_map",
        lambda output_dir=None: {
            "2330.TW": {
                "name": "TSMC",
                "ticker": "2330.TW",
                "market": "台湾",
                "track": "晶圆代工",
                "currency": "TWD",
                "kline_count": 2,
                "klines": [{"date": "2026-04-15", "close": 880}, {"date": "2026-04-16", "close": 888}],
            },
            "3711.TW": {
                "name": "ASE",
                "ticker": "3711.TW",
                "market": "台湾",
                "track": "封测",
                "currency": "TWD",
                "kline_count": 2,
                "klines": [{"date": "2026-04-15", "close": 100}, {"date": "2026-04-16", "close": 101}],
            },
        },
    )

    saved_payloads = []
    monkeypatch.setattr(
        fetcher,
        "save_kline_data",
        lambda data, output_dir=None: saved_payloads.append((data, output_dir)) or "ignored.json",
    )

    success, message, report = fetcher.sync_asian_kline_cache(output_dir="cache-dir")

    assert success is True
    assert message == "亚洲 K 线远端拉取失败，已保留现有缓存"
    assert report["missing"] == []
    assert report["reused"] == ["2330.TW", "3711.TW"]
    assert report["cache_preserved"] is True
    assert saved_payloads == []
