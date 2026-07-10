# -*- coding: utf-8 -*-
from __future__ import annotations

import datetime as dt
from types import SimpleNamespace


class _CapturingRunner:
    def __init__(self):
        self.calls = []

    def run_in_background(self, fn, *args, on_success=None, on_error=None, task_id=None, **kwargs):
        self.calls.append(
            {
                "fn": fn,
                "args": args,
                "kwargs": kwargs,
                "on_success": on_success,
                "on_error": on_error,
                "task_id": task_id,
            }
        )
        return task_id or "captured"


def test_kline_realtime_timer_defers_provider_network_call(monkeypatch, qt_application):
    from ui import kline_window_runtime as runtime

    provider_calls = []
    applied = []
    runner = _CapturingRunner()
    provider = SimpleNamespace(
        fetch_realtime_quotes_batch=lambda codes: provider_calls.append(tuple(codes))
        or {"000001": {"open": 10, "close": 11}}
    )
    window = SimpleNamespace(
        code="000001",
        _render_generation=3,
        _closing=False,
        _rt_timer=None,
        data_provider=provider,
        _log=SimpleNamespace(debug=lambda *_args: None, warning=lambda *_args: None),
        _get_market=lambda: "CN",
    )

    monkeypatch.setattr(runtime, "task_manager", runner)
    monkeypatch.setattr(runtime.MarketCalendar, "is_quote_refresh_time", lambda _market: True)
    monkeypatch.setattr(runtime, "refresh_last_bar", lambda _window, quote: applied.append(quote))

    runtime.poll_rt_update(window)

    assert provider_calls == []
    assert len(runner.calls) == 1
    pending = runner.calls[0]
    result = pending["fn"]()
    assert provider_calls == [("000001",)]
    pending["on_success"](result)
    assert applied == [{"open": 10, "close": 11}]


def test_kline_asian_cache_file_is_read_inside_background_task(monkeypatch, qt_application):
    from ui import kline_window_qt as kline_module
    from ui.tabs import asian_market_tab as asian_module

    cache_reads = []
    runner = _CapturingRunner()
    monkeypatch.setattr(kline_module, "background_job_runner", runner)
    monkeypatch.setattr(asian_module, "JSON_CACHE", "unused-test-cache.json")
    monkeypatch.setattr(asian_module, "GLOBAL_ASIAN_RT_CACHE", {})
    monkeypatch.setattr(
        kline_module,
        "load_cached_asian_stock",
        lambda path, code: cache_reads.append((path, code)) or None,
    )
    monkeypatch.setattr(
        kline_module.MarketCalendar,
        "get_latest_trade_date",
        classmethod(lambda cls, market="CN", ref_date=None: dt.date(2026, 7, 10)),
    )

    window = SimpleNamespace(
        code="2330.TW",
        name="台积电",
        vcp_data={},
        _render_generation=2,
        _closing=False,
        _get_market=lambda: "TW",
    )

    kline_module.KLineChartWindow._load_asian_chart(window)

    assert cache_reads == []
    assert len(runner.calls) == 1
    assert runner.calls[0]["fn"]()[:3] == (None, None, False)
    assert cache_reads == [("unused-test-cache.json", "2330.TW")]
