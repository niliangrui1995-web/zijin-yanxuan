# -*- coding: utf-8 -*-
import pandas as pd
import pytest

import core.lhb_pool_manager as lhb_pool_module
from core.lhb_pool_manager import LhbPoolManager


class _DummyProvider:
    def __init__(self, mapping, cache_data=None):
        self._mapping = mapping
        self.cache_data = cache_data or {}

    def get_data(self, code):
        return self._mapping.get(code)


class _DummyEngine:
    def __init__(self, bundle):
        self._bundle = bundle

    def get_precomputed_rps(self):
        return self._bundle


def _build_manager(monkeypatch):
    monkeypatch.setattr(LhbPoolManager, "_load", lambda self: None)
    monkeypatch.setattr(LhbPoolManager, "_migrate_old_cache", lambda self: None)
    monkeypatch.setattr(
        LhbPoolManager,
        "_stock_universe_provider",
        staticmethod(lambda: {"000001", "000002", "603256", "603738", "605589"}),
    )
    manager = LhbPoolManager()
    manager._data = {}
    manager._day_meta = {}
    manager._last_auto_fetch_date = ""
    return manager


def _make_kline(rows: int, closes: list[float], last_open: float | None = None):
    opens = list(closes)
    if rows and last_open is not None:
        opens[-1] = last_open
    return pd.DataFrame(
        {
            "date": pd.date_range("2026-03-01", periods=rows, freq="D").strftime("%Y-%m-%d"),
            "open": opens,
            "close": closes,
        }
    )


def test_compute_pool_allows_negative_foreign_net_if_core_conditions_match(monkeypatch):
    manager = _build_manager(monkeypatch)
    manager._data = {
        "20260413": [
            {
                "代码": "603256",
                "名称": "宏和科技",
                "上榜日期": "20260413",
                "上榜净买额(万)": 12500,
                "机构净买(万)": 7152.03,
                "外资净买(万)": -2003.99,
                "涨幅%": 3.21,
            }
        ]
    }

    pool = manager.compute_pool()

    assert [row["代码"] for row in pool] == ["603256"]
    assert pool[0]["最近上榜"] == "20260413"
    assert float(pool[0]["外资净买(万)"]) < 0


def test_compute_pool_allows_zero_institution_net_buy(monkeypatch):
    manager = _build_manager(monkeypatch)
    manager._data = {
        "20260508": [
            {
                "代码": "603738",
                "名称": "泰晶科技",
                "上榜日期": "20260508",
                "上榜净买额(万)": 18613.18,
                "机构净买(万)": 0.0,
                "外资净买(万)": -2354.84,
                "涨幅%": 9.99,
            },
            {
                "代码": "600000",
                "名称": "负机构净买",
                "上榜日期": "20260508",
                "上榜净买额(万)": 1200,
                "机构净买(万)": -1.0,
                "外资净买(万)": 0,
                "涨幅%": 1.2,
            },
        ]
    }

    pool = manager.compute_pool()

    assert [row["代码"] for row in pool] == ["603738"]
    assert pool[0]["机构净买(万)"] == 0.0


def test_compute_pool_filters_to_ai_industry_chain_pool(monkeypatch):
    manager = _build_manager(monkeypatch)
    monkeypatch.setattr(
        LhbPoolManager,
        "_stock_universe_provider",
        staticmethod(lambda: {"603738"}),
    )
    manager._data = {
        "20260508": [
            {
                "代码": "603738",
                "名称": "泰晶科技",
                "上榜日期": "20260508",
                "上榜净买额(万)": 18613.18,
                "机构净买(万)": 0.0,
                "外资净买(万)": -2354.84,
                "涨幅%": 9.99,
            },
            {
                "代码": "600000",
                "名称": "非AI股票",
                "上榜日期": "20260508",
                "上榜净买额(万)": 8800,
                "机构净买(万)": 3000,
                "外资净买(万)": 1200,
                "涨幅%": 5.2,
            },
        ]
    }

    pool = manager.compute_pool()

    assert [row["代码"] for row in pool] == ["603738"]


def test_compute_pool_prioritizes_buy_points_by_pct(monkeypatch):
    manager = _build_manager(monkeypatch)
    manager._data = {
        "20260415": [
            {
                "代码": "605589",
                "名称": "新低涨幅买点",
                "上榜日期": "20260415",
                "上榜净买额(万)": 7600,
                "机构净买(万)": 1800,
                "外资净买(万)": 300,
                "涨幅%": 2.0,
            }
        ],
        "20260414": [
            {
                "代码": "000002",
                "名称": "新近上榜",
                "上榜日期": "20260414",
                "上榜净买额(万)": 8600,
                "机构净买(万)": 2600,
                "外资净买(万)": -1500,
                "涨幅%": 1.5,
            }
        ],
        "20260413": [
            {
                "代码": "000001",
                "名称": "旧买点股",
                "上榜日期": "20260413",
                "上榜净买额(万)": 9800,
                "机构净买(万)": 3200,
                "外资净买(万)": 500,
                "涨幅%": 9.9,
            }
        ],
    }

    provider = _DummyProvider(
        {
            "000001": _make_kline(20, list(range(1, 21)), last_open=14),
            "605589": _make_kline(20, list(range(1, 21)), last_open=14),
            "000002": _make_kline(10, list(range(1, 11))),
        }
    )

    pool = manager.compute_pool(data_provider=provider)

    assert [row["代码"] for row in pool] == ["000001", "605589", "000002"]
    assert [row["买点"] for row in pool[:2]] == ["触发", "触发"]


def test_lhb_buy_point_uses_local_kline_without_realtime_quote_fetch(monkeypatch):
    manager = _build_manager(monkeypatch)
    manager._data = {
        "20260415": [
            {
                "代码": "000001",
                "名称": "本地买点",
                "上榜日期": "20260415",
                "上榜净买额(万)": 7600,
                "机构净买(万)": 1800,
                "涨幅%": 2.0,
            }
        ]
    }

    class _NoRealtimeFetchProvider(_DummyProvider):
        def fetch_realtime_quotes_batch(self, codes):
            raise AssertionError(f"龙虎榜买点不应触发实时补价: {codes}")

    provider = _NoRealtimeFetchProvider(
        {
            "000001": _make_kline(20, list(range(1, 21)), last_open=14),
        }
    )

    pool = manager.compute_pool(data_provider=provider)

    assert [row["代码"] for row in pool] == ["000001"]
    assert pool[0]["买点"] == "触发"
    assert pool[0]["_history_20"] == [float(value) for value in range(1, 21)]


def test_compute_pool_accepts_dataframe_like_kline_without_empty_attr(monkeypatch):
    pl = pytest.importorskip("polars")
    manager = _build_manager(monkeypatch)
    monkeypatch.setattr(
        LhbPoolManager,
        "_stock_universe_provider",
        staticmethod(lambda: {"300302"}),
    )
    manager._data = {
        "20260415": [
            {
                "代码": "300302",
                "名称": "同飞股份",
                "上榜日期": "20260415",
                "上榜净买额(万)": 7600,
                "机构净买(万)": 1800,
                "涨幅%": 2.0,
            }
        ]
    }
    kline = pl.DataFrame(
        {
            "date": pd.date_range("2026-03-01", periods=20, freq="D").strftime("%Y-%m-%d"),
            "open": [*range(1, 20), 14],
            "close": list(range(1, 21)),
        }
    )
    assert not hasattr(kline, "empty")

    pool = manager.compute_pool(data_provider=_DummyProvider({"300302": kline}))

    assert [row["代码"] for row in pool] == ["300302"]
    assert pool[0]["_history_20"] == [float(value) for value in range(1, 21)]
    assert pool[0]["_history_date"] == "2026-03-20"
    assert pool[0]["买点"] == "触发"


def test_add_day_records_cache_meta(monkeypatch):
    manager = _build_manager(monkeypatch)

    manager.add_day("20260407", [{"代码": "605589"}])

    assert manager.get_cached_record_count("20260407") == 1
    assert manager.get_day_meta("20260407") == {
        "record_count": 1,
        "source_count": 1,
        "last_probe_ref_date": "",
        "probe_status": "unverified",
    }


def test_get_dates_pending_validation_marks_unverified_and_broken_meta(monkeypatch):
    manager = _build_manager(monkeypatch)
    manager.add_day("20260407", [{"代码": "605589"}])

    assert manager.get_dates_pending_validation(["20260407"], "20260414") == ["20260407"]

    manager.mark_day_probe("20260407", source_count=1, validation_ref_date="20260414", status="ok")
    assert manager.get_dates_pending_validation(["20260407"], "20260414") == []

    manager._day_meta["20260407"]["record_count"] = 99
    assert manager.get_dates_pending_validation(["20260407"], "20260414") == ["20260407"]


def test_compute_pool_keeps_missing_rps_when_rps_cache_coverage_is_abnormal(monkeypatch):
    manager = _build_manager(monkeypatch)
    manager._data = {
        "20260417": [
            {
                "代码": "000001",
                "名称": "已有RPS",
                "上榜日期": "20260417",
                "上榜净买额(万)": 8600,
                "机构净买(万)": 2600,
                "外资净买(万)": 300,
                "涨幅%": 3.2,
            },
            {
                "代码": "000002",
                "名称": "缺失RPS",
                "上榜日期": "20260417",
                "上榜净买额(万)": 9100,
                "机构净买(万)": 3100,
                "外资净买(万)": 500,
                "涨幅%": 4.5,
            },
        ]
    }

    provider = _DummyProvider(
        {
            "000001": _make_kline(20, list(range(1, 21))),
            "000002": _make_kline(20, list(range(2, 22))),
        },
        cache_data={f"{i:06d}": [0] * 250 for i in range(1200)},
    )
    engine = _DummyEngine(
        {
            "date": "20260420",
            "rps120": {"000001": 90},
            "rps250": {"000001": 91},
        }
    )

    pool = manager.compute_pool(data_provider=provider, engine=engine)

    assert [row["代码"] for row in pool] == ["000002", "000001"]


def test_lhb_pool_manager_reuses_json_payload_by_file_signature(monkeypatch, tmp_path):
    cache_path = tmp_path / "lhb_pool_30d.json"
    cache_path.write_text(
        """
{
  "version": 2,
  "last_auto_fetch_date": "20260508",
  "daily_data": {
    "20260508": [
      {"代码": "603738", "名称": "泰晶科技"}
    ]
  },
  "day_meta": {}
}
""".strip(),
        encoding="utf-8",
    )
    LhbPoolManager._loaded_payload_cache.clear()
    load_calls = []
    real_load = lhb_pool_module.json.load

    def counting_load(handle):
        load_calls.append(handle.name)
        return real_load(handle)

    monkeypatch.setattr(lhb_pool_module.json, "load", counting_load)

    first = object.__new__(LhbPoolManager)
    first._cache_path = str(cache_path)
    first._legacy_pool_cache_path = str(tmp_path / "lhb_pool_20d.json")
    first._data = {}
    first._day_meta = {}
    first._last_auto_fetch_date = ""
    first._load()
    first._data["20260508"][0]["代码"] = "MUTATED"

    second = object.__new__(LhbPoolManager)
    second._cache_path = str(cache_path)
    second._legacy_pool_cache_path = str(tmp_path / "lhb_pool_20d.json")
    second._data = {}
    second._day_meta = {}
    second._last_auto_fetch_date = ""
    second._load()

    assert len(load_calls) == 1
    assert second._data["20260508"][0]["代码"] == "603738"
