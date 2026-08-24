# -*- coding: utf-8 -*-
"""Shared helpers for intraday quote snapshots and market-cap enrichment."""

from __future__ import annotations

from collections.abc import Mapping

from core.state.quote_snapshot import (
    _LEGACY_TOTAL_SHARES_KEYS,
    TOTAL_SHARES_KEY,
    get_total_shares,
)
from core.state.quote_snapshot import (
    coerce_quote_number as coerce_number,
)
from core.state.quote_snapshot import (
    get_missing_a_share_finance_codes as get_missing_a_share_finance_codes,
)


def is_a_share_code(code) -> bool:
    text = str(code or "").strip()
    return len(text) == 6 and text.isdigit()


def merge_quote_entry(existing: Mapping | None, incoming: Mapping | None) -> dict:
    if existing is not None and not isinstance(existing, Mapping):
        raise TypeError("existing quote payload must be a mapping")
    if incoming is not None and not isinstance(incoming, Mapping):
        raise TypeError("incoming quote payload must be a mapping")
    merged = dict(existing or {})
    for key, value in dict(incoming or {}).items():
        if value is None:
            continue
        merged[key] = value
    return merged


def merge_quote_snapshot_inplace(target: dict, incoming: Mapping | None) -> dict:
    for code, payload in dict(incoming or {}).items():
        target[str(code)] = merge_quote_entry(target.get(str(code)), payload)
    return target


def merge_quote_snapshot(snapshot: Mapping | None, incoming: Mapping | None) -> dict:
    """Return a merged copy without mutating either input mapping."""
    merged = {str(code): dict(payload or {}) for code, payload in dict(snapshot or {}).items()}
    return merge_quote_snapshot_inplace(merged, incoming)


def _set_total_shares(entry: dict, total_shares: float) -> None:
    if total_shares <= 0:
        return
    entry[TOTAL_SHARES_KEY] = total_shares
    for key in _LEGACY_TOTAL_SHARES_KEYS:
        entry.pop(key, None)


def _calculated_market_cap(
    total_shares: float,
    market_cap: float,
    price_base: float,
    rt_close: float,
) -> float | None:
    if total_shares > 0 and rt_close > 0:
        return total_shares * rt_close
    if market_cap > 0 and rt_close > 0 and price_base > 0:
        return market_cap * (rt_close / price_base)
    return None


def _apply_finance_values(entry: dict, info: Mapping) -> None:
    total_shares = get_total_shares(info)
    market_cap = coerce_number(info.get("market_cap"))
    price_base = coerce_number(info.get("price_base"))
    rt_close = coerce_number(entry.get("close"))
    _set_total_shares(entry, total_shares)
    if market_cap > 0:
        entry["market_cap"] = market_cap
    if price_base > 0:
        entry["price_base"] = price_base

    finance_source = str(info.get("source") or "").strip()
    if finance_source:
        entry["finance_source"] = finance_source

    calculated_market_cap = _calculated_market_cap(total_shares, market_cap, price_base, rt_close)
    if calculated_market_cap is not None:
        entry["market_cap"] = calculated_market_cap


def enrich_quotes_with_finance(
    quotes: Mapping[str, Mapping] | None, finance_data: Mapping[str, Mapping] | None
) -> dict:
    enriched = {str(code): dict(payload or {}) for code, payload in dict(quotes or {}).items()}

    for raw_code, raw_info in dict(finance_data or {}).items():
        code = str(raw_code or "").strip()
        if not code:
            continue

        info = dict(raw_info or {})
        entry = enriched.setdefault(code, {})
        _apply_finance_values(entry, info)

    return enriched


def build_finance_quote_payload(finance_data: Mapping[str, Mapping] | None) -> dict:
    return enrich_quotes_with_finance({}, finance_data)


def resolve_quote_metrics(item_dict: Mapping | None, quote: Mapping | None) -> dict:
    row = dict(item_dict or {})
    payload = dict(quote or {})

    total_shares = get_total_shares(payload, row)

    rt_close = coerce_number(payload.get("close"))
    last_close = coerce_number(payload.get("last_close"))
    if rt_close <= 0 < last_close:
        rt_close = last_close

    pct = None
    if last_close > 0 and rt_close > 0:
        pct = ((rt_close / last_close) - 1.0) * 100.0
    else:
        raw_pct = payload.get("pct")
        if raw_pct not in (None, "", "-", "--"):
            try:
                pct = float(raw_pct)
            except (TypeError, ValueError):
                pct = None

    market_cap_value = 0.0
    if total_shares > 0 and rt_close > 0:
        market_cap_value = total_shares * rt_close
    else:
        market_cap_value = coerce_number(payload.get("market_cap"))

    return {
        "total_shares": total_shares,
        "rt_close": rt_close,
        "last_close": last_close,
        "pct": pct,
        "price_text": f"{rt_close:.2f}" if rt_close > 0 else None,
        "market_cap_value": market_cap_value,
        "market_cap_text": f"{market_cap_value / 1e8:.0f}亿" if market_cap_value > 0 else None,
    }
