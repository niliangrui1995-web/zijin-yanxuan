from __future__ import annotations

import time

from PyQt6.QtWidgets import QWidget

from ui.workspaces.background_preload_receipt import BackgroundPreloadCancellationReceipt
from ui.workspaces.classic_workspace import ClassicWorkspace
from ui.workspaces.tab_registry import TabLoadReason, startup_tab_keys


class _ControlledPreloadTab(QWidget):
    def __init__(self, key: str, events: list[tuple[str, str]], parent=None):
        super().__init__(parent)
        self.key = key
        self.events = events
        self.ready = False
        events.append(("construct", key))

    def prime_background_load(self) -> None:
        self.events.append(("prime", self.key))

    def is_background_preload_complete(self) -> bool:
        return self.ready

    def cancel_background_preload(self, *, reason: str):
        del reason
        return BackgroundPreloadCancellationReceipt.immediate()


class _FailingPrimePreloadTab(_ControlledPreloadTab):
    def prime_background_load(self) -> None:
        super().prime_background_load()
        raise RuntimeError("controlled watchlist preload failure")


def _install_controlled_factories(
    workspace: ClassicWorkspace,
    events: list[tuple[str, str]],
    *,
    failing_watchlist: bool = False,
) -> None:
    for spec in workspace._tab_specs:
        key = str(spec["key"])
        tab_type = _FailingPrimePreloadTab if failing_watchlist and key == "watchlist" else _ControlledPreloadTab

        def _factory(
            *,
            key=key,
            tab_type=tab_type,
            _workspace_parent_override=None,
            **_kwargs,
        ):
            return tab_type(key, events, _workspace_parent_override or workspace)

        spec["factory"] = _factory


def _new_workspace(qt_application, events: list[tuple[str, str]], *, failing_watchlist: bool = False):
    workspace = ClassicWorkspace(
        data_provider=object(),
        engine=object(),
        background_prewarm=True,
        watchlist_startup_tasks=False,
    )
    _install_controlled_factories(workspace, events, failing_watchlist=failing_watchlist)
    workspace.resize(960, 640)
    workspace.show()
    qt_application.processEvents()
    return workspace


def _stop_preload_timer(workspace: ClassicWorkspace) -> None:
    timer = workspace._background_prewarm_timer
    if timer is not None:
        timer.stop()


def _start_visible_watchlist(workspace: ClassicWorkspace) -> _ControlledPreloadTab:
    index = workspace._tab_index_for_key("watchlist")
    assert workspace.activate_tab(index, reason=TabLoadReason.USER.value) is True
    _stop_preload_timer(workspace)
    workspace._prewarm_next_tab()
    _stop_preload_timer(workspace)
    active = workspace._background_prewarm_active_widget
    assert isinstance(active, _ControlledPreloadTab)
    assert active.key == "watchlist"
    return active


def _finish_active_step(workspace: ClassicWorkspace) -> None:
    active = workspace._background_prewarm_active_widget
    assert isinstance(active, _ControlledPreloadTab)
    active.ready = True
    workspace._prewarm_next_tab()
    _stop_preload_timer(workspace)


def _set_current_index_without_callback(workspace: ClassicWorkspace, key: str) -> None:
    index = workspace._tab_index_for_key(key)
    previous = workspace.tabs.blockSignals(True)
    try:
        workspace.tabs.setCurrentIndex(index)
    finally:
        workspace.tabs.blockSignals(previous)


def test_all_planned_preload_keeps_remaining_tabs_after_visible_watchlist_ready(qt_application):
    """Regression for reintroducing the visible-watchlist early terminal handoff."""
    events: list[tuple[str, str]] = []
    workspace = _new_workspace(qt_application, events)
    expected = list(startup_tab_keys())

    try:
        assert len(expected) == 11
        _start_visible_watchlist(workspace)
        _finish_active_step(workspace)

        after_watchlist = workspace.background_preload_status()
        assert after_watchlist["visible_watchlist_state"] == "ready"
        assert after_watchlist["finished"] is False
        assert after_watchlist["remaining_keys"] == expected[1:]

        # The next ordinary step deliberately waits after the visible tab
        # activation.  This test drives the controlled queue after that quiet
        # window rather than treating the safety gate as a failed preload.
        workspace._background_prewarm_interaction_quiet_until = time.perf_counter() - 0.001
        for key in expected[1:]:
            workspace._prewarm_next_tab()
            _stop_preload_timer(workspace)
            active = workspace._background_prewarm_active_widget
            assert isinstance(active, _ControlledPreloadTab)
            assert active.key == key
            _finish_active_step(workspace)

        workspace._prewarm_next_tab()
        _stop_preload_timer(workspace)
        completed = workspace.background_preload_status()
        assert completed["finished"] is True
        assert completed["completion_scope"] == "all_planned"
        assert completed["completion_order"] == expected
        assert completed["startup_lazy_handoff_keys"] == []
    finally:
        workspace.shutdown()
        workspace.deleteLater()


def test_nonpriority_preload_waits_for_interaction_quiet_window_before_constructing(qt_application):
    """Regression for a normal queue step constructing during active tab interaction."""
    events: list[tuple[str, str]] = []
    workspace = _new_workspace(qt_application, events)

    try:
        workspace._background_prewarm_started = True
        workspace._background_prewarm_finished = False
        workspace._background_prewarm_queue = ["system_log"]
        workspace._background_prewarm_interaction_quiet_until = time.perf_counter() + 60.0
        _stop_preload_timer(workspace)

        workspace._prewarm_next_tab()
        _stop_preload_timer(workspace)
        assert ("construct", "system_log") not in events
        assert workspace._background_prewarm_active_key == ""
        assert workspace._background_prewarm_queue == ["system_log"]

        workspace._background_prewarm_interaction_quiet_until = time.perf_counter() - 0.001
        workspace._prewarm_next_tab()
        _stop_preload_timer(workspace)
        assert ("construct", "system_log") in events
        assert workspace._background_prewarm_active_key == "system_log"
    finally:
        workspace.shutdown()
        workspace.deleteLater()


def test_latest_current_priority_bypasses_quiet_window_but_stale_priority_does_not(qt_application):
    """Regression for stale A priority constructing after the user chose and left B."""
    events: list[tuple[str, str]] = []
    workspace = _new_workspace(qt_application, events)
    coordinator = workspace._background_preload_coordinator

    try:
        workspace._background_prewarm_started = True
        workspace._background_prewarm_finished = False
        workspace._background_prewarm_queue = ["system_log", "ai_industry_chain", "foreign_block"]
        _set_current_index_without_callback(workspace, "foreign_block")

        assert coordinator.prioritize("system_log", TabLoadReason.USER.value) is True
        assert coordinator.prioritize("foreign_block", TabLoadReason.USER.value) is True
        workspace._background_prewarm_interaction_quiet_until = time.perf_counter() + 60.0
        _stop_preload_timer(workspace)

        workspace._prewarm_next_tab()
        _stop_preload_timer(workspace)
        assert [key for event, key in events if event == "construct"] == ["ai_industry_chain"]
        _finish_active_step(workspace)

        workspace._prewarm_next_tab()
        _stop_preload_timer(workspace)
        assert [key for event, key in events if event == "construct"] == [
            "ai_industry_chain",
            "foreign_block",
        ]
        _finish_active_step(workspace)

        _set_current_index_without_callback(workspace, "watchlist")
        workspace._background_prewarm_interaction_quiet_until = time.perf_counter() + 60.0
        workspace._prewarm_next_tab()
        _stop_preload_timer(workspace)
        assert ("construct", "system_log") not in events
        assert workspace._background_prewarm_active_key == ""
        assert workspace._background_prewarm_queue == ["system_log"]

        workspace._background_prewarm_interaction_quiet_until = time.perf_counter() - 0.001
        workspace._prewarm_next_tab()
        _stop_preload_timer(workspace)
        assert ("construct", "system_log") in events
    finally:
        workspace.shutdown()
        workspace.deleteLater()


def test_returning_to_watchlist_does_not_finish_or_clear_an_all_planned_queue(qt_application):
    """Regression for the old lazy-resume pause path clearing normal full staging."""
    events: list[tuple[str, str]] = []
    workspace = _new_workspace(qt_application, events)

    try:
        watchlist = workspace.ensure_tab_loaded("watchlist", reason=TabLoadReason.USER.value)
        assert isinstance(watchlist, _ControlledPreloadTab)
        watchlist._workspace_background_preload_ready = True
        workspace._background_prewarm_started = True
        workspace._background_prewarm_finished = False
        workspace._background_prewarm_queue = ["system_log", "ai_industry_chain"]
        workspace._background_preload_coordinator._completion_scope = "all_planned"
        _stop_preload_timer(workspace)

        index = workspace._tab_index_for_key("watchlist")
        assert workspace.activate_tab(index, reason=TabLoadReason.USER.value) is True

        status = workspace.background_preload_status()
        assert status["finished"] is False
        assert status["completion_scope"] == "all_planned"
        assert status["remaining_keys"] == ["system_log", "ai_industry_chain"]
        assert status["startup_lazy_handoff_keys"] == []
    finally:
        workspace.shutdown()
        workspace.deleteLater()


def test_visible_watchlist_state_reports_terminal_failure(qt_application):
    """Regression for a Watchlist failure leaving visible-ready consumers waiting forever."""
    events: list[tuple[str, str]] = []
    workspace = _new_workspace(qt_application, events, failing_watchlist=True)

    try:
        index = workspace._tab_index_for_key("watchlist")
        assert workspace.activate_tab(index, reason=TabLoadReason.USER.value) is True
        _stop_preload_timer(workspace)
        workspace._prewarm_next_tab()
        _stop_preload_timer(workspace)

        status = workspace.background_preload_status()
        assert status["visible_watchlist_state"] == "terminal_failed"
        assert status["failures"]["watchlist"]
        assert status["visible_watchlist_detail"]
    finally:
        workspace.shutdown()
        workspace.deleteLater()


def test_hidden_watchlist_staging_is_not_reported_as_visible_ready_until_mount(qt_application):
    """Status consumers must distinguish hidden cache hydration from the visible first frame."""
    events: list[tuple[str, str]] = []
    workspace = _new_workspace(qt_application, events)

    try:
        _set_current_index_without_callback(workspace, "system_log")
        workspace._initial_real_tab_activated = True
        workspace._background_prewarm_enabled = True
        workspace._background_preload_coordinator.start()
        _stop_preload_timer(workspace)

        workspace._prewarm_next_tab()
        _stop_preload_timer(workspace)
        assert workspace._background_prewarm_active_key == "watchlist"
        _finish_active_step(workspace)

        hidden_status = workspace.background_preload_status()
        assert hidden_status["visible_watchlist_state"] == "staged_ready"
        assert workspace._spec_for_key_or_index("watchlist")["mounted"] is False

        index = workspace._tab_index_for_key("watchlist")
        assert workspace.activate_tab(index, reason=TabLoadReason.USER.value) is True

        visible_status = workspace.background_preload_status()
        assert visible_status["visible_watchlist_state"] == "ready"
        assert workspace._spec_for_key_or_index("watchlist")["mounted"] is True
    finally:
        workspace.shutdown()
        workspace.deleteLater()
