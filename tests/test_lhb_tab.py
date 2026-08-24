# -*- coding: utf-8 -*-
import datetime as dt
import threading
from types import SimpleNamespace

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QShowEvent
from PyQt6.QtTest import QSignalSpy, QTest
from PyQt6.QtWidgets import QTabWidget, QWidget

import app.services.lhb_market_data_service as lhb_worker_module
import ui.tabs.lhb_tab as lhb_tab_module
from core.ai_industry_chain_pool import load_cached_ai_industry_chain_context_map
from core.market_calendar import MarketCalendar
from ui.tabs.base_stock_tab import BaseStockTab
from ui.tabs.lhb_tab import LhbTab


def test_lhb_tab_defaults_to_cache_only_ai_chain_context():
    assert LhbTab._chain_context_provider is load_cached_ai_industry_chain_context_map


def _visible_lhb_codes(tab: LhbTab) -> list[str]:
    code_col = tab.model.headers.index("代码")
    return [
        str(tab.proxy_model.data(tab.proxy_model.index(row, code_col), Qt.ItemDataRole.DisplayRole) or "")
        for row in range(tab.proxy_model.rowCount())
    ]


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


def test_lhb_manual_refresh_preserves_cache_until_replacement_succeeds(monkeypatch):
    dates = ["20260709", "20260710"]
    monkeypatch.setattr(LhbTab, "_get_manual_refresh_trade_dates", lambda self: (dates, "", "info"))

    tab = LhbTab(object(), autoload_pool=False)
    tab._get_pool_manager = lambda: (_ for _ in ()).throw(AssertionError("refresh must not clear usable cache first"))
    calls = []
    tab._start_backfill = lambda requested: calls.append(requested)
    try:
        tab._manual_refresh()

        assert calls == [dates]
    finally:
        tab.deleteLater()


def test_lhb_ensure_log_line_appends_newline_once():
    assert LhbTab._ensure_log_line("[龙虎榜池] 完成") == "[龙虎榜池] 完成\n"
    assert LhbTab._ensure_log_line("[龙虎榜池] 完成\n") == "[龙虎榜池] 完成\n"


def test_lhb_pool_window_is_30_trade_days():
    assert lhb_tab_module.POOL_WINDOW == 30


def test_lhb_pool_manager_creation_metric_identifies_trigger(monkeypatch):
    expected = object()
    metrics = []
    monkeypatch.setattr(lhb_tab_module, "LhbPoolManager", lambda: expected)
    monkeypatch.setattr(
        lhb_tab_module,
        "record_metric",
        lambda name, value, **kwargs: metrics.append((name, value, kwargs)),
    )

    assert lhb_tab_module._create_lhb_pool_manager("backfill") is expected
    assert metrics[0][0] == "lhb_pool_manager_create_ms"
    assert metrics[0][1] >= 0
    assert metrics[0][2]["tags"] == {"status": "ok", "trigger": "backfill"}


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


def test_lhb_cancel_invalidates_delayed_pool_bootstrap(monkeypatch):
    scheduled = []
    loads = []
    monkeypatch.setattr(
        lhb_tab_module.QTimer,
        "singleShot",
        lambda delay, callback: scheduled.append((delay, callback)),
    )

    tab = LhbTab(object(), autoload_pool=False)
    tab._load_and_display_pool = lambda **kwargs: loads.append(kwargs)
    try:
        tab._ensure_pool_bootstrap_started(delay_ms=250)
        stale_callback = scheduled[-1][1]

        receipt = tab.cancel_background_preload(reason="step_timeout")

        assert receipt.is_settled() is True
        assert stale_callback() is False
        assert loads == []
    finally:
        tab.deleteLater()


def test_lhb_cancel_invalidates_post_f5_pool_callback(monkeypatch):
    scheduled = []
    loads = []
    monkeypatch.setattr(lhb_tab_module.time, "monotonic", lambda: 10.0)
    monkeypatch.setattr(
        lhb_tab_module.QTimer,
        "singleShot",
        lambda delay, callback: scheduled.append((delay, callback)),
    )

    tab = LhbTab(object(), autoload_pool=False)
    tab._load_and_display_pool = lambda **kwargs: loads.append(kwargs)
    try:
        tab._post_f5_pool_defer_until = 15.0
        assert tab._schedule_post_f5_pool_load(emit_event=False) is True
        stale_callback = scheduled[-1][1]

        receipt = tab.cancel_background_preload(reason="step_timeout")

        assert receipt.is_settled() is True
        assert tab._post_f5_pool_pending is False
        assert stale_callback() is False
        assert loads == []
    finally:
        tab.deleteLater()


def test_lhb_pool_update_hidden_tab_defers_background_reload(monkeypatch):
    monkeypatch.setattr(
        lhb_tab_module.task_manager,
        "run_in_background",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("hidden tab should not submit reload")),
    )
    tab = LhbTab(object(), autoload_pool=False)
    tab._pool_bootstrap_started = True
    tab._is_current_workspace_tab = lambda: False
    try:
        tab._on_lhb_pool_updated()

        assert tab._pending_pool_refresh is True
        assert tab._pool_load_in_progress is False
    finally:
        tab.deleteLater()


def test_lhb_show_event_consumes_pending_pool_refresh(monkeypatch):
    tab = LhbTab(object(), autoload_pool=False)
    loads = []
    tab._pool_bootstrap_started = True
    tab._pending_pool_refresh = True
    tab._is_current_workspace_tab = lambda: True
    tab.isVisible = lambda: True
    tab._load_and_display_pool = lambda **kwargs: loads.append(dict(kwargs))
    try:
        tab.showEvent(QShowEvent())
        tab.showEvent(QShowEvent())

        assert tab._pending_pool_refresh is True
        assert tab._pool_update_refresh_timer.isActive() is True
        assert loads == []

        tab._pool_update_refresh_timer.stop()
        tab._run_pending_pool_refresh()
        assert tab._pending_pool_refresh is False
        assert loads == [{"emit_event": False}]
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


def test_lhb_workspace_activation_honors_initial_load_delay(monkeypatch):
    calls = []
    scheduled = []
    monkeypatch.setattr(LhbTab, "_load_and_display_pool", lambda self: calls.append("load"), raising=False)
    monkeypatch.setattr(
        lhb_tab_module.QTimer,
        "singleShot",
        lambda delay, callback: scheduled.append((delay, callback)),
    )

    tab = LhbTab(object(), autoload_pool=False, initial_load_delay_ms=1800)
    try:
        scheduled.clear()
        tab.on_workspace_tab_activated()

        assert calls == []
        assert len(scheduled) == 1
        assert scheduled[0][0] == 1800
        assert tab._pool_bootstrap_started is True
        assert scheduled[0][1]() is True
        assert calls == ["load"]
    finally:
        tab.deleteLater()


def test_lhb_auto_backfill_defers_when_tab_is_no_longer_current(monkeypatch):
    tab = LhbTab(object(), autoload_pool=False)
    started = []
    status_calls = []
    monkeypatch.setattr(tab, "_is_current_workspace_tab", lambda: False)
    monkeypatch.setattr(tab, "_latest_loaded_cached_trade_date", lambda: "20260716")
    monkeypatch.setattr(tab, "_set_pool_status", lambda *args, **kwargs: status_calls.append((args, kwargs)))
    monkeypatch.setattr(tab, "_start_backfill", lambda *args: started.append(args))
    try:
        assert not tab._start_or_defer_backfill(["20260716"], ["20260715"], "20260716")

        assert started == []
        assert tab._pending_backfill_request == (["20260716"], ["20260715"], "20260716")
        assert status_calls[-1][1]["next_step"] == "再次进入时继续后台校验"
    finally:
        tab.deleteLater()


def test_lhb_workspace_activation_resumes_deferred_backfill(monkeypatch):
    tab = LhbTab(object(), autoload_pool=False)
    started = []
    tab._pool_bootstrap_started = True
    tab._pending_backfill_request = (["20260716"], ["20260715"], "20260716")
    tab._pending_backfill_defer_when_inactive = True
    monkeypatch.setattr(tab, "_start_backfill", lambda *args, **kwargs: started.append((args, kwargs)))
    try:
        tab.on_workspace_tab_activated()

        assert started == [
            ((["20260716"], ["20260715"], "20260716"), {"defer_when_inactive": True})
        ]
        assert tab._pending_backfill_request is None
        assert tab._pending_backfill_defer_when_inactive is False
    finally:
        tab.deleteLater()


def test_lhb_workspace_activation_defers_backfill_behind_pending_pool_refresh(monkeypatch):
    tab = LhbTab(object(), autoload_pool=False)
    resumed = []
    tab._pool_bootstrap_started = True
    tab._pending_pool_refresh = True
    monkeypatch.setattr(tab, "_resume_pending_backfill", lambda: resumed.append(True) or True)
    try:
        tab.on_workspace_tab_activated()

        assert resumed == []
        assert tab._pending_pool_refresh is True
    finally:
        tab.deleteLater()


def test_lhb_workspace_activation_queues_interactive_refresh_after_cache_only_preload(monkeypatch):
    tab = LhbTab(object(), autoload_pool=False)
    resumed = []
    tab._pool_bootstrap_started = True
    tab._pool_load_in_progress = True
    tab._background_preload_cache_only = True
    monkeypatch.setattr(tab, "_resume_pending_backfill", lambda: resumed.append(True) or True)
    try:
        tab.on_workspace_tab_activated()

        assert tab._background_preload_cache_only is False
        assert tab._pending_pool_refresh is True
        assert resumed == []
    finally:
        tab.deleteLater()


def test_lhb_pool_reload_waits_for_active_backfill():
    tab = LhbTab(object(), autoload_pool=False)
    tab._backfill_in_progress = True
    try:
        tab._load_and_display_pool()

        assert tab._pending_pool_refresh is True
        assert tab._pool_load_in_progress is False
    finally:
        tab._backfill_in_progress = False
        tab.deleteLater()


def test_lhb_rps_refresh_is_queued_while_pool_load_is_active():
    tab = LhbTab(object(), autoload_pool=False)
    tab._pool_bootstrap_started = True
    tab._pool_load_in_progress = True
    try:
        tab._on_cache_bootstrap_ready()

        assert tab._rps_injected_flag is True
        assert tab._pending_pool_refresh is True
    finally:
        tab._pool_load_in_progress = False
        tab.deleteLater()


def test_lhb_fresh_pool_gaps_replace_deferred_auto_backfill(monkeypatch):
    tab = LhbTab(object(), autoload_pool=False)
    started = []
    tab._pending_backfill_request = (["20260701"], ["20260702"], "20260702")
    tab._pending_backfill_defer_when_inactive = True
    monkeypatch.setattr(tab, "_is_current_workspace_tab", lambda: True)
    monkeypatch.setattr(tab, "_start_backfill", lambda *args, **kwargs: started.append((args, kwargs)))
    try:
        lhb_tab_module._handle_lhb_pool_gaps(
            tab,
            {
                "missing": ["20260727"],
                "pending_validation": ["20260726"],
                "validation_ref_date": "20260727",
            },
            [{"code": "000001"}],
            cache_only=False,
        )

        assert started == [
            ((["20260727"], ["20260726"], "20260727"), {"defer_when_inactive": True})
        ]
        assert tab._pending_backfill_request is None
    finally:
        tab.deleteLater()


def test_lhb_fresh_gap_free_pool_discards_deferred_auto_backfill():
    tab = LhbTab(object(), autoload_pool=False)
    tab._pending_backfill_request = (["20260701"], [], "20260701")
    tab._pending_backfill_defer_when_inactive = True
    try:
        lhb_tab_module._handle_lhb_pool_gaps(
            tab,
            {"missing": [], "pending_validation": [], "validation_ref_date": "20260727"},
            [{"code": "000001"}],
            cache_only=False,
        )

        assert tab._pending_backfill_request is None
        assert tab._pending_backfill_defer_when_inactive is False
    finally:
        tab.deleteLater()


def test_lhb_fresh_pool_preserves_manual_pending_backfill():
    tab = LhbTab(object(), autoload_pool=False)
    manual_request = (["20260701"], [], "20260701")
    tab._pending_backfill_request = manual_request
    tab._pending_backfill_defer_when_inactive = False
    try:
        lhb_tab_module._handle_lhb_pool_gaps(
            tab,
            {"missing": [], "pending_validation": [], "validation_ref_date": "20260727"},
            [{"code": "000001"}],
            cache_only=False,
        )

        assert tab._pending_backfill_request == manual_request
        assert tab._pending_backfill_defer_when_inactive is False
    finally:
        tab.deleteLater()


def test_lhb_auto_backfill_is_cancelled_and_deferred_after_tab_switch(monkeypatch):
    calls = []

    class FakeLifecycle:
        @staticmethod
        def cancel(name, *, reason):
            calls.append((name, reason))
            return True

        @staticmethod
        def shutdown(*, timeout_ms=0):
            return True

    tab = LhbTab(object(), autoload_pool=False)
    tab._task_lifecycle = FakeLifecycle()
    tab._backfill_in_progress = True
    tab._active_backfill_request = (["20260716"], ["20260715"], "20260716")
    tab._active_backfill_defer_when_inactive = True
    tab._active_backfill_had_pending_pool_refresh = True
    monkeypatch.setattr(tab, "_is_current_workspace_tab", lambda: False)
    try:
        assert tab._defer_auto_backfill_if_inactive() is True

        assert calls == [("pool_backfill", "workspace_tab_deactivated")]
        assert tab._backfill_in_progress is False
        assert tab._active_backfill_request is None
        assert tab._pending_backfill_request == (["20260716"], ["20260715"], "20260716")
        assert tab._pending_backfill_defer_when_inactive is True
        assert tab._pending_pool_refresh is True
        assert tab._active_backfill_had_pending_pool_refresh is False
        assert tab.btn_refresh.isEnabled() is True
        assert tab._status_next_step == "再次进入时继续后台校验"
    finally:
        tab.deleteLater()


def test_lhb_hidden_current_or_manual_backfill_is_not_cancelled(monkeypatch):
    calls = []

    class FakeLifecycle:
        @staticmethod
        def cancel(name, *, reason):
            calls.append((name, reason))
            return True

        @staticmethod
        def shutdown(*, timeout_ms=0):
            return True

    tab = LhbTab(object(), autoload_pool=False)
    tab._task_lifecycle = FakeLifecycle()
    tab._backfill_in_progress = True
    tab._active_backfill_request = (["20260716"], [], "")
    tab._active_backfill_defer_when_inactive = True
    monkeypatch.setattr(tab, "_is_current_workspace_tab", lambda: True)
    try:
        assert tab._defer_auto_backfill_if_inactive() is False

        monkeypatch.setattr(tab, "_is_current_workspace_tab", lambda: False)
        tab._active_backfill_defer_when_inactive = False
        assert tab._defer_auto_backfill_if_inactive() is False
        assert calls == []
        assert tab._backfill_in_progress is True
    finally:
        tab.deleteLater()


def test_lhb_real_qtabwidget_switch_defers_auto_backfill(qt_application):
    calls = []

    class FakeLifecycle:
        @staticmethod
        def cancel(name, *, reason):
            calls.append((name, reason))
            return True

        @staticmethod
        def shutdown(*, timeout_ms=0):
            return True

    tabs = QTabWidget()
    tab = LhbTab(object(), autoload_pool=False)
    other = QWidget()
    tab._task_lifecycle = FakeLifecycle()
    tab._should_start_pool_on_show = lambda: False
    tabs.addTab(tab, "龙虎榜")
    tabs.addTab(other, "其他")
    tabs.setCurrentWidget(tab)
    tabs.show()
    qt_application.processEvents()
    tab._backfill_in_progress = True
    tab._active_backfill_request = (["20260716"], ["20260715"], "20260716")
    tab._active_backfill_defer_when_inactive = True
    try:
        tabs.setCurrentWidget(other)
        QTest.qWait(10)
        qt_application.processEvents()

        assert calls == [("pool_backfill", "workspace_tab_deactivated")]
        assert tab._pending_backfill_request == (["20260716"], ["20260715"], "20260716")
    finally:
        tabs.close()
        tabs.deleteLater()


def test_lhb_hiding_window_keeps_current_auto_backfill(qt_application):
    calls = []

    class FakeLifecycle:
        @staticmethod
        def cancel(name, *, reason):
            calls.append((name, reason))
            return True

        @staticmethod
        def shutdown(*, timeout_ms=0):
            return True

    tabs = QTabWidget()
    tab = LhbTab(object(), autoload_pool=False)
    tab._task_lifecycle = FakeLifecycle()
    tab._should_start_pool_on_show = lambda: False
    tabs.addTab(tab, "龙虎榜")
    tabs.show()
    qt_application.processEvents()
    tab._backfill_in_progress = True
    tab._active_backfill_request = (["20260716"], [], "")
    tab._active_backfill_defer_when_inactive = True
    try:
        tabs.hide()
        QTest.qWait(10)
        qt_application.processEvents()

        assert calls == []
        assert tab._backfill_in_progress is True
        assert tab._pending_backfill_request is None
    finally:
        tabs.close()
        tabs.deleteLater()


def test_lhb_active_backfill_queues_and_merges_new_requests(monkeypatch):
    tab = LhbTab(object(), autoload_pool=False)
    tab._backfill_in_progress = True
    tab._active_backfill_request = (["20260714"], ["20260713"], "20260714")
    tab._active_backfill_defer_when_inactive = True
    try:
        tab._start_backfill(
            ["20260716"],
            ["20260714", "20260715"],
            "20260716",
            defer_when_inactive=True,
        )
        tab._start_backfill(
            ["20260715"],
            ["20260712"],
            "20260715",
            defer_when_inactive=True,
        )

        assert tab._pending_backfill_request == (
            ["20260715", "20260716"],
            ["20260712", "20260714"],
            "20260716",
        )
        assert tab._pending_backfill_defer_when_inactive is True
    finally:
        tab._backfill_in_progress = False
        tab.deleteLater()


def test_lhb_tab_switch_merges_active_and_newer_pending_request(monkeypatch):
    calls = []

    class FakeLifecycle:
        @staticmethod
        def cancel(name, *, reason):
            calls.append((name, reason))
            return True

        @staticmethod
        def shutdown(*, timeout_ms=0):
            return True

    tab = LhbTab(object(), autoload_pool=False)
    tab._task_lifecycle = FakeLifecycle()
    tab._backfill_in_progress = True
    tab._active_backfill_request = (["20260714"], ["20260713"], "20260714")
    tab._active_backfill_defer_when_inactive = True
    tab._pending_backfill_request = (["20260716"], ["20260714", "20260715"], "20260716")
    tab._pending_backfill_defer_when_inactive = True
    monkeypatch.setattr(tab, "_is_current_workspace_tab", lambda: False)
    try:
        assert tab._defer_auto_backfill_if_inactive() is True

        assert calls == [("pool_backfill", "workspace_tab_deactivated")]
        assert tab._pending_backfill_request == (
            ["20260714", "20260716"],
            ["20260713", "20260715"],
            "20260716",
        )
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


def test_lhb_loading_status_does_not_construct_pool_manager(monkeypatch):
    tab = LhbTab(object(), autoload_pool=False)
    tab._pool_bootstrap_started = True
    monkeypatch.setattr(
        tab,
        "_get_pool_manager",
        lambda: (_ for _ in ()).throw(AssertionError("status must not load the pool cache")),
    )
    try:
        tab._set_pool_status("正在加载龙虎榜池", freshness="后台计算")

        assert tab.pool_manager is None
        assert "正在加载龙虎榜池" in tab.lbl_status.text()
    finally:
        tab.deleteLater()


def test_lhb_table_uses_targeted_coalesced_flash_repaint():
    tab = LhbTab(object(), autoload_pool=False)
    try:
        assert tab.table._coalesced_flash_repaint is True
        assert tab.table._targeted_flash_repaint is True
        assert tab.table._paint_metric_scope == "lhb"
        assert tab.table._flash_repaint_timer.isSingleShot() is True
        # Qt 6.9+ defaults this threshold to 200.  A normal LHB quote batch
        # spans about 70 rows x 4 columns, so the default promotes the cheap
        # dataChanged delivery to a full-viewport repaint.
        assert tab.table.updateThreshold() > 70 * 4
    finally:
        tab.deleteLater()


def test_lhb_shell_nav_repaint_guard_delegates_to_table(monkeypatch):
    tab = LhbTab(object(), autoload_pool=False)
    calls = []
    monkeypatch.setattr(tab.table, "prepare_shell_nav_repaint_guard", lambda: calls.append("prepared"))
    try:
        tab.prepare_shell_nav_repaint_guard()

        assert calls == ["prepared"]
    finally:
        tab.deleteLater()


def test_lhb_quote_default_resort_emits_one_layout_and_targeted_data_span():
    tab = LhbTab(object(), autoload_pool=False)
    rows = [
        {"代码": f"{row:06d}", "名称": f"股票{row}", "涨幅%": 0.0}
        for row in range(1, 51)
    ]
    tab.model.update_data(rows, hydrate_latest_quotes=False)
    source_layout = QSignalSpy(tab.model.layoutChanged)
    proxy_layout = QSignalSpy(tab.proxy_model.layoutChanged)
    source_data = QSignalSpy(tab.model.dataChanged)
    proxy_data = QSignalSpy(tab.proxy_model.dataChanged)
    quotes = {
        f"{row:06d}": {
            "close": 10.0 + row / 100.0,
            "last_close": 10.0,
            "open": 10.0,
            "zongguben": 100_000_000.0,
        }
        for row in range(1, 51)
    }
    try:
        tab._apply_quote_snapshot_now(quotes)

        assert len(source_layout) == 1
        assert len(proxy_layout) == 1
        quote_columns = [tab.model.headers.index(header) for header in ("现价", "涨幅%", "市值", "买点")]
        expected_target_span = (0, min(quote_columns), 49, max(quote_columns))
        expected_full_span = (0, 0, 49, tab.model.columnCount() - 1)
        source_spans = [
            (event[0].row(), event[0].column(), event[1].row(), event[1].column())
            for event in source_data
        ]
        proxy_spans = [
            (event[0].row(), event[0].column(), event[1].row(), event[1].column())
            for event in proxy_data
        ]
        assert expected_target_span in source_spans
        assert expected_target_span in proxy_spans
        assert expected_full_span not in source_spans
        assert expected_full_span not in proxy_spans
        assert [row["代码"] for row in tab.model.row_data[:3]] == ["000050", "000049", "000048"]
    finally:
        tab.deleteLater()


def test_lhb_hidden_cache_only_preload_stages_rows_without_model_invalidation(monkeypatch):
    tab = LhbTab(object(), autoload_pool=False)
    model_reset = QSignalSpy(tab.model.modelReset)
    layout_changed = QSignalSpy(tab.model.layoutChanged)
    data_changed = QSignalSpy(tab.model.dataChanged)
    stack_changed = QSignalSpy(tab.table_state._stack.currentChanged)
    pool_updated = QSignalSpy(lhb_tab_module.event_bus.sig_lhb_pool_updated)
    published_rows = []

    def capture_published_rows():
        published_rows.append(tab.get_watchlist_radar_rows())

    lhb_tab_module.event_bus.sig_lhb_pool_updated.connect(capture_published_rows)
    monkeypatch.setattr(tab, "_apply_quote_store_snapshot", lambda: None)
    monkeypatch.setattr(tab, "_prime_visible_local_quote_snapshot", lambda: None)
    monkeypatch.setattr(tab, "_should_start_pool_on_show", lambda: False)
    try:
        lhb_tab_module._complete_lhb_pool_load(
            tab,
            {
                "status": "ok",
                "cache_only": True,
                "pool_manager": SimpleNamespace(get_cached_dates=lambda: ["20260728"]),
                "pool": [{"代码": "000001"}],
                "row_data": [{"代码": "000001", "名称": "平安银行"}],
                "missing": [],
                "pending_validation": [],
            },
            emit_event=True,
        )

        assert tab.model.rowCount() == 0
        assert len(model_reset) == 0
        assert len(layout_changed) == 0
        assert len(data_changed) == 0
        assert len(stack_changed) == 0
        assert tab._pending_lhb_display is not None
        assert tab.get_watchlist_radar_rows() == [{"代码": "000001", "名称": "平安银行"}]
        assert len(pool_updated) == 1
        assert published_rows == [[{"代码": "000001", "名称": "平安银行"}]]
        assert tab._background_preload_done is True

        lhb_tab_module._finish_lhb_backfill_error(tab, "抓取异常", "远端超时")
        assert len(stack_changed) == 0
        assert tab._pending_lhb_display is not None

        tab.showEvent(QShowEvent())
        assert tab.model.rowCount() == 0
        assert tab._pending_lhb_display is not None
        assert len(model_reset) == 0
        assert len(layout_changed) == 0
        assert len(data_changed) == 0
        assert len(stack_changed) == 0

        monkeypatch.setattr(tab, "_should_start_pool_on_show", lambda: True)
        tab.showEvent(QShowEvent())
        first_delivery_counts = (
            len(model_reset),
            len(layout_changed),
            len(data_changed),
            len(stack_changed),
        )

        assert tab.model.rowCount() == 1
        assert tab._pending_lhb_display is None
        assert len(pool_updated) == 1

        tab.showEvent(QShowEvent())

        assert (
            len(model_reset),
            len(layout_changed),
            len(data_changed),
            len(stack_changed),
        ) == first_delivery_counts
        assert len(pool_updated) == 1
    finally:
        lhb_tab_module.event_bus.sig_lhb_pool_updated.disconnect(capture_published_rows)
        tab.deleteLater()


def test_lhb_hidden_empty_stage_publishes_empty_rows_instead_of_stale_model(monkeypatch):
    tab = LhbTab(object(), autoload_pool=False)
    tab.model.update_data([{"代码": "000001", "名称": "旧榜单"}], hydrate_latest_quotes=False)
    published_rows = []

    def capture_published_rows():
        published_rows.append(tab.get_watchlist_radar_rows())

    lhb_tab_module.event_bus.sig_lhb_pool_updated.connect(capture_published_rows)
    monkeypatch.setattr(tab, "_should_start_pool_on_show", lambda: False)
    try:
        applied = lhb_tab_module._deliver_or_stage_lhb_pool(
            tab,
            [],
            row_data=[],
            emit_event=True,
            refresh_quotes=False,
            trigger="test_empty_stage",
        )

        assert applied is False
        assert tab.model.row_data[0]["代码"] == "000001"
        assert tab.model.row_data[0]["名称"] == "旧榜单"
        assert isinstance(tab._pending_lhb_display, dict)
        assert tab.get_watchlist_radar_rows() == []
        assert published_rows == [[]]
    finally:
        lhb_tab_module.event_bus.sig_lhb_pool_updated.disconnect(capture_published_rows)
        tab.deleteLater()


def test_lhb_cancel_keeps_already_published_staged_snapshot(monkeypatch):
    tab = LhbTab(object(), autoload_pool=False)
    tab.model.update_data([{"代码": "000001", "名称": "旧榜单"}], hydrate_latest_quotes=False)
    published_rows = []

    def capture_published_rows():
        published_rows.append(tab.get_watchlist_radar_rows())

    lhb_tab_module.event_bus.sig_lhb_pool_updated.connect(capture_published_rows)
    monkeypatch.setattr(tab, "_should_start_pool_on_show", lambda: False)
    try:
        lhb_tab_module._deliver_or_stage_lhb_pool(
            tab,
            [{"代码": "000002"}],
            row_data=[{"代码": "000002", "名称": "新榜单"}],
            emit_event=True,
            refresh_quotes=False,
            trigger="test_cancel",
        )

        receipt = tab.cancel_background_preload(reason="step_timeout")

        assert receipt.is_settled() is True
        assert tab.get_watchlist_radar_rows() == [{"代码": "000002", "名称": "新榜单"}]
        assert published_rows == [[{"代码": "000002", "名称": "新榜单"}]]
    finally:
        lhb_tab_module.event_bus.sig_lhb_pool_updated.disconnect(capture_published_rows)
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

        assert {"key", "view", "source", "provider", "cache_refs", "network_capable"}.isdisjoint(lineage)
        assert lineage["status"] == "deferred"
        assert lineage["row_count"] == 0
        assert lineage["triggered_network"] is False
        assert "lhb_rows_deferred" in lineage["warnings"]
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

        assert calls == ["load"]
        assert tab._pool_bootstrap_started is True
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

        assert {"key", "view", "source", "provider", "cache_refs", "network_capable"}.isdisjoint(lineage)
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


def test_lhb_display_pool_skips_duplicate_event_and_quote_refresh(monkeypatch):
    quote_calls = []
    metrics = []
    monkeypatch.setattr(
        LhbTab,
        "refresh_table_quotes_and_market_caps",
        lambda self, *args, **kwargs: quote_calls.append(kwargs),
        raising=False,
    )
    monkeypatch.setattr(
        lhb_tab_module,
        "record_metric",
        lambda name, value, **kwargs: metrics.append((name, value, kwargs)),
    )

    tab = LhbTab(object(), autoload_pool=False)
    tab.pool_manager = SimpleNamespace(get_cached_dates=lambda: ["20260420"])
    spy = QSignalSpy(lhb_tab_module.event_bus.sig_lhb_pool_updated)
    try:
        tab._pool_bootstrap_started = True
        pool = [{"code": "300750", "name": "CATL"}]

        tab._display_pool(pool)
        tab._display_pool(pool)

        assert len(spy) == 1
        assert len(quote_calls) == 1
        assert len(tab.model.row_data) == 1
        apply_metrics = [item for item in metrics if item[0] == "lhb_pool_model_apply_ms"]
        assert [item[2]["tags"]["rows_changed"] for item in apply_metrics] == ["true", "false"]
        assert all(item[2]["tags"]["trigger"] == "direct" for item in apply_metrics)
    finally:
        tab.deleteLater()


def test_lhb_display_pool_leaves_quote_hydration_to_refresh_path(monkeypatch):
    monkeypatch.setattr(
        LhbTab,
        "refresh_table_quotes_and_market_caps",
        lambda self, *args, **kwargs: None,
        raising=False,
    )

    tab = LhbTab(object(), autoload_pool=False)
    tab.pool_manager = SimpleNamespace(get_cached_dates=lambda: ["20260420"])
    update_kwargs = []
    original_update_data = tab.model.update_data

    def capture_update_data(rows, **kwargs):
        update_kwargs.append(dict(kwargs))
        return original_update_data(rows, **kwargs)

    monkeypatch.setattr(tab.model, "update_data", capture_update_data)
    try:
        tab._display_pool([{"code": "300750", "name": "CATL"}])

        assert update_kwargs[0]["hydrate_latest_quotes"] is False
    finally:
        tab.deleteLater()


def test_lhb_visible_quote_snapshot_is_coalesced_before_apply(monkeypatch, qt_application):
    applied = []

    def capture_apply(self, quotes):
        applied.append(dict(quotes or {}))

    monkeypatch.setattr(BaseStockTab, "_apply_quote_snapshot", capture_apply)

    tab = LhbTab(object(), autoload_pool=False)
    tab._is_current_workspace_tab = lambda: True
    tab.isVisible = lambda: True
    try:
        tab._apply_quote_snapshot({"000001": {"close": 10.0}})
        tab._apply_quote_snapshot(
            {
                "000002": {"close": 20.0},
                "000001": {"close": 11.0},
            }
        )

        assert applied == []

        QTest.qWait(LhbTab.QUOTE_APPLY_DEBOUNCE_MS + 30)
        qt_application.processEvents()

        assert applied == [
            {
                "000001": {"close": 11.0},
                "000002": {"close": 20.0},
            }
        ]
    finally:
        tab.deleteLater()


def test_lhb_opening_warmup_display_pool_uses_snapshot_only(monkeypatch):
    quote_calls = []
    snapshot_calls = []
    monkeypatch.setattr(LhbTab, "_is_opening_warmup_window", lambda self: True)
    monkeypatch.setattr(
        LhbTab,
        "refresh_table_quotes_and_market_caps",
        lambda self, *args, **kwargs: quote_calls.append(kwargs),
        raising=False,
    )
    monkeypatch.setattr(
        LhbTab,
        "refresh_table_from_latest_snapshot",
        lambda self, *args, **kwargs: snapshot_calls.append(kwargs),
        raising=False,
    )

    tab = LhbTab(object(), autoload_pool=False)
    tab.pool_manager = SimpleNamespace(get_cached_dates=lambda: ["20260420"])
    try:
        tab._display_pool([{"code": "300750", "name": "CATL"}])

        assert quote_calls == []
        assert snapshot_calls == [{"async_local": True}]
        assert len(tab.model.row_data) == 1
    finally:
        tab.deleteLater()


def test_lhb_opening_warmup_quote_snapshot_flushes_in_chunks(monkeypatch):
    applied = []
    sort_calls = []

    def capture_apply(self, quotes):
        applied.append(tuple(quotes or {}))

    monkeypatch.setattr(BaseStockTab, "_apply_quote_snapshot", capture_apply)
    monkeypatch.setattr(LhbTab, "_is_opening_warmup_window", lambda self: True)
    monkeypatch.setattr(LhbTab, "OPENING_WARMUP_QUOTE_APPLY_CHUNK_SIZE", 2)
    monkeypatch.setattr(LhbTab, "OPENING_WARMUP_QUOTE_APPLY_CONTINUE_MS", 1)
    monkeypatch.setattr(LhbTab, "_schedule_default_lhb_quote_sort", lambda self: sort_calls.append("sort"))

    tab = LhbTab(object(), autoload_pool=False)
    tab._pending_quote_snapshot = {f"{idx:06d}": {"close": float(idx)} for idx in range(1, 6)}
    try:
        tab._flush_pending_quote_snapshot()
        tab._quote_apply_timer.stop()
        tab._flush_pending_quote_snapshot()
        tab._quote_apply_timer.stop()
        tab._flush_pending_quote_snapshot()

        assert applied == [
            ("000001", "000002"),
            ("000003", "000004"),
            ("000005",),
        ]
        assert tab._pending_quote_snapshot == {}
        assert sort_calls == ["sort"]
    finally:
        tab.deleteLater()


def test_lhb_deferred_quote_snapshot_outside_opening_resorts_pool(monkeypatch, qt_application):
    applied = []
    sort_calls = []

    def capture_apply(self, quotes):
        applied.append(dict(quotes or {}))
        return "applied"

    monkeypatch.setattr(BaseStockTab, "_apply_quote_snapshot", capture_apply)
    monkeypatch.setattr(LhbTab, "_is_opening_warmup_window", lambda self: False)
    monkeypatch.setattr(LhbTab, "_sort_model_for_default_lhb_order", lambda self: sort_calls.append("sort"))

    tab = LhbTab(object(), autoload_pool=False)
    tab.model.update_data([{"代码": "300750", "涨幅%": 0.0}], hydrate_latest_quotes=False)
    try:
        assert tab._apply_quote_snapshot_now({"300750": {"close": 1.0}}, defer_sort=True) == "applied"

        assert len(applied) == 1
        assert sort_calls == []
        assert tab._quote_sort_timer.isActive()
        QTest.qWait(LhbTab.QUOTE_SORT_DEBOUNCE_MS + 30)
        qt_application.processEvents()
        assert sort_calls == ["sort"]
    finally:
        tab.deleteLater()


def test_lhb_refresh_after_ai_chain_update_reloads_started_pool(monkeypatch):
    tab = LhbTab(object(), autoload_pool=False)
    calls = []
    monkeypatch.setattr(tab, "_load_and_display_pool", lambda: calls.append("reload"))
    try:
        tab._pool_bootstrap_started = True
        tab._ai_chain_context_map = {"300750": "old"}

        assert tab.refresh_data_after_ai_industry_chain_update() is True

        assert tab._ai_chain_context_map is None
        assert calls == ["reload"]
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


def test_lhb_display_uses_buy_point_pct_then_date_pct_order(monkeypatch):
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
                {"代码": "000003", "名称": "无买点低涨幅", "最近上榜": "20260420", "买点": "", "涨幅%": 1.0},
                {"代码": "000004", "名称": "无买点高涨幅", "最近上榜": "20260420", "买点": "", "涨幅%": 9.0},
                {"代码": "000005", "名称": "较早无买点", "最近上榜": "20260419", "买点": "", "涨幅%": 20.0},
            ]
        )

        assert _visible_lhb_codes(tab) == [
            "000002",
            "000001",
            "000004",
            "000003",
            "000005",
        ]

        tab._apply_quote_snapshot(
            {
                "000001": {"open": 9.0, "close": 12.0, "last_close": 10.0},
                "000003": {"close": 12.0, "last_close": 10.0},
            }
        )

        assert _visible_lhb_codes(tab) == [
            "000001",
            "000002",
            "000003",
            "000004",
            "000005",
        ]
    finally:
        tab.deleteLater()


def test_lhb_display_default_order_overrides_restored_header_sort(monkeypatch, qt_application):
    monkeypatch.setattr(
        LhbTab,
        "refresh_table_quotes_and_market_caps",
        lambda self, *args, **kwargs: None,
        raising=False,
    )

    def fake_bind_header_persistence(self, table, settings_key="header_state"):
        QTimer.singleShot(
            0,
            lambda: table.sortByColumn(self.model.headers.index("最近上榜"), Qt.SortOrder.DescendingOrder),
        )
        return True

    monkeypatch.setattr(LhbTab, "bind_header_persistence", fake_bind_header_persistence)

    tab = LhbTab(object(), autoload_pool=False)
    tab.pool_manager = SimpleNamespace(get_cached_dates=lambda: ["20260420"])
    try:
        qt_application.processEvents()
        tab._display_pool(
            [
                {"代码": "000001", "名称": "低涨幅买点", "最近上榜": "20260418", "买点": "触发", "涨幅%": 1.0},
                {"代码": "000002", "名称": "高涨幅买点", "最近上榜": "20260417", "买点": "触发", "涨幅%": 3.0},
                {"代码": "000003", "名称": "无买点高涨幅", "最近上榜": "20260420", "买点": "", "涨幅%": 9.0},
            ]
        )

        assert _visible_lhb_codes(tab) == ["000002", "000001", "000003"]
        assert tab.proxy_model.sortColumn() == -1
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
        assert tab._pending_pool_refresh is True
        assert tab._pool_retry_timer.isActive() is True
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


def test_lhb_warm_pool_reload_keeps_visible_table_during_success_or_error(monkeypatch):
    callbacks = {}

    class FakeLifecycle:
        def run_background(self, _name, _fn, **kwargs):
            callbacks.update(kwargs)
            return object()

        @staticmethod
        def shutdown(*, timeout_ms=0):
            return True

    monkeypatch.setattr(lhb_tab_module.task_manager, "is_active_task", lambda _task_id: False)
    tab = LhbTab(object(), autoload_pool=False)
    tab._task_lifecycle = FakeLifecycle()
    tab.model.update_data([{"代码": "000001", "名称": "平安银行"}], hydrate_latest_quotes=False)
    tab._refresh_lhb_lineage(list(tab.model.row_data))
    tab.table_state.show_table()
    monkeypatch.setattr(tab, "isVisible", lambda: True)
    monkeypatch.setattr(tab, "_is_current_workspace_tab", lambda: True)
    tab._pending_pool_refresh = True
    scheduled_refreshes = []
    monkeypatch.setattr(tab, "_schedule_pending_pool_refresh", lambda: scheduled_refreshes.append(True))
    stack_changes = QSignalSpy(tab.table_state._stack.currentChanged)
    model_reset = QSignalSpy(tab.model.modelReset)
    layout_changed = QSignalSpy(tab.model.layoutChanged)
    data_changed = QSignalSpy(tab.model.dataChanged)
    try:
        tab._schedule_pool_retry()
        tab._pool_update_refresh_timer.start(5_000)
        assert tab._pool_retry_timer.isActive() is True
        assert tab._pool_update_refresh_timer.isActive() is True
        tab._load_and_display_pool()

        assert tab.table_state._stack.currentWidget() is tab.table_state.table
        assert len(stack_changes) == 0
        assert tab._pool_retry_timer.isActive() is False
        assert tab._pool_update_refresh_timer.isActive() is False

        callbacks["on_success"](
            {
                "status": "ok",
                "cache_only": False,
                "pool_manager": SimpleNamespace(get_cached_dates=lambda: ["20260728"]),
                "pool": [{"代码": "000001", "名称": "平安银行"}],
                "row_data": [{"代码": "000001", "名称": "平安银行"}],
                "missing": [],
                "pending_validation": [],
            }
        )

        assert tab.table_state._stack.currentWidget() is tab.table_state.table
        assert len(stack_changes) == 0
        assert len(model_reset) == 0
        assert len(layout_changed) == 0
        assert len(data_changed) == 0
        assert scheduled_refreshes == []
        assert tab._pending_pool_refresh is False

        tab._load_and_display_pool()

        callbacks["on_success"](
            {
                "status": "ok",
                "cache_only": False,
                "pool_manager": SimpleNamespace(get_cached_dates=lambda: []),
                "pool": [],
                "row_data": [],
                "missing": [],
                "pending_validation": [],
            }
        )

        assert tab.table_state._stack.currentWidget() is tab.table_state.table
        assert len(stack_changes) == 0
        assert tab.model.rowCount() == 1
        assert len(model_reset) == 0
        assert len(layout_changed) == 0
        assert len(data_changed) == 0

        tab._load_and_display_pool()

        callbacks["on_error"]("远端异常")

        assert tab.table_state._stack.currentWidget() is tab.table_state.table
        assert len(stack_changes) == 0
        assert tab.model.rowCount() == 1
        assert len(model_reset) == 0
        assert len(layout_changed) == 0
        assert len(data_changed) == 0
    finally:
        tab.deleteLater()


def test_lhb_money_bar_scale_is_cached_and_invalidated(monkeypatch):
    tab = LhbTab(object(), autoload_pool=False)
    rows = [
        {"代码": f"{row:06d}", "上榜净买额(万)": float(row + 1)}
        for row in range(70)
    ]
    tab.model.update_data(rows, hydrate_latest_quotes=False)
    calls = 0
    original = tab.model._money_value_for_visual

    def tracked(header, row):
        nonlocal calls
        calls += 1
        return original(header, row)

    monkeypatch.setattr(tab.model, "_money_value_for_visual", tracked)
    try:
        payloads = [tab.model._money_bar_payload("上榜净买额(万)", row) for row in tab.model.row_data]

        assert calls == len(rows) * 2
        assert {payload["max_abs"] for payload in payloads if payload} == {70.0}

        before_invalidation = calls
        tab.model.set_cell_value(0, "上榜净买额(万)", 999.0, emit_signal=False)
        payload = tab.model._money_bar_payload("上榜净买额(万)", tab.model.row_data[0])

        assert calls - before_invalidation == len(rows) + 1
        assert payload["max_abs"] == 999.0

        updated_rows = [dict(row) for row in tab.model.row_data]
        updated_rows[1]["上榜净买额(万)"] = 1_234.0
        before_update = calls
        tab.model.update_data(updated_rows, hydrate_latest_quotes=False)
        payload = tab.model._money_bar_payload("上榜净买额(万)", tab.model.row_data[1])

        assert calls - before_update == len(rows) + 1
        assert payload["max_abs"] == 1_234.0
    finally:
        tab.deleteLater()


def test_lhb_pool_bootstrap_captures_cache_only_at_submission(monkeypatch):
    submissions = []
    captured = []

    class FakeLifecycle:
        def run_background(self, name, fn, **kwargs):
            submissions.append((name, fn, kwargs))
            return object()

        @staticmethod
        def shutdown(*, timeout_ms=0):
            return True

    monkeypatch.setattr(lhb_tab_module.task_manager, "is_active_task", lambda _task_id: False)
    monkeypatch.setattr(
        lhb_tab_module,
        "_load_lhb_pool_payload",
        lambda _owner, _token, *, cache_only=None: captured.append(cache_only) or {},
    )
    tab = LhbTab(object(), autoload_pool=False)
    tab._task_lifecycle = FakeLifecycle()
    tab._background_preload_cache_only = True
    try:
        tab._load_and_display_pool()
        tab._background_preload_cache_only = False
        submissions[0][1](object())

        assert captured == [True]
    finally:
        tab.deleteLater()


def test_lhb_pool_tasks_use_owner_lifecycle_deadlines(monkeypatch):
    submissions = []

    class FakeLifecycle:
        def run_background(self, name, fn, **kwargs):
            submissions.append((name, fn, kwargs))
            return object()

        @staticmethod
        def shutdown(*, timeout_ms=0):
            return True

    monkeypatch.setattr(lhb_tab_module.task_manager, "is_active_task", lambda _task_id: False)
    tab = LhbTab(object(), autoload_pool=False)
    tab._task_lifecycle = FakeLifecycle()
    try:
        tab._load_and_display_pool()
        tab._backfill_in_progress = False
        tab._start_backfill(["20260420"])

        assert [item[0] for item in submissions] == ["pool_bootstrap", "pool_backfill"]
        assert submissions[0][2]["timeout_sec"] == lhb_tab_module.LHB_POOL_BOOTSTRAP_TIMEOUT_SECONDS
        assert submissions[1][2]["timeout_sec"] == lhb_tab_module.LHB_POOL_BACKFILL_TIMEOUT_SECONDS
    finally:
        tab.deleteLater()


def test_lhb_backfill_worker_progress_does_not_access_qwidget(monkeypatch):
    submission = {}

    class FakeLifecycle:
        def run_background(self, name, fn, **kwargs):
            submission.update({"name": name, "fn": fn, "kwargs": kwargs})
            return object()

        @staticmethod
        def shutdown(*, timeout_ms=0):
            return True

    def fake_build(_owner, _missing, _validation, _ref_date, log_emit, _token):
        log_emit("warn", "[龙虎榜池] worker progress")
        return {}

    monkeypatch.setattr(lhb_tab_module, "_build_lhb_backfill_payload", fake_build)
    monkeypatch.setattr(lhb_tab_module.task_manager, "is_active_task", lambda _task_id: False)
    tab = LhbTab(object(), autoload_pool=False)
    tab._task_lifecycle = FakeLifecycle()
    worker_qwidget_accesses = []
    gui_thread_id = threading.get_ident()
    original_window = tab.window

    def tracked_window():
        thread_id = threading.get_ident()
        if thread_id != gui_thread_id:
            worker_qwidget_accesses.append(thread_id)
        return original_window()

    monkeypatch.setattr(tab, "window", tracked_window)
    try:
        tab._schedule_pool_retry()
        assert tab._pool_retry_timer.isActive() is True
        tab._start_backfill(["20260724"])
        assert tab._pool_retry_timer.isActive() is False
        worker = threading.Thread(
            target=lambda: submission["fn"](SimpleNamespace(raise_if_cancelled=lambda: None)),
            name="lhb-test-worker",
        )
        worker.start()
        worker.join(timeout=2.0)

        assert worker.is_alive() is False
        assert worker_qwidget_accesses == []
    finally:
        tab.deleteLater()


def test_lhb_backfill_error_restores_preexisting_pool_refresh_without_scheduling(monkeypatch):
    callbacks = {}

    class FakeLifecycle:
        def run_background(self, _name, _fn, **kwargs):
            callbacks.update(kwargs)
            return object()

        @staticmethod
        def shutdown(*, timeout_ms=0):
            return True

    monkeypatch.setattr(lhb_tab_module.task_manager, "is_active_task", lambda _task_id: False)
    tab = LhbTab(object(), autoload_pool=False)
    tab._task_lifecycle = FakeLifecycle()
    tab._pool_bootstrap_started = True
    tab._pending_pool_refresh = True
    scheduled_refreshes = []
    monkeypatch.setattr(tab, "_schedule_pending_pool_refresh", lambda: scheduled_refreshes.append(True))
    try:
        tab._schedule_pool_retry()
        tab._pool_update_refresh_timer.start(5_000)
        tab._start_backfill(["20260724"])

        assert tab._pending_pool_refresh is False
        assert tab._active_backfill_had_pending_pool_refresh is True
        assert tab._pool_retry_timer.isActive() is False
        assert tab._pool_update_refresh_timer.isActive() is False

        callbacks["on_error"]("远端超时")

        assert tab._pending_pool_refresh is True
        assert tab._active_backfill_had_pending_pool_refresh is False
        assert scheduled_refreshes == []
    finally:
        tab.deleteLater()


def test_lhb_backfill_records_fetch_and_validation_wall_and_thread_cpu(monkeypatch):
    metrics = []
    token = SimpleNamespace(raise_if_cancelled=lambda: None, wait=lambda _seconds: False)
    owner = SimpleNamespace(_build_backfill_progress_log=LhbTab._build_backfill_progress_log)
    monkeypatch.setattr(
        lhb_tab_module,
        "record_metric",
        lambda name, value, **kwargs: metrics.append((name, value, kwargs)),
    )
    monkeypatch.setattr(
        lhb_worker_module,
        "fetch_lhb_pool_for_date",
        lambda *_args, **_kwargs: {"records": [], "count": 0, "status": "error"},
    )

    fetched, step = lhb_tab_module._fetch_missing_lhb_dates(
        owner,
        ["20260724"],
        2,
        lambda *_args: None,
        token,
    )
    monkeypatch.setattr(
        lhb_tab_module,
        "_validate_lhb_date",
        lambda *_args: (None, {"count": 0, "status": "error"}, "warn", "probe failed"),
    )
    _, validated = lhb_tab_module._validate_lhb_dates(
        owner,
        object(),
        ["20260727"],
        "20260729",
        step,
        2,
        lambda *_args: None,
        token,
    )

    assert fetched == {}
    assert validated == {"20260727": {"count": 0, "status": "error"}}
    metric_by_name = {name: (value, kwargs) for name, value, kwargs in metrics}
    assert set(metric_by_name) == {
        "lhb_backfill_fetch_date_ms",
        "lhb_backfill_fetch_date_thread_cpu_ms",
        "lhb_backfill_validate_date_ms",
        "lhb_backfill_validate_date_thread_cpu_ms",
    }
    for value, kwargs in metric_by_name.values():
        assert value >= 0
        assert kwargs["unit"] == "ms"
        assert kwargs["tags"]["status"] == "error"
        assert kwargs["tags"]["thread_id"]


def test_lhb_backfill_all_error_stops_before_cache_and_pool_postprocessing(monkeypatch):
    calls = []
    metrics = []

    class PoolManager:
        def add_day(self, *_args, **_kwargs):
            calls.append("add_day")

        def mark_day_probe(self, *_args, **_kwargs):
            calls.append("mark_day_probe")

        def save(self):
            calls.append("save")

        def compute_pool(self, **_kwargs):
            calls.append("compute_pool")
            return []

    owner = SimpleNamespace(
        data_provider=object(),
        _get_engine=lambda: calls.append("get_engine"),
        _load_ai_chain_context_map=lambda: calls.append("load_ai_context") or {},
        _build_pool_display_rows=lambda *_args: calls.append("build_rows") or [],
    )
    token = SimpleNamespace(raise_if_cancelled=lambda: None)
    monkeypatch.setattr(lhb_tab_module, "_create_lhb_pool_manager", lambda _trigger: PoolManager())
    monkeypatch.setattr(lhb_tab_module, "_fetch_missing_lhb_dates", lambda *_args: ({}, 1))
    monkeypatch.setattr(lhb_tab_module, "_validate_lhb_dates", lambda *_args: ({}, {}))
    monkeypatch.setattr(
        lhb_tab_module,
        "record_metric",
        lambda name, value, **kwargs: metrics.append((name, value, kwargs)),
    )

    result = lhb_tab_module._build_lhb_backfill_payload(
        owner,
        ["20260724"],
        [],
        "20260729",
        lambda *_args: None,
        token,
    )

    assert result["fetched"] == {}
    assert result["validated"] == {}
    assert result["status"] == "no_valid_results"
    assert result["pool_changed"] is False
    assert result["persist_status"] == "not_attempted"
    assert calls == []
    assert [item[0] for item in metrics] == [
        "lhb_backfill_finalize_ms",
        "lhb_backfill_finalize_thread_cpu_ms",
    ]
    assert all(item[1] >= 0 for item in metrics)
    assert all(item[2]["tags"]["action"] == "skipped_no_valid_results" for item in metrics)
    assert all(item[2]["tags"]["persist_status"] == "not_attempted" for item in metrics)
    assert all(item[2]["tags"]["thread_id"] for item in metrics)


def test_lhb_backfill_validation_error_saves_probe_without_rebuilding_pool(monkeypatch):
    calls = []

    class PoolManager:
        def add_day(self, *_args, **_kwargs):
            calls.append("add_day")

        def mark_day_probe(self, *_args, **_kwargs):
            calls.append("mark_day_probe")

        def save(self):
            calls.append("save")
            return True

        def compute_pool(self, **_kwargs):
            calls.append("compute_pool")
            return []

    owner = SimpleNamespace(
        data_provider=object(),
        _get_engine=lambda: calls.append("get_engine"),
        _load_ai_chain_context_map=lambda: calls.append("load_ai_context") or {},
        _build_pool_display_rows=lambda *_args: calls.append("build_rows") or [],
    )
    token = SimpleNamespace(raise_if_cancelled=lambda: None)
    monkeypatch.setattr(lhb_tab_module, "_create_lhb_pool_manager", lambda _trigger: PoolManager())
    monkeypatch.setattr(lhb_tab_module, "_fetch_missing_lhb_dates", lambda *_args: ({}, 0))
    monkeypatch.setattr(
        lhb_tab_module,
        "_validate_lhb_dates",
        lambda *_args: ({}, {"20260724": {"count": 61, "status": "error"}}),
    )

    result = lhb_tab_module._build_lhb_backfill_payload(
        owner,
        [],
        ["20260724"],
        "20260729",
        lambda *_args: None,
        token,
    )

    assert result["pool_changed"] is False
    assert result["persist_status"] == "ok"
    assert calls == ["mark_day_probe", "save"]


def test_lhb_backfill_empty_fetch_still_updates_cached_day(monkeypatch):
    calls = []

    class PoolManager:
        def add_day(self, date_str, records, *, meta=None):
            calls.append(("add_day", date_str, records, meta))

        def mark_day_probe(self, *_args, **_kwargs):
            calls.append(("mark_day_probe",))

        def save(self):
            calls.append(("save",))
            return True

        def compute_pool(self, **_kwargs):
            calls.append(("compute_pool",))
            return []

    owner = SimpleNamespace(
        data_provider=object(),
        _get_engine=lambda: object(),
        _load_ai_chain_context_map=lambda: {},
        _build_pool_display_rows=lambda *_args: [],
    )
    token = SimpleNamespace(raise_if_cancelled=lambda: None)
    monkeypatch.setattr(lhb_tab_module, "_create_lhb_pool_manager", lambda _trigger: PoolManager())
    monkeypatch.setattr(
        lhb_tab_module,
        "_fetch_missing_lhb_dates",
        lambda *_args: ({"20260724": {"records": [], "meta": None}}, 1),
    )
    monkeypatch.setattr(lhb_tab_module, "_validate_lhb_dates", lambda *_args: ({}, {}))

    result = lhb_tab_module._build_lhb_backfill_payload(
        owner,
        ["20260724"],
        [],
        "20260729",
        lambda *_args: None,
        token,
    )

    assert result["pool_changed"] is True
    assert calls == [
        ("add_day", "20260724", [], None),
        ("save",),
        ("compute_pool",),
    ]


def test_lhb_backfill_retries_when_global_task_is_active(monkeypatch):
    active_states = iter((True, False))
    monkeypatch.setattr(lhb_tab_module.task_manager, "is_active_task", lambda _task_id: next(active_states))
    submissions = []
    captured = []
    monkeypatch.setattr(
        lhb_tab_module,
        "_build_lhb_backfill_payload",
        lambda _owner, missing, validation, ref, _log, _token: captured.append((missing, validation, ref)) or {},
    )

    class FakeLifecycle:
        def run_background(self, name, fn, **kwargs):
            submissions.append((name, fn, kwargs))
            return object()

        @staticmethod
        def shutdown(*, timeout_ms=0):
            return True

    tab = LhbTab(object(), autoload_pool=False)
    tab._task_lifecycle = FakeLifecycle()
    try:
        tab._start_backfill(["20260420"], ["20260419"], "20260420")

        assert tab._backfill_in_progress is False
        assert tab.btn_refresh.isEnabled() is True
        assert tab._pool_retry_timer.isActive() is True
        tab._pool_retry_timer.stop()
        tab._pool_retry_timer.timeout.emit()
        assert tab._pending_backfill_request is None
        assert submissions[0][0] == "pool_backfill"
        submissions[0][1](object())
        assert captured == [(["20260420"], ["20260419"], "20260420")]
    finally:
        tab.deleteLater()


def test_lhb_backfill_failures_replace_stale_loading_overlay():
    callbacks = {}

    class FakeLifecycle:
        def run_background(self, _name, _fn, **kwargs):
            callbacks.update(kwargs)
            return object()

        @staticmethod
        def shutdown(*, timeout_ms=0):
            return True

    tab = LhbTab(object(), autoload_pool=False)
    tab._task_lifecycle = FakeLifecycle()
    try:
        tab.table_state.show_loading("正在加载龙虎榜池")
        tab._start_backfill(["20260420"])
        callbacks["on_success"]({})

        assert tab.table_state._overlay._mode == "error"
        assert tab.table_state._overlay._title.text() == "同步失败"

        tab.table_state.show_loading("正在加载龙虎榜池")
        tab._start_backfill(["20260420"])
        callbacks["on_error"]("远端异常")

        assert tab.table_state._overlay._mode == "error"
        assert tab.table_state._overlay._title.text() == "抓取异常"

        tab.model.update_data([{"代码": "000001"}])
        tab.table_state.show_loading("正在加载龙虎榜池")
        tab._start_backfill(["20260420"])
        callbacks["on_error"]("远端异常")

        assert tab.table_state._stack.currentWidget() is tab.table_state.table
    finally:
        tab.deleteLater()


def test_lhb_validation_only_completion_preserves_warm_table_without_invalidation(monkeypatch):
    tab = LhbTab(object(), autoload_pool=False)
    tab.model.update_data([{"代码": "000001", "名称": "平安银行"}], hydrate_latest_quotes=False)
    tab._refresh_lhb_lineage(list(tab.model.row_data))
    tab.table_state.show_table()
    monkeypatch.setattr(tab, "isVisible", lambda: True)
    monkeypatch.setattr(tab, "_is_current_workspace_tab", lambda: True)
    tab._pending_pool_refresh = False
    tab._active_backfill_had_pending_pool_refresh = True
    scheduled_refreshes = []
    monkeypatch.setattr(tab, "_schedule_pending_pool_refresh", lambda: scheduled_refreshes.append(True))
    pool_manager = object()
    stack_changes = QSignalSpy(tab.table_state._stack.currentChanged)
    model_reset = QSignalSpy(tab.model.modelReset)
    layout_changed = QSignalSpy(tab.model.layoutChanged)
    data_changed = QSignalSpy(tab.model.dataChanged)
    try:
        lhb_tab_module._complete_lhb_backfill_success(
            tab,
            {
                "status": "validation_only",
                "fetched": {},
                "validated": {"20260724": {"count": 61, "status": "error"}},
                "pool_manager": pool_manager,
                "pool_changed": False,
            },
        )

        assert tab.pool_manager is pool_manager
        assert "校验未更新" in tab.lbl_status.text()
        assert tab.table_state._stack.currentWidget() is tab.table_state.table
        assert tab.model.rowCount() == 1
        assert len(stack_changes) == 0
        assert len(model_reset) == 0
        assert len(layout_changed) == 0
        assert len(data_changed) == 0
        assert scheduled_refreshes == []
        assert tab._pending_pool_refresh is True
    finally:
        tab.deleteLater()


def test_lhb_validation_only_status_distinguishes_partial_empty_and_persist_failure():
    tab = LhbTab(object(), autoload_pool=False)
    tab.model.update_data([{"代码": "000001"}], hydrate_latest_quotes=False)
    tab.table_state.show_table()
    try:
        cases = (
            (
                {"a": {"status": "ok"}, "b": {"status": "error"}},
                "ok",
                "部分校验失败",
            ),
            ({"a": {"status": "empty"}}, "ok", "源头暂空"),
            ({"a": {"status": "ok"}}, "error", "校验状态未保存"),
        )
        for validated, persist_status, expected_status in cases:
            lhb_tab_module._complete_lhb_backfill_success(
                tab,
                {
                    "status": "validation_only",
                    "fetched": {},
                    "validated": validated,
                    "pool_changed": False,
                    "persist_status": persist_status,
                },
            )
            assert expected_status in tab.lbl_status.text()
            assert "缓存已核验" not in tab.lbl_status.text()
    finally:
        tab.deleteLater()


def test_lhb_all_error_completion_preserves_warm_table_without_invalidation(monkeypatch):
    tab = LhbTab(object(), autoload_pool=False)
    tab.model.update_data([{"代码": "000001", "名称": "平安银行"}], hydrate_latest_quotes=False)
    tab._refresh_lhb_lineage(list(tab.model.row_data))
    tab.table_state.show_table()
    tab._pending_pool_refresh = False
    tab._active_backfill_had_pending_pool_refresh = True
    scheduled_refreshes = []
    monkeypatch.setattr(tab, "_schedule_pending_pool_refresh", lambda: scheduled_refreshes.append(True))
    stack_changes = QSignalSpy(tab.table_state._stack.currentChanged)
    model_reset = QSignalSpy(tab.model.modelReset)
    layout_changed = QSignalSpy(tab.model.layoutChanged)
    data_changed = QSignalSpy(tab.model.dataChanged)
    try:
        lhb_tab_module._complete_lhb_backfill_success(tab, {})

        assert tab.table_state._stack.currentWidget() is tab.table_state.table
        assert tab.model.rowCount() == 1
        assert len(stack_changes) == 0
        assert len(model_reset) == 0
        assert len(layout_changed) == 0
        assert len(data_changed) == 0
        assert scheduled_refreshes == []
        assert tab._pending_pool_refresh is True
    finally:
        tab.deleteLater()


def test_lhb_shutdown_cancels_owned_tasks_with_bounded_wait():
    calls = []

    class FakeLifecycle:
        @staticmethod
        def shutdown(*, timeout_ms=0):
            calls.append(timeout_ms)
            return True

    tab = LhbTab(object(), autoload_pool=False)
    tab._task_lifecycle = FakeLifecycle()
    tab._pool_load_in_progress = True
    tab._backfill_in_progress = True

    tab.shutdown()

    assert calls == [lhb_tab_module.LHB_TASK_SHUTDOWN_WAIT_TIMEOUT_MS]
    assert tab._pool_load_in_progress is False
    assert tab._backfill_in_progress is False
    tab.deleteLater()


def test_lhb_pool_update_signal_debounces_visible_refresh(monkeypatch, qt_application):
    calls = []
    monkeypatch.setattr(lhb_tab_module, "LHB_POOL_UPDATE_DEBOUNCE_MS", 20)
    monkeypatch.setattr(LhbTab, "_is_current_workspace_tab", lambda self: True)
    monkeypatch.setattr(LhbTab, "isVisible", lambda self: True, raising=False)

    tab = LhbTab(object(), autoload_pool=False)
    monkeypatch.setattr(tab, "_load_and_display_pool", lambda emit_event=True: calls.append(emit_event))
    try:
        tab._pool_bootstrap_started = True
        timeout_spy = QSignalSpy(tab._pool_update_refresh_timer.timeout)

        tab._on_lhb_pool_updated()
        tab._on_lhb_pool_updated()

        assert calls == []
        assert tab._pending_pool_refresh is True
        assert tab._pool_update_refresh_timer.isActive() is True

        if len(timeout_spy) == 0:
            assert timeout_spy.wait(1000)
        qt_application.processEvents()

        assert len(timeout_spy) == 1
        assert calls == [False]
        assert tab._pending_pool_refresh is False
        assert tab._pool_update_refresh_timer.isActive() is False
        assert tab._run_pending_pool_refresh() is False
        assert calls == [False]
    finally:
        tab.deleteLater()


def test_lhb_cache_reload_defers_pool_bootstrap_by_five_seconds(monkeypatch):
    scheduled = []
    background_calls = []
    monkeypatch.setattr(lhb_tab_module.time, "monotonic", lambda: 10.0)
    monkeypatch.setattr(
        lhb_tab_module.QTimer,
        "singleShot",
        lambda delay, callback: scheduled.append((delay, callback)),
    )
    monkeypatch.setattr(
        lhb_tab_module.task_manager,
        "run_in_background",
        lambda *args, **kwargs: background_calls.append((args, kwargs)),
    )

    tab = LhbTab(object(), autoload_pool=False)
    try:
        scheduled.clear()
        tab._pool_bootstrap_started = True
        tab._on_cache_reload_completed()

        assert background_calls == []
        assert tab._post_f5_pool_defer_until == 15.0
        assert scheduled[0][0] == lhb_tab_module.POST_F5_POOL_BOOTSTRAP_DEFER_MS == 5000
        assert tab._post_f5_pool_pending is True
        assert tab._post_f5_pool_emit_event is True
    finally:
        tab.deleteLater()


def test_lhb_watchlist_radar_rows_stays_on_display_snapshot_without_bootstrap(monkeypatch):
    monkeypatch.setattr(
        LhbTab,
        "_load_and_display_pool",
        lambda self: (_ for _ in ()).throw(AssertionError("should not load full tab")),
        raising=False,
    )

    tab = LhbTab(object(), autoload_pool=False)
    tab.pool_manager = SimpleNamespace(
        compute_pool=lambda data_provider=None, engine=None: (
            (_ for _ in ()).throw(AssertionError("watchlist radar should not compute LHB pool inline"))
        )
    )

    try:
        assert tab.get_watchlist_radar_rows() == []
        tab.model.update_data(
            [
                {
                    "代码": "300750",
                    "名称": "宁德时代",
                    "最近上榜": "04-20",
                    "_最近上榜_raw": "20260420",
                }
            ]
        )
        rows = tab.get_watchlist_radar_rows()

        assert rows[0]["代码"] == "300750"
        assert rows[0]["最近上榜"] == "04-20"
        assert rows[0]["_最近上榜_raw"] == "20260420"
    finally:
        tab.deleteLater()
