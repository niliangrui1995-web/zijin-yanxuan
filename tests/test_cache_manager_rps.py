import json

import pandas as pd
import polars as pl

from core import cache_manager as cache_manager_module
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


class FakeFrame:
    def __init__(self, rows: int, last_date: str = "2026-04-10"):
        self._rows = rows
        self.index = pd.DatetimeIndex([pd.Timestamp(last_date)])

    def __len__(self):
        return self._rows


def test_try_load_rps_from_disk_rebuilds_json_cache_when_missing(tmp_path, monkeypatch):
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
    monkeypatch.setattr(
        cache_manager_module,
        "read_active_rps_bundle",
        lambda path: (path, json.loads(cache_path.read_text(encoding="utf-8")) if cache_path.is_file() else None),
    )

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


def test_try_load_rps_from_disk_rebuilds_incomplete_json_cache(tmp_path, monkeypatch):
    cache_path = tmp_path / "vcp_rps_precomputed.json"
    cache_path.write_text(
        json.dumps(
            {
                "date": "20260420",
                "rps120": {"600000": 95.0},
                "rps250": {"600000": 92.0},
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    engine = DummyEngine()
    provider = DummyProvider({f"{i:06d}": FakeFrame(260) for i in range(600000, 601200)})
    manager = CacheManager()
    manager.rps_path = str(cache_path)
    monkeypatch.setattr(
        cache_manager_module,
        "read_active_rps_bundle",
        lambda path: (path, json.loads(cache_path.read_text(encoding="utf-8"))),
    )

    manager.try_load_rps_from_disk(engine, data_provider=provider)

    assert engine.calls == [([f"{i:06d}" for i in range(600000, 601200)], "20260410", "20260410")]
    assert engine.bundle is not None
    assert engine.bundle["date"] == "20260410"

    payload = json.loads(cache_path.read_text(encoding="utf-8"))
    assert payload["date"] == "20260410"
    assert len(payload["rps250"]) == 2


def test_infer_latest_rps_trade_date_supports_polars_datetime_column():
    frame = pl.DataFrame(
        {
            "datetime": [
                "2026-04-15 00:00:00",
                "2026-04-16 00:00:00",
                "2026-04-17 00:00:00",
            ],
            "close": [1.0, 2.0, 3.0],
        }
    ).with_columns(pl.col("datetime").str.strptime(pl.Datetime, format="%Y-%m-%d %H:%M:%S"))

    assert CacheManager._infer_latest_rps_trade_date({"600000": frame}) == "20260417"
