# -*- coding: utf-8 -*-

from __future__ import annotations

import threading
from types import SimpleNamespace
from typing import Any, cast

import pytest

from app.services.ui_task_lifecycle_service import (
    CancellationToken,
    TaskSubmissionReceipt,
    TaskSubmissionStatus,
)
from ui.workers import central_quotes_worker as worker_module


class _Provider:
    def get_quote_request_stats(self):
        return {}


def _service():
    return worker_module.CentralQuotesService(None, _Provider(), code_supplier=lambda: [])


def _accepted_submission(task_id: object = "central-quotes-test") -> TaskSubmissionReceipt:
    return TaskSubmissionReceipt(
        token=CancellationToken(),
        task_id=str(task_id),
        status=TaskSubmissionStatus.ACCEPTED,
    )


def test_quote_result_state_prefers_freshness_over_original_source():
    failed_provider = {"consecutive_failures": 3, "cooldown_until": 9_999_999_999}
    for freshness in ("network", "cache"):
        state = worker_module._quote_result_state(
            {
                "quotes": {"000001": {"close": 10.0, "source": "eastmoney", "quote_freshness": freshness}},
                "provider_stats": failed_provider,
            }
        )
        assert state[-1] is False
    stale_state = worker_module._quote_result_state(
        {
            "quotes": {"000001": {"close": 10.0, "source": "eastmoney", "quote_freshness": "stale"}},
            "provider_stats": failed_provider,
        }
    )
    assert stale_state[-1] is True


def test_realtime_terminal_callbacks_are_applied_once_per_generation(monkeypatch):
    service = _service()
    processed = []
    failures = []
    monkeypatch.setattr(
        worker_module,
        "_process_quote_fetch_result",
        lambda *args, **kwargs: processed.append((args, kwargs)),
    )
    service._record_failure = lambda reason, **kwargs: failures.append((reason, kwargs))
    try:
        begun, active = service.state.begin_fetch(1, started_at=1.0)
        assert begun is True
        callback_kwargs = {
            "service": service,
            "codes": {"000001"},
            "reason": "timer",
            "fetch_token": active.generation,
            "timing": {"submitted_at": 1.0},
        }

        worker_module._handle_realtime_fetch_result({}, **callback_kwargs)
        worker_module._handle_realtime_fetch_result({}, **callback_kwargs)
        worker_module._handle_realtime_fetch_error("late error", service=service, fetch_token=active.generation)

        assert len(processed) == 1
        assert failures == []

        begun, active = service.state.begin_fetch(1, started_at=2.0)
        assert begun is True
        worker_module._handle_realtime_fetch_error("first error", service=service, fetch_token=active.generation)
        worker_module._handle_realtime_fetch_result(
            {},
            service=service,
            codes={"000001"},
            reason="timer",
            fetch_token=active.generation,
            timing={"submitted_at": 2.0},
        )

        assert len(processed) == 1
        assert failures == [
            ("first error", {"expected_generation": active.generation}),
        ]
    finally:
        service.shutdown()


def test_realtime_submission_rejection_rolls_back_fetch_state(monkeypatch):
    class _RejectingRunner:
        def __init__(self):
            self.tokens = []

        def run_in_background(self, fn, **kwargs):
            del fn
            self.tokens.append(kwargs["cancellation_token"])
            return str(getattr(kwargs.get("task_id"), "task_id", kwargs.get("task_id")) or "rejected")

        def is_task_token_active(self, task_id, token):
            del task_id, token
            return False

        def is_task_unsettled(self, task_id):
            del task_id
            return False

    runner = _RejectingRunner()
    service = _service()
    monkeypatch.setattr(worker_module, "task_manager", runner)
    monkeypatch.setattr(worker_module, "_slow_fetch_threshold", lambda *_args, **_kwargs: 1.0)
    try:
        worker_module._submit_realtime_fetch(service, {"000001"}, "timer")

        assert service.state.fetching is False
        assert service._task_lifecycle.active_names == ()
        assert runner.tokens[0].cancelled is True
        assert runner.tokens[0].reason == "submission_rejected"
    finally:
        service.shutdown()


def test_realtime_submission_exception_rolls_back_and_reraises(monkeypatch):
    class _RaisingRunner:
        @staticmethod
        def run_in_background(fn, **kwargs):
            del fn, kwargs
            raise RuntimeError("scheduler unavailable")

    service = _service()
    monkeypatch.setattr(worker_module, "task_manager", _RaisingRunner())
    monkeypatch.setattr(worker_module, "_slow_fetch_threshold", lambda *_args, **_kwargs: 1.0)
    try:
        with pytest.raises(RuntimeError, match="scheduler unavailable"):
            worker_module._submit_realtime_fetch(service, {"000001"}, "timer")

        assert service.state.fetching is False
        assert service._task_lifecycle.active_names == ()
        assert service._task_lifecycle.submissions_settled_for(("realtime_poll",)) is True
    finally:
        service.shutdown()


def test_realtime_unknown_submission_receipt_rolls_back_and_cancels(monkeypatch):
    service = _service()
    token = CancellationToken()
    cancelled = []
    monkeypatch.setattr(
        worker_module,
        "_submit_central_task",
        lambda *_args, **_kwargs: TaskSubmissionReceipt(
            token=token,
            task_id="central_quotes",
            status=TaskSubmissionStatus.UNKNOWN,
        ),
    )
    monkeypatch.setattr(
        service._task_lifecycle,
        "cancel",
        lambda name, *, reason: cancelled.append((name, reason)) or True,
    )
    monkeypatch.setattr(worker_module, "_slow_fetch_threshold", lambda *_args, **_kwargs: 1.0)
    try:
        worker_module._submit_realtime_fetch(service, {"000001"}, "timer")

        assert service.state.fetching is False
        assert cancelled == [("realtime_poll", "submission_unconfirmed")]
    finally:
        service.shutdown()


def test_realtime_none_submission_result_fails_closed(monkeypatch):
    service = _service()
    cancelled = []
    monkeypatch.setattr(worker_module, "_submit_central_task", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        service._task_lifecycle,
        "cancel",
        lambda name, *, reason: cancelled.append((name, reason)) or True,
    )
    monkeypatch.setattr(worker_module, "_slow_fetch_threshold", lambda *_args, **_kwargs: 1.0)
    try:
        worker_module._submit_realtime_fetch(service, {"000001"}, "timer")

        assert service.state.fetching is False
        assert cancelled == [("realtime_poll", "submission_unconfirmed")]
    finally:
        service.shutdown()


@pytest.mark.parametrize(
    "receipt",
    [
        TaskSubmissionReceipt(
            token=CancellationToken(),
            task_id="invalid-status",
            status=cast(Any, "accepted"),
        ),
        TaskSubmissionReceipt(
            token=cast(Any, object()),
            task_id="invalid-token",
            status=TaskSubmissionStatus.ACCEPTED,
        ),
    ],
)
def test_central_submission_receipt_requires_exact_accepted_contract(receipt):
    assert worker_module._central_submission_failure_reason(receipt) == "submission_unconfirmed"


def test_submit_central_task_closed_returns_explicit_rejection():
    service = SimpleNamespace(_closed=True)

    receipt = worker_module._submit_central_task(
        service,
        "closed",
        lambda _token: None,
        None,
        None,
        "closed-task",
        1.0,
    )

    assert receipt.status is TaskSubmissionStatus.REJECTED
    assert receipt.task_id == "closed-task"
    assert receipt.token.cancelled is True
    assert receipt.token.reason == "owner_shutdown"


def test_realtime_none_runner_is_unconfirmed_through_real_lifecycle(monkeypatch):
    class _NoneRunner:
        def __init__(self):
            self.tokens = []

        def run_in_background(self, fn, **kwargs):
            del fn
            self.tokens.append(kwargs["cancellation_token"])
            return None

        @staticmethod
        def cancel_task(task_id, *, reason):
            del task_id, reason
            return True

        @staticmethod
        def wait_for_tasks(task_ids, *, timeout_ms):
            del task_ids, timeout_ms
            return True

    runner = _NoneRunner()
    service = _service()
    monkeypatch.setattr(worker_module, "task_manager", runner)
    monkeypatch.setattr(worker_module, "_slow_fetch_threshold", lambda *_args, **_kwargs: 1.0)
    try:
        worker_module._submit_realtime_fetch(service, {"000001"}, "timer")

        assert service.state.fetching is False
        assert runner.tokens[0].cancelled is True
        assert runner.tokens[0].reason == "submission_unconfirmed"
        assert service._task_lifecycle.active_names == ()
    finally:
        service.shutdown()


def test_off_market_unknown_submission_receipt_rolls_back_and_cancels(monkeypatch):
    service = _service()
    token = CancellationToken()
    cancelled = []
    monkeypatch.setattr(
        worker_module,
        "_submit_central_task",
        lambda *_args, **_kwargs: TaskSubmissionReceipt(
            token=token,
            task_id="central_quotes_off_market_snapshot_1",
            status=TaskSubmissionStatus.UNKNOWN,
        ),
    )
    monkeypatch.setattr(
        service._task_lifecycle,
        "cancel",
        lambda name, *, reason: cancelled.append((name, reason)) or True,
    )
    monkeypatch.setattr(worker_module, "_slow_fetch_threshold", lambda *_args, **_kwargs: 1.0)
    try:
        service._emit_off_market_snapshot({"000001"})

        assert service._off_market_snapshot_fetching is False
        assert cancelled == [("off_market_snapshot", "submission_unconfirmed")]
    finally:
        service.shutdown()


def test_shutdown_and_realtime_registration_are_linearized(monkeypatch):
    submit_started = threading.Event()
    release_submit = threading.Event()
    shutdown_finished = threading.Event()
    registration_closed_states = []

    class _Timer:
        @staticmethod
        def stop():
            return None

    class _Lifecycle:
        @staticmethod
        def cancel(name, *, reason):
            del name, reason
            return False

        @staticmethod
        def shutdown(*, timeout_ms):
            del timeout_ms
            return True

        @staticmethod
        def is_current(name, token):
            del name, token
            return True

    service = SimpleNamespace(
        _fetch_submission_lock=threading.RLock(),
        _closed=False,
        state=worker_module.QuoteRuntimeState(),
        data_provider=object(),
        _task_lifecycle=_Lifecycle(),
        _timer=_Timer(),
        _pending_fetch_timer=_Timer(),
        _off_market_snapshot_generation=0,
        _off_market_snapshot_fetching=False,
    )

    def _blocking_submit(*_args, **_kwargs):
        submit_started.set()
        assert release_submit.wait(1.0)
        registration_closed_states.append(service._closed)
        return _accepted_submission()

    monkeypatch.setattr(worker_module, "_submit_central_task", _blocking_submit)
    monkeypatch.setattr(worker_module, "_slow_fetch_threshold", lambda *_args, **_kwargs: 1.0)

    submit_thread = threading.Thread(
        target=worker_module._submit_realtime_fetch,
        args=(service, {"000001"}, "timer"),
    )
    def _shutdown() -> None:
        worker_module.CentralQuotesService.shutdown(cast(Any, service))
        shutdown_finished.set()

    shutdown_thread = threading.Thread(target=_shutdown)
    submit_thread.start()
    assert submit_started.wait(1.0)
    shutdown_thread.start()
    assert shutdown_finished.wait(0.05) is False

    release_submit.set()
    submit_thread.join(timeout=1.0)
    shutdown_thread.join(timeout=1.0)

    assert not submit_thread.is_alive()
    assert not shutdown_thread.is_alive()
    assert registration_closed_states == [False]
    assert service._closed is True
    assert service.state.fetching is False
    assert shutdown_finished.is_set()


def test_off_market_submission_rejection_and_closed_guard_reset_flag(monkeypatch):
    class _RejectingRunner:
        def __init__(self):
            self.calls = 0

        def run_in_background(self, fn, **kwargs):
            del fn
            self.calls += 1
            return str(getattr(kwargs.get("task_id"), "task_id", kwargs.get("task_id")) or "rejected")

        @staticmethod
        def is_task_token_active(task_id, token):
            del task_id, token
            return False

        @staticmethod
        def is_task_unsettled(task_id):
            del task_id
            return False

    runner = _RejectingRunner()
    service = _service()
    monkeypatch.setattr(worker_module, "task_manager", runner)
    monkeypatch.setattr(worker_module, "_slow_fetch_threshold", lambda *_args, **_kwargs: 1.0)
    try:
        service._emit_off_market_snapshot({"000001"})
        assert runner.calls == 1
        assert service._off_market_snapshot_fetching is False

        service.shutdown()
        service._emit_off_market_snapshot({"000001"})
        assert runner.calls == 1
        assert service._off_market_snapshot_fetching is False
    finally:
        service.shutdown()


def test_off_market_submission_exception_rolls_back_and_reraises(monkeypatch):
    class _RaisingRunner:
        @staticmethod
        def run_in_background(fn, **kwargs):
            del fn, kwargs
            raise RuntimeError("off-market scheduler unavailable")

    service = _service()
    monkeypatch.setattr(worker_module, "task_manager", _RaisingRunner())
    monkeypatch.setattr(worker_module, "_slow_fetch_threshold", lambda *_args, **_kwargs: 1.0)
    try:
        with pytest.raises(RuntimeError, match="off-market scheduler unavailable"):
            service._emit_off_market_snapshot({"000001"})

        assert service._off_market_snapshot_fetching is False
        assert service._task_lifecycle.active_names == ()
        assert service._task_lifecycle.submissions_settled_for(("off_market_snapshot",)) is True
    finally:
        service.shutdown()


def test_central_quote_phase_metrics_split_worker_queue_and_publish(monkeypatch):
    assert worker_module._quote_refresh_freshness_fields({"recent_cache_hit_count": 1}) == {}
    perf_values = iter((2.0, 4.0, 7.0, 7.25))
    monkeypatch.setattr(worker_module.time, "perf_counter", lambda: next(perf_values))
    metrics = []
    logs = []
    published = []
    monkeypatch.setattr(
        worker_module,
        "record_metric",
        lambda name, value, **kwargs: metrics.append((name, float(value), kwargs)),
    )
    monkeypatch.setattr(
        worker_module,
        "emit_structured_log",
        lambda event, **kwargs: logs.append((event, kwargs)),
    )
    service = SimpleNamespace(
        _fetch_quote_payload=lambda codes: {"quotes": {code: {"close": 10.0} for code in codes}},
        publish_external_quotes=lambda quotes, **kwargs: published.append((quotes, kwargs)),
    )
    timing = {"submitted_at": 1.0}

    payload = worker_module._fetch_quote_payload_timed(service, {"000001"}, timing)
    worker_module._record_and_publish_quote_refresh(
        service,
        codes={"000001"},
        quotes=payload["quotes"],
        has_valid=True,
        provider_failed=False,
        elapsed_ms=5_000.0,
        reason="cache_reload",
        quote_request_stats={
            "recent_network_result_count": 1,
            "recent_cache_hit_count": 0,
            "recent_stale_result_count": 0,
            "recent_result_count": 1,
            "recent_missing_result_count": 0,
            "recent_latest_quote_time": "2026-07-22T10:24:06+08:00",
        },
        timing=timing,
        callback_started_at=6.0,
    )

    metric_values = {name: value for name, value, _kwargs in metrics}
    assert metric_values["quote_submit_queue_ms"] == 1_000.0
    assert metric_values["quote_worker_ms"] == 2_000.0
    assert metric_values["quote_result_queue_delay_ms"] == 2_000.0
    assert metric_values["quote_publish_ms"] == 250.0
    freshness_metrics = {
        item[2]["tags"]["freshness"]: item[1]
        for item in metrics
        if item[0] == "quote_refresh_result_count"
    }
    assert freshness_metrics == {"network": 1.0, "cache": 0.0, "stale": 0.0}
    assert all(item[2]["tags"]["reason"] == "cache_reload" for item in metrics)
    assert published[0][1]["source"] == "central_quotes.realtime"
    assert logs[0][1]["result_queue_delay_ms"] == 2_000.0
    assert logs[0][1]["network_count"] == 1
    assert logs[0][1]["cache_count"] == 0
    assert logs[0][1]["stale_count"] == 0
    assert logs[0][1]["latest_quote_time"] == "2026-07-22T10:24:06+08:00"


def test_central_quote_provider_stats_timer_failure_and_health_states(monkeypatch, qt_application):
    provider = _Provider()
    monkeypatch.setattr(
        worker_module,
        "read_provider_health",
        lambda _provider: SimpleNamespace(request_stats={"a": 1}, eastmoney_cooldown_until=0),
    )
    assert worker_module._provider_request_stats(provider) == {"a": 1}
    monkeypatch.setattr(
        worker_module,
        "read_provider_health",
        lambda _provider: SimpleNamespace(request_stats={}, eastmoney_cooldown_until=0),
    )
    provider.get_quote_request_stats = lambda: (_ for _ in ()).throw(RuntimeError("bad"))
    assert worker_module._provider_request_stats(provider) == {}
    provider.get_quote_request_stats = lambda: "bad"
    assert worker_module._provider_request_stats(provider) == {}

    service = _service()
    try:
        cooldowns = []
        service._poller = SimpleNamespace(
            enter_realtime_cooldown=lambda *args, **kwargs: cooldowns.append((args, kwargs))
        )
        service._FAILURE_THRESHOLD = 2
        service._COOLDOWN_TICKS = 4
        service._record_failure("one")
        assert service._consecutive_failures == 1
        service._record_failure("two")
        assert service._circuit_breaker_cooldown == 4
        assert cooldowns[0][1]["cooldown_sec"] == 300

        class _BrokenTimer:
            def isActive(self):
                raise RuntimeError("gone")

            def start(self, interval):
                self.interval = interval

            def stop(self):
                pass

        service._timer.stop()
        service._timer = _BrokenTimer()
        assert not service._timer_is_active()
        service._closed = True
        assert not service._ensure_timer_running()
        service._closed = False
        assert service._ensure_timer_running()

        monkeypatch.setattr(service, "_timer_is_active", lambda: True)
        service._closed = True
        assert (
            service._heartbeat_runtime_status(
                quote_refreshable=True,
                market_status="交易中",
                runtime_stats={},
                cooldown_left=0,
                realtime_cache_size=0,
                last_success_age=None,
            )[0]
            == "closed"
        )
        service._closed = False
        monkeypatch.setattr(service, "_timer_is_active", lambda: False)
        assert (
            service._heartbeat_runtime_status(
                quote_refreshable=True,
                market_status="交易中",
                runtime_stats={},
                cooldown_left=0,
                realtime_cache_size=0,
                last_success_age=None,
            )[0]
            == "degraded_scheduler_inactive"
        )
        monkeypatch.setattr(service, "_timer_is_active", lambda: True)
        assert (
            service._heartbeat_runtime_status(
                quote_refreshable=False,
                market_status="收盘",
                runtime_stats={},
                cooldown_left=0,
                realtime_cache_size=0,
                last_success_age=None,
            )[0]
            == "paused_market_closed"
        )

        service._is_fetching = True
        assert (
            service._heartbeat_runtime_status(
                quote_refreshable=True,
                market_status="开盘集合竞价",
                runtime_stats={"inflight": "bad", "consecutive_failures": "bad"},
                cooldown_left=0,
                realtime_cache_size=0,
                last_success_age=None,
            )[0]
            == "opening_warmup_fetching"
        )
        assert (
            service._heartbeat_runtime_status(
                quote_refreshable=True,
                market_status="交易中",
                runtime_stats={},
                cooldown_left=0,
                realtime_cache_size=0,
                last_success_age=None,
            )[0]
            == "fetching"
        )
    finally:
        service._closed = True


def test_central_quote_status_warmup_stats_and_pressure(monkeypatch, qt_application):
    service = _service()
    try:
        assert service._rt_cache_status_text(2, "x") == "2"
        assert service._rt_cache_status_text(0, "opening_warmup") == "首轮预热中(0)"
        assert service._rt_cache_status_text(0, "fetching") == "首轮待写入(0)"
        assert service._owner_thread_status_text({}, True) == "存活"
        assert service._owner_thread_status_text({"owner_thread_applicable": True}, False) == "已停止"
        assert service._owner_thread_status_text({}, False) == "未使用(HTTP行情)"

        monkeypatch.setattr(
            worker_module.MarketCalendar,
            "get_market_status",
            lambda _market: (_ for _ in ()).throw(RuntimeError("bad")),
        )
        assert service._market_status_text() == "unknown"

        monkeypatch.setattr(worker_module, "_OPENING_WARMUP_FETCH_LIMIT", 2)
        codes = {"000001", "000002", "000003"}
        assert service._opening_warmup_codes(codes, market_status="交易中") == codes
        first = service._opening_warmup_codes(codes, market_status="开盘集合竞价")
        second = service._opening_warmup_codes(codes, market_status="开盘集合竞价")
        assert len(first) == len(second) == 2
        assert first != second
        assert service._opening_warmup_codes({"000001"}, market_status="开盘集合竞价") == {"000001"}

        assert service._stats_int({"x": "bad"}, "x") == 0
        assert service._stats_float({"x": "bad"}, "x") == 0.0
        assert service._stats_time(123) == 123.0
        assert service._stats_time("") == 0.0
        assert service._stats_time("bad") == 0.0
        parsed = service._stats_time("2026-07-15T09:30:00")
        assert parsed > 0

        assert service._quote_stats_fallback_pressure({}, now=100, label="x") == (False, "")
        stats = {
            "recent_requested_count": 120,
            "recent_pending_count": 110,
            "recent_cache_hit_count": 0,
            "recent_elapsed_ms": 12000,
            "recent_status": "partial_fallback",
            "recent_source_layers": ["sina_fallback"],
            "recent_ended_at_ts": 95,
        }
        pressure, reason = service._quote_stats_fallback_pressure(stats, now=100, label="provider")
        assert pressure and "provider fallback pressure" in reason
        stats["recent_ended_at_ts"] = 0
        assert service._quote_stats_fallback_pressure(stats, now=100, label="provider") == (False, "")
    finally:
        service.shutdown()


def test_central_quote_fallback_code_rotation(monkeypatch, qt_application):
    service = _service()
    try:
        monkeypatch.setattr(
            worker_module,
            "read_provider_health",
            lambda _provider: SimpleNamespace(request_stats={}, eastmoney_cooldown_until=200),
        )
        assert service._quote_fallback_cooldown_left({"quote_cooldown_until": "bad"}, now=100) == 100

        codes = {f"{index:06d}" for index in range(5)}
        monkeypatch.setattr(worker_module, "_FALLBACK_PRESSURE_FETCH_LIMIT", 2)
        monkeypatch.setattr(worker_module.time, "time", lambda: 100.0)
        monkeypatch.setattr(service, "_quote_fallback_cooldown_left", lambda *_args, **_kwargs: 10)
        monkeypatch.setattr(service, "_recent_quote_fallback_pressure", lambda **_kwargs: (False, ""))
        first = service._fallback_pressure_codes(codes, provider_stats={}, market_status="交易中")
        second = service._fallback_pressure_codes(codes, provider_stats={}, market_status="交易中")
        third = service._fallback_pressure_codes(codes, provider_stats={}, market_status="交易中")
        assert len(first) == len(second) == len(third) == 2
        assert service._fallback_pressure_codes(codes, provider_stats={}, market_status="开盘集合竞价") == codes

    finally:
        service.shutdown()


def test_central_quote_off_market_callbacks_and_realtime_result_error_paths(monkeypatch, qt_application):
    service = _service()
    captured = []

    def _capture_submit(_service, name, fn, on_success, on_error, task_id, _timeout):
        captured.append((name, fn, on_success, on_error))
        return _accepted_submission(task_id)

    monkeypatch.setattr(
        worker_module,
        "_submit_central_task",
        _capture_submit,
    )
    published = []
    monkeypatch.setattr(
        service,
        "publish_external_quotes",
        lambda quotes, **kwargs: published.append((quotes, kwargs)) or quotes,
    )
    try:
        service._emit_off_market_snapshot({"000001"})
        name, bg, success, error = captured.pop()
        assert name == "off_market_snapshot"
        generation = service._off_market_snapshot_generation
        service._off_market_snapshot_generation += 1
        success({"quotes": {"000001": {"close": 10}}})
        assert not published
        service._off_market_snapshot_generation = generation
        success({"quotes": {"000001": {"close": 0}}})
        assert not published
        service._off_market_snapshot_fetching = True
        service._off_market_snapshot_generation = generation
        success({"quotes": {"000001": {"close": 10}}})
        assert published[-1][1]["source"] == "central_quotes.off_market"
        service._off_market_snapshot_fetching = True
        error_generation = service._off_market_snapshot_generation
        service._off_market_snapshot_generation += 1
        error("stale")
        assert service._off_market_snapshot_fetching
        service._off_market_snapshot_generation = error_generation
        error("bad")
        assert not service._off_market_snapshot_fetching

        captured.clear()
        service._closed = False
        service._get_all_active_codes = lambda: {"000001"}
        service._run_maintenance = lambda **_kwargs: {}
        service._poller = SimpleNamespace(is_online=lambda: True, get_runtime_stats=lambda: {})
        service._ensure_timer_running = lambda: True
        service._market_status_text = lambda: "交易中"
        service._observe_quote_window = lambda _value: None
        service._opening_warmup_codes = lambda codes, **_kwargs: codes
        service._fallback_pressure_codes = lambda codes, **_kwargs: codes
        monkeypatch.setattr(worker_module.MarketCalendar, "is_quote_refresh_time", lambda: True)
        service._trigger_fetch_for_reason("manual")
        name, bg, result, failure = captured.pop()
        assert name == "realtime_poll"
        token = service._fetch_generation
        service._fetch_generation += 1
        result({"quotes": {"000001": {"close": 10, "source": "eastmoney"}}})
        assert service._is_fetching
        service._fetch_generation = token
        service._closed = True
        result({})
        assert not service._is_fetching
        service._closed = False
        service._is_fetching = True
        result({"quotes": {"000001": {"close": 10, "source": "eastmoney"}}})
        assert not service._is_fetching

        service._is_fetching = True
        service._fetch_generation = token + 1
        failure("stale")
        assert service._is_fetching
        service._fetch_generation = token
        service._closed = True
        failure("closed")
        assert not service._is_fetching
    finally:
        service._closed = True
        service._task_lifecycle.shutdown(timeout_ms=10)
