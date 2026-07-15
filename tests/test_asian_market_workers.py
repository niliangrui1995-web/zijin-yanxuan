# -*- coding: utf-8 -*-

import ast
from pathlib import Path

import pandas as pd
from PyQt6.QtTest import QSignalSpy
from yfinance.exceptions import YFRateLimitError

from ui.services import asian_market_runtime_service as runtime_service
from ui.tabs import asian_market_workers as workers

_REPO_ROOT = Path(__file__).resolve().parents[1]


class _FakeResponse:
    def __init__(self, *, text="", data=None, status_code=200):
        self.text = text
        self._data = data
        self.status_code = status_code

    def json(self):
        return self._data


class _BadJsonResponse(_FakeResponse):
    def json(self):
        raise ValueError("Expecting value: line 1 column 1 (char 0)")


class _FakeSession:
    def __init__(self, response):
        self.response = response
        self.urls = []

    def get(self, url, **kwargs):
        self.urls.append(url)
        return self.response


class _JapanTickerStub:
    def __init__(self, code, session=None):
        self.code = code
        self.session = session

    @property
    def fast_info(self):
        return {
            "lastPrice": 26460.0,
            "open": 26850.0,
            "dayHigh": 27690.0,
            "dayLow": 26340.0,
            "lastVolume": 1760500.0,
            "currency": "JPY",
            "previousClose": 27450.0,
            "regularMarketPreviousClose": 26720.0,
        }


def test_asian_market_worker_module_is_only_thread_orchestration_and_cache_state():
    worker_path = _REPO_ROOT / "ui" / "tabs" / "asian_market_workers.py"
    tree = ast.parse(worker_path.read_text(encoding="utf-8"))

    imported_modules = {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    }
    imported_from = {
        node.module or ""
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
    }
    forbidden_defs = {
        "_load_realtime_json",
        "_parse_jp_realtime_page",
        "_parse_jp_yahoo_pe_from_html",
        "_fetch_tw_realtime_quote",
        "_fetch_hk_realtime_quote",
        "_fetch_kr_realtime_quote",
        "_fetch_jp_realtime_quote",
        "_fetch_yfinance_realtime_quote",
        "_fetch_twse_pe",
        "_fetch_tpex_pe",
        "_fetch_kr_naver_pe",
        "_fetch_jp_yahoo_pe",
        "_fetch_jp_kabutan_pe",
    }
    defined_functions = {
        node.name
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }

    assert imported_modules.isdisjoint({"html", "importlib", "json", "re", "requests"})
    assert "app.services.asian_market_http_service" not in imported_from
    assert forbidden_defs.isdisjoint(defined_functions)
    assert not any(
        isinstance(node, ast.Attribute)
        and node.attr in {"json", "text", "content", "fast_info", "history", "Ticker"}
        for node in ast.walk(tree)
    )


def test_asian_quote_provider_boundary_exists_without_qt_dependencies():
    provider_path = _REPO_ROOT / "infra" / "market_data" / "asian_realtime_provider.py"
    facade_path = _REPO_ROOT / "app" / "services" / "asian_market_quote_service.py"

    assert provider_path.is_file()
    assert facade_path.is_file()

    provider_source = provider_path.read_text(encoding="utf-8")
    facade_source = facade_path.read_text(encoding="utf-8")
    provider_tree = ast.parse(provider_source)
    imported_roots = {
        (node.module or "").split(".", 1)[0]
        for node in ast.walk(provider_tree)
        if isinstance(node, ast.ImportFrom)
    } | {
        alias.name.split(".", 1)[0]
        for node in ast.walk(provider_tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    }
    assert imported_roots.isdisjoint({"PyQt6", "app", "ui"})
    assert "asian_realtime_provider" in facade_source


def test_save_global_asian_rt_cache_delegates_serialization_to_cache_service(monkeypatch):
    payload = {
        "2330.TW": {
            "close": 2080.0,
            "pct": 1.25,
            "df_today": object(),
        }
    }
    captured = []
    monkeypatch.setattr(workers, "GLOBAL_ASIAN_RT_CACHE", payload)
    monkeypatch.setattr(
        workers,
        "write_realtime_quote_cache",
        lambda quotes: captured.append(quotes),
        raising=False,
    )

    workers.save_global_asian_rt_cache()

    assert captured == [payload]


def test_runtime_service_save_rt_cache_delegates_serialization_to_cache_service(monkeypatch):
    payload = {"2330.TW": {"close": 2080.0, "pct": 1.25}}
    captured = []
    monkeypatch.setattr(workers, "GLOBAL_ASIAN_RT_CACHE", payload)
    monkeypatch.setattr(runtime_service, "RT_JSON_CACHE", "rt-cache.json")
    monkeypatch.setattr(
        runtime_service,
        "write_realtime_quote_cache",
        lambda quotes, path: captured.append((quotes, path)),
    )

    runtime_service.AsianMarketRuntimeService._save_rt_cache()

    assert captured == [(payload, "rt-cache.json")]


def test_asian_display_modules_do_not_read_business_files_directly():
    forbidden_imports = {
        "ui/kline_window_asian.py": {"json", "os"},
        "ui/tabs/asian_market_meta.py": {"pathlib", "re"},
    }
    for relative_path, forbidden_roots in forbidden_imports.items():
        tree = ast.parse((_REPO_ROOT / relative_path).read_text(encoding="utf-8"))
        imported_roots = {
            alias.name.split(".", 1)[0]
            for node in ast.walk(tree)
            if isinstance(node, ast.Import)
            for alias in node.names
        } | {
            (node.module or "").split(".", 1)[0]
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom)
        }
        direct_open_calls = [
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and (
                (isinstance(node.func, ast.Name) and node.func.id == "open")
                or (isinstance(node.func, ast.Attribute) and node.func.attr == "open")
            )
        ]

        assert imported_roots.isdisjoint(forbidden_roots), relative_path
        assert direct_open_calls == [], relative_path


def test_asian_cache_service_selects_ticker_without_ui_file_io(tmp_path):
    from app.services.asian_market_cache_service import (
        load_cached_asian_stock,
        write_json_cache,
    )

    cache_path = tmp_path / "asian.json"
    write_json_cache(
        str(cache_path),
        {
            "stocks": [
                {"ticker": "2330.TW", "klines": [{"date": "2026-07-10"}]},
                {"ticker": "0522.HK", "klines": []},
            ]
        },
    )

    assert load_cached_asian_stock(str(cache_path), "2330.TW") == {
        "ticker": "2330.TW",
        "klines": [{"date": "2026-07-10"}],
    }
    assert load_cached_asian_stock(str(cache_path), "missing.T") is None


def test_asian_metadata_repository_parses_roles_and_exclusions(tmp_path):
    from infra.storage.asian_market_metadata import read_pipeline_industry_roles

    source_path = tmp_path / "industry_dict.py"
    source_path.write_text(
        '\n'.join(
            (
                '"7735.T": "SCREEN",  # 日本PCB设备（头部｜PCB直接成像）',
                '"6594.T": "Nidec",  # 日本设备（排除）',
                '"000001.SZ": "CN",  # A股（不应读取）',
            )
        ),
        encoding="utf-8",
    )

    assert read_pipeline_industry_roles(source_path, excluded_tickers={"6594.T"}) == {
        "7735.T": "头部｜PCB直接成像"
    }


def _jp_current_page_html():
    return r"""
    <section class="_BasePriceBoard_1 _CommonPriceBoard_1 styles_DetailPage__priceBoard__x">
      <span class="_StyledNumber_1 _CommonPriceBoard__price_abc">
        <span class="_StyledNumber__item_1"><span class="_StyledNumber__value_1">5,731</span></span>
      </span>
    </section>
    <script>self.__next_f.push([1,"30:{\"detailData\":{\"indicators\":{\"previousPrice\":{\"value\":\"5,689\",\"updateDateMeta\":\"2026-04-27\"},\"openPrice\":{\"value\":\"5,747\",\"updateDateMeta\":\"2026-04-28T09:00:00+09:00\"},\"highPrice\":{\"value\":\"5,751\",\"updateDateMeta\":\"2026-04-28T09:00:00+09:00\"},\"lowPrice\":{\"value\":\"5,697\",\"updateDateMeta\":\"2026-04-28T09:04:00+09:00\"},\"volume\":{\"value\":\"248,800\",\"updateDateMeta\":\"2026-04-28T10:42:00+09:00\"}}}}"])</script>
    """


def test_fetch_updates_timeout_budget_fails_fast_before_legacy_80s_limit():
    assert workers._FETCH_UPDATES_TIMEOUT_SEC <= 45
    assert workers._OPTIONAL_NETWORK_MIN_REMAINING_SEC >= 25


def test_fetch_asian_realtime_quote_skips_yfinance_fallback_during_cooldown(monkeypatch):
    monkeypatch.setattr(workers, "_fetch_tw_realtime_quote", lambda code, session: None)
    monkeypatch.setattr(
        workers,
        "get_yf_rate_limit_status",
        lambda: {
            "active": True,
            "remaining_sec": 180.0,
            "reason": "Too Many Requests",
            "until_ts": 999.0,
        },
    )

    calls = {"yf": 0}

    def _unexpected_fallback(*args, **kwargs):
        calls["yf"] += 1
        raise AssertionError("cooldown active should skip yfinance fallback")

    monkeypatch.setattr(workers, "_fetch_yfinance_realtime_quote", _unexpected_fallback)

    quote = workers.fetch_asian_realtime_quote("2330.TW", yf_session=object())

    assert quote is None
    assert calls["yf"] == 0


def test_fetch_asian_realtime_quote_skips_yfinance_fallback_when_optional_network_disabled(monkeypatch):
    monkeypatch.setattr(workers, "_fetch_tw_realtime_quote", lambda code, session: None)
    monkeypatch.setattr(
        workers,
        "get_yf_rate_limit_status",
        lambda: {
            "active": False,
            "remaining_sec": 0.0,
            "reason": "",
            "until_ts": 0.0,
        },
    )
    monkeypatch.setattr(
        workers,
        "_fetch_yfinance_realtime_quote",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("optional fallback should wait")),
    )

    quote = workers.fetch_asian_realtime_quote("2330.TW", yf_session=object(), allow_yfinance_fallback=False)

    assert quote is None


def test_fetch_asian_realtime_quote_bad_direct_payload_skips_yfinance_fallback(monkeypatch):
    session = _FakeSession(_BadJsonResponse(text=""))
    monkeypatch.setattr(
        workers,
        "get_yf_rate_limit_status",
        lambda: {
            "active": False,
            "remaining_sec": 0.0,
            "reason": "",
            "until_ts": 0.0,
        },
    )
    monkeypatch.setattr(
        workers,
        "_fetch_yfinance_realtime_quote",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("bad direct payload should not fall back")),
    )

    quote = workers.fetch_asian_realtime_quote("3017.TW", yf_session=session)

    assert quote is None
    assert session.urls == [
        "https://mis.twse.com.tw/stock/api/getStockInfo.jsp?ex_ch=tse_3017.tw&json=1&delay=0"
    ]


def test_fetch_asian_realtime_quote_uses_tencent_for_hk(monkeypatch):
    fields = [""] * 70
    fields[0] = "100"
    fields[1] = "ASMPT"
    fields[2] = "00522"
    fields[3] = "169.300"
    fields[4] = "165.800"
    fields[5] = "163.600"
    fields[6] = "435472.0"
    fields[30] = "2026/04/28 09:42:02"
    fields[33] = "169.600"
    fields[34] = "163.600"
    session = _FakeSession(_FakeResponse(text=f'v_hk00522="{"~".join(fields)}";'))

    monkeypatch.setattr(
        workers,
        "_fetch_yfinance_realtime_quote",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("HK should use Tencent first")),
    )

    quote = workers.fetch_asian_realtime_quote("0522.HK", yf_session=session)

    assert quote is not None
    assert quote["date"] == "2026-04-28"
    assert quote["close"] == 169.3
    assert quote["previous_close"] == 165.8
    assert quote["source"] == "tencent_hk"
    assert session.urls == ["https://qt.gtimg.cn/q=hk00522"]


def test_fetch_jp_realtime_quote_parses_current_yahoo_japan_page_shape():
    session = _FakeSession(_FakeResponse(text=_jp_current_page_html()))

    quote = workers._fetch_jp_realtime_quote("5201.T", session)

    assert quote is not None
    assert quote["date"] == "2026-04-28"
    assert quote["close"] == 5731.0
    assert quote["open"] == 5747.0
    assert quote["high"] == 5751.0
    assert quote["low"] == 5697.0
    assert quote["volume"] == 248800.0
    assert quote["previous_close"] == 5689.0
    assert quote["source"] == "yj_finance_page"


def test_fetch_kr_realtime_quote_preserves_signed_falling_diff():
    session = _FakeSession(
        _FakeResponse(
            data={
                "datas": [
                    {
                        "localTradedAt": "2026-06-05T15:30:00+09:00",
                        "closePriceRaw": "2070000",
                        "compareToPreviousClosePriceRaw": "-228000",
                        "fluctuationsRatioRaw": "-9.92",
                        "compareToPreviousPrice": {"code": "5", "name": "FALLING"},
                        "openPriceRaw": "2142000",
                        "highPriceRaw": "2188000",
                        "lowPriceRaw": "2070000",
                        "accumulatedTradingVolumeRaw": "5358995",
                        "currencyType": {"code": "KRW"},
                    }
                ]
            }
        )
    )

    quote = workers._fetch_kr_realtime_quote("000660.KS", session)

    assert quote is not None
    assert quote["date"] == "2026-06-05"
    assert quote["close"] == 2070000.0
    assert quote["previous_close"] == 2298000.0
    assert quote["source"] == "naver_realtime"


def test_fetch_jp_realtime_quote_retries_with_plain_requests_after_session_error(monkeypatch):
    session = _FakeSession(_FakeResponse(text="temporarily unavailable", status_code=500))
    monkeypatch.setattr(
        workers.requests,
        "get",
        lambda *args, **kwargs: _FakeResponse(text=_jp_current_page_html(), status_code=200),
    )

    quote = workers._fetch_jp_realtime_quote("5201.T", session)

    assert quote is not None
    assert quote["date"] == "2026-04-28"
    assert quote["close"] == 5731.0
    assert quote["source"] == "yj_finance_page"


def test_fetch_single_code_returns_none_when_yahoo_rate_limited(monkeypatch):
    worker = workers.AsianMarketWorker(["2330.TW"])

    class _Ticker:
        def __init__(self, code, session=None):
            self.code = code
            self.session = session

        @property
        def fast_info(self):
            raise YFRateLimitError()

    marks = []
    monkeypatch.setattr(
        workers,
        "get_yf_rate_limit_status",
        lambda: {
            "active": False,
            "remaining_sec": 0.0,
            "reason": "",
            "until_ts": 0.0,
        },
    )
    monkeypatch.setattr(workers.yf, "Ticker", _Ticker)
    monkeypatch.setattr(workers, "fetch_asian_realtime_quote", lambda code, yf_session=None, **kwargs: None)
    monkeypatch.setattr(workers, "is_yf_rate_limit_error", lambda exc: isinstance(exc, YFRateLimitError))
    monkeypatch.setattr(
        workers,
        "mark_yf_rate_limited",
        lambda exc=None, cooldown_sec=None: marks.append(str(exc)) or 60.0,
    )

    code, payload = worker._fetch_single_code("2330.TW", object(), object())

    assert code == "2330.TW"
    assert payload is None
    assert marks == ["Too Many Requests. Rate limited. Try after a while."]


def test_fetch_single_code_uses_exchange_quote_during_yahoo_cooldown(monkeypatch):
    monkeypatch.setattr(
        workers,
        "get_yf_rate_limit_status",
        lambda: {
            "active": True,
            "remaining_sec": 180.0,
            "reason": "Too Many Requests",
            "until_ts": 999.0,
        },
    )
    monkeypatch.setattr(
        workers,
        "fetch_asian_realtime_quote",
        lambda code, yf_session=None, **kwargs: {
            "date": "2026-04-23",
            "close": 2120.0,
            "open": 2090.0,
            "high": 2125.0,
            "low": 2085.0,
            "volume": 12345.0,
            "previous_close": 2050.0,
            "currency": "TWD",
            "source": "twse_mis",
            "quote_quality": "last",
        },
    )
    monkeypatch.setattr(
        workers.yf,
        "Ticker",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("exchange quote should not need Yahoo")),
    )

    worker = workers.AsianMarketWorker(["2330.TW"])
    code, payload = worker._fetch_single_code("2330.TW", object(), object())

    assert code == "2330.TW"
    assert payload is not None
    assert payload["close"] == 2120.0
    assert round(payload["pct"], 4) == round((2120.0 / 2050.0 - 1.0) * 100.0, 4)
    assert payload["source"] == "twse_mis"


def test_fetch_single_code_keeps_exchange_quote_when_pe_rate_limited(monkeypatch):
    status = {
        "active": False,
        "remaining_sec": 0.0,
        "reason": "",
        "until_ts": 0.0,
    }
    marks = []

    class _Ticker:
        @property
        def info(self):
            raise YFRateLimitError()

    monkeypatch.setattr(workers, "get_yf_rate_limit_status", lambda: status)
    monkeypatch.setattr(workers, "is_yf_rate_limit_error", lambda exc: isinstance(exc, YFRateLimitError))
    monkeypatch.setattr(
        workers,
        "mark_yf_rate_limited",
        lambda exc=None, cooldown_sec=None: marks.append(str(exc)) or 900.0,
    )
    monkeypatch.setattr(
        workers,
        "fetch_asian_realtime_quote",
        lambda code, yf_session=None, **kwargs: {
            "date": "2026-04-23",
            "close": 1240000.0,
            "open": 1220000.0,
            "high": 1267000.0,
            "low": 1218000.0,
            "volume": 2260982.0,
            "previous_close": 1223000.0,
            "currency": "KRW",
            "source": "naver_realtime",
            "quote_quality": "last",
        },
    )
    monkeypatch.setattr(workers.yf, "Ticker", lambda *args, **kwargs: _Ticker())
    monkeypatch.setattr(
        workers.MarketCalendar,
        "is_quote_refresh_time",
        classmethod(lambda cls, market="CN": False),
    )
    monkeypatch.setattr(workers, "GLOBAL_ASIAN_RT_CACHE", {})

    worker = workers.AsianMarketWorker(["000660.KS"])
    code, payload = worker._fetch_single_code("000660.KS", object(), object())

    assert code == "000660.KS"
    assert payload is not None
    assert payload["close"] == 1240000.0
    assert payload["pe"] is None
    assert marks == ["Too Many Requests. Rate limited. Try after a while."]


def test_fetch_updates_does_not_short_circuit_on_yahoo_cooldown(monkeypatch):
    monkeypatch.setattr(
        workers,
        "get_yf_rate_limit_status",
        lambda: {
            "active": True,
            "remaining_sec": 180.0,
            "reason": "Too Many Requests",
            "until_ts": 999.0,
        },
    )
    monkeypatch.setattr(workers, "build_yf_session", lambda *args, **kwargs: object())

    worker = workers.AsianMarketWorker(["2330.TW"])
    monkeypatch.setattr(
        worker,
        "_fetch_single_code",
        lambda code, yf_session, info_session: (
            code,
            {
                "date": "2026-04-23",
                "close": 2120.0,
                "previous_close": 2050.0,
            },
        ),
    )

    updates = worker._fetch_updates()

    assert updates["2330.TW"]["close"] == 2120.0


def test_fetch_updates_prioritizes_direct_exchange_sources(monkeypatch):
    monkeypatch.setattr(workers, "build_yf_session", lambda *args, **kwargs: object())
    monkeypatch.setattr(workers, "_YF_FETCH_MAX_WORKERS", 1)
    calls = []
    worker = workers.AsianMarketWorker(["5201.T", "0522.HK", "000660.KS", "3711.TW"])

    def _fake_fetch_single_code(code, yf_session, info_session):
        calls.append(code)
        return code, {"date": "2026-04-28", "close": 1.0, "previous_close": 1.0}

    monkeypatch.setattr(worker, "_fetch_single_code", _fake_fetch_single_code)

    worker._fetch_updates()

    assert calls[:3] == ["0522.HK", "000660.KS", "3711.TW"]


def test_fetch_updates_skips_markets_in_short_backoff(monkeypatch):
    monkeypatch.setattr(workers, "build_yf_session", lambda *args, **kwargs: object())
    monkeypatch.setattr(workers, "_YF_FETCH_MAX_WORKERS", 1)
    calls = []
    worker = workers.AsianMarketWorker(["5201.T", "0522.HK"])
    worker._market_backoff_until["T"] = workers.time.time() + 60

    def _fake_fetch_single_code(code, yf_session, info_session):
        calls.append(code)
        return code, {"date": "2026-04-28", "close": 1.0, "previous_close": 1.0}

    monkeypatch.setattr(worker, "_fetch_single_code", _fake_fetch_single_code)

    updates = worker._fetch_updates()

    assert calls == ["0522.HK"]
    assert list(updates) == ["0522.HK"]


def test_auto_fetch_updates_skips_closed_markets_before_submitting(monkeypatch):
    monkeypatch.setattr(workers, "build_yf_session", lambda *args, **kwargs: object())
    monkeypatch.setattr(workers, "_YF_FETCH_MAX_WORKERS", 1)
    monkeypatch.setattr(
        workers.MarketCalendar,
        "is_quote_refresh_time",
        classmethod(lambda cls, market="CN": market == "HK"),
    )
    calls = []
    worker = workers.AsianMarketWorker(["5201.T", "0522.HK", "000660.KS"])

    def _fake_fetch_single_code(code, yf_session, info_session):
        calls.append(code)
        return code, {"date": "2026-04-28", "close": 1.0, "previous_close": 1.0}

    monkeypatch.setattr(worker, "_fetch_single_code", _fake_fetch_single_code)

    updates = worker._fetch_updates(open_markets_only=True)

    assert calls == ["0522.HK"]
    assert list(updates) == ["0522.HK"]


def test_fetch_updates_skips_only_codes_in_source_payload_backoff(monkeypatch):
    monkeypatch.setattr(workers, "build_yf_session", lambda *args, **kwargs: object())
    monkeypatch.setattr(workers, "_YF_FETCH_MAX_WORKERS", 1)
    now_ts = 1_000.0
    monkeypatch.setattr(workers.time, "time", lambda: now_ts)
    calls = []
    worker = workers.AsianMarketWorker(["3017.TW", "3711.TW", "0522.HK"])
    worker._mark_code_backoff("3017.TW", now_ts=now_ts)

    def _fake_fetch_single_code(code, yf_session, info_session):
        calls.append(code)
        return code, {"date": "2026-06-26", "close": 1.0, "previous_close": 1.0}

    monkeypatch.setattr(worker, "_fetch_single_code", _fake_fetch_single_code)

    updates = worker._fetch_updates()

    assert calls == ["0522.HK", "3711.TW"]
    assert list(updates) == ["0522.HK", "3711.TW"]


def test_fetch_single_code_bad_direct_payload_marks_code_backoff(monkeypatch):
    worker = workers.AsianMarketWorker(["3017.TW"])
    monkeypatch.setattr(
        workers,
        "fetch_asian_realtime_quote",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            workers.AsianRealtimePayloadError("twse_mis returned empty body")
        ),
    )
    monkeypatch.setattr(
        worker,
        "_fetch_yahoo_enrichment",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("bad direct payload should stop this ticket")),
    )

    code, payload = worker._fetch_single_code("3017.TW", object(), object())

    assert code == "3017.TW"
    assert payload is None
    assert worker._is_code_backoff_active("3017.TW")
    assert worker._source_payload_degraded() is True


def test_timeout_market_backoff_survives_next_short_retry_window(monkeypatch):
    now_ts = 1_000.0
    monkeypatch.setattr(workers.time, "time", lambda: now_ts)
    worker = workers.AsianMarketWorker(["5201.T"])

    worker._mark_market_backoff("T")

    assert worker._is_market_backoff_active("5201.T", now_ts=now_ts + 10 * 60)


def test_timeout_cycle_backoff_stops_next_auto_poll(monkeypatch):
    now_ts = 1_000.0
    monkeypatch.setattr(workers.time, "time", lambda: now_ts)
    monkeypatch.setattr(workers, "is_asian_quote_refresh_time", lambda codes: True)
    monkeypatch.setattr(
        workers,
        "get_yf_rate_limit_status",
        lambda: {
            "active": False,
            "remaining_sec": 0.0,
            "reason": "",
            "until_ts": 0.0,
        },
    )
    worker = workers.AsianMarketWorker(["0522.HK", "5201.T"])
    sleep_calls = []

    worker._mark_timeout_backoff(now_ts=now_ts)
    monkeypatch.setattr(
        worker,
        "_fetch_updates",
        lambda: (_ for _ in ()).throw(AssertionError("cycle backoff should skip fetch")),
    )

    def _sleep(seconds):
        sleep_calls.append(seconds)
        worker._is_running = False
        return False

    monkeypatch.setattr(worker, "_sleep_with_break", _sleep)

    worker.run()

    assert sleep_calls == [30.0]


def test_run_filters_closed_markets_only_for_auto_cycles(monkeypatch):
    monkeypatch.setattr(workers, "is_asian_quote_refresh_time", lambda codes: True)
    monkeypatch.setattr(
        workers,
        "get_yf_rate_limit_status",
        lambda: {
            "active": False,
            "remaining_sec": 0.0,
            "reason": "",
            "until_ts": 0.0,
        },
    )
    worker = workers.AsianMarketWorker(["5201.T", "0522.HK"])
    calls = []

    def _fetch_updates(*, open_markets_only=False):
        calls.append(open_markets_only)
        return {}

    def _sleep(_seconds):
        worker._is_running = False
        return False

    monkeypatch.setattr(worker, "_fetch_updates", _fetch_updates)
    monkeypatch.setattr(worker, "_sleep_with_break", _sleep)

    worker.run()

    assert calls == [True]


def test_run_keeps_manual_refresh_full_universe(monkeypatch):
    monkeypatch.setattr(workers, "is_asian_quote_refresh_time", lambda codes: True)
    monkeypatch.setattr(
        workers,
        "get_yf_rate_limit_status",
        lambda: {
            "active": False,
            "remaining_sec": 0.0,
            "reason": "",
            "until_ts": 0.0,
        },
    )
    worker = workers.AsianMarketWorker(["5201.T", "0522.HK"])
    worker._manual_refresh_requested = True
    calls = []

    def _fetch_updates(*, open_markets_only=False):
        calls.append(open_markets_only)
        return {}

    def _sleep(_seconds):
        worker._is_running = False
        return False

    monkeypatch.setattr(worker, "_fetch_updates", _fetch_updates)
    monkeypatch.setattr(worker, "_sleep_with_break", _sleep)

    worker.run()

    assert calls == [False]


def test_timeout_auto_cycle_persists_cache_without_emitting_ui_update(monkeypatch):
    monkeypatch.setattr(workers, "is_asian_quote_refresh_time", lambda codes: True)
    monkeypatch.setattr(
        workers,
        "get_yf_rate_limit_status",
        lambda: {
            "active": False,
            "remaining_sec": 0.0,
            "reason": "",
            "until_ts": 0.0,
        },
    )
    saved = []
    monkeypatch.setattr(workers, "save_global_asian_rt_cache", lambda: saved.append(True))
    worker = workers.AsianMarketWorker(["0522.HK"])
    result_spy = QSignalSpy(worker.result_ready)
    progress_spy = QSignalSpy(worker.progress)

    def _fetch_updates(*, open_markets_only=False):
        worker._last_fetch_timed_out = True
        return {"0522.HK": {"close": 169.3}}

    def _sleep(_seconds):
        worker._is_running = False
        return False

    monkeypatch.setattr(worker, "_fetch_updates", _fetch_updates)
    monkeypatch.setattr(worker, "_sleep_with_break", _sleep)

    worker.run()

    assert saved == [True]
    assert len(result_spy) == 0
    assert any("deferred UI repaint" in args[0] for args in progress_spy)


def test_source_payload_degraded_auto_cycle_persists_cache_without_emitting_ui_update(monkeypatch):
    monkeypatch.setattr(workers, "is_asian_quote_refresh_time", lambda codes: True)
    monkeypatch.setattr(
        workers,
        "get_yf_rate_limit_status",
        lambda: {
            "active": False,
            "remaining_sec": 0.0,
            "reason": "",
            "until_ts": 0.0,
        },
    )
    saved = []
    monkeypatch.setattr(workers, "save_global_asian_rt_cache", lambda: saved.append(True))
    worker = workers.AsianMarketWorker(["3711.TW", "0522.HK"])
    result_spy = QSignalSpy(worker.result_ready)
    progress_spy = QSignalSpy(worker.progress)

    def _fetch_updates(*, open_markets_only=False):
        worker._mark_source_payload_degraded()
        return {"0522.HK": {"close": 169.3}}

    def _sleep(_seconds):
        worker._is_running = False
        return False

    monkeypatch.setattr(worker, "_fetch_updates", _fetch_updates)
    monkeypatch.setattr(worker, "_sleep_with_break", _sleep)

    worker.run()

    assert saved == [True]
    assert len(result_spy) == 0
    assert any("source payload degraded" in args[0] and "deferred UI repaint" in args[0] for args in progress_spy)


def test_timeout_manual_cycle_still_emits_ui_update(monkeypatch):
    monkeypatch.setattr(workers, "is_asian_quote_refresh_time", lambda codes: True)
    monkeypatch.setattr(
        workers,
        "get_yf_rate_limit_status",
        lambda: {
            "active": False,
            "remaining_sec": 0.0,
            "reason": "",
            "until_ts": 0.0,
        },
    )
    monkeypatch.setattr(workers, "save_global_asian_rt_cache", lambda: None)
    worker = workers.AsianMarketWorker(["0522.HK"])
    worker._manual_refresh_requested = True
    result_spy = QSignalSpy(worker.result_ready)

    def _fetch_updates(*, open_markets_only=False):
        worker._last_fetch_timed_out = True
        return {"0522.HK": {"close": 169.3}}

    def _sleep(_seconds):
        worker._is_running = False
        return False

    monkeypatch.setattr(worker, "_fetch_updates", _fetch_updates)
    monkeypatch.setattr(worker, "_sleep_with_break", _sleep)

    worker.run()

    assert len(result_spy) == 1
    assert result_spy[0][0]["0522.HK"]["close"] == 169.3


class _RuntimeSignal:
    def connect(self, _callback):
        return None


class _RuntimeWorker:
    def __init__(self, codes):
        self.codes = list(codes)
        self.progress = _RuntimeSignal()
        self.result_ready = _RuntimeSignal()
        self.finished = _RuntimeSignal()
        self.calls = []
        self.running = False

    def resume_auto_refresh(self):
        self.calls.append("resume")

    def defer_auto_refresh(self, seconds, reason=""):
        self.calls.append(("defer", seconds, reason))

    def isRunning(self):
        return self.running

    def start(self):
        self.running = True
        self.calls.append("start")


def test_runtime_service_defer_prevents_auto_worker_start(monkeypatch):
    created = []
    monkeypatch.setattr(runtime_service, "is_asian_quote_refresh_time", lambda codes: True)
    service = runtime_service.AsianMarketRuntimeService(
        worker_factory=lambda codes: created.append(_RuntimeWorker(codes)) or created[-1]
    )
    service.set_target_codes(["0522.HK"])

    service.defer_auto_refresh(60, "startup_asian_sync")

    assert service.sync_runtime_state() == "deferred"
    assert created == []

    service.clear_auto_refresh_defer()

    assert service.sync_runtime_state() == "started"
    assert created[0].calls == ["resume", "start"]


def test_runtime_service_marks_deferred_repaint_progress_degraded():
    service = runtime_service.AsianMarketRuntimeService()
    state_spy = QSignalSpy(service.sig_runtime_state_changed)
    progress_spy = QSignalSpy(service.sig_progress)

    service._on_worker_progress(
        "[15:41:16] Asian market quote refresh timed out; cached 26 updates and deferred UI repaint"
    )

    assert service.runtime_state == "degraded"
    assert progress_spy[0][0].endswith("deferred UI repaint")
    assert state_spy[-1][0]["state"] == "degraded"
    assert "已缓存 26 只部分更新" in state_spy[-1][0]["message"]


def test_runtime_cache_sync_degrades_without_raising(monkeypatch):
    message = "亚洲 K 线缓存同步失败，仍缺失 1 只(2308.TW)，未覆盖现有缓存"
    service = runtime_service.AsianMarketRuntimeService()
    monkeypatch.setattr(
        service,
        "cache_staleness",
        lambda: {
            "stale": True,
            "cache_latest_trade_date": "2026-06-19",
            "expected_latest_trade_date": "2026-06-22",
        },
    )
    monkeypatch.setattr(
        runtime_service,
        "sync_asian_kline_cache",
        lambda **kwargs: (
            False,
            message,
            {
                "target_count": 51,
                "written_count": 0,
                "missing": ["2308.TW"],
            },
        ),
    )

    result = service.run_cache_sync_if_stale(emit_event=False)

    assert result["status"] == "degraded"
    assert result["error"] == message
    assert result["missing"] == ["2308.TW"]
    assert service.last_error == message


def test_asian_cache_fetcher_thread_interrupts_the_shared_cooperative_token(monkeypatch):
    observed = []

    def _sync(**kwargs):
        token = kwargs["cancellation_token"]
        observed.append(token)
        token.raise_if_cancelled()
        return True, "ok", {}

    monkeypatch.setattr(workers, "sync_asian_kline_cache", _sync)
    active_thread = workers.AsianCacheFetcherThread()
    active_thread.run()

    assert observed == [active_thread.cancellation_token]
    assert active_thread.result_success is True

    thread = workers.AsianCacheFetcherThread()

    thread.requestInterruption()
    thread.run()

    assert thread.cancellation_token.cancelled is True
    assert thread.result_success is False
    assert "取消" in thread.result_message


def test_fetch_single_code_skips_optional_network_when_deadline_is_close(monkeypatch):
    calls = []
    monkeypatch.setattr(
        workers,
        "fetch_asian_realtime_quote",
        lambda code, yf_session=None, **kwargs: calls.append(kwargs) or {
            "date": "2026-04-23",
            "close": 2120.0,
            "open": 2090.0,
            "high": 2125.0,
            "low": 2085.0,
            "volume": 12345.0,
            "previous_close": 2050.0,
            "currency": "TWD",
            "source": "twse_mis",
            "quote_quality": "last",
        },
    )
    monkeypatch.setattr(
        workers,
        "_fetch_asian_pe_fallback",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("PE fallback should wait")),
    )
    monkeypatch.setattr(
        workers.MarketCalendar,
        "is_quote_refresh_time",
        classmethod(lambda cls, market="CN": False),
    )
    monkeypatch.setattr(workers, "GLOBAL_ASIAN_RT_CACHE", {})

    worker = workers.AsianMarketWorker(["2330.TW"])
    worker._fetch_deadline_monotonic = workers.time.monotonic() + 1
    monkeypatch.setattr(
        worker,
        "_fetch_yahoo_enrichment",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("Yahoo enrichment should wait")),
    )

    code, payload = worker._fetch_single_code("2330.TW", object(), object())

    assert code == "2330.TW"
    assert payload is not None
    assert payload["close"] == 2120.0
    assert calls[0]["allow_yfinance_fallback"] is False


def test_pe_refresh_does_not_block_during_quote_time(monkeypatch):
    monkeypatch.setattr(
        workers.MarketCalendar,
        "is_quote_refresh_time",
        classmethod(lambda cls, market="CN": True),
    )
    monkeypatch.setattr(
        workers,
        "_fetch_asian_pe_fallback",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("PE fallback should wait")),
    )

    worker = workers.AsianMarketWorker(["2330.TW"])
    pe, source, updated_at = worker._refresh_pe_if_needed(
        "2330.TW",
        ticker=None,
        info_session=object(),
        pe_value=28.0,
        pe_source="cached",
        pe_updated_at=0.0,
    )

    assert (pe, source, updated_at) == (28.0, "cached", 0.0)


def test_pe_refresh_records_failed_optional_fallback_attempt(monkeypatch):
    monkeypatch.setattr(workers.time, "time", lambda: 50_000.0)
    monkeypatch.setattr(
        workers.MarketCalendar,
        "is_quote_refresh_time",
        classmethod(lambda cls, market="CN": False),
    )
    monkeypatch.setattr(
        workers,
        "get_yf_rate_limit_status",
        lambda: {
            "active": True,
            "remaining_sec": 180.0,
            "reason": "Too Many Requests",
            "until_ts": 999.0,
        },
    )
    monkeypatch.setattr(workers, "_fetch_asian_pe_fallback", lambda *args, **kwargs: (None, ""))

    worker = workers.AsianMarketWorker(["2330.TW"])
    pe, source, updated_at = worker._refresh_pe_if_needed(
        "2330.TW",
        ticker=None,
        info_session=object(),
        pe_value=None,
        pe_source="",
        pe_updated_at=0.0,
    )

    assert (pe, source) == (None, "")
    assert updated_at == 50_000.0


def test_fetch_asian_realtime_quote_uses_regular_market_previous_close_for_yfinance_fallback(monkeypatch):
    history = pd.DataFrame(
        {
            "Open": [27300.0, 26850.0],
            "High": [27850.0, 27690.0],
            "Low": [26610.0, 26340.0],
            "Close": [26720.0, 26460.0],
            "Volume": [1689500.0, 1760500.0],
        },
        index=pd.to_datetime(["2026-04-20", "2026-04-21"]).tz_localize("Asia/Tokyo"),
    )

    class _Ticker(_JapanTickerStub):
        def history(self, *args, **kwargs):
            return history

    monkeypatch.setattr(workers, "_fetch_jp_realtime_quote", lambda code, session: None)
    monkeypatch.setattr(
        workers,
        "get_yf_rate_limit_status",
        lambda: {
            "active": False,
            "remaining_sec": 0.0,
            "reason": "",
            "until_ts": 0.0,
        },
    )
    monkeypatch.setattr(workers.yf, "Ticker", _Ticker)

    quote = workers.fetch_asian_realtime_quote("3110.T", yf_session=object())

    assert quote is not None
    assert quote["source"] == "yfinance"
    assert quote["previous_close"] == 26720.0


def test_fetch_single_code_prefers_resolved_previous_close_for_pct(monkeypatch):
    history = pd.DataFrame(
        {
            "Open": [27300.0, 26850.0],
            "High": [27850.0, 27690.0],
            "Low": [26610.0, 26340.0],
            "Close": [26720.0, 26460.0],
            "Volume": [1689500.0, 1760500.0],
        },
        index=pd.to_datetime(["2026-04-20", "2026-04-21"]).tz_localize("Asia/Tokyo"),
    )

    class _Ticker(_JapanTickerStub):
        @property
        def info(self):
            return {}

        def history(self, *args, **kwargs):
            return history

    monkeypatch.setattr(workers.yf, "Ticker", _Ticker)
    monkeypatch.setattr(workers, "fetch_asian_realtime_quote", lambda code, yf_session=None, **kwargs: None)
    monkeypatch.setattr(
        workers,
        "get_yf_rate_limit_status",
        lambda: {
            "active": False,
            "remaining_sec": 0.0,
            "reason": "",
            "until_ts": 0.0,
        },
    )
    monkeypatch.setattr(workers, "GLOBAL_ASIAN_RT_CACHE", {})

    worker = workers.AsianMarketWorker(["3110.T"])
    code, payload = worker._fetch_single_code("3110.T", object(), object())

    assert code == "3110.T"
    assert payload is not None
    assert payload["previous_close"] == 26720.0
    assert round(payload["pct"], 4) == round((26460.0 / 26720.0 - 1.0) * 100.0, 4)


def test_twse_pe_fallback_parses_daily_pe_endpoint():
    session = _FakeSession(
        _FakeResponse(
            data={
                "fields": ["證券代號", "證券名稱", "收盤價", "本益比"],
                "data": [
                    ["2317", "鴻海", "180.00", "18.20"],
                    ["2330", "台積電", "2080.00", "28.46"],
                ],
            },
        )
    )

    pe, source = workers._fetch_asian_pe_fallback("2330.TW", session)

    assert pe == 28.46
    assert source == "twse_per"
    assert "BWIBBU_d" in session.urls[0]


def test_tpex_pe_fallback_parses_openapi_endpoint():
    session = _FakeSession(
        _FakeResponse(
            data=[
                {"SecuritiesCompanyCode": "6488", "PriceEarningRatio": "39.24"},
                {"SecuritiesCompanyCode": "8299", "PriceEarningRatio": "-"},
            ],
        )
    )

    pe, source = workers._fetch_asian_pe_fallback("6488.TWO", session)

    assert pe == 39.24
    assert source == "tpex_per"
    assert "tpex_mainboard_peratio_analysis" in session.urls[0]


def test_kr_naver_pe_fallback_parses_per_element():
    session = _FakeSession(_FakeResponse(text='<table><tr><td><em id="_per">20.78</em></td></tr></table>'))

    pe, source = workers._fetch_asian_pe_fallback("000660.KS", session)

    assert pe == 20.78
    assert source == "naver_per"
    assert "main.naver" in session.urls[0]


def test_jp_yahoo_pe_fallback_parses_per_data_item(monkeypatch):
    monkeypatch.setattr(
        workers,
        "get_yf_rate_limit_status",
        lambda: {
            "active": False,
            "remaining_sec": 0.0,
            "reason": "",
            "until_ts": 0.0,
        },
    )
    session = _FakeSession(
        _FakeResponse(
            text=(
                "<dt><a><span>PER</span><span>（会社予想）</span></a></dt>"
                "<dd><span>(連)</span><span>22.20</span><span>倍</span></dd>"
            )
        )
    )

    pe, source = workers._fetch_asian_pe_fallback("7735.T", session)

    assert pe == 22.20
    assert source == "yahoo_jp_per"
    assert "finance.yahoo.co.jp" in session.urls[0]


def test_jp_pe_fallback_uses_kabutan_during_yahoo_cooldown(monkeypatch):
    monkeypatch.setattr(
        workers,
        "get_yf_rate_limit_status",
        lambda: {
            "active": True,
            "remaining_sec": 180.0,
            "reason": "Too Many Requests",
            "until_ts": 999.0,
        },
    )
    monkeypatch.setattr(
        workers,
        "_fetch_jp_yahoo_pe",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("Yahoo PE should wait")),
    )
    monkeypatch.setattr(workers, "_fetch_jp_kabutan_pe", lambda symbol: (24.3, "kabutan_per"))

    pe, source = workers._fetch_asian_pe_fallback("6113.T", object())

    assert (pe, source) == (24.3, "kabutan_per")


def test_jp_pe_fallback_uses_kabutan_when_yahoo_page_errors(monkeypatch):
    session = _FakeSession(_FakeResponse(text="upstream error", status_code=500))
    request_urls = []

    def _fake_requests_get(url, **kwargs):
        request_urls.append(url)
        if "kabutan.jp" in url:
            return _FakeResponse(
                text="<table><tr><th>PER</th><td>24.3</td></tr></table>",
                status_code=200,
            )
        return _FakeResponse(text="upstream error", status_code=500)

    monkeypatch.setattr(workers.requests, "get", _fake_requests_get)

    pe, source = workers._fetch_asian_pe_fallback("6113.T", session)

    assert pe == 24.3
    assert source == "kabutan_per"
    assert any("kabutan.jp" in url for url in request_urls)


def test_fetch_single_code_uses_market_pe_fallback_when_yahoo_info_empty(monkeypatch):
    class _Ticker:
        @property
        def info(self):
            return {}

    pe_session = _FakeSession(
        _FakeResponse(
            data={
                "fields": ["證券代號", "證券名稱", "收盤價", "本益比"],
                "data": [["2330", "台積電", "2080.00", "28.46"]],
            },
        )
    )
    monkeypatch.setattr(workers.yf, "Ticker", lambda *args, **kwargs: _Ticker())
    monkeypatch.setattr(
        workers,
        "get_yf_rate_limit_status",
        lambda: {
            "active": False,
            "remaining_sec": 0.0,
            "reason": "",
            "until_ts": 0.0,
        },
    )
    monkeypatch.setattr(
        workers.MarketCalendar,
        "is_quote_refresh_time",
        classmethod(lambda cls, market="CN": False),
    )
    monkeypatch.setattr(
        workers,
        "fetch_asian_realtime_quote",
        lambda code, yf_session=None, **kwargs: {
            "date": "2026-04-23",
            "close": 2080.0,
            "open": 2070.0,
            "high": 2100.0,
            "low": 2065.0,
            "volume": 12345.0,
            "previous_close": 2050.0,
            "currency": "TWD",
            "source": "twse_mis",
            "quote_quality": "last",
        },
    )
    monkeypatch.setattr(workers, "GLOBAL_ASIAN_RT_CACHE", {})

    worker = workers.AsianMarketWorker(["2330.TW"])
    code, payload = worker._fetch_single_code("2330.TW", object(), pe_session)

    assert code == "2330.TW"
    assert payload["pe"] == 28.46
    assert payload["pe_source"] == "twse_per"
