from __future__ import annotations

import datetime as dt
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

from core import cache_manager as module
from core.cache_manager import CacheManager
from core.exceptions import BusinessRuleError, CacheIOError, DataFormatError


class _BadLength:
    def __len__(self):
        raise TypeError("no length")


class _BadNotna:
    def notna(self):
        raise TypeError("bad")


def _manager(tmp_path):
    manager = CacheManager()
    manager.rps_path = str(tmp_path / "rps.json")
    return manager


def test_cache_value_count_and_eligibility_helpers():
    assert CacheManager._legacy_pickle_path("a/b/file.json").endswith("file.pkl")
    assert CacheManager._count_valid_rps_values({"a": 1, "b": "2", "c": None, "d": float("nan")}) == 2
    assert CacheManager._count_valid_rps_values(pd.Series([1, np.nan, 3])) == 2
    assert CacheManager._count_valid_rps_values(_BadNotna()) == 0
    assert CacheManager._count_valid_rps_values(object()) == 0
    assert (
        CacheManager._count_rps250_eligible_symbols(
            {"a": range(250), "b": range(249), "none": None, "bad": _BadLength()}
        )
        == 1
    )

    small = SimpleNamespace(cache_data={"a": range(250)})
    assert CacheManager._should_rebuild_rps_payload({"rps250": {}}, small) == (False, 0, 1)
    large = SimpleNamespace(cache_data={str(index): range(250) for index in range(2000)})
    assert CacheManager._should_rebuild_rps_payload({"rps250": {"a": 1}}, large) == (True, 1, 2000)
    full = {str(index): index for index in range(1000)}
    assert CacheManager._should_rebuild_rps_payload({"rps250": full}, large) == (False, 1000, 2000)


def test_extract_trade_date_covers_columns_index_and_invalid_frames():
    assert CacheManager._extract_latest_trade_date_from_frame(None) == ""
    assert CacheManager._extract_latest_trade_date_from_frame(pd.DataFrame()) == ""
    assert CacheManager._extract_latest_trade_date_from_frame(_BadLength()) == ""
    assert (
        CacheManager._extract_latest_trade_date_from_frame(pd.DataFrame({"date": [dt.date(2026, 1, 2)]})) == "20260102"
    )
    assert (
        CacheManager._extract_latest_trade_date_from_frame(pd.DataFrame({"datetime": ["2026-01-03T10:00:00"]}))
        == "20260103"
    )
    assert CacheManager._extract_latest_trade_date_from_frame(pd.DataFrame({"date": ["bad"]})) == ""
    indexed = pd.DataFrame({"close": [1]}, index=[dt.date(2026, 1, 4)])
    assert CacheManager._extract_latest_trade_date_from_frame(indexed) == "20260104"
    indexed.index = ["2026-01-05"]
    assert CacheManager._extract_latest_trade_date_from_frame(indexed) == "20260105"

    class NoIndex:
        columns = []

        def __len__(self):
            return 1

        index = None

    assert CacheManager._extract_latest_trade_date_from_frame(NoIndex()) == ""
    assert (
        CacheManager._infer_latest_rps_trade_date(
            {"a": pd.DataFrame({"date": ["2026-01-02"]}), "b": pd.DataFrame({"date": ["2026-01-05"]})}
        )
        == "20260105"
    )


@pytest.mark.parametrize(
    "cache_data, message",
    [
        ({}, "no eligible"),
        ({"a": range(60)}, "infer latest"),
    ],
)
def test_rebuild_rejects_unusable_cache(tmp_path, cache_data, message):
    manager = _manager(tmp_path)
    with pytest.raises(BusinessRuleError, match=message):
        manager._rebuild_rps_from_cache(SimpleNamespace(), SimpleNamespace(cache_data=cache_data))


@pytest.mark.parametrize(
    ("matrix", "message"),
    [({}, "empty matrix"), ({"20260102": {"rps120": {}, "rps250": None}}, "missing")],
)
def test_rebuild_rejects_bad_engine_payload(tmp_path, matrix, message):
    manager = _manager(tmp_path)
    frame = pd.DataFrame({"close": range(60)}, index=pd.date_range("2025-10-01", periods=60))
    engine = SimpleNamespace(build_rps_matrix=lambda *_args: matrix)
    with pytest.raises(BusinessRuleError, match=message):
        manager._rebuild_rps_from_cache(engine, SimpleNamespace(cache_data={"a": frame, "bad": _BadLength()}))


def test_rebuild_success_saves_sets_engine_and_status(monkeypatch, tmp_path):
    manager = _manager(tmp_path)
    frame = pd.DataFrame({"close": range(60)}, index=pd.date_range("2026-01-01", periods=60))
    calls = []
    engine = SimpleNamespace(
        build_rps_matrix=lambda data, start, end: {end: {"rps120": {"a": 90}, "rps250": {"a": 80}}},
        set_precomputed_rps=lambda *args: calls.append(args),
    )
    statuses = []
    assert (
        manager._rebuild_rps_from_cache(
            engine,
            SimpleNamespace(cache_data={"a": frame}),
            set_status_callback=statuses.append,
        )
        is True
    )
    assert calls == [("20260301", {"a": 90}, {"a": 80})]
    assert statuses and "20260301" in statuses[0]


def test_try_load_complete_payload_and_rebuild_fallback(monkeypatch, tmp_path):
    manager = _manager(tmp_path)
    Path(manager.rps_path).write_text("present", encoding="utf-8")
    payload = {"date": "20260101", "rps120": {"a": 90}, "rps250": {"a": 80}}
    monkeypatch.setattr(module, "read_active_rps_bundle", lambda _path: (manager.rps_path, payload))
    monkeypatch.setattr(manager, "_should_rebuild_rps_payload", lambda *_args, **_kwargs: (True, 1, 2000))
    monkeypatch.setattr(
        manager,
        "_rebuild_rps_from_cache",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(BusinessRuleError("cannot rebuild")),
    )
    calls = []
    engine = SimpleNamespace(set_precomputed_rps=lambda *args: calls.append(args))
    statuses = []
    manager.try_load_rps_from_disk(
        engine, data_provider=SimpleNamespace(cache_data={}), set_status_callback=statuses.append
    )
    assert calls == [("20260101", {"a": 90}, {"a": 80})]
    assert statuses and "loaded" in statuses[0]


def test_try_load_returns_after_successful_rebuild(monkeypatch, tmp_path):
    manager = _manager(tmp_path)
    Path(manager.rps_path).write_text("present", encoding="utf-8")
    monkeypatch.setattr(
        module,
        "read_active_rps_bundle",
        lambda _path: (manager.rps_path, {"date": "x", "rps120": {}, "rps250": {}}),
    )
    monkeypatch.setattr(manager, "_should_rebuild_rps_payload", lambda *_args, **_kwargs: (True, 0, 1000))
    monkeypatch.setattr(manager, "_rebuild_rps_from_cache", lambda *_args, **_kwargs: True)
    engine = SimpleNamespace(set_precomputed_rps=lambda *_args: pytest.fail("old payload must not be installed"))
    manager.try_load_rps_from_disk(engine, data_provider=SimpleNamespace(cache_data={}))


@pytest.mark.parametrize(
    "error",
    [CacheIOError("io"), DataFormatError("format"), BusinessRuleError("rule")],
)
def test_try_load_handles_typed_cache_failures(monkeypatch, tmp_path, error):
    manager = _manager(tmp_path)
    Path(manager.rps_path).write_text("present", encoding="utf-8")
    monkeypatch.setattr(module, "read_active_rps_bundle", lambda _path: (_ for _ in ()).throw(error))
    manager.try_load_rps_from_disk(SimpleNamespace())


def test_try_load_rejects_missing_bundle_values(monkeypatch, tmp_path):
    manager = _manager(tmp_path)
    Path(manager.rps_path).write_text("present", encoding="utf-8")
    monkeypatch.setattr(
        module,
        "read_active_rps_bundle",
        lambda _path: (manager.rps_path, {"date": "x", "rps120": None, "rps250": {}}),
    )
    manager.try_load_rps_from_disk(SimpleNamespace())
