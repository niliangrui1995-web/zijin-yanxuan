# -*- coding: utf-8 -*-

import pandas as pd
from yfinance.exceptions import YFRateLimitError

from ui.tabs import asian_market_workers as workers


class _FakeResponse:
    def __init__(self, *, text="", data=None, status_code=200):
        self.text = text
        self._data = data
        self.status_code = status_code

    def json(self):
        return self._data


class _FakeSession:
    def __init__(self, response):
        self.response = response
        self.urls = []

    def get(self, url, **kwargs):
        self.urls.append(url)
        return self.response


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
    monkeypatch.setattr(workers, "fetch_asian_realtime_quote", lambda code, yf_session=None: None)
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
        lambda code, yf_session=None: {
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
        lambda code, yf_session=None: {
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

    class _Ticker:
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

    class _Ticker:
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

        @property
        def info(self):
            return {}

        def history(self, *args, **kwargs):
            return history

    monkeypatch.setattr(workers.yf, "Ticker", _Ticker)
    monkeypatch.setattr(workers, "fetch_asian_realtime_quote", lambda code, yf_session=None: None)
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
    session = _FakeSession(
        _FakeResponse(text='<table><tr><td><em id="_per">20.78</em></td></tr></table>')
    )

    pe, source = workers._fetch_asian_pe_fallback("000660.KS", session)

    assert pe == 20.78
    assert source == "naver_per"
    assert "main.naver" in session.urls[0]


def test_jp_yahoo_pe_fallback_parses_per_data_item():
    session = _FakeSession(
        _FakeResponse(
            text=(
                '<dt><a><span>PER</span><span>（会社予想）</span></a></dt>'
                '<dd><span>(連)</span><span>22.20</span><span>倍</span></dd>'
            )
        )
    )

    pe, source = workers._fetch_asian_pe_fallback("7735.T", session)

    assert pe == 22.20
    assert source == "yahoo_jp_per"
    assert "finance.yahoo.co.jp" in session.urls[0]


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
        lambda code, yf_session=None: {
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
