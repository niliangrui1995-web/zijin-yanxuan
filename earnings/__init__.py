"""Legacy earnings exports loaded on first explicit use."""

from __future__ import annotations

from importlib import import_module
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from domains.earnings import EarningsEngine
    from domains.earnings.scheduler import EarningsScheduler

__all__ = ["EarningsEngine", "EarningsScheduler"]


def __getattr__(name: str):
    targets = {
        "EarningsEngine": ("domains.earnings", "EarningsEngine"),
        "EarningsScheduler": ("domains.earnings.scheduler", "EarningsScheduler"),
    }
    target = targets.get(name)
    if target is None:
        raise AttributeError(name)
    module_name, attr_name = target
    value = getattr(import_module(module_name), attr_name)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    return sorted({*globals(), *__all__})
