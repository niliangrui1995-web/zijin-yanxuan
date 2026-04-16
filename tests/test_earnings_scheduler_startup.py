# -*- coding: utf-8 -*-
from __future__ import annotations

from core.market_calendar import MarketCalendar
from earnings.scheduler import EarningsScheduler


def test_build_startup_scan_dates_returns_recent_window_when_cache_is_empty(monkeypatch):
    monkeypatch.setattr(
        MarketCalendar,
        "get_recent_trade_dates",
        classmethod(lambda cls, n=20, ref_date=None: ["20260416", "20260415", "20260414"]),
    )

    dates = EarningsScheduler._build_startup_scan_dates("2026-04-16", has_cached_records=False)

    assert dates == ["2026-04-14", "2026-04-15", "2026-04-16"]


def test_build_startup_scan_dates_only_returns_missing_trade_days(monkeypatch):
    monkeypatch.setattr(
        MarketCalendar,
        "get_recent_trade_dates",
        classmethod(lambda cls, n=20, ref_date=None: ["20260416", "20260415", "20260414", "20260411"]),
    )

    dates = EarningsScheduler._build_startup_scan_dates("2026-04-14", has_cached_records=True)

    assert dates == ["2026-04-15", "2026-04-16"]


def test_build_startup_scan_dates_returns_empty_when_window_is_already_covered(monkeypatch):
    monkeypatch.setattr(
        MarketCalendar,
        "get_recent_trade_dates",
        classmethod(lambda cls, n=20, ref_date=None: ["20260416", "20260415", "20260414"]),
    )

    dates = EarningsScheduler._build_startup_scan_dates("2026-04-16", has_cached_records=True)

    assert dates == []
