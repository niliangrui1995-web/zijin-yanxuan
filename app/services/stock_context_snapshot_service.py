"""Application boundary for stock-context cache and repository reads."""

from __future__ import annotations

from app.services import ui_industry_chain_service as ai_pool_module
from app.services import ui_lhb_pool_service as lhb_pool_module
from app.services.ui_industry_chain_service import load_cached_ai_industry_chain_stock_codes
from domains.lhb.pool_service import collect_qualifying_codes
from infra.storage.lhb_pool_repository import LhbPoolRepository, LhbRepositoryError
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


def load_lhb_cached_realtime_projection() -> dict:
    """Return a read-only LHB code projection for cold-start quote registration.

    This deliberately avoids ``LhbPoolManager``: constructing that manager can
    migrate legacy cache files and write to disk, which is inappropriate on the
    central quote registration path.  The projection keeps the persistent LHB
    admission rules and fails open on the unavailable RPS250 refinement so an
    unloaded LHB tab cannot silently drop qualifying symbols.
    """

    cache_path, legacy_pool_path, _legacy_single_day_path = LhbPoolRepository.default_paths()
    try:
        payload, selected_path = LhbPoolRepository.load_state(cache_path, legacy_pool_path)
    except (LhbRepositoryError, OSError, RuntimeError, TypeError, ValueError):
        return {
            "codes": (),
            "status": "degraded",
            "reason": "lhb_cache_invalid",
        }

    if not payload:
        return {
            "codes": (),
            "status": "degraded",
            "reason": "lhb_cache_missing",
        }

    daily_data = payload.get("daily_data") if isinstance(payload, dict) else None
    if not isinstance(daily_data, dict):
        return {
            "codes": (),
            "status": "degraded",
            "reason": "lhb_cache_invalid",
        }

    valid_daily_data = {
        str(day): records
        for day, records in daily_data.items()
        if isinstance(records, list)
    }
    if not valid_daily_data:
        return {
            "codes": (),
            "status": "degraded",
            "reason": "lhb_cache_empty",
        }

    try:
        ai_codes = set(load_cached_ai_industry_chain_stock_codes() or ())
    except (AttributeError, OSError, RuntimeError, TypeError, ValueError):
        ai_codes = set()
    if not ai_codes:
        return {
            "codes": (),
            "status": "degraded",
            "reason": "ai_universe_cache_unavailable",
        }

    qualifying_codes, _hit_counts = collect_qualifying_codes(valid_daily_data, ai_codes)
    codes = tuple(
        sorted(
            code
            for code in qualifying_codes
            if isinstance(code, str) and len(code) == 6 and code.isdigit()
        )
    )
    if not codes:
        return {
            "codes": (),
            "status": "degraded",
            "reason": "lhb_no_qualifying_codes",
        }
    return {
        "codes": codes,
        "status": "registered_degraded",
        "reason": "lhb_rps_unavailable_keep_base_pool",
        "cache_path": str(selected_path or ""),
    }


def load_lhb_cached_realtime_codes() -> set[str]:
    """Compatibility helper exposing only the pure cached LHB code set."""

    projection = load_lhb_cached_realtime_projection()
    return {
        str(code).strip()
        for code in projection.get("codes", ())
        if isinstance(code, str) and len(str(code).strip()) == 6 and str(code).strip().isdigit()
    }


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
    "load_lhb_cached_realtime_codes",
    "load_lhb_cached_realtime_projection",
    "load_lhb_pool_rows",
    "load_named_cache_rows",
    "load_scan_cache_rows",
    "project_root",
]
