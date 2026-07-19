# -*- coding: utf-8 -*-
from __future__ import annotations

from types import SimpleNamespace

import pytest

from ui import main_window_runtime as runtime


def test_workspace_and_post_online_refresh_guards():
    assert runtime.workspace_tables(SimpleNamespace()) == []
    assert runtime.workspace_tables(SimpleNamespace(_workspace=object())) == []
    tables = [1, 2]
    workspace = SimpleNamespace(iter_tables=lambda: tables)
    assert runtime.workspace_tables(SimpleNamespace(_workspace=workspace)) is tables

    states = []
    window = SimpleNamespace(_update_network_ui=lambda online: states.append(online))
    runtime.run_post_online_refresh(window, object())
    assert states == [True]
    workspace = SimpleNamespace(run_post_online_refresh=lambda manager: states.append(manager))
    window._workspace = workspace
    manager = object()
    runtime.run_post_online_refresh(window, manager)
    assert states[-1] is manager
    workspace.run_post_online_refresh = lambda manager: (_ for _ in ()).throw(RuntimeError("bad"))
    runtime.safe_run_post_online_refresh(window, manager)


def test_current_tab_and_nav_grace_boundaries(monkeypatch):
    assert runtime._current_workspace_tab_key(SimpleNamespace()) == ""
    assert runtime._current_workspace_tab_key(SimpleNamespace(_current_workspace_tab_key=lambda: " scan ")) == "scan"
    assert (
        runtime._current_workspace_tab_key(
            SimpleNamespace(_current_workspace_tab_key=lambda: (_ for _ in ()).throw(RuntimeError("bad")))
        )
        == ""
    )
    assert (
        runtime._system_log_shell_nav_grace_remaining_ms(SimpleNamespace(_current_workspace_tab_key=lambda: "scan"))
        == 0
    )
    monkeypatch.setattr(runtime.time, "perf_counter", lambda: 100.0)
    bad = SimpleNamespace(
        _current_workspace_tab_key=lambda: "system_log",
        _workspace=SimpleNamespace(_last_system_log_shell_nav_load_at="bad"),
    )
    assert runtime._system_log_shell_nav_grace_remaining_ms(bad) == 0
    future = SimpleNamespace(
        _current_workspace_tab_key=lambda: "system_log",
        _workspace=SimpleNamespace(_last_system_log_shell_nav_load_at=101.0),
    )
    assert runtime._system_log_shell_nav_grace_remaining_ms(future) == 0
    old = SimpleNamespace(
        _current_workspace_tab_key=lambda: "system_log",
        _workspace=SimpleNamespace(_last_system_log_shell_nav_load_at=80.0),
    )
    assert runtime._system_log_shell_nav_grace_remaining_ms(old) == 0
    recent = SimpleNamespace(
        _current_workspace_tab_key=lambda: "system_log",
        _workspace=SimpleNamespace(_last_system_log_shell_nav_load_at=95.0),
    )
    assert runtime._system_log_shell_nav_grace_remaining_ms(recent) == 5000
    assert runtime._should_hold_f5_for_system_log_foreground(recent, wait_for_system_log=True)
    assert not runtime._should_hold_f5_for_system_log_foreground(recent, wait_for_system_log=False)


def test_f5_stall_grace_handles_bad_clock_and_current_deadline(monkeypatch):
    window = SimpleNamespace(_f5_precompute_ui_grace_until=200.0)
    monkeypatch.setattr(runtime.time, "perf_counter", lambda: 100.0)
    runtime._mark_f5_ui_stall_grace(window)
    assert window._f5_precompute_ui_grace_until == 200.0
    window._f5_precompute_ui_grace_until = "bad"
    runtime._mark_f5_ui_stall_grace(window)
    assert window._f5_precompute_ui_grace_until == 112.0
    monkeypatch.setattr(runtime.time, "perf_counter", lambda: (_ for _ in ()).throw(TypeError("bad")))
    window._f5_precompute_ui_grace_until = 1
    runtime._mark_f5_ui_stall_grace(window)
    assert window._f5_precompute_ui_grace_until == 1
    runtime._clear_f5_ui_stall_grace(window)
    assert window._f5_precompute_ui_grace_until == 0


def test_f5_controller_callbacks_status_done_cancel_and_failure():
    from app.services.f5_job_contract import F5JobResult, F5JobStatus

    actions = []
    labels = []
    titles = []
    starts = []
    controller = SimpleNamespace(
        start=lambda request, **callbacks: starts.append((request, callbacks)) or True,
    )
    window = SimpleNamespace(
        _is_closing=False,
        _f5_cancelled=False,
        lbl_status=SimpleNamespace(setText=lambda text: labels.append(text)),
        _set_titlebar_sync_state=lambda *args: titles.append(args),
        _on_f5_done=lambda *args: actions.append(args),
        data_provider=SimpleNamespace(tdx_vipdoc=""),
        engine="engine",
        _f5_job_controller=controller,
    )
    runtime._submit_owned_f5_task(window)
    assert len(starts) == 1
    callbacks = starts[0][1]
    callbacks["on_event"](SimpleNamespace(message="working"))
    assert labels == ["working"] and titles[-1] == ("working", "working")
    callbacks["on_finished"](
        F5JobResult(
            run_id="success",
            status=F5JobStatus.SUCCEEDED,
            requested_date="20260101",
            symbol_count=3,
            elapsed_seconds=1.5,
        )
    )
    assert actions == [(3, 1.5)]
    callbacks["on_finished"](
        F5JobResult(
            run_id="cancelled",
            status=F5JobStatus.CANCELLED,
            requested_date="20260101",
        )
    )
    assert labels[-1] == "F5 预计算已取消"
    callbacks["on_finished"](
        F5JobResult.failed(
            starts[0][0],
            error_code="failed",
            error_message="boom",
        )
    )
    assert labels[-1] == "F5 预计算失败: boom"
    window._is_closing = True
    callbacks["on_event"](SimpleNamespace(message="ignored"))
    assert labels[-1] == "F5 预计算失败: boom"


def test_f5_status_with_partial_optional_widgets():
    labels = []
    only_label = SimpleNamespace(
        lbl_status=SimpleNamespace(setText=lambda text: labels.append(text)),
    )
    runtime._set_f5_status(only_label, "label")
    assert labels == ["label"]
    titles = []
    only_title = SimpleNamespace(
        _set_titlebar_sync_state=lambda *args: titles.append(args),
    )
    runtime._set_f5_status(only_title, "title")
    assert titles == [("working", "title")]


def test_submit_f5_task_and_pending_cancel(monkeypatch):
    calls = []
    controller = SimpleNamespace(start=lambda *args, **kwargs: calls.append((args, kwargs)) or True)
    window = SimpleNamespace(
        _f5_cancelled=False,
        _f5_precompute_start_pending=False,
        _f5_job_controller=controller,
        data_provider=SimpleNamespace(tdx_vipdoc=""),
        engine=object(),
    )
    runtime._submit_owned_f5_task(window)
    assert len(calls) == 1
    assert calls[-1][0][0].timeout_seconds == 1800.0
    window._f5_precompute_start_pending = True
    runtime.start_f5_precompute(window)
    assert len(calls) == 1

    window._f5_precompute_start_pending = False
    window._f5_cancelled = True
    monkeypatch.setattr(runtime, "_system_log_shell_nav_grace_remaining_ms", lambda window: 0)
    runtime.start_f5_precompute(window)
    assert window._f5_precompute_start_pending is False


def test_f5_controller_rejects_uninitialized_runtime():
    window = SimpleNamespace(data_provider=None, engine=None)
    with pytest.raises(RuntimeError, match="dependencies are not ready"):
        runtime._ensure_f5_controller(window)
    assert not hasattr(window, "_f5_job_controller")


class _Text:
    def __init__(self):
        self.values = []

    def setText(self, text):
        self.values.append(text)


def _window_for_finish(workspace):
    return SimpleNamespace(
        _workspace=workspace,
        _update_last_f5_time=lambda: None,
        lbl_status=_Text(),
        lbl_code_count=_Text(),
        _set_titlebar_sync_state=lambda *args: None,
        central_quotes_svc=SimpleNamespace(refresh_after_cache_reload=lambda: None),
    )


def test_finish_f5_reload_scheduled_success_and_count_labels():
    calls = []
    workspace = SimpleNamespace(
        refresh_all_tabs_after_f5_scheduled=lambda **kwargs: calls.append(kwargs) or True,
        refresh_all_tabs_after_f5=lambda **kwargs: (_ for _ in ()).throw(AssertionError("not used")),
        refresh_information_sources_after_f5_scheduled=lambda **kwargs: calls.append(kwargs) or True,
    )
    event_bus = SimpleNamespace(sig_cache_reload_completed=SimpleNamespace(emit=lambda: calls.append("emit")))
    window = _window_for_finish(workspace)
    runtime.finish_f5_reload(window, count=3, elapsed=1.2, event_bus=event_bus)
    assert window.lbl_status.values and window.lbl_code_count.values
    assert "emit" in calls


def test_finish_f5_reload_legacy_fallbacks_and_no_count():
    calls = []

    def scheduled(**kwargs):
        if "skip_cache_reload_tabs" in kwargs:
            raise TypeError("legacy")
        return False

    def refresh_all(**kwargs):
        if kwargs:
            raise TypeError("legacy")
        calls.append("refresh")

    workspace = SimpleNamespace(
        refresh_all_tabs_after_f5_scheduled=scheduled,
        refresh_all_tabs_after_f5=refresh_all,
        refresh_information_sources_after_f5=lambda: calls.append("info"),
    )
    event_bus = SimpleNamespace(sig_cache_reload_completed=SimpleNamespace(emit=lambda: None))
    window = _window_for_finish(workspace)
    runtime.finish_f5_reload(window, count=0, elapsed=0, event_bus=event_bus)
    assert calls == ["refresh", "info"]
    assert window.lbl_status.values


def test_finish_f5_reload_error_paths_and_auto_sync():
    def fail(*args, **kwargs):
        raise RuntimeError("bad")

    workspace = SimpleNamespace(
        refresh_all_tabs_after_f5_scheduled=fail,
        refresh_all_tabs_after_f5=fail,
        refresh_information_sources_after_f5_scheduled=fail,
        run_fund_holdings_auto_sync_after_f5=fail,
    )
    window = _window_for_finish(workspace)
    window.central_quotes_svc.refresh_after_cache_reload = fail
    event_bus = SimpleNamespace(sig_cache_reload_completed=SimpleNamespace(emit=fail))
    runtime.finish_f5_reload(window, count=0, elapsed=0, event_bus=event_bus)


def test_finish_f5_reload_information_failure_and_optional_absence():
    def fail():
        raise RuntimeError("bad")

    workspace = SimpleNamespace(refresh_information_sources_after_f5=fail)
    window = SimpleNamespace(_workspace=workspace, _update_last_f5_time=lambda: None)
    event_bus = SimpleNamespace(sig_cache_reload_completed=SimpleNamespace(emit=lambda: None))
    runtime.finish_f5_reload(window, count=0, elapsed=0, event_bus=event_bus)
    runtime.finish_f5_reload(
        SimpleNamespace(_workspace=None, _update_last_f5_time=lambda: None),
        count=1,
        elapsed=0,
        event_bus=event_bus,
    )


def test_shutdown_main_window_runs_all_optional_services_and_contains_errors(monkeypatch):
    calls = []

    def record(name, *, fail=False):
        def action(*args, **kwargs):
            calls.append((name, args, kwargs))
            if fail:
                raise RuntimeError(name)

        return action

    monkeypatch.setattr(runtime, "shutdown_market_calendar_tasks", record("calendar"))
    monkeypatch.setattr(runtime.global_store, "reset_runtime_state", record("global"))
    window = SimpleNamespace(
        _workspace=SimpleNamespace(shutdown=record("workspace")),
        _task_lifecycle=SimpleNamespace(shutdown=record("lifecycle", fail=True)),
        startup_orchestrator=SimpleNamespace(shutdown=record("startup")),
        auto_refresh_scheduler=SimpleNamespace(shutdown=record("auto")),
        asian_market_service=SimpleNamespace(shutdown=record("asian")),
        earnings_refresh_service=SimpleNamespace(shutdown=record("earnings")),
        central_quotes_svc=SimpleNamespace(shutdown=record("quotes")),
        _save_ui_state=record("save"),
    )
    event_bus = SimpleNamespace(sig_app_closing=SimpleNamespace(emit=record("emit")))
    manager = SimpleNamespace(shutdown=record("tasks"))
    runtime.shutdown_main_window(window, event_bus=event_bus, task_manager=manager)
    assert window._is_closing and window._f5_cancelled
    assert {item[0] for item in calls} >= {
        "lifecycle",
        "calendar",
        "startup",
        "auto",
        "asian",
        "earnings",
        "quotes",
        "workspace",
        "save",
        "emit",
        "global",
        "tasks",
    }


def test_shutdown_main_window_without_optional_services(monkeypatch):
    monkeypatch.setattr(runtime, "shutdown_market_calendar_tasks", lambda **kwargs: None)
    monkeypatch.setattr(runtime.global_store, "reset_runtime_state", lambda: None)
    window = SimpleNamespace(
        _workspace=None,
        _save_ui_state=lambda: None,
        startup_orchestrator=None,
    )
    event_bus = SimpleNamespace(sig_app_closing=SimpleNamespace(emit=lambda: None))
    runtime.shutdown_main_window(
        window,
        event_bus=event_bus,
        task_manager=SimpleNamespace(shutdown=lambda: None),
    )
    assert window._pending_f5_request is False


def test_shutdown_main_window_warns_when_owned_processes_miss_deadline(monkeypatch):
    warnings = []
    monkeypatch.setattr(runtime.log, "warning", warnings.append)
    monkeypatch.setattr(runtime.kline_manager, "shutdown", lambda: False)
    monkeypatch.setattr(runtime, "shutdown_market_calendar_tasks", lambda **_kwargs: None)
    monkeypatch.setattr(runtime.global_store, "reset_runtime_state", lambda: None)
    window = SimpleNamespace(
        _workspace=None,
        _f5_job_controller=SimpleNamespace(shutdown=lambda **_kwargs: False),
        _save_ui_state=lambda: None,
    )

    runtime.shutdown_main_window(
        window,
        event_bus=SimpleNamespace(sig_app_closing=SimpleNamespace(emit=lambda: None)),
        task_manager=SimpleNamespace(shutdown=lambda: None),
    )

    assert warnings == [
        "[关闭] 停止F5子进程未在时限内完成",
        "[关闭] 关闭K线窗口未在时限内完成",
    ]
