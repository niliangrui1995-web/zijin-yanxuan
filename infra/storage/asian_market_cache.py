"""Filesystem repository for Asian-market JSON caches."""

from __future__ import annotations

import json
import os
from collections.abc import Mapping

from core.runtime_paths import CACHE_DIR

ASIAN_KLINE_CACHE = os.path.join(CACHE_DIR, "asian_klines_latest.json")
ASIAN_REALTIME_CACHE = os.path.join(CACHE_DIR, "asian_rt_latest.json")


def read_json_cache(path: str, *, default=None):
    try:
        with open(path, "r", encoding="utf-8") as file_obj:
            return json.load(file_obj)
    except (FileNotFoundError, PermissionError, OSError, TypeError, ValueError, json.JSONDecodeError):
        return default


def write_json_cache(path: str, payload: Mapping | list) -> None:
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    temp_path = f"{path}.tmp"
    try:
        with open(temp_path, "w", encoding="utf-8") as file_obj:
            json.dump(payload, file_obj, ensure_ascii=False)
            file_obj.flush()
            os.fsync(file_obj.fileno())
        os.replace(temp_path, path)
    except (PermissionError, OSError, TypeError, ValueError):
        try:
            if os.path.exists(temp_path):
                os.remove(temp_path)
        except OSError:
            pass
        raise


def cache_mtime(path: str) -> float:
    try:
        return float(os.path.getmtime(path))
    except (OSError, TypeError, ValueError):
        return 0.0


__all__ = [
    "ASIAN_KLINE_CACHE",
    "ASIAN_REALTIME_CACHE",
    "cache_mtime",
    "read_json_cache",
    "write_json_cache",
]
