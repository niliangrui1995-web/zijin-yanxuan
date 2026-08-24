# -*- coding: utf-8 -*-
import core.global_store as global_store_module
from core.global_store import global_store
from core.quote_dispatcher import publish_rt_quotes


def test_lazy_global_store_introspection_only_uses_cached_store(monkeypatch):
    lazy_store = global_store_module._LazyGlobalStore()
    monkeypatch.setattr(global_store_module, "_global_store", None)

    assert "cached_method" not in dir(lazy_store)

    class CachedStore:
        def cached_method(self):
            return None

    monkeypatch.setattr(global_store_module, "_global_store", CachedStore())

    assert "cached_method" in dir(lazy_store)
    assert bool(lazy_store) is True
    assert repr(lazy_store) == "<LazyGlobalStore>"


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


def test_global_store_reuses_unmodified_frozen_quote_entries():
    global_store.reset_runtime_state()
    try:
        first = global_store.merge_quotes(
            {
                "000001": {"close": 10.0, "last_close": 9.8},
                "000002": {"close": 20.0, "last_close": 19.8},
            }
        )

        second = global_store.merge_quotes({"000001": {"close": 10.5}})

        assert second["000001"] is not first["000001"]
        assert second["000002"] is first["000002"]
        assert first["000001"]["close"] == 10.0
        assert second["000001"]["close"] == 10.5
    finally:
        global_store.reset_runtime_state()


def test_global_store_quarantines_unknown_incoming_leaves_without_rebuilding_existing_quotes():
    class MutableLeaf:
        pass

    global_store.reset_runtime_state()
    try:
        first = global_store.merge_quotes({"000001": {"close": 10.0}, "000002": {"close": 20.0}})

        second = global_store.merge_quotes({"000001": {"unsafe": MutableLeaf(), "close": 10.5}})

        assert "unsafe" not in second["000001"]
        assert second["000002"] is first["000002"]
    finally:
        global_store.reset_runtime_state()
