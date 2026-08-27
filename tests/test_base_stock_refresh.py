# -*- coding: utf-8 -*-

from types import SimpleNamespace

from core.json_cache import save_json_file
from ui.models.stock_table_model import LEGACY_MOJIBAKE_CODE_KEY
from ui.tabs.base_stock_refresh import MarketCapRefreshBatcher


class _DummyOwner:
    def __init__(self):
        self.snapshots = []
        self.after_cap_calls = 0

    def _apply_quote_snapshot(self, payload):
        self.snapshots.append(dict(payload or {}))

    def _after_market_caps_updated(self):
        self.after_cap_calls += 1


def _reset_cache_snapshot_apply_queue(refresh_module):
    refresh_module.CacheSnapshotApplyQueue._scheduled = False
    refresh_module.CacheSnapshotApplyQueue._pending = {}


def test_market_cap_batcher_merges_overlapping_tab_requests(monkeypatch):
    from core.task_manager import task_manager
    from ui.tabs import base_stock_refresh as refresh_module
    from vcp.engine import VCPEngine

    MarketCapRefreshBatcher._scheduled = False
    MarketCapRefreshBatcher._pending_codes = set()
    MarketCapRefreshBatcher._waiters = {}

    scheduled = []
    batch_calls = []

    monkeypatch.setattr(
        refresh_module.QCoreApplication,
        "instance",
        staticmethod(lambda: SimpleNamespace(closingDown=lambda: False)),
    )
    monkeypatch.setattr(
        refresh_module.QTimer,
        "singleShot",
        staticmethod(lambda _ms, callback: scheduled.append(callback)),
    )
    monkeypatch.setattr(task_manager, "is_active_task", lambda _task_id: False)
    monkeypatch.setattr(
        task_manager,
        "run_in_background",
        lambda fn, task_id=None, on_success=None, on_error=None: on_success(fn()),
    )
    monkeypatch.setattr(
        VCPEngine,
        "batch_get_finance_info",
        staticmethod(
            lambda codes: batch_calls.append(tuple(sorted(codes))) or {code: {"zongguben": 100000000} for code in codes}
        ),
    )
    monkeypatch.setattr(
        refresh_module,
        "publish_rt_quotes",
        lambda payload, source="": dict(payload or {}),
    )

    owner_a = _DummyOwner()
    owner_b = _DummyOwner()

    MarketCapRefreshBatcher.enqueue(owner_a, ["600519", "000001"])
    MarketCapRefreshBatcher.enqueue(owner_b, ["600519", "300750"])

    assert len(scheduled) == 1
    scheduled.pop()()

    assert batch_calls == [("000001", "300750", "600519")]
    assert owner_a.after_cap_calls == 1
    assert owner_b.after_cap_calls == 1
    assert set(owner_a.snapshots[0].keys()) == {"600519", "000001"}
    assert set(owner_b.snapshots[0].keys()) == {"600519", "300750"}


def test_market_cap_batcher_prefers_local_tdx_capital(monkeypatch, tmp_path):
    from core.task_manager import task_manager
    from ui.tabs import base_stock_refresh as refresh_module

    MarketCapRefreshBatcher._scheduled = False
    MarketCapRefreshBatcher._pending_codes = set()
    MarketCapRefreshBatcher._waiters = {}

    scheduled = []

    monkeypatch.setattr(refresh_module, "FINANCE_CACHE_FILE", str(tmp_path / "finance.json"))
    monkeypatch.setattr(
        refresh_module,
        "load_local_tdx_capital_snapshot",
        lambda codes, tdx_vipdoc: {"000001": {"zongguben": 2_000_000_000, "source": "tdx_base"}},
    )
    refresh_module._FINANCE_CACHE_PATH = None
    refresh_module._FINANCE_CACHE_SIGNATURE = None
    refresh_module._FINANCE_CACHE_PAYLOAD = None
    monkeypatch.setattr(
        refresh_module.QCoreApplication,
        "instance",
        staticmethod(lambda: SimpleNamespace(closingDown=lambda: False)),
    )
    monkeypatch.setattr(
        refresh_module.QTimer,
        "singleShot",
        staticmethod(lambda _ms, callback: scheduled.append(callback)),
    )
    monkeypatch.setattr(task_manager, "is_active_task", lambda _task_id: False)
    monkeypatch.setattr(
        task_manager,
        "run_in_background",
        lambda fn, task_id=None, on_success=None, on_error=None: on_success(fn()),
    )
    monkeypatch.setattr(
        refresh_module,
        "batch_get_finance_info",
        lambda codes: (_ for _ in ()).throw(AssertionError("local TDX capital should be used first")),
    )
    monkeypatch.setattr(refresh_module, "publish_rt_quotes", lambda payload, source="": dict(payload or {}))

    owner = _DummyOwner()
    owner.data_provider = SimpleNamespace(tdx_vipdoc="D:/HT/vipdoc")

    MarketCapRefreshBatcher.enqueue(owner, ["000001"])

    assert len(scheduled) == 1
    scheduled.pop()()

    assert owner.after_cap_calls == 1
    assert owner.snapshots == [
        {
            "000001": {
                "total_shares": 2_000_000_000.0,
                "finance_source": "tdx_base",
            }
        }
    ]


def test_local_quote_snapshot_async_runs_in_background(monkeypatch):
    from core.task_manager import task_manager
    from ui.tabs import base_stock_refresh as refresh_module

    class DummyProvider:
        def __init__(self):
            self.offline_calls = []

        def build_offline_quotes(self, codes):
            self.offline_calls.append(list(codes))
            return {"000001": {"close": 10.5, "last_close": 10.0}}

    class DummyOwner:
        def __init__(self):
            self.data_provider = DummyProvider()
            self.snapshots = []

        def _resolve_active_quote_model(self):
            return SimpleNamespace(row_data=[{}])

        def _apply_quote_snapshot(self, payload):
            self.snapshots.append(dict(payload or {}))

        @staticmethod
        def _load_cached_finance_snapshot(_codes):
            return {}

    tasks = []
    owner = DummyOwner()

    monkeypatch.setattr(
        refresh_module.QCoreApplication,
        "instance",
        staticmethod(lambda: SimpleNamespace(closingDown=lambda: False)),
    )
    monkeypatch.setattr(refresh_module, "collect_table_codes", lambda _owner, _model=None: ["000001"])
    monkeypatch.setattr(refresh_module, "publish_rt_quotes", lambda payload, source="": dict(payload or {}))
    monkeypatch.setattr(task_manager, "is_active_task", lambda task_id: False)
    monkeypatch.setattr(
        task_manager,
        "run_in_background",
        lambda fn, task_id=None, on_success=None, on_error=None, **_kwargs: tasks.append(task_id) or on_success(fn()),
    )

    scheduled = refresh_module.prime_local_quote_snapshot_async(owner)

    assert scheduled is True
    assert owner.data_provider.offline_calls == [["000001"]]
    assert owner.snapshots == [{"000001": {"close": 10.5, "last_close": 10.0}}]
    assert tasks and "_local_quote_snapshot_" in tasks[0]


def test_local_quote_snapshot_replaces_complete_quote_with_older_f5_trade_date(monkeypatch):
    from app.services.ui_quote_service import resolve_quote_metrics
    from ui.tabs import base_stock_refresh as refresh_module

    class DummyProvider:
        _market_data_snapshot_trade_date = "20260810"

        def __init__(self):
            self.offline_calls = []

        def build_offline_quotes(self, codes):
            self.offline_calls.append(list(codes))
            return {
                "000001": {
                    "close": 10.5,
                    "last_close": 10.0,
                    "date": "2026-08-10",
                }
            }

    class DummyOwner:
        def __init__(self):
            self.data_provider = DummyProvider()

        @staticmethod
        def _resolve_active_quote_model():
            return SimpleNamespace(row_data=[{}])

        @staticmethod
        def _load_cached_finance_snapshot(_codes):
            return {"000001": {"zongguben": 100_000_000}}

    stale_quote = {
        "000001": {
            "close": 9.0,
            "last_close": 8.5,
            "market_cap": 900_000_000,
            "date": "2026-08-07",
        }
    }
    owner = DummyOwner()

    monkeypatch.setattr(refresh_module, "collect_table_codes", lambda _owner, _model=None: ["000001"])
    monkeypatch.setattr(refresh_module, "_latest_quote_snapshot", lambda: stale_quote)
    monkeypatch.setattr(refresh_module, "publish_rt_quotes", lambda payload, source="": dict(payload or {}))

    payload = refresh_module.prime_local_quote_snapshot(owner)

    assert owner.data_provider.offline_calls == [["000001"]]
    assert payload["000001"]["close"] == 10.5
    assert payload["000001"]["last_close"] == 10.0
    assert payload["000001"]["date"] == "2026-08-10"
    assert payload["000001"]["market_cap"] == 1_050_000_000
    assert round(resolve_quote_metrics({}, payload["000001"])["pct"], 6) == 5.0


def test_local_quote_snapshot_keeps_same_f5_trade_date_quote_out_of_offline_rebuild(monkeypatch):
    from ui.tabs import base_stock_refresh as refresh_module

    owner = SimpleNamespace(data_provider=SimpleNamespace(_market_data_snapshot_trade_date="20260810"))
    model = SimpleNamespace(row_data=[{}])
    quotes = {
        "000001": {
            "close": 10.5,
            "last_close": 10.0,
            "market_cap": 1_050_000_000,
            "date": "2026-08-10",
        }
    }

    monkeypatch.setattr(refresh_module, "collect_table_codes", lambda _owner, _model=None: ["000001"])

    assert refresh_module._collect_local_quote_targets(owner, model, quotes) == []


def test_local_quote_snapshot_rebuilds_a_stale_halted_quote_once_per_f5_snapshot(monkeypatch):
    from ui.tabs import base_stock_refresh as refresh_module

    class DummyProvider:
        _market_data_snapshot_trade_date = "20260810"

        def __init__(self):
            self.cache_data = {}
            self.offline_calls = []

        def build_offline_quotes(self, codes):
            self.offline_calls.append(list(codes))
            return {"000001": {"close": 9.0, "last_close": 8.5, "date": "2026-08-07"}}

    class DummyOwner:
        def __init__(self):
            self.data_provider = DummyProvider()

        @staticmethod
        def _resolve_active_quote_model():
            return SimpleNamespace(row_data=[{}])

        @staticmethod
        def _load_cached_finance_snapshot(_codes):
            return {"000001": {"zongguben": 100_000_000}}

    stale_quote = {
        "000001": {
            "close": 9.0,
            "last_close": 8.5,
            "market_cap": 900_000_000,
            "date": "2026-08-07",
        }
    }
    owner = DummyOwner()
    monkeypatch.setattr(refresh_module, "collect_table_codes", lambda _owner, _model=None: ["000001"])
    monkeypatch.setattr(refresh_module, "_latest_quote_snapshot", lambda: stale_quote)
    monkeypatch.setattr(refresh_module, "publish_rt_quotes", lambda payload, source="": dict(payload or {}))

    assert refresh_module.prime_local_quote_snapshot(owner)
    assert refresh_module.prime_local_quote_snapshot(owner) == {}
    assert owner.data_provider.offline_calls == [["000001"]]

    owner.data_provider.cache_data = {}

    assert refresh_module.prime_local_quote_snapshot(owner)
    assert owner.data_provider.offline_calls == [["000001"], ["000001"]]


def test_f5_snapshot_replay_primes_hidden_stale_quotes(monkeypatch):
    from ui.tabs import base_stock_refresh as refresh_module

    model = SimpleNamespace(row_data=[{}])
    owner = SimpleNamespace(
        _f5_cache_snapshot_apply=True,
        data_provider=SimpleNamespace(_market_data_snapshot_trade_date="20260810", cache_data={}),
        _resolve_active_quote_model=lambda: model,
    )
    calls = []
    stale_quote = {
        "000001": {
            "close": 9.0,
            "last_close": 8.5,
            "market_cap": 900_000_000,
            "date": "2026-08-07",
        }
    }

    monkeypatch.setattr(refresh_module, "collect_table_codes", lambda _owner, _model=None: ["000001"])
    monkeypatch.setattr(refresh_module, "_latest_quote_snapshot", lambda: stale_quote)
    monkeypatch.setattr(
        refresh_module,
        "prime_local_quote_snapshot_async",
        lambda target, current_model=None: calls.append((target, current_model)) or True,
    )

    assert refresh_module._prepare_table_refresh(owner, None, async_local=True) == (model, ["000001"])
    assert calls == [(owner, model)]


def test_hidden_tab_late_rows_prime_stale_f5_quotes(monkeypatch):
    from ui.tabs import base_stock_refresh as refresh_module
    from ui.tabs.base_stock_tab import BaseStockTab

    model = SimpleNamespace(row_data=[{}])
    calls = []
    owner = SimpleNamespace(
        data_provider=SimpleNamespace(_market_data_snapshot_trade_date="20260810", cache_data={}),
        isVisible=lambda: False,
        refresh_table_from_latest_snapshot=lambda current_model=None, **kwargs: calls.append((current_model, kwargs)),
    )
    stale_quote = {
        "000001": {
            "close": 9.0,
            "last_close": 8.5,
            "market_cap": 900_000_000,
            "date": "2026-08-07",
        }
    }

    monkeypatch.setattr(refresh_module, "collect_table_codes", lambda _owner, _model=None: ["000001"])
    monkeypatch.setattr(refresh_module, "_latest_quote_snapshot", lambda: stale_quote)

    assert BaseStockTab._prime_visible_local_quote_snapshot(owner, model) is True
    assert calls == [(model, {"async_local": True})]


def test_workspace_snapshot_uses_distinct_task_id_while_visible_snapshot_is_active(monkeypatch):
    from app.services.ui_task_service import background_job_runner as task_manager
    from ui.tabs import base_stock_refresh as refresh_module

    class DummyOwner:
        def __init__(self):
            self.data_provider = object()
            self._runtime_cleanup_done = False
            self._workspace_background_snapshot_io_done = False
            self._workspace_background_snapshot_task_id = ""

        @staticmethod
        def _resolve_active_quote_model():
            return SimpleNamespace(row_data=[{}])

    scheduled = []

    def capture_background(_owner, _runner, name, fn, **kwargs):
        scheduled.append(
            {
                "name": name,
                "fn": fn,
                "task_id": kwargs["task_id"],
                "on_success": kwargs["on_success"],
                "on_terminated": kwargs.get("on_terminated"),
            }
        )

    owner = DummyOwner()
    monkeypatch.setattr(refresh_module, "_qt_runtime_available", lambda: True)
    monkeypatch.setattr(refresh_module, "collect_table_codes", lambda _owner, _model=None: ["000001"])
    monkeypatch.setattr(refresh_module, "_latest_quote_snapshot", lambda: {})
    monkeypatch.setattr(refresh_module, "_run_owner_background", capture_background)
    monkeypatch.setattr(task_manager, "is_active_task", lambda _task_id: False)

    assert refresh_module.prime_local_quote_snapshot_async(owner) is True
    visible = scheduled[0]
    visible_task_id = str(getattr(visible["task_id"], "task_id", visible["task_id"]))
    monkeypatch.setattr(
        task_manager,
        "is_active_task",
        lambda task_id: str(getattr(task_id, "task_id", task_id)) == visible_task_id,
    )

    assert refresh_module.prime_workspace_background_snapshot(owner) is True
    workspace = scheduled[1]
    workspace_task_id = str(getattr(workspace["task_id"], "task_id", workspace["task_id"]))

    assert visible["name"] == "local_quote_snapshot"
    assert workspace["name"] == "workspace_background_snapshot"
    assert visible_task_id != workspace_task_id
    assert owner._workspace_background_snapshot_task_id == workspace_task_id

    refresh_module.cancel_workspace_background_snapshot(owner)
    assert owner._workspace_background_snapshot_io_done is False
    assert refresh_module.workspace_background_snapshot_cancellation_settled(owner) is False

    assert callable(workspace["on_terminated"])
    workspace["on_terminated"]()
    assert owner._workspace_background_snapshot_io_done is True
    assert refresh_module.workspace_background_snapshot_cancellation_settled(owner) is True


def test_refresh_table_quotes_and_market_caps_can_prime_local_snapshot_async(monkeypatch):
    from ui.tabs import base_stock_refresh as refresh_module

    calls = []

    class DummyModel:
        row_data = [{"代码": "000001"}]

    class DummyOwner:
        data_provider = None

        def _resolve_active_quote_model(self):
            return DummyModel()

        def _apply_quote_snapshot(self, payload):
            calls.append(("snapshot", dict(payload or {})))

        def async_update_market_caps(self):
            calls.append(("market_caps", None))

    monkeypatch.setattr(refresh_module, "collect_table_codes", lambda _owner, _model=None: ["000001"])
    monkeypatch.setattr(
        refresh_module, "prime_local_quote_snapshot", lambda *_args, **_kwargs: calls.append(("sync", None))
    )
    monkeypatch.setattr(
        refresh_module,
        "prime_local_quote_snapshot_async",
        lambda *_args, **_kwargs: calls.append(("async", None)) or True,
    )
    monkeypatch.setattr(
        "core.global_store.global_store.get_latest_quotes",
        lambda: {"000001": {"close": 10.0}},
    )

    refresh_module.refresh_table_quotes_and_market_caps(DummyOwner(), async_local=True)

    assert ("async", None) in calls
    assert ("sync", None) not in calls
    assert ("snapshot", {"000001": {"close": 10.0}}) in calls
    assert ("market_caps", None) in calls


def test_refresh_table_from_latest_snapshot_defers_hidden_async_snapshot_until_visible(monkeypatch):
    from ui.tabs import base_stock_refresh as refresh_module

    _reset_cache_snapshot_apply_queue(refresh_module)
    scheduled = []
    calls = []
    metrics = []
    visible = {"value": False}
    snapshot = {"000001": {"close": 10.0}}

    class DummyModel:
        row_data = [{"代码": "000001"}]

    class DummyOwner:
        def isVisible(self):
            return visible["value"]

        def _resolve_active_quote_model(self):
            return DummyModel()

        def _apply_quote_snapshot(self, payload):
            calls.append(("snapshot", dict(payload or {})))

    monkeypatch.setattr(
        refresh_module.QCoreApplication,
        "instance",
        staticmethod(lambda: SimpleNamespace(closingDown=lambda: False)),
    )
    monkeypatch.setattr(
        refresh_module.QTimer,
        "singleShot",
        staticmethod(lambda ms, callback: scheduled.append((ms, callback))),
    )
    monkeypatch.setattr(refresh_module, "record_metric", lambda name, value, **kwargs: metrics.append((name, value, kwargs)))
    monkeypatch.setattr(refresh_module, "collect_table_codes", lambda _owner, _model=None: ["000001"])
    monkeypatch.setattr(
        refresh_module, "prime_local_quote_snapshot", lambda *_args, **_kwargs: calls.append(("sync", None))
    )
    monkeypatch.setattr(
        refresh_module,
        "prime_local_quote_snapshot_async",
        lambda *_args, **_kwargs: calls.append(("async", None)) or True,
    )
    monkeypatch.setattr(
        "core.global_store.global_store.get_latest_quotes",
        lambda: snapshot,
    )

    try:
        owner = DummyOwner()
        refresh_module.refresh_table_from_latest_snapshot(owner, async_local=True)

        assert ("async", None) not in calls
        assert ("sync", None) not in calls
        assert calls == []
        assert scheduled == []
        assert owner._deferred_quote_refresh is True
        assert not refresh_module.CacheSnapshotApplyQueue.is_pending(owner)

        snapshot["000001"] = {"close": 11.0}
        refresh_module.refresh_table_from_latest_snapshot(owner, async_local=True)

        assert calls == []
        assert scheduled == []
        assert owner._deferred_quote_refresh is True
        assert not refresh_module.CacheSnapshotApplyQueue.is_pending(owner)
        assert metrics == [
            (
                "cache_snapshot_deferred_until_visible_count",
                1.0,
                {
                    "unit": "count",
                    "tags": {
                        "tab": "DummyOwner",
                        "codes": "1",
                        "signal": "cache_snapshot",
                        "reason": "hidden",
                    },
                },
            ),
            (
                "cache_snapshot_deferred_until_visible_count",
                1.0,
                {
                    "unit": "count",
                    "tags": {
                        "tab": "DummyOwner",
                        "codes": "1",
                        "signal": "cache_snapshot",
                        "reason": "hidden",
                    },
                },
            ),
        ]

        visible["value"] = True
        refresh_module.replay_deferred_quotes(owner)

        assert calls == []
        assert owner._deferred_quote_refresh is False
        assert len(scheduled) == 1
        scheduled.pop(0)[1]()
        assert calls == [("snapshot", {"000001": {"close": 11.0}})]
    finally:
        _reset_cache_snapshot_apply_queue(refresh_module)


def test_refresh_table_from_latest_snapshot_projects_hidden_watchlist_without_deferral(monkeypatch):
    """关注池隐藏时允许静默投影报价，避免回切后批量 dataChanged。"""
    from ui.tabs import base_stock_refresh as refresh_module

    calls = []

    class DummyModel:
        row_data = [{"代码": "000001"}]

    class DummyOwner:
        _runtime_cleanup_done = False

        def isVisible(self):
            return False

        def accepts_hidden_quote_projection(self):
            return True

        def _resolve_active_quote_model(self):
            return DummyModel()

        def _apply_quote_snapshot(self, payload):
            calls.append(dict(payload or {}))
            return 1

    monkeypatch.setattr(
        refresh_module.QCoreApplication,
        "instance",
        staticmethod(lambda: SimpleNamespace(closingDown=lambda: False)),
    )
    monkeypatch.setattr(refresh_module, "collect_table_codes", lambda _owner, _model=None: ["000001"])
    monkeypatch.setattr(
        "core.global_store.global_store.get_latest_quotes",
        lambda: {"000001": {"close": 10.0}},
    )

    owner = DummyOwner()
    refresh_module.refresh_table_from_latest_snapshot(owner, async_local=True)

    assert calls == [{"000001": {"close": 10.0}}]
    assert not getattr(owner, "_deferred_quote_refresh", False)
    assert not refresh_module.CacheSnapshotApplyQueue.is_pending(owner)


def test_hidden_watchlist_quote_signal_projects_model_without_showevent_replay(monkeypatch):
    from ui.tabs import base_stock_refresh as refresh_module

    applied = []

    class DummyOwner:
        _runtime_cleanup_done = False
        _deferred_quote_refresh = False

        def isVisible(self):
            return False

        def accepts_hidden_quote_projection(self):
            return True

        def _apply_quote_snapshot(self, payload):
            applied.append(dict(payload or {}))

    refresh_module.on_rt_quotes_direct(DummyOwner(), {"000001": {"close": 11.0}})

    assert applied == [{"000001": {"close": 11.0}}]


def test_hidden_watchlist_projection_is_applied_without_a_visible_flash():
    from ui.tabs import base_stock_refresh as refresh_module

    applied = []

    class DummyOwner:
        _runtime_cleanup_done = False
        _deferred_quote_refresh = False
        _workspace_active = False

        def isVisible(self):
            return False

        def accepts_hidden_quote_projection(self):
            return True

        def _apply_quote_snapshot(self, payload, *, record_flash=True):
            applied.append((dict(payload or {}), record_flash))

    refresh_module.on_rt_quotes_direct(DummyOwner(), {"000001": {"close": 11.0}})

    assert applied == [({"000001": {"close": 11.0}}, False)]


def test_active_watchlist_projection_keeps_visible_quote_flash():
    from ui.tabs import base_stock_refresh as refresh_module

    applied = []

    class DummyOwner:
        _runtime_cleanup_done = False
        _workspace_active = True

        def isVisible(self):
            return True

        def accepts_hidden_quote_projection(self):
            return True

        def _apply_quote_snapshot(self, payload, *, record_flash=True):
            applied.append((dict(payload or {}), record_flash))

    refresh_module.on_rt_quotes_direct(DummyOwner(), {"000001": {"close": 11.0}})

    assert applied == [({"000001": {"close": 11.0}}, True)]


def test_refresh_table_from_latest_snapshot_defers_visible_async_snapshots_by_tab(monkeypatch):
    from ui.tabs import base_stock_refresh as refresh_module

    _reset_cache_snapshot_apply_queue(refresh_module)
    scheduled = []
    calls = []

    class DummyOwner:
        def __init__(self, name, code):
            self.name = name
            self.code = code
            self.model = SimpleNamespace(row_data=[{"代码": code}])

        def isVisible(self):
            return True

        def _resolve_active_quote_model(self):
            return self.model

        def _apply_quote_snapshot(self, payload):
            calls.append((self.name, dict(payload or {})))

    monkeypatch.setattr(
        refresh_module.QCoreApplication,
        "instance",
        staticmethod(lambda: SimpleNamespace(closingDown=lambda: False)),
    )
    monkeypatch.setattr(
        refresh_module.QTimer,
        "singleShot",
        staticmethod(lambda ms, callback: scheduled.append((ms, callback))),
    )
    monkeypatch.setattr(refresh_module, "collect_table_codes", lambda owner, _model=None: [owner.code])
    monkeypatch.setattr(refresh_module, "prime_local_quote_snapshot_async", lambda *_args, **_kwargs: False)
    monkeypatch.setattr(
        "core.global_store.global_store.get_latest_quotes",
        lambda: {
            "000001": {"close": 10.0},
            "000002": {"close": 11.0},
        },
    )

    try:
        owner_fund = DummyOwner("fund", "000001")
        owner_foreign = DummyOwner("foreign", "000002")
        refresh_module.refresh_table_from_latest_snapshot(owner_fund, async_local=True)
        refresh_module.refresh_table_from_latest_snapshot(owner_foreign, async_local=True)

        assert calls == []
        assert len(scheduled) == 1

        _, first_callback = scheduled.pop(0)
        first_callback()
        assert calls == [("fund", {"000001": {"close": 10.0}})]
        assert len(scheduled) == 1

        _, second_callback = scheduled.pop(0)
        second_callback()
        assert calls == [
            ("fund", {"000001": {"close": 10.0}}),
            ("foreign", {"000002": {"close": 11.0}}),
        ]
    finally:
        _reset_cache_snapshot_apply_queue(refresh_module)


def test_cache_snapshot_apply_queue_slices_payload_by_batch(monkeypatch):
    from ui.tabs import base_stock_refresh as refresh_module

    _reset_cache_snapshot_apply_queue(refresh_module)
    monkeypatch.setenv("VCP_CACHE_SNAPSHOT_APPLY_CHUNK_SIZE", "2")
    scheduled = []
    calls = []

    class DummyOwner:
        def __init__(self):
            self.model = SimpleNamespace(
                headers=["代码", "现价"],
                row_data=[{"代码": f"{idx:06d}", "现价": "--"} for idx in range(1, 6)],
            )

        def _resolve_active_quote_model(self):
            return self.model

        def isVisible(self):
            return True

        def _apply_quote_snapshot(self, payload):
            calls.append(tuple(payload))
            for row in self.model.row_data:
                quote = payload.get(row["代码"])
                if quote:
                    row["现价"] = f"{float(quote['close']):.2f}"
            return {"changed_rows": len(payload)}

    monkeypatch.setattr(
        refresh_module.QCoreApplication,
        "instance",
        staticmethod(lambda: SimpleNamespace(closingDown=lambda: False)),
    )
    monkeypatch.setattr(
        refresh_module.QTimer,
        "singleShot",
        staticmethod(lambda ms, callback: scheduled.append((ms, callback))),
    )

    try:
        owner = DummyOwner()
        payload = {f"{idx:06d}": {"close": float(idx)} for idx in range(1, 6)}

        assert refresh_module.CacheSnapshotApplyQueue.enqueue(owner, payload, async_local=True) is True
        scheduled_delays = []
        while scheduled:
            delay, callback = scheduled.pop(0)
            scheduled_delays.append(delay)
            callback()

        assert scheduled_delays == [0, 16, 16]
        assert calls == [
            ("000001", "000002"),
            ("000003", "000004"),
            ("000005",),
        ]
    finally:
        _reset_cache_snapshot_apply_queue(refresh_module)


def test_cache_snapshot_apply_queue_keeps_f5_force_apply_after_flag_removed(monkeypatch):
    from ui.tabs import base_stock_refresh as refresh_module

    _reset_cache_snapshot_apply_queue(refresh_module)
    scheduled = []
    calls = []

    class DummyOwner:
        def __init__(self):
            self._runtime_cleanup_done = False
            self._f5_cache_snapshot_apply = True

        def isVisible(self):
            return False

    monkeypatch.setattr(
        refresh_module.QCoreApplication,
        "instance",
        staticmethod(lambda: SimpleNamespace(closingDown=lambda: False)),
    )
    monkeypatch.setattr(
        refresh_module.QTimer,
        "singleShot",
        staticmethod(lambda ms, callback: scheduled.append((ms, callback))),
    )
    monkeypatch.setattr(
        refresh_module,
        "_apply_cache_snapshot_payload",
        lambda owner, payload, *, signal: calls.append((dict(payload), signal)),
    )

    try:
        owner = DummyOwner()
        payload = {"000001": {"close": 10.0}}

        assert refresh_module.CacheSnapshotApplyQueue.enqueue(owner, payload, async_local=True) is True
        delattr(owner, "_f5_cache_snapshot_apply")
        scheduled.pop(0)[1]()

        assert calls == [(payload, "cache_snapshot")]
    finally:
        _reset_cache_snapshot_apply_queue(refresh_module)


def test_cache_snapshot_apply_queue_skips_unchanged_payload(monkeypatch):
    from ui.tabs import base_stock_refresh as refresh_module

    _reset_cache_snapshot_apply_queue(refresh_module)
    scheduled = []
    calls = []

    class DummyOwner:
        def __init__(self):
            self.model = SimpleNamespace(headers=["代码", "现价"], row_data=[{"代码": "000001", "现价": "--"}])

        def _resolve_active_quote_model(self):
            return self.model

        def isVisible(self):
            return True

        def _apply_quote_snapshot(self, payload):
            calls.append(dict(payload))
            self.model.row_data[0]["现价"] = f"{float(payload['000001']['close']):.2f}"
            return {"changed_rows": 1}

    monkeypatch.setattr(
        refresh_module.QCoreApplication,
        "instance",
        staticmethod(lambda: SimpleNamespace(closingDown=lambda: False)),
    )
    monkeypatch.setattr(
        refresh_module.QTimer,
        "singleShot",
        staticmethod(lambda ms, callback: scheduled.append((ms, callback))),
    )

    try:
        owner = DummyOwner()
        payload = {"000001": {"close": 10.0}}

        assert refresh_module.CacheSnapshotApplyQueue.enqueue(owner, payload, async_local=True) is True
        scheduled.pop(0)[1]()
        assert refresh_module.CacheSnapshotApplyQueue.enqueue(owner, payload, async_local=True) is True
        scheduled.pop(0)[1]()

        assert calls == [{"000001": {"close": 10.0}}]
    finally:
        _reset_cache_snapshot_apply_queue(refresh_module)


def test_workspace_background_snapshot_cancellation_discards_pending_apply_queue(monkeypatch):
    from ui.tabs import base_stock_refresh as refresh_module

    _reset_cache_snapshot_apply_queue(refresh_module)
    scheduled = []

    class DummyOwner:
        def __init__(self):
            self._runtime_cleanup_done = False
            self._workspace_background_snapshot_started = True
            self._workspace_background_snapshot_io_done = False
            self._workspace_background_snapshot_ready = False
            self._workspace_background_snapshot_cancelled = False
            self._workspace_background_snapshot_task_id = "workspace-snapshot"

        def isVisible(self):
            return False

    monkeypatch.setattr(
        refresh_module.QCoreApplication,
        "instance",
        staticmethod(lambda: SimpleNamespace(closingDown=lambda: False)),
    )
    monkeypatch.setattr(
        refresh_module.QTimer,
        "singleShot",
        staticmethod(lambda ms, callback: scheduled.append((ms, callback))),
    )

    try:
        owner = DummyOwner()
        assert refresh_module.CacheSnapshotApplyQueue.enqueue(
            owner,
            {"000001": {"close": 10.0}},
            async_local=True,
            force_apply=True,
        ) is True
        assert refresh_module.CacheSnapshotApplyQueue.is_pending(owner) is True
        assert refresh_module.workspace_background_snapshot_cancellation_settled(owner) is False

        refresh_module.cancel_workspace_background_snapshot(owner)

        assert refresh_module.CacheSnapshotApplyQueue.is_pending(owner) is False
        assert owner._workspace_background_snapshot_started is False
        assert owner._workspace_background_snapshot_ready is False
        assert owner._workspace_background_snapshot_cancelled is True
        assert refresh_module.workspace_background_snapshot_cancellation_settled(owner) is False

        owner._workspace_background_snapshot_io_done = True
        assert refresh_module.workspace_background_snapshot_cancellation_settled(owner) is True
    finally:
        _reset_cache_snapshot_apply_queue(refresh_module)


def test_refresh_table_from_latest_snapshot_keeps_sync_local_prime_when_hidden(monkeypatch):
    from ui.tabs import base_stock_refresh as refresh_module

    calls = []

    class DummyModel:
        row_data = [{LEGACY_MOJIBAKE_CODE_KEY: "000001"}]

    class DummyOwner:
        def isVisible(self):
            return False

        def _resolve_active_quote_model(self):
            return DummyModel()

        def _apply_quote_snapshot(self, payload):
            calls.append(("snapshot", dict(payload or {})))

    monkeypatch.setattr(refresh_module, "collect_table_codes", lambda _owner, _model=None: ["000001"])
    monkeypatch.setattr(
        refresh_module, "prime_local_quote_snapshot", lambda *_args, **_kwargs: calls.append(("sync", None))
    )
    monkeypatch.setattr(
        refresh_module,
        "prime_local_quote_snapshot_async",
        lambda *_args, **_kwargs: calls.append(("async", None)) or True,
    )
    monkeypatch.setattr(
        "core.global_store.global_store.get_latest_quotes",
        lambda: {"000001": {"close": 10.0}},
    )

    refresh_module.refresh_table_from_latest_snapshot(DummyOwner(), async_local=False)

    assert ("sync", None) in calls
    assert ("async", None) not in calls
    assert ("snapshot", {"000001": {"close": 10.0}}) in calls


def test_load_cached_finance_snapshot_reuses_shared_file_cache(monkeypatch, tmp_path):
    from ui.tabs import base_stock_refresh as refresh_module

    cache_file = tmp_path / "finance.json"
    save_json_file(
        str(cache_file),
        {
            "000001": {
                "info": {"zongguben": 1000000000},
            }
        },
    )

    load_calls = []
    original_load_json_file = refresh_module.load_json_file

    def _counting_load(path):
        load_calls.append(path)
        return original_load_json_file(path)

    monkeypatch.setattr(refresh_module, "FINANCE_CACHE_FILE", str(cache_file))
    monkeypatch.setattr(refresh_module, "load_json_file", _counting_load)

    refresh_module._FINANCE_CACHE_PATH = None
    refresh_module._FINANCE_CACHE_SIGNATURE = None
    refresh_module._FINANCE_CACHE_PAYLOAD = None

    first = refresh_module.load_cached_finance_snapshot(["000001"])
    second = refresh_module.load_cached_finance_snapshot(["000001"])

    assert first == {"000001": {"zongguben": 1000000000, "source": "finance_cache"}}
    assert second == first
    assert load_calls == [str(cache_file)]

    save_json_file(
        str(cache_file),
        {
            "000001": {
                "info": {
                    "zongguben": 2000000000,
                    "market_cap": 21000000000,
                }
            }
        },
    )

    third = refresh_module.load_cached_finance_snapshot(["000001"])

    assert third == {
        "000001": {
            "zongguben": 2000000000,
            "market_cap": 21000000000,
            "source": "finance_cache",
        }
    }
    assert load_calls == [str(cache_file), str(cache_file)]


def test_load_cached_finance_snapshot_prefers_local_tdx_capital(monkeypatch, tmp_path):
    from ui.tabs import base_stock_refresh as refresh_module

    cache_file = tmp_path / "finance.json"
    save_json_file(
        str(cache_file),
        {
            "000001": {
                "info": {
                    "zongguben": 1000000000,
                    "market_cap": 11000000000,
                    "source": "finance_cache",
                },
            }
        },
    )

    monkeypatch.setattr(refresh_module, "FINANCE_CACHE_FILE", str(cache_file))
    monkeypatch.setattr(
        refresh_module,
        "load_local_tdx_capital_snapshot",
        lambda codes, tdx_vipdoc: {
            "000001": {
                "zongguben": 2000000000,
                "source": "tdx_base",
            }
        },
    )

    refresh_module._FINANCE_CACHE_PATH = None
    refresh_module._FINANCE_CACHE_SIGNATURE = None
    refresh_module._FINANCE_CACHE_PAYLOAD = None

    snapshot = refresh_module.load_cached_finance_snapshot(["000001"], tdx_vipdoc="D:/HT/vipdoc")

    assert snapshot["000001"]["zongguben"] == 2000000000
    assert snapshot["000001"]["market_cap"] == 11000000000
    assert snapshot["000001"]["source"] == "tdx_base"
