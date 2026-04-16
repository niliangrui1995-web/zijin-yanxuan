# -*- coding: utf-8 -*-
from core.event_bus import event_bus
from core.global_store import global_store


def test_global_store_preserves_finance_fields_across_quote_events():
    event_bus.sig_rt_quotes.emit(
        {
            "000001": {
                "close": 10.0,
                "last_close": 9.8,
                "zongguben": 1_000_000_000,
            }
        }
    )

    event_bus.sig_rt_quotes.emit(
        {
            "000001": {
                "close": 10.5,
                "last_close": 10.0,
            }
        }
    )

    latest = global_store.get_latest_quotes()["000001"]
    assert latest["close"] == 10.5
    assert latest["last_close"] == 10.0
    assert latest["zongguben"] == 1_000_000_000
