# -*- coding: utf-8 -*-
from __future__ import annotations

import builtins
from collections import defaultdict

import pandas as pd
import pytest

from vcp.sector import SectorManager


def _manager(tmp_path, sectors: dict[str, list[str]]) -> SectorManager:
    manager = SectorManager(str(tmp_path))
    manager.sector_to_codes = defaultdict(list, sectors)
    return manager


def _pandas_frame(closes: list[float], dates=None) -> pd.DataFrame:
    source_dates = dates if dates is not None else pd.date_range("2026-01-01", periods=len(closes), freq="D")
    index = pd.DatetimeIndex(source_dates, name="datetime")
    return pd.DataFrame({"close": closes}, index=index)


def _polars_frame(closes: list[float], dates=None):
    pl = pytest.importorskip("polars")
    source_dates = dates if dates is not None else pd.date_range("2026-01-01", periods=len(closes), freq="D")
    return pl.DataFrame(
        {
            "datetime": source_dates,
            "close": closes,
        }
    )


def _two_sector_mapping() -> dict[str, list[str]]:
    return {
        "低波动": ["sh600001", "sh600002", "sh600003"],
        "高动量": ["sz000001", "sz000002", "sz000003"],
    }


def _two_sector_data(frame_factory):
    return {
        "600001": frame_factory([10, 10, 10, 10, 10, 10, 10]),
        "600002": frame_factory([10, 10, 10, 10, 10, 10, 10]),
        "600003": frame_factory([10, 10, 10, 10, 10, 10, 10]),
        "000001": frame_factory([10, 10, 10, 10, 10, 11, 12]),
        "000002": frame_factory([10, 10, 10, 10, 10, 12, 14]),
        "000003": frame_factory([10, 10, 10, 10, 10, 13, 16]),
    }


def test_build_sector_rps_returns_fast_path_result_unchanged(monkeypatch, tmp_path):
    manager = _manager(tmp_path, _two_sector_mapping())
    sentinel = {"快速板块": {2: 100.0}}
    calls = []

    def fast_path(sector_to_codes, all_data, target_date, periods):
        calls.append((sector_to_codes, all_data, target_date, periods))
        return sentinel

    monkeypatch.setattr("vcp.polars_engine.build_sector_rps_pl", fast_path)
    all_data = {"600001": object()}

    result = manager.build_sector_rps(all_data, "20260107", periods=[2])

    assert result is sentinel
    assert calls == [(_two_sector_mapping(), all_data, "20260107", [2])]


@pytest.mark.parametrize("frame_factory", [_pandas_frame, _polars_frame])
def test_build_sector_rps_fallback_matches_for_pandas_and_polars(monkeypatch, tmp_path, frame_factory):
    manager = _manager(tmp_path, _two_sector_mapping())
    monkeypatch.setattr(
        "vcp.polars_engine.build_sector_rps_pl",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("force fallback")),
    )

    result = manager.build_sector_rps(_two_sector_data(frame_factory), "20260107", periods=[2])

    assert result == {"低波动": {2: 50.0}, "高动量": {2: 100.0}}


@pytest.mark.parametrize("frame_factory", [_pandas_frame, _polars_frame])
def test_build_sector_rps_real_fast_path_matches_supported_frames(tmp_path, frame_factory):
    manager = _manager(tmp_path, _two_sector_mapping())

    result = manager.build_sector_rps(_two_sector_data(frame_factory), "20260107", periods=[2])

    assert result == {"低波动": {2: 50.0}, "高动量": {2: 100.0}}


def test_build_sector_rps_real_fast_path_respects_target_date(monkeypatch, tmp_path):
    manager = _manager(tmp_path, _two_sector_mapping())
    dates = pd.to_datetime(
        ["2026-01-01", "2026-01-02", "2026-01-03", "2026-01-04", "2026-01-05", "2026-01-07", "2026-01-08"]
    )
    all_data = {
        "600001": _polars_frame([10, 10, 10, 10, 10, 50, 50], dates),
        "600002": _polars_frame([10, 10, 10, 10, 10, 50, 50], dates),
        "600003": _polars_frame([10, 10, 10, 10, 10, 50, 50], dates),
        "000001": _polars_frame([10, 10, 10, 10, 20, 20, 20], dates),
        "000002": _polars_frame([10, 10, 10, 10, 20, 20, 20], dates),
        "000003": _polars_frame([10, 10, 10, 10, 20, 20, 20], dates),
    }
    monkeypatch.setattr(
        manager,
        "_compute_stock_returns",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("non-empty fast path must not fallback")),
    )

    result = manager.build_sector_rps(all_data, "20260106", periods=[2])

    assert result == {"低波动": {2: 50.0}, "高动量": {2: 100.0}}


def test_to_pldf_does_not_import_pandas_for_polars_input(monkeypatch):
    from vcp.polars_engine import _to_pldf

    frame = _polars_frame([10, 11, 12])
    real_import = builtins.__import__

    def guarded_import(name, *args, **kwargs):
        if name == "pandas":
            raise AssertionError("Polars input must not cold-import pandas")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", guarded_import)

    assert _to_pldf(frame) is frame


def test_build_sector_rps_fallback_uses_latest_row_not_after_target(monkeypatch, tmp_path):
    manager = _manager(tmp_path, {"目标板块": ["sh600001", "sh600002", "sh600003"]})
    monkeypatch.setattr(
        "vcp.polars_engine.build_sector_rps_pl",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("force fallback")),
    )
    dates = pd.to_datetime(
        ["2026-01-01", "2026-01-02", "2026-01-03", "2026-01-04", "2026-01-05", "2026-01-07", "2026-01-09"]
    )
    all_data = {
        code: _pandas_frame([10, 10, 10, 10, 10, 12, 30], dates=dates)
        for code in ("600001", "600002", "600003")
    }

    result = manager.build_sector_rps(all_data, "20260108", periods=[2])

    assert result == {"目标板块": {2: 100.0}}


def test_build_sector_rps_skips_invalid_members_without_losing_valid_sector(monkeypatch, tmp_path):
    manager = _manager(
        tmp_path,
        {
            "有效板块": ["sh600001", "sh600002", "sh600003"],
            "不足三只": ["sz000001", "sz000002", "sz000003", "sz000004"],
        },
    )
    monkeypatch.setattr(
        "vcp.polars_engine.build_sector_rps_pl",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("force fallback")),
    )
    all_data = {
        "600001": _pandas_frame([10, 10, 10, 10, 10, 11, 12]),
        "600002": _pandas_frame([10, 10, 10, 10, 10, 11, 12]),
        "600003": _pandas_frame([10, 10, 10, 10, 10, 11, 12]),
        "000001": _pandas_frame([10, 10, 10, 10, 10, 10, 10]),
        "000002": _pandas_frame([10, 10, 10, 10, 10, 10, 10]),
        "000003": pd.DataFrame({"not_close": range(7)}, index=pd.date_range("2026-01-01", periods=7)),
        "000004": _pandas_frame([10, 10, 10]),
    }

    result = manager.build_sector_rps(all_data, "20260107", periods=[2])

    assert result == {"有效板块": {2: 100.0}}


def test_build_sector_rps_empty_fast_path_keeps_legacy_pandas_fallback(tmp_path):
    manager = _manager(tmp_path, _two_sector_mapping())
    all_data = _two_sector_data(_pandas_frame)
    for frame in all_data.values():
        frame.index.name = None

    result = manager.build_sector_rps(all_data, "20260107", periods=[2])

    assert result == {"低波动": {2: 50.0}, "高动量": {2: 100.0}}
