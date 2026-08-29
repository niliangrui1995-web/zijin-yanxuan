# -*- coding: utf-8 -*-
from __future__ import annotations

import datetime as dt
import json

from app.services import asian_market_cache_service as cache_service
from ui.services import asian_market_runtime_service as runtime_service


def test_latest_trade_dates_by_ticker_preserves_same_market_lag(tmp_path):
    cache_path = tmp_path / "asian-klines.json"
    cache_path.write_text(
        json.dumps(
            {
                "stocks": [
                    {"ticker": "2330.TW", "klines": [{"date": "2026-07-16"}]},
                    {"ticker": "2317.TW", "klines": [{"date": "2026-07-15"}]},
                ]
            }
        ),
        encoding="utf-8",
    )
    cache_service.clear_asian_ticker_index_cache()

    assert cache_service.load_latest_trade_dates_by_ticker(str(cache_path)) == {
        "2330.TW": dt.date(2026, 7, 16),
        "2317.TW": dt.date(2026, 7, 15),
    }


def test_cache_staleness_reports_lagging_ticker_when_market_max_is_fresh(monkeypatch):
    expected_date = dt.date(2026, 7, 16)
    service = runtime_service.AsianMarketRuntimeService()
    service.set_target_codes(["2330.TW", "2317.TW", "2454.TW"])

    monkeypatch.setattr(
        runtime_service.MarketCalendar,
        "now",
        lambda _market: dt.datetime(2026, 7, 16, 17, 0),
    )
    monkeypatch.setattr(runtime_service, "cache_mtime", lambda _path: 1.0)
    monkeypatch.setattr(
        runtime_service.MarketCalendar,
        "from_timestamp",
        lambda *_args: dt.datetime(2026, 7, 16, 17, 1),
    )
    monkeypatch.setattr(runtime_service, "load_latest_trade_dates", lambda _path: {"TW": expected_date})
    monkeypatch.setattr(
        runtime_service,
        "load_latest_trade_dates_by_ticker",
        lambda _path: {
            "2330.TW": expected_date,
            "2317.TW": dt.date(2026, 7, 15),
        },
    )
    monkeypatch.setattr(service, "_expected_latest_trade_dates", lambda: {"TW": expected_date})

    staleness = service.cache_staleness()

    assert staleness["stale"] is True
    assert staleness["stale_by_mtime"] is False
    assert staleness["stale_by_trade_date"] is True
    assert staleness["stale_markets"] == []
    assert staleness["stale_tickers"] == [
        ("2317.TW", dt.date(2026, 7, 15), expected_date),
        ("2454.TW", None, expected_date),
    ]
