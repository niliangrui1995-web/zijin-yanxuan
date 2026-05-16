# -*- coding: utf-8 -*-
from __future__ import annotations

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
