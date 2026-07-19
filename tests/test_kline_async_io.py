# -*- coding: utf-8 -*-
from __future__ import annotations

import datetime as dt
from types import SimpleNamespace

import pytest

from app.services.kline_data_service import KlineDataService
from app.services.ui_task_lifecycle_service import TaskCancelledError
from ui import kline_window_asian as asian
from ui import kline_window_runtime as runtime
from ui.kline_load_controller import KlineLoadController


class _CapturingRunner:
    def __init__(self):
        self.calls = []
        self.abandoned = []
        self.cancelled = []

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

    def abandon_task(self, task_id, *, reason=None):
        self.abandoned.append((str(task_id), reason))
        return True

    def cancel_task(self, task_id, *, reason=None):
        self.cancelled.append((str(task_id), reason))
        return True


def test_kline_realtime_timer_defers_provider_network_call_and_cancels_stale(monkeypatch):
    provider_calls = []
    applied = []
    runner = _CapturingRunner()
    provider = SimpleNamespace(
        fetch_realtime_quotes_batch=lambda codes: (
            provider_calls.append(tuple(codes)) or {"000001": {"open": 10, "close": 11}}
        )
    )
    controller = KlineLoadController(window_id="async-realtime-window")
    controller.begin("000001")
    window = SimpleNamespace(
        code="000001",
        _load_controller=controller,
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
    stale = runner.calls[0]
    assert str(stale["task_id"]) == "kline:async-realtime-window:1:realtime-quote"

    controller.begin("000001")
    runtime.poll_rt_update(window)

    assert len(runner.calls) == 1
    assert runner.abandoned == []
    assert runner.cancelled == [
        ("kline:async-realtime-window:1:realtime-quote", "generation_superseded")
    ]
    with pytest.raises(TaskCancelledError):
        stale["fn"]()
    stale["on_success"]({"open": 9, "close": 9})
    assert applied == []

    stale["kwargs"]["on_terminated"]()
    assert len(runner.calls) == 2
    pending = runner.calls[1]
    assert str(pending["task_id"]) == "kline:async-realtime-window:2:realtime-quote"
    result = pending["fn"]()
    assert provider_calls == [("000001",)]
    pending["on_success"](result)
    assert applied == [{"open": 10, "close": 11}]


def test_asian_history_backfill_is_latest_only_and_ignores_stale_callbacks(monkeypatch):
    runner = _CapturingRunner()
    statuses = []
    rendered = []
    fetches = []
    controller = KlineLoadController(window_id="async-asian-window")
    controller.begin("2330.TW")
    window = SimpleNamespace(
        code="2330.TW",
        name="台积电",
        _load_controller=controller,
        _closing=False,
        vcp_data={},
        code_list=[],
        current_idx=0,
        _refresh_header_context=lambda: None,
        _render_chart=lambda frame, **kwargs: rendered.append((frame, kwargs)),
        _set_status_message=lambda *args, **kwargs: statuses.append((args, kwargs)),
    )

    def fetch(name, code, period):
        fetches.append((name, code, period))
        return {
            "klines": [
                {"date": "2026-07-15", "open": 1, "high": 2, "low": 1, "close": 2}
            ]
        }

    monkeypatch.setattr(runtime, "task_manager", runner)

    asian.schedule_asian_history_backfill(
        window,
        task_manager=runner,
        fetch_single_kline=fetch,
        submit_owned_task=runtime._submit_owned_window_task,
    )
    stale = runner.calls[0]
    assert str(stale["task_id"]) == "kline:async-asian-window:1:asian-history"

    controller.begin("7203.T")
    window.code = "7203.T"
    window.name = "丰田汽车"
    asian.schedule_asian_history_backfill(
        window,
        task_manager=runner,
        fetch_single_kline=fetch,
        submit_owned_task=runtime._submit_owned_window_task,
    )

    assert len(runner.calls) == 1
    assert runner.abandoned == []
    assert runner.cancelled == [
        ("kline:async-asian-window:1:asian-history", "generation_superseded")
    ]
    with pytest.raises(TaskCancelledError):
        stale["fn"]()
    before_stale_callbacks = list(statuses)
    stale["on_success"](({}, None))
    stale["on_error"]("stale")
    assert statuses == before_stale_callbacks
    assert rendered == []

    stale["kwargs"]["on_terminated"]()
    assert len(runner.calls) == 2
    latest = runner.calls[1]
    assert str(latest["task_id"]) == "kline:async-asian-window:2:asian-history"
    result = latest["fn"]()
    latest["on_success"](result)

    assert fetches == [("丰田汽车", "7203.T", "1y")]
    assert len(rendered) == 1
    assert rendered[0][0].iloc[-1]["close"] == 2
    assert statuses[-1][1] == {"tone": "success"}


def test_kline_asian_cache_file_is_read_and_prepared_inside_background_task(monkeypatch):
    from ui.tabs import asian_market_tab as asian_module

    cache_reads = []
    runner = _CapturingRunner()
    monkeypatch.setattr(runtime, "task_manager", runner)
    monkeypatch.setattr(asian_module, "JSON_CACHE", "unused-test-cache.json")
    monkeypatch.setattr(asian_module, "GLOBAL_ASIAN_RT_CACHE", {})

    def load_stock(path, code, *, cancellation_token=None):
        cache_reads.append((path, code, cancellation_token))
        return {
            "ticker": code,
            "klines": [
                {
                    "date": "2026-07-10",
                    "open": 100,
                    "high": 103,
                    "low": 99,
                    "close": 102,
                    "volume": 500,
                }
            ],
        }

    monkeypatch.setattr(
        runtime,
        "KlineDataService",
        lambda provider: KlineDataService(provider, asian_stock_loader=load_stock),
    )
    monkeypatch.setattr(
        runtime.MarketCalendar,
        "get_latest_trade_date",
        classmethod(lambda cls, market="CN", ref_date=None: dt.date(2026, 7, 10)),
    )

    controller = KlineLoadController(window_id="async-cache-window")
    identity = controller.begin("2330.TW")
    window = SimpleNamespace(
        code="2330.TW",
        name="台积电",
        vcp_data={},
        code_list=[],
        current_idx=0,
        data_provider=object(),
        _load_controller=controller,
        _snapshot_version=0,
        _closing=False,
        _set_status_message=lambda *args, **kwargs: None,
    )

    runtime.load_and_draw(window, identity)

    assert cache_reads == []
    assert len(runner.calls) == 1
    pending = runner.calls[0]
    assert str(pending["task_id"]) == "kline:async-cache-window:1:history"

    result = pending["fn"]()

    assert cache_reads[0][:2] == ("unused-test-cache.json", "2330.TW")
    assert cache_reads[0][2] is pending["kwargs"]["cancellation_token"]
    assert result.data_result.source == "asian_json_cache"
    assert result.frame.iloc[-1]["close"] == 102
    assert result.prepared.identity == ("async-cache-window", 1, "2330.TW")
    assert result.prepared.point_count == 1
