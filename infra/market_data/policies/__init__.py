"""Market-data source selection and fallback policies."""

from infra.market_data.policies.fallback_policy import (
    direct_quote,
    dispatch_asian_pe_fallback,
    fetch_asian_realtime_quote,
    refresh_pe_if_needed,
)

__all__ = [
    "direct_quote",
    "dispatch_asian_pe_fallback",
    "fetch_asian_realtime_quote",
    "refresh_pe_if_needed",
]
