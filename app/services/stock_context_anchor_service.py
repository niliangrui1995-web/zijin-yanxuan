"""Headless cache warm-up for the candidate tab's AI/NA anchor sources."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from app.services.stock_context_snapshot_service import (
    load_ai_chain_cache_rows,
    load_named_cache_rows,
    project_root,
)
from infra.tasks.lifecycle import raise_if_cancelled


@dataclass(frozen=True)
class StockContextAnchorCacheState:
    ai_row_count: int
    na_row_count: int


def warm_stock_context_anchor_caches(
    *,
    root: Path | None = None,
    cancellation_token=None,
) -> StockContextAnchorCacheState:
    """Read and validate existing anchor caches without constructing widgets."""

    raise_if_cancelled(cancellation_token)
    ai_rows = load_ai_chain_cache_rows()
    raise_if_cancelled(cancellation_token)
    na_rows = load_named_cache_rows("na_daily_latest.json", root=root or project_root())
    raise_if_cancelled(cancellation_token)
    return StockContextAnchorCacheState(ai_row_count=len(ai_rows), na_row_count=len(na_rows))


__all__ = ["StockContextAnchorCacheState", "warm_stock_context_anchor_caches"]
