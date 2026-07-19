# -*- coding: utf-8 -*-
"""Bounded, path-safe retention for F5 jobs and immutable generations."""

from __future__ import annotations

import json
import re
import shutil
import time
from pathlib import Path
from typing import cast

from app.services.f5_job_contract import F5JobResult, F5JobStatus
from core.f5_activation_gate import f5_snapshot_activation_boundary
from infra.market_data.warehouse_manifest import F5SnapshotManifestRecord
from infra.storage.f5_snapshot_repository import F5SnapshotRepository
from infra.storage.file_integrity import FileIntegrityError, verify_file_fingerprint

_RUN_ID_PATTERN = re.compile(r"^[0-9a-f]{32}$")
_STALE_JOB_SECONDS = 24 * 60 * 60
_GENERATION_LIMIT = 2
_GENERATION_ARTIFACT_NAMES = ("market.parquet", "rps.json", "sector_rps.json")
_TEMPORARY_SUFFIXES = (".tmp", ".part", ".partial")


def _safe_runtime_child(root: Path, child: Path) -> bool:
    try:
        return child.resolve().parent == root.resolve() and bool(_RUN_ID_PATTERN.fullmatch(child.name))
    except (OSError, RuntimeError):
        return False


def _remove_runtime_dir(root: Path, child: Path) -> bool:
    if not child.is_dir() or not _safe_runtime_child(root, child):
        return False
    try:
        shutil.rmtree(child)
        return True
    except OSError:
        return False


def _is_expired(path: Path, *, now: float, max_age_seconds: float) -> bool:
    try:
        return now - path.stat().st_mtime >= max(0.0, float(max_age_seconds))
    except OSError:
        return False


def _protected_generation_ids(repository: F5SnapshotRepository) -> set[str]:
    active = repository.active()
    protected = {active.snapshot_id} if active is not None else set()
    for snapshot in repository.recent(limit=8):
        if snapshot.snapshot_id not in protected and not _snapshot_integrity_failures(snapshot):
            protected.add(snapshot.snapshot_id)
            break
    return protected


def prune_f5_runtime(
    cache_dir: str | Path,
    *,
    keep_job_ids: set[str] | None = None,
    repository: F5SnapshotRepository | None = None,
    now: float | None = None,
) -> dict[str, int]:
    """Keep active+previous generations and only the current/recent unfinished job."""

    with f5_snapshot_activation_boundary():
        cache_root = Path(cache_dir).resolve()
        repository = repository or F5SnapshotRepository()
        current_time = time.time() if now is None else float(now)
        job_root = cache_root / "f5_jobs"
        protected = _protected_generation_ids(repository)
        protected_jobs = set(keep_job_ids or set())
        protected_jobs.update(_unfinished_job_ids(job_root, now=current_time))
        protected.update(protected_jobs)
        removed_generations = _prune_generations(cache_root / "f5_generations", protected)
        removed_jobs = _prune_jobs(job_root, protected_jobs, now=current_time)
        return {"removed_generations": removed_generations, "removed_jobs": removed_jobs}


def discard_failed_f5_generation(
    cache_dir: str | Path,
    run_id: str,
    *,
    repository: F5SnapshotRepository | None = None,
) -> bool:
    with f5_snapshot_activation_boundary():
        cache_root = Path(cache_dir).resolve()
        repository = repository or F5SnapshotRepository()
        active = repository.active()
        if active is not None and active.snapshot_id == run_id:
            return False
        generation_root = cache_root / "f5_generations"
        return _remove_runtime_dir(generation_root, generation_root / str(run_id or ""))


def _runtime_dir_inventory(root: Path) -> tuple[list[Path], list[str]]:
    if not root.is_dir():
        return [], []
    safe_dirs: list[Path] = []
    invalid_entries: list[str] = []
    for child in root.iterdir():
        if child.is_dir() and _safe_runtime_child(root, child):
            safe_dirs.append(child)
        else:
            invalid_entries.append(child.name)
    return sorted(safe_dirs, key=lambda path: path.name), sorted(invalid_entries)


def _nonempty_file(path: Path) -> bool:
    try:
        return path.is_file() and path.stat().st_size > 0
    except OSError:
        return False


def _generation_complete(path: Path) -> bool:
    return all(_nonempty_file(path / name) for name in _GENERATION_ARTIFACT_NAMES)


def _snapshot_artifact_paths(snapshot: F5SnapshotManifestRecord) -> tuple[Path, ...]:
    values = [snapshot.market_parquet_path, snapshot.rps_path, snapshot.sector_rps_path]
    if snapshot.gbbq_path:
        values.append(snapshot.gbbq_path)
    return tuple(Path(value) for value in values)


def _snapshot_fingerprints(snapshot: F5SnapshotManifestRecord) -> tuple[tuple[str, int, str], ...]:
    values = [
        (snapshot.market_parquet_path, snapshot.market_size_bytes, snapshot.market_sha256),
        (snapshot.rps_path, snapshot.rps_size_bytes, snapshot.rps_sha256),
        (
            snapshot.sector_rps_path,
            snapshot.sector_rps_size_bytes,
            snapshot.sector_rps_sha256,
        ),
    ]
    if snapshot.gbbq_path:
        values.append((snapshot.gbbq_path, snapshot.gbbq_size_bytes, snapshot.gbbq_sha256))
    return tuple(values)


def _snapshot_integrity_failures(snapshot: F5SnapshotManifestRecord | None) -> tuple[str, ...]:
    if snapshot is None:
        return ()
    failures = []
    for path, size_bytes, sha256 in _snapshot_fingerprints(snapshot):
        try:
            verify_file_fingerprint(
                path,
                expected_size_bytes=size_bytes,
                expected_sha256=sha256,
            )
        except (FileIntegrityError, OSError, TypeError, ValueError):
            failures.append(str(Path(path)))
    return tuple(sorted(set(failures)))


def _snapshot_complete_in_generation(cache_root: Path, snapshot: F5SnapshotManifestRecord | None) -> bool:
    if snapshot is None:
        return True
    generation = cache_root / "f5_generations" / snapshot.snapshot_id
    if not _safe_runtime_child(generation.parent, generation):
        return False
    try:
        expected_parent = generation.resolve()
        paths = _snapshot_artifact_paths(snapshot)
        return all(path.resolve().parent == expected_parent for path in paths) and not _snapshot_integrity_failures(
            snapshot
        )
    except (OSError, RuntimeError):
        return False


def _read_job_status(path: Path) -> tuple[F5JobStatus | None, bool]:
    result_path = path / "result.json"
    if not result_path.is_file():
        return None, True
    try:
        payload = json.loads(result_path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            return None, False
        result = F5JobResult.from_dict(payload)
    except (AttributeError, json.JSONDecodeError, KeyError, OSError, TypeError, ValueError):
        return None, False
    return (result.status, result.run_id == path.name)


def _job_runtime_inventory(job_dirs: list[Path]) -> dict[str, list[str]]:
    inventory = {
        "unfinished_job_ids": [],
        "ready_to_activate_job_ids": [],
        "terminal_job_ids": [],
        "invalid_job_ids": [],
    }
    for job_dir in job_dirs:
        status, valid = _read_job_status(job_dir)
        if not valid:
            inventory["invalid_job_ids"].append(job_dir.name)
        elif status is None or status in {F5JobStatus.PENDING, F5JobStatus.RUNNING}:
            inventory["unfinished_job_ids"].append(job_dir.name)
        elif status is F5JobStatus.READY_TO_ACTIVATE:
            inventory["ready_to_activate_job_ids"].append(job_dir.name)
        else:
            inventory["terminal_job_ids"].append(job_dir.name)
    return inventory


def _temporary_runtime_files(cache_root: Path, roots: tuple[Path, ...]) -> list[str]:
    temporary: list[str] = []
    for root in roots:
        if not root.is_dir():
            continue
        for candidate in root.rglob("*"):
            if candidate.is_file() and candidate.name.lower().endswith(_TEMPORARY_SUFFIXES):
                try:
                    temporary.append(candidate.resolve().relative_to(cache_root).as_posix())
                except (OSError, RuntimeError, ValueError):
                    temporary.append(str(candidate))
    return sorted(set(temporary))


def _f5_runtime_is_clean(receipt: dict[str, object]) -> bool:
    return all(
        (
            cast(int, receipt["generation_count"]) <= _GENERATION_LIMIT,
            cast(int, receipt["job_count"]) <= 1,
            receipt["active_snapshot_complete"],
            receipt["active_snapshot_integrity_ok"],
            receipt["previous_snapshot_integrity_ok"],
            not receipt["integrity_mismatch_paths"],
            not receipt["unexpected_generation_ids"],
            not receipt["incomplete_generation_ids"],
            not receipt["invalid_generation_entries"],
            not receipt["invalid_job_entries"],
            not receipt["invalid_job_ids"],
            not receipt["unfinished_job_ids"],
            not receipt["ready_to_activate_job_ids"],
            not receipt["temporary_files"],
        )
    )


def _active_and_previous_snapshots(
    repository: F5SnapshotRepository,
    retained_generation_ids: set[str],
) -> tuple[F5SnapshotManifestRecord | None, F5SnapshotManifestRecord | None]:
    active = repository.active()
    recent = repository.recent(limit=8)
    if active is None:
        return None, None
    previous = next(
        (
            snapshot
            for snapshot in recent
            if snapshot.snapshot_id != active.snapshot_id
            and snapshot.snapshot_id in retained_generation_ids
        ),
        None,
    )
    return active, previous


def inspect_f5_runtime(
    cache_dir: str | Path,
    *,
    repository: F5SnapshotRepository | None = None,
) -> dict[str, object]:
    """Return a read-only, fail-closed receipt for F5 jobs and snapshot generations."""

    cache_root = Path(cache_dir).resolve()
    repository = repository or F5SnapshotRepository()
    generation_root = cache_root / "f5_generations"
    job_root = cache_root / "f5_jobs"
    generation_dirs, invalid_generation_entries = _runtime_dir_inventory(generation_root)
    job_dirs, invalid_job_entries = _runtime_dir_inventory(job_root)
    generation_ids = [path.name for path in generation_dirs]
    job_ids = [path.name for path in job_dirs]
    incomplete_generation_ids = [path.name for path in generation_dirs if not _generation_complete(path)]
    protected_generation_ids = sorted(_protected_generation_ids(repository))
    unexpected_generation_ids = sorted(set(generation_ids) - set(protected_generation_ids))
    active, previous = _active_and_previous_snapshots(repository, set(generation_ids))
    active_integrity_failures = _snapshot_integrity_failures(active)
    previous_integrity_failures = _snapshot_integrity_failures(previous)
    integrity_mismatch_paths = sorted(
        set(active_integrity_failures).union(previous_integrity_failures)
    )
    active_snapshot_complete = _snapshot_complete_in_generation(cache_root, active)
    job_inventory = _job_runtime_inventory(job_dirs)
    temporary_files = _temporary_runtime_files(cache_root, (generation_root, job_root))
    receipt = {
        "active_snapshot_id": active.snapshot_id if active is not None else "",
        "active_snapshot_complete": active_snapshot_complete,
        "active_snapshot_integrity_ok": not active_integrity_failures,
        "previous_snapshot_id": previous.snapshot_id if previous is not None else "",
        "previous_snapshot_integrity_ok": not previous_integrity_failures,
        "integrity_mismatch_paths": integrity_mismatch_paths,
        "generation_limit": _GENERATION_LIMIT,
        "generation_count": len(generation_ids),
        "generation_ids": generation_ids,
        "protected_generation_ids": protected_generation_ids,
        "unexpected_generation_ids": unexpected_generation_ids,
        "incomplete_generation_ids": incomplete_generation_ids,
        "invalid_generation_entries": invalid_generation_entries,
        "job_count": len(job_ids),
        "job_ids": job_ids,
        **job_inventory,
        "terminal_job_count": len(job_inventory["terminal_job_ids"]),
        "invalid_job_entries": invalid_job_entries,
        "temporary_file_count": len(temporary_files),
        "temporary_files": temporary_files,
    }
    return {"clean": _f5_runtime_is_clean(receipt), **receipt}


def _unfinished_job_ids(root: Path, *, now: float) -> set[str]:
    if not root.is_dir():
        return set()
    return {
        child.name
        for child in root.iterdir()
        if _safe_runtime_child(root, child)
        and _job_may_still_activate(child, now=now)
    }


def _job_may_still_activate(path: Path, *, now: float) -> bool:
    if _is_expired(path, now=now, max_age_seconds=_STALE_JOB_SECONDS):
        return False
    result_path = path / "result.json"
    if not result_path.is_file():
        return True
    try:
        payload = json.loads(result_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError, TypeError, ValueError):
        return True
    return str(payload.get("status") or "") == "ready_to_activate"


def _prune_generations(root: Path, protected_ids: set[str]) -> int:
    if not root.is_dir():
        return 0
    removed = 0
    for child in root.iterdir():
        if child.name in protected_ids:
            continue
        removed += int(_remove_runtime_dir(root, child))
    return removed


def _prune_jobs(root: Path, keep_ids: set[str], *, now: float) -> int:
    if not root.is_dir():
        return 0
    removed = 0
    for child in root.iterdir():
        if child.name in keep_ids or not _safe_runtime_child(root, child):
            continue
        completed = (child / "result.json").is_file()
        expired = _is_expired(child, now=now, max_age_seconds=_STALE_JOB_SECONDS)
        if completed or expired:
            removed += int(_remove_runtime_dir(root, child))
    return removed


__all__ = ["discard_failed_f5_generation", "inspect_f5_runtime", "prune_f5_runtime"]
