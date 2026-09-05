# -*- coding: utf-8 -*-
"""UI-facing fund-holdings entrypoints."""

from __future__ import annotations

from typing import TYPE_CHECKING

from domains.fund_holdings.compare import (
    QFII_CAPITAL_ATTRIBUTE_CLIENT,
    QFII_CAPITAL_ATTRIBUTE_SELF_OWNED,
    QFII_CAPITAL_ATTRIBUTE_UNMARKED,
    SUBJECT_QFII,
    SUBJECT_RUIYUAN,
)

if TYPE_CHECKING:
    from domains.fund_holdings.store import fund_holdings_store
    from domains.fund_holdings.sync import fund_holdings_sync_service

__all__ = [
    "QFII_CAPITAL_ATTRIBUTE_CLIENT",
    "QFII_CAPITAL_ATTRIBUTE_SELF_OWNED",
    "QFII_CAPITAL_ATTRIBUTE_UNMARKED",
    "SUBJECT_QFII",
    "SUBJECT_RUIYUAN",
    "fund_holdings_store",
    "fund_holdings_sync_service",
]


def __getattr__(name: str):
    """Keep UI shell imports free of SQLite and sync initialization."""
    if name not in {"fund_holdings_store", "fund_holdings_sync_service"}:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    import domains.fund_holdings as domain

    value = getattr(domain, name)
    globals()[name] = value
    return value
