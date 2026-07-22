# -*- coding: utf-8 -*-
"""High-yield edge coverage for small UI orchestration helpers.

The tests deliberately use synchronous fakes so they never start native Qt
threads, network work, or real background jobs.
"""

from __future__ import annotations

from types import SimpleNamespace

from ui.services import log_buffer_service as log_module
from ui.workspaces import background_preload_receipt as receipt_module
from ui.workspaces import workspace_facade as facade_module


class _Signal:
    def __init__(self, *, error: Exception | None = None) -> None:
        self.calls: list[tuple] = []
        self.callbacks: list = []
        self.error = error

    def emit(self, *args) -> None:
        if self.error is not None:
            raise self.error
        self.calls.append(args)

    def connect(self, callback, **_kwargs) -> None:
        self.callbacks.append(callback)


class _RecordingStream:
    encoding = None
    errors = None

    def __init__(self) -> None:
        self.writes: list[str] = []
        self.flush_calls = 0
        self.tty = True

    def write(self, message) -> None:
        self.writes.append(str(message))

    def flush(self) -> None:
        self.flush_calls += 1

    def isatty(self) -> bool:
        return self.tty


class _BrokenStream:
    def write(self, _message) -> None:
        raise OSError("write failed")

    def flush(self) -> None:
        raise RuntimeError("flush failed")

    def isatty(self) -> bool:
        raise ValueError("tty failed")


def test_log_stream_resolution_and_fallback_edges(monkeypatch):
    stream = _RecordingStream()
    inner = SimpleNamespace(_is_ui_log_redirect=True, original=stream)
    outer = SimpleNamespace(_is_ui_log_redirect=True, original=inner)

    assert log_module._resolve_original_stream(object(), outer) is stream
    assert log_module._resolve_original_stream(None, object()) is None

    cycle = SimpleNamespace(_is_ui_log_redirect=True)
    cycle.original = cycle
    assert log_module._resolve_original_stream(cycle) is None

    monkeypatch.setattr(log_module, "_resolve_original_stream", lambda *_candidates: None)
    log_module._safe_fallback_write("discarded")

    monkeypatch.setattr(log_module, "_resolve_original_stream", lambda *_candidates: stream)
    log_module._safe_fallback_write("fallback")
    assert stream.writes == ["fallback"]
    assert stream.flush_calls == 1

    monkeypatch.setattr(log_module, "_resolve_original_stream", lambda *_candidates: _BrokenStream())
    log_module._safe_fallback_write("suppressed")


def test_log_redirect_stream_covers_value_and_failure_boundaries(monkeypatch):
    stream = _RecordingStream()
    event_signal = _Signal()
    fallback_messages: list[str] = []
    monkeypatch.setattr(log_module, "event_bus", SimpleNamespace(sig_system_log=event_signal))
    monkeypatch.setattr(log_module, "_safe_fallback_write", fallback_messages.append)

    redirect = log_module._LogRedirectStream(stream)
    assert redirect.encoding == "utf-8"
    assert redirect.errors == "replace"
    assert redirect.write(None) == 0
    assert redirect.write(" \n") == 2
    assert redirect.write(123) == 3
    assert stream.writes == ["123"]
    assert event_signal.calls == [("info", "123")]
    assert redirect.isatty() is True

    redirect.flush()
    assert stream.flush_calls == 2

    redirect.original = _BrokenStream()
    assert redirect.write("broken") == 6
    redirect.flush()
    assert redirect.isatty() is False
    assert any("原始流写入失败" in message for message in fallback_messages)
    assert any("flush失败" in message for message in fallback_messages)

    redirect.original = stream
    monkeypatch.setattr(
        log_module,
        "event_bus",
        SimpleNamespace(sig_system_log=_Signal(error=RuntimeError("event failed"))),
    )
    assert redirect.write("event") == 5
    assert any("事件总线发送失败" in message for message in fallback_messages)

    redirect.original = None
    assert redirect.encoding == "utf-8"
    assert redirect.errors == "replace"
    assert redirect.isatty() is False


def test_log_buffer_snapshots_clear_and_global_parent_adoption(monkeypatch):
    service = log_module.LogBufferService(max_entries=0)
    versioned_entries: list[tuple] = []
    clears: list[tuple] = []
    service.sig_versioned_entry.connect(lambda *args: versioned_entries.append(args))
    service.sig_cleared.connect(lambda *args: clears.append(args))

    assert service.is_installed is False
    service._capture_entry("", None)
    service._capture_entry("warn", "second")
    assert service.snapshot() == [("warn", "second")]
    assert service.snapshot_versioned() == (0, 2, [(2, "warn", "second")])
    assert service.generation == 0
    assert versioned_entries[-1] == (0, 2, "warn", "second")

    assert service.clear() == (1, 2)
    assert service.snapshot() == []
    assert clears == [(1, 2)]

    adopted: list[object] = []
    fake_service = SimpleNamespace(
        parent=lambda: None,
        setParent=adopted.append,
    )
    parent = object()
    monkeypatch.setattr(log_module, "_log_buffer_service", fake_service)
    assert log_module.get_log_buffer_service(parent=parent) is fake_service
    assert adopted == [parent]
    assert log_module.get_log_buffer_service() is fake_service


def test_preload_receipt_normalizes_and_tolerates_cancellation_failures():
    task_with_id = SimpleNamespace(task_id=" task-a ")
    assert receipt_module._normalized_task_ids(
        [task_with_id, "task-a", "", None, " task-b "]
    ) == ("task-a", "task-b")

    lifecycle_calls: list[tuple[str, str]] = []

    def cancel_lifecycle(name: str, *, reason: str) -> None:
        lifecycle_calls.append((name, reason))
        if name == "bad":
            raise RuntimeError("already stopped")

    owner = SimpleNamespace(_task_lifecycle=SimpleNamespace(cancel=cancel_lifecycle))
    receipt_module._cancel_lifecycle_tasks(owner, ("good", "bad", "after"), reason="shutdown")
    receipt_module._cancel_lifecycle_tasks(SimpleNamespace(), ("ignored",), reason="shutdown")
    assert lifecycle_calls == [
        ("good", "shutdown"),
        ("bad", "shutdown"),
        ("after", "shutdown"),
    ]

    runner_calls: list[tuple[str, str]] = []

    def cancel_runner(task_id: str, *, reason: str) -> None:
        runner_calls.append((task_id, reason))
        if task_id == "bad":
            raise ValueError("already gone")

    receipt_module._cancel_runner_tasks(cancel_runner, ("good", "bad", "after"), reason="timeout")
    receipt_module._cancel_runner_tasks(None, ("ignored",), reason="timeout")
    assert runner_calls == [
        ("good", "timeout"),
        ("bad", "timeout"),
        ("after", "timeout"),
    ]


def test_preload_receipt_status_covers_active_local_and_rejected_edges():
    immediate = receipt_module.BackgroundPreloadCancellationReceipt.immediate()
    assert immediate.active_task_ids() == ()
    assert immediate.is_settled() is True
    assert immediate.status() == {
        "accepted": True,
        "tracking_ok": True,
        "task_ids": [],
        "active_task_ids": [],
        "unknown_task_ids": [],
        "local_settled": True,
        "settled": True,
    }

    rejected = receipt_module.BackgroundPreloadCancellationReceipt(
        task_ids=("task",),
        accepted=False,
        local_settled=lambda: True,
    )
    assert rejected.is_settled() is False
    assert rejected.status()["local_settled"] is False

    def active_or_error(task_id: str) -> bool:
        if task_id == "unknown":
            raise RuntimeError("runner unavailable")
        return task_id == "active"

    active = receipt_module.BackgroundPreloadCancellationReceipt(
        task_ids=("done", "active", "unknown"),
        is_task_active=active_or_error,
        local_settled=lambda: True,
    )
    assert active.active_task_ids() == ("active",)
    assert active.unknown_task_ids() == ("unknown",)
    assert active.is_settled() is False
    assert active.status()["active_task_ids"] == ["active"]
    assert active.status()["unknown_task_ids"] == ["unknown"]
    assert active.status()["tracking_ok"] is False

    for local_settled in (
        lambda: False,
        lambda: (_ for _ in ()).throw(TypeError("not ready")),
    ):
        receipt = receipt_module.BackgroundPreloadCancellationReceipt(local_settled=local_settled)
        assert receipt.is_settled() is False
        assert receipt.status()["settled"] is False


def test_cancel_background_preload_uses_default_runner_and_reports_untrackable_tasks(monkeypatch):
    from app.services import ui_task_service

    reset_calls: list[str] = []
    cancel_calls: list[tuple[str, str]] = []
    runner = SimpleNamespace(
        is_active_task=lambda task_id: task_id == "task-a",
        cancel_task=lambda task_id, *, reason: cancel_calls.append((task_id, reason)),
    )
    monkeypatch.setattr(ui_task_service, "background_job_runner", runner)

    receipt = receipt_module.cancel_background_preload_tasks(
        SimpleNamespace(),
        lifecycle_names=("missing",),
        task_ids=(SimpleNamespace(task_id=" task-a "), "task-a", "task-b"),
        reason="tab_left",
        reset_state=lambda: reset_calls.append("reset"),
    )
    assert receipt.task_ids == ("task-a", "task-b")
    assert receipt.accepted is False
    assert receipt.active_task_ids() == ()
    assert receipt.status()["unknown_task_ids"] == ["task-a", "task-b"]
    assert receipt.status()["tracking_ok"] is False
    assert receipt.is_settled() is False
    assert cancel_calls == [("task-a", "tab_left"), ("task-b", "tab_left")]
    assert reset_calls == ["reset"]

    untrackable = receipt_module.cancel_background_preload_tasks(
        SimpleNamespace(),
        lifecycle_names=(),
        task_ids=("orphan",),
        reason="shutdown",
        reset_state=lambda: reset_calls.append("untrackable"),
        runner=SimpleNamespace(is_active_task=None, cancel_task=None),
    )
    assert untrackable.accepted is False
    assert untrackable.is_settled() is False

    empty = receipt_module.cancel_background_preload_tasks(
        SimpleNamespace(),
        lifecycle_names=(),
        task_ids=(),
        reason="shutdown",
        reset_state=lambda: reset_calls.append("empty"),
        runner=SimpleNamespace(is_active_task=None, cancel_task=None),
    )
    assert empty.accepted is True
    assert empty.is_settled() is True


def _bare_facade(workspace):
    facade = object.__new__(facade_module.WorkspaceFacade)
    facade._workspace = workspace
    return facade


def test_workspace_facade_scheduler_cleanup_and_stock_context_delegates(monkeypatch):
    class BrokenScheduler:
        def cancel(self) -> None:
            raise RuntimeError("cancel failed")

        def deleteLater(self) -> None:  # noqa: N802 - Qt compatibility
            raise RuntimeError("delete failed")

    workspace = SimpleNamespace(_scheduler=BrokenScheduler())
    facade_module._cancel_workspace_scheduler(workspace, "_scheduler")
    facade_module._cancel_workspace_scheduler(workspace, "_missing")
    assert workspace._scheduler is None

    calls: list[tuple] = []
    snapshot = object()
    signal_index = object()
    service = SimpleNamespace(
        publish_kline_signal_index=lambda index: calls.append(("publish", index)) or 3,
        published_kline_signals=lambda code: calls.append(("published", code)) or ("signal",),
        refresh_async_snapshots=lambda **kwargs: calls.append(("prime", kwargs)) or 1,
        async_snapshots_settled=lambda: calls.append(("settled",)) or True,
        cancel_async_snapshots=lambda **kwargs: calls.append(("cancel", kwargs)) or True,
    )
    facade = _bare_facade(SimpleNamespace())
    facade._stock_context_service = service
    monkeypatch.setattr(facade_module, "capture_stock_context_snapshot", lambda candidate: snapshot)

    assert facade.capture_stock_context_snapshot() is snapshot
    assert facade.publish_stock_context_signal_index(signal_index) == 3
    assert facade.get_published_stock_context_signals("600000") == ("signal",)
    assert facade.prime_stock_context_snapshots(force=True, include_fund=False, include_lhb=False) is True
    assert facade.stock_context_snapshots_settled() is True
    assert facade.cancel_stock_context_snapshots(reason="shutdown") is True
    assert facade.collect_stock_context(capture_snapshot=True) is snapshot
    assert calls == [
        ("publish", signal_index),
        ("published", "600000"),
        ("prime", {"force": True, "include_fund": False, "include_lhb": False}),
        ("settled",),
        ("cancel", {"reason": "shutdown"}),
    ]


def test_workspace_facade_tab_access_and_small_action_delegates():
    calls: list[tuple] = []
    tabs = {
        "scan": SimpleNamespace(
            get_scan_results=lambda: [{"代码": "600000"}],
            run_incremental_scan=lambda: 1,
            open_scan_settings=lambda: True,
        ),
        "lhb": SimpleNamespace(refresh_history=lambda: True),
        "fund_holdings": SimpleNamespace(
            run_full_sync=lambda: True,
            run_auto_sync_after_f5=lambda: True,
        ),
        "na_daily": SimpleNamespace(run_post_online_refresh=lambda: calls.append(("online",)) or True),
        "watchlist": SimpleNamespace(
            refresh_watchlist_names=lambda names: calls.append(("names", names)) or True,
            prime_startup_state=lambda: calls.append(("watchlist",)),
        ),
    }
    workspace = SimpleNamespace(
        get_tab=lambda key: tabs.get(key),
        get_loaded_tab=lambda key: tabs.get(key),
    )
    facade = _bare_facade(workspace)
    facade._workspace_navigation_service = SimpleNamespace(
        tab_indices_by_group=lambda: {"group": [1]},
        select_code_row=lambda code, index: calls.append(("select", code, index)) or True,
    )
    facade._workspace_table_service = SimpleNamespace(
        iter_tables=lambda: ["table"],
        refresh_all_tabs_after_f5=lambda **kwargs: calls.append(("refresh", kwargs)),
        refresh_all_tabs_after_f5_scheduled=lambda **kwargs: calls.append(("scheduled", kwargs)) or True,
        refresh_tabs_after_ai_industry_chain_update=lambda: {"scan": True},
    )
    facade._quote_universe_service = SimpleNamespace(collect_realtime_quote_codes=lambda: {"600000"})
    facade._stock_context_service = SimpleNamespace(
        prepare_post_f5_refresh=lambda: calls.append(("prepare",)),
    )

    assert facade._get_tab("scan") is tabs["scan"]
    assert facade._get_loaded_tab("scan") is tabs["scan"]
    assert facade.tab_indices_by_group() == {"group": [1]}
    assert facade.get_scan_results() == [{"代码": "600000"}]
    assert facade.iter_tables() == ["table"]
    facade.refresh_all_tabs_after_f5(skip_cache_reload_tabs=True)
    assert facade.refresh_all_tabs_after_f5_scheduled(interval_ms=7) is True
    assert facade.refresh_tabs_after_ai_industry_chain_update() == {"scan": True}
    assert facade.run_incremental_scan() is True
    assert facade.open_scan_settings() is True
    assert facade.refresh_lhb_history() is True
    assert facade.run_fund_holdings_sync() is True
    assert facade.run_fund_holdings_auto_sync_after_f5() is True
    assert facade.select_code_row("600000", 2) is True
    assert facade.get_realtime_quote_codes() == {"600000"}
    assert facade.refresh_watchlist_names({"600000": "浦发银行"}) is True
    facade.run_post_online_refresh(task_manager=object())
    assert ("names", {"600000": "浦发银行"}) in calls
    assert ("watchlist",) in calls

    no_access = _bare_facade(SimpleNamespace(get_tab=None, get_loaded_tab=None))
    assert no_access._get_tab("scan") is None
    assert no_access._get_loaded_tab("scan") is None
    assert facade_module.WorkspaceFacade._call_bool(object(), "missing") is False
    no_access._workspace = SimpleNamespace(get_loaded_tab=lambda _key: object())
    assert no_access.get_scan_results() == []


def test_workspace_facade_information_source_filters_and_error_boundaries(monkeypatch):
    assert facade_module._uses_post_f5_data_refresh(
        {"post_f5_policy": facade_module.TabPostF5Policy.DATA_REFRESH.value, "group": "other"}
    )
    assert not facade_module._uses_post_f5_data_refresh({"post_f5_policy": "", "group": "情报源"})
    assert facade_module._uses_post_f5_data_refresh({"group": facade_module.INFO_SOURCE_GROUP})
    assert not facade_module._uses_post_f5_data_refresh({"group": "other"})

    good = SimpleNamespace(
        _workspace_noninteractive_loaded=True,
        prepare_post_f5_refresh=lambda: None,
        refresh_data_after_f5=lambda: True,
    )
    hidden = SimpleNamespace(
        _workspace_noninteractive_loaded=True,
        refresh_data_after_f5=lambda: True,
    )
    invalid = object()
    tabs = {"scan": good, "hidden": hidden, "invalid": invalid}
    workspace = SimpleNamespace(
        tab_specs=lambda: [
            {"key": "ignored", "group": "other"},
            {"key": "", "group": facade_module.INFO_SOURCE_GROUP},
            {"key": "scan", "group": facade_module.INFO_SOURCE_GROUP},
            {"key": "hidden", "group": facade_module.INFO_SOURCE_GROUP},
            {"key": "invalid", "group": facade_module.INFO_SOURCE_GROUP},
        ],
        get_loaded_tab=lambda key: tabs.get(key),
        _f5_information_source_last_started_at="bad timestamp",
    )
    facade = _bare_facade(workspace)
    assert facade._iter_post_f5_information_source_tabs() == [("scan", good)]
    assert facade._is_post_f5_information_refresh_cooling_down() is False

    warnings: list[str] = []
    monkeypatch.setattr(facade_module.log, "warning", warnings.append)
    bad = SimpleNamespace(
        refresh_data_after_f5=lambda: (_ for _ in ()).throw(RuntimeError("refresh")),
        prepare_post_f5_refresh=lambda: (_ for _ in ()).throw(RuntimeError("prepare")),
    )
    assert facade._refresh_information_source_after_f5("bad", bad) is False
    facade._prepare_information_source_after_f5("bad", bad)
    facade._prepare_information_source_after_f5("missing", object())
    assert len(warnings) == 2

    assert facade._is_noninteractive_loaded_tab(None) is False
    assert facade._is_noninteractive_loaded_tab(
        SimpleNamespace(_workspace_background_preload_ready=True, _workspace_noninteractive_loaded=True)
    ) is False
    assert facade._is_noninteractive_loaded_tab(SimpleNamespace(_workspace_noninteractive_loaded=True)) is True
    assert facade._is_noninteractive_loaded_tab(SimpleNamespace(_workspace_load_reason="user")) is False
    assert facade._is_noninteractive_loaded_tab(SimpleNamespace(_workspace_load_reason="background_prewarm")) is True

    monkeypatch.setattr(facade, "_is_post_f5_information_refresh_cooling_down", lambda: True)
    assert facade.refresh_information_sources_after_f5() == {}


def test_workspace_facade_scheduled_information_source_boundaries(monkeypatch):
    timer_callbacks: list = []
    monkeypatch.setattr(
        facade_module.QTimer,
        "singleShot",
        lambda _delay, callback: timer_callbacks.append(callback),
    )

    finished: list[str] = []
    no_tabs = _bare_facade(SimpleNamespace())
    no_tabs._iter_post_f5_information_source_tabs = lambda: []
    assert no_tabs.refresh_information_sources_after_f5_scheduled(
        on_finished=lambda: finished.append("empty")
    ) is False
    timer_callbacks.pop()()
    assert finished == ["empty"]

    tab = SimpleNamespace(
        prepare_post_f5_refresh=lambda: finished.append("prepare"),
        refresh_data_after_f5=lambda: finished.append("refresh") or True,
    )
    running = _bare_facade(
        SimpleNamespace(_f5_information_source_scheduler=SimpleNamespace(is_running=lambda: True))
    )
    running._iter_post_f5_information_source_tabs = lambda: [("scan", tab)]
    assert running.refresh_information_sources_after_f5_scheduled() is True

    cooling = _bare_facade(SimpleNamespace(_f5_information_source_scheduler=None))
    cooling._iter_post_f5_information_source_tabs = lambda: [("scan", tab)]
    cooling._is_post_f5_information_refresh_cooling_down = lambda: True
    assert cooling.refresh_information_sources_after_f5_scheduled(
        on_finished=lambda: finished.append("cooldown")
    ) is False
    timer_callbacks.pop()()
    assert finished[-1] == "cooldown"

    schedulers: list = []

    class FakeScheduler:
        def __init__(self, parent, **kwargs) -> None:
            self.parent = parent
            self.kwargs = kwargs
            self.taskFailed = _Signal()
            self.finished = _Signal()
            self.tasks = []
            self.deleted = False
            schedulers.append(self)

        def start(self, tasks) -> None:
            self.tasks = list(tasks)

        def deleteLater(self) -> None:  # noqa: N802 - Qt compatibility
            self.deleted = True

    monkeypatch.setattr(facade_module, "FrameTaskScheduler", FakeScheduler)
    workspace = SimpleNamespace(_f5_information_source_scheduler=None)
    scheduled = _bare_facade(workspace)
    scheduled._iter_post_f5_information_source_tabs = lambda: [("scan", tab)]
    scheduled._is_post_f5_information_refresh_cooling_down = lambda: False

    assert scheduled.refresh_information_sources_after_f5_scheduled(
        on_finished=lambda: finished.append("done"),
        interval_ms=11,
        frame_budget_ms=3,
    ) is True
    scheduler = schedulers[0]
    assert scheduler.kwargs == {"interval_ms": 11, "frame_budget_ms": 3, "max_tasks_per_frame": 1}
    assert scheduler.tasks[0][0] == "scan"
    assert scheduler.tasks[0][1]() is True
    scheduler.finished.callbacks[0]()
    assert workspace._f5_information_source_scheduler is None
    assert scheduler.deleted is True
    assert finished[-1] == "done"
