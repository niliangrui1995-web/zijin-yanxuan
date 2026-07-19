"""Immutable contracts for opening and navigating K-line windows."""

from __future__ import annotations

from collections.abc import Buffer, Mapping, Sequence
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any, SupportsIndex, SupportsInt, cast

_IntegerInput = str | Buffer | SupportsInt | SupportsIndex


def _freeze_plain(value: Any) -> Any:
    if isinstance(value, Mapping):
        return MappingProxyType({str(key): _freeze_plain(item) for key, item in value.items()})
    if isinstance(value, (list, tuple)):
        return tuple(_freeze_plain(item) for item in value)
    if isinstance(value, (set, frozenset)):
        return frozenset(_freeze_plain(item) for item in value)
    return value


def _thaw_plain(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _thaw_plain(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_thaw_plain(item) for item in value]
    if isinstance(value, frozenset):
        return {_thaw_plain(item) for item in value}
    return value


def _integer(value: object, default: int = -1) -> int:
    try:
        return int(cast(_IntegerInput, value))
    except (TypeError, ValueError):
        return int(default)


@dataclass(frozen=True, slots=True)
class KlineNavItem:
    """Compact navigation identity; business-row payloads deliberately stay out."""

    code: str
    name: str
    source_tab_key: str = ""
    source_tab_index: int = -1

    def __post_init__(self) -> None:
        object.__setattr__(self, "code", str(self.code or "").strip())
        object.__setattr__(self, "name", str(self.name or self.code or "").strip())
        object.__setattr__(self, "source_tab_key", str(self.source_tab_key or "").strip())
        object.__setattr__(self, "source_tab_index", _integer(self.source_tab_index))


@dataclass(frozen=True, slots=True)
class KlineOpenContext:
    """One immutable stock context plus a compact navigation sequence."""

    code: str
    name: str
    vcp_data: Mapping[str, Any] = field(default_factory=dict)
    navigation: tuple[KlineNavItem, ...] = ()
    current_idx: int = 0
    source_tab_key: str = ""
    source_tab_index: int = -1

    def __post_init__(self) -> None:
        code = str(self.code or "").strip()
        object.__setattr__(self, "code", code)
        object.__setattr__(self, "name", str(self.name or code).strip() or code)
        object.__setattr__(self, "vcp_data", _freeze_plain(dict(self.vcp_data or {})))
        object.__setattr__(self, "navigation", tuple(self.navigation or ()))
        object.__setattr__(self, "current_idx", _integer(self.current_idx, 0))
        object.__setattr__(self, "source_tab_key", str(self.source_tab_key or "").strip())
        object.__setattr__(self, "source_tab_index", _integer(self.source_tab_index))

    @property
    def current_navigation_item(self) -> KlineNavItem | None:
        if 0 <= self.current_idx < len(self.navigation):
            return self.navigation[self.current_idx]
        return None

    def mutable_vcp_data(self) -> dict[str, Any]:
        return _thaw_plain(self.vcp_data)


def compact_kline_navigation(
    rows: Sequence[Mapping[str, Any]] | None,
    *,
    source_tab_key: str = "",
    source_tab_index: int = -1,
) -> tuple[KlineNavItem, ...]:
    """Project arbitrary business rows to the four fields navigation consumes."""
    default_key = str(source_tab_key or "").strip()
    default_index = _integer(source_tab_index)
    return tuple(_compact_navigation_item(raw, default_key, default_index) for raw in rows or ())


def _compact_navigation_item(raw: object, default_key: str, default_index: int) -> KlineNavItem:
    row = raw if isinstance(raw, Mapping) else {}
    row_key = str(row.get("__source_tab_key") or "").strip()
    row_index = _integer(row.get("__source_tab_index"))
    if default_key == "watchlist":
        key, index = default_key, default_index
    else:
        key = row_key or default_key
        index = row_index if row_index >= 0 else default_index
    return KlineNavItem(
        code=_first_navigation_value(row, "代码", "code", "ticker"),
        name=_first_navigation_value(row, "名称", "name"),
        source_tab_key=key,
        source_tab_index=index,
    )


def _first_navigation_value(row: Mapping, *keys: str) -> str:
    for key in keys:
        value = str(row.get(key) or "").strip()
        if value:
            return value
    return ""


__all__ = ["KlineNavItem", "KlineOpenContext", "compact_kline_navigation"]
