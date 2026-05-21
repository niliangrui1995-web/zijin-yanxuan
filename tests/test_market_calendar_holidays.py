import datetime as dt

from core.market_calendar_holidays import (
    apply_market_holiday_supplements,
    fetch_public_holidays,
    load_holidays_from_store,
    normalize_holiday_days,
    save_holidays_to_store,
)


def test_normalize_holiday_days_filters_invalid_values():
    result = normalize_holiday_days(["2026-04-16", "bad", None, "2026-04-16 09:30:00", "2026/04/16", "2026-04-17"])

    assert result == {"2026-04-16", "2026-04-17"}


def test_holiday_store_round_trip(tmp_path):
    project_root = str(tmp_path)
    save_holidays_to_store(project_root, "HK", 2026, {"2026-01-01", "2026-02-18"})

    rows = load_holidays_from_store(project_root, "HK")

    assert len(rows) == 1
    year, days, updated_at = rows[0]
    assert year == 2026
    assert days == {"2026-01-01", "2026-02-18"}
    assert isinstance(updated_at, dt.datetime)


def test_japan_holiday_supplement_repairs_2026_substitute_holiday():
    stale_source_days = {"2026-05-04", "2026-05-05"}

    result = apply_market_holiday_supplements("T", 2026, stale_source_days)

    assert "2026-05-06" in result
    assert "2026-09-22" in result


def test_japan_holiday_store_load_repairs_stale_cache(tmp_path):
    project_root = str(tmp_path)
    save_holidays_to_store(project_root, "T", 2026, {"2026-05-04", "2026-05-05"})

    rows = load_holidays_from_store(project_root, "T")

    assert len(rows) == 1
    year, days, _ = rows[0]
    assert year == 2026
    assert "2026-05-06" in days


def test_fetch_public_holidays_for_tw_delegates_to_twse(monkeypatch):
    calls: list[tuple[int, tuple[str, ...], tuple[str, ...]]] = []

    def _fake_fetch_twse(year, include_keywords, exclude_keywords):
        calls.append((year, include_keywords, exclude_keywords))
        return {"2026-02-16"}

    monkeypatch.setattr(
        "core.market_calendar_holidays.fetch_twse_holidays",
        _fake_fetch_twse,
    )

    result = fetch_public_holidays(
        market="TW",
        year=2026,
        nager_country={"HK": "HK"},
        twse_include_keywords=("放假",),
        twse_exclude_keywords=("補行上班",),
    )

    assert result == {"2026-02-16"}
    assert calls == [(2026, ("放假",), ("補行上班",))]
