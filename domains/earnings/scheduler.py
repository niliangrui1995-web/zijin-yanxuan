# -*- coding: utf-8 -*-
"""Deprecated compatibility entrypoint for the owner-scoped earnings service.

New code must import :class:`app.services.ui_earnings_service.EarningsRefreshService`.
This module intentionally contains no thread or scheduling implementation.
"""

from __future__ import annotations

from app.services.ui_earnings_service import EarningsRefreshService

__deprecated__ = True
EarningsScheduler = EarningsRefreshService

__all__ = ["EarningsScheduler"]
