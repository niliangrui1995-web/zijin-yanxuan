# -*- coding: utf-8 -*-
import datetime as dt
import json

import pytest
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import QApplication

from core.market_calendar import MarketCalendar
from ui.models.table_models import _c
from ui.tabs import asian_market_runtime as asian_runtime
from ui.tabs import asian_market_tab as asian_module
from ui.tabs.asian_market_meta import (
    format_market_display,
)
from ui.tabs.asian_market_meta import (
    get_market_status as get_asian_market_status,
)
from ui.tabs.asian_market_workers import infer_asian_markets, is_asian_quote_refresh_time


class _Signal:
    def connect(self, _callback):
        return None


class _LabelStub:
    def __init__(self):
        self.text = ""

    def setText(self, text):
        self.text = text


class _DummyWorker:
    def __init__(self, codes):
        self.codes = list(codes)
        self.progress = _Signal()
        self.result_ready = _Signal()
        self.finished = _Signal()

    def start(self):
        return None

    def isRunning(self):
        return False

    def stop(self):
        return None

    def wait(self, _timeout=0):
        return True

    def trigger_refresh(self):
        return None


class _CacheThreadStub:
    def __init__(self, running=False):
        self._running = running

    def isRunning(self):
        return self._running


class _SettingsStub:
    def __init__(self, values=None):
        self.values = dict(values or {})
        self.synced = False

    def value(self, key, default=None, type=None):
        return self.values.get(key, default)

    def setValue(self, key, value):
        self.values[key] = value

    def contains(self, key):
        return key in self.values

    def sync(self):
        self.synced = True


@pytest.fixture(autouse=True)
def _disable_saved_asian_header_state(monkeypatch):
    monkeypatch.setattr(
        asian_module.AsianMarketTab,
        "_has_saved_asian_header_state",
        lambda self, settings_key: False,
        raising=False,
    )


def _install_immediate_local_cache_runner(monkeypatch):
    class FakeTaskRunner:
        def run_in_background(self, fn, *, task_id=None, on_success=None, on_error=None):
            try:
                result = fn()
            except Exception as exc:
                if on_error is not None:
                    on_error(str(exc))
            else:
                if on_success is not None:
                    on_success(result)
            return str(task_id)

    monkeypatch.setattr(asian_module, "task_manager", FakeTaskRunner())


def _build_asian_tab_for_view_tests(monkeypatch, settings=None):
    settings = settings or _SettingsStub()
    monkeypatch.setattr(asian_module, "AsianMarketWorker", _DummyWorker)
    monkeypatch.setattr(
        asian_module.AsianMarketTab,
        "_load_local_cache",
        lambda self: setattr(self, "row_data", []),
    )
    monkeypatch.setattr(asian_module.AsianMarketTab, "_check_auto_cache", lambda self: None)
    monkeypatch.setattr(
        asian_module.AsianMarketTab,
        "_settings_section",
        lambda self: settings,
        raising=False,
    )
    monkeypatch.setattr(
        asian_module.AsianMarketTab,
        "bind_header_persistence",
        lambda self, table, settings_key="header_state": None,
        raising=False,
    )
    return asian_module.AsianMarketTab(), settings


def test_infer_asian_markets_normalizes_from_code_suffixes():
    assert infer_asian_markets(["0522.HK", "3324.TWO", "8035.T", "000660.KS"]) == [
        "HK",
        "TW",
        "T",
        "KS",
    ]


def test_asian_market_show_runtime_skips_non_interactive_load_reason():
    class DummyTab:
        _workspace_load_reason = "screenshot"
        _workspace_noninteractive_loaded = True

        def _is_current_workspace_tab(self):
            return True

    dummy = DummyTab()
    assert not asian_module.AsianMarketTab._should_start_runtime_on_show(dummy)
    assert dummy._workspace_noninteractive_loaded is True

    dummy._workspace_load_reason = "tab_switch"
    assert asian_module.AsianMarketTab._should_start_runtime_on_show(dummy)
    assert dummy._workspace_noninteractive_loaded is False


def test_asian_quote_refresh_uses_tracked_market_union(monkeypatch):
    monkeypatch.setattr(
        MarketCalendar,
        "is_quote_refresh_time",
        classmethod(lambda cls, market="CN": market == "HK"),
    )

    assert is_asian_quote_refresh_time(["0522.HK"]) is True
    assert is_asian_quote_refresh_time(["8035.T"]) is False


def test_asian_auto_cache_checks_stale_trade_dates_per_market(monkeypatch):
    class _DummyTab:
        def __init__(self):
            self._is_fetching_cache = False
            self._pending_auto_cache_sync = False
            self._cache_sync_wait_deadline = None
            self.worker = None
            self.runtime_state = None
            self.lbl_status = _LabelStub()

        def _get_cache_latest_trade_dates(self):
            return {
                "TW": dt.date(2026, 4, 17),
                "T": dt.date(2026, 4, 17),
                "KS": dt.date(2026, 4, 20),
                "HK": dt.date(2026, 4, 20),
            }

        def _get_expected_latest_trade_dates(self):
            return {
                "TW": dt.date(2026, 4, 20),
                "T": dt.date(2026, 4, 20),
                "KS": dt.date(2026, 4, 20),
                "HK": dt.date(2026, 4, 20),
            }

        def _get_cache_latest_trade_date(self):
            return dt.date(2026, 4, 20)

        def _get_expected_latest_trade_date(self):
            return dt.date(2026, 4, 20)

        def _set_runtime_state(self, state):
            self.runtime_state = state

        def _continue_auto_cache_sync(self):
            return None

    monkeypatch.setattr(
        MarketCalendar,
        "now",
        classmethod(lambda cls, market="CN": dt.datetime(2026, 4, 20, 17, 0)),
    )
    monkeypatch.setattr(
        MarketCalendar,
        "from_timestamp",
        classmethod(lambda cls, ts, market="CN": dt.datetime(2026, 4, 20, 14, 17)),
    )
    monkeypatch.setattr(asian_runtime.os.path, "exists", lambda path: True)
    monkeypatch.setattr(asian_runtime.os.path, "getmtime", lambda path: 1.0)
    monkeypatch.setattr(asian_runtime.QTimer, "singleShot", staticmethod(lambda *args, **kwargs: None))

    tab = _DummyTab()

    asian_runtime.check_auto_cache(tab)

    assert tab._pending_auto_cache_sync is True
    assert tab.runtime_state == "paused_for_cache_sync"


def test_asian_market_table_scales_columns_to_fill_view(monkeypatch):
    monkeypatch.setattr(asian_module, "AsianMarketWorker", _DummyWorker)
    monkeypatch.setattr(
        asian_module.AsianMarketTab,
        "_load_local_cache",
        lambda self: setattr(self, "row_data", []),
    )
    monkeypatch.setattr(asian_module.AsianMarketTab, "_check_auto_cache", lambda self: None)
    monkeypatch.setattr(
        asian_module.AsianMarketTab,
        "bind_header_persistence",
        lambda self, table, settings_key="header_state": None,
        raising=False,
    )

    tab = asian_module.AsianMarketTab()
    try:
        assert not hasattr(tab, "auto_cache_timer")
        tab.resize(1200, 720)
        tab.show()
        tab._fit_asian_columns_to_viewport()

        column_count = tab.asian_table.model().columnCount()
        initial_widths = [52, 70, 140, 90, 80, 90, 80, 80, 120, 250, 60, 80, 80, 80]
        total_initial = sum(initial_widths)
        total_scaled = sum(tab.asian_table.columnWidth(i) for i in range(column_count))
        viewport_width = tab.asian_table.viewport().width()

        assert column_count == len(initial_widths)
        assert abs(total_scaled - viewport_width) <= column_count

        scaled_ratio = tab.asian_table.columnWidth(9) / tab.asian_table.columnWidth(1)
        initial_ratio = initial_widths[9] / initial_widths[1]
        assert abs(scaled_ratio - initial_ratio) < 0.2
        assert total_scaled != total_initial
    finally:
        tab.deleteLater()


def test_asian_market_table_suppresses_left_rails(monkeypatch):
    monkeypatch.setattr(asian_module, "AsianMarketWorker", _DummyWorker)
    monkeypatch.setattr(
        asian_module.AsianMarketTab,
        "_load_local_cache",
        lambda self: setattr(self, "row_data", []),
    )
    monkeypatch.setattr(asian_module.AsianMarketTab, "_check_auto_cache", lambda self: None)
    monkeypatch.setattr(
        asian_module.AsianMarketTab,
        "bind_header_persistence",
        lambda self, table, settings_key="header_state": None,
        raising=False,
    )

    tab = asian_module.AsianMarketTab()
    try:
        assert tab.asian_table.property("suppressLeftRails") is True
        assert tab.asian_table.property("simpleCellPaint") is True
    finally:
        tab.deleteLater()


def test_asian_market_table_keeps_saved_column_widths(monkeypatch):
    monkeypatch.setattr(asian_module, "AsianMarketWorker", _DummyWorker)
    monkeypatch.setattr(
        asian_module.AsianMarketTab,
        "_load_local_cache",
        lambda self: setattr(self, "row_data", []),
    )
    monkeypatch.setattr(asian_module.AsianMarketTab, "_check_auto_cache", lambda self: None)
    monkeypatch.setattr(
        asian_module.AsianMarketTab,
        "_has_saved_asian_header_state",
        lambda self, settings_key: True,
        raising=False,
    )

    saved_widths = [66, 88, 126, 164, 102, 94, 112, 86, 118, 176, 132, 96, 108, 124]

    def fake_bind_header_persistence(self, table, settings_key="header_state"):
        for column, width in enumerate(saved_widths):
            if column >= table.model().columnCount():
                break
            table.setColumnWidth(column, width)
        return None

    monkeypatch.setattr(
        asian_module.AsianMarketTab,
        "bind_header_persistence",
        fake_bind_header_persistence,
        raising=False,
    )

    tab = asian_module.AsianMarketTab()
    try:
        tab.resize(1200, 720)
        tab.show()
        QApplication.processEvents()

        column_count = tab.asian_table.model().columnCount()
        actual_widths = [tab.asian_table.columnWidth(i) for i in range(column_count)]

        assert tab._auto_fit_columns_pending is False
        assert actual_widths == saved_widths[:column_count]
    finally:
        tab.deleteLater()


def test_asian_market_toolbar_keeps_search_and_refresh(monkeypatch):
    monkeypatch.setattr(asian_module, "AsianMarketWorker", _DummyWorker)
    monkeypatch.setattr(
        asian_module.AsianMarketTab,
        "_load_local_cache",
        lambda self: setattr(self, "row_data", []),
    )
    monkeypatch.setattr(asian_module.AsianMarketTab, "_check_auto_cache", lambda self: None)
    monkeypatch.setattr(
        asian_module.AsianMarketTab,
        "bind_header_persistence",
        lambda self, table, settings_key="header_state": None,
        raising=False,
    )

    tab = asian_module.AsianMarketTab()
    try:
        assert tab.search_box.property("inToolbar") is True
        assert tab.btn_refresh.property("inToolbar") is True
        assert tab.asian_table.property("ambientPulse") is False
    finally:
        tab.deleteLater()


def test_asian_market_orders_pinned_codes_before_unpinned_rows(monkeypatch):
    tab, _settings = _build_asian_tab_for_view_tests(
        monkeypatch,
        _SettingsStub({asian_module.ASIAN_PINNED_CODES_SETTINGS_KEY: ["0522.HK", "2330.TW"]}),
    )
    try:
        tab.row_data = [
            {"代码": "8035.T", "名称": "Tokyo Electron"},
            {"代码": "2330.TW", "名称": "TSMC"},
            {"代码": "0522.HK", "名称": "ASMPT"},
        ]

        tab.update_table_ui()

        assert [row["代码"] for row in tab.model.row_data] == ["0522.HK", "2330.TW", "8035.T"]
        assert [row["代码"] for row in tab.row_data] == ["8035.T", "2330.TW", "0522.HK"]
    finally:
        tab.deleteLater()


def test_asian_market_pin_and_unpin_persist_and_refresh_table(monkeypatch):
    tab, settings = _build_asian_tab_for_view_tests(monkeypatch)
    try:
        tab.row_data = [
            {"代码": "8035.T", "名称": "Tokyo Electron"},
            {"代码": "2330.TW", "名称": "TSMC"},
            {"代码": "0522.HK", "名称": "ASMPT"},
        ]
        tab.update_table_ui()

        tab._pin_asian_code_to_top("2330.TW")

        assert settings.values[asian_module.ASIAN_PINNED_CODES_SETTINGS_KEY] == ["2330.TW"]
        assert settings.synced is True
        assert [row["代码"] for row in tab.model.row_data] == ["2330.TW", "8035.T", "0522.HK"]
        assert tab._build_asian_pin_action("2330.TW")[0] == "取消置顶"

        tab._unpin_asian_code("2330.TW")

        assert settings.values[asian_module.ASIAN_PINNED_CODES_SETTINGS_KEY] == []
        assert [row["代码"] for row in tab.model.row_data] == ["8035.T", "2330.TW", "0522.HK"]
        assert tab._build_asian_pin_action("2330.TW")[0] == "置顶"
    finally:
        tab.deleteLater()


@pytest.mark.parametrize("sort_order", [Qt.SortOrder.AscendingOrder, Qt.SortOrder.DescendingOrder])
def test_asian_market_pin_stays_first_when_table_is_sorted(monkeypatch, sort_order):
    tab, _settings = _build_asian_tab_for_view_tests(monkeypatch)
    try:
        tab.row_data = [
            {"代码": "8035.T", "名称": "Tokyo Electron"},
            {"代码": "2330.TW", "名称": "TSMC"},
            {"代码": "0522.HK", "名称": "ASMPT"},
        ]
        tab.update_table_ui()
        code_col = tab.model.headers.index("代码")
        tab.asian_table.sortByColumn(code_col, sort_order)
        QApplication.processEvents()

        tab._pin_asian_code_to_top("2330.TW")
        QApplication.processEvents()

        first_source = tab.proxy_model.mapToSource(tab.proxy_model.index(0, code_col))
        assert tab.model.row_data[first_source.row()]["代码"] == "2330.TW"
    finally:
        tab.deleteLater()


def test_asian_market_rt_update_does_not_start_flash_repaint_timer(monkeypatch):
    monkeypatch.setattr(asian_module, "AsianMarketWorker", _DummyWorker)
    monkeypatch.setattr(
        asian_module.AsianMarketTab,
        "_load_local_cache",
        lambda self: setattr(self, "row_data", []),
    )
    monkeypatch.setattr(asian_module.AsianMarketTab, "_check_auto_cache", lambda self: None)
    monkeypatch.setattr(asian_module.AsianMarketTab, "_save_rt_cache", lambda self: None)
    monkeypatch.setattr(
        asian_module.AsianMarketTab,
        "bind_header_persistence",
        lambda self, table, settings_key="header_state": None,
        raising=False,
    )

    tab = asian_module.AsianMarketTab()
    try:
        tab.row_data = [
            {
                "代码": "2330.TW",
                "名称": "TSMC",
                "现价": "--",
                "涨幅%": 0.0,
                "PE": "--",
                "市场": "台湾",
                "状态": "",
                "赛道": "半导体",
                "角色定位": "龙头",
                "货币": "TWD",
                "5日涨跌%": 0.0,
                "10日涨跌%": 0.0,
                "20日涨跌%": 0.0,
            }
        ]
        tab.model.update_data(tab.row_data)
        tab.asian_table._flash_repaint_timer.stop()

        tab._on_rt_update(
            {
                "2330.TW": {
                    "close": 123.45,
                    "pct": 1.2,
                    "pe": 22.5,
                    "pct_5": 2.0,
                    "pct_10": 3.0,
                    "pct_20": 4.0,
                    "currency": "TWD",
                }
            }
        )

        assert tab.model.row_data[0]["现价"] == "123.45"
        assert tab.asian_table._flash_repaint_timer.isActive() is False
    finally:
        tab.deleteLater()


def test_asian_market_status_column_uses_plain_style(monkeypatch):
    monkeypatch.setattr(asian_module, "AsianMarketWorker", _DummyWorker)
    monkeypatch.setattr(
        asian_module.AsianMarketTab,
        "_load_local_cache",
        lambda self: setattr(self, "row_data", []),
    )
    monkeypatch.setattr(asian_module.AsianMarketTab, "_check_auto_cache", lambda self: None)
    monkeypatch.setattr(
        asian_module.AsianMarketTab,
        "bind_header_persistence",
        lambda self, table, settings_key="header_state": None,
        raising=False,
    )

    tab = asian_module.AsianMarketTab()
    try:
        assert "状态" in tab.model._plain_style_headers
        assert "涨幅%" not in tab.model._plain_style_headers
        assert "涨幅%" in tab.model._plain_background_headers
    finally:
        tab.deleteLater()


def test_asian_market_pct_column_keeps_rise_fall_color(monkeypatch):
    monkeypatch.setattr(asian_module, "AsianMarketWorker", _DummyWorker)
    monkeypatch.setattr(
        asian_module.AsianMarketTab,
        "_load_local_cache",
        lambda self: setattr(self, "row_data", []),
    )
    monkeypatch.setattr(asian_module.AsianMarketTab, "_check_auto_cache", lambda self: None)
    monkeypatch.setattr(
        asian_module.AsianMarketTab,
        "bind_header_persistence",
        lambda self, table, settings_key="header_state": None,
        raising=False,
    )

    tab = asian_module.AsianMarketTab()
    try:
        tab.model.update_data(
            [
                {
                    "代码": "00700",
                    "名称": "腾讯控股",
                    "现价": 512.0,
                    "涨幅%": 2.35,
                    "市场": "HK",
                    "状态": "交易中",
                    "赛道": "互联网",
                    "角色定位": "龙头",
                    "货币": "HKD",
                    "5日涨跌%": 1.2,
                    "10日涨跌%": -0.8,
                    "20日涨跌%": 3.6,
                }
            ]
        )
        pct_col = tab.model.headers.index("涨幅%")
        model_index = tab.model.index(0, pct_col)
        color = tab.model.data(model_index, Qt.ItemDataRole.ForegroundRole)
        background = tab.model.data(model_index, Qt.ItemDataRole.BackgroundRole)
        skip_sorted_overlay = tab.model.data(model_index, Qt.ItemDataRole.UserRole + 3)

        assert isinstance(color, QColor)
        assert color.name().lower() == QColor(_c("COLOR_RISE")).name().lower()
        assert background is None
        assert skip_sorted_overlay is False
    finally:
        tab.deleteLater()


def test_asian_market_status_display_uses_market_specific_labels(monkeypatch):
    original = MarketCalendar.get_market_status

    monkeypatch.setattr(
        MarketCalendar,
        "get_market_status",
        classmethod(lambda cls, market="CN": "午休"),
    )
    assert get_asian_market_status("T") == "🟡 午间休市"
    assert get_asian_market_status("HK") == "🟡 午间休市"

    monkeypatch.setattr(
        MarketCalendar,
        "get_market_status",
        classmethod(lambda cls, market="CN": "开盘集合竞价"),
    )
    assert get_asian_market_status("KS") == "🟡 开盘竞价"

    monkeypatch.setattr(
        MarketCalendar,
        "get_market_status",
        classmethod(lambda cls, market="CN": "交易中"),
    )
    assert get_asian_market_status("TW") == "🟢 交易中"

    monkeypatch.setattr(MarketCalendar, "get_market_status", original)


def test_asian_market_constructor_schedules_local_cache_background(monkeypatch, tmp_path):
    calls = []

    class FakeTaskRunner:
        def run_in_background(self, fn, *, task_id=None, on_success=None, on_error=None):
            calls.append((fn, task_id, on_success, on_error))
            return str(task_id)

    monkeypatch.setattr(asian_module, "AsianMarketWorker", _DummyWorker)
    monkeypatch.setattr(asian_module, "task_manager", FakeTaskRunner())
    monkeypatch.setattr(asian_module, "JSON_CACHE", str(tmp_path / "asian_klines_latest.json"))
    monkeypatch.setattr(asian_module, "RT_JSON_CACHE", str(tmp_path / "asian_rt_latest.json"))
    monkeypatch.setattr(asian_module.AsianMarketTab, "_check_auto_cache", lambda self: None)
    monkeypatch.setattr(
        asian_module.AsianMarketTab,
        "bind_header_persistence",
        lambda self, table, settings_key="header_state": None,
        raising=False,
    )

    tab = asian_module.AsianMarketTab()
    try:
        assert len(calls) == 1
        assert calls[0][1] == asian_module._ASIAN_MARKET_LOCAL_CACHE_TASK
        assert tab.row_data == []
        assert tab._load_cache_in_progress is True
    finally:
        tab.deleteLater()


def test_asian_market_apply_local_cache_payload_defers_table_update(monkeypatch):
    queued = []
    calls = []
    monkeypatch.setattr(asian_module.QTimer, "singleShot", lambda delay, callback: queued.append((delay, callback)))

    class DummyTab:
        row_data = []
        _runtime_cleanup_done = False

        def _sync_worker_codes(self):
            calls.append("sync")

        def update_table_ui(self):
            calls.append(("update", list(self.row_data)))

        @staticmethod
        def _status_metric(label, value, suffix=""):
            return f"{label}{value}{suffix}"

        def _set_asian_status(self, *args, **kwargs):
            calls.append(("status", args, kwargs))

        def _finish_local_cache_load(self):
            calls.append("finish")

    tab = DummyTab()

    asian_module.AsianMarketTab._apply_local_cache_payload(
        tab,
        {"rt_updates": {}, "rows": [{"代码": "2330.TW"}]},
    )

    assert calls == ["sync"]
    assert tab.row_data == [{"代码": "2330.TW"}]
    assert queued[0][0] == 0

    queued[0][1]()

    assert ("update", [{"代码": "2330.TW"}]) in calls
    assert "finish" in calls


def test_asian_market_display_uses_taiwan_label_for_tw_codes():
    assert format_market_display("TW", "2330.TW") == "台湾"
    assert format_market_display("TWO", "3324.TWO") == "台湾"
    assert format_market_display("中华民国", "2330.TW") == "台湾"


def test_asian_market_placeholder_rows_fill_track_for_missing_cache(monkeypatch, tmp_path):
    cache_file = tmp_path / "asian_klines_latest.json"
    cache_file.write_text(json.dumps({"stocks": []}, ensure_ascii=False), encoding="utf-8")

    monkeypatch.setattr(asian_module, "AsianMarketWorker", _DummyWorker)
    monkeypatch.setattr(asian_module, "JSON_CACHE", str(cache_file))
    monkeypatch.setattr(asian_module, "RT_JSON_CACHE", str(tmp_path / "asian_rt_cache.json"))
    monkeypatch.setattr(asian_module, "GLOBAL_ASIAN_RT_CACHE", {})
    _install_immediate_local_cache_runner(monkeypatch)
    monkeypatch.setattr(asian_module.AsianMarketTab, "_check_auto_cache", lambda self: None)
    monkeypatch.setattr(
        asian_module.AsianMarketTab,
        "bind_header_persistence",
        lambda self, table, settings_key="header_state": None,
        raising=False,
    )

    tab = asian_module.AsianMarketTab()
    try:
        row = next(item for item in tab.row_data if item["代码"] == "2330.TW")
        assert row["赛道"] not in {"", "未知赛道"}
    finally:
        tab.deleteLater()


def test_asian_market_save_rt_cache_creates_missing_parent_dir(monkeypatch, tmp_path):
    rt_cache_file = tmp_path / "missing" / "cache" / "asian_rt_latest.json"
    monkeypatch.setattr(asian_module, "RT_JSON_CACHE", str(rt_cache_file))
    monkeypatch.setattr(
        asian_module,
        "GLOBAL_ASIAN_RT_CACHE",
        {
            "2330.TW": {
                "date": "2026-05-16",
                "close": 100.0,
                "previous_close": 95.0,
                "pct": 5.263,
                "source": "unit-test",
            }
        },
    )

    asian_module.AsianMarketTab._save_rt_cache(None)

    assert rt_cache_file.exists()
    payload = json.loads(rt_cache_file.read_text(encoding="utf-8"))
    assert payload["2330.TW"]["pct"] == 5.26
    assert payload["2330.TW"]["source"] == "unit-test"


def test_asian_market_load_local_cache_normalizes_stale_yfinance_pct(monkeypatch, tmp_path):
    history_payload = {
        "stocks": [
            {
                "name": "Nittobo",
                "ticker": "3110.T",
                "market": "日本",
                "track": "高频PCB与覆铜板材料",
                "currency": "JPY",
                "klines": [
                    {
                        "date": "2026-04-17",
                        "open": 26820.0,
                        "high": 27970.0,
                        "low": 26680.0,
                        "close": 27510.0,
                        "volume": 2656700.0,
                    },
                    {
                        "date": "2026-04-20",
                        "open": 27300.0,
                        "high": 27850.0,
                        "low": 26610.0,
                        "close": 26720.0,
                        "volume": 1689500.0,
                    },
                ],
            }
        ]
    }
    rt_payload = {
        "3110.T": {
            "date": "2026-04-21",
            "close": 26540.0,
            "open": 26850.0,
            "high": 27690.0,
            "low": 26460.0,
            "volume": 1540700.0,
            "previous_close": 27450.0,
            "pct": -3.3151183970856146,
            "currency": "JPY",
            "source": "yfinance",
            "quote_quality": "last",
        }
    }
    cache_file = tmp_path / "asian_klines_latest.json"
    rt_cache_file = tmp_path / "asian_rt_latest.json"
    cache_file.write_text(json.dumps(history_payload, ensure_ascii=False), encoding="utf-8")
    rt_cache_file.write_text(json.dumps(rt_payload, ensure_ascii=False), encoding="utf-8")

    monkeypatch.setattr(asian_module, "AsianMarketWorker", _DummyWorker)
    monkeypatch.setattr(asian_module, "JSON_CACHE", str(cache_file))
    monkeypatch.setattr(asian_module, "RT_JSON_CACHE", str(rt_cache_file))
    monkeypatch.setattr(asian_module, "GLOBAL_ASIAN_RT_CACHE", {})
    _install_immediate_local_cache_runner(monkeypatch)
    monkeypatch.setattr(asian_module.AsianMarketTab, "_check_auto_cache", lambda self: None)
    monkeypatch.setattr(
        asian_module.AsianMarketTab,
        "bind_header_persistence",
        lambda self, table, settings_key="header_state": None,
        raising=False,
    )

    tab = asian_module.AsianMarketTab()
    try:
        row = next(item for item in tab.row_data if item["代码"] == "3110.T")
        assert row["涨幅%"] == pytest.approx(round((26540.0 / 26720.0 - 1.0) * 100.0, 2))
        assert asian_module.GLOBAL_ASIAN_RT_CACHE["3110.T"]["previous_close"] == 26720.0
    finally:
        tab.deleteLater()


def test_asian_market_load_local_cache_normalizes_stale_naver_pct(monkeypatch, tmp_path):
    history_payload = {
        "stocks": [
            {
                "name": "SK Hynix",
                "ticker": "000660.KS",
                "market": "韩国",
                "track": "HBM与先进存储",
                "currency": "KRW",
                "klines": [
                    {
                        "date": "2026-06-04",
                        "open": 2284000.0,
                        "high": 2327000.0,
                        "low": 2262000.0,
                        "close": 2298000.0,
                        "volume": 3941067.0,
                    },
                    {
                        "date": "2026-06-05",
                        "open": 2251000.0,
                        "high": 2257000.0,
                        "low": 2004000.0,
                        "close": 2070000.0,
                        "volume": 9041779.0,
                    },
                ],
            }
        ]
    }
    rt_payload = {
        "000660.KS": {
            "date": "2026-06-05",
            "close": 2070000.0,
            "open": 2142000.0,
            "high": 2188000.0,
            "low": 2070000.0,
            "volume": 5358995.0,
            "previous_close": 1842000.0,
            "pct": 12.38,
            "currency": "KRW",
            "source": "naver_realtime",
            "quote_quality": "last",
        }
    }
    cache_file = tmp_path / "asian_klines_latest.json"
    rt_cache_file = tmp_path / "asian_rt_latest.json"
    cache_file.write_text(json.dumps(history_payload, ensure_ascii=False), encoding="utf-8")
    rt_cache_file.write_text(json.dumps(rt_payload, ensure_ascii=False), encoding="utf-8")

    monkeypatch.setattr(asian_module, "AsianMarketWorker", _DummyWorker)
    monkeypatch.setattr(asian_module, "JSON_CACHE", str(cache_file))
    monkeypatch.setattr(asian_module, "RT_JSON_CACHE", str(rt_cache_file))
    monkeypatch.setattr(asian_module, "GLOBAL_ASIAN_RT_CACHE", {})
    _install_immediate_local_cache_runner(monkeypatch)
    monkeypatch.setattr(asian_module.AsianMarketTab, "_check_auto_cache", lambda self: None)
    monkeypatch.setattr(
        asian_module.AsianMarketTab,
        "bind_header_persistence",
        lambda self, table, settings_key="header_state": None,
        raising=False,
    )

    tab = asian_module.AsianMarketTab()
    try:
        row = next(item for item in tab.row_data if item["代码"] == "000660.KS")
        assert row["涨幅%"] == pytest.approx(round((2070000.0 / 2298000.0 - 1.0) * 100.0, 2))
        assert asian_module.GLOBAL_ASIAN_RT_CACHE["000660.KS"]["previous_close"] == 2298000.0
    finally:
        tab.deleteLater()


def test_asian_market_load_local_cache_prefers_hk_daily_close_over_delayed_quote(monkeypatch, tmp_path):
    history_payload = {
        "stocks": [
            {
                "name": "ASMPT",
                "ticker": "0522.HK",
                "market": "香港",
                "track": "先进封装与混合键合",
                "currency": "HKD",
                "klines": [
                    {
                        "date": "2026-06-04",
                        "open": 186.4,
                        "high": 191.7,
                        "low": 183.2,
                        "close": 184.0,
                        "volume": 2970687.0,
                    },
                    {
                        "date": "2026-06-05",
                        "open": 184.0,
                        "high": 184.0,
                        "low": 172.1,
                        "close": 176.0,
                        "volume": 4639221.0,
                    },
                ],
            }
        ]
    }
    rt_payload = {
        "0522.HK": {
            "date": "2026-06-05",
            "close": 175.0,
            "open": 184.0,
            "high": 184.0,
            "low": 172.1,
            "volume": 435472.0,
            "previous_close": 184.0,
            "pct": -4.89,
            "currency": "HKD",
            "source": "tencent_hk",
            "quote_quality": "free_delayed",
        }
    }
    cache_file = tmp_path / "asian_klines_latest.json"
    rt_cache_file = tmp_path / "asian_rt_latest.json"
    cache_file.write_text(json.dumps(history_payload, ensure_ascii=False), encoding="utf-8")
    rt_cache_file.write_text(json.dumps(rt_payload, ensure_ascii=False), encoding="utf-8")

    monkeypatch.setattr(asian_module, "AsianMarketWorker", _DummyWorker)
    monkeypatch.setattr(asian_module, "JSON_CACHE", str(cache_file))
    monkeypatch.setattr(asian_module, "RT_JSON_CACHE", str(rt_cache_file))
    monkeypatch.setattr(asian_module, "GLOBAL_ASIAN_RT_CACHE", {})
    _install_immediate_local_cache_runner(monkeypatch)
    monkeypatch.setattr(asian_module.AsianMarketTab, "_check_auto_cache", lambda self: None)
    monkeypatch.setattr(
        asian_module.AsianMarketTab,
        "bind_header_persistence",
        lambda self, table, settings_key="header_state": None,
        raising=False,
    )

    tab = asian_module.AsianMarketTab()
    try:
        row = next(item for item in tab.row_data if item["代码"] == "0522.HK")
        assert row["现价"] == "176.00"
        assert row["涨幅%"] == pytest.approx(round((176.0 / 184.0 - 1.0) * 100.0, 2))
        assert asian_module.GLOBAL_ASIAN_RT_CACHE["0522.HK"]["close"] == 176.0
        assert asian_module.GLOBAL_ASIAN_RT_CACHE["0522.HK"]["previous_close"] == 184.0
    finally:
        tab.deleteLater()


def test_asian_market_load_local_cache_keeps_history_when_rt_cache_is_zero(monkeypatch, tmp_path):
    history_payload = {
        "stocks": [
            {
                "name": "Murata",
                "ticker": "6981.T",
                "market": "日本",
                "track": "数据中心电力与配电",
                "currency": "JPY",
                "klines": [
                    {
                        "date": "2026-04-30",
                        "open": 4848.0,
                        "high": 5265.0,
                        "low": 4750.0,
                        "close": 5156.0,
                        "volume": 30704800.0,
                    },
                    {
                        "date": "2026-05-01",
                        "open": 5000.0,
                        "high": 5253.0,
                        "low": 4905.0,
                        "close": 5138.0,
                        "volume": 18404600.0,
                    },
                ],
            }
        ]
    }
    rt_payload = {
        "6981.T": {
            "date": None,
            "close": 0.0,
            "open": 0.0,
            "high": 0.0,
            "low": 0.0,
            "volume": 0.0,
            "previous_close": 0.0,
            "pct": 0.0,
            "currency": "",
            "source": "",
            "quote_quality": "",
        }
    }
    cache_file = tmp_path / "asian_klines_latest.json"
    rt_cache_file = tmp_path / "asian_rt_latest.json"
    cache_file.write_text(json.dumps(history_payload, ensure_ascii=False), encoding="utf-8")
    rt_cache_file.write_text(json.dumps(rt_payload, ensure_ascii=False), encoding="utf-8")

    monkeypatch.setattr(asian_module, "AsianMarketWorker", _DummyWorker)
    monkeypatch.setattr(asian_module, "JSON_CACHE", str(cache_file))
    monkeypatch.setattr(asian_module, "RT_JSON_CACHE", str(rt_cache_file))
    monkeypatch.setattr(asian_module, "GLOBAL_ASIAN_RT_CACHE", {})
    monkeypatch.setattr(asian_module, "filter_asian_tickers", lambda: {"Murata": "6981.T"})
    _install_immediate_local_cache_runner(monkeypatch)
    monkeypatch.setattr(asian_module.AsianMarketTab, "_check_auto_cache", lambda self: None)
    monkeypatch.setattr(
        asian_module.AsianMarketTab,
        "bind_header_persistence",
        lambda self, table, settings_key="header_state": None,
        raising=False,
    )

    tab = asian_module.AsianMarketTab()
    try:
        row = next(item for item in tab.row_data if item["代码"] == "6981.T")
        assert row["现价"] == "5138.00"
        assert row["涨幅%"] == pytest.approx(round((5138.0 / 5156.0 - 1.0) * 100.0, 2))
        assert asian_module.GLOBAL_ASIAN_RT_CACHE["6981.T"]["close"] == 5138.0
    finally:
        tab.deleteLater()


def test_asian_market_load_local_cache_recomputes_short_pct_for_direct_quote_sources(monkeypatch, tmp_path):
    def stock_payload(name, ticker, market, currency, close_base):
        start_date = dt.date(2026, 5, 1)
        klines = []
        for offset in range(22):
            close = close_base + offset
            klines.append(
                {
                    "date": (start_date + dt.timedelta(days=offset)).isoformat(),
                    "open": close - 0.5,
                    "high": close + 1.0,
                    "low": close - 1.0,
                    "close": close,
                    "volume": 100000.0 + offset,
                }
            )
        return {
            "name": name,
            "ticker": ticker,
            "market": market,
            "track": "高频PCB与覆铜板材料",
            "currency": currency,
            "klines": klines,
        }

    history_payload = {
        "stocks": [
            stock_payload("Nittobo", "3110.T", "日本", "JPY", 100.0),
            stock_payload("TSMC", "2330.TW", "台湾", "TWD", 200.0),
        ]
    }
    rt_payload = {
        "3110.T": {
            "date": "2026-05-22",
            "close": 121.0,
            "open": 120.5,
            "high": 122.0,
            "low": 120.0,
            "volume": 100121.0,
            "previous_close": 120.0,
            "pct": 0.0,
            "pct_5": 0.0,
            "pct_10": 0.0,
            "pct_20": 0.0,
            "currency": "JPY",
            "source": "yj_finance_page",
            "quote_quality": "last",
        },
        "2330.TW": {
            "date": "2026-05-22",
            "close": 221.0,
            "open": 220.5,
            "high": 222.0,
            "low": 220.0,
            "volume": 200121.0,
            "previous_close": 220.0,
            "pct": 0.0,
            "pct_5": 0.0,
            "pct_10": 0.0,
            "pct_20": 0.0,
            "currency": "TWD",
            "source": "twse_mis",
            "quote_quality": "last",
        },
    }
    cache_file = tmp_path / "asian_klines_latest.json"
    rt_cache_file = tmp_path / "asian_rt_latest.json"
    cache_file.write_text(json.dumps(history_payload, ensure_ascii=False), encoding="utf-8")
    rt_cache_file.write_text(json.dumps(rt_payload, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(asian_module, "filter_asian_tickers", lambda: {"Nittobo": "3110.T", "TSMC": "2330.TW"})

    payload = asian_module.build_asian_market_local_cache_payload(
        json_cache=str(cache_file),
        rt_json_cache=str(rt_cache_file),
        existing_rt_cache={},
    )

    rows = {row["代码"]: row for row in payload["rows"]}
    assert rows["3110.T"]["5日涨跌%"] == pytest.approx(round((121.0 / 116.0 - 1.0) * 100.0, 2))
    assert rows["3110.T"]["10日涨跌%"] == pytest.approx(round((121.0 / 111.0 - 1.0) * 100.0, 2))
    assert rows["3110.T"]["20日涨跌%"] == pytest.approx(round((121.0 / 101.0 - 1.0) * 100.0, 2))
    assert rows["2330.TW"]["5日涨跌%"] == pytest.approx(round((221.0 / 216.0 - 1.0) * 100.0, 2))
    assert rows["2330.TW"]["10日涨跌%"] == pytest.approx(round((221.0 / 211.0 - 1.0) * 100.0, 2))
    assert rows["2330.TW"]["20日涨跌%"] == pytest.approx(round((221.0 / 201.0 - 1.0) * 100.0, 2))


def test_asian_market_status_rows_refresh_without_quote_tick(monkeypatch):
    monkeypatch.setattr(asian_module, "AsianMarketWorker", _DummyWorker)
    monkeypatch.setattr(
        asian_module.AsianMarketTab,
        "_load_local_cache",
        lambda self: setattr(self, "row_data", []),
    )
    monkeypatch.setattr(asian_module.AsianMarketTab, "_check_auto_cache", lambda self: None)
    monkeypatch.setattr(
        asian_module.AsianMarketTab,
        "bind_header_persistence",
        lambda self, table, settings_key="header_state": None,
        raising=False,
    )

    tab = asian_module.AsianMarketTab()
    try:
        tab.row_data = [
            {
                "代码": "8035.T",
                "名称": "东京电子",
                "现价": 100.0,
                "涨幅%": 1.2,
                "市场": "日本",
                "状态": "🟢 交易中",
                "赛道": "半导体",
                "角色定位": "龙头",
                "货币": "JPY",
                "5日涨跌%": 1.0,
                "10日涨跌%": 2.0,
                "20日涨跌%": 3.0,
            }
        ]
        tab.model.update_data(tab.row_data)
        monkeypatch.setattr(asian_module, "get_market_status", lambda market: "🟡 收盘竞价")

        tab._refresh_market_status_rows()

        assert tab.model.row_data[0]["状态"] == "🟡 收盘竞价"
    finally:
        tab.deleteLater()


class _RuntimeStatusStub:
    def __init__(self):
        self.worker = None
        self.cache_thread = object()
        self._last_asian_success_at = None
        self.runtime_state = None
        self.loaded_cache = False
        self.status_calls = []

    def _set_runtime_state(self, state):
        self.runtime_state = state

    def _load_local_cache(self):
        self.loaded_cache = True

    def _set_asian_status(self, primary, *segments, freshness="", next_step=""):
        self.status_calls.append((primary, segments))


def test_on_auto_cache_finished_marks_preserved_cache_mode():
    tab = _RuntimeStatusStub()

    asian_runtime.on_auto_cache_finished(tab, True, "亚洲 K 线远端拉取失败，已保留现有缓存")

    assert tab.runtime_state == "paused_for_cache_sync"
    assert tab.loaded_cache is True
    assert tab._last_asian_success_at is not None
    assert tab.status_calls[-1] == (
        "已保留本地缓存",
        ("远端拉取失败，本次继续沿用上次成功结果",),
    )


def test_asian_market_runtime_state_uses_actual_tracked_markets(monkeypatch):
    monkeypatch.setattr(asian_module, "AsianMarketWorker", _DummyWorker)
    monkeypatch.setattr(
        asian_module.AsianMarketTab,
        "_load_local_cache",
        lambda self: setattr(self, "row_data", [{"代码": "0522.HK"}]),
    )
    monkeypatch.setattr(asian_module.AsianMarketTab, "_check_auto_cache", lambda self: None)
    monkeypatch.setattr(
        asian_module.AsianMarketTab,
        "bind_header_persistence",
        lambda self, table, settings_key="header_state": None,
        raising=False,
    )
    monkeypatch.setattr(asian_module, "is_asian_quote_refresh_time", lambda codes: codes == ["0522.HK"])

    tab = asian_module.AsianMarketTab()
    try:
        assert tab._asian_runtime_state == "running"
    finally:
        tab.deleteLater()


def test_manual_refresh_starts_lazy_runtime_worker(monkeypatch):
    monkeypatch.setattr(asian_module, "AsianMarketWorker", _DummyWorker)
    monkeypatch.setattr(
        asian_module.AsianMarketTab,
        "_load_local_cache",
        lambda self: setattr(self, "row_data", [{"代码": "0522.HK"}]),
    )
    monkeypatch.setattr(asian_module.AsianMarketTab, "_check_auto_cache", lambda self: None)
    monkeypatch.setattr(
        asian_module.AsianMarketTab,
        "bind_header_persistence",
        lambda self, table, settings_key="header_state": None,
        raising=False,
    )

    tab = asian_module.AsianMarketTab()
    try:
        tab._asian_market_service.set_target_codes(["0522.HK"])

        assert tab.worker is None

        tab._on_manual_refresh()

        assert tab.worker is not None
        assert tab._asian_runtime_state == "manual_refresh_once"
        assert tab._status_primary == "刷新已触发"
    finally:
        tab.deleteLater()


def test_asian_market_minute_tick_uses_tracked_market_refresh_window(monkeypatch):
    monkeypatch.setattr(asian_module, "AsianMarketWorker", _DummyWorker)
    monkeypatch.setattr(
        asian_module.AsianMarketTab,
        "_load_local_cache",
        lambda self: setattr(self, "row_data", [{"代码": "8035.T"}]),
    )
    monkeypatch.setattr(asian_module.AsianMarketTab, "_check_auto_cache", lambda self: None)
    monkeypatch.setattr(
        asian_module.AsianMarketTab,
        "bind_header_persistence",
        lambda self, table, settings_key="header_state": None,
        raising=False,
    )

    tab = asian_module.AsianMarketTab()
    try:
        tab._set_runtime_state("running")
        monkeypatch.setattr(tab, "_is_quote_refresh_open", lambda: False)

        tab._on_minute_tick()

        assert tab._asian_runtime_state == "paused_for_cache_sync"
    finally:
        tab.deleteLater()


def test_asian_worker_pause_message_turns_into_after_hours_idle_when_cache_not_syncing():
    class _DummyTab:
        def __init__(self):
            self.cache_thread = None
            self.row_data = [{"代码": "0522.HK"}]
            self._is_fetching_cache = False
            self._pending_auto_cache_sync = False
            self._status_freshness = "本地缓存"
            self.status_calls = []

        def _set_asian_status(self, primary, *segments, freshness="", next_step=""):
            self.status_calls.append((primary, segments, freshness, next_step))

    tab = _DummyTab()

    asian_module.AsianMarketTab._on_worker_progress(tab, "亚洲市场后台刷新已暂停，等待缓存同步完成")

    assert tab.status_calls == [("盘后静默中", tuple(), "本地缓存", "可点击刷新亚洲市场")]


def test_asian_worker_pause_message_does_not_override_active_cache_sync():
    class _DummyTab:
        def __init__(self):
            self.cache_thread = _CacheThreadStub(running=True)
            self.row_data = [{"代码": "0522.HK"}]
            self._is_fetching_cache = True
            self._pending_auto_cache_sync = False
            self._status_freshness = "本地缓存"
            self.status_calls = []

        def _set_asian_status(self, primary, *segments, freshness="", next_step=""):
            self.status_calls.append((primary, segments, freshness, next_step))

    tab = _DummyTab()

    asian_module.AsianMarketTab._on_worker_progress(tab, "亚洲市场后台刷新已暂停，等待缓存同步完成")

    assert tab.status_calls == []
