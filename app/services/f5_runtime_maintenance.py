# -*- coding: utf-8 -*-
"""Low-priority startup maintenance for isolated F5 runtime artifacts."""

from __future__ import annotations

from pathlib import Path

from app.services.f5_retention_service import prune_f5_runtime
from core.runtime_paths import CACHE_DIR
from infra.storage.f5_snapshot_repository import F5SnapshotRepository


def prune_startup_f5_runtime(
    cache_dir: str | Path = CACHE_DIR,
    *,
    repository: F5SnapshotRepository | None = None,
) -> dict[str, int]:
    return prune_f5_runtime(cache_dir, repository=repository)


__all__ = ["prune_startup_f5_runtime"]
