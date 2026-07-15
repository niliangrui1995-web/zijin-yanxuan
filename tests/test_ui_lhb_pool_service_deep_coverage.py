from __future__ import annotations

import threading
from types import SimpleNamespace

from app.services import ui_lhb_pool_service as module


def _manager():
    manager = module.LhbPoolManager.__new__(module.LhbPoolManager)
    manager._cache_path = "cache.json"
    manager._legacy_pool_cache_path = "legacy.json"
    manager._old_cache_path = "old.json"
    manager._data = {}
    manager._day_meta = {}
    manager._last_auto_fetch_date = ""
    manager._state_lock = threading.RLock()
    manager._persisted_data = {}
    manager._persisted_day_meta = {}
    manager._persisted_last_auto_fetch_date = ""
    manager._clear_requested = False
    manager.stock_universe_provider = lambda: ["000001", "sh600001"]
    return manager


def test_load_empty_legacy_upgrade_and_error_paths(monkeypatch):
    manager = _manager()
    monkeypatch.setattr(module.LhbPoolRepository, "load_state", lambda *_args: ({}, "cache.json"))
    manager._load()
    assert manager._data == {}

    raw = {"daily_data": {"20260101": [{"code": "000001"}]}, "day_meta": {}, "last_auto_fetch_date": "x"}
    monkeypatch.setattr(module.LhbPoolRepository, "load_state", lambda *_args: (raw, "legacy.json"))
    monkeypatch.setattr(manager, "_upgrade_legacy_foreign_display_cache", lambda: 1)
    saved = []
    monkeypatch.setattr(manager, "save", lambda: saved.append(True))
    manager._load()
    assert manager._last_auto_fetch_date == "x"
    assert saved == [True]

    monkeypatch.setattr(
        module.LhbPoolRepository,
        "load_state",
        lambda *_args: (_ for _ in ()).throw(module.LhbRepositoryError("broken")),
    )
    manager._data = {"stale": []}
    manager._load()
    assert manager._data == {}


def test_load_current_cache_remembers_state(monkeypatch):
    manager = _manager()
    raw = {"daily_data": {"20260101": [{"code": "000001"}]}, "day_meta": {}, "last_auto_fetch_date": "x"}
    monkeypatch.setattr(module.LhbPoolRepository, "load_state", lambda *_args: (raw, manager._cache_path))
    monkeypatch.setattr(manager, "_upgrade_legacy_foreign_display_cache", lambda: 0)
    manager._load()
    assert manager._persisted_data == manager._data
    assert manager._clear_requested is False


def test_save_success_and_repository_error(monkeypatch):
    manager = _manager()
    manager._data = {"20260101": [{"code": "000001"}]}
    manager._day_meta = {"20260101": {"record_count": 1}}
    payload = {"daily_data": manager._data, "day_meta": manager._day_meta, "last_auto_fetch_date": "today"}
    monkeypatch.setattr(module.LhbPoolRepository, "save_merged", lambda *_args, **_kwargs: payload)
    manager.save()
    assert manager.last_auto_fetch_date == "today"
    assert manager._persisted_data == manager._data

    monkeypatch.setattr(
        module.LhbPoolRepository,
        "save_merged",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(module.LhbRepositoryError("disk")),
    )
    manager.save()


def test_migrate_old_cache_variants(monkeypatch):
    manager = _manager()
    removed = []
    monkeypatch.setattr(module.LhbPoolRepository, "remove_legacy_single_day", removed.append)
    monkeypatch.setattr(module.LhbPoolRepository, "read_legacy_single_day", lambda _path: None)
    manager._migrate_old_cache()
    assert removed == []

    monkeypatch.setattr(
        module.LhbPoolRepository,
        "read_legacy_single_day",
        lambda _path: {"date_str": "20260101", "rows": [{"code": "000001"}]},
    )
    added = []
    saved = []
    monkeypatch.setattr(manager, "add_day", lambda *args: added.append(args))
    monkeypatch.setattr(manager, "save", lambda: saved.append(True))
    manager._migrate_old_cache()
    assert added and saved and removed == ["old.json"]

    manager._data["20260101"] = []
    added.clear()
    manager._migrate_old_cache()
    assert added == []

    monkeypatch.setattr(
        module.LhbPoolRepository,
        "read_legacy_single_day",
        lambda _path: (_ for _ in ()).throw(module.LhbRepositoryError("bad")),
    )
    manager._migrate_old_cache()


def test_delegate_helpers_and_stock_universe_edges():
    manager = _manager()
    assert manager._build_full_foreign_display_from_tooltip("") == module.build_full_foreign_display_from_tooltip("")
    assert manager._to_int("3", 0) == 3
    assert manager._to_float("3.5", 0) == 3.5
    assert manager.sort_pool_rows_for_display([]) == []
    assert manager._record_stock_code({"code": "000001"}) == module.record_stock_code({"code": "000001"})
    assert manager._resolve_stock_universe_codes() == {"000001"}

    manager.stock_universe_provider = None
    original = module.LhbPoolManager._stock_universe_provider
    module.LhbPoolManager._stock_universe_provider = None
    try:
        assert manager._resolve_stock_universe_codes() == set()
    finally:
        module.LhbPoolManager._stock_universe_provider = original

    manager.stock_universe_provider = lambda: (_ for _ in ()).throw(OSError("bad"))
    assert manager._resolve_stock_universe_codes() == set()
    assert manager._filter_records_to_stock_universe([{"code": "000001"}]) == []


def test_day_management_validation_prune_and_clear(monkeypatch):
    manager = _manager()
    records = [{"code": "000001"}, {"code": "999999"}]
    manager.add_day("20260101", records, {"last_probe_ref_date": "old"})
    assert manager.get_cached_dates() == {"20260101"}
    assert manager.get_cached_record_count("20260101") == 1
    assert manager.get_cached_record_count("missing") == 0
    assert manager.get_day_meta("missing") == {}
    assert manager.get_missing_dates(["20260101", "20260102"]) == ["20260102"]
    assert manager.get_dates_pending_validation(["missing", "20260101"], "new") == ["20260101"]

    manager._day_meta["20260101"] = "bad"
    assert manager.get_day_meta("20260101") == {}
    assert manager.get_dates_pending_validation(["20260101"], "new") == ["20260101"]
    manager._day_meta["20260101"] = {"record_count": 99, "last_probe_ref_date": "new"}
    assert manager.get_dates_pending_validation(["20260101"], "new") == ["20260101"]

    manager.mark_day_probe("missing", 1, "new")
    manager.mark_day_probe("20260101", 5, "new", status="checked")
    assert manager._day_meta["20260101"]["source_count"] == 5
    assert manager._day_meta["20260101"]["probe_status"] == "checked"

    manager._data["20250101"] = []
    manager._day_meta["20250101"] = {}
    saves = []
    monkeypatch.setattr(manager, "save", lambda: saves.append(True))
    manager.prune(["20260101"])
    manager.prune(["20260101"])
    assert "20250101" not in manager._data and saves == [True]

    manager.last_auto_fetch_date = "today"
    assert manager.last_auto_fetch_date == "today"
    manager.clear_all()
    assert manager._data == {} and manager._clear_requested is True and len(saves) == 2


class _BadLength:
    def __len__(self):
        raise TypeError("bad")


def test_rps_eligibility_and_filter_paths():
    manager = _manager()
    provider = SimpleNamespace(cache_data={"long": range(250), "short": range(2), "none": None, "bad": _BadLength()})
    assert manager._count_rps250_eligible_symbols(provider) == 1
    codes = {"a", "b", "c"}
    assert manager._filter_codes_by_rps250(codes.copy(), provider, None) == codes
    assert (
        manager._filter_codes_by_rps250(codes.copy(), provider, SimpleNamespace(get_precomputed_rps=lambda: None))
        == codes
    )
    assert (
        manager._filter_codes_by_rps250(
            codes.copy(), provider, SimpleNamespace(get_precomputed_rps=lambda: {"rps250": {}})
        )
        == codes
    )
    result = manager._filter_codes_by_rps250(
        codes.copy(), provider, SimpleNamespace(get_precomputed_rps=lambda: {"rps250": {"a": 90, "b": 80}})
    )
    assert result == {"a"}

    large_provider = SimpleNamespace(cache_data={str(index): range(250) for index in range(2000)})
    result = manager._filter_codes_by_rps250(
        codes.copy(), large_provider, SimpleNamespace(get_precomputed_rps=lambda: {"rps250": {"b": 80}})
    )
    assert result == {"a", "c"}


class _Series:
    def __init__(self, values, use_to_list=True):
        self.values = values
        if not use_to_list:
            self.to_list = None

    def to_list(self):
        return list(self.values)

    def tolist(self):
        return list(self.values)


class _Frame:
    def __init__(self, data, *, empty_method=False):
        self.data = data
        self.columns = list(data)
        self.empty_method = empty_method

    def __len__(self):
        return len(next(iter(self.data.values())))

    def __getitem__(self, key):
        return _Series(self.data[key])

    def is_empty(self):
        return self.empty_method


def test_frame_adapter_and_attach_history_paths(monkeypatch):
    manager = _manager()
    assert manager._coerce_kline_frame(None) is None
    pandas_like = SimpleNamespace(empty=False)
    assert manager._coerce_kline_frame(pandas_like) is pandas_like
    no_columns = SimpleNamespace(columns=None)
    assert manager._coerce_kline_frame(no_columns) is no_columns

    frame = _Frame(
        {
            "date": [f"2026-01-{index:02d}" for index in range(1, 21)],
            "close": list(range(1, 21)),
            "open": list(range(2, 22)),
        }
    )
    adapted = manager._coerce_kline_frame(frame)
    assert adapted.empty is False
    assert len(adapted) == 20
    assert list(adapted.index)[-1] == "2026-01-20"
    assert adapted.get("missing", "fallback") == "fallback"
    assert adapted["close"].tail(2).astype(float).tolist() == [19.0, 20.0]

    monkeypatch.setattr(module, "calculate_buy_point_from_history", lambda **kwargs: f"buy-{kwargs['open_price']}")
    record = {}
    manager._attach_price_history(record, "000001", SimpleNamespace(get_data=lambda _code: frame))
    assert record["_history_date"] == "2026-01-20"
    assert record["\u4e70\u70b9"] == "buy-21.0"

    close_only = _Frame(
        {"\u65e5\u671f": [f"2026-02-{index:02d}" for index in range(1, 21)], "close": list(range(1, 21))}
    )
    record = {}
    manager._attach_price_history(record, "000001", SimpleNamespace(get_data=lambda _code: close_only))
    assert record["_history_date"] == "2026-02-20"

    short = _Frame({"close": [1]})
    record = {}
    manager._attach_price_history(record, "000001", SimpleNamespace(get_data=lambda _code: short))
    assert record == {}
    manager._attach_price_history(
        record, "000001", SimpleNamespace(get_data=lambda _code: (_ for _ in ()).throw(OSError("bad")))
    )


def test_build_latest_records_bad_values_duplicates_and_no_provider():
    manager = _manager()
    data = {
        "20260102": [
            {"code": "000001", "\u4e0a\u699c\u51c0\u4e70\u989d(\u4e07)": "bad", "\u673a\u6784\u51c0\u4e70(\u4e07)": 1},
            {"code": "600001", "\u4e0a\u699c\u51c0\u4e70\u989d(\u4e07)": 2, "\u673a\u6784\u51c0\u4e70(\u4e07)": 0},
        ],
        "20260101": [
            {"code": "600001", "\u4e0a\u699c\u51c0\u4e70\u989d(\u4e07)": 3, "\u673a\u6784\u51c0\u4e70(\u4e07)": 1},
            {"code": "999999", "\u4e0a\u699c\u51c0\u4e70\u989d(\u4e07)": 3, "\u673a\u6784\u51c0\u4e70(\u4e07)": 1},
        ],
    }
    result = manager._build_latest_pool_records(data, {"000001", "600001"}, {"600001": 2}, None)
    assert set(result) == {"600001"}
    assert result["600001"]["\u4e0a\u699c\u6b21\u6570"] == 2
