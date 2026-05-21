from __future__ import annotations

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


def test_manifest_missing_dataset_returns_none(tmp_path):
    manifest = WarehouseManifest(tmp_path / "warehouse.db")

    assert manifest.latest("cn_daily_bars") is None
    assert manifest.get("cn_daily_bars", "20260511") is None
