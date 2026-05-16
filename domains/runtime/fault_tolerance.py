# -*- coding: utf-8 -*-
from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Mapping, Sequence

FALLBACK_TOKENS = ("fallback", "offline", "stale", "cooldown", "degraded")


def _text(value: Any) -> str:
    return str(value if value is not None else "").strip()


def _mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _float_value(value: Any) -> float:
    try:
        return float(value or 0.0)
    except (TypeError, ValueError):
        return 0.0


def _int_value(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _source_layers(request_stats: Mapping[str, Any]) -> tuple[str, ...]:
    return tuple(
        layer
        for layer in (_text(item) for item in request_stats.get("recent_source_layers", []) or [])
        if layer
    )


def _contains_fault_token(values: Sequence[str], tokens: Sequence[str]) -> bool:
    normalized_tokens = tuple(token.lower() for token in tokens)
    return any(any(token in value.lower() for token in normalized_tokens) for value in values)


@dataclass(frozen=True)
class ProviderFaultTolerance:
    provider_degraded: bool
    fallback_or_degraded: bool
    last_network_error: str
    cooldown_seconds_left: int
    eastmoney_cooldown_seconds_left: int
    recent_triggered_network: bool
    recent_cache_hit_count: int
    recent_pending_count: int
    recent_status: str
    recent_source_layers: tuple[str, ...]

    def as_dict(self) -> dict[str, Any]:
        return {
            "provider_degraded": self.provider_degraded,
            "fallback_or_degraded": self.fallback_or_degraded,
            "last_network_error": self.last_network_error,
            "cooldown_seconds_left": self.cooldown_seconds_left,
            "eastmoney_cooldown_seconds_left": self.eastmoney_cooldown_seconds_left,
            "recent_triggered_network": self.recent_triggered_network,
            "recent_cache_hit_count": self.recent_cache_hit_count,
            "recent_pending_count": self.recent_pending_count,
            "recent_status": self.recent_status,
            "recent_source_layers": list(self.recent_source_layers),
        }


def classify_provider_fault_tolerance(
    provider_status: Mapping[str, Any] | None,
    *,
    now: float | None = None,
    fallback_tokens: Sequence[str] = FALLBACK_TOKENS,
) -> ProviderFaultTolerance:
    status = _mapping(provider_status)
    request_stats = _mapping(status.get("request_stats"))
    runtime_stats = _mapping(status.get("runtime_stats") or status.get("provider_runtime"))
    current = time.time() if now is None else float(now)

    source_layers = _source_layers(request_stats)
    recent_status = _text(request_stats.get("recent_status"))
    last_error = _text(
        runtime_stats.get("last_error")
        or status.get("last_network_error")
        or status.get("eastmoney_last_error")
    )
    cooldown_until = _float_value(runtime_stats.get("cooldown_until") or status.get("cooldown_until"))
    eastmoney_cooldown_until = _float_value(status.get("eastmoney_cooldown_until"))
    provider_degraded = bool(cooldown_until > current or eastmoney_cooldown_until > current)
    fallback_or_degraded = bool(
        provider_degraded
        or _contains_fault_token(source_layers, fallback_tokens)
        or _contains_fault_token((recent_status,), fallback_tokens)
    )

    return ProviderFaultTolerance(
        provider_degraded=provider_degraded,
        fallback_or_degraded=fallback_or_degraded,
        last_network_error=last_error,
        cooldown_seconds_left=max(0, int(cooldown_until - current)),
        eastmoney_cooldown_seconds_left=max(0, int(eastmoney_cooldown_until - current)),
        recent_triggered_network=bool(request_stats.get("recent_triggered_network", False)),
        recent_cache_hit_count=_int_value(request_stats.get("recent_cache_hit_count")),
        recent_pending_count=_int_value(request_stats.get("recent_pending_count")),
        recent_status=recent_status,
        recent_source_layers=source_layers,
    )


def provider_fault_tolerance(provider_status: Mapping[str, Any] | None, *, now: float | None = None) -> dict[str, Any]:
    return classify_provider_fault_tolerance(provider_status, now=now).as_dict()
