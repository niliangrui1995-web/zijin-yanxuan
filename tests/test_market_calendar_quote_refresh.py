# -*- coding: utf-8 -*-

import builtins
import datetime
import sys
from datetime import date
from types import SimpleNamespace

import pandas as pd

from core.exceptions import BusinessRuleError, CacheIOError, DataFormatError, NetworkServiceError
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


def test_market_calendar_basic_normalization_and_cached_trade_dates():
    assert MarketCalendar.normalize_market("sz") == "CN"
    assert MarketCalendar.normalize_market("jpn") == "T"
    assert MarketCalendar.infer_market("2330.TW") == "TW"
    assert MarketCalendar.infer_market("000001") == "CN"
    assert MarketCalendar.infer_market("NVDA") == "US"
    assert MarketCalendar._coerce_date("2026-04-20 extra") == date(2026, 4, 20)

    cached, current = MarketCalendar._extract_cached_trade_dates(
        {"month": "2026-04", "dates": ["2026-04-20", "bad"]},
        cur_month="2026-04",
    )

    assert cached == {"2026-04-20"}
    assert current is True
    assert MarketCalendar._extract_cached_trade_dates([], cur_month="2026-04") == (None, False)
    assert MarketCalendar._extract_cached_trade_dates({"dates": []}, cur_month="2026-04") == (None, False)
    assert MarketCalendar.normalize_market("") == "CN"
    assert MarketCalendar.normalize_market("LSE") == "LSE"
    assert MarketCalendar.infer_market("") == "CN"
    assert MarketCalendar.infer_market("0700.HKG") == "HK"
    assert MarketCalendar._coerce_date(datetime.datetime(2026, 4, 20, 9, 30)) == date(2026, 4, 20)


def test_market_calendar_rejects_bad_dates_and_unsupported_types():
    try:
        MarketCalendar._coerce_date("bad-date")
    except DataFormatError as exc:
        assert "invalid date text" in str(exc)
    else:
        raise AssertionError("invalid date text should fail")

    try:
        MarketCalendar._coerce_date(object())
    except DataFormatError as exc:
        assert "unsupported date type" in str(exc)
    else:
        raise AssertionError("unsupported date type should fail")


def test_holiday_store_helpers_update_cache_metadata(monkeypatch):
    target = {}
    updated_at = datetime.datetime(2026, 4, 20, 9, 0)
    ensured = []
    saved = []

    monkeypatch.setattr(MarketCalendar, "_holiday_table_ready", False, raising=False)
    monkeypatch.setattr("domains.market_calendar.calendar_service.ensure_holiday_table", lambda root: ensured.append(root))
    monkeypatch.setattr(
        "domains.market_calendar.calendar_service.load_holidays_from_store",
        lambda root, market: [(2026, {"2026-01-01"}, updated_at)],
    )
    monkeypatch.setattr(
        "domains.market_calendar.calendar_service.save_holidays_to_store",
        lambda root, market, year, days: saved.append((market, year, days)),
    )
    monkeypatch.setattr(MarketCalendar, "_project_root", staticmethod(lambda: "D:/project"))
    monkeypatch.setattr(MarketCalendar, "_asian_holiday_updated_at", {}, raising=False)

    MarketCalendar._load_holidays_from_store("HK", target)
    MarketCalendar._save_holidays_to_store("HK", 2026, {"2026-01-01"})

    assert ensured == ["D:/project"]
    assert target == {2026: {"2026-01-01"}}
    assert MarketCalendar._asian_holiday_updated_at[("HK", 2026)] == updated_at
    assert saved == [("HK", 2026, {"2026-01-01"})]


def test_bootstrap_validate_retry_and_ensure_year_paths(monkeypatch):
    calls = []

    monkeypatch.setattr(MarketCalendar, "_asian_bootstrapped", False, raising=False)
    monkeypatch.setattr(MarketCalendar, "_asian_holidays", {"TW": {}, "HK": {}, "T": {}, "KS": {}}, raising=False)
    monkeypatch.setattr(MarketCalendar, "_load_holidays_from_store", classmethod(lambda cls, market, bucket: (_ for _ in ()).throw(CacheIOError("read"))))
    monkeypatch.setattr(MarketCalendar, "_validate_asian_year_coverage", classmethod(lambda cls: calls.append("validate")))
    monkeypatch.setattr(MarketCalendar, "_retry_empty_future_years", classmethod(lambda cls: calls.append("retry")))

    MarketCalendar._bootstrap_asian_holidays()

    assert MarketCalendar._asian_bootstrapped is True
    assert calls == ["validate", "retry"]

    scheduled = []
    monkeypatch.setattr(MarketCalendar, "_schedule_asian_holiday_refresh", classmethod(lambda cls, market, years: scheduled.append((market, years))))

    MarketCalendar._ensure_market_year("US", 2026)
    MarketCalendar._ensure_market_year("TW", 2026)

    assert scheduled == [("TW", [2026])]


def test_validate_and_retry_empty_years_schedule_missing_future_cache(monkeypatch):
    now = datetime.datetime(2026, 4, 20, 9, 0)
    scheduled = []
    monkeypatch.setattr(MarketCalendar, "_coverage_check_year", None, raising=False)
    monkeypatch.setattr(MarketCalendar, "_asian_holidays", {"TW": {2026: set()}, "HK": {}, "T": {}, "KS": {}}, raising=False)
    monkeypatch.setattr(MarketCalendar, "_asian_holiday_updated_at", {("TW", 2026): now - datetime.timedelta(days=8)}, raising=False)
    monkeypatch.setattr(MarketCalendar, "_get_market_now", classmethod(lambda cls, market="TW": now))
    monkeypatch.setattr(MarketCalendar, "_required_years", classmethod(lambda cls, market: [2025, 2026, 2027]))
    monkeypatch.setattr(MarketCalendar, "_schedule_asian_holiday_refresh", classmethod(lambda cls, market, years: scheduled.append((market, years))))

    MarketCalendar._validate_asian_year_coverage()
    MarketCalendar._validate_asian_year_coverage()
    MarketCalendar._retry_empty_future_years()

    assert ("TW", [2025, 2027]) in scheduled
    assert ("HK", [2025, 2026, 2027]) in scheduled
    assert ("TW", [2026]) in scheduled


def test_schedule_asian_holiday_refresh_updates_cache_and_clears_inflight(monkeypatch):
    monkeypatch.setattr(MarketCalendar, "_refresh_inflight", set(), raising=False)
    monkeypatch.setattr(MarketCalendar, "_asian_holidays", {"TW": {}, "HK": {}, "T": {}, "KS": {}}, raising=False)
    monkeypatch.setattr(MarketCalendar, "_asian_holiday_updated_at", {}, raising=False)
    monkeypatch.setattr(
        MarketCalendar,
        "now",
        classmethod(lambda cls, market="TW": datetime.datetime(2026, 4, 20, 9, 0)),
    )

    def fake_fetch(market, year):
        if year == 2027:
            raise NetworkServiceError("network")
        return {"2026-01-01"}

    captured = {}

    def fake_run_in_background(fn, *, on_success, on_error, task_id):
        captured["task_id"] = task_id
        captured["on_error"] = on_error
        on_success(fn())

    monkeypatch.setattr(MarketCalendar, "_fetch_public_holidays", classmethod(lambda cls, market, year: fake_fetch(market, year)))
    monkeypatch.setattr(
        MarketCalendar,
        "_save_holidays_to_store",
        classmethod(lambda cls, market, year, days: (_ for _ in ()).throw(CacheIOError("readonly"))),
    )
    monkeypatch.setattr(
        "domains.market_calendar.calendar_service.task_manager",
        SimpleNamespace(run_in_background=fake_run_in_background),
    )
    monkeypatch.setattr(
        "domains.market_calendar.calendar_service.task_registry",
        SimpleNamespace(startup=lambda task_id: SimpleNamespace(task_id=task_id)),
    )

    MarketCalendar._schedule_asian_holiday_refresh("TW", [2026, 2027])

    assert MarketCalendar._asian_holidays["TW"][2026] == {"2026-01-01"}
    assert ("TW", 2026) not in MarketCalendar._refresh_inflight
    assert ("TW", 2027) not in MarketCalendar._refresh_inflight
    assert captured["task_id"] == "holiday_refresh_TW_2026_2027"

    captured["on_error"]("failed")
    assert ("TW", 2026) not in MarketCalendar._refresh_inflight
    assert ("TW", 2027) not in MarketCalendar._refresh_inflight


def test_schedule_asian_holiday_refresh_handles_source_and_format_failures(monkeypatch):
    monkeypatch.setattr(MarketCalendar, "_refresh_inflight", set(), raising=False)
    monkeypatch.setattr(MarketCalendar, "_asian_holidays", {"TW": {}, "HK": {}, "T": {}, "KS": {}}, raising=False)
    monkeypatch.setattr(MarketCalendar, "_asian_holiday_updated_at", {}, raising=False)
    monkeypatch.setattr(MarketCalendar, "now", classmethod(lambda cls, market="TW": datetime.datetime(2026, 4, 20, 9, 0)))
    monkeypatch.setattr(MarketCalendar, "_save_holidays_to_store", classmethod(lambda cls, market, year, days: None))

    def fake_fetch(market, year):
        if year == 2026:
            raise BusinessRuleError("source unavailable")
        raise DataFormatError("bad payload")

    monkeypatch.setattr(MarketCalendar, "_fetch_public_holidays", classmethod(lambda cls, market, year: fake_fetch(market, year)))
    monkeypatch.setattr(
        "domains.market_calendar.calendar_service.task_manager",
        SimpleNamespace(run_in_background=lambda fn, *, on_success, on_error, task_id: on_success(fn())),
    )
    monkeypatch.setattr(
        "domains.market_calendar.calendar_service.task_registry",
        SimpleNamespace(startup=lambda task_id: SimpleNamespace(task_id=task_id)),
    )

    MarketCalendar._schedule_asian_holiday_refresh("TW", [2026, 2027])

    assert MarketCalendar._asian_holidays["TW"] == {2026: set(), 2027: set()}
    assert MarketCalendar._refresh_inflight == set()


def test_schedule_asian_holiday_refresh_ignores_invalid_and_duplicate_requests(monkeypatch):
    monkeypatch.setattr(MarketCalendar, "_refresh_inflight", {("TW", 2026)}, raising=False)
    called = []
    monkeypatch.setattr(
        "domains.market_calendar.calendar_service.task_manager",
        SimpleNamespace(run_in_background=lambda *args, **kwargs: called.append(True)),
    )

    MarketCalendar._schedule_asian_holiday_refresh("US", [2026])
    MarketCalendar._schedule_asian_holiday_refresh("TW", [])
    MarketCalendar._schedule_asian_holiday_refresh("TW", [2026])

    assert called == []


def test_schedule_trade_dates_refresh_runs_success_and_error(monkeypatch):
    saved = []

    class _DataStore:
        def save_json(self, key, payload):
            saved.append((key, payload))

    callbacks = {}

    def fake_run_in_background(fn, *, on_success, on_error, task_id):
        callbacks["on_error"] = on_error
        on_success(fn())

    monkeypatch.setattr(MarketCalendar, "_trade_dates_loading", False, raising=False)
    monkeypatch.setattr(MarketCalendar, "_trade_dates", None, raising=False)
    monkeypatch.setitem(sys.modules, "akshare", SimpleNamespace(tool_trade_date_hist_sina=lambda: pd.DataFrame({"trade_date": ["2026-04-20"]})))
    monkeypatch.setattr("infra.storage.DataStore", _DataStore)
    monkeypatch.setattr(
        "domains.market_calendar.calendar_service.task_manager",
        SimpleNamespace(run_in_background=fake_run_in_background),
    )
    monkeypatch.setattr(
        "domains.market_calendar.calendar_service.task_registry",
        SimpleNamespace(startup=lambda task_id: SimpleNamespace(task_id=task_id)),
    )

    MarketCalendar._schedule_trade_dates_refresh("2026-04")

    assert MarketCalendar._trade_dates_loading is False
    assert MarketCalendar._trade_dates == {"2026-04-20"}
    assert saved == [("trade_dates", {"month": "2026-04", "dates": ["2026-04-20"]})]

    MarketCalendar._trade_dates_loading = True
    callbacks["on_error"]("failed")
    assert MarketCalendar._trade_dates_loading is False


def test_schedule_trade_dates_refresh_skips_persist_after_store_close(monkeypatch):
    class _ClosedDataStore:
        is_closed = True

        def save_json(self, key, payload):
            raise AssertionError("closed store should not be written")

    def fake_run_in_background(fn, *, on_success, on_error, task_id):
        on_success(fn())

    monkeypatch.setattr(MarketCalendar, "_trade_dates_loading", False, raising=False)
    monkeypatch.setattr(MarketCalendar, "_trade_dates", None, raising=False)
    monkeypatch.setitem(sys.modules, "akshare", SimpleNamespace(tool_trade_date_hist_sina=lambda: pd.DataFrame({"trade_date": ["2026-04-20"]})))
    monkeypatch.setattr("infra.storage.DataStore", _ClosedDataStore)
    monkeypatch.setattr(
        "domains.market_calendar.calendar_service.task_manager",
        SimpleNamespace(run_in_background=fake_run_in_background),
    )
    monkeypatch.setattr(
        "domains.market_calendar.calendar_service.task_registry",
        SimpleNamespace(startup=lambda task_id: SimpleNamespace(task_id=task_id)),
    )

    MarketCalendar._schedule_trade_dates_refresh("2026-04")

    assert MarketCalendar._trade_dates_loading is False
    assert MarketCalendar._trade_dates == {"2026-04-20"}


def test_schedule_trade_dates_refresh_skips_persist_during_shutdown(monkeypatch):
    class _DataStore:
        def save_json(self, key, payload):
            raise AssertionError("shutdown should skip persistence")

    def fake_run_in_background(fn, *, on_success, on_error, task_id):
        on_success(fn())

    monkeypatch.setattr(MarketCalendar, "_trade_dates_loading", False, raising=False)
    monkeypatch.setattr(MarketCalendar, "_trade_dates", None, raising=False)
    monkeypatch.setitem(sys.modules, "akshare", SimpleNamespace(tool_trade_date_hist_sina=lambda: pd.DataFrame({"trade_date": ["2026-04-20"]})))
    monkeypatch.setattr("infra.storage.DataStore", _DataStore)
    monkeypatch.setattr(
        "domains.market_calendar.calendar_service.task_manager",
        SimpleNamespace(is_shutting_down=True, run_in_background=fake_run_in_background),
    )
    monkeypatch.setattr(
        "domains.market_calendar.calendar_service.task_registry",
        SimpleNamespace(startup=lambda task_id: SimpleNamespace(task_id=task_id)),
    )

    MarketCalendar._schedule_trade_dates_refresh("2026-04")

    assert MarketCalendar._trade_dates_loading is False
    assert MarketCalendar._trade_dates == {"2026-04-20"}


def test_schedule_trade_dates_refresh_short_circuits_and_handles_bad_payload(monkeypatch):
    calls = []
    monkeypatch.setattr(MarketCalendar, "_trade_dates_loading", True, raising=False)
    monkeypatch.setattr(
        "domains.market_calendar.calendar_service.task_manager",
        SimpleNamespace(run_in_background=lambda *args, **kwargs: calls.append(True)),
    )

    MarketCalendar._schedule_trade_dates_refresh("2026-04")

    assert calls == []

    callbacks = {}

    def fake_run_in_background(fn, *, on_success, on_error, task_id):
        callbacks["task_id"] = task_id
        try:
            on_success(fn())
        except DataFormatError as exc:
            on_error(str(exc))

    monkeypatch.setattr(MarketCalendar, "_trade_dates_loading", False, raising=False)
    monkeypatch.setitem(sys.modules, "akshare", SimpleNamespace(tool_trade_date_hist_sina=lambda: pd.DataFrame({"date": ["2026-04-20"]})))
    monkeypatch.setattr(
        "domains.market_calendar.calendar_service.task_manager",
        SimpleNamespace(run_in_background=fake_run_in_background),
    )
    monkeypatch.setattr(
        "domains.market_calendar.calendar_service.task_registry",
        SimpleNamespace(startup=lambda task_id: SimpleNamespace(task_id=task_id)),
    )

    MarketCalendar._schedule_trade_dates_refresh("2026-04")

    assert MarketCalendar._trade_dates_loading is False
    assert callbacks["task_id"] == "cn_trade_calendar_refresh"


def test_load_trade_dates_uses_store_and_schedules_stale_refresh(monkeypatch):
    scheduled = []

    class _DataStore:
        def load_json(self, key):
            return {"month": "2026-03", "dates": ["2026-04-20"]}

    monkeypatch.setattr(MarketCalendar, "_trade_dates_loading", False, raising=False)
    monkeypatch.setattr(MarketCalendar, "now", classmethod(lambda cls, market="CN": datetime.datetime(2026, 4, 20, 9, 0)))
    monkeypatch.setattr(MarketCalendar, "_schedule_trade_dates_refresh", classmethod(lambda cls, month: scheduled.append(month)))
    monkeypatch.setattr("infra.storage.DataStore", _DataStore)

    assert MarketCalendar.load_trade_dates() == {"2026-04-20"}
    assert scheduled == ["2026-04"]


def test_load_trade_dates_migrates_legacy_cache_file(monkeypatch, tmp_path):
    cache_dir = tmp_path / "data" / "Cache"
    cache_dir.mkdir(parents=True)
    cache_file = cache_dir / "trade_dates.json"
    cache_file.write_text('{"month": "2026-04", "dates": ["2026-04-20"]}', encoding="utf-8")
    saved = []

    class _DataStore:
        def load_json(self, key):
            raise OSError("store unavailable")

        def save_json(self, key, payload):
            saved.append((key, payload))

    monkeypatch.setattr(MarketCalendar, "now", classmethod(lambda cls, market="CN": datetime.datetime(2026, 4, 20, 9, 0)))
    monkeypatch.setattr(MarketCalendar, "_project_root", staticmethod(lambda: str(tmp_path)))
    monkeypatch.setattr("infra.storage.DataStore", _DataStore)

    assert MarketCalendar.load_trade_dates() == {"2026-04-20"}
    assert saved == [("trade_dates", {"month": "2026-04", "dates": ["2026-04-20"]})]
    assert (cache_dir / "trade_dates.json.migrated").exists()


def test_load_trade_dates_returns_none_while_refresh_is_loading(monkeypatch, tmp_path):
    class _DataStore:
        def load_json(self, key):
            raise TypeError("bad store")

    monkeypatch.setattr(MarketCalendar, "_trade_dates_loading", True, raising=False)
    monkeypatch.setattr(MarketCalendar, "now", classmethod(lambda cls, market="CN": datetime.datetime(2026, 4, 20, 9, 0)))
    monkeypatch.setattr(MarketCalendar, "_project_root", staticmethod(lambda: str(tmp_path)))
    monkeypatch.setattr("infra.storage.DataStore", _DataStore)

    assert MarketCalendar.load_trade_dates() is None


def test_market_calendar_timezone_fallback_and_date_coercion(monkeypatch):
    assert MarketCalendar._normalize_holiday_days(["2026-04-20", "bad"]) == {"2026-04-20"}
    assert MarketCalendar.infer_market("0700.HK") == "HK"

    fixed_today = datetime.date(2026, 4, 20)
    monkeypatch.setattr(MarketCalendar, "today", classmethod(lambda cls, market="CN": fixed_today))
    assert MarketCalendar._coerce_date(None) == fixed_today

    real_import = builtins.__import__

    def _blocked_import(name, *args, **kwargs):
        if name == "zoneinfo":
            raise ImportError("blocked")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", _blocked_import)

    cn_now = MarketCalendar._get_market_now("CN")
    us_from_ts = MarketCalendar.from_timestamp(datetime.datetime(2026, 4, 20, 12, tzinfo=datetime.timezone.utc).timestamp(), "US")

    assert isinstance(cn_now, datetime.datetime)
    assert us_from_ts.hour == 7


def test_market_calendar_ensure_year_and_trade_day_branches(monkeypatch):
    scheduled = []
    monkeypatch.setattr(MarketCalendar, "_asian_bootstrapped", True, raising=False)
    monkeypatch.setattr(MarketCalendar, "_asian_holidays", {"TW": {}, "HK": {}, "T": {}, "KS": {}}, raising=False)
    monkeypatch.setattr(MarketCalendar, "_schedule_asian_holiday_refresh", classmethod(lambda cls, market, years: scheduled.append((market, years))))

    MarketCalendar._ensure_market_year("US", 2026)
    MarketCalendar._ensure_market_year("TW", 2026)
    assert scheduled == [("TW", [2026])]

    monkeypatch.setattr(MarketCalendar, "_trade_dates", {"2026-04-20"}, raising=False)
    assert MarketCalendar.is_trade_day("2026-04-20", "CN") is True
    assert MarketCalendar.is_trade_day("2026-04-21", "CN") is False

    monkeypatch.setattr(MarketCalendar, "_trade_dates", set(), raising=False)
    monkeypatch.setattr(MarketCalendar, "load_trade_dates", classmethod(lambda cls: None))
    monkeypatch.setattr(MarketCalendar, "today", classmethod(lambda cls, market="CN": datetime.date(2026, 4, 20)))
    assert MarketCalendar.is_trade_day(datetime.date(2026, 4, 21), "CN") is True
    assert MarketCalendar.is_trade_day(datetime.date(2026, 4, 25), "CN") is False
    assert MarketCalendar.is_trade_day(datetime.date(2026, 4, 21), "US") is True

    monkeypatch.setattr(MarketCalendar, "_get_market_now", classmethod(lambda cls, market="CN": datetime.datetime(2026, 4, 20, 9, 0)))
    assert MarketCalendar.is_trade_day("bad-date", "CN") is False


def test_market_calendar_latest_and_recent_trade_date_fallbacks(monkeypatch):
    monkeypatch.setattr(MarketCalendar, "_trade_dates", None, raising=False)
    monkeypatch.setattr(MarketCalendar, "load_trade_dates", classmethod(lambda cls: {"2026-04-17", "2026-04-20"}))

    assert MarketCalendar.get_latest_trade_date("CN", datetime.date(2026, 4, 20)) == datetime.date(2026, 4, 20)

    monkeypatch.setattr(MarketCalendar, "_trade_dates", set(), raising=False)
    monkeypatch.setattr(MarketCalendar, "load_trade_dates", classmethod(lambda cls: None))
    assert MarketCalendar.get_latest_trade_date("CN", datetime.date(2026, 4, 19)) == datetime.date(2026, 4, 17)

    monkeypatch.setattr(MarketCalendar, "_trade_dates", None, raising=False)
    monkeypatch.setattr(MarketCalendar, "load_trade_dates", classmethod(lambda cls: None))
    monkeypatch.setattr(MarketCalendar, "_get_market_now", classmethod(lambda cls, market="CN": datetime.datetime(2026, 4, 20, 9, 0)))

    assert MarketCalendar.get_recent_trade_dates(2, ref_date="bad-date") == ["20260420", "20260417"]


def test_market_calendar_status_branches(monkeypatch):
    monkeypatch.setattr(MarketCalendar, "is_trade_day", classmethod(lambda cls, day, market="CN": False))
    monkeypatch.setattr(MarketCalendar, "_get_market_now", classmethod(lambda cls, market="CN": datetime.datetime(2026, 4, 20, 9, 30)))
    assert MarketCalendar.get_market_status("CN") == "\u4f11\u5e02"

    monkeypatch.setattr(MarketCalendar, "is_trade_day", classmethod(lambda cls, day, market="CN": True))
    monkeypatch.setattr(MarketCalendar, "_get_market_now", classmethod(lambda cls, market="CN": datetime.datetime(2026, 4, 20, 9, 20)))
    assert MarketCalendar.get_market_status("CN") == MarketCalendar._MARKET_PHASES["CN"][0][2]

    monkeypatch.setattr(MarketCalendar, "_get_market_now", classmethod(lambda cls, market="CN": datetime.datetime(2026, 4, 20, 8, 59)))
    assert MarketCalendar.get_market_status("CN") == "\u76d8\u524d"

    monkeypatch.setitem(MarketCalendar._MARKET_PHASES, "CN", None)
    monkeypatch.setattr(MarketCalendar, "_get_market_now", classmethod(lambda cls, market="CN": datetime.datetime(2026, 4, 20, 12, 0)))
    assert MarketCalendar.get_market_status("CN") == "\u5348\u4f11"
