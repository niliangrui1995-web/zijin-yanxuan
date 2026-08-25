import datetime as dt
import json
import threading
import time

import pytest

from core.market_calendar import MarketCalendar
from infra.tasks.lifecycle import CancellationToken, TaskCancelledError
from ui.tabs import base_stock_refresh as refresh_module
from vcp import data_provider_quotes
from vcp.data_provider import TdxDataProvider


class _FakeHttpResponse:
    def __init__(self, payload):
        self._payload = json.dumps(payload).encode("utf-8")

    def read(self, _size=-1):
        return self._payload

    def close(self):
        return None


def _make_provider():
    """Build the real provider without its local-TDX startup side effects."""
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
    provider._rt_quote_batch_pause_sec = 0.0
    provider._rt_last_network_probe = {}
    provider._rt_last_pressure_log_at = 0.0
    provider._rt_hithink_enabled = True
    provider._rt_hithink_cooldown_until = 0.0
    provider._rt_hithink_last_error = ""
    provider._rt_eastmoney_cooldown_until = 0.0
    provider._rt_eastmoney_last_error = ""
    provider._rt_last_fallback_log_at = 0.0
    provider._rt_quote_request_history = []
    provider._rt_quote_request_history_max = 64
    provider._rt_quote_request_lock = threading.RLock()
    provider._offline = False
    provider.server_pool = []
    provider.cache_data = {}
    provider.cache_lock = threading.Lock()
    provider.thread_local = threading.local()
    provider._build_offline_quotes = lambda codes: {code: {"close": 0.0} for code in codes}
    return provider


def _patch_open_market(monkeypatch):
    monkeypatch.setattr(MarketCalendar, "is_quote_refresh_time", lambda: True)
    for name in ("get_latest_trade_date", "today"):
        monkeypatch.setattr(MarketCalendar, name, lambda market="CN": dt.date(2026, 8, 25))


def _hithink_payload(codes):
    rows = []
    for index, code in enumerate(codes, start=1):
        thscode = data_provider_quotes.to_hithink_thscode(code)
        rows.append(
            {
                "thscode": thscode,
                "ticker": code,
                "last_price": 10.0 + index,
                "price_change": 0.1,
                "price_change_ratio_pct": 1.0,
                "open_price": 9.9 + index,
                "high_price": 10.1 + index,
                "low_price": 9.8 + index,
                "prev_price": 9.9 + index,
                "volume": 100_000,
                "turnover": 1_000_000.0,
            }
        )
    return {"code": 0, "data": {"timestamp": 1_777_000_000_000, "item": rows}}


def test_same_normalized_cold_batch_uses_one_hithink_request_and_returns_to_both_callers(monkeypatch):
    """Removing the shared in-flight gate would make this issue two HTTP calls."""
    provider = _make_provider()
    request_started = threading.Event()
    release_request = threading.Event()
    calls = []
    results = []
    failures = []

    def _fake_urlopen(request, **_kwargs):
        calls.append(request.full_url)
        request_started.set()
        assert release_request.wait(2.0), "test did not release the Hithink response"
        return _FakeHttpResponse(_hithink_payload(["000001", "600519"]))

    def _fetch(codes, started):
        started.set()
        try:
            results.append(provider.fetch_realtime_quotes_batch(codes))
        except BaseException as exc:  # pragma: no cover - assertion below reports worker failure.
            failures.append(exc)

    monkeypatch.setenv("HITHINK_FINANCE_API_KEY", "unit-test-key")
    monkeypatch.setattr(data_provider_quotes, "urlopen_https", _fake_urlopen)
    _patch_open_market(monkeypatch)

    first_started = threading.Event()
    second_started = threading.Event()
    first = threading.Thread(target=_fetch, args=(["600519", "000001"], first_started))
    second = threading.Thread(target=_fetch, args=(["000001", "600519", "000001"], second_started))
    first.start()
    assert first_started.wait(1.0)
    assert request_started.wait(1.0), f"first worker failed before HTTP: {failures!r}"
    second.start()
    assert second_started.wait(1.0)
    time.sleep(0.1)
    assert len(calls) == 1
    release_request.set()
    first.join(3.0)
    second.join(3.0)

    assert not first.is_alive()
    assert not second.is_alive()
    assert failures == []
    assert len(calls) == 1
    assert len(results) == 2
    assert all(set(result) == {"000001", "600519"} for result in results)
    assert all(result["000001"]["source"] == "hithink" for result in results)


def test_direct_tab_refresh_forwards_cancelled_token_and_does_not_start_hithink_io(monkeypatch):
    """Removing token forwarding would make the cancelled tab still enter HTTP I/O."""
    provider = _make_provider()
    http_calls = []
    captured = {}

    class _Owner:
        data_provider = provider
        _runtime_cleanup_done = False

    def _fake_urlopen(request, **_kwargs):
        http_calls.append(request.full_url)
        return _FakeHttpResponse(_hithink_payload(["000001"]))

    def _capture_background(owner, runner, name, fn, **kwargs):
        del owner, runner, name, kwargs
        captured["fn"] = fn

    monkeypatch.setenv("HITHINK_FINANCE_API_KEY", "unit-test-key")
    monkeypatch.setattr(data_provider_quotes, "urlopen_https", _fake_urlopen)
    monkeypatch.setattr(refresh_module, "_run_owner_background", _capture_background)
    _patch_open_market(monkeypatch)

    owner = _Owner()
    refresh_module._submit_owner_quote_refresh(owner, object(), "tab_quote_refresh", ["000001"])
    token = CancellationToken()
    token.cancel("tab_closed")

    with pytest.raises(TaskCancelledError):
        captured["fn"](token)

    assert http_calls == []


def test_direct_tab_refresh_forwards_active_token_to_hithink_timeout(monkeypatch):
    """Dropping the token would leave Hithink with the provider's full eight-second timeout."""
    provider = _make_provider()
    http_timeouts = []
    captured = {}

    class _Owner:
        data_provider = provider
        _runtime_cleanup_done = False

    def _fake_urlopen(request, **kwargs):
        del request
        http_timeouts.append(float(kwargs["timeout"]))
        return _FakeHttpResponse(_hithink_payload(["000001"]))

    def _capture_background(owner, runner, name, fn, **kwargs):
        del owner, runner, name, kwargs
        captured["fn"] = fn

    monkeypatch.setenv("HITHINK_FINANCE_API_KEY", "unit-test-key")
    monkeypatch.setattr(data_provider_quotes, "urlopen_https", _fake_urlopen)
    monkeypatch.setattr(refresh_module, "_run_owner_background", _capture_background)
    _patch_open_market(monkeypatch)

    refresh_module._submit_owner_quote_refresh(_Owner(), object(), "tab_quote_refresh", ["000001"])
    result = captured["fn"](CancellationToken.with_timeout(0.25))

    assert result["000001"]["source"] == "hithink"
    assert len(http_timeouts) == 1
    assert 0.0 < http_timeouts[0] <= 0.25
