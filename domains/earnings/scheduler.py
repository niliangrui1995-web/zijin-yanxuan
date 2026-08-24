# -*- coding: utf-8 -*-
"""Deprecated lazy compatibility entrypoint for the owner-scoped earnings service.

New code must import :class:`app.services.ui_earnings_service.EarningsRefreshService`.
The compatibility lookup stays lazy so importing the domain package does not
pull UI application services into the domain import graph.
"""

from __future__ import annotations

__deprecated__ = True
EarningsScheduler: object
__all__ = ["EarningsScheduler"]


def __getattr__(name: str):
    if name != "EarningsScheduler":
        raise AttributeError(name)
    from app.services.ui_earnings_service import EarningsRefreshService

    globals()[name] = EarningsRefreshService
    return EarningsRefreshService
