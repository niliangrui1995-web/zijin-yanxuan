import datetime as dt

from domains.global_earnings_calendar.rules import (
    beijing_time_from_local,
    date_from_any,
    date_from_beijing_time,
    date_from_compact_text,
    date_from_english_text,
    label_matches_company,
    local_code_from_ticker,
    market_from_ticker,
)


def test_global_earnings_calendar_rules_parse_dates_and_markets():
    assert market_from_ticker("2330.TW") == "TW"
    assert market_from_ticker("NVDA") == "US"
    assert local_code_from_ticker("6758.T") == "6758"
    assert date_from_compact_text("2026/05/16") == dt.date(2026, 5, 16)
    assert date_from_compact_text("20260516") == dt.date(2026, 5, 16)
    assert date_from_english_text("May 16, 2026") == dt.date(2026, 5, 16)
    assert date_from_any(dt.datetime(2026, 5, 16, 8, 0)) == dt.date(2026, 5, 16)


def test_global_earnings_calendar_rules_match_labels_and_beijing_time():
    assert label_matches_company("Sony Group (6758.T)", "Sony Group")
    assert not label_matches_company("Samsung Electronics", "SK Hynix")
    assert beijing_time_from_local(dt.date(2026, 5, 16), "16:00", utc_offset_hours=9) == "2026-05-16 15:00"
    assert date_from_beijing_time("5/17 05:00", "2026-05-16") == dt.date(2026, 5, 17)
