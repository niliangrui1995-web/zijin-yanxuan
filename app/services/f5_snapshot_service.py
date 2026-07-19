# -*- coding: utf-8 -*-
"""Application facade for resolving the currently active F5 snapshot bundle."""

from __future__ import annotations

from typing import Any

from core.f5_activation_gate import f5_snapshot_read_boundary
from core.runtime_paths import RPS_CACHE_FILE, SECTOR_RPS_CACHE_FILE
from infra.storage.f5_snapshot_repository import get_default_f5_snapshot_repository
from infra.storage.json_cache_repository import cache_file_exists, cache_file_mtime, load_json_file


def resolve_active_rps_path(fallback: str = RPS_CACHE_FILE) -> str:
    with f5_snapshot_read_boundary():
        return get_default_f5_snapshot_repository().resolve_rps_path(fallback)


def resolve_active_sector_rps_path(fallback: str = SECTOR_RPS_CACHE_FILE) -> str:
    with f5_snapshot_read_boundary():
        return get_default_f5_snapshot_repository().resolve_sector_rps_path(fallback)


def read_active_rps_bundle(fallback: str = RPS_CACHE_FILE) -> tuple[str, dict[str, Any] | None]:
    with f5_snapshot_read_boundary():
        path = resolve_active_rps_path(fallback)
        if not cache_file_exists(path):
            return path, None
        return path, _require_object_payload(load_json_file(path), label="RPS")


def read_active_sector_rps_bundle(
    fallback: str = SECTOR_RPS_CACHE_FILE,
) -> tuple[str, dict[str, Any] | None]:
    with f5_snapshot_read_boundary():
        path = resolve_active_sector_rps_path(fallback)
        if not cache_file_exists(path):
            return path, None
        return path, _require_object_payload(load_json_file(path), label="sector RPS")


def active_rps_cache_mtime(fallback: str = RPS_CACHE_FILE) -> float:
    with f5_snapshot_read_boundary():
        return cache_file_mtime(resolve_active_rps_path(fallback))


def _require_object_payload(payload: Any, *, label: str) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise ValueError(f"active F5 {label} payload must be an object")
    return payload


def load_active_rps_payload(fallback: str = RPS_CACHE_FILE) -> dict[str, Any]:
    with f5_snapshot_read_boundary():
        path = resolve_active_rps_path(fallback)
        return _require_object_payload(load_json_file(path), label="RPS")


def load_active_sector_rps_payload(fallback: str = SECTOR_RPS_CACHE_FILE) -> dict[str, Any]:
    with f5_snapshot_read_boundary():
        path = resolve_active_sector_rps_path(fallback)
        return _require_object_payload(load_json_file(path), label="sector RPS")


__all__ = [
    "active_rps_cache_mtime",
    "load_active_rps_payload",
    "load_active_sector_rps_payload",
    "read_active_rps_bundle",
    "read_active_sector_rps_bundle",
    "resolve_active_rps_path",
    "resolve_active_sector_rps_path",
]
