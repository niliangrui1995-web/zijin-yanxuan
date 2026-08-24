# -*- coding: utf-8 -*-
"""Import-safe entrypoints for canonical domain/application event channels."""

from __future__ import annotations

import threading
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from domains.runtime.qt_domain_events import DomainEventBus


_domain_events_lock = threading.Lock()
_domain_events: DomainEventBus | None = None


def _load_domain_event_bus_class() -> type[DomainEventBus]:
    """Load the Qt implementation only when a caller needs an event bus."""
    from domains.runtime.qt_domain_events import DomainEventBus

    return DomainEventBus


def get_domain_events() -> DomainEventBus:
    """Return the process-wide Qt event bus on first real use."""
    global _domain_events
    with _domain_events_lock:
        if _domain_events is None:
            _domain_events = _load_domain_event_bus_class()()
        return _domain_events


class _LazyDomainEventBus:
    """Keep existing ``domain_events.signal`` callers and test overrides lazy."""

    def __getattr__(self, name: str) -> Any:
        return getattr(get_domain_events(), name)

    def __dir__(self) -> list[str]:
        names = set(super().__dir__())
        with _domain_events_lock:
            event_bus = _domain_events
        if event_bus is not None:
            names.update(dir(event_bus))
        return sorted(names)


domain_events = _LazyDomainEventBus()


def __getattr__(name: str) -> Any:
    if name == "DomainEventBus":
        return _load_domain_event_bus_class()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = ["DomainEventBus", "domain_events", "get_domain_events"]
