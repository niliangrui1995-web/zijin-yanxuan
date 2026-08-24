"""Lazy compatibility exports for the canonical UI signal bus."""

from __future__ import annotations

from importlib import import_module
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from ui.signals.ui_signal_bus import UISignalBus, ui_signals

__all__ = ["UISignalBus", "ui_signals"]


def __getattr__(name: str) -> Any:
    if name not in __all__:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    value = getattr(import_module("ui.signals.ui_signal_bus"), name)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    return sorted({*globals(), *__all__})
