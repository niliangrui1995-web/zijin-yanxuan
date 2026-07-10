# -*- coding: utf-8 -*-
"""UI-facing earnings scheduler entrypoints."""

from __future__ import annotations

from dataclasses import dataclass
from threading import Lock
from typing import TYPE_CHECKING

from PyQt6.QtCore import QObject, pyqtSignal

from app.services.ui_event_service import domain_events as event_bus
from app.services.ui_task_service import background_job_runner as task_manager
from app.services.ui_task_service import task_registry
from core.logger import get_logger

if TYPE_CHECKING:
    import pandas as pd

    from domains.earnings.engine import EarningsEngine

log = get_logger(__name__)
STARTUP_BACKFILL_TRADE_DAYS = 10


def _pandas_module():
    import pandas as pd

    return pd


def _empty_frame():
    return _pandas_module().DataFrame()


def _create_default_engine():
    from core.ai_industry_chain_pool import (
        load_cached_ai_industry_chain_context_map,
        load_cached_ai_industry_chain_stock_codes,
    )
    from domains.earnings.engine import EarningsEngine

    return EarningsEngine(
        stock_universe_provider=load_cached_ai_industry_chain_stock_codes,
        stock_context_provider=load_cached_ai_industry_chain_context_map,
    )


class EarningsScheduler:
    """Lazy compatibility facade for the legacy domain scheduler."""

    @staticmethod
    def _implementation():
        from domains.earnings.scheduler import EarningsScheduler as implementation

        return implementation

    def __new__(cls, *args, **kwargs):
        return cls._implementation()(*args, **kwargs)

    @staticmethod
    def _normalize_trade_dates(trade_dates: list[str] | None) -> list[str]:
        normalized: list[str] = []
        for raw in trade_dates or []:
            text = str(raw or "").strip()
            if len(text) == 8 and text.isdigit():
                normalized.append(f"{text[:4]}-{text[4:6]}-{text[6:8]}")
            elif len(text) >= 10:
                normalized.append(text[:10])
        return sorted(dict.fromkeys(normalized))

    @classmethod
    def _build_startup_scan_dates(cls, last_sync_date: str, has_cached_records: bool) -> list[str]:
        from domains.market_calendar import MarketCalendar

        recent_trade_dates = cls._normalize_trade_dates(
            MarketCalendar.get_recent_trade_dates(STARTUP_BACKFILL_TRADE_DAYS)
        )
        if not recent_trade_dates:
            return []
        if not has_cached_records or not last_sync_date:
            return recent_trade_dates
        return [trade_date for trade_date in recent_trade_dates if trade_date > last_sync_date]


@dataclass(frozen=True)
class _ActiveEarningsJob:
    mode: str


class EarningsRefreshService(QObject):
    sig_new_surprises_found = pyqtSignal(object, str)
    sig_fetch_failed = pyqtSignal(str, str)

    def __init__(self, parent=None, *, engine: EarningsEngine | None = None, job_runner=None):
        super().__init__(parent)
        self._engine = engine
        self._engine_lock = Lock()
        self._job_runner = job_runner or task_manager
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

    def run_startup_gap_fill(self) -> dict:
        def _run():
            cached_rows_loader = getattr(self.engine, "get_cached_record_rows", None)
            if callable(cached_rows_loader):
                cached_rows = list(cached_rows_loader() or [])
                cached_records = len(cached_rows)
                self._emit_cached_rows(cached_rows)
            else:
                cached_df = self.engine.get_cached_records()
                if cached_df is None:
                    cached_df = _empty_frame()
                cached_records = len(cached_df)
                self._emit_success(cached_df, "warm_cache")

            missing_dates = EarningsScheduler._build_startup_scan_dates(
                self.engine.last_sync_date,
                has_cached_records=bool(self.engine.local_records),
            )
            gap_records = 0
            if missing_dates:
                combined = self._run_gap_fill_frames(missing_dates)
                gap_records = len(combined)
                self._emit_success(combined, "gap_fill")
            else:
                log.info("[业绩调度] 启动窗口已完整，无需补抓")
            return {
                "job_key": "earnings_startup_gap_fill",
                "records": int(cached_records + gap_records),
                "cached_records": int(cached_records),
                "gap_records": int(gap_records),
                "missing_dates": list(missing_dates or []),
            }

        try:
            return self._with_active("startup_gap_fill", _run)
        except Exception as exc:
            self._emit_failure("startup_gap_fill", exc)
            raise

    def _run_gap_fill_frames(self, date_list: list[str]) -> pd.DataFrame:
        frames = []
        for target_date in date_list or []:
            df = self.engine.fetch_daily_surprises(target_publish_date=target_date)
            if df is not None and not df.empty:
                frames.append(df)
        if frames:
            return _pandas_module().concat(frames, ignore_index=True)
        return _empty_frame()

    def run_gap_fill(self, date_list: list[str], *, mode: str = "gap_fill") -> dict:
        def _run():
            df = self._run_gap_fill_frames(list(date_list or []))
            self._emit_success(df, mode)
            return {
                "job_key": "earnings_gap_fill",
                "records": int(len(df)),
                "dates": list(date_list or []),
            }

        try:
            return self._with_active(mode, _run)
        except Exception as exc:
            self._emit_failure(mode, exc)
            raise

    def run_routine_scan(self, *, reason: str = "scheduled") -> dict:
        def _run():
            df = self.engine.fetch_daily_surprises()
            if df is None:
                df = _empty_frame()
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
            self._emit_failure("routine", exc)
            raise

    def force_manual_scan(self, date_list: list[str]) -> bool:
        if self.active_workers:
            log.info("[业绩调度] 手动回补跳过：已有后台任务运行中")
            return False
        task_id = task_registry.workspace("earnings_manual_gap_fill").task_id
        if self._job_runner.is_active_task(task_id):
            return False

        def _on_error(error_message: str) -> None:
            self._emit_failure("gap_fill", error_message)

        self._job_runner.run_in_background(
            lambda: self.run_gap_fill(date_list, mode="gap_fill"),
            on_error=_on_error,
            task_id=task_id,
        )
        return True

    def load_cached_records_async(self) -> bool:
        task_id = task_registry.workspace("earnings_view_warm_cache").task_id
        if self._job_runner.is_active_task(task_id):
            return False

        def _run():
            df = self.engine.get_cached_records()
            return df if df is not None else _empty_frame()

        def _on_success(df) -> None:
            self.sig_new_surprises_found.emit(df if df is not None else _empty_frame(), "warm_cache")

        def _on_error(error_message: str) -> None:
            self._emit_failure("warm_cache", error_message)

        self._job_runner.run_in_background(
            _run,
            on_success=_on_success,
            on_error=_on_error,
            task_id=task_id,
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

        self._job_runner.run_in_background(
            lambda: self.run_routine_scan(reason=reason),
            on_error=_on_error,
            task_id=task_id,
        )
        return True

    def stop_patrol(self) -> None:
        return None

    def shutdown(self) -> None:
        return None


__all__ = ["EarningsScheduler", "EarningsRefreshService"]
