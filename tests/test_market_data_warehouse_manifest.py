from __future__ import annotations

import sqlite3

from infra.market_data.warehouse_manifest import WarehouseManifest, WarehouseManifestRecord


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
