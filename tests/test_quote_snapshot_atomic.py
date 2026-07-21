# -*- coding: utf-8 -*-
from __future__ import annotations

import threading
from collections.abc import Mapping

import pytest

from core.global_store import GlobalStore
from core.state.quote_snapshot import QuoteSnapshot
from ui.tabs.watchlist_tab import _copy_quote_snapshot


def test_quote_snapshot_supports_empty_data() -> None:
    store = GlobalStore()

    snapshot = store.get_latest_quotes()

    assert isinstance(snapshot, QuoteSnapshot)
    assert isinstance(snapshot, Mapping)
    assert snapshot.version == 0
    assert snapshot.timestamp > 0
    assert snapshot.quotes == {}


def test_quote_snapshot_replace_keeps_old_snapshot_immutable() -> None:
    store = GlobalStore()
    old_snapshot = store.get_latest_quotes()

    new_snapshot = store.replace_quotes({"000001": {"close": 12.5}})

    assert store.get_latest_quotes() is new_snapshot
    assert new_snapshot.version == old_snapshot.version + 1
    assert old_snapshot == {}
    assert new_snapshot["000001"]["close"] == 12.5
    with pytest.raises(TypeError):
        new_snapshot.quotes["000002"] = {}  # type: ignore[index]
    with pytest.raises(TypeError):
        new_snapshot["000001"]["close"] = 13.0  # type: ignore[index]


def test_quote_snapshot_remains_compatible_with_watchlist_copy_boundary() -> None:
    snapshot = QuoteSnapshot.create(version=1, timestamp=1, quotes={"000001": {"close": 12.5}})

    assert _copy_quote_snapshot(snapshot) == {"000001": {"close": 12.5}}


def test_quote_snapshot_concurrent_readers_only_see_complete_replacements() -> None:
    store = GlobalStore()
    reader_count = 6
    writes = 300
    start = threading.Barrier(reader_count + 1)
    finished = threading.Event()
    all_readers_observed = threading.Event()
    errors: list[str] = []
    errors_lock = threading.Lock()
    observed: set[int] = set()

    def reader(reader_id: int) -> None:
        start.wait()
        while not finished.is_set():
            snapshot = store.get_latest_quotes()
            payload = snapshot.get("000001")
            if payload is None:
                continue
            if payload.get("version") != snapshot.version:
                with errors_lock:
                    errors.append(f"partial snapshot: {snapshot.version}/{payload!r}")
                return
            with errors_lock:
                observed.add(reader_id)
                if len(observed) == reader_count:
                    all_readers_observed.set()

    threads = [threading.Thread(target=reader, args=(reader_id,)) for reader_id in range(reader_count)]
    for thread in threads:
        thread.start()

    start.wait()
    snapshot = store.replace_quotes({"000001": {"version": 1}})
    assert snapshot.version == 1
    assert all_readers_observed.wait(2)
    for version in range(2, writes + 1):
        snapshot = store.replace_quotes({"000001": {"version": version}})
        assert snapshot.version == version
    finished.set()

    for thread in threads:
        thread.join(timeout=2)
        assert not thread.is_alive()
    assert errors == []
    assert observed == set(range(reader_count))


def test_quote_snapshot_rejects_abnormal_data_without_replacing_current_snapshot() -> None:
    store = GlobalStore()
    original = store.replace_quotes({"000001": {"close": 10.0}})

    with pytest.raises(TypeError, match="must be a mapping"):
        store.merge_quotes({"000001": object()})

    assert store.get_latest_quotes() is original
    assert store.merge_quotes(None) is original

    with pytest.raises(ValueError, match="version"):
        QuoteSnapshot(version=-1, timestamp=0, quotes={})
    with pytest.raises(ValueError, match="timestamp"):
        QuoteSnapshot(version=1, timestamp=float("nan"), quotes={})


def test_quote_snapshot_detaches_mutable_binary_leaf_values() -> None:
    store = GlobalStore()
    raw = bytearray(b"quote")

    snapshot = store.replace_quotes({"000001": {"raw": raw}})
    raw[0] = ord("Q")

    assert snapshot["000001"]["raw"] == b"quote"
