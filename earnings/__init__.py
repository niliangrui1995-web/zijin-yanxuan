"""Legacy earnings exports loaded on first explicit use."""

from __future__ import annotations

from importlib import import_module
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from domains.earnings import EarningsEngine, EarningsScheduler

__all__ = ["EarningsEngine", "EarningsScheduler"]


def __getattr__(name: str):
    if name not in __all__:
        raise AttributeError(name)
    value = getattr(import_module("domains.earnings"), name)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    return sorted({*globals(), *__all__})
