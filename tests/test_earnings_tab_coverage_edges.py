# -*- coding: utf-8 -*-
from __future__ import annotations

from types import SimpleNamespace

import pandas as pd
from PyQt6.QtTest import QSignalSpy
from PyQt6.QtWidgets import QDialog

from core.event_bus import event_bus
from ui.tabs import earnings_tab as module
from ui.tabs.earnings_tab import EarningsTab


class _Signal:
    def __init__(self):
        self.connected = []
        self.disconnected = []

    def connect(self, callback):
        self.connected.append(callback)

    def disconnect(self, callback):
        self.disconnected.append(callback)


class _Scheduler:
    def __init__(self, parent=None):
        self._parent = parent
        self.sig_new_surprises_found = _Signal()
        self.sig_fetch_failed = _Signal()
        self.loaded = 0
        self.shutdown_calls = 0
        self.manual_dates = []

    def parent(self):
        return self._parent

    def load_cached_records_async(self):
        self.loaded += 1

    def force_manual_scan(self, dates):
        self.manual_dates.append(list(dates))

    def shutdown(self):
        self.shutdown_calls += 1


def test_earnings_lazy_resolver_and_invalid_delay(monkeypatch):
    marker = type("MarkerService", (), {})
    monkeypatch.setattr(module, "_EARNINGS_REFRESH_SERVICE_CLASS", marker)
    monkeypatch.setattr(module, "EarningsRefreshService", None)
    assert module._resolve_earnings_refresh_service_class() is marker

    created = []
    monkeypatch.setattr(module, "_resolve_earnings_refresh_service_class", lambda: lambda parent=None: created.append(parent))
    module._default_earnings_scheduler(parent="host")
    assert created == ["host"]

    tab = EarningsTab(runtime_start_delay_ms="bad")
    try:
        assert tab._runtime_start_delay_ms == 0
    finally:
        tab.deleteLater()


def test_scheduler_reuses_host_service_and_runtime_start_guards(monkeypatch):
    service_type = type("SharedScheduler", (_Scheduler,), {})
    shared = service_type(parent=object())

    class _Host:
        earnings_refresh_service = shared

    class _Parent:
        def window(self):
            return _Host()

    tab = EarningsTab(parent=None)
    try:
        monkeypatch.setattr(tab, "parent", lambda: _Parent())
        monkeypatch.setattr(module, "EarningsScheduler", module._default_earnings_scheduler)
        monkeypatch.setattr(module, "_resolve_earnings_refresh_service_class", lambda: service_type)
        assert tab._ensure_scheduler() is shared
        assert not tab._owns_earnings_service
        assert shared.sig_new_surprises_found.connected == [tab._on_new_data_found]

        tab._ensure_runtime_started()
        assert shared.loaded == 1
        tab._ensure_runtime_started()
        assert shared.loaded == 1

        tab2 = EarningsTab()
        owned = _Scheduler(parent=tab2)
        monkeypatch.setattr(module, "EarningsScheduler", lambda parent=None: owned)
        tab2.row_data = [{"代码": "000001"}]
        try:
            assert tab2._ensure_scheduler() is owned
            assert tab2._owns_earnings_service
            tab2._ensure_runtime_started()
            assert owned.loaded == 0
        finally:
            tab2.deleteLater()
    finally:
        tab.deleteLater()


def test_background_preload_cancellation_uses_tab_as_workspace_snapshot_owner(monkeypatch):
    tab = EarningsTab()
    scheduler = SimpleNamespace(_cache_load_generation=0, _job_runner=object())
    captured = {}

    def _cancel(owner, **kwargs):
        captured["owner"] = owner
        captured.update(kwargs)
        return "receipt"

    try:
        tab._ensure_scheduler = lambda: scheduler
        monkeypatch.setattr(module, "cancel_background_preload_tasks", _cancel)

        assert tab.cancel_background_preload(reason="workspace_shutdown") == "receipt"
        assert captured["owner"] is scheduler
        assert captured["snapshot_owner"] is tab
    finally:
        tab.deleteLater()


def test_runtime_queue_and_delayed_visible_work_branches(monkeypatch, qt_application):
    queued = []
    monkeypatch.setattr(module.QTimer, "singleShot", lambda delay, callback: queued.append((delay, callback)))
    tab = EarningsTab(runtime_start_delay_ms=50)
    queued.clear()
    calls = []
    try:
        tab._patrol_started = True
        tab._queue_runtime_start()
        assert queued == []
        tab._patrol_started = False
        tab._runtime_cleanup_done = True
        tab._queue_runtime_start()
        assert queued == []
        tab._runtime_cleanup_done = False
        tab._queue_runtime_start()
        tab._queue_runtime_start()
        assert len(queued) == 1

        tab._runtime_cleanup_done = True
        tab._start_queued_runtime()
        assert not tab._runtime_start_queued
        tab._runtime_cleanup_done = False

        tab._initial_visible_work_pending = True
        tab._schedule_initial_visible_work()
        assert len(queued) == 1
        tab._initial_visible_work_pending = False
        tab._schedule_initial_visible_work()
        assert len(queued) == 2

        tab._runtime_cleanup_done = True
        tab._run_initial_visible_work()
        tab._runtime_cleanup_done = False
        tab._is_current_workspace_tab = lambda: False
        tab._run_initial_visible_work()
        tab._is_current_workspace_tab = lambda: True
        tab.show()
        qt_application.processEvents()
        tab._should_start_runtime_on_show = lambda: True
        tab._queue_runtime_start = lambda: calls.append("queue")
        tab.row_data = [{"代码": "000001"}]
        tab._run_initial_visible_work()
        assert calls == ["queue"]
        assert tab._initial_visible_work_done
    finally:
        tab.deleteLater()


def test_status_filter_and_manual_fetch_dialog_paths(monkeypatch):
    tab = EarningsTab()
    try:
        tab.row_data = [None, {"揭晓日": "2026-07-13"}, {"揭晓日": "2026-07-14"}]
        assert tab._latest_disclosure_date() == "2026-07-14"
        tab.search_box.setText("中际")
        tab.type_filter.set_selected_values({"财报"})
        assert "中际" in tab._current_filter_summary()
        assert "财报" in tab._current_filter_summary()
        tab._set_window_status("已刷新", "新增 1只", "")
        assert "已刷新" in tab.lbl_status.text()

        filter_calls = []
        tab.set_proxy_filter_text = lambda proxy, text: filter_calls.append((proxy, text))
        tab._on_search_text_changed("银行")
        assert filter_calls[-1] == (tab.proxy_model, "银行")
        tab._on_type_filter_changed()

        class _Dialog:
            result = QDialog.DialogCode.Rejected
            selected = ("2026-07-14", "2026-07-12")

            def __init__(self, **kwargs):
                self.kwargs = kwargs

            def exec(self):
                return self.result

            def selected_range(self):
                return self.selected

        monkeypatch.setattr("ui.components.scan_dialogs.TradeDateRangeDialog", _Dialog)
        scheduler = _Scheduler()
        tab._ensure_scheduler = lambda: scheduler
        tab._on_manual_fetch()
        assert scheduler.manual_dates == []
        _Dialog.result = QDialog.DialogCode.Accepted
        tab._on_manual_fetch()
        assert scheduler.manual_dates[0] == ["2026-07-12", "2026-07-13", "2026-07-14"]

        _Dialog.selected = ("invalid", "2026-07-14")
        tab._on_manual_fetch()
        assert "日期格式错误" in tab.lbl_status.text()
    finally:
        tab.deleteLater()


def test_record_filter_and_trade_window_failure_edges(monkeypatch):
    assert module._records_from_payload(None) == []
    assert module._records_from_payload([]) == []
    rows_without_name = [{"股票代码": "000001"}]
    assert module._filter_out_st_records(rows_without_name) == rows_without_name
    assert EarningsTab._recent_trade_window_start(0) is None

    from app.services.ui_market_calendar_service import MarketCalendar

    monkeypatch.setattr(MarketCalendar, "get_recent_trade_dates", lambda *_args, **_kwargs: [])
    assert EarningsTab._recent_trade_window_start() is None
    monkeypatch.setattr(MarketCalendar, "get_recent_trade_dates", lambda *_args, **_kwargs: ["bad"])
    assert EarningsTab._recent_trade_window_start() is None

    def _raise(*_args, **_kwargs):
        raise RuntimeError("calendar unavailable")

    monkeypatch.setattr(MarketCalendar, "get_recent_trade_dates", _raise)
    assert EarningsTab._recent_trade_window_start() is None
    rows = [{"代码": "000001"}]
    assert EarningsTab._prune_rows_to_recent_trade_window(rows) == rows


def test_display_window_fetch_failure_and_empty_data_status(monkeypatch):
    tab = EarningsTab()
    try:
        tab.row_data = []
        tab._prune_rows_to_recent_trade_window = lambda rows: list(rows)
        assert not tab._apply_display_trade_window(force_refresh=False)
        assert tab.table_state._stack.currentWidget() is tab.table_state._overlay

        tab.row_data = [{"代码": "000001", "名称": "平安银行"}]
        tab._prune_rows_to_recent_trade_window = lambda rows: list(rows)
        assert tab._apply_display_trade_window(force_refresh=True)

        tab._on_fetch_failed("warm_cache", "x" * 150)
        assert "业绩抓取失败" in tab.lbl_status.text()
        tab.row_data = []
        tab.model.update_data([])
        tab._on_fetch_failed("", "")
        assert "未知任务" in tab.lbl_status.text()

        updates = QSignalSpy(event_bus.sig_earnings_updated)
        tab._apply_display_trade_window = lambda force_refresh=False: True
        tab._on_new_data_found(pd.DataFrame(), "warm_cache")
        tab.row_data = [{"代码": "000001"}]
        tab._on_new_data_found(pd.DataFrame(), "routine")
        assert len(updates) == 2
    finally:
        tab.deleteLater()


def test_new_data_money_formats_dedupes_and_skips_older(monkeypatch):
    tab = EarningsTab()
    spy = QSignalSpy(event_bus.sig_earnings_updated)
    try:
        tab.row_data = [
            {
                "代码": "000001",
                "名称": "旧记录",
                "报告期": "2026Q1",
                "揭晓日": "2026-07-14",
                "现价": "10.00",
                "涨幅%": "1.0",
                "市值": "100亿",
                "PE(TTM)": "20.0",
            },
            "not-a-dict",
        ]
        tab._apply_display_trade_window = lambda force_refresh=False: True
        frame = pd.DataFrame(
            [
                {
                    "股票代码": "1",
                    "股票名称": "较早版本",
                    "环比增速_百分比": 10,
                    "同比增速_百分比": 20,
                    "单季净利润_新增": 9_000,
                    "单季净利润_上期": 20_000,
                    "报告期": "2026Q1",
                    "数据类型": "预告",
                    "公告日期": "2026-07-13",
                },
                {
                    "股票代码": "2",
                    "股票名称": "新增记录",
                    "环比增速_百分比": 30,
                    "同比增速_百分比": 40,
                    "单季净利润_新增": 200_000_000,
                    "单季净利润_上期": float("nan"),
                    "报告期": "2026Q2",
                    "数据类型": "财报",
                    "源公告日期": "2026-07-14",
                    "discovered_at": "2026-07-14T09:30:00",
                },
            ]
        )
        tab._on_new_data_found(frame, "routine")
        assert tab.row_data[0]["名称"] == "旧记录"
        added = next(row for row in tab.row_data if isinstance(row, dict) and row.get("代码") == "000002")
        assert added["当季利润"] == "2.00亿"
        assert added["上季利润"] == "--"
        assert added["发现时间"] == "2026-07-14T09:30:00"
        assert len(spy) == 1
    finally:
        tab.deleteLater()


def test_routine_refresh_cache_reload_cleanup_and_pe_edges(monkeypatch, qt_application):
    scheduled = []
    monkeypatch.setattr(module.QTimer, "singleShot", lambda delay, callback: scheduled.append((delay, callback)))
    tab = EarningsTab()
    scheduled.clear()
    try:
        assert tab.schedule_routine_scan_after_f5()
        assert not tab.schedule_routine_scan_after_f5()
        assert tab._f5_routine_scan_timer.isActive()
        assert tab._f5_routine_scan_timer.interval() == tab.F5_ROUTINE_SCAN_DELAY_MS
        tab._f5_routine_scan_timer.stop()
        tab._ensure_scheduler = lambda: object()
        assert tab._run_pending_routine_scan_after_f5() is False

        calls = []
        tab._apply_display_trade_window = lambda force_refresh=False: calls.append(("window", force_refresh))
        tab.refresh_table_from_latest_snapshot = lambda **kwargs: calls.append(("snapshot", kwargs))
        tab._ensure_scheduler = lambda: SimpleNamespace(load_cached_records_async=lambda **_kwargs: False)
        assert not tab.refresh_data_after_ai_industry_chain_update()
        assert calls[0] == ("window", True)

        tab._should_delay_initial_visible_work = lambda: True
        tab._schedule_initial_visible_work = lambda: calls.append("delay")
        tab._on_cache_reload_completed()
        assert calls[-1] == "delay"
        tab._should_delay_initial_visible_work = lambda: False
        tab._apply_latest_quotes_from_store = lambda: calls.append("quotes")
        tab._on_cache_reload_completed()
        assert calls[-1] == "quotes"

        tab.model.update_data(
            [
                {"市值": "100亿", "_raw_profit": 250_000_000, "PE(TTM)": "--"},
                {"市值": "3200万", "_raw_profit": 4_000_000, "PE(TTM)": "--"},
                {"市值": "1000", "_raw_profit": 100, "PE(TTM)": "--"},
                {"市值": "bad", "_raw_profit": 100, "PE(TTM)": "--"},
                {"市值": "--", "_raw_profit": 100, "PE(TTM)": "--"},
                {"市值": "1亿", "_raw_profit": "bad", "PE(TTM)": "--"},
            ]
        )
        tab._recalc_pe_ttm()
        assert tab.model.row_data[0]["PE(TTM)"] == "10.0"
        assert tab.model.row_data[1]["PE(TTM)"] == "2.0"
        assert tab.model.row_data[2]["PE(TTM)"] == "2.5"

        scheduler = _Scheduler(parent=tab)
        tab.scheduler = scheduler
        tab._owns_earnings_service = True
        tab.shutdown()
        assert scheduler.shutdown_calls == 1
        assert scheduler.sig_new_surprises_found.disconnected
        tab.shutdown()
    finally:
        tab.deleteLater()


def test_earnings_delete_cancels_pending_f5_scan(monkeypatch):
    tab = EarningsTab()
    monkeypatch.setattr(
        tab,
        "_ensure_scheduler",
        lambda: (_ for _ in ()).throw(AssertionError("scheduler revived after cleanup")),
    )
    assert tab.schedule_routine_scan_after_f5()
    assert tab._f5_routine_scan_timer.isActive()

    tab.deleteLater()

    assert not tab._f5_routine_scan_timer.isActive()
    assert tab._pending_f5_routine_scan is False
    assert tab._run_pending_routine_scan_after_f5() is False
