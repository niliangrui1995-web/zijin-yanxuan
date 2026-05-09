# -*- coding: utf-8 -*-
import pandas as pd

import ui.tabs.earnings_tab as earnings_module
from core.market_calendar import MarketCalendar
from ui.models.table_models import RtSortFilterProxyModel, StockTableModel
from ui.tabs.earnings_tab import EARNINGS_DISPLAY_TRADE_DAYS, EarningsTab


def test_recent_trade_window_start_uses_oldest_trade_day(monkeypatch):
    monkeypatch.setattr(
        MarketCalendar,
        "get_recent_trade_dates",
        classmethod(lambda cls, n=20, ref_date=None: ["20260414", "20260411", "20260410"]),
    )

    assert EarningsTab._recent_trade_window_start(3) == "2026-04-10"


def test_prune_rows_to_recent_trade_window_keeps_records_within_trade_span(monkeypatch):
    monkeypatch.setattr(
        MarketCalendar,
        "get_recent_trade_dates",
        classmethod(lambda cls, n=20, ref_date=None: ["20260414", "20260411", "20260410"]),
    )

    rows = [
        {"代码": "000001", "揭晓日": "2026-04-14"},
        {"代码": "000002", "揭晓日": "2026-04-12"},
        {"代码": "000003", "揭晓日": "2026-04-09"},
        {"代码": "000004", "揭晓日": ""},
    ]

    pruned = EarningsTab._prune_rows_to_recent_trade_window(rows, trade_days=3)

    kept_codes = [row["代码"] for row in pruned]
    assert kept_codes == ["000001", "000002", "000004"]


def test_earnings_display_trade_days_is_10():
    assert EARNINGS_DISPLAY_TRADE_DAYS == 10


def test_earnings_tab_does_not_join_realtime_quote_universe():
    assert EarningsTab.get_realtime_quote_codes(None) == set()


def test_earnings_tab_defers_scheduler_creation_until_runtime(monkeypatch):
    created = []

    class DummySignal:
        def connect(self, callback):
            self.callback = callback

    class DummyScheduler:
        sig_new_surprises_found = DummySignal()

        def start_patrol(self):
            pass

        def stop_patrol(self):
            pass

    monkeypatch.setattr(
        earnings_module,
        "EarningsScheduler",
        lambda parent=None: created.append(parent) or DummyScheduler(),
    )

    tab = EarningsTab()
    try:
        assert created == []
        assert tab.scheduler is None

        assert tab._ensure_scheduler() is tab.scheduler
        assert created == [tab]
    finally:
        tab.deleteLater()


def test_earnings_delete_later_stops_runtime_timers_and_scheduler(monkeypatch):
    created = []

    class DummySignal:
        def connect(self, callback):
            self.callback = callback

    class DummyScheduler:
        def __init__(self):
            self.sig_new_surprises_found = DummySignal()
            self.stop_calls = 0

        def start_patrol(self):
            pass

        def stop_patrol(self):
            self.stop_calls += 1

    monkeypatch.setattr(
        earnings_module,
        "EarningsScheduler",
        lambda parent=None: created.append(parent) or DummyScheduler(),
    )

    tab = EarningsTab()
    try:
        tab._ensure_runtime_started()
        tab._recalc_pe_timer.start(0)
        scheduler = tab.scheduler

        assert tab._runtime_start_timer.isActive() is True
        assert tab._recalc_pe_timer.isActive() is True

        tab.deleteLater()

        assert tab._runtime_start_timer.isActive() is False
        assert tab._recalc_pe_timer.isActive() is False
        assert scheduler.stop_calls == 1
        tab = None
    finally:
        if tab is not None:
            tab.deleteLater()


def test_earnings_runtime_start_is_gated_to_current_workspace_tab():
    class DummyTabs:
        current = None

        def currentWidget(self):
            return self.current

    class DummyParent:
        tabs = DummyTabs()

    class DummyTab:
        def parent(self):
            return DummyParent()

    tab = DummyTab()
    DummyParent.tabs.current = object()
    assert not EarningsTab._is_current_workspace_tab(tab)

    DummyParent.tabs.current = tab
    assert EarningsTab._is_current_workspace_tab(tab)


def test_earnings_runtime_show_skips_non_interactive_load_reason():
    class DummyTab:
        _workspace_load_reason = "perf_memory_probe"

        def _is_current_workspace_tab(self):
            return True

    assert not EarningsTab._should_start_runtime_on_show(DummyTab())

    DummyTab._workspace_load_reason = "tab_switch"
    assert EarningsTab._should_start_runtime_on_show(DummyTab())


def test_apply_latest_quotes_does_not_trigger_online_market_cap_backfill():
    class DummyTab:
        pass

    tab = DummyTab()
    calls = []

    tab._apply_quote_store_snapshot = lambda: calls.append("snapshot")
    tab.async_update_market_caps = lambda: calls.append("unexpected_market_caps")
    tab._recalc_pe_ttm = lambda: calls.append("pe")

    EarningsTab._apply_latest_quotes_from_store(tab)

    assert calls == ["snapshot", "pe"]


def test_filter_out_st_dataframe_removes_st_rows():
    df = pd.DataFrame(
        [
            {"股票代码": "000001", "股票名称": "平安银行"},
            {"股票代码": "000002", "股票名称": "ST晨鸣"},
            {"股票代码": "000003", "股票名称": "*ST同洲"},
        ]
    )

    filtered = EarningsTab._filter_out_st_dataframe(df)

    assert filtered["股票代码"].tolist() == ["000001"]


def test_prune_rows_to_recent_trade_window_drops_st_rows(monkeypatch):
    monkeypatch.setattr(
        MarketCalendar,
        "get_recent_trade_dates",
        classmethod(lambda cls, n=20, ref_date=None: ["20260414", "20260411", "20260410"]),
    )

    rows = [
        {"代码": "000001", "名称": "平安银行", "揭晓日": "2026-04-14"},
        {"代码": "000002", "名称": "ST晨鸣", "揭晓日": "2026-04-14"},
        {"代码": "000003", "名称": "*ST同洲", "揭晓日": ""},
        {"代码": "000004", "名称": "万科A", "揭晓日": "2026-04-09"},
    ]

    pruned = EarningsTab._prune_rows_to_recent_trade_window(rows, trade_days=3)

    kept_codes = [row["代码"] for row in pruned]
    assert kept_codes == ["000001"]


def test_rt_sort_filter_proxy_supports_multi_select_column_filters():
    model = StockTableModel(["代码", "名称", "类型"])
    model.update_data([
        {"代码": "000001", "名称": "平安银行", "类型": "预告"},
        {"代码": "000002", "名称": "万科A", "类型": "快报"},
        {"代码": "000004", "名称": "国华网安", "类型": "财报"},
    ])

    proxy = RtSortFilterProxyModel()
    proxy.setSourceModel(model)

    proxy.setColumnFilters("类型", {"预告", "财报"})
    assert proxy.rowCount() == 2

    codes = []
    for row in range(proxy.rowCount()):
        source_index = proxy.mapToSource(proxy.index(row, 0))
        codes.append(model.get_row_data(source_index.row())["代码"])
    assert codes == ["000001", "000004"]
