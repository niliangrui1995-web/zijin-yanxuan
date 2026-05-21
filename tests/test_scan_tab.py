# -*- coding: utf-8 -*-
from PyQt6.QtTest import QSignalSpy

from core.event_bus import event_bus
from ui.tabs.scan_tab import ScanTab


class _DummyProvider:
    def __init__(self):
        self.code2name = {"300093": "*ST金刚"}
        self.requests = []

    def ensure_code_name_map(self, codes=None, *, refresh_missing=False):
        normalized_codes = tuple(sorted(str(code) for code in (codes or ())))
        self.requests.append((normalized_codes, refresh_missing))
        return dict(self.code2name)


class _FakeSettings:
    def __init__(self, values=None):
        self.values = dict(values or {})
        self.synced = False

    def value(self, key, default=None, type=None):
        return self.values.get(key, default)

    def setValue(self, key, value):
        self.values[key] = value

    def sync(self):
        self.synced = True


def test_scan_tab_idle_status_summary_is_not_blank(monkeypatch):
    monkeypatch.setattr("ui.tabs.scan_tab.QTimer.singleShot", lambda *_args, **_kwargs: None)

    tab = ScanTab(data_provider=None, engine=None)
    try:
        assert tab.lbl_scan_status.text()
        assert "RPS" in tab.lbl_scan_status.text()
        assert tab.scan_search.accessibleName() == "VCP 扫描筛选"
        assert tab.btn_scan_settings.accessibleName() == "VCP 扫描参数设置"
    finally:
        tab.deleteLater()


def test_merge_scan_results_prefers_newer_trade_date(monkeypatch):
    monkeypatch.setattr("ui.tabs.scan_tab.QTimer.singleShot", lambda *_args, **_kwargs: None)

    tab = ScanTab(data_provider=None, engine=None)
    try:
        merged, stats = tab._merge_scan_results(
            [
                {"代码": "000001", "名称": "平安银行", "触发日期": "2026-04-11", "评分": 81},
                {"代码": "000002", "名称": "万科A", "触发日期": "2026-04-10", "评分": 77},
            ],
            [
                {"代码": "000001", "名称": "平安银行", "触发日期": "2026-04-14", "评分": 92},
                {"代码": "000004", "名称": "国华网安", "触发日期": "2026-04-14", "评分": 88},
            ],
        )

        merged_map = {row["代码"]: row for row in merged}
        assert merged_map["000001"]["触发日期"] == "2026-04-14"
        assert merged_map["000001"]["评分"] == 92
        assert merged_map["000004"]["名称"] == "国华网安"
        assert stats["新增"] == 1
        assert stats["更新"] == 1
        assert stats["原始命中"] == 2
    finally:
        tab.deleteLater()


def test_scan_tab_does_not_join_realtime_quote_universe(monkeypatch):
    monkeypatch.setattr("ui.tabs.scan_tab.QTimer.singleShot", lambda *_args, **_kwargs: None)

    tab = ScanTab(data_provider=None, engine=None)
    try:
        assert tab.get_realtime_quote_codes() == set()
    finally:
        tab.deleteLater()


def test_scan_tab_auto_f5_incremental_scan_skips_running_worker(monkeypatch):
    monkeypatch.setattr("ui.tabs.scan_tab.QTimer.singleShot", lambda *_args, **_kwargs: None)

    class _RunningWorker:
        def isRunning(self):
            return True

    tab = ScanTab(data_provider=None, engine=None)
    calls = []
    try:
        tab.worker = _RunningWorker()
        tab._on_incremental_scan_clicked = lambda: calls.append("start")

        assert tab.run_auto_incremental_scan_after_f5() is False
        assert calls == []
    finally:
        tab.deleteLater()


def test_scan_tab_auto_f5_incremental_scan_starts_when_idle(monkeypatch):
    monkeypatch.setattr("ui.tabs.scan_tab.QTimer.singleShot", lambda *_args, **_kwargs: None)

    tab = ScanTab(data_provider=None, engine=None)
    calls = []
    try:
        tab._settings = _FakeSettings()
        tab._resolve_incremental_scan_date = lambda: "2026-04-24"
        tab.start_scan = lambda sd, ed, merge_mode=False: calls.append((sd, ed, merge_mode)) or True

        assert tab.run_auto_incremental_scan_after_f5() is True
        assert calls == [("2026-04-24", "2026-04-24", True)]
        assert tab._settings.values[ScanTab.AUTO_F5_INCREMENTAL_SCAN_DATE_KEY] == "20260424"
        assert tab._settings.synced is True
    finally:
        tab.deleteLater()


def test_scan_tab_refresh_data_after_f5_loads_cache_and_starts_incremental(monkeypatch):
    monkeypatch.setattr("ui.tabs.scan_tab.QTimer.singleShot", lambda *_args, **_kwargs: None)

    tab = ScanTab(data_provider=None, engine=None)
    calls = []
    try:
        tab._load_scan_cache = lambda: calls.append("load_cache")
        tab.refresh_table_from_latest_snapshot = lambda current_model=None, *, async_local=True: calls.append(
            (current_model, async_local)
        )
        tab.run_auto_incremental_scan_after_f5 = lambda: calls.append("incremental") or True

        assert tab.refresh_data_after_f5() is True
        assert calls == ["load_cache", (tab.source_model, True), "incremental"]
    finally:
        tab.deleteLater()


def test_scan_tab_lineage_and_local_snapshot_hydration_use_existing_snapshot(monkeypatch):
    monkeypatch.setattr("ui.tabs.scan_tab.QTimer.singleShot", lambda *_args, **_kwargs: None)

    tab = ScanTab(data_provider=_DummyProvider(), engine=None)
    snapshot_calls = []
    local_snapshot_calls = []
    update_calls = []
    original_update_data = tab.source_model.update_data

    def _spy_update_data(rows):
        update_calls.append(len(rows))
        original_update_data(rows)

    monkeypatch.setattr(tab.source_model, "update_data", _spy_update_data)
    monkeypatch.setattr(
        tab,
        "_apply_quote_store_snapshot",
        lambda **kwargs: snapshot_calls.append(kwargs),
    )
    monkeypatch.setattr(
        tab,
        "_prime_visible_local_quote_snapshot",
        lambda current_model=None: local_snapshot_calls.append(current_model) or True,
    )

    try:
        rows = [
            {
                "代码": "300093",
                "名称": "金刚光伏",
                "收盘": 1.23,
                "触发日期": "2026-04-24",
                "评分": 88,
            }
        ]

        tab._render_scan_table(rows)
        tab._render_scan_table(rows)
        lineage = tab.get_data_lineage()

        assert update_calls == [1]
        assert len(snapshot_calls) == 2
        assert snapshot_calls[0]["current_model"] is tab.source_model
        assert local_snapshot_calls == [tab.source_model, tab.source_model]
        assert lineage["key"] == "scan"
        assert lineage["provider"] == "scan_runtime_service"
        assert lineage["trade_date"] == "2026-04-24"
        assert lineage["triggered_network"] is False
        assert lineage["row_count"] == 1
        assert "provider_fault_tolerance" in lineage
    finally:
        tab.deleteLater()


def test_scan_tab_auto_f5_incremental_scan_runs_each_f5_even_same_trade_date(monkeypatch):
    monkeypatch.setattr("ui.tabs.scan_tab.QTimer.singleShot", lambda *_args, **_kwargs: None)

    tab = ScanTab(data_provider=None, engine=None)
    calls = []
    try:
        tab._settings = _FakeSettings({ScanTab.AUTO_F5_INCREMENTAL_SCAN_DATE_KEY: "20260424"})
        tab._resolve_incremental_scan_date = lambda: "2026-04-24"
        tab.start_scan = lambda sd, ed, merge_mode=False: calls.append((sd, ed, merge_mode)) or True

        assert tab.run_auto_incremental_scan_after_f5() is True
        assert calls == [("2026-04-24", "2026-04-24", True)]
        assert tab._settings.values[ScanTab.AUTO_F5_INCREMENTAL_SCAN_DATE_KEY] == "20260424"
        assert tab._settings.synced is True
    finally:
        tab.deleteLater()


def test_merge_scan_results_keeps_existing_rows_when_incremental_scan_has_no_hits(monkeypatch):
    monkeypatch.setattr("ui.tabs.scan_tab.QTimer.singleShot", lambda *_args, **_kwargs: None)

    tab = ScanTab(data_provider=None, engine=None)
    try:
        base_rows = [
            {"代码": "000001", "名称": "平安银行", "触发日期": "2026-04-11", "评分": 81},
            {"代码": "000002", "名称": "万科A", "触发日期": "2026-04-10", "评分": 77},
        ]
        merged, stats = tab._merge_scan_results(base_rows, [])

        assert merged == base_rows
        assert stats["原始命中"] == 0
        assert stats["新增"] == 0
        assert stats["更新"] == 0
        assert stats["刷新"] == 0
    finally:
        tab.deleteLater()


def test_refresh_scan_result_names_repairs_placeholder_name(monkeypatch):
    monkeypatch.setattr("ui.tabs.scan_tab.QTimer.singleShot", lambda *_args, **_kwargs: None)

    provider = _DummyProvider()
    tab = ScanTab(data_provider=provider, engine=None)
    try:
        refreshed = tab._refresh_scan_result_names([{"代码": "300093", "名称": "300093", "触发日期": "2026-04-17"}])

        assert refreshed[0]["名称"] == "*ST金刚"
        assert provider.requests == [(("300093",), True)]
    finally:
        tab.deleteLater()


def test_incremental_scan_repairs_existing_cached_names(monkeypatch):
    monkeypatch.setattr("ui.tabs.scan_tab.QTimer.singleShot", lambda *_args, **_kwargs: None)

    provider = _DummyProvider()
    tab = ScanTab(data_provider=provider, engine=None)
    spy = QSignalSpy(event_bus.sig_scan_updated)
    try:
        tab._current_results = [{"代码": "300093", "名称": "300093", "触发日期": "2026-04-16", "评分": 80}]
        tab._scan_mode = "incremental"

        tab._on_scan_results([])

        row = next(item for item in tab._current_results if item["代码"] == "300093")
        assert row["名称"] == "*ST金刚"
        assert len(spy) == 1
    finally:
        tab.deleteLater()


def test_scan_action_state_switches_the_correct_button_for_incremental_mode(monkeypatch):
    monkeypatch.setattr("ui.tabs.scan_tab.QTimer.singleShot", lambda *_args, **_kwargs: None)

    tab = ScanTab(data_provider=None, engine=None)
    try:
        tab._scan_mode = "incremental"
        tab._scan_target_date = "2026-04-17"
        tab._set_scan_action_state("running")

        assert tab.btn_scan_action.text() == "开始扫描"
        assert tab.btn_scan_action.isEnabled() is False
        assert tab.btn_scan_increment.text() == "停止补扫"
        assert tab.btn_scan_increment.isEnabled() is True

        tab._set_scan_action_state("stopping")

        assert tab.btn_scan_action.text() == "开始扫描"
        assert tab.btn_scan_action.isEnabled() is False
        assert tab.btn_scan_increment.text() == "正在停止补扫..."
        assert tab.btn_scan_increment.isEnabled() is False

        tab._set_scan_action_state("idle")

        assert tab.btn_scan_action.text() == "开始扫描"
        assert tab.btn_scan_action.isEnabled() is True
        assert tab.btn_scan_increment.text() == "新增补扫"
        assert tab.btn_scan_increment.isEnabled() is True
    finally:
        tab.deleteLater()
