# -*- coding: utf-8 -*-
from __future__ import annotations

import logging
import time
from collections.abc import Mapping
from typing import Any

from core.observability import record_metric

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


def _quote_subset_for_model(
    owner,
    model,
    quotes: Mapping[str, Mapping[str, Any]],
) -> Mapping[str, Mapping[str, Any]]:
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


def apply_quote_snapshot(
    owner,
    quotes: Mapping[str, Mapping[str, Any]] | None,
    *,
    record_flash: bool = True,
) -> dict:
    model = resolve_active_quote_model(owner)
    stats = {
        "payload_codes": len(quotes or {}),
        "applied_codes": 0,
        "changed_rows": 0,
        "elapsed_ms": 0.0,
    }
    if not model or not hasattr(model, "update_quotes") or not quotes:
        return stats

    quote_subset = _quote_subset_for_model(owner, model, quotes)
    if not quote_subset:
        return stats

    stats["applied_codes"] = len(quote_subset)
    started_at = time.perf_counter()
    if record_flash:
        changed_rows = model.update_quotes(quote_subset)
    else:
        try:
            changed_rows = model.update_quotes(quote_subset, record_flash=False)
        except TypeError:
            # Models predating the presentation contract have no flash layer;
            # retain their normal data behavior rather than rejecting a hidden
            # Watchlist projection.
            changed_rows = model.update_quotes(quote_subset)
    elapsed_ms = (time.perf_counter() - started_at) * 1000.0
    stats["elapsed_ms"] = elapsed_ms
    if changed_rows is not None:
        try:
            stats["changed_rows"] = int(changed_rows)
        except (TypeError, ValueError):
            stats["changed_rows"] = 0
    record_metric(
        "quote_snapshot_apply_ms",
        elapsed_ms,
        unit="ms",
        tags={
            "tab": owner.__class__.__name__,
            "model": model.__class__.__name__,
            "payload_codes": str(stats["payload_codes"]),
            "applied_codes": str(stats["applied_codes"]),
            "changed_rows": str(stats["changed_rows"]),
            "record_flash": str(bool(record_flash)).lower(),
        },
    )
    return stats


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
    return (
        publisher.publish_external_quotes(
            normalized,
            source=source,
            require_valid=require_valid,
        )
        or {}
    )
