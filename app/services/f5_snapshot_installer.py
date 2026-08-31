# -*- coding: utf-8 -*-
"""Validate, publish, and install one complete F5 snapshot in the parent process."""

from __future__ import annotations

import os
import sqlite3
from collections.abc import Callable
from contextlib import nullcontext, suppress
from dataclasses import dataclass, replace
from math import isfinite
from pathlib import Path

from app.services.f5_job_contract import F5JobResult, F5JobStatus, F5SnapshotArtifacts
from app.services.f5_retention_service import discard_failed_f5_generation, prune_f5_runtime
from core.exceptions import CacheIOError, DataFormatError
from core.f5_activation_gate import f5_snapshot_activation_boundary
from core.f5_resource_guard import (
    F5_ACTIVATION_LOAD_MIN_COMMIT_HEADROOM_BYTES,
    F5_WORKER_START_MIN_COMMIT_HEADROOM_BYTES,
    F5MemoryPressureError,
    ensure_f5_commit_headroom,
)
from core.logger import get_logger
from infra.market_data.f5_market_snapshot_store import F5MarketSnapshotStore
from infra.market_data.market_data_warehouse import MARKET_DATA_SCHEMA_VERSION, MARKET_DATASET, WarehouseReadResult
from infra.market_data.warehouse_manifest import (
    F5SnapshotManifestRecord,
    WarehouseManifest,
    WarehouseManifestRecord,
)
from infra.storage.f5_snapshot_repository import F5SnapshotRepository
from infra.storage.file_integrity import (
    FileIntegrityError,
    is_sha256_hexdigest,
    verify_file_fingerprint,
)
from infra.storage.json_cache_repository import load_json_file, remove_cache_file, save_json_file

log = get_logger(__name__)

_ACTIVATION_ERRORS = (
    AttributeError,
    CacheIOError,
    DataFormatError,
    KeyError,
    OSError,
    RuntimeError,
    sqlite3.Error,
    TypeError,
    ValueError,
)
_ACTIVATION_BOUNDARY_ERRORS = _ACTIVATION_ERRORS + (Exception,)


class F5ActivationCancelled(RuntimeError):
    """Raised before activation may mutate a closing parent process."""


def _raise_if_activation_cancelled(cancelled_checker) -> None:
    if cancelled_checker is not None and cancelled_checker():
        raise F5ActivationCancelled("parent shutdown cancelled F5 activation")


def _require_artifacts(result: F5JobResult) -> F5SnapshotArtifacts:
    artifacts = result.artifacts
    if artifacts is None:
        raise ValueError("F5 result has no snapshot artifacts")
    return artifacts


@dataclass(frozen=True)
class _ParentSnapshotState:
    snapshot: F5SnapshotManifestRecord | None
    market_record: WarehouseManifestRecord | None
    cache_data: object
    source_status: dict
    trade_date: str
    rps_payload: dict
    gbbq_cache_path: str
    gbbq_cache_existed: bool
    gbbq_cache_bytes: bytes
    local_gbbq: dict
    local_gbbq_code_cache: dict
    local_gbbq_loaded: bool


@dataclass(frozen=True)
class _ValidatedBundle:
    market: WarehouseReadResult
    rps120: dict
    rps250: dict
    gbbq_payload: dict | None = None


def _snapshot_record(artifacts: F5SnapshotArtifacts, created_at: str) -> F5SnapshotManifestRecord:
    return F5SnapshotManifestRecord(
        snapshot_id=artifacts.snapshot_id,
        requested_date=artifacts.requested_date,
        effective_trade_date=artifacts.effective_trade_date,
        market_parquet_path=artifacts.market_parquet_path,
        market_schema_version=artifacts.market_schema_version,
        market_source=artifacts.market_source,
        market_source_version=artifacts.market_source_version,
        market_symbol_count=artifacts.market_symbol_count,
        market_row_count=artifacts.market_row_count,
        rps_path=artifacts.rps_path,
        rps_date=artifacts.rps_date,
        rps_valid_count=artifacts.rps_valid_count,
        sector_rps_path=artifacts.sector_rps_path,
        sector_date=artifacts.sector_date,
        sector_count=artifacts.sector_count,
        created_at=created_at,
        gbbq_path=artifacts.gbbq_path,
        market_size_bytes=artifacts.market_size_bytes,
        market_sha256=artifacts.market_sha256,
        rps_size_bytes=artifacts.rps_size_bytes,
        rps_sha256=artifacts.rps_sha256,
        sector_rps_size_bytes=artifacts.sector_rps_size_bytes,
        sector_rps_sha256=artifacts.sector_rps_sha256,
        gbbq_size_bytes=artifacts.gbbq_size_bytes,
        gbbq_sha256=artifacts.gbbq_sha256,
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
        data_status="ok",
    )


def _invalidate_parent_adjustment_cache(data_provider) -> None:
    lock = getattr(data_provider, "_local_gbbq_lock", None)
    if lock is None:
        return
    with lock:
        data_provider._local_gbbq = {}
        data_provider._local_gbbq_code_cache = {}
        data_provider._local_gbbq_loaded = False


def _valid_trade_date(value: str) -> bool:
    text = str(value or "")
    return len(text) == 8 and text.isdigit()


def _artifact_identity_error(result: F5JobResult) -> str:
    artifacts = _require_artifacts(result)
    if artifacts.snapshot_id != result.run_id:
        return "snapshot id does not match result run id"
    if artifacts.requested_date != result.requested_date:
        return "artifact requested date does not match result"
    if artifacts.effective_trade_date != result.effective_trade_date:
        return "artifact effective date does not match result"
    dates = (result.requested_date, result.effective_trade_date, artifacts.rps_date, artifacts.sector_date)
    if not all(_valid_trade_date(value) for value in dates):
        return "snapshot dates must use yyyyMMdd"
    if artifacts.rps_date != result.effective_trade_date or artifacts.sector_date != result.effective_trade_date:
        return "derived artifact dates do not match effective date"
    return ""


def _artifact_count_error(result: F5JobResult) -> str:
    artifacts = _require_artifacts(result)
    artifact_counts = (
        artifacts.market_symbol_count,
        artifacts.market_row_count,
        artifacts.rps_valid_count,
        artifacts.sector_count,
        artifacts.rps250_valid_count,
    )
    if any(int(value or 0) <= 0 for value in artifact_counts):
        return "snapshot artifact counts must be positive"
    result_counts = (result.symbol_count, result.rps_valid_count, result.sector_count)
    expected_counts = (artifacts.market_symbol_count, artifacts.rps_valid_count, artifacts.sector_count)
    if result_counts != expected_counts:
        return "snapshot artifact counts do not match result"
    if int(artifacts.market_schema_version or 0) != MARKET_DATA_SCHEMA_VERSION:
        return "snapshot market schema version is unsupported"
    if not artifacts.market_source or not artifacts.market_source_version:
        return "snapshot market provenance is missing"
    return ""


def _artifact_path_error(result: F5JobResult, cache_dir: Path) -> str:
    artifacts = _require_artifacts(result)
    expected_dir = (cache_dir / "f5_generations" / result.run_id).resolve()
    paths = (artifacts.market_parquet_path, artifacts.rps_path, artifacts.sector_rps_path)
    expected_paths = (
        expected_dir / "market.parquet",
        expected_dir / "rps.json",
        expected_dir / "sector_rps.json",
    )
    try:
        resolved_paths = tuple(Path(path).resolve() for path in paths)
    except (OSError, RuntimeError, TypeError, ValueError):
        return "snapshot artifact path is invalid"
    if resolved_paths != expected_paths:
        return "snapshot artifact paths do not match the job generation contract"
    if artifacts.gbbq_path and Path(artifacts.gbbq_path).resolve() != expected_dir / "gbbq.json":
        return "snapshot gbbq path does not match the job generation contract"
    return ""


def _artifact_fingerprint_contract_error(label: str, size_bytes: object, sha256: object) -> str:
    if not isinstance(size_bytes, int) or isinstance(size_bytes, bool):
        return f"snapshot {label} size is malformed"
    if size_bytes <= 0:
        return f"snapshot {label} size must be positive"
    if not is_sha256_hexdigest(sha256):
        return f"snapshot {label} SHA-256 is malformed"
    return ""


def _artifact_integrity_contract_error(result: F5JobResult) -> str:
    artifacts = _require_artifacts(result)
    required = (
        ("market", artifacts.market_size_bytes, artifacts.market_sha256),
        ("RPS", artifacts.rps_size_bytes, artifacts.rps_sha256),
        ("sector RPS", artifacts.sector_rps_size_bytes, artifacts.sector_rps_sha256),
    )
    for label, size_bytes, sha256 in required:
        error = _artifact_fingerprint_contract_error(label, size_bytes, sha256)
        if error:
            return error
    if artifacts.gbbq_path:
        return _artifact_fingerprint_contract_error(
            "gbbq",
            artifacts.gbbq_size_bytes,
            artifacts.gbbq_sha256,
        )
    if artifacts.gbbq_size_bytes != 0 or artifacts.gbbq_sha256:
        return "snapshot gbbq fingerprint requires a gbbq path"
    return ""


def _verify_artifact_integrity(artifacts: F5SnapshotArtifacts) -> None:
    fingerprints = [
        (artifacts.market_parquet_path, artifacts.market_size_bytes, artifacts.market_sha256),
        (artifacts.rps_path, artifacts.rps_size_bytes, artifacts.rps_sha256),
        (artifacts.sector_rps_path, artifacts.sector_rps_size_bytes, artifacts.sector_rps_sha256),
    ]
    if artifacts.gbbq_path:
        fingerprints.append((artifacts.gbbq_path, artifacts.gbbq_size_bytes, artifacts.gbbq_sha256))
    for path, size_bytes, sha256 in fingerprints:
        verify_file_fingerprint(
            path,
            expected_size_bytes=size_bytes,
            expected_sha256=sha256,
        )


def _valid_rps_value_count(values: dict) -> int:
    valid = 0
    for value in values.values():
        try:
            valid += int(isfinite(float(value)))
        except (TypeError, ValueError):
            continue
    return valid


def _validated_rps_payload(artifacts, rps_payload: dict) -> tuple[dict, dict]:
    if str(rps_payload.get("date") or "") != artifacts.effective_trade_date:
        raise ValueError("RPS date does not match effective market trade date")
    rps120, rps250 = rps_payload.get("rps120"), rps_payload.get("rps250")
    if not isinstance(rps120, dict) or not rps120 or not isinstance(rps250, dict) or not rps250:
        raise ValueError("RPS snapshot has empty rps120/rps250")
    if set(rps120) != set(rps250):
        raise ValueError("RPS120/RPS250 symbol sets do not match")
    if _valid_rps_value_count(rps120) != artifacts.rps_valid_count:
        raise ValueError("RPS120 valid count does not match artifact contract")
    if _valid_rps_value_count(rps250) != artifacts.rps250_valid_count:
        raise ValueError("RPS250 valid count does not match artifact contract")
    return rps120, rps250


def _validate_sector_payload(artifacts, sector_payload: dict) -> None:
    if str(sector_payload.get("date") or "") != artifacts.effective_trade_date:
        raise ValueError("sector RPS date does not match effective market trade date")
    sector_rps = sector_payload.get("sector_rps")
    if not isinstance(sector_rps, dict) or not sector_rps:
        raise ValueError("sector RPS snapshot is empty")
    if len(sector_rps) != artifacts.sector_count:
        raise ValueError("sector RPS count does not match artifact contract")


def _validated_gbbq_payload(path: str) -> dict | None:
    if not path:
        return None
    payload = load_json_file(path)
    if not isinstance(payload, dict) or not isinstance(payload.get("data"), dict):
        raise ValueError("gbbq snapshot payload is invalid")
    if int(payload.get("records", -1)) < 0 or payload.get("mtime") is None:
        raise ValueError("gbbq snapshot metadata is invalid")
    return payload


def _read_optional_bytes(path: str) -> tuple[bool, bytes]:
    if not path or not Path(path).is_file():
        return False, b""
    return True, Path(path).read_bytes()


def _atomic_restore_bytes(path: str, payload: bytes) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    temp_path = target.with_name(f"{target.name}.rollback.tmp")
    try:
        with temp_path.open("wb") as file_obj:
            file_obj.write(payload)
            file_obj.flush()
            os.fsync(file_obj.fileno())
        os.replace(temp_path, target)
    finally:
        with suppress(OSError):
            temp_path.unlink()


class F5SnapshotInstaller:
    def __init__(self, *, data_provider, engine, database_path: str, cache_dir: str) -> None:
        self.data_provider = data_provider
        self.engine = engine
        self.manifest = WarehouseManifest(db_path=database_path)
        self.repository = F5SnapshotRepository(self.manifest)
        self.cache_dir = Path(cache_dir).resolve()

    def activate(self, result: F5JobResult, *, cancelled_checker=None) -> F5JobResult:
        contract_error = self._contract_error(result)
        if contract_error is not None:
            self._discard_failed_generation(result.run_id)
            return contract_error
        try:
            _raise_if_activation_cancelled(cancelled_checker)
            ensure_f5_commit_headroom(
                F5_ACTIVATION_LOAD_MIN_COMMIT_HEADROOM_BYTES,
                stage="F5 快照激活加载",
            )
            bundle = self._load_validated_bundle(result)
            _raise_if_activation_cancelled(cancelled_checker)
            ensure_f5_commit_headroom(
                F5_WORKER_START_MIN_COMMIT_HEADROOM_BYTES,
                stage="F5 快照激活提交",
            )
            snapshot = self._activate_bundle_atomically(result, bundle, cancelled_checker)
            if cancelled_checker is None or not cancelled_checker():
                self._update_compatibility_mirrors(snapshot)
                self._prune_runtime(result.run_id)
            return replace(result, status=F5JobStatus.SUCCEEDED, error_code="", error_message="")
        except F5MemoryPressureError as exc:
            log.warning("[F5] snapshot activation rejected by resource guard: %s", exc)
            self._discard_failed_generation(result.run_id)
            return replace(
                result,
                status=F5JobStatus.FAILED,
                error_code=exc.error_code,
                error_message=str(exc),
            )
        except F5ActivationCancelled as exc:
            self._discard_failed_generation(result.run_id)
            return replace(
                result,
                status=F5JobStatus.CANCELLED,
                error_code="activation_cancelled",
                error_message=str(exc),
            )
        except FileIntegrityError as exc:
            log.error("[F5] snapshot integrity verification failed: %s", exc)
            self._discard_failed_generation(result.run_id)
            return replace(
                result,
                status=F5JobStatus.FAILED,
                error_code="snapshot_integrity_mismatch",
                error_message=str(exc),
            )
        except _ACTIVATION_BOUNDARY_ERRORS as exc:
            log.error("[F5] snapshot activation failed: %s", exc, exc_info=True)
            self._discard_failed_generation(result.run_id)
            return replace(
                result,
                status=F5JobStatus.FAILED,
                error_code="snapshot_activation_failed",
                error_message=str(exc),
            )

    def _activate_bundle_atomically(
        self,
        result: F5JobResult,
        bundle: _ValidatedBundle,
        cancelled_checker: Callable[[], bool] | None,
    ) -> F5SnapshotManifestRecord:
        with f5_snapshot_activation_boundary():
            _raise_if_activation_cancelled(cancelled_checker)
            state = self._capture_parent_state()
            published = False
            try:
                snapshot = _snapshot_record(_require_artifacts(result), result.completed_at)
                self.repository.publish(snapshot=snapshot, market_record=_market_record(snapshot))
                published = True
                _raise_if_activation_cancelled(cancelled_checker)
                self._install_parent_memory(result, bundle)
                _raise_if_activation_cancelled(cancelled_checker)
            except _ACTIVATION_BOUNDARY_ERRORS:
                self._rollback_parent_state(
                    state,
                    published=published,
                    failed_snapshot_id=result.run_id,
                    failed_market_trade_date=result.effective_trade_date,
                )
                raise
            return snapshot

    def prune_after_terminal(self, result: F5JobResult) -> None:
        if result.status is not F5JobStatus.SUCCEEDED:
            self._discard_failed_generation(result.run_id)
        else:
            self._prune_runtime(result.run_id)

    def _contract_error(self, result: F5JobResult) -> F5JobResult | None:
        error = "F5 result is not ready to activate"
        if result.status is F5JobStatus.READY_TO_ACTIVATE and result.artifacts is not None:
            try:
                error = (
                    _artifact_identity_error(result)
                    or _artifact_count_error(result)
                    or _artifact_path_error(result, self.cache_dir)
                    or _artifact_integrity_contract_error(result)
                )
            except _ACTIVATION_BOUNDARY_ERRORS as exc:
                error = f"snapshot artifact contract is malformed: {exc}"
            if not error:
                return None
        return replace(
            result,
            status=F5JobStatus.FAILED,
            error_code="activation_contract_invalid",
            error_message=error,
        )

    def _capture_parent_state(self) -> _ParentSnapshotState:
        gbbq_cache_path = str(getattr(self.data_provider, "gbbq_cache_file", "") or "")
        gbbq_cache_existed, gbbq_cache_bytes = _read_optional_bytes(gbbq_cache_path)
        gbbq_lock = getattr(self.data_provider, "_local_gbbq_lock", None)
        with gbbq_lock if gbbq_lock is not None else nullcontext():
            local_gbbq = dict(getattr(self.data_provider, "_local_gbbq", {}) or {})
            local_gbbq_code_cache = dict(getattr(self.data_provider, "_local_gbbq_code_cache", {}) or {})
            local_gbbq_loaded = bool(getattr(self.data_provider, "_local_gbbq_loaded", False))
        return _ParentSnapshotState(
            snapshot=self.repository.active(),
            market_record=self.manifest.latest(MARKET_DATASET),
            cache_data=getattr(self.data_provider, "cache_data", {}),
            source_status=dict(getattr(self.data_provider, "_last_market_data_source_status", {}) or {}),
            trade_date=str(getattr(self.data_provider, "_market_data_snapshot_trade_date", "") or ""),
            rps_payload=dict(self.engine.get_precomputed_rps() or {}),
            gbbq_cache_path=gbbq_cache_path,
            gbbq_cache_existed=gbbq_cache_existed,
            gbbq_cache_bytes=gbbq_cache_bytes,
            local_gbbq=local_gbbq,
            local_gbbq_code_cache=local_gbbq_code_cache,
            local_gbbq_loaded=local_gbbq_loaded,
        )

    def _load_validated_bundle(self, result: F5JobResult) -> _ValidatedBundle:
        artifacts = _require_artifacts(result)
        _verify_artifact_integrity(artifacts)
        store = F5MarketSnapshotStore(Path(artifacts.market_parquet_path).parent)
        market = store.read_market_snapshot(
            trade_date=artifacts.effective_trade_date,
            expected_symbol_count=artifacts.market_symbol_count,
            expected_row_count=artifacts.market_row_count,
        )
        if not market.status.ok or not market.data:
            raise ValueError(f"market snapshot validation failed: {market.status.error}")
        rps_payload = load_json_file(artifacts.rps_path)
        sector_payload = load_json_file(artifacts.sector_rps_path)
        rps120, rps250 = _validated_rps_payload(artifacts, rps_payload)
        _validate_sector_payload(artifacts, sector_payload)
        return _ValidatedBundle(
            market=market,
            rps120=rps120,
            rps250=rps250,
            gbbq_payload=_validated_gbbq_payload(artifacts.gbbq_path),
        )

    def _install_parent_memory(self, result: F5JobResult, bundle: _ValidatedBundle) -> None:
        artifacts = _require_artifacts(result)
        with self.data_provider.cache_lock:
            self.data_provider.cache_data = bundle.market.data
            self.data_provider._market_data_snapshot_trade_date = artifacts.effective_trade_date
            self.data_provider._last_market_data_source_status = bundle.market.status.to_dict()
            self.engine.set_precomputed_rps(artifacts.rps_date, bundle.rps120, bundle.rps250)
        if bundle.gbbq_payload is not None:
            gbbq_cache_path = str(getattr(self.data_provider, "gbbq_cache_file", "") or "")
            if not gbbq_cache_path:
                raise ValueError("parent gbbq cache path is missing")
            save_json_file(gbbq_cache_path, bundle.gbbq_payload)
            _invalidate_parent_adjustment_cache(self.data_provider)

    def _rollback_parent_state(
        self,
        state: _ParentSnapshotState,
        *,
        published: bool,
        failed_snapshot_id: str,
        failed_market_trade_date: str,
    ) -> None:
        if published:
            try:
                self.repository.restore_active_pointers(
                    snapshot=state.snapshot,
                    market_record=state.market_record,
                )
            except (OSError, RuntimeError, sqlite3.Error, TypeError, ValueError) as exc:
                log.critical("[F5] snapshot pointer rollback failed: %s", exc, exc_info=True)
            try:
                self.repository.delete_inactive(failed_snapshot_id)
            except (OSError, RuntimeError, sqlite3.Error, TypeError, ValueError) as exc:
                log.critical("[F5] failed snapshot manifest cleanup failed: %s", exc, exc_info=True)
            if state.market_record is None or state.market_record.trade_date != failed_market_trade_date:
                try:
                    self.manifest.delete_inactive_market_record(MARKET_DATASET, failed_market_trade_date)
                except (OSError, RuntimeError, sqlite3.Error, TypeError, ValueError) as exc:
                    log.critical("[F5] failed market manifest cleanup failed: %s", exc, exc_info=True)
        with self.data_provider.cache_lock:
            self.data_provider.cache_data = state.cache_data
            self.data_provider._market_data_snapshot_trade_date = state.trade_date
            self.data_provider._last_market_data_source_status = state.source_status
            try:
                self.engine.set_precomputed_rps(
                    state.rps_payload.get("date", ""),
                    state.rps_payload.get("rps120", {}),
                    state.rps_payload.get("rps250", {}),
                )
            except (AttributeError, RuntimeError, TypeError, ValueError) as exc:
                log.critical("[F5] parent RPS rollback failed: %s", exc, exc_info=True)
        self._restore_parent_adjustment_state(state)

    def _restore_parent_adjustment_state(self, state: _ParentSnapshotState) -> None:
        try:
            if state.gbbq_cache_existed:
                _atomic_restore_bytes(state.gbbq_cache_path, state.gbbq_cache_bytes)
            else:
                remove_cache_file(state.gbbq_cache_path)
        except (OSError, RuntimeError, TypeError, ValueError) as exc:
            log.critical("[F5] parent gbbq file rollback failed: %s", exc, exc_info=True)
        gbbq_lock = getattr(self.data_provider, "_local_gbbq_lock", None)
        with gbbq_lock if gbbq_lock is not None else nullcontext():
            self.data_provider._local_gbbq = state.local_gbbq
            self.data_provider._local_gbbq_code_cache = state.local_gbbq_code_cache
            self.data_provider._local_gbbq_loaded = state.local_gbbq_loaded

    def _update_compatibility_mirrors(self, snapshot: F5SnapshotManifestRecord) -> None:
        try:
            self.repository.update_compatibility_mirrors(snapshot)
        except OSError as exc:
            log.warning("[F5] compatibility mirror update skipped: %s", exc)

    def _prune_runtime(self, run_id: str) -> None:
        try:
            prune_f5_runtime(self.cache_dir, keep_job_ids={run_id}, repository=self.repository)
        except (OSError, RuntimeError, sqlite3.Error, TypeError, ValueError) as exc:
            log.warning("[F5] runtime retention skipped: %s", exc)

    def _discard_failed_generation(self, run_id: str) -> None:
        try:
            discard_failed_f5_generation(self.cache_dir, run_id, repository=self.repository)
            self._prune_runtime(run_id)
        except (OSError, RuntimeError, sqlite3.Error, TypeError, ValueError) as exc:
            log.warning("[F5] failed generation cleanup skipped: %s", exc)


__all__ = ["F5SnapshotInstaller"]
