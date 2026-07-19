# -*- coding: utf-8 -*-

from __future__ import annotations

import gc
import weakref
from types import SimpleNamespace

import pytest

from ui.tabs import base_stock_refresh as refresh


class _Owner:
    def __init__(self, rows=None):
        self._runtime_cleanup_done = False
        self._active_model_ref = None
        self._deferred_quote_refresh = False
        self._f5_cache_snapshot_apply = False
        self.applied = []
        self.after_calls = 0
        self.model = SimpleNamespace(row_data=list(rows or []), headers=["代码", "现价"])
        self.data_provider = SimpleNamespace(tdx_vipdoc="D:/HT/vipdoc")

    def _resolve_active_quote_model(self):
        return self.model

    @staticmethod
    def _normalize_quote_code(code):
        return str(code or "").strip()

    @staticmethod
    def _is_blank_quote_value(value, zero_is_blank=True):
        return value in (None, "", "--") or bool(zero_is_blank and value == 0)

    def _apply_quote_snapshot(self, payload):
        self.applied.append(dict(payload or {}))
        return {"changed_rows": len(payload or {})}

    def _after_market_caps_updated(self):
        self.after_calls += 1

    def isVisible(self):
        return True

    def window(self):
        return None


@pytest.fixture(autouse=True)
def _reset_shared_state():
    refresh.CacheSnapshotApplyQueue._scheduled = False
    refresh.CacheSnapshotApplyQueue._pending = {}
    refresh.MarketCapRefreshBatcher._scheduled = False
    refresh.MarketCapRefreshBatcher._pending_codes = set()
    refresh.MarketCapRefreshBatcher._waiters = {}
    refresh._FINANCE_CACHE_PATH = None
    refresh._FINANCE_CACHE_SIGNATURE = None
    refresh._FINANCE_CACHE_PAYLOAD = None
    yield
    refresh.CacheSnapshotApplyQueue._scheduled = False
    refresh.CacheSnapshotApplyQueue._pending = {}
    refresh.MarketCapRefreshBatcher._scheduled = False
    refresh.MarketCapRefreshBatcher._pending_codes = set()
    refresh.MarketCapRefreshBatcher._waiters = {}


def test_runtime_guards_visibility_and_latest_snapshot_errors(monkeypatch):
    assert refresh._is_qt_object_deleted(None)
    monkeypatch.setattr(refresh, "sip", SimpleNamespace(isdeleted=lambda obj: True))
    assert refresh._is_qt_object_deleted(object())
    monkeypatch.setattr(
        refresh,
        "sip",
        SimpleNamespace(isdeleted=lambda obj: (_ for _ in ()).throw(RuntimeError("deleted"))),
    )
    assert not refresh._is_qt_object_deleted(object())

    owner = _Owner()
    assert refresh._is_owner_runtime_active(owner)
    owner._runtime_cleanup_done = True
    assert not refresh._is_owner_runtime_active(owner)

    owner = _Owner()
    assert refresh._should_prime_local_snapshot(owner, async_local=False)
    owner.isVisible = lambda: False
    assert not refresh._should_prime_local_snapshot(owner, async_local=True)
    owner.isVisible = lambda: (_ for _ in ()).throw(RuntimeError("gone"))
    assert not refresh._should_prime_local_snapshot(owner, async_local=True)
    assert refresh._should_prime_local_snapshot(SimpleNamespace(), async_local=True)

    from core.global_store import global_store

    monkeypatch.setattr(global_store, "get_latest_quotes", lambda: (_ for _ in ()).throw(ValueError("bad")))
    assert refresh._latest_quote_snapshot() == {}
    assert refresh._current_finance_cache_file() == str(refresh.FINANCE_CACHE_FILE)


def test_code_collection_and_missing_finance_cover_normalization_branches(monkeypatch):
    owner = _Owner()
    assert refresh.collect_table_codes(owner, SimpleNamespace()) == []
    model = SimpleNamespace(
        row_data=[
            {"代码": "1", "现价": "", "涨幅%": 0, "_zongguben": 0},
            {"代码": "", "现价": 1, "涨幅%": 1},
            {"代码": "X.US", "现价": "", "涨幅%": ""},
            {"代码": "000001", "现价": 10, "涨幅%": None, "_zongguben": 0},
            {"代码": "000001", "现价": 10, "涨幅%": 1, "_zongguben": 3},
        ]
    )
    monkeypatch.setattr(refresh, "is_a_share_code", lambda code: str(code).isdigit())
    assert refresh.collect_table_codes(owner, model) == ["000001", "X.US"]
    assert refresh.collect_quote_refresh_codes(owner, model) == ["000001"]
    assert refresh.collect_quote_refresh_codes(owner, model, force=True) == ["000001", "X.US"]

    monkeypatch.setattr(refresh, "_latest_quote_snapshot", lambda: {"000001": {"zongguben": 0}})
    assert refresh.collect_missing_finance_codes(owner, SimpleNamespace()) == []
    assert refresh.collect_missing_finance_codes(owner, model) == ["000001"]


def test_finance_snapshot_cache_and_loader_paths(monkeypatch):
    shared_loader = refresh._load_shared_finance_cache_payload
    assert refresh.load_cached_finance_snapshot(["bad"], tdx_vipdoc="x") == {}
    monkeypatch.setattr(refresh, "is_a_share_code", lambda code: str(code).isdigit())
    monkeypatch.setattr(
        refresh,
        "load_local_tdx_capital_snapshot",
        lambda codes, root: (_ for _ in ()).throw(OSError("missing")),
    )
    monkeypatch.setattr(
        refresh,
        "_load_shared_finance_cache_payload",
        lambda path: {
            "000001": {"info": {"zongguben": 10, "name": "Ping"}},
            "000002": {"info": {}},
        },
    )
    result = refresh.load_cached_finance_snapshot(["000001", "000002"], tdx_vipdoc="x")
    assert result["000001"]["source"] == "finance_cache"
    assert "000002" not in result

    monkeypatch.setattr(
        refresh,
        "_load_shared_finance_cache_payload",
        lambda path: (_ for _ in ()).throw(OSError("bad")),
    )
    assert refresh.load_cached_finance_snapshot(["000001"]) == {}
    assert refresh._finance_entry_has_share_capital({"zongguben": 1})
    assert not refresh._finance_entry_has_share_capital(None)

    monkeypatch.setattr(refresh, "_get_finance_cache_signature", lambda path: None)
    assert shared_loader("missing") == {}
    assert shared_loader("missing") == {}
    monkeypatch.setattr(refresh, "_get_finance_cache_signature", lambda path: (1, 2))
    monkeypatch.setattr(refresh, "load_json_file", lambda path: {"code": {"info": {}}})
    assert "code" in shared_loader("present")
    assert "code" in shared_loader("present")

    owner = _Owner()
    owner._runtime_cleanup_done = True
    assert refresh._resolve_cached_finance_loader(owner)(["000001"]) == {}
    owner._runtime_cleanup_done = False
    owner._load_cached_finance_snapshot = lambda codes: {codes[0]: {"zongguben": 2}}
    assert refresh._resolve_cached_finance_loader(owner)(["000001"])["000001"]["zongguben"] == 2
    del owner._load_cached_finance_snapshot
    loader = refresh._resolve_cached_finance_loader(owner)
    owner._runtime_cleanup_done = True
    assert loader(["000001"]) == {}


def test_local_quote_target_build_and_sync_prime_paths(monkeypatch):
    monkeypatch.setattr(refresh, "is_a_share_code", lambda code: str(code).isdigit())
    owner = _Owner([{"代码": "000001"}, {"代码": "000002"}, {"代码": "X.US"}])
    targets = refresh._collect_local_quote_targets(
        owner,
        owner.model,
        {"000001": {"close": 10, "market_cap": 1}, "000002": {"close": 0, "zongguben": 1}},
    )
    assert targets == ["000002"]

    owner._runtime_cleanup_done = True
    assert refresh._build_local_quote_payload(owner, ["000001"]) == {}

    class _BrokenOwner(_Owner):
        @property
        def data_provider(self):
            raise RuntimeError("gone")

        @data_provider.setter
        def data_provider(self, value):
            pass

    assert refresh._build_local_quote_payload(_BrokenOwner(), ["000001"]) == {}

    owner = _Owner([{"代码": "000001"}])
    monkeypatch.setattr(refresh, "build_offline_quotes", lambda provider, codes: {"000001": {"close": 10}})
    owner._load_cached_finance_snapshot = lambda codes: (_ for _ in ()).throw(ValueError("bad"))
    monkeypatch.setattr(refresh, "enrich_quotes_with_finance", lambda quotes, finance: {**quotes, "finance": finance})
    assert refresh._build_local_quote_payload(owner, ["000001"])["finance"] == {}

    owner._runtime_cleanup_done = True
    assert refresh.prime_local_quote_snapshot(owner) == {}
    owner._runtime_cleanup_done = False
    assert refresh.prime_local_quote_snapshot(owner, SimpleNamespace()) == {}
    monkeypatch.setattr(refresh, "_collect_local_quote_targets", lambda *args: [])
    assert refresh.prime_local_quote_snapshot(owner, owner.model) == {}
    monkeypatch.setattr(refresh, "_collect_local_quote_targets", lambda *args: ["000001"])
    monkeypatch.setattr(refresh, "_build_local_quote_payload", lambda *args, **kwargs: {})
    assert refresh.prime_local_quote_snapshot(owner, owner.model) == {}
    monkeypatch.setattr(
        refresh,
        "_build_local_quote_payload",
        lambda *args, **kwargs: {"000001": {"close": 10}},
    )
    monkeypatch.setattr(refresh, "publish_rt_quotes", lambda payload, source: {**payload, "source": source})
    published = refresh.prime_local_quote_snapshot(owner, owner.model)
    assert published["source"].endswith(".local_cache")


def test_local_quote_payload_reuses_current_prices_when_only_finance_is_missing(monkeypatch):
    owner = _Owner([{"代码": "000001"}, {"代码": "000002"}])
    latest_quotes = {
        "000001": {"close": 11.0, "last_close": 10.0},
        "000002": {"market_cap": 2_000_000_000},
    }
    offline_calls = []

    monkeypatch.setattr(refresh, "_latest_quote_snapshot", lambda: latest_quotes)
    monkeypatch.setattr(
        refresh,
        "build_offline_quotes",
        lambda _provider, codes: offline_calls.append(list(codes))
        or {"000002": {"close": 20.0, "last_close": 19.0}},
    )
    owner._load_cached_finance_snapshot = lambda codes: {
        code: {"zongguben": 100_000_000} for code in codes
    }

    payload = refresh._build_local_quote_payload(
        owner,
        ["000001", "000002"],
        latest_quotes=latest_quotes,
    )

    assert offline_calls == [["000002"]]
    assert payload["000001"]["close"] == 11.0
    assert payload["000001"]["market_cap"] == 1_100_000_000
    assert payload["000002"]["close"] == 20.0


def test_async_local_prime_captures_real_callbacks_and_guards(monkeypatch):
    owner = _Owner([{"代码": "000001"}])
    monkeypatch.setattr(refresh, "_collect_local_quote_targets", lambda *args: ["000001"])
    monkeypatch.setattr(
        refresh.QCoreApplication,
        "instance",
        staticmethod(lambda: SimpleNamespace(closingDown=lambda: False)),
    )
    from app.services import ui_task_service

    manager = SimpleNamespace(is_active_task=lambda key: False)
    monkeypatch.setattr(ui_task_service, "background_job_runner", manager)
    captured = {}
    monkeypatch.setattr(
        refresh,
        "_run_owner_background",
        lambda owner, runner, name, fn, **kwargs: captured.update(fn=fn, **kwargs),
    )
    assert refresh.prime_local_quote_snapshot_async(owner, owner.model)

    monkeypatch.setattr(
        refresh,
        "_build_local_quote_payload",
        lambda *args, **kwargs: {"000001": {"close": 10}},
    )
    assert captured["fn"](None)["000001"]["close"] == 10
    published = []
    monkeypatch.setattr(refresh, "publish_rt_quotes", lambda payload, source: published.append(source) or payload)
    captured["on_success"]({"000001": {"close": 10}})
    assert owner.applied[-1]["000001"]["close"] == 10
    assert owner.after_calls == 1
    captured["on_success"]({})
    captured["on_error"]("")
    captured["on_error"]("bad")

    owner._runtime_cleanup_done = True
    assert captured["fn"](None) == {}
    captured["on_success"]({"000001": {"close": 11}})

    owner._runtime_cleanup_done = False
    manager.is_active_task = lambda key: True
    assert refresh.prime_local_quote_snapshot_async(owner, owner.model)

    monkeypatch.setattr(refresh.QCoreApplication, "instance", staticmethod(lambda: None))
    assert not refresh.prime_local_quote_snapshot_async(owner, owner.model)


def test_defer_signatures_filtering_and_apply_metrics(monkeypatch):
    owner = _Owner()
    assert not refresh._should_defer_cache_snapshot_apply(owner, async_local=False)
    owner.isVisible = lambda: False
    assert not refresh._should_defer_cache_snapshot_apply(owner, async_local=True)
    owner.isVisible = lambda: (_ for _ in ()).throw(TypeError("gone"))
    assert not refresh._should_defer_cache_snapshot_apply(owner, async_local=True)
    owner.isVisible = None
    assert not refresh._should_defer_cache_snapshot_apply(owner, async_local=True)
    owner._f5_cache_snapshot_apply = True
    monkeypatch.setattr(
        refresh.QCoreApplication,
        "instance",
        staticmethod(lambda: SimpleNamespace(closingDown=lambda: False)),
    )
    assert refresh._should_defer_cache_snapshot_apply(owner, async_local=True)

    custom = object()
    stable = refresh._stable_signature_value({"b": {2, 1}, "a": [custom, None]})
    assert stable[0][0] == "a" and "object" in stable[0][1][0]

    owner = _Owner()
    assert refresh._quote_code_candidates(owner, "1") == ["1", "000001"]
    owner._normalize_quote_code = lambda code: "abc.hk"
    assert refresh._quote_code_candidates(owner, "") == ["abc.hk", "ABC.HK"]
    owner._normalize_quote_code = lambda code: (_ for _ in ()).throw(ValueError("bad"))
    assert refresh._quote_code_candidates(owner, "") == []

    owner = _Owner()
    model = SimpleNamespace(
        row_data=[None, {"code": "1", "现价": 10}, {"代码": "1", "现价": 11}],
        _headers=["现价"],
    )
    signatures = refresh._row_signature_by_code(owner, model, {"000001": {"close": 10}})
    assert "000001" in signatures
    owner._active_model_ref = None
    owner._resolve_active_quote_model = lambda: model
    payload = {"000001": {"nested": [1, 2]}}
    first = refresh._payload_signature_for_codes(owner, payload)
    assert first["000001"][1] is not None
    assert refresh._filter_unchanged_cache_snapshot_payload(owner, {}) == {}
    owner._cache_snapshot_applied_signatures = "bad"
    assert refresh._filter_unchanged_cache_snapshot_payload(owner, payload) == payload
    refresh._remember_cache_snapshot_payload(owner, payload)
    assert isinstance(owner._cache_snapshot_applied_signatures, dict)
    assert refresh._filter_unchanged_cache_snapshot_payload(owner, payload) == {}
    refresh._remember_cache_snapshot_payload(owner, {})

    assert refresh._extract_changed_rows({"changed_rows": "2"}) == 2
    assert refresh._extract_changed_rows({"changed_rows": None}) is None
    assert refresh._extract_changed_rows("3") == 3
    assert refresh._extract_changed_rows(object()) is None
    monkeypatch.setenv("VCP_CACHE_SNAPSHOT_APPLY_CHUNK_SIZE", "bad")
    assert refresh._cache_snapshot_apply_chunk_size() == refresh._QUOTE_SNAPSHOT_APPLY_CHUNK_SIZE
    assert refresh._split_payload_chunk({"a": 1, "b": 2}, 1) == ({"a": 1}, {"b": 2})

    metrics = []
    monkeypatch.setattr(refresh, "record_metric", lambda *args, **kwargs: metrics.append((args, kwargs)))
    refresh._apply_cache_snapshot_payload(owner, {"000001": {"close": 11}}, signal="test")
    assert metrics[-1][1]["tags"]["changed_rows"] == "1"
    refresh._apply_cache_snapshot_payload(owner, {"000001": {"close": 11}}, signal="test")
    assert len(metrics) == 1


def test_cache_snapshot_queue_merges_chunks_and_handles_shutdown(monkeypatch):
    scheduled = []
    app = SimpleNamespace(closingDown=lambda: False)
    monkeypatch.setattr(refresh.QCoreApplication, "instance", staticmethod(lambda: app))
    monkeypatch.setattr(refresh.QTimer, "singleShot", lambda delay, callback: scheduled.append(callback))
    monkeypatch.setattr(refresh, "_cache_snapshot_apply_chunk_size", lambda: 1)
    owner = _Owner()

    assert not refresh.CacheSnapshotApplyQueue.enqueue(owner, {}, async_local=True)
    assert refresh.CacheSnapshotApplyQueue.enqueue(owner, {"a": {"close": 1}}, async_local=True)
    assert refresh.CacheSnapshotApplyQueue.enqueue(owner, {"b": {"close": 2}}, async_local=True)
    assert len(scheduled) == 1
    scheduled.pop(0)()
    assert owner.applied == [{"a": {"close": 1}}]
    assert len(scheduled) == 1
    scheduled.pop(0)()
    assert owner.applied[-1] == {"b": {"close": 2}}
    refresh.CacheSnapshotApplyQueue.flush_one()

    refresh.CacheSnapshotApplyQueue._pending[id(owner)] = (weakref.ref(owner), {"c": {}}, False)
    monkeypatch.setattr(refresh.QCoreApplication, "instance", staticmethod(lambda: None))
    refresh.CacheSnapshotApplyQueue._schedule()
    assert not refresh.CacheSnapshotApplyQueue._pending

    refresh.CacheSnapshotApplyQueue._pending[id(owner)] = (weakref.ref(owner), {"c": {}}, False)
    refresh.CacheSnapshotApplyQueue.flush_one()
    assert not refresh.CacheSnapshotApplyQueue._pending


def test_market_cap_batcher_waiter_edge_paths_and_flush_callbacks(monkeypatch):
    owner = _Owner()
    refresh.MarketCapRefreshBatcher.enqueue(owner, [])
    assert owner.after_calls == 1

    monkeypatch.setattr(refresh.QCoreApplication, "instance", staticmethod(lambda: None))
    refresh.MarketCapRefreshBatcher.enqueue(owner, ["000001"])
    assert not refresh.MarketCapRefreshBatcher._scheduled

    dead = _Owner()
    dead_id = id(dead)
    refresh.MarketCapRefreshBatcher._waiters[dead_id] = (weakref.ref(dead), {"000001"})
    del dead
    gc.collect()
    refresh.MarketCapRefreshBatcher._prune_waiters()
    assert dead_id not in refresh.MarketCapRefreshBatcher._waiters

    owner._runtime_cleanup_done = True
    refresh.MarketCapRefreshBatcher._notify_waiters({id(owner): (weakref.ref(owner), {"000001"})}, {"000001": {}})
    owner._runtime_cleanup_done = False
    refresh.MarketCapRefreshBatcher._notify_waiters({id(owner): (weakref.ref(owner), {"000001"})}, {})
    assert owner.after_calls == 2

    owner._load_cached_finance_snapshot = lambda codes: (_ for _ in ()).throw(OSError("bad"))
    finance, missing = refresh.MarketCapRefreshBatcher._load_waiter_finance_snapshot(
        {id(owner): (weakref.ref(owner), {"000001"})}, ["000001"]
    )
    assert finance == {} and missing == ["000001"]

    monkeypatch.setattr(
        refresh.QCoreApplication,
        "instance",
        staticmethod(lambda: SimpleNamespace(closingDown=lambda: False)),
    )
    from app.services import ui_task_service

    callbacks = {}
    manager = SimpleNamespace(
        is_active_task=lambda key: False,
        run_in_background=lambda fn, **kwargs: callbacks.update(fn=fn, **kwargs),
    )
    monkeypatch.setattr(ui_task_service, "background_job_runner", manager)
    refresh.MarketCapRefreshBatcher._pending_codes = {"000001"}
    refresh.MarketCapRefreshBatcher._waiters = {id(owner): (weakref.ref(owner), {"000001"})}
    refresh.MarketCapRefreshBatcher.flush()
    monkeypatch.setattr(refresh, "batch_get_finance_info", lambda codes: {"000001": {"zongguben": 1}})
    assert callbacks["fn"]()["000001"]["zongguben"] == 1
    monkeypatch.setattr(refresh, "build_finance_quote_payload", lambda data: {"000001": {"_zongguben": 1}})
    monkeypatch.setattr(refresh, "publish_rt_quotes", lambda payload, source: payload)
    callbacks["on_success"]({"000001": {"zongguben": 1}})
    callbacks["on_error"]("bad")


def test_submit_refresh_prepare_snapshot_subscribe_and_replay_paths(monkeypatch):
    owner = _Owner([{"代码": "000001"}])
    owner.data_provider.fetch_realtime_quotes_batch = lambda codes: {"000001": {"close": 10}}
    owner._publish_quote_payload = lambda quotes, source: quotes
    captured = {}
    monkeypatch.setattr(
        refresh,
        "_run_owner_background",
        lambda owner, runner, name, fn, **kwargs: captured.update(fn=fn, **kwargs),
    )
    refresh._submit_owner_quote_refresh(owner, object(), "task", ["000001"])
    assert captured["fn"](None)["000001"]["close"] == 10
    captured["on_success"]({})
    captured["on_success"]({"000001": {"close": 10}})
    captured["on_error"]("")
    captured["on_error"]("bad")

    assert refresh._prepare_table_refresh(owner, SimpleNamespace(), False) is None
    monkeypatch.setattr(refresh, "collect_table_codes", lambda *args: [])
    assert refresh._prepare_table_refresh(owner, owner.model, False) is None

    monkeypatch.setattr(refresh, "collect_table_codes", lambda *args: ["000001"])
    monkeypatch.setattr(refresh, "prime_local_quote_snapshot", lambda *args: {"ok": 1})
    assert refresh._prepare_table_refresh(owner, owner.model, False)[1] == ["000001"]
    owner.isVisible = lambda: False
    assert refresh._prepare_table_refresh(owner, owner.model, True)[1] == ["000001"]

    monkeypatch.setattr(refresh, "_latest_quote_snapshot", lambda: {})
    refresh._refresh_table_from_latest_snapshot_impl(owner, owner.model, async_local=False)
    monkeypatch.setattr(refresh, "_latest_quote_snapshot", lambda: {"000001": {"close": 10}})
    monkeypatch.setattr(refresh.CacheSnapshotApplyQueue, "enqueue", lambda *args, **kwargs: False)
    refresh.refresh_table_from_latest_snapshot(owner, owner.model, async_local=False)
    assert owner.applied

    from app.services import ui_event_service

    fake_signal = SimpleNamespace(
        connect=lambda callback: None,
        disconnect=lambda callback: (_ for _ in ()).throw(TypeError("no")),
    )
    monkeypatch.setattr(ui_event_service, "domain_events", SimpleNamespace(sig_rt_quotes=fake_signal))
    owner._quote_signal_connected = True
    owner._on_rt_quotes_direct = lambda quotes: None
    owner.model.update_quotes = lambda quotes: None
    from core.global_store import global_store

    monkeypatch.setattr(global_store, "get_latest_quotes", lambda: {"000001": {"close": 10}})
    owner.isVisible = lambda: False
    refresh.subscribe_global_quotes(owner, owner.model)
    assert owner._deferred_quote_refresh

    owner.isVisible = lambda: False
    refresh.on_rt_quotes_direct(owner, {"000001": {}})
    assert owner._deferred_quote_refresh
    owner.isVisible = lambda: True
    refresh.on_rt_quotes_direct(owner, {"000001": {"close": 11}})
    assert owner.applied[-1]["000001"]["close"] == 11

    owner._deferred_quote_refresh = False
    refresh.replay_deferred_quotes(owner)
    owner._deferred_quote_refresh = True
    monkeypatch.setattr(global_store, "get_latest_quotes", lambda: (_ for _ in ()).throw(ValueError("bad")))
    refresh.replay_deferred_quotes(owner)
    assert not owner._deferred_quote_refresh


def test_async_update_market_caps_guards_and_no_missing_hook(monkeypatch):
    owner = _Owner()
    owner._runtime_cleanup_done = True
    refresh.async_update_market_caps(owner)
    owner._runtime_cleanup_done = False
    monkeypatch.setattr(refresh.QCoreApplication, "instance", staticmethod(lambda: None))
    refresh.async_update_market_caps(owner)

    monkeypatch.setattr(
        refresh.QCoreApplication,
        "instance",
        staticmethod(lambda: SimpleNamespace(closingDown=lambda: False)),
    )
    owner.window = lambda: SimpleNamespace(_is_closing=True)
    refresh.async_update_market_caps(owner)
    owner.window = lambda: None
    owner.model = SimpleNamespace()
    refresh.async_update_market_caps(owner)

    owner.model = SimpleNamespace(row_data=[])
    monkeypatch.setattr(refresh, "collect_missing_finance_codes", lambda *args: [])
    refresh.async_update_market_caps(owner)
    assert owner.after_calls == 1


def test_default_finance_loader_and_async_prime_remaining_branches(monkeypatch):
    owner = _Owner([{"代码": "000001"}])
    monkeypatch.setattr(
        refresh,
        "load_cached_finance_snapshot",
        lambda codes, tdx_vipdoc=None: {codes[0]: {"root": tdx_vipdoc}},
    )
    loader = refresh._resolve_cached_finance_loader(owner)
    assert loader(["000001"])["000001"]["root"] == "D:/HT/vipdoc"

    owner._runtime_cleanup_done = True
    assert not refresh.prime_local_quote_snapshot_async(owner)
    owner._runtime_cleanup_done = False
    assert not refresh.prime_local_quote_snapshot_async(owner, SimpleNamespace())

    from core.global_store import global_store

    monkeypatch.setattr(global_store, "get_latest_quotes", lambda: (_ for _ in ()).throw(ValueError("bad")))
    monkeypatch.setattr(refresh, "_collect_local_quote_targets", lambda *args: [])
    assert not refresh.prime_local_quote_snapshot_async(owner, owner.model)

    monkeypatch.setattr(refresh, "_collect_local_quote_targets", lambda *args: ["000001"])
    monkeypatch.setattr(
        refresh.QCoreApplication,
        "instance",
        staticmethod(lambda: SimpleNamespace(closingDown=lambda: False)),
    )
    from app.services import ui_task_service

    monkeypatch.setattr(
        ui_task_service,
        "background_job_runner",
        SimpleNamespace(is_active_task=lambda key: False),
    )
    captured = {}
    monkeypatch.setattr(
        refresh,
        "_run_owner_background",
        lambda owner, runner, name, fn, **kwargs: captured.update(fn=fn, **kwargs),
    )
    assert refresh.prime_local_quote_snapshot_async(owner)

    monkeypatch.setattr(refresh.QCoreApplication, "instance", staticmethod(lambda: None))
    assert captured["fn"](None) == {}
    monkeypatch.setattr(
        refresh.QCoreApplication,
        "instance",
        staticmethod(lambda: SimpleNamespace(closingDown=lambda: False)),
    )
    monkeypatch.setattr(
        refresh,
        "_build_local_quote_payload",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("gone")),
    )
    assert captured["fn"](None) == {}

    monkeypatch.setattr(refresh, "publish_rt_quotes", lambda payload, source: {})
    captured["on_success"]({"000001": {"close": 10}})
    owner._apply_quote_snapshot = lambda payload: (_ for _ in ()).throw(RuntimeError("gone"))
    monkeypatch.setattr(refresh, "publish_rt_quotes", lambda payload, source: payload)
    captured["on_success"]({"000001": {"close": 10}})


def test_signature_empty_queue_dead_owner_and_candidate_branch(monkeypatch):
    owner = _Owner()
    owner._normalize_quote_code = lambda code: "abc"
    assert refresh._quote_code_candidates(owner, "raw") == ["raw", "abc", "ABC"]
    monkeypatch.setattr(refresh, "_payload_signature_for_codes", lambda owner, payload: {})
    refresh._remember_cache_snapshot_payload(owner, {"a": {}})
    assert not hasattr(owner, "_cache_snapshot_applied_signatures")

    monkeypatch.setattr(
        refresh.QCoreApplication,
        "instance",
        staticmethod(lambda: SimpleNamespace(closingDown=lambda: False)),
    )
    dead = _Owner()
    dead_id = id(dead)
    refresh.CacheSnapshotApplyQueue._pending[dead_id] = (weakref.ref(dead), {"a": {}}, False)
    del dead
    gc.collect()
    refresh.CacheSnapshotApplyQueue.flush_one()
    assert not refresh.CacheSnapshotApplyQueue._pending


def test_market_cap_flush_guards_background_errors_and_rescheduling(monkeypatch):
    refresh.MarketCapRefreshBatcher.flush()

    owner = _Owner()
    refresh.MarketCapRefreshBatcher._pending_codes = {"000001"}
    refresh.MarketCapRefreshBatcher._waiters = {id(owner): (weakref.ref(owner), {"000001"})}
    monkeypatch.setattr(refresh.QCoreApplication, "instance", staticmethod(lambda: None))
    refresh.MarketCapRefreshBatcher.flush()
    assert not refresh.MarketCapRefreshBatcher._pending_codes

    app = SimpleNamespace(closingDown=lambda: False)
    monkeypatch.setattr(refresh.QCoreApplication, "instance", staticmethod(lambda: app))
    scheduled = []
    monkeypatch.setattr(
        refresh.MarketCapRefreshBatcher,
        "_schedule_flush",
        classmethod(lambda cls: scheduled.append(True)),
    )
    from app.services import ui_task_service

    manager = SimpleNamespace(is_active_task=lambda key: True)
    monkeypatch.setattr(ui_task_service, "background_job_runner", manager)
    refresh.MarketCapRefreshBatcher._pending_codes = {"000001"}
    refresh.MarketCapRefreshBatcher._waiters = {id(owner): (weakref.ref(owner), {"000001"})}
    refresh.MarketCapRefreshBatcher.flush()
    assert scheduled == [True]

    callbacks = {}
    manager.is_active_task = lambda key: False
    manager.run_in_background = lambda fn, **kwargs: callbacks.update(fn=fn, **kwargs)
    refresh.MarketCapRefreshBatcher._pending_codes = {"000001"}
    refresh.MarketCapRefreshBatcher._waiters = {id(owner): (weakref.ref(owner), {"000001"})}
    refresh.MarketCapRefreshBatcher.flush()

    monkeypatch.setattr(refresh.QCoreApplication, "instance", staticmethod(lambda: None))
    assert callbacks["fn"]() == {}
    monkeypatch.setattr(refresh.QCoreApplication, "instance", staticmethod(lambda: app))
    monkeypatch.setattr(
        refresh.MarketCapRefreshBatcher,
        "_load_waiter_finance_snapshot",
        lambda *args: (_ for _ in ()).throw(OSError("bad")),
    )
    assert callbacks["fn"]() == {}

    monkeypatch.setattr(refresh, "build_finance_quote_payload", lambda data: {})
    refresh.MarketCapRefreshBatcher._pending_codes = {"000002"}
    callbacks["on_success"]({"000001": {"zongguben": 1}})
    callbacks["on_success"]({})
    callbacks["on_error"]("")
    callbacks["on_error"]("bad")
    assert len(scheduled) >= 4


def test_refresh_table_quotes_all_guards_and_submission(monkeypatch):
    owner = _Owner([{"代码": "000001"}])
    monkeypatch.setattr(refresh, "_prepare_table_refresh", lambda *args, **kwargs: None)
    refresh.refresh_table_quotes_and_market_caps(owner)

    monkeypatch.setattr(refresh, "_prepare_table_refresh", lambda *args, **kwargs: (owner.model, ["000001"]))
    monkeypatch.setattr(refresh, "_latest_quote_snapshot", lambda: {})
    owner.async_update_market_caps = lambda: None
    owner.data_provider = None
    refresh.refresh_table_quotes_and_market_caps(owner)

    owner.data_provider = SimpleNamespace(fetch_realtime_quotes_batch=lambda codes: {})
    monkeypatch.setattr(refresh, "collect_quote_refresh_codes", lambda *args, **kwargs: [])
    refresh.refresh_table_quotes_and_market_caps(owner)

    monkeypatch.setattr(refresh, "collect_quote_refresh_codes", lambda *args, **kwargs: ["000001"])
    from app.services import ui_task_service

    manager = SimpleNamespace(is_active_task=lambda task_id: True)
    monkeypatch.setattr(ui_task_service, "background_job_runner", manager)
    refresh.refresh_table_quotes_and_market_caps(owner, quote_task_id="custom")

    manager.is_active_task = lambda task_id: False
    submitted = []
    monkeypatch.setattr(refresh, "_submit_owner_quote_refresh", lambda *args: submitted.append(args))
    refresh.refresh_table_quotes_and_market_caps(owner, quote_task_id=None)
    assert submitted and submitted[-1][-1] == ["000001"]

    monkeypatch.setattr(refresh, "_prepare_table_refresh", lambda *args, **kwargs: None)
    refresh._refresh_table_from_latest_snapshot_impl(owner)


def test_subscribe_global_quotes_remaining_branches(monkeypatch):
    from app.services import ui_event_service
    from core.global_store import global_store

    connected = []
    fake_signal = SimpleNamespace(connect=lambda callback: connected.append(callback), disconnect=lambda callback: None)
    monkeypatch.setattr(ui_event_service, "domain_events", SimpleNamespace(sig_rt_quotes=fake_signal))

    owner = _Owner()
    owner._on_rt_quotes_direct = lambda quotes: None
    refresh.subscribe_global_quotes(owner, None)
    assert owner._quote_signal_connected
    assert connected

    updated = []
    owner = _Owner()
    owner._on_rt_quotes_direct = lambda quotes: None
    owner.model.update_quotes = lambda quotes: updated.append(quotes)
    monkeypatch.setattr(global_store, "get_latest_quotes", lambda: {"000001": {"close": 10}})
    refresh.subscribe_global_quotes(owner)
    assert updated

    monkeypatch.setattr(global_store, "get_latest_quotes", lambda: {})
    refresh.subscribe_global_quotes(owner)
    monkeypatch.setattr(global_store, "get_latest_quotes", lambda: (_ for _ in ()).throw(ValueError("bad")))
    refresh.subscribe_global_quotes(owner)


def test_async_market_caps_latest_snapshot_error_still_enqueues(monkeypatch):
    owner = _Owner([{"代码": "000001"}])
    monkeypatch.setattr(
        refresh.QCoreApplication,
        "instance",
        staticmethod(lambda: SimpleNamespace(closingDown=lambda: False)),
    )
    from core.global_store import global_store

    monkeypatch.setattr(global_store, "get_latest_quotes", lambda: (_ for _ in ()).throw(ValueError("bad")))
    monkeypatch.setattr(refresh, "collect_missing_finance_codes", lambda *args: ["000001"])
    calls = []
    monkeypatch.setattr(refresh.MarketCapRefreshBatcher, "enqueue", lambda *args: calls.append(args))
    refresh.async_update_market_caps(owner)
    assert calls
