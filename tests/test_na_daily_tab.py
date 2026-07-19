# -*- coding: utf-8 -*-
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
