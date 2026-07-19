# -*- coding: utf-8 -*-
from core.global_store import global_store
from core.quote_dispatcher import publish_rt_quotes


def test_global_store_preserves_finance_fields_across_quote_events():
    publish_rt_quotes(
        {
            "000001": {
                "close": 10.0,
                "last_close": 9.8,
                "zongguben": 1_000_000_000,
            }
        }
    )

    publish_rt_quotes(
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


def test_global_store_ignores_missing_quote_snapshot():
    global_store.merge_quotes({"000001": {"close": 10.0}})

    global_store.merge_quotes(None)

    assert global_store.get_latest_quotes() == {"000001": {"close": 10.0}}
