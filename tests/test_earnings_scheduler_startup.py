# -*- coding: utf-8 -*-
from __future__ import annotations

from datetime import date, datetime

import pandas as pd

from core.market_calendar import MarketCalendar
from earnings.scheduler import EarningsScheduler, FetchWorker


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


def test_build_startup_scan_dates_returns_recent_window_when_last_sync_is_missing(monkeypatch):
    monkeypatch.setattr(
        MarketCalendar,
        "get_recent_trade_dates",
        classmethod(lambda cls, n=20, ref_date=None: ["20260416", "20260415"]),
    )

    dates = EarningsScheduler._build_startup_scan_dates("", has_cached_records=True)

    assert dates == ["2026-04-15", "2026-04-16"]


def test_build_startup_scan_dates_returns_empty_when_window_is_already_covered(monkeypatch):
    monkeypatch.setattr(
        MarketCalendar,
        "get_recent_trade_dates",
        classmethod(lambda cls, n=20, ref_date=None: ["20260416", "20260415", "20260414"]),
    )

    dates = EarningsScheduler._build_startup_scan_dates("2026-04-16", has_cached_records=True)

    assert dates == []


def test_trigger_routine_scan_runs_once_when_idle(monkeypatch):
    scheduler = EarningsScheduler()
    calls = []
    monkeypatch.setattr(
        scheduler,
        "_run_in_background",
        lambda mode, missing_dates=None, target_date=None: calls.append((mode, missing_dates, target_date)),
    )
    try:
        assert scheduler.trigger_routine_scan(reason="test") is True
        assert calls == [("routine", None, None)]
    finally:
        scheduler.stop_patrol()


def test_trigger_routine_scan_skips_when_worker_active(monkeypatch):
    scheduler = EarningsScheduler()
    calls = []
    monkeypatch.setattr(scheduler, "_run_in_background", lambda *args, **kwargs: calls.append(args))
    scheduler.active_workers.add(object())
    try:
        assert scheduler.trigger_routine_scan(reason="test") is False
        assert calls == []
    finally:
        scheduler.stop_patrol()


def test_fetch_worker_failure_uses_failed_signal_not_empty_success(qt_application):
    class FailingEngine:
        def fetch_daily_surprises(self, *args, **kwargs):
            raise RuntimeError("source timeout")

    worker = FetchWorker(FailingEngine(), "routine")
    finished = []
    failed = []
    worker.sig_finished.connect(lambda df, mode: finished.append((df, mode)))
    worker.sig_failed.connect(lambda mode, error_text: failed.append((mode, error_text)))

    worker.run()

    assert finished == []
    assert failed == [("routine", "source timeout")]


def test_scheduler_forwards_worker_failure_without_new_data_signal(qt_application):
    scheduler = EarningsScheduler()
    successes = []
    failures = []
    scheduler.sig_new_surprises_found.connect(lambda df, mode: successes.append((df, mode)))
    scheduler.sig_fetch_failed.connect(lambda mode, error_text: failures.append((mode, error_text)))
    try:
        scheduler._on_worker_failed("gap_fill", "provider unavailable")

        assert successes == []
        assert failures == [("gap_fill", "provider unavailable")]
    finally:
        scheduler.stop_patrol()


def test_scheduler_empty_result_still_emits_new_surprises_signal(qt_application):
    scheduler = EarningsScheduler()
    successes = []
    failures = []
    scheduler.sig_new_surprises_found.connect(lambda df, mode: successes.append((df, mode)))
    scheduler.sig_fetch_failed.connect(lambda mode, error_text: failures.append((mode, error_text)))
    empty_df = pd.DataFrame()
    try:
        scheduler._on_worker_finished(empty_df, "routine")

        assert len(successes) == 1
        assert successes[0][0].empty
        assert successes[0][1] == "routine"
        assert failures == []
    finally:
        scheduler.stop_patrol()


def test_fetch_worker_warm_cache_and_routine_modes(qt_application):
    cached = pd.DataFrame({"code": ["000001"]})
    daily = pd.DataFrame({"code": ["600000"]})

    class Engine:
        def get_cached_records(self):
            return cached

        def fetch_daily_surprises(self, *args, **kwargs):
            assert args == ()
            assert kwargs == {}
            return daily

    for mode, expected in (("warm_cache", cached), ("routine", daily)):
        worker = FetchWorker(Engine(), mode)
        finished = []
        worker.sig_finished.connect(lambda df, worker_mode: finished.append((df, worker_mode)))

        worker.run()

        assert finished == [(expected, mode)]


def test_fetch_worker_gap_fill_combines_only_non_empty_days(qt_application):
    first = pd.DataFrame({"code": ["000001"]})
    third = pd.DataFrame({"code": ["600000"]})
    calls = []

    class Engine:
        def fetch_daily_surprises(self, target_publish_date=None):
            calls.append(target_publish_date)
            return {"2026-04-01": first, "2026-04-02": pd.DataFrame(), "2026-04-03": third}[target_publish_date]

    worker = FetchWorker(Engine(), "gap_fill", missing_dates=["2026-04-01", "2026-04-02", "2026-04-03"])
    finished = []
    worker.sig_finished.connect(lambda df, mode: finished.append((df, mode)))

    worker.run()

    assert calls == ["2026-04-01", "2026-04-02", "2026-04-03"]
    assert finished[0][1] == "gap_fill"
    assert list(finished[0][0]["code"]) == ["000001", "600000"]


def test_fetch_worker_empty_gap_fill_and_single_mode(qt_application):
    calls = []

    class Engine:
        def fetch_daily_surprises(self, target_publish_date=None):
            calls.append(target_publish_date)
            return pd.DataFrame({"date": [target_publish_date]}) if target_publish_date == "2026-04-08" else pd.DataFrame()

    empty_worker = FetchWorker(Engine(), "gap_fill", missing_dates=["2026-04-01"])
    single_worker = FetchWorker(Engine(), "single", target_date="2026-04-08")
    finished = []
    empty_worker.sig_finished.connect(lambda df, mode: finished.append((df, mode)))
    single_worker.sig_finished.connect(lambda df, mode: finished.append((df, mode)))

    empty_worker.run()
    single_worker.run()

    assert calls == ["2026-04-01", "2026-04-08"]
    assert finished[0][0].empty
    assert finished[0][1] == "gap_fill"
    assert list(finished[1][0]["date"]) == ["2026-04-08"]
    assert finished[1][1] == "single"


def test_normalize_trade_dates_deduplicates_and_ignores_invalid_values():
    assert EarningsScheduler._normalize_trade_dates([None, "", "20260401", "2026-04-02T15:00", "20260401", "bad"]) == [
        "2026-04-01",
        "2026-04-02",
    ]
    assert EarningsScheduler._normalize_trade_dates(None) == []


def test_run_in_background_tracks_worker_until_finished(monkeypatch, qt_application):
    created = []

    class _Signal:
        def __init__(self):
            self.callbacks = []

        def connect(self, callback):
            self.callbacks.append(callback)

    class _Worker:
        def __init__(self, engine, mode, missing_dates=None, target_date=None):
            self.engine = engine
            self.mode = mode
            self.missing_dates = missing_dates
            self.target_date = target_date
            self.sig_finished = _Signal()
            self.sig_failed = _Signal()
            self.finished = _Signal()
            self.started = False
            self.deleted = False
            created.append(self)

        def start(self):
            self.started = True

        def deleteLater(self):
            self.deleted = True

    monkeypatch.setattr("earnings.scheduler.FetchWorker", _Worker)
    scheduler = EarningsScheduler()
    try:
        scheduler._run_in_background("gap_fill", missing_dates=["2026-04-01"])

        worker = created[0]
        assert worker in scheduler.active_workers
        assert worker.started is True
        assert len(worker.sig_finished.callbacks) == 1
        assert len(worker.sig_failed.callbacks) == 1

        worker.finished.callbacks[0]()

        assert worker not in scheduler.active_workers
        assert worker.deleted is True
    finally:
        scheduler.stop_patrol()


def test_start_patrol_runs_cache_warm_and_optional_gap_fill(monkeypatch, qt_application):
    scheduler = EarningsScheduler()
    calls = []
    monkeypatch.setattr(
        scheduler,
        "_run_in_background",
        lambda mode, missing_dates=None, target_date=None: calls.append((mode, missing_dates, target_date)),
    )
    monkeypatch.setattr(EarningsScheduler, "_build_startup_scan_dates", classmethod(lambda cls, last, has_cached_records: []))
    scheduler.engine.last_sync_date = "2026-04-01"
    scheduler.engine.local_records = {"cached": True}
    try:
        scheduler.start_patrol()

        assert calls == [("warm_cache", None, None)]

        calls.clear()
        monkeypatch.setattr(
            EarningsScheduler,
            "_build_startup_scan_dates",
            classmethod(lambda cls, last, has_cached_records: ["2026-04-08"]),
        )

        scheduler.start_patrol()

        assert calls == [("warm_cache", None, None), ("gap_fill", ["2026-04-08"], None)]
    finally:
        scheduler.stop_patrol()


def test_force_manual_scan_and_check_schedule(monkeypatch, qt_application):
    scheduler = EarningsScheduler()
    calls = []
    moments = [datetime(2026, 4, 8, 8, 30), datetime(2026, 4, 8, 8, 30), datetime(2026, 4, 8, 8, 31)]

    monkeypatch.setattr(
        scheduler,
        "_run_in_background",
        lambda mode, missing_dates=None, target_date=None: calls.append((mode, missing_dates, target_date)),
    )
    monkeypatch.setattr(MarketCalendar, "now", classmethod(lambda cls, market="CN": moments.pop(0)))
    scheduler.last_check_day = date(2026, 4, 7)
    scheduler.triggered_today.add("old")
    scheduler.target_times = [(8, 30)]
    try:
        scheduler.force_manual_scan(["2026-04-01", "2026-04-08"])
        scheduler._check_schedule()
        scheduler._check_schedule()
        scheduler._check_schedule()

        assert calls == [
            ("gap_fill", ["2026-04-01", "2026-04-08"], None),
            ("routine", None, None),
        ]
        assert scheduler.triggered_today == {"8:30"}
        assert scheduler.last_check_day == date(2026, 4, 8)
    finally:
        scheduler.stop_patrol()
