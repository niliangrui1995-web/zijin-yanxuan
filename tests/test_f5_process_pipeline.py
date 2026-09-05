# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import shutil
import subprocess
import sys
import threading
import time
from concurrent.futures import Future
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pandas as pd
import pytest

from app.services import f5_job_runner as f5_job_runner_module
from app.services import f5_snapshot_installer as installer_module
from app.services.f5_job_contract import (
    F5_JOB_SCHEMA_VERSION,
    F5JobEvent,
    F5JobRequest,
    F5JobResult,
    F5JobStatus,
    F5Phase,
    F5SnapshotArtifacts,
)
from app.services.f5_job_runner import ProcessF5JobHandle, ProcessF5JobRunner
from app.services.f5_retention_service import (
    discard_failed_f5_generation,
    inspect_f5_runtime,
    prune_f5_runtime,
)
from app.services.f5_snapshot_installer import F5SnapshotInstaller
from core import rps_precomputer as rps_module
from core.f5_activation_gate import f5_snapshot_read_boundary
from core.f5_resource_guard import F5MemoryPressureError
from core.rps_precomputer import RPSPrecomputer
from infra.market_data.f5_market_snapshot_store import F5MarketSnapshotStore
from infra.market_data.market_data_warehouse import MARKET_DATASET, WarehouseReadResult, WarehouseStatus
from infra.market_data.warehouse_manifest import (
    F5SnapshotManifestRecord,
    WarehouseManifest,
    WarehouseManifestRecord,
)
from infra.storage import json_cache_repository as json_cache_module
from infra.storage.f5_job_repository import F5JobRepository
from infra.storage.f5_snapshot_repository import F5SnapshotRepository
from infra.storage.file_integrity import (
    FileIntegrityError,
    fingerprint_file,
    verify_file_fingerprint,
)
from infra.tasks import app_worker_process as app_worker_process_module
from infra.tasks.app_worker_process import build_f5_worker_command
from ui.services.f5_job_controller import F5JobController


def _wait_for_worker_monitor(controller: F5JobController, *, timeout: float = 1.0):
    monitor = controller._monitor
    assert monitor is not None
    assert monitor.wait(timeout), "F5 worker monitor did not finish before timeout"
    controller._poll()
    return monitor


def _request(tmp_path: Path, *, run_id: str = "a" * 32) -> F5JobRequest:
    request = F5JobRequest.build(
        project_root=str(tmp_path),
        data_dir=str(tmp_path / "data"),
        cache_dir=str(tmp_path / "cache"),
        tdx_vipdoc=str(tmp_path / "vipdoc"),
        requested_date="20260715",
        timeout_seconds=2,
    )
    cache_dir = Path(request.cache_dir)
    return replace(
        request,
        run_id=run_id,
        job_dir=str(cache_dir / "f5_jobs" / run_id),
        snapshot_dir=str(cache_dir / "f5_generations" / run_id),
    )


def _snapshot(run_id: str, root: Path, *, created_at: str) -> F5SnapshotManifestRecord:
    generation = root / "f5_generations" / run_id
    generation.mkdir(parents=True, exist_ok=True)
    for name in ("market.parquet", "rps.json", "sector_rps.json"):
        (generation / name).write_text("{}", encoding="utf-8")
    market_fingerprint = fingerprint_file(generation / "market.parquet")
    rps_fingerprint = fingerprint_file(generation / "rps.json")
    sector_fingerprint = fingerprint_file(generation / "sector_rps.json")
    return F5SnapshotManifestRecord(
        snapshot_id=run_id,
        requested_date="20260715",
        effective_trade_date="20260714",
        market_parquet_path=str(generation / "market.parquet"),
        market_schema_version=3,
        market_source="vipdoc",
        market_source_version="test",
        market_symbol_count=2,
        market_row_count=120,
        rps_path=str(generation / "rps.json"),
        rps_date="20260714",
        rps_valid_count=2,
        sector_rps_path=str(generation / "sector_rps.json"),
        sector_date="20260714",
        sector_count=1,
        created_at=created_at,
        market_size_bytes=market_fingerprint.size_bytes,
        market_sha256=market_fingerprint.sha256,
        rps_size_bytes=rps_fingerprint.size_bytes,
        rps_sha256=rps_fingerprint.sha256,
        sector_rps_size_bytes=sector_fingerprint.size_bytes,
        sector_rps_sha256=sector_fingerprint.sha256,
    )


def _market_record(snapshot: F5SnapshotManifestRecord) -> WarehouseManifestRecord:
    return WarehouseManifestRecord.build(
        dataset=MARKET_DATASET,
        trade_date=snapshot.effective_trade_date,
        schema_version=snapshot.market_schema_version,
        source=snapshot.market_source,
        source_version=snapshot.market_source_version,
        parquet_path=snapshot.market_parquet_path,
        symbol_count=snapshot.market_symbol_count,
        row_count=snapshot.market_row_count,
        updated_at=snapshot.created_at,
    )


def _ready_result(request: F5JobRequest) -> F5JobResult:
    artifacts = F5SnapshotArtifacts(
        snapshot_id=request.run_id,
        requested_date=request.requested_date,
        effective_trade_date="20260714",
        market_parquet_path=str(Path(request.snapshot_dir) / "market.parquet"),
        market_schema_version=3,
        market_source="vipdoc",
        market_source_version="test",
        market_symbol_count=2,
        market_row_count=120,
        rps_path=str(Path(request.snapshot_dir) / "rps.json"),
        rps_date="20260714",
        rps_valid_count=2,
        sector_rps_path=str(Path(request.snapshot_dir) / "sector_rps.json"),
        sector_date="20260714",
        sector_count=1,
        market_size_bytes=1,
        market_sha256="0" * 64,
        rps_size_bytes=1,
        rps_sha256="0" * 64,
        sector_rps_size_bytes=1,
        sector_rps_sha256="0" * 64,
        rps250_valid_count=2,
    )
    return F5JobResult(
        run_id=request.run_id,
        status=F5JobStatus.READY_TO_ACTIVATE,
        requested_date=request.requested_date,
        effective_trade_date="20260714",
        symbol_count=2,
        rps_valid_count=2,
        sector_count=1,
        artifacts=artifacts,
    )


def _stage_ready_generation(
    request: F5JobRequest,
    *,
    market_date: str = "20260714",
    include_gbbq: bool = False,
) -> F5JobResult:
    dates = pd.bdate_range(end=pd.Timestamp(market_date), periods=60)
    frames = {
        code: pd.DataFrame(
            {
                "datetime": dates,
                "open": range(60),
                "high": range(1, 61),
                "low": range(60),
                "close": range(1, 61),
            }
        )
        for code in ("000001", "600001")
    }
    status = F5MarketSnapshotStore(request.snapshot_dir).stage_market_dataset(frames, market_date)
    assert status.ok
    generation = Path(request.snapshot_dir)
    (generation / "rps.json").write_text(
        json.dumps(
            {
                "date": "20260714",
                "rps120": {"000001": 90, "600001": 80},
                "rps250": {"000001": 70, "600001": 60},
            }
        ),
        encoding="utf-8",
    )
    (generation / "sector_rps.json").write_text(
        json.dumps({"date": "20260714", "sector_rps": {"AI": {"5": 99}}}),
        encoding="utf-8",
    )
    gbbq_path = ""
    if include_gbbq:
        gbbq_file = generation / "gbbq.json"
        gbbq_file.write_text(
            json.dumps({"data": {"000001": []}, "mtime": 2.0, "records": 1}),
            encoding="utf-8",
        )
        gbbq_path = str(gbbq_file)
    market_fingerprint = fingerprint_file(status.parquet_path)
    rps_fingerprint = fingerprint_file(generation / "rps.json")
    sector_fingerprint = fingerprint_file(generation / "sector_rps.json")
    gbbq_fingerprint = fingerprint_file(gbbq_path) if gbbq_path else None
    result = _ready_result(request)
    artifacts = replace(
        result.artifacts,
        market_parquet_path=status.parquet_path,
        market_symbol_count=status.symbol_count,
        market_row_count=status.row_count,
        market_size_bytes=market_fingerprint.size_bytes,
        market_sha256=market_fingerprint.sha256,
        rps_size_bytes=rps_fingerprint.size_bytes,
        rps_sha256=rps_fingerprint.sha256,
        sector_rps_size_bytes=sector_fingerprint.size_bytes,
        sector_rps_sha256=sector_fingerprint.sha256,
        gbbq_path=gbbq_path,
        gbbq_size_bytes=gbbq_fingerprint.size_bytes if gbbq_fingerprint else 0,
        gbbq_sha256=gbbq_fingerprint.sha256 if gbbq_fingerprint else "",
    )
    return replace(result, artifacts=artifacts)


class _ParentEngine:
    def __init__(self, *, fail_on_date: str = "") -> None:
        self.fail_on_date = fail_on_date
        self.payload = {"date": "20260713", "rps120": {"old": 1}, "rps250": {"old": 1}}

    def get_precomputed_rps(self):
        return self.payload

    def set_precomputed_rps(self, date, rps120, rps250):
        if date == self.fail_on_date:
            raise RuntimeError("install failed")
        self.payload = {"date": date, "rps120": rps120, "rps250": rps250}


def _parent_provider(tmp_path: Path):
    gbbq_cache_file = tmp_path / "parent-gbbq.json"
    gbbq_cache_file.write_text(
        json.dumps({"data": {"old": []}, "mtime": 1.0, "records": 1}),
        encoding="utf-8",
    )
    return SimpleNamespace(
        cache_lock=threading.RLock(),
        cache_data={"old": object()},
        _market_data_snapshot_trade_date="20260713",
        _last_market_data_source_status={"old": True},
        gbbq_cache_file=str(gbbq_cache_file),
        _local_gbbq_lock=threading.RLock(),
        _local_gbbq={"old": object()},
        _local_gbbq_code_cache={"old": object()},
        _local_gbbq_loaded=True,
    )


def test_contract_and_event_file_protocol_round_trip(tmp_path):
    request = _request(tmp_path)
    assert F5_JOB_SCHEMA_VERSION == 2
    assert F5JobRequest.from_dict(request.to_dict()) == request
    assert request.requested_date == "20260715"
    with pytest.raises(ValueError, match="schema_version"):
        F5JobRequest.from_dict({**request.to_dict(), "schema_version": 999})
    with pytest.raises(ValueError, match="schema_version"):
        F5JobRequest.from_dict({**request.to_dict(), "schema_version": 1})

    repository = F5JobRepository(request.job_dir)
    repository.write_request(request.to_dict())
    event = F5JobEvent(request.run_id, 1, F5Phase.MARKET_SYNC, "同步")
    repository.append_event(event.to_dict())
    with repository.events_path.open("ab") as file_obj:
        file_obj.write(b'{"partial":')

    payloads, offset = repository.read_events()
    assert [F5JobEvent.from_dict(payload) for payload in payloads] == [event]
    assert repository.read_events(offset=offset) == ([], offset)


def test_streaming_file_fingerprint_detects_size_and_sha256_changes(tmp_path):
    artifact = tmp_path / "artifact.bin"
    artifact.write_bytes(b"abcdef")
    fingerprint = fingerprint_file(artifact, chunk_size=2)

    assert fingerprint.size_bytes == 6
    assert fingerprint.sha256 == "bef57ec7f53a6d40beb640a780a639c83bc29ac8a9816f1fc6c5c6dcd93c4721"
    assert verify_file_fingerprint(
        artifact,
        expected_size_bytes=fingerprint.size_bytes,
        expected_sha256=fingerprint.sha256,
    ) == fingerprint

    artifact.write_bytes(b"abcdeg")
    with pytest.raises(FileIntegrityError, match="SHA-256 mismatch"):
        verify_file_fingerprint(
            artifact,
            expected_size_bytes=fingerprint.size_bytes,
            expected_sha256=fingerprint.sha256,
        )


def test_worker_command_uses_absolute_paths_and_entry_precedes_single_instance(tmp_path, monkeypatch):
    project_root = tmp_path.resolve()
    job_dir = project_root / "cache" / "f5_jobs" / ("a" * 32)
    monkeypatch.setattr(sys, "frozen", False, raising=False)
    command = build_f5_worker_command(project_root=str(project_root), job_dir=str(job_dir))
    assert Path(command[0]).is_absolute()
    assert command[1:3] == ["-m", "app.workers.f5_worker_main"]
    assert Path(command[-1]).is_absolute()

    entry_source = (Path(__file__).resolve().parents[1] / "vcp_hunter_qt.pyw").read_text(encoding="utf-8")
    worker_offset = entry_source.index('if "--app-worker=f5" in sys.argv:')
    assert worker_offset < entry_source.index("from core.single_instance import")
    if "from PyQt6" in entry_source:
        assert worker_offset < entry_source.index("from PyQt6")


def test_f5_worker_reserves_foreground_cpu_and_disables_nested_math_parallelism(tmp_path, monkeypatch):
    captured = {}

    def _spawn(command, **kwargs):
        captured.update(command=command, **kwargs)
        return object()

    monkeypatch.setattr(app_worker_process_module.os, "cpu_count", lambda: 12)
    monkeypatch.setattr(app_worker_process_module, "spawn_silent_process", _spawn)

    result = app_worker_process_module.spawn_f5_worker(
        project_root=str(tmp_path),
        job_dir=str(tmp_path / "job"),
    )

    assert result is not None
    assert captured["env"]["POLARS_MAX_THREADS"] == "2"
    assert captured["env"]["PYTHONFAULTHANDLER"] == "1"
    for variable in ("OPENBLAS_NUM_THREADS", "OMP_NUM_THREADS", "MKL_NUM_THREADS", "NUMEXPR_NUM_THREADS"):
        assert captured["env"][variable] == "1"
    assert Path(captured["stdout"].name) == Path(tmp_path / "job" / "worker.stdout.log")
    assert Path(captured["stderr"].name) == Path(tmp_path / "job" / "worker.stderr.log")


def test_f5_job_builds_job_local_coherent_bundle_with_effective_date(tmp_path, monkeypatch):
    request = _request(tmp_path)
    dates = pd.date_range("2026-04-16", periods=60, freq="B")
    frames = {
        code: pd.DataFrame(
            {
                "datetime": dates,
                "open": range(60),
                "high": range(1, 61),
                "low": range(60),
                "close": range(1, 61),
            }
        )
        for code in ("000001", "600001")
    }

    class _Provider:
        def __init__(self):
            self.cache_lock = threading.RLock()
            self.cache_data = {}
            self.code2name = {}
            self.tdx_vipdoc = str(tmp_path / "vipdoc")

        def ensure_adjustment_metadata(self, *, force=False):
            Path(self.gbbq_cache_file).write_text(
                json.dumps({"data": {"000001": []}, "mtime": 1.0, "records": 1}),
                encoding="utf-8",
            )

        @staticmethod
        def load_cache_from_disk():
            return ""

        @staticmethod
        def _get_codes_from_vipdoc():
            return {"000001": "A", "600001": "B"}

        @staticmethod
        def is_online():
            return False

        @staticmethod
        def set_online_mode(_online):
            return None

        def sync_market_data(self, codes, **kwargs):
            self.cache_data = dict(frames)
            kwargs["snapshot_writer"](self.cache_data, kwargs["snapshot_date"])

    class _Engine:
        @staticmethod
        def build_rps_matrix(all_data, start_date, end_date):
            assert set(all_data) == {"000001", "600001"}
            assert start_date == end_date == "20260708"
            return {
                end_date: {
                    "rps120": {"000001": 90, "600001": 80},
                    "rps250": {"000001": 70, "600001": 60},
                }
            }

    import vcp.sector as sector

    sector_manager = SimpleNamespace(build_sector_rps=lambda _data, _date: {"AI": {5: 99}})
    monkeypatch.setattr(sector.SectorManager, "get_instance", lambda _root: sector_manager)
    monkeypatch.setattr(rps_module, "ensure_cache_dir", lambda: None)

    result = RPSPrecomputer.run_f5_job(request, data_provider=_Provider(), engine=_Engine())

    assert result.status is F5JobStatus.READY_TO_ACTIVATE
    assert result.requested_date == "20260715"
    assert result.effective_trade_date == "20260708"
    assert Path(result.artifacts.market_parquet_path).parent == Path(request.snapshot_dir)
    assert Path(result.artifacts.market_parquet_path).is_file()
    assert Path(result.artifacts.rps_path).is_file()
    assert Path(result.artifacts.sector_rps_path).is_file()
    assert Path(result.artifacts.gbbq_path).is_file()
    market_fingerprint = fingerprint_file(result.artifacts.market_parquet_path)
    rps_fingerprint = fingerprint_file(result.artifacts.rps_path)
    sector_fingerprint = fingerprint_file(result.artifacts.sector_rps_path)
    assert market_fingerprint.size_bytes == result.artifacts.market_size_bytes
    assert market_fingerprint.sha256 == result.artifacts.market_sha256
    assert rps_fingerprint.size_bytes == result.artifacts.rps_size_bytes
    assert rps_fingerprint.sha256 == result.artifacts.rps_sha256
    assert sector_fingerprint.size_bytes == result.artifacts.sector_rps_size_bytes
    assert sector_fingerprint.sha256 == result.artifacts.sector_rps_sha256
    gbbq_fingerprint = fingerprint_file(result.artifacts.gbbq_path)
    assert gbbq_fingerprint.size_bytes == result.artifacts.gbbq_size_bytes
    assert gbbq_fingerprint.sha256 == result.artifacts.gbbq_sha256


def test_market_snapshot_date_validation_uses_symbol_latest_date_mode(tmp_path):
    base_dates = pd.bdate_range(end="2026-07-14", periods=3)
    frames = {}
    for code in ("000001", "600001"):
        frames[code] = pd.DataFrame(
            {"datetime": base_dates, "open": [1, 2, 3], "high": [2, 3, 4], "low": [0, 1, 2], "close": [1, 2, 3]}
        )
    frames["300001"] = pd.DataFrame(
        {
            "datetime": base_dates.append(pd.DatetimeIndex(["2026-07-15"])),
            "open": [1, 2, 3, 4],
            "high": [2, 3, 4, 5],
            "low": [0, 1, 2, 3],
            "close": [1, 2, 3, 4],
        }
    )
    store = F5MarketSnapshotStore(tmp_path / "generation")
    status = store.stage_market_dataset(frames, "20260714")

    loaded = store.read_market_snapshot(
        trade_date="20260714",
        expected_symbol_count=status.symbol_count,
        expected_row_count=status.row_count,
    )

    assert loaded.status.ok
    assert set(loaded.data) == {"000001", "600001", "300001"}


class _StubbornProcess:
    def __init__(self):
        self.running = True
        self.terminate_calls = 0
        self.kill_calls = 0
        self.wait_calls = []

    def poll(self):
        return None if self.running else -9

    def terminate(self):
        self.terminate_calls += 1

    def kill(self):
        self.kill_calls += 1
        self.running = False

    def wait(self, timeout=None):
        self.wait_calls.append(timeout)
        if self.running:
            raise subprocess.TimeoutExpired("f5", timeout)
        return -9


def test_forced_deadline_exit_is_reaped_and_persisted_as_cancelled(tmp_path):
    request = _request(tmp_path)
    repository = F5JobRepository(request.job_dir)
    process = _StubbornProcess()
    handle = ProcessF5JobHandle(request, repository, process)
    handle.cancel("deadline_exceeded")
    handle._cancel_requested_at = time.monotonic() - 10
    handle._terminate_requested_at = time.monotonic() - 10

    handle.enforce_cancel_grace(0.1)
    result = handle.result()

    assert process.kill_calls == 1
    assert process.wait_calls
    assert result.status is F5JobStatus.CANCELLED
    assert result.error_code == "deadline_exceeded"
    assert F5JobResult.from_dict(repository.read_result()).error_code == "deadline_exceeded"


def test_real_subprocess_cancel_and_crash_without_result_are_terminal(tmp_path):
    request = _request(tmp_path)
    repository = F5JobRepository(request.job_dir)
    sleeping = subprocess.Popen(
        [sys.executable, "-c", "import sys; sys.stdin.read()"],
        stdin=subprocess.PIPE,
    )
    handle = ProcessF5JobHandle(request, repository, sleeping)
    handle.cancel("user_cancelled")
    handle._cancel_requested_at = time.monotonic() - 10
    handle._terminate_requested_at = time.monotonic() - 10
    handle.enforce_cancel_grace(0.0)
    cancelled = handle.result()
    assert cancelled.status is F5JobStatus.CANCELLED
    assert sleeping.poll() is not None

    crashed_request = _request(tmp_path, run_id="b" * 32)
    crashed_repository = F5JobRepository(crashed_request.job_dir)
    crashed_repository.worker_stderr_path.write_text("fatal worker marker", encoding="utf-8")
    crashed_process = subprocess.Popen([sys.executable, "-c", "raise SystemExit(7)"])
    crashed_process.wait(timeout=5)
    crashed_handle = ProcessF5JobHandle(crashed_request, crashed_repository, crashed_process)
    crashed = crashed_handle.result()
    assert crashed is not None
    assert crashed.status is F5JobStatus.FAILED
    assert crashed.error_code == "worker_process_exited"
    assert crashed.worker_exit_code == 7
    assert "exit_code=7" in crashed.error_message
    assert "fatal worker marker" in crashed.error_message
    assert F5JobResult.from_dict(crashed_repository.read_result()).worker_exit_code == 7

    finished = []
    controller = F5JobController(
        runner=SimpleNamespace(start=lambda _request: crashed_handle),
        installer=SimpleNamespace(prune_after_terminal=lambda _result: None),
    )
    controller.start(crashed_request, on_finished=finished.append)
    _wait_for_worker_monitor(controller)
    assert finished[0].error_code == "worker_process_exited"


def test_process_handle_treats_zero_exit_without_result_as_failure(tmp_path):
    request = _request(tmp_path, run_id="c" * 32)
    repository = F5JobRepository(request.job_dir)
    repository.worker_stderr_path.write_text("native exit marker", encoding="utf-8")
    process = subprocess.Popen([sys.executable, "-c", "import os; os._exit(0)"])
    process.wait(timeout=5)

    result = ProcessF5JobHandle(request, repository, process).result()

    assert result is not None
    assert result.status is F5JobStatus.FAILED
    assert result.worker_exit_code == 0
    assert "exit_code=0" in result.error_message


def test_process_handle_retries_terminal_result_after_transient_read_error(tmp_path, monkeypatch):
    request = _request(tmp_path, run_id="d" * 32)
    repository = F5JobRepository(request.job_dir)
    worker_result = F5JobResult.failed(
        request,
        error_code="worker_memory_exhausted",
        error_message="MemoryError",
    )
    attempts = []

    def _read_result():
        attempts.append(True)
        if len(attempts) == 1:
            raise OSError("temporary sharing violation")
        return worker_result.to_dict()

    class _ExitedProcess:
        returncode = 1

        @staticmethod
        def poll():
            return 1

        @staticmethod
        def wait(timeout=None):
            return 1

    monkeypatch.setattr(repository, "read_result", _read_result)
    monkeypatch.setattr(f5_job_runner_module.time, "sleep", lambda _seconds: None)

    result = ProcessF5JobHandle(request, repository, _ExitedProcess()).result()

    assert result is not None
    assert result.error_code == "worker_memory_exhausted"
    assert len(attempts) >= 2


def test_process_handle_terminalizes_malformed_worker_result(tmp_path):
    request = _request(tmp_path, run_id="e" * 32)
    repository = F5JobRepository(request.job_dir)
    repository.write_result({"schema_version": 0})

    class _ExitedProcess:
        returncode = 23

        @staticmethod
        def poll():
            return 23

        @staticmethod
        def wait(timeout=None):
            return 23

    result = ProcessF5JobHandle(request, repository, _ExitedProcess()).result()

    assert result is not None
    assert result.status is F5JobStatus.FAILED
    assert result.error_code == "worker_result_unreadable"
    assert result.worker_exit_code == 23
    assert "unsupported F5 result schema_version: 0" in result.error_message
    persisted = F5JobResult.from_dict(repository.read_result())
    assert persisted.error_code == "worker_result_unreadable"


def test_process_handle_terminalizes_invalid_json_from_real_repository(tmp_path, monkeypatch):
    request = _request(tmp_path)
    repository = F5JobRepository(request.job_dir)
    repository.result_path.write_text('{"schema_version":', encoding="utf-8")
    process = SimpleNamespace(returncode=23, poll=lambda: 23, wait=lambda timeout=None: 23)
    monkeypatch.setattr(f5_job_runner_module.time, "sleep", lambda _seconds: None)

    result = ProcessF5JobHandle(request, repository, process).result()

    assert result is not None
    assert result.status is F5JobStatus.FAILED
    assert result.error_code == "worker_result_unreadable"
    assert result.worker_exit_code == 23
    assert "json payload invalid" in result.error_message
    assert F5JobResult.from_dict(repository.read_result()).error_code == "worker_result_unreadable"


def test_process_handle_retries_wrapped_repository_read_error(tmp_path, monkeypatch):
    request = _request(tmp_path)
    repository = F5JobRepository(request.job_dir)
    expected = F5JobResult.failed(request, error_code="worker_failed", error_message="worker failure")
    repository.write_result(expected.to_dict())
    process = SimpleNamespace(returncode=1, poll=lambda: 1, wait=lambda timeout=None: 1)
    real_open = open
    attempts = []

    def _transient_open(path, mode="r", **kwargs):
        if str(path) == str(repository.result_path) and mode == "r":
            attempts.append(True)
            if len(attempts) == 1:
                raise PermissionError("temporary sharing violation")
        return real_open(path, mode, **kwargs)

    monkeypatch.setattr(json_cache_module, "open", _transient_open, raising=False)
    monkeypatch.setattr(f5_job_runner_module.time, "sleep", lambda _seconds: None)

    result = ProcessF5JobHandle(request, repository, process).result()

    assert result == expected
    assert len(attempts) == 2


def test_process_handle_keeps_terminal_result_when_repository_write_is_wrapped(tmp_path, monkeypatch):
    request = _request(tmp_path)
    repository = F5JobRepository(request.job_dir)
    process = SimpleNamespace(returncode=7, poll=lambda: 7, wait=lambda timeout=None: 7)

    def _deny_replace(_source, _target):
        raise PermissionError("terminal result path is locked")

    monkeypatch.setattr(json_cache_module.os, "replace", _deny_replace)
    monkeypatch.setattr(f5_job_runner_module.time, "sleep", lambda _seconds: None)
    handle = ProcessF5JobHandle(request, repository, process)

    result = handle.result()

    assert result is not None
    assert result.status is F5JobStatus.FAILED
    assert result.error_code == "worker_process_exited"
    assert result.worker_exit_code == 7
    assert handle.result() is result
    assert not repository.result_path.exists()
    assert not list(repository.job_dir.glob("*.tmp"))


def test_process_handle_blocks_ready_result_when_worker_exits_nonzero(tmp_path):
    request = _request(tmp_path, run_id="f" * 32)
    repository = F5JobRepository(request.job_dir)
    repository.write_result(_ready_result(request).to_dict())
    repository.worker_stderr_path.write_text("native crash after result", encoding="utf-8")

    class _ExitedProcess:
        returncode = 7

        @staticmethod
        def poll():
            return 7

        @staticmethod
        def wait(timeout=None):
            return 7

    result = ProcessF5JobHandle(request, repository, _ExitedProcess()).result()

    assert result is not None
    assert result.status is F5JobStatus.FAILED
    assert result.error_code == "worker_ready_exit_nonzero"
    assert result.worker_exit_code == 7
    assert "ready_to_activate" in result.error_message
    assert "native crash after result" in result.error_message
    persisted = F5JobResult.from_dict(repository.read_result())
    assert persisted.error_code == "worker_ready_exit_nonzero"


def test_runner_rejects_critical_memory_pressure_before_spawning_worker(tmp_path, monkeypatch):
    request = _request(tmp_path, run_id="e" * 32)
    spawned = []
    pressure = F5MemoryPressureError(stage="F5 启动", headroom_bytes=128 * 1024**2, minimum_bytes=2 * 1024**3)
    monkeypatch.setattr(f5_job_runner_module, "ensure_f5_commit_headroom", lambda *_args, **_kwargs: (_ for _ in ()).throw(pressure))
    monkeypatch.setattr(f5_job_runner_module, "spawn_f5_worker", lambda **_kwargs: spawned.append(True))

    with pytest.raises(F5MemoryPressureError):
        ProcessF5JobRunner().start(request)

    assert spawned == []
    assert not Path(request.job_dir).exists()

    finished = []
    controller = F5JobController(
        runner=SimpleNamespace(start=lambda _request: (_ for _ in ()).throw(pressure)),
        installer=SimpleNamespace(prune_after_terminal=lambda _result: None),
    )
    assert controller.start(request, on_finished=finished.append) is True
    assert finished[0].error_code == "insufficient_memory_headroom"


def test_process_handle_os_errors_still_attempt_kill_and_reap(tmp_path):
    request = _request(tmp_path)
    repository = F5JobRepository(request.job_dir)

    class _OsErrorProcess:
        def __init__(self):
            self.running = True
            self.kill_calls = 0
            self.wait_calls = 0

        def poll(self):
            return None if self.running else -9

        def terminate(self):
            raise ProcessLookupError("terminate race")

        def kill(self):
            self.kill_calls += 1
            self.running = False

        def wait(self, timeout=None):
            self.wait_calls += 1
            if self.running:
                raise subprocess.TimeoutExpired("f5", timeout)
            return -9

    process = _OsErrorProcess()
    handle = ProcessF5JobHandle(request, repository, process)
    handle.cancel("user_cancelled")
    handle._cancel_requested_at = time.monotonic() - 10
    handle.enforce_cancel_grace(0.0)
    assert process.kill_calls == 1
    assert process.wait_calls >= 1


def test_controller_converts_spawn_failure_to_terminal_callback(tmp_path):
    request = _request(tmp_path)
    finished = []
    runner = SimpleNamespace(start=lambda _request: (_ for _ in ()).throw(OSError("spawn failed")))
    installer = SimpleNamespace(prune_after_terminal=lambda result: None)
    controller = F5JobController(runner=runner, installer=installer)

    accepted = controller.start(request, on_finished=finished.append)

    assert accepted is True
    assert finished[0].status is F5JobStatus.FAILED
    assert finished[0].error_code == "worker_start_failed"


def test_controller_deadline_rejects_ready_result_before_activation(tmp_path):
    request = _request(tmp_path)
    handle = _ReadyHandle(_ready_result(request))
    activated = []
    finished = []
    installer = SimpleNamespace(
        activate=lambda result, **_kwargs: activated.append(result) or result,
        prune_after_terminal=lambda _result: None,
    )
    controller = F5JobController(runner=SimpleNamespace(start=lambda _request: handle), installer=installer)
    controller.start(request, on_finished=finished.append)
    controller._started_at = time.monotonic() - request.timeout_seconds - 1

    _wait_for_worker_monitor(controller)

    assert activated == []
    assert controller._activation_cancel_reason == "deadline_exceeded"
    assert finished[0].status is F5JobStatus.CANCELLED
    assert finished[0].error_code == "deadline_exceeded"


def test_controller_rejects_stale_self_consistent_result(tmp_path):
    request = _request(tmp_path)
    stale_request = _request(tmp_path, run_id="b" * 32)
    finished = []
    activated = []
    controller = F5JobController(
        runner=SimpleNamespace(start=lambda _request: _ReadyHandle(_ready_result(stale_request))),
        installer=SimpleNamespace(
            activate=lambda result, **_kwargs: activated.append(result) or result,
            prune_after_terminal=lambda _result: None,
        ),
    )
    controller.start(request, on_finished=finished.append)

    _wait_for_worker_monitor(controller)

    assert activated == []
    assert finished[0].error_code == "worker_result_mismatch"


def test_controller_rejects_worker_claiming_parent_only_success(tmp_path):
    request = _request(tmp_path)
    forged = replace(_ready_result(request), status=F5JobStatus.SUCCEEDED)
    finished = []
    controller = F5JobController(
        runner=SimpleNamespace(start=lambda _request: _ReadyHandle(forged)),
        installer=SimpleNamespace(prune_after_terminal=lambda _result: None),
    )
    controller.start(request, on_finished=finished.append)

    _wait_for_worker_monitor(controller)

    assert finished[0].status is F5JobStatus.FAILED
    assert finished[0].error_code == "worker_result_mismatch"


def test_controller_poll_error_terminates_and_reaps_worker(tmp_path):
    request = _request(tmp_path)
    calls = []

    class _BrokenHandle:
        def poll_events(self):
            raise ValueError("corrupt event")

        def cancel(self, reason):
            calls.append(("cancel", reason))

        def is_running(self):
            return "force" not in calls

        def force_terminate(self):
            calls.append("force")

        def result(self):
            calls.append("result")
            return None

    finished = []
    controller = F5JobController(
        runner=SimpleNamespace(start=lambda _request: _BrokenHandle()),
        installer=SimpleNamespace(prune_after_terminal=lambda _result: None),
    )
    controller.start(request, on_finished=finished.append)

    _wait_for_worker_monitor(controller)

    assert ("cancel", "controller_failed") in calls
    assert "force" in calls and "result" in calls
    assert finished[0].error_code == "f5_controller_failed"


def test_controller_monitor_base_exception_publishes_terminal_failure(tmp_path):
    request = _request(tmp_path)
    calls = []

    class _SystemExitHandle:
        def __init__(self):
            self.running = True

        def poll_events(self):
            raise SystemExit("poll events aborted")

        def cancel(self, reason):
            calls.append(("cancel", reason))

        def is_running(self):
            return self.running

        def force_terminate(self):
            calls.append("force")
            self.running = False

        def result(self):
            calls.append("result")
            return None

    finished = []
    controller = F5JobController(
        runner=SimpleNamespace(start=lambda _request: _SystemExitHandle()),
        installer=SimpleNamespace(prune_after_terminal=lambda _result: None),
    )
    controller.start(request, on_finished=finished.append)

    _wait_for_worker_monitor(controller)

    assert ("cancel", "controller_failed") in calls
    assert "force" in calls and "result" in calls
    assert finished[0].error_code == "f5_controller_failed"
    assert "SystemExit: poll events aborted" in finished[0].error_message
    assert controller._handle is None


def test_controller_waits_for_worker_exit_after_result_is_written(tmp_path):
    request = _request(tmp_path)
    ready = _ready_result(request)
    activated = threading.Event()

    class _ResultBeforeExitHandle(_ReadyHandle):
        running = True

        def is_running(self):
            return self.running

    handle = _ResultBeforeExitHandle(ready)
    installer = SimpleNamespace(
        activate=lambda result, **_kwargs: activated.set() or replace(result, status=F5JobStatus.SUCCEEDED),
        prune_after_terminal=lambda _result: None,
    )
    controller = F5JobController(runner=SimpleNamespace(start=lambda _request: handle), installer=installer)
    controller.start(request)

    controller._poll()
    assert not activated.is_set()
    assert controller._handle is handle

    handle.running = False
    _wait_for_worker_monitor(controller)
    assert activated.wait(1)
    controller._poll()
    persisted = F5JobResult.from_dict(F5JobRepository(request.job_dir).read_result())
    assert persisted.status is F5JobStatus.SUCCEEDED


def test_activation_deadline_cancels_validation_without_window_close(tmp_path, monkeypatch):
    request = _request(tmp_path)
    ready = _ready_result(request)
    provider = _parent_provider(tmp_path)
    installer = F5SnapshotInstaller(
        data_provider=provider,
        engine=_ParentEngine(),
        database_path=str(tmp_path / "manifest.db"),
        cache_dir=request.cache_dir,
    )
    entered = threading.Event()
    release = threading.Event()
    bundle = installer_module._ValidatedBundle(
        market=WarehouseReadResult(
            {"new": object()},
            WarehouseStatus(ok=True, trade_date="20260714", symbol_count=2, row_count=120),
        ),
        rps120={"new": 90},
        rps250={"new": 80},
    )

    def _blocked_load(_result):
        entered.set()
        release.wait(5)
        return bundle

    monkeypatch.setattr(installer, "_load_validated_bundle", _blocked_load)
    finished = []
    controller = F5JobController(
        runner=SimpleNamespace(start=lambda _request: _ReadyHandle(ready)),
        installer=installer,
    )
    controller.start(request, on_finished=finished.append)
    _wait_for_worker_monitor(controller)
    assert entered.wait(1)
    controller._started_at = time.monotonic() - request.timeout_seconds - 1
    _wait_for_worker_monitor(controller)
    assert controller._activation_cancelled.is_set()
    release.set()
    controller._activation_thread.join(timeout=2)
    controller._poll()

    assert finished[0].status is F5JobStatus.CANCELLED
    assert finished[0].error_code == "deadline_exceeded"
    assert installer.repository.active() is None


class _ReadyHandle:
    def __init__(self, result):
        self._result = result

    def poll_events(self):
        return ()

    def result(self):
        return self._result

    def cancel(self, reason="cancelled"):
        self.cancel_reason = reason

    def enforce_cancel_grace(self, grace_seconds=2.0):
        return None

    def force_terminate(self):
        return None

    def is_running(self):
        return False


def test_controller_gui_poll_only_drains_monitor_memory(tmp_path):
    request = _request(tmp_path)
    event = F5JobEvent(request.run_id, 1, F5Phase.PREPARE, "started")
    failed = F5JobResult.failed(request, error_code="expected", error_message="expected")
    gui_thread_id = threading.get_ident()

    class _CompletedHandle:
        def __init__(self):
            self.calls = []
            self.event_delivered = False

        def _record(self, name):
            self.calls.append((name, threading.get_ident()))

        def poll_events(self):
            self._record("poll_events")
            if self.event_delivered:
                return ()
            self.event_delivered = True
            return (event,)

        def enforce_cancel_grace(self, _grace_seconds=2.0):
            self._record("enforce_cancel_grace")

        def is_running(self):
            self._record("is_running")
            return False

        def result(self):
            self._record("result")
            return failed

        def cancel(self, _reason="cancelled"):
            self._record("cancel")

        def force_terminate(self):
            self._record("force_terminate")

    handle = _CompletedHandle()
    event_threads = []
    finished_threads = []
    controller = F5JobController(
        runner=SimpleNamespace(start=lambda _request: handle),
        installer=SimpleNamespace(prune_after_terminal=lambda _result: None),
    )
    controller.start(
        request,
        on_event=lambda _event: event_threads.append(threading.get_ident()),
        on_finished=lambda _result: finished_threads.append(threading.get_ident()),
    )

    _wait_for_worker_monitor(controller)

    assert handle.calls
    assert {thread_id for _, thread_id in handle.calls} == {
        next(thread_id for _, thread_id in handle.calls)
    }
    assert all(thread_id != gui_thread_id for _, thread_id in handle.calls)
    assert event_threads == [gui_thread_id]
    assert finished_threads == [gui_thread_id]


def test_monitor_delivers_final_events_in_order_before_terminal(tmp_path):
    request = _request(tmp_path)
    events = (
        F5JobEvent(request.run_id, 1, F5Phase.PREPARE, "first"),
        F5JobEvent(request.run_id, 2, F5Phase.MARKET_SYNC, "final"),
    )
    failed = F5JobResult.failed(request, error_code="expected", error_message="expected")

    class _OrderedHandle:
        def __init__(self):
            self.poll_count = 0

        def poll_events(self):
            index = self.poll_count
            self.poll_count += 1
            return (events[index],) if index < len(events) else ()

        def enforce_cancel_grace(self, _grace_seconds=2.0):
            return None

        def is_running(self):
            return False

        def result(self):
            return failed

        def cancel(self, _reason="cancelled"):
            return None

        def force_terminate(self):
            return None

    delivered = []
    controller = F5JobController(
        runner=SimpleNamespace(start=lambda _request: _OrderedHandle()),
        installer=SimpleNamespace(prune_after_terminal=lambda _result: None),
    )
    controller.start(
        request,
        on_event=lambda current: delivered.append(current.seq),
        on_finished=lambda _result: delivered.append("terminal"),
    )

    _wait_for_worker_monitor(controller)

    assert delivered == [1, 2, "terminal"]


def test_monitor_reads_result_once_only_after_worker_exit(tmp_path):
    request = _request(tmp_path)
    failed = F5JobResult.failed(request, error_code="expected", error_message="expected")

    class _ControlledHandle:
        def __init__(self):
            self.release = threading.Event()
            self.first_poll = threading.Event()
            self.calls = []
            self.result_calls = 0

        def poll_events(self):
            self.calls.append(("poll_events", self.release.is_set()))
            self.first_poll.set()
            return ()

        def enforce_cancel_grace(self, _grace_seconds=2.0):
            self.calls.append(("enforce_cancel_grace", self.release.is_set()))

        def is_running(self):
            running = not self.release.is_set()
            self.calls.append(("is_running", running))
            return running

        def result(self):
            self.result_calls += 1
            self.calls.append(("result", self.release.is_set()))
            assert self.release.is_set()
            return failed

        def cancel(self, _reason="cancelled"):
            return None

        def force_terminate(self):
            self.release.set()

    handle = _ControlledHandle()
    controller = F5JobController(
        runner=SimpleNamespace(start=lambda _request: handle),
        installer=SimpleNamespace(prune_after_terminal=lambda _result: None),
    )
    controller.start(request)
    assert handle.first_poll.wait(1)
    assert handle.result_calls == 0

    handle.release.set()
    controller._monitor._wake_event.set()
    _wait_for_worker_monitor(controller)

    assert handle.result_calls == 1
    false_running_index = next(
        index for index, call in enumerate(handle.calls) if call == ("is_running", False)
    )
    result_index = next(index for index, call in enumerate(handle.calls) if call[0] == "result")
    assert false_running_index < result_index


def test_shutdown_commands_monitor_to_cancel_force_and_reap(tmp_path):
    request = _request(tmp_path)
    gui_thread_id = threading.get_ident()

    class _StubbornHandle:
        def __init__(self):
            self.running = True
            self.polled = threading.Event()
            self.calls = []
            self.result_calls = 0
            self.reaped = False

        def _record(self, name):
            self.calls.append((name, threading.get_ident()))

        def poll_events(self):
            self._record("poll_events")
            self.polled.set()
            return ()

        def cancel(self, _reason="cancelled"):
            self._record("cancel")

        def enforce_cancel_grace(self, _grace_seconds=2.0):
            self._record("enforce_cancel_grace")

        def is_running(self):
            self._record("is_running")
            return self.running

        def force_terminate(self):
            self._record("force_terminate")
            self.running = False
            self.reaped = True

        def result(self):
            self._record("result")
            assert not self.running
            self.result_calls += 1
            return F5JobResult.cancelled(request, reason="owner_shutdown")

    handle = _StubbornHandle()
    controller = F5JobController(
        runner=SimpleNamespace(start=lambda _request: handle, shutdown=lambda: None),
        installer=SimpleNamespace(prune_after_terminal=lambda _result: None),
        poll_interval_ms=50,
    )
    controller.start(request)
    assert handle.polled.wait(1)

    assert controller.shutdown(timeout_ms=1_000) is True

    assert handle.running is False
    assert handle.reaped is True
    assert handle.result_calls == 1
    assert {thread_id for _, thread_id in handle.calls} == {
        next(thread_id for _, thread_id in handle.calls)
    }
    assert all(thread_id != gui_thread_id for _, thread_id in handle.calls)
    assert controller.last_result.status is F5JobStatus.CANCELLED
    assert controller.last_result.error_message == "owner_shutdown"


def test_controller_keeps_terminal_diagnostics_for_runtime_probe(tmp_path):
    request = _request(tmp_path)
    result = F5JobResult.failed(request, error_code="probe", error_message="expected")
    handle = _ReadyHandle(result)
    handle.process = SimpleNamespace(pid=54321)
    finished = []
    controller = F5JobController(
        runner=SimpleNamespace(start=lambda _request: handle),
        installer=SimpleNamespace(prune_after_terminal=lambda _result: None),
    )

    assert controller.start(request, on_finished=finished.append) is True
    _wait_for_worker_monitor(controller)

    assert finished == [result]
    assert controller.last_request == request
    assert controller.last_result == result
    assert controller.last_worker_pid == 54321


def test_controller_shutdown_cancels_inflight_activation_before_publish(tmp_path):
    request = _request(tmp_path)
    ready = _ready_result(request)
    started = threading.Event()
    published = []

    class _Installer:
        @staticmethod
        def activate(result, *, cancelled_checker):
            started.set()
            while not cancelled_checker():
                time.sleep(0.005)
            return replace(result, status=F5JobStatus.CANCELLED, error_code="activation_cancelled")

        @staticmethod
        def prune_after_terminal(result):
            if result.status is F5JobStatus.SUCCEEDED:
                published.append(result)

    controller = F5JobController(
        runner=SimpleNamespace(start=lambda _request: _ReadyHandle(ready), shutdown=lambda: None),
        installer=_Installer(),
    )
    controller.start(request)
    _wait_for_worker_monitor(controller)
    assert started.wait(1)

    assert controller.shutdown(timeout_ms=1_000) is True
    assert published == []


def test_noncooperative_validation_timeout_cannot_publish_after_close(tmp_path, monkeypatch):
    request = _request(tmp_path)
    ready = _ready_result(request)
    provider = _parent_provider(tmp_path)
    engine = _ParentEngine()
    installer = F5SnapshotInstaller(
        data_provider=provider,
        engine=engine,
        database_path=str(tmp_path / "manifest.db"),
        cache_dir=request.cache_dir,
    )
    entered = threading.Event()
    release = threading.Event()
    market = WarehouseReadResult(
        {"new": object()},
        WarehouseStatus(ok=True, trade_date="20260714", symbol_count=2, row_count=120),
    )
    bundle = installer_module._ValidatedBundle(market=market, rps120={"new": 90}, rps250={"new": 80})

    def _blocked_load(_result):
        entered.set()
        release.wait(5)
        return bundle

    monkeypatch.setattr(installer, "_load_validated_bundle", _blocked_load)
    controller = F5JobController(
        runner=SimpleNamespace(start=lambda _request: _ReadyHandle(ready), shutdown=lambda: None),
        installer=installer,
    )
    controller.start(request)
    _wait_for_worker_monitor(controller)
    assert entered.wait(1)
    future = controller._activation_future
    assert controller._activation_thread.daemon is True

    assert controller.shutdown(timeout_ms=10) is False
    release.set()
    result = future.result(timeout=2)

    assert result.status is F5JobStatus.CANCELLED
    assert installer.repository.active() is None
    assert engine.payload["date"] == "20260713"


def test_unexpected_activation_thread_crash_becomes_terminal_failure(tmp_path):
    request = _request(tmp_path)
    ready = _ready_result(request)
    finished = []
    installer = SimpleNamespace(
        activate=lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("boom")),
        prune_after_terminal=lambda _result: None,
    )
    controller = F5JobController(
        runner=SimpleNamespace(start=lambda _request: _ReadyHandle(ready)),
        installer=installer,
    )
    controller.start(request, on_finished=finished.append)
    _wait_for_worker_monitor(controller)
    controller._activation_thread.join(timeout=1)
    controller._poll()

    assert finished[0].status is F5JobStatus.FAILED
    assert finished[0].error_code == "activation_worker_failed"


def test_shutdown_contains_unexpected_activation_future_exception(tmp_path):
    request = _request(tmp_path)
    ready = _ready_result(request)
    generation = Path(request.snapshot_dir)
    generation.mkdir(parents=True)
    artifact = generation / "artifact.tmp"
    artifact.write_text("ready", encoding="utf-8")
    cleanup_done = threading.Event()
    cleanup_results = []

    def _cleanup(result):
        cleanup_results.append(result)
        artifact.unlink()
        generation.rmdir()
        cleanup_done.set()

    installer = SimpleNamespace(
        activate=lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("boom")),
        prune_after_terminal=_cleanup,
    )
    finished = []
    controller = F5JobController(
        runner=SimpleNamespace(start=lambda _request: _ReadyHandle(ready), shutdown=lambda: None),
        installer=installer,
    )
    controller.start(request, on_finished=finished.append)
    _wait_for_worker_monitor(controller)
    controller._activation_thread.join(timeout=1)

    assert controller.shutdown(timeout_ms=100) is True
    assert cleanup_done.is_set()
    persisted = F5JobResult.from_dict(F5JobRepository(request.job_dir).read_result())
    assert persisted.status is F5JobStatus.FAILED
    assert persisted.error_code == "activation_worker_failed"
    assert finished == cleanup_results
    assert not generation.exists()
    assert controller._request is None
    assert controller._activation_future is None


def test_shutdown_waits_for_owned_terminal_cleanup_thread(tmp_path):
    request = _request(tmp_path)
    cleanup_started = threading.Event()
    cleanup_release = threading.Event()
    cleanup_done = threading.Event()

    def _cleanup(_result):
        cleanup_started.set()
        cleanup_release.wait(2)
        cleanup_done.set()

    controller = F5JobController(
        runner=SimpleNamespace(shutdown=lambda: None),
        installer=SimpleNamespace(prune_after_terminal=_cleanup),
    )
    controller._request = request
    controller._started_at = time.monotonic()
    controller._finish(F5JobResult.failed(request, error_code="failed", error_message="failed"))
    assert cleanup_started.wait(1)
    assert controller.is_running is True

    assert controller.shutdown(timeout_ms=10) is False
    assert cleanup_done.is_set() is False

    cleanup_release.set()

    assert controller.shutdown(timeout_ms=1_000) is True
    assert cleanup_done.is_set() is True
    assert controller.is_running is False


def test_shutdown_terminalizes_pending_activation_future(tmp_path):
    request = _request(tmp_path)
    ready = _ready_result(request)
    generation = Path(request.snapshot_dir)
    generation.mkdir(parents=True)
    artifact = generation / "artifact.tmp"
    artifact.write_text("ready", encoding="utf-8")
    cleanup_done = threading.Event()

    def _cleanup(_result):
        artifact.unlink()
        generation.rmdir()
        cleanup_done.set()

    controller = F5JobController(
        runner=SimpleNamespace(start=lambda _request: _ReadyHandle(ready), shutdown=lambda: None),
        installer=SimpleNamespace(prune_after_terminal=_cleanup),
    )
    controller.start(request)
    pending_future = Future()
    controller._activation_future = pending_future

    assert controller.shutdown(timeout_ms=100) is True
    assert pending_future.cancelled()
    assert cleanup_done.wait(1)
    persisted = F5JobResult.from_dict(F5JobRepository(request.job_dir).read_result())
    assert persisted.status is F5JobStatus.CANCELLED
    assert persisted.error_message == "owner_shutdown"
    assert not generation.exists()
    assert controller._request is None
    assert controller._activation_future is None


def test_shutdown_terminalizes_ready_worker_before_poll(tmp_path):
    request = _request(tmp_path)
    ready = _ready_result(request)
    F5JobRepository(request.job_dir).write_result(ready.to_dict())
    generation = Path(request.snapshot_dir)
    generation.mkdir(parents=True)
    artifact = generation / "artifact.tmp"
    artifact.write_text("ready", encoding="utf-8")
    cleanup_done = threading.Event()

    def _cleanup(_result):
        artifact.unlink()
        generation.rmdir()
        cleanup_done.set()

    controller = F5JobController(
        runner=SimpleNamespace(start=lambda _request: _ReadyHandle(ready), shutdown=lambda: None),
        installer=SimpleNamespace(prune_after_terminal=_cleanup),
    )
    controller.start(request)

    assert controller.shutdown(timeout_ms=100) is True
    assert cleanup_done.wait(1)
    persisted = F5JobResult.from_dict(F5JobRepository(request.job_dir).read_result())
    assert persisted.status is F5JobStatus.CANCELLED
    assert persisted.error_message == "owner_shutdown"
    assert not generation.exists()
    assert controller._request is None
    assert controller._handle is None


def test_atomic_manifest_and_retention_keep_active_plus_previous(tmp_path):
    cache_dir = tmp_path / "cache"
    manifest = WarehouseManifest(tmp_path / "manifest.db")
    repository = F5SnapshotRepository(manifest)
    run_ids = [character * 32 for character in "abc"]
    snapshots = [
        _snapshot(run_id, cache_dir, created_at=f"2026-07-1{index}T00:00:00")
        for index, run_id in enumerate(run_ids, start=1)
    ]
    for snapshot in snapshots:
        repository.publish(snapshot=snapshot, market_record=_market_record(snapshot))

    fixed_rps = cache_dir / "fixed-rps.json"
    fixed_rps.write_text('{"date":"legacy"}', encoding="utf-8")
    assert repository.resolve_rps_path(str(fixed_rps)) == snapshots[2].rps_path

    old_job = cache_dir / "f5_jobs" / run_ids[0]
    old_job.mkdir(parents=True)
    (old_job / "result.json").write_text("{}", encoding="utf-8")
    current_job = cache_dir / "f5_jobs" / run_ids[2]
    current_job.mkdir(parents=True)
    report = prune_f5_runtime(
        cache_dir,
        keep_job_ids={run_ids[2]},
        repository=repository,
        now=time.time() + 10 * 60,
    )

    assert repository.active().snapshot_id == run_ids[2]
    assert manifest.latest(MARKET_DATASET).parquet_path == snapshots[2].market_parquet_path
    assert not (cache_dir / "f5_generations" / run_ids[0]).exists()
    assert (cache_dir / "f5_generations" / run_ids[1]).is_dir()
    assert (cache_dir / "f5_generations" / run_ids[2]).is_dir()
    assert report == {"removed_generations": 1, "removed_jobs": 1}
    assert discard_failed_f5_generation(cache_dir, run_ids[2], repository=repository) is False

    failed = _snapshot("d" * 32, cache_dir, created_at="2026-07-15T00:00:00")
    repository.publish(snapshot=failed, market_record=_market_record(failed))
    repository.restore_active_pointers(snapshot=snapshots[2], market_record=_market_record(snapshots[2]))
    assert discard_failed_f5_generation(cache_dir, failed.snapshot_id, repository=repository) is True
    prune_f5_runtime(cache_dir, repository=repository)
    assert (cache_dir / "f5_generations" / run_ids[1]).is_dir()
    assert (cache_dir / "f5_generations" / run_ids[2]).is_dir()


def test_startup_retention_keeps_ready_job_and_removes_terminal_jobs(tmp_path):
    cache_dir = tmp_path / "cache"
    repository = F5SnapshotRepository(WarehouseManifest(tmp_path / "manifest.db"))
    run_ids = {status: character * 32 for status, character in zip(("ready_to_activate", "failed", "cancelled"), "abc")}
    for status, run_id in run_ids.items():
        job_dir = cache_dir / "f5_jobs" / run_id
        generation = cache_dir / "f5_generations" / run_id
        job_dir.mkdir(parents=True)
        generation.mkdir(parents=True)
        (job_dir / "result.json").write_text(json.dumps({"status": status}), encoding="utf-8")
        (generation / "artifact.tmp").write_text(status, encoding="utf-8")

    report = prune_f5_runtime(cache_dir, repository=repository)

    assert report["removed_jobs"] == 2
    assert (cache_dir / "f5_jobs" / run_ids["ready_to_activate"]).is_dir()
    assert not (cache_dir / "f5_jobs" / run_ids["failed"]).exists()
    assert not (cache_dir / "f5_jobs" / run_ids["cancelled"]).exists()
    assert (cache_dir / "f5_generations" / run_ids["ready_to_activate"]).is_dir()
    assert report == {"removed_generations": 2, "removed_jobs": 2}


def test_f5_runtime_inspection_accepts_active_previous_and_terminal_jobs(tmp_path):
    cache_dir = tmp_path / "cache"
    repository = F5SnapshotRepository(WarehouseManifest(tmp_path / "manifest.db"))
    snapshots = [
        _snapshot(character * 32, cache_dir, created_at=f"2026-07-1{index}T00:00:00")
        for index, character in enumerate("ab", start=1)
    ]
    for snapshot in snapshots:
        repository.publish(snapshot=snapshot, market_record=_market_record(snapshot))

    request = _request(tmp_path, run_id="c" * 32)
    terminal = F5JobResult.cancelled(request, reason="owner_shutdown")
    F5JobRepository(request.job_dir).write_result(terminal.to_dict())

    receipt = inspect_f5_runtime(cache_dir, repository=repository)

    assert receipt["clean"] is True
    assert receipt["active_snapshot_id"] == snapshots[-1].snapshot_id
    assert receipt["generation_ids"] == [snapshots[0].snapshot_id, snapshots[1].snapshot_id]
    assert receipt["generation_count"] == 2
    assert receipt["terminal_job_ids"] == [request.run_id]
    assert receipt["unfinished_job_ids"] == []
    assert receipt["ready_to_activate_job_ids"] == []
    assert receipt["temporary_files"] == []


def test_f5_runtime_inspection_ignores_stale_manifest_without_retained_generation(tmp_path):
    cache_dir = tmp_path / "cache"
    repository = F5SnapshotRepository(WarehouseManifest(tmp_path / "manifest.db"))
    stale = _snapshot("a" * 32, cache_dir, created_at="2026-07-13T00:00:00")
    active = _snapshot("b" * 32, cache_dir, created_at="2026-07-14T00:00:00")
    repository.publish(snapshot=stale, market_record=_market_record(stale))
    repository.publish(snapshot=active, market_record=_market_record(active))
    shutil.rmtree(cache_dir / "f5_generations" / stale.snapshot_id)

    receipt = inspect_f5_runtime(cache_dir, repository=repository)

    assert receipt["clean"] is True
    assert receipt["active_snapshot_id"] == active.snapshot_id
    assert receipt["previous_snapshot_id"] == ""
    assert receipt["previous_snapshot_integrity_ok"] is True
    assert receipt["integrity_mismatch_paths"] == []


def test_f5_runtime_inspection_fails_closed_for_orphans_unfinished_jobs_and_temp_files(tmp_path):
    cache_dir = tmp_path / "cache"
    repository = F5SnapshotRepository(WarehouseManifest(tmp_path / "manifest.db"))
    active = _snapshot("a" * 32, cache_dir, created_at="2026-07-14T00:00:00")
    repository.publish(snapshot=active, market_record=_market_record(active))

    orphan_id = "b" * 32
    orphan = cache_dir / "f5_generations" / orphan_id
    orphan.mkdir(parents=True)
    (orphan / "market.parquet").write_text("partial", encoding="utf-8")
    (orphan / "market.parquet.123.tmp").write_text("partial", encoding="utf-8")

    unfinished_id = "c" * 32
    (cache_dir / "f5_jobs" / unfinished_id).mkdir(parents=True)
    ready_request = _request(tmp_path, run_id="d" * 32)
    F5JobRepository(ready_request.job_dir).write_result(_ready_result(ready_request).to_dict())
    invalid_result_id = "e" * 32
    invalid_result_dir = cache_dir / "f5_jobs" / invalid_result_id
    invalid_result_dir.mkdir(parents=True)
    (invalid_result_dir / "result.json").write_text("[]", encoding="utf-8")
    (cache_dir / "f5_jobs" / "not-a-run-id").mkdir(parents=True)

    receipt = inspect_f5_runtime(cache_dir, repository=repository)

    assert receipt["clean"] is False
    assert receipt["unexpected_generation_ids"] == [orphan_id]
    assert receipt["incomplete_generation_ids"] == [orphan_id]
    assert receipt["unfinished_job_ids"] == [unfinished_id]
    assert receipt["ready_to_activate_job_ids"] == [ready_request.run_id]
    assert receipt["invalid_job_ids"] == [invalid_result_id]
    assert receipt["invalid_job_entries"] == ["not-a-run-id"]
    assert receipt["temporary_file_count"] == 1


def test_f5_runtime_inspection_rejects_terminal_job_accumulation(tmp_path):
    cache_dir = tmp_path / "cache"
    repository = F5SnapshotRepository(WarehouseManifest(tmp_path / "manifest.db"))
    for index, character in enumerate("abc", start=1):
        request = _request(tmp_path, run_id=character * 32)
        result = F5JobResult.cancelled(request, reason=f"terminal-{index}")
        F5JobRepository(request.job_dir).write_result(result.to_dict())

    receipt = inspect_f5_runtime(cache_dir, repository=repository)

    assert receipt["clean"] is False
    assert receipt["job_count"] == 3
    assert receipt["terminal_job_ids"] == [character * 32 for character in "abc"]


def test_f5_runtime_inspection_fails_closed_for_active_and_previous_content_damage(tmp_path):
    cache_dir = tmp_path / "cache"
    repository = F5SnapshotRepository(WarehouseManifest(tmp_path / "manifest.db"))
    previous = _snapshot("a" * 32, cache_dir, created_at="2026-07-13T00:00:00")
    active = _snapshot("b" * 32, cache_dir, created_at="2026-07-14T00:00:00")
    repository.publish(snapshot=previous, market_record=_market_record(previous))
    repository.publish(snapshot=active, market_record=_market_record(active))
    Path(active.rps_path).write_text('{"tampered":true}', encoding="utf-8")
    Path(previous.sector_rps_path).write_text('{"tampered":true}', encoding="utf-8")

    receipt = inspect_f5_runtime(cache_dir, repository=repository)

    assert receipt["clean"] is False
    assert receipt["active_snapshot_integrity_ok"] is False
    assert receipt["previous_snapshot_id"] == previous.snapshot_id
    assert receipt["previous_snapshot_integrity_ok"] is False
    assert receipt["integrity_mismatch_paths"] == sorted(
        [active.rps_path, previous.sector_rps_path]
    )


def test_installer_rolls_back_manifest_memory_and_rps_on_install_failure(tmp_path, monkeypatch):
    request = _request(tmp_path, run_id="b" * 32)
    cache_dir = Path(request.cache_dir)
    old_snapshot = _snapshot("a" * 32, cache_dir, created_at="2026-07-13T00:00:00")
    provider = SimpleNamespace(
        cache_lock=threading.RLock(),
        cache_data={"old": object()},
        _market_data_snapshot_trade_date="20260713",
        _last_market_data_source_status={"old": True},
    )

    class _Engine:
        def __init__(self):
            self.payload = {"date": "20260713", "rps120": {"old": 1}, "rps250": {"old": 1}}

        def get_precomputed_rps(self):
            return self.payload

        def set_precomputed_rps(self, date, rps120, rps250):
            if date == "20260714":
                raise RuntimeError("install failed")
            self.payload = {"date": date, "rps120": rps120, "rps250": rps250}

    engine = _Engine()
    installer = F5SnapshotInstaller(
        data_provider=provider,
        engine=engine,
        database_path=str(tmp_path / "manifest.db"),
        cache_dir=str(cache_dir),
    )
    installer.repository.publish(snapshot=old_snapshot, market_record=_market_record(old_snapshot))
    market = WarehouseReadResult(
        {"new": object()},
        WarehouseStatus(ok=True, trade_date="20260714", symbol_count=2, row_count=120),
    )
    bundle = installer_module._ValidatedBundle(market=market, rps120={"new": 90}, rps250={"new": 80})
    monkeypatch.setattr(installer, "_load_validated_bundle", lambda _result: bundle)

    result = installer.activate(_ready_result(request))

    assert result.status is F5JobStatus.FAILED
    assert installer.repository.active().snapshot_id == old_snapshot.snapshot_id
    assert provider.cache_data.keys() == {"old"}
    assert provider._market_data_snapshot_trade_date == "20260713"
    assert engine.payload["date"] == "20260713"
    assert installer.manifest.latest(MARKET_DATASET).parquet_path == old_snapshot.market_parquet_path
    assert installer.repository.get(request.run_id) is None


def test_installer_same_trade_date_rollback_restores_old_market_path(tmp_path, monkeypatch):
    request = _request(tmp_path, run_id="b" * 32)
    cache_dir = Path(request.cache_dir)
    old_snapshot = _snapshot("a" * 32, cache_dir, created_at="2026-07-14T08:00:00")
    provider = _parent_provider(tmp_path)
    provider._market_data_snapshot_trade_date = "20260714"
    engine = _ParentEngine()
    engine.payload["date"] = "20260714"
    installer = F5SnapshotInstaller(
        data_provider=provider,
        engine=engine,
        database_path=str(tmp_path / "manifest.db"),
        cache_dir=str(cache_dir),
    )
    old_market = _market_record(old_snapshot)
    installer.repository.publish(snapshot=old_snapshot, market_record=old_market)
    market = WarehouseReadResult(
        {"new": object()},
        WarehouseStatus(ok=True, trade_date="20260714", symbol_count=2, row_count=120),
    )
    bundle = installer_module._ValidatedBundle(market=market, rps120={"new": 90}, rps250={"new": 80})
    monkeypatch.setattr(installer, "_load_validated_bundle", lambda _result: bundle)
    monkeypatch.setattr(
        installer,
        "_install_parent_memory",
        lambda _result, _bundle: (_ for _ in ()).throw(RuntimeError("install failed")),
    )

    result = installer.activate(_ready_result(request))

    restored_market = installer.manifest.get(MARKET_DATASET, "20260714")
    assert result.status is F5JobStatus.FAILED
    assert installer.repository.active().snapshot_id == old_snapshot.snapshot_id
    assert restored_market is not None
    assert restored_market.parquet_path == old_snapshot.market_parquet_path
    assert installer.manifest.latest(MARKET_DATASET).parquet_path == old_snapshot.market_parquet_path
    assert installer.repository.get(request.run_id) is None


def test_installer_full_success_publishes_market_rps_and_gbbq(tmp_path, monkeypatch):
    request = _request(tmp_path)
    ready = _stage_ready_generation(request, include_gbbq=True)
    provider = _parent_provider(tmp_path)
    engine = _ParentEngine()
    installer = F5SnapshotInstaller(
        data_provider=provider,
        engine=engine,
        database_path=str(tmp_path / "manifest.db"),
        cache_dir=request.cache_dir,
    )
    monkeypatch.setattr(installer, "_update_compatibility_mirrors", lambda _snapshot: None)
    monkeypatch.setattr(installer, "_prune_runtime", lambda _run_id: None)

    activated = installer.activate(ready)

    assert activated.status is F5JobStatus.SUCCEEDED
    active = installer.repository.active()
    assert active.snapshot_id == request.run_id
    assert active.market_sha256 == ready.artifacts.market_sha256
    assert active.rps_sha256 == ready.artifacts.rps_sha256
    assert active.sector_rps_sha256 == ready.artifacts.sector_rps_sha256
    assert active.gbbq_path == ready.artifacts.gbbq_path
    assert active.gbbq_sha256 == ready.artifacts.gbbq_sha256
    assert provider._market_data_snapshot_trade_date == "20260714"
    assert set(provider.cache_data) == {"000001", "600001"}
    assert engine.payload["date"] == "20260714"
    assert json.loads(Path(provider.gbbq_cache_file).read_text(encoding="utf-8"))["mtime"] == 2.0
    assert provider._local_gbbq_loaded is False


def test_installer_rejects_memory_pressure_before_loading_next_full_market_bundle(tmp_path, monkeypatch):
    request = _request(tmp_path)
    ready = _stage_ready_generation(request)
    provider = _parent_provider(tmp_path)
    installer = F5SnapshotInstaller(
        data_provider=provider,
        engine=_ParentEngine(),
        database_path=str(tmp_path / "manifest.db"),
        cache_dir=request.cache_dir,
    )
    pressure = F5MemoryPressureError(
        stage="F5 快照激活",
        headroom_bytes=128 * 1024**2,
        minimum_bytes=2 * 1024**3,
    )
    loaded = []
    monkeypatch.setattr(
        installer_module,
        "ensure_f5_commit_headroom",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(pressure),
    )
    monkeypatch.setattr(installer, "_load_validated_bundle", lambda _result: loaded.append(True))

    result = installer.activate(ready)

    assert result.status is F5JobStatus.FAILED
    assert result.error_code == "insufficient_memory_headroom"
    assert loaded == []


def test_installer_rechecks_memory_after_loading_next_full_market_bundle(tmp_path, monkeypatch):
    request = _request(tmp_path)
    ready = _stage_ready_generation(request)
    provider = _parent_provider(tmp_path)
    installer = F5SnapshotInstaller(
        data_provider=provider,
        engine=_ParentEngine(),
        database_path=str(tmp_path / "manifest.db"),
        cache_dir=request.cache_dir,
    )
    bundle = installer_module._ValidatedBundle(
        market=WarehouseReadResult(
            {"new": object()},
            WarehouseStatus(ok=True, trade_date="20260714", symbol_count=1, row_count=60),
        ),
        rps120={"new": 90},
        rps250={"new": 80},
    )
    calls = []
    pressure = F5MemoryPressureError(
        stage="F5 快照激活提交",
        headroom_bytes=128 * 1024**2,
        minimum_bytes=2 * 1024**3,
    )

    def _guard(_minimum_bytes, *, stage):
        calls.append(stage)
        if stage == "F5 快照激活提交":
            raise pressure

    monkeypatch.setattr(installer_module, "ensure_f5_commit_headroom", _guard)
    monkeypatch.setattr(installer, "_load_validated_bundle", lambda _result: bundle)

    result = installer.activate(ready)

    assert result.status is F5JobStatus.FAILED
    assert result.error_code == "insufficient_memory_headroom"
    assert calls == ["F5 快照激活加载", "F5 快照激活提交"]
    assert installer.repository.active() is None


def test_activation_gate_never_exposes_mixed_pointer_memory_and_rps(tmp_path, monkeypatch):
    request = _request(tmp_path)
    ready = _stage_ready_generation(request)
    provider = _parent_provider(tmp_path)
    engine = _ParentEngine()
    installer = F5SnapshotInstaller(
        data_provider=provider,
        engine=engine,
        database_path=str(tmp_path / "manifest.db"),
        cache_dir=request.cache_dir,
    )
    monkeypatch.setattr(installer, "_update_compatibility_mirrors", lambda _snapshot: None)
    monkeypatch.setattr(installer, "_prune_runtime", lambda _run_id: None)
    publish = installer.repository.publish
    published = threading.Event()
    release = threading.Event()

    def _blocking_publish(**kwargs):
        publish(**kwargs)
        published.set()
        release.wait(2)

    monkeypatch.setattr(installer.repository, "publish", _blocking_publish)
    activation = threading.Thread(target=lambda: installer.activate(ready))
    activation.start()
    assert published.wait(1)
    observed = []
    read_done = threading.Event()

    def _read_consistent_state():
        with f5_snapshot_read_boundary():
            observed.append(
                (
                    installer.repository.active().snapshot_id,
                    provider._market_data_snapshot_trade_date,
                    engine.payload["date"],
                )
            )
        read_done.set()

    reader = threading.Thread(target=_read_consistent_state)
    reader.start()
    assert not read_done.wait(0.05)
    release.set()
    activation.join(timeout=2)
    reader.join(timeout=2)

    assert observed == [(request.run_id, "20260714", "20260714")]


@pytest.mark.parametrize("artifact_name", ["market", "rps", "sector_rps", "gbbq"])
def test_installer_rejects_same_shape_content_tampering_before_publish(tmp_path, artifact_name):
    request = _request(tmp_path)
    ready = _stage_ready_generation(request, include_gbbq=True)
    generation = Path(request.snapshot_dir)
    if artifact_name == "market":
        import polars as pl

        market_path = generation / "market.parquet"
        frame = pl.read_parquet(market_path).with_columns((pl.col("close") + 1000).alias("close"))
        frame.write_parquet(market_path)
    elif artifact_name == "rps":
        path = generation / "rps.json"
        payload = json.loads(path.read_text(encoding="utf-8"))
        payload["rps120"]["000001"] = 91
        path.write_text(json.dumps(payload), encoding="utf-8")
    elif artifact_name == "sector_rps":
        path = generation / "sector_rps.json"
        payload = json.loads(path.read_text(encoding="utf-8"))
        payload["sector_rps"]["AI"]["5"] = 98
        path.write_text(json.dumps(payload), encoding="utf-8")
    else:
        path = generation / "gbbq.json"
        payload = json.loads(path.read_text(encoding="utf-8"))
        payload["data"] = {"600001": []}
        path.write_text(json.dumps(payload), encoding="utf-8")

    old_snapshot = _snapshot("b" * 32, Path(request.cache_dir), created_at="2026-07-13T00:00:00")
    provider = _parent_provider(tmp_path)
    engine = _ParentEngine()
    installer = F5SnapshotInstaller(
        data_provider=provider,
        engine=engine,
        database_path=str(tmp_path / "manifest.db"),
        cache_dir=request.cache_dir,
    )
    installer.repository.publish(snapshot=old_snapshot, market_record=_market_record(old_snapshot))

    rejected = installer.activate(ready)

    assert rejected.status is F5JobStatus.FAILED
    assert rejected.error_code == "snapshot_integrity_mismatch"
    assert installer.repository.active().snapshot_id == old_snapshot.snapshot_id
    assert set(provider.cache_data) == {"old"}
    assert provider._market_data_snapshot_trade_date == "20260713"
    assert engine.payload["date"] == "20260713"
    assert not generation.exists()


@pytest.mark.parametrize(
    ("case", "expected_code"),
    [
        ("missing_rps", "snapshot_integrity_mismatch"),
        ("corrupt_rps", "snapshot_activation_failed"),
        ("empty_rps250", "snapshot_activation_failed"),
        ("wrong_rps250_count", "snapshot_activation_failed"),
        ("market_date_mismatch", "snapshot_activation_failed"),
        ("wrong_path", "activation_contract_invalid"),
        ("zero_count", "activation_contract_invalid"),
        ("malformed_count", "activation_contract_invalid"),
        ("date_mismatch", "activation_contract_invalid"),
        ("bad_digest", "activation_contract_invalid"),
        ("bad_size", "activation_contract_invalid"),
        ("malformed_integrity_size", "activation_contract_invalid"),
    ],
)
def test_installer_rejects_missing_empty_corrupt_or_mismatched_artifacts(
    tmp_path,
    monkeypatch,
    case,
    expected_code,
):
    request = _request(tmp_path)
    market_date = "20260713" if case == "market_date_mismatch" else "20260714"
    ready = _stage_ready_generation(request, market_date=market_date)
    generation = Path(request.snapshot_dir)
    if case == "missing_rps":
        (generation / "rps.json").unlink()
    elif case == "corrupt_rps":
        (generation / "rps.json").write_text("{broken", encoding="utf-8")
    elif case == "empty_rps250":
        (generation / "rps.json").write_text(
            json.dumps({"date": "20260714", "rps120": {"000001": 90, "600001": 80}, "rps250": {}}),
            encoding="utf-8",
        )
    elif case == "wrong_rps250_count":
        (generation / "rps.json").write_text(
            json.dumps(
                {
                    "date": "20260714",
                    "rps120": {"000001": 90, "600001": 80},
                    "rps250": {"000001": 70, "600001": None},
                }
            ),
            encoding="utf-8",
        )
    elif case == "wrong_path":
        ready = replace(ready, artifacts=replace(ready.artifacts, rps_path=str(tmp_path / "outside.json")))
    elif case == "zero_count":
        ready = replace(ready, artifacts=replace(ready.artifacts, rps_valid_count=0))
    elif case == "malformed_count":
        ready = replace(ready, artifacts=replace(ready.artifacts, market_row_count="bad"))
    elif case == "date_mismatch":
        ready = replace(ready, artifacts=replace(ready.artifacts, effective_trade_date="20260713"))
    elif case == "bad_digest":
        ready = replace(ready, artifacts=replace(ready.artifacts, market_sha256="not-a-sha256"))
    elif case == "bad_size":
        ready = replace(ready, artifacts=replace(ready.artifacts, market_size_bytes=0))
    elif case == "malformed_integrity_size":
        ready = replace(ready, artifacts=replace(ready.artifacts, market_size_bytes="1"))
    if case in {"corrupt_rps", "empty_rps250", "wrong_rps250_count"}:
        fingerprint = fingerprint_file(generation / "rps.json")
        ready = replace(
            ready,
            artifacts=replace(
                ready.artifacts,
                rps_size_bytes=fingerprint.size_bytes,
                rps_sha256=fingerprint.sha256,
            ),
        )
    provider = _parent_provider(tmp_path)
    installer = F5SnapshotInstaller(
        data_provider=provider,
        engine=_ParentEngine(),
        database_path=str(tmp_path / "manifest.db"),
        cache_dir=request.cache_dir,
    )
    monkeypatch.setattr(installer, "_update_compatibility_mirrors", lambda _snapshot: None)

    rejected = installer.activate(ready)

    assert rejected.status is F5JobStatus.FAILED
    assert rejected.error_code == expected_code
    assert installer.repository.active() is None
    assert set(provider.cache_data) == {"old"}
    assert not generation.exists()


def test_unexpected_install_error_rolls_back_gbbq_memory_and_manifest(tmp_path, monkeypatch):
    request = _request(tmp_path)
    ready = _stage_ready_generation(request, include_gbbq=True)
    provider = _parent_provider(tmp_path)
    old_gbbq = Path(provider.gbbq_cache_file).read_bytes()
    engine = _ParentEngine()
    installer = F5SnapshotInstaller(
        data_provider=provider,
        engine=engine,
        database_path=str(tmp_path / "manifest.db"),
        cache_dir=request.cache_dir,
    )
    install_parent = installer._install_parent_memory

    def _install_then_crash(result, bundle):
        install_parent(result, bundle)
        raise AssertionError("unexpected install crash")

    monkeypatch.setattr(installer, "_install_parent_memory", _install_then_crash)

    rejected = installer.activate(ready)

    assert rejected.status is F5JobStatus.FAILED
    assert installer.repository.active() is None
    assert installer.manifest.latest(MARKET_DATASET) is None
    assert installer.repository.get(request.run_id) is None
    assert Path(provider.gbbq_cache_file).read_bytes() == old_gbbq
    assert provider._local_gbbq_loaded is True
    assert set(provider._local_gbbq) == {"old"}
    assert engine.payload["date"] == "20260713"
