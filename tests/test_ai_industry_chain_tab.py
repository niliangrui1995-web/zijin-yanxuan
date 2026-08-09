# -*- coding: utf-8 -*-
from copy import deepcopy
from pathlib import Path

import pandas as pd
import pytest
from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QColor
from PyQt6.QtTest import QSignalSpy
from PyQt6.QtWidgets import QTabWidget, QWidget

from app.services.ui_event_service import domain_events as event_bus
from app.services.ui_watchlist_service import watchlist_vm
from core.global_store import global_store
from infra.tasks.lifecycle import CancellationToken
from ui.tabs import ai_industry_chain_tab as tab_module
from ui.tabs.ai_industry_chain_tab import AIIndustryChainTab
from ui.theme import theme_manager


class DummyProvider:
    def __init__(self):
        self.calls = []

    def get_data(self, code):
        if code == "002384":
            return pd.DataFrame({"close": list(range(1, 22))})
        return pd.DataFrame({"close": [10, 11, 12, 13, 14, 15]})

    def fetch_realtime_quotes_batch(self, codes):
        self.calls.append(list(codes))
        return {}


class NoFetchProvider(DummyProvider):
    def fetch_realtime_quotes_batch(self, codes):
        raise AssertionError(f"should reuse global quote snapshot, got {codes}")


def _write_workbook(path: Path):
    openpyxl = pytest.importorskip("openpyxl")
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "AI产业链"
    ws.append(["细分板块", "代码", "公司名称", "备注"])
    ws.append(["200G EML / InP激光器", "002384", "东山精密", "高速光芯片/光模块链条"])
    ws.append(["光DSP / 200G SerDes", "暂无", "暂无合格A股纯标的", "不硬凑"])
    ws.append(["CW / UHP激光器", "688498", "源杰科技", "布局CW激光器"])
    wb.save(path)


def test_ai_industry_chain_show_runtime_skips_non_interactive_load_reason():
    class DummyTab:
        _workspace_load_reason = "screenshot"
        _workspace_noninteractive_loaded = True

        def _is_current_workspace_tab(self):
            return True

    dummy = DummyTab()
    assert not AIIndustryChainTab._should_start_runtime_on_show(dummy)
    assert dummy._workspace_noninteractive_loaded is True

    dummy._workspace_load_reason = "tab_switch"
    assert AIIndustryChainTab._should_start_runtime_on_show(dummy)
    assert dummy._workspace_noninteractive_loaded is False


def test_ai_industry_chain_loads_workbook_and_period_returns(monkeypatch, tmp_path):
    workbook_path = tmp_path / "AI产业链.xlsx"
    _write_workbook(workbook_path)
    monkeypatch.setattr(QTimer, "singleShot", lambda *args, **kwargs: None)

    provider = DummyProvider()
    tab = AIIndustryChainTab(provider, workbook_path=workbook_path)
    cache_refresh_calls = []
    monkeypatch.setattr(
        tab,
        "refresh_table_from_latest_snapshot",
        lambda current_model=None, *, async_local=True: cache_refresh_calls.append((current_model, async_local)),
    )

    try:
        tab._load_chain_data(async_period_returns=False)

        assert tab.model.headers == [
            "序号",
            "代码",
            "名称",
            "现价",
            "涨幅",
            "市值",
            "细分板块",
            "5日涨幅",
            "10日涨幅",
            "20日涨幅",
            "备注",
        ]
        assert len(tab.model.row_data) == 2
        first = tab.model.row_data[0]
        assert first["代码"] == "002384"
        assert first["名称"] == "东山精密"
        assert first["细分板块"] == "200G EML / InP激光器"
        assert first["备注"] == "高速光芯片/光模块链条"
        assert first["5日涨幅"] == pytest.approx((21 / 16 - 1) * 100)
        assert first["10日涨幅"] == pytest.approx((21 / 11 - 1) * 100)
        assert first["20日涨幅"] == pytest.approx((21 / 1 - 1) * 100)
        assert cache_refresh_calls == [(tab.model, True)]
        assert provider.calls == []
    finally:
        tab.close()
        tab.deleteLater()


def test_ai_industry_chain_does_not_subscribe_or_contribute_realtime_codes(monkeypatch, tmp_path):
    workbook_path = tmp_path / "AI产业链.xlsx"
    _write_workbook(workbook_path)
    monkeypatch.setattr(QTimer, "singleShot", lambda *args, **kwargs: None)
    subscribe_calls = []
    monkeypatch.setattr(
        AIIndustryChainTab,
        "subscribe_global_quotes",
        lambda self, *args, **kwargs: subscribe_calls.append((args, kwargs)),
    )

    tab = AIIndustryChainTab(DummyProvider(), workbook_path=workbook_path)

    try:
        tab._load_chain_data(async_period_returns=False)

        assert subscribe_calls == []
        assert tab.get_realtime_quote_codes() == set()
    finally:
        tab.close()
        tab.deleteLater()


def test_ai_industry_chain_uses_plain_pct_headers_for_quotes(monkeypatch, tmp_path):
    workbook_path = tmp_path / "AI产业链.xlsx"
    _write_workbook(workbook_path)
    monkeypatch.setattr(QTimer, "singleShot", lambda *args, **kwargs: None)

    tab = AIIndustryChainTab(DummyProvider(), workbook_path=workbook_path)
    monkeypatch.setattr(tab, "refresh_table_quotes_and_market_caps", lambda **kwargs: None)

    try:
        tab._load_chain_data(async_period_returns=False)
        tab.model.update_quotes(
            {
                "002384": {"close": 12.6, "last_close": 12.0, "zongguben": 1_000_000_000},
            }
        )

        row = tab.model.row_data[0]
        assert row["现价"] == "12.60"
        assert row["涨幅"] == pytest.approx(5.0)
        assert row["市值"] == "126亿"

        pct_col = tab.model.headers.index("涨幅")
        assert tab.model.data(tab.model.index(0, pct_col), Qt.ItemDataRole.DisplayRole) == "+5.00%"
        remark_col = tab.model.headers.index("备注")
        assert tab.model.data(tab.model.index(0, remark_col), Qt.ItemDataRole.ForegroundRole).name() == QColor(
            theme_manager.get("TEXT_MUTED")
        ).name()
    finally:
        tab.close()
        tab.deleteLater()


def test_ai_industry_chain_coalesces_dense_quote_repaints(monkeypatch, tmp_path):
    workbook_path = tmp_path / "AI产业链.xlsx"
    _write_workbook(workbook_path)
    monkeypatch.setattr(QTimer, "singleShot", lambda *args, **kwargs: None)

    tab = AIIndustryChainTab(DummyProvider(), workbook_path=workbook_path)

    try:
        assert tab.model._sparse_update_coalescing is True
        assert tab.table._coalesced_flash_repaint is True
        assert tab.table._flash_repaint_timer.isSingleShot()

        tab.model.update_data(
            [
                {
                    "代码": f"00000{idx}",
                    "名称": str(idx),
                    "现价": "10.00",
                    "涨幅": 0.0,
                    "市值": "10亿",
                }
                for idx in range(1, 6)
            ],
            hydrate_latest_quotes=False,
        )
        changed = QSignalSpy(tab.model.dataChanged)
        tab.model.update_quotes(
            {
                "000001": {"close": 10.1, "last_close": 10.0},
                "000003": {"close": 10.3, "last_close": 10.0},
                "000005": {"close": 10.5, "last_close": 10.0},
            }
        )

        assert len(changed) == 1
    finally:
        tab.close()
        tab.deleteLater()


def test_ai_industry_chain_reset_view_restores_source_order(monkeypatch, tmp_path):
    workbook_path = tmp_path / "AI产业链.xlsx"
    _write_workbook(workbook_path)
    monkeypatch.setattr(QTimer, "singleShot", lambda *args, **kwargs: None)

    tab = AIIndustryChainTab(DummyProvider(), workbook_path=workbook_path)
    monkeypatch.setattr(tab, "refresh_table_quotes_and_market_caps", lambda **kwargs: None)

    try:
        tab._load_chain_data(async_period_returns=False)
        code_col = tab.model.headers.index("代码")

        tab.table.sortByColumn(code_col, Qt.SortOrder.DescendingOrder)
        assert tab.proxy_model.sortColumn() == code_col

        tab._reset_view()

        assert tab.proxy_model.sortColumn() == -1
        visible_codes = []
        for row in range(tab.proxy_model.rowCount()):
            source_row = tab.proxy_model.mapToSource(tab.proxy_model.index(row, 0)).row()
            visible_codes.append(tab.model.row_data[source_row]["代码"])
        assert visible_codes == ["002384", "688498"]
    finally:
        tab.close()
        tab.deleteLater()


def test_ai_industry_chain_refreshes_from_global_snapshot_without_fetch(monkeypatch, tmp_path, qt_application):
    workbook_path = tmp_path / "AI产业链.xlsx"
    _write_workbook(workbook_path)
    global_store.reset_quotes()

    provider = DummyProvider()
    tab = AIIndustryChainTab(provider, workbook_path=workbook_path)
    monkeypatch.setattr(tab, "refresh_table_quotes_and_market_caps", lambda **kwargs: None)

    try:
        assert tab.table._paint_metric_scope == "ai_industry_chain"
        assert tab.table._targeted_flash_repaint is False

        tab._load_chain_data(async_period_returns=False)
        global_store.merge_quotes(
            {
                "002384": {"close": 13.2, "last_close": 12.0, "zongguben": 1_000_000_000},
            }
        )

        tab.show()
        qt_application.processEvents()
        qt_application.processEvents()

        row = tab.model.row_data[0]
        assert row["现价"] == "13.20"
        assert row["涨幅"] == pytest.approx(10.0)
        assert row["市值"] == "132亿"
        assert provider.calls == []
    finally:
        global_store.reset_quotes()
        tab.close()
        tab.deleteLater()


def test_ai_industry_chain_load_reuses_global_quote_snapshot(monkeypatch, tmp_path):
    workbook_path = tmp_path / "AI产业链.xlsx"
    _write_workbook(workbook_path)
    monkeypatch.setattr(QTimer, "singleShot", lambda *args, **kwargs: None)
    global_store.reset_quotes()
    global_store.merge_quotes(
        {
            "002384": {"close": 13.2, "last_close": 12.0, "zongguben": 1_000_000_000},
            "688498": {"close": 98.5, "last_close": 102.0, "zongguben": 120_000_000},
        }
    )

    tab = AIIndustryChainTab(NoFetchProvider(), workbook_path=workbook_path)

    try:
        tab._load_chain_data(async_period_returns=False)

        row = tab.model.row_data[0]
        assert row["现价"] == "13.20"
        assert row["涨幅"] == pytest.approx(10.0)
        assert row["市值"] == "132亿"
    finally:
        global_store.reset_quotes()
        tab.close()
        tab.deleteLater()


def test_ai_industry_chain_watchlist_payload_maps_segment_and_source(monkeypatch):
    original_cache = deepcopy(watchlist_vm._cache)
    monkeypatch.setattr(watchlist_vm, "_save_data", lambda: None)
    watchlist_vm._cache = {}

    payload = AIIndustryChainTab._build_watchlist_payload(
        {
            "代码": "002384",
            "名称": "东山精密",
            "现价": "13.20",
            "涨幅": 10.0,
            "市值": "132亿",
            "细分板块": "200G EML / InP激光器",
            "备注": "高速光芯片/光模块链条",
        }
    )

    try:
        assert payload["细分板块"] == "200G EML / InP激光器"
        assert payload["AI产业链"] == "高速光芯片/光模块链条"
        assert payload["来源标签"] == ["AI产业链"]

        assert watchlist_vm.add_stock("002384", "东山精密", payload) is True
        entry = watchlist_vm._cache["002384"]
        assert entry["细分板块"] == "200G EML / InP激光器"
        assert entry["AI产业链"] == "高速光芯片/光模块链条"
        assert entry["来源标签"] == ["AI产业链"]
        assert "涨幅" not in entry
    finally:
        watchlist_vm._cache = original_cache


def test_ai_industry_chain_emits_updated_event_after_successful_load(monkeypatch, tmp_path):
    workbook_path = tmp_path / "AI产业链.xlsx"
    _write_workbook(workbook_path)
    monkeypatch.setattr(QTimer, "singleShot", lambda *args, **kwargs: None)

    tab = AIIndustryChainTab(DummyProvider(), workbook_path=workbook_path)
    monkeypatch.setattr(tab, "refresh_table_quotes_and_market_caps", lambda **kwargs: None)
    spy = QSignalSpy(event_bus.sig_ai_industry_chain_updated)

    try:
        tab._load_chain_data(async_period_returns=False)

        assert len(spy) == 1
    finally:
        tab.close()
        tab.deleteLater()


def test_ai_industry_chain_prime_background_loads_workbook_in_owner_task(monkeypatch, tmp_path):
    workbook_path = tmp_path / "AI产业链.xlsx"
    _write_workbook(workbook_path)
    monkeypatch.setattr(QTimer, "singleShot", lambda *args, **kwargs: None)

    tab = AIIndustryChainTab(DummyProvider(), workbook_path=workbook_path)
    refresh_calls = []
    snapshot_calls = []
    task_calls = []

    class _Lifecycle:
        def run_background(self, name, fn, **kwargs):
            task_calls.append((name, fn, kwargs))

    monkeypatch.setattr(tab_module, "task_lifecycle_for", lambda *_args, **_kwargs: _Lifecycle())
    monkeypatch.setattr(tab_module, "_should_defer_period_return_commit", lambda _tab: True)
    monkeypatch.setattr(
        tab,
        "refresh_table_quotes_and_market_caps",
        lambda **kwargs: refresh_calls.append(kwargs),
    )
    monkeypatch.setattr(
        tab,
        "_apply_quote_store_snapshot",
        lambda *args, **kwargs: snapshot_calls.append((args, kwargs)),
    )

    try:
        assert tab.prime_background_load() is True

        assert tab._runtime_started is False
        assert tab._background_prime_loading is True
        assert tab._background_prime_done is False
        assert len(tab.model.row_data) == 0
        assert [item[0] for item in task_calls] == ["chain-load"]

        _name, worker, options = task_calls[0]
        payload = worker(CancellationToken())
        assert len(tab.model.row_data) == 0
        options["on_success"](payload)

        assert tab._background_prime_loading is False
        assert tab._background_prime_done is True
        assert tab._runtime_started is True
        assert len(tab.model.row_data) == 2
        assert tab._chain_codes == {"002384", "688498"}
        assert tab.model.row_data[0]["5日涨幅"] == "--"
        assert tab._pending_period_return_source_rows is None
        assert tab._period_return_loading is True
        assert tab.is_background_preload_complete() is False
        assert refresh_calls == []
        assert len(snapshot_calls) == 1
        assert [item[0] for item in task_calls] == ["chain-load", "period-returns"]

        _name, worker, options = task_calls[1]
        period_rows = worker(CancellationToken())
        options["on_success"](period_rows)

        assert tab._period_return_loading is False
        assert tab.model.row_data[0]["5日涨幅"] == pytest.approx((21 / 16 - 1) * 100)
        assert tab._pending_period_return_rows is None
        assert tab.is_background_preload_complete() is True

        late_commits = []
        monkeypatch.setattr(
            tab_module,
            "_commit_period_return_rows",
            lambda *_args, **_kwargs: late_commits.append(True),
        )
        tab.on_workspace_tab_activated()
        assert late_commits == []

        tab._ensure_runtime_started()
        assert tab._runtime_start_timer.isActive() is False
        assert [item[0] for item in task_calls] == ["chain-load", "period-returns"]
    finally:
        tab.close()
        tab.deleteLater()


def test_ai_industry_chain_background_ready_fails_closed_for_all_pending_work(
    monkeypatch,
    tmp_path,
):
    workbook_path = tmp_path / "AI产业链.xlsx"
    _write_workbook(workbook_path)
    monkeypatch.setattr(QTimer, "singleShot", lambda *args, **kwargs: None)
    tab = AIIndustryChainTab(DummyProvider(), workbook_path=workbook_path)
    tab._background_prime_done = True

    try:
        assert tab.is_background_preload_complete() is True

        tab._pending_period_return_rows = (1, [])
        assert tab.is_background_preload_complete() is False

        tab._pending_period_return_rows = None
        tab._pending_period_return_source_rows = (1, [])
        assert tab.is_background_preload_complete() is False

        tab._pending_period_return_source_rows = None
        tab._pending_post_f5_refresh = True
        assert tab.is_background_preload_complete() is False
    finally:
        tab.close()
        tab.deleteLater()


def test_ai_industry_chain_runtime_timer_skips_hidden_tab_and_retries(monkeypatch, tmp_path):
    workbook_path = tmp_path / "AI产业链.xlsx"
    _write_workbook(workbook_path)
    tab = AIIndustryChainTab(DummyProvider(), workbook_path=workbook_path, runtime_start_delay_ms=25)
    visible = {"value": False}
    scheduled = []

    monkeypatch.setattr(
        tab_module,
        "_is_current_visible_chain_tab",
        lambda _tab: visible["value"],
    )
    monkeypatch.setattr(tab, "_should_start_runtime_on_show", lambda: True)
    monkeypatch.setattr(
        tab_module,
        "_schedule_chain_load",
        lambda _tab, **kwargs: scheduled.append(kwargs) or True,
    )

    try:
        assert tab._runtime_start_timer.parent() is tab
        assert tab._runtime_start_timer.isSingleShot() is True

        tab._ensure_runtime_started()
        assert tab._runtime_start_timer.isActive() is True
        tab._runtime_start_timer.stop()
        tab._runtime_start_timer.timeout.emit()

        assert tab._runtime_started is False
        assert scheduled == []

        visible["value"] = True
        tab._ensure_runtime_started()
        tab._runtime_start_timer.stop()
        tab._runtime_start_timer.timeout.emit()

        assert tab._runtime_started is True
        assert scheduled == [{"force_workbook": False}]
    finally:
        tab.close()
        tab.deleteLater()


def test_ai_industry_chain_initial_load_prefers_cache_and_applies_only_on_delivery(monkeypatch, tmp_path):
    workbook_path = tmp_path / "AI产业链.xlsx"
    _write_workbook(workbook_path)
    cached_rows = [{"代码": "002384", "名称": "东山精密", "细分板块": "PCB", "备注": "高速互联"}]
    task_calls = []
    period_calls = []

    class _Lifecycle:
        def run_background(self, name, fn, **kwargs):
            task_calls.append((name, fn, kwargs))

    monkeypatch.setattr(tab_module, "task_lifecycle_for", lambda *_args, **_kwargs: _Lifecycle())
    monkeypatch.setattr(tab_module, "load_cached_ai_industry_chain_rows", lambda _path: cached_rows)
    monkeypatch.setattr(
        tab_module,
        "refresh_ai_industry_chain_rows",
        lambda _path: (_ for _ in ()).throw(AssertionError("valid cache must avoid workbook read")),
    )

    tab = AIIndustryChainTab(DummyProvider(), workbook_path=workbook_path)
    monkeypatch.setattr(tab, "refresh_table_from_latest_snapshot", lambda *args, **kwargs: None)
    monkeypatch.setattr(tab, "_schedule_period_returns", lambda rows: period_calls.append(rows) or True)
    spy = QSignalSpy(event_bus.sig_ai_industry_chain_updated)

    try:
        assert tab_module._schedule_chain_load(tab, force_workbook=False) is True
        assert len(tab.model.row_data) == 0
        assert [item[0] for item in task_calls] == ["chain-load"]

        _name, worker, options = task_calls[0]
        rows, source = worker(CancellationToken())
        assert source == "cache"
        assert rows == cached_rows
        assert len(tab.model.row_data) == 0

        options["on_success"]((rows, source))

        assert [row["代码"] for row in tab.model.row_data] == ["002384"]
        assert period_calls == [cached_rows]
        assert len(spy) == 1
    finally:
        tab.close()
        tab.deleteLater()


def test_ai_industry_chain_manual_and_f5_refresh_force_workbook(monkeypatch, tmp_path, qt_application):
    workbook_path = tmp_path / "AI产业链.xlsx"
    _write_workbook(workbook_path)
    task_calls = []
    workbook_calls = []
    rows = [{"代码": "002384", "名称": "东山精密"}]

    class _Lifecycle:
        def run_background(self, name, fn, **kwargs):
            task_calls.append((name, fn, kwargs))

    monkeypatch.setattr(tab_module, "task_lifecycle_for", lambda *_args, **_kwargs: _Lifecycle())
    monkeypatch.setattr(
        tab_module,
        "load_cached_ai_industry_chain_rows",
        lambda _path: (_ for _ in ()).throw(AssertionError("explicit refresh must bypass cache")),
    )
    monkeypatch.setattr(
        tab_module,
        "refresh_ai_industry_chain_rows",
        lambda path: workbook_calls.append(Path(path)) or rows,
    )

    tabs = QTabWidget()
    tab = AIIndustryChainTab(
        DummyProvider(),
        workbook_path=workbook_path,
        runtime_start_delay_ms=60_000,
    )
    tabs.addTab(tab, "AI产业链")
    tabs.show()
    qt_application.processEvents()
    monkeypatch.setattr(tab, "refresh_table_from_latest_snapshot", lambda *args, **kwargs: None)
    monkeypatch.setattr(tab, "_schedule_period_returns", lambda _rows: True)

    try:
        tab.btn_refresh.click()
        _name, worker, options = task_calls.pop(0)
        payload = worker(CancellationToken())
        options["on_success"](payload)

        assert tab.refresh_data_after_f5() is True
        assert tab._runtime_start_timer.isActive() is False
        _name, worker, options = task_calls.pop(0)
        payload = worker(CancellationToken())
        options["on_success"](payload)
        tab._runtime_start_timer.timeout.emit()

        assert workbook_calls == [workbook_path, workbook_path]
        assert task_calls == []
    finally:
        tabs.close()
        tabs.deleteLater()


def test_ai_industry_chain_hidden_f5_refresh_resumes_once_on_activation(
    monkeypatch,
    tmp_path,
    qt_application,
):
    workbook_path = tmp_path / "AI产业链.xlsx"
    _write_workbook(workbook_path)
    scheduled = []

    monkeypatch.setattr(
        tab_module,
        "_schedule_chain_load",
        lambda _tab, **kwargs: scheduled.append(kwargs) or True,
    )

    tabs = QTabWidget()
    tab = AIIndustryChainTab(DummyProvider(), workbook_path=workbook_path)
    other = QWidget()
    tabs.addTab(tab, "AI产业链")
    tabs.addTab(other, "其他")
    tabs.setCurrentWidget(other)
    tabs.show()
    qt_application.processEvents()
    try:
        assert tab.refresh_data_after_f5() is True
        assert tab.refresh_data_after_f5() is True
        assert tab._pending_post_f5_refresh is True
        assert scheduled == []

        tabs.setCurrentWidget(tab)
        qt_application.processEvents()
        tab.on_workspace_tab_activated()
        tab.on_workspace_tab_activated()

        assert tab._pending_post_f5_refresh is False
        assert tab._runtime_started is True
        assert scheduled == [{"force_workbook": True}]
        assert tab._runtime_start_timer.isActive() is False
        tab._runtime_start_timer.timeout.emit()
        assert scheduled == [{"force_workbook": True}]
    finally:
        tabs.close()
        tabs.deleteLater()


def test_ai_industry_chain_cleanup_stops_timer_and_cancels_owner_tasks(monkeypatch, tmp_path):
    workbook_path = tmp_path / "AI产业链.xlsx"
    _write_workbook(workbook_path)
    shutdown_calls = []

    class _Lifecycle:
        def run_background(self, *_args, **_kwargs):
            return None

        def shutdown(self, *, timeout_ms):
            shutdown_calls.append(timeout_ms)
            return True

    tab = AIIndustryChainTab(DummyProvider(), workbook_path=workbook_path, runtime_start_delay_ms=1000)
    tab._task_lifecycle = _Lifecycle()

    try:
        tab._ensure_runtime_started()
        assert tab._runtime_start_timer.isActive() is True
        assert tab_module._schedule_chain_load(tab, force_workbook=False) is True
        tab._pending_post_f5_refresh = True

        tab._cleanup_runtime_state()

        assert tab._runtime_start_timer.isActive() is False
        assert tab._chain_load_active is False
        assert tab._pending_post_f5_refresh is False
        assert tab._runtime_cleanup_done is True
        assert shutdown_calls == [750]
        assert tab.refresh_data_after_f5() is False
    finally:
        tab.close()
        tab.deleteLater()


def test_ai_industry_chain_queued_activation_cannot_revive_cleaned_up_refresh(monkeypatch, tmp_path):
    workbook_path = tmp_path / "AI产业链.xlsx"
    _write_workbook(workbook_path)
    visible = {"value": False}
    scheduled = []
    monkeypatch.setattr(
        tab_module,
        "_is_current_visible_chain_tab",
        lambda _tab: visible["value"],
    )
    monkeypatch.setattr(
        tab_module,
        "_schedule_chain_load",
        lambda _tab, **kwargs: scheduled.append(kwargs) or True,
    )

    tab = AIIndustryChainTab(DummyProvider(), workbook_path=workbook_path)
    activation_callback = tab.on_workspace_tab_activated
    assert tab.refresh_data_after_f5() is True
    assert tab._pending_post_f5_refresh is True

    tab.deleteLater()
    visible["value"] = True
    activation_callback()
    tab._runtime_start_timer.timeout.emit()

    assert tab._runtime_cleanup_done is True
    assert tab._pending_post_f5_refresh is False
    assert tab.refresh_data_after_f5() is False
    assert scheduled == []


def test_ai_industry_chain_default_period_returns_use_owner_background_task(monkeypatch, tmp_path):
    workbook_path = tmp_path / "AI产业链.xlsx"
    _write_workbook(workbook_path)
    monkeypatch.setattr(QTimer, "singleShot", lambda *args, **kwargs: None)
    batch_calls = []
    task_calls = []

    class _Provider(DummyProvider):
        def get_data_batch(self, codes):
            batch_calls.append(tuple(codes))
            return {code: self.get_data(code) for code in codes}

    class _Lifecycle:
        def run_background(self, name, fn, **kwargs):
            task_calls.append((name, kwargs["task_id"]))
            kwargs["on_success"](fn(CancellationToken()))

    tab = AIIndustryChainTab(_Provider(), workbook_path=workbook_path)
    monkeypatch.setattr(tab_module, "task_lifecycle_for", lambda *_args, **_kwargs: _Lifecycle())
    monkeypatch.setattr(tab, "refresh_table_from_latest_snapshot", lambda *args, **kwargs: None)

    try:
        tab._load_chain_data()

        assert len(task_calls) == 1
        assert task_calls[0][0] == "period-returns"
        assert batch_calls == [("002384", "688498")]
        assert tab.model.row_data[0]["5日涨幅"] == pytest.approx((21 / 16 - 1) * 100)
    finally:
        tab.close()
        tab.deleteLater()


def test_ai_industry_chain_defers_hidden_period_return_commit(monkeypatch, tmp_path):
    workbook_path = tmp_path / "AI产业链.xlsx"
    _write_workbook(workbook_path)
    monkeypatch.setattr(QTimer, "singleShot", lambda *args, **kwargs: None)
    tab = AIIndustryChainTab(DummyProvider(), workbook_path=workbook_path)
    tab.model.update_data([{"代码": "002384", "5日涨幅": "--"}])
    tab._period_return_generation = 1
    result = [{"代码": "002384", "5日涨幅": 31.25, "10日涨幅": 90.0, "20日涨幅": 2000.0}]
    tab._pending_period_return_rows = (1, result)

    class _PendingLifecycle:
        def run_background(self, *_args, **_kwargs):
            return None

    monkeypatch.setattr(tab_module, "task_lifecycle_for", lambda *_args, **_kwargs: _PendingLifecycle())
    monkeypatch.setattr(tab_module, "_should_defer_period_return_commit", lambda _tab: True)

    try:
        assert tab._schedule_period_returns([{"代码": "002384"}])
        assert tab._period_return_generation == 2
        assert tab._pending_period_return_rows is None

        tab_module._on_period_returns_ready(tab, 1, result)
        assert tab._pending_period_return_rows is None

        tab_module._on_period_returns_ready(tab, 2, result)
        assert tab.model.row_data[0]["5日涨幅"] == "--"
        assert tab._pending_period_return_rows == (2, result)

        tab_module._apply_pending_period_return_rows(tab)
        assert tab.model.row_data[0]["5日涨幅"] == 31.25
        assert tab._pending_period_return_rows is None
    finally:
        tab.close()
        tab.deleteLater()


def test_ai_industry_chain_does_not_start_period_return_work_after_tab_is_left(monkeypatch, tmp_path):
    workbook_path = tmp_path / "AI产业链.xlsx"
    _write_workbook(workbook_path)
    monkeypatch.setattr(QTimer, "singleShot", lambda *args, **kwargs: None)
    tab = AIIndustryChainTab(DummyProvider(), workbook_path=workbook_path)
    deferred = {"value": True}
    task_calls = []

    class _Lifecycle:
        def run_background(self, name, fn, **kwargs):
            task_calls.append(name)
            kwargs["on_success"](fn(CancellationToken()))

    monkeypatch.setattr(tab_module, "task_lifecycle_for", lambda *_args, **_kwargs: _Lifecycle())
    monkeypatch.setattr(
        tab_module,
        "_should_defer_period_return_commit",
        lambda _tab: deferred["value"],
    )

    try:
        assert tab._schedule_period_returns([{"代码": "002384"}])
        assert task_calls == []
        assert tab._pending_period_return_source_rows == (1, [{"代码": "002384"}])

        deferred["value"] = False
        tab_module._start_pending_period_return_work(tab)

        assert task_calls == ["period-returns"]
        assert tab._pending_period_return_source_rows is None
    finally:
        tab.close()
        tab.deleteLater()
