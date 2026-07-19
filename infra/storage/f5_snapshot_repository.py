# -*- coding: utf-8 -*-
"""Resolver and atomic publisher for complete F5 snapshot bundles."""

from __future__ import annotations

import os
import shutil
import threading
import uuid
from contextlib import suppress
from pathlib import Path

from core.runtime_paths import RPS_CACHE_FILE, SECTOR_RPS_CACHE_FILE
from infra.market_data.warehouse_manifest import (
    F5SnapshotManifestRecord,
    WarehouseManifest,
    WarehouseManifestRecord,
)


def _atomic_copy(source: str | Path, destination: str | Path) -> None:
    source_path = Path(source)
    destination_path = Path(destination)
    destination_path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = destination_path.with_name(
        f"{destination_path.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp"
    )
    try:
        shutil.copyfile(source_path, temp_path)
        os.replace(temp_path, destination_path)
    finally:
        with suppress(OSError):
            if temp_path.exists():
                temp_path.unlink()


class F5SnapshotRepository:
    def __init__(self, manifest: WarehouseManifest | None = None) -> None:
        self.manifest = manifest or WarehouseManifest()

    def active(self) -> F5SnapshotManifestRecord | None:
        return self.manifest.active_f5_snapshot()

    def get(self, snapshot_id: str) -> F5SnapshotManifestRecord | None:
        return self.manifest.get_f5_snapshot(snapshot_id)

    def delete_inactive(self, snapshot_id: str) -> None:
        self.manifest.delete_inactive_f5_snapshot(snapshot_id)

    def recent(self, *, limit: int = 2) -> tuple[F5SnapshotManifestRecord, ...]:
        return self.manifest.recent_f5_snapshots(limit=limit)

    def publish(
        self,
        *,
        snapshot: F5SnapshotManifestRecord,
        market_record: WarehouseManifestRecord,
    ) -> None:
        self.manifest.publish_f5_snapshot(snapshot=snapshot, market_record=market_record)

    def restore_active_pointers(
        self,
        *,
        snapshot: F5SnapshotManifestRecord | None,
        market_record: WarehouseManifestRecord | None,
    ) -> None:
        self.manifest.restore_f5_active_pointers(snapshot=snapshot, market_record=market_record)

    def update_compatibility_mirrors(self, snapshot: F5SnapshotManifestRecord) -> None:
        _atomic_copy(snapshot.rps_path, RPS_CACHE_FILE)
        _atomic_copy(snapshot.sector_rps_path, SECTOR_RPS_CACHE_FILE)

    def resolve_rps_path(self, fallback: str = RPS_CACHE_FILE) -> str:
        active = self.active()
        if active is not None and Path(active.rps_path).is_file():
            return active.rps_path
        return str(fallback or "")

    def resolve_sector_rps_path(self, fallback: str = SECTOR_RPS_CACHE_FILE) -> str:
        active = self.active()
        if active is not None and Path(active.sector_rps_path).is_file():
            return active.sector_rps_path
        return str(fallback or "")


_DEFAULT_REPOSITORY: F5SnapshotRepository | None = None
_DEFAULT_LOCK = threading.Lock()


def get_default_f5_snapshot_repository() -> F5SnapshotRepository:
    global _DEFAULT_REPOSITORY
    with _DEFAULT_LOCK:
        if _DEFAULT_REPOSITORY is None:
            _DEFAULT_REPOSITORY = F5SnapshotRepository()
        return _DEFAULT_REPOSITORY


def resolve_active_rps_path(fallback: str = RPS_CACHE_FILE) -> str:
    return get_default_f5_snapshot_repository().resolve_rps_path(fallback)


def resolve_active_sector_rps_path(fallback: str = SECTOR_RPS_CACHE_FILE) -> str:
    return get_default_f5_snapshot_repository().resolve_sector_rps_path(fallback)


__all__ = [
    "F5SnapshotRepository",
    "get_default_f5_snapshot_repository",
    "resolve_active_rps_path",
    "resolve_active_sector_rps_path",
]
