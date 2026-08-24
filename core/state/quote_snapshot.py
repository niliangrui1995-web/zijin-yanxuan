# -*- coding: utf-8 -*-
"""Immutable realtime quote snapshot model."""

from __future__ import annotations

import math
import time
from collections.abc import Iterable, Iterator, Mapping
from dataclasses import InitVar, dataclass, field
from types import MappingProxyType

# Recursive input contract enforced when ``strict_values=True``.  The default
# compatibility mode still accepts ``object`` so unknown legacy leaves can be observed.
type QuoteScalar = None | bool | int | float | complex | str | bytes
type QuoteValue = (
    QuoteScalar
    | Mapping[object, QuoteValue]
    | list[QuoteValue]
    | tuple[QuoteValue, ...]
    | set[QuoteValue]
    | frozenset[QuoteValue]
    | bytearray
    | memoryview
)
type QuotePayload = Mapping[str, object]
type QuoteMap = Mapping[str, QuotePayload]
type MutableQuoteMap = dict[str, dict[str, object]]

_SUPPORTED_SCALAR_TYPES = (type(None), bool, int, float, complex, str, bytes)
_QUARANTINED_VALUE = object()
TOTAL_SHARES_KEY = "total_shares"
_LEGACY_TOTAL_SHARES_KEYS = ("_zongguben", "zongguben")


def coerce_quote_number(value: object) -> float:
    if value in (None, "", "-", "--"):
        return 0.0
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def get_total_shares(*entries: Mapping | None) -> float:
    """Read canonical share capital while accepting legacy quote payloads."""
    for entry in entries:
        if not isinstance(entry, Mapping):
            continue
        for key in (TOTAL_SHARES_KEY, *_LEGACY_TOTAL_SHARES_KEYS):
            total_shares = coerce_quote_number(entry.get(key))
            if total_shares > 0:
                return total_shares
    return 0.0


def get_missing_a_share_finance_codes(codes: Iterable[str], snapshot: Mapping[str, Mapping] | None) -> list[str]:
    snapshot = snapshot or {}
    missing: list[str] = []
    seen: set[str] = set()
    for raw_code in codes or []:
        code = str(raw_code or "").strip()
        if len(code) != 6 or not code.isdigit() or code in seen:
            continue
        seen.add(code)
        if get_total_shares(snapshot.get(code)) <= 0:
            missing.append(code)
    return missing


def _unknown_type_name(value: object) -> str:
    value_type = type(value)
    return f"{value_type.__module__}.{value_type.__qualname__}"


def _canonical_text(value: object) -> str:
    converted = value if isinstance(value, str) else str(value)
    if type(converted) is str:
        return converted
    return str.__getitem__(converted, slice(None))


def _unsupported_leaf(
    value: object,
    *,
    strict_values: bool,
    unknown_leaf_types: dict[str, int],
) -> object:
    type_name = _unknown_type_name(value)
    if strict_values:
        raise TypeError(f"unsupported mutable quote leaf type: {type_name}")
    unknown_leaf_types[type_name] = unknown_leaf_types.get(type_name, 0) + 1
    return _QUARANTINED_VALUE


def _freeze_value(
    value: object,
    *,
    strict_values: bool,
    unknown_leaf_types: dict[str, int],
) -> object:
    if isinstance(value, Mapping):
        frozen_mapping: dict[object, object] = {}
        quarantined = False
        for key, item in value.items():
            frozen_key = _freeze_value(
                key,
                strict_values=strict_values,
                unknown_leaf_types=unknown_leaf_types,
            )
            frozen_item = _freeze_value(
                item,
                strict_values=strict_values,
                unknown_leaf_types=unknown_leaf_types,
            )
            if frozen_key is _QUARANTINED_VALUE or frozen_item is _QUARANTINED_VALUE:
                quarantined = True
                continue
            try:
                hash(frozen_key)
            except TypeError:
                type_name = _unknown_type_name(key)
                if strict_values:
                    raise TypeError(f"unsupported mutable quote leaf type: {type_name}") from None
                unknown_leaf_types[type_name] = unknown_leaf_types.get(type_name, 0) + 1
                quarantined = True
                continue
            frozen_mapping[frozen_key] = frozen_item
        return _QUARANTINED_VALUE if quarantined else MappingProxyType(frozen_mapping)
    if isinstance(value, (list, tuple)):
        frozen_sequence = tuple(
            _freeze_value(
                item,
                strict_values=strict_values,
                unknown_leaf_types=unknown_leaf_types,
            )
            for item in value
        )
        if any(item is _QUARANTINED_VALUE for item in frozen_sequence):
            return _QUARANTINED_VALUE
        return frozen_sequence
    if isinstance(value, (set, frozenset)):
        frozen_set_items: list[object] = []
        for item in value:
            frozen_item = _freeze_value(
                item,
                strict_values=strict_values,
                unknown_leaf_types=unknown_leaf_types,
            )
            if frozen_item is _QUARANTINED_VALUE:
                return _QUARANTINED_VALUE
            try:
                hash(frozen_item)
            except TypeError:
                return _unsupported_leaf(
                    item,
                    strict_values=strict_values,
                    unknown_leaf_types=unknown_leaf_types,
                )
            frozen_set_items.append(frozen_item)
        return frozenset(frozen_set_items)
    if isinstance(value, (bytearray, memoryview)):
        return bytes(memoryview(value))
    if type(value) in _SUPPORTED_SCALAR_TYPES:
        return value
    if isinstance(value, int):
        return int.__add__(value, 0)
    if isinstance(value, float):
        return float.__add__(value, 0.0)
    if isinstance(value, complex):
        return complex.__add__(value, 0j)
    if isinstance(value, str):
        return _canonical_text(value)
    if isinstance(value, bytes):
        return bytes(memoryview(value))

    return _unsupported_leaf(
        value,
        strict_values=strict_values,
        unknown_leaf_types=unknown_leaf_types,
    )


def _freeze_quotes(
    quotes: QuoteMap,
    *,
    strict_values: bool,
) -> tuple[QuoteMap, int, tuple[str, ...]]:
    frozen: dict[str, Mapping[str, object]] = {}
    unknown_leaf_types: dict[str, int] = {}
    for raw_code, payload in quotes.items():
        code = _canonical_text(raw_code)
        if not isinstance(payload, Mapping):
            raise TypeError(f"quote payload for {code!r} must be a mapping")
        frozen_payload: dict[str, object] = {}
        for key, value in payload.items():
            frozen_value = _freeze_value(
                value,
                strict_values=strict_values,
                unknown_leaf_types=unknown_leaf_types,
            )
            if frozen_value is _QUARANTINED_VALUE:
                continue
            frozen_payload[_canonical_text(key)] = frozen_value
        frozen[code] = MappingProxyType(frozen_payload)
    return (
        MappingProxyType(frozen),
        sum(unknown_leaf_types.values()),
        tuple(sorted(unknown_leaf_types)),
    )


def _non_null_frozen_quote_updates(payload: QuotePayload) -> dict[str, object]:
    return {key: value for key, value in payload.items() if value is not None}


def _updated_frozen_quote_payload(
    existing_payload: QuotePayload | None,
    incoming_payload: QuotePayload,
) -> QuotePayload | None:
    updates = _non_null_frozen_quote_updates(incoming_payload)
    if not updates:
        return None
    if existing_payload is None:
        return MappingProxyType(updates)
    if all(existing_payload.get(key) == value for key, value in updates.items()):
        return None
    return MappingProxyType({**existing_payload, **updates})


def _merge_frozen_quotes(existing: QuoteMap, incoming: QuoteMap) -> QuoteMap:
    """Merge sanitized incoming entries while reusing unchanged frozen payloads."""
    merged: dict[str, Mapping[str, object]] | None = None
    for code, incoming_payload in incoming.items():
        updated_payload = _updated_frozen_quote_payload(existing.get(code), incoming_payload)
        if updated_payload is None:
            continue
        if merged is None:
            merged = dict(existing)
        merged[code] = updated_payload
    return existing if merged is None else MappingProxyType(merged)


def _copy_frozen_value(value: object) -> object:
    if isinstance(value, Mapping):
        return {key: _copy_frozen_value(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return tuple(_copy_frozen_value(item) for item in value)
    if isinstance(value, frozenset):
        return frozenset(_copy_frozen_value(item) for item in value)
    return value


def snapshot_to_mutable_dict(
    snapshot: QuoteMap | None,
    codes: Iterable[object] | None = None,
) -> MutableQuoteMap:
    """Copy a read-only quote snapshot across its mutable mapping boundary."""
    if not isinstance(snapshot, Mapping):
        return {}
    source: Mapping[str, QuotePayload]
    if isinstance(snapshot, QuoteSnapshot):
        source = snapshot
    else:
        source = QuoteSnapshot.create(
            version=0,
            timestamp=0,
            quotes={
                raw_code: payload
                for raw_code, payload in snapshot.items()
                if isinstance(payload, Mapping)
            },
        )
    code_filter = (
        None
        if codes is None
        else {
            text
            for code in codes
            if (text := _canonical_text(code)).strip()
        }
    )
    copied: MutableQuoteMap = {}
    for raw_code, payload in source.items():
        code = _canonical_text(raw_code)
        if code_filter is not None and code not in code_filter:
            continue
        if not isinstance(payload, Mapping):
            continue
        copied[code] = {
            _canonical_text(key): _copy_frozen_value(value)
            for key, value in payload.items()
        }
    return copied


@dataclass(frozen=True, slots=True, eq=False)
class QuoteSnapshot(Mapping[str, QuotePayload]):
    """Versioned immutable quote data with Mapping compatibility."""

    version: int
    timestamp: float
    quotes: QuoteMap
    strict_values: InitVar[bool] = False
    unknown_leaf_count: int = field(init=False, default=0, compare=False, repr=False)
    unknown_leaf_types: tuple[str, ...] = field(init=False, default=(), compare=False, repr=False)

    def __post_init__(self, strict_values: bool) -> None:
        if isinstance(self.version, bool) or not isinstance(self.version, int) or self.version < 0:
            raise ValueError("version must be a non-negative integer")
        version = int.__add__(self.version, 0)
        if isinstance(self.timestamp, bool) or not isinstance(self.timestamp, (int, float)):
            raise ValueError("timestamp must be a finite non-negative number")
        timestamp = float(self.timestamp)
        if not math.isfinite(timestamp) or timestamp < 0:
            raise ValueError("timestamp must be a finite non-negative number")
        if not isinstance(self.quotes, Mapping):
            raise TypeError("quotes must be a mapping")

        frozen_quotes, unknown_leaf_count, unknown_leaf_types = _freeze_quotes(
            self.quotes,
            strict_values=bool(strict_values),
        )
        object.__setattr__(self, "version", version)
        object.__setattr__(self, "timestamp", timestamp)
        object.__setattr__(self, "quotes", frozen_quotes)
        object.__setattr__(self, "unknown_leaf_count", unknown_leaf_count)
        object.__setattr__(self, "unknown_leaf_types", unknown_leaf_types)

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
        strict_values: bool = False,
    ) -> QuoteSnapshot:
        return cls(
            version=version,
            timestamp=time.time() if timestamp is None else timestamp,
            quotes=quotes,
            strict_values=strict_values,
        )

    @classmethod
    def _from_frozen(
        cls,
        *,
        version: int,
        timestamp: float,
        quotes: QuoteMap,
        unknown_leaf_count: int = 0,
        unknown_leaf_types: tuple[str, ...] = (),
    ) -> QuoteSnapshot:
        """Build a snapshot from data already normalized by this module."""
        if isinstance(version, bool) or not isinstance(version, int) or version < 0:
            raise ValueError("version must be a non-negative integer")
        if isinstance(timestamp, bool) or not isinstance(timestamp, (int, float)):
            raise ValueError("timestamp must be a finite non-negative number")
        timestamp_value = float(timestamp)
        if not math.isfinite(timestamp_value) or timestamp_value < 0:
            raise ValueError("timestamp must be a finite non-negative number")
        if not isinstance(quotes, Mapping):
            raise TypeError("quotes must be a mapping")
        snapshot = object.__new__(cls)
        object.__setattr__(snapshot, "version", int.__add__(version, 0))
        object.__setattr__(snapshot, "timestamp", timestamp_value)
        object.__setattr__(snapshot, "quotes", quotes)
        object.__setattr__(snapshot, "unknown_leaf_count", int(unknown_leaf_count))
        object.__setattr__(snapshot, "unknown_leaf_types", tuple(unknown_leaf_types))
        return snapshot

    def __getitem__(self, key: str) -> Mapping[str, object]:
        return self.quotes[key]

    def __iter__(self) -> Iterator[str]:
        return iter(self.quotes)

    def __len__(self) -> int:
        return len(self.quotes)

    def to_mutable_dict(self, codes: Iterable[object] | None = None) -> MutableQuoteMap:
        return snapshot_to_mutable_dict(self, codes)

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


def merge_quote_snapshot(
    snapshot: QuoteSnapshot,
    incoming: Mapping[str, Mapping[str, object]],
    *,
    version: int,
    timestamp: float | None = None,
    strict_values: bool = False,
) -> tuple[QuoteSnapshot, QuoteSnapshot]:
    """Merge incoming quotes without refreezing untouched entries.

    The first result is safe to publish. The second preserves unknown-leaf
    observations for the caller's existing telemetry path.
    """
    if not isinstance(snapshot, QuoteSnapshot):
        raise TypeError("snapshot must be a QuoteSnapshot")
    if not isinstance(incoming, Mapping):
        raise TypeError("incoming quotes must be a mapping")

    frozen_incoming, unknown_leaf_count, unknown_leaf_types = _freeze_quotes(
        incoming,
        strict_values=strict_values,
    )
    timestamp_value = time.time() if timestamp is None else timestamp
    merged_quotes = _merge_frozen_quotes(snapshot.quotes, frozen_incoming)
    published = QuoteSnapshot._from_frozen(
        version=version,
        timestamp=timestamp_value,
        quotes=merged_quotes,
    )
    observed = QuoteSnapshot._from_frozen(
        version=version,
        timestamp=timestamp_value,
        quotes=merged_quotes,
        unknown_leaf_count=unknown_leaf_count,
        unknown_leaf_types=unknown_leaf_types,
    )
    return published, observed


__all__ = [
    "MutableQuoteMap",
    "QuoteMap",
    "QuotePayload",
    "QuoteScalar",
    "QuoteSnapshot",
    "QuoteValue",
    "TOTAL_SHARES_KEY",
    "coerce_quote_number",
    "get_missing_a_share_finance_codes",
    "get_total_shares",
    "merge_quote_snapshot",
    "snapshot_to_mutable_dict",
]
