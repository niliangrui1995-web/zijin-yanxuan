import datetime as dt
import http.client
import json
import threading

import pandas as pd

from core.market_calendar import MarketCalendar
from vcp.data_provider import TdxDataProvider


def _make_provider():
    provider = TdxDataProvider.__new__(TdxDataProvider)
    provider._rt_quote_cache = {}
    provider._rt_quote_time = {}
    provider._rt_quote_lock = threading.Lock()
    provider._rt_api_call_timeout_sec = 8.0
    provider._rt_runtime_lock = threading.RLock()
    provider._rt_runtime = None
    provider._rt_runtime_failure_threshold = 3
    provider._rt_runtime_cooldown_sec = 300.0
    provider._rt_runtime_cooldown_until = 0.0
    provider._rt_runtime_consecutive_failures = 0
    provider._rt_runtime_last_success_at = 0.0
    provider._rt_runtime_reconnect_archived = 0
    provider._rt_runtime_last_error = ""
    provider._rt_runtime_thread_threshold = 4
    provider._rt_runtime_dedup_window_sec = 8.5
    provider._rt_quote_batch_size = 20
    provider._rt_quote_min_batch_size = 5
    provider._rt_quote_batch_pause_sec = 0.12
    provider._rt_last_network_probe = {}
    provider._rt_last_pressure_log_at = 0.0
    provider._offline = False
    provider.server_pool = []
    provider.cache_data = {}
    provider.cache_lock = threading.Lock()
    provider.thread_local = threading.local()
    return provider


class _FakeHttpResponse:
    def __init__(self, payload):
        self._payload = json.dumps(payload).encode("utf-8")

    def read(self, _size=-1):
        return self._payload

    def close(self):
        return None


def test_set_online_mode_keeps_empty_server_pool_for_eastmoney_mode():
    provider = _make_provider()
    provider._offline = True

    provider.set_online_mode(True)

    assert provider._offline is False
    assert provider.server_pool == []


def test_fetch_realtime_quotes_batch_uses_eastmoney_live_quotes_without_tdx_pool(monkeypatch):
    provider = _make_provider()
    provider._build_offline_quotes = lambda codes: {code: {"close": 0} for code in codes}
    provider.force_reconnect_servers = lambda: None
    provider.set_online_mode = lambda online=True: None

    def _fake_urlopen(request, timeout=8):
        del timeout
        url = request.full_url
        assert "secids=0.000001,1.600519" in url
        return _FakeHttpResponse(
            {
                "rc": 0,
                "data": {
                    "diff": [
                        {
                            "f2": 11.19,
                            "f5": 143215,
                            "f6": 160274827.37,
                            "f12": "000001",
                            "f15": 11.21,
                            "f16": 11.15,
                            "f17": 11.16,
                            "f18": 11.17,
                        },
                        {
                            "f2": 1462.07,
                            "f5": 12052,
                            "f6": 1754436217.0,
                            "f12": "600519",
                            "f15": 1467.88,
                            "f16": 1442.0,
                            "f17": 1444.98,
                            "f18": 1446.9,
                        },
                    ]
                },
            }
        )

    monkeypatch.setattr(MarketCalendar, "is_quote_refresh_time", lambda: True)
    monkeypatch.setattr(MarketCalendar, "get_latest_trade_date", lambda market="CN": dt.date(2026, 4, 15))
    monkeypatch.setattr(MarketCalendar, "today", lambda market="CN": dt.date(2026, 4, 15))
    monkeypatch.setattr("urllib.request.urlopen", _fake_urlopen)

    result = provider.fetch_realtime_quotes_batch(["000001", "600519"])

    assert result == {
        "000001": {
            "open": 11.16,
            "high": 11.21,
            "low": 11.15,
            "close": 11.19,
            "volume": 143215.0,
            "amount": 160274827.37,
            "last_close": 11.17,
            "change": 0.0,
            "pct": 0.0,
            "date": "2026-04-15",
            "source": "eastmoney",
            "name": "",
        },
        "600519": {
            "open": 1444.98,
            "high": 1467.88,
            "low": 1442.0,
            "close": 1462.07,
            "volume": 12052.0,
            "amount": 1754436217.0,
            "last_close": 1446.9,
            "change": 0.0,
            "pct": 0.0,
            "date": "2026-04-15",
            "source": "eastmoney",
            "name": "",
        },
    }


def test_fetch_realtime_quotes_batch_off_market_keeps_offline_quotes(monkeypatch):
    provider = _make_provider()
    provider._build_offline_quotes = lambda codes: {
        code: {
            "open": 10.0,
            "high": 10.0,
            "low": 10.0,
            "close": 10.0,
            "volume": 0.0,
            "amount": 0.0,
            "last_close": 9.8,
            "date": "2026-04-14",
        }
        for code in codes
    }

    monkeypatch.setattr(MarketCalendar, "is_quote_refresh_time", lambda: False)

    result = provider.fetch_realtime_quotes_batch(["000001"])

    assert result == {
        "000001": {
            "open": 10.0,
            "high": 10.0,
            "low": 10.0,
            "close": 10.0,
            "volume": 0.0,
            "amount": 0.0,
            "last_close": 9.8,
            "date": "2026-04-14",
        }
    }


def test_fetch_realtime_quotes_batch_retries_backup_eastmoney_host(monkeypatch):
    provider = _make_provider()
    provider._build_offline_quotes = lambda codes: {code: {"close": 0} for code in codes}
    provider._rt_eastmoney_hosts = [
        "push2.eastmoney.com",
        "88.push2.eastmoney.com",
    ]

    seen_hosts = []

    def _fake_urlopen(request, timeout=8):
        del timeout
        seen_hosts.append(request.full_url.split("/")[2])
        if len(seen_hosts) == 1:
            raise http.client.RemoteDisconnected("Remote end closed connection without response")
        return _FakeHttpResponse(
            {
                "rc": 0,
                "data": {
                    "diff": [
                        {
                            "f2": 11.19,
                            "f5": 143215,
                            "f6": 160274827.37,
                            "f12": "000001",
                            "f15": 11.21,
                            "f16": 11.15,
                            "f17": 11.16,
                            "f18": 11.17,
                        }
                    ]
                },
            }
        )

    monkeypatch.setattr(MarketCalendar, "is_quote_refresh_time", lambda: True)
    monkeypatch.setattr(MarketCalendar, "get_latest_trade_date", lambda market="CN": dt.date(2026, 4, 15))
    monkeypatch.setattr(MarketCalendar, "today", lambda market="CN": dt.date(2026, 4, 15))
    monkeypatch.setattr("urllib.request.urlopen", _fake_urlopen)

    result = provider.fetch_realtime_quotes_batch(["000001"])

    assert seen_hosts == ["push2.eastmoney.com", "88.push2.eastmoney.com"]
    assert result["000001"]["close"] == 11.19


def test_test_network_uses_eastmoney_http(monkeypatch):
    provider = _make_provider()
    seen_urls = []

    def _fake_urlopen(request, timeout=8):
        del timeout
        seen_urls.append(request.full_url)
        return _FakeHttpResponse(
            {
                "rc": 0,
                "data": {
                    "diff": [
                        {
                            "f2": 11.19,
                            "f5": 143215,
                            "f6": 160274827.37,
                            "f12": "000001",
                            "f15": 11.21,
                            "f16": 11.15,
                            "f17": 11.16,
                            "f18": 11.17,
                        }
                    ]
                },
            }
        )

    monkeypatch.setattr(MarketCalendar, "today", lambda market="CN": dt.date(2026, 4, 15))
    monkeypatch.setattr("urllib.request.urlopen", _fake_urlopen)
    monkeypatch.setattr("socket.socket", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("should not use socket")))

    assert provider.test_network(timeout=3) is True
    assert seen_urls
    assert "eastmoney.com" in seen_urls[0]


def test_test_network_records_probe_detail(monkeypatch):
    provider = _make_provider()

    def _fake_urlopen(request, timeout=8):
        del timeout
        if "gridlist.html" in request.full_url:
            return _FakeHttpResponse({"html": "ok"})
        return _FakeHttpResponse(
            {
                "rc": 0,
                "data": {
                    "diff": [
                        {
                            "f2": 11.19,
                            "f5": 143215,
                            "f6": 160274827.37,
                            "f12": "000001",
                            "f15": 11.21,
                            "f16": 11.15,
                            "f17": 11.16,
                            "f18": 11.17,
                        }
                    ]
                },
            }
        )

    monkeypatch.setattr(MarketCalendar, "today", lambda market="CN": dt.date(2026, 4, 15))
    monkeypatch.setattr("urllib.request.urlopen", _fake_urlopen)

    assert provider.test_network(timeout=3) is True
    assert provider.get_last_network_probe()["ok"] is True
    assert provider.get_last_network_probe()["page_probe"] == "ok"
    assert provider.get_last_network_probe()["quote_probe"] == "ok"


def test_fetch_realtime_quotes_batch_uses_recent_cache_within_dedup_window(monkeypatch):
    provider = _make_provider()
    cached_quote = {
        "open": 11.16,
        "high": 11.21,
        "low": 11.15,
        "close": 11.19,
        "volume": 143215,
        "amount": 160274827.37,
        "last_close": 11.17,
        "date": "2026-04-15",
    }
    provider._rt_quote_cache["000001"] = dict(cached_quote)
    provider._rt_quote_time["000001"] = 100.0

    monkeypatch.setattr(MarketCalendar, "is_quote_refresh_time", lambda: True)
    monkeypatch.setattr(MarketCalendar, "get_latest_trade_date", lambda market="CN": dt.date(2026, 4, 15))
    monkeypatch.setattr(MarketCalendar, "today", lambda market="CN": dt.date(2026, 4, 15))
    monkeypatch.setattr("time.time", lambda: 105.0)
    monkeypatch.setattr(
        provider,
        "_fetch_eastmoney_quotes_with_split_retry",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("should not fetch network quote")),
    )

    result = provider.fetch_realtime_quotes_batch(["000001"])

    assert result == {"000001": cached_quote}


def test_fetch_realtime_quotes_batch_pauses_between_batches(monkeypatch):
    provider = _make_provider()
    provider._rt_quote_batch_size = 2
    provider._rt_quote_batch_pause_sec = 0.25
    seen_batches = []
    sleep_calls = []

    def _fake_fetch(batch, inferred_trade_date, min_batch_size):
        del min_batch_size
        seen_batches.append((tuple(batch), inferred_trade_date))
        return (
            {
                code: {
                    "open": 10.0,
                    "high": 10.1,
                    "low": 9.9,
                    "close": 10.0,
                    "volume": 1.0,
                    "amount": 2.0,
                    "last_close": 9.8,
                    "date": inferred_trade_date,
                }
                for code in batch
            },
            [],
        )

    monkeypatch.setattr(MarketCalendar, "is_quote_refresh_time", lambda: True)
    monkeypatch.setattr(MarketCalendar, "get_latest_trade_date", lambda market="CN": dt.date(2026, 4, 15))
    monkeypatch.setattr(MarketCalendar, "today", lambda market="CN": dt.date(2026, 4, 15))
    monkeypatch.setattr(provider, "_fetch_eastmoney_quotes_with_split_retry", _fake_fetch)
    monkeypatch.setattr("time.sleep", lambda seconds: sleep_calls.append(seconds))

    result = provider.fetch_realtime_quotes_batch(["000001", "000002", "000003", "000004", "000005"])

    assert set(result) == {"000001", "000002", "000003", "000004", "000005"}
    assert seen_batches == [
        (("000001", "000002"), "2026-04-15"),
        (("000003", "000004"), "2026-04-15"),
        (("000005",), "2026-04-15"),
    ]
    assert sleep_calls == [0.25, 0.25]


def test_split_retry_does_not_expand_disconnect_failures(monkeypatch):
    provider = _make_provider()
    seen_batches = []

    def _fake_request(codes, inferred_trade_date):
        del inferred_trade_date
        seen_batches.append(tuple(codes))
        raise http.client.RemoteDisconnected("Remote end closed connection without response")

    monkeypatch.setattr(provider, "_request_eastmoney_quote_batch", _fake_request)

    quotes, failures = provider._fetch_eastmoney_quotes_with_split_retry(
        ["000001", "000002", "000003", "000004", "000005", "000006"],
        "2026-04-15",
        5,
    )

    assert quotes == {}
    assert seen_batches == [("000001", "000002", "000003", "000004", "000005", "000006")]
    assert failures == ["Remote end closed connection without response"]


def test_fetch_realtime_quotes_batch_switches_remaining_batches_to_sina_after_disconnect(monkeypatch):
    provider = _make_provider()
    provider._rt_quote_batch_size = 2
    provider._rt_quote_batch_pause_sec = 0.25
    seen_batches = []
    sleep_calls = []
    provider._build_offline_quotes = lambda codes: {
        code: {
            "open": 10.0,
            "high": 10.0,
            "low": 10.0,
            "close": 10.0,
            "volume": 0.0,
            "amount": 0.0,
            "last_close": 9.8,
            "date": "2026-04-15",
        }
        for code in codes
    }

    def _fake_fetch(batch, inferred_trade_date, min_batch_size):
        del inferred_trade_date, min_batch_size
        seen_batches.append(tuple(batch))
        return {}, ["Remote end closed connection without response"]

    monkeypatch.setattr(MarketCalendar, "is_quote_refresh_time", lambda: True)
    monkeypatch.setattr(MarketCalendar, "get_latest_trade_date", lambda market="CN": dt.date(2026, 4, 15))
    monkeypatch.setattr(MarketCalendar, "today", lambda market="CN": dt.date(2026, 4, 15))
    monkeypatch.setattr(provider, "_fetch_eastmoney_quotes_with_split_retry", _fake_fetch)
    monkeypatch.setattr(
        provider,
        "_request_sina_quote_batch",
        lambda codes, inferred_trade_date: {
            code: {
                "open": 10.0,
                "high": 10.1,
                "low": 9.9,
                "close": 10.0,
                "volume": 1.0,
                "amount": 2.0,
                "last_close": 9.8,
                "change": 0.2,
                "pct": 2.04,
                "date": inferred_trade_date,
                "source": "sina",
            }
            for code in codes
        },
    )
    monkeypatch.setattr("time.sleep", lambda seconds: sleep_calls.append(seconds))

    result = provider.fetch_realtime_quotes_batch(["000001", "000002", "000003", "000004"])

    assert set(result) == {"000001", "000002", "000003", "000004"}
    assert seen_batches == [("000001", "000002")]
    assert sleep_calls == [0.25]
    assert all(result[code]["source"] == "sina" for code in result)
    assert provider._rt_eastmoney_cooldown_until > 0.0


def test_fetch_realtime_quotes_batch_falls_back_to_sina_after_eastmoney_disconnect(monkeypatch):
    provider = _make_provider()
    provider._rt_quote_batch_size = 20
    provider._rt_quote_batch_pause_sec = 0.0
    provider._rt_runtime_consecutive_failures = 2
    provider._rt_runtime_last_error = "stale-error"

    monkeypatch.setattr(MarketCalendar, "is_quote_refresh_time", lambda: True)
    monkeypatch.setattr(MarketCalendar, "get_latest_trade_date", lambda market="CN": dt.date(2026, 4, 15))
    monkeypatch.setattr(MarketCalendar, "today", lambda market="CN": dt.date(2026, 4, 15))
    monkeypatch.setattr(
        provider,
        "_fetch_eastmoney_quotes_with_split_retry",
        lambda batch, inferred_trade_date, min_batch_size: ({}, ["Remote end closed connection without response"]),
    )
    monkeypatch.setattr(
        provider,
        "_request_sina_quote_batch",
        lambda codes, inferred_trade_date: {
            code: {
                "open": 11.16,
                "high": 11.21,
                "low": 11.15,
                "close": 11.19,
                "volume": 143215,
                "amount": 160274827.37,
                "last_close": 11.17,
                "change": 0.02,
                "pct": 0.18,
                "date": inferred_trade_date,
                "source": "sina",
            }
            for code in codes
        },
    )

    result = provider.fetch_realtime_quotes_batch(["000001", "600519"])

    assert set(result) == {"000001", "600519"}
    assert result["000001"]["source"] == "sina"
    assert provider._rt_eastmoney_cooldown_until > 0.0
    assert provider._rt_runtime_consecutive_failures == 0
    assert provider._rt_runtime_last_error == ""
    assert provider._rt_runtime_last_success_at > 0.0


def test_register_realtime_success_clears_runtime_cooldown():
    provider = _make_provider()
    provider._rt_runtime_cooldown_until = 9999999999.0
    provider._rt_runtime_consecutive_failures = 2
    provider._rt_runtime_last_error = "stale-error"

    provider._register_realtime_success()

    assert provider._rt_runtime_cooldown_until == 0.0
    assert provider._rt_runtime_consecutive_failures == 0
    assert provider._rt_runtime_last_error == ""
    assert provider._rt_runtime_last_success_at > 0.0


def test_fetch_realtime_quotes_batch_skips_eastmoney_when_in_cooldown(monkeypatch):
    provider = _make_provider()
    provider._rt_eastmoney_cooldown_until = 9999999999.0

    monkeypatch.setattr(MarketCalendar, "is_quote_refresh_time", lambda: True)
    monkeypatch.setattr(MarketCalendar, "get_latest_trade_date", lambda market="CN": dt.date(2026, 4, 15))
    monkeypatch.setattr(MarketCalendar, "today", lambda market="CN": dt.date(2026, 4, 15))
    monkeypatch.setattr(
        provider,
        "_fetch_eastmoney_quotes_with_split_retry",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("should skip eastmoney during cooldown")),
    )
    monkeypatch.setattr(
        provider,
        "_request_sina_quote_batch",
        lambda codes, inferred_trade_date: {
            code: {
                "open": 10.0,
                "high": 10.1,
                "low": 9.9,
                "close": 10.0,
                "volume": 1.0,
                "amount": 2.0,
                "last_close": 9.8,
                "change": 0.2,
                "pct": 2.04,
                "date": inferred_trade_date,
                "source": "sina",
            }
            for code in codes
        },
    )

    result = provider.fetch_realtime_quotes_batch(["000001"])

    assert result["000001"]["source"] == "sina"


def test_force_reconnect_servers_resets_eastmoney_state_without_tdx_speed_test(monkeypatch):
    provider = _make_provider()
    provider._offline = False
    provider._rt_runtime_cooldown_until = 123.0
    provider._rt_runtime_consecutive_failures = 2
    provider._rt_runtime_last_error = "old-error"

    called = []
    monkeypatch.setattr(provider, "_reset_realtime_runtime", lambda *args, **kwargs: called.append((args, kwargs)))

    provider.force_reconnect_servers()

    assert provider._rt_runtime_cooldown_until == 0.0
    assert provider._rt_runtime_consecutive_failures == 0
    assert provider._rt_runtime_last_error == ""
    assert called == [
        (
            ("强制刷新东方财富实时行情连接",),
            {"log_warning": False, "penalize_server": False},
        )
    ]


def test_get_data_fresh_for_chart_returns_local_history_when_no_tdx_pool(monkeypatch):
    provider = _make_provider()
    provider.server_pool = []
    hist_df = pd.DataFrame(
        {
            "open": [10.0, 10.2],
            "high": [10.4, 10.5],
            "low": [9.9, 10.0],
            "close": [10.1, 10.3],
            "volume": [1000, 1200],
        },
        index=pd.to_datetime(["2026-04-14", "2026-04-15"]),
    )
    provider.cache_data = {"000001": hist_df}
    provider._get_thread_api = lambda: (_ for _ in ()).throw(AssertionError("should not use pytdx api"))

    monkeypatch.setattr(MarketCalendar, "today", lambda market="CN": dt.date(2026, 4, 15))

    result = provider.get_data_fresh_for_chart("000001", force_sync=True)

    assert result is hist_df
