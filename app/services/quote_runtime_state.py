# -*- coding: utf-8 -*-
"""Atomic runtime state for the central quote service."""

from __future__ import annotations

import math
import threading
from collections.abc import Mapping
from dataclasses import dataclass, replace
from typing import cast


def _validated_bool(value: object, field_name: str) -> bool:
    if not isinstance(value, bool):
        raise TypeError(f"{field_name} must be bool")
    return value


def _validated_non_negative_int(value: object, field_name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{field_name} must be a non-negative integer")
    return value


def _validated_non_negative_float(value: object, field_name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{field_name} must be a finite non-negative number")
    normalized = float(value)
    if not math.isfinite(normalized) or normalized < 0:
        raise ValueError(f"{field_name} must be a finite non-negative number")
    return normalized


def _validated_text(value: object, field_name: str) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{field_name} must be str")
    return value.strip()


@dataclass(frozen=True, slots=True)
class QuoteRuntimeStateSnapshot:
    fetching: bool = False
    generation: int = 0
    started_at: float = 0.0
    failure_count: int = 0
    current_source: str = ""
    pending_reason: str = ""
    warned_slow: bool = False
    codes_count: int = 0


class QuoteRuntimeState:
    """Thread-safe state holder whose writes are atomic snapshot replacements."""

    __slots__ = ("_lock", "_snapshot")
    _FIELDS = frozenset(QuoteRuntimeStateSnapshot.__dataclass_fields__)
    _COUNTER_FIELDS = frozenset({"generation", "failure_count", "codes_count"})

    def __init__(
        self,
        *,
        fetching: bool = False,
        generation: int = 0,
        started_at: float = 0.0,
        failure_count: int = 0,
        current_source: str = "",
        pending_reason: str = "",
        warned_slow: bool = False,
        codes_count: int = 0,
    ) -> None:
        self._lock = threading.RLock()
        self._snapshot = self._validated(
            QuoteRuntimeStateSnapshot(
                fetching=fetching,
                generation=generation,
                started_at=started_at,
                failure_count=failure_count,
                current_source=current_source,
                pending_reason=pending_reason,
                warned_slow=warned_slow,
                codes_count=codes_count,
            )
        )

    @staticmethod
    def _validated(snapshot: QuoteRuntimeStateSnapshot) -> QuoteRuntimeStateSnapshot:
        return replace(
            snapshot,
            fetching=_validated_bool(snapshot.fetching, "fetching"),
            generation=_validated_non_negative_int(snapshot.generation, "generation"),
            started_at=_validated_non_negative_float(snapshot.started_at, "started_at"),
            failure_count=_validated_non_negative_int(snapshot.failure_count, "failure_count"),
            current_source=_validated_text(snapshot.current_source, "current_source"),
            pending_reason=_validated_text(snapshot.pending_reason, "pending_reason"),
            warned_slow=_validated_bool(snapshot.warned_slow, "warned_slow"),
            codes_count=_validated_non_negative_int(snapshot.codes_count, "codes_count"),
        )

    def read(self) -> QuoteRuntimeStateSnapshot:
        with self._lock:
            return self._snapshot

    def update(
        self,
        *,
        expected_generation: int | None = None,
        increments: Mapping[str, int] | None = None,
        **changes: object,
    ) -> QuoteRuntimeStateSnapshot:
        unknown = set(changes) - self._FIELDS
        if unknown:
            names = ", ".join(sorted(unknown))
            raise ValueError(f"unknown quote runtime state fields: {names}")
        counter_increments = dict(increments or {})
        unknown_counters = set(counter_increments) - self._COUNTER_FIELDS
        if unknown_counters:
            names = ", ".join(sorted(unknown_counters))
            raise ValueError(f"unknown quote runtime state counters: {names}")
        overlap = set(counter_increments) & set(changes)
        if overlap:
            names = ", ".join(sorted(overlap))
            raise ValueError(f"state fields cannot be updated and incremented together: {names}")
        for field_name, delta in counter_increments.items():
            if isinstance(delta, bool) or not isinstance(delta, int):
                raise ValueError(f"{field_name} increment must be an integer")
        with self._lock:
            current = self._snapshot
            if expected_generation is not None and current.generation != expected_generation:
                return current
            generation = current.generation + counter_increments.get("generation", 0)
            failure_count = current.failure_count + counter_increments.get("failure_count", 0)
            codes_count = current.codes_count + counter_increments.get("codes_count", 0)
            candidate = QuoteRuntimeStateSnapshot(
                fetching=cast(bool, changes.get("fetching", current.fetching)),
                generation=cast(int, changes.get("generation", generation)),
                started_at=cast(float, changes.get("started_at", current.started_at)),
                failure_count=cast(int, changes.get("failure_count", failure_count)),
                current_source=cast(str, changes.get("current_source", current.current_source)),
                pending_reason=cast(str, changes.get("pending_reason", current.pending_reason)),
                warned_slow=cast(bool, changes.get("warned_slow", current.warned_slow)),
                codes_count=cast(int, changes.get("codes_count", codes_count)),
            )
            self._snapshot = self._validated(candidate)
            return self._snapshot

    @property
    def fetching(self) -> bool:
        return self.read().fetching

    @property
    def generation(self) -> int:
        return self.read().generation

    @property
    def started_at(self) -> float:
        return self.read().started_at

    @property
    def failure_count(self) -> int:
        return self.read().failure_count

    @property
    def current_source(self) -> str:
        return self.read().current_source

    @property
    def pending_reason(self) -> str:
        return self.read().pending_reason

    @property
    def warned_slow(self) -> bool:
        return self.read().warned_slow

    @property
    def codes_count(self) -> int:
        return self.read().codes_count


class QuoteRuntimeStateCompatMixin:
    """Legacy CentralQuotes property surface backed by one runtime state."""

    state: QuoteRuntimeState

    def runtime_state_snapshot(self) -> QuoteRuntimeStateSnapshot:
        return self.state.read()

    @property
    def _is_fetching(self) -> bool:
        return self.state.fetching

    @_is_fetching.setter
    def _is_fetching(self, value: bool) -> None:
        self.state.update(fetching=bool(value))

    @property
    def _fetch_start_time(self) -> float:
        return self.state.started_at

    @_fetch_start_time.setter
    def _fetch_start_time(self, value: float) -> None:
        self.state.update(started_at=float(value))

    @property
    def _fetch_warned_slow(self) -> bool:
        return self.state.warned_slow

    @_fetch_warned_slow.setter
    def _fetch_warned_slow(self, value: bool) -> None:
        self.state.update(warned_slow=bool(value))

    @property
    def _fetch_codes_count(self) -> int:
        return self.state.codes_count

    @_fetch_codes_count.setter
    def _fetch_codes_count(self, value: int) -> None:
        self.state.update(codes_count=int(value))

    @property
    def _fetch_generation(self) -> int:
        return self.state.generation

    @_fetch_generation.setter
    def _fetch_generation(self, value: int) -> None:
        self.state.update(generation=int(value))

    @property
    def _pending_fetch_reason(self) -> str:
        return self.state.pending_reason

    @_pending_fetch_reason.setter
    def _pending_fetch_reason(self, value: str) -> None:
        self.state.update(pending_reason=str(value or ""))

    @property
    def _consecutive_failures(self) -> int:
        return self.state.failure_count

    @_consecutive_failures.setter
    def _consecutive_failures(self, value: int) -> None:
        self.state.update(failure_count=int(value))


__all__ = ["QuoteRuntimeState", "QuoteRuntimeStateCompatMixin", "QuoteRuntimeStateSnapshot"]
