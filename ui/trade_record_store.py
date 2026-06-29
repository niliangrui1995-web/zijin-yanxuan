# -*- coding: utf-8 -*-
"""Load local account trade records for K-line annotations."""

from __future__ import annotations

import csv
from pathlib import Path

TRADE_RECORD_DIR = Path(__file__).resolve().parent.parent / "data" / "Trade"


def _pick(row: dict, *keys: str, default: str = "") -> str:
    for key in keys:
        value = row.get(key)
        if value not in (None, ""):
            return str(value).strip()
    return default


def _to_float(value, default: float = 0.0) -> float:
    text = str(value or "").strip().replace(",", "")
    if not text:
        return default
    try:
        return float(text)
    except (TypeError, ValueError):
        return default


def normalize_trade_date(value) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    head = text[:10].replace("/", "-").replace(".", "-")
    compact = head.replace("-", "")
    if len(compact) >= 8 and compact[:8].isdigit():
        return f"{compact[:4]}-{compact[4:6]}-{compact[6:8]}"
    return ""


def _side_from_quantity(quantity: float) -> str:
    if quantity > 0:
        return "buy"
    if quantity < 0:
        return "sell"
    return "other"


def _normalize_trade_record(row: dict, source: str) -> dict:
    quantity = _to_float(_pick(row, "成交数量", "quantity"))
    return {
        "date": normalize_trade_date(_pick(row, "成交日期", "date")),
        "code": _pick(row, "证券代码", "代码", "code"),
        "name": _pick(row, "证券名称", "名称", "name"),
        "quantity": quantity,
        "price": _to_float(_pick(row, "成交均价", "成交价格", "price")),
        "amount": _to_float(_pick(row, "成交金额", "amount")),
        "fee": _to_float(_pick(row, "手续费", "fee")),
        "stampTax": _to_float(_pick(row, "印花税", "stamp_tax", "stampTax")),
        "otherFee": _to_float(_pick(row, "其他杂费", "other_fee", "otherFee")),
        "cashAmount": _to_float(_pick(row, "发生金额", "cash_amount", "cashAmount")),
        "side": _side_from_quantity(quantity),
        "source": source,
    }


def _iter_trade_csv_paths(base_dir: Path) -> list[Path]:
    if not base_dir.exists():
        return []
    return sorted(path for path in base_dir.glob("*.csv") if path.is_file())


def _dedupe_key(record: dict) -> tuple:
    return (
        record.get("date"),
        record.get("code"),
        record.get("name"),
        record.get("quantity"),
        record.get("price"),
        record.get("amount"),
        record.get("fee"),
        record.get("stampTax"),
        record.get("otherFee"),
        record.get("cashAmount"),
    )


def load_all_trade_records(base_dir: Path | None = None) -> list[dict]:
    root = base_dir or TRADE_RECORD_DIR
    records: list[dict] = []
    seen: set[tuple] = set()
    for path in _iter_trade_csv_paths(root):
        try:
            with path.open("r", encoding="utf-8-sig", newline="") as fh:
                reader = csv.DictReader(fh)
                for row in reader:
                    record = _normalize_trade_record(row, path.name)
                    if not (record["date"] and record["name"]):
                        continue
                    key = _dedupe_key(record)
                    if key in seen:
                        continue
                    seen.add(key)
                    records.append(record)
        except OSError:
            continue
    return records


def _record_matches_security(record: dict, code: str, name: str) -> bool:
    record_code = str(record.get("code") or "").strip()
    record_name = str(record.get("name") or "").strip()
    code_text = str(code or "").strip()
    name_text = str(name or "").strip()
    if record_code and code_text and record_code == code_text:
        return True
    return bool(record_name and name_text and record_name == name_text)


def load_trade_records_for_security(code: str, name: str, base_dir: Path | None = None) -> list[dict]:
    return [
        record
        for record in load_all_trade_records(base_dir)
        if _record_matches_security(record, code=code, name=name)
    ]
