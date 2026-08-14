# -*- coding: utf-8 -*-
import json
from pathlib import Path

import pytest
from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QColor
from PyQt6.QtTest import QSignalSpy

from core.event_bus import event_bus
from core.global_store import global_store
from core.quote_dispatcher import publish_rt_quotes
from core.task_manager import task_manager
from ui.services import na_daily_service as na_daily_service_module
from ui.services.na_daily_service import NADailyRefreshService
from ui.tabs import na_daily_tab as na_daily_tab_module
from ui.tabs.na_daily_tab import NADailyTab
from ui.theme import theme_manager


@pytest.fixture(autouse=True)
def _isolate_na_daily_cache(monkeypatch, tmp_path):
    monkeypatch.setattr(na_daily_service_module, "NA_DAILY_CACHE_FILE", str(tmp_path / "na_daily_latest.json"))


class DummyProvider:
    def __init__(self, response=None):
        self.response = response or {}
        self.calls = []

    def fetch_realtime_quotes_batch(self, codes):
        self.calls.append(list(codes))
        return dict(self.response)


class DummyQuotePublisher:
    def publish_external_quotes(self, payload, *, source: str, require_valid: bool = False):
        return publish_rt_quotes(payload, source=source, require_valid=require_valid)


def _build_tab(monkeypatch, provider):
    monkeypatch.setattr(QTimer, "singleShot", lambda *args, **kwargs: None)
    tab = NADailyTab(provider)
    tab._quote_publisher = DummyQuotePublisher()
    assert not hasattr(tab, "_patrol_timer")
    monkeypatch.setattr(
        tab,
        "_load_cached_finance_snapshot",
        staticmethod(lambda codes: {}),
        raising=False,
    )
    return tab


def test_na_daily_show_runtime_skips_non_interactive_load_reason():
    class DummyTab:
        _workspace_load_reason = "screenshot"
        _workspace_noninteractive_loaded = True

        def _is_current_workspace_tab(self):
            return True

    dummy = DummyTab()
    assert not NADailyTab._should_start_runtime_on_show(dummy)
    assert dummy._workspace_noninteractive_loaded is True

    dummy._workspace_load_reason = "tab_switch"
    assert NADailyTab._should_start_runtime_on_show(dummy)
    assert dummy._workspace_noninteractive_loaded is False


def test_na_daily_service_uses_sibling_daily_report_output_dir():
    service = NADailyRefreshService()
    expected = Path(service._project_root()).parent / "每日战报" / "每日热点输出"
    assert Path(service._get_na_daily_output_dir()) == expected


def test_na_daily_service_no_report_files_preserves_existing_cache(monkeypatch, tmp_path):
    cache_file = tmp_path / "na_daily_latest.json"
    monkeypatch.setattr(na_daily_service_module, "NA_DAILY_CACHE_FILE", str(cache_file))

    service = NADailyRefreshService()
    service._rows = [{"代码": "000001", "日报时间": "20260415"}]
    service._report_files = ["D:/reports/战报_202604150930.md"]
    service._report_signature = ("sig",)
    monkeypatch.setattr(service, "_build_na_daily_rows", lambda: ([], [], ()))

    result = service.refresh_full(emit_event=False)

    assert result["status"] == "skipped"
    assert result["records"] == 1
    assert service.rows == [{"代码": "000001", "日报时间": "20260415"}]
    assert not cache_file.exists()


def test_na_daily_tab_refresh_table_market_data_only_fetches_blank_quotes(monkeypatch):
    provider = DummyProvider(
        {
            "000003": {"close": 30.0, "last_close": 29.0},
        }
    )
    monkeypatch.setattr(
        global_store,
        "get_latest_quotes",
        lambda: {
            "000001": {"close": 10.5, "last_close": 10.0},
            "000002": {"close": 20.0, "last_close": 20.0},
        },
    )
    monkeypatch.setattr(task_manager, "is_active_task", lambda task_id: False)
    monkeypatch.setattr(
        task_manager,
        "run_in_background",
        lambda func, on_success=None, on_error=None, task_id=None, **_kwargs: on_success(func()),
    )

    tab = _build_tab(monkeypatch, provider)
    try:
        tab._na_daily_codes = {"000001", "000002", "000003"}
        tab.model.update_data(
            [
                {"代码": "000001", "名称": "A", "现价": "--", "涨幅%": "--", "市值": "--"},
                {"代码": "000002", "名称": "B", "现价": "20.00", "涨幅%": 0.0, "市值": "--"},
                {"代码": "000003", "名称": "C", "现价": "--", "涨幅%": "--", "市值": "--"},
            ]
        )

        assert tab.model.row_data[0]["现价"] == "10.50"
        assert tab._collect_quote_refresh_codes(force=False) == ["000003"]

        spy = QSignalSpy(event_bus.sig_rt_quotes)
        monkeypatch.setattr(tab, "async_update_market_caps", lambda: None)
        tab.refresh_table_quotes_and_market_caps(quote_task_id="na_daily_quotes")

        assert provider.calls == [["000003"]]
        assert len(spy) == 1
        payload = spy[0][0]
        assert payload["000003"]["close"] == 30.0
    finally:
        tab.close()
        tab.deleteLater()


def test_na_daily_tab_apply_rows_triggers_cap_and_quote_refresh(monkeypatch, tmp_path):
    provider = DummyProvider()
    monkeypatch.setattr(global_store, "get_latest_quotes", lambda: {})

    tab = _build_tab(monkeypatch, provider)
    refresh_calls = []
    monkeypatch.setattr(
        tab,
        "refresh_table_quotes_and_market_caps",
        lambda **kwargs: refresh_calls.append(kwargs),
    )

    report_file = Path(tmp_path) / "战报_202604150930.md"
    report_file.write_text("# test\n", encoding="utf-8")

    try:
        tab._apply_na_daily_rows(
            [
                {
                    "代码": "000001",
                    "名称": "A",
                    "现价": "--",
                    "涨幅%": "--",
                    "市值": "--",
                    "日报时间": "20260415",
                    "细分板块": "",
                    "股价弹性": "",
                    "催化剂": "",
                    "风控": "",
                    "评级": "",
                    "_report_ts": 20260415093000,
                    "_report_row_rank": 0,
                }
            ],
            [str(report_file)],
            ("sig",),
        )

        assert tab._na_daily_codes == {"000001"}
        assert refresh_calls == [{"quote_task_id": "na_daily_quotes"}]
    finally:
        tab.close()
        tab.deleteLater()


def test_na_daily_history_browser_keeps_latest_snapshot_and_marks_missing_dates(monkeypatch, tmp_path):
    provider = DummyProvider()
    tab = _build_tab(monkeypatch, provider)
    monkeypatch.setattr(tab, "_refresh_history_index", lambda: None, raising=False)

    latest_file = Path(tmp_path) / "战报_20260814083002.md"
    latest_file.write_text("# latest\n", encoding="utf-8")
    latest_row = {
        "代码": "000001",
        "名称": "最新标的",
        "现价": "--",
        "涨幅%": "--",
        "市值": "--",
        "日报时间": "20260814",
        "细分板块": "最新",
        "股价弹性": "",
        "催化剂": "最新催化",
        "风控": "",
        "评级": "",
        "_report_ts": 20260814083002,
        "_report_row_rank": 0,
    }
    history_row = {**latest_row, "代码": "000002", "名称": "历史标的", "日报时间": "20260810", "催化剂": "历史催化"}

    try:
        tab._apply_na_daily_rows([latest_row], [str(latest_file)], ("latest",), emit_event=False, refresh_quotes=False)
        tab._na_daily_service._rows = [dict(latest_row)]
        tab._na_daily_service._report_files = [str(latest_file)]

        dialog = tab._show_history_browser()
        dialog.set_history_entries(
            [
                {"date": "20260814", "state": "available", "report_files": [str(latest_file)]},
                {
                    "date": "20260813",
                    "state": "missing",
                    "report_files": [],
                    "manifest_status": "failed_exception",
                    "message": "no meaningful upstream evidence",
                },
                {"date": "20260810", "state": "available", "report_files": ["D:/reports/战报_20260810083002.md"]},
            ]
        )
        missing_index = dialog.date_combo.findData("20260813")
        assert "缺失" in dialog.date_combo.itemText(missing_index)

        spy = QSignalSpy(event_bus.sig_na_daily_updated)
        tab._apply_history_payload(
            {
                "status": "success",
                "report_date": "20260810",
                "rows": [history_row],
                "report_files": ["D:/reports/战报_20260810083002.md"],
            }
        )

        assert dialog.model.row_data == [history_row]
        assert tab.model.row_data == [latest_row]
        assert tab.get_row_data() == [latest_row]
        assert tab._na_daily_codes == {"000001"}
        assert tab._na_daily_service.rows == [latest_row]
        assert len(spy) == 0

        tab._apply_history_payload(
            {
                "status": "missing",
                "report_date": "20260813",
                "message": "no meaningful upstream evidence",
                "rows": [],
                "report_files": [],
            }
        )

        assert dialog.model.row_data == []
        assert "2026-08-13" in dialog.history_status_label.text()
        assert "未生成战报" in dialog.history_status_label.text()
        assert tab.model.row_data == [latest_row]
        assert tab.get_row_data() == [latest_row]
        assert len(spy) == 0

        tab._apply_history_payload(
            {
                "status": "error",
                "report_date": "20260810",
                "message": "读取失败",
                "rows": [],
                "report_files": [],
            }
        )

        assert "历史战报加载失败" in dialog.history_status_label.text()
        assert tab.model.row_data == [latest_row]
        assert len(spy) == 0
    finally:
        dialog = getattr(tab, "_history_dialog", None)
        if dialog is not None:
            dialog.close()
        tab.close()
        tab.deleteLater()


def test_na_daily_history_browser_wires_background_selection_and_discards_stale_results(monkeypatch, tmp_path):
    class QueuedRunner:
        def __init__(self):
            self.jobs = []
            self.cancellations = []

        def run_in_background(self, fn, **kwargs):
            self.jobs.append((fn, dict(kwargs)))
            return str(kwargs["task_id"])

        def abandon_task(self, task_id):
            return bool(task_id)

        def cancel_task(self, task_id, *, reason="cancelled"):
            self.cancellations.append((str(task_id), reason))
            return True

        @staticmethod
        def wait_for_tasks(_task_ids, *, timeout_ms):
            return timeout_ms >= 0

        @staticmethod
        def is_task_unsettled(_task_id):
            return False

    def write_report(date, code, catalyst):
        report_dir = tmp_path / date
        report_dir.mkdir(exist_ok=True)
        report_file = report_dir / f"战报_{date}083002.md"
        report_file.write_text("# structured sidecar\n", encoding="utf-8")
        report_file.with_suffix(".json").write_text(
            json.dumps(
                {
                    "sniper_tables": [
                        {
                            "track_name": "历史接线测试",
                            "targets": [{"name": f"标的{code}", "code": code, "catalyst": catalyst, "risk": "🟢"}],
                        }
                    ],
                    "today_advice": [{"code": code, "priority": "P1"}],
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        return report_file

    runner = QueuedRunner()
    provider = DummyProvider()
    tab = _build_tab(monkeypatch, provider)
    monkeypatch.setattr(na_daily_tab_module, "task_manager", runner)
    if hasattr(tab, "_task_lifecycle"):
        delattr(tab, "_task_lifecycle")
    monkeypatch.setattr(tab._na_daily_service, "_get_na_daily_output_dir", lambda: str(tmp_path))

    latest_file = write_report("20260814", "000001", "最新催化")
    write_report("20260810", "000002", "历史催化")
    missing_dir = tmp_path / "20260813"
    missing_dir.mkdir()
    (missing_dir / "run_manifest.json").write_text(
        json.dumps({"status": "failed_exception", "status_reason": "no meaningful upstream evidence"}),
        encoding="utf-8-sig",
    )
    non_trading_dir = tmp_path / "20260809"
    non_trading_dir.mkdir()
    (non_trading_dir / "run_manifest.json").write_text(
        json.dumps({"status": "skipped_non_trading_day"}),
        encoding="utf-8-sig",
    )
    latest_row = {
        "代码": "000001",
        "名称": "最新标的",
        "现价": "--",
        "涨幅%": "--",
        "市值": "--",
        "日报时间": "20260814",
        "细分板块": "最新",
        "股价弹性": "",
        "催化剂": "最新催化",
        "风控": "",
        "评级": "P1",
        "_report_ts": 20260814083002,
        "_report_row_rank": 0,
    }

    try:
        tab._na_daily_service.apply_refresh_payload(
            {
                "status": "success",
                "rows": [latest_row],
                "report_files": [str(latest_file)],
                "report_signature": ("latest",),
            },
            emit_event=False,
        )
        tab._apply_na_daily_rows([latest_row], [str(latest_file)], ("latest",), emit_event=False, refresh_quotes=False)
        cache_path = Path(na_daily_service_module.NA_DAILY_CACHE_FILE)
        cache_before = cache_path.read_text(encoding="utf-8")
        spy = QSignalSpy(event_bus.sig_na_daily_updated)

        dialog = tab._show_history_browser()
        assert len(runner.jobs) == 1
        index_fn, index_kwargs = runner.jobs[0]
        assert index_kwargs["task_id"] == na_daily_tab_module._NA_DAILY_HISTORY_INDEX_TASK
        assert index_kwargs["timeout_sec"] == 30
        index_kwargs["on_success"](index_fn())

        history_index = dialog.date_combo.findData("20260810")
        missing_index = dialog.date_combo.findData("20260813")
        non_trading_index = dialog.date_combo.findData("20260809")
        assert history_index > 0
        assert "缺失" in dialog.date_combo.itemText(missing_index)
        assert "非交易日" in dialog.date_combo.itemText(non_trading_index)

        dialog.date_combo.setCurrentIndex(history_index)
        assert len(runner.jobs) == 2
        stale_load_fn, stale_load_kwargs = runner.jobs[1]
        assert stale_load_kwargs["task_id"] == na_daily_tab_module._NA_DAILY_HISTORY_LOAD_TASK
        assert stale_load_kwargs["timeout_sec"] == 30
        stale_payload = stale_load_fn()

        tab._refresh_history_index()
        assert len(runner.jobs) == 3
        assert any(reason == "history_index_refresh" for _task_id, reason in runner.cancellations)
        refreshed_index_fn, refreshed_index_kwargs = runner.jobs[2]
        refreshed_index_kwargs["on_success"](refreshed_index_fn())
        assert dialog.date_combo.currentData() == ""
        assert dialog.model.row_data == []
        stale_load_kwargs["on_success"](stale_payload)
        assert dialog.model.row_data == []

        dialog.date_combo.setCurrentIndex(history_index)
        assert len(runner.jobs) == 4
        history_load_fn, history_load_kwargs = runner.jobs[3]
        history_load_kwargs["on_success"](history_load_fn())
        assert [(row["代码"], row["日报时间"]) for row in dialog.model.row_data] == [("000002", "20260810")]
        assert tab.model.row_data == [latest_row]
        assert tab.get_row_data() == [latest_row]
        assert tab._na_daily_service.rows == [latest_row]
        assert len(spy) == 0
        assert cache_path.read_text(encoding="utf-8") == cache_before

        dialog.date_combo.setCurrentIndex(missing_index)
        assert len(runner.jobs) == 5
        missing_load_fn, missing_load_kwargs = runner.jobs[4]
        missing_load_kwargs["on_success"](missing_load_fn())
        assert dialog.model.row_data == []
        assert "2026-08-13" in dialog.history_status_label.text()
        assert "未生成战报" in dialog.history_status_label.text()
        assert tab.model.row_data == [latest_row]
        assert tab._na_daily_service.rows == [latest_row]
        assert len(spy) == 0
        assert cache_path.read_text(encoding="utf-8") == cache_before

        dialog.date_combo.setCurrentIndex(history_index)
        late_load_fn, late_load_kwargs = runner.jobs[5]
        late_payload = late_load_fn()
        tab.shutdown()
        assert not dialog.isVisible()
        late_load_kwargs["on_success"](late_payload)
        assert tab.model.row_data == [latest_row]
        assert tab._na_daily_service.rows == [latest_row]
        assert len(spy) == 0
        assert cache_path.read_text(encoding="utf-8") == cache_before
    finally:
        dialog = getattr(tab, "_history_dialog", None)
        if dialog is not None:
            dialog.close()
        tab.close()
        tab.deleteLater()


def test_na_daily_report_time_column_uses_muted_text(monkeypatch):
    provider = DummyProvider()
    tab = _build_tab(monkeypatch, provider)
    try:
        tab.model.update_data(
            [
                {
                    "代码": "000001",
                    "名称": "A",
                    "现价": "--",
                    "涨幅%": "--",
                    "市值": "--",
                    "日报时间": "20260415",
                    "细分板块": "",
                    "股价弹性": "",
                    "催化剂": "",
                    "风控": "",
                    "评级": "",
                }
            ]
        )

        muted = QColor(theme_manager.get("TEXT_MUTED")).name()
        report_time_col = tab.model.headers.index("日报时间")
        idx = tab.model.index(0, report_time_col)
        assert tab.model.data(idx, Qt.ItemDataRole.ForegroundRole).name() == muted
    finally:
        tab.close()
        tab.deleteLater()


def test_na_daily_prime_background_load_schedules_rows_without_ui_thread_parse(monkeypatch, tmp_path):
    provider = DummyProvider()
    monkeypatch.setattr(global_store, "get_latest_quotes", lambda: {})

    tab = _build_tab(monkeypatch, provider)
    refresh_calls = []
    snapshot_calls = []
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

    report_file = Path(tmp_path) / "战报_202604150930.md"
    report_file.write_text("# test\n", encoding="utf-8")
    rows = [
        {
            "代码": "000001",
            "名称": "A",
            "现价": "--",
            "涨幅%": "--",
            "市值": "--",
            "日报时间": "20260415",
            "细分板块": "先进封装",
            "股价弹性": "",
            "催化剂": "北美催化",
            "风控": "",
            "评级": "",
            "_report_ts": 20260415093000,
            "_report_row_rank": 0,
        }
    ]

    monkeypatch.setattr(
        tab._na_daily_service,
        "refresh_full",
        lambda **kwargs: (_ for _ in ()).throw(AssertionError("refresh_full must not run in UI thread")),
    )
    monkeypatch.setattr(
        na_daily_tab_module,
        "build_na_daily_refresh_payload",
        lambda output_dir, *, limit=5: {
            "status": "success",
            "rows": rows,
            "report_files": [str(report_file)],
            "report_signature": ("sig",),
            "records": 1,
        },
    )
    scheduled = {}

    class FakeTaskRunner:
        def is_active_task(self, task_id):
            return False

        def run_in_background(self, fn, *, task_id=None, on_success=None, on_error=None):
            scheduled["fn"] = fn
            scheduled["task_id"] = task_id
            scheduled["on_success"] = on_success
            scheduled["on_error"] = on_error
            return str(task_id)

    monkeypatch.setattr(na_daily_tab_module, "task_manager", FakeTaskRunner())

    try:
        assert tab.is_background_preload_complete() is False
        tab.prime_background_load()

        assert tab._runtime_started is True
        assert tab._background_prime_done is False
        assert tab._background_prime_loading is True
        assert tab.is_background_preload_complete() is False
        assert len(tab.model.row_data) == 0
        assert scheduled["task_id"] == na_daily_tab_module._NA_DAILY_REFRESH_TASK

        scheduled["on_success"](scheduled["fn"]())

        assert tab._background_prime_done is True
        assert tab._background_prime_loading is False
        assert tab.is_background_preload_complete() is True
        assert len(tab.model.row_data) == 1
        assert tab._na_daily_codes == {"000001"}
        assert not hasattr(tab, "_patrol_timer")
        assert refresh_calls == []
        assert len(snapshot_calls) == 1
    finally:
        tab.close()
        tab.deleteLater()


def test_na_daily_background_prime_reloads_local_rows_after_early_interactive_start(monkeypatch):
    tab = _build_tab(monkeypatch, DummyProvider())
    calls = []
    tab._runtime_started = True
    monkeypatch.setattr(tab, "_load_na_daily_report", lambda: calls.append("load") or True)

    try:
        assert tab.prime_background_load() is True
        assert calls == ["load"]
        assert tab._background_prime_loading is True
    finally:
        tab.close()
        tab.deleteLater()


def test_na_daily_foreground_runtime_timer_runs_exactly_once(qt_application, monkeypatch):
    tab = _build_tab(monkeypatch, DummyProvider())
    calls = []
    tab._runtime_start_delay_ms = 0
    monkeypatch.setattr(tab, "_load_na_daily_report", lambda: calls.append("load") or True)

    try:
        tab._ensure_runtime_started()
        tab._ensure_runtime_started()
        qt_application.processEvents()
        qt_application.processEvents()

        assert calls == ["load"]
        assert tab._runtime_start_timer.isActive() is False
    finally:
        tab.shutdown()
        tab.deleteLater()


def test_na_daily_background_prime_cancels_queued_foreground_read(qt_application, monkeypatch):
    tab = _build_tab(monkeypatch, DummyProvider())
    calls = []
    tab._runtime_start_delay_ms = 60_000
    monkeypatch.setattr(tab, "_load_na_daily_report", lambda: calls.append("load") or True)

    try:
        tab._ensure_runtime_started()
        assert tab._runtime_start_timer.isActive() is True

        assert tab.prime_background_load() is True
        qt_application.processEvents()

        assert calls == ["load"]
        assert tab._runtime_start_timer.isActive() is False
    finally:
        tab.shutdown()
        tab.deleteLater()


def test_na_daily_shutdown_cancels_queued_runtime_read(qt_application, monkeypatch):
    tab = _build_tab(monkeypatch, DummyProvider())
    calls = []
    tab._runtime_start_delay_ms = 0
    monkeypatch.setattr(tab, "_load_na_daily_report", lambda: calls.append("load") or True)

    tab._ensure_runtime_started()
    tab.shutdown()
    qt_application.processEvents()
    qt_application.processEvents()

    assert calls == []
    assert tab._runtime_start_timer.isActive() is False
    tab.deleteLater()
