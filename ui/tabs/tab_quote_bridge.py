# -*- coding: utf-8 -*-
from __future__ import annotations

import logging

_CODE_KEY = "\u4ee3\u7801"


def resolve_active_quote_model(owner):
    return (
        getattr(owner, "_active_model_ref", None)
        or getattr(owner, "source_model", None)
        or getattr(owner, "model", None)
    )


def _quote_code_candidates(owner, raw_code) -> list[str]:
    raw = str(raw_code or "").strip()
    candidates = []
    if raw:
        candidates.append(raw)
        if raw.isdigit():
            candidates.append(raw.zfill(6))

    normalize_code = getattr(owner, "_normalize_quote_code", None)
    if callable(normalize_code):
        normalized = str(normalize_code(raw_code) or "").strip()
        if normalized:
            candidates.append(normalized)
            if normalized.isdigit():
                candidates.append(normalized.zfill(6))
            else:
                candidates.append(normalized.upper())

    return list(dict.fromkeys(candidates))


def _quote_subset_for_model(owner, model, quotes: dict) -> dict:
    row_data = getattr(model, "row_data", None)
    if not row_data:
        return quotes

    quote_map = dict(quotes or {})
    if not quote_map:
        return {}

    subset = {}
    for row in row_data:
        if not isinstance(row, dict):
            continue
        for code in _quote_code_candidates(owner, row.get(_CODE_KEY, "")):
            if code in quote_map:
                subset[code] = quote_map[code]
                if len(subset) == len(quote_map):
                    break
        if len(subset) == len(quote_map):
            break
    return subset


def apply_quote_snapshot(owner, quotes: dict | None) -> None:
    model = resolve_active_quote_model(owner)
    if model and hasattr(model, "update_quotes") and quotes:
        quote_subset = _quote_subset_for_model(owner, model, quotes)
        if quote_subset:
            model.update_quotes(quote_subset)


def resolve_quote_publisher(owner):
    publisher = getattr(owner, "_quote_publisher", None)
    if publisher is not None:
        return publisher
    owner_window = owner.window()
    return getattr(owner_window, "central_quotes_svc", None)


def publish_quote_payload(owner, payload, *, source: str, require_valid: bool = False) -> dict:
    normalized = dict(payload or {})
    if not normalized:
        return {}

    publisher = resolve_quote_publisher(owner)
    if publisher is None or not hasattr(publisher, "publish_external_quotes"):
        if not getattr(owner, "_missing_quote_publisher_warned", False):
            owner._missing_quote_publisher_warned = True
            logging.getLogger(__name__).warning(
                f"[{owner.__class__.__name__}] 未找到 central_quotes_svc，已跳过外部报价广播"
            )
        return {}

    owner._missing_quote_publisher_warned = False
    return publisher.publish_external_quotes(
        normalized,
        source=source,
        require_valid=require_valid,
    ) or {}
