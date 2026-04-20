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


@pytest.fixture(autouse=True)
def _disable_saved_asian_header_state(monkeypatch):
    monkeypatch.setattr(
        asian_module.AsianMarketTab,
        "_has_saved_asian_header_state",
        lambda self, settings_key: False,
        raising=False,
    )


def test_infer_asian_markets_normalizes_from_code_suffixes():
    assert infer_asian_markets(["0522.HK", "3324.TWO", "8035.T", "000660.KS"]) == [
        "HK",
        "TW",
        "T",
        "KS",
    ]


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


def test_asian_market_toolbar_checkbox_uses_toolbar_styling(monkeypatch):
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
        assert tab.chk_cf_proxy.property("inToolbar") is True
        assert tab.chk_cf_proxy.text() == "优先使用稳定海外线路"
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
        tab.model.update_data([
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
        ])
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

    assert tab.status_calls == [
        ("盘后静默中", tuple(), "本地缓存", "可点击刷新亚洲市场")
    ]


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
