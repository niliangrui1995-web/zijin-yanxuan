import struct

import pandas as pd
import pytest

from vcp.data_provider_local import apply_forward_adjustment, build_offline_quotes, load_local_tdx_capital_snapshot


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


def test_build_offline_quotes_handles_non_pandas_dataframe():
    class _FakeFrame:
        def __init__(self, rows):
            self._rows = list(rows)

        def __len__(self):
            return len(self._rows)

        def to_dicts(self):
            return list(self._rows)

    fake_rows = [
        {
            "datetime": "2026-04-09",
            "open": 10.0,
            "high": 10.4,
            "low": 9.9,
            "close": 10.2,
            "volume": 1000,
            "amount": 10_000.0,
        },
        {
            "datetime": "2026-04-10",
            "open": 10.3,
            "high": 10.8,
            "low": 10.1,
            "close": 10.6,
            "volume": 1500,
            "amount": 15_500.0,
        },
    ]

    quotes = build_offline_quotes(["000001"], lambda code: _FakeFrame(fake_rows))

    assert quotes["000001"]["open"] == pytest.approx(10.3)
    assert quotes["000001"]["close"] == pytest.approx(10.6)
    assert quotes["000001"]["last_close"] == pytest.approx(10.2)
    assert quotes["000001"]["date"] == "2026-04-10"


def test_load_local_tdx_capital_snapshot_reads_base_dbf(tmp_path):
    hq_cache = tmp_path / "T0002" / "hq_cache"
    hq_cache.mkdir(parents=True)
    base_dbf = hq_cache / "base.dbf"

    fields = [("GPDM", "C", 6, 0), ("ZGB", "N", 14, 2)]
    header_len = 32 + len(fields) * 32 + 1
    record_len = 1 + sum(field[2] for field in fields)
    header = bytearray(32)
    header[0] = 0x03
    header[4:8] = struct.pack("<I", 2)
    header[8:10] = struct.pack("<H", header_len)
    header[10:12] = struct.pack("<H", record_len)

    field_desc = bytearray()
    for name, field_type, length, decimals in fields:
        desc = bytearray(32)
        desc[: len(name)] = name.encode("ascii")
        desc[11] = ord(field_type)
        desc[16] = length
        desc[17] = decimals
        field_desc.extend(desc)

    records = [
        b" " + b"000001" + f"{1940591.87:14.2f}".encode("ascii"),
        b" " + b"688129" + f"{12047.87:14.2f}".encode("ascii"),
    ]
    base_dbf.write_bytes(bytes(header) + bytes(field_desc) + b"\r" + b"".join(records) + b"\x1a")

    snapshot = load_local_tdx_capital_snapshot(["000001", "688129", "300001"], str(tmp_path / "vipdoc"))

    assert snapshot["000001"]["zongguben"] == pytest.approx(19_405_918_700)
    assert snapshot["688129"]["zongguben"] == pytest.approx(120_478_700)
    assert snapshot["000001"]["source"] == "tdx_base"
    assert "300001" not in snapshot
