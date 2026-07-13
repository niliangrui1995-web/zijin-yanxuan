import datetime
import subprocess
import sys
from pathlib import Path

import pandas as pd
import pytest
from PyQt6.QtTest import QSignalSpy

import app.services.earnings_refresh_process_service as earnings_process_service
import app.services.foreign_block_market_data_service as foreign_market_service
from app.services.ui_config_service import app_config
from app.services.ui_event_service import domain_events as event_bus
from infra.tasks.lifecycle import CancellationToken, TaskCancelledError
from ui.services import auto_refresh_tasks as auto_refresh_task_module
from ui.services.auto_refresh_scheduler import AutoRefreshJob, AutoRefreshScheduler
from ui.services.auto_refresh_tasks import AutoRefreshTaskService


class _ImmediateRunner:
    def __init__(self):
        self.jobs = []

    def is_active_task(self, _task_id):
        return False

    def run_in_background(self, fn, *, on_success=None, on_error=None, task_id=None, **_kwargs):
        self.jobs.append(task_id)
        try:
            result = fn()
        except Exception as exc:
            if on_error:
                on_error(str(exc))
        else:
            if on_success:
                on_success(result)
        return task_id


class _QueuedRunner:
    def __init__(self):
        self.jobs = []
        self.active = set()
        self.job_kwargs = {}
        self.lifecycle_calls = []

    def is_active_task(self, task_id):
        return task_id in self.active

    def run_in_background(self, fn, *, on_success=None, on_error=None, task_id=None, **kwargs):
        self.jobs.append((task_id, fn, on_success, on_error))
        self.active.add(task_id)
        self.job_kwargs[task_id] = dict(kwargs)
        return task_id

    def abandon_task(self, task_id, **_kwargs):
        self.lifecycle_calls.append(("abandon", task_id))
        self.active.discard(task_id)
        return True

    def cancel_task(self, task_id, *, reason="cancelled"):
        self.lifecycle_calls.append(("cancel", task_id, reason))
        return task_id in self.active

    def wait_for_tasks(self, task_ids, *, timeout_ms):
        self.lifecycle_calls.append(("wait", tuple(task_ids), timeout_ms))
        return True


class _TaskService:
    def __init__(self):
        self.calls = []
        self.fail = False
        self.asian_cache_result = {"records": 0, "status": "skipped", "message": "cache fresh"}

    def run_lhb_daily(self, trade_date):
        self.calls.append(("lhb_daily", trade_date))
        if self.fail:
            raise RuntimeError("lhb failed")
        return {"records": 3}

    def run_foreign_block_daily(self, trade_date):
        self.calls.append(("foreign_block_daily", trade_date))
        return {"records": 2}

    def run_fund_holdings_daily(self, trade_date):
        self.calls.append(("fund_holdings_daily", trade_date))
        return {"message": "ok"}

    def run_na_daily_full_0925(self, trade_date):
        self.calls.append(("na_daily_full_0925", trade_date))
        return {"records": 4}

    def run_na_daily_incremental(self, trade_date):
        self.calls.append(("na_daily_incremental", trade_date))
        return {"records": 4, "status": "success"}

    def prepare_asian_market_runtime(self):
        self.calls.append(("asian_market_runtime", "prepare"))
        return {"target_codes": ["2330.TW"]}

    def sync_asian_market_runtime(self, prepared=None):
        self.calls.append(("asian_market_runtime", "sync", tuple((prepared or {}).get("target_codes") or [])))
        return {"status": "started"}

    def run_asian_market_cache_sync(self, trade_date):
        self.calls.append(("asian_market_cache_sync", trade_date))
        return dict(self.asian_cache_result)

    def run_earnings_startup_gap_fill(self, trade_date):
        self.calls.append(("earnings_startup_gap_fill", trade_date))
        return {"records": 1}

    def run_earnings_routine(self, trade_date, *, routine_time):
        self.calls.append(("earnings_routine", trade_date, routine_time))
        return {"records": 1}


def _reset_scheduler_settings():
    app_config.remove("auto_refresh_scheduler")
    app_config.sync()


def _scheduler(now, *, runner=None, task_service=None, extended_jobs=False):
    scheduler = AutoRefreshScheduler(
        task_service=task_service or _TaskService(),
        job_runner=runner or _ImmediateRunner(),
        clock=lambda: now[0],
    )
    scheduler.extended_jobs_enabled = bool(extended_jobs)
    return scheduler


def test_auto_refresh_scheduler_skips_daily_jobs_before_trigger(monkeypatch):
    _reset_scheduler_settings()
    now = [datetime.datetime(2026, 4, 20, 19, 59)]
    tasks = _TaskService()
    scheduler = _scheduler(now, task_service=tasks)
    monkeypatch.setattr(
        "ui.services.auto_refresh_scheduler.MarketCalendar.is_trade_day",
        classmethod(lambda cls, day, market="CN": True),
    )

    scheduler.tick()

    assert tasks.calls == []


def test_auto_refresh_scheduler_triggers_2000_trade_day_jobs_and_dedupes(monkeypatch):
    _reset_scheduler_settings()
    now = [datetime.datetime(2026, 4, 20, 20, 0)]
    tasks = _TaskService()
    lhb_spy = QSignalSpy(event_bus.sig_lhb_pool_updated)
    block_spy = QSignalSpy(event_bus.sig_block_trade_updated)
    status_spy = QSignalSpy(event_bus.sig_auto_refresh_status_changed)
    scheduler = _scheduler(now, task_service=tasks)
    monkeypatch.setattr(
        "ui.services.auto_refresh_scheduler.MarketCalendar.is_trade_day",
        classmethod(lambda cls, day, market="CN": True),
    )

    scheduler.tick()
    scheduler.tick()

    assert tasks.calls == [("lhb_daily", "20260420"), ("foreign_block_daily", "20260420")]
    assert len(lhb_spy) == 1
    assert len(block_spy) == 1
    assert any(args[0]["status"] == "success" for args in status_spy)


def test_auto_refresh_scheduler_triggers_fund_holdings_after_2030(monkeypatch):
    _reset_scheduler_settings()
    now = [datetime.datetime(2026, 4, 20, 20, 30)]
    tasks = _TaskService()
    fund_spy = QSignalSpy(event_bus.sig_fund_holdings_updated)
    scheduler = _scheduler(now, task_service=tasks)
    monkeypatch.setattr(
        "ui.services.auto_refresh_scheduler.MarketCalendar.is_trade_day",
        classmethod(lambda cls, day, market="CN": True),
    )

    scheduler.tick()

    assert ("fund_holdings_daily", "20260420") in tasks.calls
    assert len(fund_spy) == 1


def test_auto_refresh_scheduler_skips_trade_day_gated_jobs_on_non_trade_day(monkeypatch):
    _reset_scheduler_settings()
    now = [datetime.datetime(2026, 4, 19, 20, 30)]
    tasks = _TaskService()
    scheduler = _scheduler(now, task_service=tasks)
    monkeypatch.setattr(
        "ui.services.auto_refresh_scheduler.MarketCalendar.is_trade_day",
        classmethod(lambda cls, day, market="CN": False),
    )

    scheduler.tick()

    assert tasks.calls == [("fund_holdings_daily", "20260419")]


def test_auto_refresh_scheduler_does_not_resubmit_running_job(monkeypatch):
    _reset_scheduler_settings()
    now = [datetime.datetime(2026, 4, 20, 20, 10)]
    runner = _QueuedRunner()
    tasks = _TaskService()
    scheduler = _scheduler(now, runner=runner, task_service=tasks)
    scheduler.DAILY_JOBS = (AutoRefreshJob("lhb_daily", 20, 0, True),)
    monkeypatch.setattr(
        "ui.services.auto_refresh_scheduler.MarketCalendar.is_trade_day",
        classmethod(lambda cls, day, market="CN": True),
    )

    scheduler.tick()
    scheduler.tick()

    lhb_jobs = [job for job in runner.jobs if job[0] == "auto_refresh_lhb_daily"]
    assert len(lhb_jobs) == 1


def test_auto_refresh_scheduler_owns_token_deadline_and_cancels_on_shutdown():
    _reset_scheduler_settings()
    now = [datetime.datetime(2026, 4, 20, 20, 0)]
    runner = _QueuedRunner()
    scheduler = _scheduler(now, runner=runner, task_service=_TaskService())

    scheduler._submit_job(
        "lhb_daily",
        "20260420",
        lambda *, cancellation_token=None: cancellation_token.raise_if_cancelled() or {"records": 1},
    )

    task_id = "auto_refresh_lhb_daily"
    token = runner.job_kwargs[task_id]["cancellation_token"]
    assert runner.job_kwargs[task_id]["timeout_sec"] == scheduler.JOB_TIMEOUT_SECONDS["lhb_daily"]
    assert token.cancelled is False

    scheduler.shutdown()

    assert token.cancelled is True
    assert token.reason == "owner_shutdown"
    assert ("cancel", task_id, "owner_shutdown") in runner.lifecycle_calls
    assert any(call[0] == "wait" and call[2] == scheduler.SHUTDOWN_WAIT_TIMEOUT_MS for call in runner.lifecycle_calls)
    _, run_fn, on_success, _ = runner.jobs[-1]
    with pytest.raises(TaskCancelledError, match="owner_shutdown"):
        run_fn()
    assert task_id in runner.active
    assert on_success is not None


def test_foreign_block_fetch_stops_after_provider_stage_cancels(monkeypatch):
    token = CancellationToken()
    calls = []

    def fetch_calendar(**_kwargs):
        calls.append("calendar")
        token.cancel("provider_cancelled")
        return ["2026-04-20"]

    monkeypatch.setattr(foreign_market_service, "fetch_trade_calendar", fetch_calendar)

    with pytest.raises(TaskCancelledError, match="provider_cancelled"):
        foreign_market_service.fetch_foreign_block_records(
            days_to_fetch=30,
            cancellation_token=token,
        )

    assert calls == ["calendar"]


def test_auto_refresh_scheduler_catches_up_when_started_after_2000(monkeypatch):
    _reset_scheduler_settings()
    now = [datetime.datetime(2026, 4, 20, 20, 10)]
    tasks = _TaskService()
    scheduler = _scheduler(now, task_service=tasks)
    scheduler.DAILY_JOBS = (AutoRefreshJob("lhb_daily", 20, 0, True),)
    monkeypatch.setattr(
        "ui.services.auto_refresh_scheduler.MarketCalendar.is_trade_day",
        classmethod(lambda cls, day, market="CN": True),
    )

    scheduler.tick()

    assert tasks.calls == [("lhb_daily", "20260420")]


def test_auto_refresh_scheduler_failed_job_uses_retry_backoff(monkeypatch):
    _reset_scheduler_settings()
    now = [datetime.datetime(2026, 4, 20, 20, 10)]
    tasks = _TaskService()
    tasks.fail = True
    status_spy = QSignalSpy(event_bus.sig_auto_refresh_status_changed)
    scheduler = _scheduler(now, task_service=tasks)
    scheduler.DAILY_JOBS = (AutoRefreshJob("lhb_daily", 20, 0, True),)
    monkeypatch.setattr(
        "ui.services.auto_refresh_scheduler.MarketCalendar.is_trade_day",
        classmethod(lambda cls, day, market="CN": True),
    )

    scheduler.tick()
    scheduler.tick()
    now[0] = datetime.datetime(2026, 4, 20, 20, 16)
    scheduler.tick()

    assert tasks.calls == [
        ("lhb_daily", "20260420"),
        ("lhb_daily", "20260420"),
    ]
    assert any(args[0]["status"] == "failed" and args[0]["error"] == "lhb failed" for args in status_spy)


def test_auto_refresh_scheduler_uses_30_second_global_timer():
    assert AutoRefreshScheduler.CHECK_INTERVAL_MS == 30_000


def test_auto_refresh_service_shell_imports_stay_lightweight():
    project_root = Path(__file__).resolve().parents[1]
    command = r"""
import sys

import ui.services.auto_refresh_scheduler

if "ui.services.auto_refresh_tasks" in sys.modules:
    raise SystemExit("scheduler imported auto_refresh_tasks eagerly")

import ui.services.auto_refresh_tasks

blocked_prefixes = (
    "akshare",
    "app.services.asian_market_service",
    "app.services.ui_fund_holdings_service",
    "core.lhb_pool_manager",
    "domains.fund_holdings.store",
    "numpy",
    "openpyxl",
    "pandas",
    "ui.services.asian_market_runtime_service",
    "ui.services.earnings_refresh_service",
    "ui.tabs.asian_market_workers",
    "vcp.fetchers.asian_kline_fetcher",
    "yfinance",
)
loaded = sorted(
    name
    for name in sys.modules
    if any(name == prefix or name.startswith(f"{prefix}.") for prefix in blocked_prefixes)
)
if loaded:
    raise SystemExit("unexpected heavy imports: " + ", ".join(loaded))
"""

    completed = subprocess.run(
        [sys.executable, "-c", command],
        cwd=project_root,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr or completed.stdout


def test_auto_refresh_asian_prepare_warms_worker_before_gui_callback():
    project_root = Path(__file__).resolve().parents[1]
    command = r"""
import datetime
import sys

from PyQt6.QtCore import QCoreApplication

from ui.services import asian_market_runtime_service as runtime_service
from ui.services.auto_refresh_scheduler import AutoRefreshScheduler
from ui.services.auto_refresh_tasks import AutoRefreshTaskService

worker_module_name = "ui.tabs.asian_market_workers"
if worker_module_name in sys.modules:
    raise SystemExit("worker module was imported before prepare")

runtime_service.filter_asian_tickers = lambda: {"TSMC": "2330.TW"}


class ProbeService:
    def __init__(self):
        self._worker = None
        self._codes = []
        self.worker_module_before_sync = None

    def set_target_codes(self, codes):
        self._codes = list(codes or [])

    def sync_runtime_state(self):
        self.worker_module_before_sync = sys.modules.get(worker_module_name)
        if self.worker_module_before_sync is None:
            raise AssertionError("GUI callback cold-imported worker module")
        runtime_service.is_asian_quote_refresh_time(self._codes)
        if sys.modules.get(worker_module_name) is not self.worker_module_before_sync:
            raise AssertionError("GUI callback replaced worker module")
        return "running"


class QueuedRunner:
    def __init__(self):
        self.jobs = []

    def is_active_task(self, _task_id):
        return False

    def run_in_background(self, fn, *, on_success=None, on_error=None, task_id=None):
        self.jobs.append((task_id, fn, on_success, on_error))
        return task_id


app = QCoreApplication.instance() or QCoreApplication([])
probe = ProbeService()
runner = QueuedRunner()
task_service = AutoRefreshTaskService(asian_market_service=probe)
scheduler = AutoRefreshScheduler(
    task_service=task_service,
    job_runner=runner,
    clock=lambda: datetime.datetime(2026, 4, 20, 1, 0),
)
scheduler._maybe_submit_asian_market_runtime(datetime.datetime(2026, 4, 20, 1, 0))
_, run_fn, on_success, _ = runner.jobs[0]
prepared = run_fn()
prepared_worker_module = sys.modules.get(worker_module_name)
if prepared_worker_module is None:
    raise SystemExit("prepare did not warm worker module")
on_success(prepared)
if probe.worker_module_before_sync is not prepared_worker_module:
    raise SystemExit("GUI callback did not reuse warmed worker module")
"""

    completed = subprocess.run(
        [sys.executable, "-c", command],
        cwd=project_root,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr or completed.stdout


def test_auto_refresh_scheduler_queues_asian_runtime_before_touching_service(monkeypatch):
    _reset_scheduler_settings()
    now = [datetime.datetime(2026, 4, 20, 1, 0)]
    runner = _QueuedRunner()
    tasks = _TaskService()
    status_spy = QSignalSpy(event_bus.sig_auto_refresh_status_changed)
    scheduler = _scheduler(now, runner=runner, task_service=tasks, extended_jobs=True)
    scheduler.DAILY_JOBS = ()
    monkeypatch.setattr(
        "ui.services.auto_refresh_scheduler.MarketCalendar.is_market_active",
        classmethod(lambda cls, market="CN": False),
    )

    scheduler.tick()
    scheduler.tick()

    assert tasks.calls == []
    asian_jobs = [job for job in runner.jobs if job[0] == "auto_refresh_asian_market_runtime"]
    assert len(asian_jobs) == 1

    _, run_fn, on_success, _on_error = asian_jobs[0]
    prepared = run_fn()
    assert tasks.calls == [("asian_market_runtime", "prepare")]

    on_success(prepared)
    assert tasks.calls[-1] == ("asian_market_runtime", "sync", ("2330.TW",))
    assert any(
        args[0]["job_key"] == "asian_market_runtime"
        and args[0]["status"] == "success"
        and args[0]["message"] == "started"
        for args in status_spy
    )


def test_auto_refresh_scheduler_triggers_na_daily_full_after_0925(monkeypatch):
    _reset_scheduler_settings()
    now = [datetime.datetime(2026, 4, 20, 9, 24)]
    tasks = _TaskService()
    scheduler = _scheduler(now, task_service=tasks, extended_jobs=True)
    scheduler.DAILY_JOBS = ()
    monkeypatch.setattr(
        "ui.services.auto_refresh_scheduler.MarketCalendar.is_trade_day",
        classmethod(lambda cls, day, market="CN": True),
    )
    monkeypatch.setattr(
        "ui.services.auto_refresh_scheduler.MarketCalendar.is_market_active",
        classmethod(lambda cls, market="CN": False),
    )

    scheduler.tick()
    now[0] = datetime.datetime(2026, 4, 20, 9, 25)
    scheduler.tick()
    scheduler.tick()

    assert ("na_daily_full_0925", "20260420") in tasks.calls
    assert [call[0] for call in tasks.calls].count("na_daily_full_0925") == 1


def test_auto_refresh_scheduler_runs_na_incremental_only_when_market_active(monkeypatch):
    _reset_scheduler_settings()
    now = [datetime.datetime(2026, 4, 20, 10, 0)]
    tasks = _TaskService()
    scheduler = _scheduler(now, task_service=tasks, extended_jobs=True)
    scheduler.DAILY_JOBS = ()
    market_active = [False]
    monkeypatch.setattr(
        "ui.services.auto_refresh_scheduler.MarketCalendar.is_market_active",
        classmethod(lambda cls, market="CN": market_active[0]),
    )
    monkeypatch.setattr(
        "ui.services.auto_refresh_scheduler.MarketCalendar.is_trade_day",
        classmethod(lambda cls, day, market="CN": True),
    )

    scheduler.tick()
    market_active[0] = True
    scheduler.tick()

    assert ("na_daily_incremental", "20260420") in tasks.calls


def test_auto_refresh_scheduler_triggers_asian_cache_sync_after_close(monkeypatch):
    _reset_scheduler_settings()
    now = [datetime.datetime(2026, 4, 20, 16, 29)]
    tasks = _TaskService()
    scheduler = _scheduler(now, task_service=tasks, extended_jobs=True)
    scheduler.DAILY_JOBS = ()
    monkeypatch.setattr(
        "ui.services.auto_refresh_scheduler.MarketCalendar.is_market_active",
        classmethod(lambda cls, market="CN": False),
    )
    monkeypatch.setattr(
        "ui.services.auto_refresh_scheduler.MarketCalendar.is_trade_day",
        classmethod(lambda cls, day, market="CN": True),
    )

    scheduler.tick()
    now[0] = datetime.datetime(2026, 4, 20, 16, 30)
    scheduler.tick()
    scheduler.tick()

    assert ("asian_market_cache_sync", "20260420") in tasks.calls
    assert [call[0] for call in tasks.calls].count("asian_market_cache_sync") == 1


def test_auto_refresh_scheduler_degraded_asian_cache_uses_retry_backoff(monkeypatch):
    _reset_scheduler_settings()
    now = [datetime.datetime(2026, 4, 20, 16, 30)]
    tasks = _TaskService()
    tasks.asian_cache_result = {
        "status": "degraded",
        "message": "亚洲 K 线缓存同步失败，仍缺失 1 只(2308.TW)，未覆盖现有缓存",
        "error": "亚洲 K 线缓存同步失败，仍缺失 1 只(2308.TW)，未覆盖现有缓存",
    }
    status_spy = QSignalSpy(event_bus.sig_auto_refresh_status_changed)
    scheduler = _scheduler(now, task_service=tasks, extended_jobs=True)
    scheduler.DAILY_JOBS = ()
    monkeypatch.setattr(
        "ui.services.auto_refresh_scheduler.MarketCalendar.is_market_active",
        classmethod(lambda cls, market="CN": False),
    )
    monkeypatch.setattr(
        "ui.services.auto_refresh_scheduler.MarketCalendar.is_trade_day",
        classmethod(lambda cls, day, market="CN": True),
    )

    scheduler.tick()
    scheduler.tick()
    now[0] = datetime.datetime(2026, 4, 20, 16, 36)
    scheduler.tick()

    assert [call[0] for call in tasks.calls].count("asian_market_cache_sync") == 2
    assert any(
        args[0]["job_key"] == "asian_market_cache_sync"
        and args[0]["status"] == "degraded"
        and "2308.TW" in args[0]["error"]
        for args in status_spy
    )


def test_auto_refresh_scheduler_runs_latest_earnings_routine_once(monkeypatch):
    _reset_scheduler_settings()
    now = [datetime.datetime(2026, 4, 20, 8, 29)]
    tasks = _TaskService()
    scheduler = _scheduler(now, task_service=tasks, extended_jobs=True)
    scheduler.DAILY_JOBS = ()
    monkeypatch.setattr(
        "ui.services.auto_refresh_scheduler.MarketCalendar.is_market_active",
        classmethod(lambda cls, market="CN": False),
    )
    monkeypatch.setattr(
        "ui.services.auto_refresh_scheduler.MarketCalendar.is_trade_day",
        classmethod(lambda cls, day, market="CN": True),
    )

    scheduler.tick()
    now[0] = datetime.datetime(2026, 4, 20, 8, 30)
    scheduler.tick()
    scheduler.tick()

    assert ("earnings_routine", "20260420", "08:30") in tasks.calls
    assert [call[0] for call in tasks.calls].count("earnings_routine") == 1


def test_auto_refresh_task_service_runs_fund_holdings_sync(monkeypatch):
    calls = []
    monkeypatch.setattr(
        "app.services.ui_fund_holdings_service.fund_holdings_sync_service.sync_latest_all",
        lambda: calls.append("sync") or {"message": "done"},
    )

    result = AutoRefreshTaskService().run_fund_holdings_daily("20260420")

    assert calls == ["sync"]
    assert result["trade_date"] == "20260420"
    assert result["message"] == "done"


def test_auto_refresh_task_service_runs_earnings_refresh_in_subprocess(monkeypatch):
    calls = []

    def fake_refresh(mode, *, routine_time=""):
        calls.append((mode, routine_time))
        return {"status": "success", "job_key": f"earnings_{mode}", "records": 3}

    monkeypatch.setattr(earnings_process_service, "run_earnings_refresh", fake_refresh)
    service = AutoRefreshTaskService(earnings_service=object())

    startup = service.run_earnings_startup_gap_fill("20260420")
    routine = service.run_earnings_routine("20260420", routine_time="08:30")

    assert calls == [("startup-gap-fill", ""), ("routine", "08:30")]
    assert startup["trade_date"] == "20260420"
    assert routine["trade_date"] == "20260420"
    assert routine["routine_time"] == "08:30"


def test_earnings_refresh_subprocess_uses_hidden_module_runner(monkeypatch):
    calls = []

    def fake_run(module_name, module_args=None, **kwargs):
        calls.append((module_name, list(module_args or []), kwargs))
        return subprocess.CompletedProcess(
            args=[],
            returncode=0,
            stdout='worker log\n{"status":"success","job_key":"earnings_routine","records":2}\n',
            stderr="",
        )

    monkeypatch.setattr(earnings_process_service, "run_python_module", fake_run)

    result = earnings_process_service.run_earnings_refresh("routine", routine_time="08:30")

    assert result == {"status": "success", "job_key": "earnings_routine", "records": 2}
    assert calls[0][0] == "domains.earnings.refresh_cache"
    assert calls[0][1] == ["routine", "--routine-time", "08:30"]
    assert calls[0][2]["no_window"] is True
    assert calls[0][2]["check"] is True


def test_earnings_refresh_process_clamps_timeout_to_owner_deadline(monkeypatch):
    calls = []

    class DeadlineToken:
        checks = 0

        def raise_if_cancelled(self):
            self.checks += 1

        @staticmethod
        def remaining_seconds():
            return 2.5

    def fake_run(_module_name, _module_args=None, **kwargs):
        calls.append(kwargs)
        return subprocess.CompletedProcess(
            args=[],
            returncode=0,
            stdout='{"status":"success","job_key":"earnings_routine","records":0}\n',
            stderr="",
        )

    token = DeadlineToken()
    monkeypatch.setattr(earnings_process_service, "run_python_module", fake_run)

    result = earnings_process_service.run_earnings_refresh(
        "routine",
        routine_time="08:30",
        cancellation_token=token,
    )

    assert result["status"] == "success"
    assert calls[0]["timeout"] == pytest.approx(2.5)
    assert token.checks >= 3


def test_earnings_refresh_process_rechecks_cancellation_after_worker_returns(monkeypatch):
    token = CancellationToken()

    def fake_run(_module_name, _module_args=None, **_kwargs):
        token.cancel("owner_shutdown")
        return subprocess.CompletedProcess(
            args=[],
            returncode=0,
            stdout='{"status":"success","job_key":"earnings_routine","records":0}\n',
            stderr="",
        )

    monkeypatch.setattr(earnings_process_service, "run_python_module", fake_run)

    with pytest.raises(TaskCancelledError, match="owner_shutdown"):
        earnings_process_service.run_earnings_refresh("routine", cancellation_token=token)


def test_auto_refresh_task_service_writes_lhb_pool_cache(monkeypatch):
    calls = []

    class FakePoolManager:
        last_auto_fetch_date = ""

        def add_day(self, date_text, records):
            calls.append(("add_day", date_text, records))

        def prune(self, trade_dates):
            calls.append(("prune", tuple(trade_dates)))

        def save(self):
            calls.append(("save",))

        def get_cached_dates(self):
            return ["20260420"]

    monkeypatch.setattr(
        "app.services.lhb_market_data_service.fetch_lhb_pool_for_date",
        lambda date_text, emit_success_log=False, return_meta=True, cancellation_token=None: {
            "records": [{"code": "300308"}, {"code": "600000"}],
            "status": "ok",
        },
    )
    monkeypatch.setattr("core.ai_industry_chain_pool.load_cached_ai_industry_chain_stock_codes", lambda: {"300308"})
    monkeypatch.setattr("core.lhb_pool_manager.LhbPoolManager", FakePoolManager)
    monkeypatch.setattr(
        "app.services.ui_market_calendar_service.MarketCalendar.get_recent_trade_dates",
        classmethod(lambda cls, n, ref_date=None: ["20260420"]),
    )

    result = AutoRefreshTaskService().run_lhb_daily("20260420")

    assert calls == [
        ("add_day", "20260420", [{"code": "300308"}]),
        ("prune", ("20260420",)),
        ("save",),
    ]
    assert result["records"] == 1
    assert result["cached_trade_days"] == 1


def test_auto_refresh_task_service_writes_foreign_block_cache(monkeypatch):
    saved = []
    monkeypatch.setattr(
        "ui.services.auto_refresh_tasks._fetch_foreign_block_records",
        lambda *, days_to_fetch: {"records": [{"raw": 1}], "timeout_chunks": [], "failed_chunks": []},
    )
    monkeypatch.setattr(
        "ui.services.auto_refresh_tasks._build_foreign_block_rows",
        lambda records: [{"代码": "300750", "交易日期": "20260420"}],
    )
    monkeypatch.setattr(
        "ui.services.auto_refresh_tasks._latest_foreign_block_trade_date",
        lambda rows: "20260420",
    )
    monkeypatch.setattr(
        "ui.services.auto_refresh_tasks._save_foreign_block_cache",
        lambda rows, *, days_to_fetch, latest_trade_date: saved.append((rows, days_to_fetch, latest_trade_date)),
    )

    result = AutoRefreshTaskService().run_foreign_block_daily("20260420")

    assert saved == [([{"代码": "300750", "交易日期": "20260420"}], 30, "20260420")]
    assert result["records"] == 1
    assert result["latest_trade_date"] == "20260420"


def test_auto_refresh_task_service_saves_partial_foreign_block_cache(monkeypatch):
    saved = []
    monkeypatch.setattr(
        "ui.services.auto_refresh_tasks._fetch_foreign_block_records",
        lambda *, days_to_fetch: {
            "records": [{"raw": 1}],
            "timeout_chunks": ["20260428-20260513"],
            "failed_chunks": [],
        },
    )
    monkeypatch.setattr(
        "ui.services.auto_refresh_tasks._build_foreign_block_rows",
        lambda records: [{"代码": "300750", "交易日期": "20260611"}],
    )
    monkeypatch.setattr(
        "ui.services.auto_refresh_tasks._latest_foreign_block_trade_date",
        lambda rows: "20260611",
    )
    monkeypatch.setattr(
        "ui.services.auto_refresh_tasks._save_foreign_block_cache",
        lambda rows, *, days_to_fetch, latest_trade_date: saved.append((rows, days_to_fetch, latest_trade_date)),
    )

    result = AutoRefreshTaskService().run_foreign_block_daily("20260611")

    assert saved == [([{"代码": "300750", "交易日期": "20260611"}], 30, "20260611")]
    assert result["status"] == "partial"
    assert result["timeout_chunks"] == ["20260428-20260513"]
    assert result["latest_trade_date"] == "20260611"


def test_auto_refresh_foreign_block_fetches_latest_chunk_first(monkeypatch):
    calls = []

    class FixedDatetime(datetime.datetime):
        @classmethod
        def now(cls):
            return cls(2026, 6, 11, 20, 0)

    monkeypatch.setattr(foreign_market_service.datetime, "datetime", FixedDatetime)
    monkeypatch.setattr(foreign_market_service, "BLOCK_TRADE_MAX_RETRIES", 1)
    monkeypatch.setattr(
        foreign_market_service,
        "fetch_trade_calendar",
        lambda **_kwargs: pd.date_range("2026-04-01", "2026-06-11").strftime("%Y-%m-%d").tolist(),
    )

    def fetch_block_trades(start_date, end_date, **_kwargs):
        calls.append((start_date, end_date))
        return [
            {
                "交易日期": end_date,
                "买方营业部": "高盛上海营业部",
                "卖方营业部": "普通营业部",
            }
        ]

    monkeypatch.setattr(foreign_market_service, "fetch_block_trades", fetch_block_trades)

    payload = foreign_market_service.fetch_foreign_block_records(days_to_fetch=30)

    assert calls[0] == ("20260529", "20260611")
    assert payload["records"][0]["交易日期"] == "20260611"


def test_auto_refresh_foreign_block_rows_filter_to_ai_chain_pool(monkeypatch):
    monkeypatch.setattr(
        foreign_market_service,
        "_filter_rows_to_ai_chain_codes",
        lambda rows, **_kwargs: [row for row in rows if row.get("代码") == "300308"],
    )

    rows = auto_refresh_task_module._build_foreign_block_rows(
        [
            {
                "交易日期": "20260420",
                "证券代码": "300308",
                "证券简称": "中际旭创",
                "买方营业部": "高盛上海营业部",
                "卖方营业部": "普通营业部",
                "收盘价": 120,
                "成交价格": 118,
                "折溢率": -0.02,
                "成交量": 10000,
                "成交额": 1180000,
            },
            {
                "交易日期": "20260420",
                "证券代码": "600000",
                "证券简称": "浦发银行",
                "买方营业部": "高盛上海营业部",
                "卖方营业部": "普通营业部",
                "收盘价": 10,
                "成交价格": 9.8,
                "折溢率": -0.02,
                "成交量": 10000,
                "成交额": 98000,
            },
        ]
    )

    assert [row["代码"] for row in rows] == ["300308"]
    assert rows[0]["成交金额(万元)"] == "118.00"
