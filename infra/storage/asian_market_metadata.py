"""Filesystem reader for optional Asian-market metadata sources."""

from __future__ import annotations

import re
from collections.abc import Collection
from pathlib import Path

_ASIAN_TICKER_MARKERS = ('.T"', '.TW"', '.TWO"', '.KS"', '.HK"')
_TICKER_RE = re.compile(r'"([A-Z0-9\.]+)"')
_PARENTHESIZED_ROLE_RE = re.compile(r"[(（](.*?)[)）]")


def _parse_role_line(line: str, excluded_tickers: Collection[str]) -> tuple[str, str] | None:
    if "#" not in line or not any(marker in line for marker in _ASIAN_TICKER_MARKERS):
        return None
    ticker_match = _TICKER_RE.search(line)
    if ticker_match is None:
        return None
    code = ticker_match.group(1)
    if code in excluded_tickers:
        return None
    comment = line.rsplit("#", 1)[-1].strip()
    role_match = _PARENTHESIZED_ROLE_RE.search(comment)
    if role_match is not None:
        comment = role_match.group(1).strip()
    return (code, comment) if comment else None


def read_pipeline_industry_roles(
    path: str | Path,
    *,
    excluded_tickers: Collection[str] = (),
) -> dict[str, str]:
    source_path = Path(path)
    if not source_path.is_file():
        return {}
    roles: dict[str, str] = {}
    with source_path.open("r", encoding="utf-8") as file_obj:
        for line in file_obj:
            parsed = _parse_role_line(line, excluded_tickers)
            if parsed is not None:
                roles[parsed[0]] = parsed[1]
    return roles


__all__ = ["read_pipeline_industry_roles"]
