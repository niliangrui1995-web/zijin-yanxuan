# -*- coding: utf-8 -*-
from __future__ import annotations

import datetime as dt
from types import SimpleNamespace

import pandas as pd
import pytest
import requests

from infra.market_data import asian_market_http, asian_realtime_provider
from infra.market_data.adjustment_service import AdjustmentService
from infra.market_data.local_history_provider import LocalHistoryProvider
from infra.market_data.tdx_data_provider import TdxDataProvider
from infra.tasks.lifecycle import CancellationToken, TaskCancelledError
from ui import kline_window_asian, kline_window_runtime
from vcp import data_provider_quotes, data_provider_realtime
from vcp.fetchers import asian_kline_fetcher


def _history_frame() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "Open": [10.0, 11.0],
            "High": [12.0, 13.0],
            "Low": [9.0, 10.0],
            "Close": [11.0, 12.0],
            "Volume": [100.0, 200.0],
        },
        index=pd.to_datetime(["2026-07-15", "2026-07-16"]),
    )


def test_asian_http_applies_deadline_to_real_request_and_stops_retry_after_cancel(monkeypatch):
    token = CancellationToken.with_timeout(0.75)
    observed_timeouts: list[float] = []

    def _request(*_args, **kwargs):
        observed_timeouts.append(float(kwargs["timeout"]))
        token.cancel("window_switched")
        raise requests.RequestException("cancel after first attempt")

    monkeypatch.setattr(asian_market_http, "requests_get_https", _request)

    with pytest.raises(TaskCancelledError, match="window_switched"):
        asian_market_http.asian_market_get(
            "https://example.com",
            timeout=15,
            retries=2,
            cancellation_token=token,
        )

    assert len(observed_timeouts) == 1
    assert 0 < observed_timeouts[0] <= 0.75


def test_asian_history_backfill_forwards_token_into_fetcher(monkeypatch):
    token = CancellationToken.with_timeout(30)
    seen = []

    def _fetch(name, code, period, *, cancellation_token=None):
        seen.append((name, code, period, cancellation_token))
        return {
            "klines": [
                {"date": "2026-07-15", "open": 1, "high": 2, "low": 1, "close": 2}
            ]
        }

    context = SimpleNamespace(code="2330.TW")
    monkeypatch.setattr(
        kline_window_asian.MarketCalendar,
        "get_latest_completed_trade_date",
        lambda _market: dt.date(2026, 7, 15),
    )
    _payload, data_result = kline_window_asian._load_asian_backfill(
        token,
        request_name="台积电",
        request_code="2330.TW",
        context=context,
        fetch_single_kline=_fetch,
    )

    assert seen == [("台积电", "2330.TW", "1y", token)]
    assert data_result.frame is not None and not data_result.frame.empty


def test_kline_initial_and_realtime_quotes_forward_token_to_provider():
    token = CancellationToken.with_timeout(30)
    seen = []

    def _asian_quote(code, *, cancellation_token=None):
        seen.append(("asian", code, cancellation_token))
        return {"close": 12.0}

    result = SimpleNamespace(latest_trade_date=dt.date(2026, 7, 15), market="TW")
    frame, fetched, error = kline_window_runtime._merge_asian_initial_quote(
        pd.DataFrame(
            {"open": [10.0], "high": [11.0], "low": [9.0], "close": [10.0], "volume": [1.0]},
            index=[pd.Timestamp("2026-07-14")],
        ),
        result=result,
        code="2330.TW",
        target_trade_date=dt.date(2026, 7, 16),
        cached_quote=None,
        quote_fetcher=_asian_quote,
        cancellation_token=token,
    )
    assert error is None and fetched == {"close": 12.0}
    assert frame is not None

    class _Provider:
        def fetch_realtime_quotes_batch(self, codes, *, cancellation_token=None):
            seen.append(("cn", tuple(codes), cancellation_token))
            return {"000001": {"close": 11.0}}

    assert kline_window_runtime._fetch_realtime_quote(
        token,
        market="CN",
        request_code="000001",
        data_provider=_Provider(),
    ) == {"close": 11.0}
    assert seen == [("asian", "2330.TW", token), ("cn", ("000001",), token)]


def test_yfinance_history_uses_remaining_deadline_and_observes_cancel_after_network(monkeypatch):
    token = CancellationToken.with_timeout(0.75)
    observed_timeouts: list[float] = []

    class _Ticker:
        def history(self, **kwargs):
            observed_timeouts.append(float(kwargs["timeout"]))
            token.cancel("generation_replaced")
            return _history_frame()

    monkeypatch.setattr(
        asian_kline_fetcher,
        "get_yf_rate_limit_status",
        lambda: {"active": False, "remaining_sec": 0.0},
    )
    monkeypatch.setitem(
        __import__("sys").modules,
        "yfinance",
        SimpleNamespace(Ticker=lambda *_args, **_kwargs: _Ticker()),
    )

    with pytest.raises(TaskCancelledError, match="generation_replaced"):
        asian_kline_fetcher._fetch_yfinance_history_rows(
            "2330.TW",
            object(),
            start_date=dt.date(2026, 1, 1),
            end_date=dt.date(2026, 7, 16),
            cancellation_token=token,
        )

    assert len(observed_timeouts) == 1
    assert 0 < observed_timeouts[0] <= 0.75


def test_yfinance_realtime_token_path_avoids_unbounded_fast_info_and_has_timeout():
    token = CancellationToken.with_timeout(0.75)
    observed_timeouts: list[float] = []

    class _Ticker:
        @property
        def fast_info(self):
            raise AssertionError("cancellable K-line path must not enter unbounded fast_info")

        def history(self, **kwargs):
            observed_timeouts.append(float(kwargs["timeout"]))
            return _history_frame()

    quote = asian_realtime_provider.fetch_yfinance_realtime_quote(
        "2330.TW",
        object(),
        yf_module=SimpleNamespace(Ticker=lambda *_args, **_kwargs: _Ticker()),
        cancellation_token=token,
    )

    assert quote is not None and quote["close"] == 12.0
    assert len(observed_timeouts) == 1
    assert 0 < observed_timeouts[0] <= 0.75


def test_cn_realtime_http_receives_token_deadline_and_checks_after_body(monkeypatch):
    token = CancellationToken.with_timeout(0.75)
    observed_timeouts: list[float] = []

    class _Response:
        def read(self):
            token.cancel("chart_closed")
            return b'{"rc":0,"data":{"diff":[]}}'

        def close(self):
            return None

    def _urlopen(_request, *, timeout):
        observed_timeouts.append(float(timeout))
        return _Response()

    monkeypatch.setattr(data_provider_quotes, "urlopen_https", _urlopen)
    provider = SimpleNamespace(
        _rt_api_call_timeout_sec=8.0,
        _rt_eastmoney_hosts=["push2.eastmoney.com"],
        _rt_eastmoney_fast_fail_on_edge_error=False,
    )

    with pytest.raises(TaskCancelledError, match="chart_closed"):
        data_provider_quotes.request_eastmoney_quote_batch(
            provider,
            ["000001"],
            "2026-07-16",
            cancellation_token=token,
        )

    assert len(observed_timeouts) == 1
    assert 0 < observed_timeouts[0] <= 0.75


def test_cn_realtime_cancelled_primary_source_never_enters_fallback_sources():
    token = CancellationToken.with_timeout(30)
    entered: list[str] = []

    def _eastmoney(*_args, cancellation_token=None):
        entered.append("eastmoney")
        assert cancellation_token is token
        token.cancel("generation_replaced")
        return {}, ["primary cancelled"]

    provider = SimpleNamespace(
        _fetch_eastmoney_quotes_with_split_retry=_eastmoney,
        _request_sina_quote_batch=lambda *_args, **_kwargs: entered.append("sina") or {},
        _request_tencent_quote_batch=lambda *_args, **_kwargs: entered.append("tencent") or {},
    )

    with pytest.raises(TaskCancelledError, match="generation_replaced"):
        data_provider_realtime._fetch_realtime_quote_batch_sources(
            provider,
            ["000001"],
            inferred_trade_date="2026-07-16",
            min_batch_size=1,
            eastmoney_available=True,
            cancellation_token=token,
        )

    assert entered == ["eastmoney"]


def test_cn_history_applies_deadline_to_pytdx_socket_and_stops_retry_after_cancel():
    token = CancellationToken.with_timeout(0.75)
    observed_timeouts: list[float] = []

    class _Client:
        def __init__(self):
            self.timeout = 30.0

        def gettimeout(self):
            return self.timeout

        def settimeout(self, timeout):
            self.timeout = float(timeout)

    class _Api:
        def __init__(self):
            self.client = _Client()
            self.calls = 0

        def get_security_bars(self, *_args):
            self.calls += 1
            observed_timeouts.append(self.client.timeout)
            token.cancel("symbol_switched")
            raise OSError("cancel after first pytdx read")

    provider = SimpleNamespace(
        tdx_vipdoc="",
        _get_market_code=lambda _code: 0,
        _apply_forward_adjustment=lambda _api, _market, _code, frame: frame,
    )
    api = _Api()

    with pytest.raises(TaskCancelledError, match="symbol_switched"):
        LocalHistoryProvider(provider).fetch_standard_data(
            api,
            "000001",
            cancellation_token=token,
        )

    assert api.calls == 1
    assert len(observed_timeouts) == 1
    assert 0 < observed_timeouts[0] <= 0.75
    assert api.client.timeout == 30.0


def test_cn_adjustment_applies_deadline_to_xdxr_socket_and_restores_after_cancel():
    token = CancellationToken.with_timeout(0.75)
    observed_timeouts: dict[str, float] = {}

    class _Client:
        def __init__(self):
            self.timeout = 30.0

        def gettimeout(self):
            return self.timeout

        def settimeout(self, timeout):
            self.timeout = float(timeout)

    class _Api:
        def __init__(self):
            self.client = _Client()
            self.xdxr_calls = 0

        def get_security_bars(self, *_args):
            observed_timeouts["bars"] = self.client.timeout
            return [
                {
                    "datetime": "2026-07-16 15:00",
                    "open": 10.0,
                    "high": 11.0,
                    "low": 9.0,
                    "close": 10.5,
                    "vol": 100.0,
                }
            ]

        def get_xdxr_info(self, *_args):
            self.xdxr_calls += 1
            observed_timeouts["xdxr"] = self.client.timeout
            token.cancel("window_closed_during_adjustment")
            return []

    provider = TdxDataProvider.__new__(TdxDataProvider)
    provider.tdx_vipdoc = ""
    provider._local_gbbq = {}
    provider._get_market_code = lambda _code: 0
    provider._get_local_gbbq_for_code = lambda _code: {}
    provider._get_adjustment_service = lambda: AdjustmentService(provider)
    api = _Api()

    with pytest.raises(TaskCancelledError, match="window_closed_during_adjustment"):
        LocalHistoryProvider(provider).fetch_standard_data(
            api,
            "000001",
            cancellation_token=token,
        )

    assert api.xdxr_calls == 1
    assert 0 < observed_timeouts["bars"] <= 0.75
    assert 0 < observed_timeouts["xdxr"] <= 0.75
    assert api.client.timeout == 30.0


def test_cn_adjustment_does_not_enter_xdxr_network_when_already_cancelled():
    token = CancellationToken.with_timeout(30)
    token.cancel("stale_generation")
    api = SimpleNamespace(
        client=SimpleNamespace(gettimeout=lambda: 30.0, settimeout=lambda _timeout: None),
        get_xdxr_info=lambda *_args: pytest.fail("cancelled adjustment entered the network"),
    )
    provider = TdxDataProvider.__new__(TdxDataProvider)
    provider._local_gbbq = {}
    provider._get_local_gbbq_for_code = lambda _code: {}
    provider._get_adjustment_service = lambda: AdjustmentService(provider)

    with pytest.raises(TaskCancelledError, match="stale_generation"):
        provider._apply_forward_adjustment(
            api,
            0,
            "000001",
            _history_frame(),
            cancellation_token=token,
        )


def test_paginated_asian_history_checks_token_before_next_page(monkeypatch):
    token = CancellationToken.with_timeout(30)
    calls = []
    payload = [
        {
            "localTradedAt": f"2026-07-{day:02d}",
            "openPrice": "10",
            "highPrice": "11",
            "lowPrice": "9",
            "closePrice": "10",
            "accumulatedTradingVolume": "100",
        }
        for day in range(1, 21)
    ]

    class _Response:
        def json(self):
            token.cancel("chart_closed")
            return payload

    def _request(url, **kwargs):
        calls.append((url, kwargs["timeout"]))
        return _Response()

    monkeypatch.setattr(asian_kline_fetcher, "requests_get_https", _request)

    with pytest.raises(TaskCancelledError, match="chart_closed"):
        asian_kline_fetcher._fetch_kr_history_naver(
            "005930.KS",
            object(),
            start_date=dt.date(2026, 1, 1),
            end_date=dt.date(2026, 7, 16),
            cancellation_token=token,
        )

    assert len(calls) == 1
    assert 0 < calls[0][1] <= 2.0
