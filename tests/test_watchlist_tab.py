# -*- coding: utf-8 -*-
from copy import deepcopy
from types import SimpleNamespace

import pytest
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor, QHideEvent, QShowEvent
from PyQt6.QtTest import QSignalSpy
from PyQt6.QtWidgets import QApplication, QPushButton, QWidget

from app.services.stock_context_model_service import StockContextSnapshot
from core.event_bus import event_bus
from ui.tabs import watchlist_tab as watchlist_module
from ui.theme import theme_manager
from ui.viewmodels.watchlist_vm import watchlist_vm


class _DummyProvider:
    def __init__(self):
        self.code2name = {"600519": "贵州茅台"}

    def is_online(self):
        return False


def _patch_watchlist_constructor(monkeypatch):
    monkeypatch.setattr(watchlist_module.WatchlistTab, "subscribe_global_quotes", lambda self: None)
    monkeypatch.setattr(watchlist_module.WatchlistTab, "_load_special_data", lambda self: None)
    monkeypatch.setattr(
        watchlist_module.WatchlistTab,
        "bind_header_persistence",
        lambda self, table, settings_key="header_state": None,
        raising=False,
    )
    monkeypatch.setattr(watchlist_module.QTimer, "singleShot", staticmethod(lambda *_args, **_kwargs: None))


def _run_background_inline(calls=None):
    def _run(fn, *args, on_success=None, on_error=None, task_id=None, **kwargs):
        if calls is not None:
            calls.append({"fn": fn, "args": args, "kwargs": kwargs, "task_id": task_id})
        result = fn(*args, **kwargs)
        if on_success is not None:
            on_success(result)
        return task_id or "inline"

    return _run


def test_watchlist_startup_can_skip_indicator_refresh(monkeypatch):
    calc_calls = []
    monkeypatch.setattr(watchlist_module.WatchlistTab, "subscribe_global_quotes", lambda self: None)
    monkeypatch.setattr(watchlist_module.task_manager, "run_in_background", _run_background_inline())
    monkeypatch.setattr(
        watchlist_module.watchlist_vm,
        "get_watchlist_data",
        lambda: {"600519": {"名称": "贵州茅台"}},
    )
    monkeypatch.setattr(
        watchlist_module.WatchlistTab,
        "_request_vcp_calc",
        lambda self, *args, **kwargs: calc_calls.append((args, kwargs)),
    )

    tab = watchlist_module.WatchlistTab(
        _DummyProvider(),
        startup_indicator_refresh_enabled=False,
    )
    try:
        assert calc_calls == []
        assert tab._delayed_special_timer is None
        assert tab.model.row_data
    finally:
        tab.shutdown()
        tab.deleteLater()


def test_watchlist_startup_can_delay_indicator_refresh_and_skip_followup(monkeypatch):
    calc_calls = []
    monkeypatch.setattr(watchlist_module.WatchlistTab, "subscribe_global_quotes", lambda self: None)
    monkeypatch.setattr(watchlist_module.task_manager, "run_in_background", _run_background_inline())
    monkeypatch.setattr(
        watchlist_module.watchlist_vm,
        "get_watchlist_data",
        lambda: {"600519": {"名称": "贵州茅台"}},
    )
    monkeypatch.setattr(
        watchlist_module.WatchlistTab,
        "_request_vcp_calc",
        lambda self, *args, **kwargs: calc_calls.append((args, kwargs)),
    )

    tab = watchlist_module.WatchlistTab(
        _DummyProvider(),
        startup_indicator_refresh_delay_ms=1800,
        startup_followup_refresh_enabled=False,
    )
    try:
        assert calc_calls == [((), {"delay_ms": 1800})]
        assert tab._delayed_special_timer is None
        assert tab.model.row_data
    finally:
        tab.shutdown()
        tab.deleteLater()


def test_watchlist_first_open_defers_data_until_background_delivery(monkeypatch):
    submitted = {}
    load_calls = []

    def _capture_task(fn, *args, on_success=None, on_error=None, task_id=None, **kwargs):
        submitted.update(
            {
                "fn": fn,
                "args": args,
                "on_success": on_success,
                "on_error": on_error,
                "task_id": task_id,
                "kwargs": kwargs,
            }
        )
        return task_id

    monkeypatch.setattr(watchlist_module.WatchlistTab, "subscribe_global_quotes", lambda self: None)
    monkeypatch.setattr(watchlist_module.task_manager, "run_in_background", _capture_task)
    monkeypatch.setattr(
        watchlist_module.watchlist_vm,
        "get_watchlist_data",
        lambda: load_calls.append("load") or {"600519": {"名称": "贵州茅台"}},
    )
    monkeypatch.setattr(
        watchlist_module.WatchlistTab,
        "_refresh_quotes_from_store_or_live",
        lambda self, **kwargs: None,
    )
    monkeypatch.setattr(watchlist_module.WatchlistTab, "_latest_trade_date_text", lambda self: "2026-07-15")

    tab = watchlist_module.WatchlistTab(
        _DummyProvider(),
        startup_indicator_refresh_enabled=False,
    )
    try:
        assert submitted["task_id"] == "watchlist_initial_data"
        assert load_calls == []
        assert tab.model.row_data == []

        payload = submitted["fn"]()

        assert load_calls == ["load"]
        assert tab.model.row_data == []

        submitted["on_success"](payload)

        assert [row["代码"] for row in tab.model.row_data] == ["600519"]
    finally:
        tab.shutdown()
        tab.deleteLater()


def test_watchlist_initial_job_contains_only_plain_snapshots():
    code_names = {"600519": "贵州茅台"}
    live_rows = {"600519": {"现价": "1500", "涨幅%": "1.2", "市值": "2.0万亿"}}

    job = watchlist_module._build_watchlist_initial_job(code_names, live_rows)
    code_names.clear()
    live_rows["600519"]["现价"] = "0"

    assert job.func is watchlist_module._load_watchlist_initial_payload
    assert job.args[0] == {"600519": "贵州茅台"}
    assert job.args[1]["600519"]["现价"] == "1500"
    assert all(not isinstance(value, QWidget) for value in job.args)


def test_watchlist_initial_payload_merges_quote_snapshot_before_model_reset(monkeypatch):
    monkeypatch.setattr(
        watchlist_module.watchlist_vm,
        "get_watchlist_data",
        lambda: {"600519": {"名称": "贵州茅台"}},
    )

    payload = watchlist_module._load_watchlist_initial_payload(
        {"600519": "贵州茅台"},
        {},
        None,
    )

    rows = watchlist_module._merge_watchlist_quote_snapshot(
        payload["rows"],
        {"600519": {"close": 1510.0, "last_close": 1500.0, "zongguben": 1_000_000_000}},
    )

    row = rows[0]
    assert row["现价"] == "1510.00"
    assert row["涨幅%"] == pytest.approx(2 / 3)
    assert row["市值"] == "15100亿"
    assert row["_zongguben"] == 1_000_000_000


def test_watchlist_initial_delivery_uses_latest_quote_snapshot(monkeypatch):
    submitted = {}

    def _capture_task(fn, *args, on_success=None, on_error=None, task_id=None, **kwargs):
        submitted.update({"fn": fn, "on_success": on_success, "task_id": task_id})
        return task_id

    monkeypatch.setattr(watchlist_module.WatchlistTab, "subscribe_global_quotes", lambda self: None)
    monkeypatch.setattr(watchlist_module.task_manager, "run_in_background", _capture_task)
    monkeypatch.setattr(
        watchlist_module.watchlist_vm,
        "get_watchlist_data",
        lambda: {"600519": {"名称": "贵州茅台"}},
    )
    latest = {
        "600519": {"close": 1520.0, "last_close": 1500.0, "zongguben": 1_000_000_000}
    }
    monkeypatch.setattr(
        watchlist_module,
        "_capture_latest_quote_snapshot",
        lambda codes=None: {code: dict(latest[code]) for code in (codes or latest) if code in latest},
    )

    tab = watchlist_module.WatchlistTab(
        _DummyProvider(),
        startup_indicator_refresh_enabled=False,
        startup_followup_refresh_enabled=False,
    )
    try:
        payload = submitted["fn"]()
        latest["600519"]["close"] = 1530.0

        submitted["on_success"](payload)

        assert tab.model.row_data[0]["现价"] == "1530.00"
    finally:
        tab.shutdown()
        tab.deleteLater()


def test_watchlist_pending_quote_timer_is_cancelled_on_shutdown(monkeypatch):
    _patch_watchlist_constructor(monkeypatch)
    calls = []
    tab = watchlist_module.WatchlistTab(_DummyProvider())
    try:
        monkeypatch.setattr(
            tab,
            "_run_async_local_quote_refresh",
            lambda task_id: calls.append((task_id, tab._closing)),
        )
        tab._refresh_quotes_async_local(quote_task_id="watchlist_quotes")
        assert tab._quote_refresh_timer is not None
        assert tab._quote_refresh_timer.isActive()

        tab.shutdown()
        QApplication.instance().processEvents()

        assert calls == []
        assert not tab._quote_refresh_timer.isActive()
        assert tab._pending_quote_task_id is None
    finally:
        tab.deleteLater()


def test_watchlist_loading_overlay_is_delayed_and_cancelled_for_quick_delivery(monkeypatch):
    submitted = {}
    loading_calls = []

    def _capture_task(fn, *args, on_success=None, on_error=None, task_id=None, **kwargs):
        submitted.update({"fn": fn, "on_success": on_success, "task_id": task_id})
        return task_id

    monkeypatch.setattr(watchlist_module.WatchlistTab, "subscribe_global_quotes", lambda self: None)
    monkeypatch.setattr(watchlist_module.task_manager, "run_in_background", _capture_task)
    monkeypatch.setattr(
        watchlist_module.TableStateWrapper,
        "show_loading",
        lambda self, *args, **kwargs: loading_calls.append((args, kwargs)),
    )
    monkeypatch.setattr(
        watchlist_module.watchlist_vm,
        "get_watchlist_data",
        lambda: {"600519": {"名称": "贵州茅台"}},
    )

    tab = watchlist_module.WatchlistTab(
        _DummyProvider(),
        startup_indicator_refresh_enabled=False,
        startup_followup_refresh_enabled=False,
    )
    try:
        assert loading_calls == []
        assert tab._initial_loading_timer is not None
        assert tab._initial_loading_timer.isActive()

        submitted["on_success"](submitted["fn"]())

        assert loading_calls == []
        assert not tab._initial_loading_timer.isActive()
        assert tab.model.row_data
    finally:
        tab.shutdown()
        tab.deleteLater()


def test_watchlist_loading_overlay_appears_only_when_pending_load_is_slow(monkeypatch):
    submitted = {}
    loading_calls = []

    def _capture_task(fn, *args, on_success=None, on_error=None, task_id=None, **kwargs):
        submitted.update({"on_success": on_success, "task_id": task_id})
        return task_id

    monkeypatch.setattr(watchlist_module.WatchlistTab, "subscribe_global_quotes", lambda self: None)
    monkeypatch.setattr(watchlist_module.task_manager, "run_in_background", _capture_task)
    monkeypatch.setattr(
        watchlist_module.TableStateWrapper,
        "show_loading",
        lambda self, *args, **kwargs: loading_calls.append((args, kwargs)),
    )

    tab = watchlist_module.WatchlistTab(
        _DummyProvider(),
        startup_indicator_refresh_enabled=False,
        startup_followup_refresh_enabled=False,
    )
    try:
        tab._show_initial_loading_if_pending()
        assert len(loading_calls) == 1

        tab._finish_initial_data_loading()
        tab._show_initial_loading_if_pending()
        assert len(loading_calls) == 1
    finally:
        tab.shutdown()
        tab.deleteLater()


def test_watchlist_cancel_invalidates_queued_lineage_and_loading_overlay(monkeypatch):
    scheduled = []
    lineage_calls = []
    monkeypatch.setattr(watchlist_module.WatchlistTab, "subscribe_global_quotes", lambda self: None)
    monkeypatch.setattr(
        watchlist_module.WatchlistTab,
        "bind_header_persistence",
        lambda self, table, settings_key="header_state": None,
        raising=False,
    )
    monkeypatch.setattr(
        watchlist_module.QTimer,
        "singleShot",
        lambda delay, callback: scheduled.append((delay, callback)),
    )

    tab = watchlist_module.WatchlistTab(_DummyProvider(), startup_tasks_enabled=False)
    tab._describe_watchlist_rows = lambda rows: lineage_calls.append(rows)
    try:
        tab._initial_data_loading = True
        watchlist_module._apply_special_data_payload(
            tab,
            {"rows": []},
            refresh_indicators=False,
            indicator_delay_ms=None,
        )
        stale_callback = scheduled[-1][1]
        tab._initial_data_loading = True

        receipt = tab.cancel_background_preload(reason="step_timeout")

        assert receipt.is_settled() is True
        assert tab._initial_data_loading is False
        stale_callback()
        assert lineage_calls == []
    finally:
        tab.shutdown()
        tab.deleteLater()


def test_watchlist_initial_delivery_is_ignored_after_shutdown(monkeypatch):
    submitted = {}

    def _capture_task(fn, *args, on_success=None, on_error=None, task_id=None, **kwargs):
        submitted.update({"on_success": on_success, "task_id": task_id})
        return task_id

    monkeypatch.setattr(watchlist_module.WatchlistTab, "subscribe_global_quotes", lambda self: None)
    monkeypatch.setattr(watchlist_module.task_manager, "run_in_background", _capture_task)
    monkeypatch.setattr(watchlist_module.task_manager, "cancel_task", lambda *args, **kwargs: True)
    monkeypatch.setattr(watchlist_module.task_manager, "wait_for_tasks", lambda *args, **kwargs: True)

    tab = watchlist_module.WatchlistTab(
        _DummyProvider(),
        startup_indicator_refresh_enabled=False,
    )
    try:
        tab.shutdown()
        submitted["on_success"](
            {"rows": [{"代码": "600519", "名称": "贵州茅台"}], "elapsed_ms": 1.0, "row_count": 1}
        )

        assert tab.model.row_data == []
    finally:
        tab.deleteLater()


def test_watchlist_first_open_records_segmented_metrics(monkeypatch):
    metrics = []
    monkeypatch.setattr(watchlist_module.WatchlistTab, "subscribe_global_quotes", lambda self: None)
    monkeypatch.setattr(watchlist_module.task_manager, "run_in_background", _run_background_inline())
    monkeypatch.setattr(
        watchlist_module.watchlist_vm,
        "get_watchlist_data",
        lambda: {"600519": {"名称": "贵州茅台"}},
    )
    monkeypatch.setattr(
        watchlist_module.WatchlistTab,
        "_refresh_quotes_from_store_or_live",
        lambda self, **kwargs: None,
    )
    monkeypatch.setattr(watchlist_module.WatchlistTab, "_latest_trade_date_text", lambda self: "2026-07-15")
    monkeypatch.setattr(
        watchlist_module,
        "record_metric",
        lambda name, value, **kwargs: metrics.append((name, float(value), kwargs)),
    )

    tab = watchlist_module.WatchlistTab(
        _DummyProvider(),
        startup_indicator_refresh_enabled=False,
    )
    try:
        metric_names = [name for name, _value, _kwargs in metrics]
        assert metric_names == [
            "watchlist_tab_import_ms",
            "watchlist_tab_ui_construct_ms",
            "watchlist_tab_initial_data_ms",
        ]
        assert all(value >= 0 for _name, value, _kwargs in metrics)
    finally:
        tab.shutdown()
        tab.deleteLater()


def test_watchlist_prime_startup_state_respects_indicator_delay(monkeypatch):
    calc_calls = []
    monkeypatch.setattr(watchlist_module.WatchlistTab, "subscribe_global_quotes", lambda self: None)
    monkeypatch.setattr(
        watchlist_module.WatchlistTab,
        "_refresh_quotes_from_store_or_live",
        lambda self, *args, **kwargs: None,
    )
    monkeypatch.setattr(
        watchlist_module.WatchlistTab,
        "_request_vcp_calc",
        lambda self, *args, **kwargs: calc_calls.append((args, kwargs)),
    )

    tab = watchlist_module.WatchlistTab(
        _DummyProvider(),
        startup_tasks_enabled=False,
        startup_indicator_refresh_delay_ms=1800,
    )
    try:
        tab.model.update_data([{"\u4ee3\u7801": "600519"}])

        tab.prime_startup_state()

        assert calc_calls == [((), {"delay_ms": 1800, "allow_noninteractive": True})]
    finally:
        tab.shutdown()
        tab.deleteLater()


def test_watchlist_background_preload_waits_for_rows_and_converges_once(monkeypatch):
    _patch_watchlist_constructor(monkeypatch)
    cache_snapshot_calls = []
    calc_calls = []
    live_quote_calls = []
    tab = watchlist_module.WatchlistTab(
        _DummyProvider(),
        startup_tasks_enabled=False,
        startup_indicator_refresh_enabled=False,
    )
    try:
        tab._workspace_noninteractive_loaded = True
        tab._task_lifecycle = SimpleNamespace(active_names=("initial_data",), shutdown=lambda **_kwargs: True)
        tab._apply_quote_store_snapshot = lambda: cache_snapshot_calls.append("cache")
        tab._request_vcp_calc = lambda *args, **kwargs: calc_calls.append((args, kwargs))
        tab._can_fetch_live_quotes_now = lambda: True
        tab._refresh_quotes_async_local = lambda **kwargs: live_quote_calls.append(kwargs)
        tab._refresh_quotes_from_store_or_live = lambda **_kwargs: (_ for _ in ()).throw(
            AssertionError("background preload must stay cache-only")
        )

        assert tab.prime_background_load() is False
        assert tab.is_background_preload_complete() is False

        watchlist_module._apply_special_data_payload(
            tab,
            {"rows": [{"代码": "600519", "名称": "贵州茅台"}]},
            refresh_indicators=False,
            indicator_delay_ms=None,
        )

        assert cache_snapshot_calls == ["cache"]
        assert live_quote_calls == []
        assert calc_calls == [
            ((), {"delay_ms": tab._startup_indicator_refresh_delay_ms, "allow_noninteractive": True})
        ]
        assert tab.prime_background_load() is False
        assert cache_snapshot_calls == ["cache"]

        tab._background_preload.vcp_generation = 7
        tab._complete_background_preload_vcp(7)
        assert tab.is_background_preload_complete() is True

        tab._workspace_noninteractive_loaded = False
        watchlist_module._apply_watchlist_rows(
            tab,
            [{"代码": "000001", "名称": "平安银行"}],
            refresh_quote_store=False,
            describe_rows=False,
        )
        assert len(live_quote_calls) == 1
    finally:
        tab.shutdown()
        tab.deleteLater()


def test_watchlist_hidden_background_preload_commits_metrics_before_ready(monkeypatch):
    _patch_watchlist_constructor(monkeypatch)
    restored_current = {"key": "scan"}
    tab = watchlist_module.WatchlistTab(
        _DummyProvider(),
        startup_tasks_enabled=False,
        startup_indicator_refresh_delay_ms=60_000,
    )
    tab._is_current_workspace_tab = lambda: restored_current["key"] == "watchlist"
    tab._apply_quote_store_snapshot = lambda: None
    tab._schedule_watchlist_metrics_persist = lambda _payload: None
    tab.model.update_data([{"代码": "600519", "名称": "贵州茅台", "RPS强度": "--"}])

    try:
        assert tab.prime_background_load() is True
        assert tab._vcp_calc_timer.isActive() is True
        assert tab.is_background_preload_complete() is False
        tab.hideEvent(QHideEvent())
        assert tab._vcp_calc_timer.isActive() is True
        assert tab._pending_vcp_calc is False

        tab._vcp_calc_timer.stop()
        tab._vcp_task_generation = 7
        tab._background_preload.vcp_generation = 7
        payload = {
            "600519": {
                "rps": "95",
                "subsector": "白酒",
                "remark": "核心资产",
            }
        }
        tab._last_vcp_payload_signature = tab._vcp_payload_signature(payload)
        watchlist_module._emit_vcp_if_current(tab, 7, payload)

        assert tab._deferred_vcp_payload is None
        assert tab._pending_vcp_apply_payload == payload
        assert tab._vcp_apply_timer.isActive() is True
        assert tab.model.row_data[0]["RPS强度"] == "--"
        assert tab.is_background_preload_complete() is False
        tab.hideEvent(QHideEvent())
        assert tab._vcp_apply_timer.isActive() is True

        tab._vcp_apply_timer.stop()
        tab._flush_pending_vcp_apply()

        assert tab.model.row_data[0]["RPS强度"] == "95"
        assert tab.model.row_data[0]["细分板块"] == "白酒"
        assert tab._background_preload.vcp_committed is True
        assert tab._background_preload.vcp_pending is False
        assert tab._pending_vcp_apply_payload is None
        assert tab._deferred_vcp_payload is None
        assert tab.is_background_preload_complete() is True

        tab._deferred_vcp_payload = {"stale": {}}
        assert tab.is_background_preload_complete() is False
        tab._deferred_vcp_payload = None
        assert tab.is_background_preload_complete() is True

        late_applies = []
        monkeypatch.setattr(
            tab,
            "_apply_vcp_indicators_ui",
            lambda payload: late_applies.append(payload),
        )
        restored_current["key"] = "watchlist"
        tab.showEvent(QShowEvent())
        tab.on_workspace_tab_activated()
        assert late_applies == []
    finally:
        tab.shutdown()
        tab.deleteLater()


def test_watchlist_background_prewarm_blocks_auto_indicator_recalc(monkeypatch):
    monkeypatch.setattr(watchlist_module.WatchlistTab, "subscribe_global_quotes", lambda self: None)
    monkeypatch.setattr(watchlist_module.WatchlistTab, "_load_special_data", lambda self: None)
    monkeypatch.setattr(
        watchlist_module.WatchlistTab,
        "_refresh_quotes_from_store_or_live",
        lambda self, *args, **kwargs: None,
    )

    tab = watchlist_module.WatchlistTab(
        _DummyProvider(),
        startup_tasks_enabled=False,
        startup_indicator_refresh_enabled=False,
    )
    try:
        tab._workspace_noninteractive_loaded = True

        tab._request_vcp_calc(delay_ms=0)
        assert not hasattr(tab, "_vcp_calc_timer")

        tab.model.update_data([{"代码": "600519"}])
        tab.prime_startup_state()

        assert hasattr(tab, "_vcp_calc_timer")
        assert tab._vcp_calc_timer.isActive() is True
    finally:
        tab.shutdown()
        tab.deleteLater()


def test_watchlist_hidden_context_update_defers_indicator_recalc(monkeypatch):
    monkeypatch.setattr(watchlist_module.WatchlistTab, "subscribe_global_quotes", lambda self: None)
    monkeypatch.setattr(watchlist_module.WatchlistTab, "_load_special_data", lambda self: None)
    monkeypatch.setattr(
        watchlist_module.WatchlistTab,
        "_refresh_quotes_from_store_or_live",
        lambda self, *args, **kwargs: None,
    )

    current = {"value": False}
    applied = []
    tab = watchlist_module.WatchlistTab(_DummyProvider(), startup_tasks_enabled=False)
    tab._is_current_workspace_tab = lambda: current["value"]
    tab._apply_vcp_indicators_ui = lambda payload: applied.append(payload)
    try:
        watchlist_module.event_bus.sig_stock_context_snapshot_updated.emit()

        assert tab._pending_vcp_calc is True
        assert not hasattr(tab, "_vcp_calc_timer")

        tab._on_vcp_watchlist_ready({"600519": {"rps": "95"}})
        assert applied == []
        assert tab._pending_vcp_calc is True

        current["value"] = True
        tab.showEvent(QShowEvent())

        assert tab._pending_vcp_calc is False
        assert hasattr(tab, "_vcp_calc_timer")
        assert tab._vcp_calc_timer.isActive() is True
        assert tab._vcp_calc_timer.interval() == watchlist_module.WatchlistTab.POST_SHOW_VCP_CALC_DELAY_MS
    finally:
        tab.shutdown()
        tab.deleteLater()


def test_watchlist_ready_payload_queues_visible_apply(monkeypatch):
    monkeypatch.setattr(watchlist_module.WatchlistTab, "subscribe_global_quotes", lambda self: None)
    monkeypatch.setattr(watchlist_module.WatchlistTab, "_load_special_data", lambda self: None)
    now = {"value": 100.4}
    monkeypatch.setattr(watchlist_module.time, "monotonic", lambda: now["value"])

    applied = []
    tab = watchlist_module.WatchlistTab(_DummyProvider(), startup_tasks_enabled=False)
    tab._is_active_workspace_tab_for_vcp = lambda: True
    tab._apply_vcp_indicators_ui = lambda payload: applied.append(payload)
    tab._last_vcp_tab_shown_at = 100.0
    try:
        payload = {"600519": {"rps": "95"}}

        tab._on_vcp_watchlist_ready(payload)

        assert applied == []
        assert tab._vcp_apply_timer.isActive() is True
        assert tab._vcp_apply_timer.interval() >= 2000

        tab._flush_pending_vcp_apply()

        assert applied == [payload]
    finally:
        tab.shutdown()
        tab.deleteLater()


def test_watchlist_context_updates_are_throttled_intraday(monkeypatch):
    monkeypatch.setattr(watchlist_module.WatchlistTab, "subscribe_global_quotes", lambda self: None)
    monkeypatch.setattr(watchlist_module.WatchlistTab, "_load_special_data", lambda self: None)
    monkeypatch.setattr(
        watchlist_module.WatchlistTab,
        "_refresh_quotes_from_store_or_live",
        lambda self, *args, **kwargs: None,
    )

    now = {"value": 100.0}
    monkeypatch.setattr(watchlist_module.time, "monotonic", lambda: now["value"])
    task_calls = []
    monkeypatch.setattr(
        watchlist_module.task_manager,
        "run_in_background",
        lambda fn, *args, **kwargs: task_calls.append({"fn": fn, "args": args, "kwargs": kwargs}),
    )

    tab = watchlist_module.WatchlistTab(_DummyProvider(), startup_tasks_enabled=False)
    tab._is_active_workspace_tab_for_vcp = lambda: True
    try:
        tab.model.update_data([{"\u4ee3\u7801": "600519"}])

        tab._request_vcp_calc(delay_ms=0)
        tab._do_vcp_calc()

        assert task_calls
        assert tab._last_vcp_calc_started_at == 100.0

        now["value"] = 120.0
        tab._on_cache_or_earnings_updated()

        assert tab._vcp_calc_timer.interval() == 40_000
    finally:
        tab.shutdown()
        tab.deleteLater()


def test_watchlist_na_daily_update_uses_intraday_throttle(monkeypatch):
    monkeypatch.setattr(watchlist_module.WatchlistTab, "subscribe_global_quotes", lambda self: None)
    monkeypatch.setattr(watchlist_module.WatchlistTab, "_load_special_data", lambda self: None)
    calls = []
    monkeypatch.setattr(
        watchlist_module.WatchlistTab,
        "_request_vcp_calc",
        lambda self, *args, **kwargs: calls.append((args, kwargs)),
    )

    tab = watchlist_module.WatchlistTab(_DummyProvider(), startup_tasks_enabled=False)
    try:
        tab._on_na_daily_updated()

        assert calls == [((), {"min_interval_ms": watchlist_module.WatchlistTab.CONTEXT_REFRESH_MIN_INTERVAL_MS})]
    finally:
        tab.shutdown()
        tab.deleteLater()


def test_watchlist_na_daily_update_defers_when_workspace_tab_hidden(monkeypatch, qt_application):
    monkeypatch.setattr(watchlist_module.WatchlistTab, "subscribe_global_quotes", lambda self: None)
    monkeypatch.setattr(watchlist_module.WatchlistTab, "_load_special_data", lambda self: None)

    host = QWidget()
    current_widget = QWidget(host)
    host._workspace = SimpleNamespace(tabs=SimpleNamespace(currentWidget=lambda: current_widget))
    tab = watchlist_module.WatchlistTab(_DummyProvider(), parent=host, startup_tasks_enabled=False)
    try:
        tab.model.update_data([{"代码": "600519"}])

        tab._on_na_daily_updated()

        assert tab._pending_vcp_calc is True
        assert not hasattr(tab, "_vcp_calc_timer")
    finally:
        tab.shutdown()
        tab.deleteLater()
        current_widget.deleteLater()
        host.deleteLater()


def test_watchlist_vcp_calc_queries_plain_snapshot_inside_background_task(monkeypatch):
    monkeypatch.setattr(watchlist_module.WatchlistTab, "subscribe_global_quotes", lambda self: None)
    monkeypatch.setattr(watchlist_module.WatchlistTab, "_load_special_data", lambda self: None)
    monkeypatch.setattr(
        watchlist_module.WatchlistTab,
        "_refresh_quotes_from_store_or_live",
        lambda self, *args, **kwargs: None,
    )

    queued = []

    def _queue_background(fn, *args, **kwargs):
        queued.append({"fn": fn, "args": args, "kwargs": kwargs})
        return kwargs.get("task_id")

    monkeypatch.setattr(watchlist_module.task_manager, "run_in_background", _queue_background)

    tab = watchlist_module.WatchlistTab(_DummyProvider(), startup_tasks_enabled=False)
    tab._is_active_workspace_tab_for_vcp = lambda: True
    gather_calls = []
    tab._gather_radar_data = lambda codes: gather_calls.append(list(codes)) or ({}, {}, {}, {}, {}, {})
    capture_calls = []
    rps_load_calls = []

    def _capture_workspace_stock_context(_workspace, *, include_rps_bundle):
        capture_calls.append(include_rps_bundle)
        return StockContextSnapshot()

    monkeypatch.setattr(watchlist_module, "capture_workspace_stock_context", _capture_workspace_stock_context)
    monkeypatch.setattr(
        watchlist_module,
        "load_active_rps_payload",
        lambda: rps_load_calls.append("rps") or {},
    )
    try:
        tab.model.update_data([{"\u4ee3\u7801": "600519"}])

        tab._do_vcp_calc()

        assert gather_calls == []
        assert capture_calls == [False]
        assert rps_load_calls == []
        assert queued[0]["kwargs"]["task_id"] == "watchlist_vcp_refresh"
        closure = dict(
            zip(
                queued[0]["fn"].__code__.co_freevars,
                (cell.cell_contents for cell in queued[0]["fn"].__closure__),
                strict=True,
            )
        )
        assert tab not in closure["fn"].args
        queued[0]["fn"]()
        assert gather_calls == []
        assert rps_load_calls == ["rps"]
    finally:
        tab.shutdown()
        tab.deleteLater()


def test_watchlist_source_column_is_hidden_from_display_model():
    tab = watchlist_module.WatchlistTab(_DummyProvider())
    try:
        tab.model.update_data(
            [
                {
                    "代码": "600519",
                    "名称": "贵州茅台",
                    "来源": "手动｜龙虎榜",
                    "现价": "--",
                    "涨幅%": "--",
                    "市值": "--",
                    "RPS强度": "--",
                    "细分板块": "",
                    "摘要": "",
                    "备注": "",
                    "业绩异动": "",
                    "大宗交易": "",
                    "龙虎榜": "",
                    "来源标签": ["手动", "龙虎榜"],
                }
            ]
        )

        assert "来源" not in tab.model.headers
        assert "摘要" in tab.model.headers
        assert "备注" in tab.model.headers
        assert "业绩异动" not in tab.model.headers
        assert "大宗交易" not in tab.model.headers
        assert "龙虎榜" not in tab.model.headers
        assert tab.model.get_row_data(0)["来源"] == "手动｜龙虎榜"
    finally:
        tab.deleteLater()


def test_watchlist_lhb_column_displays_buy_point_rocket(monkeypatch):
    _patch_watchlist_constructor(monkeypatch)

    captured = {}

    def _fake_bulk_patch_entries(payload, remove_keys=None):
        captured["payload"] = payload
        return True

    persist_calls = []
    monkeypatch.setattr(watchlist_vm, "bulk_patch_entries", _fake_bulk_patch_entries)
    monkeypatch.setattr(watchlist_module.task_manager, "run_in_background", _run_background_inline(persist_calls))

    tab = watchlist_module.WatchlistTab(_DummyProvider())
    try:
        tab.model.update_data(
            [
                {
                    "代码": "600519",
                    "名称": "贵州茅台",
                    "来源": "手动",
                    "现价": "--",
                    "涨幅%": "--",
                    "市值": "--",
                    "RPS强度": "",
                    "细分板块": "",
                    "摘要": "",
                    "备注": "",
                    "业绩异动": "",
                    "大宗交易": "",
                    "龙虎榜": "",
                    "龙虎榜日期": "",
                    "龙虎榜净额(万)": "",
                    "来源标签": ["手动"],
                }
            ]
        )

        tab._apply_vcp_indicators_ui(
            {
                "600519": {
                    "rps": "",
                    "subsector": "",
                    "remark": "",
                    "block_trade": "",
                    "block_trade_amount_wan": "",
                    "earnings": "",
                    "earnings_qoq_pct": "",
                    "lhb": {
                        "text": "04-20 | 净买1200万",
                        "date": "20260420",
                        "net_wan": 1200,
                        "buy_point": "触发",
                    },
                }
            }
        )

        row = tab.model.row_data[0]
        note_idx = tab.model.index(0, tab.model.headers.index("备注"))

        assert row["龙虎榜"] == "触发"
        assert row["龙虎榜日期"] == "20260420"
        assert row["龙虎榜净额(万)"] == 1200
        assert row["备注"] == "🚀"
        assert tab.model.data(note_idx, Qt.ItemDataRole.DisplayRole) == "🚀"
        assert captured["payload"]["600519"]["龙虎榜"] == "触发"
    finally:
        tab.deleteLater()


def test_watchlist_lhb_note_stays_blank_when_no_buy_point(monkeypatch):
    _patch_watchlist_constructor(monkeypatch)

    captured = {}

    def _fake_bulk_patch_entries(payload, remove_keys=None):
        captured["payload"] = payload
        return True

    monkeypatch.setattr(watchlist_vm, "bulk_patch_entries", _fake_bulk_patch_entries)
    monkeypatch.setattr(watchlist_module.task_manager, "run_in_background", _run_background_inline())

    tab = watchlist_module.WatchlistTab(_DummyProvider())
    try:
        tab.model.update_data(
            [
                {
                    "代码": "600519",
                    "名称": "贵州茅台",
                    "来源": "手动",
                    "现价": "--",
                    "涨幅%": "--",
                    "市值": "--",
                    "RPS强度": "",
                    "细分板块": "",
                    "摘要": "",
                    "备注": "",
                    "业绩异动": "",
                    "大宗交易": "",
                    "龙虎榜": "",
                    "龙虎榜日期": "",
                    "龙虎榜净额(万)": "",
                    "来源标签": ["手动"],
                }
            ]
        )

        tab._apply_vcp_indicators_ui(
            {
                "600519": {
                    "rps": "",
                    "subsector": "",
                    "remark": "",
                    "block_trade": "",
                    "block_trade_amount_wan": "",
                    "earnings": "",
                    "earnings_qoq_pct": "",
                    "lhb": {
                        "text": "04-20 | 净买1200万",
                        "date": "20260420",
                        "net_wan": 1200,
                        "buy_point": "",
                    },
                }
            }
        )

        row = tab.model.row_data[0]

        assert row["龙虎榜"] == ""
        assert row["备注"] == ""
        assert row["龙虎榜日期"] == "20260420"
        assert row["龙虎榜净额(万)"] == 1200
        assert captured["payload"]["600519"]["龙虎榜"] == ""
    finally:
        tab.deleteLater()


def test_watchlist_detail_columns_use_muted_text_like_lhb_context():
    tab = watchlist_module.WatchlistTab(_DummyProvider())
    try:
        tab.model.update_data(
            [
                {
                    "代码": "600519",
                    "名称": "贵州茅台",
                    "来源": "手动｜龙虎榜",
                    "现价": "1688.00",
                    "涨幅%": "1.20",
                    "市值": "21000亿",
                    "RPS强度": "95/93",
                    "细分板块": "白酒",
                    "摘要": "AI链备注",
                    "备注": "预增25% / 机构专用买入2709万 / 04-20 | 净买1200万",
                    "业绩异动": "预增25%",
                    "大宗交易": "机构专用买入2709万",
                    "龙虎榜": "04-20 | 净买1200万",
                }
            ]
        )

        muted = QColor(theme_manager.get("TEXT_MUTED")).name()
        for header in ["RPS强度", "细分板块", "摘要", "备注"]:
            idx = tab.model.index(0, tab.model.headers.index(header))
            assert tab.model.data(idx, Qt.ItemDataRole.ForegroundRole).name() == muted
    finally:
        tab.deleteLater()


def test_watchlist_note_merges_signal_columns_into_single_cell():
    assert (
        watchlist_module.WatchlistTab._format_watchlist_note("预增25%", "机构专用买入2709万", "触发")
        == "预增25% / 机构专用买入2709万 / 🚀"
    )


def test_watchlist_vm_add_stock_emits_add_signal(monkeypatch):
    original_cache = deepcopy(watchlist_vm._cache)
    spy = QSignalSpy(event_bus.sig_watchlist_changed)

    monkeypatch.setattr(watchlist_vm, "_save_data", lambda: None)
    watchlist_vm._cache = {}

    try:
        added = watchlist_vm.add_stock("600519", "贵州茅台", {"现价": 123.45, "细分板块": "白酒"})

        assert added is True
        assert watchlist_vm._cache["600519"]["名称"] == "贵州茅台"
        assert watchlist_vm._cache["600519"]["现价"] == "--"
        assert watchlist_vm._cache["600519"]["细分板块"] == "白酒"
        assert len(spy) == 1
        assert spy[0][0] == "add"
        assert spy[0][1] == "600519"
    finally:
        watchlist_vm._cache = original_cache


def test_watchlist_vm_get_watchlist_data_returns_deep_copy(monkeypatch):
    original_cache = deepcopy(watchlist_vm._cache)
    watchlist_vm._cache = {
        "600519": {"名称": "贵州茅台", "标签": {"来源": "自选"}},
    }

    try:
        snapshot = watchlist_vm.get_watchlist_data()
        snapshot["600519"]["标签"]["来源"] = "外部修改"

        assert watchlist_vm._cache["600519"]["标签"]["来源"] == "自选"
    finally:
        watchlist_vm._cache = original_cache


def test_watchlist_vm_public_patch_interfaces(monkeypatch):
    original_cache = deepcopy(watchlist_vm._cache)
    monkeypatch.setattr(watchlist_vm, "_save_data", lambda: None)
    watchlist_vm._cache = {
        "600519": {"名称": "贵州茅台", "旧字段": "待清理"},
        "000001": {"名称": "平安银行"},
    }

    try:
        assert watchlist_vm.patch_entry("600519", {"备注": "核心观察"}) is True
        assert watchlist_vm._cache["600519"]["备注"] == "核心观察"

        changed = watchlist_vm.bulk_patch_entries(
            {
                "600519": {"RPS强度": "95/93"},
                "000001": {"RPS强度": "88/80"},
            },
            remove_keys=["旧字段"],
        )
        assert changed is True
        assert "旧字段" not in watchlist_vm._cache["600519"]
        assert watchlist_vm._cache["000001"]["RPS强度"] == "88/80"

        replaced = watchlist_vm.replace_watchlist_data(
            {
                "000001": {"名称": "平安银行"},
                "600519": {"名称": "贵州茅台", "备注": "核心观察"},
            }
        )
        assert replaced is True
        assert list(watchlist_vm._cache.keys()) == ["000001", "600519"]
    finally:
        watchlist_vm._cache = original_cache


def test_watchlist_toolbar_uses_add_stock_button_and_accepts_a_share_code(monkeypatch):
    monkeypatch.setattr(watchlist_module.WatchlistTab, "subscribe_global_quotes", lambda self: None)
    monkeypatch.setattr(watchlist_module.WatchlistTab, "_load_special_data", lambda self: None)
    monkeypatch.setattr(
        watchlist_module.WatchlistTab,
        "bind_header_persistence",
        lambda self, table, settings_key="header_state": None,
        raising=False,
    )
    monkeypatch.setattr(watchlist_module, "show_toast", lambda *args, **kwargs: None)
    monkeypatch.setattr(watchlist_module.QTimer, "singleShot", staticmethod(lambda *_args, **_kwargs: None))
    monkeypatch.setattr(watchlist_vm, "is_in_watchlist", lambda code: False)

    added_calls = []

    def _fake_add_stock(code, name, vcp_data=None, source_tags=None):
        added_calls.append((code, name, dict(vcp_data or {}), list(source_tags or [])))
        return True

    monkeypatch.setattr(watchlist_vm, "add_stock", _fake_add_stock)

    tab = watchlist_module.WatchlistTab(_DummyProvider())
    try:
        button_texts = [btn.text() for btn in tab.findChildren(QPushButton)]
        assert tab.sp_search.accessibleName() == "关注池筛选"
        assert tab.add_stock_input.accessibleName() == "添加自选股输入框"
        assert "📄 导出" not in button_texts
        assert "添加自选股" in button_texts

        tab.add_stock_input.setText("sh600519")
        tab._add_custom_stock()

        assert added_calls == [
            (
                "600519",
                "贵州茅台",
                {"代码": "600519", "名称": "贵州茅台", "code": "600519", "name": "贵州茅台"},
                ["手动"],
            )
        ]
        assert tab.add_stock_input.text() == ""
    finally:
        tab.deleteLater()


def test_watchlist_can_disable_startup_tasks_for_controlled_window_smoke(monkeypatch):
    load_calls = []
    monkeypatch.setattr(watchlist_module.WatchlistTab, "subscribe_global_quotes", lambda self: None)
    monkeypatch.setattr(watchlist_module.WatchlistTab, "_load_special_data", lambda self: load_calls.append("load"))
    monkeypatch.setattr(
        watchlist_module.WatchlistTab,
        "bind_header_persistence",
        lambda self, table, settings_key="header_state": None,
        raising=False,
    )

    tab = watchlist_module.WatchlistTab(_DummyProvider(), startup_tasks_enabled=False)
    try:
        assert load_calls == []
        assert tab._delayed_special_timer is None
    finally:
        tab.shutdown()
        tab.deleteLater()


def test_watchlist_render_uses_store_only_when_live_quotes_unavailable(monkeypatch):
    monkeypatch.setattr(watchlist_module.WatchlistTab, "subscribe_global_quotes", lambda self: None)
    monkeypatch.setattr(
        watchlist_module.WatchlistTab,
        "bind_header_persistence",
        lambda self, table, settings_key="header_state": None,
        raising=False,
    )

    tab = watchlist_module.WatchlistTab(_DummyProvider(), startup_tasks_enabled=False)
    refresh_calls = []
    snapshot_calls = []
    monkeypatch.setattr(tab, "_refresh_quotes_async_local", lambda **kwargs: refresh_calls.append(kwargs))
    monkeypatch.setattr(
        tab, "_apply_quote_store_snapshot", lambda *args, **kwargs: snapshot_calls.append((args, kwargs))
    )
    monkeypatch.setattr(tab, "_request_vcp_calc", lambda *args, **kwargs: None)

    try:
        tab._render_table(["600519"], {"600519": {"名称": "贵州茅台"}}, {})

        assert len(tab.model.row_data) == 1
        assert refresh_calls == []
        assert len(snapshot_calls) == 1
    finally:
        tab.shutdown()
        tab.deleteLater()


def test_watchlist_render_keeps_live_refresh_during_quote_window(monkeypatch):
    provider = _DummyProvider()
    provider.is_online = lambda: True
    monkeypatch.setattr(watchlist_module.MarketCalendar, "is_quote_refresh_time", lambda: True)
    monkeypatch.setattr(watchlist_module.WatchlistTab, "subscribe_global_quotes", lambda self: None)
    monkeypatch.setattr(
        watchlist_module.WatchlistTab,
        "bind_header_persistence",
        lambda self, table, settings_key="header_state": None,
        raising=False,
    )

    tab = watchlist_module.WatchlistTab(provider, startup_tasks_enabled=False)
    refresh_calls = []
    snapshot_calls = []
    monkeypatch.setattr(tab, "_refresh_quotes_async_local", lambda **kwargs: refresh_calls.append(kwargs))
    monkeypatch.setattr(
        tab, "_apply_quote_store_snapshot", lambda *args, **kwargs: snapshot_calls.append((args, kwargs))
    )
    monkeypatch.setattr(tab, "_request_vcp_calc", lambda *args, **kwargs: None)

    try:
        tab._render_table(["600519"], {"600519": {"名称": "贵州茅台"}}, {})

        assert len(snapshot_calls) == 1
        assert len(refresh_calls) == 1
        assert refresh_calls[0]["quote_task_id"]
    finally:
        tab.shutdown()
        tab.deleteLater()


def test_watchlist_lineage_and_signature_skip_unchanged_render(monkeypatch):
    monkeypatch.setattr(watchlist_module.WatchlistTab, "subscribe_global_quotes", lambda self: None)
    monkeypatch.setattr(
        watchlist_module.WatchlistTab,
        "bind_header_persistence",
        lambda self, table, settings_key="header_state": None,
        raising=False,
    )
    monkeypatch.setattr(watchlist_module.MarketCalendar, "is_quote_refresh_time", lambda: False)

    tab = watchlist_module.WatchlistTab(_DummyProvider(), startup_tasks_enabled=False)
    update_calls = []
    quote_refresh_calls = []
    original_update_data = tab.model.update_data
    monkeypatch.setattr(tab, "_refresh_quotes_from_store_or_live", lambda **kwargs: quote_refresh_calls.append(kwargs))
    monkeypatch.setattr(tab, "_request_vcp_calc", lambda *args, **kwargs: None)

    def _spy_update_data(rows, **kwargs):
        update_calls.append((len(rows), kwargs))
        original_update_data(rows, **kwargs)

    monkeypatch.setattr(tab.model, "update_data", _spy_update_data)

    try:
        data = {"600519": {"名称": "贵州茅台", "现价": "1500.00"}}
        tab._render_table(["600519"], data, {})
        tab._render_table(["600519"], data, {})
        lineage = tab.get_data_lineage()

        assert update_calls == [(1, {"hydrate_latest_quotes": False})]
        assert len(quote_refresh_calls) == 1
        assert {"key", "view", "source", "provider", "cache_refs", "network_capable"}.isdisjoint(lineage)
        assert lineage["triggered_network"] is False
        assert lineage["row_count"] == 1
        assert lineage["last_table_update"]
        assert "provider_fault_tolerance" in lineage
    finally:
        tab.shutdown()
        tab.deleteLater()


def test_watchlist_shutdown_stops_timers_and_disconnects_runtime_signals(monkeypatch):
    monkeypatch.setattr(watchlist_module.WatchlistTab, "subscribe_global_quotes", lambda self: None)
    monkeypatch.setattr(watchlist_module.WatchlistTab, "_load_special_data", lambda self: None)
    monkeypatch.setattr(
        watchlist_module.WatchlistTab,
        "bind_header_persistence",
        lambda self, table, settings_key="header_state": None,
        raising=False,
    )

    tab = watchlist_module.WatchlistTab(_DummyProvider())
    tab._is_active_workspace_tab_for_vcp = lambda: True
    try:
        tab._request_vcp_calc(delay_ms=1000)
        assert tab._delayed_special_timer.isActive() is True
        assert tab._vcp_calc_timer.isActive() is True

        calc_calls = []
        tab._request_vcp_calc = lambda *args, **kwargs: calc_calls.append("calc")
        tab.shutdown()
        event_bus.sig_cache_bootstrap_ready.emit()

        assert tab._closing is True
        assert tab._delayed_special_timer.isActive() is False
        assert tab._vcp_calc_timer.isActive() is False
        assert calc_calls == []
    finally:
        tab.deleteLater()


def test_watchlist_status_summary_includes_recent_refresh(monkeypatch):
    monkeypatch.setattr(watchlist_module.WatchlistTab, "subscribe_global_quotes", lambda self: None)
    monkeypatch.setattr(watchlist_module.task_manager, "run_in_background", _run_background_inline())
    monkeypatch.setattr(
        watchlist_module.WatchlistTab,
        "bind_header_persistence",
        lambda self, table, settings_key="header_state": None,
        raising=False,
    )
    monkeypatch.setattr(watchlist_module.QTimer, "singleShot", staticmethod(lambda *_args, **_kwargs: None))
    monkeypatch.setattr(
        watchlist_module.WatchlistTab,
        "_request_vcp_calc",
        lambda self, delay_ms=500: None,
        raising=False,
    )
    monkeypatch.setattr(
        watchlist_module.WatchlistTab,
        "refresh_table_quotes_and_market_caps",
        lambda self, current_model=None, force_quotes=False, quote_task_id=None: None,
        raising=False,
    )
    monkeypatch.setattr(
        watchlist_module.watchlist_vm,
        "get_watchlist_data",
        lambda: {
            "600519": {
                "名称": "贵州茅台",
                "RPS强度": "95/93",
                "备注": "AI链备注",
                "业绩异动": "预增",
                "大宗交易": "机构买入",
                "龙虎榜": "净买入",
            }
        },
    )

    tab = watchlist_module.WatchlistTab(_DummyProvider())
    try:
        tab._touch_watchlist_update("10:21")
        tab._update_status_summary()
        summary = tab.lbl_sp_status.text()

        assert "结果 1/1只" in summary
        assert "时效 10:21" in summary
        assert "来源 龙虎｜业绩｜大宗" in summary
    finally:
        tab.deleteLater()


def test_watchlist_requests_recalc_after_ai_chain_update(monkeypatch):
    monkeypatch.setattr(watchlist_module.WatchlistTab, "subscribe_global_quotes", lambda self: None)
    monkeypatch.setattr(watchlist_module.WatchlistTab, "_load_special_data", lambda self: None)
    monkeypatch.setattr(watchlist_module.QTimer, "singleShot", staticmethod(lambda *_args, **_kwargs: None))

    calls = []
    current = {}
    monkeypatch.setattr(
        watchlist_module.WatchlistTab,
        "_request_vcp_calc",
        lambda self, delay_ms=500: calls.append(delay_ms) if current.get("tab") is self else None,
    )

    tab = watchlist_module.WatchlistTab(_DummyProvider())
    current["tab"] = tab
    try:
        watchlist_module.event_bus.sig_ai_industry_chain_updated.emit()

        assert calls == [500]
    finally:
        tab.deleteLater()


def test_watchlist_requests_recalc_after_lhb_pool_update(monkeypatch):
    monkeypatch.setattr(watchlist_module.WatchlistTab, "subscribe_global_quotes", lambda self: None)
    monkeypatch.setattr(watchlist_module.WatchlistTab, "_load_special_data", lambda self: None)
    monkeypatch.setattr(watchlist_module.QTimer, "singleShot", staticmethod(lambda *_args, **_kwargs: None))

    calls = []
    current = {}
    monkeypatch.setattr(
        watchlist_module.WatchlistTab,
        "_request_vcp_calc",
        lambda self, **kwargs: calls.append(kwargs) if current.get("tab") is self else None,
    )

    tab = watchlist_module.WatchlistTab(_DummyProvider())
    current["tab"] = tab
    try:
        watchlist_module.event_bus.sig_lhb_pool_updated.emit()

        assert calls == [{"min_interval_ms": tab.CONTEXT_REFRESH_MIN_INTERVAL_MS}]
    finally:
        tab.deleteLater()


def test_watchlist_gather_radar_data_requests_source_cache_fallback(monkeypatch):
    _patch_watchlist_constructor(monkeypatch)

    captured = {}

    def _collect_watchlist_radar_data(**kwargs):
        captured.update(kwargs)
        return {}, {"300750": "液冷"}, {}, {}, {}, None

    tab = watchlist_module.WatchlistTab(_DummyProvider())
    tab._workspace = SimpleNamespace(collect_watchlist_radar_data=_collect_watchlist_radar_data)
    try:
        result = tab._gather_radar_data(["300750"])

        assert result[1] == {"300750": "液冷"}
        assert captured == {
            "include_source_cache_fallback": True,
            "target_codes": ["300750"],
            "allow_lhb_cache_compute": False,
        }
    finally:
        tab.deleteLater()


def test_watchlist_clears_stale_special_columns_when_current_round_has_no_signal(monkeypatch):
    _patch_watchlist_constructor(monkeypatch)

    captured = {}

    def _fake_bulk_patch_entries(payload, remove_keys=None):
        captured["payload"] = payload
        captured["remove_keys"] = list(remove_keys or [])
        return True

    persist_calls = []
    monkeypatch.setattr(watchlist_vm, "bulk_patch_entries", _fake_bulk_patch_entries)
    monkeypatch.setattr(watchlist_module.task_manager, "run_in_background", _run_background_inline(persist_calls))

    tab = watchlist_module.WatchlistTab(_DummyProvider())
    try:
        tab.model.update_data(
            [
                {
                    "代码": "600519",
                    "名称": "贵州茅台",
                    "来源": "手动",
                    "现价": "--",
                    "涨幅%": "--",
                    "市值": "--",
                    "RPS强度": "95/93",
                    "细分板块": "",
                    "摘要": "旧备注",
                    "备注": "预增25% / 机构专用买入2709万 / 04-20 | 净买1200万",
                    "业绩异动": "预增25%",
                    "业绩环比%": 25.0,
                    "大宗交易": "机构专用买入2709万",
                    "大宗交易金额(万)": 2709,
                    "龙虎榜": "04-20 | 净买1200万",
                    "龙虎榜日期": "20260420",
                    "龙虎榜净额(万)": 1200,
                    "来源标签": ["手动"],
                }
            ]
        )

        tab._apply_vcp_indicators_ui(
            {
                "600519": {
                    "rps": "95/93",
                    "subsector": "",
                    "remark": "",
                    "block_trade": "",
                    "block_trade_amount_wan": "",
                    "earnings": "",
                    "earnings_qoq_pct": "",
                    "lhb": "",
                }
            }
        )

        row = tab.model.row_data[0]

        assert row["大宗交易"] == ""
        assert row["大宗交易金额(万)"] == ""
        assert row["摘要"] == ""
        assert row["备注"] == ""
        assert row["业绩异动"] == ""
        assert row["业绩环比%"] == ""
        assert row["龙虎榜"] == ""
        assert row["龙虎榜日期"] == ""
        assert row["龙虎榜净额(万)"] == ""
        assert captured["payload"]["600519"]["大宗交易"] == ""
        assert captured["payload"]["600519"]["大宗交易金额(万)"] == ""
        assert captured["payload"]["600519"]["备注"] == ""
        assert captured["payload"]["600519"]["业绩异动"] == ""
        assert captured["payload"]["600519"]["业绩环比%"] == ""
        assert captured["payload"]["600519"]["龙虎榜"] == ""
        assert captured["payload"]["600519"]["龙虎榜日期"] == ""
        assert captured["payload"]["600519"]["龙虎榜净额(万)"] == ""
        assert persist_calls[0]["task_id"] == "watchlist_vcp_persist"
    finally:
        tab.deleteLater()


def test_watchlist_indicator_apply_batches_model_update(monkeypatch):
    _patch_watchlist_constructor(monkeypatch)
    monkeypatch.setattr(watchlist_vm, "bulk_patch_entries", lambda *_args, **_kwargs: True)
    persist_calls = []
    monkeypatch.setattr(
        watchlist_module.task_manager,
        "run_in_background",
        lambda fn, *args, on_success=None, on_error=None, task_id=None, **kwargs: persist_calls.append(
            {"fn": fn, "args": args, "kwargs": kwargs, "task_id": task_id}
        )
        or (task_id or "queued"),
    )

    tab = watchlist_module.WatchlistTab(_DummyProvider())
    try:
        tab.model.update_data(
            [
                {
                    "代码": "600519",
                    "名称": "贵州茅台",
                    "来源": "手动",
                    "现价": "--",
                    "涨幅%": "--",
                    "市值": "--",
                    "RPS强度": "",
                    "细分板块": "",
                    "摘要": "",
                    "备注": "",
                    "业绩异动": "",
                    "大宗交易": "",
                    "龙虎榜": "",
                },
                {
                    "代码": "300750",
                    "名称": "宁德时代",
                    "来源": "手动",
                    "现价": "--",
                    "涨幅%": "--",
                    "市值": "--",
                    "RPS强度": "",
                    "细分板块": "",
                    "摘要": "",
                    "备注": "",
                    "业绩异动": "",
                    "大宗交易": "",
                    "龙虎榜": "",
                },
                {
                    "代码": "000001",
                    "名称": "平安银行",
                    "来源": "手动",
                    "现价": "--",
                    "涨幅%": "--",
                    "市值": "--",
                    "RPS强度": "",
                    "细分板块": "",
                    "摘要": "",
                    "备注": "",
                    "业绩异动": "",
                    "大宗交易": "",
                    "龙虎榜": "",
                },
            ]
        )
        rps_column = tab.model.headers.index("RPS强度")
        tab.proxy_model.sort(rps_column, Qt.SortOrder.DescendingOrder)
        spy = QSignalSpy(tab.model.dataChanged)
        proxy_layout_spy = QSignalSpy(tab.proxy_model.layoutChanged)

        tab._apply_vcp_indicators_ui(
            {
                "600519": {"rps": "95", "subsector": "白酒", "remark": "AI链备注"},
                "000001": {"rps": "80", "subsector": "银行"},
            }
        )

        assert 1 <= len(spy) <= 3
        for change in spy:
            roles = {int(getattr(role, "value", role)) for role in change[2]}
            assert int(Qt.ItemDataRole.UserRole) + 1 not in roles
        assert tab.table_sp._flash_repaint_timer.isActive() is False
        assert len(proxy_layout_spy) <= 1
        assert tab.model.row_data[0]["RPS强度"] == "95"
        assert tab.model.row_data[0]["摘要"] == "AI链备注"
        assert tab.model.row_data[0]["备注"] == ""
        assert tab.model.row_data[2]["细分板块"] == "银行"
        assert tab.proxy_model.data(tab.proxy_model.index(0, rps_column), Qt.ItemDataRole.DisplayRole) == "95"
        assert persist_calls[0]["task_id"] == "watchlist_vcp_persist"
    finally:
        tab.deleteLater()


def test_watchlist_indicator_apply_queues_persist_without_sync_write(monkeypatch):
    _patch_watchlist_constructor(monkeypatch)
    direct_writes = []
    queued = []
    monkeypatch.setattr(watchlist_vm, "bulk_patch_entries", lambda *args, **kwargs: direct_writes.append((args, kwargs)))
    monkeypatch.setattr(
        watchlist_module.task_manager,
        "run_in_background",
        lambda fn, *args, on_success=None, on_error=None, task_id=None, **kwargs: queued.append(
            {"fn": fn, "args": args, "kwargs": kwargs, "task_id": task_id}
        )
        or (task_id or "queued"),
    )

    tab = watchlist_module.WatchlistTab(_DummyProvider())
    try:
        tab.model.update_data(
            [
                {
                    "代码": "600519",
                    "名称": "贵州茅台",
                    "来源": "手动",
                    "现价": "--",
                    "涨幅%": "--",
                    "市值": "--",
                    "RPS强度": "",
                    "细分板块": "",
                    "摘要": "",
                    "备注": "",
                    "业绩异动": "",
                    "大宗交易": "",
                    "龙虎榜": "",
                }
            ]
        )

        tab._apply_vcp_indicators_ui({"600519": {"rps": "95", "subsector": "白酒"}})

        assert direct_writes == []
        assert queued[0]["task_id"] == "watchlist_vcp_persist"
        queued[0]["fn"]()
        assert direct_writes[0][0][0]["600519"]["RPS强度"] == "95"
    finally:
        tab.deleteLater()


def test_watchlist_indicator_apply_skips_redundant_payload_persist(monkeypatch):
    _patch_watchlist_constructor(monkeypatch)
    queued = []
    monkeypatch.setattr(
        watchlist_module.task_manager,
        "run_in_background",
        lambda fn, *args, on_success=None, on_error=None, task_id=None, **kwargs: queued.append(
            {"fn": fn, "args": args, "kwargs": kwargs, "task_id": task_id}
        )
        or (task_id or "queued"),
    )

    tab = watchlist_module.WatchlistTab(_DummyProvider())
    try:
        tab.model.update_data(
            [
                {
                    "代码": "600519",
                    "名称": "贵州茅台",
                    "来源": "手动",
                    "现价": "--",
                    "涨幅%": "--",
                    "市值": "--",
                    "RPS强度": "",
                    "细分板块": "",
                    "摘要": "",
                    "备注": "",
                    "业绩异动": "",
                    "大宗交易": "",
                    "龙虎榜": "",
                }
            ]
        )
        payload = {"600519": {"rps": "95", "subsector": "白酒", "remark": "AI链备注"}}

        tab._apply_vcp_indicators_ui(payload)
        tab._apply_vcp_indicators_ui(payload)

        assert len(queued) == 1
        assert queued[0]["task_id"] == "watchlist_vcp_persist"
    finally:
        tab.deleteLater()


def test_watchlist_writes_earnings_report_label_to_column(monkeypatch):
    _patch_watchlist_constructor(monkeypatch)

    captured = {}

    def _fake_bulk_patch_entries(payload, remove_keys=None):
        captured["payload"] = payload
        return True

    monkeypatch.setattr(watchlist_vm, "bulk_patch_entries", _fake_bulk_patch_entries)
    monkeypatch.setattr(watchlist_module.task_manager, "run_in_background", _run_background_inline())

    tab = watchlist_module.WatchlistTab(_DummyProvider())
    try:
        tab.model.update_data(
            [
                {
                    "代码": "600519",
                    "名称": "贵州茅台",
                    "来源": "手动",
                    "现价": "--",
                    "涨幅%": "--",
                    "市值": "--",
                    "RPS强度": "",
                    "细分板块": "",
                    "摘要": "",
                    "备注": "",
                    "业绩异动": "",
                    "业绩环比%": "",
                    "大宗交易": "",
                    "大宗交易金额(万)": "",
                    "龙虎榜": "",
                    "龙虎榜日期": "",
                    "龙虎榜净额(万)": "",
                    "来源标签": ["手动"],
                }
            ]
        )

        tab._apply_vcp_indicators_ui(
            {
                "600519": {
                    "rps": "",
                    "subsector": "",
                    "remark": "AI链备注",
                    "block_trade": "",
                    "block_trade_amount_wan": "",
                    "earnings": "一季度 32.5%",
                    "earnings_qoq_pct": 32.5,
                    "lhb": "",
                }
            }
        )

        row = tab.model.row_data[0]
        assert row["摘要"] == "AI链备注"
        assert row["备注"] == "一季度 32.5%"
        assert row["业绩异动"] == "一季度 32.5%"
        assert row["业绩环比%"] == 32.5
        assert captured["payload"]["600519"]["备注"] == "AI链备注"
        assert captured["payload"]["600519"]["业绩异动"] == "一季度 32.5%"
        assert captured["payload"]["600519"]["业绩环比%"] == 32.5
    finally:
        tab.deleteLater()
