from __future__ import annotations

import builtins
import datetime
import threading
from pathlib import Path

import pandas as pd
import polars as pl
import pytest

import core.rps_precomputer as rps_precomputer_module
import infra.market_data.market_data_warehouse as warehouse_module
from core.rps_precomputer import RPSPrecomputer
from infra.market_data.market_data_warehouse import (
    MARKET_DATA_SCHEMA_VERSION,
    MarketDataWarehouse,
    _atomic_parquet_write,
    _frame_to_polars,
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


def _manifest_record(
    warehouse: MarketDataWarehouse,
    *,
    schema_version: int = MARKET_DATA_SCHEMA_VERSION,
    parquet_path: str | None = None,
    symbol_count: int = 1,
    row_count: int = 1,
    data_status: str = "ok",
) -> WarehouseManifestRecord:
    return WarehouseManifestRecord.build(
        dataset=warehouse.dataset,
        trade_date="20260511",
        schema_version=schema_version,
        source="vipdoc",
        source_version="unit-test",
        parquet_path=parquet_path or str(warehouse.parquet_path),
        symbol_count=symbol_count,
        row_count=row_count,
        data_status=data_status,
    )


def _write_minimal_parquet(path: Path, *, include_close: bool = True, rows: int = 1) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "datetime": [f"2026-05-{11 + idx:02d}" for idx in range(rows)],
        "open": [1.0 + idx for idx in range(rows)],
        "high": [1.1 + idx for idx in range(rows)],
        "low": [0.9 + idx for idx in range(rows)],
        "_code": ["000001" for _idx in range(rows)],
    }
    if include_close:
        payload["close"] = [1.0 + idx for idx in range(rows)]
    pl.DataFrame(payload).write_parquet(path)


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


def test_warehouse_helper_branches_and_atomic_cleanup(tmp_path, monkeypatch):
    assert _frame_to_polars("000001", None) is None
    assert _frame_to_polars("000001", pd.DataFrame()) is None
    assert _frame_to_polars("000001", object()) is None

    pl_frame = pl.DataFrame({"datetime": ["2026-05-11"], "open": [1.0]})
    converted = _frame_to_polars("000001", pl_frame)

    assert converted["_code"][0] == "000001"

    class _BrokenFrame:
        def write_parquet(self, path, compression):
            Path(path).write_text("temp", encoding="utf-8")
            raise RuntimeError("write failed")

    def _raise_unlink(self):
        raise OSError("cannot unlink")

    monkeypatch.setattr(Path, "unlink", _raise_unlink)

    with pytest.raises(RuntimeError, match="write failed"):
        _atomic_parquet_write(_BrokenFrame(), tmp_path / "final.parquet")


def test_warehouse_current_status_and_validation_edge_statuses(tmp_path, monkeypatch):
    warehouse = _warehouse(tmp_path)

    warehouse.manifest.upsert(_manifest_record(warehouse, data_status="stale"))
    stale = warehouse.current_status()
    assert stale.ok is False
    assert stale.fallback_reason == "stale"

    old_schema = _manifest_record(warehouse, schema_version=MARKET_DATA_SCHEMA_VERSION - 1)
    warehouse.manifest.upsert(old_schema)
    assert warehouse.current_status().data_status == "schema_incompatible"
    assert warehouse.validate_manifest(record=old_schema).data_status == "schema_incompatible"

    missing = _manifest_record(warehouse, parquet_path=str(tmp_path / "missing.parquet"))
    assert warehouse.validate_manifest(record=missing).data_status == "missing_parquet"

    _write_minimal_parquet(warehouse.parquet_path)
    record = _manifest_record(warehouse)
    monkeypatch.setattr(warehouse, "_inspect_parquet", lambda _path: (_ for _ in ()).throw(RuntimeError("bad parquet")))
    assert warehouse.validate_manifest(record=record).data_status == "parquet_unreadable"


def test_warehouse_validation_detects_missing_columns_and_manifest_mismatch(tmp_path):
    warehouse = _warehouse(tmp_path)

    _write_minimal_parquet(warehouse.parquet_path, include_close=False)
    record = _manifest_record(warehouse)
    missing_columns = warehouse.validate_manifest(record=record)
    assert missing_columns.data_status == "schema_incompatible"
    assert "close" in missing_columns.error

    _write_minimal_parquet(warehouse.parquet_path, rows=2)
    row_mismatch = warehouse.validate_manifest(record=_manifest_record(warehouse, row_count=99, symbol_count=1))
    symbol_mismatch = warehouse.validate_manifest(record=_manifest_record(warehouse, row_count=2, symbol_count=99))

    assert row_mismatch.data_status == "manifest_mismatch"
    assert "row_count" in row_mismatch.error
    assert symbol_mismatch.data_status == "manifest_mismatch"
    assert "symbol_count" in symbol_mismatch.error


def test_warehouse_is_available_uses_parquet_validation(tmp_path):
    warehouse = _warehouse(tmp_path)
    warehouse.write_market_dataset({"000001": _sample_frame([10.0])}, "20260511")

    assert warehouse.is_available() is True


def test_warehouse_read_full_market_detects_manifest_mismatches_and_read_errors(tmp_path, monkeypatch):
    warehouse = _warehouse(tmp_path)
    warehouse.write_market_dataset({"000001": _sample_frame([10.0, 10.5])}, "20260511")

    warehouse.manifest.upsert(_manifest_record(warehouse, row_count=99, symbol_count=1))
    row_mismatch = warehouse.read_full_market()
    assert row_mismatch.data is None
    assert row_mismatch.status.data_status == "manifest_mismatch"
    assert "row_count" in row_mismatch.status.error

    warehouse.manifest.upsert(_manifest_record(warehouse, row_count=2, symbol_count=99))
    symbol_mismatch = warehouse.read_full_market()
    assert symbol_mismatch.data is None
    assert symbol_mismatch.status.data_status == "manifest_mismatch"
    assert "symbol_count" in symbol_mismatch.status.error

    warehouse.manifest.upsert(_manifest_record(warehouse, row_count=2, symbol_count=1))
    monkeypatch.setattr(pl, "read_parquet", lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("read failed")))
    unreadable = warehouse.read_full_market()
    assert unreadable.data is None
    assert unreadable.status.data_status == "parquet_unreadable"


def test_warehouse_read_symbol_edge_statuses(tmp_path, monkeypatch):
    warehouse = _warehouse(tmp_path)

    no_code = warehouse.read_symbol("")
    assert no_code.data is None
    assert no_code.status.data_status == "missing_manifest"

    _write_minimal_parquet(warehouse.parquet_path, include_close=False)
    warehouse.manifest.upsert(_manifest_record(warehouse))
    missing_columns = warehouse.read_symbol("000001")
    assert missing_columns.data is None
    assert missing_columns.status.data_status == "schema_incompatible"

    _write_minimal_parquet(warehouse.parquet_path)
    warehouse.manifest.upsert(_manifest_record(warehouse))
    monkeypatch.setattr(pl, "scan_parquet", lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("scan failed")))
    unreadable = warehouse.read_symbol("000001")
    assert unreadable.data is None
    assert unreadable.status.data_status == "parquet_unreadable"


def test_warehouse_write_market_dataset_handles_empty_conversion_and_missing_dependency(tmp_path, monkeypatch):
    warehouse = _warehouse(tmp_path)

    empty = warehouse.write_market_dataset({}, "20260511")
    assert empty.ok is False
    assert empty.data_status == "empty_dataset"

    monkeypatch.setattr(
        warehouse_module,
        "_frame_to_polars",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(ValueError("bad frame")),
    )
    bad_frame = warehouse.write_market_dataset({"000001": _sample_frame([10.0])}, "20260511")
    assert bad_frame.ok is False
    assert bad_frame.data_status == "empty_dataset"

    real_import = builtins.__import__

    def _blocked_import(name, *args, **kwargs):
        if name == "polars":
            raise ImportError("polars missing")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", _blocked_import)
    missing_dependency = warehouse.write_market_dataset({"000001": _sample_frame([10.0])}, "20260511")
    assert missing_dependency.ok is False
    assert missing_dependency.data_status == "dependency_missing"


def test_warehouse_register_existing_parquet_and_meta_date_edges(tmp_path, monkeypatch):
    warehouse = _warehouse(tmp_path)

    missing = warehouse.register_existing_parquet()
    assert missing.data_status == "missing_parquet"
    assert warehouse._read_meta_trade_date() == ""

    _write_minimal_parquet(warehouse.parquet_path)
    pl.DataFrame({"date": ["20260511"], "n_stocks": [1], "version": [MARKET_DATA_SCHEMA_VERSION]}).write_parquet(
        warehouse.meta_path
    )
    registered = warehouse.register_existing_parquet(trade_date="")
    assert registered.ok is True
    assert registered.trade_date == "20260511"

    warehouse.meta_path.write_text("not parquet", encoding="utf-8")
    assert warehouse._read_meta_trade_date() == ""

    monkeypatch.setattr(warehouse, "_inspect_parquet", lambda _path: (_ for _ in ()).throw(RuntimeError("bad parquet")))
    unreadable = warehouse.register_existing_parquet(trade_date="20260511")
    assert unreadable.data_status == "parquet_unreadable"


def test_warehouse_register_and_inspect_reject_missing_code_column(tmp_path):
    warehouse = _warehouse(tmp_path)
    warehouse.parquet_dir.mkdir(parents=True, exist_ok=True)
    pl.DataFrame({"datetime": ["2026-05-11"], "open": [1.0], "high": [1.1], "low": [0.9], "close": [1.0]}).write_parquet(
        warehouse.parquet_path
    )

    inspected = warehouse._inspect_parquet(warehouse.parquet_path)
    registered = warehouse.register_existing_parquet(trade_date="20260511")

    assert inspected["row_count"] == 0
    assert registered.data_status == "schema_incompatible"
    assert "_code" in registered.error


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


def test_f5_stage1_progress_updates_status_under_system_log_backpressure(monkeypatch):
    import sys
    from types import SimpleNamespace

    import core.cache_policy as cache_policy

    monkeypatch.setattr(cache_policy, "cleanup_stale_caches", lambda _project_root: None)
    monkeypatch.setitem(
        sys.modules,
        "vcp.polars_engine",
        SimpleNamespace(save_cache_parquet=lambda _cache_data, _today_str: True),
    )

    guard_calls = []

    class _Guard:
        def __enter__(self):
            guard_calls.append(("enter",))
            return self

        def __exit__(self, exc_type, exc, traceback):
            guard_calls.append(("exit",))
            return False

    def _fake_backpressure(label, *, allowed_info_loggers=()):
        guard_calls.append((label, allowed_info_loggers))
        return _Guard()

    monkeypatch.setattr(rps_precomputer_module, "system_log_backpressure", _fake_backpressure)

    class _Provider:
        def __init__(self):
            self.cache_data = {}
            self.cache_lock = threading.Lock()
            self.code2name = {}
            self.tdx_vipdoc = ""

        @staticmethod
        def _load_local_gbbq(force=False):
            return None

        @staticmethod
        def load_cache_from_disk():
            return ""

        @staticmethod
        def _get_codes_from_vipdoc():
            return {f"{idx:06d}": f"Stock {idx}" for idx in range(2000)}

        @staticmethod
        def is_online():
            return False

        @staticmethod
        def set_online_mode(_online):
            return None

        def sync_market_data(self, codes, force_refresh=False, progress_callback=None):
            if progress_callback:
                progress_callback(1000, len(codes), "ETA 1 min")
            self.cache_data = {code: object() for code in codes}

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

    assert ("F5", ("core.rps_precomputer",)) in guard_calls
    assert ("enter",) in guard_calls
    assert ("exit",) in guard_calls
    assert any("1000/2000 ETA 1 min" in message for message in messages)
    assert done and done[0][0] == 2000


def test_f5_skips_duplicate_stage1_checkpoint_after_provider_save(monkeypatch):
    import sys
    from types import SimpleNamespace

    import core.cache_policy as cache_policy

    monkeypatch.setattr(cache_policy, "cleanup_stale_caches", lambda _project_root: None)

    def _unexpected_checkpoint(_cache_data, _today_str):
        raise AssertionError("provider already saved the stage1 cache")

    monkeypatch.setitem(
        sys.modules,
        "vcp.polars_engine",
        SimpleNamespace(save_cache_parquet=_unexpected_checkpoint),
    )

    today = datetime.date.today().strftime("%Y%m%d")

    class _Provider:
        def __init__(self):
            self.cache_data = {}
            self.cache_lock = threading.Lock()
            self.code2name = {}
            self.tdx_vipdoc = ""
            self._last_market_data_parquet_saved_date = ""

        @staticmethod
        def _load_local_gbbq(force=False):
            return None

        @staticmethod
        def load_cache_from_disk():
            return ""

        @staticmethod
        def _get_codes_from_vipdoc():
            return {"000001": "Ping An Bank"}

        @staticmethod
        def is_online():
            return False

        @staticmethod
        def set_online_mode(_online):
            return None

        def sync_market_data(self, codes, force_refresh=False, progress_callback=None):
            self.cache_data = {code: object() for code in codes}
            self._last_market_data_parquet_saved_date = today

    done = []

    RPSPrecomputer.run_f5_pipeline(
        _Provider(),
        engine=object(),
        cancelled_checker=lambda: True,
        set_status_callback=lambda _message: None,
        done_callback=lambda count, elapsed: done.append((count, elapsed)),
    )

    assert done and done[0][0] == 1


def test_f5_ui_status_skips_separator_noise():
    messages = []

    rps_precomputer_module._emit_status(messages.append, "\n" + "=" * 60)
    rps_precomputer_module._emit_status(messages.append, "[F5] 盘后一键预计算 -- 开始")
    rps_precomputer_module._emit_status(messages.append, "=" * 60)

    assert messages == ["[F5] 盘后一键预计算 -- 开始"]


def test_f5_stage1_progress_throttles_dense_status_updates():
    messages = []
    progress_state = {}

    for done in (1, 50, 199):
        rps_precomputer_module._handle_stage1_progress(
            done,
            2000,
            "ETA 1 min",
            messages.append,
            progress_state,
        )

    assert messages == []

    rps_precomputer_module._handle_stage1_progress(
        200,
        2000,
        "ETA 1 min",
        messages.append,
        progress_state,
    )
    rps_precomputer_module._handle_stage1_progress(
        201,
        2000,
        "ETA 1 min",
        messages.append,
        progress_state,
    )
    rps_precomputer_module._handle_stage1_progress(
        1000,
        2000,
        "ETA 1 min",
        messages.append,
        progress_state,
    )

    assert [message for message in messages if "ETA 1 min" in message] == [
        "[F5] 阶段1/3: 重读本地数据 200/2000 ETA 1 min",
        "[F5] 阶段1/3: 重读本地数据 1000/2000 ETA 1 min",
    ]
