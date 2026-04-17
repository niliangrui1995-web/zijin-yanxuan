# -*- coding: utf-8 -*-
"""基金持仓对比与聚合规则。"""

from __future__ import annotations

import re
from datetime import date

SUBJECT_TYPE_QFII = "qfii"
SUBJECT_TYPE_FUND = "fund"

SUBJECT_QFII = {
    "subject_code": "QFII",
    "subject_name": "QFII",
    "subject_type": SUBJECT_TYPE_QFII,
    "display_order": 10,
}

SUBJECT_RUIYUAN = {
    "subject_code": "007119",
    "subject_name": "睿远成长价值混合A",
    "subject_type": SUBJECT_TYPE_FUND,
    "display_order": 20,
}

SUBJECTS = {
    SUBJECT_QFII["subject_code"]: SUBJECT_QFII,
    SUBJECT_RUIYUAN["subject_code"]: SUBJECT_RUIYUAN,
}

_QUARTER_KEY_RE = re.compile(r"^\s*(\d{4})\s*[-/]?\s*[Qq季度]?\s*([1-4])\s*$")
_DATE_KEY_RE = re.compile(r"^\s*(\d{4})[-/](\d{2})[-/](\d{2})\s*$")
_EPSILON = 1e-6


def coerce_float(value) -> float:
    if value in (None, "", "-", "--"):
        return 0.0
    if isinstance(value, str):
        value = value.replace(",", "").replace("%", "").strip()
        if not value:
            return 0.0
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def coerce_int(value) -> int:
    return int(round(coerce_float(value)))


def normalize_quarter_key(value) -> str:
    text = str(value or "").strip()
    if not text:
        raise ValueError("季度不能为空")

    match = _QUARTER_KEY_RE.match(text)
    if match:
        return f"{int(match.group(1)):04d}Q{int(match.group(2))}"

    date_match = _DATE_KEY_RE.match(text)
    if not date_match:
        raise ValueError(f"无法识别季度格式: {value}")

    year = int(date_match.group(1))
    month = int(date_match.group(2))
    quarter = ((month - 1) // 3) + 1
    return f"{year:04d}Q{quarter}"


def quarter_parts(quarter_key: str) -> tuple[int, int]:
    key = normalize_quarter_key(quarter_key)
    return int(key[:4]), int(key[-1])


def quarter_sort_value(quarter_key: str) -> int:
    year, quarter = quarter_parts(quarter_key)
    return year * 10 + quarter


def quarter_end_date(quarter_key: str) -> date:
    year, quarter = quarter_parts(quarter_key)
    month = quarter * 3
    day = 31 if month in (3, 12) else 30
    return date(year, month, day)


def quarter_end_date_text(quarter_key: str) -> str:
    return quarter_end_date(quarter_key).isoformat()


def previous_natural_quarter(quarter_key: str) -> str:
    year, quarter = quarter_parts(quarter_key)
    if quarter == 1:
        return f"{year - 1:04d}Q4"
    return f"{year:04d}Q{quarter - 1}"


def get_compare_quarter_key(subject_type: str, quarter_key: str) -> str:
    key = normalize_quarter_key(quarter_key)
    return previous_natural_quarter(key)


def iter_desc_quarters(current_quarter_key: str, *, steps: int) -> list[str]:
    quarter_key = normalize_quarter_key(current_quarter_key)
    quarters: list[str] = []
    cursor = quarter_key
    for _ in range(max(1, steps)):
        quarters.append(cursor)
        cursor = previous_natural_quarter(cursor)
    return quarters


def quarter_key_for_date(today: date) -> str:
    quarter = ((today.month - 1) // 3) + 1
    return f"{today.year:04d}Q{quarter}"


def choose_display_ratio(snapshot: dict | None) -> tuple[str, float]:
    row = dict(snapshot or {})
    subject_type = str(row.get("subject_type") or "").strip().lower()
    if subject_type == SUBJECT_TYPE_FUND:
        return "净值占比", coerce_float(row.get("net_value_ratio_pct"))
    return "持股比例", coerce_float(row.get("hold_ratio_pct"))


def build_qfii_snapshots(raw_rows: list[dict], subject: dict, quarter_key: str, end_date: str) -> list[dict]:
    grouped: dict[str, dict] = {}
    for raw in raw_rows or []:
        stock_code = str(raw.get("SECURITY_CODE") or "").strip()
        if not stock_code:
            continue
        bucket = grouped.setdefault(
            stock_code,
            {
                "subject_code": subject["subject_code"],
                "subject_name": subject["subject_name"],
                "subject_type": subject["subject_type"],
                "quarter_key": quarter_key,
                "quarter_label": quarter_key,
                "compare_quarter_key": get_compare_quarter_key(subject["subject_type"], quarter_key),
                "end_date": end_date,
                "stock_code": stock_code,
                "stock_name": str(raw.get("SECURITY_NAME_ABBR") or "").strip(),
                "holders_count": 0,
                "hold_num_shares": 0.0,
                "hold_market_value_cny": 0.0,
                "net_value_ratio_pct": 0.0,
                "free_hold_ratio_pct": 0.0,
                "hold_ratio_pct": 0.0,
                "latest_source_update": "",
                "raw_source": "eastmoney_qfii",
            },
        )
        bucket["stock_name"] = bucket["stock_name"] or str(raw.get("SECURITY_NAME_ABBR") or "").strip()
        bucket["holders_count"] += 1
        bucket["hold_num_shares"] += coerce_float(raw.get("HOLD_NUM"))
        bucket["hold_market_value_cny"] += coerce_float(raw.get("HOLDER_MARKET_CAP"))
        bucket["free_hold_ratio_pct"] += coerce_float(raw.get("FREE_HOLDNUM_RATIO"))
        bucket["hold_ratio_pct"] += coerce_float(raw.get("HOLD_RATIO"))
        update_date = str(raw.get("UPDATE_DATE") or "").strip()
        if update_date and update_date > str(bucket["latest_source_update"] or ""):
            bucket["latest_source_update"] = update_date

    return sorted(
        grouped.values(),
        key=lambda item: (-coerce_float(item.get("hold_market_value_cny")), item.get("stock_code", "")),
    )


def build_ruiyuan_snapshots(raw_rows: list[dict], subject: dict, quarter_key: str, end_date: str) -> list[dict]:
    snapshots: list[dict] = []
    for raw in raw_rows or []:
        stock_code = str(raw.get("stock_code") or "").strip()
        if not stock_code:
            continue
        snapshots.append(
            {
                "subject_code": subject["subject_code"],
                "subject_name": subject["subject_name"],
                "subject_type": subject["subject_type"],
                "quarter_key": quarter_key,
                "quarter_label": quarter_key,
                "compare_quarter_key": get_compare_quarter_key(subject["subject_type"], quarter_key),
                "end_date": end_date,
                "stock_code": stock_code,
                "stock_name": str(raw.get("stock_name") or "").strip(),
                "holders_count": 1,
                "hold_num_shares": coerce_float(raw.get("hold_num_shares")),
                "hold_market_value_cny": coerce_float(raw.get("hold_market_value_cny")),
                "net_value_ratio_pct": coerce_float(raw.get("net_value_ratio_pct")),
                "free_hold_ratio_pct": 0.0,
                "hold_ratio_pct": 0.0,
                "latest_source_update": str(raw.get("latest_source_update") or end_date).strip(),
                "raw_source": "eastmoney_fund",
            }
        )

    return sorted(
        snapshots,
        key=lambda item: (-coerce_float(item.get("hold_market_value_cny")), item.get("stock_code", "")),
    )


def _classify_change(curr: dict | None, prev: dict | None) -> tuple[str, float]:
    if curr and not prev:
        return "新进", 1.0
    if prev and not curr:
        return "退出", -1.0

    curr_hold = coerce_float((curr or {}).get("hold_num_shares"))
    prev_hold = coerce_float((prev or {}).get("hold_num_shares"))
    delta = curr_hold - prev_hold
    if abs(delta) > _EPSILON:
        return ("增持", delta) if delta > 0 else ("减持", delta)

    _, curr_ratio = choose_display_ratio(curr)
    _, prev_ratio = choose_display_ratio(prev)
    ratio_delta = curr_ratio - prev_ratio
    if abs(ratio_delta) > _EPSILON:
        return ("增持", ratio_delta) if ratio_delta > 0 else ("减持", ratio_delta)

    return "持平", 0.0


def build_change_rows(subject: dict, snapshots: list[dict]) -> list[dict]:
    snapshot_map: dict[str, dict[str, dict]] = {}
    for snapshot in snapshots or []:
        quarter_key = normalize_quarter_key(snapshot.get("quarter_key"))
        stock_code = str(snapshot.get("stock_code") or "").strip()
        if not stock_code:
            continue
        snapshot_map.setdefault(quarter_key, {})[stock_code] = dict(snapshot)

    rows: list[dict] = []
    ordered_quarters = sorted(snapshot_map.keys(), key=quarter_sort_value, reverse=True)

    for quarter_key in ordered_quarters:
        compare_quarter_key = get_compare_quarter_key(subject["subject_type"], quarter_key)
        current_rows = snapshot_map.get(quarter_key, {})
        previous_rows = snapshot_map.get(compare_quarter_key, {})
        stock_codes = sorted(set(current_rows) | set(previous_rows))

        for stock_code in stock_codes:
            curr = current_rows.get(stock_code)
            prev = previous_rows.get(stock_code)
            ratio_label, curr_ratio = choose_display_ratio(curr)
            _, prev_ratio = choose_display_ratio(prev)
            change_type, change_score = _classify_change(curr, prev)
            curr_hold = coerce_float((curr or {}).get("hold_num_shares"))
            prev_hold = coerce_float((prev or {}).get("hold_num_shares"))
            curr_value = coerce_float((curr or {}).get("hold_market_value_cny"))
            prev_value = coerce_float((prev or {}).get("hold_market_value_cny"))
            curr_free_ratio = coerce_float((curr or {}).get("free_hold_ratio_pct"))
            prev_free_ratio = coerce_float((prev or {}).get("free_hold_ratio_pct"))
            curr_hold_ratio = coerce_float((curr or {}).get("hold_ratio_pct"))
            prev_hold_ratio = coerce_float((prev or {}).get("hold_ratio_pct"))

            rows.append(
                {
                    "subject_code": subject["subject_code"],
                    "subject_name": subject["subject_name"],
                    "subject_type": subject["subject_type"],
                    "quarter_key": quarter_key,
                    "compare_quarter_key": compare_quarter_key,
                    "end_date": str((curr or prev or {}).get("end_date") or quarter_end_date_text(quarter_key)),
                    "stock_code": stock_code,
                    "stock_name": str((curr or prev or {}).get("stock_name") or "").strip(),
                    "change_type": change_type,
                    "ratio_label": ratio_label,
                    "holders_count": coerce_int((curr or prev or {}).get("holders_count")),
                    "curr_hold_num_shares": curr_hold,
                    "prev_hold_num_shares": prev_hold,
                    "delta_hold_num_shares": curr_hold - prev_hold,
                    "curr_hold_market_value_cny": curr_value,
                    "prev_hold_market_value_cny": prev_value,
                    "delta_hold_market_value_cny": curr_value - prev_value,
                    "curr_ratio_pct": curr_ratio,
                    "prev_ratio_pct": prev_ratio,
                    "delta_ratio_pct": curr_ratio - prev_ratio,
                    "curr_net_value_ratio_pct": coerce_float((curr or {}).get("net_value_ratio_pct")),
                    "prev_net_value_ratio_pct": coerce_float((prev or {}).get("net_value_ratio_pct")),
                    "delta_net_value_ratio_pct": coerce_float((curr or {}).get("net_value_ratio_pct"))
                    - coerce_float((prev or {}).get("net_value_ratio_pct")),
                    "curr_free_hold_ratio_pct": curr_free_ratio,
                    "prev_free_hold_ratio_pct": prev_free_ratio,
                    "delta_free_hold_ratio_pct": curr_free_ratio - prev_free_ratio,
                    "curr_hold_ratio_pct": curr_hold_ratio,
                    "prev_hold_ratio_pct": prev_hold_ratio,
                    "delta_hold_ratio_pct": curr_hold_ratio - prev_hold_ratio,
                    "latest_source_update": str((curr or prev or {}).get("latest_source_update") or "").strip(),
                    "sort_quarter": quarter_sort_value(quarter_key),
                    "sort_value": max(curr_value, prev_value),
                    "change_score": change_score,
                }
            )

    return sorted(
        rows,
        key=lambda item: (
            -int(item.get("sort_quarter", 0) or 0),
            -coerce_float(item.get("sort_value")),
            item.get("stock_code", ""),
        ),
    )
