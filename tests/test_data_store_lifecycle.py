# -*- coding: utf-8 -*-
import sqlite3
import weakref

import pytest


def test_close_all_closes_tracked_instances_after_singleton_reset(monkeypatch, tmp_path):
    """close_all closes instances even if tests reset the singleton reference."""
    from core.data_store import DataStore

    monkeypatch.setattr(DataStore, "_instance", None)
    monkeypatch.setattr(DataStore, "_instances", weakref.WeakSet())

    db_path = tmp_path / "tracked_store.db"
    store = DataStore(db_path=str(db_path))
    DataStore._instance = None

    DataStore.close_all()
    DataStore.close_all()

    assert store._closed is True


def test_closed_store_operations_fail_as_sqlite_lifecycle_error(monkeypatch, tmp_path):
    from core.data_store import DataStore

    monkeypatch.setattr(DataStore, "_instance", None)
    monkeypatch.setattr(DataStore, "_instances", weakref.WeakSet())

    db_path = tmp_path / "closed_store.db"
    store = DataStore(db_path=str(db_path))
    store.close()

    assert store.is_closed is True
    with pytest.raises(sqlite3.ProgrammingError, match="closed"):
        store.save_json("late_write", {"value": 1})


def test_close_is_reentrant_when_destructor_cleanup_runs_inside_store_lock(monkeypatch, tmp_path):
    """Coverage/GC can finalize an old singleton while the shared store lock is held."""
    from core.data_store import DataStore

    monkeypatch.setattr(DataStore, "_instance", None)
    monkeypatch.setattr(DataStore, "_instances", weakref.WeakSet())
    store = DataStore(db_path=str(tmp_path / "reentrant_close.db"))

    with DataStore._lock:
        store.close()

    assert store.is_closed is True
