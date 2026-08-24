# -*- coding: utf-8 -*-
"""Import-safe entrypoints for the process-wide quote snapshot store."""

from __future__ import annotations

import threading
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from core.qt_global_store import GlobalStore


_global_store_lock = threading.Lock()
_global_store: GlobalStore | None = None


def _load_global_store_class() -> type[GlobalStore]:
    """Load the Qt implementation only when a caller needs the store."""
    from core.qt_global_store import GlobalStore

    return GlobalStore


def get_global_store() -> GlobalStore:
    """Return the process-wide Qt snapshot store on first real use."""
    global _global_store
    with _global_store_lock:
        if _global_store is None:
            _global_store = _load_global_store_class()()
        return _global_store


class _LazyGlobalStore:
    """Keep existing ``global_store.method`` callers and test overrides lazy."""

    def __getattr__(self, name: str) -> Any:
        return getattr(get_global_store(), name)

    def __dir__(self) -> list[str]:
        names = set(super().__dir__())
        with _global_store_lock:
            store = _global_store
        if store is not None:
            names.update(dir(store))
        return sorted(names)

    def __bool__(self) -> bool:
        return True

    def __repr__(self) -> str:
        return "<LazyGlobalStore>"


global_store = _LazyGlobalStore()


def __getattr__(name: str) -> Any:
    if name == "GlobalStore":
        return _load_global_store_class()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = ["GlobalStore", "get_global_store", "global_store"]
