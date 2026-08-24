"""Deprecated compatibility exports for :mod:`infra.market_data.tdx_data_provider`."""

from __future__ import annotations

from infra.market_data import tdx_data_provider as _provider_module
from infra.market_data.tdx_data_provider import TdxDataProvider

__all__ = ["TdxDataProvider"]


def __getattr__(name: str):
    return getattr(_provider_module, name)
