"""Deprecated compatibility exports for :mod:`app.services.ui_industry_chain_service`."""

from __future__ import annotations

import app.services.ui_industry_chain_service as _implementation
from app.services.ui_industry_chain_service import (
    AI_CHAIN_CANDIDATE_PATHS,
    AI_CHAIN_CODES_CACHE_FILE,
    AI_CHAIN_CONTEXT_CACHE_FILE,
    AI_CHAIN_FILE,
    AI_CHAIN_ROWS_CACHE_FILE,
    PLACEHOLDER,
    cell_text,
    filter_rows_to_ai_chain_codes,
    format_ai_industry_chain_context,
    get_ai_industry_chain_source_mtime,
    load_ai_industry_chain_context_map,
    load_ai_industry_chain_rows,
    load_ai_industry_chain_stock_codes,
    load_cached_ai_industry_chain_context_map,
    load_cached_ai_industry_chain_rows,
    load_cached_ai_industry_chain_stock_codes,
    normalize_ai_chain_code,
    normalize_stock_code_from_row,
    refresh_ai_industry_chain_rows,
    resolve_ai_chain_file,
)

__all__ = [
    "AI_CHAIN_CANDIDATE_PATHS",
    "AI_CHAIN_CODES_CACHE_FILE",
    "AI_CHAIN_CONTEXT_CACHE_FILE",
    "AI_CHAIN_FILE",
    "AI_CHAIN_ROWS_CACHE_FILE",
    "PLACEHOLDER",
    "cell_text",
    "filter_rows_to_ai_chain_codes",
    "format_ai_industry_chain_context",
    "get_ai_industry_chain_source_mtime",
    "load_ai_industry_chain_context_map",
    "load_ai_industry_chain_rows",
    "load_ai_industry_chain_stock_codes",
    "load_cached_ai_industry_chain_context_map",
    "load_cached_ai_industry_chain_rows",
    "load_cached_ai_industry_chain_stock_codes",
    "normalize_ai_chain_code",
    "normalize_stock_code_from_row",
    "refresh_ai_industry_chain_rows",
    "resolve_ai_chain_file",
]


def __getattr__(name: str):
    return getattr(_implementation, name)
