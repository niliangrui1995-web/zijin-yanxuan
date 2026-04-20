# -*- coding: utf-8 -*-
from __future__ import annotations

import logging


def resolve_active_quote_model(owner):
    return (
        getattr(owner, "_active_model_ref", None)
        or getattr(owner, "source_model", None)
        or getattr(owner, "model", None)
    )


def apply_quote_snapshot(owner, quotes: dict | None) -> None:
    model = resolve_active_quote_model(owner)
    if model and hasattr(model, "update_quotes") and quotes:
        model.update_quotes(quotes)


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
