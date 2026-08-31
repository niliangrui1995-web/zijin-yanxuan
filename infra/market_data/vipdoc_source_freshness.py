# -*- coding: utf-8 -*-
"""Low-memory freshness inspection for local Tongdaxin daily-bar sources."""

from __future__ import annotations

import hashlib
import os
import struct
from collections import Counter
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path

from core.runtime_paths import MIN_HISTORY_BARS

_TDX_DAY_RECORD_BYTES = 32
_SOURCE_DIRECTORIES = (
    ("sh", "lday", "sh", ("60", "68")),
    ("sz", "lday", "sz", ("00", "30")),
    ("bj", "lday", "bj", ("92",)),
)


@dataclass(frozen=True)
class VipdocSourceFreshness:
    source_path: str
    effective_trade_date: str
    symbol_count: int
    dated_symbol_count: int
    signature: str
    unstable: bool = False
    source_file_count: int = 0

    def to_dict(self) -> dict:
        return asdict(self)


def _parse_trade_date(raw_tail: bytes) -> str:
    if len(raw_tail) != _TDX_DAY_RECORD_BYTES:
        return ""
    try:
        text = f"{struct.unpack_from('<I', raw_tail, 0)[0]:08d}"
        datetime.strptime(text, "%Y%m%d")
    except (OverflowError, ValueError):
        return ""
    return text


def _entry_is_a_share_day(entry, prefix: str, code_prefixes: tuple[str, ...]) -> bool:
    name = entry.name.lower()
    if not name.startswith(prefix) or not name.endswith(".day"):
        return False
    code = name[len(prefix) : -4]
    return len(code) == 6 and code.isdigit() and code.startswith(code_prefixes)


def _source_entries(vipdoc_path: Path):
    for market, period, prefix, code_prefixes in _SOURCE_DIRECTORIES:
        directory = vipdoc_path / market / period
        try:
            with os.scandir(directory) as entries:
                for entry in entries:
                    if entry.is_file(follow_symlinks=False) and _entry_is_a_share_day(entry, prefix, code_prefixes):
                        yield entry
        except FileNotFoundError:
            continue


def _read_day_tail(path: str, size_bytes: int) -> bytes:
    if int(size_bytes or 0) < _TDX_DAY_RECORD_BYTES:
        return b""
    try:
        with open(path, "rb") as handle:
            handle.seek(-_TDX_DAY_RECORD_BYTES, os.SEEK_END)
            return handle.read(_TDX_DAY_RECORD_BYTES)
    except OSError:
        return b""


def inspect_vipdoc_daily_source(vipdoc_path: str | os.PathLike[str] | None) -> VipdocSourceFreshness:
    """Inspect only each ``.day`` tail; never materialize DataFrames or Parquet.

    The signature covers the accepted file name, metadata, and final raw record.
    A caller can compare two reports around a full refresh to reject a snapshot
    created while Tongdaxin was still updating its local files.
    """

    root = Path(vipdoc_path or "").resolve() if vipdoc_path else Path()
    if not vipdoc_path or not root.is_dir():
        return VipdocSourceFreshness(str(root), "", 0, 0, "")

    records: list[tuple[str, int, int, bytes, str]] = []
    unstable = False
    source_file_count = 0
    try:
        for entry in _source_entries(root):
            source_file_count += 1
            try:
                before = os.stat(entry.path, follow_symlinks=False)
            except OSError:
                unstable = True
                continue
            before_has_history = int(before.st_size // _TDX_DAY_RECORD_BYTES) >= MIN_HISTORY_BARS
            raw_tail = _read_day_tail(entry.path, before.st_size) if before_has_history else b""
            try:
                after = os.stat(entry.path, follow_symlinks=False)
            except OSError:
                unstable = True
                continue
            after_has_history = int(after.st_size // _TDX_DAY_RECORD_BYTES) >= MIN_HISTORY_BARS
            if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
                unstable = unstable or before_has_history or after_has_history
                continue
            if not before_has_history:
                continue
            records.append(
                (
                    entry.name.lower(),
                    int(before.st_size),
                    int(before.st_mtime_ns),
                    raw_tail,
                    _parse_trade_date(raw_tail),
                )
            )
    except OSError:
        unstable = True

    digest = hashlib.sha256()
    date_counts: Counter[str] = Counter()
    for name, size_bytes, mtime_ns, raw_tail, trade_date in sorted(records):
        digest.update(name.encode("ascii", errors="ignore"))
        digest.update(b"\0")
        digest.update(str(size_bytes).encode("ascii"))
        digest.update(b"\0")
        digest.update(str(mtime_ns).encode("ascii"))
        digest.update(b"\0")
        digest.update(raw_tail)
        digest.update(b"\n")
        if trade_date:
            date_counts[trade_date] += 1

    effective_trade_date = ""
    if date_counts:
        effective_trade_date = max(date_counts.items(), key=lambda item: (item[1], item[0]))[0]
    return VipdocSourceFreshness(
        source_path=str(root),
        effective_trade_date=effective_trade_date,
        symbol_count=len(records),
        dated_symbol_count=sum(date_counts.values()),
        signature=digest.hexdigest() if records else "",
        unstable=unstable,
        source_file_count=source_file_count,
    )


__all__ = ["VipdocSourceFreshness", "inspect_vipdoc_daily_source"]
