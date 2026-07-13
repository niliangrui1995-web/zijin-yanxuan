# -*- coding: utf-8 -*-
"""Earnings-domain exports loaded on first explicit use."""

from __future__ import annotations

from importlib import import_module
from typing import TYPE_CHECKING

from domains import _resolve_lazy_export

if TYPE_CHECKING:
    from domains.earnings.engine import EarningsEngine
    from domains.earnings.scheduler import EarningsScheduler

__all__ = ["EarningsEngine", "EarningsScheduler"]

_EXPORTS = {
    "EarningsEngine": ("domains.earnings.engine", "EarningsEngine"),
    "EarningsScheduler": ("domains.earnings.scheduler", "EarningsScheduler"),
}


def __getattr__(name: str):
    return _resolve_lazy_export(name, _EXPORTS, globals(), import_module)


def __dir__() -> list[str]:
    return sorted({*globals(), *__all__})
