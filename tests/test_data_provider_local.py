import pandas as pd
import pytest

from vcp.data_provider_local import apply_forward_adjustment


def test_apply_forward_adjustment_handles_integer_volume_columns():
    local_gbbq = {
        "000001": pd.DataFrame(
            [
                {
                    "datetime": "20260410",
                    "songgu_qianzongguben": 1.0,
                    "hongli_panqianliutong": 0.0,
                }
            ]
        )
    }
    bars = pd.DataFrame(
        {
            "datetime": pd.to_datetime(["2026-04-08", "2026-04-09", "2026-04-10"]),
            "open": [10.0, 10.2, 10.5],
            "high": [10.3, 10.4, 10.6],
            "low": [9.9, 10.0, 10.1],
            "close": [10.1, 10.3, 10.5],
            "vol": [90, 95, 100],
            "volume": [100, 120, 140],
        }
    ).set_index("datetime")

    adjusted = apply_forward_adjustment(None, 0, "000001", bars, local_gbbq)

    assert list(adjusted["volume"]) == pytest.approx([110.0, 132.0, 140.0])
    assert list(adjusted["vol"]) == pytest.approx([99.0, 104.5, 100.0])
    assert str(adjusted["volume"].dtype) == "float64"
    assert str(adjusted["vol"].dtype) == "float64"
