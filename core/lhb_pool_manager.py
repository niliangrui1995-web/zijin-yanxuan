"""Deprecated compatibility exports for :mod:`app.services.ui_lhb_pool_service`."""

from __future__ import annotations

import app.services.ui_lhb_pool_service as _implementation
from app.services.ui_lhb_pool_service import POOL_WINDOW, LhbPoolManager

__all__ = ["LhbPoolManager", "POOL_WINDOW"]


def __getattr__(name: str):
    return getattr(_implementation, name)
