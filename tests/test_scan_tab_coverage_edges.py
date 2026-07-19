# -*- coding: utf-8 -*-
from __future__ import annotations

from datetime import date
from types import SimpleNamespace

import pandas as pd
import pytest
from PyQt6.QtCore import Qt
from PyQt6.QtTest import QSignalSpy

from core.event_bus import event_bus
from ui.tabs import scan_tab as scan_module
from ui.tabs.scan_tab import ScanTab


class _Settings:
    def __init__(self, values=None):
        self.values = dict(values or {})
        self.synced = 0

    def value(self, key, default=None, type=None):
        return self.values.get(key, default)

    def setValue(self, key, value):
        self.values[key] = value

    def sync(self):
        self.synced += 1


class _Signal:
    def __init__(self):
        self.callback = None

    def connect(self, callback):
        self.callback = callback


class _Worker:
    def __init__(self, *args, **kwargs):
        self.args = args
        self.kwargs = kwargs
        self.progress = _Signal()
        self.result_ready = _Signal()
        self.finished_scan = _Signal()
        self.finished = _Signal()
        self.started = False
        self.running = False
        self.cancelled = False
        self.deleted = False

    def start(self):
        self.started = True
        self.running = True

    def isRunning(self):
        return self.running

    def cancel(self):
        self.cancelled = True
        self.running = False

    def deleteLater(self):
        self.deleted = True


class _Lifecycle:
    def __init__(self):
        self.calls = []
        self.token = object()

    def begin(self, task_id, *, timeout_sec):
        self.calls.append(("begin", task_id, timeout_sec))
        return self.token

    def cancel(self, task_id, *, reason):
        self.calls.append(("cancel", task_id, reason))

    def complete(self, task_id, token):
        self.calls.append(("complete", task_id, token))

    def shutdown(self, *, timeout_ms):
        self.calls.append(("shutdown", timeout_ms))


@pytest.fixture
def scan_tab(monkeypatch):
    monkeypatch.setattr(scan_module.QTimer, "singleShot", lambda *_args, **_kwargs: None)
    tab = ScanTab(data_provider=None, engine=object())
    yield tab
    tab.deleteLater()


def test_scan_settings_presets_and_lineage_edges(scan_tab):
    settings = _Settings()
    scan_tab._settings = settings

    scan_tab._apply_scan_params({"rps": 91, "amp": 0.6, "ma_bind": 0.08, "amount": 2.5, "high250": 0.2})
    assert scan_tab._get_scan_params() == {
        "rps": 91,
        "amp": 0.6,
        "ma_bind": 0.08,
        "amount": 2.5,
        "high250": 0.2,
    }
    scan_tab._save_scan_params()
    assert settings.values["rps_threshold"] == 91
    assert settings.values["high_250_threshold"] == 0.2
    assert settings.synced == 1
    assert scan_tab._scan_param_segments() == ("RPS≥91", "振幅≤60%", "均线≤8%")

    settings.values["user_presets"] = '{"稳健": {"rps": 90}}'
    assert scan_tab._load_user_presets()["稳健"]["rps"] == 90
    settings.values["user_presets"] = "{broken"
    assert scan_tab._load_user_presets() == {}
    settings.values["user_presets"] = {"not": "serialized"}
    assert scan_tab._load_user_presets() == {}
    scan_tab._save_user_presets({"激进": {"rps": 80}})
    assert "激进" in settings.values["user_presets"]

    scan_tab._current_results = [None, {"触发日期": "2026-07-10"}, {"触发日期": "2026-07-14"}]
    assert scan_tab._latest_scan_trigger_date() == "2026-07-14"
    assert ScanTab._normalize_scan_date("2026/07/14 extra") == "20260714"

    scan_tab._current_results = []
    scan_tab._last_scan_result = None
    lineage = scan_tab.get_data_lineage()
    assert lineage["row_count"] == 0
    assert "scan_rows_empty" in lineage["warnings"]


def test_scan_cache_trade_date_inference_and_calendar_fallback(monkeypatch, scan_tab):
    class _BadLength:
        def __len__(self):
            raise TypeError("bad length")

    class _BadIndex:
        def __len__(self):
            return 1

        @property
        def index(self):
            raise AttributeError("bad index")

    scan_tab.data_provider = SimpleNamespace(
        cache_data={
            "none": None,
            "empty": [],
            "bad-length": _BadLength(),
            "bad-index": _BadIndex(),
            "older": pd.DataFrame({"x": [1]}, index=[pd.Timestamp("2026-07-11")]),
            "newer": pd.DataFrame({"x": [1]}, index=["2026-07-14 00:00:00"]),
            "invalid": pd.DataFrame({"x": [1]}, index=["not-a-date"]),
        }
    )
    assert scan_tab._infer_cache_latest_trade_date() == "20260714"
    assert scan_tab._resolve_incremental_scan_date() == "2026-07-14"

    scan_tab.data_provider = SimpleNamespace(cache_data={})
    monkeypatch.setattr(scan_module.MarketCalendar, "get_latest_trade_date", lambda _market: date(2026, 7, 13))
    assert scan_tab._resolve_incremental_scan_date() == "2026-07-13"
    monkeypatch.setattr(scan_module.MarketCalendar, "get_latest_trade_date", lambda _market: None)
    monkeypatch.setattr(scan_module.MarketCalendar, "today", lambda _market: date(2026, 7, 14))
    assert scan_tab._resolve_incremental_scan_date() == "2026-07-14"


def test_scan_merge_name_fallback_and_finish_messages(scan_tab):
    merged, stats = scan_tab._merge_scan_results(
        [None, {"代码": ""}, {"代码": "000001", "触发日期": "2026-07-14", "评分": 80}],
        [
            None,
            {"代码": ""},
            {"代码": "000001", "触发日期": "2026-07-13", "评分": 70},
            {"代码": "000001", "触发日期": "2026/07/14", "评分": 90},
            {"代码": "000002", "触发日期": "2026-07-14", "评分": 88},
        ],
    )
    by_code = {row["代码"]: row for row in merged}
    assert by_code["000001"]["评分"] == 90
    assert stats == {"原始命中": 5, "新增": 1, "更新": 0, "刷新": 1, "忽略": 1}

    class _FailingProvider:
        code2name = {"000001": "平安银行"}

        def ensure_code_name_map(self, *_args, **_kwargs):
            raise RuntimeError("offline")

    scan_tab.data_provider = _FailingProvider()
    rows = scan_tab._refresh_scan_result_names([None, {"代码": "000001", "名称": "000001"}, {"代码": ""}])
    assert rows[1]["名称"] == "平安银行"
    scan_tab.data_provider = None
    assert scan_tab._refresh_scan_result_names([{"代码": "000002"}]) == [{"代码": "000002"}]

    scan_tab._scan_target_date = "2026-07-14"
    scan_tab._pending_scan_results = [{"代码": "000001"}]
    scan_tab._last_incremental_stats = {"原始命中": 0}
    assert "无新增信号" in scan_tab._build_incremental_finish_message()
    scan_tab._last_incremental_stats = {"原始命中": 3, "新增": 1, "更新": 1, "刷新": 1}
    message = scan_tab._build_incremental_finish_message()
    assert "命中 3 条" in message
    assert "更新 2 只" in message


def test_scan_status_and_action_state_full_mode(scan_tab):
    scan_tab._current_results = [{"代码": "000001", "触发日期": "2026-07-14"}]
    scan_tab.source_model.update_data([{"代码": "000001", "名称": "平安银行"}])
    scan_tab._refresh_scan_status("就绪")
    assert "就绪" in scan_tab.lbl_scan_status.text()

    scan_tab._scan_mode = "full"
    scan_tab._scan_target_date = "2026-07-01 ~ 2026-07-14"
    scan_tab._set_scan_action_state("running")
    assert scan_tab.btn_scan_action.text() == "停止扫描"
    assert scan_tab.btn_scan_action.isEnabled()
    assert not scan_tab.btn_scan_increment.isEnabled()
    scan_tab._set_scan_action_state("stopping")
    assert scan_tab.btn_scan_action.text() == "正在停止..."
    scan_tab.source_model.update_data([])
    scan_tab._current_results = []
    scan_tab._set_scan_action_state("idle")
    assert scan_tab.btn_scan_action.text() == "开始扫描"


def test_scan_dialog_handlers_and_pending_schedule(monkeypatch, scan_tab):
    calls = []

    class _RangeDialog:
        class DialogCode:
            Accepted = 1

        result = 0

        def __init__(self, _parent):
            pass

        def exec(self):
            return self.result

        def selected_range(self):
            return "2026-07-01", "2026-07-14"

    monkeypatch.setattr("ui.components.scan_dialogs.VCPScanRangeDialog", _RangeDialog)
    scan_tab.start_scan = lambda *args, **kwargs: calls.append((args, kwargs)) or True
    scan_tab._on_scan_action_clicked()
    assert calls == []
    _RangeDialog.result = 1
    scan_tab._on_scan_action_clicked()
    assert calls == [(('2026-07-01', '2026-07-14'), {"merge_mode": False})]

    class _SettingsDialog:
        class DialogCode:
            Accepted = 1

        result = 0

        def __init__(self, *_args):
            pass

        def exec(self):
            return self.result

        def values(self):
            return {"rps": 92, "amp": 0.5, "ma_bind": 0.06, "amount": 1.2, "high250": 0.15}

        def user_presets(self):
            return {"saved": {"rps": 92}}

    monkeypatch.setattr("ui.components.scan_dialogs.VCPScanSettingsDialog", _SettingsDialog)
    monkeypatch.setattr(scan_module, "show_toast", lambda *args: calls.append(args))
    scan_tab._show_scan_settings()
    _SettingsDialog.result = 1
    scan_tab._settings = _Settings()
    scan_tab._show_scan_settings()
    assert scan_tab.spn_scan_rps.value() == 92
    assert any(call and call[0] == "VCP 扫描参数已保存" for call in calls if isinstance(call, tuple))

    assert scan_tab.schedule_auto_incremental_scan_after_f5()
    assert not scan_tab.schedule_auto_incremental_scan_after_f5()
    assert scan_tab._f5_auto_incremental_timer.isActive()
    assert scan_tab._f5_auto_incremental_timer.interval() == ScanTab.F5_AUTO_INCREMENTAL_DELAY_MS
    scan_tab.run_auto_incremental_scan_after_f5 = lambda: calls.append("auto") or True
    assert scan_tab._run_pending_auto_incremental_scan_after_f5() is True
    assert not scan_tab._f5_auto_incremental_timer.isActive()
    assert scan_tab._pending_f5_auto_incremental is False
    assert scan_tab.open_scan_settings()

    scan_tab.worker = _Worker()
    scan_tab.worker.running = True
    scan_tab.cancel_scan = lambda: calls.append("cancel") or True
    scan_tab._on_scan_action_clicked()
    assert scan_tab._on_incremental_scan_clicked() is True
    assert calls[-2:] == ["cancel", "cancel"]

    scan_tab.worker = None
    scan_tab.start_scan = lambda *_args, **_kwargs: False
    scan_tab.run_auto_incremental_scan_after_f5 = ScanTab.run_auto_incremental_scan_after_f5.__get__(scan_tab)
    assert not scan_tab.run_auto_incremental_scan_after_f5()

    scan_tab._apply_quote_store_snapshot = lambda **kwargs: calls.append(kwargs)
    scan_tab._on_cache_reload_completed()
    assert calls[-1]["current_model"] is scan_tab.source_model
    assert scan_tab.get_scan_results() == list(scan_tab._current_results)
    scan_tab._on_incremental_scan_clicked = lambda: calls.append("incremental") or True
    assert scan_tab.run_incremental_scan()


def test_scan_start_cancel_finish_and_shutdown(monkeypatch, scan_tab):
    lifecycle = _Lifecycle()
    created = []

    def _worker_factory(*args, **kwargs):
        worker = _Worker(*args, **kwargs)
        created.append(worker)
        return worker

    scan_tab._task_lifecycle = lifecycle
    scan_tab._settings = _Settings()
    monkeypatch.setattr("ui.workers.scan_worker.ScanWorker", _worker_factory)
    scan_tab._set_scan_action_state = lambda state: lifecycle.calls.append(("state", state))

    assert scan_tab.start_scan("20260701", "20260714", merge_mode=True)
    worker = created[0]
    assert worker.started
    assert worker.args[2:4] == ("2026-07-01", "2026-07-14")
    assert scan_tab._scan_mode == "incremental"
    assert scan_tab._scan_target_date == "2026-07-14"
    assert not scan_tab.start_scan("2026-07-01", "2026-07-14")

    assert scan_tab.cancel_scan()
    assert worker.cancelled
    assert ("cancel", "scan", "user_cancelled") in lifecycle.calls
    assert not scan_tab.cancel_scan()

    worker.running = False
    worker.deleted = False
    scan_tab._scan_token = lifecycle.token
    scan_tab._on_worker_thread_finished()
    assert worker.deleted
    assert scan_tab.worker is None
    assert any(call[0] == "complete" for call in lifecycle.calls)

    worker2 = _Worker()
    scan_tab.worker = worker2
    revived = []
    scan_tab.run_auto_incremental_scan_after_f5 = lambda: revived.append(True) or True
    assert scan_tab.schedule_auto_incremental_scan_after_f5()
    assert scan_tab._f5_auto_incremental_timer.isActive()
    shutdown_calls = []
    monkeypatch.setattr(scan_module, "request_thread_shutdown", lambda *args, **kwargs: shutdown_calls.append((args, kwargs)))
    scan_tab.shutdown()
    assert shutdown_calls[0][1]["label"] == "Scan worker"
    assert ("shutdown", 2000) in lifecycle.calls
    assert not scan_tab._f5_auto_incremental_timer.isActive()
    assert scan_tab._pending_f5_auto_incremental is False
    assert scan_tab._run_pending_auto_incremental_scan_after_f5() is False
    assert revived == []

    scan_tab.worker = None
    scan_tab._scan_token = None
    assert not scan_tab.start_scan("2026-07-01", "2026-07-14", merge_mode=False)
    assert not scan_tab.refresh_data_after_f5()
    assert len(created) == 1

    scan_tab._scan_mode = "full"
    scan_tab._on_scan_results([{"代码": "000003", "名称": "测试", "触发日期": "2026-07-14"}])
    assert scan_tab._pending_scan_results[0]["代码"] == "000003"


def test_scan_result_render_finish_and_cache_paths(monkeypatch, scan_tab):
    progress = QSignalSpy(event_bus.sig_task_progress)
    system_log = QSignalSpy(event_bus.sig_system_log)
    saved = []
    monkeypatch.setattr(scan_module, "save_scan_cache", lambda rows, params: saved.append((rows, params)) or "cleared")

    scan_tab._pending_scan_results = []
    scan_tab._scan_mode = "full"
    scan_tab._on_scan_finished(True, "done")
    assert saved and progress[-1][1] == 100
    scan_tab._on_scan_finished(False, "failed")
    assert progress[-1][1] == 0

    scan_tab._render_scan_table([])
    assert scan_tab.source_model.rowCount() == 0
    scan_tab._render_scan_table([{"代码": "000001", "名称": "平安银行", "收盘": "bad", "评分": "--"}])
    assert scan_tab.source_model.rowCount() == 1
    price_index = scan_tab.source_model.index(0, scan_tab.source_model.headers.index("现价"))
    assert scan_tab.source_model.data(price_index, Qt.ItemDataRole.DisplayRole) == "bad"
    assert system_log

    monkeypatch.setattr(scan_module, "save_scan_cache", lambda *_args: "saved")
    scan_tab._save_scan_cache([{"代码": "000001"}])

    def _raise_save(*_args):
        raise OSError("disk full")

    monkeypatch.setattr(scan_module, "save_scan_cache", _raise_save)
    scan_tab._save_scan_cache([])

    applied = []
    background_calls = []

    class _CacheLifecycle:
        def run_background(self, name, fn, **kwargs):
            background_calls.append((name, fn, kwargs))

        def shutdown(self, *, timeout_ms):
            return timeout_ms >= 0

    class _Token:
        def raise_if_cancelled(self):
            return None

    scan_tab._task_lifecycle = _CacheLifecycle()
    scan_tab._apply_scan_cache_payload = lambda payload, rows: applied.append((payload, rows))
    monkeypatch.setattr(scan_module, "load_scan_cache", lambda: ({"results": [{"代码": "000001"}]}, True))
    monkeypatch.setattr(scan_tab, "isVisible", lambda: True)

    assert scan_tab._load_scan_cache() is True
    assert applied == []
    task_name, background_fn, submit_kwargs = background_calls.pop()
    assert task_name == "scan_cache_load"
    submit_kwargs["on_success"](background_fn(_Token()))
    assert applied[0][1][0]["代码"] == "000001"

    scan_tab._on_scan_cache_loaded(([], False))
    scan_tab._on_scan_cache_loaded(({"results": []}, False))
    submit_kwargs["on_error"]("broken")
    assert len(system_log) >= 2

    monkeypatch.setattr(scan_tab, "isVisible", lambda: False)
    scan_tab._on_scan_cache_loaded(({"results": [{"代码": "000002"}]}, False))
    assert scan_tab._scan_cache_preload.deferred_payload is None
    assert len(applied) == 2
    assert applied[-1][1] == [{"代码": "000002"}]
    monkeypatch.setattr(scan_tab, "isVisible", lambda: True)
    scan_tab._apply_deferred_scan_cache()
    assert applied[-1][1] == [{"代码": "000002"}]
    assert scan_tab._scan_cache_preload.deferred_payload is None

    rendered = []
    scan_tab._refresh_scan_result_names = lambda rows: list(rows)
    scan_tab._render_scan_table = lambda rows: rendered.append(list(rows))
    real_apply = ScanTab._apply_scan_cache_payload.__get__(scan_tab)
    real_apply(
        {
            "saved_at": "2026-07-14T10:00:00",
            "params": {"rps": 90, "amp": 0.5, "ma_bind": 0.06},
        },
        [{"代码": "000001"}],
    )
    assert rendered == [[{"代码": "000001"}]]
    before_errors = len(system_log)
    real_apply(None, [])
    assert len(system_log) == before_errors + 1


def test_scan_kline_handlers_and_context_menu(monkeypatch, scan_tab):
    scan_tab._render_scan_table(
        [
            {"代码": "000001", "名称": "平安银行", "收盘": 10, "触发日期": "2026-07-14"},
            {"代码": "000002", "名称": "万科A", "收盘": 5, "触发日期": "2026-07-13"},
        ]
    )
    with_list = QSignalSpy(event_bus.sig_show_kline_with_list)
    index = scan_tab.proxy_model.index(0, scan_tab.source_model.headers.index("代码"))
    scan_tab._handle_show_kline(index)
    assert len(with_list) == 1
    assert len(with_list[0][1]) == 2
    scan_tab._handle_show_kline(None)

    filter_calls = []
    scan_tab.set_proxy_filter_text = lambda model, text: filter_calls.append((model, text))
    scan_tab._on_search_text_changed("银行")
    assert filter_calls == [(scan_tab.proxy_model, "银行")]

    menu_calls = []
    monkeypatch.setattr(scan_tab.table_scan, "indexAt", lambda _pos: index)
    monkeypatch.setattr(
        "ui.components.stock_context_menu.build_stock_context_menu",
        lambda *args, **kwargs: menu_calls.append((args, kwargs)),
    )
    scan_tab._show_context_menu(object())
    assert menu_calls[0][1]["vcp_data"]["代码"] in {"000001", "000002"}

    single = QSignalSpy(event_bus.sig_show_kline)

    class _DetachedModel:
        def rowCount(self):
            return 0

        def index(self, row, column):
            return (row, column)

        def data(self, index, _role):
            return "000999" if index[1] == scan_tab.source_model.headers.index("代码") else "测试"

    class _DetachedIndex:
        def isValid(self):
            return True

        def model(self):
            return _DetachedModel()

        def row(self):
            return 9

    scan_tab._handle_show_kline(_DetachedIndex())
    assert single[-1][0] == "000999"

    invalid = scan_tab.source_model.index(-1, -1)
    monkeypatch.setattr(scan_tab.table_scan, "indexAt", lambda _pos: invalid)
    scan_tab._show_context_menu(object())
