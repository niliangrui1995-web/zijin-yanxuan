import datetime as dt
import http.client
import json
import threading
import time
import traceback
import urllib.error
from urllib.parse import parse_qs, urlsplit

import pandas as pd
import pytest

from core.market_calendar import MarketCalendar
from domains.quotes.snapshot import merge_quote_entry
from vcp import data_provider_quotes, data_provider_realtime, data_provider_realtime_mixin
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


def _patch_open_market(monkeypatch):
    monkeypatch.setattr(MarketCalendar, "is_quote_refresh_time", lambda: True)
    for name in ("get_latest_trade_date", "today"):
        monkeypatch.setattr(MarketCalendar, name, lambda market="CN": dt.date(2026, 4, 15))


def _offline_quotes(codes):
    return {
        code: {
            "open": 9.8,
            "high": 10.0,
            "low": 9.7,
            "close": 9.9,
            "volume": 0.0,
            "amount": 0.0,
            "last_close": 9.8,
            "date": "2026-04-15",
            "source": "offline",
        }
        for code in codes
    }


def _network_quote(code, inferred_trade_date, source):
    return {
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
        "source": source,
    }


def test_quote_symbol_helpers_route_bse_code_to_bj_without_changing_shanghai_b_shares():
    assert data_provider_quotes.to_eastmoney_secid("920045") == "0.920045"
    assert data_provider_quotes.to_sina_symbol("920045") == "bj920045"
    assert data_provider_quotes.to_tencent_symbol("920045") == "bj920045"
    assert data_provider_quotes.to_eastmoney_secid("900001") == "1.900001"
    assert data_provider_quotes.to_sina_symbol("900001") == "sh900001"


def test_hithink_symbol_helper_maps_a_share_exchanges_without_guessing_nonstandard_codes():
    assert data_provider_quotes.to_hithink_thscode("000001") == "000001.SZ"
    assert data_provider_quotes.to_hithink_thscode("600519") == "600519.SH"
    assert data_provider_quotes.to_hithink_thscode("920045") == "920045.BJ"
    assert data_provider_quotes.to_hithink_thscode("not-a-code") == ""


def test_request_hithink_quote_batch_maps_snapshot_schema_and_preserves_volume_unit(monkeypatch):
    provider = _make_provider()
    quote_epoch = int(
        dt.datetime(
            2026,
            4,
            16,
            10,
            0,
            tzinfo=dt.timezone(dt.timedelta(hours=8)),
        ).timestamp()
    )
    quote_time = dt.datetime.fromtimestamp(
        quote_epoch,
        tz=dt.timezone(dt.timedelta(hours=8)),
    ).isoformat(timespec="seconds")
    seen = {}

    def _fake_urlopen(
        request,
        timeout=8,
        allowed_hosts=None,
        reserved_tun_host_addresses=None,
        benchmark_resolver_addresses=None,
    ):
        del timeout
        seen["url"] = request.full_url
        seen["api_key"] = request.get_header("X-api-key")
        seen["allowed_hosts"] = allowed_hosts
        seen["reserved_tun_host_addresses"] = reserved_tun_host_addresses
        seen["benchmark_resolver_addresses"] = benchmark_resolver_addresses
        return _FakeHttpResponse(
            {
                "code": 0,
                "message": "ok",
                "request_id": "unit-test-request",
                "data": {
                    "timestamp": quote_epoch * 1000,
                    "item": [
                        {
                            "thscode": "000001.SZ",
                            "ticker": "000001",
                            "last_price": 11.19,
                            "price_change": 0.02,
                            "price_change_ratio_pct": 0.18,
                            "open_price": 11.16,
                            "high_price": 11.21,
                            "low_price": 11.15,
                            "prev_price": 11.17,
                            "volume": 14_321_500,
                            "turnover": 160_274_827.37,
                        }
                    ],
                },
            }
        )

    monkeypatch.setenv("HITHINK_FINANCE_API_KEY", "unit-test-key")
    monkeypatch.setattr(data_provider_quotes, "urlopen_https", _fake_urlopen)

    result = provider._request_hithink_quote_batch(["000001"], "2026-04-15")

    assert urlsplit(seen["url"]).path == "/api/a-share/prices/snapshot"
    assert parse_qs(urlsplit(seen["url"]).query) == {"thscodes": ["000001.SZ"]}
    assert seen["api_key"] == "unit-test-key"
    assert seen["allowed_hosts"] == {"fuyao.aicubes.cn"}
    assert seen["reserved_tun_host_addresses"] == {"fuyao.aicubes.cn": {"198.18.0.67"}}
    assert seen["benchmark_resolver_addresses"] is None
    assert result == {
        "000001": {
            "open": 11.16,
            "high": 11.21,
            "low": 11.15,
            "close": 11.19,
            "volume": 14321500.0,
            "amount": 160274827.37,
            "last_close": 11.17,
            "change": 0.02,
            "pct": 0.18,
            "date": "2026-04-15",
            "source": "hithink",
            "quote_time": quote_time,
            "quote_freshness": "network",
        }
    }
    assert "name" not in result["000001"]
    assert merge_quote_entry({"name": "平安银行"}, result["000001"])["name"] == "平安银行"


def test_request_hithink_quote_batch_rejects_nonzero_business_code_without_retry(monkeypatch):
    provider = _make_provider()
    calls = []

    def _fake_urlopen(
        request,
        timeout=8,
        allowed_hosts=None,
        reserved_tun_host_addresses=None,
        benchmark_resolver_addresses=None,
    ):
        del timeout, allowed_hosts, reserved_tun_host_addresses, benchmark_resolver_addresses
        calls.append(request.full_url)
        return _FakeHttpResponse(
            {
                "code": 2003,
                "message": "invalid key",
                "request_id": "unit-test-request",
                "data": None,
            }
        )

    monkeypatch.setenv("HITHINK_FINANCE_API_KEY", "unit-test-key")
    monkeypatch.setattr(data_provider_quotes, "urlopen_https", _fake_urlopen)

    with pytest.raises(RuntimeError, match="code=2003"):
        provider._request_hithink_quote_batch(["000001"], "2026-04-15")

    assert len(calls) == 1


def test_request_hithink_quote_batch_retries_http_429_within_one_request_budget(monkeypatch):
    provider = _make_provider()
    provider._rt_api_call_timeout_sec = 1.0
    calls = []
    waits = []

    def _fake_urlopen(
        request,
        timeout=8,
        allowed_hosts=None,
        reserved_tun_host_addresses=None,
        benchmark_resolver_addresses=None,
    ):
        del timeout, allowed_hosts, reserved_tun_host_addresses, benchmark_resolver_addresses
        calls.append(request.full_url)
        if len(calls) == 1:
            raise urllib.error.HTTPError(request.full_url, 429, "Too Many Requests", hdrs=None, fp=None)
        return _FakeHttpResponse(
            {
                "code": 0,
                "data": {
                    "timestamp": None,
                    "item": [
                        {
                            "thscode": "000001.SZ",
                            "last_price": 11.19,
                            "price_change": 0.02,
                            "price_change_ratio_pct": 0.18,
                            "open_price": 11.16,
                            "high_price": 11.21,
                            "low_price": 11.15,
                            "prev_price": 11.17,
                            "volume": 14_321_500,
                            "turnover": 160_274_827.37,
                        }
                    ],
                },
            }
        )

    monkeypatch.setenv("HITHINK_FINANCE_API_KEY", "unit-test-key")
    monkeypatch.setattr(data_provider_quotes, "urlopen_https", _fake_urlopen)
    monkeypatch.setattr(
        data_provider_quotes,
        "wait_with_cancellation",
        lambda seconds, _token: waits.append(seconds),
    )

    result = provider._request_hithink_quote_batch(["000001"], "2026-04-15")

    assert len(calls) == 2
    assert waits == [pytest.approx(0.15)]
    assert result["000001"]["source"] == "hithink"


def test_request_hithink_quote_batch_keeps_retry_timeout_inside_one_source_budget(monkeypatch):
    provider = _make_provider()
    provider._rt_api_call_timeout_sec = 1.0
    clock = [100.0]
    calls = []

    def _fake_urlopen(
        _request,
        timeout=8,
        allowed_hosts=None,
        reserved_tun_host_addresses=None,
        benchmark_resolver_addresses=None,
    ):
        del timeout, allowed_hosts, reserved_tun_host_addresses, benchmark_resolver_addresses
        calls.append("hithink")
        clock[0] += 2.0
        raise TimeoutError("hithink timed out")

    monkeypatch.setenv("HITHINK_FINANCE_API_KEY", "unit-test-key")
    monkeypatch.setattr(data_provider_quotes, "urlopen_https", _fake_urlopen)
    monkeypatch.setattr(data_provider_quotes.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(data_provider_quotes, "wait_with_cancellation", lambda *_args: None)

    with pytest.raises(RuntimeError, match="同花顺实时报价请求失败"):
        provider._request_hithink_quote_batch(["000001"], "2026-04-15")

    assert calls == ["hithink"]


def test_request_hithink_quote_batch_hides_api_key_from_exception_traceback(monkeypatch):
    provider = _make_provider()
    secret = "unit-test-key"

    def _fake_urlopen(
        _request,
        timeout=8,
        allowed_hosts=None,
        reserved_tun_host_addresses=None,
        benchmark_resolver_addresses=None,
    ):
        del timeout, allowed_hosts, reserved_tun_host_addresses, benchmark_resolver_addresses
        raise OSError(f"proxy reflected X-api-key: {secret}")

    monkeypatch.setenv("HITHINK_FINANCE_API_KEY", secret)
    monkeypatch.setattr(data_provider_quotes, "urlopen_https", _fake_urlopen)
    monkeypatch.setattr(data_provider_quotes, "wait_with_cancellation", lambda *_args: None)

    with pytest.raises(RuntimeError, match="同花顺实时报价请求失败") as captured:
        provider._request_hithink_quote_batch(["000001"], "2026-04-15")

    rendered = "".join(traceback.format_exception(captured.type, captured.value, captured.tb))
    assert secret not in rendered


def test_fetch_realtime_quotes_batch_prefers_hithink_and_does_not_touch_legacy_sources(monkeypatch):
    provider = _make_provider()
    provider._rt_hithink_enabled = True
    provider._rt_quote_batch_pause_sec = 0.0
    provider._build_offline_quotes = lambda codes: {code: {"close": 0} for code in codes}

    def _hithink(batch, inferred_trade_date):
        assert batch == ["000001", "600519"]
        return {
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
                "source": "hithink",
            }
            for code in batch
        }

    _patch_open_market(monkeypatch)
    monkeypatch.setattr(provider, "_request_hithink_quote_batch", _hithink)
    monkeypatch.setattr(
        provider,
        "_fetch_eastmoney_quotes_with_split_retry",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("Hithink should cover the batch")),
    )

    result = provider.fetch_realtime_quotes_batch(["000001", "600519"])

    assert {code: quote["source"] for code, quote in result.items()} == {
        "000001": "hithink",
        "600519": "hithink",
    }
    assert provider.get_quote_request_stats()["recent_source_layers"] == ["hithink"]


def test_fetch_realtime_quotes_batch_uses_legacy_only_for_hithink_missing_codes(monkeypatch):
    provider = _make_provider()
    provider._rt_hithink_enabled = True
    provider._rt_quote_batch_pause_sec = 0.0
    provider._build_offline_quotes = lambda codes: {code: {"close": 0} for code in codes}
    eastmoney_batches = []

    def _quote(code, inferred_trade_date, source):
        return {
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
            "source": source,
        }

    def _hithink(batch, inferred_trade_date):
        assert batch == ["000001", "600519"]
        return {"000001": _quote("000001", inferred_trade_date, "hithink")}

    def _eastmoney(batch, inferred_trade_date, min_batch_size):
        del min_batch_size
        eastmoney_batches.append(tuple(batch))
        return {"600519": _quote("600519", inferred_trade_date, "eastmoney")}, []

    _patch_open_market(monkeypatch)
    monkeypatch.setattr(provider, "_request_hithink_quote_batch", _hithink)
    monkeypatch.setattr(provider, "_fetch_eastmoney_quotes_with_split_retry", _eastmoney)
    monkeypatch.setattr(
        provider,
        "_request_sina_quote_batch",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("legacy primary covers missing code")),
    )

    result = provider.fetch_realtime_quotes_batch(["000001", "600519"])

    assert eastmoney_batches == [("600519",)]
    assert result["000001"]["source"] == "hithink"
    assert result["600519"]["source"] == "eastmoney"
    assert {result[code]["quote_freshness"] for code in result} == {"network"}
    assert set(provider.get_quote_request_stats()["recent_source_layers"]) == {"eastmoney", "hithink"}


def test_hithink_transient_failure_falls_back_with_live_quote_and_publishes_cooldown(monkeypatch):
    provider = _make_provider()
    provider._rt_hithink_enabled = True
    provider._rt_quote_batch_pause_sec = 0.0

    _patch_open_market(monkeypatch)
    monkeypatch.setattr(
        provider,
        "_request_hithink_quote_batch",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(TimeoutError("hithink timed out")),
    )
    monkeypatch.setattr(
        provider,
        "_fetch_eastmoney_quotes_with_split_retry",
        lambda batch, inferred_trade_date, min_batch_size: (
            {
                code: {
                    "close": 10.0,
                    "last_close": 9.8,
                    "date": inferred_trade_date,
                    "source": "eastmoney",
                }
                for code in batch
            },
            [],
        ),
    )

    result = provider.fetch_realtime_quotes_batch(["000001"])

    assert result["000001"]["source"] == "eastmoney"
    assert result["000001"]["quote_freshness"] == "network"
    assert provider._rt_hithink_cooldown_until > time.time()
    stats = provider.get_quote_request_stats()
    assert stats["quote_primary_source"] == "hithink"
    assert stats["quote_cooldown_until"] == provider._rt_hithink_cooldown_until


def test_large_hithink_request_retries_one_transient_batch_without_global_cooldown(monkeypatch):
    provider = _make_provider()
    provider._rt_hithink_enabled = True
    provider._rt_quote_batch_size = 2
    provider._rt_hithink_quote_batch_size = 2
    provider._rt_quote_batch_pause_sec = 0.0
    hithink_calls = []
    attempts = {}

    def _hithink(batch, inferred_trade_date):
        key = tuple(batch)
        hithink_calls.append(key)
        attempts[key] = attempts.get(key, 0) + 1
        if key == ("000003", "000004") and attempts[key] == 1:
            raise TimeoutError("hithink timed out")
        return {code: _network_quote(code, inferred_trade_date, "hithink") for code in batch}

    _patch_open_market(monkeypatch)
    monkeypatch.setattr(provider, "_request_hithink_quote_batch", _hithink)
    monkeypatch.setattr(
        provider,
        "_fetch_eastmoney_quotes_with_split_retry",
        lambda batch, inferred_trade_date, _min_batch_size: (
            {code: _network_quote(code, inferred_trade_date, "eastmoney") for code in batch},
            [],
        ),
    )
    monkeypatch.setattr(data_provider_realtime, "wait_with_cancellation", lambda *_args, **_kwargs: None)

    result = provider.fetch_realtime_quotes_batch(
        ["000001", "000002", "000003", "000004", "000005", "000006"]
    )

    assert hithink_calls == [
        ("000001", "000002"),
        ("000003", "000004"),
        ("000003", "000004"),
        ("000005", "000006"),
    ]
    assert {quote["source"] for quote in result.values()} == {"hithink"}
    assert provider._rt_hithink_cooldown_until == 0.0


def test_large_hithink_request_isolates_one_persistent_batch_and_continues_primary(monkeypatch):
    provider = _make_provider()
    provider._rt_hithink_enabled = True
    provider._rt_quote_batch_size = 2
    provider._rt_hithink_quote_batch_size = 2
    provider._rt_quote_batch_pause_sec = 0.0
    hithink_calls = []
    eastmoney_batches = []

    def _hithink(batch, inferred_trade_date):
        key = tuple(batch)
        hithink_calls.append(key)
        if key == ("000003", "000004"):
            raise TimeoutError("hithink timed out")
        return {code: _network_quote(code, inferred_trade_date, "hithink") for code in batch}

    def _eastmoney(batch, inferred_trade_date, _min_batch_size):
        eastmoney_batches.append(tuple(batch))
        return {code: _network_quote(code, inferred_trade_date, "eastmoney") for code in batch}, []

    _patch_open_market(monkeypatch)
    monkeypatch.setattr(provider, "_request_hithink_quote_batch", _hithink)
    monkeypatch.setattr(provider, "_fetch_eastmoney_quotes_with_split_retry", _eastmoney)
    monkeypatch.setattr(data_provider_realtime, "wait_with_cancellation", lambda *_args, **_kwargs: None)

    result = provider.fetch_realtime_quotes_batch(
        ["000001", "000002", "000003", "000004", "000005", "000006"]
    )

    assert hithink_calls == [
        ("000001", "000002"),
        ("000003", "000004"),
        ("000003", "000004"),
        ("000005", "000006"),
    ]
    assert eastmoney_batches == [("000003", "000004")]
    assert {result[code]["source"] for code in ("000001", "000002", "000005", "000006")} == {"hithink"}
    assert {result[code]["source"] for code in ("000003", "000004")} == {"eastmoney"}
    assert provider._rt_hithink_cooldown_until == 0.0


def test_hithink_primary_uses_100_code_blocks_while_legacy_fallback_stays_small(monkeypatch):
    provider = _make_provider()
    provider._rt_hithink_enabled = True
    provider._rt_hithink_quote_batch_size = 100
    provider._rt_quote_batch_size = 20
    provider._rt_quote_batch_pause_sec = 0.0
    hithink_batches = []
    eastmoney_batches = []
    codes = [f"{index:06d}" for index in range(1, 101)]

    def _hithink(batch, _inferred_trade_date):
        hithink_batches.append(tuple(batch))
        return {}

    def _eastmoney(batch, inferred_trade_date, _min_batch_size):
        eastmoney_batches.append(tuple(batch))
        return {code: _network_quote(code, inferred_trade_date, "eastmoney") for code in batch}, []

    _patch_open_market(monkeypatch)
    monkeypatch.setattr(provider, "_request_hithink_quote_batch", _hithink)
    monkeypatch.setattr(provider, "_fetch_eastmoney_quotes_with_split_retry", _eastmoney)

    result = provider.fetch_realtime_quotes_batch(codes)

    assert hithink_batches == [tuple(codes)]
    assert [len(batch) for batch in eastmoney_batches] == [20, 20, 20, 20, 20]
    assert {quote["source"] for quote in result.values()} == {"eastmoney"}


def test_tun_blocked_legacy_fallbacks_do_not_advance_runtime_cooldown(monkeypatch):
    provider = _make_provider()
    provider._rt_hithink_enabled = True
    provider._rt_hithink_cooldown_until = time.time() + 60.0
    provider._rt_quote_batch_size = 1
    provider._rt_quote_batch_pause_sec = 0.0
    provider._build_offline_quotes = _offline_quotes
    runtime_failures = []
    blocked = "private or local HTTPS hosts are not allowed: 'https://push2.eastmoney.com/'"

    _patch_open_market(monkeypatch)
    monkeypatch.setattr(
        provider,
        "_fetch_eastmoney_quotes_with_split_retry",
        lambda *_args, **_kwargs: ({}, [blocked]),
    )
    monkeypatch.setattr(
        provider,
        "_request_sina_quote_batch",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError(blocked)),
    )
    monkeypatch.setattr(
        provider,
        "_request_tencent_quote_batch",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError(blocked)),
    )
    monkeypatch.setattr(provider, "_register_realtime_failure", lambda reason: runtime_failures.append(reason))

    for code in ("000001", "000002", "000003"):
        result = provider.fetch_realtime_quotes_batch([code])
        assert result[code]["source"] == "offline"

    assert runtime_failures == []
    assert provider._rt_runtime_cooldown_until == 0.0


def test_hithink_enabled_bypasses_legacy_runtime_cooldown_gate(monkeypatch):
    provider = _make_provider()
    provider._rt_hithink_enabled = True
    provider._rt_runtime_cooldown_until = time.time() + 300.0
    provider._rt_quote_batch_pause_sec = 0.0
    hithink_calls = []

    def _hithink(batch, inferred_trade_date):
        hithink_calls.append(tuple(batch))
        return {code: _network_quote(code, inferred_trade_date, "hithink") for code in batch}

    _patch_open_market(monkeypatch)
    monkeypatch.setattr(provider, "_request_hithink_quote_batch", _hithink)

    result = provider.fetch_realtime_quotes_batch(["000001"])

    assert hithink_calls == [("000001",)]
    assert result["000001"]["source"] == "hithink"
    assert provider._rt_runtime_cooldown_until == 0.0


def test_hithink_enabled_does_not_register_legacy_runtime_failure(monkeypatch):
    provider = _make_provider()
    provider._rt_hithink_enabled = True
    provider._rt_quote_batch_pause_sec = 0.0
    provider._build_offline_quotes = _offline_quotes
    runtime_failures = []

    _patch_open_market(monkeypatch)
    monkeypatch.setattr(
        provider,
        "_request_hithink_quote_batch",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("hithink upstream unavailable")),
    )
    monkeypatch.setattr(
        provider,
        "_fetch_eastmoney_quotes_with_split_retry",
        lambda *_args, **_kwargs: ({}, ["eastmoney upstream unavailable"]),
    )
    monkeypatch.setattr(
        provider,
        "_request_sina_quote_batch",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("sina upstream unavailable")),
    )
    monkeypatch.setattr(
        provider,
        "_request_tencent_quote_batch",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("tencent upstream unavailable")),
    )
    monkeypatch.setattr(provider, "_register_realtime_failure", lambda reason: runtime_failures.append(reason))

    result = provider.fetch_realtime_quotes_batch(["000001"])

    assert result["000001"]["source"] == "offline"
    assert runtime_failures == []


def test_hithink_isolates_bad_code_without_cooling_down_the_valid_batch(monkeypatch):
    provider = _make_provider()
    provider._rt_hithink_enabled = True
    provider._rt_quote_batch_pause_sec = 0.0
    legacy_batches = []

    def _fake_urlopen(
        request,
        timeout=8,
        allowed_hosts=None,
        reserved_tun_host_addresses=None,
        benchmark_resolver_addresses=None,
    ):
        del timeout, allowed_hosts, reserved_tun_host_addresses, benchmark_resolver_addresses
        thscodes = parse_qs(urlsplit(request.full_url).query)["thscodes"][0].split(",")
        if "000000.SZ" in thscodes:
            return _FakeHttpResponse({"code": 1002, "data": None})
        return _FakeHttpResponse(
            {
                "code": 0,
                "data": {
                    "timestamp": None,
                    "item": [
                        {
                            "thscode": "000001.SZ",
                            "last_price": 11.19,
                            "price_change": 0.02,
                            "price_change_ratio_pct": 0.18,
                            "open_price": 11.16,
                            "high_price": 11.21,
                            "low_price": 11.15,
                            "prev_price": 11.17,
                            "volume": 14_321_500,
                            "turnover": 160_274_827.37,
                        }
                    ],
                },
            }
        )

    def _eastmoney(batch, inferred_trade_date, min_batch_size):
        del min_batch_size
        legacy_batches.append(tuple(batch))
        return {
            "000000": {
                "close": 10.0,
                "last_close": 9.8,
                "date": inferred_trade_date,
                "source": "eastmoney",
            }
        }, []

    _patch_open_market(monkeypatch)
    monkeypatch.setenv("HITHINK_FINANCE_API_KEY", "unit-test-key")
    monkeypatch.setattr(data_provider_quotes, "urlopen_https", _fake_urlopen)
    monkeypatch.setattr(provider, "_fetch_eastmoney_quotes_with_split_retry", _eastmoney)

    result = provider.fetch_realtime_quotes_batch(["000001", "000000"])

    assert result["000001"]["source"] == "hithink"
    assert result["000000"]["source"] == "eastmoney"
    assert legacy_batches == [("000000",)]
    assert provider._rt_hithink_cooldown_until == 0.0


def test_hithink_error_redacts_api_key_before_health_or_log_output(monkeypatch):
    provider = _make_provider()
    provider._rt_hithink_enabled = True
    provider._rt_quote_batch_pause_sec = 0.0
    secret = "unit-test-key"
    logs = []

    _patch_open_market(monkeypatch)
    monkeypatch.setenv("HITHINK_FINANCE_API_KEY", secret)
    monkeypatch.setattr(
        provider,
        "_request_hithink_quote_batch",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError(f"proxy\nX-api-key: {secret}")),
    )
    monkeypatch.setattr(
        provider,
        "_fetch_eastmoney_quotes_with_split_retry",
        lambda batch, inferred_trade_date, min_batch_size: (
            {
                code: {
                    "close": 10.0,
                    "last_close": 9.8,
                    "date": inferred_trade_date,
                    "source": "eastmoney",
                }
                for code in batch
            },
            [],
        ),
    )
    monkeypatch.setattr(data_provider_quotes.log, "warning", lambda message: logs.append(str(message)))

    result = provider.fetch_realtime_quotes_batch(["000001"])

    assert result["000001"]["source"] == "eastmoney"
    assert secret not in provider._rt_hithink_last_error
    assert "\n" not in provider._rt_hithink_last_error
    assert all(secret not in message for message in logs)
    assert all(secret not in str(value) for value in provider.get_quote_request_stats().values())


def test_missing_hithink_key_disables_primary_and_uses_legacy_fallback(monkeypatch):
    provider = _make_provider()
    provider._rt_hithink_enabled = True
    provider._rt_quote_batch_pause_sec = 0.0

    _patch_open_market(monkeypatch)
    monkeypatch.delenv("HITHINK_FINANCE_API_KEY", raising=False)
    monkeypatch.setattr(
        provider,
        "_request_hithink_quote_batch",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("HITHINK_FINANCE_API_KEY 未配置")),
    )
    monkeypatch.setattr(
        provider,
        "_fetch_eastmoney_quotes_with_split_retry",
        lambda batch, inferred_trade_date, min_batch_size: (
            {
                code: {
                    "close": 11.19,
                    "last_close": 11.17,
                    "date": inferred_trade_date,
                    "source": "eastmoney",
                    "quote_freshness": "network",
                }
                for code in batch
            },
            [],
        ),
    )

    result = provider.fetch_realtime_quotes_batch(["000001"])

    assert result["000001"]["source"] == "eastmoney"
    assert provider._rt_hithink_enabled is False
    assert provider._rt_hithink_cooldown_until == 0.0


def test_test_network_prefers_hithink_when_it_is_enabled(monkeypatch):
    provider = _make_provider()
    provider._rt_hithink_enabled = True

    monkeypatch.setattr(
        provider,
        "_request_hithink_quote_batch",
        lambda codes, inferred_trade_date: {codes[0]: {"close": 11.19, "date": inferred_trade_date}},
    )
    monkeypatch.setattr(
        provider,
        "_request_eastmoney_quote_batch",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("Hithink probe should short-circuit")),
    )

    assert provider.test_network(timeout=3) is True
    probe = provider.get_last_network_probe()
    assert probe["hithink_quote_probe"] == "ok"
    assert probe["quote_probe"] == "ok"


def _make_sina_quote_fetcher(seen):
    def _fetch(batch, inferred_trade_date):
        seen.append(tuple(batch))
        return {
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
            for code in batch
        }

    return _fetch


class _FakeHttpResponse:
    def __init__(self, payload):
        self._payload = json.dumps(payload).encode("utf-8")

    def read(self, _size=-1):
        return self._payload

    def close(self):
        return None


class _FakeTextResponse:
    def __init__(self, text, encoding="gbk"):
        self._payload = text.encode(encoding)

    def read(self, _size=-1):
        return self._payload

    def close(self):
        return None


def _tencent_line(
    symbol="sh600519",
    *,
    code="600519",
    name="MOUTAI",
    close="1384.79",
    last_close="1401.17",
    open_price="1400.00",
    volume="52753",
    amount="7316111748",
    change="-16.38",
    pct="-1.17",
    high="1401.17",
    low="1380.00",
    quote_time="20260430161422",
):
    fields = [""] * 38
    fields[0] = "1"
    fields[1] = name
    fields[2] = code
    fields[3] = close
    fields[4] = last_close
    fields[5] = open_price
    fields[6] = volume
    fields[30] = quote_time
    fields[31] = change
    fields[32] = pct
    fields[33] = high
    fields[34] = low
    fields[35] = f"{close}/{volume}/{amount}"
    fields[36] = volume
    fields[37] = str(float(amount) / 10000.0)
    return f'v_{symbol}="' + "~".join(fields) + '";'


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
    quote_epoch = 1_776_219_846
    quote_time = dt.datetime.fromtimestamp(
        quote_epoch,
        tz=dt.timezone(dt.timedelta(hours=8)),
    ).isoformat(timespec="seconds")

    def _fake_urlopen(request, timeout=8):
        del timeout
        url = request.full_url
        assert "/api/qt/ulist/get" in url
        assert "/api/qt/ulist.np/get" not in url
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
                            "f124": quote_epoch,
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
                            "f124": quote_epoch,
                        },
                    ]
                },
            }
        )

    _patch_open_market(monkeypatch)
    monkeypatch.setattr(data_provider_quotes, "urlopen_https", _fake_urlopen)

    result = provider.fetch_realtime_quotes_batch(["000001", "600519"])

    assert result == {
        "000001": {
            "open": 11.16,
            "high": 11.21,
            "low": 11.15,
            "close": 11.19,
            "volume": 14321500.0,
            "amount": 160274827.37,
            "last_close": 11.17,
            "change": 0.0,
            "pct": 0.0,
            "date": "2026-04-15",
            "source": "eastmoney",
            "name": "",
            "quote_time": quote_time,
            "quote_received_at": pytest.approx(time.time(), abs=2.0),
            "quote_freshness": "network",
        },
        "600519": {
            "open": 1444.98,
            "high": 1467.88,
            "low": 1442.0,
            "close": 1462.07,
            "volume": 1205200.0,
            "amount": 1754436217.0,
            "last_close": 1446.9,
            "change": 0.0,
            "pct": 0.0,
            "date": "2026-04-15",
            "source": "eastmoney",
            "name": "",
            "quote_time": quote_time,
            "quote_received_at": pytest.approx(time.time(), abs=2.0),
            "quote_freshness": "network",
        },
    }


def test_request_tencent_quote_batch_parses_realtime_payload(monkeypatch):
    provider = _make_provider()
    seen_urls = []

    def _fake_urlopen(request, timeout=8):
        del timeout
        seen_urls.append(request.full_url)
        return _FakeTextResponse(_tencent_line())

    monkeypatch.setattr(data_provider_quotes, "urlopen_https", _fake_urlopen)

    result = provider._request_tencent_quote_batch(["600519"], "2026-04-15")

    assert seen_urls == ["https://qt.gtimg.cn/q=sh600519"]
    assert result == {
        "600519": {
            "open": 1400.0,
            "high": 1401.17,
            "low": 1380.0,
            "close": 1384.79,
            "volume": 5275300.0,
            "amount": 7316111748.0,
            "last_close": 1401.17,
            "change": -16.38,
            "pct": -1.17,
            "date": "2026-04-30",
            "source": "tencent",
            "name": "MOUTAI",
            "quote_time": "2026-04-30T16:14:22+08:00",
            "quote_freshness": "network",
        }
    }


def test_request_sina_quote_batch_preserves_exchange_quote_time(monkeypatch):
    provider = _make_provider()
    fields = [""] * 32
    fields[0] = "MOUTAI"
    fields[1] = "1400.00"
    fields[2] = "1401.17"
    fields[3] = "1384.79"
    fields[4] = "1401.17"
    fields[5] = "1380.00"
    fields[8] = "5275300"
    fields[9] = "7316111748"
    fields[30] = "2026-04-30"
    fields[31] = "14:24:06"
    response = 'var hq_str_sh600519="' + ",".join(fields) + '";'
    monkeypatch.setattr(
        data_provider_quotes,
        "urlopen_https",
        lambda *_args, **_kwargs: _FakeTextResponse(response),
    )

    result = provider._request_sina_quote_batch(["600519"], "2026-04-15")

    assert result["600519"]["volume"] == 5275300.0
    assert result["600519"]["quote_time"] == "2026-04-30T14:24:06+08:00"
    assert result["600519"]["quote_freshness"] == "network"


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
            "quote_time": "2026-04-14",
            "quote_freshness": "stale",
        }
    }
    stats = provider.get_quote_request_stats()
    assert stats["recent_network_result_count"] == 0
    assert stats["recent_cache_hit_count"] == 0
    assert stats["recent_stale_result_count"] == 1
    assert stats["recent_result_count"] == 1
    assert stats["recent_missing_result_count"] == 0


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

    _patch_open_market(monkeypatch)
    monkeypatch.setattr(data_provider_quotes, "urlopen_https", _fake_urlopen)

    result = provider.fetch_realtime_quotes_batch(["000001"])

    assert seen_hosts == ["push2.eastmoney.com", "88.push2.eastmoney.com"]
    assert result["000001"]["close"] == 11.19


def test_opening_warmup_pressure_fast_fails_eastmoney_backup_host(monkeypatch):
    provider = _make_provider()
    provider._rt_quote_batch_size = 20
    provider._rt_quote_batch_pause_sec = 0.0
    provider._rt_fallback_pressure_fetch_limit = 20
    provider._rt_eastmoney_hosts = [
        "push2.eastmoney.com",
        "88.push2.eastmoney.com",
    ]
    codes = [f"{idx:06d}" for idx in range(1, 41)]
    seen_hosts = []
    sina_seen = []

    provider._build_offline_quotes = _offline_quotes

    def _fake_urlopen(request, timeout=8):
        del timeout
        seen_hosts.append(request.full_url.split("/")[2])
        raise urllib.error.HTTPError(request.full_url, 502, "Bad Gateway", hdrs=None, fp=None)

    _patch_open_market(monkeypatch)
    monkeypatch.setattr(MarketCalendar, "get_market_status", lambda market="CN": "\u5f00\u76d8\u96c6\u5408\u7ade\u4ef7")
    monkeypatch.setattr(data_provider_quotes, "urlopen_https", _fake_urlopen)
    monkeypatch.setattr(provider, "_request_sina_quote_batch", _make_sina_quote_fetcher(sina_seen))
    monkeypatch.setattr(
        provider,
        "_request_tencent_quote_batch",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("Sina covers throttled batch")),
    )

    result = provider.fetch_realtime_quotes_batch(codes)

    assert seen_hosts == ["push2.eastmoney.com"]
    assert sina_seen == [tuple(codes[:20])]
    assert all(result[code]["source"] == "sina" for code in codes[:20])
    assert all(result[code]["source"] == "offline" for code in codes[20:])
    assert not getattr(provider, "_rt_eastmoney_fast_fail_on_edge_error", False)
    stats = provider.get_quote_request_stats()
    assert stats["recent_network_attempted_count"] == 20
    assert stats["recent_network_throttled"] is True
    assert stats["recent_network_throttle_reason"] == "fallback_pressure"
    assert stats["recent_status"] == "network_partial_with_fallback"


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
    monkeypatch.setattr(data_provider_quotes, "urlopen_https", _fake_urlopen)
    monkeypatch.setattr(
        "socket.socket", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("should not use socket"))
    )

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
    monkeypatch.setattr(data_provider_quotes, "urlopen_https", _fake_urlopen)

    assert provider.test_network(timeout=3) is True
    assert provider.get_last_network_probe()["ok"] is True
    assert provider.get_last_network_probe()["page_probe"] == "deferred"
    assert provider.get_last_network_probe()["quote_probe"] == "ok"


def test_test_network_accepts_tencent_when_primary_quote_sources_fail(monkeypatch):
    provider = _make_provider()

    def _fake_urlopen(request, timeout=8):
        del timeout
        url = request.full_url
        if "gridlist.html" in url:
            return _FakeTextResponse("ok", encoding="utf-8")
        if "eastmoney.com/api/qt/ulist/get" in url:
            raise OSError("eastmoney unavailable")
        if "hq.sinajs.cn" in url:
            raise OSError("sina unavailable")
        if "qt.gtimg.cn" in url:
            return _FakeTextResponse(
                _tencent_line(
                    "sz000001",
                    code="000001",
                    name="PINGAN",
                    close="11.49",
                    last_close="11.52",
                    open_price="11.50",
                    volume="1139242",
                    amount="1312827776",
                    change="-0.03",
                    pct="-0.26",
                    high="11.60",
                    low="11.46",
                )
            )
        raise AssertionError(url)

    monkeypatch.setattr(MarketCalendar, "today", lambda market="CN": dt.date(2026, 4, 15))
    monkeypatch.setattr(data_provider_quotes, "urlopen_https", _fake_urlopen)

    assert provider.test_network(timeout=3) is True
    probe = provider.get_last_network_probe()
    assert probe["ok"] is True
    assert probe["eastmoney_quote_probe"].startswith("fail:")
    assert probe["sina_quote_probe"].startswith("fail:")
    assert probe["tencent_quote_probe"] == "ok"


def test_test_network_uses_one_total_deadline_across_fallback_sources(monkeypatch):
    provider = _make_provider()
    clock = [100.0]
    calls = []

    def _slow_primary(*_args):
        calls.append(("eastmoney", provider._rt_api_call_timeout_sec))
        clock[0] += 2.1
        raise TimeoutError("primary timed out")

    monkeypatch.setattr(data_provider_realtime_mixin.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(provider, "_request_eastmoney_quote_batch", _slow_primary)
    monkeypatch.setattr(
        provider,
        "_request_sina_quote_batch",
        lambda *_args: (_ for _ in ()).throw(AssertionError("deadline must stop fallback")),
    )

    assert provider.test_network(timeout=2) is False
    assert calls == [("eastmoney", pytest.approx(1.6))]
    probe = provider.get_last_network_probe()
    assert probe["sina_quote_probe"] == "deadline"
    assert probe["elapsed_ms"] == 2100.0


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
        "quote_time": "2026-04-15T10:00:01+08:00",
        "quote_received_at": 100.0,
        "quote_freshness": "network",
    }
    provider._rt_quote_cache["000001"] = dict(cached_quote)
    provider._rt_quote_time["000001"] = 100.0

    _patch_open_market(monkeypatch)
    monkeypatch.setattr("time.time", lambda: 105.0)
    monkeypatch.setattr(
        provider,
        "_fetch_eastmoney_quotes_with_split_retry",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("should not fetch network quote")),
    )

    result = provider.fetch_realtime_quotes_batch(["000001"])

    expected = dict(cached_quote)
    expected["quote_freshness"] = "cache"
    assert result == {"000001": expected}
    stats = provider.get_quote_request_stats()
    assert stats["recent_network_result_count"] == 0
    assert stats["recent_cache_hit_count"] == 1
    assert stats["recent_stale_result_count"] == 0
    assert stats["recent_result_count"] == 1
    assert stats["recent_missing_result_count"] == 0
    assert stats["recent_latest_quote_time"] == "2026-04-15T10:00:01+08:00"


def test_fetch_realtime_quotes_batch_counts_network_cache_and_stale_results(monkeypatch):
    provider = _make_provider()
    provider._rt_quote_batch_pause_sec = 0.0
    provider._rt_quote_cache["000001"] = {
        "close": 10.0,
        "last_close": 9.8,
        "quote_time": "2026-04-15T10:00:01+08:00",
        "quote_received_at": 100.0,
        "quote_freshness": "network",
    }
    provider._rt_quote_time["000001"] = 100.0
    provider._build_offline_quotes = lambda codes: {
        code: {"close": 9.7, "last_close": 9.6, "date": "2026-04-14", "source": "offline"}
        for code in codes
    }

    def _fake_fetch(batch, inferred_trade_date, min_batch_size):
        del min_batch_size
        assert batch == ["000002", "000003"]
        return (
            {
                "000002": {
                    "close": 10.2,
                    "last_close": 10.0,
                    "date": inferred_trade_date,
                    "source": "eastmoney",
                    "quote_time": "2026-04-15T10:00:02+08:00",
                }
            },
            [],
        )

    _patch_open_market(monkeypatch)
    monkeypatch.setattr(time, "time", lambda: 105.0)
    monkeypatch.setattr(provider, "_fetch_eastmoney_quotes_with_split_retry", _fake_fetch)
    monkeypatch.setattr(
        provider,
        "_request_sina_quote_batch",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("sina unavailable")),
    )
    monkeypatch.setattr(
        provider,
        "_request_tencent_quote_batch",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("tencent unavailable")),
    )

    result = provider.fetch_realtime_quotes_batch(["000001", "000002", "000003"])

    assert result["000001"]["quote_freshness"] == "cache"
    assert result["000001"]["quote_time"] == "2026-04-15T10:00:01+08:00"
    assert result["000002"]["quote_freshness"] == "network"
    assert result["000002"]["quote_time"] == "2026-04-15T10:00:02+08:00"
    assert result["000003"]["quote_freshness"] == "stale"
    assert result["000003"]["quote_time"] == "2026-04-14"
    stats = provider.get_quote_request_stats()
    assert stats["recent_network_result_count"] == 1
    assert stats["recent_cache_hit_count"] == 1
    assert stats["recent_stale_result_count"] == 1
    assert stats["recent_result_count"] == 3
    assert stats["recent_missing_result_count"] == 0
    assert stats["recent_latest_quote_time"] == "2026-04-15T10:00:02+08:00"


def test_fetch_realtime_quotes_batch_counts_missing_when_all_layers_are_empty(monkeypatch):
    provider = _make_provider()
    provider._rt_quote_batch_pause_sec = 0.0
    provider._build_offline_quotes = lambda _codes: {}
    _patch_open_market(monkeypatch)
    monkeypatch.setattr(
        provider,
        "_fetch_eastmoney_quotes_with_split_retry",
        lambda *_args, **_kwargs: ({}, ["eastmoney unavailable"]),
    )
    monkeypatch.setattr(
        provider,
        "_request_sina_quote_batch",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("sina unavailable")),
    )
    monkeypatch.setattr(
        provider,
        "_request_tencent_quote_batch",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("tencent unavailable")),
    )

    result = provider.fetch_realtime_quotes_batch(["000001"])

    assert result == {}
    stats = provider.get_quote_request_stats()
    assert stats["recent_network_result_count"] == 0
    assert stats["recent_cache_hit_count"] == 0
    assert stats["recent_stale_result_count"] == 0
    assert stats["recent_result_count"] == 0
    assert stats["recent_missing_result_count"] == 1


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

    _patch_open_market(monkeypatch)
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


def test_fetch_realtime_quotes_batch_records_request_stats(monkeypatch):
    provider = _make_provider()
    provider._rt_quote_batch_size = 2
    provider._rt_quote_batch_pause_sec = 0.0

    def _fake_fetch(batch, inferred_trade_date, min_batch_size):
        del min_batch_size
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
                    "source": "eastmoney",
                }
                for code in batch
            },
            [],
        )

    _patch_open_market(monkeypatch)
    monkeypatch.setattr(provider, "_fetch_eastmoney_quotes_with_split_retry", _fake_fetch)

    provider.fetch_realtime_quotes_batch(["000001", "000001", "000002", "000003"])

    stats = provider.get_quote_request_stats()
    assert stats["history_size"] == 1
    assert stats["recent_requested_count"] == 4
    assert stats["recent_unique_requested_count"] == 3
    assert stats["recent_pending_count"] == 3
    assert stats["recent_batch_count"] == 2
    assert stats["recent_codes_count"] == 1
    assert stats["recent_duplicate_requested_codes"] == {"000001": 2}
    assert stats["recent_triggered_network"] is True
    assert stats["recent_source_layers"] == ["eastmoney"]
    assert stats["recent_network_result_count"] == 3
    assert stats["recent_cache_hit_count"] == 0
    assert stats["recent_stale_result_count"] == 0
    assert stats["recent_result_count"] == 3
    assert stats["recent_missing_result_count"] == 0
    assert stats["recent_batches"][0]["codes_count"] == 2
    assert stats["recent_batches"][0]["duplicate_codes"] == {}


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


def test_split_retry_does_not_expand_eastmoney_edge_failures(monkeypatch):
    provider = _make_provider()
    seen_batches = []

    def _fake_request(codes, inferred_trade_date):
        del inferred_trade_date
        seen_batches.append(tuple(codes))
        raise RuntimeError("HTTP Error 502: Bad Gateway")

    monkeypatch.setattr(provider, "_request_eastmoney_quote_batch", _fake_request)

    quotes, failures = provider._fetch_eastmoney_quotes_with_split_retry(
        ["000001", "000002", "000003", "000004", "000005", "000006"],
        "2026-04-15",
        5,
    )

    assert quotes == {}
    assert seen_batches == [("000001", "000002", "000003", "000004", "000005", "000006")]
    assert failures == ["HTTP Error 502: Bad Gateway"]


def test_split_retry_caps_non_disconnect_failures_and_enters_cooldown(monkeypatch):
    provider = _make_provider()
    provider._rt_quote_batch_size = 16
    provider._rt_quote_min_batch_size = 1
    provider._rt_quote_batch_pause_sec = 0.0
    eastmoney_seen = []
    cooldown_reasons = []

    def _fail_request(codes, inferred_trade_date):
        del inferred_trade_date
        eastmoney_seen.append(tuple(codes))
        raise RuntimeError("unexpected upstream response")

    _patch_open_market(monkeypatch)
    monkeypatch.setattr(provider, "_request_eastmoney_quote_batch", _fail_request)
    monkeypatch.setattr(provider, "_request_sina_quote_batch", _make_sina_quote_fetcher([]))
    monkeypatch.setattr(
        provider,
        "_request_tencent_quote_batch",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("Sina should cover the failed batch")),
    )
    monkeypatch.setattr(provider, "_enter_eastmoney_cooldown", lambda reason: cooldown_reasons.append(reason))

    codes = [f"{idx:06d}" for idx in range(1, 17)]
    result = provider.fetch_realtime_quotes_batch(codes)

    assert len(eastmoney_seen) == 3
    assert cooldown_reasons and "split retry budget exhausted" in cooldown_reasons[0]
    assert set(result) == set(codes)
    assert all(quote["source"] == "sina" for quote in result.values())


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

    _patch_open_market(monkeypatch)
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


def test_fetch_realtime_quotes_batch_switches_remaining_batches_to_sina_after_eastmoney_502(monkeypatch):
    provider = _make_provider()
    provider._rt_quote_batch_size = 2
    provider._rt_quote_batch_pause_sec = 0.0
    eastmoney_seen = []
    sina_seen = []

    def _fake_fetch(batch, inferred_trade_date, min_batch_size):
        del inferred_trade_date, min_batch_size
        eastmoney_seen.append(tuple(batch))
        return {}, ["HTTP Error 502: Bad Gateway"]

    def _fake_sina(codes, inferred_trade_date):
        sina_seen.append(tuple(codes))
        return {
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
        }

    _patch_open_market(monkeypatch)
    monkeypatch.setattr(provider, "_fetch_eastmoney_quotes_with_split_retry", _fake_fetch)
    monkeypatch.setattr(provider, "_request_sina_quote_batch", _fake_sina)
    monkeypatch.setattr(
        provider,
        "_request_tencent_quote_batch",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("should not use Tencent when Sina covers")),
    )

    result = provider.fetch_realtime_quotes_batch(["000001", "000002", "000003", "000004"])

    assert set(result) == {"000001", "000002", "000003", "000004"}
    assert eastmoney_seen == [("000001", "000002")]
    assert sina_seen == [("000001", "000002"), ("000003", "000004")]
    assert all(result[code]["source"] == "sina" for code in result)
    assert provider._rt_eastmoney_cooldown_until > 0.0


def test_fetch_realtime_quotes_batch_throttles_large_request_after_midround_fallback(monkeypatch):
    provider = _make_provider()
    provider._rt_quote_batch_size = 20
    provider._rt_quote_batch_pause_sec = 0.0
    provider._rt_fallback_pressure_fetch_limit = 20
    codes = [f"{idx:06d}" for idx in range(1, 121)]
    eastmoney_seen = []
    sina_seen = []

    provider._build_offline_quotes = _offline_quotes

    def _fake_fetch(batch, inferred_trade_date, min_batch_size):
        del inferred_trade_date, min_batch_size
        eastmoney_seen.append(tuple(batch))
        return {}, ["HTTP Error 502: Bad Gateway"]

    _patch_open_market(monkeypatch)
    monkeypatch.setattr(provider, "_fetch_eastmoney_quotes_with_split_retry", _fake_fetch)
    monkeypatch.setattr(provider, "_request_sina_quote_batch", _make_sina_quote_fetcher(sina_seen))
    monkeypatch.setattr(
        provider,
        "_request_tencent_quote_batch",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("Sina covers throttled batches")),
    )

    result = provider.fetch_realtime_quotes_batch(codes)

    assert set(result) == set(codes)
    assert eastmoney_seen == [tuple(codes[:20])]
    assert sina_seen == [tuple(codes[:20])]
    assert all(result[code]["source"] == "sina" for code in codes[:20])
    assert all(result[code]["source"] == "offline" for code in codes[20:])
    stats = provider.get_quote_request_stats()
    assert stats["recent_pending_count"] == 120
    assert stats["recent_network_attempted_count"] == 20
    assert stats["recent_network_throttled"] is True
    assert stats["recent_network_throttle_reason"] == "fallback_pressure"
    assert stats["recent_status"] == "network_partial_with_fallback"
    assert stats["recent_batch_count"] == 1
    assert stats["recent_network_result_count"] == 20
    assert stats["recent_cache_hit_count"] == 0
    assert stats["recent_stale_result_count"] == 100
    assert stats["recent_result_count"] == 120
    assert stats["recent_missing_result_count"] == 0
    assert "network_throttled_fallback_pressure" in stats["recent_source_layers"]


def test_fetch_realtime_quotes_batch_limits_medium_cooldown_fallback_to_first_batch(monkeypatch):
    provider = _make_provider()
    provider._rt_quote_batch_size = 20
    provider._rt_quote_batch_pause_sec = 0.0
    provider._rt_eastmoney_cooldown_until = time.time() + 120.0
    codes = [f"{idx:06d}" for idx in range(1, 61)]
    sina_seen = []

    provider._build_offline_quotes = _offline_quotes

    _patch_open_market(monkeypatch)
    monkeypatch.setattr(
        provider,
        "_fetch_eastmoney_quotes_with_split_retry",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("Eastmoney is cooling down")),
    )
    monkeypatch.setattr(provider, "_request_sina_quote_batch", _make_sina_quote_fetcher(sina_seen))
    monkeypatch.setattr(
        provider,
        "_request_tencent_quote_batch",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("Sina covers throttled batch")),
    )

    result = provider.fetch_realtime_quotes_batch(codes)

    assert set(result) == set(codes)
    assert sina_seen == [tuple(codes[:20])]
    assert all(result[code]["source"] == "sina" for code in codes[:20])
    assert all(result[code]["source"] == "offline" for code in codes[20:])
    stats = provider.get_quote_request_stats()
    assert stats["recent_pending_count"] == 60
    assert stats["recent_network_attempted_count"] == 20
    assert stats["recent_network_throttled"] is True
    assert stats["recent_network_throttle_reason"] == "fallback_pressure"
    assert stats["recent_status"] == "network_partial_with_fallback"
    assert stats["recent_batch_count"] == 1
    assert stats["recent_network_result_count"] == 20
    assert stats["recent_cache_hit_count"] == 0
    assert stats["recent_stale_result_count"] == 40
    assert stats["recent_result_count"] == 60
    assert stats["recent_missing_result_count"] == 0
    assert "network_throttled_fallback_pressure" in stats["recent_source_layers"]


def test_fetch_realtime_quotes_batch_keeps_later_fallback_batches_alive_after_disconnect(monkeypatch):
    provider = _make_provider()
    provider._rt_quote_batch_size = 2
    provider._rt_quote_batch_pause_sec = 0.0
    eastmoney_seen = []
    sina_seen = []
    tencent_seen = []

    def _quote(code, inferred_trade_date, source):
        return {
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
            "source": source,
        }

    provider._build_offline_quotes = lambda codes: {
        code: _quote(code, "2026-04-15", "offline") for code in codes
    }

    def _fake_fetch(batch, inferred_trade_date, min_batch_size):
        del inferred_trade_date, min_batch_size
        eastmoney_seen.append(tuple(batch))
        return {}, ["Remote end closed connection without response"]

    def _fake_sina(codes, inferred_trade_date):
        sina_seen.append(tuple(codes))
        if tuple(codes) == ("000001", "000002"):
            raise OSError("[WinError 10053] 你的主机中的软件中止了一个已建立的连接。")
        return {code: _quote(code, inferred_trade_date, "sina") for code in codes}

    def _fake_tencent(codes, inferred_trade_date):
        del inferred_trade_date
        tencent_seen.append(tuple(codes))
        raise OSError("[WinError 10053] 你的主机中的软件中止了一个已建立的连接。")

    _patch_open_market(monkeypatch)
    monkeypatch.setattr(provider, "_fetch_eastmoney_quotes_with_split_retry", _fake_fetch)
    monkeypatch.setattr(provider, "_request_sina_quote_batch", _fake_sina)
    monkeypatch.setattr(provider, "_request_tencent_quote_batch", _fake_tencent)

    result = provider.fetch_realtime_quotes_batch(["000001", "000002", "000003", "000004"])

    assert set(result) == {"000001", "000002", "000003", "000004"}
    assert result["000001"]["source"] == "offline"
    assert result["000002"]["source"] == "offline"
    assert result["000003"]["source"] == "sina"
    assert result["000004"]["source"] == "sina"
    assert eastmoney_seen == [("000001", "000002")]
    assert sina_seen == [("000001", "000002"), ("000003", "000004")]
    assert tencent_seen == [("000001", "000002")]

    stats = provider.get_quote_request_stats()
    assert stats["recent_batch_count"] == 2
    assert [batch["status"] for batch in stats["recent_batches"]] == ["failed", "ok"]
    assert stats["recent_status"] == "network_partial_with_fallback"
    assert stats["recent_network_result_count"] == 2
    assert stats["recent_cache_hit_count"] == 0
    assert stats["recent_stale_result_count"] == 2
    assert stats["recent_result_count"] == 4
    assert stats["recent_missing_result_count"] == 0
    assert "offline_missing_fallback" in stats["recent_source_layers"]


def test_fetch_realtime_quotes_batch_falls_back_to_sina_after_eastmoney_disconnect(monkeypatch):
    provider = _make_provider()
    provider._rt_quote_batch_size = 20
    provider._rt_quote_batch_pause_sec = 0.0
    provider._rt_runtime_consecutive_failures = 2
    provider._rt_runtime_last_error = "stale-error"

    _patch_open_market(monkeypatch)
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
    monkeypatch.setattr(
        provider,
        "_request_tencent_quote_batch",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("should not use Tencent when Sina covers")),
    )

    result = provider.fetch_realtime_quotes_batch(["000001", "600519"])

    assert set(result) == {"000001", "600519"}
    assert result["000001"]["source"] == "sina"
    assert provider._rt_eastmoney_cooldown_until > 0.0
    assert provider._rt_runtime_consecutive_failures == 0
    assert provider._rt_runtime_last_error == ""
    assert provider._rt_runtime_last_success_at > 0.0


def test_fetch_realtime_quotes_batch_falls_back_to_tencent_after_sina_failure(monkeypatch):
    provider = _make_provider()
    provider._rt_quote_batch_size = 20
    provider._rt_quote_batch_pause_sec = 0.0
    provider._build_offline_quotes = lambda codes: {code: {"close": 0} for code in codes}

    _patch_open_market(monkeypatch)
    monkeypatch.setattr(
        provider,
        "_fetch_eastmoney_quotes_with_split_retry",
        lambda batch, inferred_trade_date, min_batch_size: ({}, ["eastmoney unavailable"]),
    )
    monkeypatch.setattr(
        provider,
        "_request_sina_quote_batch",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("sina unavailable")),
    )
    monkeypatch.setattr(
        provider,
        "_request_tencent_quote_batch",
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
                "source": "tencent",
            }
            for code in codes
        },
    )

    result = provider.fetch_realtime_quotes_batch(["000001"])

    assert result["000001"]["source"] == "tencent"
    assert result["000001"]["close"] == 10.0


def test_fetch_realtime_quotes_batch_uses_tencent_for_sina_missing_codes(monkeypatch):
    provider = _make_provider()
    provider._rt_quote_batch_size = 20
    provider._rt_quote_batch_pause_sec = 0.0
    provider._build_offline_quotes = lambda codes: {code: {"close": 0} for code in codes}
    tencent_seen = []

    _patch_open_market(monkeypatch)
    monkeypatch.setattr(
        provider,
        "_fetch_eastmoney_quotes_with_split_retry",
        lambda batch, inferred_trade_date, min_batch_size: ({}, ["eastmoney unavailable"]),
    )
    monkeypatch.setattr(
        provider,
        "_request_sina_quote_batch",
        lambda codes, inferred_trade_date: {
            "000001": {
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
        },
    )

    def _fake_tencent(codes, inferred_trade_date):
        tencent_seen.append(tuple(codes))
        return {
            code: {
                "open": 1400.0,
                "high": 1401.17,
                "low": 1380.0,
                "close": 1384.79,
                "volume": 52753,
                "amount": 7316111748.0,
                "last_close": 1401.17,
                "change": -16.38,
                "pct": -1.17,
                "date": inferred_trade_date,
                "source": "tencent",
            }
            for code in codes
        }

    monkeypatch.setattr(provider, "_request_tencent_quote_batch", _fake_tencent)

    result = provider.fetch_realtime_quotes_batch(["000001", "600519"])

    assert result["000001"]["source"] == "sina"
    assert result["600519"]["source"] == "tencent"
    assert tencent_seen == [("600519",)]


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

    _patch_open_market(monkeypatch)
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


def test_force_reconnect_servers_resets_hithink_and_eastmoney_state_without_tdx_speed_test(monkeypatch):
    provider = _make_provider()
    provider._offline = False
    provider._rt_runtime_cooldown_until = 123.0
    provider._rt_runtime_consecutive_failures = 2
    provider._rt_runtime_last_error = "old-error"
    provider._rt_hithink_cooldown_until = 456.0
    provider._rt_hithink_last_error = "old-hithink-error"

    called = []
    monkeypatch.setattr(provider, "_reset_realtime_runtime", lambda *args, **kwargs: called.append((args, kwargs)))

    provider.force_reconnect_servers()

    assert provider._rt_runtime_cooldown_until == 0.0
    assert provider._rt_runtime_consecutive_failures == 0
    assert provider._rt_runtime_last_error == ""
    assert provider._rt_hithink_cooldown_until == 0.0
    assert provider._rt_hithink_last_error == ""
    assert called == [
        (
            ("强制刷新同花顺盘中实时行情连接",),
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
