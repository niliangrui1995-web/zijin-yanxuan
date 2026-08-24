"""Deprecated lazy compatibility entrypoint for the earnings engine."""

from __future__ import annotations

import warnings
from importlib import import_module
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from domains.earnings.engine import EarningsEngine

__deprecated__ = True
__all__ = ["EarningsEngine"]


def _implementation() -> Any:
    return import_module("domains.earnings.engine")


def __getattr__(name: str) -> Any:
    implementation = _implementation()
    try:
        value = getattr(implementation, name)
    except AttributeError as exc:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}") from exc

    if name.startswith("_"):
        warnings.warn(
            f"{__name__}.{name} is a private compatibility read; "
            "patch domains.earnings.engine directly instead.",
            DeprecationWarning,
            stacklevel=2,
        )
    elif name in __all__:
        globals()[name] = value
    return value


def __dir__() -> list[str]:
    return sorted({*globals(), *__all__})
