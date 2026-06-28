# -*- coding: utf-8 -*-
"""UI-facing earnings scheduler entrypoints."""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd
from PyQt6.QtCore import QObject, pyqtSignal

from app.services.ui_earnings_email_service import send_recent_earnings_email_digest
from app.services.ui_event_service import domain_events as event_bus
from app.services.ui_task_service import background_job_runner as task_manager
from app.services.ui_task_service import task_registry
from core.ai_industry_chain_pool import load_ai_industry_chain_stock_codes
from core.logger import get_logger
from domains.earnings import EarningsScheduler
from domains.earnings.engine import EarningsEngine

log = get_logger(__name__)


@dataclass(frozen=True)
class _ActiveEarningsJob:
    mode: str


class EarningsRefreshService(QObject):
    sig_new_surprises_found = pyqtSignal(object, str)
    sig_fetch_failed = pyqtSignal(str, str)

    def __init__(self, parent=None, *, engine: EarningsEngine | None = None, job_runner=None):
        super().__init__(parent)
        self.engine = engine or EarningsEngine(stock_universe_provider=load_ai_industry_chain_stock_codes)
        self._job_runner = job_runner or task_manager
        self.active_workers: set[_ActiveEarningsJob] = set()

    def _with_active(self, mode: str, fn):
        job = _ActiveEarningsJob(str(mode or "unknown"))
        self.active_workers.add(job)
        try:
            return fn()
        finally:
            self.active_workers.discard(job)

    def _emit_success(self, df, mode: str) -> None:
        frame = df if df is not None else pd.DataFrame()
        self.sig_new_surprises_found.emit(frame, mode)
        event_bus.sig_earnings_updated.emit()

    def _emit_failure(self, mode: str, error) -> None:
        error_text = str(error or "unknown error")
        self.sig_fetch_failed.emit(mode, error_text)

    def run_startup_gap_fill(self) -> dict:
        def _run():
            cached_df = self.engine.get_cached_records()
            if cached_df is None:
                cached_df = pd.DataFrame()
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
                "records": int(len(cached_df) + gap_records),
                "cached_records": int(len(cached_df)),
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
            return pd.concat(frames, ignore_index=True)
        return pd.DataFrame()

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
                df = pd.DataFrame()
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

    def run_email_digest(self) -> dict:
        scan_result = getattr(self.engine, "last_scan_result", {}) or {}
        if str(scan_result.get("status") or "").strip() == "degraded":
            return {
                "job_key": "earnings_email_digest",
                "status": "degraded",
                "reason": "earnings_scan_degraded",
                "error": str(scan_result.get("error") or "earnings scan degraded").strip(),
                "records": 0,
                "email_sent": False,
            }
        return send_recent_earnings_email_digest(self.engine.local_records)

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
            return df if df is not None else pd.DataFrame()

        def _on_success(df) -> None:
            self.sig_new_surprises_found.emit(df if df is not None else pd.DataFrame(), "warm_cache")

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
