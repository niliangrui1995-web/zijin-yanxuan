from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.services import stock_context_snapshot_service as snapshot_module
from app.services.ui_task_lifecycle_service import TaskLifecycleGroup
from infra.tasks.lifecycle import CancellationToken, TaskCancelledError
from ui.workspaces.stock_context_service import StockContextService


class _QueuedRunner:
    def __init__(self):
        self.jobs = []
        self.calls = []

    def run_in_background(self, fn, **kwargs):
        self.jobs.append((fn, dict(kwargs)))
        return str(getattr(kwargs.get("task_id"), "task_id", kwargs.get("task_id")) or "")

    def abandon_task(self, task_id, **_kwargs):
        self.calls.append(("abandon", str(task_id)))
        return True

    def cancel_task(self, task_id, *, reason="cancelled"):
        self.calls.append(("cancel", str(task_id), reason))
        return True

    def wait_for_tasks(self, task_ids, *, timeout_ms):
        self.calls.append(("wait", tuple(task_ids), timeout_ms))
        return True


def test_stock_context_force_refresh_replaces_snapshot_token(monkeypatch):
    runner = _QueuedRunner()
    service = StockContextService(SimpleNamespace(engine=None))
    service._task_lifecycle = TaskLifecycleGroup(runner)
    monkeypatch.setattr(service, "_load_fund_holding_rows_snapshot", lambda *, cancellation_token=None: [])

    assert service.refresh_async_snapshots(force=True, include_lhb=False) is True
    first_token = runner.jobs[-1][1]["cancellation_token"]
    assert service.refresh_async_snapshots(force=True, include_lhb=False) is True
    second_token = runner.jobs[-1][1]["cancellation_token"]

    assert len(runner.jobs) == 2
    assert first_token.cancelled is True
    assert first_token.reason == "replaced"
    assert second_token.cancelled is False
    assert any(call[0] == "abandon" for call in runner.calls)


def test_stock_context_shutdown_cancels_snapshot_without_publishing_success(monkeypatch):
    runner = _QueuedRunner()
    service = StockContextService(SimpleNamespace(engine=None))
    service._task_lifecycle = TaskLifecycleGroup(runner)
    monkeypatch.setattr(
        service,
        "_load_fund_holding_rows_snapshot",
        lambda *, cancellation_token=None: [{"代码": "000001"}],
    )

    service.refresh_async_snapshots(force=True, include_lhb=False)
    run_fn, kwargs = runner.jobs[-1]
    token = kwargs["cancellation_token"]

    assert service.shutdown(timeout_ms=29) is True
    with pytest.raises(TaskCancelledError, match="owner_shutdown"):
        run_fn()

    assert token.cancelled is True
    assert service._fund_rows_snapshot == []
    assert service._fund_rows_loaded is False
    wait_calls = [call for call in runner.calls if call[0] == "wait"]
    assert wait_calls
    assert all(0 <= call[2] <= 29 for call in wait_calls)


def test_stock_context_repository_stops_between_fund_store_queries():
    token = CancellationToken()

    class _Store:
        def get_latest_quarter_map(self):
            token.cancel("between_store_queries")
            return {"QFII": "2025Q4"}

        def query_change_rows(self, **_kwargs):
            raise AssertionError("cancelled repository read must not run second query")

    with pytest.raises(TaskCancelledError, match="between_store_queries"):
        snapshot_module.load_fund_holding_snapshot(
            cancellation_token=token,
            store=_Store(),
        )
