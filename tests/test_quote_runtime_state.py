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


def test_quote_runtime_state_semantic_fetch_transitions_are_atomic() -> None:
    state = QuoteRuntimeState(current_source="eastmoney")

    begun, active = state.begin_fetch(2, started_at=10.5)
    duplicate_begin, duplicate_snapshot = state.begin_fetch(2, started_at=11.0)

    assert begun is True
    assert active.fetching is True
    assert active.generation == 1
    assert active.started_at == 10.5
    assert active.codes_count == 2
    assert duplicate_begin is False
    assert duplicate_snapshot is active

    stale, stale_snapshot = state.finish_fetch(0)
    finished, idle = state.finish_fetch(active.generation)
    duplicate_finish, duplicate_idle = state.fail_fetch(active.generation)

    assert stale is False
    assert stale_snapshot is active
    assert finished is True
    assert idle.fetching is False
    assert idle.started_at == 0.0
    assert idle.warned_slow is False
    assert idle.codes_count == 0
    assert duplicate_finish is False
    assert duplicate_idle is idle


def test_quote_runtime_state_try_update_distinguishes_apply_stale_and_noop() -> None:
    state = QuoteRuntimeState(generation=2)

    applied, updated = state.try_update(expected_generation=2, pending_reason="cache_reload")
    stale, stale_snapshot = state.try_update(expected_generation=1, pending_reason="timer")
    noop, noop_snapshot = state.try_update(expected_generation=2, pending_reason="cache_reload")

    assert applied is True
    assert updated.pending_reason == "cache_reload"
    assert stale is False
    assert stale_snapshot is updated
    assert noop is False
    assert noop_snapshot is updated


def test_quote_runtime_state_pending_reason_merge_and_consume_are_atomic() -> None:
    state = QuoteRuntimeState()

    remembered_timer, timer_snapshot = state.remember_pending_reason("timer")
    remembered_reload, reload_snapshot = state.remember_pending_reason("cache_reload")
    downgraded, unchanged = state.remember_pending_reason("timer")
    consumed, consumed_snapshot = state.consume_pending_reason()
    empty, empty_snapshot = state.consume_pending_reason()

    assert remembered_timer is True
    assert timer_snapshot.pending_reason == "timer"
    assert remembered_reload is True
    assert reload_snapshot.pending_reason == "cache_reload"
    assert downgraded is False
    assert unchanged is reload_snapshot
    assert consumed == "cache_reload"
    assert consumed_snapshot.pending_reason == ""
    assert empty == ""
    assert empty_snapshot is consumed_snapshot


def test_quote_runtime_state_only_one_concurrent_begin_wins() -> None:
    state = QuoteRuntimeState()
    barrier = threading.Barrier(5)
    results: list[bool] = []
    result_lock = threading.Lock()

    def begin() -> None:
        barrier.wait()
        applied, _snapshot = state.begin_fetch(1, started_at=1.0)
        with result_lock:
            results.append(applied)

    threads = [threading.Thread(target=begin) for _ in range(4)]
    for thread in threads:
        thread.start()
    barrier.wait()
    for thread in threads:
        thread.join(timeout=2)
        assert not thread.is_alive()

    assert results.count(True) == 1
    assert results.count(False) == 3


def test_quote_runtime_state_finish_and_fail_compete_for_one_terminal_transition() -> None:
    state = QuoteRuntimeState()
    _begun, active = state.begin_fetch(1, started_at=1.0)
    barrier = threading.Barrier(3)
    results: list[bool] = []
    result_lock = threading.Lock()

    def end(transition) -> None:
        barrier.wait()
        applied, _snapshot = transition(active.generation)
        with result_lock:
            results.append(applied)

    threads = [
        threading.Thread(target=end, args=(state.finish_fetch,)),
        threading.Thread(target=end, args=(state.fail_fetch,)),
    ]
    for thread in threads:
        thread.start()
    barrier.wait()
    for thread in threads:
        thread.join(timeout=2)
        assert not thread.is_alive()

    assert sorted(results) == [False, True]


def test_quote_runtime_state_shutdown_is_idempotent_and_absorbing() -> None:
    state = QuoteRuntimeState(failure_count=2, current_source="sina", pending_reason="cache_reload")
    _begun, active = state.begin_fetch(3, started_at=2.0)

    shutdown = state.shutdown()
    repeated = state.shutdown()
    began_after_shutdown, after_begin = state.begin_fetch(1, started_at=3.0)
    updated_after_shutdown, after_update = state.try_update(fetching=True)

    assert shutdown.generation == active.generation + 1
    assert shutdown.fetching is False
    assert shutdown.started_at == 0.0
    assert shutdown.pending_reason == ""
    assert shutdown.codes_count == 0
    assert shutdown.current_source == "sina"
    assert shutdown.failure_count == 2
    assert repeated is shutdown
    assert began_after_shutdown is False
    assert after_begin is shutdown
    assert updated_after_shutdown is False
    assert after_update is shutdown


@pytest.mark.parametrize(
    ("codes_count", "started_at", "message"),
    [
        (0, 1.0, "codes_count"),
        (1, 0.0, "started_at"),
    ],
)
def test_quote_runtime_state_begin_rejects_invalid_active_fields_without_partial_write(
    codes_count,
    started_at,
    message,
) -> None:
    state = QuoteRuntimeState()
    original = state.read()

    with pytest.raises(ValueError, match=message):
        state.begin_fetch(codes_count, started_at=started_at)

    assert state.read() is original
