import threading

from vcp.data_provider import TdxDataProvider


def _make_provider():
    provider = TdxDataProvider.__new__(TdxDataProvider)
    provider._rt_quote_cache = {}
    provider._rt_quote_time = {}
    provider._rt_quote_lock = threading.Lock()
    provider._rt_runtime_lock = threading.RLock()
    provider._rt_runtime = None
    provider._rt_runtime_consecutive_failures = 0
    provider._rt_runtime_last_success_at = 0.0
    provider._rt_runtime_reconnect_archived = 0
    provider._rt_runtime_cooldown_until = 0.0
    provider._rt_runtime_last_error = ""
    provider.cache_data = {}
    provider._rt_quote_cache_ttl_sec = 30
    provider._rt_quote_cache_max_entries = 2
    return provider


def test_compact_runtime_caches_removes_expired_and_oversized_entries():
    provider = _make_provider()
    provider.cache_data = {"000001": object(), "000002": object()}
    provider._rt_quote_cache = {
        "stale": {"close": 1},
        "oldest": {"close": 2},
        "fresh": {"close": 3},
    }
    provider._rt_quote_time = {
        "stale": 10,
        "oldest": 90,
        "fresh": 110,
    }

    stats = provider.compact_runtime_caches(now=120)

    assert stats["removed_rt_quotes"] == 1
    assert stats["rt_quote_cache_size"] == 2
    assert stats["history_symbol_count"] == 2
    assert "stale" not in provider._rt_quote_cache
    assert set(provider._rt_quote_cache) == {"oldest", "fresh"}


def test_compact_runtime_caches_trims_oldest_when_cache_exceeds_budget():
    provider = _make_provider()
    provider._rt_quote_cache_ttl_sec = 999
    provider._rt_quote_cache = {
        "a": {"close": 1},
        "b": {"close": 2},
        "c": {"close": 3},
    }
    provider._rt_quote_time = {
        "a": 10,
        "b": 20,
        "c": 30,
    }

    stats = provider.compact_runtime_caches(now=31)

    assert stats["removed_rt_quotes"] == 1
    assert stats["rt_quote_cache_size"] == 2
    assert "a" not in provider._rt_quote_cache
    assert set(provider._rt_quote_cache) == {"b", "c"}
