# -*- coding: utf-8 -*-
"""Stable market-data ports exposed lazily to application orchestration."""

from __future__ import annotations

from importlib import import_module
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from infra.market_data.adjustment_service import AdjustmentService
    from infra.market_data.local_history_provider import LocalHistoryProvider
    from infra.market_data.market_data_warehouse import (
        MARKET_DATA_SCHEMA_VERSION,
        MARKET_DATASET,
        MarketDataWarehouse,
        WarehouseReadResult,
        WarehouseStatus,
        get_default_market_data_warehouse,
    )
    from infra.market_data.provider_ports import (
        DataProviderPort,
        EnginePort,
        MarketDataPorts,
        RealtimeQuotePort,
        as_market_data_ports,
    )
    from infra.market_data.realtime_quote_provider import RealtimeQuoteProvider
    from infra.market_data.tdx_data_provider import TdxDataProvider
    from infra.market_data.warehouse_manifest import WarehouseManifest, WarehouseManifestRecord

__all__ = [
    "AdjustmentService",
    "DataProviderPort",
    "EnginePort",
    "LocalHistoryProvider",
    "MARKET_DATASET",
    "MARKET_DATA_SCHEMA_VERSION",
    "MarketDataPorts",
    "MarketDataWarehouse",
    "RealtimeQuotePort",
    "RealtimeQuoteProvider",
    "TdxDataProvider",
    "WarehouseManifest",
    "WarehouseManifestRecord",
    "WarehouseReadResult",
    "WarehouseStatus",
    "as_market_data_ports",
    "get_default_market_data_warehouse",
]

_EXPORTS = {
    "AdjustmentService": ("infra.market_data.adjustment_service", "AdjustmentService"),
    "DataProviderPort": ("infra.market_data.provider_ports", "DataProviderPort"),
    "EnginePort": ("infra.market_data.provider_ports", "EnginePort"),
    "LocalHistoryProvider": ("infra.market_data.local_history_provider", "LocalHistoryProvider"),
    "MARKET_DATASET": ("infra.market_data.market_data_warehouse", "MARKET_DATASET"),
    "MARKET_DATA_SCHEMA_VERSION": ("infra.market_data.market_data_warehouse", "MARKET_DATA_SCHEMA_VERSION"),
    "MarketDataPorts": ("infra.market_data.provider_ports", "MarketDataPorts"),
    "MarketDataWarehouse": ("infra.market_data.market_data_warehouse", "MarketDataWarehouse"),
    "RealtimeQuotePort": ("infra.market_data.provider_ports", "RealtimeQuotePort"),
    "RealtimeQuoteProvider": ("infra.market_data.realtime_quote_provider", "RealtimeQuoteProvider"),
    "TdxDataProvider": ("infra.market_data.tdx_data_provider", "TdxDataProvider"),
    "WarehouseManifest": ("infra.market_data.warehouse_manifest", "WarehouseManifest"),
    "WarehouseManifestRecord": ("infra.market_data.warehouse_manifest", "WarehouseManifestRecord"),
    "WarehouseReadResult": ("infra.market_data.market_data_warehouse", "WarehouseReadResult"),
    "WarehouseStatus": ("infra.market_data.market_data_warehouse", "WarehouseStatus"),
    "as_market_data_ports": ("infra.market_data.provider_ports", "as_market_data_ports"),
    "get_default_market_data_warehouse": (
        "infra.market_data.market_data_warehouse",
        "get_default_market_data_warehouse",
    ),
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
