# -*- coding: utf-8 -*-
"""Headless worker for CPU-heavy stock-context fund snapshot construction."""

from __future__ import annotations

import argparse
import os
import sqlite3
import sys
from pathlib import Path
from typing import Sequence

from app.services.stock_context_fund_snapshot_contract import (
    StockContextFundSnapshotRequest,
    StockContextFundSnapshotResult,
    StockContextFundSnapshotStatus,
)
from core.exceptions import CacheIOError, DataFormatError
from infra.storage.stock_context_fund_snapshot_repository import (
    StockContextFundSnapshotRepository,
)

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


def _build_snapshot_rows(request: StockContextFundSnapshotRequest) -> list[dict]:
    # Keep all datastore imports behind the request-specific process environment.
    os.environ["VCP_HUNTER_DB_PATH"] = request.database_path

    from app.services.stock_context_signal_builder_service import format_fund_holding_store_rows
    from app.services.stock_context_snapshot_service import load_fund_holding_snapshot
    from app.services.ui_fund_holdings_service import (
        QFII_CAPITAL_ATTRIBUTE_UNMARKED,
        SUBJECT_QFII,
    )

    latest_quarter_map, change_rows = load_fund_holding_snapshot(stock_codes=request.stock_codes)
    qfii_subject_code = str((SUBJECT_QFII or {}).get("subject_code") or "")
    return format_fund_holding_store_rows(
        latest_quarter_map,
        change_rows,
        qfii_subject_code=qfii_subject_code,
        unmarked_capital_attribute=QFII_CAPITAL_ATTRIBUTE_UNMARKED,
    )


def execute_request(
    request: StockContextFundSnapshotRequest,
    repository: StockContextFundSnapshotRepository,
) -> StockContextFundSnapshotResult:
    try:
        result = StockContextFundSnapshotResult.succeeded(request, _build_snapshot_rows(request))
    except _WORKER_ERRORS as exc:
        result = StockContextFundSnapshotResult.failed(
            request,
            error_code="worker_crash",
            error_message=str(exc),
        )
    repository.write_result(result.to_dict())
    return result


def _parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Headless stock-context fund snapshot worker")
    parser.add_argument("--job-dir", required=True)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)
    repository = StockContextFundSnapshotRepository(args.job_dir)
    try:
        request = StockContextFundSnapshotRequest.from_dict(repository.read_request())
        os.environ["VCP_HUNTER_DB_PATH"] = request.database_path
        if not Path(request.database_path).is_absolute():
            raise ValueError("database_path must be absolute")
        result = execute_request(request, repository)
    except _WORKER_ERRORS:
        return 1
    return 0 if result.status is StockContextFundSnapshotStatus.SUCCEEDED else 1


if __name__ == "__main__":
    sys.exit(main())
