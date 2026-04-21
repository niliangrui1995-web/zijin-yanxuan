# -*- coding: utf-8 -*-
"""Typed ports for market-data provider and engine boundaries."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class DataProviderPort(Protocol):
    code2name: dict[str, str]
    cache_data: dict[str, Any]

    def fetch_realtime_quotes_batch(self, codes: list[str]) -> dict[str, dict]:
        ...

    def test_network(self, timeout: int = 3) -> bool:
        ...

    def set_online_mode(self, enabled: bool) -> None:
        ...

    def load_cache_from_disk(self) -> str:
        ...

    def ensure_code_name_map(self, refresh_missing: bool = True) -> dict[str, str]:
        ...


@runtime_checkable
class EnginePort(Protocol):
    def full_scan(self, *args, **kwargs):
        ...

    def incremental_scan(self, *args, **kwargs):
        ...


@dataclass(frozen=True)
class MarketDataPorts:
    provider: DataProviderPort
    engine: EnginePort


def as_market_data_ports(provider: DataProviderPort, engine: EnginePort) -> MarketDataPorts:
    return MarketDataPorts(provider=provider, engine=engine)
