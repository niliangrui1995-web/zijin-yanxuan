# -*- coding: utf-8 -*-

from core.market_calendar import MarketCalendar


def _fake_status(status: str):
    return classmethod(lambda cls, market="CN": status)


def test_is_quote_refresh_time_allows_lunch(monkeypatch):
    monkeypatch.setattr(MarketCalendar, "get_market_status", _fake_status("午休"))
    assert MarketCalendar.is_quote_refresh_time("CN") is True


def test_is_quote_refresh_time_rejects_after_hours(monkeypatch):
    monkeypatch.setattr(MarketCalendar, "get_market_status", _fake_status("盘后"))
    assert MarketCalendar.is_quote_refresh_time("CN") is False
