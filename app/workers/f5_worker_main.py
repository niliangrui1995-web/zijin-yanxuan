# -*- coding: utf-8 -*-
"""Headless F5 worker process. This module must not import Qt."""

from __future__ import annotations

import argparse
import logging
import sqlite3
import sys
import time
from dataclasses import replace
from pathlib import Path
from typing import Sequence

from app.services.f5_job_contract import F5JobRequest, F5JobResult, F5JobStatus
from app.services.f5_retention_service import discard_failed_f5_generation
from core.exceptions import CacheIOError, DataFormatError
from infra.market_data.warehouse_manifest import WarehouseManifest
from infra.storage.f5_job_repository import F5JobRepository
from infra.storage.f5_snapshot_repository import F5SnapshotRepository

_WORKER_ERRORS = (
    AttributeError,
    CacheIOError,
    DataFormatError,
    ImportError,
    KeyError,
    OSError,
    RuntimeError,
    sqlite3.Error,
    TypeError,
    ValueError,
)


def _configure_worker_logging(repository: F5JobRepository) -> None:
    handler = logging.FileHandler(repository.log_path, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
    root = logging.getLogger()
    root.addHandler(handler)
    root.setLevel(logging.INFO)


def execute_request(request: F5JobRequest, repository: F5JobRepository | None = None) -> F5JobResult:
    repository = repository or F5JobRepository(request.job_dir)
    deadline = time.monotonic() + max(1.0, float(request.timeout_seconds))
    cancel_state = {"reason": ""}

    def _cancelled() -> bool:
        reason = repository.cancel_reason()
        if reason:
            cancel_state["reason"] = reason
            return True
        if time.monotonic() >= deadline:
            cancel_state["reason"] = "deadline_exceeded"
            return True
        return False

    def _event(event) -> None:
        repository.append_event(event.to_dict())

    try:
        from core.rps_precomputer import RPSPrecomputer

        result = RPSPrecomputer.run_f5_job(
            request,
            cancelled_checker=_cancelled,
            event_callback=_event,
        )
    except _WORKER_ERRORS as exc:
        logging.getLogger(__name__).exception("F5 worker crashed")
        result = F5JobResult.failed(
            request,
            error_code="worker_crash",
            error_message=str(exc),
        )
    if result.status is F5JobStatus.CANCELLED:
        reason = cancel_state["reason"] or "cancelled"
        code = "deadline_exceeded" if reason == "deadline_exceeded" else "cancelled"
        result = replace(result, error_code=code, error_message=reason)
    repository.write_result(result.to_dict())
    if result.status in {F5JobStatus.CANCELLED, F5JobStatus.FAILED}:
        _discard_failed_generation(request)
    return result


def _discard_failed_generation(request: F5JobRequest) -> None:
    try:
        manifest = WarehouseManifest(db_path=request.database_path)
        repository = F5SnapshotRepository(manifest)
        discard_failed_f5_generation(request.cache_dir, request.run_id, repository=repository)
    except _WORKER_ERRORS as exc:
        logging.getLogger(__name__).warning("F5 failed generation cleanup skipped: %s", exc)


def _parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Headless F5 snapshot worker")
    parser.add_argument("--job-dir", required=True)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)
    repository = F5JobRepository(args.job_dir)
    _configure_worker_logging(repository)
    request = None
    try:
        request = F5JobRequest.from_dict(repository.read_request())
        if Path(request.job_dir).resolve() != Path(args.job_dir).resolve():
            raise ValueError("request job_dir does not match worker argument")
        result = execute_request(request, repository)
    except _WORKER_ERRORS as exc:
        logging.getLogger(__name__).exception("F5 worker initialization failed")
        if request is None:
            return 1
        result = F5JobResult.failed(
            request,
            error_code="worker_initialization_failed",
            error_message=str(exc),
        )
        repository.write_result(result.to_dict())
    if result.status is F5JobStatus.READY_TO_ACTIVATE:
        return 0
    if result.status is F5JobStatus.CANCELLED:
        return 2
    return 1


if __name__ == "__main__":
    sys.exit(main())
