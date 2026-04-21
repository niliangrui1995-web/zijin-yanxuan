from __future__ import annotations

import pandas as pd

from domains.scan import IndicatorService, RpsService
from vcp.engine import VCPEngine


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
