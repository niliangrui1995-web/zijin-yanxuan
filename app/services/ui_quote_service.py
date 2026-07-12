# -*- coding: utf-8 -*-
"""UI-facing quote normalization and publishing entrypoints."""

from __future__ import annotations

from domains.quotes import (
    build_finance_quote_payload,
    coerce_number,
    enrich_quotes_with_finance,
    get_missing_a_share_finance_codes,
    is_a_share_code,
    merge_quote_snapshot_inplace,
    publish_rt_quotes,
    resolve_quote_metrics,
)
from infra.market_data.provider_ports import (
    OfflineQuotePort,
    OnlineStatusPort,
    ProviderHealthPort,
    ProviderHealthSnapshot,
    RealtimeQuotePolicyPort,
    RealtimeQuoteRequestPolicy,
)


def build_offline_quotes(provider: object | None, codes: list[str]) -> dict[str, dict]:
    """Build fallback quotes through the provider's public port."""
    if not isinstance(provider, OfflineQuotePort):
        return {}
    try:
        payload = provider.build_offline_quotes(codes) or {}
    except (AttributeError, OSError, RuntimeError, TypeError, ValueError):
        return {}
    return payload if isinstance(payload, dict) else {}


def is_provider_online(provider: object | None) -> bool:
    """Read connectivity through the provider's public status port."""
    if not isinstance(provider, OnlineStatusPort):
        return True
    try:
        return bool(provider.is_online())
    except (AttributeError, RuntimeError, TypeError, ValueError):
        return False


def read_realtime_quote_request_policy(provider: object | None) -> RealtimeQuoteRequestPolicy:
    """Read an immutable request policy with conservative defaults."""
    if not isinstance(provider, RealtimeQuotePolicyPort):
        return RealtimeQuoteRequestPolicy()
    try:
        policy = provider.read_realtime_quote_request_policy()
    except (AttributeError, RuntimeError, TypeError, ValueError):
        return RealtimeQuoteRequestPolicy()
    return policy if isinstance(policy, RealtimeQuoteRequestPolicy) else RealtimeQuoteRequestPolicy()


def read_provider_health(provider: object | None) -> ProviderHealthSnapshot:
    """Read the provider's public health snapshot with a safe empty fallback."""
    if not isinstance(provider, ProviderHealthPort):
        return ProviderHealthSnapshot.empty()
    try:
        snapshot = provider.read_provider_health()
    except (AttributeError, RuntimeError, TypeError, ValueError):
        return ProviderHealthSnapshot.empty()
    return snapshot if isinstance(snapshot, ProviderHealthSnapshot) else ProviderHealthSnapshot.empty()

__all__ = [
    "build_offline_quotes",
    "build_finance_quote_payload",
    "coerce_number",
    "enrich_quotes_with_finance",
    "get_missing_a_share_finance_codes",
    "is_provider_online",
    "is_a_share_code",
    "merge_quote_snapshot_inplace",
    "publish_rt_quotes",
    "read_provider_health",
    "read_realtime_quote_request_policy",
    "resolve_quote_metrics",
]
