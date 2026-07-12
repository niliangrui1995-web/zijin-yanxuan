# -*- coding: utf-8 -*-
"""Pure parsers for North-America daily battle reports."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from typing import Any

_STOCK_SECTION_RE = re.compile(r"##\s*二、标的狙击表(.*?)(?=##\s*三、|$)", re.DOTALL)
_RECOMMENDATION_SECTION_RE = re.compile(
    r"##\s*四、今日操作建议(.*?)(?=##\s*[一二三四五六七八九十]|$)",
    re.DOTALL,
)
_INDUSTRY_RE = re.compile(r"###\s+(?:\U0001f534\s*|\U0001f7e1\s*|\U0001f7e2\s*)?(.+?)[\n\r]")


def _clean_industry(value: object) -> str:
    industry = re.split(r"[（(]", str(value or ""))[0].strip()
    return re.sub(r"^赛道[A-Za-z0-9]+[：:\s]*", "", industry)


def _table_cells(row_text: str) -> list[str]:
    cells = [cell.strip() for cell in row_text.split("|")]
    if len(cells) >= 3 and cells[0] == "" and cells[-1] == "":
        return cells[1:-1]
    return cells


def _is_separator(cells: Sequence[str]) -> bool:
    return bool(cells) and all("---" in cell or not cell for cell in cells)


def _industry_blocks(section: str) -> list[tuple[str, str]]:
    matches = list(_INDUSTRY_RE.finditer(section))
    blocks: list[tuple[str, str]] = []
    for index, matched in enumerate(matches):
        start = matched.end()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(section)
        blocks.append((_clean_industry(matched.group(1)), section[start:end]))
    return blocks


def _stock_table(block: str) -> tuple[list[str], list[list[str]]]:
    header: list[str] = []
    rows: list[list[str]] = []
    for row_text in block.strip().split("\n"):
        cells = _table_cells(row_text)
        if "代码" in cells and ("标的" in cells or "名称" in cells):
            header = cells
            continue
        if not header or _is_separator(cells):
            continue
        rows.append(cells)
    return header, rows


def _column_index(header: Sequence[str], keywords: Sequence[str]) -> int:
    for index, title in enumerate(header):
        if any(keyword in title for keyword in keywords):
            return index
    return -1


def _stock_column_indexes(header: Sequence[str]) -> dict[str, int]:
    return {
        "name": _column_index(header, ("标的", "名称")),
        "code": _column_index(header, ("代码",)),
        "chg_3m": _column_index(header, ("近3月",)),
        "percentile": _column_index(header, ("分位",)),
        "elasticity": _column_index(header, ("弹性",)),
        "rs": _column_index(header, ("RS",)),
        "weekly": _column_index(header, ("周线",)),
        "catalyst": _column_index(header, ("催化剂",)),
        "risk": _column_index(header, ("风控",)),
    }


def _cell(row: Sequence[str], index: int) -> str:
    if index < 0 or index >= len(row):
        return ""
    return row[index].replace("**", "").strip()


def _stock_row(industry: str, row: Sequence[str], indexes: Mapping[str, int]) -> dict[str, str] | None:
    code = _cell(row, indexes["code"])
    if len(row) < 3 or re.fullmatch(r"\d{6}", code) is None:
        return None
    return {
        "行业": industry,
        "名称": _cell(row, indexes["name"]),
        "代码": code,
        "近3月": _cell(row, indexes["chg_3m"]),
        "分位": _cell(row, indexes["percentile"]),
        "弹性": _cell(row, indexes["elasticity"]),
        "RS强度": _cell(row, indexes["rs"]),
        "周线趋势": _cell(row, indexes["weekly"]),
        "催化剂": _cell(row, indexes["catalyst"]),
        "风控": _cell(row, indexes["risk"]),
    }


def parse_battle_report(content: str) -> list[dict[str, str]]:
    matched = _STOCK_SECTION_RE.search(content)
    if matched is None:
        return []
    stocks: list[dict[str, str]] = []
    for industry, block in _industry_blocks(matched.group(1)):
        header, rows = _stock_table(block)
        if not header:
            continue
        indexes = _stock_column_indexes(header)
        for row in rows:
            stock = _stock_row(industry, row, indexes)
            if stock is not None:
                stocks.append(stock)
    return stocks


def _recommendation_row(line: str) -> tuple[str, dict[str, str]] | None:
    code_match = re.search(r"(\d{6})", line)
    if code_match is None:
        return None
    cells = _table_cells(line)
    if len(cells) < 3:
        return None
    return code_match.group(1), {
        "priority": _cell(cells, 0),
        "reason": _cell(cells, 3),
        "strategy": _cell(cells, 4),
    }


def _is_recommendation_header(line: str) -> bool:
    return line.startswith("|") and "优先级" in line


def _is_markdown_separator(line: str) -> bool:
    return line.startswith("|") and "---" in line


def parse_recommendations(content: str) -> dict[str, dict[str, str]]:
    matched = _RECOMMENDATION_SECTION_RE.search(content)
    if matched is None:
        return {}
    result: dict[str, dict[str, str]] = {}
    in_table = False
    found_separator = False
    for line in matched.group(1).split("\n"):
        stripped = line.strip()
        if not in_table:
            in_table = _is_recommendation_header(stripped)
            continue
        if _is_markdown_separator(stripped):
            found_separator = True
            continue
        if not stripped.startswith("|") or not stripped:
            break
        parsed = _recommendation_row(stripped) if found_separator else None
        if parsed is not None:
            result[parsed[0]] = parsed[1]
    return result


def _structured_stocks(data: Mapping[str, Any]) -> list[dict[str, Any]]:
    stocks: list[dict[str, Any]] = []
    for track in data.get("sniper_tables", []):
        if not isinstance(track, Mapping):
            continue
        industry = _clean_industry(track.get("track_name", "未知赛道"))
        for target in track.get("targets", []):
            if not isinstance(target, Mapping):
                continue
            stocks.append(
                {
                    "行业": industry,
                    "名称": target.get("name", ""),
                    "代码": str(target.get("code", "") or "").strip(),
                    "近3月": target.get("chg_3m", ""),
                    "分位": target.get("percentile_250d", ""),
                    "量能": target.get("volume", ""),
                    "弹性": target.get("elasticity", ""),
                    "催化剂": target.get("catalyst", ""),
                    "风控": target.get("risk", ""),
                }
            )
    return stocks


def _structured_recommendations(data: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    recommendations: dict[str, dict[str, Any]] = {}
    for advice in data.get("today_advice", []):
        if not isinstance(advice, Mapping) or not advice.get("code"):
            continue
        recommendations[str(advice["code"]).strip()] = {
            "priority": advice.get("priority", ""),
            "reason": advice.get("reason", ""),
            "strategy": advice.get("strategy", ""),
        }
    return recommendations


def parse_structured_report(data: Mapping[str, Any]) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]]]:
    return _structured_stocks(data), _structured_recommendations(data)
