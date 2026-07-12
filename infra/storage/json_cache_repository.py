# -*- coding: utf-8 -*-
"""UTF-8 JSON cache repository with atomic writes and file metadata."""

from __future__ import annotations

import json
import os
from typing import Any

from core.exceptions import CacheIOError, DataFormatError


def _normalize_special_value(value: object) -> tuple[bool, object]:
    for attribute in ("item", "isoformat"):
        operation = getattr(value, attribute, None)
        if not callable(operation):
            continue
        try:
            result = operation()
        except (TypeError, ValueError):
            continue
        return True, _normalize_for_json(result) if attribute == "item" else result
    return False, value


def _normalize_for_json(value: object) -> object:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, dict):
        return {str(key): _normalize_for_json(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_normalize_for_json(item) for item in value]
    converted, normalized = _normalize_special_value(value)
    if converted:
        return normalized
    return str(value)


def load_json_file(path: str) -> Any:
    """Read a JSON cache or raise the stable cache exceptions."""

    try:
        with open(path, "r", encoding="utf-8") as file_obj:
            return json.load(file_obj)
    except (FileNotFoundError, PermissionError, OSError) as exc:
        raise CacheIOError(f"cache read failed: {path}") from exc
    except json.JSONDecodeError as exc:
        raise DataFormatError(f"json payload invalid: {path}") from exc


def save_json_file(path: str, payload: object) -> None:
    """Atomically write a normalized JSON cache."""

    parent_dir = os.path.dirname(path)
    temp_path = f"{path}.tmp"
    try:
        if parent_dir:
            os.makedirs(parent_dir, exist_ok=True)
        with open(temp_path, "w", encoding="utf-8") as file_obj:
            json.dump(_normalize_for_json(payload), file_obj, ensure_ascii=False)
            file_obj.flush()
            os.fsync(file_obj.fileno())
        os.replace(temp_path, path)
    except (PermissionError, OSError) as exc:
        try:
            if os.path.exists(temp_path):
                os.remove(temp_path)
        except OSError:
            pass
        raise CacheIOError(f"cache write failed: {path}") from exc


def remove_cache_file(path: str) -> None:
    """Best-effort cache removal."""

    try:
        if path and os.path.exists(path):
            os.remove(path)
    except OSError:
        pass


def cache_file_signature(path: str) -> tuple[int, int] | None:
    try:
        stat_result = os.stat(path)
    except (FileNotFoundError, OSError, TypeError, ValueError):
        return None
    return int(stat_result.st_mtime_ns), int(stat_result.st_size)


def cache_file_mtime(path: str) -> float:
    try:
        return float(os.path.getmtime(path))
    except (FileNotFoundError, OSError, TypeError, ValueError):
        return 0.0


def cache_file_exists(path: str) -> bool:
    try:
        return bool(path) and os.path.isfile(path)
    except (OSError, TypeError, ValueError):
        return False


__all__ = [
    "cache_file_exists",
    "cache_file_mtime",
    "cache_file_signature",
    "load_json_file",
    "remove_cache_file",
    "save_json_file",
]
