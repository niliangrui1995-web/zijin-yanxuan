import threading
from pathlib import Path

import pandas as pd

from vcp.data_provider_history_mixin import TdxDataProviderHistoryMixin


class _DummyProvider(TdxDataProviderHistoryMixin):
    def __init__(self, local_df=None):
        self.cache_data = {}
        self.cache_lock = threading.Lock()
        self.tdx_vipdoc = "D:\\HT\\vipdoc"
        self._local_df = local_df
        self.fetch_calls = 0

    def _fetch_from_local_tdx(self, code):
        self.fetch_calls += 1
        return self._local_df


def test_get_data_falls_back_to_local_tdx_when_runtime_cache_is_empty():
    df = pd.DataFrame(
        {
            "open": [10.0, 10.5],
            "high": [10.8, 10.9],
            "low": [9.9, 10.2],
            "close": [10.6, 10.7],
            "amount": [1000.0, 1100.0],
            "volume": [100, 120],
        },
        index=pd.to_datetime(["2026-04-15", "2026-04-16"]),
    )

    provider = _DummyProvider(local_df=df)

    result = provider.get_data("000001")

    assert result is df
    assert provider.fetch_calls == 1
    assert provider.cache_data["000001"] is df


def test_get_data_prefers_existing_runtime_cache_without_local_reload():
    cached = pd.DataFrame(
        {
            "open": [20.0],
            "high": [21.0],
            "low": [19.5],
            "close": [20.8],
            "amount": [2200.0],
            "volume": [180],
        },
        index=pd.to_datetime(["2026-04-16"]),
    )

    provider = _DummyProvider(local_df=None)
    provider.cache_data["600000"] = cached

    result = provider.get_data("600000")

    assert result is cached
    assert provider.fetch_calls == 0


def _build_tnf_record(code: str, name: str) -> bytes:
    record = bytearray(TdxDataProviderHistoryMixin._TNF_RECORD_SIZE)
    record[19:23] = b"FZDM"
    record[TdxDataProviderHistoryMixin._TNF_CODE_OFFSET : TdxDataProviderHistoryMixin._TNF_CODE_OFFSET + 6] = (
        code.encode("ascii")
    )
    name_bytes = name.encode("gbk")
    start = TdxDataProviderHistoryMixin._TNF_NAME_OFFSET
    record[start : start + len(name_bytes)] = name_bytes
    return bytes(record)


def test_parse_tnf_name_file_extracts_a_share_names(tmp_path):
    tnf_path = Path(tmp_path) / "szs.tnf"
    tnf_path.write_bytes(
        b"".join(
            [
                _build_tnf_record("300093", "*ST金刚"),
                _build_tnf_record("300236", "上海新阳"),
                _build_tnf_record("002709", "天赐材料"),
            ]
        )
    )

    parsed = TdxDataProviderHistoryMixin._parse_tnf_name_file(str(tnf_path))

    assert parsed["300093"] == "*ST金刚"
    assert parsed["300236"] == "上海新阳"
    assert parsed["002709"] == "天赐材料"
