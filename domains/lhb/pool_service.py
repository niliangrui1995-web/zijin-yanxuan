# -*- coding: utf-8 -*-
"""Pure policies for the dragon-tiger-list rolling pool."""

from __future__ import annotations

from collections.abc import Iterable

from domains.industry_chain.pool_service import normalize_ai_chain_code

POOL_WINDOW = 30
AI_CHAIN_BSE_LHB_ENABLED_CODES = frozenset({"920045"})
AI_CHAIN_BSE_LHB_MISSING_RPS250_EXEMPT_CODES = frozenset({"920045"})
NET_BUY_WAN_KEY = "net_buy_wan"
INSTITUTION_NET_BUY_WAN_KEY = "institution_net_buy_wan"
_LEGACY_NET_BUY_WAN_KEY = "上榜净买额(万)"
_LEGACY_INSTITUTION_NET_BUY_WAN_KEY = "机构净买(万)"


def to_int(value: object, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def to_float(value: object, default: float = 0.0) -> float:
    try:
        normalized = value
        if isinstance(normalized, str):
            normalized = normalized.strip().replace("%", "").replace("+", "")
            if normalized in {"", "-", "--"}:
                return default
        return float(normalized)
    except (TypeError, ValueError):
        return default


def net_buy_wan_value(record: dict) -> float:
    """Read canonical net-buy data while accepting persisted display-key records."""
    source = record if isinstance(record, dict) else {}
    return to_float(source.get(NET_BUY_WAN_KEY, source.get(_LEGACY_NET_BUY_WAN_KEY)), 0.0)


def institution_net_buy_wan_value(record: dict) -> float:
    """Read canonical institution net-buy data while accepting legacy records."""
    source = record if isinstance(record, dict) else {}
    return to_float(
        source.get(INSTITUTION_NET_BUY_WAN_KEY, source.get(_LEGACY_INSTITUTION_NET_BUY_WAN_KEY)),
        0.0,
    )


def _format_foreign_branch_line(line: str) -> str:
    if "：" not in line:
        return ""
    branch, detail = line.split("：", 1)
    detail = detail.strip()
    if detail.startswith("净买"):
        return f"{branch}+{detail[2:]}"
    if detail.startswith("净卖"):
        return f"{branch}-{detail[2:]}"
    if detail.startswith("平衡"):
        return f"{branch}±0"
    return ""


def _has_foreign_summary(lines: list[str]) -> bool:
    return bool(lines) and lines[0].startswith("外资合计：")


def build_full_foreign_display_from_tooltip(tooltip: str) -> str:
    tooltip_text = str(tooltip or "").strip()
    if not tooltip_text:
        return ""
    if tooltip_text == "当日未发现外资席位上榜":
        return "未现身"

    lines = [line.strip() for line in tooltip_text.splitlines() if line.strip()]
    if not _has_foreign_summary(lines):
        return ""
    summary = lines[0][len("外资合计：") :].strip()
    short_parts = [part for line in lines[1:] if (part := _format_foreign_branch_line(line))]
    return f"{summary} | {' / '.join(short_parts)}" if short_parts else summary


def upgrade_legacy_foreign_display_cache(data: dict[str, list[dict]]) -> int:
    updated_count = 0
    for records in data.values():
        if not isinstance(records, list):
            continue
        for record in records:
            if not isinstance(record, dict):
                continue
            display = str(record.get("外资净买入") or "").strip()
            if "等" not in display or "席" not in display:
                continue
            full_display = build_full_foreign_display_from_tooltip(record.get("_外资净买入_tooltip", ""))
            if full_display and full_display != display:
                record["外资净买入"] = full_display
                updated_count += 1
    return updated_count


def pool_sort_key(row: dict) -> tuple:
    source = row or {}
    has_buy_point = 1 if str(source.get("买点", "") or "").strip() else 0
    pct = to_float(source.get("涨幅%"), 0.0)
    recent_date = str(source.get("_最近上榜_raw") or source.get("最近上榜", "") or "")
    if has_buy_point:
        return (1, pct, recent_date, 0.0)
    return (0, 0.0, recent_date, pct)


def sort_pool_rows_for_display(rows: Iterable[dict] | None) -> list[dict]:
    return sorted(list(rows or []), key=pool_sort_key, reverse=True)


def build_day_meta(
    records: list[dict],
    *,
    source_count: int | None = None,
    validation_ref_date: str = "",
    probe_status: str = "unverified",
) -> dict:
    record_count = len(records) if isinstance(records, list) else 0
    return {
        "record_count": record_count,
        "source_count": record_count if source_count is None else to_int(source_count, record_count),
        "last_probe_ref_date": str(validation_ref_date or ""),
        "probe_status": str(probe_status or "unverified"),
    }


def normalize_day_meta(meta: dict | None, records: list[dict]) -> dict:
    normalized = build_day_meta(records)
    if not isinstance(meta, dict):
        return normalized
    actual_count = len(records) if isinstance(records, list) else 0
    normalized["record_count"] = actual_count
    normalized["source_count"] = to_int(meta.get("source_count"), actual_count)
    normalized["last_probe_ref_date"] = str(meta.get("last_probe_ref_date", "") or "")
    normalized["probe_status"] = str(meta.get("probe_status", normalized["probe_status"]) or normalized["probe_status"])
    return normalized


def repair_day_meta(data: dict[str, list[dict]], day_meta: dict | None) -> dict[str, dict]:
    source_meta = day_meta if isinstance(day_meta, dict) else {}
    return {
        date_str: normalize_day_meta(source_meta.get(date_str), records if isinstance(records, list) else [])
        for date_str, records in data.items()
    }


def record_stock_code(record: dict) -> str:
    if not isinstance(record, dict):
        return ""
    return normalize_ai_chain_code(
        record.get("代码")
        or record.get("股票代码")
        or record.get("证券代码")
        or record.get("stock_code")
        or record.get("code")
    )


def filter_records_to_stock_universe(records: Iterable[dict] | None, stock_codes: set[str]) -> list[dict]:
    return [record for record in records or [] if record_stock_code(record) in stock_codes]


def is_bse_code(code: str) -> bool:
    return bool(code) and (code[:2] in ("43", "83", "87") or code[0] == "9")


def is_st_stock(name: str) -> bool:
    return "ST" in str(name or "").upper()


def collect_qualifying_codes(
    data_snapshot: dict[str, list[dict]],
    stock_universe_codes: set[str],
) -> tuple[set[str], dict[str, int]]:
    qualifying_codes: set[str] = set()
    code_hit_count: dict[str, int] = {}
    for records in data_snapshot.values():
        for record in records:
            code = record_stock_code(record)
            if not code or code not in stock_universe_codes:
                continue
            if (
                (is_bse_code(code) and code not in AI_CHAIN_BSE_LHB_ENABLED_CODES)
                or is_st_stock(record.get("名称", ""))
            ):
                continue
            net_buy = net_buy_wan_value(record)
            institution_net_buy = institution_net_buy_wan_value(record)
            if net_buy > 0 and institution_net_buy >= 0:
                qualifying_codes.add(code)
                code_hit_count[code] = code_hit_count.get(code, 0) + 1
    return qualifying_codes, code_hit_count


__all__ = [
    "INSTITUTION_NET_BUY_WAN_KEY",
    "NET_BUY_WAN_KEY",
    "POOL_WINDOW",
    "build_day_meta",
    "build_full_foreign_display_from_tooltip",
    "collect_qualifying_codes",
    "filter_records_to_stock_universe",
    "is_bse_code",
    "is_st_stock",
    "institution_net_buy_wan_value",
    "net_buy_wan_value",
    "normalize_day_meta",
    "pool_sort_key",
    "record_stock_code",
    "repair_day_meta",
    "sort_pool_rows_for_display",
    "to_float",
    "to_int",
    "upgrade_legacy_foreign_display_cache",
]
