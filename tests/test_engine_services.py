from __future__ import annotations

import builtins
import importlib
from datetime import datetime

import pandas as pd
import polars as pl
import pytest

import domains.scan.breakout_monitor_service as breakout_monitor_module
from domains.market_calendar import MarketCalendar
from domains.scan import BreakoutMonitorService, IndicatorService, RpsService, VcpScannerService
from vcp.engine import VCPEngine
from vcp.models import VCPParams


def test_engine_uses_rps_service_for_precomputed_bundle():
    engine = VCPEngine.get_instance()
    engine._rps_service = RpsService()

    engine.set_precomputed_rps("20260420", {"000001": 88.0}, {"000001": 92.0})

    bundle = engine.get_precomputed_rps()
    assert bundle == {
        "date": "20260420",
        "rps120": {"000001": 88.0},
        "rps250": {"000001": 92.0},
    }


def test_indicator_service_calculates_core_columns_for_pandas_frame():
    df = pd.DataFrame(
        {
            "close": [10 + idx * 0.1 for idx in range(260)],
            "open": [10 + idx * 0.1 - 0.05 for idx in range(260)],
            "high": [10 + idx * 0.1 + 0.08 for idx in range(260)],
            "low": [10 + idx * 0.1 - 0.1 for idx in range(260)],
            "volume": [1000 + idx for idx in range(260)],
        },
        index=pd.date_range("2025-01-01", periods=260, freq="D", name="datetime"),
    )

    result = IndicatorService.calculate_indicators(df)

    assert "SMA50" in result.columns
    assert "entangle" in result.columns
    assert result.attrs["vcp_indicators_ready"] is True


def test_indicator_service_returns_early_for_short_and_ready_frames():
    short = pd.DataFrame({"close": [1.0]})
    assert IndicatorService.calculate_indicators(short) is short
    assert IndicatorService.calculate_indicators(None) is None

    ready = pd.DataFrame({"entangle": [0.01 for _idx in range(10)], "MACD": [0.0 for _idx in range(10)]})
    ready.attrs["vcp_indicators_ready"] = True
    assert IndicatorService.calculate_indicators(ready) is ready

    pldf = pl.DataFrame({"entangle": [0.01 for _idx in range(10)], "MACD": [0.0 for _idx in range(10)]})
    assert IndicatorService.calculate_indicators(pldf) is pldf


def test_indicator_service_handles_datetime_index_without_name_and_existing_amount():
    dates = pd.date_range("2026-01-01", periods=260, freq="D")
    df = pd.DataFrame(
        {
            "close": [10.0 + idx * 0.1 for idx in range(260)],
            "open": [10.0 + idx * 0.1 - 0.05 for idx in range(260)],
            "high": [10.0 + idx * 0.1 + 0.1 for idx in range(260)],
            "low": [10.0 + idx * 0.1 - 0.1 for idx in range(260)],
            "volume": [1000.0 + idx for idx in range(260)],
            "amount": [0.0 for _idx in range(260)],
        },
        index=dates,
    )

    result = IndicatorService.calculate_indicators(df, include_chart=False)

    assert result.index.name == "datetime"
    assert result.attrs["vcp_core_ready"] is True
    assert "MACD" not in result.columns
    assert result["amount"].iloc[-1] > 0


def test_indicator_service_restores_date_index_name():
    dates = pd.date_range("2026-01-01", periods=260, freq="D", name="date")
    df = pd.DataFrame(
        {
            "close": [10.0 + idx * 0.1 for idx in range(260)],
            "open": [10.0 + idx * 0.1 - 0.05 for idx in range(260)],
            "high": [10.0 + idx * 0.1 + 0.1 for idx in range(260)],
            "low": [10.0 + idx * 0.1 - 0.1 for idx in range(260)],
            "volume": [1000.0 + idx for idx in range(260)],
        },
        index=dates,
    )

    result = IndicatorService.calculate_indicators(df, include_chart=False)

    assert result.index.name == "date"


def test_indicator_service_handles_plain_pandas_index_and_computed_polars_result():
    df = pd.DataFrame(
        {
            "close": [10.0 + idx * 0.1 for idx in range(260)],
            "open": [10.0 + idx * 0.1 - 0.05 for idx in range(260)],
            "high": [10.0 + idx * 0.1 + 0.1 for idx in range(260)],
            "low": [10.0 + idx * 0.1 - 0.1 for idx in range(260)],
            "volume": [1000.0 + idx for idx in range(260)],
        }
    )
    result = IndicatorService.calculate_indicators(df, include_chart=False)
    assert "entangle" in result.columns

    pldf = pl.DataFrame(df)
    pl_result = IndicatorService.calculate_indicators(pldf, include_chart=False)
    assert isinstance(pl_result, pl.DataFrame)
    assert "entangle" in pl_result.columns


def test_legacy_vcp_engine_module_is_a_thin_alias_shim():
    legacy_module = importlib.import_module("vcp.engine")
    target_module = importlib.import_module("app.services.scan_engine_facade")

    assert legacy_module is target_module


def test_rps_service_falls_back_to_pandas_matrix(monkeypatch):
    try:
        polars_engine = importlib.import_module("vcp.polars_engine")
    except ImportError:
        polars_engine = None
    if polars_engine is not None:
        monkeypatch.setattr(polars_engine, "build_prices_matrix_fast", lambda *_args, **_kwargs: ([], [], []))
        monkeypatch.setattr(polars_engine, "build_rps_matrix_pl", lambda *_args, **_kwargs: {})
    monkeypatch.setattr(MarketCalendar, "today", classmethod(lambda cls, market="CN": "2026-04-20"))

    dates = pd.date_range("2025-01-01", periods=260, freq="D", name="datetime")
    data = {
        "000001": pd.DataFrame({"close": [10 + idx * 0.10 for idx in range(260)]}, index=dates),
        "000002": pd.DataFrame({"close": [10 + idx * 0.02 for idx in range(260)]}, index=dates),
    }

    result = RpsService().build_rps_matrix(data, "2025-09-17", "2025-09-17")

    assert "20250917" in result
    assert set(result["20250917"]["rps120"]) == {"000001", "000002"}
    assert result["20250917"]["rps250"]["000001"] > result["20250917"]["rps250"]["000002"]


def test_rps_service_uses_fast_prices_matrix(monkeypatch):
    polars_engine = importlib.import_module("vcp.polars_engine")
    dates = pd.DatetimeIndex([pd.Timestamp("2026-04-20")])
    monkeypatch.setattr(polars_engine, "build_prices_matrix_fast", lambda *_args: ([[10.0]], ["000001"], dates))

    prices = RpsService.build_prices_matrix({}, pd.Timestamp("2026-04-01"))

    assert list(prices.columns) == ["000001"]
    assert prices.loc[pd.Timestamp("2026-04-20"), "000001"] == 10.0


def test_rps_service_build_prices_matrix_handles_empty_and_bad_frames():
    assert RpsService.build_prices_matrix({}, pd.Timestamp("2026-04-01")).empty

    bad = pd.DataFrame({"open": [1.0]}, index=pd.date_range("2026-04-01", periods=1))
    assert RpsService.build_prices_matrix({"000001": bad}, pd.Timestamp("2026-04-01")).empty


def test_rps_service_uses_polars_result_and_cache(monkeypatch):
    polars_engine = importlib.import_module("vcp.polars_engine")
    service = RpsService()
    monkeypatch.setattr(MarketCalendar, "today", classmethod(lambda cls, market="CN": "2026-04-20"))
    monkeypatch.setattr(polars_engine, "build_rps_matrix_pl", lambda *_args: {"20260420": {"rps120": {"000001": 90}}})

    assert service.build_rps_matrix({"000001": pd.DataFrame()}, "2026-04-20", "2026-04-20") == {
        "20260420": {"rps120": {"000001": 90}}
    }

    service._daily_rps_cache[("2026-04-20", "2026-04-20")] = {"cached": True}
    assert service.build_rps_matrix({}, "2026-04-20", "2026-04-20") == {"cached": True}


def test_rps_service_returns_empty_when_polars_fails_and_prices_empty(monkeypatch):
    polars_engine = importlib.import_module("vcp.polars_engine")
    service = RpsService()
    monkeypatch.setattr(MarketCalendar, "today", classmethod(lambda cls, market="CN": "2026-04-20"))
    monkeypatch.setattr(
        polars_engine,
        "build_rps_matrix_pl",
        lambda *_args: (_ for _ in ()).throw(RuntimeError("fast path failed")),
    )
    monkeypatch.setattr(RpsService, "build_prices_matrix", staticmethod(lambda *_args, **_kwargs: pd.DataFrame()))

    assert service.build_rps_matrix({"000001": pd.DataFrame()}, "2026-04-20", "2026-04-20") == {}


def test_rps_service_handles_missing_fast_engine(monkeypatch):
    original_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "vcp.polars_engine":
            raise ImportError("missing fast engine")
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    monkeypatch.setattr(MarketCalendar, "today", classmethod(lambda cls, market="CN": "2026-04-20"))

    assert RpsService.build_prices_matrix({}, pd.Timestamp("2026-04-01")).empty
    assert RpsService().build_rps_matrix({}, "2026-04-20", "2026-04-20") == {}


def test_rps_service_logs_fast_prices_failure_and_uses_last_available_date(monkeypatch):
    polars_engine = importlib.import_module("vcp.polars_engine")
    dates = pd.date_range("2025-01-01", periods=260, freq="D", name="datetime")
    prices = pd.DataFrame(
        {
            "000001": [10 + idx for idx in range(260)],
            "000002": [20 + idx for idx in range(260)],
        },
        index=dates,
    )
    service = RpsService()

    monkeypatch.setattr(
        polars_engine,
        "build_prices_matrix_fast",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(ValueError("fast prices failed")),
    )
    monkeypatch.setattr(polars_engine, "build_rps_matrix_pl", lambda *_args, **_kwargs: {})
    monkeypatch.setattr(RpsService, "build_prices_matrix", staticmethod(lambda *_args, **_kwargs: prices))
    monkeypatch.setattr(MarketCalendar, "today", classmethod(lambda cls, market="CN": "2026-04-20"))

    result = service.build_rps_matrix({"000001": prices}, "2026-01-01", "2026-01-02")

    assert list(result) == [dates[-1].strftime("%Y%m%d")]


def test_breakout_monitor_quick_check_scores_breakout_and_near_breakout(monkeypatch):
    monkeypatch.setattr(MarketCalendar, "now", classmethod(lambda cls, market="CN": datetime(2026, 4, 20, 14, 0)))

    pool_entry = {"box_high": 10.0, "score": 80.0, "vol_ma25": 1000.0}

    ok, status, score = BreakoutMonitorService.rt_quick_check(
        {"close": 11.0, "open": 10.5, "high": 11.2, "low": 10.1, "volume": 1600},
        pool_entry,
    )
    assert ok is True
    assert score == 105.0
    assert status

    ok, status, score = BreakoutMonitorService.rt_quick_check(
        {"close": 9.8, "open": 9.6, "high": 9.9, "low": 9.5, "volume": 100},
        pool_entry,
    )
    assert ok is True
    assert score == 90.0
    assert status

    ok, _status, score = BreakoutMonitorService.rt_quick_check(
        {"close": 11.0, "open": 10.5, "high": 11.0, "low": 11.0, "volume": 100},
        pool_entry,
    )
    assert ok is False
    assert score == 0


def test_breakout_monitor_quick_check_covers_rejection_and_volume_branches(monkeypatch):
    pool_entry = {"box_high": 10.0, "score": 80.0, "vol_ma25": 1000.0}

    assert BreakoutMonitorService.rt_quick_check({"close": 0, "open": 1}, pool_entry)[0] is False
    assert BreakoutMonitorService.rt_quick_check({"close": 10, "open": 11}, pool_entry)[0] is False

    monkeypatch.setattr(MarketCalendar, "now", classmethod(lambda cls, market="CN": datetime(2026, 4, 20, 14, 0)))

    ok, status, score = BreakoutMonitorService.rt_quick_check(
        {"close": 11.0, "open": 10.0, "high": 11.2, "low": 10.0, "volume": 100},
        pool_entry,
    )
    assert ok is True
    assert score == 70.0
    assert status

    ok, status, score = BreakoutMonitorService.rt_quick_check(
        {"close": 11.0, "open": 10.0, "high": 11.2, "low": 10.0, "volume": 100},
        {"box_high": 10.0, "score": 80.0, "vol_ma25": 0},
    )
    assert ok is True
    assert score == 80.0
    assert status

    ok, status, score = BreakoutMonitorService.rt_quick_check(
        {"close": 9.0, "open": 8.5, "high": 9.2, "low": 8.0, "volume": 1000},
        pool_entry,
    )
    assert ok is True
    assert score == 80.0
    assert status


def test_breakout_monitor_estimate_full_day_volume_sessions(monkeypatch):
    monkeypatch.setattr(MarketCalendar, "now", classmethod(lambda cls, market="CN": datetime(2026, 4, 20, 9, 20)))
    assert BreakoutMonitorService.estimate_full_day_volume(100) == 0

    monkeypatch.setattr(MarketCalendar, "now", classmethod(lambda cls, market="CN": datetime(2026, 4, 20, 9, 45)))
    assert BreakoutMonitorService.estimate_full_day_volume(100) == 800

    monkeypatch.setattr(MarketCalendar, "now", classmethod(lambda cls, market="CN": datetime(2026, 4, 20, 12, 30)))
    assert BreakoutMonitorService.estimate_full_day_volume(120) == 240

    monkeypatch.setattr(MarketCalendar, "now", classmethod(lambda cls, market="CN": datetime(2026, 4, 20, 15, 1)))
    assert BreakoutMonitorService.estimate_full_day_volume(240) == 240


def test_breakout_monitor_precompute_filters_st_and_adds_metadata(monkeypatch):
    dates = pd.date_range("2025-01-01", periods=260, freq="D", name="datetime")
    frame = pd.DataFrame(
        {
            "close": [10 + idx * 0.05 for idx in range(260)],
            "high": [10 + idx * 0.05 + 0.1 for idx in range(260)],
            "low": [10 + idx * 0.05 - 0.1 for idx in range(260)],
            "volume": [1000 + idx for idx in range(260)],
            "entangle": [0.01 for _idx in range(260)],
        },
        index=dates,
    )
    monkeypatch.setattr(
        VcpScannerService,
        "evaluate_conditions",
        staticmethod(lambda *_args, **_kwargs: (True, "OK", {})),
    )
    monkeypatch.setattr(
        breakout_monitor_module,
        "batch_check_institution",
        lambda codes: {code: {"has_institution": code == "000001", "detail": "holder"} for code in codes},
    )
    monkeypatch.setattr(
        breakout_monitor_module,
        "batch_check_market_cap",
        lambda codes, close_prices=None: {code: 5_000_000_000 for code in codes},
    )

    pool = BreakoutMonitorService.precompute_ready_pool(
        {"000001": frame, "000002": frame.copy()},
        {"000001": 91.0, "000002": 92.0},
        {"000001": 93.0, "000002": 94.0},
        VCPParams(),
        code2name={"000001": "Alpha", "000002": "ST Beta"},
    )

    assert set(pool) == {"000001"}
    assert "market_cap" in pool["000001"]
    assert pool["000001"]["institution_tag"]


def test_breakout_monitor_precompute_covers_diagnostic_skip_branches(monkeypatch):
    with pytest.raises(InterruptedError):
        BreakoutMonitorService.precompute_ready_pool(
            {"000001": pd.DataFrame()},
            {},
            {},
            VCPParams(),
            cancelled_checker=lambda: True,
        )

    progress = []
    short_pool = BreakoutMonitorService.precompute_ready_pool(
        {"000001": pd.DataFrame({"close": [1.0]})},
        {},
        {},
        VCPParams(),
        progress_callback=progress.append,
    )
    assert short_pool == {}
    assert progress

    dates = pd.date_range("2026-01-01", periods=260, freq="D", name="datetime")
    no_rps = pd.DataFrame(
        {
            "close": [10.0 for _idx in range(260)],
            "high": [11.0 for _idx in range(260)],
            "low": [9.0 for _idx in range(260)],
            "volume": [1000.0 for _idx in range(260)],
            "entangle": [0.01 for _idx in range(260)],
        },
        index=dates,
    )
    assert BreakoutMonitorService.precompute_ready_pool({"000001": no_rps}, {}, {}, VCPParams()) == {}

    no_indicators = no_rps.drop(columns=["entangle"])
    monkeypatch.setattr(
        IndicatorService,
        "calculate_indicators",
        staticmethod(lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("indicator failed"))),
    )
    assert (
        BreakoutMonitorService.precompute_ready_pool(
            {"000001": no_indicators},
            {"000001": 90.0},
            {"000001": 91.0},
            VCPParams(),
        )
        == {}
    )

    monkeypatch.setattr(IndicatorService, "calculate_indicators", staticmethod(lambda df, **_kwargs: df.assign(entangle=0.01)))
    monkeypatch.setattr(
        VcpScannerService,
        "evaluate_conditions",
        staticmethod(lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("eval failed"))),
    )
    assert (
        BreakoutMonitorService.precompute_ready_pool(
            {"000001": no_indicators},
            {"000001": 90.0},
            {"000001": 91.0},
            VCPParams(),
        )
        == {}
    )

    monkeypatch.setattr(VcpScannerService, "evaluate_conditions", staticmethod(lambda *_args, **_kwargs: (False, "no", {})))
    assert BreakoutMonitorService.precompute_ready_pool({"000001": no_rps}, {"000001": 90.0}, {"000001": 91.0}, VCPParams()) == {}

    class RejectingSectorManager:
        def check_sector_rps(self, code, sector_rps_dict, sector_threshold):
            return False, "weak sector", None

    monkeypatch.setattr(
        VcpScannerService,
        "evaluate_conditions",
        staticmethod(lambda *_args, **_kwargs: (True, "OK", {"区间最高价": 12.0, "区间最低点": 9.0, "评分": 80})),
    )
    assert (
        BreakoutMonitorService.precompute_ready_pool(
            {"000001": no_rps},
            {"000001": 90.0},
            {"000001": 91.0},
            VCPParams(),
            sector_manager=RejectingSectorManager(),
            sector_rps_dict={"sector": 50},
        )
        == {}
    )


def test_breakout_monitor_precompute_covers_polars_conversion_and_post_filters(monkeypatch):
    dates = pd.date_range("2026-01-01", periods=260, freq="D")
    pldf = pl.DataFrame(
        {
            "datetime": dates,
            "close": [10.0 for _idx in range(260)],
            "high": [11.0 for _idx in range(260)],
            "low": [9.0 for _idx in range(260)],
            "volume": [1000.0 for _idx in range(260)],
            "entangle": [0.01 for _idx in range(260)],
        }
    )
    monkeypatch.setattr(
        VcpScannerService,
        "evaluate_conditions",
        staticmethod(
            lambda *_args, **_kwargs: (
                True,
                "OK",
                {"区间最高价": 12.0, "区间最低点": 9.0, "评分": 80, "RPS强度": "90/91"},
            )
        ),
    )
    monkeypatch.setattr(
        breakout_monitor_module,
        "batch_check_institution",
        lambda codes: {code: {"has_institution": False, "detail": ""} for code in codes},
    )
    monkeypatch.setattr(
        breakout_monitor_module,
        "batch_check_market_cap",
        lambda codes, close_prices=None: {code: 1 for code in codes},
    )

    pool = BreakoutMonitorService.precompute_ready_pool(
        {"000001": pldf},
        {"000001": 90.0},
        {"000001": 91.0},
        VCPParams(),
    )

    assert pool["000001"]["institution_tag"]
    assert pool["000001"]["small_cap"] is True

    monkeypatch.setattr(
        breakout_monitor_module,
        "batch_check_institution",
        lambda codes: (_ for _ in ()).throw(RuntimeError("institution failed")),
    )
    monkeypatch.setattr(
        breakout_monitor_module,
        "batch_check_market_cap",
        lambda codes, close_prices=None: {code: 0 for code in codes},
    )
    pool = BreakoutMonitorService.precompute_ready_pool({"000001": pldf}, {"000001": 90.0}, {"000001": 91.0}, VCPParams())
    assert pool["000001"]["market_cap"]

    monkeypatch.setattr(breakout_monitor_module, "batch_check_institution", lambda codes: {})
    monkeypatch.setattr(
        breakout_monitor_module,
        "batch_check_market_cap",
        lambda codes, close_prices=None: (_ for _ in ()).throw(RuntimeError("cap failed")),
    )
    assert BreakoutMonitorService.precompute_ready_pool({"000001": pldf}, {"000001": 90.0}, {"000001": 91.0}, VCPParams())


def test_vcp_basic_entry_gates_reject_low_liquidity():
    pldf = pl.DataFrame(
        {
            "amount": [1.0 for _idx in range(10)],
            "entangle": [0.01 for _idx in range(10)],
            "SMA50": [10.0 + idx * 0.01 for idx in range(10)],
        }
    )
    row = {
        "SMA200": 9.0,
        "SMA50": 10.0,
        "SMA150": 9.5,
        "close": 11.0,
        "open": 10.8,
    }
    params = VCPParams(min_history_days=5, min_amount_20d=8e7, enable_ma_slope=False)

    reason = VcpScannerService._check_basic_entry_gates(
        pldf,
        9,
        row,
        90.0,
        92.0,
        params,
        skip_red_check=False,
    )

    assert reason


def test_vcp_flexible_peaks_keeps_spaced_highs_above_baseline():
    closes = [80.0 for _idx in range(130)]
    for idx, value in ((10, 100.0), (40, 98.0), (75, 95.0), (110, 94.0)):
        closes[idx] = value
    frame = pl.DataFrame({"close": closes})

    peaks, reason = VcpScannerService.calculate_flexible_peaks(frame, 129, VCPParams())

    assert reason == "OK"
    assert peaks == [(10, 100.0), (40, 98.0), (75, 95.0), (110, 94.0)]


def test_vcp_score_rewards_contracting_volume_and_confirmed_breakout():
    pldf = pl.DataFrame({"volume": [1000.0 for _idx in range(50)] + [500.0 for _idx in range(10)]})
    buy_zone = pl.DataFrame({"volume": [500.0 for _idx in range(10)]})
    row = {
        "ATR10": 1.0,
        "ATR20": 2.0,
        "ATR60": 3.0,
        "close": 11.0,
        "volume": 1600.0,
        "vol_ma25": 1000.0,
    }

    score, dist, status = VcpScannerService._score_vcp_setup(pldf, 59, row, 92.0, buy_zone, 10.0)

    assert score == 116.0
    assert round(dist, 4) == -0.0909
    assert status


def test_vcp_metrics_include_internal_indices_and_peak_dates():
    s1 = pl.DataFrame({"high": [10.0, 12.0], "low": [9.0, 10.0]})
    s2 = pl.DataFrame({"high": [11.0, 13.0], "low": [10.0, 11.0]})
    zone = {
        "peak_idx": 10,
        "last_peak_idx": 75,
        "h2_idx": 40,
        "h3_idx": 75,
        "s1": s1,
        "s2": s2,
        "box_low": 9.0,
        "box_high": 13.0,
        "left_amp": 0.25,
    }

    metrics = VcpScannerService._build_vcp_metrics(
        {"close": 12.5},
        91.0,
        93.0,
        101.25,
        0.04,
        "near",
        ["2026-01-01", "2026-02-01", "2026-03-01"],
        zone,
        80,
    )

    assert metrics["_hit_base"] == 70
    assert metrics["_hit_E"] == 6
    assert metrics["_high1_idx"] == 10
    assert metrics["_high2_date"] == "2026-02-01"
    assert metrics["_peak_dates"] == ["2026-01-01", "2026-02-01", "2026-03-01"]


def _vcp_structure_frame() -> pl.DataFrame:
    return pl.DataFrame(
        {
            "high": [10.0 + (idx % 5) for idx in range(120)],
            "low": [8.0 + (idx % 3) for idx in range(120)],
            "volume": [1000.0 for _idx in range(120)],
            "datetime": pd.date_range("2026-01-01", periods=120, freq="D"),
        }
    )


def test_vcp_structure_rules_cover_rejection_branches():
    pldf = _vcp_structure_frame()
    params = VCPParams(amp_threshold=0.20)
    final_peaks = [(10, 12.0), (40, 11.8), (70, 11.5)]
    base_zone = VcpScannerService._build_vcp_zone_context(pldf, 90, final_peaks)
    row = {"High_250": 15.0, "close": 12.0}

    too_early_zone = dict(base_zone, last_peak_idx=89)
    assert VcpScannerService._check_vcp_structure_rules(pldf, 90, row, final_peaks, params, too_early_zone)

    wide_buy_zone = dict(base_zone, buy_zone=pl.DataFrame({"low": [8.0], "high": [12.0]}), left_amp=0.01)
    assert VcpScannerService._check_vcp_structure_rules(pldf, 90, row, final_peaks, params, wide_buy_zone)

    wide_left_zone = dict(base_zone, left_amp=0.50, buy_zone=pl.DataFrame({"low": [10.0], "high": [10.1]}))
    assert VcpScannerService._check_vcp_structure_rules(pldf, 90, row, final_peaks, params, wide_left_zone)

    assert VcpScannerService._check_vcp_structure_rules(pldf, 90, {"High_250": 0, "close": 12.0}, final_peaks, params, base_zone)
    assert VcpScannerService._check_vcp_structure_rules(
        pldf,
        90,
        {"High_250": 30.0, "close": 12.0},
        final_peaks,
        VCPParams(high_250_threshold=0.01),
        base_zone,
    )
    assert VcpScannerService._check_vcp_structure_rules(
        pldf,
        90,
        {"High_250": 15.0, "close": base_zone["box_low"] * 1.01},
        final_peaks,
        params,
        base_zone,
    )


def test_vcp_evaluate_conditions_main_flow_branches(monkeypatch):
    frame = pl.DataFrame(
        {
            "datetime": pd.date_range("2026-01-01", periods=3, freq="D"),
            "close": [10.0, 11.0, 12.0],
            "open": [9.0, 10.0, 11.0],
            "high": [11.0, 12.0, 13.0],
            "low": [8.0, 9.0, 10.0],
            "volume": [1000.0, 1000.0, 1000.0],
            "amount": [1e8, 1e8, 1e8],
            "entangle": [0.01, 0.01, 0.01],
            "SMA50": [10.0, 10.5, 11.0],
            "SMA150": [9.0, 9.5, 10.0],
            "SMA200": [8.0, 8.5, 9.0],
            "High_250": [13.0, 13.0, 13.0],
        }
    )
    params = VCPParams(min_history_days=1, enable_ma_slope=False)

    ok, reason, metrics = VcpScannerService.evaluate_conditions(frame, datetime(2026, 2, 1), 90.0, 91.0, params=params)
    assert (ok, reason, metrics) == (False, "非交易日", {})

    ok, reason, metrics = VcpScannerService.evaluate_conditions(frame, datetime(2026, 1, 3), float("nan"), 91.0, params=params)
    assert ok is False
    assert reason

    monkeypatch.setattr(
        VcpScannerService,
        "_check_basic_entry_gates",
        staticmethod(lambda *_args, **_kwargs: "basic failed"),
    )
    ok, reason, metrics = VcpScannerService.evaluate_conditions(frame, datetime(2026, 1, 3), 90.0, 91.0, params=params)
    assert (ok, reason, metrics) == (False, "basic failed", {})

    monkeypatch.setattr(VcpScannerService, "_check_basic_entry_gates", staticmethod(lambda *_args, **_kwargs: None))
    monkeypatch.setattr(
        VcpScannerService,
        "calculate_flexible_peaks",
        staticmethod(lambda *_args, **_kwargs: (None, "no peaks")),
    )
    ok, reason, metrics = VcpScannerService.evaluate_conditions(frame, datetime(2026, 1, 3), 90.0, 91.0, params=params)
    assert (ok, reason, metrics) == (False, "no peaks", {})

    monkeypatch.setattr(
        VcpScannerService,
        "calculate_flexible_peaks",
        staticmethod(lambda *_args, **_kwargs: ([(0, 11.0), (1, 12.0), (2, 13.0)], "OK")),
    )
    monkeypatch.setattr(
        VcpScannerService,
        "_check_vcp_structure_rules",
        staticmethod(lambda *_args, **_kwargs: "structure failed"),
    )
    ok, reason, metrics = VcpScannerService.evaluate_conditions(frame, datetime(2026, 1, 3), 90.0, 91.0, params=params)
    assert (ok, reason, metrics) == (False, "structure failed", {})

    monkeypatch.setattr(VcpScannerService, "_check_vcp_structure_rules", staticmethod(lambda *_args, **_kwargs: None))
    monkeypatch.setattr(
        VcpScannerService,
        "_build_vcp_zone_context",
        staticmethod(
            lambda *_args, **_kwargs: {
                "peak_idx": 0,
                "last_peak_idx": 1,
                "h2_idx": 1,
                "h3_idx": 2,
                "buy_zone": pl.DataFrame({"volume": [500.0]}),
                "box_low": 8.0,
                "box_high": 13.0,
                "left_amp": 0.10,
                "s1": pl.DataFrame({"high": [11.0], "low": [8.0]}),
                "s2": pl.DataFrame({"high": [12.0], "low": [9.0]}),
            }
        ),
    )
    monkeypatch.setattr(
        VcpScannerService,
        "_score_vcp_setup",
        staticmethod(lambda *_args, **_kwargs: (99.0, 0.01, "near")),
    )
    monkeypatch.setattr(
        VcpScannerService,
        "_format_peak_dates",
        staticmethod(lambda *_args, **_kwargs: ["2026-01-01", "2026-01-02", "2026-01-03"]),
    )

    ok, reason, metrics = VcpScannerService.evaluate_conditions(frame, datetime(2026, 1, 3), 90.0, 91.0, params=params)

    assert ok is True
    assert reason == "OK"
    assert metrics["评分"] == 99.0
