from __future__ import annotations

import builtins
import datetime as dt
import threading
from collections import defaultdict
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

import vcp.data_provider_cache as cache_module
import vcp.polars_engine as polars_engine
import vcp.sector as sector_module
from vcp.sector import SectorManager


class _Log:
    def __init__(self):
        self.info_messages = []
        self.error_messages = []

    def info(self, message):
        self.info_messages.append(str(message))

    def error(self, message):
        self.error_messages.append(str(message))


def _provider(tmp_path):
    return SimpleNamespace(
        _rt_quote_lock=threading.RLock(),
        _rt_quote_cache={},
        _rt_quote_time={},
        cache_lock=threading.RLock(),
        cache_data={},
        legacy_cache_file=str(tmp_path / "legacy.pkl"),
        legacy_fallback_cache_file=str(tmp_path / "fallback.pkl"),
        get_realtime_runtime_stats=lambda: {"state": "ready"},
    )


def _bare_manager(mapping=None):
    manager = SectorManager.__new__(SectorManager)
    manager.tdx_root = "unused"
    manager.code_to_sectors = defaultdict(list)
    manager.sector_to_codes = defaultdict(list, mapping or {})
    manager.all_sector_names = sorted(manager.sector_to_codes)
    manager._hy_count = 0
    manager._gn_count = 0
    return manager


def test_sector_parser_reads_industry_and_concept_files(tmp_path):
    cache_dir = tmp_path / "T0002" / "hq_cache"
    cache_dir.mkdir(parents=True)
    (tmp_path / "incon.dat").write_bytes(
        "#OTHER\nX|ignored\n#TDXNHY\nT01|Banking\nT02|Technology\n#NEXT\n".encode("gbk")
    )
    (cache_dir / "tdxhy.cfg").write_text(
        "# comment\n\n1|600001|T01\n0|000001|T02\ninvalid\n",
        encoding="gbk",
    )
    (cache_dir / "infoharbor_block.dat").write_bytes(
        "preamble\n#GN_AI,2,code\n1#600001,0#000001\n#GN_Empty,0,code".encode("gbk")
    )

    manager = SectorManager(str(tmp_path))

    assert manager._hy_count == 2
    assert manager._gn_count == 2
    assert manager.get_sectors("600001") == ["Banking".join(("\u884c\u4e1a_", "")), "GN_AI"]
    assert manager.get_sectors("sz000001") == ["Technology".join(("\u884c\u4e1a_", "")), "GN_AI"]
    assert manager.get_sectors("999999") == []
    assert manager.all_sector_names == sorted(manager.all_sector_names)


def test_sector_parser_uses_block_fallback_and_handles_missing_files(tmp_path):
    cache_dir = tmp_path / "T0002" / "hq_cache"
    cache_dir.mkdir(parents=True)
    (cache_dir / "block.dat").write_bytes("#GN_Backup,1\n1#600002".encode("gbk"))

    manager = SectorManager(str(tmp_path))

    assert manager._hy_count == 0
    assert manager._gn_count == 1
    assert manager.get_sectors("600002") == ["GN_Backup"]


def test_sector_parser_tolerates_read_failures(monkeypatch, tmp_path):
    cache_dir = tmp_path / "T0002" / "hq_cache"
    cache_dir.mkdir(parents=True)
    industry = cache_dir / "tdxhy.cfg"
    concept = cache_dir / "infoharbor_block.dat"
    incon = tmp_path / "incon.dat"
    for path in (industry, concept, incon):
        path.write_bytes(b"present")
    real_open = builtins.open

    def raising_open(path, *args, **kwargs):
        if str(path) in {str(industry), str(concept), str(incon)}:
            raise OSError("unreadable")
        return real_open(path, *args, **kwargs)

    monkeypatch.setattr(builtins, "open", raising_open)
    manager = SectorManager(str(tmp_path))

    assert manager.all_sector_names == []
    assert manager._hy_count == 0
    assert manager._gn_count == 0


def test_sector_singleton_uses_configured_and_explicit_roots(monkeypatch, tmp_path):
    monkeypatch.setattr(sector_module.SectorManager, "_instance", None)
    monkeypatch.setattr(sector_module.SectorManager, "_instance_root", None)
    monkeypatch.setattr(sector_module, "DEFAULT_TDX_ROOT", str(tmp_path / "default"))
    monkeypatch.setattr("core.app_config.app_config.get", lambda *_args: "")

    first = SectorManager.get_instance()
    same = SectorManager.get_instance()
    explicit = SectorManager.get_instance(str(tmp_path / "explicit"))

    assert first is same
    assert explicit is not first
    assert explicit.tdx_root.endswith("explicit")


def test_sector_location_return_alias_and_ranking_edges():
    manager = _bare_manager({"A": ["sh600001", "sh600002", "sh600003"], "B": ["sz000001"]})
    frame = pd.DataFrame(
        {"close": [0.0, 10.0, 12.0, 15.0]},
        index=pd.to_datetime(["2026-01-01", "2026-01-02", "2026-01-02", "2026-01-04"]),
    )
    target = dt.date(2026, 1, 3)
    located = manager._locate_pandas_close(frame, target, pd)
    assert located == (2, 12.0)
    assert manager._locate_pandas_close(pd.DataFrame({"x": [1]}), target, pd) is None
    assert manager._locate_pandas_close(frame, dt.date(2025, 1, 1), pd) is None
    assert manager._period_returns(frame, 2, 0, [1], is_polars=False) == {}
    assert manager._period_returns(frame, 2, 12, [1, 2, 9], is_polars=False) == {1: 0.2}

    aliases = {}
    manager._store_code_return_aliases(aliases, "600001", {5: 0.1})
    manager._store_code_return_aliases(aliases, "sz000001", {5: 0.2})
    assert set(aliases) == {"600001", "sh600001", "sz000001", "000001"}
    assert manager._normalize_rps_target_date("20260102") == dt.date(2026, 1, 2)
    assert manager._normalize_rps_target_date(pd.Timestamp("2026-01-03")) == dt.date(2026, 1, 3)
    sentinel = object()
    assert manager._normalize_rps_target_date(sentinel) is sentinel

    medians = manager._aggregate_sector_medians(
        {
            "sh600001": {5: 0.1},
            "sh600002": {5: 0.3},
            "sh600003": {5: 0.2},
            "sz000001": {5: 9.0},
        },
        [5, 10],
    )
    assert medians == {"A": {5: pytest.approx(0.2)}}
    assert manager._rank_sector_returns({"A": {5: 0.1}, "B": {5: 0.2}}, [5, 10]) == {
        "A": {5: 50.0},
        "B": {5: 100.0},
    }


def test_sector_compute_returns_skips_bad_frames_and_supports_polars():
    pl = pytest.importorskip("polars")
    manager = _bare_manager()
    dates = pd.date_range("2026-01-01", periods=8)
    pandas_frame = pd.DataFrame({"close": np.arange(1.0, 9.0)}, index=dates)
    polars_frame = pl.DataFrame({"datetime": dates, "close": np.arange(1.0, 9.0)})
    bad_frame = pd.DataFrame({"wrong": np.arange(8)}, index=dates)

    result = manager._compute_stock_returns(
        {"600001": pandas_frame, "sz000002": polars_frame, "bad": bad_frame, "short": pandas_frame[:2], "none": None},
        dt.date(2026, 1, 8),
        [2],
    )

    assert result["600001"][2] == pytest.approx(2 / 6)
    assert result["sh600001"] == result["600001"]
    assert result["sz000002"][2] == pytest.approx(2 / 6)
    assert "bad" not in result


def test_sector_polars_locator_and_check_constraints():
    pl = pytest.importorskip("polars")
    manager = _bare_manager()
    frame = pl.DataFrame({"datetime": [dt.date(2026, 1, 1), dt.date(2026, 1, 3)], "close": [10.0, 12.0]})
    assert manager._locate_polars_close(frame, dt.date(2026, 1, 2), pl) == (0, 10.0)
    assert manager._locate_polars_close(frame, dt.date(2025, 1, 1), pl) is None
    assert manager._locate_polars_close(pl.DataFrame({"close": [1]}), dt.date(2026, 1, 1), pl) is None

    manager.code_to_sectors["sh600001"] = [
        "GN_VeryLongSectorName",
        "\u884c\u4e1a_Bank",
        "GN_Cold",
        "GN_Missing",
    ]
    passed, info, best = manager.check_sector_rps(
        "600001",
        {
            "GN_VeryLongSectorName": {5: 88, 10: 91},
            "\u884c\u4e1a_Bank": {5: 80},
            "GN_Cold": {5: 10},
        },
        threshold=90,
    )
    assert passed is True
    assert best == 91
    assert len(info.split(" | ")) == 3
    assert manager.check_sector_rps("000999", {}, threshold=1) == (False, "", 0)
    manager.code_to_sectors["sz000999"] = ["GN_Absent"]
    assert manager.check_sector_rps("000999", {}, threshold=1) == (False, "", 0)


def test_runtime_cache_pruning_downcast_and_compaction(monkeypatch, tmp_path):
    provider = _provider(tmp_path)
    provider._rt_quote_cache = {"expired": 1, "old": 2, "new": 3}
    provider._rt_quote_time = {"expired": 0, "old": 90, "new": 95}
    provider._rt_quote_cache_ttl_sec = 20
    provider._rt_quote_cache_max_entries = 1

    assert cache_module.prune_rt_quote_cache(provider, now=100) == 2
    assert provider._rt_quote_cache == {"new": 3}
    stats = cache_module.compact_runtime_caches(provider, now=100)
    assert stats == {
        "removed_rt_quotes": 0,
        "rt_quote_cache_size": 1,
        "history_symbol_count": 0,
        "rt_runtime": {"state": "ready"},
    }

    provider.cache_data = {f"{index:06d}": pd.DataFrame({"x": [1.0], "n": [1]}) for index in range(52)}
    provider.cache_data["none"] = None
    sleep_calls = []
    monkeypatch.setattr(cache_module.time, "sleep", sleep_calls.append)
    log = _Log()
    cache_module.downcast_memory(provider, logger=log)
    cache_module.downcast_memory(provider, logger=log)
    assert str(provider.cache_data["000000"]["x"].dtype) == "float32"
    assert sleep_calls == [0]
    assert len(log.info_messages) == 1


def test_market_data_warehouse_helpers_cover_status_shapes_and_failures(monkeypatch, tmp_path):
    provider = _provider(tmp_path)
    warehouse = object()
    provider.market_data_warehouse = warehouse
    assert cache_module._get_market_data_warehouse(provider) is warehouse

    del provider.market_data_warehouse
    monkeypatch.setattr("infra.market_data.market_data_warehouse.get_default_market_data_warehouse", lambda: warehouse)
    assert cache_module._get_market_data_warehouse(provider) is warehouse
    assert provider.market_data_warehouse is warehouse

    cache_module._set_market_data_source_status(
        provider, SimpleNamespace(to_dict=lambda: {"ok": True}), active_layer="warehouse"
    )
    assert provider._last_market_data_source_status == {"ok": True, "active_layer": "warehouse"}
    cache_module._set_market_data_source_status(provider, {"ok": False})
    assert provider._last_market_data_source_status == {"ok": False}

    class RejectingProvider:
        def __setattr__(self, _name, _value):
            raise AttributeError("read only")

    cache_module._set_market_data_source_status(RejectingProvider(), {"ok": True})


def test_load_cache_from_warehouse_removes_legacy_files(monkeypatch, tmp_path):
    provider = _provider(tmp_path)
    for path in (
        provider.legacy_cache_file,
        provider.legacy_cache_file + ".corrupted",
        provider.legacy_fallback_cache_file,
    ):
        open(path, "w", encoding="utf-8").close()
    status = SimpleNamespace(ok=True, trade_date="20260714", data_status="ok", error="", to_dict=lambda: {"ok": True})
    provider.market_data_warehouse = SimpleNamespace(
        read_full_market=lambda: SimpleNamespace(status=status, data={"000001": "frame"})
    )
    log = _Log()

    assert cache_module.load_cache_from_disk(provider, logger=log) == "20260714"
    assert provider.cache_data == {"000001": "frame"}
    assert all(
        not __import__("os").path.exists(path)
        for path in (provider.legacy_cache_file, provider.legacy_fallback_cache_file)
    )
    assert provider._last_market_data_source_status == {"ok": True}


@pytest.mark.parametrize("manifest_ok", [True, False])
def test_load_cache_bootstraps_legacy_parquet_with_warehouse(monkeypatch, tmp_path, manifest_ok):
    provider = _provider(tmp_path)
    status = SimpleNamespace(
        ok=False, trade_date="", data_status="missing", error="none", to_dict=lambda: {"ok": False}
    )
    registered = SimpleNamespace(ok=manifest_ok, to_dict=lambda: {"ok": manifest_ok})
    provider.market_data_warehouse = SimpleNamespace(
        read_full_market=lambda: SimpleNamespace(status=status, data={}),
        register_existing_parquet=lambda **kwargs: registered,
    )
    monkeypatch.setattr(polars_engine, "load_cache_parquet", lambda: ({"600001": "frame"}, "20260713"))

    assert cache_module.load_cache_from_disk(provider, logger=_Log()) == "20260713"
    assert provider.cache_data == {"600001": "frame"}
    expected = "legacy_parquet_bootstrap" if manifest_ok else "legacy_parquet_without_manifest"
    assert provider._last_market_data_source_status["active_layer"] == expected


def test_load_cache_without_warehouse_and_error_cleanup_paths(monkeypatch, tmp_path):
    provider = _provider(tmp_path)
    provider.market_data_warehouse = None
    monkeypatch.setattr(cache_module, "_get_market_data_warehouse", lambda _provider: None)
    monkeypatch.setattr(polars_engine, "load_cache_parquet", lambda: ({"000001": "frame"}, "20260712"))
    assert cache_module.load_cache_from_disk(provider, logger=_Log()) == "20260712"
    assert provider._last_market_data_source_status["active_layer"] == "legacy_parquet_without_manifest"

    provider.cache_data = {}
    for path in (provider.legacy_cache_file, provider.legacy_fallback_cache_file):
        open(path, "w", encoding="utf-8").close()
    monkeypatch.setattr(polars_engine, "load_cache_parquet", lambda: (_ for _ in ()).throw(OSError("bad")))
    log = _Log()
    assert cache_module.load_cache_from_disk(provider, logger=log) == ""
    assert log.error_messages
    assert not __import__("os").path.exists(provider.legacy_cache_file)
