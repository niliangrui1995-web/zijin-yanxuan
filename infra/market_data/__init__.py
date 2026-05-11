# -*- coding: utf-8 -*-
"""Stable market-data ports exposed to application-layer orchestration."""

from infra.market_data.adjustment_service import AdjustmentService
from infra.market_data.local_history_provider import LocalHistoryProvider
from infra.market_data.market_data_warehouse import (
    MARKET_DATASET,
    MARKET_DATA_SCHEMA_VERSION,
    MarketDataWarehouse,
    WarehouseReadResult,
    WarehouseStatus,
    get_default_market_data_warehouse,
)
from infra.market_data.provider_ports import DataProviderPort, EnginePort, MarketDataPorts, as_market_data_ports
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
    "MarketDataWarehouse",
    "MarketDataPorts",
    "RealtimeQuoteProvider",
    "TdxDataProvider",
    "WarehouseManifest",
    "WarehouseManifestRecord",
    "WarehouseReadResult",
    "WarehouseStatus",
    "as_market_data_ports",
    "get_default_market_data_warehouse",
]
