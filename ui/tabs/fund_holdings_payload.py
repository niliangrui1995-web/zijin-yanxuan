# -*- coding: utf-8 -*-
"""Payload and row-building helpers for the fund holdings tab."""

from __future__ import annotations

from app.services.ui_fund_holdings_service import (
    QFII_CAPITAL_ATTRIBUTE_UNMARKED,
    SUBJECT_QFII,
    fund_holdings_store,
)
from app.services.ui_industry_chain_service import filter_rows_to_ai_chain_codes, normalize_ai_chain_code
from app.services.ui_task_lifecycle_service import CancellationToken
from ui.tabs.fund_holdings_rules import (
    FUND_DISPLAY_PLACEHOLDER,
    capital_attribute_label,
    format_amount,
    format_pct,
)
from ui.tabs.fund_holdings_subjects import shorten_subject_name


def _raise_if_cancelled(cancellation_token: CancellationToken | None) -> None:
    if cancellation_token is not None:
        cancellation_token.raise_if_cancelled()


def _run_stage(cancellation_token, fn, *args, **kwargs):
    _raise_if_cancelled(cancellation_token)
    result = fn(*args, **kwargs)
    _raise_if_cancelled(cancellation_token)
    return result


def _cancellable_rows(rows, cancellation_token):
    for row in rows or []:
        _raise_if_cancelled(cancellation_token)
        yield row


def load_ai_chain_context_map_safely(
    provider,
    *,
    cancellation_token: CancellationToken | None = None,
) -> dict[str, str]:
    _raise_if_cancelled(cancellation_token)
    try:
        result = dict(provider() or {})
    except (FileNotFoundError, RuntimeError, OSError, ValueError):
        return {}
    _raise_if_cancelled(cancellation_token)
    return result


def build_ai_chain_context_text(
    stock_code: str,
    chain_context_map: dict[str, str] | None,
    concept_sector_cache: dict[str, str],
    *,
    placeholder: str = FUND_DISPLAY_PLACEHOLDER,
) -> str:
    code = normalize_ai_chain_code(stock_code)
    if not code:
        return placeholder

    cached = concept_sector_cache.get(code)
    if cached is not None:
        return cached

    context_text = str((chain_context_map or {}).get(code) or "").strip() or placeholder
    concept_sector_cache[code] = context_text
    return context_text


def resolve_query_quarters(
    latest_quarter_map: dict[str, str],
    *,
    quarter_scope: str,
    quarter_keys=None,
    latest_scope: str = "latest",
    all_scope: str = "all",
    selected_scope: str = "selected",
) -> set[str] | None:
    scope = str(quarter_scope or latest_scope).strip().lower()
    if scope == all_scope:
        return None
    if scope == selected_scope:
        return {str(quarter_key or "").strip() for quarter_key in (quarter_keys or []) if str(quarter_key or "").strip()}
    return {
        str(quarter_key or "").strip()
        for quarter_key in (latest_quarter_map or {}).values()
        if str(quarter_key or "").strip()
    }


def filter_rows_to_stock_universe(
    rows: list[dict],
    stock_universe_provider,
    *,
    cancellation_token: CancellationToken | None = None,
) -> list[dict]:
    _raise_if_cancelled(cancellation_token)
    try:
        stock_codes = stock_universe_provider() or set()
    except (FileNotFoundError, RuntimeError, OSError, TypeError, ValueError):
        return []
    _raise_if_cancelled(cancellation_token)
    try:
        result = filter_rows_to_ai_chain_codes(
            rows,
            code_keys=("stock_code", "代码", "股票代码"),
            stock_codes=stock_codes,
        )
    except (FileNotFoundError, RuntimeError, OSError, TypeError, ValueError):
        return []
    _raise_if_cancelled(cancellation_token)
    return result


def query_change_rows_for_scope(
    quarter_keys: set[str] | None,
    *,
    stock_universe_provider,
    store=fund_holdings_store,
    cancellation_token: CancellationToken | None = None,
) -> list[dict]:
    _raise_if_cancelled(cancellation_token)
    try:
        rows = store.query_change_rows(quarter_keys=quarter_keys)
    except TypeError:
        rows = store.query_change_rows()
        if quarter_keys is not None:
            rows = [row for row in rows or [] if str(row.get("quarter_key") or "").strip() in quarter_keys]
    _raise_if_cancelled(cancellation_token)
    return filter_rows_to_stock_universe(
        rows,
        stock_universe_provider,
        cancellation_token=cancellation_token,
    )


def build_fund_holdings_view_rows(
    change_rows: list[dict],
    *,
    latest_quarter_map: dict[str, str],
    chain_context_map: dict[str, str] | None,
    concept_sector_cache: dict[str, str],
    capital_attribute_labels: dict[str, str],
    placeholder: str = FUND_DISPLAY_PLACEHOLDER,
    subject_code_qfii: str = SUBJECT_QFII["subject_code"],
    cancellation_token: CancellationToken | None = None,
) -> list[dict]:
    rows: list[dict] = []
    for row in _cancellable_rows(change_rows, cancellation_token):
        stock_code = str(row.get("stock_code") or "").strip()
        subject_code = str(row.get("subject_code") or "").strip()
        quarter_key = str(row.get("quarter_key") or "").strip()
        change_type = str(row.get("change_type") or "").strip()
        capital_attribute = str(row.get("capital_attribute") or "").strip()
        subject_name = str(row.get("subject_name") or "").strip()
        if subject_code == subject_code_qfii and not capital_attribute:
            capital_attribute = QFII_CAPITAL_ATTRIBUTE_UNMARKED
        capital_attribute_text = capital_attribute_label(capital_attribute, capital_attribute_labels)
        has_curr = change_type != "退出"
        has_prev = change_type != "新进"
        rows.append(
            {
                "代码": stock_code,
                "名称": str(row.get("stock_name") or "").strip(),
                "市价": placeholder,
                "涨幅%": placeholder,
                "市值": placeholder,
                "主体": shorten_subject_name(subject_name),
                "主体原名": subject_name,
                "资金属性": capital_attribute_text,
                "主体代码": subject_code,
                "季度": quarter_key,
                "变化类型": change_type,
                "本期占比": format_pct(row.get("curr_ratio_pct"), show=has_curr, placeholder=placeholder),
                "本期持股": format_amount(
                    row.get("curr_hold_num_shares"),
                    divisor=10000.0,
                    show=has_curr,
                    placeholder=placeholder,
                ),
                "上期持股": format_amount(
                    row.get("prev_hold_num_shares"),
                    divisor=10000.0,
                    show=has_prev,
                    placeholder=placeholder,
                ),
                "持股变化": format_amount(
                    row.get("delta_hold_num_shares"),
                    divisor=10000.0,
                    show=has_curr or has_prev,
                    signed=True,
                    placeholder=placeholder,
                ),
                "概念板块": build_ai_chain_context_text(
                    stock_code,
                    chain_context_map,
                    concept_sector_cache,
                    placeholder=placeholder,
                ),
                "_capital_attribute_value": capital_attribute,
                "_is_latest_subject_quarter": quarter_key == latest_quarter_map.get(subject_code),
            }
        )
    return rows


def load_fund_holdings_view_payload(
    *,
    quarter_scope: str,
    quarter_keys=None,
    stock_universe_provider,
    chain_context_provider,
    capital_attribute_labels: dict[str, str],
    latest_scope: str = "latest",
    all_scope: str = "all",
    selected_scope: str = "selected",
    store=fund_holdings_store,
    cancellation_token: CancellationToken | None = None,
) -> dict:
    latest_quarter_map = _run_stage(cancellation_token, store.get_latest_quarter_map)
    latest_sync_map = _run_stage(cancellation_token, store.get_latest_sync_map)
    normalized_scope = str(quarter_scope or latest_scope).strip().lower()
    query_quarters = resolve_query_quarters(
        latest_quarter_map,
        quarter_scope=normalized_scope,
        quarter_keys=quarter_keys,
        latest_scope=latest_scope,
        all_scope=all_scope,
        selected_scope=selected_scope,
    )
    change_rows = query_change_rows_for_scope(
        query_quarters,
        stock_universe_provider=stock_universe_provider,
        store=store,
        cancellation_token=cancellation_token,
    )
    concept_sector_cache: dict[str, str] = {}
    chain_context_map = load_ai_chain_context_map_safely(
        chain_context_provider,
        cancellation_token=cancellation_token,
    )
    return {
        "latest_quarter_map": latest_quarter_map,
        "latest_sync_map": latest_sync_map,
        "concept_sector_cache": concept_sector_cache,
        "view_rows": build_fund_holdings_view_rows(
            change_rows,
            latest_quarter_map=latest_quarter_map,
            chain_context_map=chain_context_map,
            concept_sector_cache=concept_sector_cache,
            capital_attribute_labels=capital_attribute_labels,
            cancellation_token=cancellation_token,
        ),
        "loaded_quarter_scope": normalized_scope,
        "loaded_quarter_keys": sorted(query_quarters or []),
    }
