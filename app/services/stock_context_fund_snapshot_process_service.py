# -*- coding: utf-8 -*-
"""Parent-side boundary for isolated stock-context fund snapshot work."""

from __future__ import annotations

import subprocess
import tempfile
from pathlib import Path
from typing import Sequence

from app.services.stock_context_fund_snapshot_contract import (
    StockContextFundSnapshotRequest,
    StockContextFundSnapshotResult,
    StockContextFundSnapshotStatus,
)
from infra.storage.data_store import resolve_data_store_path
from infra.storage.stock_context_fund_snapshot_repository import (
    StockContextFundSnapshotRepository,
)
from infra.tasks.app_worker_process import build_stock_context_fund_snapshot_worker_command
from infra.tasks.lifecycle import CancellationToken
from infra.tasks.process_runner import (
    apply_windows_no_window_kwargs,
    build_domestic_process_env,
    run_cancellable_process,
)

DEFAULT_FUND_SNAPSHOT_PROCESS_TIMEOUT_SECONDS = 90.0


def _worker_failure_message(completed) -> str:
    stderr = str(getattr(completed, "stderr", "") or "").strip()
    return stderr[-1000:] if stderr else "worker did not publish a result"


def _process_kwargs(root: Path, request: StockContextFundSnapshotRequest) -> dict:
    kwargs = {
        "cwd": str(root),
        "env": build_domestic_process_env(
            extra={
                "PYTHONIOENCODING": "utf-8",
                "VCP_HUNTER_DB_PATH": request.database_path,
            }
        ),
        "stdin": subprocess.DEVNULL,
        "text": True,
        "encoding": "utf-8",
        "errors": "replace",
        "creationflags": int(getattr(subprocess, "BELOW_NORMAL_PRIORITY_CLASS", 0) or 0),
    }
    apply_windows_no_window_kwargs(kwargs)
    return kwargs


def _run_worker(
    *,
    root: Path,
    job_dir: str,
    request: StockContextFundSnapshotRequest,
    cancellation_token,
    timeout: float,
):
    command = build_stock_context_fund_snapshot_worker_command(
        project_root=str(root),
        job_dir=job_dir,
    )
    return run_cancellable_process(
        command,
        cancellation_token=cancellation_token,
        timeout=timeout,
        capture_output=True,
        check=False,
        **_process_kwargs(root, request),
    )


def _validated_result_rows(
    repository: StockContextFundSnapshotRepository,
    completed,
    request: StockContextFundSnapshotRequest,
) -> list[dict]:
    payload = repository.read_result()
    if payload is None:
        detail = _worker_failure_message(completed)
        raise RuntimeError(f"stock-context fund snapshot worker failed: {detail}")
    result = StockContextFundSnapshotResult.from_dict(payload)
    if result.request_id != request.request_id:
        raise RuntimeError("stock-context fund snapshot result request_id mismatch")
    if result.status is not StockContextFundSnapshotStatus.SUCCEEDED:
        detail = result.error_message or result.error_code or "worker failed"
        raise RuntimeError(f"stock-context fund snapshot worker failed: {detail}")
    if int(getattr(completed, "returncode", 0) or 0) != 0:
        detail = _worker_failure_message(completed)
        raise RuntimeError(f"stock-context fund snapshot worker exited non-zero: {detail}")
    return [dict(row) for row in result.rows]


def load_stock_context_fund_snapshot_in_subprocess(
    *,
    project_root: str | Path,
    database_path: str | Path | None = None,
    stock_codes: Sequence[object] | None = None,
    cancellation_token=None,
    timeout_seconds: float = DEFAULT_FUND_SNAPSHOT_PROCESS_TIMEOUT_SECONDS,
) -> list[dict]:
    """Build the full snapshot out-of-process and return only JSON-safe rows."""

    root = Path(project_root).resolve()
    timeout = max(0.1, float(timeout_seconds))
    token = cancellation_token or CancellationToken.with_timeout(timeout)
    resolved_database_path = database_path if database_path is not None else resolve_data_store_path()
    request = StockContextFundSnapshotRequest.build(
        database_path=str(resolved_database_path),
        stock_codes=stock_codes,
    )
    token.raise_if_cancelled()

    with tempfile.TemporaryDirectory(prefix="vcp-stock-context-fund-") as job_dir:
        repository = StockContextFundSnapshotRepository(job_dir)
        repository.write_request(request.to_dict())
        completed = _run_worker(
            root=root,
            job_dir=job_dir,
            request=request,
            cancellation_token=token,
            timeout=timeout,
        )
        token.raise_if_cancelled()
        return _validated_result_rows(repository, completed, request)


__all__ = [
    "DEFAULT_FUND_SNAPSHOT_PROCESS_TIMEOUT_SECONDS",
    "load_stock_context_fund_snapshot_in_subprocess",
]
