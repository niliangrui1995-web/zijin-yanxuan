# -*- coding: utf-8 -*-
"""UI-facing fund-holdings entrypoints."""

from __future__ import annotations

from domains.fund_holdings import (
    QFII_CAPITAL_ATTRIBUTE_CLIENT,
    QFII_CAPITAL_ATTRIBUTE_SELF_OWNED,
    QFII_CAPITAL_ATTRIBUTE_UNMARKED,
    SUBJECT_QFII,
    SUBJECT_RUIYUAN,
    fund_holdings_store,
    fund_holdings_sync_service,
)

__all__ = [
    "QFII_CAPITAL_ATTRIBUTE_CLIENT",
    "QFII_CAPITAL_ATTRIBUTE_SELF_OWNED",
    "QFII_CAPITAL_ATTRIBUTE_UNMARKED",
    "SUBJECT_QFII",
    "SUBJECT_RUIYUAN",
    "fund_holdings_store",
    "fund_holdings_sync_service",
]
