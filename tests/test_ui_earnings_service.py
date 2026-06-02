# -*- coding: utf-8 -*-
from __future__ import annotations

import pandas as pd
import pytest

from app.services.ui_earnings_service import EarningsRefreshService, EarningsScheduler


class _FakeEarningsEngine:
    def __init__(self, *, cached=None, daily=None, fail=False):
        self.last_sync_date = "2026-04-14"
        self.local_records = [{"cached": True}]
        self.cached = cached if cached is not None else pd.DataFrame()
        self.daily = daily if daily is not None else pd.DataFrame()
        self.fail = fail
        self.fetch_calls = []

    def get_cached_records(self):
        return self.cached

    def fetch_daily_surprises(self, *, target_publish_date=None):
        self.fetch_calls.append(target_publish_date)
        if self.fail:
            raise RuntimeError("provider timeout")
        return self.daily.assign(target_publish_date=target_publish_date)


class _FakeJobRunner:
    def __init__(self, *, active=False):
        self.active = active
        self.calls = []

    def is_active_task(self, task_id):
        self.calls.append(("is_active", task_id))
        return self.active

    def run_in_background(self, fn, *, on_success=None, on_error=None, task_id=None):
        self.calls.append(("run", task_id))
        try:
            result = fn()
        except Exception as exc:
            if on_error is not None:
                on_error(str(exc))
        else:
            if on_success is not None:
                on_success(result)
        return str(task_id)


def test_earnings_refresh_startup_gap_fill_emits_cached_then_gap_rows(monkeypatch, qt_application):
    cached = pd.DataFrame([{"code": "000001"}])
    daily = pd.DataFrame([{"code": "000002"}])
    engine = _FakeEarningsEngine(cached=cached, daily=daily)
    monkeypatch.setattr(
        EarningsScheduler,
        "_build_startup_scan_dates",
        staticmethod(lambda last_sync_date, has_cached_records: ["2026-04-15", "2026-04-16"]),
    )
    service = EarningsRefreshService(engine=engine, job_runner=_FakeJobRunner())
    emitted = []
    service.sig_new_surprises_found.connect(lambda frame, mode: emitted.append((len(frame), mode)))

    result = service.run_startup_gap_fill()

    assert result["cached_records"] == 1
    assert result["gap_records"] == 2
    assert result["records"] == 3
    assert result["missing_dates"] == ["2026-04-15", "2026-04-16"]
    assert engine.fetch_calls == ["2026-04-15", "2026-04-16"]
    assert emitted == [(1, "warm_cache"), (2, "gap_fill")]
    assert service.active_workers == set()


def test_earnings_refresh_gap_fill_failure_emits_failure_and_clears_active(qt_application):
    service = EarningsRefreshService(engine=_FakeEarningsEngine(fail=True), job_runner=_FakeJobRunner())
    failures = []
    service.sig_fetch_failed.connect(lambda mode, error_text: failures.append((mode, error_text)))

    with pytest.raises(RuntimeError):
        service.run_gap_fill(["2026-04-16"], mode="gap_fill")

    assert failures == [("gap_fill", "provider timeout")]
    assert service.active_workers == set()


def test_earnings_refresh_background_entrypoints_skip_active_tasks(qt_application):
    runner = _FakeJobRunner(active=True)
    service = EarningsRefreshService(engine=_FakeEarningsEngine(), job_runner=runner)

    assert service.load_cached_records_async() is False
    assert service.trigger_routine_scan(reason="manual") is False
    assert [call[0] for call in runner.calls] == ["is_active", "is_active"]


def test_earnings_refresh_startup_gap_fill_handles_empty_cache_and_no_gap(monkeypatch, qt_application):
    engine = _FakeEarningsEngine(cached=None, daily=pd.DataFrame())
    engine.local_records = []
    monkeypatch.setattr(
        EarningsScheduler,
        "_build_startup_scan_dates",
        staticmethod(lambda last_sync_date, has_cached_records: []),
    )
    service = EarningsRefreshService(engine=engine, job_runner=_FakeJobRunner())
    emitted = []
    service.sig_new_surprises_found.connect(lambda frame, mode: emitted.append((len(frame), mode)))

    result = service.run_startup_gap_fill()

    assert result["records"] == 0
    assert result["missing_dates"] == []
    assert emitted == [(0, "warm_cache")]


def test_earnings_refresh_startup_gap_fill_failure_emits_and_reraises(monkeypatch, qt_application):
    class FailingEngine(_FakeEarningsEngine):
        def get_cached_records(self):
            raise RuntimeError("cache failed")

    monkeypatch.setattr(
        EarningsScheduler,
        "_build_startup_scan_dates",
        staticmethod(lambda last_sync_date, has_cached_records: []),
    )
    service = EarningsRefreshService(engine=FailingEngine(), job_runner=_FakeJobRunner())
    failures = []
    service.sig_fetch_failed.connect(lambda mode, error_text: failures.append((mode, error_text)))

    with pytest.raises(RuntimeError):
        service.run_startup_gap_fill()

    assert failures == [("startup_gap_fill", "cache failed")]
    assert service.active_workers == set()


def test_earnings_refresh_gap_fill_success_with_empty_frames(qt_application):
    service = EarningsRefreshService(engine=_FakeEarningsEngine(daily=pd.DataFrame()), job_runner=_FakeJobRunner())
    emitted = []
    service.sig_new_surprises_found.connect(lambda frame, mode: emitted.append((len(frame), mode)))

    result = service.run_gap_fill(["2026-04-16"], mode="manual_gap")

    assert result == {"job_key": "earnings_gap_fill", "records": 0, "dates": ["2026-04-16"]}
    assert emitted == [(0, "manual_gap")]


def test_earnings_refresh_routine_scan_success_and_failure(qt_application):
    service = EarningsRefreshService(
        engine=_FakeEarningsEngine(daily=pd.DataFrame([{"code": "000001"}])),
        job_runner=_FakeJobRunner(),
    )
    emitted = []
    service.sig_new_surprises_found.connect(lambda frame, mode: emitted.append((len(frame), mode)))

    assert service.run_routine_scan(reason=" manual ") == {
        "job_key": "earnings_routine",
        "records": 1,
        "reason": "manual",
    }
    assert emitted == [(1, "routine")]

    failing = EarningsRefreshService(engine=_FakeEarningsEngine(fail=True), job_runner=_FakeJobRunner())
    failures = []
    failing.sig_fetch_failed.connect(lambda mode, error_text: failures.append((mode, error_text)))
    with pytest.raises(RuntimeError):
        failing.run_routine_scan()
    assert failures == [("routine", "provider timeout")]


def test_earnings_refresh_background_entrypoints_run_and_emit(qt_application):
    runner = _FakeJobRunner(active=False)
    engine = _FakeEarningsEngine(cached=None, daily=pd.DataFrame([{"code": "000001"}]))
    service = EarningsRefreshService(engine=engine, job_runner=runner)
    emitted = []
    failures = []
    service.sig_new_surprises_found.connect(lambda frame, mode: emitted.append((len(frame), mode)))
    service.sig_fetch_failed.connect(lambda mode, error_text: failures.append((mode, error_text)))

    assert service.force_manual_scan(["2026-04-16"]) is True
    assert service.load_cached_records_async() is True
    assert service.trigger_routine_scan(reason="manual") is True
    service.stop_patrol()
    service.shutdown()

    assert [call[0] for call in runner.calls] == ["is_active", "run", "is_active", "run", "is_active", "run"]
    assert ("gap_fill", "provider timeout") not in failures
    assert ("routine", "provider timeout") not in failures
    assert ("warm_cache", "provider timeout") not in failures
    assert emitted


def test_earnings_refresh_background_entrypoints_skip_when_service_busy(qt_application):
    service = EarningsRefreshService(engine=_FakeEarningsEngine(), job_runner=_FakeJobRunner(active=False))
    service.active_workers.add(object())

    assert service.force_manual_scan(["2026-04-16"]) is False
    assert service.trigger_routine_scan(reason="manual") is False


def test_earnings_refresh_covers_none_and_background_error_callbacks(monkeypatch, qt_application):
    class NoneCacheEngine(_FakeEarningsEngine):
        def get_cached_records(self):
            return None

    class NoneDailyEngine(_FakeEarningsEngine):
        def fetch_daily_surprises(self, *, target_publish_date=None):
            self.fetch_calls.append(target_publish_date)
            return None

    monkeypatch.setattr(
        EarningsScheduler,
        "_build_startup_scan_dates",
        staticmethod(lambda last_sync_date, has_cached_records: []),
    )
    service = EarningsRefreshService(engine=NoneCacheEngine(), job_runner=_FakeJobRunner())
    assert service.run_startup_gap_fill()["records"] == 0

    routine = EarningsRefreshService(engine=NoneDailyEngine(), job_runner=_FakeJobRunner())
    assert routine.run_routine_scan()["records"] == 0

    assert EarningsRefreshService(engine=_FakeEarningsEngine(), job_runner=_FakeJobRunner(active=True)).force_manual_scan(
        ["2026-04-16"]
    ) is False

    failures = []
    manual = EarningsRefreshService(engine=_FakeEarningsEngine(fail=True), job_runner=_FakeJobRunner(active=False))
    manual.sig_fetch_failed.connect(lambda mode, error_text: failures.append((mode, error_text)))
    assert manual.force_manual_scan(["2026-04-16"]) is True

    cache_fail = EarningsRefreshService(engine=_FakeEarningsEngine(fail=True), job_runner=_FakeJobRunner(active=False))
    cache_fail.engine.get_cached_records = lambda: (_ for _ in ()).throw(RuntimeError("cache failed"))
    cache_fail.sig_fetch_failed.connect(lambda mode, error_text: failures.append((mode, error_text)))
    assert cache_fail.load_cached_records_async() is True

    routine_fail = EarningsRefreshService(engine=_FakeEarningsEngine(fail=True), job_runner=_FakeJobRunner(active=False))
    routine_fail.sig_fetch_failed.connect(lambda mode, error_text: failures.append((mode, error_text)))
    assert routine_fail.trigger_routine_scan(reason="manual") is True

    assert ("gap_fill", "provider timeout") in failures
    assert ("warm_cache", "cache failed") in failures
    assert ("routine", "provider timeout") in failures
