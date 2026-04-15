# -*- coding: utf-8 -*-
import pandas as pd

from core.market_calendar import MarketCalendar
from ui.tabs.earnings_tab import EarningsTab, EARNINGS_DISPLAY_TRADE_DAYS


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
