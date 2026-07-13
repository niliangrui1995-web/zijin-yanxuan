"""Application boundary for stock-context cache and repository reads."""

from __future__ import annotations

from app.services import ui_industry_chain_service as ai_pool_module
from app.services import ui_lhb_pool_service as lhb_pool_module
from infra.storage.stock_context_repository import (
    coerce_cache_rows,
    lhb_pool_cache_signature,
    load_earnings_state_payload,
    load_named_cache_rows,
    load_scan_cache_rows,
    project_root,
)
from infra.tasks.lifecycle import raise_if_cancelled as _raise_if_cancelled


def load_ai_chain_cache_rows() -> list[dict]:
    try:
        return coerce_cache_rows(ai_pool_module.load_cached_ai_industry_chain_rows())
    except (AttributeError, OSError, RuntimeError, TypeError, ValueError):
        return []


def load_lhb_pool_rows(*, engine=None, cancellation_token=None) -> list[dict]:
    _raise_if_cancelled(cancellation_token)
    try:
        rows = coerce_cache_rows(lhb_pool_module.LhbPoolManager().compute_pool(data_provider=None, engine=engine))
    except (AttributeError, KeyError, OSError, RuntimeError, TypeError, ValueError):
        return []
    _raise_if_cancelled(cancellation_token)
    return rows


def load_fund_holding_snapshot(
    *,
    stock_codes=None,
    cancellation_token=None,
    store=None,
) -> tuple[dict, list[dict]]:
    if store is None:
        try:
            from app.services.ui_fund_holdings_service import fund_holdings_store

            store = fund_holdings_store
        except (ImportError, RuntimeError):
            return {}, []

    _raise_if_cancelled(cancellation_token)
    try:
        latest_quarter_map = dict(store.get_latest_quarter_map() or {})
    except (AttributeError, OSError, RuntimeError, TypeError, ValueError):
        return {}, []
    _raise_if_cancelled(cancellation_token)
    try:
        change_rows = list(store.query_change_rows(stock_codes=stock_codes) or [])
    except (AttributeError, OSError, RuntimeError, TypeError, ValueError):
        return {}, []
    _raise_if_cancelled(cancellation_token)
    return latest_quarter_map, change_rows


__all__ = [
    "coerce_cache_rows",
    "lhb_pool_cache_signature",
    "load_ai_chain_cache_rows",
    "load_earnings_state_payload",
    "load_fund_holding_snapshot",
    "load_lhb_pool_rows",
    "load_named_cache_rows",
    "load_scan_cache_rows",
    "project_root",
]
