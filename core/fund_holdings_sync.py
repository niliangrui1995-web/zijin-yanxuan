"""Deprecated compatibility exports for :mod:`domains.fund_holdings.sync`."""

from __future__ import annotations

from domains.fund_holdings import sync as _sync_module
from domains.fund_holdings.sync import FundHoldingsSyncService, fund_holdings_sync_service

__all__ = ["FundHoldingsSyncService", "fund_holdings_sync_service"]


def __getattr__(name: str):
    return getattr(_sync_module, name)
