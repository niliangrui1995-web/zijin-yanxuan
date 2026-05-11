from __future__ import annotations

import threading
import datetime

import pandas as pd
import polars as pl

from core.rps_precomputer import RPSPrecomputer
from infra.market_data.market_data_warehouse import (
    MARKET_DATA_SCHEMA_VERSION,
    MarketDataWarehouse,
)
from infra.market_data.warehouse_manifest import WarehouseManifest, WarehouseManifestRecord
from vcp.data_provider_cache import load_cache_from_disk
from vcp.data_provider_history_mixin import TdxDataProviderHistoryMixin


def _sample_frame(values: list[float]) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "open": values,
            "high": [value + 0.5 for value in values],
            "low": [value - 0.5 for value in values],
            "close": [value + 0.2 for value in values],
            "volume": [1000 + idx for idx, _value in enumerate(values)],
            "amount": [10000.0 + idx for idx, _value in enumerate(values)],
        },
        index=pd.to_datetime(["2026-05-08", "2026-05-11"][: len(values)]),
    )


def _warehouse(tmp_path) -> MarketDataWarehouse:
    return MarketDataWarehouse(
        parquet_dir=tmp_path / "parquet",
        manifest=WarehouseManifest(tmp_path / "warehouse.db"),
    )


def test_warehouse_write_then_read_full_market_and_single_symbol(tmp_path):
    warehouse = _warehouse(tmp_path)
    cache_data = {
        "000001": _sample_frame([10.0, 10.5]),
        "600000": _sample_frame([20.0, 20.5]),
    }

    write_status = warehouse.write_market_dataset(cache_data, "20260511", source="vipdoc")
    full = warehouse.read_full_market()
    one = warehouse.read_symbol("000001")
    validation = warehouse.validate_manifest()

    assert write_status.ok is True
    assert full.status.ok is True
    assert set(full.data) == {"000001", "600000"}
    assert one.status.ok is True
    assert list(one.data["close"]) == [10.2, 10.7]
    assert isinstance(one.data.index, pd.DatetimeIndex)
    assert validation.ok is True
    assert validation.row_count == 4
    assert validation.symbol_count == 2


def test_warehouse_missing_manifest_does_not_read_parquet(tmp_path):
    warehouse = _warehouse(tmp_path)
    warehouse.parquet_dir.mkdir(parents=True)
    pl.DataFrame(
        {
            "datetime": ["2026-05-11"],
            "open": [1.0],
            "high": [1.1],
            "low": [0.9],
            "close": [1.0],
            "_code": ["000001"],
        }
    ).write_parquet(warehouse.parquet_path)

    result = warehouse.read_full_market()

    assert result.data is None
    assert result.status.ok is False
    assert result.status.data_status == "missing_manifest"
    assert result.status.fallback_reason == "missing_manifest"


def test_warehouse_missing_parquet_returns_status_for_fallback(tmp_path):
    warehouse = _warehouse(tmp_path)
    warehouse.manifest.upsert(
        WarehouseManifestRecord.build(
            dataset=warehouse.dataset,
            trade_date="20260511",
            schema_version=MARKET_DATA_SCHEMA_VERSION,
            source="vipdoc",
            source_version="unit-test",
            parquet_path=str(warehouse.parquet_path),
            symbol_count=1,
            row_count=1,
        )
    )

    result = warehouse.read_full_market()

    assert result.data is None
    assert result.status.ok is False
    assert result.status.data_status == "missing_parquet"
    assert result.status.fallback_reason == "missing_parquet"


def test_warehouse_schema_incompatible_returns_status_for_fallback(tmp_path):
    warehouse = _warehouse(tmp_path)
    warehouse.parquet_dir.mkdir(parents=True)
    pl.DataFrame({"datetime": ["2026-05-11"], "_code": ["000001"]}).write_parquet(warehouse.parquet_path)
    warehouse.manifest.upsert(
        WarehouseManifestRecord.build(
            dataset=warehouse.dataset,
            trade_date="20260511",
            schema_version=MARKET_DATA_SCHEMA_VERSION,
            source="vipdoc",
            source_version="unit-test",
            parquet_path=str(warehouse.parquet_path),
            symbol_count=1,
            row_count=1,
        )
    )

    result = warehouse.read_full_market()

    assert result.data is None
    assert result.status.ok is False
    assert result.status.data_status == "schema_incompatible"
    assert result.status.fallback_reason == "schema_incompatible"
    assert "close" in result.status.error


class _DummyProvider(TdxDataProviderHistoryMixin):
    def __init__(self, warehouse, local_df=None):
        self.cache_data = {}
        self.cache_lock = threading.Lock()
        self.market_data_warehouse = warehouse
        self.tdx_vipdoc = "D:\\HT\\vipdoc"
        self._local_df = local_df
        self.fetch_calls = 0

    def _fetch_from_local_tdx(self, code):
        self.fetch_calls += 1
        return self._local_df


def test_provider_get_data_uses_warehouse_before_vipdoc(tmp_path):
    warehouse = _warehouse(tmp_path)
    warehouse.write_market_dataset({"000001": _sample_frame([10.0, 10.5])}, "20260511")
    provider = _DummyProvider(warehouse=warehouse, local_df=None)

    result = provider.get_data("000001")

    assert list(result["close"]) == [10.2, 10.7]
    assert provider.fetch_calls == 0
    assert provider.cache_data["000001"] is result
    assert provider._last_market_data_source_status["active_layer"] == "parquet_sqlite_warehouse"


def test_provider_get_data_falls_back_to_vipdoc_when_warehouse_missing_symbol(tmp_path):
    warehouse = _warehouse(tmp_path)
    warehouse.write_market_dataset({"000001": _sample_frame([10.0, 10.5])}, "20260511")
    local = _sample_frame([30.0, 30.5])
    provider = _DummyProvider(warehouse=warehouse, local_df=local)

    result = provider.get_data("600000")

    assert result is local
    assert provider.fetch_calls == 1
    assert provider.cache_data["600000"] is local
    assert provider._last_market_data_source_status["active_layer"] == "vipdoc_fallback"


class _NoopLogger:
    def info(self, *_args, **_kwargs):
        return None

    def error(self, *_args, **_kwargs):
        return None


def test_load_cache_from_disk_prefers_warehouse_full_market(tmp_path):
    warehouse = _warehouse(tmp_path)
    warehouse.write_market_dataset({"000001": _sample_frame([10.0, 10.5])}, "20260511")
    provider = type("Provider", (), {})()
    provider.cache_data = {}
    provider.cache_lock = threading.Lock()
    provider.market_data_warehouse = warehouse
    provider.legacy_cache_file = str(tmp_path / "legacy.pkl")
    provider.legacy_fallback_cache_file = str(tmp_path / "legacy_fallback.pkl")

    trade_date = load_cache_from_disk(provider, logger=_NoopLogger())

    assert trade_date == "20260511"
    assert set(provider.cache_data) == {"000001"}
    assert provider._last_market_data_source_status["active_layer"] == "parquet_sqlite_warehouse"


def test_f5_stage1_uses_provider_cache_loader_before_vipdoc_reread(monkeypatch):
    import core.cache_policy as cache_policy

    monkeypatch.setattr(cache_policy, "cleanup_stale_caches", lambda _project_root: None)

    today = datetime.date.today().strftime("%Y%m%d")

    class _Provider:
        def __init__(self):
            self.cache_data = {}
            self.cache_lock = threading.Lock()
            self.code2name = {}
            self.load_calls = 0

        def load_cache_from_disk(self):
            self.load_calls += 1
            self.cache_data = {f"{idx:06d}": object() for idx in range(2001)}
            return today

        @staticmethod
        def _get_codes_from_vipdoc():
            return {"000001": "Ping An Bank"}

    provider = _Provider()
    messages = []
    done = []

    RPSPrecomputer.run_f5_pipeline(
        provider,
        engine=object(),
        cancelled_checker=lambda: True,
        set_status_callback=messages.append,
        done_callback=lambda count, elapsed: done.append((count, elapsed)),
    )

    assert provider.load_calls == 1
    assert provider.code2name == {"000001": "Ping An Bank"}
    assert any("local warehouse cache" in message for message in messages)
    assert done and done[0][0] == 2001
