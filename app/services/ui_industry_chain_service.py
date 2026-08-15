# -*- coding: utf-8 -*-
"""Application facade combining pure industry-chain rules with storage I/O."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from domains.industry_chain.pool_service import (
    PLACEHOLDER,
    build_ai_industry_chain_context_map,
    cell_text,
    format_ai_industry_chain_context,
    normalize_ai_chain_code,
    normalize_stock_code_from_row,
)
from domains.industry_chain.pool_service import (
    filter_rows_to_ai_chain_codes as _filter_rows,
)
from infra.storage.industry_chain_repository import (
    AI_CHAIN_CANDIDATE_PATHS,
    AI_CHAIN_CODES_CACHE_FILE,
    AI_CHAIN_CONTEXT_CACHE_FILE,
    AI_CHAIN_FILE,
    AI_CHAIN_ROWS_CACHE_FILE,
    IndustryChainRepository,
    resolve_ai_chain_file,
)


def _repository() -> IndustryChainRepository:
    """Build from module-level paths so legacy monkeypatch seams stay functional."""

    return IndustryChainRepository(
        workbook_path=AI_CHAIN_FILE,
        rows_cache_path=AI_CHAIN_ROWS_CACHE_FILE,
        codes_cache_path=AI_CHAIN_CODES_CACHE_FILE,
        context_cache_path=AI_CHAIN_CONTEXT_CACHE_FILE,
    )


def _source_path(workbook_path: Any | None) -> Any:
    return workbook_path if workbook_path is not None else AI_CHAIN_FILE


def load_ai_industry_chain_rows(workbook_path: Any | None = None) -> list[dict]:
    return _repository().read_rows(_source_path(workbook_path))


def load_cached_ai_industry_chain_rows(workbook_path: Any | None = None) -> list[dict]:
    return _repository().load_cached_rows(_source_path(workbook_path))


def load_cached_ai_industry_chain_stock_codes(workbook_path: Any | None = None) -> set[str]:
    return _repository().load_cached_stock_codes(_source_path(workbook_path))


def load_cached_ai_industry_chain_context_map(workbook_path: Any | None = None) -> dict[str, str]:
    return _repository().load_cached_context_map(_source_path(workbook_path))


def refresh_ai_industry_chain_rows(workbook_path: Any | None = None) -> list[dict]:
    path = _source_path(workbook_path)
    rows = load_ai_industry_chain_rows(path)
    _repository().write_caches(rows, path)
    return rows


def get_ai_industry_chain_source_mtime(workbook_path: Any | None = None) -> float:
    return _repository().source_mtime(_source_path(workbook_path))


def load_ai_industry_chain_stock_codes(workbook_path: Any | None = None) -> set[str]:
    path = _source_path(workbook_path)
    repository = _repository()
    if repository.is_default_source(path):
        cached = load_cached_ai_industry_chain_stock_codes(path)
        if cached:
            return cached
    return {code for row in refresh_ai_industry_chain_rows(path) if (code := normalize_ai_chain_code(row.get("代码")))}


def load_ai_industry_chain_context_map(workbook_path: Any | None = None) -> dict[str, str]:
    path = _source_path(workbook_path)
    repository = _repository()
    if repository.is_default_source(path):
        cached = load_cached_ai_industry_chain_context_map(path)
        if cached:
            return cached
    return build_ai_industry_chain_context_map(refresh_ai_industry_chain_rows(path))


def filter_rows_to_ai_chain_codes(
    rows: Iterable[dict] | None,
    *,
    code_keys: Iterable[str] = ("代码", "股票代码", "证券代码", "stock_code"),
    stock_codes: Iterable[str] | None = None,
    workbook_path: Any | None = None,
) -> list[dict]:
    allowed_codes = stock_codes if stock_codes is not None else load_ai_industry_chain_stock_codes(workbook_path)
    return _filter_rows(rows, code_keys=code_keys, stock_codes=allowed_codes)


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
