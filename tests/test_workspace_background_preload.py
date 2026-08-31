from __future__ import annotations

import importlib
import time
from types import SimpleNamespace

import pytest
from PyQt6.QtWidgets import QWidget

import app.bootstrap.startup_orchestrator as startup_orchestrator_module
import ui.main_window_qt as main_window_qt_module
import ui.workspaces.classic_workspace as classic_workspace_module
from ui.tabs.stock_candidate_tab import StockCandidateTab
from ui.workspaces.background_preload_receipt import (
    BackgroundPreloadCancellationReceipt,
    cancel_background_preload_tasks,
)
from ui.workspaces.classic_workspace import ClassicWorkspace
from ui.workspaces.tab_registry import (
    TAB_DEFINITIONS,
    TabLoadReason,
    startup_tab_keys,
    widget_prewarm_tab_keys,
)


class _ControlledPreloadTab(QWidget):
    def __init__(self, key: str, events: list[tuple[str, str]], parent=None):
        super().__init__(parent)
        self.key = key
        self.events = events
        self.ready = False
        self.prime_calls = 0
        events.append(("construct", key))

    def prime_background_load(self):
        self.prime_calls += 1
        self.events.append(("prime", self.key))

    def is_background_preload_complete(self) -> bool:
        return self.ready

    def cancel_background_preload(self, *, reason: str):
        del reason
        return BackgroundPreloadCancellationReceipt.immediate()


class _ViewportBackgroundPreloadTab(_ControlledPreloadTab):
    def __init__(self, key: str, events: list[tuple[str, str]], parent=None):
        super().__init__(key, events, parent)
        self.workspace = parent
        self.viewport_background_sync_calls = 0
        self.preload_reveal_calls = 0
        self.guard_load_reasons = []

    def prepare_workspace_preload_reveal(self) -> None:
        self.preload_reveal_calls += 1

    def prepare_workspace_preload_repaint_guard(self, *, load_reason: str) -> None:
        self.guard_load_reasons.append(load_reason)

    def sync_workspace_viewport_background(self) -> None:
        self.viewport_background_sync_calls += 1
        workspace = self.workspace
        spec = workspace._spec_for_key_or_index("watchlist")
        assert spec["mounted"] is True
        assert workspace.tabs.currentWidget() is self


class _FailingPrimeTab(_ControlledPreloadTab):
    def prime_background_load(self):
        super().prime_background_load()
        raise RuntimeError("prime failed")


class _SupplementalSnapshotPreloadTab(_ControlledPreloadTab):
    def __init__(self, key: str, events: list[tuple[str, str]], parent=None):
        super().__init__(key, events, parent)
        self.snapshot_ready = False
        self.snapshot_prime_calls = 0

    def prime_workspace_background_snapshot(self) -> bool:
        self.snapshot_prime_calls += 1
        return True

    def is_workspace_background_snapshot_complete(self) -> bool:
        return self.snapshot_ready


class _DeferredConstructionPreloadTab(_ControlledPreloadTab):
    """A staged QWidget shell whose heavy GUI phases finish on later turns."""

    def __init__(self, key: str, events: list[tuple[str, str]], parent=None):
        super().__init__(key, events, parent)
        self.ui_construction_ready = False

    def is_background_ui_construction_complete(self) -> bool:
        return self.ui_construction_ready


class _ControlledCancellationReceipt:
    def __init__(self):
        self.settled = False

    def is_settled(self) -> bool:
        return self.settled

    def status(self) -> dict:
        return {
            "accepted": True,
            "task_ids": ["controlled-hydration"],
            "active_task_ids": [] if self.settled else ["controlled-hydration"],
            "local_settled": self.settled,
            "settled": self.settled,
        }


class _CancellablePreloadTab(_ControlledPreloadTab):
    def __init__(self, key: str, events: list[tuple[str, str]], parent=None):
        super().__init__(key, events, parent)
        self.cancel_calls = 0
        self.cancel_reasons = []
        self.activation_calls = 0
        self.cancellation_receipt = _ControlledCancellationReceipt()

    def cancel_background_preload(self, *, reason: str):
        self.cancel_calls += 1
        self.cancel_reasons.append(reason)
        self.events.append(("cancel", self.key))
        assert reason in {"step_timeout", "step_failed", "owner_shutdown", "watchlist_visible"}
        return self.cancellation_receipt

    def shutdown(self):
        self.cancellation_receipt.settled = True

    def on_workspace_tab_activated(self):
        self.activation_calls += 1


class _PausablePreloadTab(_ControlledPreloadTab):
    def __init__(self, key: str, events: list[tuple[str, str]], parent=None):
        super().__init__(key, events, parent)
        self.paused = False
        self.pause_calls = 0
        self.resume_calls = 0

    def pause_background_preload(self) -> bool:
        self.pause_calls += 1
        self.paused = True
        self.events.append(("pause", self.key))
        return True

    def resume_background_preload(self) -> bool:
        self.resume_calls += 1
        self.paused = False
        self.events.append(("resume", self.key))
        return True


class _WatchlistForegroundHoldPreloadTab(_ControlledPreloadTab):
    def should_hold_background_prewarm(self) -> bool:
        return True


class _ImmediateCancellationPreloadTab(_ControlledPreloadTab):
    def __init__(self, key: str, events: list[tuple[str, str]], parent=None):
        super().__init__(key, events, parent)
        self.cancel_reasons: list[str] = []

    def cancel_background_preload(self, *, reason: str):
        self.cancel_reasons.append(reason)
        self.events.append(("cancel", self.key))
        return BackgroundPreloadCancellationReceipt.immediate()


class _FailingCancellablePrimeTab(_CancellablePreloadTab):
    def prime_background_load(self):
        super().prime_background_load()
        raise RuntimeError("prime failed after hydration started")


class _RejectedCancellationTab(_ControlledPreloadTab):
    def cancel_background_preload(self, *, reason: str):
        del reason
        return BackgroundPreloadCancellationReceipt(accepted=False)


class _RaisingCancellationTab(_ControlledPreloadTab):
    def cancel_background_preload(self, *, reason: str):
        del reason
        raise RuntimeError("cancellation unavailable")


class _ExplodingSettlementReceipt:
    @staticmethod
    def status() -> dict:
        return {"accepted": True, "settled": False}

    @staticmethod
    def is_settled() -> bool:
        raise RuntimeError("settlement probe failed")


class _ExplodingSettlementTab(_ControlledPreloadTab):
    def cancel_background_preload(self, *, reason: str):
        del reason
        return _ExplodingSettlementReceipt()


class _ExplodingStatusReceipt:
    @staticmethod
    def status() -> dict:
        raise RuntimeError("status probe failed")

    @staticmethod
    def is_settled() -> bool:
        return True


class _ExplodingStatusTab(_ControlledPreloadTab):
    def cancel_background_preload(self, *, reason: str):
        del reason
        return _ExplodingStatusReceipt()


class _FakeLifecycle:
    def __init__(self, task_ids_by_name=None):
        self.calls = []
        self.task_ids_by_name = dict(task_ids_by_name or {})

    def task_ids_for(self, names):
        return tuple(
            task_id
            for name in names
            for task_id in self.task_ids_by_name.get(name, ())
        )

    def cancel(self, name, *, reason):
        self.calls.append((name, reason))
        self.task_ids_by_name.pop(name, None)
        return name == "first"

    @staticmethod
    def submissions_settled_for(names):
        del names
        return True


class _FakeRunner:
    def __init__(self):
        self.active = {"task-a", "task-b"}
        self.cancel_calls = []

    def is_active_task(self, task_id):
        return task_id in self.active

    def is_task_unsettled(self, task_id):
        return task_id in self.active

    def cancel_task(self, task_id, *, reason):
        self.cancel_calls.append((task_id, reason))
        return True


def _install_controlled_factories(workspace: ClassicWorkspace, events: list[tuple[str, str]]) -> None:
    # These manual state-machine tests advance the coordinator explicitly;
    # dedicated timing regressions exercise the production quiet window.
    workspace.BACKGROUND_PREWARM_INTERACTION_QUIET_MS = 0
    for spec in workspace._tab_specs:
        key = spec["key"]
        spec["factory"] = lambda key=key, **_kwargs: _ControlledPreloadTab(key, events, workspace)


def _stop_preload_timer(workspace: ClassicWorkspace) -> None:
    timer = workspace._background_prewarm_timer
    if timer is not None:
        timer.stop()


def _process_events_until(qt_application, predicate, *, timeout_seconds: float = 1.0) -> bool:
    deadline = time.perf_counter() + timeout_seconds
    while time.perf_counter() < deadline:
        qt_application.processEvents()
        if predicate():
            return True
        time.sleep(0.001)
    qt_application.processEvents()
    return bool(predicate())


def test_preload_ready_waits_for_supplemental_local_snapshot_physical_completion():
    from ui.workspaces.background_tab_preload import BackgroundTabPreloadCoordinator

    tab = _SupplementalSnapshotPreloadTab("scan", [])
    tab.ready = True
    try:
        assert BackgroundTabPreloadCoordinator._widget_preload_complete(tab) is False
        assert tab.snapshot_prime_calls == 1

        tab.snapshot_ready = True
        assert BackgroundTabPreloadCoordinator._widget_preload_complete(tab) is True
        assert tab.snapshot_prime_calls == 2
    finally:
        tab.deleteLater()


def test_preload_waits_for_staged_gui_construction_before_prime(qt_application):
    """A deferred QWidget shell must not run its runtime prime in the factory callback."""
    events: list[tuple[str, str]] = []
    workspace = ClassicWorkspace(
        data_provider=object(),
        engine=object(),
        background_prewarm=False,
        watchlist_startup_tasks=False,
    )
    _install_controlled_factories(workspace, events)
    first_key = "scan"
    first_spec = workspace._spec_for_key_or_index(first_key)
    first_spec["factory"] = lambda **_kwargs: _DeferredConstructionPreloadTab(
        first_key,
        events,
        workspace,
    )

    try:
        workspace._initial_real_tab_activated = True
        workspace._background_prewarm_enabled = True
        workspace._background_prewarm_started = True
        workspace._background_prewarm_finished = False
        workspace._background_prewarm_queue = [first_key]
        _stop_preload_timer(workspace)

        workspace._prewarm_next_tab()
        _stop_preload_timer(workspace)
        active = workspace._background_prewarm_active_widget
        assert isinstance(active, _DeferredConstructionPreloadTab)
        assert events == [("construct", first_key)]
        assert workspace.background_preload_status()["active_key"] == first_key

        active.ui_construction_ready = True
        workspace._prewarm_next_tab()
        _stop_preload_timer(workspace)
        assert events == [("construct", first_key), ("prime", first_key)]
        assert workspace.background_preload_status()["active_key"] == first_key
    finally:
        workspace.shutdown()
        workspace.deleteLater()


def test_preload_uses_registry_order_and_waits_for_each_data_completion(qt_application):
    events: list[tuple[str, str]] = []
    workspace = ClassicWorkspace(
        data_provider=object(),
        engine=object(),
        background_prewarm=False,
        watchlist_startup_tasks=False,
    )
    _install_controlled_factories(workspace, events)
    started_steps: list[str] = []
    workspace._background_preload_coordinator.stepStarted.connect(started_steps.append)

    try:
        initial = workspace.ensure_tab_loaded("watchlist", reason=TabLoadReason.RESTORE_LAST_TAB.value)
        assert isinstance(initial, _ControlledPreloadTab)
        workspace._initial_real_tab_activated = True
        workspace._background_prewarm_enabled = True
        workspace._start_background_tab_prewarm()
        _stop_preload_timer(workspace)

        expected_order = startup_tab_keys()
        assert workspace._background_prewarm_timer.parent() is workspace._background_preload_coordinator
        assert workspace._background_prewarm_queue == list(expected_order)
        current_index = workspace.tabs.currentIndex()

        for expected_key in expected_order:
            workspace._prewarm_next_tab()
            _stop_preload_timer(workspace)

            assert workspace._background_prewarm_active_key == expected_key
            assert workspace._background_prewarm_start_order[-1] == expected_key
            assert [key for event, key in events if event == "prime"][-1] == expected_key

            # A second poll must not construct or prime the following tab while
            # the current cache-only hydration is still active.
            event_count = len(events)
            workspace._prewarm_next_tab()
            _stop_preload_timer(workspace)
            assert len(events) == event_count
            assert workspace._background_prewarm_active_key == expected_key

            active = workspace._background_prewarm_active_widget
            active.ready = True
            workspace._prewarm_next_tab()
            _stop_preload_timer(workspace)
            assert workspace._background_prewarm_active_key == ""

        workspace._prewarm_next_tab()
        _stop_preload_timer(workspace)

        status = workspace.background_preload_status()
        assert status["finished"] is True
        assert status["start_order"] == list(expected_order)
        assert started_steps == list(expected_order)
        assert status["completion_order"] == list(expected_order)
        assert status["failures"] == {}
        assert status["max_concurrent_steps"] == 1
        assert workspace._background_prewarm_finished_at > 0.0
        assert workspace.tabs.currentIndex() == current_index
        assert len(workspace.iter_tabs()) == len(TAB_DEFINITIONS) == 11
        assert all(tab.prime_calls == 1 for tab in workspace.iter_tabs())
    finally:
        workspace.shutdown()
        workspace.deleteLater()


def test_preload_waits_for_startup_cache_bootstrap_terminal_signal(qt_application):
    events: list[tuple[str, str]] = []
    workspace = ClassicWorkspace(
        data_provider=object(),
        engine=object(),
        host=SimpleNamespace(_startup_enabled=True),
        background_prewarm=False,
        watchlist_startup_tasks=False,
    )
    _install_controlled_factories(workspace, events)

    try:
        workspace._initial_real_tab_activated = True
        workspace._background_prewarm_enabled = True

        workspace._start_background_tab_prewarm()

        assert workspace._background_prewarm_started is False
        assert workspace._background_prewarm_queue == []
        assert workspace.background_preload_status()["startup_cache_bootstrap_ready"] is False

        workspace._on_startup_cache_bootstrap_ready()
        _stop_preload_timer(workspace)

        assert workspace._background_prewarm_started is True
        assert workspace._background_prewarm_queue == list(startup_tab_keys())
        assert workspace.background_preload_status()["startup_cache_bootstrap_ready"] is True
    finally:
        workspace.shutdown()
        workspace.deleteLater()


def test_preload_failure_and_timeout_record_terminal_steps_then_continue(qt_application):
    events: list[tuple[str, str]] = []
    workspace = ClassicWorkspace(
        data_provider=object(),
        engine=object(),
        background_prewarm=False,
        watchlist_startup_tasks=False,
    )
    _install_controlled_factories(workspace, events)

    try:
        workspace._initial_real_tab_activated = True
        workspace._background_prewarm_enabled = True
        workspace._start_background_tab_prewarm()
        _stop_preload_timer(workspace)
        expected_order = list(startup_tab_keys())

        workspace._prewarm_next_tab()
        _stop_preload_timer(workspace)
        first_widget = workspace._background_prewarm_active_widget
        first_widget.is_background_preload_complete = lambda: (_ for _ in ()).throw(RuntimeError("failed"))
        workspace._prewarm_next_tab()
        _stop_preload_timer(workspace)

        workspace._prewarm_next_tab()
        _stop_preload_timer(workspace)
        workspace._background_prewarm_active_started_at = 0.0
        workspace._prewarm_next_tab()
        _stop_preload_timer(workspace)

        workspace._prewarm_next_tab()
        _stop_preload_timer(workspace)
        assert workspace._background_prewarm_active_key == expected_order[2]
        status = workspace.background_preload_status()
        assert status["completion_order"][:2] == expected_order[:2]
        assert status["failures"][expected_order[0]] == "failed"
        assert expected_order[1] in status["timeouts"]
    finally:
        workspace.shutdown()
        workspace.deleteLater()


def test_timeout_waits_for_cancel_receipt_before_starting_next_step(qt_application):
    events: list[tuple[str, str]] = []
    workspace = ClassicWorkspace(
        data_provider=object(),
        engine=object(),
        background_prewarm=False,
        watchlist_startup_tasks=False,
    )
    _install_controlled_factories(workspace, events)
    first_key = startup_tab_keys()[0]
    first_spec = workspace._spec_for_key_or_index(first_key)
    first_spec["factory"] = lambda **_kwargs: _CancellablePreloadTab(first_key, events, workspace)

    try:
        workspace._initial_real_tab_activated = True
        workspace._background_prewarm_enabled = True
        workspace._start_background_tab_prewarm()
        _stop_preload_timer(workspace)

        workspace._prewarm_next_tab()
        _stop_preload_timer(workspace)
        first_widget = workspace._background_prewarm_active_widget
        workspace._background_prewarm_active_started_at = 0.0

        workspace._prewarm_next_tab()
        _stop_preload_timer(workspace)

        status = workspace.background_preload_status()
        assert first_widget.cancel_calls == 1
        assert status["timeouts"] == [first_key]
        assert status["completion_order"] == []
        assert status["cancelling_key"] == first_key
        assert status["cancel_receipt"]["settled"] is False
        assert workspace._background_prewarm_active_key == first_key
        assert events.count(("construct", startup_tab_keys()[1])) == 0

        workspace._prewarm_next_tab()
        _stop_preload_timer(workspace)
        assert first_widget.cancel_calls == 1
        assert workspace._background_prewarm_active_key == first_key
        assert events.count(("construct", startup_tab_keys()[1])) == 0

        first_widget.cancellation_receipt.settled = True
        workspace._prewarm_next_tab()
        _stop_preload_timer(workspace)
        assert workspace._background_prewarm_active_key == ""

        workspace._prewarm_next_tab()
        _stop_preload_timer(workspace)
        assert workspace._background_prewarm_active_key == startup_tab_keys()[1]
        assert workspace.background_preload_status()["max_concurrent_steps"] == 1
    finally:
        workspace.shutdown()
        workspace.deleteLater()


def test_checker_failure_waits_for_cancel_receipt_before_starting_next_step(qt_application):
    events: list[tuple[str, str]] = []
    workspace = ClassicWorkspace(
        data_provider=object(),
        engine=object(),
        background_prewarm=False,
        watchlist_startup_tasks=False,
    )
    _install_controlled_factories(workspace, events)
    first_key = startup_tab_keys()[0]
    first_spec = workspace._spec_for_key_or_index(first_key)
    first_spec["factory"] = lambda **_kwargs: _CancellablePreloadTab(first_key, events, workspace)

    try:
        workspace._initial_real_tab_activated = True
        workspace._background_prewarm_enabled = True
        workspace._start_background_tab_prewarm()
        _stop_preload_timer(workspace)
        workspace._prewarm_next_tab()
        _stop_preload_timer(workspace)
        first_widget = workspace._background_prewarm_active_widget
        first_widget.is_background_preload_complete = lambda: (_ for _ in ()).throw(RuntimeError("failed"))

        workspace._prewarm_next_tab()
        _stop_preload_timer(workspace)

        status = workspace.background_preload_status()
        assert first_widget.cancel_reasons == ["step_failed"]
        assert status["failures"][first_key] == "failed"
        assert status["completion_order"] == []
        assert status["cancelling_key"] == first_key
        assert workspace._background_prewarm_active_key == first_key
        assert events.count(("construct", startup_tab_keys()[1])) == 0

        first_widget.cancellation_receipt.settled = True
        workspace._prewarm_next_tab()
        _stop_preload_timer(workspace)
        workspace._prewarm_next_tab()
        _stop_preload_timer(workspace)
        assert workspace._background_prewarm_active_key == startup_tab_keys()[1]
        assert workspace.background_preload_status()["max_concurrent_steps"] == 1
    finally:
        workspace.shutdown()
        workspace.deleteLater()


def test_prime_failure_after_start_waits_for_cancel_receipt_before_next_step(qt_application):
    events: list[tuple[str, str]] = []
    workspace = ClassicWorkspace(
        data_provider=object(),
        engine=object(),
        background_prewarm=False,
        watchlist_startup_tasks=False,
    )
    _install_controlled_factories(workspace, events)
    first_key = startup_tab_keys()[0]
    first_spec = workspace._spec_for_key_or_index(first_key)
    first_spec["factory"] = lambda **_kwargs: _FailingCancellablePrimeTab(first_key, events, workspace)

    try:
        workspace._initial_real_tab_activated = True
        workspace._background_prewarm_enabled = True
        workspace._start_background_tab_prewarm()
        _stop_preload_timer(workspace)
        workspace._prewarm_next_tab()
        _stop_preload_timer(workspace)

        first_widget = workspace._background_prewarm_active_widget
        status = workspace.background_preload_status()
        assert first_widget.cancel_reasons == ["step_failed"]
        assert status["failures"][first_key] == "startup prime failed"
        assert status["cancelling_key"] == first_key
        assert status["completion_order"] == []
        assert events.count(("construct", startup_tab_keys()[1])) == 0

        workspace._prewarm_next_tab()
        _stop_preload_timer(workspace)
        assert events.count(("construct", startup_tab_keys()[1])) == 0

        first_widget.cancellation_receipt.settled = True
        workspace._prewarm_next_tab()
        _stop_preload_timer(workspace)
        workspace._prewarm_next_tab()
        _stop_preload_timer(workspace)
        assert workspace._background_prewarm_active_key == startup_tab_keys()[1]
    finally:
        workspace.shutdown()
        workspace.deleteLater()


def test_prioritized_cancelled_step_retries_interactive_load_exactly_once(qt_application):
    events: list[tuple[str, str]] = []
    workspace = ClassicWorkspace(
        data_provider=object(),
        engine=object(),
        background_prewarm=False,
        watchlist_startup_tasks=False,
    )
    _install_controlled_factories(workspace, events)
    first_key = startup_tab_keys()[0]
    first_spec = workspace._spec_for_key_or_index(first_key)
    first_spec["factory"] = lambda **_kwargs: _CancellablePreloadTab(first_key, events, workspace)
    workspace._activation_callback_delay_ms = lambda: 0

    try:
        workspace._initial_real_tab_activated = True
        workspace._background_prewarm_enabled = True
        workspace._start_background_tab_prewarm()
        _stop_preload_timer(workspace)
        workspace._prewarm_next_tab()
        _stop_preload_timer(workspace)
        first_widget = workspace._background_prewarm_active_widget
        qt_application.processEvents()
        first_widget.activation_calls = 0
        coordinator = workspace._background_preload_coordinator
        assert coordinator.prioritize(first_key, TabLoadReason.USER.value) is True
        assert coordinator.prioritize(first_key, TabLoadReason.USER.value) is True
        workspace._background_prewarm_active_started_at = 0.0
        workspace._prewarm_next_tab()
        _stop_preload_timer(workspace)
        assert first_widget.activation_calls == 0

        first_widget.cancellation_receipt.settled = True
        workspace._prewarm_next_tab()
        _stop_preload_timer(workspace)
        qt_application.processEvents()

        assert first_widget.activation_calls == 1
        assert first_widget._workspace_noninteractive_loaded is False
        assert first_widget._workspace_load_reason == TabLoadReason.USER.value
        assert workspace.background_preload_status()["promoted_order"] == [first_key]
    finally:
        workspace.shutdown()
        workspace.deleteLater()


def test_cancellation_receipt_tracks_every_real_worker_slot_until_all_exit():
    owner = type("Owner", (), {})()
    owner._task_lifecycle = _FakeLifecycle()
    runner = _FakeRunner()

    receipt = cancel_background_preload_tasks(
        owner,
        lifecycle_names=("first", "second"),
        task_ids=("task-a", "task-b", "task-already-settled"),
        reason="step_timeout",
        reset_state=lambda: None,
        runner=runner,
    )

    assert owner._task_lifecycle.calls == [
        ("first", "step_timeout"),
        ("second", "step_timeout"),
    ]
    assert runner.cancel_calls == [
        ("task-a", "step_timeout"),
        ("task-b", "step_timeout"),
        ("task-already-settled", "step_timeout"),
    ]
    assert receipt.is_settled() is False
    runner.active.remove("task-a")
    assert receipt.is_settled() is False
    runner.active.remove("task-b")
    assert receipt.is_settled() is True


def test_cancellation_receipt_tracks_dynamic_lifecycle_task_ids():
    owner = type("Owner", (), {})()
    owner._task_lifecycle = _FakeLifecycle(
        {"dynamic-preload": ("runtime-generated-preload",)}
    )
    runner = _FakeRunner()
    runner.active = {"runtime-generated-preload"}

    receipt = cancel_background_preload_tasks(
        owner,
        lifecycle_names=("dynamic-preload",),
        task_ids=(),
        reason="step_timeout",
        reset_state=lambda: None,
        runner=runner,
    )

    assert runner.cancel_calls == [
        ("runtime-generated-preload", "step_timeout"),
    ]
    assert receipt.status()["task_ids"] == ["runtime-generated-preload"]
    assert receipt.is_settled() is False

    runner.active.clear()
    assert receipt.is_settled() is True


def test_cancellation_receipt_rejects_failed_scheduler_cancellation():
    class _RejectingRunner(_FakeRunner):
        def cancel_task(self, task_id, *, reason):
            self.cancel_calls.append((task_id, reason))
            return False

    owner = type("Owner", (), {})()
    runner = _RejectingRunner()
    runner.active = {"task-a"}

    receipt = cancel_background_preload_tasks(
        owner,
        lifecycle_names=(),
        task_ids=("task-a",),
        reason="step_timeout",
        reset_state=lambda: None,
        runner=runner,
    )

    assert receipt.accepted is False
    runner.active.clear()
    assert receipt.is_settled() is False


def test_cancellation_receipt_fails_closed_when_dynamic_task_tracking_raises():
    class _ExplodingLifecycle:
        def task_ids_for(self, names):
            del names
            raise RuntimeError("task id tracking failed")

        def cancel(self, name, *, reason):
            del name, reason
            return True

    owner = type("Owner", (), {})()
    owner._task_lifecycle = _ExplodingLifecycle()
    runner = _FakeRunner()

    receipt = cancel_background_preload_tasks(
        owner,
        lifecycle_names=("hidden-generated",),
        task_ids=(),
        reason="step_timeout",
        reset_state=lambda: None,
        runner=runner,
    )

    assert receipt.accepted is False
    assert receipt.is_settled() is False
    assert receipt.status()["tracking_ok"] is False


def test_cancellation_receipt_fails_closed_when_legacy_lifecycle_cannot_list_task_ids():
    class _LegacyLifecycle:
        @staticmethod
        def cancel(name, *, reason):
            del name, reason
            return True

    owner = type("Owner", (), {})()
    owner._task_lifecycle = _LegacyLifecycle()

    receipt = cancel_background_preload_tasks(
        owner,
        lifecycle_names=("hidden-generated",),
        task_ids=(),
        reason="step_timeout",
        reset_state=lambda: None,
        runner=_FakeRunner(),
    )

    assert receipt.accepted is False
    assert receipt.status()["tracking_ok"] is False
    assert receipt.is_settled() is False


def test_cancellation_receipt_fails_closed_when_lifecycle_cannot_report_submissions():
    class _LegacyLifecycle:
        @staticmethod
        def task_ids_for(names):
            del names
            return ()

        @staticmethod
        def cancel(name, *, reason):
            del name, reason
            return True

    owner = type("Owner", (), {})()
    owner._task_lifecycle = _LegacyLifecycle()

    receipt = cancel_background_preload_tasks(
        owner,
        lifecycle_names=("submission-inflight",),
        task_ids=(),
        reason="step_timeout",
        reset_state=lambda: None,
        runner=_FakeRunner(),
    )

    assert receipt.accepted is False
    assert receipt.status()["tracking_ok"] is False
    assert receipt.is_settled() is False


def test_cancellation_receipt_uses_bounded_wait_from_wait_only_legacy_runner():
    class _WaitOnlyRunner:
        def __init__(self):
            self.settled = False
            self.wait_calls = []

        @staticmethod
        def cancel_task(task_id, *, reason):
            del task_id, reason
            return True

        def wait_for_tasks(self, task_ids, *, timeout_ms):
            self.wait_calls.append((tuple(task_ids), timeout_ms))
            return self.settled

    owner = type("Owner", (), {})()
    runner = _WaitOnlyRunner()
    receipt = cancel_background_preload_tasks(
        owner,
        lifecycle_names=(),
        task_ids=("legacy-wait-task",),
        reason="step_timeout",
        reset_state=lambda: None,
        runner=runner,
    )

    assert receipt.accepted is True
    assert receipt.is_settled() is False
    runner.settled = True
    assert receipt.is_settled() is True
    assert runner.wait_calls
    assert all(timeout_ms == 0 for _task_ids, timeout_ms in runner.wait_calls)


def test_cancellation_receipt_tracks_workspace_snapshot_on_distinct_owner():
    lifecycle_owner = type("LifecycleOwner", (), {})()
    lifecycle_owner._task_lifecycle = _FakeLifecycle(
        {"warm-cache": ("runtime-generated-warm-cache",)}
    )
    snapshot_owner = type("SnapshotOwner", (), {})()
    snapshot_owner._task_lifecycle = _FakeLifecycle(
        {"workspace_background_snapshot": ("workspace-snapshot",)}
    )
    snapshot_owner._workspace_background_snapshot_started = True
    snapshot_owner._workspace_background_snapshot_task_id = "workspace-snapshot"
    snapshot_owner._workspace_background_snapshot_cancelled = False
    snapshot_owner.snapshot_apply_pending = True

    def _cancel_workspace_background_snapshot_preload() -> None:
        snapshot_owner._workspace_background_snapshot_cancelled = True
        snapshot_owner.snapshot_apply_pending = False

    def _workspace_background_snapshot_preload_settled() -> bool:
        return (
            snapshot_owner._workspace_background_snapshot_cancelled
            and not snapshot_owner.snapshot_apply_pending
        )

    snapshot_owner._cancel_workspace_background_snapshot_preload = (
        _cancel_workspace_background_snapshot_preload
    )
    snapshot_owner._workspace_background_snapshot_preload_settled = (
        _workspace_background_snapshot_preload_settled
    )
    runner = _FakeRunner()
    runner.active = {"runtime-generated-warm-cache", "workspace-snapshot"}

    receipt = cancel_background_preload_tasks(
        lifecycle_owner,
        snapshot_owner=snapshot_owner,
        lifecycle_names=("warm-cache",),
        task_ids=(),
        reason="workspace_shutdown",
        reset_state=lambda: None,
        runner=runner,
    )

    assert lifecycle_owner._task_lifecycle.calls == [
        ("warm-cache", "workspace_shutdown"),
    ]
    assert snapshot_owner._task_lifecycle.calls == [
        ("workspace_background_snapshot", "workspace_shutdown"),
    ]
    assert receipt.status()["task_ids"] == [
        "runtime-generated-warm-cache",
        "workspace-snapshot",
    ]
    assert receipt.is_settled() is False

    runner.active.clear()
    assert receipt.is_settled() is True


def test_cancellation_receipt_auto_tracks_workspace_snapshot_until_worker_physical_exit():
    owner = type("Owner", (), {})()
    owner._task_lifecycle = _FakeLifecycle()
    owner._workspace_background_snapshot_started = True
    owner._workspace_background_snapshot_task_id = "workspace-snapshot"
    owner._workspace_background_snapshot_cancelled = False
    owner.snapshot_apply_pending = True

    def _cancel_workspace_background_snapshot_preload() -> None:
        owner._workspace_background_snapshot_cancelled = True
        owner.snapshot_apply_pending = False

    def _workspace_background_snapshot_preload_settled() -> bool:
        return owner._workspace_background_snapshot_cancelled and not owner.snapshot_apply_pending

    owner._cancel_workspace_background_snapshot_preload = _cancel_workspace_background_snapshot_preload
    owner._workspace_background_snapshot_preload_settled = _workspace_background_snapshot_preload_settled
    runner = _FakeRunner()
    runner.active.add("workspace-snapshot")

    receipt = cancel_background_preload_tasks(
        owner,
        lifecycle_names=("first",),
        task_ids=("task-a", "task-b"),
        reason="workspace_shutdown",
        reset_state=lambda: None,
        runner=runner,
    )

    assert owner._task_lifecycle.calls == [
        ("first", "workspace_shutdown"),
        ("workspace_background_snapshot", "workspace_shutdown"),
    ]
    assert runner.cancel_calls == [
        ("task-a", "workspace_shutdown"),
        ("task-b", "workspace_shutdown"),
        ("workspace-snapshot", "workspace_shutdown"),
    ]
    assert owner.snapshot_apply_pending is False
    assert receipt.status()["active_task_ids"] == ["task-a", "task-b", "workspace-snapshot"]

    runner.active.remove("task-a")
    runner.active.remove("task-b")
    assert receipt.is_settled() is False
    assert receipt.status()["active_task_ids"] == ["workspace-snapshot"]

    runner.active.remove("workspace-snapshot")
    assert receipt.is_settled() is True
    assert receipt.status()["local_settled"] is True


@pytest.mark.parametrize("definition", TAB_DEFINITIONS, ids=lambda definition: definition.key)
def test_every_registered_tab_exposes_preload_cancellation_receipt_capability(definition):
    module = importlib.import_module(definition.module_name)
    tab_class = getattr(module, definition.class_name)

    assert callable(getattr(tab_class, "prime_background_load", None))
    assert callable(getattr(tab_class, "is_background_preload_complete", None))
    assert callable(getattr(tab_class, "cancel_background_preload", None))
    if definition.key != "system_log":
        assert callable(getattr(tab_class, "on_workspace_tab_activated", None)) or callable(
            getattr(tab_class, "_ensure_runtime_started", None)
        )


def test_shutdown_keeps_receipt_that_proves_active_hydration_settled(qt_application):
    events: list[tuple[str, str]] = []
    workspace = ClassicWorkspace(
        data_provider=object(),
        engine=object(),
        background_prewarm=False,
        watchlist_startup_tasks=False,
    )
    _install_controlled_factories(workspace, events)
    first_key = startup_tab_keys()[0]
    first_spec = workspace._spec_for_key_or_index(first_key)
    first_spec["factory"] = lambda **_kwargs: _CancellablePreloadTab(first_key, events, workspace)
    workspace._initial_real_tab_activated = True
    workspace._background_prewarm_enabled = True
    workspace._start_background_tab_prewarm()
    _stop_preload_timer(workspace)
    workspace._prewarm_next_tab()
    _stop_preload_timer(workspace)

    workspace.shutdown()

    status = workspace.background_preload_status()
    assert status["shutdown_cancel_receipts"]
    assert status["shutdown_cancellation_settled"] is True
    assert status["shutdown_cancel_receipts"][0]["settled"] is True
    workspace.deleteLater()


def test_preload_waits_for_runtime_and_initial_real_tab_without_pending_fanout(qt_application):
    workspace = ClassicWorkspace(
        data_provider=None,
        engine=None,
        background_prewarm=False,
        watchlist_startup_tasks=False,
    )
    workspace._background_prewarm_enabled = True
    workspace._initial_real_tab_activated = True

    try:
        workspace._start_background_tab_prewarm()
        assert workspace._background_prewarm_started is False
        assert workspace._background_prewarm_queue == []
        assert workspace._runtime_pending_tab_loads == {}

        workspace.attach_runtime_services(data_provider=object())
        assert workspace._background_prewarm_started is False
        assert workspace._runtime_pending_tab_loads == {}

        workspace.attach_runtime_services(engine=object())
        _stop_preload_timer(workspace)
        assert workspace._background_prewarm_started is True
        assert workspace._background_prewarm_queue == list(startup_tab_keys())
        assert workspace._runtime_pending_tab_loads == {}
    finally:
        workspace.shutdown()
        workspace.deleteLater()


def test_interactive_activation_promotes_preloaded_widget_without_reconstruction(qt_application):
    events: list[tuple[str, str]] = []
    workspace = ClassicWorkspace(
        data_provider=object(),
        engine=object(),
        background_prewarm=False,
        watchlist_startup_tasks=False,
    )
    _install_controlled_factories(workspace, events)

    try:
        widget = workspace.ensure_tab_loaded("scan", reason=TabLoadReason.BACKGROUND_PREWARM.value)
        assert isinstance(widget, _ControlledPreloadTab)
        assert widget._workspace_noninteractive_loaded is True
        scan_index = workspace._tab_index_for_key("scan")

        assert workspace.activate_tab(scan_index, reason=TabLoadReason.USER.value) is True
        qt_application.processEvents()

        assert workspace.get_loaded_tab("scan") is widget
        assert widget._workspace_noninteractive_loaded is False
        assert widget._workspace_load_reason == TabLoadReason.USER.value
        assert [key for event, key in events if event == "construct"].count("scan") == 1
    finally:
        workspace.shutdown()
        workspace.deleteLater()


def test_interactive_click_prioritizes_future_step_without_duplicate_construct_or_prime(qt_application):
    events: list[tuple[str, str]] = []
    workspace = ClassicWorkspace(
        data_provider=object(),
        engine=object(),
        background_prewarm=False,
        watchlist_startup_tasks=False,
    )
    _install_controlled_factories(workspace, events)

    try:
        workspace._initial_real_tab_activated = True
        workspace._background_prewarm_enabled = True
        workspace._start_background_tab_prewarm()
        _stop_preload_timer(workspace)

        workspace._prewarm_next_tab()
        _stop_preload_timer(workspace)
        first_key = startup_tab_keys()[0]
        assert workspace._background_prewarm_active_key == first_key

        scan_index = workspace._tab_index_for_key("scan")
        workspace._lazy_loading_keys.add("scan")
        assert workspace.activate_tab(scan_index, reason=TabLoadReason.USER.value) is True
        _stop_preload_timer(workspace)
        assert workspace._background_prewarm_queue[0] == "scan"
        assert workspace._lazy_loading_keys == set()
        assert [key for event, key in events if event == "construct"].count("scan") == 0

        # A stale zero-delay callback no longer owns the promoted key.
        classic_workspace_module._load_queued_tab(workspace, "scan", TabLoadReason.USER.value)
        assert [key for event, key in events if event == "construct"].count("scan") == 0

        first_widget = workspace._background_prewarm_active_widget
        first_widget.ready = True
        workspace._prewarm_next_tab()
        _stop_preload_timer(workspace)
        workspace._prewarm_next_tab()
        _stop_preload_timer(workspace)

        promoted = workspace.get_loaded_tab("scan")
        assert workspace._background_prewarm_active_key == "scan"
        assert promoted._workspace_noninteractive_loaded is True
        assert [key for event, key in events if event == "construct"].count("scan") == 1
        assert [key for event, key in events if event == "prime"].count("scan") == 1

        # Repeated activation while the serial step is active cannot start a second read.
        assert workspace.activate_tab(scan_index, reason=TabLoadReason.USER.value) is True
        assert [key for event, key in events if event == "construct"].count("scan") == 1
        assert [key for event, key in events if event == "prime"].count("scan") == 1

        promoted.ready = True
        workspace._prewarm_next_tab()
        _stop_preload_timer(workspace)
        assert promoted._workspace_noninteractive_loaded is False

        workspace._prewarm_next_tab()
        _stop_preload_timer(workspace)
        assert workspace._background_prewarm_active_key == "system_log"
        status = workspace.background_preload_status()
        assert status["promoted_order"] == ["scan"]
        assert status["pending_priority_keys"] == []
    finally:
        workspace.shutdown()
        workspace.deleteLater()


@pytest.mark.parametrize("target_key", ["foreign_block", "earnings", "fund_holdings", "lhb"])
def test_interactive_priority_recursively_promotes_ai_dependency_before_consumer(
    qt_application,
    target_key,
):
    events: list[tuple[str, str]] = []
    workspace = ClassicWorkspace(
        data_provider=object(),
        engine=object(),
        background_prewarm=False,
        watchlist_startup_tasks=False,
    )
    _install_controlled_factories(workspace, events)

    try:
        workspace._initial_real_tab_activated = True
        workspace._background_prewarm_enabled = True
        workspace._start_background_tab_prewarm()
        _stop_preload_timer(workspace)
        workspace._prewarm_next_tab()
        _stop_preload_timer(workspace)

        target_index = workspace._tab_index_for_key(target_key)
        assert workspace.activate_tab(target_index, reason=TabLoadReason.USER.value) is True
        _stop_preload_timer(workspace)
        assert workspace._background_prewarm_queue[:2] == ["ai_industry_chain", target_key]

        workspace._background_prewarm_active_widget.ready = True
        workspace._prewarm_next_tab()
        _stop_preload_timer(workspace)
        workspace._prewarm_next_tab()
        _stop_preload_timer(workspace)
        assert workspace._background_prewarm_active_key == "ai_industry_chain"
        assert events.count(("construct", target_key)) == 0

        workspace._background_prewarm_active_widget.ready = True
        workspace._prewarm_next_tab()
        _stop_preload_timer(workspace)
        workspace._prewarm_next_tab()
        _stop_preload_timer(workspace)
        assert workspace._background_prewarm_active_key == target_key
        assert events.index(("prime", "ai_industry_chain")) < events.index(("prime", target_key))
        assert workspace.background_preload_status()["max_concurrent_steps"] == 1
    finally:
        workspace.shutdown()
        workspace.deleteLater()


def test_interactive_priority_before_first_real_tab_is_owned_by_dependency_queue(qt_application):
    events: list[tuple[str, str]] = []
    workspace = ClassicWorkspace(
        data_provider=object(),
        engine=object(),
        background_prewarm=True,
        watchlist_startup_tasks=False,
    )
    _install_controlled_factories(workspace, events)

    try:
        assert workspace._initial_real_tab_activated is False
        target_index = workspace._tab_index_for_key("stock_candidates")
        assert workspace.activate_tab(target_index, reason=TabLoadReason.USER.value) is True
        _stop_preload_timer(workspace)

        status = workspace.background_preload_status()
        assert status["started"] is True
        assert status["pending_priority_keys"] == ["stock_candidates"]
        assert status["priority_closures"]["stock_candidates"][0] == "watchlist"
        assert workspace._background_prewarm_queue[0] == "watchlist"
        assert events == []

        workspace._prewarm_next_tab()
        _stop_preload_timer(workspace)
        assert workspace._background_prewarm_active_key == "watchlist"
        assert events[:2] == [("construct", "watchlist"), ("prime", "watchlist")]
        assert events.count(("construct", "stock_candidates")) == 0
    finally:
        workspace.shutdown()
        workspace.deleteLater()


def test_prestart_restore_marks_initial_tab_ready_only_after_hydration(qt_application):
    events: list[tuple[str, str]] = []
    host = SimpleNamespace(_launch_started_at=time.perf_counter() - 0.1)
    workspace = ClassicWorkspace(
        data_provider=object(),
        engine=object(),
        host=host,
        background_prewarm=True,
        watchlist_startup_tasks=False,
    )
    _install_controlled_factories(workspace, events)

    try:
        watchlist_index = workspace._tab_index_for_key("watchlist")
        assert workspace.activate_tab(watchlist_index, reason=TabLoadReason.RESTORE_LAST_TAB.value) is True
        _stop_preload_timer(workspace)
        workspace._prewarm_next_tab()
        _stop_preload_timer(workspace)
        assert workspace._initial_real_tab_activated is False

        workspace._background_prewarm_active_widget.ready = True
        workspace._prewarm_next_tab()
        _stop_preload_timer(workspace)
        assert workspace._initial_real_tab_activated is True
        assert workspace._initial_tab_ready_elapsed_ms >= 100.0
    finally:
        workspace.shutdown()
        workspace.deleteLater()


def test_visible_watchlist_prewarm_continues_hidden_staging_before_runtime_consumers(
    qt_application,
    monkeypatch,
):
    events: list[tuple[str, str]] = []
    workspace = ClassicWorkspace(
        data_provider=object(),
        engine=object(),
        background_prewarm=False,
        watchlist_startup_tasks=False,
    )
    _install_controlled_factories(workspace, events)
    quote_universe_refreshes = []
    workspace._refresh_central_quote_code_supplier = (
        lambda: quote_universe_refreshes.append("refresh")
    )
    workspace.resize(960, 640)
    workspace.show()
    qt_application.processEvents()

    try:
        workspace._background_prewarm_enabled = True
        watchlist_index = workspace._tab_index_for_key("watchlist")
        placeholder = workspace.tabs.widget(watchlist_index)
        assert workspace.activate_tab(watchlist_index, reason=TabLoadReason.USER.value) is True
        _stop_preload_timer(workspace)

        expected_order = list(startup_tab_keys())
        workspace._prewarm_next_tab()
        _stop_preload_timer(workspace)
        assert workspace._background_prewarm_active_key == "watchlist"
        watchlist = workspace._background_prewarm_active_widget
        watchlist_spec = workspace._spec_for_key_or_index("watchlist")
        assert watchlist_spec["loaded"] is True
        assert watchlist_spec["mounted"] is False
        assert workspace.tabs.currentWidget() is placeholder
        assert watchlist.parentWidget() is workspace._background_preload_staging_host

        watchlist.ready = True
        workspace._prewarm_next_tab()
        _stop_preload_timer(workspace)
        qt_application.processEvents()

        status = workspace.background_preload_status()
        hidden_specs = [spec for spec in workspace.tab_specs() if spec["key"] != "watchlist"]

        assert status["finished"] is False
        assert status["completion_scope"] == "all_planned"
        assert status["visible_watchlist_state"] == "ready"
        assert status["start_order"] == ["watchlist"]
        assert status["completion_order"] == ["watchlist"]
        assert status["startup_lazy_handoff_keys"] == []
        assert status["startup_lazy_handoff_count"] == 0
        assert quote_universe_refreshes == []
        assert status["remaining_keys"] == expected_order[1:]
        assert status["pending_priority_keys"] == []
        assert status["active_key"] == ""
        assert status["active_step_count"] == 0
        assert status["max_concurrent_steps"] == 1
        assert status["blocked_reason"] == ""

        assert workspace.tabs.count() == len(TAB_DEFINITIONS) == 11
        assert workspace.tabs.currentWidget() is watchlist
        assert watchlist_spec["mounted"] is True
        assert watchlist.isVisible() is True
        assert len(hidden_specs) == 10
        assert all(not spec["loaded"] for spec in hidden_specs)
        assert all(
            workspace.tabs.widget(workspace._tab_index_for_key(spec["key"])) is spec["widget"]
            for spec in hidden_specs
        )
        assert all(("construct", key) not in events for key in expected_order[1:])

        startup_host = SimpleNamespace(workspace=workspace)
        assert startup_orchestrator_module._background_preload_is_settled(startup_host) is False
        assert main_window_qt_module._background_tab_preload_settled(workspace) is False

        for expected_key in expected_order[1:]:
            workspace._prewarm_next_tab()
            _stop_preload_timer(workspace)
            assert workspace._background_prewarm_active_key == expected_key
            workspace._background_prewarm_active_widget.ready = True
            workspace._prewarm_next_tab()
            _stop_preload_timer(workspace)

        # The final terminal step only schedules the queue drain; advance once
        # more to let the coordinator record all-planned completion.
        workspace._prewarm_next_tab()
        _stop_preload_timer(workspace)

        final_status = workspace.background_preload_status()
        assert final_status["finished"] is True
        assert final_status["completion_scope"] == "all_planned"
        assert final_status["visible_watchlist_state"] == "ready"
        assert final_status["startup_lazy_handoff_keys"] == []
        assert final_status["remaining_keys"] == []
        assert final_status["active_key"] == ""
        assert final_status["active_step_count"] == 0
        assert final_status["max_concurrent_steps"] == 1
        assert final_status["blocked_reason"] == ""
        assert all(spec["loaded"] for spec in hidden_specs)
        assert all(spec["mounted"] is False for spec in hidden_specs)
        assert all(
            workspace.tabs.widget(workspace._tab_index_for_key(spec["key"])) is spec["page_widget"]
            for spec in hidden_specs
        )
        assert quote_universe_refreshes == ["refresh"]
        assert startup_orchestrator_module._background_preload_is_settled(startup_host) is True
        assert main_window_qt_module._background_tab_preload_settled(workspace) is True

        prewarm_calls = []
        scheduled = []
        finished_at = workspace._background_prewarm_finished_at
        monkeypatch.setattr(
            main_window_qt_module.time,
            "perf_counter",
            lambda: finished_at + main_window_qt_module.KLINE_PREWARM_SHELL_NAV_QUIET_SEC + 0.1,
        )
        monkeypatch.setattr(
            main_window_qt_module.kline_manager,
            "prewarm",
            lambda **kwargs: prewarm_calls.append(kwargs) or True,
        )
        monkeypatch.setattr(
            main_window_qt_module.QTimer,
            "singleShot",
            lambda delay_ms, callback: scheduled.append((delay_ms, callback)),
        )
        main_window = SimpleNamespace(
            _workspace=workspace,
            _kline_prewarm_enabled=True,
            _is_closing=False,
            _pending_f5_request=False,
            _f5_precompute_start_pending=False,
            _f5_job_controller=None,
        )

        main_window_qt_module._try_post_paint_kline_prewarm(main_window)

        assert prewarm_calls == [{"delay_ms": 0, "hidden_view": True}]
        assert scheduled == []

        assert workspace.tabs.count() == len(TAB_DEFINITIONS) == 11
        assert events.count(("construct", "stock_candidates")) == 1
    finally:
        workspace.shutdown()
        workspace.deleteLater()


def test_visible_watchlist_holds_ordinary_prewarm_until_foreground_is_released(qt_application):
    class _ForegroundProtectedPreloadTab(_ControlledPreloadTab):
        def should_hold_background_prewarm(self) -> bool:
            return True

    events: list[tuple[str, str]] = []
    workspace = ClassicWorkspace(
        data_provider=object(),
        engine=object(),
        background_prewarm=False,
        watchlist_startup_tasks=False,
    )
    _install_controlled_factories(workspace, events)
    watchlist_spec = workspace._spec_for_key_or_index("watchlist")
    watchlist_spec["factory"] = lambda **_kwargs: _ForegroundProtectedPreloadTab(
        "watchlist", events, workspace
    )
    workspace.resize(960, 640)
    workspace.show()
    qt_application.processEvents()

    try:
        watchlist_index = workspace._tab_index_for_key("watchlist")
        workspace._background_prewarm_enabled = True
        assert workspace.activate_tab(watchlist_index, reason=TabLoadReason.USER.value) is True
        _stop_preload_timer(workspace)

        workspace._prewarm_next_tab()
        _stop_preload_timer(workspace)
        assert workspace._background_prewarm_active_key == "watchlist"
        workspace._background_prewarm_active_widget.ready = True

        workspace._prewarm_next_tab()
        _stop_preload_timer(workspace)
        workspace._prewarm_next_tab()
        _stop_preload_timer(workspace)

        held_status = workspace.background_preload_status()
        assert workspace._background_prewarm_active_key == ""
        assert held_status["foreground_hold_reason"] == "watchlist_active"
        assert held_status["start_order"] == ["watchlist"]
        assert held_status["remaining_keys"] == list(startup_tab_keys())[1:]

        system_log_index = workspace._tab_index_for_key("system_log")
        previous_blocked = workspace.tabs.blockSignals(True)
        try:
            workspace.tabs.setCurrentIndex(system_log_index)
        finally:
            workspace.tabs.blockSignals(previous_blocked)
        classic_workspace_module._set_workspace_tab_activity(workspace, "system_log")

        workspace._prewarm_next_tab()
        _stop_preload_timer(workspace)
        assert workspace._background_prewarm_active_key == "system_log"
        assert workspace.background_preload_status()["foreground_hold_reason"] == ""
    finally:
        workspace.shutdown()
        workspace.deleteLater()


def test_background_prewarm_placeholder_replacement_arms_current_ai_repaint_guard(qt_application):
    class _GuardedAiTab(_ControlledPreloadTab):
        def __init__(self, key, events, parent=None):
            super().__init__(key, events, parent)
            self.guard_load_reasons = []

        def prepare_workspace_preload_repaint_guard(self, *, load_reason: str) -> None:
            self.guard_load_reasons.append(load_reason)

    events: list[tuple[str, str]] = []
    workspace = ClassicWorkspace(
        data_provider=object(),
        engine=object(),
        background_prewarm=False,
        watchlist_startup_tasks=False,
    )
    _install_controlled_factories(workspace, events)
    ai_spec = workspace._spec_for_key_or_index("ai_industry_chain")
    ai_spec["factory"] = lambda **_kwargs: _GuardedAiTab("ai_industry_chain", events, workspace)

    try:
        ai_index = workspace._tab_index_for_key("ai_industry_chain")
        blocked = workspace.tabs.blockSignals(True)
        workspace.tabs.setCurrentIndex(ai_index)
        workspace.tabs.blockSignals(blocked)
        ai_tab = workspace.ensure_tab_loaded(
            "ai_industry_chain",
            reason=TabLoadReason.RESTORE_LAST_TAB.value,
        )
        assert workspace.tabs.currentWidget() is ai_tab
        assert ai_tab.guard_load_reasons == [TabLoadReason.RESTORE_LAST_TAB.value]

        ai_tab.guard_load_reasons.clear()
        workspace._background_prewarm_active_key = "na_daily"
        na_tab = workspace.ensure_tab_loaded(
            "na_daily",
            reason=TabLoadReason.BACKGROUND_PREWARM.value,
        )

        assert na_tab is workspace.get_loaded_tab("na_daily")
        # 北美表在隐藏 staging host 预载，不再通过 live QTabWidget 替换占位页，
        # 因而不会给当前 AI 页制造额外的 preload-repaint guard。
        assert ai_tab.guard_load_reasons == []
        assert workspace._spec_for_key_or_index("na_daily")["mounted"] is False
        assert na_tab.parentWidget() is workspace._background_preload_staging_host
        assert workspace.tabs.currentWidget() is ai_tab
        assert workspace.tabs.count() == len(TAB_DEFINITIONS) == 11
    finally:
        workspace.shutdown()
        workspace.deleteLater()


def test_background_prewarm_placeholder_replacement_does_not_arm_current_watchlist_reveal_guard(
    qt_application,
):
    events: list[tuple[str, str]] = []
    workspace = ClassicWorkspace(
        data_provider=object(),
        engine=object(),
        background_prewarm=False,
        watchlist_startup_tasks=False,
    )
    _install_controlled_factories(workspace, events)
    watchlist_spec = workspace._spec_for_key_or_index("watchlist")
    watchlist_spec["factory"] = lambda **_kwargs: _ViewportBackgroundPreloadTab(
        "watchlist", events, workspace
    )

    try:
        watchlist_index = workspace._tab_index_for_key("watchlist")
        blocked = workspace.tabs.blockSignals(True)
        workspace.tabs.setCurrentIndex(watchlist_index)
        workspace.tabs.blockSignals(blocked)
        watchlist = workspace.ensure_tab_loaded(
            "watchlist",
            reason=TabLoadReason.USER.value,
        )
        assert workspace.tabs.currentWidget() is watchlist
        assert watchlist.guard_load_reasons == []

        workspace.ensure_tab_loaded(
            "na_daily",
            reason=TabLoadReason.BACKGROUND_PREWARM.value,
        )

        assert watchlist.guard_load_reasons == []
    finally:
        workspace.shutdown()
        workspace.deleteLater()


@pytest.mark.parametrize("target_key", widget_prewarm_tab_keys())
def test_transition_tables_stay_staged_during_background_prewarm_until_activation(
    qt_application,
    target_key,
):
    class _TransitionTable(_ControlledPreloadTab):
        def __init__(self, key, events, parent=None):
            super().__init__(key, events, parent)
            self.viewport_background_sync_calls = 0
            self.preload_reveal_calls = 0
            self.guard_load_reasons = []

        def prepare_workspace_preload_reveal(self) -> None:
            self.preload_reveal_calls += 1

        def prepare_workspace_preload_repaint_guard(self, *, load_reason: str) -> None:
            self.guard_load_reasons.append(load_reason)

        def sync_workspace_viewport_background(self) -> None:
            self.viewport_background_sync_calls += 1

    events: list[tuple[str, str]] = []
    workspace = ClassicWorkspace(
        data_provider=object(),
        engine=object(),
        background_prewarm=False,
        watchlist_startup_tasks=False,
    )
    _install_controlled_factories(workspace, events)

    try:
        target_index = workspace._tab_index_for_key(target_key)
        placeholder = workspace.tabs.widget(target_index)
        workspace._background_prewarm_active_key = target_key
        target_spec = workspace._spec_for_key_or_index(target_key)
        target_spec["factory"] = lambda **runtime_kwargs: _TransitionTable(
            target_key,
            events,
            runtime_kwargs.get("_workspace_parent_override", workspace),
        )

        target = workspace.ensure_tab_loaded(
            target_key,
            reason=TabLoadReason.BACKGROUND_PREWARM.value,
        )

        assert target is target_spec["widget"]
        assert target_spec["loaded"] is True
        assert target_spec["mounted"] is False
        assert workspace.tabs.widget(target_index) is placeholder
        assert target.parentWidget() is workspace._background_preload_staging_host
        assert workspace.tabs.count() == len(TAB_DEFINITIONS) == 11
        assert target.preload_reveal_calls == 0
        assert target.guard_load_reasons == []

        assert workspace.activate_tab(target_index, reason=TabLoadReason.SHELL_NAV.value) is True

        assert target_spec["mounted"] is True
        assert workspace.tabs.currentWidget() is target
        assert workspace.tabs.indexOf(target) == target_index
        assert target.parentWidget() is not workspace._background_preload_staging_host
        assert target.viewport_background_sync_calls == 1
        # A hidden preload does not paint the live table.  The intentional
        # first mount arms exactly one required preload_reveal frame, and a
        # repeated activation must not manufacture another structural reveal.
        assert target.preload_reveal_calls == 1
        assert target.guard_load_reasons == [TabLoadReason.BACKGROUND_PREWARM.value]
        assert workspace.activate_tab(target_index, reason=TabLoadReason.SHELL_NAV.value) is True
        assert target.preload_reveal_calls == 1
        assert target.guard_load_reasons == [TabLoadReason.BACKGROUND_PREWARM.value]
        assert workspace.tabs.count() == len(TAB_DEFINITIONS) == 11
    finally:
        workspace.shutdown()
        workspace.deleteLater()


def test_stock_candidate_background_staging_host_resolves_workspace_context(qt_application):
    workspace = ClassicWorkspace(
        data_provider=object(),
        engine=object(),
        background_prewarm=False,
        watchlist_startup_tasks=False,
    )
    try:
        staging_host = classic_workspace_module._ensure_background_preload_staging_host(workspace)
        staged_owner = SimpleNamespace(parent=lambda: staging_host)

        assert StockCandidateTab._workspace(staged_owner) is workspace
    finally:
        workspace.shutdown()
        workspace.deleteLater()


@pytest.mark.parametrize("target_key", ("asian_market", "na_daily"))
def test_active_transition_table_keeps_placeholder_until_background_step_is_ready(
    qt_application,
    target_key,
):
    """点击正在隐藏预载的交易表时，不能把未完成的表提前揭示到可见页。"""

    events: list[tuple[str, str]] = []
    workspace = ClassicWorkspace(
        data_provider=object(),
        engine=object(),
        background_prewarm=False,
        watchlist_startup_tasks=False,
    )
    _install_controlled_factories(workspace, events)

    try:
        workspace._initial_real_tab_activated = True
        workspace._background_prewarm_enabled = True
        workspace._background_preload_coordinator.start()
        _stop_preload_timer(workspace)
        workspace._background_prewarm_queue[:] = [target_key]

        target_index = workspace._tab_index_for_key(target_key)
        placeholder = workspace.tabs.widget(target_index)
        workspace._prewarm_next_tab()
        _stop_preload_timer(workspace)

        target_spec = workspace._spec_for_key_or_index(target_key)
        target = target_spec["widget"]
        assert workspace._background_prewarm_active_key == target_key
        assert target_spec["loaded"] is True
        assert target_spec["mounted"] is False
        assert target.parentWidget() is workspace._background_preload_staging_host

        assert workspace.activate_tab(target_index, reason=TabLoadReason.SHELL_NAV.value) is True
        _stop_preload_timer(workspace)

        deferred_status = workspace.background_preload_status()
        assert workspace.tabs.currentWidget() is placeholder
        assert target_spec["mounted"] is False
        assert target.parentWidget() is workspace._background_preload_staging_host
        assert target_key in deferred_status["pending_priority_keys"]
        assert workspace.tabs.count() == len(TAB_DEFINITIONS) == 11

        target.ready = True
        workspace._prewarm_next_tab()
        _stop_preload_timer(workspace)

        assert workspace._background_prewarm_active_key == ""
        assert workspace.tabs.currentWidget() is target
        assert target_spec["mounted"] is True
        assert target.parentWidget() is not workspace._background_preload_staging_host
        assert workspace.tabs.count() == len(TAB_DEFINITIONS) == 11
    finally:
        workspace.shutdown()
        workspace.deleteLater()


def test_visible_watchlist_prime_failure_keeps_hidden_staging_queue_intact(
    qt_application,
):
    events: list[tuple[str, str]] = []
    workspace = ClassicWorkspace(
        data_provider=object(),
        engine=object(),
        background_prewarm=True,
        watchlist_startup_tasks=False,
    )
    _install_controlled_factories(workspace, events)
    watchlist_spec = workspace._spec_for_key_or_index("watchlist")
    watchlist_spec["factory"] = lambda **_kwargs: _FailingPrimeTab(
        "watchlist", events, workspace
    )
    workspace.resize(960, 640)
    workspace.show()
    qt_application.processEvents()

    try:
        watchlist_index = workspace._tab_index_for_key("watchlist")
        placeholder = workspace.tabs.widget(watchlist_index)
        assert workspace.activate_tab(watchlist_index, reason=TabLoadReason.USER.value) is True
        _stop_preload_timer(workspace)

        workspace._prewarm_next_tab()
        _stop_preload_timer(workspace)

        status = workspace.background_preload_status()
        assert status["finished"] is False
        assert status["completion_scope"] == "all_planned"
        assert status["visible_watchlist_state"] == "terminal_failed"
        assert status["visible_watchlist_detail"] == "startup prime failed"
        assert status["start_order"] == ["watchlist"]
        assert status["completion_order"] == ["watchlist"]
        assert status["failures"] == {"watchlist": "startup prime failed"}
        assert status["startup_lazy_handoff_keys"] == []
        assert status["remaining_keys"] == list(startup_tab_keys())[1:]
        assert workspace.tabs.count() == len(TAB_DEFINITIONS) == 11
        assert workspace.tabs.currentWidget() is workspace.get_loaded_tab("watchlist")
        assert workspace.tabs.currentWidget() is not placeholder
        assert all(
            ("construct", key) not in events for key in list(startup_tab_keys())[1:]
        )
    finally:
        workspace.shutdown()
        workspace.deleteLater()


def test_current_watchlist_placeholder_mounts_when_background_step_has_no_priority(
    qt_application,
):
    events: list[tuple[str, str]] = []
    workspace = ClassicWorkspace(
        data_provider=object(),
        engine=object(),
        background_prewarm=False,
        watchlist_startup_tasks=False,
    )
    _install_controlled_factories(workspace, events)
    workspace.resize(960, 640)
    workspace.show()
    qt_application.processEvents()

    try:
        watchlist_index = workspace._tab_index_for_key("watchlist")
        placeholder = workspace.tabs.widget(watchlist_index)
        blocked = workspace.tabs.blockSignals(True)
        workspace.tabs.setCurrentIndex(watchlist_index)
        workspace.tabs.blockSignals(blocked)
        workspace._initial_real_tab_activated = True
        workspace._background_prewarm_enabled = True
        workspace._background_preload_coordinator.start()
        _stop_preload_timer(workspace)

        workspace._prewarm_next_tab()
        _stop_preload_timer(workspace)
        assert workspace.background_preload_status()["pending_priority_keys"] == []
        staged = workspace.get_loaded_tab("watchlist")
        assert staged is workspace._background_prewarm_active_widget
        assert workspace.tabs.currentWidget() is placeholder

        staged.ready = True
        workspace._prewarm_next_tab()
        _stop_preload_timer(workspace)

        status = workspace.background_preload_status()
        assert status["finished"] is False
        assert status["completion_scope"] == "all_planned"
        assert status["visible_watchlist_state"] == "ready"
        assert status["remaining_keys"] == list(startup_tab_keys())[1:]
        assert workspace.tabs.currentWidget() is staged
        assert workspace._spec_for_key_or_index("watchlist")["mounted"] is True
        assert workspace.tabs.count() == len(TAB_DEFINITIONS) == 11
    finally:
        workspace.shutdown()
        workspace.deleteLater()


def test_staged_watchlist_mount_syncs_viewport_background_after_placeholder_replace(
    qt_application,
):
    events: list[tuple[str, str]] = []
    workspace = ClassicWorkspace(
        data_provider=object(),
        engine=object(),
        background_prewarm=False,
        watchlist_startup_tasks=False,
    )
    _install_controlled_factories(workspace, events)
    watchlist_spec = workspace._spec_for_key_or_index("watchlist")
    watchlist_spec["factory"] = lambda **_kwargs: _ViewportBackgroundPreloadTab(
        "watchlist", events, workspace
    )
    workspace.resize(960, 640)
    workspace.show()
    qt_application.processEvents()

    try:
        watchlist_index = workspace._tab_index_for_key("watchlist")
        placeholder = workspace.tabs.widget(watchlist_index)
        blocked = workspace.tabs.blockSignals(True)
        workspace.tabs.setCurrentIndex(watchlist_index)
        workspace.tabs.blockSignals(blocked)
        workspace._initial_real_tab_activated = True
        workspace._background_prewarm_enabled = True
        workspace._background_preload_coordinator.start()
        _stop_preload_timer(workspace)

        workspace._prewarm_next_tab()
        _stop_preload_timer(workspace)
        staged = workspace.get_loaded_tab("watchlist")
        assert isinstance(staged, _ViewportBackgroundPreloadTab)
        assert workspace.tabs.currentWidget() is placeholder
        assert staged.viewport_background_sync_calls == 0
        assert staged.preload_reveal_calls == 0
        # The transient widget tag is cleared while VCP warmup is still running;
        # staged mount must retain its original preload reason separately.
        staged._workspace_load_reason = ""

        staged.ready = True
        workspace._prewarm_next_tab()
        _stop_preload_timer(workspace)

        assert workspace.tabs.count() == len(TAB_DEFINITIONS) == 11
        assert workspace.tabs.currentWidget() is staged
        assert watchlist_spec["mounted"] is True
        assert staged.viewport_background_sync_calls == 1
        assert staged.preload_reveal_calls == 1
        assert staged.guard_load_reasons == [TabLoadReason.BACKGROUND_PREWARM.value]
    finally:
        workspace.shutdown()
        workspace.deleteLater()


def test_direct_watchlist_restore_syncs_viewport_background_after_placeholder_replace(
    qt_application,
):
    events: list[tuple[str, str]] = []
    workspace = ClassicWorkspace(
        data_provider=object(),
        engine=object(),
        background_prewarm=False,
        watchlist_startup_tasks=False,
    )
    _install_controlled_factories(workspace, events)
    watchlist_spec = workspace._spec_for_key_or_index("watchlist")
    watchlist_spec["factory"] = lambda **_kwargs: _ViewportBackgroundPreloadTab(
        "watchlist", events, workspace
    )
    workspace.resize(960, 640)
    workspace.show()
    qt_application.processEvents()

    try:
        watchlist_index = workspace._tab_index_for_key("watchlist")
        placeholder = workspace.tabs.widget(watchlist_index)

        tab = workspace.ensure_tab_loaded(
            "watchlist",
            reason=TabLoadReason.RESTORE_LAST_TAB.value,
        )

        assert isinstance(tab, _ViewportBackgroundPreloadTab)
        assert workspace.tabs.count() == len(TAB_DEFINITIONS) == 11
        assert workspace.tabs.currentWidget() is tab
        assert workspace.tabs.currentWidget() is not placeholder
        assert watchlist_spec["mounted"] is True
        assert tab.viewport_background_sync_calls == 1
        assert tab.preload_reveal_calls == 0
        assert tab.guard_load_reasons == [TabLoadReason.RESTORE_LAST_TAB.value]
    finally:
        workspace.shutdown()
        workspace.deleteLater()


def test_user_watchlist_activation_discards_stale_restore_priority(qt_application):
    events: list[tuple[str, str]] = []
    workspace = ClassicWorkspace(
        data_provider=object(),
        engine=object(),
        background_prewarm=True,
        watchlist_startup_tasks=False,
    )
    _install_controlled_factories(workspace, events)
    workspace.resize(960, 640)
    workspace.show()
    qt_application.processEvents()

    try:
        restored_index = workspace._tab_index_for_key("stock_candidates")
        watchlist_index = workspace._tab_index_for_key("watchlist")
        assert workspace.activate_tab(
            restored_index,
            reason=TabLoadReason.RESTORE_LAST_TAB.value,
        ) is True
        _stop_preload_timer(workspace)
        assert workspace.background_preload_status()["pending_priority_keys"] == [
            "stock_candidates"
        ]

        assert workspace.activate_tab(watchlist_index, reason=TabLoadReason.USER.value) is True
        _stop_preload_timer(workspace)
        assert workspace.background_preload_status()["pending_priority_keys"] == [
            "watchlist"
        ]

        workspace._prewarm_next_tab()
        _stop_preload_timer(workspace)
        assert workspace._background_prewarm_active_key == "watchlist"
        workspace._background_prewarm_active_widget.ready = True
        workspace._prewarm_next_tab()
        _stop_preload_timer(workspace)

        status = workspace.background_preload_status()
        assert status["finished"] is False
        assert status["completion_scope"] == "all_planned"
        assert status["pending_priority_keys"] == []
        assert status["startup_lazy_handoff_keys"] == []
        assert status["remaining_keys"] == list(startup_tab_keys())[1:]
        assert all(
            ("construct", key) not in events for key in list(startup_tab_keys())[1:]
        )
    finally:
        workspace.shutdown()
        workspace.deleteLater()


def test_user_activation_cancels_queued_workspace_restore_timer(qt_application):
    workspace = ClassicWorkspace(
        data_provider=object(),
        engine=object(),
        background_prewarm=True,
        watchlist_startup_tasks=False,
    )
    workspace.resize(960, 640)
    workspace.show()
    qt_application.processEvents()

    try:
        watchlist_index = workspace._tab_index_for_key("watchlist")
        target_index = workspace._tab_index_for_key("stock_candidates")
        other_index = workspace._tab_index_for_key("system_log")
        blocked = workspace.tabs.blockSignals(True)
        workspace.tabs.setCurrentIndex(other_index)
        workspace.tabs.blockSignals(blocked)
        workspace.schedule_restore_last_tab("stock_candidates", delay_ms=60_000)
        restore_timer = workspace._restore_last_tab_timer
        assert restore_timer is not None and restore_timer.isActive()

        workspace.tabs.setCurrentIndex(watchlist_index)
        assert workspace._restore_last_tab_timer is None
        assert restore_timer.isActive() is False
        assert workspace.tabs.currentIndex() == watchlist_index

        restore_timer.timeout.emit()
        assert workspace.tabs.currentIndex() == watchlist_index
        assert "stock_candidates" not in workspace.background_preload_status()[
            "pending_priority_keys"
        ]
        assert target_index != watchlist_index
    finally:
        workspace.shutdown()
        workspace.deleteLater()


def test_watchlist_return_demotes_restore_priority_without_clearing_hidden_staging(
    qt_application,
):
    events: list[tuple[str, str]] = []
    workspace = ClassicWorkspace(
        data_provider=object(),
        engine=object(),
        background_prewarm=True,
        watchlist_startup_tasks=False,
    )
    _install_controlled_factories(workspace, events)
    workspace.resize(960, 640)
    workspace.show()
    qt_application.processEvents()

    try:
        watchlist_index = workspace._tab_index_for_key("watchlist")
        assert workspace.activate_tab(watchlist_index, reason=TabLoadReason.USER.value) is True
        _stop_preload_timer(workspace)
        workspace._prewarm_next_tab()
        _stop_preload_timer(workspace)
        workspace._background_prewarm_active_widget.ready = True
        workspace._prewarm_next_tab()
        _stop_preload_timer(workspace)
        assert workspace.background_preload_status()["completion_scope"] == "all_planned"

        target_index = workspace._tab_index_for_key("stock_candidates")
        assert workspace.activate_tab(
            target_index,
            reason=TabLoadReason.RESTORE_LAST_TAB.value,
        ) is True
        _stop_preload_timer(workspace)
        resumed = workspace.background_preload_status()
        assert resumed["finished"] is False
        assert resumed["completion_scope"] == "all_planned"
        assert resumed["pending_priority_keys"] == ["stock_candidates"]

        assert workspace.activate_tab(watchlist_index, reason=TabLoadReason.USER.value) is True
        status = workspace.background_preload_status()
        assert status["finished"] is False
        assert status["completion_scope"] == "all_planned"
        assert status["pending_priority_keys"] == []
        assert status["interactive_handoff_targets"] == []
        assert status["watchlist_resume_pause_requested"] is False
        assert status["startup_lazy_handoff_keys"] == []
        assert status["remaining_keys"] == list(startup_tab_keys())[1:]
        assert all(
            ("construct", key) not in events for key in list(startup_tab_keys())[1:]
        )
    finally:
        workspace.shutdown()
        workspace.deleteLater()


def test_shell_nav_return_demotes_cold_target_before_hidden_construction(
    qt_application,
):
    """A rapid shell-nav return must win before a cold target constructs."""
    events: list[tuple[str, str]] = []
    workspace = ClassicWorkspace(
        data_provider=object(),
        engine=object(),
        background_prewarm=True,
        watchlist_startup_tasks=False,
    )
    _install_controlled_factories(workspace, events)
    workspace.resize(960, 640)
    workspace.show()
    qt_application.processEvents()

    try:
        watchlist_index = workspace._tab_index_for_key("watchlist")
        assert workspace.activate_tab(watchlist_index, reason=TabLoadReason.USER.value) is True
        _stop_preload_timer(workspace)
        workspace._prewarm_next_tab()
        _stop_preload_timer(workspace)
        workspace._background_prewarm_active_widget.ready = True
        workspace._prewarm_next_tab()
        _stop_preload_timer(workspace)
        assert workspace.background_preload_status()["completion_scope"] == "all_planned"

        workspace.prepare_shell_group_rebuild_navigation(interval_ms=5_000)
        scan_index = workspace._tab_index_for_key("scan")
        assert workspace.activate_tab(scan_index, reason=TabLoadReason.SHELL_NAV.value) is True
        qt_application.processEvents()

        # The 120ms shell-group quiet window must keep the zero-delay preload
        # callback from entering QWidget construction before a quick return.
        assert ("construct", "scan") not in events
        assert workspace._background_prewarm_active_key == ""
        assert workspace.background_preload_status()["timer_active"] is True

        assert workspace.activate_tab(watchlist_index, reason=TabLoadReason.SHELL_NAV.value) is True
        status = workspace.background_preload_status()
        assert status["finished"] is False
        assert status["completion_scope"] == "all_planned"
        assert status["pending_priority_keys"] == []
        assert "scan" in status["remaining_keys"]
        assert ("construct", "scan") not in events
    finally:
        workspace.shutdown()
        workspace.deleteLater()


def test_watchlist_return_requeues_active_priority_and_keeps_full_queue_held(
    qt_application,
):
    events: list[tuple[str, str]] = []
    workspace = ClassicWorkspace(
        data_provider=object(),
        engine=object(),
        background_prewarm=True,
        watchlist_startup_tasks=False,
    )
    _install_controlled_factories(workspace, events)
    watchlist_spec = workspace._spec_for_key_or_index("watchlist")
    watchlist_spec["factory"] = lambda **_kwargs: _WatchlistForegroundHoldPreloadTab(
        "watchlist",
        events,
        workspace,
    )
    workspace.resize(960, 640)
    workspace.show()
    qt_application.processEvents()

    try:
        watchlist_index = workspace._tab_index_for_key("watchlist")
        assert workspace.activate_tab(watchlist_index, reason=TabLoadReason.USER.value) is True
        _stop_preload_timer(workspace)
        workspace._prewarm_next_tab()
        _stop_preload_timer(workspace)
        workspace._background_prewarm_active_widget.ready = True
        workspace._prewarm_next_tab()
        _stop_preload_timer(workspace)

        target_index = workspace._tab_index_for_key("stock_candidates")
        assert workspace.activate_tab(target_index, reason=TabLoadReason.USER.value) is True
        _stop_preload_timer(workspace)
        workspace._prewarm_next_tab()
        _stop_preload_timer(workspace)
        assert workspace._background_prewarm_active_key == "ai_industry_chain"

        assert workspace.activate_tab(watchlist_index, reason=TabLoadReason.USER.value) is True
        paused = workspace.background_preload_status()
        assert paused["finished"] is False
        assert paused["completion_scope"] == "all_planned"
        assert paused["watchlist_resume_pause_requested"] is False
        assert paused["pending_priority_keys"] == []
        assert paused["interactive_handoff_targets"] == []

        workspace._prewarm_next_tab()
        _stop_preload_timer(workspace)

        status = workspace.background_preload_status()
        assert status["finished"] is False
        assert status["completion_scope"] == "all_planned"
        assert status["watchlist_resume_pause_requested"] is False
        assert status["active_key"] == ""
        assert status["foreground_hold_reason"] == "watchlist_active"
        assert "ai_industry_chain" in status["remaining_keys"]
        assert "ai_industry_chain" not in status["completion_order"]
        assert "ai_industry_chain" not in status["failures"]
        assert "stock_candidates" in status["remaining_keys"]
        assert status["startup_lazy_handoff_keys"] == []
        assert events.count(("construct", "ai_industry_chain")) == 1
        assert events.count(("construct", "stock_candidates")) == 0
    finally:
        workspace.shutdown()
        workspace.deleteLater()


def test_watchlist_return_keeps_noncurrent_priority_tail_hidden_until_quiet_queue_resumes(
    qt_application,
):
    events: list[tuple[str, str]] = []
    workspace = ClassicWorkspace(
        data_provider=object(),
        engine=object(),
        background_prewarm=True,
        watchlist_startup_tasks=False,
    )
    _install_controlled_factories(workspace, events)
    workspace.resize(960, 640)
    workspace.show()
    qt_application.processEvents()

    try:
        watchlist_index = workspace._tab_index_for_key("watchlist")
        foreign_index = workspace._tab_index_for_key("foreign_block")
        assert workspace.activate_tab(watchlist_index, reason=TabLoadReason.USER.value) is True
        _stop_preload_timer(workspace)
        workspace._prewarm_next_tab()
        _stop_preload_timer(workspace)
        watchlist = workspace._background_prewarm_active_widget
        assert workspace.activate_tab(
            foreign_index,
            reason=TabLoadReason.RESTORE_LAST_TAB.value,
        ) is True
        _stop_preload_timer(workspace)

        watchlist.ready = True
        workspace._prewarm_next_tab()
        _stop_preload_timer(workspace)
        assert workspace.background_preload_status()["completion_scope"] == "all_planned"
        workspace._prewarm_next_tab()
        _stop_preload_timer(workspace)
        assert workspace._background_prewarm_active_key == "ai_industry_chain"
        active = workspace._background_prewarm_active_widget

        assert workspace.activate_tab(watchlist_index, reason=TabLoadReason.USER.value) is True
        paused = workspace.background_preload_status()
        assert workspace.tabs.currentWidget() is watchlist
        assert paused["watchlist_resume_pause_requested"] is False
        assert paused["pending_priority_keys"] == []

        active.ready = True
        workspace._prewarm_next_tab()
        _stop_preload_timer(workspace)

        status = workspace.background_preload_status()
        assert status["finished"] is False
        assert status["completion_scope"] == "all_planned"
        assert status["watchlist_resume_pause_requested"] is False
        assert "foreign_block" in status["remaining_keys"]
        assert status["startup_lazy_handoff_keys"] == []
        assert events.count(("construct", "ai_industry_chain")) == 1
        assert events.count(("construct", "foreign_block")) == 0
    finally:
        workspace.shutdown()
        workspace.deleteLater()


def test_visible_watchlist_pauses_an_already_active_cooperative_background_step(
    qt_application,
):
    """Returning to Watchlist must stop an in-flight hidden tab before its next GUI slice."""
    events: list[tuple[str, str]] = []
    workspace = ClassicWorkspace(
        data_provider=object(),
        engine=object(),
        background_prewarm=True,
        watchlist_startup_tasks=False,
    )
    _install_controlled_factories(workspace, events)
    watchlist_spec = workspace._spec_for_key_or_index("watchlist")
    watchlist_spec["factory"] = lambda **_kwargs: _WatchlistForegroundHoldPreloadTab(
        "watchlist",
        events,
        workspace,
    )
    ai_spec = workspace._spec_for_key_or_index("ai_industry_chain")
    ai_spec["factory"] = lambda **_kwargs: _PausablePreloadTab(
        "ai_industry_chain",
        events,
        workspace,
    )
    workspace.resize(960, 640)
    workspace.show()
    qt_application.processEvents()

    try:
        watchlist_index = workspace._tab_index_for_key("watchlist")
        foreign_index = workspace._tab_index_for_key("foreign_block")
        system_log_index = workspace._tab_index_for_key("system_log")
        assert workspace.activate_tab(watchlist_index, reason=TabLoadReason.USER.value) is True
        _stop_preload_timer(workspace)
        workspace._prewarm_next_tab()
        _stop_preload_timer(workspace)
        workspace._background_prewarm_active_widget.ready = True
        workspace._prewarm_next_tab()
        _stop_preload_timer(workspace)

        assert workspace.activate_tab(foreign_index, reason=TabLoadReason.USER.value) is True
        _stop_preload_timer(workspace)
        workspace._prewarm_next_tab()
        _stop_preload_timer(workspace)
        active = workspace._background_prewarm_active_widget
        assert isinstance(active, _PausablePreloadTab)
        assert workspace._background_prewarm_active_key == "ai_industry_chain"

        assert workspace.activate_tab(watchlist_index, reason=TabLoadReason.USER.value) is True
        status = workspace.background_preload_status()
        assert active.paused is True
        assert status["foreground_hold_reason"] == "watchlist_active"
        assert status["foreground_hold_active_key"] == "ai_industry_chain"
        assert status["foreground_hold_mode"] == "paused"

        active.ready = True
        workspace._prewarm_next_tab()
        _stop_preload_timer(workspace)
        assert workspace._background_prewarm_active_key == "ai_industry_chain"
        assert "ai_industry_chain" not in workspace._background_prewarm_completion_order

        assert workspace.activate_tab(system_log_index, reason=TabLoadReason.USER.value) is True
        _stop_preload_timer(workspace)
        assert active.paused is False
        assert active.resume_calls == 1
        workspace._prewarm_next_tab()
        _stop_preload_timer(workspace)
        assert "ai_industry_chain" in workspace._background_prewarm_completion_order
    finally:
        workspace.shutdown()
        workspace.deleteLater()


def test_visible_watchlist_cancels_and_requeues_nonpausable_active_background_step(
    qt_application,
):
    """A non-cooperative hidden tab must be settled and retried, never marked terminal."""
    events: list[tuple[str, str]] = []
    workspace = ClassicWorkspace(
        data_provider=object(),
        engine=object(),
        background_prewarm=True,
        watchlist_startup_tasks=False,
    )
    _install_controlled_factories(workspace, events)
    watchlist_spec = workspace._spec_for_key_or_index("watchlist")
    watchlist_spec["factory"] = lambda **_kwargs: _WatchlistForegroundHoldPreloadTab(
        "watchlist",
        events,
        workspace,
    )
    ai_spec = workspace._spec_for_key_or_index("ai_industry_chain")
    ai_spec["factory"] = lambda **_kwargs: _ImmediateCancellationPreloadTab(
        "ai_industry_chain",
        events,
        workspace,
    )
    workspace.resize(960, 640)
    workspace.show()
    qt_application.processEvents()

    try:
        watchlist_index = workspace._tab_index_for_key("watchlist")
        foreign_index = workspace._tab_index_for_key("foreign_block")
        assert workspace.activate_tab(watchlist_index, reason=TabLoadReason.USER.value) is True
        _stop_preload_timer(workspace)
        workspace._prewarm_next_tab()
        _stop_preload_timer(workspace)
        workspace._background_prewarm_active_widget.ready = True
        workspace._prewarm_next_tab()
        _stop_preload_timer(workspace)

        assert workspace.activate_tab(foreign_index, reason=TabLoadReason.USER.value) is True
        _stop_preload_timer(workspace)
        workspace._prewarm_next_tab()
        _stop_preload_timer(workspace)
        active = workspace._background_prewarm_active_widget
        assert isinstance(active, _ImmediateCancellationPreloadTab)

        assert workspace.activate_tab(watchlist_index, reason=TabLoadReason.USER.value) is True
        _stop_preload_timer(workspace)
        status = workspace.background_preload_status()
        assert active.cancel_reasons == ["watchlist_visible"]
        assert status["active_key"] == ""
        assert status["remaining_keys"].count("ai_industry_chain") == 1
        assert "ai_industry_chain" not in status["completion_order"]
        assert "ai_industry_chain" not in status["failures"]

        workspace._prewarm_next_tab()
        _stop_preload_timer(workspace)
        assert workspace._background_prewarm_active_key == ""
    finally:
        workspace.shutdown()
        workspace.deleteLater()


def test_visible_watchlist_waits_for_active_cancellation_before_requeueing(qt_application):
    """An unsettled cancellation remains fail-closed and cannot launch the next hidden tab."""
    events: list[tuple[str, str]] = []
    workspace = ClassicWorkspace(
        data_provider=object(),
        engine=object(),
        background_prewarm=True,
        watchlist_startup_tasks=False,
    )
    _install_controlled_factories(workspace, events)
    watchlist_spec = workspace._spec_for_key_or_index("watchlist")
    watchlist_spec["factory"] = lambda **_kwargs: _WatchlistForegroundHoldPreloadTab(
        "watchlist",
        events,
        workspace,
    )
    ai_spec = workspace._spec_for_key_or_index("ai_industry_chain")
    ai_spec["factory"] = lambda **_kwargs: _CancellablePreloadTab(
        "ai_industry_chain",
        events,
        workspace,
    )
    workspace.resize(960, 640)
    workspace.show()
    qt_application.processEvents()

    try:
        watchlist_index = workspace._tab_index_for_key("watchlist")
        foreign_index = workspace._tab_index_for_key("foreign_block")
        assert workspace.activate_tab(watchlist_index, reason=TabLoadReason.USER.value) is True
        _stop_preload_timer(workspace)
        workspace._prewarm_next_tab()
        _stop_preload_timer(workspace)
        workspace._background_prewarm_active_widget.ready = True
        workspace._prewarm_next_tab()
        _stop_preload_timer(workspace)

        assert workspace.activate_tab(foreign_index, reason=TabLoadReason.USER.value) is True
        _stop_preload_timer(workspace)
        workspace._prewarm_next_tab()
        _stop_preload_timer(workspace)
        active = workspace._background_prewarm_active_widget
        assert isinstance(active, _CancellablePreloadTab)

        assert workspace.activate_tab(watchlist_index, reason=TabLoadReason.USER.value) is True
        cancelling = workspace.background_preload_status()
        assert active.cancel_reasons == ["watchlist_visible"]
        assert cancelling["active_key"] == "ai_industry_chain"
        assert cancelling["foreground_hold_mode"] == "cancelling"
        assert "ai_industry_chain" not in cancelling["completion_order"]
        assert "ai_industry_chain" not in cancelling["failures"]

        workspace._prewarm_next_tab()
        _stop_preload_timer(workspace)
        assert workspace._background_prewarm_active_key == "ai_industry_chain"
        assert events.count(("construct", "system_log")) == 0

        active.cancellation_receipt.settled = True
        workspace._prewarm_next_tab()
        _stop_preload_timer(workspace)
        settled = workspace.background_preload_status()
        assert settled["active_key"] == ""
        assert settled["remaining_keys"].count("ai_industry_chain") == 1
        assert "ai_industry_chain" not in settled["completion_order"]
        assert "ai_industry_chain" not in settled["failures"]

        workspace._prewarm_next_tab()
        _stop_preload_timer(workspace)
        assert workspace._background_prewarm_active_key == ""
        assert events.count(("construct", "system_log")) == 0
    finally:
        workspace.shutdown()
        workspace.deleteLater()


def test_visible_watchlist_holds_the_last_active_step_even_when_queue_is_empty(qt_application):
    """The foreground guarantee covers a popped final item, not only queued work."""
    events: list[tuple[str, str]] = []
    workspace = ClassicWorkspace(
        data_provider=object(),
        engine=object(),
        background_prewarm=False,
        watchlist_startup_tasks=False,
    )
    _install_controlled_factories(workspace, events)
    watchlist_spec = workspace._spec_for_key_or_index("watchlist")
    watchlist_spec["factory"] = lambda **_kwargs: _WatchlistForegroundHoldPreloadTab(
        "watchlist",
        events,
        workspace,
    )
    workspace.resize(960, 640)
    workspace.show()
    qt_application.processEvents()

    try:
        watchlist_index = workspace._tab_index_for_key("watchlist")
        watchlist = workspace.ensure_tab_loaded("watchlist", reason=TabLoadReason.USER.value)
        assert isinstance(watchlist, _WatchlistForegroundHoldPreloadTab)
        watchlist._workspace_background_preload_ready = True
        assert workspace.activate_tab(watchlist_index, reason=TabLoadReason.USER.value) is True
        active = _PausablePreloadTab("ai_industry_chain", events, workspace)
        workspace._background_prewarm_enabled = True
        workspace._background_prewarm_started = True
        workspace._background_prewarm_finished = False
        workspace._background_prewarm_queue = []
        workspace._background_prewarm_active_key = "ai_industry_chain"
        workspace._background_prewarm_active_widget = active
        workspace._background_prewarm_active_started_at = time.perf_counter()
        workspace._background_preload_coordinator._active_step_count = 1

        assert workspace.activate_tab(watchlist_index, reason=TabLoadReason.USER.value) is True
        active.ready = True
        workspace._prewarm_next_tab()
        _stop_preload_timer(workspace)

        status = workspace.background_preload_status()
        assert active.paused is True
        assert status["foreground_hold_reason"] == "watchlist_active"
        assert status["active_key"] == "ai_industry_chain"
        assert status["completion_order"] == []
    finally:
        workspace.shutdown()
        workspace.deleteLater()


def test_interactive_priority_uses_latest_requested_target_without_ending_full_staging(qt_application):
    events: list[tuple[str, str]] = []
    workspace = ClassicWorkspace(
        data_provider=object(),
        engine=object(),
        background_prewarm=True,
        watchlist_startup_tasks=False,
    )
    _install_controlled_factories(workspace, events)
    workspace.resize(960, 640)
    workspace.show()
    qt_application.processEvents()

    try:
        watchlist_index = workspace._tab_index_for_key("watchlist")
        assert workspace.activate_tab(watchlist_index, reason=TabLoadReason.USER.value) is True
        _stop_preload_timer(workspace)
        workspace._prewarm_next_tab()
        _stop_preload_timer(workspace)
        workspace._background_prewarm_active_widget.ready = True
        workspace._prewarm_next_tab()
        _stop_preload_timer(workspace)

        first_key = "system_log"
        latest_key = "ai_industry_chain"
        assert workspace.activate_tab(
            workspace._tab_index_for_key(first_key),
            reason=TabLoadReason.USER.value,
        ) is True
        _stop_preload_timer(workspace)
        assert workspace.activate_tab(
            workspace._tab_index_for_key(latest_key),
            reason=TabLoadReason.USER.value,
        ) is True
        _stop_preload_timer(workspace)

        resumed = workspace.background_preload_status()
        assert resumed["pending_priority_keys"] == [latest_key]
        assert resumed["interactive_handoff_targets"] == []
        assert workspace._background_prewarm_queue[0] == latest_key

        workspace._prewarm_next_tab()
        _stop_preload_timer(workspace)
        assert workspace._background_prewarm_active_key == latest_key
        workspace._background_prewarm_active_widget.ready = True
        workspace._prewarm_next_tab()
        _stop_preload_timer(workspace)

        status = workspace.background_preload_status()
        assert status["finished"] is False
        assert status["completion_scope"] == "all_planned"
        assert first_key in status["remaining_keys"]
        assert status["startup_lazy_handoff_keys"] == []
        assert events.count(("construct", first_key)) == 0
        assert events.count(("construct", latest_key)) == 1
    finally:
        workspace.shutdown()
        workspace.deleteLater()


def test_latest_target_replaces_demoted_priority_while_previous_step_settles(qt_application):
    events: list[tuple[str, str]] = []
    workspace = ClassicWorkspace(
        data_provider=object(),
        engine=object(),
        background_prewarm=True,
        watchlist_startup_tasks=False,
    )
    _install_controlled_factories(workspace, events)
    workspace.resize(960, 640)
    workspace.show()
    qt_application.processEvents()

    try:
        watchlist_index = workspace._tab_index_for_key("watchlist")
        assert workspace.activate_tab(watchlist_index, reason=TabLoadReason.USER.value) is True
        _stop_preload_timer(workspace)
        workspace._prewarm_next_tab()
        _stop_preload_timer(workspace)
        workspace._background_prewarm_active_widget.ready = True
        workspace._prewarm_next_tab()
        _stop_preload_timer(workspace)

        first_target = "foreign_block"
        latest_target = "system_log"
        assert workspace.activate_tab(
            workspace._tab_index_for_key(first_target),
            reason=TabLoadReason.USER.value,
        ) is True
        _stop_preload_timer(workspace)
        workspace._prewarm_next_tab()
        _stop_preload_timer(workspace)
        assert workspace._background_prewarm_active_key == "ai_industry_chain"
        active = workspace._background_prewarm_active_widget

        assert workspace.activate_tab(watchlist_index, reason=TabLoadReason.USER.value) is True
        assert workspace.background_preload_status()["watchlist_resume_pause_requested"] is False
        assert workspace.activate_tab(
            workspace._tab_index_for_key(latest_target),
            reason=TabLoadReason.USER.value,
        ) is True
        _stop_preload_timer(workspace)
        resumed = workspace.background_preload_status()
        assert resumed["watchlist_resume_pause_requested"] is False
        assert resumed["pending_priority_keys"] == [latest_target]
        assert resumed["interactive_handoff_targets"] == []

        active.ready = True
        workspace._prewarm_next_tab()
        _stop_preload_timer(workspace)
        workspace._prewarm_next_tab()
        _stop_preload_timer(workspace)
        assert workspace._background_prewarm_active_key == latest_target
        workspace._background_prewarm_active_widget.ready = True
        workspace._prewarm_next_tab()
        _stop_preload_timer(workspace)

        status = workspace.background_preload_status()
        assert status["finished"] is False
        assert status["completion_scope"] == "all_planned"
        assert first_target in status["remaining_keys"]
        assert status["startup_lazy_handoff_keys"] == []
        assert events.count(("construct", latest_target)) == 1
        assert events.count(("construct", first_target)) == 0
    finally:
        workspace.shutdown()
        workspace.deleteLater()


def test_interactive_priority_dependency_failure_keeps_full_staging_queue_running(qt_application):
    events: list[tuple[str, str]] = []
    workspace = ClassicWorkspace(
        data_provider=object(),
        engine=object(),
        background_prewarm=True,
        watchlist_startup_tasks=False,
    )
    _install_controlled_factories(workspace, events)
    workspace.resize(960, 640)
    workspace.show()
    qt_application.processEvents()

    try:
        watchlist_index = workspace._tab_index_for_key("watchlist")
        assert workspace.activate_tab(watchlist_index, reason=TabLoadReason.USER.value) is True
        _stop_preload_timer(workspace)
        workspace._prewarm_next_tab()
        _stop_preload_timer(workspace)
        workspace._background_prewarm_active_widget.ready = True
        workspace._prewarm_next_tab()
        _stop_preload_timer(workspace)
        assert workspace.background_preload_status()["finished"] is False
        assert workspace.background_preload_status()["completion_scope"] == "all_planned"

        target_key = "foreign_block"
        target_index = workspace._tab_index_for_key(target_key)
        assert workspace.activate_tab(target_index, reason=TabLoadReason.USER.value) is True
        _stop_preload_timer(workspace)
        assert workspace.background_preload_status()["pending_priority_keys"] == [target_key]

        workspace._prewarm_next_tab()
        _stop_preload_timer(workspace)
        assert workspace._background_prewarm_active_key == "ai_industry_chain"
        ai_widget = workspace._background_prewarm_active_widget
        ai_widget.is_background_preload_complete = lambda: (_ for _ in ()).throw(
            RuntimeError("ai preload failed")
        )
        workspace._prewarm_next_tab()
        _stop_preload_timer(workspace)

        workspace._prewarm_next_tab()
        _stop_preload_timer(workspace)
        status = workspace.background_preload_status()
        assert status["finished"] is False
        assert status["completion_scope"] == "all_planned"
        assert status["interactive_handoff_targets"] == []
        assert status["dependency_failures"][target_key] == (
            "dependencies_not_ready:ai_industry_chain"
        )
        assert target_key not in status["remaining_keys"]
        assert status["startup_lazy_handoff_keys"] == []
        assert status["remaining_keys"]
        assert events.count(("construct", target_key)) == 0
        assert events.count(("construct", "ai_industry_chain")) == 1
    finally:
        workspace.shutdown()
        workspace.deleteLater()


def test_prestart_priority_waits_for_startup_cache_bootstrap_then_resumes(qt_application):
    events: list[tuple[str, str]] = []
    workspace = ClassicWorkspace(
        data_provider=object(),
        engine=object(),
        host=SimpleNamespace(_startup_enabled=True),
        background_prewarm=True,
        watchlist_startup_tasks=False,
    )
    _install_controlled_factories(workspace, events)

    try:
        target_index = workspace._tab_index_for_key("stock_candidates")
        assert workspace.activate_tab(target_index, reason=TabLoadReason.USER.value) is True
        assert workspace._background_prewarm_started is False
        assert workspace.background_preload_status()["pending_priority_keys"] == ["stock_candidates"]

        workspace._on_startup_cache_bootstrap_ready()
        _stop_preload_timer(workspace)
        assert workspace._background_prewarm_started is True
        assert workspace._background_prewarm_queue[0] == "watchlist"
        assert events == []
    finally:
        workspace.shutdown()
        workspace.deleteLater()


def test_stock_candidates_priority_waits_for_complete_dependency_closure(qt_application):
    events: list[tuple[str, str]] = []
    workspace = ClassicWorkspace(
        data_provider=object(),
        engine=object(),
        background_prewarm=False,
        watchlist_startup_tasks=False,
    )
    _install_controlled_factories(workspace, events)
    expected_chain = [
        "watchlist",
        "ai_industry_chain",
        "na_daily",
        "scan",
        "foreign_block",
        "earnings",
        "fund_holdings",
        "lhb",
        "asian_market",
        "stock_candidates",
    ]

    try:
        workspace._initial_real_tab_activated = True
        workspace._background_prewarm_enabled = True
        workspace._start_background_tab_prewarm()
        _stop_preload_timer(workspace)
        workspace._prewarm_next_tab()
        _stop_preload_timer(workspace)

        target_index = workspace._tab_index_for_key("stock_candidates")
        assert workspace.activate_tab(target_index, reason=TabLoadReason.USER.value) is True
        _stop_preload_timer(workspace)
        status = workspace.background_preload_status()
        assert status["priority_closures"]["stock_candidates"] == expected_chain
        assert workspace._background_prewarm_queue[: len(expected_chain) - 1] == expected_chain[1:]
        assert events.count(("construct", "stock_candidates")) == 0

        workspace._background_prewarm_active_widget.ready = True
        workspace._prewarm_next_tab()
        _stop_preload_timer(workspace)
        for expected_key in expected_chain[1:]:
            workspace._prewarm_next_tab()
            _stop_preload_timer(workspace)
            assert workspace._background_prewarm_active_key == expected_key
            if expected_key == "stock_candidates":
                ready_keys = set(workspace.background_preload_status()["ready_keys"])
                assert set(expected_chain[:-1]).issubset(ready_keys)
            workspace._background_prewarm_active_widget.ready = True
            workspace._prewarm_next_tab()
            _stop_preload_timer(workspace)

        assert events.count(("construct", "stock_candidates")) == 1
        assert events.count(("prime", "stock_candidates")) == 1
        assert workspace.background_preload_status()["max_concurrent_steps"] == 1
    finally:
        workspace.shutdown()
        workspace.deleteLater()


def test_failed_upstream_rejects_consumer_hydration_and_queue_continues(qt_application):
    events: list[tuple[str, str]] = []
    workspace = ClassicWorkspace(
        data_provider=object(),
        engine=object(),
        background_prewarm=False,
        watchlist_startup_tasks=False,
    )
    _install_controlled_factories(workspace, events)

    try:
        workspace._initial_real_tab_activated = True
        workspace._background_prewarm_enabled = True
        workspace._start_background_tab_prewarm()
        _stop_preload_timer(workspace)
        workspace._prewarm_next_tab()
        _stop_preload_timer(workspace)
        target_index = workspace._tab_index_for_key("foreign_block")
        assert workspace.activate_tab(target_index, reason=TabLoadReason.USER.value) is True

        workspace._background_prewarm_active_widget.ready = True
        workspace._prewarm_next_tab()
        _stop_preload_timer(workspace)
        workspace._prewarm_next_tab()
        _stop_preload_timer(workspace)
        ai_widget = workspace._background_prewarm_active_widget
        ai_widget.is_background_preload_complete = lambda: (_ for _ in ()).throw(
            RuntimeError("ai preload failed")
        )
        workspace._prewarm_next_tab()
        _stop_preload_timer(workspace)
        assert workspace._background_prewarm_active_key == ""

        workspace._prewarm_next_tab()
        _stop_preload_timer(workspace)
        status = workspace.background_preload_status()
        assert status["dependency_failures"]["foreign_block"] == (
            "dependencies_not_ready:ai_industry_chain"
        )
        assert events.count(("construct", "foreign_block")) == 0
        assert workspace._background_prewarm_active_key == ""

        workspace._prewarm_next_tab()
        _stop_preload_timer(workspace)
        assert workspace._background_prewarm_active_key == "system_log"
        assert workspace.background_preload_status()["max_concurrent_steps"] == 1
    finally:
        workspace.shutdown()
        workspace.deleteLater()


def test_dependency_gate_repairs_out_of_order_queue_before_hydration(qt_application):
    events: list[tuple[str, str]] = []
    workspace = ClassicWorkspace(
        data_provider=object(),
        engine=object(),
        background_prewarm=False,
        watchlist_startup_tasks=False,
    )
    _install_controlled_factories(workspace, events)

    try:
        workspace._initial_real_tab_activated = True
        workspace._background_prewarm_enabled = True
        workspace._start_background_tab_prewarm()
        _stop_preload_timer(workspace)
        queue = workspace._background_prewarm_queue
        queue.remove("foreign_block")
        queue.insert(0, "foreign_block")

        workspace._prewarm_next_tab()
        _stop_preload_timer(workspace)
        assert workspace._background_prewarm_active_key == ""
        assert workspace._background_prewarm_queue[:2] == ["ai_industry_chain", "foreign_block"]
        assert events.count(("construct", "foreign_block")) == 0

        workspace._prewarm_next_tab()
        _stop_preload_timer(workspace)
        assert workspace._background_prewarm_active_key == "ai_industry_chain"
        assert events[:2] == [("construct", "ai_industry_chain"), ("prime", "ai_industry_chain")]
    finally:
        workspace.shutdown()
        workspace.deleteLater()


@pytest.mark.parametrize(
    ("tab_class", "accepted"),
    [
        (_RejectedCancellationTab, False),
        (_RaisingCancellationTab, False),
        (_CancellablePreloadTab, True),
        (_ExplodingSettlementTab, True),
        (_ExplodingStatusTab, False),
    ],
)
def test_unsettled_cancellation_deadline_stops_queue_fail_closed(
    qt_application,
    tab_class,
    accepted,
):
    events: list[tuple[str, str]] = []
    workspace = ClassicWorkspace(
        data_provider=object(),
        engine=object(),
        background_prewarm=False,
        watchlist_startup_tasks=False,
    )
    _install_controlled_factories(workspace, events)
    first_key = startup_tab_keys()[0]
    first_spec = workspace._spec_for_key_or_index(first_key)
    first_spec["factory"] = lambda **_kwargs: tab_class(first_key, events, workspace)

    try:
        workspace._initial_real_tab_activated = True
        workspace._background_prewarm_enabled = True
        workspace._start_background_tab_prewarm()
        _stop_preload_timer(workspace)
        workspace._prewarm_next_tab()
        _stop_preload_timer(workspace)
        workspace._background_prewarm_active_started_at = 0.0
        workspace._prewarm_next_tab()
        _stop_preload_timer(workspace)

        coordinator = workspace._background_preload_coordinator
        coordinator._cancel_settlement_started_at = 0.0
        workspace._prewarm_next_tab()
        _stop_preload_timer(workspace)
        assert workspace.background_preload_status()["cancellation_blocked"] is False
        coordinator._cancel_settlement_started_at = time.perf_counter() - 10.0
        workspace._prewarm_next_tab()

        status = workspace.background_preload_status()
        assert status["cancellation_blocked"] is True
        assert status["blocked_reason"] == "cancellation_timeout"
        assert status["cancellation_timeout_keys"] == [first_key]
        assert status["cancel_receipt"]["accepted"] is accepted
        assert status["completion_order"] == []
        assert status["active_key"] == first_key
        assert status["active_step_count"] == 1
        assert workspace._background_prewarm_timer.isActive() is True
        assert events.count(("construct", startup_tab_keys()[1])) == 0

        workspace._prewarm_next_tab()
        assert workspace._background_prewarm_timer.isActive() is True
        assert events.count(("construct", startup_tab_keys()[1])) == 0
    finally:
        workspace.shutdown()
        workspace.deleteLater()


def test_fail_closed_queue_automatically_resumes_after_physical_settlement(qt_application):
    events: list[tuple[str, str]] = []
    workspace = ClassicWorkspace(
        data_provider=object(),
        engine=object(),
        background_prewarm=False,
        watchlist_startup_tasks=False,
    )
    _install_controlled_factories(workspace, events)
    first_key = startup_tab_keys()[0]
    first_spec = workspace._spec_for_key_or_index(first_key)
    first_spec["factory"] = lambda **_kwargs: _CancellablePreloadTab(first_key, events, workspace)
    workspace.BACKGROUND_PREWARM_CANCEL_BLOCKED_POLL_INTERVAL_MS = 5
    workspace.BACKGROUND_PREWARM_INTERVAL_MS = 5

    try:
        workspace._initial_real_tab_activated = True
        workspace._background_prewarm_enabled = True
        workspace._start_background_tab_prewarm()
        _stop_preload_timer(workspace)
        workspace._prewarm_next_tab()
        _stop_preload_timer(workspace)
        first_widget = workspace._background_prewarm_active_widget
        workspace._background_prewarm_active_started_at = 0.0
        workspace._prewarm_next_tab()
        _stop_preload_timer(workspace)
        coordinator = workspace._background_preload_coordinator
        coordinator._cancel_settlement_started_at = time.perf_counter() - 10.0
        workspace._prewarm_next_tab()
        assert workspace.background_preload_status()["cancellation_blocked"] is True
        assert workspace._background_prewarm_timer.isActive() is True

        assert not _process_events_until(
            qt_application,
            lambda: False,
            timeout_seconds=0.03,
        )
        assert workspace._background_prewarm_active_key == first_key
        assert events.count(("construct", startup_tab_keys()[1])) == 0

        first_widget.cancellation_receipt.settled = True
        assert _process_events_until(
            qt_application,
            lambda: workspace._background_prewarm_active_key == startup_tab_keys()[1],
        )
        assert workspace._background_prewarm_active_key == startup_tab_keys()[1]
        assert workspace.background_preload_status()["max_concurrent_steps"] == 1
    finally:
        workspace.shutdown()
        workspace.deleteLater()


@pytest.mark.parametrize("terminal_status", ["failed", "timeout"])
def test_prioritized_preload_terminal_failure_still_activates_once_and_continues(
    qt_application,
    monkeypatch,
    terminal_status,
):
    events: list[tuple[str, str]] = []
    activations: list[str] = []
    workspace = ClassicWorkspace(
        data_provider=object(),
        engine=object(),
        background_prewarm=False,
        watchlist_startup_tasks=False,
    )
    _install_controlled_factories(workspace, events)
    if terminal_status == "failed":
        scan_spec = workspace._spec_for_key_or_index("scan")
        scan_spec["factory"] = lambda **_kwargs: _FailingPrimeTab("scan", events, workspace)
    monkeypatch.setattr(
        workspace,
        "_notify_tab_activated",
        lambda key, _widget: activations.append(key),
    )

    try:
        workspace._initial_real_tab_activated = True
        workspace._background_prewarm_enabled = True
        workspace._start_background_tab_prewarm()
        _stop_preload_timer(workspace)
        workspace._prewarm_next_tab()
        _stop_preload_timer(workspace)

        scan_index = workspace._tab_index_for_key("scan")
        assert workspace.activate_tab(scan_index, reason=TabLoadReason.USER.value) is True
        first_widget = workspace._background_prewarm_active_widget
        first_widget.ready = True
        workspace._prewarm_next_tab()
        _stop_preload_timer(workspace)
        workspace._prewarm_next_tab()
        _stop_preload_timer(workspace)

        promoted = workspace.get_loaded_tab("scan")
        if terminal_status == "timeout":
            workspace._background_prewarm_active_started_at = 0.0
            workspace._prewarm_next_tab()
            _stop_preload_timer(workspace)

        assert promoted._workspace_noninteractive_loaded is False
        assert promoted._workspace_load_reason == TabLoadReason.USER.value
        assert [key for event, key in events if event == "construct"].count("scan") == 1
        assert [key for event, key in events if event == "prime"].count("scan") == 1
        assert activations.count("scan") == 1

        status = workspace.background_preload_status()
        assert "scan" in status["failures"]
        assert ("scan" in status["timeouts"]) is (terminal_status == "timeout")
        assert status["pending_priority_keys"] == []

        workspace._prewarm_next_tab()
        _stop_preload_timer(workspace)
        assert workspace._background_prewarm_active_key == "system_log"
    finally:
        workspace.shutdown()
        workspace.deleteLater()


def test_shutdown_stops_owned_preload_timer_and_clears_active_step(qt_application):
    workspace = ClassicWorkspace(
        data_provider=object(),
        engine=object(),
        background_prewarm=False,
        watchlist_startup_tasks=False,
    )
    workspace._background_prewarm_enabled = True
    workspace._background_prewarm_started = True
    workspace._background_prewarm_queue = ["scan"]
    workspace._background_prewarm_active_key = "watchlist"
    workspace._background_prewarm_active_widget = object()
    workspace._background_prewarm_timer.start(60_000)

    workspace.shutdown()

    assert workspace._background_prewarm_timer.isActive() is False
    assert workspace._background_prewarm_queue == []
    assert workspace._background_prewarm_active_key == ""
    assert workspace._background_prewarm_active_widget is None
    assert workspace._background_prewarm_enabled is False
    workspace.deleteLater()
