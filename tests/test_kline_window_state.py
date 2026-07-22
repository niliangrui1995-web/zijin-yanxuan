# -*- coding: utf-8 -*-
from types import SimpleNamespace

from app.services.kline_open_context import KlineNavItem, KlineOpenContext
from app.services.ui_task_lifecycle_service import task_lifecycle_for
from ui.kline_window_state import (
    current_kline_open_context,
    initialize_kline_window_state,
    reset_kline_window_lease_state,
)


def test_initialize_state_uses_immutable_open_context_without_business_rows():
    context = KlineOpenContext(
        code="000001",
        name="平安银行",
        vcp_data={"形态": "VCP", "nested": {"value": 1}},
        navigation=(
            KlineNavItem("000001", "平安银行", "scan", 4),
            KlineNavItem("000002", "万科A", "scan", 4),
        ),
        current_idx=0,
        source_tab_key="scan",
        source_tab_index=4,
    )
    window = SimpleNamespace()

    initialize_kline_window_state(
        window,
        main_window="main",
        code="ignored",
        name="ignored",
        data_provider="provider",
        vcp_data={"ignored": True},
        code_list=[{"代码": "ignored", "large": list(range(100))}],
        current_idx=9,
        open_context=context,
    )

    assert window.code == "000001"
    assert window.name == "平安银行"
    assert window.vcp_data == {"形态": "VCP", "nested": {"value": 1}}
    assert window.code_list == [
        {"代码": "000001", "名称": "平安银行", "__source_tab_key": "scan", "__source_tab_index": 4},
        {"代码": "000002", "名称": "万科A", "__source_tab_key": "scan", "__source_tab_index": 4},
    ]
    assert window._open_context_resolved is True
    assert window._load_controller.window_id
    assert window._render_generation == 0


def test_initialize_state_keeps_legacy_entry_and_unique_window_identity():
    first = SimpleNamespace()
    second = SimpleNamespace()
    kwargs = dict(
        main_window=None,
        code=" 2330.TW ",
        name=" 台积电 ",
        data_provider=None,
        vcp_data={"market": "TW"},
        code_list=None,
        current_idx=0,
        open_context=None,
    )

    initialize_kline_window_state(first, **kwargs)
    initialize_kline_window_state(second, **kwargs)

    assert first.code == "2330.TW"
    assert first.name == "台积电"
    assert first._open_context_resolved is False
    assert first._load_controller.window_id != second._load_controller.window_id
    assert first._runtime_lifecycle.runtime_active is True


def test_current_context_reuses_initial_snapshot_and_rebuilds_navigation_switch():
    initial = KlineOpenContext(code="000001", name="平安银行", vcp_data={"initial": True})
    window = SimpleNamespace(
        code="000001",
        name="平安银行",
        vcp_data={"initial": True},
        code_list=[
            {"代码": "000001", "名称": "平安银行", "__source_tab_key": "scan", "__source_tab_index": 4},
            {"代码": "000002", "名称": "万科A", "__source_tab_key": "scan", "__source_tab_index": 4},
        ],
        current_idx=0,
        _open_context=initial,
    )

    assert current_kline_open_context(window) is initial
    window.code = "000002"
    window.name = "万科A"
    window.vcp_data = {"switched": True}
    window.current_idx = 1
    switched = current_kline_open_context(window)

    assert switched.code == "000002"
    assert switched.current_idx == 1
    assert switched.mutable_vcp_data() == {"switched": True}
    assert switched.navigation[1].source_tab_key == "scan"


def test_reset_lease_preserves_the_single_realtime_timer_instance():
    window = SimpleNamespace()
    initialize_kline_window_state(
        window,
        main_window=None,
        code="2330.TW",
        name="台积电",
        data_provider=None,
        vcp_data={"market": "TW"},
        code_list=None,
        current_idx=0,
        open_context=None,
    )
    stopped = []
    timer = SimpleNamespace(stop=lambda: stopped.append("realtime"))
    poll_timer = SimpleNamespace(stop=lambda: stopped.append("poll"))
    watchdog_timer = SimpleNamespace(stop=lambda: stopped.append("watchdog"))
    window._rt_timer = timer
    window._render_commit_timer = poll_timer
    window._render_watchdog_timer = watchdog_timer
    window._snapshot_render_query_pending = True
    window._snapshot_render_deadline = 123.0
    window.browser = object()

    reset_kline_window_lease_state(
        window,
        main_window=None,
        code="0700.HK",
        name="腾讯控股",
        data_provider=None,
        vcp_data={"market": "HK"},
        code_list=None,
        current_idx=0,
        open_context=None,
    )

    assert window._rt_timer is timer
    assert window._render_commit_timer is poll_timer
    assert window._render_watchdog_timer is watchdog_timer
    assert window._snapshot_render_query_pending is False
    assert window._snapshot_render_deadline is None
    assert stopped == ["realtime", "poll", "watchdog"]


def test_reset_lease_replaces_the_shutdown_task_lifecycle():
    window = SimpleNamespace()
    initialize_kline_window_state(
        window,
        main_window=None,
        code="000001",
        name="平安银行",
        data_provider=None,
        vcp_data=None,
        code_list=None,
        current_idx=0,
        open_context=None,
    )
    closed_lifecycle = task_lifecycle_for(window)
    assert closed_lifecycle.shutdown(timeout_ms=0) is True

    reset_kline_window_lease_state(
        window,
        main_window=None,
        code="002156",
        name="通富微电",
        data_provider=None,
        vcp_data=None,
        code_list=None,
        current_idx=0,
        open_context=None,
    )

    active_lifecycle = task_lifecycle_for(window)
    token = active_lifecycle.begin("history_load")
    assert active_lifecycle is not closed_lifecycle
    assert token.cancelled is False
