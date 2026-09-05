from __future__ import annotations

import builtins

import numpy as np
import pandas as pd
import polars as pl
import pytest

from core.rps_cache_identity import rps_cache_key
from domains.scan.rps_service import RpsService
from vcp import polars_engine as engine


def _market_frames(codes=("000001", "000002")):
    dates = pd.date_range(end="2026-09-04", periods=engine.RPS_BUFFER_DAYS + 1, name="datetime")
    return {
        code: pd.DataFrame({"close": np.linspace(10.0, end, len(dates))}, index=dates)
        for code, end in zip(codes, (30.0, 20.0), strict=True)
    }


@pytest.mark.parametrize("without_scipy", [False, True])
def test_accelerated_rps_ranking_matches_pandas_for_ties_and_singletons(monkeypatch, without_scipy):
    if without_scipy:
        original_import = builtins.__import__

        def import_without_scipy(name, *args, **kwargs):
            if name == "scipy" or name.startswith("scipy."):
                raise ImportError("exercise NumPy fallback")
            return original_import(name, *args, **kwargs)

        monkeypatch.setattr(builtins, "__import__", import_without_scipy)
    matrix = np.array([[0.0, 0.0, 0.0], [2.0, 1.0, 2.0], [np.nan, 4.0, np.nan], [np.nan] * 3])

    np.testing.assert_allclose(
        engine._numpy_rank_pct_axis1(matrix),
        pd.DataFrame(matrix).rank(axis=1, pct=True).to_numpy(),
        equal_nan=True,
    )


@pytest.mark.parametrize("changed_field", ["close", "date"])
def test_rps_cache_key_tracks_intermediate_price_and_date_corrections(changed_field):
    data = _market_frames()
    before = rps_cache_key(data, "20260904", "20260904")
    frame = data["000001"]
    if changed_field == "close":
        frame.iloc[-251, 0] *= 10
    else:
        dates = list(frame.index)
        dates[-251] -= pd.Timedelta(hours=1)
        frame.index = pd.DatetimeIndex(dates, name="datetime")

    assert rps_cache_key(data, "20260904", "20260904") != before


def test_rps_cache_version_is_stable_across_equivalent_frame_reloads():
    data = _market_frames()
    reloaded = {code: frame.copy(deep=True) for code, frame in data.items()}

    assert rps_cache_key(data, "20260904", "20260904") == rps_cache_key(reloaded, "20260904", "20260904")


def test_disk_prices_cache_rejects_changed_security_universe(tmp_path):
    with engine.prices_matrix_cache_scope(str(tmp_path / "prices.parquet")):
        engine.build_rps_matrix_pl(_market_frames(), "20260904", "20260904")
        result = engine.build_rps_matrix_pl(_market_frames(("300001", "300002")), "20260904", "20260904")

    assert set(result["20260904"]["rps250"]) == {"300001", "300002"}


@pytest.mark.parametrize("same_service", [False, True])
def test_rps_cache_rejects_historical_price_correction_with_unchanged_last_price(tmp_path, same_service):
    data = _market_frames()
    service = RpsService(rps_matrix_builder=engine.build_rps_matrix_pl)
    with engine.prices_matrix_cache_scope(str(tmp_path / "prices.parquet")):
        before = service.build_rps_matrix(data, "20260904", "20260904")
        data["000001"].iloc[-251, 0] *= 10
        if not same_service:
            service = RpsService(rps_matrix_builder=engine.build_rps_matrix_pl)
        after = service.build_rps_matrix(data, "20260904", "20260904")

    assert before["20260904"]["rps250"]["000001"] == 100.0
    assert after["20260904"]["rps250"]["000001"] == 50.0


def test_validated_prices_cache_remains_reusable(tmp_path, monkeypatch):
    data = _market_frames()
    with engine.prices_matrix_cache_scope(str(tmp_path / "prices.parquet")):
        expected = engine.build_rps_matrix_pl(data, "20260904", "20260904")
        monkeypatch.setattr(
            engine,
            "build_prices_matrix_fast",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("validated cache should be reused")),
        )
        result = engine.build_rps_matrix_pl(data, "20260904", "20260904")

    assert result == expected


def test_unversioned_prices_cache_is_rebuilt_from_current_source(tmp_path):
    data = _market_frames()
    dates = data["000001"].index.to_numpy()
    with engine.prices_matrix_cache_scope(str(tmp_path / "prices.parquet")):
        engine._save_prices_matrix(np.ones((len(dates), 2)), ["stale-a", "stale-b"], dates)
        result = engine.build_rps_matrix_pl(data, "20260904", "20260904")

    assert set(result["20260904"]["rps250"]) == set(data)


def test_polars_rps_cache_key_tracks_intermediate_price_correction():
    frame = pl.from_pandas(_market_frames()["000001"].reset_index())
    data = {"000001": frame}
    before = rps_cache_key(data, "20260904", "20260904")
    corrected = frame.with_row_index().with_columns(
        pl.when(pl.col("index") == 20).then(200.0).otherwise(pl.col("close")).alias("close")
    ).drop("index")

    assert rps_cache_key({"000001": corrected}, "20260904", "20260904") != before


def test_changed_source_during_calculation_is_not_cached(tmp_path, monkeypatch):
    data = _market_frames()
    original_builder = engine.build_prices_matrix_fast

    def build_then_correct(*args, **kwargs):
        result = original_builder(*args, **kwargs)
        data["000001"].iloc[-251, 0] *= 10
        return result

    cache = {}
    cache_path = tmp_path / "prices.parquet"
    monkeypatch.setattr(engine, "build_prices_matrix_fast", build_then_correct)
    with engine.prices_matrix_cache_scope(str(cache_path)):
        engine.build_rps_matrix_pl(data, "20260904", "20260904", cache)

    assert cache == {}
    assert not cache_path.exists()


def test_accelerated_price_matrix_matches_pandas_suspension_fill_limit():
    dates = pd.date_range("2026-08-01", periods=12, name="datetime")
    data = {
        "000001": pd.DataFrame({"close": [10.0, 11.0]}, index=dates[:2]),
        "000002": pd.DataFrame({"close": np.arange(12, dtype=float) + 20.0}, index=dates),
    }
    expected = RpsService.build_prices_matrix(data, dates[0], dates[-1])
    matrix, columns, _dates = engine.build_prices_matrix_fast(data, dates[0], dates[-1])

    np.testing.assert_allclose(matrix, expected[columns].to_numpy(), equal_nan=True)


@pytest.mark.parametrize("unit", ["ms", "us", "ns"])
@pytest.mark.parametrize("index_name", ["date", "datetime", "trade_date", None])
def test_accelerated_prices_preserve_datetime_index_names_and_units(index_name, unit):
    dates = pd.date_range("2026-09-01", periods=4, name=index_name).as_unit(unit)
    data = {"000001": pd.DataFrame({"close": [10.0, 11.0, 12.0, 13.0]}, index=dates)}
    expected = RpsService.build_prices_matrix(data, dates[0], dates[-1])

    matrix, columns, result_dates = engine.build_prices_matrix_fast(data, dates[0], dates[-1])

    assert columns == ["000001"]
    np.testing.assert_array_equal(result_dates, dates.to_numpy().astype("datetime64[D]"))
    np.testing.assert_allclose(matrix, expected.to_numpy())
    assert data["000001"].index.name == index_name


def test_rps_from_parquet_date_index_matches_pandas(tmp_path):
    frames = _market_frames()
    wide = pd.concat([frame["close"].rename(code) for code, frame in frames.items()], axis=1)
    wide.index.name = "date"
    parquet_path = tmp_path / "source.parquet"
    pl.from_pandas(wide.reset_index()).with_columns(pl.col("date").cast(pl.Date)).write_parquet(parquet_path)
    reloaded = pl.read_parquet(parquet_path).to_pandas().set_index("date")
    data = {code: reloaded[[code]].rename(columns={code: "close"}) for code in reloaded.columns}
    expected = RpsService().build_rps_matrix(data, "20260904", "20260904")

    with engine.prices_matrix_cache_scope(str(tmp_path / "prices.parquet")):
        result = engine.build_rps_matrix_pl(data, "20260904", "20260904", {})

    assert result == expected
