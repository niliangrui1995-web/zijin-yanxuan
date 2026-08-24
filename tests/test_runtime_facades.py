from __future__ import annotations

import importlib

from app.services import (
    runtime_services,
    scan_engine_facade,
)
from vcp.models import VCPParams


def test_scan_engine_facade_delegates_to_domain_services(monkeypatch):
    calls = []

    monkeypatch.setattr(
        scan_engine_facade.VcpScannerService,
        "calculate_flexible_peaks",
        staticmethod(lambda pldf, curr_idx, params: calls.append(("peaks", curr_idx, params)) or ([(1, 10.0)], "OK")),
    )
    monkeypatch.setattr(
        scan_engine_facade.VcpScannerService,
        "check_ma_slope",
        staticmethod(lambda pldf, curr_idx, params: calls.append(("slope", curr_idx, params)) or (True, 0.01)),
    )
    monkeypatch.setattr(
        scan_engine_facade.VcpScannerService,
        "evaluate_conditions",
        staticmethod(
            lambda df, day, rps120, rps250, history, params, skip_red: (
                calls.append(("eval", day, rps120, rps250, history, params, skip_red)) or (True, "OK", {"score": 88})
            )
        ),
    )
    monkeypatch.setattr(
        scan_engine_facade.BreakoutMonitorService,
        "rt_quick_check",
        staticmethod(lambda quote, entry: calls.append(("quick", quote, entry)) or (True, "near", 91)),
    )
    monkeypatch.setattr(
        scan_engine_facade,
        "batch_get_finance_info",
        lambda codes: calls.append(("finance", tuple(codes))) or {"000001": {"pe": 10}},
    )

    params = VCPParams(enable_ma_slope=False)

    assert scan_engine_facade.VCPEngine._calculate_flexible_peaks("df", 9, params) == ([(1, 10.0)], "OK")
    assert scan_engine_facade.VCPEngine._check_ma_slope("df", 8, params) == (True, 0.01)
    assert scan_engine_facade.VCPEngine.evaluate_conditions("df", "2026-04-20", 91, 92, {"x": 1}, params, True) == (
        True,
        "OK",
        {"score": 88},
    )
    assert scan_engine_facade.VCPEngine.rt_quick_check({"close": 10}, {"box_high": 11}) == (True, "near", 91)
    assert scan_engine_facade.VCPEngine.batch_get_finance_info(["000001"]) == {"000001": {"pe": 10}}

    assert calls == [
        ("peaks", 9, params),
        ("slope", 8, params),
        ("eval", "2026-04-20", 91, 92, {"x": 1}, params, True),
        ("quick", {"close": 10}, {"box_high": 11}),
        ("finance", ("000001",)),
    ]


def test_scan_engine_facade_covers_lazy_rps_and_remaining_delegates(monkeypatch):
    calls = []

    class FakeRpsService:
        def __init__(self, **_accelerators):
            self.bundle = None

        def set_precomputed_rps(self, cache_date, rps120, rps250):
            self.bundle = {"date": cache_date, "rps120": rps120, "rps250": rps250}

        def get_precomputed_rps(self):
            return self.bundle

        @staticmethod
        def build_prices_matrix(data_dict, min_start, end_ts=None, **_accelerators):
            calls.append(("prices", data_dict, min_start, end_ts))
            return {"prices": data_dict}

        def build_rps_matrix(self, data_dict, start_date, end_date):
            calls.append(("rps", data_dict, start_date, end_date))
            return {"rps120": {"000001": 91}}

    monkeypatch.setattr(scan_engine_facade, "RpsService", FakeRpsService)
    monkeypatch.setattr(scan_engine_facade.VCPEngine, "_instance", None)
    monkeypatch.setattr(
        scan_engine_facade.IndicatorService,
        "calculate_indicators",
        staticmethod(lambda df, include_chart=True: calls.append(("indicators", df, include_chart)) or {"df": df}),
    )
    monkeypatch.setattr(
        scan_engine_facade.BreakoutMonitorService,
        "precompute_ready_pool",
        staticmethod(lambda *args, **kwargs: calls.append(("pool", args, kwargs)) or {"000001": {"score": 88}}),
    )
    monkeypatch.setattr(
        scan_engine_facade.BreakoutMonitorService,
        "estimate_full_day_volume",
        staticmethod(lambda current_volume: calls.append(("volume", current_volume)) or current_volume * 2),
    )
    monkeypatch.setattr(
        scan_engine_facade,
        "batch_check_market_cap",
        lambda codes, close_prices=None: calls.append(("cap", codes, close_prices)) or {"000001": 100},
    )
    monkeypatch.setattr(
        scan_engine_facade,
        "batch_check_institution",
        lambda codes: calls.append(("inst", codes)) or {"000001": {"has_institution": True}},
    )

    engine = scan_engine_facade.VCPEngine.get_instance()
    engine.__init__()
    engine._rps_service = None

    engine.set_precomputed_rps("2026-04-20", {"000001": 91}, {"000001": 92})

    assert engine.get_precomputed_rps()["date"] == "2026-04-20"
    assert scan_engine_facade.VCPEngine.calculate_indicators("df", include_chart=False) == {"df": "df"}
    assert scan_engine_facade.VCPEngine._build_prices_matrix({"000001": "frame"}, "start", "end") == {
        "prices": {"000001": "frame"}
    }
    assert engine.build_rps_matrix({"000001": "frame"}, "2026-04-01", "2026-04-20") == {"rps120": {"000001": 91}}
    assert scan_engine_facade.VCPEngine.batch_check_market_cap(["000001"], {"000001": 10}) == {"000001": 100}
    assert scan_engine_facade.VCPEngine.batch_check_institution(["000001"]) == {
        "000001": {"has_institution": True}
    }
    assert scan_engine_facade.VCPEngine.precompute_ready_pool(
        {"000001": "frame"},
        {"000001": 91},
        {"000001": 92},
        params="params",
        sector_manager="sector",
        progress_callback="progress",
    ) == {"000001": {"score": 88}}
    assert scan_engine_facade.VCPEngine._estimate_full_day_volume(100) == 200
    assert ("volume", 100) in calls


def test_legacy_vcp_engine_import_points_at_scan_facade():
    legacy_module = importlib.import_module("vcp.engine")

    assert legacy_module is not scan_engine_facade
    assert legacy_module.VCPEngine is scan_engine_facade.VCPEngine


def test_runtime_services_load_local_tdx_capital_snapshot_delegates(monkeypatch):
    monkeypatch.setattr(
        runtime_services,
        "_load_snapshot",
        lambda codes, tdx_vipdoc: {"codes": list(codes), "tdx_vipdoc": tdx_vipdoc},
    )

    assert runtime_services.load_local_tdx_capital_snapshot(["000001"], "D:/vipdoc") == {
        "codes": ["000001"],
        "tdx_vipdoc": "D:/vipdoc",
    }
