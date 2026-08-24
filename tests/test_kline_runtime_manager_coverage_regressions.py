# -*- coding: utf-8 -*-
from __future__ import annotations

import datetime as dt
from types import SimpleNamespace

import pandas as pd
import pytest

from ui import kline_window_runtime as runtime
from ui.components import kline_window_manager as manager_module
from ui.kline_load_controller import KlineLoadController


class _Log:
    def __init__(self):
        self.warnings = []

    def warning(self, message):
        self.warnings.append(message)


class _Signal:
    def __init__(self):
        self.callbacks = []

    def connect(self, callback):
        self.callbacks.append(callback)

    def disconnect(self, callback):
        self.callbacks.remove(callback)


class _PoolChart:
    def __init__(self, *, healthy=True, park_result=True):
        self.healthy = healthy
        self.park_result = park_result
        self.hidden = False
        self.deleted = False
        self.opacity = None
        self.geometry_value = None

    def _browser_is_pool_healthy(self):
        return self.healthy

    def hide(self):
        self.hidden = True

    def deleteLater(self):
        self.deleted = True

    def setWindowOpacity(self, value):
        self.opacity = value

    def setGeometry(self, value):
        self.geometry_value = value

    def park_preheated_shell(self):
        return self.park_result


def _frame(date="2026-08-25"):
    return pd.DataFrame(
        {"open": [10.0], "high": [11.0], "low": [9.0], "close": [10.5], "volume": [100.0]},
        index=[pd.Timestamp(date)],
    )


def _runtime_window(code="000001"):
    controller = KlineLoadController(window_id="coverage-window")
    identity = controller.begin(code)
    return SimpleNamespace(
        _closing=False,
        _runtime_active=True,
        _load_controller=controller,
        _snapshot_version=0,
        _latest_rt_quote=None,
        _last_rt_quote_fingerprint=None,
        _rt_prepare_inflight=False,
        _rt_prepare_owner=None,
        code=code,
        df=_frame(),
        _history_frame=None,
        _log=_Log(),
    ), identity


def _prewarm_manager(view=None):
    resumed = []
    disposed = []
    return SimpleNamespace(
        _prewarm_view=view,
        _prewarm_window=None,
        _prewarm_started=True,
        _prewarm_cancelled=False,
        _prewarm_ready=False,
        _prewarm_failure="",
        _prewarm_load_callback=None,
        _prewarm_termination_callback=None,
        _shutting_down=False,
        _idle_chart=None,
        _reclaiming_chart=None,
        _pending_open=SimpleNamespace(request_resume=lambda: resumed.append(True)),
        _dispose_prewarm_resource=lambda *, reason: disposed.append(reason) or True,
        _coverage_resumed=resumed,
        _coverage_disposed=disposed,
    )


def test_asian_runtime_lazy_facade_forwards_arguments(monkeypatch):
    import app.services.asian_market_service as service

    calls = []
    monkeypatch.setattr(service, "fetch_single_kline", lambda *args, **kwargs: calls.append((args, kwargs)) or "frame")
    monkeypatch.setattr(service, "get_yf_rate_limit_status", lambda: {"active": True})
    monkeypatch.setattr(service, "is_yf_rate_limit_error", lambda exc: exc == "limited")
    monkeypatch.setattr(
        service,
        "mark_yf_rate_limited",
        lambda exc, cooldown_sec=None: calls.append((exc, cooldown_sec)) or cooldown_sec,
    )

    assert runtime.fetch_single_kline("台积电", "2330.TW", period="6mo", session="session") == "frame"
    assert calls[0] == (("台积电", "2330.TW"), {"period": "6mo", "session": "session", "cancellation_token": None})
    assert runtime.get_yf_rate_limit_status() == {"active": True}
    assert runtime.is_yf_rate_limit_error("limited")
    assert runtime.mark_yf_rate_limited("429") is None
    assert runtime.mark_yf_rate_limited("429", cooldown_sec=12) == 12


def test_owned_task_submission_exception_rolls_back_ticket_and_discards(monkeypatch):
    window, identity = _runtime_window()
    discarded = []
    lifecycle = SimpleNamespace(run_background=lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("rejected")))
    monkeypatch.setattr(runtime, "task_lifecycle_for", lambda *_args, **_kwargs: lifecycle)

    with pytest.raises(RuntimeError, match="rejected"):
        runtime._submit_owned_window_task(
            window,
            "history_load",
            lambda _token: None,
            lambda _result: None,
            window._load_controller.task_id("history", identity=identity),
            10.0,
            on_discarded=lambda: discarded.append("discarded"),
            identity=identity,
        )

    assert window._active_kline_task_tickets == set()
    assert window._running_kline_task_submission is None
    assert window._load_controller.running_task is None
    assert discarded == ["discarded"]


def test_cn_and_asian_initial_quote_errors_fail_closed(monkeypatch):
    logger = _Log()
    target_date = dt.date(2026, 8, 25)
    monkeypatch.setattr(runtime, "is_provider_online", lambda _provider: True)
    monkeypatch.setattr(runtime.MarketCalendar, "is_quote_refresh_time", lambda _market: True)
    stale = _frame("2026-08-25")
    assert runtime._fetch_missing_cn_quote(object(), "000001", stale, target_date, logger) is None

    class BrokenProvider:
        @staticmethod
        def fetch_realtime_quotes_batch(_codes):
            raise ValueError("quote unavailable")

    assert runtime._fetch_missing_cn_quote(BrokenProvider(), "000001", _frame("2026-08-24"), target_date, logger) is None
    assert "quote unavailable" in logger.warnings[-1]

    result = SimpleNamespace(latest_trade_date=dt.date(2026, 8, 24), market="TW")
    frame = _frame("2026-08-24")
    merged, fetched, error = runtime._merge_asian_initial_quote(
        frame,
        result=result,
        code="2330.TW",
        target_trade_date=target_date,
        cached_quote=None,
        quote_fetcher=lambda _code: (_ for _ in ()).throw(ValueError("network")),
        cancellation_token=None,
    )
    assert merged is frame and fetched is None and isinstance(error, ValueError)

    with pytest.raises(runtime.TaskCancelledError):
        runtime._merge_asian_initial_quote(
            frame,
            result=result,
            code="2330.TW",
            target_trade_date=target_date,
            cached_quote=None,
            quote_fetcher=lambda _code: (_ for _ in ()).throw(runtime.TaskCancelledError("cancelled")),
            cancellation_token=None,
        )


def test_runtime_stale_and_error_paths_do_not_cross_window_ownership(monkeypatch):
    window, identity = _runtime_window()
    window._closing = True
    runtime.load_and_draw(window, identity)
    assert not hasattr(window, "_active_kline_task_tickets")
    window._closing = False
    window._load_controller.begin("000002")
    runtime.load_and_draw(window, identity)
    assert not hasattr(window, "_active_kline_task_tickets")
    runtime._start_history_load(window, identity)
    assert not hasattr(window, "_active_kline_task_tickets")

    window._load_controller = None
    window._get_market = lambda: "CN"
    monkeypatch.setattr(runtime.MarketCalendar, "is_quote_refresh_time", lambda _market: True)
    runtime.poll_rt_update(window)
    runtime.refresh_last_bar(window, {"close": 10})
    assert window._latest_rt_quote is None

    logger = _Log()
    failed_window = SimpleNamespace(_log=logger)
    monkeypatch.setattr(runtime, "is_yf_rate_limit_error", lambda exc: exc.args == ("limited",))
    monkeypatch.setattr(runtime, "mark_yf_rate_limited", lambda exc: 30.0)
    runtime._handle_asian_quote_error(failed_window, "2330.TW", RuntimeError("limited"))
    runtime._handle_asian_quote_error(failed_window, "2330.TW", RuntimeError("network"))
    assert "30" in logger.warnings[0]
    assert "network" in logger.warnings[1]


def test_realtime_backlog_rejects_invalid_owner_and_keeps_latest_retryable(monkeypatch):
    window, identity = _runtime_window()
    other = window._load_controller.begin("000002")
    assert not runtime._controller_owns_realtime_frame(window._load_controller, identity)

    window._latest_rt_quote = "invalid"
    assert runtime._take_current_realtime_backlog(window, other) is None
    assert window._latest_rt_quote is None
    window._latest_rt_quote = runtime._RealtimeQuoteBacklog(identity, {"close": 1}, ("one",))
    assert runtime._take_current_realtime_backlog(window, other) is None
    assert window._latest_rt_quote is None
    assert runtime._begin_realtime_prepare(window) is None

    window._latest_rt_quote = runtime._RealtimeQuoteBacklog(other, {"close": 2}, ("two",))
    window.df = None
    assert runtime._begin_realtime_prepare(window) is None

    request = SimpleNamespace(owner=("coverage-window", other.generation, 1), identity=other, quote={"close": 3}, fingerprint=("three",), controller=window._load_controller)
    window._rt_prepare_owner = ("someone-else", 1, 1)
    runtime._discard_realtime_prepare(window, request)
    assert window._rt_prepare_owner == ("someone-else", 1, 1)
    window._rt_prepare_owner = request.owner
    window._latest_rt_quote = None
    runtime._retry_realtime_prepare("retry", window=window, request=request)
    assert isinstance(window._latest_rt_quote, runtime._RealtimeQuoteBacklog)
    assert window._rt_prepare_inflight is False


def test_asian_realtime_frame_uses_asian_merge_contract(monkeypatch):
    import ui.kline_window_asian as asian

    frame = _frame()
    merged = _frame("2026-08-26")
    monkeypatch.setattr(asian, "apply_asian_live_quote", lambda current, quote, *, market: merged)

    assert runtime._merge_realtime_frame(frame, {"close": 11}, market="TW") is merged


def test_prewarm_failure_paths_release_resources_and_resume_open(monkeypatch):
    view = SimpleNamespace(loadFinished=_Signal())
    manager = _prewarm_manager(view)
    manager._prewarm_load_callback = lambda _ok: None
    view.loadFinished.connect(manager._prewarm_load_callback)
    manager_module._complete_hidden_prewarm(manager, view, 0.0, False)
    assert manager._prewarm_failure == "load_failed"
    assert manager._coverage_disposed == ["load_failed"]
    assert manager._coverage_resumed == [True]

    manager = _prewarm_manager(view)
    manager_module._expire_hidden_prewarm(manager, view)
    assert manager._prewarm_failure == "load_timeout"
    assert manager._coverage_disposed == ["load_timeout"]
    assert manager._coverage_resumed == [True]

    manager = _prewarm_manager(view)
    manager._prewarm_cancelled = True
    manager_module._load_hidden_prewarm_view(manager, view)
    assert manager._coverage_disposed == ["cancelled_before_load"]

    manager = _prewarm_manager()
    manager._prewarm_cancelled = True
    manager_module._create_hidden_prewarm_view(manager, 0.0)
    assert manager._prewarm_started is False

    manager = _prewarm_manager(view)
    monkeypatch.setattr(manager_module, "_install_keeper_termination", lambda *_args: False)
    manager_module._complete_hidden_prewarm(manager, view, 0.0, True)
    assert manager._prewarm_failure == "termination_guard_failed"
    assert manager._coverage_disposed == ["termination_guard_failed"]


def test_full_window_prewarm_fails_closed_on_bad_keeper_or_timeout(monkeypatch):
    disposed = []
    manager = _prewarm_manager()
    chart = _PoolChart(healthy=False)
    chart._last_shell_load_ok = False
    manager._prewarm_window = chart
    monkeypatch.setattr(manager_module, "_dispose_full_window", lambda value: disposed.append(value) or True)

    manager_module._poll_full_window_prewarm(manager, chart, 0.0, "geometry")
    assert manager._prewarm_failure == "full_window_failed"
    assert disposed == [chart]
    assert manager._coverage_resumed == [True]

    manager = _prewarm_manager()
    chart = _PoolChart(healthy=True)
    manager._prewarm_window = chart
    manager._prewarm_cancelled = True
    manager_module._poll_full_window_prewarm(manager, chart, 0.0, "geometry")
    assert manager._prewarm_window is None
    assert manager._coverage_resumed == [True]

    manager = _prewarm_manager()
    chart = _PoolChart(healthy=True)
    manager._prewarm_window = chart
    monkeypatch.setattr(manager_module, "_full_window_renderer_settled", lambda _chart: False)
    monkeypatch.setattr(manager_module.time, "perf_counter", lambda: 999.0)
    manager_module._poll_full_window_prewarm(manager, chart, 0.0, "geometry")
    assert manager._prewarm_failure == "full_window_timeout"
    assert manager._coverage_resumed == [True]


def test_prewarm_page_return_is_rejected_when_attach_or_guard_fails(monkeypatch):
    page = SimpleNamespace()
    manager = _prewarm_manager()
    monkeypatch.setattr(manager_module, "_attach_page_to_application", lambda _page: False)
    assert not manager_module._release_manager_page(manager, page, shell_ready=True, html_bytes=10)

    manager = _prewarm_manager()
    monkeypatch.setattr(manager_module, "_attach_page_to_application", lambda _page: True)
    monkeypatch.setattr(manager_module, "_install_keeper_termination", lambda *_args: False)
    monkeypatch.setattr(manager_module, "_set_browser_property", lambda *_args: None)
    assert not manager_module._release_manager_page(manager, page, shell_ready=True, html_bytes=10)
    assert manager._prewarm_view is None
    assert manager._prewarm_failure == "termination_guard_failed"


def test_keeper_termination_and_full_window_failure_helpers_fail_closed(monkeypatch):
    class _Page:
        def __init__(self):
            self.renderProcessTerminated = _Signal()
            self.properties = {}

        def setProperty(self, name, value):
            self.properties[name] = value

        def property(self, name):
            return self.properties.get(name)

    page = _Page()
    manager = _prewarm_manager(page)
    manager._prewarm_termination_callback = lambda: None
    page.renderProcessTerminated.disconnect = lambda _callback: (_ for _ in ()).throw(TypeError("gone"))
    assert manager_module._disconnect_keeper_termination(manager, page)
    assert manager._prewarm_termination_callback is None

    page = _Page()
    manager = _prewarm_manager(page)
    assert manager_module._install_keeper_termination(manager, page)
    manager._prewarm_termination_callback()
    assert page.property(manager_module.KLINE_SHELL_READY_PROPERTY) is False
    assert manager._prewarm_failure == "render_process_terminated"
    assert manager._coverage_disposed == ["render_process_terminated"]

    assert not manager_module._transition_chart_pool_state(object(), object(), reason="missing")

    class BrokenTransition:
        @staticmethod
        def transition(*_args, **_kwargs):
            raise RuntimeError("deleted")

    assert not manager_module._transition_chart_pool_state(BrokenTransition(), object(), reason="broken")

    class BrokenPoolState:
        def __getattribute__(self, _name):
            raise RuntimeError("deleted")

    assert manager_module._full_window_keeper_failed(BrokenPoolState())
    assert manager_module._dispose_full_window(None)

    class FallbackWindow:
        def __init__(self):
            self.hidden = False
            self.deleted = False

        def hide(self):
            self.hidden = True

        def deleteLater(self):
            self.deleted = True

    fallback = FallbackWindow()
    assert manager_module._dispose_full_window(fallback)
    assert fallback.hidden and fallback.deleted

    disposals = []
    monkeypatch.setattr(manager_module, "_dispose_full_window", lambda chart: disposals.append(chart) or True)
    manager = _prewarm_manager()
    unhealthy = _PoolChart(healthy=False)
    manager._prewarm_window = unhealthy
    manager_module._finish_full_window_prewarm(manager, unhealthy, 0.0, "geometry")
    assert manager._prewarm_failure == "full_window_not_healthy"
    assert manager._prewarm_window is None
    assert disposals == [unhealthy]

    manager = _prewarm_manager()
    healthy = _PoolChart(healthy=True, park_result=True)
    manager._prewarm_window = healthy
    manager._install_idle_chart_termination = lambda _chart: False
    manager_module._finish_full_window_prewarm(manager, healthy, 0.0, "geometry")
    assert manager._prewarm_failure == "full_window_termination_guard_failed"
    assert manager._idle_chart is None
    assert disposals[-1] is healthy


def test_chart_open_gates_and_full_window_prewarm_fallbacks(monkeypatch):
    queued = []
    notices = []

    def gate_manager(**overrides):
        values = {
            "_shutting_down": False,
            "_prewarm_view": None,
            "_prewarm_ready": False,
            "_prewarm_window": None,
            "_reclaiming_chart": None,
            "_webengine_available": True,
            "_pending_open": SimpleNamespace(queue=lambda request: queued.append(request)),
            "_notify_webengine_preparing": lambda *_args: notices.append("preparing"),
            "notify_data_provider_preparing": lambda *_args: notices.append("provider"),
            "_start_webengine_preflight_async": lambda: notices.append("preflight"),
            "_notify_webengine_unavailable": lambda *_args: notices.append("unavailable"),
        }
        values.update(overrides)
        return SimpleNamespace(**values)

    pending = object()
    preparing = gate_manager(_prewarm_view=object())
    assert manager_module._chart_open_is_blocked(preparing, None, "000001", "平安银行", object(), pending)
    assert queued == [pending] and notices == ["preparing"]

    queued.clear()
    notices.clear()
    preflight = gate_manager(_webengine_available=None)
    assert manager_module._chart_open_is_blocked(preflight, None, "000001", "平安银行", object(), pending)
    assert notices == ["preflight", "preparing"]

    queued.clear()
    notices.clear()
    unavailable = gate_manager(_webengine_available=False)
    assert manager_module._chart_open_is_blocked(unavailable, None, "000001", "平安银行", object(), pending)
    assert notices == ["unavailable"]

    notices.clear()
    provider_wait = gate_manager()
    assert manager_module._chart_open_is_blocked(provider_wait, None, "000001", "平安银行", None)
    assert notices == ["provider"]

    created = []
    manager = _prewarm_manager()
    monkeypatch.setattr(manager_module, "_create_hidden_prewarm_view", lambda target, started: created.append((target, started)))
    manager_module._create_hidden_full_window_keeper(manager, 12.0)
    assert created == [(manager, 12.0)]

    manager = _prewarm_manager()
    manager._prewarm_main_window = object()
    monkeypatch.setattr(
        manager_module,
        "_load_kline_window_class",
        lambda: (_ for _ in ()).throw(RuntimeError("construction failed")),
    )
    manager_module._create_hidden_full_window_keeper(manager, 12.0)
    assert manager._prewarm_started is False
    assert "construction failed" in manager._prewarm_failure
    assert manager._coverage_resumed == [True]

    class BrokenActivation:
        @staticmethod
        def show():
            return None

        @staticmethod
        def raise_():
            raise AttributeError("no raise")

        @staticmethod
        def activateWindow():
            return None

    assert manager_module._activate_chart(BrokenActivation())


def test_idle_pool_termination_and_safe_browser_helpers_dispose_stale_keeper(monkeypatch):
    class ExplodingChart:
        def __getattribute__(self, _name):
            raise RuntimeError("deleted")

    class ExplodingBrowser:
        @staticmethod
        def page():
            raise RuntimeError("deleted")

    assert manager_module._safe_chart_browser(ExplodingChart()) is None
    assert manager_module._safe_browser_page(ExplodingBrowser()) is None

    page = SimpleNamespace(renderProcessTerminated=_Signal())
    chart = SimpleNamespace(browser=SimpleNamespace(page=lambda: page), transitions=[])
    chart.transition = lambda target, *, reason: chart.transitions.append((target, reason))
    resumed = []
    disposed = []
    owner = SimpleNamespace(
        _idle_chart=chart,
        _reclaiming_chart=None,
        _prewarm_ready=True,
        _prewarm_failure="",
        _idle_termination_callback=None,
        _disconnect_idle_chart_termination=lambda: True,
        _pending_open=SimpleNamespace(request_resume=lambda: resumed.append(True)),
    )
    monkeypatch.setattr(manager_module, "_dispose_full_window", lambda value: disposed.append(value) or True)

    assert manager_module._KLineManagerWindowPoolLifecycle._install_idle_chart_termination(owner, chart)
    callback = owner._idle_termination_callback[1]
    callback()

    assert chart.transitions[-1][1] == "idle_render_process_terminated"
    assert owner._idle_chart is None
    assert owner._prewarm_failure == "idle_render_process_terminated"
    assert disposed == [chart]
    assert resumed == [True]


def test_full_window_prewarm_successfully_parks_a_healthy_keeper(monkeypatch):
    metrics = []
    logs = []
    manager = _prewarm_manager()
    chart = _PoolChart(healthy=True, park_result=True)
    manager._prewarm_window = chart
    manager._install_idle_chart_termination = lambda _chart: True
    monkeypatch.setattr(manager_module, "record_metric", lambda *args, **kwargs: metrics.append((args, kwargs)))
    monkeypatch.setattr(manager_module, "emit_structured_log", lambda *args, **kwargs: logs.append((args, kwargs)))

    manager_module._finish_full_window_prewarm(manager, chart, 0.0, "geometry")

    assert manager._prewarm_window is None
    assert manager._idle_chart is chart
    assert manager._prewarm_ready is True
    assert manager._prewarm_failure == ""
    assert manager._coverage_resumed == [True]
    assert metrics and logs


def test_runtime_error_callbacks_do_not_update_closed_or_broken_windows(monkeypatch):
    window, identity = _runtime_window()
    window._closing = True
    runtime.poll_rt_update(window)

    window._closing = False
    monkeypatch.setattr(runtime, "refresh_last_bar", lambda *_args: (_ for _ in ()).throw(RuntimeError("deleted")))
    runtime._apply_realtime_quote_result(
        {"close": 10},
        window=window,
        request_code=identity.code,
        request_generation=identity.generation,
    )

    window._set_status_message = lambda *_args, **_kwargs: (_ for _ in ()).throw(ValueError("deleted"))
    runtime._report_cn_history_error(window, identity.code, identity.generation, "provider failed")


def test_asian_history_result_persists_quote_then_schedules_missing_render(monkeypatch):
    window, identity = _runtime_window("2330.TW")
    cache_writes = []
    quote_errors = []
    backfills = []
    monkeypatch.setattr(
        runtime.asian_market_cache_service,
        "set_realtime_quote",
        lambda cache, code, quote: cache_writes.append((cache, code, quote)),
    )
    monkeypatch.setattr(runtime, "_handle_asian_quote_error", lambda *_args: quote_errors.append(_args[2]))
    monkeypatch.setattr(runtime, "_schedule_missing_asian_history", lambda target: backfills.append(target))
    result = runtime._PreparedHistoryLoad(
        runtime.KlineDataResult(
            code="2330.TW",
            market="TW",
            data=_frame(),
            source="asian_json_cache",
            degraded=False,
            degradation_reason="",
            latest_trade_date=dt.date(2026, 8, 25),
        ),
        None,
        None,
        fetched_asian_quote={"close": 100.0},
        quote_error=RuntimeError("quote error"),
    )

    runtime._apply_history_load_result(result, window=window, request=SimpleNamespace(identity=identity, market="TW"))

    assert cache_writes[-1][1:] == ("2330.TW", {"close": 100.0})
    assert quote_errors == [result.quote_error]
    assert backfills == [window]


def test_realtime_preparation_checks_backlog_after_claiming_frame(monkeypatch):
    fallback_window = SimpleNamespace(_closing=False, code="000001", _render_generation=3)
    assert runtime._is_current_request(fallback_window, "000001", 3)

    window, identity = _runtime_window()
    assert window._load_controller.claim_frame(identity)
    assert runtime._begin_realtime_prepare(window) is None

    window._latest_rt_quote = runtime._RealtimeQuoteBacklog(identity, {"close": 10}, ("quote",))
    window.df = None
    assert runtime._begin_realtime_prepare(window) is None

    monkeypatch.setattr(
        runtime,
        "raise_if_cancelled",
        lambda _token: (_ for _ in ()).throw(runtime.TaskCancelledError("cancelled")),
    )
    with pytest.raises(runtime.TaskCancelledError):
        runtime._fetch_realtime_quote(None, market="CN", request_code="000001", data_provider=object())
