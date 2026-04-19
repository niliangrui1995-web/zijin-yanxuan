# -*- coding: utf-8 -*-

from types import SimpleNamespace

from ui.tabs.base_stock_refresh import MarketCapRefreshBatcher


class _DummyOwner:
    def __init__(self):
        self.snapshots = []
        self.after_cap_calls = 0

    def _apply_quote_snapshot(self, payload):
        self.snapshots.append(dict(payload or {}))

    def _after_market_caps_updated(self):
        self.after_cap_calls += 1


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
            lambda codes: batch_calls.append(tuple(sorted(codes)))
            or {code: {"zongguben": 100000000} for code in codes}
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
