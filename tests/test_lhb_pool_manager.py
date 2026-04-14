# -*- coding: utf-8 -*-
import pandas as pd

from core.lhb_pool_manager import LhbPoolManager


class _DummyProvider:
    def __init__(self, mapping):
        self._mapping = mapping

    def get_data(self, code):
        return self._mapping.get(code)


def _build_manager(monkeypatch):
    monkeypatch.setattr(LhbPoolManager, "_load", lambda self: None)
    monkeypatch.setattr(LhbPoolManager, "_migrate_old_cache", lambda self: None)
    manager = LhbPoolManager()
    manager._data = {}
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


def test_compute_pool_prioritizes_recent_listing_before_older_buy_point(monkeypatch):
    manager = _build_manager(monkeypatch)
    manager._data = {
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
            "000002": _make_kline(10, list(range(1, 11))),
        }
    )

    pool = manager.compute_pool(data_provider=provider)

    assert [row["代码"] for row in pool] == ["000002", "000001"]
    assert pool[1]["买点"] == "✅"
