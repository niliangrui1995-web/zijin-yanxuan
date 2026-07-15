# -*- coding: utf-8 -*-

from __future__ import annotations

import datetime as dt
from types import SimpleNamespace

import pytest

from app.services.ui_task_lifecycle_service import CancellationToken
from ui.tabs import asian_market_runtime as runtime
from ui.tabs import asian_market_workers as workers


class _Signal:
    def __init__(self):
        self.calls = []

    def connect(self, callback):
        self.calls.append(callback)

    def emit(self, *args):
        self.calls.append(args)


class _Label:
    def __init__(self):
        self.text = ""

    def setText(self, value):
        self.text = value


def test_runtime_status_and_worker_dispatch_cover_fallbacks():
    calls = []
    tab = SimpleNamespace(
        _set_asian_status=lambda *args, **kwargs: calls.append((args, kwargs)),
        lbl_status=_Label(),
    )
    runtime._set_tab_status(tab, "primary", "segment", freshness="fresh", next_step="next")
    assert calls == [(("primary", "segment"), {"freshness": "fresh", "next_step": "next"})]

    tab._set_asian_status = None
    runtime._set_tab_status(tab, "primary", "", "segment")
    assert tab.lbl_status.text == "primary | segment"

    assert runtime.runtime_state_text("running") == "运行"
    assert runtime.runtime_state_text("custom") == "custom"
    assert runtime.call_worker_method(SimpleNamespace(), "go") is None
    assert runtime.call_worker_method(SimpleNamespace(worker=object()), "go") is None
    assert runtime.call_worker_method(SimpleNamespace(worker=SimpleNamespace(go=lambda: 7)), "go") == 7

    def _fail():
        raise RuntimeError("boom")

    failing = SimpleNamespace(worker=SimpleNamespace(go=_fail))
    assert runtime.call_worker_method(failing, "go") is None
    assert (
        runtime.worker_resume_auto_refresh(SimpleNamespace(worker=SimpleNamespace(resume_auto_refresh=lambda: 1))) == 1
    )
    assert (
        runtime.worker_pause_for_cache_sync(SimpleNamespace(worker=SimpleNamespace(pause_for_cache_sync=lambda: 2)))
        == 2
    )
    assert runtime.worker_trigger_refresh(SimpleNamespace(worker=SimpleNamespace(trigger_refresh=lambda: 3))) == 3


class _Calendar:
    current = dt.datetime(2026, 7, 13, 17, 0)

    @classmethod
    def now(cls, _market):
        return cls.current

    @staticmethod
    def from_timestamp(value, _market):
        return dt.datetime.fromtimestamp(value)


def _cache_tab(**overrides):
    calls = []
    defaults = dict(
        _is_fetching_cache=False,
        _pending_auto_cache_sync=False,
        _cache_sync_wait_deadline=None,
        _get_cache_latest_trade_date=lambda: dt.date(2026, 7, 10),
        _get_expected_latest_trade_date=lambda: dt.date(2026, 7, 13),
        _get_cache_latest_trade_dates=lambda: {"TW": dt.date(2026, 7, 10)},
        _get_expected_latest_trade_dates=lambda: {
            "TW": dt.date(2026, 7, 13),
            "HK": None,
        },
        _set_runtime_state=lambda value: calls.append(("state", value)),
        _continue_auto_cache_sync=lambda: calls.append(("continue",)),
        _set_asian_status=lambda *args, **kwargs: calls.append(("status", args, kwargs)),
        worker=SimpleNamespace(pause_for_cache_sync=lambda: calls.append(("pause",))),
    )
    defaults.update(overrides)
    return SimpleNamespace(**defaults), calls


def test_check_auto_cache_covers_fresh_stale_weekend_and_guards(monkeypatch):
    from app.services import ui_market_calendar_service

    monkeypatch.setattr(ui_market_calendar_service, "MarketCalendar", _Calendar)
    singles = []
    monkeypatch.setattr(runtime.QTimer, "singleShot", lambda delay, callback: singles.append((delay, callback)))

    guarded, _ = _cache_tab(_is_fetching_cache=True)
    runtime.check_auto_cache(guarded)
    assert not singles

    fresh, fresh_calls = _cache_tab(
        _get_cache_latest_trade_date=lambda: dt.date(2026, 7, 13),
        _get_expected_latest_trade_date=lambda: dt.date(2026, 7, 13),
        _get_cache_latest_trade_dates=lambda: {"TW": dt.date(2026, 7, 13)},
        _get_expected_latest_trade_dates=lambda: {"TW": dt.date(2026, 7, 13)},
    )
    monkeypatch.setattr(runtime, "cache_mtime", lambda _path: dt.datetime(2026, 7, 13, 17).timestamp())
    runtime.check_auto_cache(fresh)
    assert fresh_calls == []

    _Calendar.current = dt.datetime(2026, 7, 13, 10, 0)
    stale, calls = _cache_tab()
    monkeypatch.setattr(runtime, "cache_mtime", lambda _path: 0)
    runtime.check_auto_cache(stale)
    assert stale._pending_auto_cache_sync is True
    assert ("state", "paused_for_cache_sync") in calls
    assert ("pause",) in calls
    assert singles[-1][0] == 0

    _Calendar.current = dt.datetime(2026, 7, 12, 10, 0)  # Sunday exercises weekend rollback.
    no_market_helpers, calls = _cache_tab()
    del no_market_helpers._get_cache_latest_trade_dates
    del no_market_helpers._get_expected_latest_trade_dates
    runtime.check_auto_cache(no_market_helpers)
    assert no_market_helpers._pending_auto_cache_sync
    assert calls


class _FakeThread:
    def __init__(self, *, cancellation_token=None):
        self.cancellation_token = cancellation_token
        self.finished = _Signal()
        self.started = False
        self.deleted = False

    def start(self):
        self.started = True

    def deleteLater(self):
        self.deleted = True

    def isRunning(self):
        return self.started


def test_continue_auto_cache_sync_covers_all_wait_and_start_paths(monkeypatch):
    monkeypatch.setattr(runtime.QTimer, "singleShot", lambda delay, callback: None)
    lifecycle = SimpleNamespace(begin=lambda *args, **kwargs: "token")
    monkeypatch.setattr(runtime, "task_lifecycle_for", lambda tab: lifecycle)
    monkeypatch.setattr(runtime, "AsianCacheFetcherThread", _FakeThread)

    tab, _ = _cache_tab(_pending_auto_cache_sync=False)
    runtime.continue_auto_cache_sync(tab)
    assert tab.cache_thread if hasattr(tab, "cache_thread") else True

    running_cache = _FakeThread()
    running_cache.started = True
    tab, _ = _cache_tab(_pending_auto_cache_sync=True, cache_thread=running_cache)
    runtime.continue_auto_cache_sync(tab)
    assert tab.cache_thread is running_cache

    calls = []
    busy_worker = SimpleNamespace(isRunning=lambda: True, wait_for_cycle_idle=lambda timeout: False)
    tab, _ = _cache_tab(
        _pending_auto_cache_sync=True,
        cache_thread=None,
        worker=busy_worker,
        _cache_sync_wait_deadline=dt.datetime.now() - dt.timedelta(seconds=1),
        _set_asian_status=lambda *args, **kwargs: calls.append(args),
    )
    runtime.continue_auto_cache_sync(tab)
    assert tab._pending_auto_cache_sync is False
    assert "超时" in calls[-1][0]

    tab, _ = _cache_tab(
        _pending_auto_cache_sync=True,
        cache_thread=None,
        worker=busy_worker,
        _cache_sync_wait_deadline=dt.datetime.now() + dt.timedelta(seconds=30),
    )
    runtime.continue_auto_cache_sync(tab)
    assert tab._pending_auto_cache_sync is True
    assert tab.cache_thread is None

    idle_worker = SimpleNamespace(isRunning=lambda: True, wait_for_cycle_idle=lambda timeout: True)
    tab, _ = _cache_tab(_pending_auto_cache_sync=True, cache_thread=None, worker=idle_worker)
    runtime.continue_auto_cache_sync(tab)
    assert tab._is_fetching_cache is True
    assert isinstance(tab.cache_thread, _FakeThread)
    assert tab.cache_thread.started
    assert len(tab.cache_thread.finished.calls) == 2


@pytest.mark.parametrize(
    ("complete", "cancelled", "shutting_down", "expected"),
    [
        (False, False, False, []),
        (True, True, False, []),
        (True, False, True, []),
        (True, False, False, [(True, "done")]),
    ],
)
def test_handle_auto_cache_thread_finished_lifecycle_branches(complete, cancelled, shutting_down, expected):
    calls = []
    token = SimpleNamespace(cancelled=cancelled)
    lifecycle = SimpleNamespace(complete=lambda *args: complete)
    thread = SimpleNamespace(result_success=True, result_message="done", cancellation_token=token)
    tab = SimpleNamespace(
        cache_thread=thread,
        _task_lifecycle=lifecycle,
        _asian_shutting_down=shutting_down,
        _runtime_cleanup_done=False,
        _on_auto_cache_finished=lambda *args: calls.append(args),
    )
    runtime._handle_auto_cache_thread_finished(tab, thread)
    assert tab.cache_thread is None
    assert calls == expected


def test_handle_auto_cache_thread_finished_without_lifecycle_and_cleanup_guard():
    calls = []
    thread = SimpleNamespace(result_success=False, result_message=None, cancellation_token=None)
    tab = SimpleNamespace(
        cache_thread=object(),
        _task_lifecycle=None,
        _asian_shutting_down=False,
        _runtime_cleanup_done=True,
        _on_auto_cache_finished=lambda *args: calls.append(args),
    )
    runtime._handle_auto_cache_thread_finished(tab, thread)
    assert calls == []
    assert tab.cache_thread is not None


def _health_tab(**overrides):
    defaults = dict(
        _last_health_log_at=0.0,
        _last_asian_success_at=None,
        _last_health_signature=None,
        _is_fetching_cache=False,
        _pending_auto_cache_sync=False,
        _runtime_state_text=lambda: "running",
        _is_quote_refresh_open=lambda: True,
        row_data=[{}],
        worker=SimpleNamespace(isRunning=lambda: True),
        cache_thread=None,
    )
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def test_log_asian_health_covers_throttle_closed_market_and_changes(monkeypatch):
    class _Now(dt.datetime):
        stamp = 2_000_000_000.0

        @classmethod
        def now(cls, tz=None):
            return cls.fromtimestamp(cls.stamp)

    monkeypatch.setattr(runtime.dt, "datetime", _Now)
    tab = _health_tab(_last_health_log_at=1_999_999_950.0)
    runtime.log_asian_health(tab)
    assert tab._last_health_log_at == 1_999_999_950.0

    tab = _health_tab(_last_health_log_at=1_999_999_000.0, _is_quote_refresh_open=lambda: False)
    signature = ("running", "-", False, True, 1)
    tab._last_health_signature = signature
    runtime.log_asian_health(tab)
    assert tab._last_health_log_at == 1_999_999_000.0

    tab._last_health_signature = None
    runtime.log_asian_health(tab)
    assert tab._last_health_log_at == 2_000_000_000.0
    assert tab._last_health_signature == signature

    tab = _health_tab(
        _last_health_log_at=0.0,
        _last_asian_success_at=dt.datetime(2026, 7, 15, 9),
        _is_fetching_cache=True,
        cache_thread=SimpleNamespace(isRunning=lambda: True),
    )
    runtime.log_asian_health(tab)
    assert tab._last_health_signature[1] == "2026-07-15 09:00:00"


@pytest.mark.parametrize(
    ("fetching", "open_now", "state", "expected_state", "worker_method"),
    [
        (True, True, "running", None, None),
        (False, True, "manual_refresh_once", None, None),
        (False, True, "paused", "running", "resume"),
        (False, False, "running", "paused_for_cache_sync", "pause"),
    ],
)
def test_on_minute_tick_runtime_branches(fetching, open_now, state, expected_state, worker_method):
    calls = []
    worker = SimpleNamespace(
        resume_auto_refresh=lambda: calls.append("resume"),
        pause_for_cache_sync=lambda: calls.append("pause"),
    )
    tab = SimpleNamespace(
        _refresh_market_status_rows=lambda: calls.append("rows"),
        _is_fetching_cache=fetching,
        _pending_auto_cache_sync=False,
        _is_quote_refresh_open=lambda: open_now,
        _asian_runtime_state=state,
        _set_runtime_state=lambda value: calls.append(value),
        worker=worker,
        _check_auto_cache=lambda: calls.append("cache"),
        _log_asian_health=lambda: calls.append("health"),
    )
    runtime.on_minute_tick(tab)
    assert calls[0] == "rows"
    assert calls[-2:] == ["cache", "health"]
    if expected_state:
        assert expected_state in calls
    if worker_method:
        assert worker_method in calls


def test_refresh_market_status_rows_covers_empty_missing_header_and_changed_rows():
    runtime.refresh_market_status_rows(SimpleNamespace(model=SimpleNamespace(row_data=[])))
    runtime.refresh_market_status_rows(SimpleNamespace(model=SimpleNamespace(row_data=[{"code": "x"}], headers=[])))

    signal = _Signal()
    model = SimpleNamespace(
        row_data=[
            {"代码": "", "状态": "-"},
            {"代码": "2330.TW", "状态": "open"},
            {"代码": "plain", "状态": "old"},
        ],
        headers=["状态"],
        index=lambda row, col: (row, col),
        dataChanged=signal,
    )
    runtime.refresh_market_status_rows(SimpleNamespace(model=model), status_provider=lambda market: f"status:{market}")
    assert model.row_data[1]["状态"] == "status:TW"
    assert model.row_data[2]["状态"] == "status:"
    assert signal.calls == [((1, 0), (1, 0)), ((2, 0), (2, 0))]


@pytest.mark.parametrize(
    ("success", "message", "expected_fragment"),
    [
        (True, "已保留现有缓存", "已保留本地缓存"),
        (True, "ok", "收盘缓存同步完成"),
        (False, "", "收盘缓存同步失败"),
    ],
)
def test_auto_cache_finished_and_klines_ready(success, message, expected_fragment):
    calls = []
    tab = SimpleNamespace(
        _is_fetching_cache=True,
        _set_runtime_state=lambda value: calls.append(("state", value)),
        worker=SimpleNamespace(pause_for_cache_sync=lambda: calls.append(("pause",))),
        _load_local_cache=lambda: calls.append(("load",)),
        _last_asian_success_at=None,
        _set_asian_status=lambda *args, **kwargs: calls.append(("status", args, kwargs)),
    )
    runtime.on_auto_cache_finished(tab, success, message)
    assert tab._is_fetching_cache is False
    assert any(expected_fragment in str(call) for call in calls)
    if success:
        assert ("load",) in calls
        assert isinstance(tab._last_asian_success_at, dt.datetime)

    runtime.on_asian_klines_ready(tab)
    assert calls.count(("load",)) >= 1


def test_worker_helpers_and_cache_persistence_branches(monkeypatch):
    monkeypatch.setattr(
        workers.MarketCalendar,
        "infer_market",
        classmethod(lambda cls, code: str(code).split(".")[-1]),
    )
    monkeypatch.setattr(
        workers.MarketCalendar,
        "normalize_market",
        classmethod(lambda cls, market: market),
    )
    monkeypatch.setattr(
        workers.MarketCalendar,
        "is_quote_refresh_time",
        classmethod(lambda cls, market: market in {"TW", "HK"}),
    )
    assert workers.infer_asian_markets(["", "2330.TW", "0522.HK", "again.HK", "bad.US"]) == ["TW", "HK"]
    assert workers.infer_asian_markets([]) == ["TW", "HK", "T", "KS"]
    assert workers.is_asian_quote_refresh_time(["2330.TW"])
    assert [workers._asian_quote_fetch_priority(code) for code in ["x.HK", "x.KS", "x.TW", "x.T", "x.US"]] == [
        0,
        1,
        2,
        3,
        4,
    ]
    assert workers._asian_market_suffix(" x.tw ") == "TW"
    assert workers._asian_quote_market("x.HK") == "HK"
    assert workers._is_code_quote_refresh_time("x.US") is True
    assert workers._is_code_quote_refresh_time("x.KS") is False
    assert workers._filter_open_market_codes(["x.TW", "x.KS"]) == (["x.TW"], ["KS"])

    monkeypatch.setattr(workers.time, "monotonic", lambda: 100.0)
    assert workers._seconds_until_monotonic(None) is None
    assert workers._seconds_until_monotonic(130.0) == 30.0
    assert workers._has_optional_network_budget(None)
    assert workers._has_optional_network_budget(130.0)
    assert not workers._has_optional_network_budget(110.0)

    monkeypatch.setattr(workers, "write_realtime_quote_cache", lambda payload: (_ for _ in ()).throw(OSError("disk")))
    workers.save_global_asian_rt_cache()


def test_worker_state_backoff_error_and_delegate_methods(monkeypatch):
    worker = workers.AsianMarketWorker(["x.TW"])
    progress = []
    worker.progress.connect(progress.append)
    worker._emit_status_once("one")
    worker._emit_status_once("one")
    assert progress == ["one"]

    worker.pause_for_cache_sync()
    assert worker._pause_mode
    worker.resume_auto_refresh()
    worker.trigger_refresh()
    assert not worker._pause_mode and worker._manual_refresh_requested
    assert worker.wait_for_cycle_idle(0)
    worker.stop()
    assert not worker._is_running and not worker._manual_refresh_requested

    worker._is_running = True
    clock = iter([0.0, 0.0, 1.0])
    monkeypatch.setattr(workers.time, "time", lambda: next(clock))
    monkeypatch.setattr(workers.time, "sleep", lambda delay: setattr(worker, "_is_running", False))
    assert worker._sleep_with_break(1.0) is False

    monkeypatch.setattr(workers, "is_yf_rate_limit_error", lambda exc: isinstance(exc, LookupError))
    monkeypatch.setattr(workers, "mark_yf_rate_limited", lambda exc: 60.0)
    assert worker._handle_optional_yahoo_error("x", LookupError("rate"), "ctx") is True
    assert worker._handle_optional_yahoo_error("x", ValueError("bad"), "ctx") is False
    with pytest.raises(AssertionError):
        worker._handle_optional_yahoo_error("x", AssertionError("unexpected"), "ctx")

    worker._market_backoff_until = {"TW": 10, "HK": 30}
    worker._prune_market_backoff(20)
    assert worker._market_backoff_until == {"HK": 30}
    worker._code_backoff_until = {"a": 10, "b": 30}
    worker._prune_code_backoff(20)
    assert worker._code_backoff_until == {"b": 30}
    worker._mark_market_backoff("", now_ts=10)
    worker._mark_market_backoff("tw", now_ts=10)
    worker._mark_code_backoff("", now_ts=10)
    worker._mark_code_backoff("abc.tw", now_ts=10)
    assert worker._is_market_backoff_active("a.TW", now_ts=11)
    assert worker._is_code_backoff_active("abc.tw", now_ts=11)
    assert not worker._is_market_backoff_active("", now_ts=11)
    assert not worker._is_code_backoff_active("", now_ts=11)

    worker._timeout_backoff_until = 50
    worker._mark_timeout_backoff(now_ts=10, duration_sec=20)
    assert worker._timeout_backoff_until == 50
    worker.defer_auto_refresh(0)
    worker.defer_auto_refresh(5, "test")
    assert worker._timeout_backoff_remaining(now_ts=0) > 0
    assert worker._timeout_backoff_remaining(now_ts=10_000) == 0
    worker._clear_timeout_backoff()
    worker._mark_source_payload_degraded()
    assert worker._source_payload_degraded()

    monkeypatch.setattr(workers.quote_service, "fetch_yahoo_enrichment", lambda *args, **kwargs: (args, kwargs))
    _args, kwargs = worker._fetch_yahoo_enrichment("x.TW", object(), allow_network=False)
    assert kwargs["allow_network"] is False
    monkeypatch.setattr(workers.quote_service, "refresh_pe_if_needed", lambda *args, **kwargs: kwargs)
    assert (
        worker._refresh_pe_if_needed(
            "x.TW", ticker=None, info_session=None, pe_value=None, pe_source="", pe_updated_at=0
        )["pe_value"]
        is None
    )


def test_worker_fetch_single_code_deadline_payload_error_none_and_success(monkeypatch):
    worker = workers.AsianMarketWorker(["x.TW"])
    monkeypatch.setattr(workers.time, "monotonic", lambda: 100.0)
    worker._fetch_deadline_monotonic = 99.0
    assert worker._fetch_single_code("x.TW", None, None) == ("x.TW", None)

    worker._fetch_deadline_monotonic = None
    monkeypatch.setattr(
        workers.quote_service,
        "fetch_normalized_asian_quote",
        lambda *args, **kwargs: (_ for _ in ()).throw(workers.AsianRealtimePayloadError("bad")),
    )
    assert worker._fetch_single_code("x.TW", None, None) == ("x.TW", None)
    assert worker._is_code_backoff_active("x.TW")
    assert worker._source_payload_degraded()

    monkeypatch.setattr(workers.quote_service, "fetch_normalized_asian_quote", lambda *args, **kwargs: None)
    assert worker._fetch_single_code("y.HK", None, None) == ("y.HK", None)

    payload = {"close": 10}
    monkeypatch.setattr(workers.quote_service, "fetch_normalized_asian_quote", lambda *args, **kwargs: payload)
    assert worker._fetch_single_code("z.T", None, None) == ("z.T", payload)
    assert workers.GLOBAL_ASIAN_RT_CACHE["z.T"] == payload


def test_worker_fetch_updates_empty_closed_backoff_and_success(monkeypatch):
    monkeypatch.setattr(workers, "build_yf_session", lambda: object())
    empty = workers.AsianMarketWorker(["", "  "])
    assert empty._fetch_updates() == {}

    closed = workers.AsianMarketWorker(["x.KS"])
    monkeypatch.setattr(workers, "_filter_open_market_codes", lambda codes: ([], ["KS"]))
    assert closed._fetch_updates(open_markets_only=True) == {}

    backed = workers.AsianMarketWorker(["x.TW"])
    backed._market_backoff_until = {"TW": 10_000_000_000.0}
    assert backed._fetch_updates() == {}

    worker = workers.AsianMarketWorker(["x.T", "x.HK", "x.TW", "x.HK"])
    calls = []
    monkeypatch.setattr(workers, "_YF_FETCH_MAX_WORKERS", 1)
    monkeypatch.setattr(
        worker,
        "_fetch_single_code",
        lambda code, yf_session, info_session: calls.append(code) or (code, {"close": 1}),
    )
    updates = worker._fetch_updates()
    assert list(updates) == ["x.HK", "x.TW", "x.T"]
    assert calls == ["x.HK", "x.TW", "x.T"]
    assert worker._fetch_deadline_monotonic is None


class _Future:
    def __init__(self, *, value=None, error=None, done=True):
        self.value = value
        self.error = error
        self._done = done
        self.cancelled = False

    def result(self, timeout=None):
        if self.error is not None:
            raise self.error
        return self.value

    def done(self):
        return self._done

    def cancel(self):
        self.cancelled = True


class _Executor:
    def __init__(self, futures):
        self.futures = iter(futures)
        self.shutdown_args = None

    def submit(self, *args, **kwargs):
        return next(self.futures)

    def shutdown(self, **kwargs):
        self.shutdown_args = kwargs


@pytest.mark.parametrize(
    ("error", "rate_limited", "raises"),
    [
        (LookupError("rate"), True, False),
        (ValueError("bad"), False, False),
        (AssertionError("unexpected"), False, True),
    ],
)
def test_worker_fetch_updates_future_error_branches(monkeypatch, error, rate_limited, raises):
    future = _Future(error=error)
    executor = _Executor([future])
    monkeypatch.setattr(workers, "build_yf_session", lambda: object())
    monkeypatch.setattr(workers.concurrent.futures, "ThreadPoolExecutor", lambda **kwargs: executor)
    monkeypatch.setattr(workers.concurrent.futures, "as_completed", lambda futures, timeout: iter(futures))
    monkeypatch.setattr(workers, "is_yf_rate_limit_error", lambda exc: rate_limited)
    marks = []
    monkeypatch.setattr(workers, "mark_yf_rate_limited", lambda exc: marks.append(exc) or 30)
    worker = workers.AsianMarketWorker(["x.TW"])
    if raises:
        with pytest.raises(AssertionError):
            worker._fetch_updates()
    else:
        assert worker._fetch_updates() == {}
    assert bool(marks) is rate_limited
    assert executor.shutdown_args == {"wait": True, "cancel_futures": True}


def test_worker_fetch_updates_timeout_cancelled_and_false_payload_branches(monkeypatch):
    monkeypatch.setattr(workers, "build_yf_session", lambda: object())

    timeout_future = _Future(done=False)
    executor = _Executor([timeout_future])
    monkeypatch.setattr(workers.concurrent.futures, "ThreadPoolExecutor", lambda **kwargs: executor)

    def _timeout(_futures, timeout):
        raise workers.concurrent.futures.TimeoutError()

    monkeypatch.setattr(workers.concurrent.futures, "as_completed", _timeout)
    worker = workers.AsianMarketWorker(["x.TW"])
    assert worker._fetch_updates() == {}
    assert worker._last_fetch_timed_out
    assert timeout_future.cancelled
    assert worker._is_market_backoff_active("x.TW")
    assert executor.shutdown_args == {"wait": False, "cancel_futures": True}

    false_future = _Future(value=("x.TW", None))
    executor = _Executor([false_future])
    monkeypatch.setattr(workers.concurrent.futures, "ThreadPoolExecutor", lambda **kwargs: executor)
    monkeypatch.setattr(workers.concurrent.futures, "as_completed", lambda futures, timeout: iter(futures))
    worker = workers.AsianMarketWorker(["x.TW"])
    assert worker._fetch_updates() == {}

    cancelled_future = _Future(value=("x.TW", {"close": 1}))
    executor = _Executor([cancelled_future])
    monkeypatch.setattr(workers.concurrent.futures, "ThreadPoolExecutor", lambda **kwargs: executor)
    worker = workers.AsianMarketWorker(["x.TW"])
    worker._is_running = False
    assert worker._fetch_updates() == {}
    assert executor.shutdown_args == {"wait": False, "cancel_futures": True}


def test_worker_sync_delegate_and_minor_branches(monkeypatch):
    from app.services import asian_market_service

    monkeypatch.setattr(asian_market_service, "sync_asian_kline_cache", lambda *args, **kwargs: (args, kwargs))
    assert workers.sync_asian_kline_cache(1, key=2) == ((1,), {"key": 2})

    worker = workers.AsianMarketWorker(["x.TW"])
    worker.defer_auto_refresh(5, "")
    assert worker._timeout_backoff_until > 0

    monkeypatch.setattr(workers, "build_yf_session", lambda: object())
    monkeypatch.setattr(workers, "_filter_open_market_codes", lambda codes: (codes, []))
    monkeypatch.setattr(worker, "_fetch_single_code", lambda *args: ("x.TW", {}))
    assert worker._fetch_updates(open_markets_only=True) == {}


def test_worker_run_pause_closed_backoff_success_and_error_paths(monkeypatch):
    monkeypatch.setattr(workers, "get_yf_rate_limit_status", lambda: {"active": False, "remaining_sec": 0})
    monkeypatch.setattr(workers, "is_asian_quote_refresh_time", lambda codes: True)

    paused = workers.AsianMarketWorker(["x.TW"])
    paused._pause_mode = True
    monkeypatch.setattr(paused, "_sleep_with_break", lambda seconds: False)
    paused.run()
    assert not paused._manual_refresh_requested

    closed = workers.AsianMarketWorker(["x.TW"])
    monkeypatch.setattr(workers, "is_asian_quote_refresh_time", lambda codes: False)
    monkeypatch.setattr(closed, "_sleep_with_break", lambda seconds: False)
    closed.run()

    monkeypatch.setattr(workers, "is_asian_quote_refresh_time", lambda codes: True)
    backed = workers.AsianMarketWorker(["x.TW"])
    backed._timeout_backoff_until = 100
    monkeypatch.setattr(workers.time, "time", lambda: 0)
    monkeypatch.setattr(backed, "_sleep_with_break", lambda seconds: False)
    backed.run()

    successful = workers.AsianMarketWorker(["x.TW"])
    successful._manual_refresh_requested = True
    monkeypatch.setattr(successful, "_fetch_updates", lambda **kwargs: {"x.TW": {"close": 1}})
    monkeypatch.setattr(successful, "_sleep_with_break", lambda seconds: False)
    saved = []
    monkeypatch.setattr(workers, "save_global_asian_rt_cache", lambda: saved.append(True))
    successful.run()
    assert saved == [True]
    assert not successful._manual_refresh_requested

    for exc, expected in [
        (RuntimeError("Connection failed"), "连接 Yahoo Finance 失败"),
        (TypeError("NoneType is not subscriptable"), "上游返回了空响应"),
        (ValueError("bad"), "亚洲行情拉取异常"),
    ]:
        worker = workers.AsianMarketWorker(["x.TW"])
        worker._manual_refresh_requested = True
        emitted = []
        worker.progress.connect(emitted.append)
        monkeypatch.setattr(worker, "_fetch_updates", lambda **kwargs: (_ for _ in ()).throw(exc))
        monkeypatch.setattr(worker, "_sleep_with_break", lambda seconds: False)
        worker.run()
        assert any(expected in message for message in emitted)
        assert not worker._manual_refresh_requested


def test_worker_run_remaining_control_flow_and_exception_branches(monkeypatch):
    stopped = workers.AsianMarketWorker([])
    stopped._is_running = False
    assert stopped.run() is None

    monkeypatch.setattr(workers, "get_yf_rate_limit_status", lambda: {"active": True, "remaining_sec": 30})
    monkeypatch.setattr(workers, "is_asian_quote_refresh_time", lambda codes: True)
    monkeypatch.setattr(workers, "mark_yf_rate_limited", lambda exc: 30)
    monkeypatch.setattr(workers, "is_yf_rate_limit_error", lambda exc: isinstance(exc, LookupError))

    limited = workers.AsianMarketWorker(["x.TW"])
    limited._manual_refresh_requested = True
    emitted = []
    limited.progress.connect(emitted.append)
    monkeypatch.setattr(limited, "_fetch_updates", lambda **kwargs: (_ for _ in ()).throw(LookupError("rate")))
    monkeypatch.setattr(limited, "_sleep_with_break", lambda seconds: False)
    limited.run()
    assert any("限流" in message for message in emitted)

    unexpected = workers.AsianMarketWorker(["x.TW"])
    unexpected._manual_refresh_requested = True
    monkeypatch.setattr(unexpected, "_fetch_updates", lambda **kwargs: (_ for _ in ()).throw(AssertionError("bad")))
    with pytest.raises(AssertionError):
        unexpected.run()

    stopped_during_fetch = workers.AsianMarketWorker(["x.TW"])

    def _stop_fetch(**kwargs):
        stopped_during_fetch._is_running = False
        return {}

    monkeypatch.setattr(stopped_during_fetch, "_fetch_updates", _stop_fetch)
    stopped_during_fetch.run()
    assert not stopped_during_fetch._is_running

    post_market = workers.AsianMarketWorker(["x.TW"])
    post_market._manual_refresh_requested = True
    calls = []

    def _market_open(_codes):
        calls.append("market")
        return len(calls) > 1

    monkeypatch.setattr(workers, "is_asian_quote_refresh_time", _market_open)
    monkeypatch.setattr(post_market, "_fetch_updates", lambda **kwargs: {})
    monkeypatch.setattr(post_market, "_sleep_with_break", lambda seconds: False)
    post_market.run()
    assert calls.count("market") >= 2


def test_cache_fetcher_success_cancel_error_and_cancel_methods(monkeypatch):
    token = CancellationToken.with_timeout(60)
    thread = workers.AsianCacheFetcherThread(cancellation_token=token)
    monkeypatch.setattr(workers, "sync_asian_kline_cache", lambda **kwargs: (True, "ok", {}))
    thread.run()
    assert thread.result_success and thread.result_message == "ok"

    thread.cancel("manual")
    assert thread.cancellation_token.cancelled
    thread.run()
    assert not thread.result_success and "已取消" in thread.result_message

    token = CancellationToken.with_timeout(60)
    thread = workers.AsianCacheFetcherThread(cancellation_token=token)
    monkeypatch.setattr(workers, "sync_asian_kline_cache", lambda **kwargs: (_ for _ in ()).throw(OSError("disk")))
    thread.run()
    assert not thread.result_success and "disk" in thread.result_message

    token = CancellationToken.with_timeout(60)
    thread = workers.AsianCacheFetcherThread(cancellation_token=token)
    thread.requestInterruption()
    assert token.cancelled
