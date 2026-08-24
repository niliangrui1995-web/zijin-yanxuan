# -*- coding: utf-8 -*-
from __future__ import annotations

import threading

from app.services.asian_market_cache_service import AsianQuoteCacheStore, get_realtime_quote, merge_realtime_quote


def test_asian_quote_cache_returns_isolated_quote_snapshots():
    cache = AsianQuoteCacheStore()
    cache["2330.TW"] = {"close": 1000.0, "source": "cache"}

    quote = get_realtime_quote(cache, "2330.TW")
    quote["close"] = 1.0

    assert cache["2330.TW"] == {"close": 1000.0, "source": "cache"}


def test_asian_quote_cache_merges_concurrent_updates_without_exposing_partial_entries():
    cache = AsianQuoteCacheStore()
    cache["2330.TW"] = {"close": 1000.0, "source": "cache"}
    barrier = threading.Barrier(3)
    failures = []

    def writer(offset: int) -> None:
        try:
            barrier.wait()
            for value in range(200):
                merge_realtime_quote(cache, "2330.TW", {"close": float(offset + value)})
        except BaseException as exc:  # noqa: BLE001 - test records worker failures for the assertion below.
            failures.append(exc)

    def reader() -> None:
        try:
            barrier.wait()
            for _ in range(400):
                quote = get_realtime_quote(cache, "2330.TW")
                assert quote["source"] == "cache"
                assert isinstance(quote["close"], float)
                snapshot = cache.snapshot()
                assert snapshot["2330.TW"]["source"] == "cache"
        except BaseException as exc:  # noqa: BLE001 - test records worker failures for the assertion below.
            failures.append(exc)

    threads = [threading.Thread(target=writer, args=(index * 1_000,)) for index in range(2)]
    threads.append(threading.Thread(target=reader))
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=5)

    assert all(not thread.is_alive() for thread in threads)
    assert failures == []
