# -*- coding: utf-8 -*-
import datetime as dt

from core.market_calendar import MarketCalendar
from ui.tabs.lhb_tab import LhbTab
import ui.workers.lhb_worker as lhb_worker_module


def test_lhb_reference_trade_date_uses_previous_day_before_20(monkeypatch):
    monkeypatch.setattr(MarketCalendar, "_get_market_now", classmethod(lambda cls, market="CN": dt.datetime(2026, 4, 14, 8, 30)))
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
    monkeypatch.setattr(MarketCalendar, "_get_market_now", classmethod(lambda cls, market="CN": dt.datetime(2026, 4, 14, 20, 5)))
    monkeypatch.setattr(MarketCalendar, "is_trade_day", classmethod(lambda cls, day=None, market="CN": True))
    monkeypatch.setattr(
        MarketCalendar,
        "get_latest_trade_date",
        classmethod(lambda cls, market="CN", ref_date=None: dt.date(2026, 4, 14)),
    )

    assert LhbTab._get_lhb_reference_trade_date() == dt.date(2026, 4, 14)


def test_lhb_manual_refresh_prefers_today_when_probe_finds_data(monkeypatch):
    monkeypatch.setattr(MarketCalendar, "_get_market_now", classmethod(lambda cls, market="CN": dt.datetime(2026, 4, 20, 19, 45)))
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
        classmethod(lambda cls, n=20, ref_date=None: [ref_date.strftime("%Y%m%d")]),
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
    monkeypatch.setattr(MarketCalendar, "_get_market_now", classmethod(lambda cls, market="CN": dt.datetime(2026, 4, 20, 19, 45)))
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
        classmethod(lambda cls, n=20, ref_date=None: [ref_date.strftime("%Y%m%d")]),
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


def test_lhb_build_backfill_progress_log_formats_statuses():
    ok_level, ok_msg = LhbTab._build_backfill_progress_log(1, 20, "20260401", {"status": "ok", "count": 68})
    empty_level, empty_msg = LhbTab._build_backfill_progress_log(2, 20, "20260402", {"status": "empty", "count": 0})
    err_level, err_msg = LhbTab._build_backfill_progress_log(3, 20, "20260403", {"status": "error", "count": 0})

    assert ok_level == "info"
    assert ok_msg == "[龙虎榜池] [01/20] 20260401 完成 | 68条"
    assert empty_level == "info"
    assert empty_msg == "[龙虎榜池] [02/20] 20260402 无可用数据"
    assert err_level == "warn"
    assert err_msg == "[龙虎榜池] [03/20] 20260403 抓取异常 | 已记0条"


def test_lhb_should_refresh_after_probe_only_on_count_mismatch():
    assert LhbTab._should_refresh_after_probe(61, {"status": "ok", "count": 65}) is True
    assert LhbTab._should_refresh_after_probe(61, {"status": "ok", "count": 61}) is False
    assert LhbTab._should_refresh_after_probe(61, {"status": "empty", "count": 0}) is False
    assert LhbTab._should_refresh_after_probe(61, {"status": "error", "count": 0}) is False


def test_lhb_can_defer_pool_bootstrap_until_first_show(monkeypatch):
    calls = []
    monkeypatch.setattr(LhbTab, "_start_auto_scheduler", lambda self: calls.append("scheduler"), raising=False)
    monkeypatch.setattr(LhbTab, "_load_and_display_pool", lambda self: calls.append("load"), raising=False)

    tab = LhbTab(object(), autoload_pool=False)
    try:
        assert calls == ["scheduler"]
        tab._ensure_pool_bootstrap_started()
        assert calls == ["scheduler", "load"]
    finally:
        tab.deleteLater()
