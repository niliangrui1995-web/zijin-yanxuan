import threading
from types import SimpleNamespace

import infra.market_data.tdx_data_provider as provider_module
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


def test_tdx_provider_market_data_source_status_uses_memory_then_warehouse():
    provider = _make_provider()
    provider.cache_data = {"000001": [1, 2, 3]}
    provider._last_market_data_source_status = {"ok": True, "active_layer": "warehouse", "fallback_reason": ""}
    provider.market_data_warehouse = SimpleNamespace(
        current_status=lambda validate_parquet=False: SimpleNamespace(to_dict=lambda: {"ok": True, "data_status": "ok"})
    )
    provider.tdx_vipdoc = "D:/HT/vipdoc"

    status = provider.get_market_data_source_status()

    assert status["ok"] is True
    assert status["active_layer"] == "warehouse"
    assert status["memory_symbol_count"] == 1
    assert status["memory_row_count"] == 3

    provider.cache_data = {}
    status = provider.get_market_data_source_status()
    assert status["active_layer"] == "parquet_sqlite_warehouse"


def test_tdx_provider_market_data_source_status_handles_warehouse_error_and_unavailable():
    provider = _make_provider()
    provider.cache_data = {}
    provider._last_market_data_source_status = {}
    provider.market_data_warehouse = SimpleNamespace(
        current_status=lambda validate_parquet=False: (_ for _ in ()).throw(RuntimeError("broken"))
    )
    provider.tdx_vipdoc = "D:/HT/vipdoc"

    status = provider.get_market_data_source_status()

    assert status["ok"] is False
    assert status["active_layer"] == "vipdoc_fallback_ready"
    assert status["fallback_reason"] == "warehouse_status_error"

    provider._get_market_data_warehouse = lambda: None
    provider.tdx_vipdoc = ""
    status = provider.get_market_data_source_status()
    assert status["active_layer"] == "unavailable"


def test_tdx_provider_loads_gbbq_code_cache_with_lru_eviction():
    provider = _make_provider()
    provider.tdx_vipdoc = "D:/HT/vipdoc"
    provider._local_gbbq = {}
    provider._local_gbbq_loaded = False
    provider._local_gbbq_lock = threading.RLock()
    provider._local_gbbq_code_cache = {"000000": "old"}
    provider._local_gbbq_code_cache_max = 2
    provider._get_adjustment_service = lambda: SimpleNamespace(
        load_local_gbbq_for_code=lambda code: {code: f"frame-{code}", "000002": "frame-000002"}
    )

    loaded = provider._load_local_gbbq_for_code("000001")

    assert loaded == {"000001": "frame-000001"}
    assert set(provider._local_gbbq_code_cache) == {"000001", "000002"}
    assert provider._load_local_gbbq_for_code("") == {}

    provider._local_gbbq_loaded = True
    provider._local_gbbq = {"all": "loaded"}
    assert provider._get_local_gbbq_for_code("000001") == {"all": "loaded"}


def test_tdx_provider_connects_to_first_valid_server_and_disconnects_invalid_nodes():
    class _Api:
        def __init__(self):
            self.calls = []
            self.disconnects = 0

        def connect(self, ip, port, time_out=5):
            self.calls.append((ip, port, time_out))
            return ip != "bad"

        def get_security_count(self, market):
            return 0 if self.calls[-1][0] == "empty" else 10

        def disconnect(self):
            self.disconnects += 1

    provider = _make_provider()
    provider.server_pool = [("bad", 7709), ("empty", 7709), ("good", 7709)]
    api = _Api()

    selected = provider._connect_api_to_best_server(api, time_out=1)

    assert selected == ("good", 7709)
    assert api.disconnects == 1

    provider.server_pool = []
    assert provider._connect_api_to_best_server(api, allow_unconnected=True) is None


def test_tdx_provider_deprioritizes_servers_and_records_quote_stats():
    provider = _make_provider()
    provider.server_pool = [("a", 1), ("b", 2), ("c", 3)]

    provider._deprioritize_server(("b", 2), "slow")
    assert provider.server_pool == [("a", 1), ("c", 3), ("b", 2)]

    provider._rt_quote_request_history_max = 2
    provider._record_realtime_quote_request(
        {
            "started_at": 1,
            "ended_at": 2,
            "requested_count": 3,
            "batches": [{"signature": "same"}, {"signature": "same"}],
            "triggered_network": True,
            "source_layers": ["cache", "network"],
        }
    )
    provider._record_realtime_quote_request({"started_at": 3, "ended_at": 4, "batches": [{"signature": "other"}]})

    stats = provider.get_quote_request_stats()

    assert stats["history_size"] == 2
    assert stats["network_batch_history_size"] == 3
    assert stats["repeated_batch_signature_count"] == 1
    assert stats["recent_started_at"]


def test_tdx_provider_small_helpers_and_local_gbbq_paths(monkeypatch):
    provider = _make_provider()
    provider._local_gbbq_lock = threading.RLock()
    provider._local_gbbq = {"all": "cached"}
    provider._local_gbbq_loaded = True
    provider._local_gbbq_code_cache = {"000001": "one"}
    provider._local_gbbq_code_cache_max = 2

    assert provider_module._iso_from_timestamp(object()) == ""
    assert provider_module._iso_from_timestamp(0) == ""
    assert provider._load_local_gbbq() == {"all": "cached"}

    provider._local_gbbq_loaded = False
    provider.tdx_vipdoc = ""
    assert provider._ensure_local_gbbq_loaded() == {"all": "cached"}
    assert provider._get_local_gbbq_for_code("000001") == {"all": "cached"}

    loaded_calls = []
    provider.tdx_vipdoc = "D:/HT/vipdoc"
    provider._get_adjustment_service = lambda: SimpleNamespace(
        load_local_gbbq=lambda force=False: loaded_calls.append(force) or {"fresh": force},
        load_local_gbbq_for_code=lambda code: {code: f"frame-{code}"},
        get_market_code=lambda code: ("market", code),
    )

    assert provider._load_local_gbbq(force=True) == {"fresh": True}
    assert provider._ensure_local_gbbq_loaded() == {"fresh": True}
    assert loaded_calls == [True]
    assert provider._get_market_code("000001") == ("market", "000001")

    provider._local_gbbq_loaded = False
    provider._local_gbbq_code_cache = {"000001": "one"}
    assert provider._load_local_gbbq_for_code("000001") == {"000001": "one"}

    monkeypatch.setattr(provider_module.MarketCalendar, "now", lambda _market: SimpleNamespace(hour=9, minute=29))
    assert provider._is_before_930_today() is True
    monkeypatch.setattr(provider_module.MarketCalendar, "now", lambda _market: SimpleNamespace(hour=15, minute=0))
    assert provider._is_after_1500_today() is True


def test_tdx_provider_status_and_cache_helpers_handle_fallbacks(monkeypatch):
    provider = _make_provider()

    class _BadFrame:
        def __len__(self):
            raise TypeError("bad frame")

    provider.cache_data = {"bad": _BadFrame()}
    provider._last_market_data_source_status = {"ok": False}
    provider.market_data_warehouse = None
    provider.tdx_vipdoc = "D:/HT/vipdoc"
    status = provider.get_market_data_source_status()
    assert status["memory_row_count"] == 0
    assert status["active_layer"] == "memory_cache"

    monkeypatch.setattr(provider_module, "downcast_memory", lambda target, logger=None: setattr(target, "downcasted", True))
    provider._downcast_memory()
    assert provider.downcasted is True


def test_tdx_provider_connect_api_failure_paths():
    class _DisconnectRaisesApi:
        def __init__(self):
            self.disconnects = 0

        @staticmethod
        def connect(ip, port, time_out=5):
            return True

        @staticmethod
        def get_security_count(market):
            return 0

        def disconnect(self):
            self.disconnects += 1
            raise RuntimeError("disconnect failed")

    class _ConnectRaisesApi:
        @staticmethod
        def connect(ip, port, time_out=5):
            raise TimeoutError("timeout")

        @staticmethod
        def disconnect():
            raise RuntimeError("disconnect failed")

    provider = _make_provider()

    provider.server_pool = [("empty", 7709)]
    api = _DisconnectRaisesApi()
    try:
        provider._connect_api_to_best_server(api)
    except ConnectionError as exc:
        assert "pytdx" in str(exc)
    assert api.disconnects == 1

    provider.server_pool = [("timeout", 7709)]
    try:
        provider._connect_api_to_best_server(_ConnectRaisesApi())
    except ConnectionError as exc:
        assert "pytdx" in str(exc)

    provider.server_pool = []
    try:
        provider._connect_api_to_best_server(_ConnectRaisesApi())
    except ConnectionError as exc:
        assert "pytdx" in str(exc)

    provider.server_pool = [("only", 7709)]
    provider._deprioritize_server(("only", 7709), "single")
    assert provider.server_pool == [("only", 7709)]
    provider.server_pool = [("a", 1), ("b", 2)]
    provider._deprioritize_server(("missing", 3), "missing")
    assert provider.server_pool == [("a", 1), ("b", 2)]


def test_tdx_provider_realtime_and_history_facade_methods():
    provider = _make_provider()
    calls = []

    realtime = SimpleNamespace(
        archive_runtime=lambda runtime: calls.append(("archive", runtime)),
        ensure_runtime=lambda: "runtime",
        reset_runtime=lambda reason, log_warning=True, penalize_server=True: calls.append(
            ("reset", reason, log_warning, penalize_server)
        ),
        register_success=lambda: calls.append(("success",)),
        enter_cooldown=lambda reason, cooldown_sec=None: calls.append(("cooldown", reason, cooldown_sec)),
        register_failure=lambda reason: calls.append(("failure", reason)),
        submit_request=lambda params_list, timeout_sec: ("submit", params_list, timeout_sec),
        get_runtime_stats=lambda: {"ok": True},
        protect_against_thread_anomaly=lambda count, threshold=None: count > (threshold or 0),
    )
    provider._get_realtime_quote_provider = lambda: realtime
    provider._get_local_gbbq_for_code = lambda code: {"gbbq": code}
    provider._get_adjustment_service = lambda: SimpleNamespace(
        apply_forward_adjustment=lambda api, market, code, df, local_gbbq=None: (api, market, code, df, local_gbbq)
    )
    provider._get_local_history_provider = lambda: SimpleNamespace(
        fetch_standard_data=lambda api, code, count=None: (api, code, count)
    )
    provider.TdxHq_API = lambda **kwargs: ("api", kwargs)
    provider.server_pool = []
    provider.thread_local = threading.local()

    assert provider._create_api_client()[1]["auto_retry"] is False
    assert provider._ensure_realtime_runtime() == "runtime"
    provider._archive_realtime_runtime("old")
    provider._reset_realtime_runtime("bad", log_warning=False, penalize_server=False)
    provider._register_realtime_success()
    provider.enter_realtime_cooldown("cool", cooldown_sec=7)
    provider._enter_realtime_cooldown("cool2")
    provider._register_realtime_failure("failed")
    assert provider._submit_realtime_quote_request([{"code": "000001"}], 3) == ("submit", [{"code": "000001"}], 3)
    assert provider.get_realtime_runtime_stats() == {"ok": True}
    assert provider.protect_against_thread_anomaly(5, threshold=4) is True
    assert provider._get_thread_api()[0] == "api"
    assert provider._apply_forward_adjustment("api", 0, "000001", "df") == (
        "api",
        0,
        "000001",
        "df",
        {"gbbq": "000001"},
    )
    assert provider._fetch_standard_data("api", "000001", count=88) == ("api", "000001", 88)
    assert ("archive", "old") in calls
    assert ("failure", "failed") in calls


def test_tdx_provider_quote_stats_initializes_missing_runtime_fields():
    provider = TdxDataProvider.__new__(TdxDataProvider)

    assert provider.get_quote_request_stats()["history_size"] == 0

    provider._rt_quote_request_history_max = 2
    provider._record_realtime_quote_request({"started_at": 1, "batches": [{"signature": "old"}]})
    provider._record_realtime_quote_request({"started_at": 2, "batches": [{"signature": "middle"}]})
    provider._record_realtime_quote_request({"started_at": 3, "batches": [{"signature": "new"}]})

    stats = provider.get_quote_request_stats()

    assert stats["history_size"] == 2
    assert stats["recent_batches"] == [{"signature": "new"}]
