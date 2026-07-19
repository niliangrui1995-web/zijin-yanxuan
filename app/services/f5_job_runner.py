# -*- coding: utf-8 -*-
"""Subprocess runner and injectable contract for F5 jobs."""

from __future__ import annotations

import subprocess
import time
from typing import Protocol

from app.services.f5_job_contract import F5JobEvent, F5JobRequest, F5JobResult
from core.f5_activation_gate import f5_snapshot_activation_boundary
from infra.storage.f5_job_repository import F5JobRepository
from infra.tasks.app_worker_process import spawn_f5_worker


class F5JobHandle(Protocol):
    job_id: str

    def poll_events(self) -> tuple[F5JobEvent, ...]: ...

    def result(self) -> F5JobResult | None: ...

    def cancel(self, reason: str = "cancelled") -> None: ...

    def enforce_cancel_grace(self, grace_seconds: float = 2.0) -> None: ...

    def force_terminate(self) -> None: ...

    def is_running(self) -> bool: ...


class F5JobRunner(Protocol):
    def start(self, request: F5JobRequest) -> F5JobHandle: ...


class _RepositoryEventCursor:
    def __init__(self, repository: F5JobRepository) -> None:
        self.repository = repository
        self.offset = 0

    def poll_events(self) -> tuple[F5JobEvent, ...]:
        payloads, self.offset = self.repository.read_events(offset=self.offset)
        return tuple(F5JobEvent.from_dict(payload) for payload in payloads)


class ProcessF5JobHandle(_RepositoryEventCursor):
    def __init__(self, request: F5JobRequest, repository: F5JobRepository, process) -> None:
        super().__init__(repository)
        self.request = request
        self.process = process
        self.job_id = request.run_id
        self._cancel_requested_at = 0.0
        self._terminate_requested_at = 0.0
        self._cancel_reason = ""
        self._started_at = time.monotonic()
        self._result = None

    def result(self) -> F5JobResult | None:
        if self._result is not None:
            return self._result
        payload = self.repository.read_result()
        if payload is not None:
            self._result = F5JobResult.from_dict(payload)
        elif self._cancel_reason and not self.is_running():
            error_code = "deadline_exceeded" if self._cancel_reason == "deadline_exceeded" else "cancelled"
            self._result = F5JobResult.cancelled(
                self.request,
                elapsed_seconds=time.monotonic() - self._started_at,
                reason=self._cancel_reason,
                error_code=error_code,
            )
            self.repository.write_result(self._result.to_dict())
        return self._result

    def cancel(self, reason: str = "cancelled") -> None:
        if not self._cancel_reason:
            self._cancel_reason = str(reason or "cancelled")
        self.repository.request_cancel(self._cancel_reason)
        if self._cancel_requested_at <= 0:
            self._cancel_requested_at = time.monotonic()

    def enforce_cancel_grace(self, grace_seconds: float = 2.0) -> None:
        if self._cancel_requested_at <= 0 or not self.is_running():
            return
        grace = max(0.0, float(grace_seconds))
        if time.monotonic() - self._cancel_requested_at < grace:
            return
        if self._terminate_requested_at <= 0:
            try:
                self.process.terminate()
            except OSError:
                self.force_terminate()
                return
            self._terminate_requested_at = time.monotonic()
            return
        if time.monotonic() - self._terminate_requested_at >= grace:
            self.force_terminate()

    def force_terminate(self) -> None:
        if not self.is_running():
            return
        try:
            try:
                self.process.terminate()
            except OSError:
                pass
            if self._wait_process(0.5):
                return
            try:
                self.process.kill()
            except OSError:
                pass
            self._wait_process(0.5)
        finally:
            self._reap()

    def is_running(self) -> bool:
        try:
            running = self.process.poll() is None
        except OSError:
            running = False
        if not running:
            self._reap()
        return running

    def _wait_process(self, timeout: float) -> bool:
        try:
            self.process.wait(timeout=timeout)
            return True
        except (OSError, subprocess.TimeoutExpired):
            return False

    def _reap(self) -> None:
        try:
            self.process.wait(timeout=0)
        except (OSError, subprocess.TimeoutExpired):
            return


class ProcessF5JobRunner:
    def start(self, request: F5JobRequest) -> ProcessF5JobHandle:
        with f5_snapshot_activation_boundary():
            repository = F5JobRepository(request.job_dir)
            repository.write_request(request.to_dict())
            process = spawn_f5_worker(project_root=request.project_root, job_dir=request.job_dir)
        return ProcessF5JobHandle(request, repository, process)


def persist_f5_terminal_result(request: F5JobRequest, result: F5JobResult) -> None:
    F5JobRepository(request.job_dir).write_result(result.to_dict())


__all__ = [
    "F5JobHandle",
    "F5JobRunner",
    "ProcessF5JobRunner",
    "persist_f5_terminal_result",
]
