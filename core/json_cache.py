# -*- coding: utf-8 -*-
"""
core/json_cache.py
轻量 JSON 缓存助手：统一 UTF-8 编码、原子写盘和旧缓存文件清理。
"""

import json
import os

from core.exceptions import CacheIOError, DataFormatError


def _normalize_for_json(value):
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, dict):
        return {str(key): _normalize_for_json(val) for key, val in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_normalize_for_json(item) for item in value]
    if hasattr(value, "item"):
        try:
            return _normalize_for_json(value.item())
        except (TypeError, ValueError):
            pass
    if hasattr(value, "isoformat"):
        try:
            return value.isoformat()
        except (TypeError, ValueError):
            pass
    return str(value)


def load_json_file(path: str):
    """读取 JSON 缓存并返回 Python 对象。"""
    try:
        with open(path, "r", encoding="utf-8") as file_obj:
            return json.load(file_obj)
    except (FileNotFoundError, PermissionError, OSError) as exc:
        raise CacheIOError(f"cache read failed: {path}") from exc
    except json.JSONDecodeError as exc:
        raise DataFormatError(f"json payload invalid: {path}") from exc


def save_json_file(path: str, payload) -> None:
    """原子写入 JSON 缓存。"""
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
    """静默删除缓存文件；文件不存在时直接跳过。"""
    try:
        if path and os.path.exists(path):
            os.remove(path)
    except OSError:
        pass
