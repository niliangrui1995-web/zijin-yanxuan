# -*- coding: utf-8 -*-
from core.market_calendar import MarketCalendar
from ui.tabs.earnings_tab import EarningsTab


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
