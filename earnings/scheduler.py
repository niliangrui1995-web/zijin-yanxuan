"""Deprecated lazy compatibility entrypoint for the earnings scheduler."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from domains.earnings.scheduler import EarningsScheduler

__deprecated__ = True
__all__ = ["EarningsScheduler"]


def __getattr__(name: str) -> Any:
    if name != "EarningsScheduler":
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    from domains.earnings.scheduler import EarningsScheduler

    globals()[name] = EarningsScheduler
    return EarningsScheduler


def __dir__() -> list[str]:
    return sorted({*globals(), *__all__})
