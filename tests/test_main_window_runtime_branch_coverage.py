# -*- coding: utf-8 -*-
from __future__ import annotations

from types import SimpleNamespace

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


def test_f5_callbacks_cancel_status_done_and_run(monkeypatch):
    actions = []
    labels = []
    titles = []
    window = SimpleNamespace(
        _is_closing=False,
        _f5_cancelled=False,
        _call_in_ui=lambda callback: callback(),
        lbl_status=SimpleNamespace(setText=lambda text: labels.append(text)),
        _set_titlebar_sync_state=lambda *args: titles.append(args),
        _on_f5_done=lambda *args: actions.append(args),
        data_provider="provider",
        engine="engine",
    )
    callbacks = runtime._F5TaskCallbacks(window)
    assert not callbacks.cancelled()
    callbacks.set_status("working")
    assert labels == ["working"] and titles[-1] == ("working", "working")
    callbacks.done(3, 1.5)
    assert actions == [(3, 1.5)]
    window._is_closing = True
    callbacks.set_status("ignored")
    assert labels == ["working"]
    window._is_closing = False
    callbacks.token = SimpleNamespace(cancelled=True)
    assert callbacks.cancelled()
    callbacks.token = None
    window._f5_cancelled = True
    assert callbacks.cancelled()

    import core.rps_precomputer as rps

    captured = []
    monkeypatch.setattr(
        rps.RPSPrecomputer,
        "run_f5_pipeline",
        lambda **kwargs: captured.append(kwargs) or 7,
    )
    window._f5_cancelled = False
    token = SimpleNamespace(cancelled=False)
    assert callbacks.run(token) == 7
    assert captured[-1]["data_provider"] == "provider"


def test_f5_callbacks_status_with_partial_optional_widgets():
    labels = []
    only_label = SimpleNamespace(
        _is_closing=False,
        _f5_cancelled=False,
        _call_in_ui=lambda callback: callback(),
        lbl_status=SimpleNamespace(setText=lambda text: labels.append(text)),
    )
    runtime._F5TaskCallbacks(only_label).set_status("label")
    assert labels == ["label"]
    titles = []
    only_title = SimpleNamespace(
        _is_closing=False,
        _f5_cancelled=False,
        _call_in_ui=lambda callback: callback(),
        _set_titlebar_sync_state=lambda *args: titles.append(args),
    )
    runtime._F5TaskCallbacks(only_title).set_status("title")
    assert titles == [("working", "title")]


def test_submit_f5_task_and_pending_cancel(monkeypatch):
    calls = []
    lifecycle = SimpleNamespace(run_background=lambda *args, **kwargs: calls.append((args, kwargs)))
    monkeypatch.setattr(runtime, "task_lifecycle_for", lambda *args, **kwargs: lifecycle)
    window = SimpleNamespace(_f5_cancelled=False, _f5_precompute_start_pending=False)
    runtime._submit_owned_f5_task(window, object())
    assert calls[-1][1]["timeout_sec"] == 1800.0
    window._f5_precompute_start_pending = True
    runtime.start_f5_precompute(window, task_manager=object())
    assert len(calls) == 1

    window._f5_precompute_start_pending = False
    window._f5_cancelled = True
    monkeypatch.setattr(runtime, "_system_log_shell_nav_grace_remaining_ms", lambda window: 0)
    runtime.start_f5_precompute(window, task_manager=object())
    assert window._f5_precompute_start_pending is False


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
    window = SimpleNamespace(_workspace=None, _save_ui_state=lambda: None)
    event_bus = SimpleNamespace(sig_app_closing=SimpleNamespace(emit=lambda: None))
    runtime.shutdown_main_window(
        window,
        event_bus=event_bus,
        task_manager=SimpleNamespace(shutdown=lambda: None),
    )
