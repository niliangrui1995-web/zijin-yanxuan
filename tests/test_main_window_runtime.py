# -*- coding: utf-8 -*-
from types import SimpleNamespace

import ui.main_window_runtime as runtime


class _TaskManager:
    def __init__(self):
        self.calls = []

    def run_in_background(self, fn, *, task_id=None, **kwargs):
        self.calls.append((fn, task_id, kwargs))
        return task_id


class _F5Controller:
    def __init__(self):
        self.starts = []

    def start(self, request, *, on_event=None, on_finished=None):
        self.starts.append((request, on_event, on_finished))
        return True


def _make_window(*, current_tab="system_log", last_system_log_nav_at=100.0):
    state = {"current_tab": current_tab}
    return SimpleNamespace(
        _workspace=SimpleNamespace(_last_system_log_shell_nav_load_at=last_system_log_nav_at),
        _current_workspace_tab_key=lambda: state["current_tab"],
        _tab_state=state,
        _f5_cancelled=False,
        data_provider=SimpleNamespace(tdx_vipdoc=""),
        engine=object(),
        _f5_job_controller=_F5Controller(),
        _call_in_ui=lambda callback: callback(),
        _on_f5_done=lambda _count, _elapsed: None,
    )


def test_start_f5_precompute_defers_after_system_log_shell_nav(monkeypatch):
    scheduled = []
    window = _make_window(current_tab="system_log", last_system_log_nav_at=100.0)
    manager = _TaskManager()

    monkeypatch.setattr(runtime.time, "perf_counter", lambda: 105.0)
    monkeypatch.setattr(runtime.QTimer, "singleShot", lambda delay, callback: scheduled.append((delay, callback)))

    runtime.start_f5_precompute(window)

    assert scheduled and scheduled[0][0] == 5000
    assert manager.calls == []
    assert window._f5_precompute_start_pending is True
    assert getattr(window, "_f5_precompute_ui_grace_until", 0.0) == 0.0

    window._tab_state["current_tab"] = "watchlist"
    scheduled[0][1]()

    assert window._f5_precompute_start_pending is False
    assert manager.calls == []
    assert len(window._f5_job_controller.starts) == 1
    assert window._f5_precompute_ui_grace_until == 117.0


def test_start_f5_precompute_holds_while_system_log_stays_foreground(monkeypatch):
    scheduled = []
    window = _make_window(current_tab="system_log", last_system_log_nav_at=100.0)
    manager = _TaskManager()

    monkeypatch.setattr(runtime.time, "perf_counter", lambda: 105.0)
    monkeypatch.setattr(runtime.QTimer, "singleShot", lambda delay, callback: scheduled.append((delay, callback)))

    runtime.start_f5_precompute(window)
    scheduled[0][1]()

    assert manager.calls == []
    assert window._f5_precompute_start_pending is True
    assert scheduled[-1][0] == runtime.F5_SYSTEM_LOG_FOREGROUND_RECHECK_MS

    window._tab_state["current_tab"] = "scan"
    scheduled[-1][1]()

    assert window._f5_precompute_start_pending is False
    assert manager.calls == []
    assert len(window._f5_job_controller.starts) == 1


def test_start_f5_precompute_runs_immediately_outside_system_log_nav_grace(monkeypatch):
    scheduled = []
    window = _make_window(current_tab="watchlist", last_system_log_nav_at=100.0)
    manager = _TaskManager()

    monkeypatch.setattr(runtime.time, "perf_counter", lambda: 105.0)
    monkeypatch.setattr(runtime.QTimer, "singleShot", lambda delay, callback: scheduled.append((delay, callback)))

    runtime.start_f5_precompute(window)

    assert scheduled == []
    assert manager.calls == []
    assert len(window._f5_job_controller.starts) == 1
    assert getattr(window, "_f5_precompute_start_pending") is False
