# -*- coding: utf-8 -*-
"""Filesystem persistence for the LHB rolling pool."""

from __future__ import annotations

import copy
import hashlib
import json
import os
import sqlite3
import tempfile
import threading
import uuid
from contextlib import contextmanager
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CACHE_PATH = PROJECT_ROOT / "data" / "Cache" / "lhb_pool_30d.json"
DEFAULT_LEGACY_POOL_PATH = PROJECT_ROOT / "data" / "Cache" / "lhb_pool_20d.json"
DEFAULT_SINGLE_DAY_CACHE_PATH = PROJECT_ROOT / "data" / "Cache" / "lhb_cache.json"


class LhbRepositoryError(RuntimeError):
    """Raised when an LHB cache filesystem operation cannot be completed."""


@contextmanager
def _serialized_cache_write(cache_path: str):
    """Use a sidecar SQLite transaction as a cross-process write mutex."""

    lock_dir = os.path.join(tempfile.gettempdir(), "vcp_hunter_write_locks")
    os.makedirs(lock_dir, exist_ok=True)
    lock_key = hashlib.sha256(os.path.basename(cache_path).encode("utf-8")).hexdigest()[:16]
    lock_path = os.path.join(lock_dir, f"lhb-pool-{lock_key}.sqlite3")
    connection = sqlite3.connect(lock_path, timeout=30, isolation_level=None)
    try:
        connection.execute("PRAGMA busy_timeout=30000")
        connection.execute("BEGIN IMMEDIATE")
        yield
        connection.commit()
    finally:
        if connection.in_transaction:
            try:
                connection.rollback()
            except sqlite3.Error:
                pass
        connection.close()


class LhbPoolRepository:
    """Load and atomically merge date-level changes into the LHB cache."""

    _loaded_payload_lock = threading.RLock()
    _loaded_payload_cache: dict[str, tuple[tuple[int, int], dict]] = {}

    @staticmethod
    def default_paths() -> tuple[str, str, str]:
        return (str(DEFAULT_CACHE_PATH), str(DEFAULT_LEGACY_POOL_PATH), str(DEFAULT_SINGLE_DAY_CACHE_PATH))

    @staticmethod
    def cache_file_signature(cache_path: str) -> tuple[int, int] | None:
        try:
            stat = os.stat(cache_path)
        except OSError:
            return None
        return (int(stat.st_size), int(stat.st_mtime_ns))

    @classmethod
    def load_json_payload(cls, cache_path: str) -> dict:
        signature = cls.cache_file_signature(cache_path)
        if signature is None:
            return {}
        cache_key = os.path.abspath(cache_path)
        with cls._loaded_payload_lock:
            cached = cls._loaded_payload_cache.get(cache_key)
            if cached is not None and cached[0] == signature:
                return copy.deepcopy(cached[1])

        with open(cache_path, "r", encoding="utf-8") as handle:
            raw = json.load(handle)
        payload = raw if isinstance(raw, dict) else {}
        with cls._loaded_payload_lock:
            cls._loaded_payload_cache[cache_key] = (signature, copy.deepcopy(payload))
        return payload

    @classmethod
    def remember_json_payload(cls, cache_path: str, payload: dict) -> None:
        signature = cls.cache_file_signature(cache_path)
        if signature is None:
            return
        cache_key = os.path.abspath(cache_path)
        with cls._loaded_payload_lock:
            cls._loaded_payload_cache[cache_key] = (signature, copy.deepcopy(payload))

    @staticmethod
    def read_uncached_payload(cache_path: str) -> dict:
        if not os.path.exists(cache_path):
            return {}
        with open(cache_path, "r", encoding="utf-8") as stream:
            payload = json.load(stream)
        return payload if isinstance(payload, dict) else {}

    @classmethod
    def load_state(cls, cache_path: str, legacy_pool_path: str) -> tuple[dict, str]:
        selected_path = cache_path
        if not os.path.exists(selected_path) and os.path.exists(legacy_pool_path):
            selected_path = legacy_pool_path
        if not os.path.exists(selected_path):
            return {}, selected_path
        try:
            return cls.load_json_payload(selected_path), selected_path
        except (OSError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise LhbRepositoryError(str(exc)) from exc

    @staticmethod
    def _changed_days(
        current_data: dict[str, list[dict]],
        current_meta: dict[str, dict],
        persisted_data: dict[str, list[dict]],
        persisted_meta: dict[str, dict],
    ) -> tuple[set[str], set[str]]:
        deleted_days = set(persisted_data).difference(current_data)
        dirty_days = {
            day
            for day in set(current_data).union(persisted_data)
            if current_data.get(day) != persisted_data.get(day) or current_meta.get(day) != persisted_meta.get(day)
        }
        return deleted_days, dirty_days

    @classmethod
    def _latest_for_merge(
        cls,
        cache_path: str,
        persisted_data: dict[str, list[dict]],
        persisted_meta: dict[str, dict],
        persisted_last_fetch: str,
    ) -> dict:
        try:
            cache_exists = os.path.exists(cache_path)
            latest = cls.read_uncached_payload(cache_path)
        except (OSError, TypeError, ValueError, json.JSONDecodeError):
            cache_exists = False
            latest = {}
        if not cache_exists and persisted_data:
            return {
                "last_auto_fetch_date": persisted_last_fetch,
                "daily_data": persisted_data,
                "day_meta": persisted_meta,
            }
        return latest

    @staticmethod
    def _merge_payload(
        latest: dict,
        *,
        current_data: dict[str, list[dict]],
        current_meta: dict[str, dict],
        current_last_fetch: str,
        deleted_days: set[str],
        dirty_days: set[str],
        last_fetch_changed: bool,
        clear_requested: bool,
    ) -> dict:
        if clear_requested:
            merged_data: dict[str, list[dict]] = {}
            merged_meta: dict[str, dict] = {}
        else:
            stored_data = latest.get("daily_data", {})
            stored_meta = latest.get("day_meta", {})
            merged_data = copy.deepcopy(stored_data) if isinstance(stored_data, dict) else {}
            merged_meta = copy.deepcopy(stored_meta) if isinstance(stored_meta, dict) else {}
            for day in deleted_days:
                merged_data.pop(day, None)
                merged_meta.pop(day, None)
            for day in dirty_days:
                if day in current_data:
                    merged_data[day] = copy.deepcopy(current_data[day])
                    merged_meta[day] = copy.deepcopy(current_meta.get(day, {}))
        latest_last_fetch = str(latest.get("last_auto_fetch_date", "") or "")
        merged_last_fetch = current_last_fetch if clear_requested or last_fetch_changed else latest_last_fetch
        return {
            "version": 2,
            "last_auto_fetch_date": merged_last_fetch,
            "daily_data": merged_data,
            "day_meta": merged_meta,
        }

    @staticmethod
    def _write_payload_atomic(cache_path: str, payload: dict) -> None:
        temp_path = f"{cache_path}.{os.getpid()}.{uuid.uuid4().hex}.tmp"
        try:
            with open(temp_path, "w", encoding="utf-8") as stream:
                json.dump(payload, stream, ensure_ascii=False)
                stream.flush()
                os.fsync(stream.fileno())
            with open(temp_path, "r", encoding="utf-8") as stream:
                json.load(stream)
            os.replace(temp_path, cache_path)
        finally:
            try:
                os.remove(temp_path)
            except (FileNotFoundError, OSError):
                pass

    @classmethod
    def save_merged(
        cls,
        cache_path: str,
        *,
        current_data: dict[str, list[dict]],
        current_meta: dict[str, dict],
        current_last_fetch: str,
        persisted_data: dict[str, list[dict]],
        persisted_meta: dict[str, dict],
        persisted_last_fetch: str,
        clear_requested: bool,
    ) -> dict:
        deleted_days, dirty_days = cls._changed_days(current_data, current_meta, persisted_data, persisted_meta)
        last_fetch_changed = current_last_fetch != persisted_last_fetch
        try:
            os.makedirs(os.path.dirname(cache_path), exist_ok=True)
            with _serialized_cache_write(cache_path):
                latest = cls._latest_for_merge(
                    cache_path,
                    persisted_data,
                    persisted_meta,
                    persisted_last_fetch,
                )
                payload = cls._merge_payload(
                    latest,
                    current_data=current_data,
                    current_meta=current_meta,
                    current_last_fetch=current_last_fetch,
                    deleted_days=deleted_days,
                    dirty_days=dirty_days,
                    last_fetch_changed=last_fetch_changed,
                    clear_requested=clear_requested,
                )
                cls._write_payload_atomic(cache_path, payload)
        except (OSError, sqlite3.Error, TypeError, ValueError) as exc:
            raise LhbRepositoryError(str(exc)) from exc
        cls.remember_json_payload(cache_path, payload)
        return payload

    @staticmethod
    def read_legacy_single_day(cache_path: str) -> dict:
        if not os.path.exists(cache_path):
            return {}
        try:
            with open(cache_path, "r", encoding="utf-8") as handle:
                payload = json.load(handle)
            return payload if isinstance(payload, dict) else {}
        except (OSError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise LhbRepositoryError(str(exc)) from exc

    @staticmethod
    def remove_legacy_single_day(cache_path: str) -> None:
        try:
            os.remove(cache_path)
        except FileNotFoundError:
            return
        except OSError as exc:
            raise LhbRepositoryError(str(exc)) from exc


__all__ = [
    "DEFAULT_CACHE_PATH",
    "DEFAULT_LEGACY_POOL_PATH",
    "DEFAULT_SINGLE_DAY_CACHE_PATH",
    "LhbPoolRepository",
    "LhbRepositoryError",
]
