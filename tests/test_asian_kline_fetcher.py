# -*- coding: utf-8 -*-
import importlib
import sys
import types

from yfinance.exceptions import YFRateLimitError


def _load_fetcher_module(monkeypatch):
    fake_industry = types.ModuleType("industry_dict")
    fake_industry.OLIGARCH_DICT = {}
    fake_industry.VANGUARD_TICKERS = {}
    fake_session = types.ModuleType("vcp.fetchers.yf_session")
    fake_session.build_yf_session = lambda use_cf_proxy=True: object()
    fake_session.get_yf_rate_limit_status = lambda: {
        "active": False,
        "remaining_sec": 0.0,
        "reason": "",
        "until_ts": 0.0,
    }
    fake_session.is_yf_rate_limit_error = lambda exc: False
    fake_session.mark_yf_rate_limited = lambda exc=None, cooldown_sec=None: 0.0

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


def test_filter_asian_tickers_includes_local_tuc_override(monkeypatch):
    fetcher = _load_fetcher_module(monkeypatch)
    monkeypatch.setattr(fetcher, "VANGUARD_TICKERS", {"ASE": "3711.TW"}, raising=False)

    tickers = fetcher.filter_asian_tickers()

    assert tickers["TUC"] == "6274.TWO"


def test_filter_asian_tickers_includes_ai_pcb_equipment_japan_names(monkeypatch):
    fetcher = _load_fetcher_module(monkeypatch)
    monkeypatch.setattr(fetcher, "VANGUARD_TICKERS", {}, raising=False)

    tickers = fetcher.filter_asian_tickers()

    assert tickers["Shin-Etsu"] == "4063.T"
    assert tickers["SUMCO"] == "3436.T"
    assert tickers["SCREEN Holdings"] == "7735.T"
    assert tickers["Nidec"] == "6594.T"
    assert tickers["AMADA"] == "6113.T"
    assert tickers["Union Tool"] == "6278.T"
    assert tickers["Ushio"] == "6925.T"
    assert tickers["Accretech"] == "7729.T"
    assert tickers["MJC"] == "6871.T"
    assert tickers["Fujikura"] == "5803.T"
    assert tickers["SKC"] == "011790.KS"
    assert tickers["Murata"] == "6981.T"


def test_find_track_works_with_local_tsmc_tw_override(monkeypatch):
    fetcher = _load_fetcher_module(monkeypatch)
    monkeypatch.setattr(fetcher, "VANGUARD_TICKERS", {"TSMC": "TSM"}, raising=False)
    monkeypatch.setattr(
        fetcher,
        "OLIGARCH_DICT",
        {
            "先进制程代工": ["TSMC (台积电)"],
        },
        raising=False,
    )

    assert fetcher._find_track("2330.TW") == "先进制程代工"


def test_find_track_uses_local_track_override_for_tuc(monkeypatch):
    fetcher = _load_fetcher_module(monkeypatch)
    monkeypatch.setattr(fetcher, "VANGUARD_TICKERS", {}, raising=False)
    monkeypatch.setattr(fetcher, "OLIGARCH_DICT", {}, raising=False)

    assert fetcher._find_track("6274.TWO") == "高频PCB与覆铜板材料"
    assert fetcher._find_track("8035.T") == "前道晶圆设备与量测"


def test_find_track_uses_local_track_override_for_refined_japan_sectors(monkeypatch):
    fetcher = _load_fetcher_module(monkeypatch)
    monkeypatch.setattr(fetcher, "VANGUARD_TICKERS", {}, raising=False)
    monkeypatch.setattr(fetcher, "OLIGARCH_DICT", {}, raising=False)

    assert fetcher._find_track("4063.T") == "关键晶圆材料与特种工艺"
    assert fetcher._find_track("3436.T") == "关键晶圆材料与特种工艺"
    assert fetcher._find_track("7729.T") == "半导体测试设备与探针卡"
    assert fetcher._find_track("6871.T") == "半导体测试设备与探针卡"
    assert fetcher._find_track("5802.T") == "光芯片与硅光"
    assert fetcher._find_track("5803.T") == "光通信无源器件与精密零部件"
    assert fetcher._find_track("011790.KS") == "IC载板与封装材料"
    assert fetcher._find_track("6981.T") == "数据中心电力与配电"


def test_find_track_uses_local_track_override_for_ai_pcb_equipment(monkeypatch):
    fetcher = _load_fetcher_module(monkeypatch)
    monkeypatch.setattr(fetcher, "VANGUARD_TICKERS", {}, raising=False)
    monkeypatch.setattr(fetcher, "OLIGARCH_DICT", {}, raising=False)

    for ticker in ["7735.T", "6594.T", "6113.T", "6278.T", "6925.T"]:
        assert fetcher._find_track(ticker) == "AI PCB设备与关键耗材"


def test_finalize_klines_drops_nan_close(monkeypatch):
    fetcher = _load_fetcher_module(monkeypatch)

    rows = fetcher._finalize_klines(
        [
            {
                "date": "2026-04-30",
                "open": 100.0,
                "high": 110.0,
                "low": 90.0,
                "close": float("nan"),
                "volume": 1000,
            },
            {
                "date": "2026-05-01",
                "open": 101.0,
                "high": 111.0,
                "low": 91.0,
                "close": 105.0,
                "volume": 1200,
            },
        ],
        start_date=fetcher.date(2026, 4, 1),
        end_date=fetcher.date(2026, 5, 2),
    )

    assert rows == [
        {
            "date": "2026-05-01",
            "open": 101.0,
            "high": 111.0,
            "low": 91.0,
            "close": 105.0,
            "volume": 1200,
        }
    ]


def test_asian_market_meta_labels_ai_pcb_equipment_names_and_roles():
    from ui.tabs.asian_market_meta import get_ch_names_mapping, get_role_mapping

    names = get_ch_names_mapping()
    roles = get_role_mapping()

    assert names["7735.T"] == "SCREEN"
    assert names["6594.T"] == "尼得科"
    assert names["6113.T"] == "天田"
    assert names["6278.T"] == "Union Tool"
    assert names["6925.T"] == "牛尾电机"
    assert names["4063.T"] == "信越化学"
    assert names["3436.T"] == "SUMCO"
    assert names["7729.T"] == "东京精密"
    assert names["6871.T"] == "日本微电子"
    assert names["5803.T"] == "藤仓"
    assert names["011790.KS"] == "SKC"
    assert names["6981.T"] == "村田制作所"
    assert roles["7735.T"] == "LDI/直接成像设备"
    assert roles["6594.T"] == "PCB电测/光学检测+精密马达"
    assert roles["6113.T"] == "机械钻孔+激光钻孔"
    assert roles["6278.T"] == "PCB微钻/钻针耗材"
    assert roles["6925.T"] == "曝光/直接成像设备"
    assert roles["4063.T"] == "硅片+半导体材料龙头"
    assert roles["3436.T"] == "半导体硅片龙头"
    assert roles["7729.T"] == "探针台/精密计量设备"
    assert roles["6871.T"] == "存储探针卡/晶圆测试耗材"
    assert roles["5802.T"] == "光器件/光芯片上游核心"
    assert roles["5803.T"] == "光纤/光连接精密组件"
    assert roles["011790.KS"] == "玻璃基板/封装材料载体"
    assert roles["6981.T"] == "AI服务器MLCC/被动元件龙头"


def test_fetch_single_kline_routes_to_market_specific_history_source(monkeypatch):
    fetcher = _load_fetcher_module(monkeypatch)
    monkeypatch.setattr(
        fetcher,
        "_resolve_period_window",
        lambda period: (fetcher.date(2025, 4, 1), fetcher.date(2026, 4, 20), 260),
    )
    monkeypatch.setattr(fetcher, "_find_track", lambda ticker: f"track:{ticker}")

    route_hits = []

    def _make_rows(close_value):
        return [
            {
                "date": "2026-04-17",
                "open": close_value - 1,
                "high": close_value + 1,
                "low": close_value - 2,
                "close": close_value,
                "volume": 123456,
            }
        ]

    monkeypatch.setattr(
        fetcher,
        "_fetch_tw_history_twse",
        lambda *args, **kwargs: route_hits.append("TW") or _make_rows(100.0),
    )
    monkeypatch.setattr(
        fetcher,
        "_fetch_tw_history_tpex",
        lambda *args, **kwargs: route_hits.append("TWO") or _make_rows(200.0),
    )
    monkeypatch.setattr(
        fetcher,
        "_fetch_kr_history_naver",
        lambda *args, **kwargs: route_hits.append("KS") or _make_rows(300.0),
    )
    monkeypatch.setattr(
        fetcher,
        "_fetch_jp_history_yahoo_japan",
        lambda *args, **kwargs: route_hits.append("T") or _make_rows(400.0),
    )
    monkeypatch.setattr(
        fetcher,
        "_fetch_hk_history_tencent",
        lambda *args, **kwargs: route_hits.append("HK") or _make_rows(500.0),
    )

    cases = [
        ("TSMC", "2330.TW", "TWD", "twse_stock_day"),
        ("TUC", "6274.TWO", "TWD", "tpex_trading_stock"),
        ("Samsung", "005930.KS", "KRW", "naver_history"),
        ("TEL", "8035.T", "JPY", "yj_history"),
        ("ASMPT", "0522.HK", "HKD", "tencent_hk_qfq"),
    ]

    for name, ticker, currency, source in cases:
        payload = fetcher.fetch_single_kline(name, ticker, period="1y", session=object())
        assert payload["ticker"] == ticker
        assert payload["currency"] == currency
        assert payload["source"] == source
        assert payload["track"] == f"track:{ticker}"
        assert payload["kline_count"] == 1
        assert payload["klines"][0]["date"] == "2026-04-17"

    assert route_hits == ["TW", "TWO", "KS", "T", "HK"]


def test_jp_history_falls_back_to_yfinance_when_yahoo_japan_empty(monkeypatch):
    fetcher = _load_fetcher_module(monkeypatch)
    monkeypatch.setattr(fetcher, "_fetch_jp_history_yahoo_japan", lambda *args, **kwargs: [])
    monkeypatch.setattr(
        fetcher,
        "_fetch_yfinance_history_rows",
        lambda *args, **kwargs: [
            {
                "date": "2026-04-30",
                "open": 4900.0,
                "high": 5100.0,
                "low": 4800.0,
                "close": 5000.0,
                "volume": 123456,
            }
        ],
    )

    rows, source = fetcher._fetch_market_history_rows(
        "6981.T",
        object(),
        start_date=fetcher.date(2026, 4, 1),
        end_date=fetcher.date(2026, 5, 2),
        target_rows=260,
    )

    assert source == "yfinance_history"
    assert rows[0]["close"] == 5000.0


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


def test_sync_asian_kline_cache_rescues_stale_symbol_before_write(monkeypatch):
    fetcher = _load_fetcher_module(monkeypatch)
    monkeypatch.setattr(
        fetcher,
        "filter_asian_tickers",
        lambda market_filter=None: {
            "TSMC": "2330.TW",
            "MediaTek": "2454.TW",
        },
    )
    monkeypatch.setattr(
        fetcher,
        "fetch_all_asian_klines",
        lambda **kwargs: [
            {
                "name": "TSMC",
                "ticker": "2330.TW",
                "market": "台湾",
                "track": "晶圆代工",
                "currency": "TWD",
                "kline_count": 2,
                "klines": [{"date": "2026-04-24", "close": 880}, {"date": "2026-04-27", "close": 888}],
            },
            {
                "name": "MediaTek",
                "ticker": "2454.TW",
                "market": "台湾",
                "track": "边缘AI芯片",
                "currency": "TWD",
                "kline_count": 2,
                "klines": [{"date": "2026-03-30", "close": 1510}, {"date": "2026-03-31", "close": 1490}],
            },
        ],
    )
    monkeypatch.setattr(fetcher, "build_yf_session", lambda use_cf_proxy=True: object())
    monkeypatch.setattr(
        fetcher,
        "fetch_single_kline",
        lambda name, ticker, **kwargs: {
            "name": name,
            "ticker": ticker,
            "market": "台湾",
            "track": "边缘AI芯片",
            "currency": "TWD",
            "kline_count": 2,
            "klines": [{"date": "2026-04-24", "close": 2435}, {"date": "2026-04-27", "close": 2435}],
        } if ticker == "2454.TW" else None,
    )
    monkeypatch.setattr(fetcher, "_load_cached_row_map", lambda output_dir=None: {})

    saved_payloads = []
    monkeypatch.setattr(
        fetcher,
        "save_kline_data",
        lambda data, output_dir=None: saved_payloads.append((data, output_dir)) or "ignored.json",
    )

    success, message, report = fetcher.sync_asian_kline_cache(output_dir="cache-dir")

    assert success is True
    assert report["stale"] == ["2454.TW"]
    assert report["single_recovered"] == ["2454.TW"]
    assert report["missing"] == []
    assert len(saved_payloads) == 1
    written = {row["ticker"]: row for row in saved_payloads[0][0]}
    assert written["2454.TW"]["klines"][-1]["date"] == "2026-04-27"


def test_sync_asian_kline_cache_rejects_stale_old_cache_reuse(monkeypatch):
    fetcher = _load_fetcher_module(monkeypatch)
    monkeypatch.setattr(
        fetcher,
        "filter_asian_tickers",
        lambda market_filter=None: {
            "TSMC": "2330.TW",
            "MediaTek": "2454.TW",
        },
    )
    monkeypatch.setattr(
        fetcher,
        "fetch_all_asian_klines",
        lambda **kwargs: [
            {
                "name": "TSMC",
                "ticker": "2330.TW",
                "market": "台湾",
                "track": "晶圆代工",
                "currency": "TWD",
                "kline_count": 2,
                "klines": [{"date": "2026-04-24", "close": 880}, {"date": "2026-04-27", "close": 888}],
            }
        ],
    )
    monkeypatch.setattr(fetcher, "build_yf_session", lambda use_cf_proxy=True: object())
    monkeypatch.setattr(fetcher, "fetch_single_kline", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        fetcher,
        "_load_cached_row_map",
        lambda output_dir=None: {
            "2454.TW": {
                "name": "MediaTek",
                "ticker": "2454.TW",
                "market": "台湾",
                "track": "边缘AI芯片",
                "currency": "TWD",
                "kline_count": 2,
                "klines": [{"date": "2026-03-30", "close": 1510}, {"date": "2026-03-31", "close": 1490}],
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

    assert success is False
    assert report["missing"] == ["2454.TW"]
    assert report["reused"] == []
    assert report["stale"] == ["2454.TW"]
    assert "2454.TW" in message
    assert saved_payloads == []


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


def test_fetch_single_kline_returns_none_when_yahoo_rate_limited(monkeypatch):
    fetcher = _load_fetcher_module(monkeypatch)
    monkeypatch.setattr(
        fetcher,
        "_resolve_period_window",
        lambda period: (fetcher.date(2025, 4, 1), fetcher.date(2026, 4, 20), 260),
    )

    def _raise_rate_limit(*args, **kwargs):
        raise YFRateLimitError()

    marks = []
    monkeypatch.setattr(fetcher, "_fetch_market_history_rows", _raise_rate_limit)
    monkeypatch.setattr(fetcher, "is_yf_rate_limit_error", lambda exc: isinstance(exc, YFRateLimitError))
    monkeypatch.setattr(
        fetcher,
        "mark_yf_rate_limited",
        lambda exc=None, cooldown_sec=None: marks.append(str(exc)) or 30.0,
    )

    payload = fetcher.fetch_single_kline("TSMC", "2330.TW", period="1y", session=object())

    assert payload is None
    assert marks == ["Too Many Requests. Rate limited. Try after a while."]
