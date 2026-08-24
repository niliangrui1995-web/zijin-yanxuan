# -*- coding: utf-8 -*-
import importlib
import os
import sys
import types
from datetime import date

import pytest
from yfinance.exceptions import YFRateLimitError


def _load_fetcher_module(monkeypatch):
    fake_industry = types.ModuleType("industry_dict")
    fake_industry.OLIGARCH_DICT = {}
    fake_industry.VANGUARD_TICKERS = {}
    fake_session = types.ModuleType("vcp.fetchers.yf_session")
    fake_session.build_yf_session = lambda: object()
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
    fetcher = importlib.import_module("vcp.fetchers.asian_kline_fetcher")
    monkeypatch.setattr(fetcher, "_expected_market_latest_dates", lambda _tickers: {})
    return fetcher


def test_fetcher_imports_without_external_industry_dict(monkeypatch):
    fake_session = types.ModuleType("vcp.fetchers.yf_session")
    fake_session.build_yf_session = lambda: object()
    fake_session.get_yf_rate_limit_status = lambda: {
        "active": False,
        "remaining_sec": 0.0,
        "reason": "",
        "until_ts": 0.0,
    }
    fake_session.is_yf_rate_limit_error = lambda exc: False
    fake_session.mark_yf_rate_limited = lambda exc=None, cooldown_sec=None: 0.0

    monkeypatch.delitem(sys.modules, "industry_dict", raising=False)
    monkeypatch.setitem(sys.modules, "vcp.fetchers.yf_session", fake_session)
    monkeypatch.setattr(os.path, "isfile", lambda _path: False)

    sys.modules.pop("vcp.fetchers.asian_kline_fetcher", None)
    fetcher = importlib.import_module("vcp.fetchers.asian_kline_fetcher")

    assert fetcher.OLIGARCH_DICT == {}
    assert fetcher.VANGUARD_TICKERS == {}
    assert fetcher.filter_asian_tickers()["TSMC"] == "2330.TW"


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
    assert tickers["AMADA"] == "6113.T"
    assert tickers["Union Tool"] == "6278.T"
    assert tickers["Ushio"] == "6925.T"
    assert tickers["Accretech"] == "7729.T"
    assert tickers["MJC"] == "6871.T"
    assert tickers["Fujikura"] == "5803.T"
    assert tickers["SKC"] == "011790.KS"
    assert tickers["Murata"] == "6981.T"
    assert "Nidec" not in tickers
    assert "6594.T" not in tickers.values()


def test_filter_asian_tickers_excludes_nidec_from_upstream_industry_dict(monkeypatch):
    fetcher = _load_fetcher_module(monkeypatch)
    monkeypatch.setattr(
        fetcher,
        "VANGUARD_TICKERS",
        {"Nidec": "6594.T", "SCREEN Holdings": "7735.T"},
        raising=False,
    )

    tickers = fetcher.filter_asian_tickers()

    assert tickers["SCREEN Holdings"] == "7735.T"
    assert "Nidec" not in tickers
    assert "6594.T" not in tickers.values()


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

    assert fetcher._find_track("2330.TW") == "\u5148\u8fdb\u5236\u7a0b\u4ee3\u5de5"
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

    for ticker in ["7735.T", "6113.T", "6278.T", "6925.T"]:
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
    assert names["6113.T"] == "天田"
    assert names["6278.T"] == "Union Tool"
    assert names["6925.T"] == "牛尾电机"
    assert names["4063.T"] == "信越化学"
    assert names["3436.T"] == "SUMCO"
    assert names["7729.T"] == "东京精密"
    assert names["6871.T"] == "日本微电子"
    assert names["5803.T"] == "藤仓"
    assert names["011790.KS"] == "SKC"
    assert names["0522.HK"] == "ASMPT"
    assert names["6981.T"] == "村田制作所"
    assert names["3324.TWO"] == "双鸿"
    assert names["3017.TW"] == "奇鋐"
    assert names["2316.TW"] == "楠梓电"
    assert "6594.T" not in names
    assert roles["7735.T"] == "头部｜PCB直接成像"
    assert roles["6113.T"] == "头部｜PCB激光钻孔"
    assert roles["6278.T"] == "龙头｜PCB精密微钻"
    assert roles["6925.T"] == "头部｜PCB曝光光源"
    assert roles["4063.T"] == "龙头｜硅片半导体材料"
    assert roles["3436.T"] == "头部｜半导体硅片"
    assert roles["7729.T"] == "头部｜探针台/计量"
    assert roles["6871.T"] == "头部｜存储探针卡"
    assert roles["5802.T"] == "头部｜光器件上游"
    assert roles["5803.T"] == "头部｜光纤连接组件"
    assert roles["011790.KS"] == "头部｜玻璃基板先行"
    assert roles["0522.HK"] == "头部｜先进封装设备"
    assert roles["6981.T"] == "龙头｜MLCC被动元件"
    assert roles["3324.TWO"] == "头部｜服务器散热模组"
    assert roles["3017.TW"] == "龙头｜服务器液冷模组"
    assert roles["2316.TW"] == "二线｜AI高速PCB弹性"
    assert "6594.T" not in roles


def test_asian_market_meta_roles_cover_asian_universe_with_rank_labels(monkeypatch):
    fetcher = _load_fetcher_module(monkeypatch)
    from ui.tabs.asian_market_meta import get_ch_names_mapping, get_role_mapping

    names = get_ch_names_mapping()
    roles = get_role_mapping()
    tickers = fetcher.filter_asian_tickers()

    missing_names = sorted(code for code in tickers.values() if not names.get(code))
    missing_roles = sorted(code for code in tickers.values() if not roles.get(code))
    unlabeled_roles = sorted(
        code
        for code in tickers.values()
        if roles.get(code) and not roles[code].startswith(("龙头｜", "头部｜", "二线｜"))
    )

    assert missing_names == []
    assert missing_roles == []
    assert unlabeled_roles == []


def test_fetch_single_kline_routes_to_primary_sources_while_yahoo_is_cooling_down(monkeypatch):
    fetcher = _load_fetcher_module(monkeypatch)
    monkeypatch.setattr(
        fetcher,
        "get_yf_rate_limit_status",
        lambda: {"active": True, "remaining_sec": 30.0, "cooldown_until": 30.0, "last_error": "rate limited"},
    )
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
    monkeypatch.setattr(
        fetcher,
        "_fetch_yfinance_history_rows",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("Yahoo fallback should not run")),
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


def test_primary_source_rate_limit_does_not_start_yahoo_cooldown(monkeypatch):
    fetcher = _load_fetcher_module(monkeypatch)
    monkeypatch.setattr(
        fetcher,
        "_resolve_period_window",
        lambda period: (fetcher.date(2025, 4, 1), fetcher.date(2026, 4, 20), 260),
    )
    monkeypatch.setattr(
        fetcher,
        "_fetch_market_history_rows",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("HTTP 429 from Tencent")),
    )
    monkeypatch.setattr(fetcher, "is_yf_rate_limit_error", lambda exc: "429" in str(exc))
    marks = []
    monkeypatch.setattr(
        fetcher,
        "mark_yf_rate_limited",
        lambda exc=None, cooldown_sec=None: marks.append(str(exc)) or 30.0,
    )

    assert fetcher.fetch_single_kline("ASMPT", "0522.HK", period="1y", session=object()) is None
    assert marks == []


def test_twse_history_uses_default_tls_verification(monkeypatch, public_dns_resolution):
    fetcher = _load_fetcher_module(monkeypatch)

    class _Response:
        def json(self):
            return {
                "stat": "OK",
                "data": [["115/04/20", "1,000", "", "100.00", "110.00", "90.00", "105.00"]],
            }

    class _Session:
        def __init__(self):
            self.kwargs = None

        def get(self, url, **kwargs):
            self.kwargs = dict(kwargs)
            return _Response()

    session = _Session()
    rows = fetcher._fetch_tw_history_twse(
        "2330.TW",
        session,
        start_date=fetcher.date(2026, 4, 1),
        end_date=fetcher.date(2026, 4, 30),
    )

    assert rows[0]["date"] == "2026-04-20"
    assert "verify" not in session.kwargs


def test_twse_history_tls_failure_fails_closed(monkeypatch, public_dns_resolution):
    fetcher = _load_fetcher_module(monkeypatch)

    class SSLError(Exception):
        pass

    class _Session:
        def get(self, url, **kwargs):
            raise SSLError("CERTIFICATE_VERIFY_FAILED: test")

    rows = fetcher._fetch_tw_history_twse(
        "2330.TW",
        _Session(),
        start_date=fetcher.date(2026, 4, 1),
        end_date=fetcher.date(2026, 4, 30),
    )

    assert rows == []


def test_tw_history_falls_back_to_yfinance_when_twse_empty(monkeypatch):
    fetcher = _load_fetcher_module(monkeypatch)
    monkeypatch.setattr(fetcher, "_fetch_tw_history_twse", lambda *args, **kwargs: [])
    monkeypatch.setattr(
        fetcher,
        "_fetch_yfinance_history_rows",
        lambda *args, **kwargs: [
            {
                "date": "2026-04-30",
                "open": 800.0,
                "high": 820.0,
                "low": 790.0,
                "close": 810.0,
                "volume": 123456,
            }
        ],
    )

    rows, source = fetcher._fetch_market_history_rows(
        "2330.TW",
        object(),
        start_date=fetcher.date(2026, 4, 1),
        end_date=fetcher.date(2026, 5, 2),
        target_rows=260,
    )

    assert source == "yfinance_history"
    assert rows[0]["close"] == 810.0


def test_tw_history_falls_back_to_yfinance_when_twse_transport_fails(monkeypatch):
    fetcher = _load_fetcher_module(monkeypatch)
    monkeypatch.setattr(
        fetcher,
        "_fetch_tw_history_twse",
        lambda *args, **kwargs: (_ for _ in ()).throw(OSError("unexpected EOF from transport stream")),
    )
    monkeypatch.setattr(fetcher, "_fetch_tw_history_twse_system_curl", lambda *args, **kwargs: [])
    monkeypatch.setattr(
        fetcher,
        "_fetch_yfinance_history_rows",
        lambda *args, **kwargs: [{"date": "2026-08-19", "close": 1200.0}],
    )

    rows, source = fetcher._fetch_market_history_rows(
        "2330.TW",
        object(),
        start_date=date(2026, 8, 1),
        end_date=date(2026, 8, 20),
        target_rows=260,
    )

    assert source == "yfinance_history"
    assert rows == [{"date": "2026-08-19", "close": 1200.0}]


def test_system_curl_official_transport_uses_strict_https_controls(monkeypatch, public_dns_resolution):
    fetcher = _load_fetcher_module(monkeypatch)
    commands = []

    def _run(command, **kwargs):
        commands.append((command, kwargs))
        return types.SimpleNamespace(returncode=0, stdout='{"stat": "OK"}', stderr="")

    fake_subprocess = types.SimpleNamespace(
        run=_run,
        DEVNULL=object(),
        CREATE_NO_WINDOW=0,
        TimeoutExpired=TimeoutError,
    )
    monkeypatch.setattr(fetcher, "subprocess", fake_subprocess, raising=False)
    monkeypatch.setattr(fetcher, "_SYSTEM_CURL_PATH", "C:\\Windows\\System32\\curl.exe", raising=False)
    monkeypatch.setattr(fetcher.os.path, "isfile", lambda _path: True)

    payload = fetcher._fetch_official_json_via_system_curl(
        "https://www.twse.com.tw/exchangeReport/STOCK_DAY?response=json&date=20260801&stockNo=2330",
        allowed_hosts={"www.twse.com.tw"},
        headers={"User-Agent": "Mozilla/5.0"},
        timeout=20,
    )

    assert payload == {"stat": "OK"}
    command, kwargs = commands[-1]
    assert command[0] == "C:\\Windows\\System32\\curl.exe"
    assert "--disable" in command
    assert command[command.index("--proto") + 1] == "=https"
    assert "--tlsv1.2" in command
    assert "--insecure" not in command
    assert "-k" not in command
    assert "--location" not in command
    assert kwargs["shell"] is False

    with pytest.raises(ValueError, match="not allowed"):
        fetcher._fetch_official_json_via_system_curl(
            "https://example.com/data.json",
            allowed_hosts={"www.twse.com.tw"},
            headers={},
            timeout=20,
        )


@pytest.mark.parametrize(
    ("ticker", "primary_name", "system_name", "source"),
    [
        ("2330.TW", "_fetch_tw_history_twse", "_fetch_tw_history_twse_system_curl", "twse_stock_day_system_curl"),
        ("6274.TWO", "_fetch_tw_history_tpex", "_fetch_tw_history_tpex_system_curl", "tpex_trading_stock_system_curl"),
    ],
)
def test_taiwan_history_uses_system_curl_before_yfinance_fallback(monkeypatch, ticker, primary_name, system_name, source):
    fetcher = _load_fetcher_module(monkeypatch)
    expected_rows = [{"date": "2026-08-19", "close": 700.0}]
    monkeypatch.setattr(fetcher, primary_name, lambda *args, **kwargs: [])
    monkeypatch.setattr(fetcher, system_name, lambda *args, **kwargs: expected_rows, raising=False)
    monkeypatch.setattr(
        fetcher,
        "_fetch_yfinance_history_rows",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("Yahoo fallback should not run")),
    )

    rows, resolved_source = fetcher._fetch_market_history_rows(
        ticker,
        object(),
        start_date=date(2026, 8, 1),
        end_date=date(2026, 8, 20),
        target_rows=260,
    )

    assert rows == expected_rows
    assert resolved_source == source


def test_two_history_falls_back_to_yfinance_when_tpex_transport_fails(monkeypatch):
    fetcher = _load_fetcher_module(monkeypatch)
    monkeypatch.setattr(
        fetcher,
        "_fetch_tw_history_tpex",
        lambda *args, **kwargs: (_ for _ in ()).throw(OSError("unexpected EOF from transport stream")),
    )
    monkeypatch.setattr(fetcher, "_fetch_tw_history_tpex_system_curl", lambda *args, **kwargs: [])
    monkeypatch.setattr(
        fetcher,
        "_fetch_yfinance_history_rows",
        lambda *args, **kwargs: [{"date": "2026-08-19", "close": 700.0}],
    )

    rows, source = fetcher._fetch_market_history_rows(
        "6274.TWO",
        object(),
        start_date=date(2026, 8, 1),
        end_date=date(2026, 8, 20),
        target_rows=260,
    )

    assert source == "yfinance_history"
    assert rows == [{"date": "2026-08-19", "close": 700.0}]


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
    monkeypatch.setattr(fetcher, "build_yf_session", lambda: object())
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
    monkeypatch.setattr(fetcher, "build_yf_session", lambda: object())
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


def test_scoped_sync_preserves_other_markets_and_cached_history(monkeypatch):
    fetcher = _load_fetcher_module(monkeypatch)
    cached_tsmc = {
        "name": "TSMC",
        "ticker": "2330.TW",
        "market": "台湾",
        "track": "晶圆代工",
        "currency": "TWD",
        "kline_count": 2,
        "klines": [
            {"date": "2026-08-14", "close": 2400},
            {"date": "2026-08-15", "close": 2410},
        ],
    }
    cached_japan = {
        "name": "Tokyo Electron",
        "ticker": "8035.T",
        "market": "日本",
        "track": "设备",
        "currency": "JPY",
        "kline_count": 1,
        "klines": [{"date": "2026-08-19", "close": 30000}],
    }
    fresh_tsmc = {
        "name": "TSMC",
        "ticker": "2330.TW",
        "market": "台湾",
        "track": "晶圆代工",
        "currency": "TWD",
        "kline_count": 1,
        "klines": [{"date": "2026-08-19", "close": 2350}],
    }
    monkeypatch.setattr(fetcher, "filter_asian_tickers", lambda _market=None: {"TSMC": "2330.TW"})
    monkeypatch.setattr(fetcher, "fetch_all_asian_klines", lambda **_kwargs: [fresh_tsmc])
    monkeypatch.setattr(
        fetcher,
        "_load_cached_row_map",
        lambda _output_dir=None: {"2330.TW": cached_tsmc, "8035.T": cached_japan},
    )
    saved_payloads = []
    monkeypatch.setattr(
        fetcher,
        "save_kline_data",
        lambda data, output_dir=None: saved_payloads.append((data, output_dir)),
    )

    success, _message, report = fetcher.sync_asian_kline_cache(market_filter="TW", output_dir="cache-dir")

    assert success is True
    assert report["written_count"] == 2
    written = {row["ticker"]: row for row in saved_payloads[0][0]}
    assert set(written) == {"2330.TW", "8035.T"}
    assert [bar["date"] for bar in written["2330.TW"]["klines"]] == [
        "2026-08-14",
        "2026-08-15",
        "2026-08-19",
    ]


def test_scoped_sync_refuses_to_create_partial_shared_snapshot(monkeypatch):
    fetcher = _load_fetcher_module(monkeypatch)
    monkeypatch.setattr(fetcher, "filter_asian_tickers", lambda _market=None: {"TSMC": "2330.TW"})
    monkeypatch.setattr(fetcher, "_load_cached_row_map", lambda _output_dir=None: {})
    fetch_calls = []
    save_calls = []
    monkeypatch.setattr(fetcher, "fetch_all_asian_klines", lambda **kwargs: fetch_calls.append(kwargs) or [])
    monkeypatch.setattr(fetcher, "save_kline_data", lambda *args, **kwargs: save_calls.append(args))

    success, message, report = fetcher.sync_asian_kline_cache(market_filter="TW", output_dir="cache-dir")

    assert success is False
    assert "未执行覆盖写入" in message
    assert report["cache_preserved"] is False
    assert fetch_calls == []
    assert save_calls == []


def test_sync_does_not_write_when_budget_expires_during_rescue(monkeypatch):
    fetcher = _load_fetcher_module(monkeypatch)
    now = [0.0]
    cached_rows = {
        "2330.TW": {
            "name": "TSMC",
            "ticker": "2330.TW",
            "market": "台湾",
            "kline_count": 1,
            "klines": [{"date": "2026-08-19", "close": 2350}],
        },
        "3711.TW": {
            "name": "ASE",
            "ticker": "3711.TW",
            "market": "台湾",
            "kline_count": 1,
            "klines": [{"date": "2026-08-19", "close": 100}],
        },
    }
    monkeypatch.setattr(fetcher.time, "monotonic", lambda: now[0])
    monkeypatch.setattr(fetcher, "filter_asian_tickers", lambda _market=None: {"TSMC": "2330.TW", "ASE": "3711.TW"})
    monkeypatch.setattr(fetcher, "fetch_all_asian_klines", lambda **_kwargs: [cached_rows["2330.TW"]])
    monkeypatch.setattr(fetcher, "_load_cached_row_map", lambda _output_dir=None: cached_rows)

    def _rescue(_name, _ticker, **_kwargs):
        now[0] = 11.0
        return cached_rows["3711.TW"]

    monkeypatch.setattr(fetcher, "fetch_single_kline", _rescue)
    saved_payloads = []
    monkeypatch.setattr(fetcher, "save_kline_data", lambda *args, **kwargs: saved_payloads.append(args))

    success, _message, report = fetcher.sync_asian_kline_cache(output_dir="cache-dir", time_budget_sec=10)

    assert success is True
    assert report["time_budget_exhausted"] is True
    assert saved_payloads == []


def test_sync_asian_kline_cache_rescues_stale_symbol_before_write(monkeypatch):
    fetcher = _load_fetcher_module(monkeypatch)
    monkeypatch.setattr(
        fetcher,
        "get_yf_rate_limit_status",
        lambda: {"active": True, "remaining_sec": 30.0, "cooldown_until": 30.0, "last_error": "rate limited"},
    )
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
    monkeypatch.setattr(fetcher, "build_yf_session", lambda: object())
    monkeypatch.setattr(
        fetcher,
        "fetch_single_kline",
        lambda name, ticker, **kwargs: (
            {
                "name": name,
                "ticker": ticker,
                "market": "台湾",
                "track": "边缘AI芯片",
                "currency": "TWD",
                "kline_count": 2,
                "klines": [{"date": "2026-04-24", "close": 2435}, {"date": "2026-04-27", "close": 2435}],
            }
            if ticker == "2454.TW"
            else None
        ),
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


def test_sync_asian_kline_cache_rejects_market_wide_stale_snapshot(monkeypatch):
    fetcher = _load_fetcher_module(monkeypatch)
    target = {"TSMC": "2330.TW", "TUC": "6274.TWO"}
    stale_rows = [
        {
            "name": name,
            "ticker": ticker,
            "market": "台湾",
            "track": "测试",
            "currency": "TWD",
            "kline_count": 1,
            "klines": [{"date": "2026-08-14", "close": 100.0}],
        }
        for name, ticker in target.items()
    ]
    monkeypatch.setattr(fetcher, "filter_asian_tickers", lambda market_filter=None: target)
    monkeypatch.setattr(fetcher, "fetch_all_asian_klines", lambda **kwargs: stale_rows)
    monkeypatch.setattr(fetcher, "build_yf_session", lambda: object())
    monkeypatch.setattr(fetcher, "fetch_single_kline", lambda *args, **kwargs: None)
    monkeypatch.setattr(fetcher, "_load_cached_row_map", lambda output_dir=None: {row["ticker"]: row for row in stale_rows})
    monkeypatch.setattr(
        fetcher,
        "_expected_market_latest_dates",
        lambda tickers: {"TW": date(2026, 8, 19)},
        raising=False,
    )
    saved_payloads = []
    monkeypatch.setattr(
        fetcher,
        "save_kline_data",
        lambda data, output_dir=None: saved_payloads.append((data, output_dir)),
    )

    success, message, report = fetcher.sync_asian_kline_cache(output_dir="cache-dir")

    assert success is False
    assert report["stale"] == ["2330.TW", "6274.TWO"]
    assert report["missing"] == ["2330.TW", "6274.TWO"]
    assert "2330.TW" in message
    assert saved_payloads == []


def test_sync_rejects_market_wide_interior_tail_gap(monkeypatch):
    fetcher = _load_fetcher_module(monkeypatch)
    target = {"TSMC": "2330.TW", "TUC": "6274.TWO"}
    cached_rows = [
        {
            "name": name,
            "ticker": ticker,
            "market": "台湾",
            "track": "测试",
            "currency": "TWD",
            "kline_count": 1,
            "klines": [{"date": "2026-08-14", "close": 100.0}],
        }
        for name, ticker in target.items()
    ]
    fresh_rows = [
        {
            **row,
            "kline_count": 2,
            "klines": [
                {"date": "2026-08-17", "close": 101.0},
                {"date": "2026-08-19", "close": 102.0},
            ],
        }
        for row in cached_rows
    ]
    monkeypatch.setattr(fetcher, "filter_asian_tickers", lambda _market=None: target)
    monkeypatch.setattr(fetcher, "fetch_all_asian_klines", lambda **_kwargs: fresh_rows)
    monkeypatch.setattr(fetcher, "_load_cached_row_map", lambda _output_dir=None: {row["ticker"]: row for row in cached_rows})
    monkeypatch.setattr(fetcher, "_expected_market_latest_dates", lambda _tickers: {"TW": date(2026, 8, 19)})
    monkeypatch.setattr(
        fetcher.MarketCalendar,
        "is_trade_day",
        lambda day, market: day in {date(2026, 8, 17), date(2026, 8, 18), date(2026, 8, 19)},
    )
    saved_payloads = []
    monkeypatch.setattr(fetcher, "save_kline_data", lambda *args, **kwargs: saved_payloads.append(args))

    success, _message, report = fetcher.sync_asian_kline_cache(output_dir="cache-dir")

    assert success is False
    assert report["market_wide_missing_sessions"] == ["TW:2026-08-18"]
    assert saved_payloads == []


def test_sync_asian_kline_cache_rejects_stale_existing_snapshot_when_fetch_is_empty(monkeypatch):
    fetcher = _load_fetcher_module(monkeypatch)
    target = {"TSMC": "2330.TW"}
    stale_row = {
        "name": "TSMC",
        "ticker": "2330.TW",
        "market": "台湾",
        "track": "测试",
        "currency": "TWD",
        "kline_count": 1,
        "klines": [{"date": "2026-08-14", "close": 100.0}],
    }
    monkeypatch.setattr(fetcher, "filter_asian_tickers", lambda market_filter=None: target)
    monkeypatch.setattr(fetcher, "fetch_all_asian_klines", lambda **kwargs: [])
    monkeypatch.setattr(fetcher, "_load_cached_row_map", lambda output_dir=None: {"2330.TW": stale_row})
    monkeypatch.setattr(
        fetcher,
        "_expected_market_latest_dates",
        lambda tickers: {"TW": date(2026, 8, 19)},
        raising=False,
    )

    success, message, report = fetcher.sync_asian_kline_cache(output_dir="cache-dir")

    assert success is False
    assert report["stale"] == ["2330.TW"]
    assert report["missing"] == ["2330.TW"]
    assert "未覆盖现有缓存" in message


def test_time_budget_exhaustion_rejects_stale_existing_snapshot(monkeypatch):
    fetcher = _load_fetcher_module(monkeypatch)
    stale_row = {
        "name": "TSMC",
        "ticker": "2330.TW",
        "market": "台湾",
        "track": "测试",
        "currency": "TWD",
        "kline_count": 1,
        "klines": [{"date": "2026-08-14", "close": 100.0}],
    }
    monkeypatch.setattr(fetcher, "_load_cached_row_map", lambda output_dir=None: {"2330.TW": stale_row})

    success, message, report = fetcher._time_budget_exhausted_result(
        {"2330.TW"},
        "cache-dir",
        {"TW": date(2026, 8, 19)},
    )

    assert success is False
    assert report["stale"] == ["2330.TW"]
    assert report["missing"] == ["2330.TW"]
    assert "before cache was complete" in message


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
    monkeypatch.setattr(fetcher, "build_yf_session", lambda: object())
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


def test_fetch_all_asian_klines_stops_before_fetch_when_time_budget_exhausted(monkeypatch):
    fetcher = _load_fetcher_module(monkeypatch)
    monkeypatch.setattr(
        fetcher,
        "filter_asian_tickers",
        lambda market_filter=None: {
            "TSMC": "2330.TW",
            "ASE": "3711.TW",
        },
    )

    fetch_calls = []
    monkeypatch.setattr(
        fetcher,
        "fetch_single_kline",
        lambda *args, **kwargs: fetch_calls.append((args, kwargs)) or None,
    )

    rows = fetcher.fetch_all_asian_klines(time_budget_sec=0)

    assert rows == []
    assert fetch_calls == []


def test_fetch_all_asian_klines_does_not_stop_primary_sources_during_yahoo_cooldown(monkeypatch):
    fetcher = _load_fetcher_module(monkeypatch)
    monkeypatch.setattr(
        fetcher,
        "filter_asian_tickers",
        lambda market_filter=None: {
            "Samsung": "005930.KS",
            "ASMPT": "0522.HK",
        },
    )
    monkeypatch.setattr(
        fetcher,
        "get_yf_rate_limit_status",
        lambda: {"active": True, "remaining_sec": 30.0, "cooldown_until": 30.0, "last_error": "rate limited"},
    )
    monkeypatch.setattr(fetcher.time, "sleep", lambda _seconds: None)
    fetch_calls = []

    def _fetch(name, ticker, **_kwargs):
        fetch_calls.append(ticker)
        return {"name": name, "ticker": ticker, "market": "primary", "kline_count": 1, "klines": []}

    monkeypatch.setattr(fetcher, "fetch_single_kline", _fetch)

    rows = fetcher.fetch_all_asian_klines()

    assert fetch_calls == ["005930.KS", "0522.HK"]
    assert [row["ticker"] for row in rows] == ["0522.HK", "005930.KS"]


def test_sync_asian_kline_cache_preserves_snapshot_when_time_budget_exhausted(monkeypatch):
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
        "_load_cached_row_map",
        lambda output_dir=None: {
            "2330.TW": {
                "name": "TSMC",
                "ticker": "2330.TW",
                "market": "cache",
                "track": "cache",
                "currency": "TWD",
                "kline_count": 1,
                "klines": [{"date": "2026-04-16", "close": 888}],
            },
            "3711.TW": {
                "name": "ASE",
                "ticker": "3711.TW",
                "market": "cache",
                "track": "cache",
                "currency": "TWD",
                "kline_count": 1,
                "klines": [{"date": "2026-04-16", "close": 101}],
            },
        },
    )

    saved_payloads = []
    monkeypatch.setattr(
        fetcher,
        "save_kline_data",
        lambda data, output_dir=None: saved_payloads.append((data, output_dir)) or "ignored.json",
    )

    success, message, report = fetcher.sync_asian_kline_cache(output_dir="cache-dir", time_budget_sec=0)

    assert success is True
    assert message == "Asian kline sync time budget exhausted; kept existing cache"
    assert report["time_budget_exhausted"] is True
    assert report["cache_preserved"] is True
    assert report["reused"] == ["2330.TW", "3711.TW"]
    assert report["missing"] == []
    assert saved_payloads == []


def test_yfinance_fallback_skips_request_during_yahoo_cooldown(monkeypatch):
    fetcher = _load_fetcher_module(monkeypatch)
    monkeypatch.setattr(
        fetcher,
        "get_yf_rate_limit_status",
        lambda: {"active": True, "remaining_sec": 30.0, "cooldown_until": 30.0, "last_error": "rate limited"},
    )
    ticker_calls = []
    fake_yfinance = types.SimpleNamespace(
        Ticker=lambda *args, **kwargs: ticker_calls.append((args, kwargs))
        or (_ for _ in ()).throw(AssertionError("should not create Yahoo ticker"))
    )
    monkeypatch.setitem(sys.modules, "yfinance", fake_yfinance)

    rows = fetcher._fetch_yfinance_history_rows(
        "2330.TW",
        object(),
        start_date=fetcher.date(2025, 4, 1),
        end_date=fetcher.date(2026, 4, 20),
    )

    assert rows == []
    assert ticker_calls == []


def test_yfinance_fallback_marks_only_its_own_rate_limit(monkeypatch):
    fetcher = _load_fetcher_module(monkeypatch)

    class _Ticker:
        def history(self, **_kwargs):
            raise YFRateLimitError()

    marks = []
    monkeypatch.setitem(sys.modules, "yfinance", types.SimpleNamespace(Ticker=lambda *args, **kwargs: _Ticker()))
    monkeypatch.setattr(
        fetcher,
        "get_yf_rate_limit_status",
        lambda: {"active": False, "remaining_sec": 0.0, "cooldown_until": 0.0, "last_error": ""},
    )
    monkeypatch.setattr(fetcher, "is_yf_rate_limit_error", lambda exc: isinstance(exc, YFRateLimitError))
    monkeypatch.setattr(
        fetcher,
        "mark_yf_rate_limited",
        lambda exc=None, cooldown_sec=None: marks.append(str(exc)) or 30.0,
    )

    rows = fetcher._fetch_yfinance_history_rows(
        "2330.TW",
        object(),
        start_date=fetcher.date(2025, 4, 1),
        end_date=fetcher.date(2026, 4, 20),
    )

    assert rows == []
    assert marks == ["Too Many Requests. Rate limited. Try after a while."]
