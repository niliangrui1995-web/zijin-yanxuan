from __future__ import annotations

import builtins
from datetime import date, datetime
from types import SimpleNamespace

import numpy as np
import pandas as pd
import polars as pl
import pytest

from core.rps_cache_identity import rps_cache_key
from vcp import polars_engine as engine


def _dated_polars_frame(closes: list[float], *, start: str = "2026-01-01") -> pl.DataFrame:
    return pl.DataFrame(
        {
            "datetime": pd.date_range(start, periods=len(closes), freq="D"),
            "close": closes,
        }
    )


def _block_import(monkeypatch, blocked_name: str) -> None:
    real_import = builtins.__import__

    def guarded_import(name, *args, **kwargs):
        if name == blocked_name or name.startswith(f"{blocked_name}."):
            raise ImportError(f"blocked {blocked_name}")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", guarded_import)


def test_atomic_parquet_write_replaces_destination_and_cleans_failed_temp(tmp_path, monkeypatch):
    final_path = tmp_path / "nested" / "matrix.parquet"
    engine._atomic_parquet_write(pl.DataFrame({"value": [1, 2]}), str(final_path))

    assert pl.read_parquet(final_path).to_dict(as_series=False) == {"value": [1, 2]}
    assert list(final_path.parent.glob("*.tmp")) == []

    class FailingFrame:
        def write_parquet(self, path, *, compression):
            assert compression == "zstd"
            with open(path, "wb") as handle:
                handle.write(b"partial")
            raise RuntimeError("write failed")

    with pytest.raises(RuntimeError, match="write failed"):
        engine._atomic_parquet_write(FailingFrame(), str(final_path))
    assert list(final_path.parent.glob("*.tmp")) == []

    monkeypatch.setattr(engine.os, "remove", lambda _path: (_ for _ in ()).throw(OSError("locked")))
    with pytest.raises(RuntimeError, match="write failed"):
        engine._atomic_parquet_write(FailingFrame(), str(final_path))
    assert len(list(final_path.parent.glob("*.tmp"))) == 1


def test_numpy_pct_change_and_rank_cover_scipy_and_numpy_fallback(monkeypatch):
    matrix = np.array([[1.0, np.nan, 3.0], [2.0, 4.0, 1.5], [4.0, 2.0, 6.0]])

    changed = engine._numpy_pct_change(matrix, 1)
    assert np.isnan(changed[0]).all()
    np.testing.assert_allclose(changed[2], [1.0, -0.5, 3.0])
    assert np.isnan(engine._numpy_pct_change(matrix, 5)).all()

    ranked = engine._numpy_rank_pct_axis1(matrix)
    np.testing.assert_allclose(ranked[0, [0, 2]], [0.5, 1.0])
    assert np.isnan(ranked[0, 1])

    with monkeypatch.context() as scoped:
        _block_import(scoped, "scipy")
        fallback = engine._numpy_rank_pct_axis1(np.array([[3.0, 1.0, 2.0], [np.nan, 4.0, np.nan]]))
    np.testing.assert_allclose(fallback[0], [1.0, 1 / 3, 2 / 3])
    np.testing.assert_allclose(fallback[1], [np.nan, 1.0, np.nan], equal_nan=True)


def test_to_pldf_handles_none_pandas_index_columns_and_unknown_objects():
    assert engine._to_pldf(None) is None
    assert engine._to_pldf(object()) is None

    indexed = pd.DataFrame(
        {"close": [10.0, 11.0]},
        index=pd.DatetimeIndex(["2026-01-01", "2026-01-02"], name="datetime"),
    )
    converted = engine._to_pldf(indexed)
    assert converted.columns == ["datetime", "close"]

    plain = pd.DataFrame({"datetime": pd.to_datetime(["2026-01-01"]), "close": [9.0]})
    assert engine._to_pldf(plain).to_dict(as_series=False)["close"] == [9.0]


def test_rps_cache_key_changes_when_the_current_snapshot_changes():
    frame = pd.DataFrame(
        {"close": [10.0, 11.0]},
        index=pd.DatetimeIndex(["2026-01-01", "2026-01-02"], name="datetime"),
    )
    data = {"000001": frame}

    before = rps_cache_key(data, "20260101", "20260102")
    frame.loc[frame.index[-1], "close"] = 12.0

    assert rps_cache_key(data, "20260101", "20260102") != before


def test_rps_cache_key_uses_pandas_index_trade_date_when_no_date_column_exists():
    frame = pd.DataFrame(
        {"close": [10.0, 11.0]},
        index=pd.DatetimeIndex(["2026-01-01", "2026-01-02"], name="datetime"),
    )
    data = {"000001": frame}

    before = rps_cache_key(data, "20260101", "20260102")
    frame.index = pd.DatetimeIndex(["2026-01-01", "2026-01-03"], name="datetime")

    assert rps_cache_key(data, "20260101", "20260102") != before


def test_build_prices_matrix_fast_filters_invalid_frames_and_forward_fills():
    indexed = pd.DataFrame(
        {"close": [10.0, 12.0]},
        index=pd.DatetimeIndex(["2026-01-01", "2026-01-03"], name="datetime"),
    )
    data = {
        "000001": indexed,
        "000002": _dated_polars_frame([20.0, 21.0, 22.0]),
        "none": None,
        "empty": pl.DataFrame({"datetime": [], "close": []}),
        "missing": pl.DataFrame({"datetime": [date(2026, 1, 1)]}),
        "invalid": pl.DataFrame({"datetime": ["bad-date"], "close": [1.0]}),
        "old": _dated_polars_frame([5.0], start="2025-01-01"),
    }

    matrix, columns, dates = engine.build_prices_matrix_fast(data, "2026-01-01", "2026-01-03")

    assert columns == ["000001", "000002"]
    assert list(dates) == [date(2026, 1, 1), date(2026, 1, 2), date(2026, 1, 3)]
    by_code = {code: matrix[:, index] for index, code in enumerate(columns)}
    np.testing.assert_allclose(by_code["000001"], [10.0, 10.0, 12.0])
    np.testing.assert_allclose(by_code["000002"], [20.0, 21.0, 22.0])

    empty_matrix, empty_columns, empty_dates = engine.build_prices_matrix_fast(
        {"old": _dated_polars_frame([1.0], start="2020-01-01")},
        date(2026, 1, 1),
        datetime(2026, 1, 3),
    )
    assert empty_matrix.shape == (0, 0)
    assert empty_columns == []
    assert empty_dates.size == 0


def test_prices_matrix_cache_round_trip_missing_empty_and_corrupt(tmp_path, monkeypatch):
    cache_path = tmp_path / "prices.parquet"
    monkeypatch.setattr(engine, "_PRICES_MATRIX_CACHE", str(cache_path))

    assert engine._load_prices_matrix() is None

    engine._save_prices_matrix(
        np.array([[1.0, 2.0], [3.0, 4.0]]),
        ["000001", "600000"],
        np.array([np.datetime64("2026-01-01"), np.datetime64("2026-01-02")]),
    )
    matrix, columns, dates = engine._load_prices_matrix()
    np.testing.assert_allclose(matrix, [[1.0, 2.0], [3.0, 4.0]])
    assert columns == ["000001", "600000"]
    assert list(dates) == [date(2026, 1, 1), date(2026, 1, 2)]

    pl.DataFrame({"date": []}).write_parquet(cache_path)
    assert engine._load_prices_matrix() is None

    cache_path.write_bytes(b"not parquet")
    assert engine._load_prices_matrix() is None

    monkeypatch.setattr(
        engine,
        "_atomic_parquet_write",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(ValueError("serialization failed")),
    )
    engine._save_prices_matrix(np.array([[1.0]]), ["000001"], np.array([date(2026, 1, 1)]))


def test_build_rps_matrix_returns_cached_and_empty_results(monkeypatch):
    cached_result = {"20260105": {"rps50": {"000001": 100.0}}}
    cache = {rps_cache_key({}, "20260101", "20260105"): cached_result}
    assert engine.build_rps_matrix_pl({}, "20260101", "20260105", cache) is cached_result

    monkeypatch.setattr(engine, "_load_prices_matrix", lambda *_args: None)
    monkeypatch.setattr(
        engine,
        "build_prices_matrix_fast",
        lambda *_args, **_kwargs: (np.empty((0, 0)), [], np.array([])),
    )
    assert engine.build_rps_matrix_pl({}, "20260101", "20260105") == {}


def test_build_rps_matrix_computes_weekend_fallback_and_updates_cache(monkeypatch):
    dates = np.arange(np.datetime64("2025-04-17"), np.datetime64("2026-01-03"))
    base = np.arange(1, len(dates) + 1, dtype=float)
    matrix = np.column_stack([base, base**1.02, base**1.05])
    saved = []
    monkeypatch.setattr(engine, "_load_prices_matrix", lambda *_args: None)
    monkeypatch.setattr(engine, "build_prices_matrix_fast", lambda *_args, **_kwargs: (matrix, ["A", "B", "C"], dates))
    monkeypatch.setattr(engine, "_save_prices_matrix", lambda *args: saved.append(args))
    cache = {}

    result = engine.build_rps_matrix_pl({}, "20260104", "20260104", cache)

    assert list(result) == ["20260102"]
    assert set(result["20260102"]) == {"rps50", "rps120", "rps250"}
    assert result["20260102"]["rps250"]["C"] == 100.0
    assert cache[rps_cache_key({}, "20260104", "20260104")] == result
    assert len(saved) == 1
    np.testing.assert_allclose(saved[0][0], matrix)


def test_build_rps_matrix_reuses_covering_numpy_cache(monkeypatch):
    end = np.datetime64("2026-01-05")
    start = end - np.timedelta64(engine.RPS_BUFFER_DAYS + 10, "D")
    dates = np.arange(start, end + np.timedelta64(1, "D"))
    matrix = np.column_stack(
        [
            np.linspace(1.0, 10.0, len(dates)),
            np.linspace(1.0, 20.0, len(dates)),
            np.linspace(1.0, 30.0, len(dates)),
        ]
    )
    monkeypatch.setattr(engine, "_load_prices_matrix", lambda *_args: (matrix, ["A", "B", "C"], dates))
    monkeypatch.setattr(
        engine,
        "build_prices_matrix_fast",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("covering cache must be reused")),
    )
    monkeypatch.setattr(engine, "_save_prices_matrix", lambda *_args: None)

    result = engine.build_rps_matrix_pl({}, "20260101", "20260105")

    assert set(result) == {"20260101", "20260102", "20260103", "20260104", "20260105"}
    assert all(set(day) == {"rps50", "rps120", "rps250"} for day in result.values())


def test_save_cache_parquet_covers_import_empty_conversion_and_publish_failures(tmp_path, monkeypatch):
    monkeypatch.setattr(engine, "_PARQUET_CACHE_DIR", str(tmp_path))

    with monkeypatch.context() as scoped:
        _block_import(scoped, "polars")
        assert engine.save_cache_parquet({}, "20260101") is False

    assert engine.save_cache_parquet(
        {
            "none": None,
            "empty_pd": pd.DataFrame(),
            "empty_pl": pl.DataFrame({"close": []}),
            "invalid": object(),
        },
        "20260101",
    ) is False

    warehouse_module = __import__(
        "infra.market_data.market_data_warehouse",
        fromlist=["get_default_market_data_warehouse"],
    )
    bad_status = SimpleNamespace(ok=False, data_status="invalid", error="rejected", parquet_path="")
    monkeypatch.setattr(
        warehouse_module,
        "get_default_market_data_warehouse",
        lambda: SimpleNamespace(write_polars_dataset=lambda *_args, **_kwargs: bad_status),
    )
    assert engine.save_cache_parquet({"000001": _dated_polars_frame([1.0])}, "20260101") is False

    monkeypatch.setattr(
        warehouse_module,
        "get_default_market_data_warehouse",
        lambda: (_ for _ in ()).throw(RuntimeError("warehouse unavailable")),
    )
    assert engine.save_cache_parquet({"000001": _dated_polars_frame([1.0])}, "20260101") is False


def test_load_cache_parquet_handles_import_version_grouping_and_read_failure(tmp_path, monkeypatch):
    monkeypatch.setattr(engine, "_PARQUET_CACHE_DIR", str(tmp_path))
    parquet_path = tmp_path / "market_data.parquet"
    meta_path = tmp_path / "meta.parquet"

    assert engine.load_cache_parquet() is None
    pl.DataFrame({"_code": ["000001"], "close": [10.0]}).write_parquet(parquet_path)

    with monkeypatch.context() as scoped:
        _block_import(scoped, "polars")
        assert engine.load_cache_parquet() is None

    pl.DataFrame({"date": ["20260101"], "version": [2]}).write_parquet(meta_path)
    assert engine.load_cache_parquet() is None

    pl.DataFrame({"date": ["20260101"], "version": [3]}).write_parquet(meta_path)
    pl.DataFrame(
        {
            "_code": ["000001", "000001", "600000"],
            "close": [10.0, 11.0, 20.0],
        }
    ).write_parquet(parquet_path)
    cache, trade_date = engine.load_cache_parquet()
    assert trade_date == "20260101"
    assert set(cache) == {"000001", "600000"}
    assert cache["000001"].columns == ["close"]
    assert cache["000001"]["close"].to_list() == [10.0, 11.0]

    meta_path.unlink()
    pl.DataFrame({"close": [1.0]}).write_parquet(parquet_path)
    assert engine.load_cache_parquet() == ({}, "")

    parquet_path.write_bytes(b"corrupt")
    assert engine.load_cache_parquet() is None


def test_build_sector_rps_handles_default_periods_prefixed_codes_and_invalid_rows():
    length = 56
    dates = pd.date_range("2025-11-01", periods=length, freq="D")
    all_data = {
        "sh600001": _dated_polars_frame(list(np.linspace(10, 20, length)), start=str(dates[0].date())),
        "sh600002": _dated_polars_frame(list(np.linspace(10, 25, length)), start=str(dates[0].date())),
        "sh600003": _dated_polars_frame(list(np.linspace(10, 30, length)), start=str(dates[0].date())),
        "none": None,
        "short": _dated_polars_frame([1.0, 2.0]),
        "missing": pl.DataFrame({"datetime": dates}),
        "future": _dated_polars_frame([1.0] * length, start="2027-01-01"),
        "zero": _dated_polars_frame([1.0] * (length - 1) + [0.0], start=str(dates[0].date())),
        "bad": pl.DataFrame({"datetime": dates, "close": ["bad"] * length}),
    }
    sectors = {"上涨": ["600001", "600002", "600003"]}

    result = engine.build_sector_rps_pl(sectors, all_data, dates[-1].to_pydatetime())

    assert set(result["上涨"]) == {5, 10, 15, 20, 50}
    assert all(value == 100.0 for value in result["上涨"].values())


def test_build_sector_rps_returns_empty_for_no_returns_or_no_members():
    assert engine.build_sector_rps_pl({}, {}, date(2026, 1, 1), periods=[1]) == {}

    frame = _dated_polars_frame([1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0])
    assert engine.build_sector_rps_pl({}, {"600001": frame}, date(2026, 1, 7), periods=[1]) == {}

    zero_history = _dated_polars_frame([0.0, 1.0, 2.0, 3.0, 4.0, 5.0, 6.0])
    assert engine.build_sector_rps_pl({"x": ["sh600001"]}, {"600001": zero_history}, date(2026, 1, 2), periods=[5]) == {}
