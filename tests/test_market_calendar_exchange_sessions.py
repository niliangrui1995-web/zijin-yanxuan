# -*- coding: utf-8 -*-

import datetime

from core.market_calendar import MarketCalendar


def _always_trade_day():
    return classmethod(lambda cls, day, market="CN": True)


def _fake_now(value: datetime.datetime):
    return classmethod(lambda cls, market="CN": value)


def test_from_timestamp_converts_same_instant_to_different_market_times():
    ts = datetime.datetime(2026, 4, 14, 1, 0, tzinfo=datetime.timezone.utc).timestamp()

    cn_now = MarketCalendar.from_timestamp(ts, "CN")
    jp_now = MarketCalendar.from_timestamp(ts, "T")
    hk_now = MarketCalendar.from_timestamp(ts, "HK")

    assert cn_now == datetime.datetime(2026, 4, 14, 9, 0)
    assert hk_now == datetime.datetime(2026, 4, 14, 9, 0)
    assert jp_now == datetime.datetime(2026, 4, 14, 10, 0)


def test_cn_call_auction_status(monkeypatch):
    monkeypatch.setattr(MarketCalendar, "is_trade_day", _always_trade_day())
    monkeypatch.setattr(
        MarketCalendar,
        "_get_market_now",
        _fake_now(datetime.datetime(2026, 4, 14, 9, 20)),
    )

    assert MarketCalendar.get_market_status("CN") == "开盘集合竞价"
    assert MarketCalendar.is_market_active("CN") is True


def test_cn_pre_open_session_is_refreshable(monkeypatch):
    monkeypatch.setattr(MarketCalendar, "is_trade_day", _always_trade_day())
    monkeypatch.setattr(
        MarketCalendar,
        "_get_market_now",
        _fake_now(datetime.datetime(2026, 4, 14, 9, 27)),
    )

    assert MarketCalendar.get_market_status("CN") == "开市前时段"
    assert MarketCalendar.is_market_active("CN") is True
    assert MarketCalendar.is_quote_refresh_time("CN") is True


def test_cn_early_morning_premarket_stays_idle(monkeypatch):
    monkeypatch.setattr(MarketCalendar, "is_trade_day", _always_trade_day())
    monkeypatch.setattr(
        MarketCalendar,
        "_get_market_now",
        _fake_now(datetime.datetime(2026, 4, 14, 8, 30)),
    )

    assert MarketCalendar.get_market_status("CN") == "盘前"
    assert MarketCalendar.is_market_active("CN") is False
    assert MarketCalendar.is_quote_refresh_time("CN") is False


def test_japan_extended_close_session(monkeypatch):
    monkeypatch.setattr(MarketCalendar, "is_trade_day", _always_trade_day())
    monkeypatch.setattr(
        MarketCalendar,
        "_get_market_now",
        _fake_now(datetime.datetime(2026, 4, 14, 15, 27)),
    )

    assert MarketCalendar.get_market_status("T") == "收盘集合竞价"
    assert MarketCalendar.is_market_active("T") is True


def test_japan_session_fallback_uses_1530(monkeypatch):
    monkeypatch.setattr(MarketCalendar, "is_trade_day", _always_trade_day())
    monkeypatch.setattr(
        MarketCalendar,
        "_get_market_now",
        _fake_now(datetime.datetime(2026, 4, 14, 15, 20)),
    )
    monkeypatch.setitem(MarketCalendar._MARKET_PHASES, "T", None)

    assert MarketCalendar.get_market_status("T") == "交易中"
    assert MarketCalendar.is_market_active("T") is True


def test_japan_stale_holiday_cache_marks_20260506_closed(monkeypatch):
    monkeypatch.setattr(MarketCalendar, "_asian_bootstrapped", True, raising=False)
    monkeypatch.setattr(
        MarketCalendar,
        "_asian_holidays",
        {"TW": {}, "HK": {}, "T": {2026: {"2026-05-04", "2026-05-05"}}, "KS": {}},
        raising=False,
    )
    monkeypatch.setattr(
        MarketCalendar,
        "_get_market_now",
        _fake_now(datetime.datetime(2026, 5, 6, 10, 0)),
    )

    assert MarketCalendar.get_market_status("T") == "休市"
    assert MarketCalendar.is_market_active("T") is False


def test_korea_stale_holiday_cache_marks_20260525_closed(monkeypatch):
    monkeypatch.setattr(MarketCalendar, "_asian_bootstrapped", True, raising=False)
    monkeypatch.setattr(
        MarketCalendar,
        "_asian_holidays",
        {"TW": {}, "HK": {}, "T": {}, "KS": {2026: {"2026-05-05", "2026-05-24"}}},
        raising=False,
    )
    monkeypatch.setattr(
        MarketCalendar,
        "_get_market_now",
        _fake_now(datetime.datetime(2026, 5, 25, 10, 0)),
    )

    assert MarketCalendar.get_market_status("KS") == "\u4f11\u5e02"
    assert MarketCalendar.is_market_active("KS") is False
    assert MarketCalendar.is_quote_refresh_time("KS") is False


def test_hk_closing_auction_status(monkeypatch):
    monkeypatch.setattr(MarketCalendar, "is_trade_day", _always_trade_day())
    monkeypatch.setattr(
        MarketCalendar,
        "_get_market_now",
        _fake_now(datetime.datetime(2026, 4, 14, 16, 5)),
    )

    assert MarketCalendar.get_market_status("HK") == "收市竞价"
    assert MarketCalendar.is_quote_refresh_time("HK") is True


def test_kr_closing_auction_status(monkeypatch):
    monkeypatch.setattr(MarketCalendar, "is_trade_day", _always_trade_day())
    monkeypatch.setattr(
        MarketCalendar,
        "_get_market_now",
        _fake_now(datetime.datetime(2026, 4, 14, 15, 25)),
    )

    assert MarketCalendar.get_market_status("KS") == "收盘集合竞价"
    assert MarketCalendar.is_market_active("KS") is True


def test_tw_pre_and_post_close_sessions_are_refreshable(monkeypatch):
    monkeypatch.setattr(MarketCalendar, "is_trade_day", _always_trade_day())

    monkeypatch.setattr(
        MarketCalendar,
        "_get_market_now",
        _fake_now(datetime.datetime(2026, 4, 14, 8, 45)),
    )
    assert MarketCalendar.get_market_status("TW") == "盘前委托"
    assert MarketCalendar.is_market_active("TW") is True
    assert MarketCalendar.is_quote_refresh_time("TW") is True

    monkeypatch.setattr(
        MarketCalendar,
        "_get_market_now",
        _fake_now(datetime.datetime(2026, 4, 14, 14, 5)),
    )
    assert MarketCalendar.get_market_status("TW") == "盘后定价申报"
    assert MarketCalendar.is_market_active("TW") is True
    assert MarketCalendar.is_quote_refresh_time("TW") is True
