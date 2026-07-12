"""Persistence repository for stock-context snapshots."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import infra.storage.data_store as data_store_module
from core.exceptions import CacheIOError, DataFormatError
from core.runtime_paths import PROJECT_ROOT
from infra.storage.json_cache_repository import load_json_file


def project_root() -> Path:
    return Path(PROJECT_ROOT)


def coerce_cache_rows(value) -> list[dict]:
    if not isinstance(value, (list, tuple)):
        return []
    return [dict(row) for row in value if isinstance(row, dict)]


def load_scan_cache_rows(*, root: str | Path | None = None) -> list[dict]:
    try:
        payload = data_store_module.DataStore().load_json("scan_cache", default={})
    except (AttributeError, OSError, RuntimeError, TypeError, ValueError, sqlite3.Error):
        payload = None
    if not payload:
        old_path = Path(root or project_root()) / "data" / "scan_cache.json"
        try:
            with old_path.open("r", encoding="utf-8") as handle:
                payload = json.load(handle)
        except (OSError, TypeError, ValueError, json.JSONDecodeError):
            payload = None
    if isinstance(payload, dict):
        return coerce_cache_rows(payload.get("results", []))
    return coerce_cache_rows(payload)


def load_named_cache_rows(
    filename: str,
    *,
    root: str | Path | None = None,
    payload_key: str = "rows",
) -> list[dict]:
    cache_path = Path(root or project_root()) / "data" / "Cache" / filename
    try:
        payload = load_json_file(str(cache_path))
    except (
        AttributeError,
        CacheIOError,
        DataFormatError,
        OSError,
        RuntimeError,
        TypeError,
        ValueError,
    ):
        return []
    if not isinstance(payload, dict):
        return []
    return coerce_cache_rows(payload.get(payload_key, []))


def load_earnings_state_payload() -> tuple[dict, str]:
    store = data_store_module.data_store
    try:
        payload = store.load_earnings_state() or {}
        row = store.fetch_one(
            "SELECT updated_at FROM kv_store WHERE key = ?",
            ("earnings_state",),
            default={},
        ) or {}
    except (AttributeError, OSError, RuntimeError, TypeError, ValueError, sqlite3.Error):
        return {}, ""
    updated_at = str(row.get("updated_at") or "").strip() if isinstance(row, dict) else ""
    return (payload if isinstance(payload, dict) else {}), updated_at


def lhb_pool_cache_signature(*, root: str | Path | None = None) -> tuple[str, int, int] | None:
    cache_dir = Path(root or project_root()) / "data" / "Cache"
    cache_path = cache_dir / "lhb_pool_30d.json"
    legacy_path = cache_dir / "lhb_pool_20d.json"
    if not cache_path.exists() and legacy_path.exists():
        cache_path = legacy_path
    try:
        stat = cache_path.stat()
    except OSError:
        return None
    return (str(cache_path), int(stat.st_size), int(stat.st_mtime_ns))


__all__ = [
    "coerce_cache_rows",
    "lhb_pool_cache_signature",
    "load_earnings_state_payload",
    "load_named_cache_rows",
    "load_scan_cache_rows",
    "project_root",
]
