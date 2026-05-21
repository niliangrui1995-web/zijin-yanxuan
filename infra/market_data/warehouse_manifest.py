# -*- coding: utf-8 -*-
"""SQLite manifest for local market-data warehouse datasets."""

from __future__ import annotations

import sqlite3
import threading
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any


def _default_db_path() -> Path:
    project_root = Path(__file__).resolve().parents[2]
    return project_root / "data" / "vcp_hunter.db"


@dataclass(frozen=True)
class WarehouseManifestRecord:
    dataset: str
    trade_date: str
    schema_version: int
    source: str
    source_version: str
    parquet_path: str
    symbol_count: int
    row_count: int
    updated_at: str
    data_status: str
    error: str = ""

    @classmethod
    def build(
        cls,
        *,
        dataset: str,
        trade_date: str,
        schema_version: int,
        source: str,
        source_version: str,
        parquet_path: str,
        symbol_count: int,
        row_count: int,
        data_status: str = "ok",
        error: str = "",
        updated_at: str | None = None,
    ) -> "WarehouseManifestRecord":
        return cls(
            dataset=str(dataset or "").strip(),
            trade_date=str(trade_date or "").strip(),
            schema_version=int(schema_version),
            source=str(source or "").strip(),
            source_version=str(source_version or "").strip(),
            parquet_path=str(parquet_path or "").strip(),
            symbol_count=int(symbol_count or 0),
            row_count=int(row_count or 0),
            updated_at=updated_at or datetime.now().isoformat(timespec="seconds"),
            data_status=str(data_status or "").strip() or "ok",
            error=str(error or "").strip(),
        )

    @classmethod
    def from_row(cls, row: sqlite3.Row | dict[str, Any]) -> "WarehouseManifestRecord":
        data = dict(row)
        return cls(
            dataset=str(data.get("dataset") or ""),
            trade_date=str(data.get("trade_date") or ""),
            schema_version=int(data.get("schema_version") or 0),
            source=str(data.get("source") or ""),
            source_version=str(data.get("source_version") or ""),
            parquet_path=str(data.get("parquet_path") or ""),
            symbol_count=int(data.get("symbol_count") or 0),
            row_count=int(data.get("row_count") or 0),
            updated_at=str(data.get("updated_at") or ""),
            data_status=str(data.get("data_status") or ""),
            error=str(data.get("error") or ""),
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class WarehouseManifest:
    """Small SQLite table for dataset state, paths, and quality status."""

    def __init__(self, db_path: str | Path | None = None) -> None:
        self.db_path = Path(db_path) if db_path is not None else _default_db_path()
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._ensure_table()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path), timeout=10, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        return conn

    def _ensure_table(self) -> None:
        with self._lock:
            with self._connect() as conn:
                conn.executescript(
                    """
                    CREATE TABLE IF NOT EXISTS market_data_manifest (
                        dataset TEXT NOT NULL,
                        trade_date TEXT NOT NULL,
                        schema_version INTEGER NOT NULL,
                        source TEXT NOT NULL,
                        source_version TEXT NOT NULL,
                        parquet_path TEXT NOT NULL,
                        symbol_count INTEGER NOT NULL,
                        row_count INTEGER NOT NULL,
                        updated_at TEXT NOT NULL,
                        data_status TEXT NOT NULL,
                        error TEXT NOT NULL DEFAULT '',
                        PRIMARY KEY (dataset, trade_date)
                    );

                    CREATE INDEX IF NOT EXISTS idx_market_data_manifest_latest
                    ON market_data_manifest (dataset, updated_at DESC, trade_date DESC);
                    """
                )

    def upsert(self, record: WarehouseManifestRecord) -> None:
        payload = record.to_dict()
        with self._lock:
            with self._connect() as conn:
                conn.execute(
                    """
                    INSERT INTO market_data_manifest (
                        dataset, trade_date, schema_version, source, source_version,
                        parquet_path, symbol_count, row_count, updated_at, data_status, error
                    )
                    VALUES (
                        :dataset, :trade_date, :schema_version, :source, :source_version,
                        :parquet_path, :symbol_count, :row_count, :updated_at, :data_status, :error
                    )
                    ON CONFLICT(dataset, trade_date) DO UPDATE SET
                        schema_version=excluded.schema_version,
                        source=excluded.source,
                        source_version=excluded.source_version,
                        parquet_path=excluded.parquet_path,
                        symbol_count=excluded.symbol_count,
                        row_count=excluded.row_count,
                        updated_at=excluded.updated_at,
                        data_status=excluded.data_status,
                        error=excluded.error
                    """,
                    payload,
                )

    def latest(self, dataset: str) -> WarehouseManifestRecord | None:
        with self._lock:
            with self._connect() as conn:
                row = conn.execute(
                    """
                    SELECT * FROM market_data_manifest
                    WHERE dataset = ?
                    ORDER BY updated_at DESC, trade_date DESC
                    LIMIT 1
                    """,
                    (dataset,),
                ).fetchone()
        return WarehouseManifestRecord.from_row(row) if row is not None else None

    def get(self, dataset: str, trade_date: str) -> WarehouseManifestRecord | None:
        with self._lock:
            with self._connect() as conn:
                row = conn.execute(
                    """
                    SELECT * FROM market_data_manifest
                    WHERE dataset = ? AND trade_date = ?
                    """,
                    (dataset, trade_date),
                ).fetchone()
        return WarehouseManifestRecord.from_row(row) if row is not None else None
