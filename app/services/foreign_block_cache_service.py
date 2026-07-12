# -*- coding: utf-8 -*-
"""Application facade for the foreign block-trade cache."""

from __future__ import annotations

import datetime
from typing import Any, Callable, TypedDict

from infra.storage import foreign_block_repository
from infra.storage.foreign_block_repository import ForeignBlockCachePayload

ForeignBlockRow = dict[str, Any]
ForeignBlockRowFilter = Callable[[list[ForeignBlockRow]], list[ForeignBlockRow]]


class ForeignBlockCacheView(TypedDict):
    saved_at: str
    days_to_fetch: int
    latest_trade_date: str
    rows: list[ForeignBlockRow]
    raw_count: int


def _latest_trade_date(rows: list[ForeignBlockRow]) -> str:
    return max(
        (
            str(row.get("交易日期", "")).strip()
            for row in rows
            if str(row.get("交易日期", "")).strip()
        ),
        default="",
    )


def build_foreign_block_cache_payload(
    rows: list[ForeignBlockRow],
    *,
    days_to_fetch: int,
    latest_trade_date: str = "",
    saved_at: str = "",
) -> ForeignBlockCachePayload:
    normalized_rows = [dict(row) for row in (rows or [])]
    return {
        "saved_at": saved_at or datetime.datetime.now().isoformat(timespec="seconds"),
        "days_to_fetch": int(days_to_fetch),
        "latest_trade_date": str(latest_trade_date or _latest_trade_date(normalized_rows)).strip(),
        "rows": normalized_rows,
    }


def save_foreign_block_cache(
    rows: list[ForeignBlockRow],
    *,
    days_to_fetch: int,
    latest_trade_date: str = "",
) -> ForeignBlockCachePayload:
    payload = build_foreign_block_cache_payload(
        rows,
        days_to_fetch=days_to_fetch,
        latest_trade_date=latest_trade_date,
    )
    foreign_block_repository.save_foreign_block_cache_payload(payload)
    return payload


def load_foreign_block_cache(*, row_filter: ForeignBlockRowFilter | None = None) -> ForeignBlockCacheView:
    payload = foreign_block_repository.load_foreign_block_cache_payload()
    raw_rows = payload["rows"]
    rows = row_filter(raw_rows) if row_filter is not None else raw_rows
    return {
        "saved_at": payload["saved_at"],
        "days_to_fetch": payload["days_to_fetch"],
        "latest_trade_date": payload["latest_trade_date"],
        "rows": rows,
        "raw_count": len(raw_rows),
    }


__all__ = [
    "ForeignBlockCachePayload",
    "ForeignBlockCacheView",
    "build_foreign_block_cache_payload",
    "load_foreign_block_cache",
    "save_foreign_block_cache",
]
