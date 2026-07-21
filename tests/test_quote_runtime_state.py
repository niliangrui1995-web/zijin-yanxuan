# -*- coding: utf-8 -*-
from __future__ import annotations

import threading

import pytest

from app.services.quote_runtime_state import QuoteRuntimeState


def test_quote_runtime_state_defaults_and_update() -> None:
    state = QuoteRuntimeState()

    assert state.fetching is False
    assert state.generation == 0
    assert state.started_at == 0.0
    assert state.failure_count == 0
    assert state.current_source == ""

    updated = state.update(
        fetching=True,
        generation=1,
        started_at=10.5,
        failure_count=2,
        current_source="eastmoney",
    )

    assert updated.fetching is True
    assert state.read() is updated
    assert state.current_source == "eastmoney"


def test_quote_runtime_state_rejects_invalid_updates_without_partial_write() -> None:
    state = QuoteRuntimeState(generation=3, current_source="sina")
    original = state.read()

    with pytest.raises(ValueError, match="failure_count"):
        state.update(fetching=True, failure_count=-1)
    with pytest.raises(ValueError, match="unknown"):
        state.update(unsupported=True)

    assert state.read() is original
    with pytest.raises(AttributeError):
        state.fetching = True  # type: ignore[misc]

    stale_update = state.update(expected_generation=2, fetching=True)
    assert stale_update is original
    assert state.fetching is False


def test_quote_runtime_state_concurrent_reads_are_atomic() -> None:
    state = QuoteRuntimeState()
    finished = threading.Event()
    all_readers_observed = threading.Event()
    errors: list[str] = []
    observed: set[int] = set()
    observed_lock = threading.Lock()

    def reader(reader_id: int) -> None:
        while not finished.is_set():
            snapshot = state.read()
            if snapshot.current_source and snapshot.current_source != str(snapshot.generation):
                errors.append(f"{snapshot.generation}/{snapshot.current_source}")
                return
            if snapshot.generation:
                with observed_lock:
                    observed.add(reader_id)
                    if len(observed) == 4:
                        all_readers_observed.set()

    readers = [threading.Thread(target=reader, args=(reader_id,)) for reader_id in range(4)]
    for thread in readers:
        thread.start()

    state.update(fetching=True, generation=1, started_at=1.0, current_source="1")
    assert all_readers_observed.wait(2)
    for generation in range(2, 300):
        state.update(
            fetching=bool(generation % 2),
            generation=generation,
            started_at=float(generation),
            current_source=str(generation),
        )
    finished.set()

    for thread in readers:
        thread.join(timeout=2)
        assert not thread.is_alive()
    assert errors == []
    assert observed == set(range(4))


def test_quote_runtime_state_counter_increments_are_atomic() -> None:
    state = QuoteRuntimeState()

    def increment() -> None:
        for _ in range(250):
            state.update(increments={"generation": 1, "failure_count": 1})

    threads = [threading.Thread(target=increment) for _ in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=2)
        assert not thread.is_alive()

    assert state.generation == 1_000
    assert state.failure_count == 1_000

    with pytest.raises(ValueError, match="updated and incremented"):
        state.update(generation=1, increments={"generation": 1})
