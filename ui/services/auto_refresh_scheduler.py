# -*- coding: utf-8 -*-
from __future__ import annotations

import datetime
import threading
from dataclasses import dataclass
from functools import partial
from typing import TYPE_CHECKING

from PyQt6.QtCore import QObject, QTimer

from app.services.ui_config_service import app_config
from app.services.ui_event_service import domain_events as event_bus
from app.services.ui_market_calendar_service import MarketCalendar
from app.services.ui_task_lifecycle_service import TaskLifecycleGroup, invoke_with_cancellation
from app.services.ui_task_service import background_job_runner as task_manager
from app.services.ui_task_service import task_registry
from core.logger import get_logger

if TYPE_CHECKING:
    from ui.services.auto_refresh_tasks import AutoRefreshTaskService

log = get_logger(__name__)


@dataclass(frozen=True)
class AutoRefreshJob:
    key: str
    trigger_hour: int
    trigger_minute: int
    require_trade_day: bool


def _job_timeout_seconds(scheduler, state_key: str, status_job_key: str = "") -> float:
    for key in (str(state_key or ""), str(status_job_key or "")):
        if key in scheduler.JOB_TIMEOUT_SECONDS:
            return scheduler.JOB_TIMEOUT_SECONDS[key]
        if key.startswith("earnings_routine"):
            return scheduler.JOB_TIMEOUT_SECONDS["earnings_routine"]
    return scheduler.DEFAULT_JOB_TIMEOUT_SECONDS


def _result_status(result) -> tuple[str, str]:
    if not isinstance(result, dict):
        return "", ""
    return (
        str(result.get("status") or "").strip(),
        str(result.get("error") or result.get("message") or "").strip(),
    )


def _handle_retryable_result(scheduler, state_key, status_job_key, trade_date, started_at, status, error) -> None:
    result_error = error or status
    scheduler._set_error(state_key, result_error)
    scheduler._emit_status(
        status_job_key,
        status,
        trade_date,
        message=status,
        started_at=started_at.isoformat(timespec="seconds"),
        finished_at=scheduler._clock().isoformat(timespec="seconds"),
        error=result_error,
    )


def _handle_job_success(
    scheduler,
    result,
    *,
    state_key,
    status_job_key,
    trade_date,
    started_at,
    mark_success_date,
    skipped_is_success,
) -> None:
    if scheduler._is_shutdown:
        return
    scheduler._running_jobs.discard(state_key)
    status, result_error = _result_status(result)
    if status in scheduler.RETRYABLE_RESULT_STATUSES:
        _handle_retryable_result(
            scheduler,
            state_key,
            status_job_key,
            trade_date,
            started_at,
            status,
            result_error or scheduler._success_message(status_job_key, result),
        )
        return
    if mark_success_date and (skipped_is_success or status != "skipped"):
        scheduler._set_last_success_date(state_key, trade_date)
    scheduler._emit_status(
        status_job_key,
        "skipped" if status == "skipped" and not skipped_is_success else "success",
        trade_date,
        message=scheduler._success_message(status_job_key, result),
        started_at=started_at.isoformat(timespec="seconds"),
        finished_at=scheduler._clock().isoformat(timespec="seconds"),
    )
    if status != "skipped":
        scheduler._emit_data_event(status_job_key)


def _handle_job_error(scheduler, error_message, *, state_key, status_job_key, trade_date, started_at) -> None:
    if scheduler._is_shutdown:
        return
    scheduler._running_jobs.discard(state_key)
    scheduler._set_error(state_key, error_message)
    scheduler._emit_status(
        status_job_key,
        "failed",
        trade_date,
        message="failed",
        started_at=started_at.isoformat(timespec="seconds"),
        finished_at=scheduler._clock().isoformat(timespec="seconds"),
        error=str(error_message or "").strip(),
    )


class AutoRefreshScheduler(QObject):
    CHECK_INTERVAL_MS = 30_000
    RETRY_BACKOFF_SECONDS = 5 * 60
    RETRYABLE_RESULT_STATUSES = {"failed", "degraded"}
    EARNINGS_ROUTINE_TIMES = ((8, 30), (12, 0), (17, 0), (19, 0), (21, 0), (23, 0))
    SHUTDOWN_WAIT_TIMEOUT_MS = 1000
    DEFAULT_JOB_TIMEOUT_SECONDS = 5 * 60.0
    JOB_TIMEOUT_SECONDS = {
        "lhb_daily": 5 * 60.0,
        "foreign_block_daily": 10 * 60.0,
        "fund_holdings_daily": 15 * 60.0,
        "na_daily_incremental": 5 * 60.0,
        "na_daily_full_0925": 10 * 60.0,
        "asian_market_runtime": 2 * 60.0,
        "asian_market_cache_sync": 10 * 60.0,
        "earnings_startup_gap_fill": 15 * 60.0,
        "earnings_routine": 15 * 60.0,
    }

    DAILY_JOBS = (
        AutoRefreshJob("lhb_daily", 20, 0, True),
        AutoRefreshJob("foreign_block_daily", 20, 0, True),
        AutoRefreshJob("fund_holdings_daily", 20, 30, False),
    )

    def __init__(
        self,
        *,
        data_provider=None,
        engine=None,
        na_daily_service=None,
        asian_market_service=None,
        earnings_service=None,
        task_service: AutoRefreshTaskService | None = None,
        job_runner=None,
        clock=None,
        parent=None,
    ):
        super().__init__(parent)
        self._settings = app_config.section("auto_refresh_scheduler")
        self._task_service = task_service
        self._task_service_kwargs = {
            "data_provider": data_provider,
            "engine": engine,
            "na_daily_service": na_daily_service,
            "asian_market_service": asian_market_service,
            "earnings_service": earnings_service,
        }
        self._task_service_lock = threading.Lock()
        self._job_runner = job_runner or task_manager
        self._task_lifecycle = TaskLifecycleGroup(self._job_runner)
        self._clock = clock or (lambda: MarketCalendar.now("CN"))
        self._running_jobs: set[str] = set()
        self._is_shutdown = False
        self.extended_jobs_enabled = True
        self._timer = QTimer(self)
        self._timer.setObjectName("autoRefreshSchedulerTimer")
        self._timer.timeout.connect(self.tick)

    @property
    def timer(self) -> QTimer:
        return self._timer

    def start(self) -> None:
        self._is_shutdown = False
        if not self._timer.isActive():
            self._timer.start(self.CHECK_INTERVAL_MS)
        self.tick()

    def stop(self) -> None:
        self._timer.stop()

    def shutdown(self) -> None:
        self.stop()
        self._is_shutdown = True
        self._task_lifecycle.shutdown(timeout_ms=self.SHUTDOWN_WAIT_TIMEOUT_MS)
        self._running_jobs.clear()

    def tick(self) -> None:
        now = self._clock()
        for job in self.DAILY_JOBS:
            self._maybe_submit_daily_job(job, now)
        if not self.extended_jobs_enabled:
            return
        self._maybe_submit_na_daily_incremental(now)
        self._maybe_submit_na_daily_full(now)
        self._maybe_submit_asian_market_runtime(now)
        self._maybe_submit_asian_cache_sync(now)
        self._maybe_submit_earnings_startup_gap_fill(now)
        self._maybe_submit_earnings_routines(now)

    def _maybe_submit_daily_job(self, job: AutoRefreshJob, now: datetime.datetime) -> bool:
        trade_date = now.strftime("%Y%m%d")
        if not self._time_reached(now, job.trigger_hour, job.trigger_minute):
            return False
        if job.require_trade_day and not MarketCalendar.is_trade_day(now.date(), market="CN"):
            return False
        if self._last_success_date(job.key) == trade_date:
            return False
        if job.key in self._running_jobs or self._job_runner.is_active_task(self._task_id(job.key)):
            self._emit_status(job.key, "skipped", trade_date, message="already running")
            return False
        if self._in_retry_backoff(job.key, now):
            return False
        self._submit_daily_job(job, trade_date)
        return True

    @staticmethod
    def _time_reached(now: datetime.datetime, hour: int, minute: int) -> bool:
        return (now.hour, now.minute) >= (int(hour), int(minute))

    def _task_id(self, job_key: str) -> str:
        return task_registry.workspace(f"auto_refresh_{job_key}").task_id

    def _job_prefix(self, job_key: str) -> str:
        return str(job_key or "").strip()

    def _last_success_date(self, job_key: str) -> str:
        return str(self._settings.value(f"{self._job_prefix(job_key)}/last_success_date", "") or "").strip()

    def _set_last_success_date(self, job_key: str, trade_date: str) -> None:
        self._settings.setValue(f"{self._job_prefix(job_key)}/last_success_date", str(trade_date or "").strip())
        self._settings.setValue(f"{self._job_prefix(job_key)}/last_error", "")
        self._settings.sync()

    def _last_attempt_at(self, job_key: str) -> str:
        return str(self._settings.value(f"{self._job_prefix(job_key)}/last_attempt_at", "") or "").strip()

    def _set_attempt(self, job_key: str, now: datetime.datetime) -> None:
        self._settings.setValue(f"{self._job_prefix(job_key)}/last_attempt_at", now.isoformat(timespec="seconds"))
        self._settings.sync()

    def _set_error(self, job_key: str, error: str) -> None:
        self._settings.setValue(f"{self._job_prefix(job_key)}/last_error", str(error or "").strip())
        self._settings.sync()

    def _in_retry_backoff(self, job_key: str, now: datetime.datetime) -> bool:
        last_error = str(self._settings.value(f"{self._job_prefix(job_key)}/last_error", "") or "").strip()
        if not last_error:
            return False
        raw_attempt = self._last_attempt_at(job_key)
        if not raw_attempt:
            return False
        try:
            last_attempt = datetime.datetime.fromisoformat(raw_attempt)
        except ValueError:
            return False
        return (now - last_attempt).total_seconds() < self.RETRY_BACKOFF_SECONDS

    def _submit_daily_job(self, job: AutoRefreshJob, trade_date: str) -> None:
        def _run(*, cancellation_token=None):
            task_service = self._task_service_instance()
            if job.key == "lhb_daily":
                return invoke_with_cancellation(task_service.run_lhb_daily, cancellation_token, trade_date)
            if job.key == "foreign_block_daily":
                return invoke_with_cancellation(task_service.run_foreign_block_daily, cancellation_token, trade_date)
            if job.key == "fund_holdings_daily":
                return invoke_with_cancellation(task_service.run_fund_holdings_daily, cancellation_token, trade_date)
            raise ValueError(f"unknown auto refresh job: {job.key}")

        self._submit_job(job.key, trade_date, _run)

    def _task_service_instance(self):
        service = self._task_service
        if service is not None:
            return service
        with self._task_service_lock:
            service = self._task_service
            if service is None:
                from ui.services.auto_refresh_tasks import AutoRefreshTaskService

                service = AutoRefreshTaskService(**self._task_service_kwargs)
                self._task_service = service
        return service

    def _submit_job(
        self,
        job_key: str,
        trade_date: str,
        run_fn,
        *,
        state_key: str | None = None,
        status_job_key: str | None = None,
        mark_success_date: bool = True,
        skipped_is_success: bool = False,
    ) -> None:
        state_key = state_key or job_key
        status_job_key = status_job_key or job_key
        started_at = self._clock()
        self._running_jobs.add(state_key)
        self._set_attempt(state_key, started_at)
        self._emit_status(
            status_job_key,
            "running",
            trade_date,
            message="started",
            started_at=started_at.isoformat(timespec="seconds"),
        )

        callback_args = {
            "state_key": state_key,
            "status_job_key": status_job_key,
            "trade_date": trade_date,
            "started_at": started_at,
        }
        on_success = partial(
            _handle_job_success,
            self,
            mark_success_date=mark_success_date,
            skipped_is_success=skipped_is_success,
            **callback_args,
        )
        on_error = partial(_handle_job_error, self, **callback_args)
        self._task_lifecycle.run_background(
            state_key,
            lambda cancellation_token: invoke_with_cancellation(run_fn, cancellation_token),
            on_success=on_success,
            on_error=on_error,
            task_id=self._task_id(state_key),
            timeout_sec=_job_timeout_seconds(self, state_key, status_job_key),
        )

    def _can_submit(self, state_key: str, now: datetime.datetime) -> bool:
        if state_key in self._running_jobs or self._job_runner.is_active_task(self._task_id(state_key)):
            self._emit_status(state_key, "skipped", now.strftime("%Y%m%d"), message="already running")
            return False
        if self._in_retry_backoff(state_key, now):
            return False
        return True

    def _earnings_job_running(self) -> bool:
        return any(str(key).startswith("earnings_") for key in self._running_jobs)

    def _maybe_submit_na_daily_incremental(self, now: datetime.datetime) -> bool:
        state_key = "na_daily_incremental"
        trade_date = now.strftime("%Y%m%d")
        if not MarketCalendar.is_market_active("CN"):
            return False
        if not self._can_submit(state_key, now):
            return False
        self._submit_job(
            state_key,
            trade_date,
            lambda *, cancellation_token=None: invoke_with_cancellation(
                self._task_service_instance().run_na_daily_incremental,
                cancellation_token,
                trade_date,
            ),
            mark_success_date=False,
        )
        return True

    def _maybe_submit_na_daily_full(self, now: datetime.datetime) -> bool:
        state_key = "na_daily_full_0925"
        trade_date = now.strftime("%Y%m%d")
        if not self._time_reached(now, 9, 25):
            return False
        if not MarketCalendar.is_trade_day(now.date(), market="CN"):
            return False
        if self._last_success_date(state_key) == trade_date:
            return False
        if not self._can_submit(state_key, now):
            return False
        self._submit_job(
            state_key,
            trade_date,
            lambda *, cancellation_token=None: invoke_with_cancellation(
                self._task_service_instance().run_na_daily_full_0925,
                cancellation_token,
                trade_date,
            ),
        )
        return True

    def _maybe_submit_asian_market_runtime(self, now: datetime.datetime) -> bool:
        state_key = "asian_market_runtime"
        trade_date = now.strftime("%Y%m%d")
        if state_key in self._running_jobs or self._job_runner.is_active_task(self._task_id(state_key)):
            return False
        self._running_jobs.add(state_key)

        def _prepare(*, cancellation_token=None):
            return invoke_with_cancellation(
                self._task_service_instance().prepare_asian_market_runtime,
                cancellation_token,
            )

        def _on_success(prepared):
            if self._is_shutdown:
                return
            try:
                result = self._task_service_instance().sync_asian_market_runtime(prepared)
            except (AttributeError, OSError, RuntimeError, TypeError, ValueError) as exc:
                _on_error(str(exc))
                return
            self._running_jobs.discard(state_key)
            status = str((result or {}).get("status") or "skipped")
            if status in {"started", "stopped", "running"}:
                self._emit_status(state_key, "success", trade_date, message=status)
            self._maybe_submit_asian_cache_sync(self._clock())

        def _on_error(error_message: str):
            if self._is_shutdown:
                return
            self._running_jobs.discard(state_key)
            self._emit_status(
                state_key,
                "failed",
                trade_date,
                message="failed",
                error=str(error_message or "").strip(),
            )
            self._maybe_submit_asian_cache_sync(self._clock())

        self._task_lifecycle.run_background(
            state_key,
            lambda cancellation_token: invoke_with_cancellation(_prepare, cancellation_token),
            on_success=_on_success,
            on_error=_on_error,
            task_id=self._task_id(state_key),
            timeout_sec=_job_timeout_seconds(self, state_key),
        )
        return True

    def _maybe_submit_asian_cache_sync(self, now: datetime.datetime) -> bool:
        state_key = "asian_market_cache_sync"
        trade_date = now.strftime("%Y%m%d")
        if not self._time_reached(now, 16, 30):
            return False
        if "asian_market_runtime" in self._running_jobs:
            return False
        if self._last_success_date(state_key) == trade_date:
            return False
        if not self._can_submit(state_key, now):
            return False
        self._submit_job(
            state_key,
            trade_date,
            lambda *, cancellation_token=None: invoke_with_cancellation(
                self._task_service_instance().run_asian_market_cache_sync,
                cancellation_token,
                trade_date,
            ),
            skipped_is_success=True,
        )
        return True

    def _maybe_submit_earnings_startup_gap_fill(self, now: datetime.datetime) -> bool:
        state_key = "earnings_startup_gap_fill"
        trade_date = now.strftime("%Y%m%d")
        if self._last_success_date(state_key) == trade_date:
            return False
        if self._earnings_job_running():
            self._emit_status(state_key, "skipped", trade_date, message="earnings job already running")
            return False
        if not self._can_submit(state_key, now):
            return False
        self._submit_job(
            state_key,
            trade_date,
            lambda *, cancellation_token=None: invoke_with_cancellation(
                self._task_service_instance().run_earnings_startup_gap_fill,
                cancellation_token,
                trade_date,
            ),
        )
        return True

    def _maybe_submit_earnings_routines(self, now: datetime.datetime) -> bool:
        trade_date = now.strftime("%Y%m%d")
        due_times = [
            (hour, minute) for hour, minute in self.EARNINGS_ROUTINE_TIMES if self._time_reached(now, hour, minute)
        ]
        if not due_times:
            return False
        hour, minute = due_times[-1]
        time_key = f"{hour:02d}{minute:02d}"
        state_key = f"earnings_routine_{time_key}"
        if self._last_success_date(state_key) == trade_date:
            return False
        if self._earnings_job_running():
            self._emit_status("earnings_routine", "skipped", trade_date, message="earnings job already running")
            return False
        if not self._can_submit(state_key, now):
            return False
        routine_time = f"{hour:02d}:{minute:02d}"
        self._submit_job(
            "earnings_routine",
            trade_date,
            lambda routine_time=routine_time, *, cancellation_token=None: invoke_with_cancellation(
                self._task_service_instance().run_earnings_routine,
                cancellation_token,
                trade_date,
                routine_time=routine_time,
            ),
            state_key=state_key,
            status_job_key="earnings_routine",
        )
        return True

    @staticmethod
    def _success_message(job_key: str, result) -> str:
        if isinstance(result, dict):
            reason = str(result.get("reason") or "").strip()
            count = result.get("records")
            if count is not None:
                return f"records={count}; reason={reason}" if reason else f"records={count}"
            message = str(result.get("message") or "").strip()
            if message:
                return message
        return f"{job_key} completed"

    @staticmethod
    def _emit_data_event(job_key: str) -> None:
        if job_key == "lhb_daily":
            event_bus.sig_lhb_pool_updated.emit()
        elif job_key == "foreign_block_daily":
            event_bus.sig_block_trade_updated.emit()
        elif job_key == "fund_holdings_daily":
            event_bus.sig_fund_holdings_updated.emit()
        elif job_key in {"na_daily_incremental", "na_daily_full_0925"}:
            event_bus.sig_na_daily_updated.emit()
        elif job_key == "asian_market_cache_sync":
            event_bus.sig_asian_klines_ready.emit()
        elif job_key in {"earnings_startup_gap_fill", "earnings_routine"}:
            event_bus.sig_earnings_updated.emit()

    @staticmethod
    def _emit_status(
        job_key: str,
        status: str,
        trade_date: str,
        *,
        message: str = "",
        started_at: str = "",
        finished_at: str = "",
        error: str = "",
    ) -> None:
        event_bus.sig_auto_refresh_status_changed.emit(
            {
                "job_key": str(job_key or "").strip(),
                "status": str(status or "").strip(),
                "trade_date": str(trade_date or "").strip(),
                "message": str(message or "").strip(),
                "started_at": str(started_at or "").strip(),
                "finished_at": str(finished_at or "").strip(),
                "error": str(error or "").strip(),
            }
        )
