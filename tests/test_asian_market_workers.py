# -*- coding: utf-8 -*-

import pandas as pd
from yfinance.exceptions import YFRateLimitError

from ui.tabs import asian_market_workers as workers


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
