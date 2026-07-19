from __future__ import annotations

import sqlite3

from infra.market_data.warehouse_manifest import (
    F5SnapshotManifestRecord,
    WarehouseManifest,
    WarehouseManifestRecord,
)


def test_manifest_upsert_and_latest_round_trip(tmp_path):
    manifest = WarehouseManifest(tmp_path / "warehouse.db")
    record = WarehouseManifestRecord.build(
        dataset="cn_daily_bars",
        trade_date="20260511",
        schema_version=3,
        source="vipdoc",
        source_version="unit-test",
        parquet_path=str(tmp_path / "market_data.parquet"),
        symbol_count=2,
        row_count=4,
        data_status="ok",
        updated_at="2026-05-11T15:30:00",
    )

    manifest.upsert(record)
    loaded = manifest.latest("cn_daily_bars")

    assert loaded == record
    assert manifest.get("cn_daily_bars", "20260511") == record


def test_manifest_latest_prefers_newer_update_for_same_dataset(tmp_path):
    manifest = WarehouseManifest(tmp_path / "warehouse.db")
    older = WarehouseManifestRecord.build(
        dataset="cn_daily_bars",
        trade_date="20260510",
        schema_version=3,
        source="vipdoc",
        source_version="unit-test",
        parquet_path=str(tmp_path / "old.parquet"),
        symbol_count=1,
        row_count=2,
        updated_at="2026-05-10T15:30:00",
    )
    newer = WarehouseManifestRecord.build(
        dataset="cn_daily_bars",
        trade_date="20260511",
        schema_version=3,
        source="vipdoc",
        source_version="unit-test",
        parquet_path=str(tmp_path / "new.parquet"),
        symbol_count=2,
        row_count=4,
        updated_at="2026-05-11T15:30:00",
    )

    manifest.upsert(older)
    manifest.upsert(newer)

    assert manifest.latest("cn_daily_bars") == newer


def test_manifest_latest_follows_last_atomic_publish_pointer(tmp_path):
    manifest = WarehouseManifest(tmp_path / "warehouse.db")
    current = WarehouseManifestRecord.build(
        dataset="cn_daily_bars",
        trade_date="20260511",
        schema_version=3,
        source="vipdoc",
        source_version="unit-test",
        parquet_path=str(tmp_path / "current.parquet"),
        symbol_count=2,
        row_count=4,
        updated_at="2026-05-11T15:30:00",
    )
    republished_backfill = WarehouseManifestRecord.build(
        dataset="cn_daily_bars",
        trade_date="20260510",
        schema_version=3,
        source="vipdoc",
        source_version="unit-test",
        parquet_path=str(tmp_path / "backfill.parquet"),
        symbol_count=2,
        row_count=4,
        updated_at="2026-05-10T15:30:00",
    )

    manifest.upsert(current)
    manifest.upsert(republished_backfill)

    assert manifest.latest("cn_daily_bars") == republished_backfill


def test_manifest_missing_dataset_returns_none(tmp_path):
    manifest = WarehouseManifest(tmp_path / "warehouse.db")

    assert manifest.latest("cn_daily_bars") is None
    assert manifest.get("cn_daily_bars", "20260511") is None


def test_manifest_reads_pre_pointer_schema_for_backward_compatibility(tmp_path):
    db_path = tmp_path / "warehouse.db"
    connection = sqlite3.connect(db_path)
    connection.execute(
        """
        CREATE TABLE market_data_manifest (
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
        )
        """
    )
    connection.execute(
        """
        INSERT INTO market_data_manifest VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "cn_daily_bars",
            "20260511",
            3,
            "vipdoc",
            "legacy",
            str(tmp_path / "market_data.parquet"),
            2,
            4,
            "2026-05-11T15:30:00",
            "ok",
            "",
        ),
    )
    connection.commit()
    connection.close()

    loaded = WarehouseManifest(db_path).latest("cn_daily_bars")

    assert loaded is not None
    assert loaded.trade_date == "20260511"
    assert loaded.source_version == "legacy"


def test_f5_manifest_migrates_integrity_columns_and_round_trips_fingerprints(tmp_path):
    db_path = tmp_path / "warehouse.db"
    connection = sqlite3.connect(db_path)
    connection.executescript(
        """
        CREATE TABLE f5_snapshot_manifest (
            snapshot_id TEXT PRIMARY KEY, requested_date TEXT NOT NULL,
            effective_trade_date TEXT NOT NULL, market_parquet_path TEXT NOT NULL,
            market_schema_version INTEGER NOT NULL, market_source TEXT NOT NULL,
            market_source_version TEXT NOT NULL, market_symbol_count INTEGER NOT NULL,
            market_row_count INTEGER NOT NULL, rps_path TEXT NOT NULL,
            rps_date TEXT NOT NULL, rps_valid_count INTEGER NOT NULL,
            sector_rps_path TEXT NOT NULL, sector_date TEXT NOT NULL,
            sector_count INTEGER NOT NULL, created_at TEXT NOT NULL
        );
        CREATE TABLE f5_snapshot_active (
            slot INTEGER PRIMARY KEY CHECK(slot = 1), snapshot_id TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        """
    )
    legacy_snapshot_id = "b" * 32
    connection.execute(
        """
        INSERT INTO f5_snapshot_manifest (
            snapshot_id, requested_date, effective_trade_date, market_parquet_path,
            market_schema_version, market_source, market_source_version,
            market_symbol_count, market_row_count, rps_path, rps_date,
            rps_valid_count, sector_rps_path, sector_date, sector_count, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            legacy_snapshot_id,
            "20260714",
            "20260714",
            str(tmp_path / "legacy-market.parquet"),
            3,
            "vipdoc",
            "legacy",
            1,
            60,
            str(tmp_path / "legacy-rps.json"),
            "20260714",
            1,
            str(tmp_path / "legacy-sector.json"),
            "20260714",
            1,
            "2026-07-14T15:30:00",
        ),
    )
    connection.execute(
        "INSERT INTO f5_snapshot_active (slot, snapshot_id, updated_at) VALUES (1, ?, ?)",
        (legacy_snapshot_id, "2026-07-14T15:30:00"),
    )
    connection.commit()
    connection.close()

    manifest = WarehouseManifest(db_path)
    with sqlite3.connect(db_path) as connection:
        columns = {
            row[1]
            for row in connection.execute("PRAGMA table_info(f5_snapshot_manifest)").fetchall()
        }
    assert {
        "gbbq_path",
        "market_size_bytes",
        "market_sha256",
        "rps_size_bytes",
        "rps_sha256",
        "sector_rps_size_bytes",
        "sector_rps_sha256",
        "gbbq_size_bytes",
        "gbbq_sha256",
    }.issubset(columns)
    legacy = manifest.active_f5_snapshot()
    assert legacy is not None
    assert legacy.snapshot_id == legacy_snapshot_id
    assert legacy.market_size_bytes == 0
    assert legacy.market_sha256 == ""

    snapshot = F5SnapshotManifestRecord(
        snapshot_id="a" * 32,
        requested_date="20260715",
        effective_trade_date="20260714",
        market_parquet_path=str(tmp_path / "market.parquet"),
        market_schema_version=3,
        market_source="vipdoc",
        market_source_version="unit-test",
        market_symbol_count=2,
        market_row_count=120,
        rps_path=str(tmp_path / "rps.json"),
        rps_date="20260714",
        rps_valid_count=2,
        sector_rps_path=str(tmp_path / "sector_rps.json"),
        sector_date="20260714",
        sector_count=1,
        created_at="2026-07-15T15:30:00",
        gbbq_path=str(tmp_path / "gbbq.json"),
        market_size_bytes=101,
        market_sha256="1" * 64,
        rps_size_bytes=102,
        rps_sha256="2" * 64,
        sector_rps_size_bytes=103,
        sector_rps_sha256="3" * 64,
        gbbq_size_bytes=104,
        gbbq_sha256="4" * 64,
    )
    market_record = WarehouseManifestRecord.build(
        dataset="cn_daily_bars",
        trade_date="20260714",
        schema_version=3,
        source="vipdoc",
        source_version="unit-test",
        parquet_path=snapshot.market_parquet_path,
        symbol_count=2,
        row_count=120,
        updated_at=snapshot.created_at,
    )

    manifest.publish_f5_snapshot(snapshot=snapshot, market_record=market_record)

    assert manifest.active_f5_snapshot() == snapshot
