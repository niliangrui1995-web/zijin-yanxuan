import struct
import threading
from types import SimpleNamespace

import pandas as pd
import pytest

from vcp import data_provider_realtime_mixin as realtime_mixin_module
from vcp.data_provider_local import apply_forward_adjustment, build_offline_quotes, load_local_tdx_capital_snapshot
from vcp.data_provider_realtime_mixin import TdxDataProviderRealtimeMixin


def _fallback_quote_frame() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "open": [20.0, 20.3],
            "high": [20.4, 20.8],
            "low": [19.9, 20.1],
            "close": [20.2, 20.6],
            "volume": [2000, 2500],
            "amount": [20_000.0, 25_500.0],
        },
        index=pd.to_datetime(["2026-04-09", "2026-04-10"]),
    )


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


def test_realtime_mixin_builds_offline_quotes_from_one_batch_history_read():
    first = pd.DataFrame(
        {
            "open": [10.0, 10.3],
            "high": [10.4, 10.8],
            "low": [9.9, 10.1],
            "close": [10.2, 10.6],
            "volume": [1000, 1500],
            "amount": [10_000.0, 15_500.0],
        },
        index=pd.to_datetime(["2026-04-09", "2026-04-10"]),
    )
    second = first * 2
    batch_calls = []

    provider = TdxDataProviderRealtimeMixin()
    provider.get_data_batch = lambda codes: batch_calls.append(tuple(codes)) or {
        "000001": first,
        "600000": second,
    }
    provider.get_data = lambda _code: (_ for _ in ()).throw(AssertionError("per-symbol history read is forbidden"))

    quotes = provider._build_offline_quotes(["000001", "600000"])

    assert batch_calls == [("000001", "600000")]
    assert quotes["000001"]["close"] == pytest.approx(10.6)
    assert quotes["600000"]["close"] == pytest.approx(21.2)


def test_realtime_mixin_prefers_tail_only_warehouse_quotes(monkeypatch):
    provider = TdxDataProviderRealtimeMixin()
    provider.cache_lock = threading.RLock()
    provider.cache_data = {}
    provider._last_market_data_source_status = {}
    provider.get_data_batch = lambda _codes: (_ for _ in ()).throw(
        AssertionError("warehouse quote hits must not materialize full history frames")
    )
    status = SimpleNamespace(ok=True, to_dict=lambda: {"active_layer": "parquet_sqlite_warehouse"})
    warehouse = object()
    provider._get_market_data_warehouse = lambda: warehouse
    monkeypatch.setattr(
        realtime_mixin_module,
        "read_latest_quotes",
        lambda actual, codes: SimpleNamespace(
            status=status,
            data={code: {"close": 10.5, "last_close": 10.0} for code in codes},
        )
        if actual is warehouse
        else None,
    )

    quotes = provider._build_offline_quotes(["000001", "600000"])

    assert set(quotes) == {"000001", "600000"}
    assert provider._last_market_data_source_status == {"active_layer": "parquet_sqlite_warehouse"}
    assert provider.cache_data == {}


def test_realtime_mixin_falls_back_only_for_missing_warehouse_quotes(monkeypatch):
    provider = TdxDataProviderRealtimeMixin()
    provider.cache_lock = threading.RLock()
    provider.cache_data = {}
    warehouse = object()
    provider._get_market_data_warehouse = lambda: warehouse
    status = SimpleNamespace(ok=True, to_dict=lambda: {"active_layer": "parquet_sqlite_warehouse"})
    monkeypatch.setattr(
        realtime_mixin_module,
        "read_latest_quotes",
        lambda actual, _codes: SimpleNamespace(
            status=status,
            data={"000001": {"close": 10.5, "last_close": 10.0}},
        )
        if actual is warehouse
        else None,
    )
    batch_calls = []
    provider.get_data_batch = lambda codes: batch_calls.append(tuple(codes)) or {"600000": _fallback_quote_frame()}

    quotes = provider._build_offline_quotes(["000001", "600000"])

    assert batch_calls == [("600000",)]
    assert quotes["000001"]["close"] == pytest.approx(10.5)
    assert quotes["600000"]["close"] == pytest.approx(20.6)
    assert provider.cache_data == {}


@pytest.mark.parametrize("failure_point", ["getter", "reader"])
def test_realtime_mixin_contains_warehouse_quote_errors_and_uses_history_fallback(monkeypatch, failure_point):
    provider = TdxDataProviderRealtimeMixin()
    provider.cache_lock = threading.RLock()
    provider.cache_data = {}
    warehouse = object()
    if failure_point == "getter":
        provider._get_market_data_warehouse = lambda: (_ for _ in ()).throw(RuntimeError("manifest locked"))
    else:
        provider._get_market_data_warehouse = lambda: warehouse
        monkeypatch.setattr(
            realtime_mixin_module,
            "read_latest_quotes",
            lambda _warehouse, _codes: (_ for _ in ()).throw(RuntimeError("parquet busy")),
        )
    batch_calls = []
    provider.get_data_batch = lambda codes: batch_calls.append(tuple(codes)) or {"000001": _fallback_quote_frame()}

    quotes = provider._build_offline_quotes(["000001"])

    assert batch_calls == [("000001",)]
    assert quotes["000001"]["close"] == pytest.approx(20.6)


def test_realtime_mixin_reads_native_cache_tail_without_dataframe_conversion():
    class _NativeFrame:
        height = 2

        def __init__(self):
            self.rows = [
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

        def row(self, index, *, named=False):
            assert named is True
            return dict(self.rows[index])

        def to_pandas(self):
            raise AssertionError("whole-frame conversion is forbidden")

    frame = _NativeFrame()
    provider = TdxDataProviderRealtimeMixin()
    provider.cache_lock = threading.RLock()
    provider.cache_data = {"000001": frame}
    provider.get_data_batch = lambda _codes: (_ for _ in ()).throw(
        AssertionError("cached native frame must not enter the batch conversion path")
    )

    quotes = provider._build_offline_quotes(["000001"])

    assert quotes["000001"]["close"] == pytest.approx(10.6)
    assert quotes["000001"]["last_close"] == pytest.approx(10.2)
    assert quotes["000001"]["date"] == "2026-04-10"
    assert provider.cache_data["000001"] is frame


def test_realtime_mixin_reads_current_cache_mapping_after_atomic_swap_lock():
    old_frame = pd.DataFrame(
        {
            "open": [9.0],
            "high": [9.5],
            "low": [8.8],
            "close": [9.2],
            "volume": [1000],
            "amount": [9_200.0],
        },
        index=pd.to_datetime(["2026-04-09"]),
    )
    current_frame = pd.DataFrame(
        {
            "open": [10.0],
            "high": [10.8],
            "low": [9.9],
            "close": [10.6],
            "volume": [1500],
            "amount": [15_500.0],
        },
        index=pd.to_datetime(["2026-04-10"]),
    )
    provider = TdxDataProviderRealtimeMixin()
    provider.cache_data = {"000001": old_frame}

    class _AtomicSwapLock:
        def __enter__(self):
            provider.cache_data = {"000001": current_frame}

        def __exit__(self, _exc_type, _exc, _traceback):
            return False

    provider.cache_lock = _AtomicSwapLock()
    provider.get_data_batch = lambda _codes: (_ for _ in ()).throw(
        AssertionError("current in-memory snapshot should satisfy the quote")
    )

    quotes = provider._build_offline_quotes(["000001"])

    assert quotes["000001"]["close"] == pytest.approx(10.6)
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

    assert snapshot["000001"]["total_shares"] == pytest.approx(19_405_918_700)
    assert snapshot["688129"]["total_shares"] == pytest.approx(120_478_700)
    assert snapshot["000001"]["source"] == "tdx_base"
    assert "300001" not in snapshot
