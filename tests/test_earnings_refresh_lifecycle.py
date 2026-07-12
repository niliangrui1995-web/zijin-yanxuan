from __future__ import annotations

import datetime as dt
import threading

from domains.global_earnings_calendar.service import GlobalEarningsCalendarService
from infra.tasks.lifecycle import CancellationToken, TaskCancelledError
from ui.components.trade_calendar import EarningsCalendarRefreshWorker


class _MemoryStore:
    def __init__(self) -> None:
        self.data = {}

    def load_json(self, key, default=None):
        return self.data.get(key, default)

    def save_json(self, key, data) -> None:
        self.data[key] = data


class _EmptyProvider:
    def fetch(self, *_args, **_kwargs):
        return []


def test_refresh_service_checks_cancellation_between_providers():
    token = CancellationToken()
    calls = []

    class _CancellingProvider:
        def fetch(self, *_args, **_kwargs):
            calls.append("first")
            token.cancel("dialog_closed")
            return []

    class _MustNotRun:
        def fetch(self, *_args, **_kwargs):
            calls.append("second")
            return []

    service = GlobalEarningsCalendarService(
        data_store=_MemoryStore(),
        universe={},
        confirmed_provider=_EmptyProvider(),
        nasdaq_provider=_MustNotRun(),
        provider=_EmptyProvider(),
        yfinance_provider=_EmptyProvider(),
        official_providers=[("Cancelling", _CancellingProvider())],
    )

    try:
        service.refresh_events(
            today=dt.date(2026, 7, 12),
            lookahead_days=1,
            cancellation_token=token,
        )
    except TaskCancelledError as exc:
        assert "dialog_closed" in str(exc)
    else:
        raise AssertionError("refresh should stop at the cancellation boundary")

    assert calls == ["first"]


def test_refresh_worker_cancels_cooperatively_without_emitting_error(qt_application):
    started = threading.Event()
    observed_tokens = []

    class _Service:
        def refresh_events(self, *, cancellation_token=None):
            observed_tokens.append(cancellation_token)
            started.set()
            cancellation_token.wait(2.0)
            cancellation_token.raise_if_cancelled()
            return ["unexpected"]

    worker = EarningsCalendarRefreshWorker(_Service(), timeout_seconds=5.0)
    results = []
    errors = []
    worker.sig_result.connect(results.append)
    worker.sig_error.connect(errors.append)
    worker.start()
    try:
        assert started.wait(0.5)
        worker.cancel("panel_disposed")
        assert worker.wait(1000)
        qt_application.processEvents()

        assert observed_tokens == [worker.cancellation_token]
        assert worker.cancellation_token.cancelled is True
        assert results == []
        assert errors == []
    finally:
        worker.cancel("test_cleanup")
        worker.wait(1000)
