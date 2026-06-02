import datetime as dt

import domains.global_earnings_calendar.rules as rules
from domains.global_earnings_calendar.rules import (
    beijing_time_from_local,
    date_from_any,
    date_from_beijing_time,
    date_from_compact_text,
    date_from_english_text,
    datetime_from_beijing_time,
    label_matches_company,
    local_code_from_ticker,
    market_from_ticker,
    normalize_text,
    text_has_any,
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


def test_global_earnings_calendar_rules_cover_invalid_date_paths(monkeypatch):
    assert normalize_text("A股-2330.TW") == "a2330tw"
    assert text_has_any("Quarterly Results", ("results",))
    assert not label_matches_company("", "Sony")

    assert date_from_compact_text("2026/13/40") is None
    assert date_from_compact_text("20261340") is None

    monkeypatch.setitem(rules.ENGLISH_MONTHS, "may", None)
    assert date_from_english_text("May 1, 2026") is None
    assert date_from_english_text("February 31, 2026") is None


def test_global_earnings_calendar_rules_cover_date_like_objects_and_bad_text():
    class DateLike:
        def date(self):
            return dt.date(2026, 5, 16)

    class BadDateLike:
        def date(self):
            raise TypeError("bad date")

    assert date_from_any(DateLike()) == dt.date(2026, 5, 16)
    assert date_from_any(BadDateLike()) is None
    assert date_from_any("") is None
    assert date_from_any("not-a-date") is None


def test_global_earnings_calendar_rules_cover_beijing_time_edge_cases():
    day = dt.date(2026, 5, 16)

    assert beijing_time_from_local(day, "no time", utc_offset_hours=8) == ""
    assert beijing_time_from_local(day, "25:00", utc_offset_hours=8) == ""

    assert date_from_beijing_time("no date", "2026-05-16") is None
    assert date_from_beijing_time("2026/2/31 10:00", "2026-05-16") is None
    assert date_from_beijing_time("5/17 10:00", "bad report date") is None
    assert date_from_beijing_time("2/31 10:00", "2026-02-01") is None

    assert datetime_from_beijing_time("5/17", "2026-05-16") is None
    assert datetime_from_beijing_time("5/17 25:00", "2026-05-16") is None
