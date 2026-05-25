# -*- coding: utf-8 -*-
import datetime as dt
from types import SimpleNamespace

from PyQt6.QtCore import Qt
from PyQt6.QtTest import QSignalSpy

import ui.tabs.lhb_tab as lhb_tab_module
import ui.workers.lhb_worker as lhb_worker_module
from core.market_calendar import MarketCalendar
from ui.tabs.lhb_tab import LhbTab


def test_lhb_reference_trade_date_uses_previous_day_before_20(monkeypatch):
    monkeypatch.setattr(
        MarketCalendar, "_get_market_now", classmethod(lambda cls, market="CN": dt.datetime(2026, 4, 14, 8, 30))
    )
    monkeypatch.setattr(MarketCalendar, "is_trade_day", classmethod(lambda cls, day=None, market="CN": True))
    monkeypatch.setattr(
        MarketCalendar,
        "get_latest_trade_date",
        classmethod(
            lambda cls, market="CN", ref_date=None: (
                dt.date(2026, 4, 13) if ref_date == dt.date(2026, 4, 13) else dt.date(2026, 4, 14)
            )
        ),
    )

    assert LhbTab._get_lhb_reference_trade_date() == dt.date(2026, 4, 13)


def test_lhb_reference_trade_date_keeps_today_after_20(monkeypatch):
    monkeypatch.setattr(
        MarketCalendar, "_get_market_now", classmethod(lambda cls, market="CN": dt.datetime(2026, 4, 14, 20, 5))
    )
    monkeypatch.setattr(MarketCalendar, "is_trade_day", classmethod(lambda cls, day=None, market="CN": True))
    monkeypatch.setattr(
        MarketCalendar,
        "get_latest_trade_date",
        classmethod(lambda cls, market="CN", ref_date=None: dt.date(2026, 4, 14)),
    )

    assert LhbTab._get_lhb_reference_trade_date() == dt.date(2026, 4, 14)


def test_lhb_manual_refresh_prefers_today_when_probe_finds_data(monkeypatch):
    monkeypatch.setattr(
        MarketCalendar, "_get_market_now", classmethod(lambda cls, market="CN": dt.datetime(2026, 4, 20, 19, 45))
    )
    monkeypatch.setattr(MarketCalendar, "is_trade_day", classmethod(lambda cls, day=None, market="CN": True))
    monkeypatch.setattr(
        MarketCalendar,
        "get_latest_trade_date",
        classmethod(
            lambda cls, market="CN", ref_date=None: (
                dt.date(2026, 4, 17) if ref_date == dt.date(2026, 4, 19) else dt.date(2026, 4, 20)
            )
        ),
    )
    monkeypatch.setattr(
        MarketCalendar,
        "get_recent_trade_dates",
        classmethod(lambda cls, n=30, ref_date=None: [ref_date.strftime("%Y%m%d")]),
    )
    monkeypatch.setattr(
        lhb_worker_module,
        "probe_lhb_detail_count_for_date",
        lambda date_str, return_meta=False: {"status": "ok", "count": 12, "message": "ok"},
    )

    trade_dates, message, level = LhbTab._get_manual_refresh_trade_dates()

    assert trade_dates == ["20260420"]
    assert "优先抓取今日数据" in message
    assert level == "info"


def test_lhb_manual_refresh_falls_back_to_previous_trade_day_when_today_empty(monkeypatch):
    monkeypatch.setattr(
        MarketCalendar, "_get_market_now", classmethod(lambda cls, market="CN": dt.datetime(2026, 4, 20, 19, 45))
    )
    monkeypatch.setattr(MarketCalendar, "is_trade_day", classmethod(lambda cls, day=None, market="CN": True))
    monkeypatch.setattr(
        MarketCalendar,
        "get_latest_trade_date",
        classmethod(
            lambda cls, market="CN", ref_date=None: (
                dt.date(2026, 4, 17) if ref_date == dt.date(2026, 4, 19) else dt.date(2026, 4, 20)
            )
        ),
    )
    monkeypatch.setattr(
        MarketCalendar,
        "get_recent_trade_dates",
        classmethod(lambda cls, n=30, ref_date=None: [ref_date.strftime("%Y%m%d")]),
    )
    monkeypatch.setattr(
        lhb_worker_module,
        "probe_lhb_detail_count_for_date",
        lambda date_str, return_meta=False: {"status": "empty", "count": 0, "message": "empty"},
    )

    trade_dates, message, level = LhbTab._get_manual_refresh_trade_dates()

    assert trade_dates == ["20260417"]
    assert "回退到上一交易日 20260417" in message
    assert level == "info"


def test_lhb_ensure_log_line_appends_newline_once():
    assert LhbTab._ensure_log_line("[龙虎榜池] 完成") == "[龙虎榜池] 完成\n"
    assert LhbTab._ensure_log_line("[龙虎榜池] 完成\n") == "[龙虎榜池] 完成\n"


def test_lhb_pool_window_is_30_trade_days():
    assert lhb_tab_module.POOL_WINDOW == 30


def test_lhb_build_backfill_progress_log_formats_statuses():
    ok_level, ok_msg = LhbTab._build_backfill_progress_log(1, 30, "20260401", {"status": "ok", "count": 68})
    empty_level, empty_msg = LhbTab._build_backfill_progress_log(2, 30, "20260402", {"status": "empty", "count": 0})
    err_level, err_msg = LhbTab._build_backfill_progress_log(3, 30, "20260403", {"status": "error", "count": 0})

    assert ok_level == "info"
    assert ok_msg == "[龙虎榜池] [01/30] 20260401 完成 | 68条"
    assert empty_level == "info"
    assert empty_msg == "[龙虎榜池] [02/30] 20260402 无可用数据"
    assert err_level == "warn"
    assert err_msg == "[龙虎榜池] [03/30] 20260403 抓取异常 | 已记0条"


def test_lhb_should_refresh_after_probe_only_on_count_mismatch():
    assert LhbTab._should_refresh_after_probe(61, {"status": "ok", "count": 65}) is True
    assert LhbTab._should_refresh_after_probe(61, {"status": "ok", "count": 61}) is False
    assert LhbTab._should_refresh_after_probe(61, {"status": "empty", "count": 0}) is False
    assert LhbTab._should_refresh_after_probe(61, {"status": "error", "count": 0}) is False


def test_lhb_can_defer_pool_bootstrap_until_first_show(monkeypatch):
    calls = []
    monkeypatch.setattr(LhbTab, "_load_and_display_pool", lambda self: calls.append("load"), raising=False)

    tab = LhbTab(object(), autoload_pool=False)
    try:
        assert calls == []
        tab._ensure_pool_bootstrap_started()
        assert calls == ["load"]
    finally:
        tab.deleteLater()


def test_lhb_workspace_activation_starts_deferred_pool_once(monkeypatch):
    calls = []
    monkeypatch.setattr(LhbTab, "_load_and_display_pool", lambda self: calls.append("load"), raising=False)

    tab = LhbTab(object(), autoload_pool=False)
    try:
        tab.on_workspace_tab_activated()
        tab.on_workspace_tab_activated()

        assert calls == ["load"]
        assert tab._pool_bootstrap_started is True
    finally:
        tab.deleteLater()


def test_lhb_deferred_status_does_not_read_pool_cache(monkeypatch):
    monkeypatch.setattr(
        lhb_tab_module,
        "LhbPoolManager",
        lambda: (_ for _ in ()).throw(AssertionError("pool manager should stay lazy")),
    )

    tab = LhbTab(object(), autoload_pool=False)
    try:
        assert tab.pool_manager is None
        assert tab._pool_bootstrap_started is False
    finally:
        tab.deleteLater()


def test_lhb_data_lineage_reports_deferred_without_pool_cache(monkeypatch):
    monkeypatch.setattr(
        lhb_tab_module,
        "LhbPoolManager",
        lambda: (_ for _ in ()).throw(AssertionError("pool manager should stay lazy")),
    )

    tab = LhbTab(object(), autoload_pool=False)
    try:
        lineage = tab.get_data_lineage()

        assert lineage["key"] == "lhb"
        assert lineage["source"] == "LhbPoolManager cache + local_quote_snapshot"
        assert lineage["status"] == "deferred"
        assert lineage["row_count"] == 0
        assert lineage["triggered_network"] is False
        assert "lhb_rows_deferred" in lineage["warnings"]
        assert "data/Cache/lhb_pool_30d.json" in lineage["cache_refs"]
    finally:
        tab.deleteLater()


def test_lhb_delete_later_stops_retry_timer_without_auto_scheduler(monkeypatch):
    monkeypatch.setattr(LhbTab, "_load_and_display_pool", lambda self: None, raising=False)

    tab = LhbTab(object(), autoload_pool=False)
    retry_timer = tab._pool_retry_timer
    tab._schedule_pool_retry()
    assert not hasattr(tab, "_auto_timer")
    assert not hasattr(tab, "_auto_initial_check_timer")
    assert retry_timer.isActive()

    tab.deleteLater()

    assert not retry_timer.isActive()


def test_lhb_show_bootstrap_skips_non_interactive_load_reason():
    class DummyTab:
        _workspace_load_reason = "perf_memory_probe"
        _workspace_noninteractive_loaded = True

        def _is_current_workspace_tab(self):
            return True

    dummy = DummyTab()
    assert not LhbTab._should_start_pool_on_show(dummy)
    assert dummy._workspace_noninteractive_loaded is True

    dummy._workspace_load_reason = "tab_switch"
    assert LhbTab._should_start_pool_on_show(dummy)
    assert dummy._workspace_noninteractive_loaded is False


def test_lhb_prime_background_load_starts_deferred_pool_once(monkeypatch):
    calls = []
    monkeypatch.setattr(LhbTab, "_load_and_display_pool", lambda self: calls.append("load"), raising=False)

    tab = LhbTab(object(), autoload_pool=False)
    try:
        tab.prime_background_load()
        tab.prime_background_load()

        assert calls == []
        assert tab._pool_bootstrap_started is False
    finally:
        tab.deleteLater()


def test_lhb_data_lineage_updates_after_pool_display(monkeypatch):
    monkeypatch.setattr(
        LhbTab,
        "refresh_table_quotes_and_market_caps",
        lambda self, *args, **kwargs: None,
        raising=False,
    )

    tab = LhbTab(object(), autoload_pool=False)
    tab.pool_manager = SimpleNamespace(get_cached_dates=lambda: ["20260419", "20260420"])
    try:
        tab._display_pool([{"code": "300750", "name": "CATL"}])
        lineage = tab.get_data_lineage()

        assert lineage["key"] == "lhb"
        assert lineage["status"] == "loaded"
        assert lineage["row_count"] == 1
        assert lineage["trade_date"] == "20260420"
        assert lineage["cached_trade_days"] == 2
        assert lineage["pool_window_days"] == lhb_tab_module.POOL_WINDOW
    finally:
        tab.deleteLater()


def test_lhb_display_pool_emits_update_without_self_reload(monkeypatch):
    monkeypatch.setattr(
        LhbTab,
        "refresh_table_quotes_and_market_caps",
        lambda self, *args, **kwargs: None,
        raising=False,
    )

    tab = LhbTab(object(), autoload_pool=False)
    tab.pool_manager = SimpleNamespace(get_cached_dates=lambda: ["20260420"])
    reload_calls = []
    monkeypatch.setattr(tab, "_load_and_display_pool", lambda emit_event=True: reload_calls.append(emit_event))
    spy = QSignalSpy(lhb_tab_module.event_bus.sig_lhb_pool_updated)
    try:
        tab._pool_bootstrap_started = True
        tab._display_pool([{"code": "300750", "name": "CATL"}])

        assert len(spy) == 1
        assert reload_calls == []
        assert tab.table_state._stack.currentWidget() is tab.table_state.table
    finally:
        tab.deleteLater()


def test_lhb_columns_replace_listing_reason_with_ai_chain_context():
    tab = LhbTab(object(), autoload_pool=False)
    try:
        assert "上榜原因" not in tab.columns
        assert LhbTab.AI_CHAIN_CONTEXT_COLUMN in tab.columns
    finally:
        tab.deleteLater()


def test_lhb_display_pool_shows_ai_chain_context_in_reason_slot(monkeypatch):
    monkeypatch.setattr(
        LhbTab,
        "refresh_table_quotes_and_market_caps",
        lambda self, *args, **kwargs: None,
        raising=False,
    )
    monkeypatch.setattr(
        LhbTab,
        "_chain_context_provider",
        staticmethod(lambda: {"300750": "动力电池链 | 宁德备注"}),
    )

    tab = LhbTab(object(), autoload_pool=False)
    tab.pool_manager = SimpleNamespace(get_cached_dates=lambda: ["20260420"])
    try:
        tab._display_pool(
            [
                {
                    "代码": "300750",
                    "名称": "宁德时代",
                    "最近上榜": "20260420",
                    "买点": "触发",
                    "上榜原因": "日涨幅偏离值达到7%",
                }
            ]
        )
        row = tab.model.get_row_data(0)
        buy_point_idx = tab.model.index(0, tab.model.headers.index("买点"))

        assert row[LhbTab.AI_CHAIN_CONTEXT_COLUMN] == "动力电池链 | 宁德备注"
        assert row["买点"] == "触发"
        assert tab.model.data(buy_point_idx, Qt.ItemDataRole.DisplayRole) == "🚀"
        assert tab.model.data(buy_point_idx, Qt.ItemDataRole.UserRole + 2) is None
        assert "上榜原因" not in row
        assert row["_原始上榜原因"] == "日涨幅偏离值达到7%"
    finally:
        tab.deleteLater()


def test_lhb_display_keeps_buy_points_sorted_by_live_pct(monkeypatch):
    monkeypatch.setattr(
        LhbTab,
        "refresh_table_quotes_and_market_caps",
        lambda self, *args, **kwargs: None,
        raising=False,
    )

    tab = LhbTab(object(), autoload_pool=False)
    tab.pool_manager = SimpleNamespace(get_cached_dates=lambda: ["20260420"])
    try:
        tab._display_pool(
            [
                {
                    "代码": "000001",
                    "名称": "低涨幅买点",
                    "最近上榜": "20260418",
                    "买点": "触发",
                    "涨幅%": 1.0,
                    "_history_20": [8.0] * 10 + [12.0] * 10,
                    "_history_date": "2026-04-18",
                },
                {"代码": "000002", "名称": "高涨幅买点", "最近上榜": "20260417", "买点": "触发", "涨幅%": 3.0},
                {"代码": "000003", "名称": "无买点高涨幅", "最近上榜": "20260420", "买点": "", "涨幅%": 9.0},
            ]
        )

        assert [tab.model.get_row_data(row)["代码"] for row in range(tab.model.rowCount())] == [
            "000002",
            "000001",
            "000003",
        ]

        tab._apply_quote_snapshot({"000001": {"open": 9.0, "close": 12.0, "last_close": 10.0}})

        assert [tab.model.get_row_data(row)["代码"] for row in range(tab.model.rowCount())] == [
            "000001",
            "000002",
            "000003",
        ]
    finally:
        tab.deleteLater()


def test_lhb_pool_bootstrap_skips_duplicate_active_task(monkeypatch):
    task_ids = []
    monkeypatch.setattr(
        lhb_tab_module.task_manager,
        "is_active_task",
        lambda task_id: task_ids.append(task_id) or True,
    )
    monkeypatch.setattr(
        lhb_tab_module.task_manager,
        "run_in_background",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("duplicate task should not be submitted")),
    )

    tab = LhbTab(object(), autoload_pool=False)
    try:
        tab.table_state.show_table()
        tab._load_and_display_pool()

        assert task_ids
        assert tab._pool_load_in_progress is False
        assert tab.table_state._stack.currentWidget() is tab.table_state.table
    finally:
        tab.deleteLater()


def test_lhb_pool_bootstrap_schedules_background_task(monkeypatch):
    tasks = []
    monkeypatch.setattr(lhb_tab_module.task_manager, "is_active_task", lambda task_id: False)
    monkeypatch.setattr(LhbTab, "_get_lhb_trade_dates", lambda self, n=30: ["20260420"], raising=False)
    monkeypatch.setattr(
        lhb_tab_module.task_manager,
        "run_in_background",
        lambda fn, on_success=None, on_error=None, task_id=None: tasks.append((fn, on_success, on_error, task_id)),
    )

    tab = LhbTab(object(), autoload_pool=False)
    try:
        tab._ensure_pool_bootstrap_started()

        assert len(tasks) == 1
        assert "lhb_pool_bootstrap" in str(tasks[0][3])
        assert tab._pool_load_in_progress is True
    finally:
        tab.deleteLater()


def test_lhb_watchlist_radar_rows_reads_cache_without_bootstrap(monkeypatch):
    monkeypatch.setattr(
        LhbTab,
        "_load_and_display_pool",
        lambda self: (_ for _ in ()).throw(AssertionError("should not load full tab")),
        raising=False,
    )
    monkeypatch.setattr(LhbTab, "_get_engine", staticmethod(lambda: None), raising=False)

    tab = LhbTab(object(), autoload_pool=False)
    calls = []
    tab.pool_manager = SimpleNamespace(
        compute_pool=lambda data_provider=None, engine=None: (
            calls.append((data_provider, engine))
            or [
                {
                    "代码": "300750",
                    "名称": "宁德时代",
                    "最近上榜": "20260420",
                    "上榜净买额(万)": 1200,
                    "机构净买(万)": 800,
                    "外资净买(万)": -150,
                }
            ]
        )
    )

    try:
        rows = tab.get_watchlist_radar_rows()

        assert calls == [(None, None)]
        assert rows[0]["代码"] == "300750"
        assert rows[0]["最近上榜"] == "04-20"
        assert rows[0]["_最近上榜_raw"] == "20260420"
    finally:
        tab.deleteLater()
