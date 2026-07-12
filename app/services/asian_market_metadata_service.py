"""Application facade for optional Asian-market business metadata."""

from __future__ import annotations

from collections.abc import Collection
from pathlib import Path

from core.logger import get_logger
from infra.storage.asian_market_metadata import read_pipeline_industry_roles

log = get_logger(__name__)
PROJECT_ROOT = Path(__file__).resolve().parents[2]
PIPELINE_INDUSTRY_DICT = PROJECT_ROOT.parent / "每日战报" / "每日战报" / "industry_dict.py"


def load_pipeline_industry_roles(
    *,
    excluded_tickers: Collection[str] = (),
) -> dict[str, str]:
    try:
        return read_pipeline_industry_roles(
            PIPELINE_INDUSTRY_DICT,
            excluded_tickers=excluded_tickers,
        )
    except (FileNotFoundError, PermissionError, OSError, TypeError, ValueError) as exc:
        log.error("[AsianMarket] failed to load pipeline industry roles: %s", exc)
        return {}


__all__ = ["load_pipeline_industry_roles"]
