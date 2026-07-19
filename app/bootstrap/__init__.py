# -*- coding: utf-8 -*-
"""Bootstrap exports loaded on first use.

The main-window shell needs :class:`ApplicationBootstrap` before first paint,
but the startup orchestrator pulls in the complete task/runtime stack.  Keep
the package initializer lazy so importing the lightweight shell adapter does
not also import every deferred startup service.
"""

from __future__ import annotations

from importlib import import_module
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.bootstrap.application_bootstrap import ApplicationBootstrap
    from app.bootstrap.startup_orchestrator import StartupHostAdapter, StartupOrchestrator

__all__ = ["ApplicationBootstrap", "StartupHostAdapter", "StartupOrchestrator"]

_EXPORTS = {
    "ApplicationBootstrap": ("app.bootstrap.application_bootstrap", "ApplicationBootstrap"),
    "StartupHostAdapter": ("app.bootstrap.startup_orchestrator", "StartupHostAdapter"),
    "StartupOrchestrator": ("app.bootstrap.startup_orchestrator", "StartupOrchestrator"),
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
