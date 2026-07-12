from __future__ import annotations

import pandas as pd
import pytest

from app.services.ui_earnings_service import EarningsRefreshService
from infra.tasks.lifecycle import CancellationToken, TaskCancelledError


class _QueuedRunner:
    def __init__(self):
        self.jobs = []
        self.active = set()
        self.calls = []

    def is_active_task(self, task_id):
        return str(task_id) in self.active

    def run_in_background(self, fn, **kwargs):
        task_id = str(kwargs.get("task_id") or "")
        self.jobs.append((fn, dict(kwargs)))
        self.active.add(task_id)
        return task_id

    def abandon_task(self, task_id, **_kwargs):
        self.calls.append(("abandon", str(task_id)))
        self.active.discard(str(task_id))
        return True

    def cancel_task(self, task_id, *, reason="cancelled"):
        self.calls.append(("cancel", str(task_id), reason))
        return True

    def wait_for_tasks(self, task_ids, *, timeout_ms):
        self.calls.append(("wait", tuple(task_ids), timeout_ms))
        return True


class _Engine:
    def __init__(self):
        self.last_sync_date = ""
        self.local_records = []
        self.last_scan_result = {}

    @staticmethod
    def get_cached_records(*, cancellation_token=None):
        if cancellation_token is not None:
            cancellation_token.raise_if_cancelled()
        return pd.DataFrame()

    @staticmethod
    def fetch_daily_surprises(*, target_publish_date=None, cancellation_token=None):
        if cancellation_token is not None:
            cancellation_token.raise_if_cancelled()
        return pd.DataFrame([{"date": target_publish_date}])


def test_earnings_manual_job_owns_token_deadline_and_shutdown_cancels(qt_application):
    runner = _QueuedRunner()
    service = EarningsRefreshService(engine=_Engine(), job_runner=runner)
    emitted = []
    service.sig_new_surprises_found.connect(lambda *_args: emitted.append("success"))

    assert service.force_manual_scan(["2026-04-16"]) is True
    run_fn, kwargs = runner.jobs[-1]
    token = kwargs["cancellation_token"]
    assert kwargs["timeout_sec"] == service.MANUAL_GAP_TIMEOUT_SECONDS

    assert service.shutdown(timeout_ms=41) is True

    assert token.cancelled is True
    with pytest.raises(TaskCancelledError, match="owner_shutdown"):
        run_fn()
    assert emitted == []
    assert any(call[0] == "wait" and call[2] == 41 for call in runner.calls)


def test_earnings_gap_fill_stops_inside_date_loop_without_success_signal(qt_application):
    token = CancellationToken()

    class _CancellingEngine(_Engine):
        def __init__(self):
            super().__init__()
            self.calls = []

        def fetch_daily_surprises(self, *, target_publish_date=None, cancellation_token=None):
            self.calls.append(target_publish_date)
            token.cancel("date_loop_cancel")
            return pd.DataFrame([{"date": target_publish_date}])

    engine = _CancellingEngine()
    service = EarningsRefreshService(engine=engine, job_runner=_QueuedRunner())
    emitted = []
    service.sig_new_surprises_found.connect(lambda *_args: emitted.append("success"))

    with pytest.raises(TaskCancelledError, match="date_loop_cancel"):
        service.run_gap_fill(
            ["2026-04-16", "2026-04-17"],
            cancellation_token=token,
        )

    assert engine.calls == ["2026-04-16"]
    assert emitted == []


def test_earnings_engine_stops_between_report_provider_loops(monkeypatch):
    from domains.earnings.engine import EarningsEngine

    token = CancellationToken()
    engine = EarningsEngine.__new__(EarningsEngine)
    calls = []

    def _guidance(*_args, **_kwargs):
        calls.append("guidance")
        token.cancel("provider_loop_cancel")
        return []

    monkeypatch.setattr(engine, "_collect_guidance_candidates", _guidance)
    monkeypatch.setattr(
        engine,
        "_collect_report_candidates",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("later provider must not run")),
    )

    with pytest.raises(TaskCancelledError, match="provider_loop_cancel"):
        engine._collect_daily_surprise_candidates(
            ["20260331", "20251231"],
            "2026-04-16",
            cancellation_token=token,
        )

    assert calls == ["guidance"]
