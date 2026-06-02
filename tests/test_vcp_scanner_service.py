from __future__ import annotations

from datetime import datetime

import pandas as pd
import polars as pl

import domains.scan.vcp_scanner_service as vcp_scanner_module
from domains.scan import VcpScannerService
from vcp.models import VCPParams


def test_vcp_flexible_peaks_cover_rejection_edges(monkeypatch):
    params = VCPParams()
    monkeypatch.setattr(vcp_scanner_module, "LOOKBACK_DAYS", 80)
    monkeypatch.setattr(vcp_scanner_module, "EXCLUDE_DAYS_FOR_PEAKS", 5)
    monkeypatch.setattr(vcp_scanner_module, "GROUP_DAYS", 10)
    monkeypatch.setattr(vcp_scanner_module, "MIN_PEAKS_COUNT", 3)
    monkeypatch.setattr(vcp_scanner_module, "MAX_PEAKS_COUNT", 5)
    monkeypatch.setattr(vcp_scanner_module, "PEAKS_FROM_GROUPS", 5)
    monkeypatch.setattr(vcp_scanner_module, "PCT_BASELINE", 0.93)
    monkeypatch.setattr(vcp_scanner_module, "MERGE_WITHIN_DAYS", 5)
    monkeypatch.setattr(vcp_scanner_module, "FLEXIBLE_MIN_INTERVAL", 10)
    monkeypatch.setattr(vcp_scanner_module, "FLEXIBLE_MAX_INTERVAL", 120)

    assert VcpScannerService.calculate_flexible_peaks(pl.DataFrame({"close": [10.0] * 10}), 4, params)[0] is None
    assert VcpScannerService.calculate_flexible_peaks(pl.DataFrame({"close": [10.0] * 30}), 24, params)[0] is None
    assert VcpScannerService.calculate_flexible_peaks(pl.DataFrame({"close": [10.0] * 35}), 25, params)[0] is None
    assert VcpScannerService.calculate_flexible_peaks(pl.DataFrame({"close": [0.0] * 80}), 60, params)[0] is None

    weak = [70.0] * 80
    weak[5] = 100.0
    weak[25] = 80.0
    weak[45] = 79.0

    assert VcpScannerService.calculate_flexible_peaks(pl.DataFrame({"close": weak}), 60, params)[0] is None


def test_vcp_flexible_peaks_cover_merge_count_interval_and_span(monkeypatch):
    params = VCPParams()
    monkeypatch.setattr(vcp_scanner_module, "LOOKBACK_DAYS", 80)
    monkeypatch.setattr(vcp_scanner_module, "EXCLUDE_DAYS_FOR_PEAKS", 0)
    monkeypatch.setattr(vcp_scanner_module, "GROUP_DAYS", 3)
    monkeypatch.setattr(vcp_scanner_module, "MIN_PEAKS_COUNT", 3)
    monkeypatch.setattr(vcp_scanner_module, "MAX_PEAKS_COUNT", 5)
    monkeypatch.setattr(vcp_scanner_module, "PEAKS_FROM_GROUPS", 5)
    monkeypatch.setattr(vcp_scanner_module, "PCT_BASELINE", 0.90)
    monkeypatch.setattr(vcp_scanner_module, "MERGE_WITHIN_DAYS", 20)
    monkeypatch.setattr(vcp_scanner_module, "FLEXIBLE_MIN_INTERVAL", 10)
    monkeypatch.setattr(vcp_scanner_module, "FLEXIBLE_MAX_INTERVAL", 120)

    merged_too_few = [70.0] * 80
    for idx, value in ((5, 100.0), (8, 98.0), (30, 96.0)):
        merged_too_few[idx] = value
    assert VcpScannerService.calculate_flexible_peaks(pl.DataFrame({"close": merged_too_few}), 60, params)[0] is None

    merge_keeps_later_peak = [70.0] * 80
    for idx, value in ((5, 98.0), (8, 100.0), (30, 96.0)):
        merge_keeps_later_peak[idx] = value
    assert VcpScannerService.calculate_flexible_peaks(pl.DataFrame({"close": merge_keeps_later_peak}), 60, params)[0] is None

    monkeypatch.setattr(vcp_scanner_module, "MERGE_WITHIN_DAYS", 1)
    monkeypatch.setattr(vcp_scanner_module, "MAX_PEAKS_COUNT", 2)
    too_many = [70.0] * 80
    for idx, value in ((5, 100.0), (25, 98.0), (45, 96.0)):
        too_many[idx] = value
    assert VcpScannerService.calculate_flexible_peaks(pl.DataFrame({"close": too_many}), 60, params)[0] is None

    monkeypatch.setattr(vcp_scanner_module, "MAX_PEAKS_COUNT", 5)
    monkeypatch.setattr(vcp_scanner_module, "FLEXIBLE_MAX_INTERVAL", 20)
    assert VcpScannerService.calculate_flexible_peaks(pl.DataFrame({"close": too_many}), 60, params)[0] is None

    monkeypatch.setattr(vcp_scanner_module, "FLEXIBLE_MAX_INTERVAL", 120)
    short_span = [70.0] * 80
    for idx, value in ((35, 100.0), (41, 98.0), (47, 96.0)):
        short_span[idx] = value
    assert VcpScannerService.calculate_flexible_peaks(pl.DataFrame({"close": short_span}), 60, params)[0] is None

    valid = [70.0] * 80
    for idx, value in ((5, 100.0), (25, 98.0), (45, 96.0)):
        valid[idx] = value
    peaks, message = VcpScannerService.calculate_flexible_peaks(pl.DataFrame({"close": valid}), 60, params)

    assert message == "OK"
    assert [idx for idx, _price in peaks] == [5, 25, 45]


def test_vcp_ma_slope_prepare_and_date_fallbacks(monkeypatch):
    params = VCPParams(enable_ma_slope=True)
    pldf = pl.DataFrame({"SMA50": [100.0, 101.0, 102.0, 103.0, 104.0, 110.0]})
    monkeypatch.setattr(vcp_scanner_module, "MIN_SMA50_SLOPE", 0.001)

    assert VcpScannerService.check_ma_slope(pldf, 5, VCPParams(enable_ma_slope=False)) == (True, 0)
    assert VcpScannerService.check_ma_slope(pldf, 4, params) == (False, 0)
    assert VcpScannerService.check_ma_slope(pl.DataFrame({"SMA50": [0.0, None, 1.0, 2.0, 3.0, 4.0]}), 5, params) == (
        False,
        0,
    )
    assert VcpScannerService.check_ma_slope(pl.DataFrame({"SMA50": [100.0] * 6}), 5, params)[0] is False
    assert VcpScannerService.check_ma_slope(pldf, 5, params)[0] is True

    indexed = pd.DataFrame({"close": [1.0]}, index=pd.date_range("2026-01-01", periods=1, name="datetime"))
    assert "datetime" in VcpScannerService._prepare_price_frame(indexed).columns
    assert VcpScannerService._prepare_price_frame(pd.DataFrame({"close": [1.0]})).height == 1

    bad_dates = pl.DataFrame({"datetime": ["not-a-date", "2026-01-02"]})
    assert VcpScannerService._locate_current_trade_index(bad_dates, "2026-01-02") == 1

    calls = []
    monkeypatch.setattr(
        "domains.scan.vcp_scanner_service.IndicatorService.calculate_indicators",
        lambda frame, include_chart=False: calls.append((frame, include_chart))
        or frame.with_columns(pl.lit(0.01).alias("entangle")),
    )
    ensured = VcpScannerService._ensure_vcp_indicators(pl.DataFrame({"close": [1.0]}))

    assert "entangle" in ensured.columns
    assert calls[0][1] is False


def test_vcp_basic_entry_gates_cover_failures_and_success(monkeypatch):
    pldf = pl.DataFrame(
        {
            "amount": [1e8] * 30,
            "entangle": [0.01] * 30,
            "SMA50": [100.0 + idx for idx in range(30)],
        }
    )
    base_row = {"open": 10.0, "close": 12.0, "SMA50": 11.0, "SMA150": 10.0, "SMA200": 9.0}
    monkeypatch.setattr(vcp_scanner_module, "MIN_SMA50_SLOPE", 0.001)

    assert VcpScannerService._check_basic_entry_gates(pldf, 1, base_row, 90, 90, VCPParams(min_history_days=10), False)
    assert VcpScannerService._check_basic_entry_gates(
        pl.DataFrame({"amount": [1e8] * 220, "entangle": [0.01] * 220, "SMA50": [100.0] * 220}),
        210,
        dict(base_row, SMA200=None),
        90,
        90,
        VCPParams(min_history_days=200),
        False,
    )
    assert VcpScannerService._check_basic_entry_gates(
        pldf, 20, dict(base_row, SMA50=None), 90, 90, VCPParams(min_history_days=1), False
    )
    assert VcpScannerService._check_basic_entry_gates(
        pldf, 20, dict(base_row, SMA50=9.0, SMA150=10.0), 90, 90, VCPParams(min_history_days=1), False
    )
    assert VcpScannerService._check_basic_entry_gates(
        pldf, 20, dict(base_row, open=12.0, close=11.5), 90, 90, VCPParams(min_history_days=1), False
    )
    assert VcpScannerService._check_basic_entry_gates(
        pl.DataFrame({"amount": [1.0] * 30, "entangle": [0.01] * 30, "SMA50": [100.0] * 30}),
        20,
        base_row,
        90,
        90,
        VCPParams(min_history_days=1),
        True,
    )
    assert VcpScannerService._check_basic_entry_gates(
        pl.DataFrame({"amount": [1e8] * 30, "entangle": [0.20] * 30, "SMA50": [100.0] * 30}),
        20,
        base_row,
        90,
        90,
        VCPParams(min_history_days=1, ma_bind_threshold=0.05),
        True,
    )
    assert VcpScannerService._check_basic_entry_gates(pldf, 20, base_row, 90, 70, VCPParams(min_history_days=1), True)
    assert VcpScannerService._check_basic_entry_gates(
        pldf, 20, base_row, 95, 85, VCPParams(min_history_days=1, rps_threshold=80), True
    )
    assert VcpScannerService._check_basic_entry_gates(
        pl.DataFrame({"amount": [1e8] * 30, "entangle": [0.01] * 30, "SMA50": [100.0] * 30}),
        20,
        base_row,
        90,
        90,
        VCPParams(min_history_days=1, enable_ma_slope=True),
        True,
    )

    assert VcpScannerService._check_basic_entry_gates(pldf, 20, base_row, 90, 90, VCPParams(min_history_days=1), True) is None


def _vcp_structure_frame() -> pl.DataFrame:
    return pl.DataFrame(
        {
            "high": [10.0 + (idx % 5) for idx in range(120)],
            "low": [8.0 + (idx % 3) for idx in range(120)],
            "volume": [1000.0 for _idx in range(120)],
            "datetime": pd.date_range("2026-01-01", periods=120, freq="D"),
        }
    )


def _vcp_late_structure_frame() -> pl.DataFrame:
    high = [10.0] * 120
    high[20] = 12.0
    low = [8.0] * 120
    return pl.DataFrame(
        {
            "high": high,
            "low": low,
            "volume": [1000.0 for _idx in range(120)],
            "datetime": pd.date_range("2026-01-01", periods=120, freq="D"),
        }
    )


def test_vcp_structure_rules_cover_later_shape_branches():
    pldf = _vcp_late_structure_frame()
    params = VCPParams(amp_threshold=0.90, high_250_threshold=0.90)
    row = {"High_250": 20.0, "close": 12.0}

    prior_zone = VcpScannerService._build_vcp_zone_context(pldf, 90, [(20, 12.0), (50, 11.8), (80, 11.5)])
    prior_zone["buy_zone"] = pl.DataFrame({"low": [10.0], "high": [10.1]})

    assert VcpScannerService._check_vcp_structure_rules(
        pldf,
        90,
        {"High_250": 0.0, "close": 12.0},
        [(20, 12.0), (50, 11.8), (80, 11.5)],
        params,
        prior_zone,
    )
    assert VcpScannerService._check_vcp_structure_rules(
        pldf,
        90,
        {"High_250": 100.0, "close": 12.0},
        [(20, 12.0), (50, 11.8), (80, 11.5)],
        VCPParams(amp_threshold=0.90, high_250_threshold=0.01),
        prior_zone,
    )
    assert VcpScannerService._check_vcp_structure_rules(
        pldf,
        90,
        {"High_250": 20.0, "close": 8.1},
        [(20, 12.0), (50, 11.8), (80, 11.5)],
        params,
        prior_zone,
    )

    short_peaks = [(20, 12.0), (25, 11.8), (28, 11.5)]
    short_zone = VcpScannerService._build_vcp_zone_context(pldf, 90, short_peaks)
    short_zone["buy_zone"] = pl.DataFrame({"low": [10.0], "high": [10.1]})
    assert VcpScannerService._check_vcp_structure_rules(pldf, 90, row, short_peaks, params, short_zone)

    compact_peaks = [(20, 12.0), (22, 11.8), (24, 11.5)]
    compact_zone = VcpScannerService._build_vcp_zone_context(pldf, 90, compact_peaks)
    compact_zone["buy_zone"] = pl.DataFrame({"low": [10.0], "high": [10.1]})
    compact_params = VCPParams(amp_threshold=0.90, high_250_threshold=0.90)
    vcp_scanner_module.MIN_FIRST_TO_THIRD_DAYS = 1
    vcp_scanner_module.MIN_R1_R2_DAYS = 20
    try:
        assert VcpScannerService._check_vcp_structure_rules(pldf, 90, row, compact_peaks, compact_params, compact_zone)
    finally:
        vcp_scanner_module.MIN_FIRST_TO_THIRD_DAYS = 50
        vcp_scanner_module.MIN_R1_R2_DAYS = 50

    deep_r2_zone = dict(
        prior_zone,
        buy_zone=pl.DataFrame({"low": [10.0], "high": [10.1]}),
        s1=pl.DataFrame({"low": [10.0], "high": [12.0]}),
        s2=pl.DataFrame({"low": [8.0], "high": [11.0]}),
    )
    assert VcpScannerService._check_vcp_structure_rules(pldf, 90, row, [(20, 12.0), (50, 11.8), (80, 11.5)], params, deep_r2_zone)

    high_r1_zone = dict(
        prior_zone,
        buy_zone=pl.DataFrame({"low": [10.0], "high": [10.1]}),
        box_low=8.0,
        box_high=12.0,
        s1=pl.DataFrame({"low": [11.0], "high": [12.0]}),
        s2=pl.DataFrame({"low": [10.0], "high": [11.0]}),
    )
    assert VcpScannerService._check_vcp_structure_rules(pldf, 90, row, [(20, 12.0), (50, 11.8), (80, 11.5)], params, high_r1_zone)

    clean_zone = dict(
        prior_zone,
        s1=pl.DataFrame({"low": [8.0], "high": [12.0]}),
        s2=pl.DataFrame({"low": [8.5], "high": [11.0]}),
    )
    assert VcpScannerService._check_vcp_structure_rules(
        pldf, 90, row, [(20, 12.0), (50, 11.8), (80, 11.5)], params, clean_zone
    ) is None


def test_vcp_scoring_dates_stage_and_default_params_branch():
    score_frame = pl.DataFrame(
        {
            "datetime": pd.date_range("2026-01-01", periods=50, freq="D"),
            "volume": [1000.0] * 50,
            "high": [12.0] * 50,
            "low": [10.0] * 50,
        }
    )
    date_frame = pl.DataFrame(
        {
            "datetime": pl.Series("datetime", [datetime(2026, 1, 1), "2026-01-20"], dtype=pl.Object),
            "volume": [1000.0, 1000.0],
            "high": [12.0, 11.0],
            "low": [10.0, 10.0],
        }
    )
    row = {"close": 11.0, "volume": 1000.0, "vol_ma25": 1000.0, "ATR10": 3.0, "ATR20": 2.0, "ATR60": 1.0}
    buy_zone = pl.DataFrame({"volume": [1000.0]})

    assert VcpScannerService._score_vcp_setup(score_frame, 45, row, 80.0, buy_zone, 10.0)[2]
    assert VcpScannerService._score_vcp_setup(score_frame, 45, row, 80.0, buy_zone, 11.4)[2]
    assert VcpScannerService._score_vcp_setup(score_frame, 45, row, 80.0, buy_zone, 13.0)[2]
    assert VcpScannerService._format_peak_dates(date_frame, [(0, 12.0), (1, 11.0)]) == ["2026-01-01", "2026-01-20"]
    assert VcpScannerService._resolve_buy_stage(16, 15) == "pre_observation"
    assert VcpScannerService._resolve_buy_stage(17, 15) == "observation"
    assert VcpScannerService._resolve_buy_stage(18, 15) == "buy_confirmed"

    frame = pl.DataFrame({"datetime": pd.date_range("2026-01-01", periods=2, freq="D"), "close": [10.0, 11.0]})
    ok, reason, metrics = VcpScannerService.evaluate_conditions(frame, datetime(2026, 2, 1), 90.0, 91.0)
    assert ok is False
    assert reason
    assert metrics == {}
