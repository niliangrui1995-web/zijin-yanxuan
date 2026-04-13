import json

import pandas as pd

from core.cache_manager import CacheManager


class DummyEngine:
    def __init__(self):
        self.calls = []
        self.bundle = None

    def build_rps_matrix(self, all_data, start_date, end_date):
        self.calls.append((sorted(all_data.keys()), start_date, end_date))
        return {
            end_date: {
                "rps120": {"600000": 95.0, "000001": 88.0},
                "rps250": {"600000": 92.0, "000001": 85.0},
            }
        }

    def set_precomputed_rps(self, cache_date, rps120, rps250):
        self.bundle = {
            "date": cache_date,
            "rps120": rps120,
            "rps250": rps250,
        }


class DummyProvider:
    def __init__(self, cache_data):
        self.cache_data = cache_data


def test_try_load_rps_from_disk_rebuilds_json_cache_when_missing(tmp_path):
    cache_path = tmp_path / "vcp_rps_precomputed.json"
    legacy_path = tmp_path / "vcp_rps_precomputed.pkl"
    legacy_path.write_bytes(b"legacy")

    dates = pd.date_range("2026-01-01", periods=72, freq="B")
    df_a = pd.DataFrame({"close": range(len(dates))}, index=dates)
    df_b = pd.DataFrame({"close": range(len(dates) - 1)}, index=dates[:-1])

    engine = DummyEngine()
    provider = DummyProvider({"600000": df_a, "000001": df_b})
    manager = CacheManager()
    manager.rps_path = str(cache_path)

    manager.try_load_rps_from_disk(engine, data_provider=provider)

    assert engine.bundle is not None
    assert engine.bundle["date"] == "20260410"
    assert engine.calls == [(["000001", "600000"], "20260410", "20260410")]
    assert cache_path.exists()
    assert not legacy_path.exists()

    payload = json.loads(cache_path.read_text(encoding="utf-8"))
    assert payload["date"] == "20260410"
    assert payload["rps120"]["600000"] == 95.0
    assert payload["rps250"]["000001"] == 85.0
