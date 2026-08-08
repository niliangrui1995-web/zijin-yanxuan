# -*- coding: utf-8 -*-
from __future__ import annotations

import pandas as pd
import pytest

from app.services import ui_earnings_service
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


def test_earnings_refresh_service_defers_default_engine_construction(monkeypatch, qt_application):
    sentinel = object()
    created = []
    monkeypatch.setattr(
        ui_earnings_service,
        "_create_default_engine",
        lambda: created.append("engine") or sentinel,
    )

    service = EarningsRefreshService(job_runner=_FakeJobRunner())

    assert service._engine is None
    assert created == []
    assert service.engine is sentinel
    assert service.engine is sentinel
    assert created == ["engine"]


def test_startup_cache_probe_uses_rows_without_importing_dataframe_layer(monkeypatch, qt_application):
    class RowOnlyEngine:
        last_sync_date = "2026-04-16"
        local_records = [{"股票代码": "000001"}]

        @staticmethod
        def get_cached_record_rows():
            return [{"股票代码": "000001"}]

        @staticmethod
        def get_cached_records():
            raise AssertionError("startup cache probe should use row records")

    monkeypatch.setattr(
        EarningsScheduler,
        "_build_startup_scan_dates",
        staticmethod(lambda last_sync_date, has_cached_records: []),
    )
    monkeypatch.setattr(
        ui_earnings_service,
        "_pandas_module",
        lambda: (_ for _ in ()).throw(AssertionError("pandas should stay cold without UI receivers")),
    )
    service = EarningsRefreshService(engine=RowOnlyEngine(), job_runner=_FakeJobRunner())

    result = service.run_startup_gap_fill()

    assert result["records"] == 1
    assert result["cached_records"] == 1
    assert result["gap_records"] == 0


def test_cached_earnings_rows_capability_is_structural_and_iterable():
    class RowStreamEngine:
        @staticmethod
        def get_cached_record_rows():
            yield {"stock_code": "000001"}

    engine = RowStreamEngine()

    assert isinstance(engine, ui_earnings_service.CachedEarningsRowsPort)
    assert list(engine.get_cached_record_rows()) == [{"stock_code": "000001"}]


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


def test_earnings_refresh_routine_surfaces_degraded_scan(qt_application):
    class DegradedEngine(_FakeEarningsEngine):
        def fetch_daily_surprises(self, *, target_publish_date=None):
            self.last_scan_result = {
                "status": "degraded",
                "error": "同花顺历史底稿数据源缺口：300738 奥飞数据；可重试；最后成功依据：300738=N/A",
                "retryable": True,
                "source_gaps": [
                    {
                        "source": "同花顺历史底稿",
                        "symbol": "300738",
                        "stock_name": "奥飞数据",
                        "retryable": True,
                        "last_success_basis": "N/A",
                    }
                ],
            }
            return pd.DataFrame()

    service = EarningsRefreshService(engine=DegradedEngine(), job_runner=_FakeJobRunner())
    emitted = []
    degradations = []
    service.sig_new_surprises_found.connect(lambda frame, mode: emitted.append((len(frame), mode)))
    service.sig_scan_degraded.connect(lambda scan_result, mode: degradations.append((scan_result, mode)))

    routine_result = service.run_routine_scan(reason="scheduled:08:30")

    assert routine_result["status"] == "degraded"
    assert routine_result["error"] == "同花顺历史底稿数据源缺口：300738 奥飞数据；可重试；最后成功依据：300738=N/A"
    assert routine_result["retryable"] is True
    assert routine_result["source_gaps"][0]["symbol"] == "300738"
    assert emitted == [(0, "routine")]
    assert degradations == [
        (
            {
                "status": "degraded",
                "error": "同花顺历史底稿数据源缺口：300738 奥飞数据；可重试；最后成功依据：300738=N/A",
                "retryable": True,
                "source_gaps": [
                    {
                        "source": "同花顺历史底稿",
                        "symbol": "300738",
                        "stock_name": "奥飞数据",
                        "retryable": True,
                        "last_success_basis": "N/A",
                    }
                ],
            },
            "routine",
        )
    ]


def test_earnings_refresh_gap_fill_surfaces_degraded_scan(qt_application):
    class DegradedEngine(_FakeEarningsEngine):
        def fetch_daily_surprises(self, *, target_publish_date=None):
            self.last_scan_result = {
                "status": "degraded",
                "error": "同花顺历史底稿数据源缺口：600641 先导基电；可重试；最后成功依据：600641=N/A",
                "retryable": True,
                "source_gaps": [
                    {
                        "source": "同花顺历史底稿",
                        "symbol": "600641",
                        "stock_name": "先导基电",
                        "retryable": True,
                        "last_success_basis": "N/A",
                    }
                ],
            }
            return pd.DataFrame()

    service = EarningsRefreshService(engine=DegradedEngine(), job_runner=_FakeJobRunner())
    degradations = []
    service.sig_scan_degraded.connect(lambda scan_result, mode: degradations.append((scan_result, mode)))

    result = service.run_gap_fill(["2026-04-16"], mode="gap_fill")

    assert result["status"] == "degraded"
    assert result["retryable"] is True
    assert result["source_gaps"][0]["symbol"] == "600641"
    assert degradations[0][1] == "gap_fill"


def test_earnings_refresh_startup_gap_fill_surfaces_degraded_scan(monkeypatch, qt_application):
    class DegradedEngine(_FakeEarningsEngine):
        def fetch_daily_surprises(self, *, target_publish_date=None):
            self.last_scan_result = {
                "status": "degraded",
                "error": "同花顺历史底稿数据源缺口：600641 先导基电；可重试；最后成功依据：600641=N/A",
                "retryable": True,
                "source_gaps": [
                    {
                        "source": "同花顺历史底稿",
                        "symbol": "600641",
                        "stock_name": "先导基电",
                        "retryable": True,
                        "last_success_basis": "N/A",
                    }
                ],
            }
            return pd.DataFrame()

    monkeypatch.setattr(
        EarningsScheduler,
        "_build_startup_scan_dates",
        staticmethod(lambda last_sync_date, has_cached_records: ["2026-04-16"]),
    )
    service = EarningsRefreshService(engine=DegradedEngine(), job_runner=_FakeJobRunner())
    degradations = []
    service.sig_scan_degraded.connect(lambda scan_result, mode: degradations.append((scan_result, mode)))

    result = service.run_startup_gap_fill()

    assert result["status"] == "degraded"
    assert result["retryable"] is True
    assert result["source_gaps"][0]["symbol"] == "600641"
    assert degradations[0][1] == "startup_gap_fill"


def test_earnings_refresh_cached_rows_replays_persisted_degraded_scan(qt_application):
    engine = _FakeEarningsEngine()
    engine.last_scan_result = {
        "status": "degraded",
        "retryable": True,
        "source_gaps": [
            {
                "source": "同花顺历史底稿",
                "symbol": "300738",
                "stock_name": "奥飞数据",
                "last_success_basis": "N/A",
            }
        ],
    }
    service = EarningsRefreshService(
        engine=engine,
        job_runner=_FakeJobRunner(),
        cache_rows_loader=lambda: [{"股票代码": "000001"}],
    )
    degradations = []
    service.sig_scan_degraded.connect(lambda scan_result, mode: degradations.append((scan_result, mode)))

    assert service.load_cached_records_async()

    assert degradations == [(engine.last_scan_result, "warm_cache")]


def test_earnings_refresh_background_entrypoints_run_and_emit(qt_application):
    runner = _FakeJobRunner(active=False)
    engine = _FakeEarningsEngine(cached=None, daily=pd.DataFrame([{"code": "000001"}]))
    service = EarningsRefreshService(
        engine=engine,
        job_runner=runner,
        cache_rows_loader=lambda: [{"股票代码": "000001"}],
    )
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

    cache_fail = EarningsRefreshService(
        engine=_FakeEarningsEngine(fail=True),
        job_runner=_FakeJobRunner(active=False),
        cache_rows_loader=lambda: (_ for _ in ()).throw(RuntimeError("cache failed")),
    )
    cache_fail.sig_fetch_failed.connect(lambda mode, error_text: failures.append((mode, error_text)))
    assert cache_fail.load_cached_records_async() is True

    routine_fail = EarningsRefreshService(engine=_FakeEarningsEngine(fail=True), job_runner=_FakeJobRunner(active=False))
    routine_fail.sig_fetch_failed.connect(lambda mode, error_text: failures.append((mode, error_text)))
    assert routine_fail.trigger_routine_scan(reason="manual") is True

    assert ("gap_fill", "provider timeout") in failures
    assert ("warm_cache", "cache failed") in failures
    assert ("routine", "provider timeout") in failures


def test_view_cache_replay_never_constructs_heavy_engine(monkeypatch, qt_application):
    monkeypatch.setattr(
        ui_earnings_service,
        "_create_default_engine",
        lambda: (_ for _ in ()).throw(AssertionError("view cache must not construct EarningsEngine")),
    )
    service = EarningsRefreshService(
        job_runner=_FakeJobRunner(active=False),
        cache_rows_loader=lambda: [{"股票代码": "000001", "股票名称": "平安银行"}],
    )
    emitted = []
    service.sig_new_surprises_found.connect(lambda payload, mode: emitted.append((payload, mode)))

    assert service.load_cached_records_async() is True
    assert service._engine is None
    assert emitted == [([{"股票代码": "000001", "股票名称": "平安银行"}], "warm_cache")]


def test_ai_chain_cache_reload_supersedes_an_older_view_snapshot(qt_application):
    class QueuedRunner:
        def __init__(self):
            self.jobs = []

        @staticmethod
        def is_active_task(_task_id):
            return False

        def run_in_background(self, fn, **kwargs):
            self.jobs.append((fn, kwargs.get("on_success")))
            return str(kwargs.get("task_id") or "")

    runner = QueuedRunner()
    payloads = iter(
        [
            [{"股票代码": "000001", "所属行业与概念": "旧链路"}],
            [{"股票代码": "000002", "所属行业与概念": "新链路"}],
        ]
    )
    service = EarningsRefreshService(job_runner=runner, cache_rows_loader=lambda: next(payloads))
    emitted = []
    service.sig_new_surprises_found.connect(lambda payload, _mode: emitted.append(payload))

    assert service.load_cached_records_async() is True
    old_run, old_success = runner.jobs[0]
    old_result = old_run()
    assert service.load_cached_records_async(supersede=True) is True
    old_success(old_result)
    run, on_success = runner.jobs[1]
    on_success(run())

    assert emitted == [[{"股票代码": "000002", "所属行业与概念": "新链路"}]]
