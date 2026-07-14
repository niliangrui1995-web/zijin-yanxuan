from __future__ import annotations

import builtins
import importlib
import threading
from datetime import date
from types import SimpleNamespace

import pandas as pd
import polars as pl
import pytest

from vcp import data_provider_history_mixin as history

data_store_module = importlib.import_module("infra.storage.data_store")


def _frame(rows: int = 3, *, start: str = "2026-01-01", volume_name: str = "volume") -> pd.DataFrame:
    index = pd.date_range(start, periods=rows, freq="D")
    return pd.DataFrame({"close": range(1, rows + 1), volume_name: range(rows)}, index=index)


def _polars_frame(rows: int = 3, *, start: str = "2026-01-01", volume_name: str = "volume") -> pl.DataFrame:
    return pl.DataFrame(
        {
            "datetime": pd.date_range(start, periods=rows, freq="D"),
            "close": range(1, rows + 1),
            volume_name: range(rows),
        }
    )


class _Store:
    def __init__(self, payload=None, *, load_error: Exception | None = None, save_error: Exception | None = None):
        self.payload = payload
        self.load_error = load_error
        self.save_error = save_error
        self.saved = []

    def load_json(self, _key, default=None):
        if self.load_error is not None:
            raise self.load_error
        return self.payload if self.payload is not None else default

    def save_json(self, key, payload):
        if self.save_error is not None:
            raise self.save_error
        self.saved.append((key, dict(payload)))


class _Provider(history.TdxDataProviderHistoryMixin):
    def __init__(self):
        self.cache_data = {}
        self.cache_lock = threading.Lock()
        self.tdx_vipdoc = "D:\\HT\\vipdoc"
        self._offline = True
        self.server_pool = []
        self.code2name = {}
        self.legacy_cache_file = "legacy.pkl"
        self.legacy_fallback_cache_file = "fallback.pkl"
        self.local_by_code = {}

    def _fetch_from_local_tdx(self, code):
        value = self.local_by_code.get(code)
        if isinstance(value, Exception):
            raise value
        return value

    def _downcast_memory(self):
        return None

    def _is_before_930_today(self):
        return False

    def _is_trading_day(self):
        return True


class _Status:
    def __init__(self, ok: bool, **payload):
        self.ok = ok
        self.payload = {"ok": ok, **payload}

    def to_dict(self):
        return dict(self.payload)


def test_worker_budget_and_trade_date_helpers_cover_invalid_empty_and_polars_paths():
    assert history._resolve_market_sync_workers(offline=True) == 20
    assert history._resolve_market_sync_workers(offline=False, requested_max_workers="invalid") == history.MARKET_SYNC_WORKERS
    assert history._resolve_market_sync_workers(offline=True, requested_max_workers=-1) == 20

    assert history._normalize_trade_date(None) == ""
    assert history._normalize_trade_date(object()) == ""
    assert history._normalize_trade_date(pd.NaT) == ""
    assert history._normalize_trade_date("2026-07-14") == "20260714"

    assert history._cached_frame_trade_date(None) == ""
    assert history._cached_frame_trade_date(pd.DataFrame()) == ""
    assert history._cached_frame_trade_date(pd.DataFrame({"date": ["2026-07-13", "2026-07-14"]})) == "20260714"
    assert history._cached_frame_trade_date(_frame(2, start="2026-07-12")) == "20260713"
    assert history._cached_frame_trade_date(pl.DataFrame({"close": []})) == ""
    assert history._cached_frame_trade_date(pl.DataFrame({"close": [1.0]})) == ""
    assert history._cached_frame_trade_date(pl.DataFrame({"trade_date": [date(2026, 7, 14)]})) == "20260714"

    broken = SimpleNamespace(height=1, columns=("date",), get_column=lambda _name: (_ for _ in ()).throw(KeyError("bad")))
    assert history._cached_frame_trade_date(broken) == ""


def test_requested_cache_helpers_require_every_code_and_return_oldest_date():
    pandas_frame = pd.DataFrame({"datetime": pd.to_datetime(["2026-07-14"])})
    polars_frame = pl.DataFrame({"date": [date(2026, 7, 13)]})
    cache = {"A": pandas_frame, "B": polars_frame}

    assert history._requested_cache_has_coverage(cache, ["A", "B"]) is True
    assert history._requested_cache_has_coverage(cache, ["missing"]) is False
    assert history._requested_cache_has_coverage({"A": pd.DataFrame()}, ["A"]) is False
    assert history._requested_cache_has_coverage({"A": pl.DataFrame({"date": []})}, ["A"]) is False
    assert history._requested_cached_trade_date(cache, ["A", "B"]) == "20260713"
    assert history._requested_cached_trade_date(cache, ["A", "missing"]) == ""


def test_name_wrapper_and_local_name_map_cache_paths(monkeypatch):
    provider = _Provider()
    assert provider._is_placeholder_name("000001", "000001") is True
    assert provider._normalize_code_name_targets(["000001", "bad", "000001"]) == ["000001"]
    assert provider._parse_tnf_name_file_for_codes("unused", set()) == {}

    provider._local_tdx_name_map_cache = {"000001": "平安银行"}
    cached = provider._load_local_tdx_name_map()
    cached["000001"] = "changed"
    assert provider._local_tdx_name_map_cache["000001"] == "平安银行"

    del provider._local_tdx_name_map_cache
    provider.tdx_vipdoc = ""
    assert provider._load_local_tdx_name_map() == {}
    assert provider._local_tdx_name_map_cache == {}

    provider.tdx_vipdoc = "D:\\HT\\vipdoc"
    provider._TNF_NAME_FILES = ("one.tnf", "two.tnf")
    calls = []

    def parse(path):
        calls.append(path)
        return {"000001": "平安银行"} if path.endswith("one.tnf") else {"600000": "浦发银行"}

    monkeypatch.setattr(provider, "_parse_tnf_name_file", parse)
    assert provider._load_local_tdx_name_map() == {"000001": "平安银行", "600000": "浦发银行"}
    assert len(calls) == 2


def test_targeted_local_name_lookup_filters_cache_and_stops_when_complete(monkeypatch):
    provider = _Provider()
    assert provider._load_local_tdx_name_map_for_codes([]) == {}

    provider._local_tdx_name_map_cache = {"000001": "平安银行", "600000": "600000"}
    assert provider._load_local_tdx_name_map_for_codes(["000001", "600000"]) == {"000001": "平安银行"}

    del provider._local_tdx_name_map_cache
    provider.tdx_vipdoc = ""
    assert provider._load_local_tdx_name_map_for_codes(["000001"]) == {}

    provider.tdx_vipdoc = "D:\\HT\\vipdoc"
    provider._TNF_NAME_FILES = ("one.tnf", "two.tnf")
    calls = []

    def parse(path, targets):
        calls.append((path, set(targets)))
        return {code: f"name-{code}" for code in targets}

    monkeypatch.setattr(provider, "_parse_tnf_name_file_for_codes", parse)
    result = provider._load_local_tdx_name_map_for_codes(["000001", "600000"])
    assert result == {"000001": "name-000001", "600000": "name-600000"}
    assert len(calls) == 1


def test_merge_and_targeted_name_maps_cover_persistence_and_store_failures(monkeypatch):
    provider = _Provider()
    store = _Store({"000001": "000001", "603196": "old"})
    monkeypatch.setattr(data_store_module, "DataStore", lambda: store)
    monkeypatch.setattr(
        provider,
        "_load_local_tdx_name_map",
        lambda: {"000001": "平安银行", "600000": "600000", "600519": "贵州茅台"},
    )

    merged = provider._merge_local_tdx_name_map({"": "ignored", "000001": "000001"}, persist=True)
    assert merged == {"000001": "平安银行", "600519": "贵州茅台"}
    assert store.saved[-1][0] == "vcp_code_names"

    failing_store = _Store(save_error=OSError("readonly"))
    monkeypatch.setattr(data_store_module, "DataStore", lambda: failing_store)
    assert provider._merge_local_tdx_name_map({}, persist=True)["000001"] == "平安银行"

    monkeypatch.setattr(provider, "_load_local_tdx_name_map", lambda: {})
    assert provider._merge_local_tdx_name_map({"000001": "old"}) == {"000001": "old"}

    monkeypatch.setattr(data_store_module, "DataStore", lambda: _Store(load_error=RuntimeError("db down")))
    monkeypatch.setattr(provider, "_load_local_tdx_name_map_for_codes", lambda codes: {code: f"local-{code}" for code in codes})
    assert provider._get_code_name_map_for_targets([]) == {}
    targeted = provider._get_code_name_map_for_targets(["000001", "603196"])
    assert targeted == {"000001": "local-000001", "603196": "璞源材料"}


def test_load_cached_name_map_handles_empty_and_error(monkeypatch):
    provider = _Provider()
    monkeypatch.setattr(data_store_module, "DataStore", lambda: _Store(load_error=ValueError("bad json")))
    assert provider.load_cached_code_name_map() == {}

    monkeypatch.setattr(data_store_module, "DataStore", lambda: _Store({"": "ignored"}))
    assert provider.load_cached_code_name_map() == {}


def test_get_all_codes_covers_offline_sources_and_online_filters(monkeypatch):
    provider = _Provider()
    store = _Store({"000001": "平安银行"})
    monkeypatch.setattr(data_store_module, "DataStore", lambda: store)
    monkeypatch.setattr(provider, "_merge_local_tdx_name_map", lambda cached, persist: {**cached, "600000": "浦发银行"})
    assert provider.get_all_codes() == {"000001": "平安银行", "600000": "浦发银行"}

    empty_store = _Store({})
    monkeypatch.setattr(data_store_module, "DataStore", lambda: empty_store)
    monkeypatch.setattr(provider, "_get_codes_from_vipdoc", lambda: {"000001": "from-vipdoc"})
    assert provider.get_all_codes() == {"000001": "from-vipdoc"}
    provider.tdx_vipdoc = ""
    assert provider.get_all_codes() == {}

    class Api:
        def get_security_count(self, market):
            return 0 if market == 0 else 4

        def get_security_list(self, market, offset):
            assert market == 1 and offset == 0
            return [
                {"code": "600000", "name": "浦发银行"},
                {"code": "688001", "name": "科创公司"},
                {"code": "600001", "name": "ST退市"},
                {"code": "000001", "name": "wrong market"},
            ]

    provider._offline = False
    provider.server_pool = [object()]
    provider._get_thread_api = lambda: Api()
    provider.tdx_vipdoc = "D:\\HT\\vipdoc"
    online_store = _Store({})
    monkeypatch.setattr(data_store_module, "DataStore", lambda: online_store)
    assert provider.get_all_codes() == {"600000": "浦发银行", "688001": "科创公司"}
    assert online_store.saved[-1][0] == "vcp_code_names"

    monkeypatch.setattr(data_store_module, "DataStore", lambda: _Store({}, save_error=OSError("readonly")))
    assert provider.get_all_codes() == {"600000": "浦发银行", "688001": "科创公司"}


def test_get_codes_from_vipdoc_scans_supported_files_and_manual_alias(tmp_path, monkeypatch):
    vipdoc = tmp_path / "vipdoc"
    sh_dir = vipdoc / "sh" / "lday"
    sz_dir = vipdoc / "sz" / "lday"
    sh_dir.mkdir(parents=True)
    sz_dir.mkdir(parents=True)
    for path in (
        sh_dir / "sh600000.day",
        sh_dir / "sh603196.day",
        sh_dir / "sh000001.day",
        sz_dir / "sz000001.day",
        sz_dir / "sz300001.day",
        sz_dir / "sz600000.day",
        sz_dir / "notes.txt",
    ):
        path.write_bytes(b"")

    provider = _Provider()
    provider.tdx_vipdoc = str(vipdoc)
    monkeypatch.setattr(data_store_module, "DataStore", lambda: _Store({"600000": "浦发银行", "603196": "old"}))
    monkeypatch.setattr(provider, "_merge_local_tdx_name_map", lambda cached, persist: dict(cached))

    result = provider._get_codes_from_vipdoc()

    assert result == {
        "600000": "浦发银行",
        "603196": "璞源材料",
        "000001": "000001",
        "300001": "300001",
    }


def test_ensure_code_name_map_covers_full_scan_online_failure_and_store_failure(monkeypatch):
    provider = _Provider()
    provider._offline = False
    provider.code2name = {"": "ignored", "000001": "cached-name", "600000": "600000"}
    monkeypatch.setattr(provider, "_get_codes_from_vipdoc", lambda: {"000001": "000001", "600000": "600000"})
    provider.fetch_realtime_quotes_batch = lambda _codes: (_ for _ in ()).throw(RuntimeError("network down"))
    monkeypatch.setattr(data_store_module, "DataStore", lambda: _Store({}))

    result = provider.ensure_code_name_map(refresh_missing=True)
    assert result == {"000001": "cached-name", "600000": "600000"}

    provider.fetch_realtime_quotes_batch = lambda _codes: {
        "bad": {"name": "ignored"},
        "600000": {"name": "浦发银行"},
    }
    monkeypatch.setattr(data_store_module, "DataStore", lambda: _Store({}, save_error=ValueError("bad store")))
    assert provider.ensure_code_name_map(["600000"], refresh_missing=True) == {
        "000001": "cached-name",
        "600000": "浦发银行",
    }


@pytest.mark.parametrize(
    ("local_value", "vipdoc", "expected_status"),
    [
        (_frame(10), True, "次新股/上市不足250天"),
        (None, True, "offline data missing"),
        (None, False, "offline data missing"),
        (OSError("broken file"), True, "本地读取异常: broken file"),
    ],
)
def test_worker_fetch_offline_missing_short_and_error(local_value, vipdoc, expected_status):
    provider = _Provider()
    provider.tdx_vipdoc = "D:\\HT\\vipdoc" if vipdoc else ""
    provider.local_by_code["000001"] = local_value

    assert provider._worker_fetch("000001", False, None) == ("000001", None, expected_status)


def test_worker_fetch_offline_renames_pandas_and_polars_volume_columns():
    provider = _Provider()
    provider.local_by_code["pandas"] = _frame(250, volume_name="vol")
    provider.local_by_code["polars"] = _polars_frame(250, volume_name="vol")

    _, pandas_result, status = provider._worker_fetch("pandas", False, None)
    assert status == "OK" and "volume" in pandas_result.columns and "vol" not in pandas_result.columns

    _, polars_result, status = provider._worker_fetch("polars", False, None)
    assert status == "OK" and "volume" in polars_result.columns and "vol" not in polars_result.columns


def _online_provider(monkeypatch, fetch_values):
    provider = _Provider()
    provider._offline = False
    provider._get_thread_api = lambda: object()
    values = iter(fetch_values)

    def fetch(_api, _code, *, count):
        value = next(values)
        if isinstance(value, Exception):
            raise value
        return value

    provider._fetch_standard_data = fetch
    monkeypatch.setattr(history.time, "sleep", lambda _seconds: None)
    return provider


def test_worker_fetch_online_incremental_combines_polars_and_deduplicates(monkeypatch):
    existing = _polars_frame(3, start="2026-01-01")
    new = _polars_frame(3, start="2026-01-03")
    provider = _online_provider(monkeypatch, [new])

    code, result, status = provider._worker_fetch("000001", False, existing)

    assert code == "000001" and status == "OK"
    assert isinstance(result, pd.DataFrame)
    assert list(result.index) == list(pd.date_range("2026-01-01", periods=5, freq="D"))


@pytest.mark.parametrize(
    ("full_value", "expected_status", "has_frame"),
    [
        (_frame(250, start="2020-01-01"), "OK", True),
        (_frame(10, start="2020-01-01"), "次新股/上市不足250天", False),
        (None, "全量下载超时", False),
    ],
)
def test_worker_fetch_online_gap_forces_full_refresh(monkeypatch, full_value, expected_status, has_frame):
    existing = _frame(3, start="2026-01-01")
    far_new = _frame(3, start="2026-02-01")
    provider = _online_provider(monkeypatch, [far_new, full_value])

    _, result, status = provider._worker_fetch("000001", False, existing)

    assert status == expected_status
    assert (result is not None) is has_frame


def test_worker_fetch_online_timeout_full_paths_and_exceptions(monkeypatch):
    provider = _online_provider(monkeypatch, [None])
    assert provider._worker_fetch("000001", False, _frame(3))[2] == "增量下载超时"

    for value, expected in (
        (_frame(250), "OK"),
        (_frame(10), "次新股/上市不足250天"),
        (None, "全量下载超时"),
        (ValueError("invalid bars"), "invalid bars"),
        (RuntimeError("transport"), "底层结构异常/长期停牌"),
    ):
        provider = _online_provider(monkeypatch, [value])
        assert provider._worker_fetch("000001", True, _frame(3))[2] == expected


def test_load_cache_from_disk_clears_snapshot_when_cache_is_empty(monkeypatch):
    provider = _Provider()
    monkeypatch.setattr(history, "load_cache_from_disk", lambda _provider, *, logger: "2026-07-14")
    assert provider.load_cache_from_disk() == "2026-07-14"
    assert provider._market_data_snapshot_trade_date == ""


def _fixed_calendar(monkeypatch, *, today=date(2026, 7, 14), latest=date(2026, 7, 14), previous=date(2026, 7, 13)):
    monkeypatch.setattr(history.MarketCalendar, "today", classmethod(lambda cls, market="CN": today))
    monkeypatch.setattr(
        history.MarketCalendar,
        "get_latest_trade_date",
        classmethod(lambda cls, market="CN", ref_date=None: previous if ref_date == today.replace(day=today.day - 1) else latest),
    )


def test_sync_market_data_empty_and_preopen_previous_snapshot(monkeypatch):
    provider = _Provider()
    assert provider.sync_market_data([]) is True

    provider.cache_data["000001"] = _frame(1, start="2026-07-13")
    provider._is_before_930_today = lambda: True
    provider._worker_fetch = lambda *_args: (_ for _ in ()).throw(AssertionError("previous snapshot should be reused"))
    _fixed_calendar(monkeypatch)
    assert provider.sync_market_data(["000001"]) is True


def test_sync_market_data_reports_failures_progress_and_unsaved_parquet(monkeypatch):
    provider = _Provider()
    provider.tdx_vipdoc = ""
    provider.load_cache_from_disk = lambda: None
    provider._worker_fetch = lambda code, *_args: (code, None, "missing")
    codes = [f"{index:06d}" for index in range(50)]
    progress_calls = []
    clock = iter(range(0, 10000, 100))
    monkeypatch.setattr(history.time, "time", lambda: next(clock))
    monkeypatch.setattr(history.time, "sleep", lambda _seconds: None)
    _fixed_calendar(monkeypatch)

    from vcp import polars_engine

    monkeypatch.setattr(polars_engine, "save_cache_parquet", lambda *_args: False)

    def broken_progress(completed, total, eta):
        progress_calls.append((completed, total, eta))
        raise ValueError("closed widget")

    assert provider.sync_market_data(codes, progress_callback=broken_progress, max_workers=2) is True
    assert progress_calls
    assert any("min" in eta for _completed, _total, eta in progress_calls)
    assert provider._last_market_data_parquet_saved_date == ""
    assert provider._market_data_snapshot_trade_date == ""


def test_sync_market_data_contains_parquet_exception_and_force_refresh(monkeypatch):
    provider = _Provider()
    provider.cache_data["000001"] = _frame(1, start="2026-07-13")
    seen = []
    provider._worker_fetch = lambda code, force, existing: seen.append((force, existing)) or (code, _frame(250), "OK")
    _fixed_calendar(monkeypatch)

    from vcp import polars_engine

    monkeypatch.setattr(polars_engine, "save_cache_parquet", lambda *_args: (_ for _ in ()).throw(OSError("disk full")))
    assert provider.sync_market_data(["000001"], force_refresh=True, max_workers=1) is True
    assert seen == [(True, None)]
    assert provider._market_data_snapshot_trade_date == ""


def test_get_data_normalizes_memory_and_covers_callable_warehouse_race():
    provider = _Provider()
    provider.cache_data["000001"] = _polars_frame(2)
    memory = provider.get_data("000001")
    assert isinstance(memory, pd.DataFrame)
    assert provider._last_market_data_source_status["active_layer"] == "memory_cache"

    warehouse_frame = _frame(2, start="2026-02-01")
    concurrent = _frame(1, start="2026-03-01")

    class Warehouse:
        def read_symbol(self, code):
            provider.cache_data[code] = concurrent
            return SimpleNamespace(status=_Status(True, active_layer="warehouse"), data=warehouse_frame)

    provider._get_market_data_warehouse = lambda: Warehouse()
    assert provider.get_data("600000") is concurrent
    assert provider._last_market_data_source_status["active_layer"] == "warehouse"


def test_get_data_covers_warehouse_failure_vipdoc_race_and_unavailable():
    provider = _Provider()
    provider.market_data_warehouse = SimpleNamespace(
        read_symbol=lambda _code: SimpleNamespace(status=_Status(False, data_status="missing"), data=None)
    )
    existing = _frame(1, start="2026-04-01")
    fetched = _frame(1, start="2026-04-02")

    def fetch(code):
        provider.cache_data[code] = existing
        return fetched

    provider._fetch_from_local_tdx = fetch
    assert provider.get_data("000001") is existing
    assert provider._last_market_data_source_status["active_layer"] == "memory_cache_after_vipdoc"

    provider = _Provider()
    provider.tdx_vipdoc = ""
    assert provider.get_data("000001") is None
    assert provider._last_market_data_source_status["data_status"] == "history_unavailable"


def test_get_data_batch_normalizes_memory_warehouse_and_local_fallbacks():
    provider = _Provider()
    provider.cache_data = {
        "memory": _polars_frame(2),
        "empty": pd.DataFrame(),
    }
    warehouse_existing = _frame(1, start="2026-05-01")
    provider.cache_data["race"] = None

    class Warehouse:
        def read_symbols(self, codes):
            assert codes == ["empty", "race", "invalid", "local", "missing"]
            provider.cache_data["race"] = warehouse_existing
            return SimpleNamespace(
                status=_Status(True, active_layer="warehouse"),
                data={
                    "race": _frame(2, start="2026-05-02"),
                    "invalid": pd.DataFrame(),
                },
            )

    provider._get_market_data_warehouse = lambda: Warehouse()
    provider.local_by_code["empty"] = _frame(1, start="2026-06-01")
    provider.local_by_code["invalid"] = pd.DataFrame()
    provider.local_by_code["local"] = _frame(1, start="2026-06-02")
    provider.local_by_code["missing"] = None

    result = provider.get_data_batch(["", None, "memory", "empty", "race", "invalid", "local", "missing", "memory"])

    assert set(result) == {"memory", "empty", "race", "local"}
    assert isinstance(result["memory"], pd.DataFrame)
    assert result["race"] is warehouse_existing
    assert result["empty"] is provider.cache_data["empty"]
    assert result["empty"].empty
    assert provider.get_data_batch([]) == {}


def test_get_data_batch_contains_warehouse_error_and_chart_delegates():
    provider = _Provider()
    provider._get_market_data_warehouse = lambda: SimpleNamespace(
        read_symbols=lambda _codes: (_ for _ in ()).throw(RuntimeError("warehouse down"))
    )
    provider.tdx_vipdoc = ""
    assert provider.get_data_batch(["000001"]) == {}

    calls = []
    local_history = SimpleNamespace(
        get_data_fresh_for_chart=lambda code, *, force_sync: calls.append((code, force_sync)) or "fresh"
    )
    provider._get_local_history_provider = lambda: local_history
    assert provider.get_data_fresh_for_chart("000001", force_sync=True) == "fresh"
    assert calls == [("000001", True)]


def test_sync_market_data_contains_missing_polars_import(monkeypatch):
    provider = _Provider()
    provider.cache_data["000001"] = _frame(1, start="2026-07-13")
    provider._worker_fetch = lambda code, *_args: (code, _frame(250), "OK")
    _fixed_calendar(monkeypatch)
    real_import = builtins.__import__

    def guarded_import(name, *args, **kwargs):
        if name == "vcp.polars_engine":
            raise ImportError("polars unavailable")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", guarded_import)
    assert provider.sync_market_data(["000001"], force_refresh=True, max_workers=1) is True
    assert provider._market_data_snapshot_trade_date == ""
