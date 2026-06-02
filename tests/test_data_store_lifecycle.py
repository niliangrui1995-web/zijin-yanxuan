# -*- coding: utf-8 -*-
import weakref


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
