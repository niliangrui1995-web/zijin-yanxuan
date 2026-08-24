"""Deprecated compatibility exports for :mod:`app.services.scan_engine_facade`."""

from __future__ import annotations

from app.services import scan_engine_facade as _engine_module
from app.services.scan_engine_facade import VCPEngine

__all__ = ["VCPEngine"]


def __getattr__(name: str):
    return getattr(_engine_module, name)
