# -*- coding: utf-8 -*-

from __future__ import annotations

from importlib import import_module

__all__ = [
    "build_finance_quote_payload",
    "coerce_number",
    "enrich_quotes_with_finance",
    "get_missing_a_share_finance_codes",
    "has_valid_quote",
    "is_a_share_code",
    "merge_quote_entry",
    "merge_quote_snapshot_inplace",
    "normalize_quote_payload",
    "publish_rt_quotes",
    "resolve_quote_metrics",
]

_EXPORTS = {
    "build_finance_quote_payload": ("domains.quotes.snapshot", "build_finance_quote_payload"),
    "coerce_number": ("domains.quotes.snapshot", "coerce_number"),
    "enrich_quotes_with_finance": ("domains.quotes.snapshot", "enrich_quotes_with_finance"),
    "get_missing_a_share_finance_codes": ("domains.quotes.snapshot", "get_missing_a_share_finance_codes"),
    "has_valid_quote": ("domains.quotes.dispatcher", "has_valid_quote"),
    "is_a_share_code": ("domains.quotes.snapshot", "is_a_share_code"),
    "merge_quote_entry": ("domains.quotes.snapshot", "merge_quote_entry"),
    "merge_quote_snapshot_inplace": ("domains.quotes.snapshot", "merge_quote_snapshot_inplace"),
    "normalize_quote_payload": ("domains.quotes.dispatcher", "normalize_quote_payload"),
    "publish_rt_quotes": ("domains.quotes.dispatcher", "publish_rt_quotes"),
    "resolve_quote_metrics": ("domains.quotes.snapshot", "resolve_quote_metrics"),
}


def __getattr__(name: str):
    target = _EXPORTS.get(name)
    if target is None:
        raise AttributeError(name)
    module_name, attr_name = target
    module = import_module(module_name)
    value = getattr(module, attr_name)
    globals()[name] = value
    return value
