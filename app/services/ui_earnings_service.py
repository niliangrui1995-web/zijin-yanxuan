# -*- coding: utf-8 -*-
"""UI-facing earnings scheduler entrypoints."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from threading import Lock
from typing import TYPE_CHECKING, Protocol, runtime_checkable

from PyQt6.QtCore import QObject, pyqtSignal

from app.services.ui_event_service import domain_events as event_bus
from app.services.ui_task_lifecycle_service import TaskLifecycleGroup, invoke_with_cancellation
from app.services.ui_task_lifecycle_service import raise_if_cancelled as shared_raise_if_cancelled
from app.services.ui_task_service import background_job_runner as task_manager
from app.services.ui_task_service import task_registry
from core.logger import get_logger

if TYPE_CHECKING:
    import pandas as pd

    from domains.earnings.engine import EarningsEngine

log = get_logger(__name__)
STARTUP_BACKFILL_TRADE_DAYS = 10


@runtime_checkable
class CachedEarningsRowsPort(Protocol):
    """Optional engine capability for dataframe-free cache startup."""

    def get_cached_record_rows(self) -> Iterable[Mapping[str, object]]: ...


def _pandas_module():
    import pandas as pd

    return pd


def _empty_frame():
    return _pandas_module().DataFrame()


def _create_default_engine():
    from app.services.ui_industry_chain_service import (
        load_cached_ai_industry_chain_context_map,
        load_cached_ai_industry_chain_stock_codes,
    )
    from domains.earnings.engine import EarningsEngine

    return EarningsEngine(
        stock_universe_provider=load_cached_ai_industry_chain_stock_codes,
        stock_context_provider=load_cached_ai_industry_chain_context_map,
    )


def _normalize_trade_dates(trade_dates: list[str] | None) -> list[str]:
    from domains.market_calendar.calendar_service import normalize_trade_dates

    return normalize_trade_dates(trade_dates)


def _build_startup_scan_dates(last_sync_date: str, has_cached_records: bool) -> list[str]:
    from domains.market_calendar import MarketCalendar

    recent_trade_dates = _normalize_trade_dates(MarketCalendar.get_recent_trade_dates(STARTUP_BACKFILL_TRADE_DAYS))
    if not recent_trade_dates:
        return []
    if not has_cached_records or not last_sync_date:
        return recent_trade_dates
    return [trade_date for trade_date in recent_trade_dates if trade_date > last_sync_date]


@dataclass(frozen=True)
class _ActiveEarningsJob:
    mode: str


def _load_startup_cache(service, cancellation_token=None):
    engine = service.engine
    if isinstance(engine, CachedEarningsRowsPort):
        rows = [dict(row) for row in invoke_with_cancellation(engine.get_cached_record_rows, cancellation_token)]
        return rows, len(rows), True
    frame = invoke_with_cancellation(engine.get_cached_records, cancellation_token)
    frame = frame if frame is not None else _empty_frame()
    return frame, len(frame), False


def _load_startup_gap(service, cancellation_token=None):
    engine = service.engine
    missing_dates = service._build_startup_scan_dates(
        engine.last_sync_date,
        has_cached_records=bool(engine.local_records),
    )
    if not missing_dates:
        log.info("[业绩调度] 启动窗口已完整，无需补抓")
        return missing_dates, None, 0
    combined = service._run_gap_fill_frames(missing_dates, cancellation_token=cancellation_token)
    return missing_dates, combined, len(combined)


def _emit_startup_payloads(service, cached_payload, cached_rows_mode, combined) -> None:
    if cached_rows_mode:
        service._emit_cached_rows(cached_payload)
    else:
        service._emit_success(cached_payload, "warm_cache")
    if combined is not None:
        service._emit_success(combined, "gap_fill")


def _run_startup_gap_fill(service, cancellation_token=None) -> dict:
    service._raise_if_cancelled(cancellation_token)
    cached_payload, cached_records, cached_rows_mode = _load_startup_cache(service, cancellation_token)
    service._raise_if_cancelled(cancellation_token)
    missing_dates, combined, gap_records = _load_startup_gap(service, cancellation_token)
    service._raise_if_cancelled(cancellation_token)
    _emit_startup_payloads(service, cached_payload, cached_rows_mode, combined)
    return {
        "job_key": "earnings_startup_gap_fill",
        "records": int(cached_records + gap_records),
        "cached_records": int(cached_records),
        "gap_records": int(gap_records),
        "missing_dates": list(missing_dates or []),
    }


class EarningsRefreshService(QObject):
    sig_new_surprises_found = pyqtSignal(object, str)
    sig_fetch_failed = pyqtSignal(str, str)
    MANUAL_GAP_TIMEOUT_SECONDS = 10 * 60.0
    STARTUP_GAP_TIMEOUT_SECONDS = 10 * 60.0
    WARM_CACHE_TIMEOUT_SECONDS = 60.0
    ROUTINE_TIMEOUT_SECONDS = 10 * 60.0
    SHUTDOWN_WAIT_TIMEOUT_MS = 1000

    _normalize_trade_dates = staticmethod(_normalize_trade_dates)
    _build_startup_scan_dates = staticmethod(_build_startup_scan_dates)
    _raise_if_cancelled = staticmethod(shared_raise_if_cancelled)

    def __init__(self, parent=None, *, engine: EarningsEngine | None = None, job_runner=None):
        super().__init__(parent)
        self._engine = engine
        self._engine_lock = Lock()
        self._job_runner = job_runner or task_manager
        self._task_lifecycle = TaskLifecycleGroup(self._job_runner)
        self._shutdown = False
        self.active_workers: set[_ActiveEarningsJob] = set()

    @property
    def engine(self) -> EarningsEngine:
        if self._engine is None:
            with self._engine_lock:
                if self._engine is None:
                    self._engine = _create_default_engine()
        return self._engine

    @engine.setter
    def engine(self, value: EarningsEngine | None) -> None:
        self._engine = value

    def _with_active(self, mode: str, fn):
        job = _ActiveEarningsJob(str(mode or "unknown"))
        self.active_workers.add(job)
        try:
            return fn()
        finally:
            self.active_workers.discard(job)

    def _emit_success(self, df, mode: str) -> None:
        frame = df if df is not None else _empty_frame()
        self.sig_new_surprises_found.emit(frame, mode)
        event_bus.sig_earnings_updated.emit()

    def _emit_cached_rows(self, rows: list[dict], mode: str = "warm_cache") -> None:
        if self.receivers(self.sig_new_surprises_found) > 0:
            self.sig_new_surprises_found.emit(_pandas_module().DataFrame(rows), mode)
        event_bus.sig_earnings_updated.emit()

    def _emit_failure(self, mode: str, error) -> None:
        error_text = str(error or "unknown error")
        self.sig_fetch_failed.emit(mode, error_text)

    def _emit_failure_unless_cancelled(self, mode: str, error, cancellation_token=None) -> None:
        if cancellation_token is not None and cancellation_token.cancelled:
            return
        self._emit_failure(mode, error)

    def run_startup_gap_fill(self, *, cancellation_token=None) -> dict:
        try:
            return self._with_active(
                "startup_gap_fill",
                lambda: _run_startup_gap_fill(self, cancellation_token),
            )
        except Exception as exc:
            self._emit_failure_unless_cancelled("startup_gap_fill", exc, cancellation_token)
            raise

    def _run_gap_fill_frames(self, date_list: list[str], *, cancellation_token=None) -> pd.DataFrame:
        frames = []
        for target_date in date_list or []:
            self._raise_if_cancelled(cancellation_token)
            if cancellation_token is None:
                df = self.engine.fetch_daily_surprises(target_publish_date=target_date)
            else:
                df = invoke_with_cancellation(
                    self.engine.fetch_daily_surprises,
                    cancellation_token,
                    target_publish_date=target_date,
                )
            if df is not None and not df.empty:
                frames.append(df)
        self._raise_if_cancelled(cancellation_token)
        if frames:
            return _pandas_module().concat(frames, ignore_index=True)
        return _empty_frame()

    def run_gap_fill(self, date_list: list[str], *, mode: str = "gap_fill", cancellation_token=None) -> dict:
        def _run():
            df = self._run_gap_fill_frames(
                list(date_list or []),
                cancellation_token=cancellation_token,
            )
            self._raise_if_cancelled(cancellation_token)
            self._emit_success(df, mode)
            return {
                "job_key": "earnings_gap_fill",
                "records": int(len(df)),
                "dates": list(date_list or []),
            }

        try:
            return self._with_active(mode, _run)
        except Exception as exc:
            self._emit_failure_unless_cancelled(mode, exc, cancellation_token)
            raise

    def run_routine_scan(self, *, reason: str = "scheduled", cancellation_token=None) -> dict:
        def _run():
            self._raise_if_cancelled(cancellation_token)
            df = (
                invoke_with_cancellation(self.engine.fetch_daily_surprises, cancellation_token)
                if cancellation_token is not None
                else self.engine.fetch_daily_surprises()
            )
            if df is None:
                df = _empty_frame()
            self._raise_if_cancelled(cancellation_token)
            self._emit_success(df, "routine")
            scan_result = getattr(self.engine, "last_scan_result", {}) or {}
            status = str(scan_result.get("status") or "").strip()
            result = {
                "job_key": "earnings_routine",
                "records": int(len(df)),
                "reason": str(reason or "").strip(),
            }
            if status == "degraded":
                result["status"] = "degraded"
                result["error"] = str(scan_result.get("error") or "earnings scan degraded").strip()
            return result

        try:
            return self._with_active("routine", _run)
        except Exception as exc:
            self._emit_failure_unless_cancelled("routine", exc, cancellation_token)
            raise

    def start_patrol(self) -> bool:
        """Compatibility startup hook backed by the owner-scoped lifecycle."""
        if self.active_workers:
            return False
        task_id = task_registry.workspace("earnings_startup_gap_fill").task_id
        if self._job_runner.is_active_task(task_id):
            return False

        self._task_lifecycle.run_background(
            "startup-gap-fill",
            lambda cancellation_token: self.run_startup_gap_fill(cancellation_token=cancellation_token),
            on_error=lambda error_message: self._emit_failure("startup_gap_fill", error_message),
            task_id=task_id,
            timeout_sec=self.STARTUP_GAP_TIMEOUT_SECONDS,
        )
        return True

    def force_manual_scan(self, date_list: list[str]) -> bool:
        if self.active_workers:
            log.info("[业绩调度] 手动回补跳过：已有后台任务运行中")
            return False
        task_id = task_registry.workspace("earnings_manual_gap_fill").task_id
        if self._job_runner.is_active_task(task_id):
            return False

        def _on_error(error_message: str) -> None:
            self._emit_failure("gap_fill", error_message)

        self._task_lifecycle.run_background(
            "manual-gap-fill",
            lambda cancellation_token: self.run_gap_fill(
                date_list,
                mode="gap_fill",
                cancellation_token=cancellation_token,
            ),
            on_error=_on_error,
            task_id=task_id,
            timeout_sec=self.MANUAL_GAP_TIMEOUT_SECONDS,
        )
        return True

    def load_cached_records_async(self) -> bool:
        task_id = task_registry.workspace("earnings_view_warm_cache").task_id
        if self._job_runner.is_active_task(task_id):
            return False

        def _run(cancellation_token):
            df = invoke_with_cancellation(self.engine.get_cached_records, cancellation_token)
            return df if df is not None else _empty_frame()

        def _on_success(df) -> None:
            if self._shutdown:
                return
            self.sig_new_surprises_found.emit(df if df is not None else _empty_frame(), "warm_cache")

        def _on_error(error_message: str) -> None:
            self._emit_failure("warm_cache", error_message)

        self._task_lifecycle.run_background(
            "warm-cache",
            _run,
            on_success=_on_success,
            on_error=_on_error,
            task_id=task_id,
            timeout_sec=self.WARM_CACHE_TIMEOUT_SECONDS,
        )
        return True

    def trigger_routine_scan(self, reason: str = "manual") -> bool:
        if self.active_workers:
            log.info(f"[业绩调度] 即时巡检跳过({reason})：已有后台任务运行中")
            return False
        task_id = task_registry.workspace("earnings_manual_routine").task_id
        if self._job_runner.is_active_task(task_id):
            return False

        def _on_error(error_message: str) -> None:
            self._emit_failure("routine", error_message)

        self._task_lifecycle.run_background(
            "routine",
            lambda cancellation_token: self.run_routine_scan(
                reason=reason,
                cancellation_token=cancellation_token,
            ),
            on_error=_on_error,
            task_id=task_id,
            timeout_sec=self.ROUTINE_TIMEOUT_SECONDS,
        )
        return True

    def stop_patrol(self) -> None:
        self._task_lifecycle.cancel("startup-gap-fill", reason="patrol_stopped")
        self._task_lifecycle.cancel("routine", reason="patrol_stopped")

    def shutdown(self, *, timeout_ms: int | None = None) -> bool:
        self._shutdown = True
        return self._task_lifecycle.shutdown(
            timeout_ms=self.SHUTDOWN_WAIT_TIMEOUT_MS if timeout_ms is None else timeout_ms,
        )


EarningsScheduler = EarningsRefreshService


__all__ = ["CachedEarningsRowsPort", "EarningsScheduler", "EarningsRefreshService"]
