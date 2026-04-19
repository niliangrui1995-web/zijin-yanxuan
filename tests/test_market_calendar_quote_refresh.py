# -*- coding: utf-8 -*-

from datetime import date

from core.market_calendar import MarketCalendar


def _fake_status(status: str):
    return classmethod(lambda cls, market="CN": status)


def test_is_quote_refresh_time_allows_lunch(monkeypatch):
    monkeypatch.setattr(MarketCalendar, "get_market_status", _fake_status("午休"))
    assert MarketCalendar.is_quote_refresh_time("CN") is True


def test_is_quote_refresh_time_rejects_after_hours(monkeypatch):
    monkeypatch.setattr(MarketCalendar, "get_market_status", _fake_status("盘后"))
    assert MarketCalendar.is_quote_refresh_time("CN") is False


def test_is_trade_day_is_conservative_for_today_when_calendar_missing(monkeypatch):
    monkeypatch.setattr(MarketCalendar, "_trade_dates", None, raising=False)
    monkeypatch.setattr(MarketCalendar, "_trade_dates_loading", False, raising=False)
    monkeypatch.setattr(MarketCalendar, "today", classmethod(lambda cls, market="CN": date(2026, 4, 6)))
    monkeypatch.setattr(MarketCalendar, "load_trade_dates", classmethod(lambda cls: None))

    assert MarketCalendar.is_trade_day(date(2026, 4, 6), "CN") is False


def test_get_latest_trade_date_falls_back_to_weekday_when_calendar_missing(monkeypatch):
    monkeypatch.setattr(MarketCalendar, "_trade_dates", None, raising=False)
    monkeypatch.setattr(MarketCalendar, "_trade_dates_loading", False, raising=False)
    monkeypatch.setattr(MarketCalendar, "load_trade_dates", classmethod(lambda cls: None))

    assert MarketCalendar.get_latest_trade_date("CN", date(2026, 4, 6)) == date(2026, 4, 6)
    assert MarketCalendar.get_latest_trade_date("CN", date(2026, 4, 5)) == date(2026, 4, 3)
