# -*- coding: utf-8 -*-
from __future__ import annotations

import logging
from pathlib import Path

import pytest

from app.services.f5_job_contract import F5JobEvent, F5JobRequest, F5JobResult, F5JobStatus, F5Phase
from app.workers import f5_worker_main as worker
from core import rps_precomputer as rps_module
from infra.storage.f5_job_repository import F5JobRepository


def _request(tmp_path: Path, *, timeout_seconds: float = 2.0) -> F5JobRequest:
    return F5JobRequest.build(
        project_root=str(tmp_path),
        data_dir=str(tmp_path / "data"),
        cache_dir=str(tmp_path / "cache"),
        tdx_vipdoc=str(tmp_path / "vipdoc"),
        requested_date="20260716",
        timeout_seconds=timeout_seconds,
    )


def _result(request: F5JobRequest, status: F5JobStatus) -> F5JobResult:
    return F5JobResult(
        run_id=request.run_id,
        status=status,
        requested_date=request.requested_date,
    )


def test_worker_logging_uses_job_local_utf8_file(monkeypatch, tmp_path):
    repository = F5JobRepository(tmp_path / "job")
    captured = {}
    handler = logging.NullHandler()
    root_logger = logging.getLogger()
    original_level = root_logger.level
    monkeypatch.setattr(
        worker.logging,
        "FileHandler",
        lambda path, *, encoding: captured.update(path=path, encoding=encoding) or handler,
    )
    attached = []
    monkeypatch.setattr(worker, "attach_shared_log_handler", attached.append)

    try:
        worker._configure_worker_logging(repository)
    finally:
        root_logger.removeHandler(handler)
        root_logger.setLevel(original_level)

    assert captured["path"] == repository.log_path
    assert captured["encoding"] == "utf-8"
    assert handler.formatter is not None
    assert "%(asctime)s" in handler.formatter._fmt
    assert attached == [handler]


def test_execute_request_publishes_progress_and_ready_result(monkeypatch, tmp_path):
    request = _request(tmp_path)
    repository = F5JobRepository(request.job_dir)

    def _run(request_arg, *, cancelled_checker, event_callback):
        assert request_arg is request
        assert cancelled_checker() is False
        event_callback(F5JobEvent(request.run_id, 1, F5Phase.PREPARE, "准备完成"))
        return _result(request, F5JobStatus.READY_TO_ACTIVATE)

    monkeypatch.setattr(rps_module.RPSPrecomputer, "run_f5_job", staticmethod(_run))

    result = worker.execute_request(request, repository)

    assert result.status is F5JobStatus.READY_TO_ACTIVATE
    assert F5JobResult.from_dict(repository.read_result()).status is F5JobStatus.READY_TO_ACTIVATE
    events, _ = repository.read_events()
    assert [event["message"] for event in events] == ["准备完成"]


def test_execute_request_preserves_cancel_reason_and_discards_generation(monkeypatch, tmp_path):
    request = _request(tmp_path)
    repository = F5JobRepository(request.job_dir)
    repository.request_cancel("operator_cancelled")
    discarded = []

    def _run(_request, *, cancelled_checker, event_callback):
        assert cancelled_checker() is True
        assert event_callback is not None
        return F5JobResult.cancelled(request)

    monkeypatch.setattr(rps_module.RPSPrecomputer, "run_f5_job", staticmethod(_run))
    monkeypatch.setattr(worker, "_discard_failed_generation", discarded.append)

    result = worker.execute_request(request, repository)

    assert result.status is F5JobStatus.CANCELLED
    assert result.error_code == "cancelled"
    assert result.error_message == "operator_cancelled"
    assert discarded == [request]


def test_execute_request_converts_deadline_and_worker_crash_to_terminal_results(monkeypatch, tmp_path):
    deadline_request = _request(tmp_path / "deadline", timeout_seconds=1)
    deadline_repository = F5JobRepository(deadline_request.job_dir)
    ticks = iter((10.0, 12.0))
    discarded = []
    real_monotonic = worker.time.monotonic
    monkeypatch.setattr(worker.time, "monotonic", lambda: next(ticks))

    def _cancelled(_request, *, cancelled_checker, event_callback):
        assert cancelled_checker() is True
        assert event_callback is not None
        return F5JobResult.cancelled(deadline_request)

    monkeypatch.setattr(rps_module.RPSPrecomputer, "run_f5_job", staticmethod(_cancelled))
    monkeypatch.setattr(worker, "_discard_failed_generation", discarded.append)

    deadline_result = worker.execute_request(deadline_request, deadline_repository)

    assert deadline_result.error_code == "deadline_exceeded"
    assert deadline_result.error_message == "deadline_exceeded"
    monkeypatch.setattr(worker.time, "monotonic", real_monotonic)

    crash_request = _request(tmp_path / "crash")
    crash_repository = F5JobRepository(crash_request.job_dir)
    monkeypatch.setattr(
        rps_module.RPSPrecomputer,
        "run_f5_job",
        staticmethod(lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("deterministic crash"))),
    )

    crash_result = worker.execute_request(crash_request, crash_repository)

    assert crash_result.status is F5JobStatus.FAILED
    assert crash_result.error_code == "worker_crash"
    assert crash_result.error_message == "RuntimeError: deterministic crash"
    assert discarded == [deadline_request, crash_request]


@pytest.mark.parametrize(
    ("exception_factory", "expected_code"),
    [
        (lambda: AssertionError("invariant failed"), "worker_crash"),
        (lambda: MemoryError("out of memory"), "worker_memory_exhausted"),
        (lambda: SystemExit(7), "worker_system_exit"),
        (lambda: KeyboardInterrupt(), "worker_interrupted"),
    ],
)
def test_execute_request_terminalizes_base_exceptions(monkeypatch, tmp_path, exception_factory, expected_code):
    request = _request(tmp_path / expected_code)
    repository = F5JobRepository(request.job_dir)
    discarded = []
    monkeypatch.setattr(
        rps_module.RPSPrecomputer,
        "run_f5_job",
        staticmethod(lambda *_args, **_kwargs: (_ for _ in ()).throw(exception_factory())),
    )
    monkeypatch.setattr(worker, "_discard_failed_generation", discarded.append)

    result = worker.execute_request(request, repository)

    assert result.status is F5JobStatus.FAILED
    assert result.error_code == expected_code
    assert type(exception_factory()).__name__ in result.error_message
    assert F5JobResult.from_dict(repository.read_result()).error_code == expected_code
    assert discarded == [request]


def test_failed_generation_cleanup_is_best_effort(monkeypatch, tmp_path):
    request = _request(tmp_path)
    calls = []
    monkeypatch.setattr(worker, "WarehouseManifest", lambda *, db_path: calls.append(("manifest", db_path)) or "m")
    monkeypatch.setattr(worker, "F5SnapshotRepository", lambda manifest: calls.append(("repository", manifest)) or "r")
    monkeypatch.setattr(
        worker,
        "discard_failed_f5_generation",
        lambda cache_dir, run_id, *, repository: calls.append((cache_dir, run_id, repository)),
    )

    worker._discard_failed_generation(request)

    assert calls == [
        ("manifest", request.database_path),
        ("repository", "m"),
        (request.cache_dir, request.run_id, "r"),
    ]

    monkeypatch.setattr(worker, "WarehouseManifest", lambda **_kwargs: (_ for _ in ()).throw(OSError("locked")))
    worker._discard_failed_generation(request)


def test_worker_main_rejects_missing_and_mismatched_requests(monkeypatch, tmp_path):
    monkeypatch.setattr(worker, "_configure_worker_logging", lambda _repository: None)
    missing_job = tmp_path / "missing-job"
    assert worker.main(["--job-dir", str(missing_job)]) == 1

    request = _request(tmp_path / "mismatch")
    argument_repository = F5JobRepository(tmp_path / "argument-job")
    argument_repository.write_request(request.to_dict())

    assert worker.main(["--job-dir", str(argument_repository.job_dir)]) == 1
    result = F5JobResult.from_dict(argument_repository.read_result())
    assert result.status is F5JobStatus.FAILED
    assert result.error_code == "worker_initialization_failed"
    assert "job_dir does not match" in result.error_message


def test_worker_main_terminalizes_logging_initialization_failure(monkeypatch, tmp_path):
    request = _request(tmp_path / "logging-failure")
    repository = F5JobRepository(request.job_dir)
    repository.write_request(request.to_dict())
    monkeypatch.setattr(
        worker,
        "_configure_worker_logging",
        lambda _repository: (_ for _ in ()).throw(OSError("job log unavailable")),
    )

    assert worker.main(["--job-dir", request.job_dir]) == 1

    result = F5JobResult.from_dict(repository.read_result())
    assert result.status is F5JobStatus.FAILED
    assert result.error_code == "worker_initialization_failed"
    assert "job log unavailable" in result.error_message


@pytest.mark.parametrize(
    ("status", "expected_exit"),
    [
        (F5JobStatus.READY_TO_ACTIVATE, 0),
        (F5JobStatus.CANCELLED, 2),
        (F5JobStatus.FAILED, 1),
    ],
)
def test_worker_main_maps_terminal_status_to_process_exit(monkeypatch, tmp_path, status, expected_exit):
    request = _request(tmp_path / status.value)
    repository = F5JobRepository(request.job_dir)
    repository.write_request(request.to_dict())
    monkeypatch.setattr(worker, "_configure_worker_logging", lambda _repository: None)
    monkeypatch.setattr(worker, "execute_request", lambda request_arg, _repository: _result(request_arg, status))

    assert worker.main(["--job-dir", request.job_dir]) == expected_exit
