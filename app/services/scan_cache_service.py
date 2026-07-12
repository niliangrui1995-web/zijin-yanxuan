"""Application service for the persisted scan-result snapshot."""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

from core.runtime_paths import PROJECT_ROOT
from infra.storage.data_store import DataStore

SCAN_CACHE_KEY = "scan_cache"


def save_scan_cache(results: list, params_snapshot: dict) -> str:
    store = DataStore()
    if not results:
        store.delete_key(SCAN_CACHE_KEY)
        return "cleared"
    store.save_json(
        SCAN_CACHE_KEY,
        {
            "saved_at": dt.datetime.now().isoformat(),
            "count": len(results),
            "params": dict(params_snapshot),
            "results": list(results),
        },
    )
    return "saved"


def _load_legacy_scan_cache(path: Path) -> dict:
    try:
        with path.open("r", encoding="utf-8") as file_obj:
            payload = json.load(file_obj)
    except (FileNotFoundError, PermissionError, OSError, TypeError, ValueError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def load_scan_cache() -> tuple[dict, bool]:
    store = DataStore()
    payload = store.load_json(SCAN_CACHE_KEY) or {}
    if isinstance(payload, dict) and payload:
        return payload, False
    legacy_path = Path(PROJECT_ROOT) / "data" / "scan_cache.json"
    payload = _load_legacy_scan_cache(legacy_path)
    if not payload:
        return {}, False
    store.save_json(SCAN_CACHE_KEY, payload)
    try:
        legacy_path.replace(legacy_path.with_suffix(f"{legacy_path.suffix}.migrated"))
    except OSError:
        pass
    return payload, True


__all__ = ["load_scan_cache", "save_scan_cache"]
