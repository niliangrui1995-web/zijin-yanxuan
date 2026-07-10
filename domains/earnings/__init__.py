# -*- coding: utf-8 -*-
"""Earnings-domain exports loaded on first explicit use."""

from __future__ import annotations

from importlib import import_module
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from domains.earnings.engine import EarningsEngine
    from domains.earnings.scheduler import EarningsScheduler

__all__ = ["EarningsEngine", "EarningsScheduler"]

_EXPORTS = {
    "EarningsEngine": ("domains.earnings.engine", "EarningsEngine"),
    "EarningsScheduler": ("domains.earnings.scheduler", "EarningsScheduler"),
}


def __getattr__(name: str):
    target = _EXPORTS.get(name)
    if target is None:
        raise AttributeError(name)
    module_name, attr_name = target
    value = getattr(import_module(module_name), attr_name)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    return sorted({*globals(), *__all__})
