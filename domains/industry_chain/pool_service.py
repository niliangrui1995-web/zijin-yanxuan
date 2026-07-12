# -*- coding: utf-8 -*-
"""Pure normalization, filtering and context rules for industry-chain rows."""

from __future__ import annotations

from collections.abc import Iterable, Sequence

PLACEHOLDER = "--"


def cell_text(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value).strip()


def normalize_ai_chain_code(value: object) -> str:
    text = cell_text(value)
    if text.endswith(".0"):
        text = text[:-2]
    if text.isdigit() and len(text) <= 6:
        text = text.zfill(6)
    return text if len(text) == 6 and text.isdigit() else ""


def build_ai_industry_chain_rows(raw_rows: Iterable[Sequence[object]]) -> list[dict]:
    """Convert worksheet values into stable application-facing row dictionaries."""

    rows = list(raw_rows)
    if not rows:
        return []

    headers = [cell_text(value) for value in rows[0]]
    header_map = {header: index for index, header in enumerate(headers) if header}

    def _get(row: Sequence[object], header: str) -> str:
        index = header_map.get(header)
        if index is None or index >= len(row):
            return ""
        return cell_text(row[index])

    result: list[dict] = []
    for raw_row in rows[1:]:
        code = normalize_ai_chain_code(_get(raw_row, "代码"))
        if not code:
            continue
        result.append(
            {
                "代码": code,
                "名称": _get(raw_row, "公司名称") or _get(raw_row, "名称") or code,
                "现价": PLACEHOLDER,
                "涨幅": PLACEHOLDER,
                "市值": PLACEHOLDER,
                "细分板块": _get(raw_row, "细分板块") or _get(raw_row, "细分环节"),
                "5日涨幅": PLACEHOLDER,
                "10日涨幅": PLACEHOLDER,
                "20日涨幅": PLACEHOLDER,
                "备注": _get(raw_row, "备注"),
            }
        )
    return result


def format_ai_industry_chain_context(row: dict | None, *, placeholder: str = PLACEHOLDER) -> str:
    if not isinstance(row, dict):
        return placeholder

    parts: list[str] = []
    for key in ("细分板块", "细分环节", "备注"):
        text = cell_text(row.get(key))
        if text and text not in parts:
            parts.append(text)
    return " | ".join(parts) if parts else placeholder


def build_ai_industry_chain_context_map(rows: Iterable[dict]) -> dict[str, str]:
    grouped: dict[str, list[str]] = {}
    for row in rows:
        code = normalize_ai_chain_code(row.get("代码"))
        text = format_ai_industry_chain_context(row)
        if not code or text == PLACEHOLDER:
            continue
        bucket = grouped.setdefault(code, [])
        if text not in bucket:
            bucket.append(text)
    return {code: "；".join(values) for code, values in grouped.items()}


def normalize_stock_code_from_row(row: dict, code_keys: Iterable[str]) -> str:
    if not isinstance(row, dict):
        return ""
    for key in code_keys:
        code = normalize_ai_chain_code(row.get(key))
        if code:
            return code
    return ""


def filter_rows_to_ai_chain_codes(
    rows: Iterable[dict] | None,
    *,
    code_keys: Iterable[str] = ("代码", "股票代码", "证券代码", "stock_code"),
    stock_codes: Iterable[str],
) -> list[dict]:
    """Filter rows against an explicitly supplied stock universe."""

    allowed_codes = {normalize_ai_chain_code(code) for code in stock_codes}
    allowed_codes.discard("")
    return [
        row
        for row in rows or []
        if isinstance(row, dict) and normalize_stock_code_from_row(row, code_keys) in allowed_codes
    ]


__all__ = [
    "PLACEHOLDER",
    "build_ai_industry_chain_context_map",
    "build_ai_industry_chain_rows",
    "cell_text",
    "filter_rows_to_ai_chain_codes",
    "format_ai_industry_chain_context",
    "normalize_ai_chain_code",
    "normalize_stock_code_from_row",
]
