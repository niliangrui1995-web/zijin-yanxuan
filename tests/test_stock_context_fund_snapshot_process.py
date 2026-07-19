# -*- coding: utf-8 -*-
from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.services import stock_context_fund_snapshot_process_service as process_service
from app.services.stock_context_fund_snapshot_contract import (
    StockContextFundSnapshotRequest,
    StockContextFundSnapshotResult,
    StockContextFundSnapshotStatus,
)
from app.workers import stock_context_fund_snapshot_worker_main as worker_main
from infra.storage.stock_context_fund_snapshot_repository import (
    StockContextFundSnapshotRepository,
)
from infra.tasks.app_worker_process import build_stock_context_fund_snapshot_worker_command
from infra.tasks.lifecycle import CancellationToken, TaskCancelledError


def _request(tmp_path: Path, *, stock_codes=None) -> StockContextFundSnapshotRequest:
    return StockContextFundSnapshotRequest.build(
        database_path=str(tmp_path / "vcp_hunter.db"),
        stock_codes=stock_codes,
    )


def test_contract_and_repository_preserve_filter_semantics(tmp_path):
    unfiltered = _request(tmp_path, stock_codes=None)
    explicitly_empty = _request(tmp_path, stock_codes=[])

    assert StockContextFundSnapshotRequest.from_dict(unfiltered.to_dict()) == unfiltered
    assert unfiltered.stock_codes is None
    assert explicitly_empty.stock_codes == ()

    rows = [{"代码": "000001", "变化类型": "新进"}]
    result = StockContextFundSnapshotResult.succeeded(unfiltered, rows)
    assert StockContextFundSnapshotResult.from_dict(result.to_dict()) == result

    repository = StockContextFundSnapshotRepository(tmp_path / "job")
    repository.write_request(unfiltered.to_dict())
    repository.write_result(result.to_dict())
    assert repository.read_request() == unfiltered.to_dict()
    assert repository.read_result() == result.to_dict()

    with pytest.raises(ValueError, match="schema_version"):
        StockContextFundSnapshotRequest.from_dict({**unfiltered.to_dict(), "schema_version": 999})


def test_worker_command_supports_source_frozen_and_early_entry(tmp_path, monkeypatch):
    project_root = tmp_path.resolve()
    job_dir = project_root / "job"
    monkeypatch.setattr(sys, "frozen", False, raising=False)
    source_command = build_stock_context_fund_snapshot_worker_command(
        project_root=str(project_root),
        job_dir=str(job_dir),
    )
    assert Path(source_command[0]).is_absolute()
    assert source_command[1:3] == ["-m", "app.workers.stock_context_fund_snapshot_worker_main"]
    assert Path(source_command[-1]).is_absolute()

    monkeypatch.setattr(sys, "frozen", True, raising=False)
    frozen_command = build_stock_context_fund_snapshot_worker_command(
        project_root=str(project_root),
        job_dir=str(job_dir),
    )
    assert frozen_command[:2] == [sys.executable, "--app-worker=stock-context-fund-snapshot"]

    entry_source = (Path(__file__).resolve().parents[1] / "vcp_hunter_qt.pyw").read_text(encoding="utf-8")
    worker_offset = entry_source.index('if "--app-worker=stock-context-fund-snapshot" in sys.argv:')
    assert worker_offset < entry_source.index("from core.single_instance import")
    assert "PyQt" not in (Path(worker_main.__file__).read_text(encoding="utf-8"))


def test_worker_publishes_success_and_failure_results(tmp_path, monkeypatch):
    request = _request(tmp_path, stock_codes=["000001"])
    repository = StockContextFundSnapshotRepository(tmp_path / "job")
    repository.write_request(request.to_dict())
    monkeypatch.setattr(worker_main, "_build_snapshot_rows", lambda _request: [{"代码": "000001"}])

    assert worker_main.main(["--job-dir", str(repository.job_dir)]) == 0
    success = StockContextFundSnapshotResult.from_dict(repository.read_result())
    assert success.status is StockContextFundSnapshotStatus.SUCCEEDED
    assert list(success.rows) == [{"代码": "000001"}]

    def _fail(_request):
        raise RuntimeError("deterministic failure")

    monkeypatch.setattr(worker_main, "_build_snapshot_rows", _fail)
    assert worker_main.main(["--job-dir", str(repository.job_dir)]) == 1
    failure = StockContextFundSnapshotResult.from_dict(repository.read_result())
    assert failure.status is StockContextFundSnapshotStatus.FAILED
    assert failure.error_code == "worker_crash"
    assert failure.error_message == "deterministic failure"


def test_parent_service_reads_lightweight_result_and_cleans_job_dir(tmp_path, monkeypatch):
    captured = {}

    def _run(command, **kwargs):
        captured.update({"command": command, **kwargs})
        repository = StockContextFundSnapshotRepository(command[-1])
        request = StockContextFundSnapshotRequest.from_dict(repository.read_request())
        captured["job_dir"] = repository.job_dir
        repository.write_result(
            StockContextFundSnapshotResult.succeeded(request, [{"代码": "600001"}]).to_dict()
        )
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(process_service, "run_cancellable_process", _run)
    db_path = tmp_path / "data" / "vcp_hunter.db"
    rows = process_service.load_stock_context_fund_snapshot_in_subprocess(
        project_root=tmp_path,
        database_path=db_path,
        stock_codes=["600001"],
        timeout_seconds=12,
    )

    assert rows == [{"代码": "600001"}]
    assert captured["capture_output"] is True
    assert captured["check"] is False
    assert captured["timeout"] == 12
    assert captured["env"]["VCP_HUNTER_DB_PATH"] == str(db_path.resolve())
    assert captured["stdin"] is subprocess.DEVNULL
    assert not captured["job_dir"].exists()


def test_parent_service_propagates_cancellation_and_cleans_job_dir(tmp_path, monkeypatch):
    token = CancellationToken()
    captured = {}

    def _cancel(command, **_kwargs):
        captured["job_dir"] = Path(command[-1])
        token.cancel("test_cancel")
        token.raise_if_cancelled()
        return SimpleNamespace(returncode=0, stderr="")

    monkeypatch.setattr(process_service, "run_cancellable_process", _cancel)
    with pytest.raises(TaskCancelledError, match="test_cancel"):
        process_service.load_stock_context_fund_snapshot_in_subprocess(
            project_root=tmp_path,
            database_path=tmp_path / "vcp_hunter.db",
            cancellation_token=token,
        )
    assert not captured["job_dir"].exists()


def test_parent_service_rejects_mismatched_or_failed_result(tmp_path, monkeypatch):
    def _mismatch(command, **_kwargs):
        repository = StockContextFundSnapshotRepository(command[-1])
        repository.write_result(
            StockContextFundSnapshotResult(
                request_id="wrong-request",
                status=StockContextFundSnapshotStatus.SUCCEEDED,
            ).to_dict()
        )
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(process_service, "run_cancellable_process", _mismatch)
    with pytest.raises(RuntimeError, match="request_id mismatch"):
        process_service.load_stock_context_fund_snapshot_in_subprocess(
            project_root=tmp_path,
            database_path=tmp_path / "vcp_hunter.db",
        )
