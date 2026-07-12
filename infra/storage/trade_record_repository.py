# -*- coding: utf-8 -*-
"""Persist local account trade records outside the source repository."""

from __future__ import annotations

import csv
import os
import shutil
from pathlib import Path
from typing import Mapping


def resolve_trade_record_dir(environ: Mapping[str, str] | None = None) -> Path:
    env = os.environ if environ is None else environ
    override = str(env.get("VCP_HUNTER_TRADE_RECORD_DIR") or "").strip()
    if override:
        return Path(override).expanduser()

    local_app_data = str(env.get("LOCALAPPDATA") or "").strip()
    if local_app_data:
        base_dir = Path(local_app_data)
    else:
        xdg_data_home = str(env.get("XDG_DATA_HOME") or "").strip()
        base_dir = Path(xdg_data_home).expanduser() if xdg_data_home else Path.home() / ".local" / "share"
    return base_dir / "ZijinYanxuan" / "Trade"


TRADE_RECORD_DIR = resolve_trade_record_dir()
LEGACY_TRADE_RECORD_DIR = Path(__file__).resolve().parents[2] / "data" / "Trade"


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


def migrate_legacy_trade_records(
    destination: Path | None = None,
    legacy_dir: Path | None = None,
) -> int:
    target_dir = destination or TRADE_RECORD_DIR
    sources = _iter_trade_csv_paths(legacy_dir or LEGACY_TRADE_RECORD_DIR)
    if not sources:
        return 0
    try:
        target_dir.mkdir(parents=True, exist_ok=True)
    except OSError:
        return 0
    copied = 0
    for source in sources:
        target = target_dir / source.name
        if target.exists():
            continue
        try:
            shutil.copy2(source, target)
        except OSError:
            continue
        copied += 1
    return copied


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
    paths = _iter_trade_csv_paths(root)
    if base_dir is None and not paths:
        migrate_legacy_trade_records(root, LEGACY_TRADE_RECORD_DIR)
        paths = _iter_trade_csv_paths(root)
    records: list[dict] = []
    seen: set[tuple] = set()
    for path in paths:
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
