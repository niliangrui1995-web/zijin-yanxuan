# -*- coding: utf-8 -*-
"""Stable request, progress, artifact, and terminal-result contracts for F5 jobs."""

from __future__ import annotations

import datetime as dt
import uuid
from dataclasses import asdict, dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any

F5_JOB_SCHEMA_VERSION = 2


class F5JobStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    READY_TO_ACTIVATE = "ready_to_activate"
    SUCCEEDED = "succeeded"
    CANCELLED = "cancelled"
    FAILED = "failed"


class F5Phase(StrEnum):
    PREPARE = "prepare"
    GBBQ = "gbbq"
    MARKET_SYNC = "market_sync"
    MARKET_STAGE = "market_stage"
    RPS = "rps"
    SECTOR_RPS = "sector_rps"
    VALIDATE = "validate"
    ACTIVATE = "activate"
    COMPLETE = "complete"


def _now_iso() -> str:
    return dt.datetime.now().isoformat(timespec="seconds")


def _validate_schema_version(payload: dict[str, Any], contract_name: str) -> None:
    version = int(payload.get("schema_version") or 0)
    if version != F5_JOB_SCHEMA_VERSION:
        raise ValueError(f"unsupported {contract_name} schema_version: {version}")


@dataclass(frozen=True)
class F5JobRequest:
    run_id: str
    requested_date: str
    project_root: str
    data_dir: str
    cache_dir: str
    job_dir: str
    snapshot_dir: str
    database_path: str
    tdx_vipdoc: str
    created_at: str = field(default_factory=_now_iso)
    timeout_seconds: float = 30 * 60.0
    schema_version: int = F5_JOB_SCHEMA_VERSION

    @classmethod
    def build(
        cls,
        *,
        project_root: str,
        data_dir: str,
        cache_dir: str,
        tdx_vipdoc: str,
        requested_date: str | None = None,
        timeout_seconds: float = 30 * 60.0,
    ) -> "F5JobRequest":
        run_id = uuid.uuid4().hex
        cache_root = Path(cache_dir).resolve()
        return cls(
            run_id=run_id,
            requested_date=str(requested_date or dt.date.today().strftime("%Y%m%d")),
            project_root=str(Path(project_root).resolve()),
            data_dir=str(Path(data_dir).resolve()),
            cache_dir=str(cache_root),
            job_dir=str(cache_root / "f5_jobs" / run_id),
            snapshot_dir=str(cache_root / "f5_generations" / run_id),
            database_path=str(Path(data_dir).resolve() / "vcp_hunter.db"),
            tdx_vipdoc=str(Path(tdx_vipdoc).resolve()) if tdx_vipdoc else "",
            timeout_seconds=max(1.0, float(timeout_seconds or 0.0)),
        )

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "F5JobRequest":
        _validate_schema_version(payload, "F5 request")
        return cls(**{name: payload[name] for name in cls.__dataclass_fields__ if name in payload})

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class F5JobEvent:
    run_id: str
    seq: int
    phase: F5Phase
    message: str
    completed: int = 0
    total: int = 0
    timestamp: str = field(default_factory=_now_iso)
    kind: str = "progress"
    schema_version: int = F5_JOB_SCHEMA_VERSION

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "F5JobEvent":
        _validate_schema_version(payload, "F5 event")
        data = dict(payload)
        data["phase"] = F5Phase(str(data.get("phase") or F5Phase.PREPARE))
        return cls(**{name: data[name] for name in cls.__dataclass_fields__ if name in data})

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["phase"] = self.phase.value
        return payload


@dataclass(frozen=True)
class F5SnapshotArtifacts:
    snapshot_id: str
    requested_date: str
    effective_trade_date: str
    market_parquet_path: str
    market_schema_version: int
    market_source: str
    market_source_version: str
    market_symbol_count: int
    market_row_count: int
    rps_path: str
    rps_date: str
    rps_valid_count: int
    sector_rps_path: str
    sector_date: str
    sector_count: int
    market_size_bytes: int
    market_sha256: str
    rps_size_bytes: int
    rps_sha256: str
    sector_rps_size_bytes: int
    sector_rps_sha256: str
    gbbq_path: str = ""
    gbbq_size_bytes: int = 0
    gbbq_sha256: str = ""
    rps250_valid_count: int = 0

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "F5SnapshotArtifacts":
        return cls(**{name: payload[name] for name in cls.__dataclass_fields__ if name in payload})

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class F5JobResult:
    run_id: str
    status: F5JobStatus
    requested_date: str
    effective_trade_date: str = ""
    symbol_count: int = 0
    rps_valid_count: int = 0
    sector_count: int = 0
    elapsed_seconds: float = 0.0
    artifacts: F5SnapshotArtifacts | None = None
    error_code: str = ""
    error_message: str = ""
    worker_exit_code: int | None = None
    worker_stderr_tail: str = ""
    warnings: tuple[str, ...] = ()
    completed_at: str = field(default_factory=_now_iso)
    schema_version: int = F5_JOB_SCHEMA_VERSION

    @classmethod
    def failed(
        cls,
        request: F5JobRequest,
        *,
        error_code: str,
        error_message: str,
        elapsed_seconds: float = 0.0,
        worker_exit_code: int | None = None,
        worker_stderr_tail: str = "",
        warnings: tuple[str, ...] = (),
    ) -> "F5JobResult":
        return cls(
            run_id=request.run_id,
            status=F5JobStatus.FAILED,
            requested_date=request.requested_date,
            elapsed_seconds=elapsed_seconds,
            error_code=str(error_code or "f5_failed"),
            error_message=str(error_message or "F5 job failed"),
            worker_exit_code=int(worker_exit_code) if worker_exit_code is not None else None,
            worker_stderr_tail=str(worker_stderr_tail or ""),
            warnings=tuple(warnings),
        )

    @classmethod
    def cancelled(
        cls,
        request: F5JobRequest,
        *,
        elapsed_seconds: float = 0.0,
        reason: str = "cancelled",
        error_code: str = "cancelled",
        warnings: tuple[str, ...] = (),
    ) -> "F5JobResult":
        return cls(
            run_id=request.run_id,
            status=F5JobStatus.CANCELLED,
            requested_date=request.requested_date,
            elapsed_seconds=elapsed_seconds,
            error_code=str(error_code or "cancelled"),
            error_message=str(reason or "cancelled"),
            warnings=tuple(warnings),
        )

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "F5JobResult":
        _validate_schema_version(payload, "F5 result")
        data = dict(payload)
        data["status"] = F5JobStatus(str(data.get("status") or F5JobStatus.FAILED))
        artifacts = data.get("artifacts")
        data["artifacts"] = F5SnapshotArtifacts.from_dict(artifacts) if isinstance(artifacts, dict) else None
        data["warnings"] = tuple(data.get("warnings") or ())
        raw_exit_code = data.get("worker_exit_code")
        try:
            data["worker_exit_code"] = int(raw_exit_code) if raw_exit_code is not None else None
        except (TypeError, ValueError):
            data["worker_exit_code"] = None
        data["worker_stderr_tail"] = str(data.get("worker_stderr_tail") or "")
        return cls(**{name: data[name] for name in cls.__dataclass_fields__ if name in data})

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["status"] = self.status.value
        return payload


__all__ = [
    "F5_JOB_SCHEMA_VERSION",
    "F5JobEvent",
    "F5JobRequest",
    "F5JobResult",
    "F5JobStatus",
    "F5Phase",
    "F5SnapshotArtifacts",
]
