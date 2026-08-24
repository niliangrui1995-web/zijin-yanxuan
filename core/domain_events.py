"""Legacy import-safe facade for domain/application event channels."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from domains.runtime.domain_events import domain_events, get_domain_events

if TYPE_CHECKING:
    from domains.runtime.domain_events import DomainEventBus


def __getattr__(name: str) -> Any:
    if name == "DomainEventBus":
        from domains.runtime.domain_events import DomainEventBus

        return DomainEventBus
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = ["DomainEventBus", "domain_events", "get_domain_events"]
