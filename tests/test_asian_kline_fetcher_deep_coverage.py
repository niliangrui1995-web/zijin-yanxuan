from __future__ import annotations

import builtins
import sys
from datetime import date
from types import SimpleNamespace

import pandas as pd
import pytest

from vcp.fetchers import asian_kline_fetcher as fetcher


class _Response:
    def __init__(self, *, payload=None, text=""):
        self.payload = payload
        self.text = text

    def json(self):
        return self.payload


def _row(ticker: str, *, day: str = "2026-07-14", market: str = "test", name: str | None = None):
    return {
        "name": name or ticker,
        "ticker": ticker,
        "market": market,
        "track": "track",
        "currency": "TWD",
        "source": "test",
        "kline_count": 1,
        "klines": [{"date": day, "open": 1.0, "high": 2.0, "low": 0.5, "close": 1.5, "volume": 10}],
    }


def test_industry_loader_covers_cached_spec_success_and_failures(monkeypatch):
    cached = SimpleNamespace(name="cached")
    monkeypatch.setitem(sys.modules, "industry_dict", cached)
    assert fetcher._load_industry_module() is cached

    monkeypatch.delitem(sys.modules, "industry_dict", raising=False)
    monkeypatch.setattr(fetcher.os.path, "isfile", lambda _path: True)
    monkeypatch.setattr(fetcher.importlib.util, "spec_from_file_location", lambda *_args: None)
    assert fetcher._load_industry_module() is None

    monkeypatch.setattr(
        fetcher.importlib.util,
        "spec_from_file_location",
        lambda *_args: SimpleNamespace(loader=None),
    )
    assert fetcher._load_industry_module() is None

    module = SimpleNamespace()

    class Loader:
        def exec_module(self, target):
            target.loaded = True

    monkeypatch.setattr(
        fetcher.importlib.util,
        "spec_from_file_location",
        lambda *_args: SimpleNamespace(loader=Loader()),
    )
    monkeypatch.setattr(fetcher.importlib.util, "module_from_spec", lambda _spec: module)
    assert fetcher._load_industry_module() is module
    assert module.loaded is True

    monkeypatch.setattr(
        fetcher.importlib.util, "module_from_spec", lambda _spec: (_ for _ in ()).throw(TypeError("bad spec"))
    )
    assert fetcher._load_industry_module() is None


def test_industry_mapping_refresh_handles_ready_missing_and_partial_modules(monkeypatch):
    monkeypatch.setattr(fetcher, "OLIGARCH_DICT", {"ready": ["x"]})
    monkeypatch.setattr(fetcher, "VANGUARD_TICKERS", {"ready": "1.T"})
    monkeypatch.setattr(
        fetcher,
        "_load_industry_module",
        lambda: (_ for _ in ()).throw(AssertionError("ready mappings should not reload")),
    )
    fetcher._ensure_industry_mappings_loaded()

    monkeypatch.setattr(fetcher, "OLIGARCH_DICT", {})
    monkeypatch.setattr(fetcher, "VANGUARD_TICKERS", {})
    monkeypatch.setattr(fetcher, "_load_industry_module", lambda: None)
    fetcher._ensure_industry_mappings_loaded()
    assert fetcher.OLIGARCH_DICT == {}

    module = SimpleNamespace(OLIGARCH_DICT={"track": ["Company"]}, VANGUARD_TICKERS={"Company": "1.T"})
    monkeypatch.setattr(fetcher, "_load_industry_module", lambda: module)
    fetcher._ensure_industry_mappings_loaded()
    assert fetcher.OLIGARCH_DICT == {"track": ["Company"]}
    assert fetcher.VANGUARD_TICKERS == {"Company": "1.T"}


def test_tls_deadline_and_time_budget_helpers_cover_all_outcomes(monkeypatch):
    class SSLError(Exception):
        pass

    assert fetcher._is_tls_verification_error(SSLError("failed")) is True
    assert fetcher._is_tls_verification_error(RuntimeError("SSLCertVerificationError")) is True
    assert fetcher._is_tls_verification_error(RuntimeError("CERTIFICATE_VERIFY_FAILED")) is True
    assert fetcher._is_tls_verification_error(RuntimeError("network")) is False

    monotonic_values = iter([100.0, 100.0, 105.0, 105.0])
    monkeypatch.setattr(fetcher.time, "monotonic", lambda: next(monotonic_values))
    assert fetcher._deadline_from_time_budget(None) is None
    assert fetcher._deadline_from_time_budget("bad") is None
    assert fetcher._deadline_from_time_budget(0) == 100.0
    assert fetcher._deadline_from_time_budget(4) == 104.0
    checkpoints = []
    assert fetcher._deadline_exceeded(None, lambda: checkpoints.append("checked")) is False
    assert fetcher._deadline_exceeded(104.0) is True
    assert checkpoints == ["checked"]
    assert fetcher._remaining_time_budget(None) is None
    assert fetcher._remaining_time_budget(103.0) == 0.0

    monkeypatch.setattr(fetcher, "_load_cached_row_map", lambda _output: {"A": _row("A")})
    success, _message, report = fetcher._time_budget_exhausted_result({"A"}, "cache")
    assert success is True and report["reused"] == ["A"]

    success, _message, report = fetcher._time_budget_exhausted_result({"A", "B"}, "cache")
    assert success is False and report["missing"] == ["B"]

    monkeypatch.setattr(
        fetcher,
        "_load_cached_row_map",
        lambda _output: (_ for _ in ()).throw(PermissionError("locked")),
    )
    assert fetcher._time_budget_exhausted_result({"A"}, "cache")[2]["missing"] == ["A"]


def test_market_filters_sync_targets_and_track_matching(monkeypatch):
    monkeypatch.setattr(
        fetcher,
        "_get_asian_source_tickers",
        lambda: {"Taiwan": "1.TW", "Japan": "2.T", "NoSuffix": "ABC"},
    )
    assert fetcher._get_market_suffix("1.TW") == ".TW"
    assert fetcher._get_market_suffix("ABC") is None
    assert fetcher._get_market_name("ABC") == "未知"
    assert fetcher.filter_asian_tickers("tw") == {"Taiwan": "1.TW"}
    assert fetcher.filter_asian_tickers("invalid") == {}
    assert fetcher.filter_asian_tickers() == {"Taiwan": "1.TW", "Japan": "2.T"}

    assert fetcher._build_sync_target_map(single_ticker="   ") == {}
    assert fetcher._build_sync_target_map(single_ticker="2.T") == {"Japan": "2.T"}
    assert fetcher._build_sync_target_map(single_ticker="9.HK") == {"9.HK": "9.HK"}
    assert fetcher._build_sync_target_map("JP") == {"Japan": "2.T"}

    cases = [
        ("Foo", "FOO.T", "Foo Corporation"),
        ("A-B", "AB.T", "AB"),
        ("A-B", "AB.T", "AB (Japan)"),
        ("abc", "X.T", "alpha beta corp"),
        ("ab", "X.T", "alpha beta (company)"),
        ("Unrelated", "abc.T", "alpha beta corp"),
        ("Unrelated", "ab.T", "alpha beta (company)"),
    ]
    for company, ticker, member in cases:
        monkeypatch.setattr(fetcher, "ASIAN_LOCAL_TRACK_OVERRIDES", {})
        monkeypatch.setattr(fetcher, "_get_asian_source_tickers", lambda c=company, t=ticker: {c: t})
        monkeypatch.setattr(fetcher, "OLIGARCH_DICT", {"matched": [member]})
        monkeypatch.setattr(fetcher, "VANGUARD_TICKERS", {company: ticker})
        assert fetcher._find_track(ticker) == "matched"

    monkeypatch.setattr(fetcher, "_get_asian_source_tickers", lambda: {})
    assert fetcher._find_track("UNKNOWN.T") == "未知赛道"


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (None, None),
        (10, 10.0),
        (float("inf"), None),
        ("--", None),
        (" 1,234.5% ", 1234.5),
        ("price=-12.25 TWD", -12.25),
        ("not numeric", None),
    ],
)
def test_numeric_normalizer(raw, expected):
    assert fetcher._to_float(raw) == expected


def test_date_kline_staleness_and_period_helpers(monkeypatch):
    assert fetcher._normalize_iso_date(None) is None
    assert fetcher._normalize_iso_date("2026-07-14") == "2026-07-14"
    assert fetcher._normalize_iso_date("2026/07/14") == "2026-07-14"
    assert fetcher._normalize_iso_date("2026/07/14 12:30:00") == "2026-07-14"
    assert fetcher._normalize_iso_date("20260714") == "2026-07-14"
    assert fetcher._normalize_iso_date("bad") is None
    assert fetcher._normalize_roc_date(None) is None
    assert fetcher._normalize_roc_date("2026-07-14") == "2026-07-14"
    assert fetcher._normalize_roc_date("115/07/14") == "2026-07-14"
    assert fetcher._normalize_roc_date("bad/99/99") is None
    assert fetcher._date_from_iso(None) is None
    assert fetcher._date_from_iso("bad") is None

    assert fetcher._last_kline_date(None) is None
    assert fetcher._last_kline_date({"klines": []}) is None
    rows = {
        "new": _row("new", day="2026-07-14", market="TW"),
        "old": _row("old", day="2026-07-13", market="TW"),
        "other": _row("other", day="2026-07-12", market="JP"),
        "invalid": {"ticker": "invalid", "market": "", "klines": [{"date": "bad"}]},
    }
    assert fetcher._market_latest_dates(rows) == {"TW": date(2026, 7, 14), "JP": date(2026, 7, 12)}
    assert fetcher._find_stale_kline_tickers(rows, set(rows)) == ["invalid", "old"]
    assert fetcher._drop_stale_kline_rows(rows, set(rows)) == ["invalid", "old"]
    assert "old" not in rows

    for period, expected_rows in (("bad", 260), ("0d", 5), ("2mo", 50), ("2y", 520)):
        start, end, target_rows = fetcher._resolve_period_window(period)
        assert start < end and target_rows == expected_rows

    assert list(fetcher._iter_month_starts(date(2025, 12, 15), date(2026, 2, 1))) == [
        date(2025, 12, 1),
        date(2026, 1, 1),
        date(2026, 2, 1),
    ]
    assert fetcher._extract_yj_history_value([], 0) is None
    assert fetcher._extract_yj_history_value([{"value": "1,234"}], 0) == 1234.0
    assert fetcher._extract_yj_history_value(["12.5"], 0) == 12.5


def test_finalize_klines_filters_defaults_deduplicates_and_sorts():
    rows = fetcher._finalize_klines(
        [
            None,
            {"date": "bad", "close": 1},
            {"date": "2026-06-30", "close": 1},
            {"date": "2026-07-15", "close": 1},
            {"date": "2026-07-13", "close": None},
            {"date": "2026-07-14", "close": "10.126", "volume": "1,234.4"},
            {"date": "2026-07-12", "open": 8, "high": 11, "low": 7, "close": 10, "volume": 2},
            {"date": "2026-07-14", "open": 9, "high": None, "low": None, "close": 11, "volume": None},
        ],
        start_date=date(2026, 7, 1),
        end_date=date(2026, 7, 14),
    )
    assert [row["date"] for row in rows] == ["2026-07-12", "2026-07-14"]
    assert rows[-1] == {"date": "2026-07-14", "open": 9.0, "high": 11.0, "low": 9.0, "close": 11.0, "volume": 0}


def test_twse_and_tpex_parsers_cover_empty_status_invalid_and_transport_errors(monkeypatch):
    assert fetcher._fetch_tw_history_twse("", object(), start_date=date(2026, 7, 1), end_date=date(2026, 7, 2)) == []
    payloads = iter(
        [
            {"stat": "NO DATA"},
            {
                "stat": "OK",
                "data": [
                    ["bad"],
                    ["115/07/14", "1,000", "", "10", "12", "9", "11"],
                ],
            },
        ]
    )
    monkeypatch.setattr(fetcher, "_iter_month_starts", lambda *_args: [date(2026, 6, 1), date(2026, 7, 1)])
    monkeypatch.setattr(fetcher, "requests_get_https", lambda *_args, **_kwargs: _Response(payload=next(payloads)))
    twse = fetcher._fetch_tw_history_twse("2330.TW", object(), start_date=date(2026, 6, 1), end_date=date(2026, 7, 31))
    assert twse == [{"date": "2026-07-14", "open": 10.0, "high": 12.0, "low": 9.0, "close": 11.0, "volume": 1000.0}]

    monkeypatch.setattr(
        fetcher,
        "requests_get_https",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("network")),
    )
    with pytest.raises(RuntimeError, match="network"):
        fetcher._fetch_tw_history_twse("2330.TW", object(), start_date=date(2026, 7, 1), end_date=date(2026, 7, 2))

    assert fetcher._fetch_tw_history_tpex("", object(), start_date=date(2026, 7, 1), end_date=date(2026, 7, 2)) == []
    payloads = iter(
        [
            {"stat": "fail"},
            {
                "stat": "ok",
                "tables": [{"data": [["bad"], ["2026/07/14", "2", "", "10", "12", "9", "11"]]}],
            },
        ]
    )
    monkeypatch.setattr(fetcher, "requests_get_https", lambda *_args, **_kwargs: _Response(payload=next(payloads)))
    tpex = fetcher._fetch_tw_history_tpex("6274.TWO", object(), start_date=date(2026, 6, 1), end_date=date(2026, 7, 31))
    assert tpex[-1]["volume"] == 2000.0


def test_naver_parser_covers_empty_pagination_invalid_dates_and_oldest_stop(monkeypatch):
    assert fetcher._fetch_kr_history_naver("", object(), start_date=date(2026, 1, 1), end_date=date(2026, 7, 1)) == []

    valid = [
        {
            "localTradedAt": f"2026-07-{day:02d}",
            "openPrice": "10",
            "highPrice": "12",
            "lowPrice": "9",
            "closePrice": "11",
            "accumulatedTradingVolume": "1,000",
        }
        for day in range(1, 21)
    ]
    valid[0]["localTradedAt"] = "bad"
    payloads = iter([valid, []])
    calls = []
    monkeypatch.setattr(
        fetcher,
        "requests_get_https",
        lambda url, **_kwargs: calls.append(url) or _Response(payload=next(payloads)),
    )
    rows = fetcher._fetch_kr_history_naver(
        "005930.KS", object(), start_date=date(2026, 7, 1), end_date=date(2026, 7, 31)
    )
    assert len(rows) == 19 and len(calls) == 2

    old_payload = [dict(valid[1], localTradedAt="2025-12-31") for _ in range(fetcher._KR_HISTORY_PAGE_SIZE)]
    monkeypatch.setattr(fetcher, "requests_get_https", lambda *_args, **_kwargs: _Response(payload=old_payload))
    assert (
        len(
            fetcher._fetch_kr_history_naver(
                "005930.KS", object(), start_date=date(2026, 1, 1), end_date=date(2026, 7, 1)
            )
        )
        == 20
    )


def test_yahoo_japan_parser_covers_token_pagination_and_value_shapes(monkeypatch):
    assert (
        fetcher._fetch_jp_history_yahoo_japan("", object(), start_date=date(2026, 1, 1), end_date=date(2026, 7, 1))
        == []
    )
    monkeypatch.setattr(fetcher, "requests_get_https", lambda *_args, **_kwargs: _Response(text="no token"))
    assert (
        fetcher._fetch_jp_history_yahoo_japan(
            "8035.T", object(), start_date=date(2026, 1, 1), end_date=date(2026, 7, 1)
        )
        == []
    )

    histories = [
        {
            "date": f"2026-07-{day:02d}",
            "values": [{"value": "10"}, "12", "9", "11", "1,000"],
        }
        for day in range(1, 21)
    ]
    histories[0]["date"] = "bad"
    api_payloads = iter(
        [
            {"response": {"history": {"histories": histories}}},
            {"response": {"history": {"histories": []}}},
        ]
    )
    calls = []

    def request(url, **_kwargs):
        calls.append(url)
        if len(calls) == 1:
            return _Response(text=r"jwtToken\":\"secret\"")
        return _Response(payload=next(api_payloads))

    monkeypatch.setattr(fetcher, "requests_get_https", request)
    rows = fetcher._fetch_jp_history_yahoo_japan(
        "8035.T", object(), start_date=date(2026, 7, 1), end_date=date(2026, 7, 31)
    )
    assert len(rows) == 19
    assert rows[0]["close"] == 11.0 and rows[0]["volume"] == 1000.0
    assert len(calls) == 3


def test_yfinance_history_covers_import_error_non_rate_empty_and_valid_rows(monkeypatch):
    monkeypatch.setattr(fetcher, "get_yf_rate_limit_status", lambda: {"active": False, "remaining_sec": 0.0})
    real_import = builtins.__import__

    def guarded_import(name, *args, **kwargs):
        if name == "yfinance":
            raise ImportError("not installed")
        return real_import(name, *args, **kwargs)

    with monkeypatch.context() as scoped:
        scoped.setattr(builtins, "__import__", guarded_import)
        assert (
            fetcher._fetch_yfinance_history_rows(
                "1.T", object(), start_date=date(2026, 1, 1), end_date=date(2026, 1, 2)
            )
            == []
        )

    class RaisingTicker:
        def history(self, **_kwargs):
            raise RuntimeError("transport")

    monkeypatch.setitem(sys.modules, "yfinance", SimpleNamespace(Ticker=lambda *_args, **_kwargs: RaisingTicker()))
    monkeypatch.setattr(fetcher, "is_yf_rate_limit_error", lambda _exc: False)
    with pytest.raises(RuntimeError, match="transport"):
        fetcher._fetch_yfinance_history_rows("1.T", object(), start_date=date(2026, 1, 1), end_date=date(2026, 1, 2))

    class EmptyTicker:
        def history(self, **_kwargs):
            return pd.DataFrame()

    monkeypatch.setitem(sys.modules, "yfinance", SimpleNamespace(Ticker=lambda *_args, **_kwargs: EmptyTicker()))
    assert (
        fetcher._fetch_yfinance_history_rows("1.T", object(), start_date=date(2026, 1, 1), end_date=date(2026, 1, 2))
        == []
    )

    frame = pd.DataFrame(
        {"Open": [10, 20], "High": [12, 22], "Low": [9, 19], "Close": [11, 21], "Volume": [100, 200]},
        index=pd.Index(["2026-01-01", "bad-index"]),
    )
    monkeypatch.setitem(
        sys.modules,
        "yfinance",
        SimpleNamespace(Ticker=lambda *_args, **_kwargs: SimpleNamespace(history=lambda **_kwargs: frame)),
    )
    rows = fetcher._fetch_yfinance_history_rows("1.T", object(), start_date=date(2026, 1, 1), end_date=date(2026, 1, 2))
    assert rows == [{"date": "2026-01-01", "open": 10.0, "high": 12.0, "low": 9.0, "close": 11.0, "volume": 100.0}]


def test_tencent_hk_parser_prefers_qfq_and_skips_short_invalid_rows(monkeypatch):
    payload = {
        "data": {
            "hk00700": {
                "qfqday": [
                    ["short"],
                    ["bad", "1", "2", "3", "0", "10"],
                    ["2026-07-14", "10", "11", "12", "9", "1,000"],
                ],
                "day": [["2026-07-13", "1", "1", "1", "1", "1"]],
            }
        }
    }
    urls = []
    monkeypatch.setattr(
        fetcher,
        "requests_get_https",
        lambda url, **_kwargs: urls.append(url) or _Response(payload=payload),
    )
    rows = fetcher._fetch_hk_history_tencent(
        "0700.HK",
        object(),
        start_date=date(2026, 1, 1),
        end_date=date(2026, 7, 14),
        target_rows=1000,
    )
    assert rows == [{"date": "2026-07-14", "open": 10.0, "close": 11.0, "high": 12.0, "low": 9.0, "volume": 1000.0}]
    assert ",800,qfq" in urls[0]


def test_market_router_unsupported_and_fetch_single_empty_known_unknown_errors(monkeypatch):
    assert fetcher._fetch_market_history_rows(
        "ABC",
        object(),
        start_date=date(2026, 1, 1),
        end_date=date(2026, 1, 2),
        target_rows=5,
    ) == ([], "unsupported")

    monkeypatch.setattr(fetcher, "_resolve_period_window", lambda _period: (date(2026, 1, 1), date(2026, 1, 2), 5))
    monkeypatch.setattr(fetcher, "_fetch_market_history_rows", lambda *_args, **_kwargs: ([], "test"))
    assert fetcher.fetch_single_kline("Unknown", "abc", session=object()) is None

    monkeypatch.setattr(
        fetcher,
        "_fetch_market_history_rows",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(ValueError("bad payload")),
    )
    monkeypatch.setattr(fetcher, "is_yf_rate_limit_error", lambda _exc: False)
    assert fetcher.fetch_single_kline("Unknown", "abc", session=object()) is None

    monkeypatch.setattr(
        fetcher,
        "_fetch_market_history_rows",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(KeyboardInterrupt()),
    )
    with pytest.raises(KeyboardInterrupt):
        fetcher.fetch_single_kline("Unknown", "abc", session=object())


def test_fetch_all_single_filter_failures_sorting_and_unexpected_error(monkeypatch):
    monkeypatch.setattr(fetcher.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(fetcher, "build_yf_session", lambda: object())
    monkeypatch.setattr(fetcher, "_get_asian_source_tickers", lambda: {"Known": "1.T"})
    monkeypatch.setattr(fetcher, "fetch_single_kline", lambda name, ticker, **_kwargs: _row(ticker, name=name))
    assert fetcher.fetch_all_asian_klines(single_ticker="missing.T") == []
    assert [row["ticker"] for row in fetcher.fetch_all_asian_klines(single_ticker="1.T")] == ["1.T"]

    monkeypatch.setattr(fetcher, "filter_asian_tickers", lambda _market=None: {})
    assert fetcher.fetch_all_asian_klines() == []

    monkeypatch.setattr(
        fetcher,
        "filter_asian_tickers",
        lambda _market=None: {"Zulu": "Z.T", "Empty": "E.T", "Rate": "R.T", "KnownError": "K.T"},
    )
    monkeypatch.setattr(fetcher, "is_yf_rate_limit_error", lambda exc: "rate" in str(exc))

    def fetch(name, ticker, **_kwargs):
        if ticker == "E.T":
            return None
        if ticker == "R.T":
            raise RuntimeError("rate limited")
        if ticker == "K.T":
            raise OSError("network")
        return _row(ticker, market="Z", name=name)

    monkeypatch.setattr(fetcher, "fetch_single_kline", fetch)
    assert [row["ticker"] for row in fetcher.fetch_all_asian_klines()] == ["Z.T"]

    monkeypatch.setattr(fetcher, "filter_asian_tickers", lambda _market=None: {"Boom": "B.T"})
    monkeypatch.setattr(
        fetcher, "fetch_single_kline", lambda *_args, **_kwargs: (_ for _ in ()).throw(KeyboardInterrupt())
    )
    with pytest.raises(KeyboardInterrupt):
        fetcher.fetch_all_asian_klines()


def test_sync_empty_target_and_empty_fetch_cache_errors(monkeypatch):
    monkeypatch.setattr(fetcher, "_build_sync_target_map", lambda **_kwargs: {})
    success, message, report = fetcher.sync_asian_kline_cache()
    assert success is False and report["target_count"] == 0 and "没有找到" in message

    monkeypatch.setattr(fetcher, "_build_sync_target_map", lambda **_kwargs: {"A": "A.T", "B": "B.T"})
    monkeypatch.setattr(fetcher, "_resolve_cache_output_dir", lambda output: output or "cache")
    monkeypatch.setattr(fetcher, "fetch_all_asian_klines", lambda **_kwargs: [])
    monkeypatch.setattr(
        fetcher,
        "_load_cached_row_map",
        lambda _output: (_ for _ in ()).throw(OSError("corrupt")),
    )
    success, message, report = fetcher.sync_asian_kline_cache(output_dir="cache")
    assert success is False and report["missing"] == ["A.T", "B.T"] and "全量拉取失败" in message


def test_sync_rescue_rate_known_stale_deadline_and_cache_load_failure(monkeypatch):
    target = {"A": "A.T", "B": "B.T", "C": "C.T"}
    monkeypatch.setattr(fetcher, "_build_sync_target_map", lambda **_kwargs: target)
    monkeypatch.setattr(fetcher, "_resolve_cache_output_dir", lambda output: output or "cache")
    monkeypatch.setattr(
        fetcher, "fetch_all_asian_klines", lambda **_kwargs: [_row("A.T", day="2026-07-14", market="M")]
    )
    monkeypatch.setattr(fetcher, "build_yf_session", lambda: object())
    monkeypatch.setattr(fetcher, "is_yf_rate_limit_error", lambda exc: "rate" in str(exc))

    def rescue(_name, ticker, **_kwargs):
        if ticker == "B.T":
            raise RuntimeError("rate limited")
        raise ValueError("bad payload")

    monkeypatch.setattr(fetcher, "fetch_single_kline", rescue)
    monkeypatch.setattr(
        fetcher,
        "_load_cached_row_map",
        lambda _output: (_ for _ in ()).throw(PermissionError("locked")),
    )
    success, _message, report = fetcher.sync_asian_kline_cache(output_dir="cache")
    assert success is False and report["missing"] == ["B.T", "C.T"]

    checks = []
    monkeypatch.setattr(fetcher, "_deadline_from_time_budget", lambda _budget: 1.0)
    monkeypatch.setattr(fetcher, "_remaining_time_budget", lambda _deadline: 1.0)

    def deadline_exceeded(_deadline, checkpoint=None):
        if checkpoint:
            checkpoint()
        checks.append(True)
        return len(checks) >= 2

    monkeypatch.setattr(fetcher, "_deadline_exceeded", deadline_exceeded)
    monkeypatch.setattr(
        fetcher, "_time_budget_exhausted_result", lambda *_args: (False, "exhausted", {"time_budget_exhausted": True})
    )
    result = fetcher.sync_asian_kline_cache(output_dir="cache", cancellation_checkpoint=lambda: None)
    assert result[2]["missing"] == ["B.T", "C.T"]

    monkeypatch.setattr(fetcher, "_deadline_exceeded", lambda _deadline, _checkpoint=None: True)
    result = fetcher.sync_asian_kline_cache(output_dir="cache", cancellation_checkpoint=lambda: None)
    assert result[2]["time_budget_exhausted"] is True


def test_sync_rejects_stale_rescue_and_reraises_unexpected(monkeypatch):
    target = {"A": "A.T", "B": "B.T"}
    monkeypatch.setattr(fetcher, "_build_sync_target_map", lambda **_kwargs: target)
    monkeypatch.setattr(fetcher, "_resolve_cache_output_dir", lambda output: output or "cache")
    monkeypatch.setattr(
        fetcher, "fetch_all_asian_klines", lambda **_kwargs: [_row("A.T", day="2026-07-14", market="M")]
    )
    monkeypatch.setattr(fetcher, "build_yf_session", lambda: object())
    monkeypatch.setattr(
        fetcher, "fetch_single_kline", lambda *_args, **_kwargs: _row("B.T", day="2026-07-13", market="M")
    )
    monkeypatch.setattr(fetcher, "_load_cached_row_map", lambda _output: {})
    success, _message, report = fetcher.sync_asian_kline_cache(output_dir="cache")
    assert success is False and report["stale"] == ["B.T"] and report["missing"] == ["B.T"]

    monkeypatch.setattr(
        fetcher, "fetch_single_kline", lambda *_args, **_kwargs: (_ for _ in ()).throw(KeyboardInterrupt())
    )
    with pytest.raises(KeyboardInterrupt):
        fetcher.sync_asian_kline_cache(output_dir="cache")


def test_main_dry_run_strict_success_failure_and_normal_save(monkeypatch, capsys):
    monkeypatch.setattr(fetcher, "filter_asian_tickers", lambda _market=None: {"Company": "1.T"})
    monkeypatch.setattr(fetcher, "_find_track", lambda _ticker: "Track")
    monkeypatch.setattr(sys, "argv", ["asian_kline_fetcher.py", "--dry-run", "--market", "JP"])
    fetcher.main()
    assert "1.T" in capsys.readouterr().out

    strict_calls = []
    monkeypatch.setattr(
        fetcher,
        "sync_asian_kline_cache",
        lambda **kwargs: strict_calls.append(kwargs) or (True, "done", {}),
    )
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "asian_kline_fetcher.py",
            "--strict-sync",
            "--ticker",
            "1.T",
            "--workers",
            "2",
            "--period",
            "2y",
            "--output-dir",
            "out",
            "--time-budget-sec",
            "3",
        ],
    )
    fetcher.main()
    assert strict_calls[0] == {
        "market_filter": None,
        "single_ticker": "1.T",
        "max_workers": 2,
        "period": "2y",
        "output_dir": "out",
        "time_budget_sec": 3.0,
    }

    monkeypatch.setattr(fetcher, "sync_asian_kline_cache", lambda **_kwargs: (False, "failed", {}))
    with pytest.raises(SystemExit) as exit_info:
        fetcher.main()
    assert exit_info.value.code == 1

    saved = []
    monkeypatch.setattr(fetcher, "fetch_all_asian_klines", lambda **_kwargs: [_row("1.T")])
    monkeypatch.setattr(fetcher, "save_kline_data", lambda data, output: saved.append((data, output)))
    monkeypatch.setattr(sys, "argv", ["asian_kline_fetcher.py", "--output-dir", "out"])
    fetcher.main()
    assert saved[0][1] == "out"

    monkeypatch.setattr(fetcher, "fetch_all_asian_klines", lambda **_kwargs: [])
    fetcher.main()
