# -*- coding: utf-8 -*-
"""Job-local market snapshot staging and validation for F5 workers."""

from __future__ import annotations

import threading
from contextlib import suppress
from dataclasses import replace
from pathlib import Path

from core.logger import get_logger
from core.market_snapshot_dates import infer_effective_trade_date
from infra.market_data.market_data_warehouse import (
    MARKET_DATA_SCHEMA_VERSION,
    MARKET_DATA_SOURCE_VERSION,
    MARKET_DATASET,
    REQUIRED_MARKET_COLUMNS,
    PolarsError,
    WarehouseReadResult,
    WarehouseStatus,
    _atomic_parquet_write,
    _frame_to_polars,
    _read_failure,
)

log = get_logger(__name__)


def _failed_status(path: Path, trade_date: str, data_status: str, error: str) -> WarehouseStatus:
    return WarehouseStatus(
        ok=False,
        dataset=MARKET_DATASET,
        trade_date=str(trade_date or ""),
        parquet_path=str(path),
        data_status=data_status,
        error=str(error or ""),
        active_layer="warehouse_unavailable",
        fallback_reason=data_status,
    )


def _convert_market_frames(cache_data) -> list:
    frames = []
    for code, frame in (cache_data or {}).items():
        try:
            converted = _frame_to_polars(str(code), frame)
        except (AttributeError, KeyError, RuntimeError, TypeError, ValueError, PolarsError) as exc:
            log.debug("[F5] skip market frame %s while staging: %s", code, exc)
            converted = None
        if converted is not None:
            frames.append(converted)
    return frames


def _partition_market_frame(frame) -> dict:
    cache_data = {}
    for part in frame.partition_by("_code", maintain_order=True):
        cache_data[str(part["_code"][0])] = part.drop("_code")
    return cache_data


def _validated_read_status(status, cache_data, row_count, expected_symbols, expected_rows):
    if expected_rows and int(expected_rows) != row_count:
        return _read_failure(status, "manifest_mismatch", f"row_count expected={expected_rows} parquet={row_count}")
    symbol_count = len(cache_data)
    if expected_symbols and int(expected_symbols) != symbol_count:
        error = f"symbol_count expected={expected_symbols} parquet={symbol_count}"
        return _read_failure(status, "manifest_mismatch", error)
    resolved = replace(status, ok=True, symbol_count=symbol_count, row_count=row_count, data_status="ok")
    return WarehouseReadResult(cache_data, resolved)


class F5MarketSnapshotStore:
    """Write/read one immutable market Parquet inside a job-owned generation directory."""

    def __init__(self, output_dir: str | Path) -> None:
        self.output_dir = Path(output_dir).resolve()
        self.parquet_path = self.output_dir / "market.parquet"
        self._lock = threading.RLock()

    def stage_market_dataset(self, cache_data, trade_date: str) -> WarehouseStatus:
        try:
            import polars as pl
        except ImportError as exc:
            return _failed_status(self.parquet_path, trade_date, "dependency_missing", str(exc))
        frames = _convert_market_frames(cache_data)
        if not frames:
            return _failed_status(self.parquet_path, trade_date, "empty_dataset", "no market frames to stage")
        data = pl.concat(frames, how="vertical_relaxed")
        missing = sorted(REQUIRED_MARKET_COLUMNS.difference(data.columns))
        if missing:
            return _failed_status(self.parquet_path, trade_date, "schema_incompatible", ", ".join(missing))
        try:
            with self._lock:
                _atomic_parquet_write(data, self.parquet_path, compression="zstd")
        except (OSError, RuntimeError, TypeError, ValueError, PolarsError) as exc:
            with suppress(OSError):
                self.parquet_path.unlink()
            return _failed_status(self.parquet_path, trade_date, "parquet_unreadable", str(exc))
        return WarehouseStatus(
            ok=True,
            dataset=MARKET_DATASET,
            trade_date=str(trade_date or ""),
            schema_version=MARKET_DATA_SCHEMA_VERSION,
            source="vipdoc",
            source_version=MARKET_DATA_SOURCE_VERSION,
            parquet_path=str(self.parquet_path),
            symbol_count=int(data["_code"].n_unique()),
            row_count=int(data.height),
            data_status="ok",
            active_layer="staged_generation",
        )

    def read_market_snapshot(
        self,
        *,
        trade_date: str,
        expected_symbol_count: int = 0,
        expected_row_count: int = 0,
    ) -> WarehouseReadResult:
        status = self._initial_read_status(trade_date)
        if not status.ok:
            return WarehouseReadResult(None, status)
        try:
            import polars as pl

            with self._lock:
                frame = pl.read_parquet(str(self.parquet_path))
            missing = sorted(REQUIRED_MARKET_COLUMNS.difference(frame.columns))
            if missing:
                return _read_failure(status, "schema_incompatible", f"missing columns: {', '.join(missing)}")
            cache_data = _partition_market_frame(frame)
            actual_trade_date = infer_effective_trade_date(cache_data)
            if actual_trade_date != str(trade_date or ""):
                error = f"trade_date expected={trade_date} parquet={actual_trade_date or '-'}"
                return _read_failure(status, "trade_date_mismatch", error)
            return _validated_read_status(
                status, cache_data, int(frame.height), expected_symbol_count, expected_row_count
            )
        except (ImportError, OSError, RuntimeError, TypeError, ValueError, PolarsError) as exc:
            return _read_failure(status, "parquet_unreadable", str(exc))

    def _initial_read_status(self, trade_date: str) -> WarehouseStatus:
        if not self.parquet_path.is_file():
            return _failed_status(
                self.parquet_path,
                trade_date,
                "missing_parquet",
                f"parquet file is missing: {self.parquet_path}",
            )
        return WarehouseStatus(
            ok=True,
            dataset=MARKET_DATASET,
            trade_date=str(trade_date or ""),
            schema_version=MARKET_DATA_SCHEMA_VERSION,
            parquet_path=str(self.parquet_path),
            data_status="ok",
            active_layer="staged_generation",
        )


__all__ = ["F5MarketSnapshotStore"]
