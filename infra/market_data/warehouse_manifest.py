# -*- coding: utf-8 -*-
"""SQLite manifest for local market-data warehouse datasets."""

from __future__ import annotations

import sqlite3
import threading
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from core.f5_activation_gate import f5_snapshot_read_boundary

_BASE_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS market_data_manifest (
    dataset TEXT NOT NULL, trade_date TEXT NOT NULL, schema_version INTEGER NOT NULL,
    source TEXT NOT NULL, source_version TEXT NOT NULL, parquet_path TEXT NOT NULL,
    symbol_count INTEGER NOT NULL, row_count INTEGER NOT NULL, updated_at TEXT NOT NULL,
    data_status TEXT NOT NULL, error TEXT NOT NULL DEFAULT '', PRIMARY KEY (dataset, trade_date)
);
CREATE INDEX IF NOT EXISTS idx_market_data_manifest_latest
ON market_data_manifest (dataset, updated_at DESC, trade_date DESC);
CREATE TABLE IF NOT EXISTS market_data_manifest_active (
    dataset TEXT PRIMARY KEY, trade_date TEXT NOT NULL, updated_at TEXT NOT NULL
);
"""

_F5_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS f5_snapshot_manifest (
    snapshot_id TEXT PRIMARY KEY, requested_date TEXT NOT NULL, effective_trade_date TEXT NOT NULL,
    market_parquet_path TEXT NOT NULL, market_schema_version INTEGER NOT NULL,
    market_source TEXT NOT NULL, market_source_version TEXT NOT NULL,
    market_symbol_count INTEGER NOT NULL, market_row_count INTEGER NOT NULL,
    rps_path TEXT NOT NULL, rps_date TEXT NOT NULL, rps_valid_count INTEGER NOT NULL,
    sector_rps_path TEXT NOT NULL, sector_date TEXT NOT NULL, sector_count INTEGER NOT NULL,
    created_at TEXT NOT NULL, gbbq_path TEXT NOT NULL DEFAULT '',
    market_size_bytes INTEGER NOT NULL DEFAULT 0, market_sha256 TEXT NOT NULL DEFAULT '',
    rps_size_bytes INTEGER NOT NULL DEFAULT 0, rps_sha256 TEXT NOT NULL DEFAULT '',
    sector_rps_size_bytes INTEGER NOT NULL DEFAULT 0,
    sector_rps_sha256 TEXT NOT NULL DEFAULT '',
    gbbq_size_bytes INTEGER NOT NULL DEFAULT 0, gbbq_sha256 TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_f5_snapshot_created ON f5_snapshot_manifest (created_at DESC);
CREATE TABLE IF NOT EXISTS f5_snapshot_active (
    slot INTEGER PRIMARY KEY CHECK(slot = 1), snapshot_id TEXT NOT NULL, updated_at TEXT NOT NULL
);
"""

_F5_ARTIFACT_COLUMN_DEFINITIONS = {
    "gbbq_path": "TEXT NOT NULL DEFAULT ''",
    "market_size_bytes": "INTEGER NOT NULL DEFAULT 0",
    "market_sha256": "TEXT NOT NULL DEFAULT ''",
    "rps_size_bytes": "INTEGER NOT NULL DEFAULT 0",
    "rps_sha256": "TEXT NOT NULL DEFAULT ''",
    "sector_rps_size_bytes": "INTEGER NOT NULL DEFAULT 0",
    "sector_rps_sha256": "TEXT NOT NULL DEFAULT ''",
    "gbbq_size_bytes": "INTEGER NOT NULL DEFAULT 0",
    "gbbq_sha256": "TEXT NOT NULL DEFAULT ''",
}

_UPSERT_MARKET_SQL = """
INSERT INTO market_data_manifest (
    dataset, trade_date, schema_version, source, source_version, parquet_path,
    symbol_count, row_count, updated_at, data_status, error
) VALUES (
    :dataset, :trade_date, :schema_version, :source, :source_version, :parquet_path,
    :symbol_count, :row_count, :updated_at, :data_status, :error
) ON CONFLICT(dataset, trade_date) DO UPDATE SET
    schema_version=excluded.schema_version, source=excluded.source,
    source_version=excluded.source_version, parquet_path=excluded.parquet_path,
    symbol_count=excluded.symbol_count, row_count=excluded.row_count,
    updated_at=excluded.updated_at, data_status=excluded.data_status, error=excluded.error
"""

_ACTIVATE_MARKET_SQL = """
INSERT INTO market_data_manifest_active (dataset, trade_date, updated_at)
VALUES (:dataset, :trade_date, :updated_at)
ON CONFLICT(dataset) DO UPDATE SET trade_date=excluded.trade_date, updated_at=excluded.updated_at
"""

_UPSERT_F5_SQL = """
INSERT INTO f5_snapshot_manifest (
    snapshot_id, requested_date, effective_trade_date, market_parquet_path,
    market_schema_version, market_source, market_source_version, market_symbol_count,
    market_row_count, rps_path, rps_date, rps_valid_count, sector_rps_path,
    sector_date, sector_count, created_at, gbbq_path,
    market_size_bytes, market_sha256, rps_size_bytes, rps_sha256,
    sector_rps_size_bytes, sector_rps_sha256, gbbq_size_bytes, gbbq_sha256
) VALUES (
    :snapshot_id, :requested_date, :effective_trade_date, :market_parquet_path,
    :market_schema_version, :market_source, :market_source_version, :market_symbol_count,
    :market_row_count, :rps_path, :rps_date, :rps_valid_count, :sector_rps_path,
    :sector_date, :sector_count, :created_at, :gbbq_path,
    :market_size_bytes, :market_sha256, :rps_size_bytes, :rps_sha256,
    :sector_rps_size_bytes, :sector_rps_sha256, :gbbq_size_bytes, :gbbq_sha256
) ON CONFLICT(snapshot_id) DO UPDATE SET
    requested_date=excluded.requested_date, effective_trade_date=excluded.effective_trade_date,
    market_parquet_path=excluded.market_parquet_path, market_schema_version=excluded.market_schema_version,
    market_source=excluded.market_source, market_source_version=excluded.market_source_version,
    market_symbol_count=excluded.market_symbol_count, market_row_count=excluded.market_row_count,
    rps_path=excluded.rps_path, rps_date=excluded.rps_date, rps_valid_count=excluded.rps_valid_count,
    sector_rps_path=excluded.sector_rps_path, sector_date=excluded.sector_date,
    sector_count=excluded.sector_count, created_at=excluded.created_at,
    gbbq_path=excluded.gbbq_path, market_size_bytes=excluded.market_size_bytes,
    market_sha256=excluded.market_sha256, rps_size_bytes=excluded.rps_size_bytes,
    rps_sha256=excluded.rps_sha256,
    sector_rps_size_bytes=excluded.sector_rps_size_bytes,
    sector_rps_sha256=excluded.sector_rps_sha256,
    gbbq_size_bytes=excluded.gbbq_size_bytes, gbbq_sha256=excluded.gbbq_sha256
"""

_ACTIVATE_F5_SQL = """
INSERT INTO f5_snapshot_active (slot, snapshot_id, updated_at)
VALUES (1, :snapshot_id, :created_at)
ON CONFLICT(slot) DO UPDATE SET snapshot_id=excluded.snapshot_id, updated_at=excluded.updated_at
"""


def _publish_snapshot(conn, market_payload: dict[str, Any], snapshot_payload: dict[str, Any]) -> None:
    conn.execute(_UPSERT_MARKET_SQL, market_payload)
    conn.execute(_ACTIVATE_MARKET_SQL, market_payload)
    conn.execute(_UPSERT_F5_SQL, snapshot_payload)
    conn.execute(_ACTIVATE_F5_SQL, snapshot_payload)


def _restore_active_pointers(conn, market_record, snapshot) -> None:
    if market_record is None:
        conn.execute("DELETE FROM market_data_manifest_active WHERE dataset = ?", ("cn_daily_bars",))
    else:
        conn.execute(_UPSERT_MARKET_SQL, market_record.to_dict())
        conn.execute(_ACTIVATE_MARKET_SQL, market_record.to_dict())
    if snapshot is None:
        conn.execute("DELETE FROM f5_snapshot_active WHERE slot = 1")
    else:
        conn.execute(_ACTIVATE_F5_SQL, snapshot.to_dict())


def _ensure_f5_artifact_columns(conn: sqlite3.Connection) -> None:
    existing = {
        str(row[1])
        for row in conn.execute("PRAGMA table_info(f5_snapshot_manifest)").fetchall()
    }
    for column, definition in _F5_ARTIFACT_COLUMN_DEFINITIONS.items():
        if column not in existing:
            conn.execute(f"ALTER TABLE f5_snapshot_manifest ADD COLUMN {column} {definition}")


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


@dataclass(frozen=True)
class F5SnapshotManifestRecord:
    snapshot_id: str
    requested_date: str
    effective_trade_date: str
    market_parquet_path: str
    market_schema_version: int
    market_source: str
    market_source_version: str
    market_symbol_count: int
    market_row_count: int
    rps_path: str
    rps_date: str
    rps_valid_count: int
    sector_rps_path: str
    sector_date: str
    sector_count: int
    created_at: str
    gbbq_path: str = ""
    market_size_bytes: int = 0
    market_sha256: str = ""
    rps_size_bytes: int = 0
    rps_sha256: str = ""
    sector_rps_size_bytes: int = 0
    sector_rps_sha256: str = ""
    gbbq_size_bytes: int = 0
    gbbq_sha256: str = ""

    @classmethod
    def from_row(cls, row: sqlite3.Row | dict[str, Any]) -> "F5SnapshotManifestRecord":
        data = dict(row)
        return cls(**{name: data[name] for name in cls.__dataclass_fields__})

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

    @contextmanager
    def _connection(self):
        conn = self._connect()
        try:
            with conn:
                yield conn
        finally:
            conn.close()

    def _ensure_table(self) -> None:
        with self._lock, self._connection() as conn:
            conn.executescript(_BASE_SCHEMA_SQL)
            conn.executescript(_F5_SCHEMA_SQL)
            _ensure_f5_artifact_columns(conn)

    def upsert(self, record: WarehouseManifestRecord) -> None:
        payload = record.to_dict()
        with self._lock, self._connection() as conn:
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
            conn.execute(
                """
                    INSERT INTO market_data_manifest_active (dataset, trade_date, updated_at)
                    VALUES (:dataset, :trade_date, :updated_at)
                    ON CONFLICT(dataset) DO UPDATE SET
                        trade_date=excluded.trade_date,
                        updated_at=excluded.updated_at
                    """,
                payload,
            )

    def latest(self, dataset: str) -> WarehouseManifestRecord | None:
        with self._lock, self._connection() as conn:
            row = conn.execute(
                """
                    SELECT manifest.*
                    FROM market_data_manifest_active AS active
                    JOIN market_data_manifest AS manifest
                      ON manifest.dataset = active.dataset
                     AND manifest.trade_date = active.trade_date
                    WHERE active.dataset = ?
                    LIMIT 1
                    """,
                (dataset,),
            ).fetchone()
            if row is None:
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
        with self._lock, self._connection() as conn:
            row = conn.execute(
                """
                    SELECT * FROM market_data_manifest
                    WHERE dataset = ? AND trade_date = ?
                    """,
                (dataset, trade_date),
            ).fetchone()
        return WarehouseManifestRecord.from_row(row) if row is not None else None

    def publish_f5_snapshot(
        self,
        *,
        snapshot: F5SnapshotManifestRecord,
        market_record: WarehouseManifestRecord,
    ) -> None:
        """Atomically publish the market pointer and all F5-derived artifact paths."""

        with f5_snapshot_read_boundary(), self._lock, self._connection() as conn:
            _publish_snapshot(conn, market_record.to_dict(), snapshot.to_dict())

    def restore_f5_active_pointers(
        self,
        *,
        snapshot: F5SnapshotManifestRecord | None,
        market_record: WarehouseManifestRecord | None,
    ) -> None:
        """Restore only active pointers after a failed parent-memory installation."""

        with f5_snapshot_read_boundary(), self._lock, self._connection() as conn:
            _restore_active_pointers(conn, market_record, snapshot)

    def active_f5_snapshot(self) -> F5SnapshotManifestRecord | None:
        with f5_snapshot_read_boundary(), self._lock, self._connection() as conn:
            row = conn.execute(
                """
                    SELECT snapshot.*
                    FROM f5_snapshot_active AS active
                    JOIN f5_snapshot_manifest AS snapshot
                      ON snapshot.snapshot_id = active.snapshot_id
                    WHERE active.slot = 1
                    LIMIT 1
                """
            ).fetchone()
        return F5SnapshotManifestRecord.from_row(row) if row is not None else None

    def get_f5_snapshot(self, snapshot_id: str) -> F5SnapshotManifestRecord | None:
        with f5_snapshot_read_boundary(), self._lock, self._connection() as conn:
            row = conn.execute(
                "SELECT * FROM f5_snapshot_manifest WHERE snapshot_id = ? LIMIT 1",
                (str(snapshot_id or ""),),
            ).fetchone()
        return F5SnapshotManifestRecord.from_row(row) if row is not None else None

    def delete_inactive_f5_snapshot(self, snapshot_id: str) -> None:
        with f5_snapshot_read_boundary(), self._lock, self._connection() as conn:
            conn.execute(
                """
                DELETE FROM f5_snapshot_manifest
                WHERE snapshot_id = ?
                  AND snapshot_id NOT IN (SELECT snapshot_id FROM f5_snapshot_active)
                """,
                (str(snapshot_id or ""),),
            )

    def delete_inactive_market_record(self, dataset: str, trade_date: str) -> None:
        with f5_snapshot_read_boundary(), self._lock, self._connection() as conn:
            conn.execute(
                """
                DELETE FROM market_data_manifest
                WHERE dataset = ? AND trade_date = ?
                  AND NOT EXISTS (
                    SELECT 1 FROM market_data_manifest_active AS active
                    WHERE active.dataset = market_data_manifest.dataset
                      AND active.trade_date = market_data_manifest.trade_date
                  )
                """,
                (str(dataset or ""), str(trade_date or "")),
            )

    def recent_f5_snapshots(self, *, limit: int = 2) -> tuple[F5SnapshotManifestRecord, ...]:
        with f5_snapshot_read_boundary(), self._lock, self._connection() as conn:
            rows = conn.execute(
                "SELECT * FROM f5_snapshot_manifest ORDER BY created_at DESC LIMIT ?",
                (max(1, int(limit or 1)),),
            ).fetchall()
        return tuple(F5SnapshotManifestRecord.from_row(row) for row in rows)
