# -*- coding: utf-8 -*-
"""Typed ports for market-data provider and engine boundaries."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Protocol, runtime_checkable


def _freeze_health_value(value: Any) -> Any:
    if isinstance(value, Mapping):
        return MappingProxyType({key: _freeze_health_value(item) for key, item in value.items()})
    if isinstance(value, (list, tuple)):
        return tuple(_freeze_health_value(item) for item in value)
    if isinstance(value, (set, frozenset)):
        return frozenset(_freeze_health_value(item) for item in value)
    return value


def _thaw_health_value(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {key: _thaw_health_value(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_thaw_health_value(item) for item in value]
    if isinstance(value, frozenset):
        return {_thaw_health_value(item) for item in value}
    return value


@dataclass(frozen=True)
class ProviderHealthSnapshot:
    """Immutable health data published by a market-data provider."""

    request_stats: Mapping[str, Any]
    runtime_stats: Mapping[str, Any]
    eastmoney_cooldown_until: float = 0.0
    eastmoney_last_error: str = ""
    quote_cooldown_until: float = 0.0
    quote_last_error: str = ""
    hithink_cooldown_until: float = 0.0
    hithink_last_error: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "request_stats", _freeze_health_value(dict(self.request_stats or {})))
        object.__setattr__(self, "runtime_stats", _freeze_health_value(dict(self.runtime_stats or {})))
        object.__setattr__(self, "eastmoney_cooldown_until", float(self.eastmoney_cooldown_until or 0.0))
        object.__setattr__(self, "eastmoney_last_error", str(self.eastmoney_last_error or ""))
        object.__setattr__(self, "quote_cooldown_until", float(self.quote_cooldown_until or 0.0))
        object.__setattr__(self, "quote_last_error", str(self.quote_last_error or ""))
        object.__setattr__(self, "hithink_cooldown_until", float(self.hithink_cooldown_until or 0.0))
        object.__setattr__(self, "hithink_last_error", str(self.hithink_last_error or ""))

    @classmethod
    def empty(cls) -> ProviderHealthSnapshot:
        return cls(request_stats={}, runtime_stats={})

    def as_dict(self) -> dict[str, Any]:
        return {
            "request_stats": _thaw_health_value(self.request_stats),
            "runtime_stats": _thaw_health_value(self.runtime_stats),
            "eastmoney_cooldown_until": self.eastmoney_cooldown_until,
            "eastmoney_last_error": self.eastmoney_last_error,
            "quote_cooldown_until": self.quote_cooldown_until,
            "quote_last_error": self.quote_last_error,
            "hithink_cooldown_until": self.hithink_cooldown_until,
            "hithink_last_error": self.hithink_last_error,
        }


@dataclass(frozen=True)
class RealtimeQuoteRequestPolicy:
    """Public, immutable timing and batching policy for quote consumers."""

    api_call_timeout_sec: float = 8.0
    batch_size: int = 20

    def __post_init__(self) -> None:
        object.__setattr__(self, "api_call_timeout_sec", max(0.1, float(self.api_call_timeout_sec or 8.0)))
        object.__setattr__(self, "batch_size", max(1, int(self.batch_size or 20)))


@runtime_checkable
class ProviderHealthPort(Protocol):
    def read_provider_health(self) -> ProviderHealthSnapshot: ...


@runtime_checkable
class OnlineStatusPort(Protocol):
    def is_online(self) -> bool: ...


@runtime_checkable
class RealtimeQuotePolicyPort(Protocol):
    def read_realtime_quote_request_policy(self) -> RealtimeQuoteRequestPolicy: ...


@runtime_checkable
class OfflineQuotePort(Protocol):
    def build_offline_quotes(self, codes: list[str]) -> dict[str, dict]: ...


@runtime_checkable
class DataProviderPort(ProviderHealthPort, OnlineStatusPort, RealtimeQuotePolicyPort, OfflineQuotePort, Protocol):
    code2name: dict[str, str]
    cache_data: dict[str, Any]

    def fetch_realtime_quotes_batch(self, codes: list[str], *, cancellation_token=None) -> dict[str, dict]: ...

    def test_network(self, timeout: int = 3) -> bool: ...

    def set_online_mode(self, enabled: bool) -> None: ...

    def load_cache_from_disk(self) -> str: ...

    def ensure_code_name_map(
        self,
        codes=None,
        *,
        refresh_missing: bool = False,
        cancellation_token=None,
    ) -> dict[str, str]: ...

    def ensure_adjustment_metadata(self, *, force: bool = False) -> dict: ...


@runtime_checkable
class RealtimeQuotePort(ProviderHealthPort, OnlineStatusPort, RealtimeQuotePolicyPort, Protocol):
    def fetch_realtime_quotes_batch(self, codes: list[str], *, cancellation_token=None) -> dict[str, dict]: ...

    def get_realtime_runtime_stats(self) -> dict: ...

    def compact_runtime_caches(self, now: float | None = None) -> dict: ...

    def protect_against_thread_anomaly(self, pytdx_thread_count: int, threshold: int | None = None) -> bool: ...

    def enter_realtime_cooldown(self, reason: str, cooldown_sec: float | None = None) -> None: ...


@runtime_checkable
class EnginePort(Protocol):
    def full_scan(self, *args, **kwargs): ...

    def incremental_scan(self, *args, **kwargs): ...


@dataclass(frozen=True)
class MarketDataPorts:
    provider: DataProviderPort
    engine: EnginePort


def as_market_data_ports(provider: DataProviderPort, engine: EnginePort) -> MarketDataPorts:
    return MarketDataPorts(provider=provider, engine=engine)
