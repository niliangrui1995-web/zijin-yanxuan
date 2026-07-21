# -*- coding: utf-8 -*-
"""Immutable realtime quote snapshot model."""

from __future__ import annotations

import math
import time
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any


def _freeze_value(value: Any) -> Any:
    if isinstance(value, Mapping):
        return MappingProxyType({key: _freeze_value(item) for key, item in value.items()})
    if isinstance(value, (list, tuple)):
        return tuple(_freeze_value(item) for item in value)
    if isinstance(value, (set, frozenset)):
        return frozenset(_freeze_value(item) for item in value)
    if isinstance(value, (bytearray, memoryview)):
        return bytes(value)
    return value


def _freeze_quotes(quotes: Mapping[str, Mapping[str, object]]) -> Mapping[str, Mapping[str, object]]:
    frozen: dict[str, Mapping[str, object]] = {}
    for raw_code, payload in quotes.items():
        code = str(raw_code)
        if not isinstance(payload, Mapping):
            raise TypeError(f"quote payload for {code!r} must be a mapping")
        frozen[code] = MappingProxyType({str(key): _freeze_value(value) for key, value in payload.items()})
    return MappingProxyType(frozen)


@dataclass(frozen=True, slots=True, eq=False)
class QuoteSnapshot(Mapping[str, Mapping[str, object]]):
    """Versioned immutable quote data with Mapping compatibility."""

    version: int
    timestamp: float
    quotes: Mapping[str, Mapping[str, object]]

    def __post_init__(self) -> None:
        if isinstance(self.version, bool) or not isinstance(self.version, int) or self.version < 0:
            raise ValueError("version must be a non-negative integer")
        if isinstance(self.timestamp, bool) or not isinstance(self.timestamp, (int, float)):
            raise ValueError("timestamp must be a finite non-negative number")
        timestamp = float(self.timestamp)
        if not math.isfinite(timestamp) or timestamp < 0:
            raise ValueError("timestamp must be a finite non-negative number")
        if not isinstance(self.quotes, Mapping):
            raise TypeError("quotes must be a mapping")

        object.__setattr__(self, "timestamp", timestamp)
        object.__setattr__(self, "quotes", _freeze_quotes(self.quotes))

    @classmethod
    def empty(cls, *, version: int = 0, timestamp: float | None = None) -> QuoteSnapshot:
        return cls(
            version=version,
            timestamp=time.time() if timestamp is None else timestamp,
            quotes={},
        )

    @classmethod
    def create(
        cls,
        *,
        version: int,
        quotes: Mapping[str, Mapping[str, object]],
        timestamp: float | None = None,
    ) -> QuoteSnapshot:
        return cls(
            version=version,
            timestamp=time.time() if timestamp is None else timestamp,
            quotes=quotes,
        )

    def __getitem__(self, key: str) -> Mapping[str, object]:
        return self.quotes[key]

    def __iter__(self) -> Iterator[str]:
        return iter(self.quotes)

    def __len__(self) -> int:
        return len(self.quotes)

    def __eq__(self, other: object) -> bool:
        if isinstance(other, QuoteSnapshot):
            return (
                self.version == other.version
                and self.timestamp == other.timestamp
                and dict(self.quotes) == dict(other.quotes)
            )
        if isinstance(other, Mapping):
            return dict(self.quotes) == dict(other)
        return NotImplemented
