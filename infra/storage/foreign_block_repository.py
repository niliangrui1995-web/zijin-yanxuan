# -*- coding: utf-8 -*-
"""Persistence boundary for the foreign block-trade snapshot."""

from __future__ import annotations

from pathlib import Path
from typing import Any, TypedDict

from core.exceptions import DataFormatError
from infra.storage import json_cache_repository


class ForeignBlockCachePayload(TypedDict):
    saved_at: str
    days_to_fetch: int
    latest_trade_date: str
    rows: list[dict[str, Any]]


_CACHE_FILE = Path(__file__).resolve().parents[2] / "data" / "Cache" / "foreign_block_trade_latest.json"


def _normalize_rows(value: object) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        raise DataFormatError("block trade cache rows invalid")
    if any(not isinstance(row, dict) for row in value):
        raise DataFormatError("block trade cache row invalid")
    return [dict(row) for row in value]


def _normalize_days_to_fetch(value: object) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError) as exc:
        raise DataFormatError("block trade cache days_to_fetch invalid") from exc


def load_foreign_block_cache_payload() -> ForeignBlockCachePayload:
    payload = json_cache_repository.load_json_file(str(_CACHE_FILE))
    if not isinstance(payload, dict):
        raise DataFormatError("block trade cache payload invalid")
    return {
        "saved_at": str(payload.get("saved_at", "")).strip(),
        "days_to_fetch": _normalize_days_to_fetch(payload.get("days_to_fetch", 0)),
        "latest_trade_date": str(payload.get("latest_trade_date", "")).strip(),
        "rows": _normalize_rows(payload.get("rows")),
    }


def save_foreign_block_cache_payload(payload: ForeignBlockCachePayload) -> None:
    json_cache_repository.save_json_file(str(_CACHE_FILE), payload)


__all__ = [
    "ForeignBlockCachePayload",
    "load_foreign_block_cache_payload",
    "save_foreign_block_cache_payload",
]
