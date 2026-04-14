# -*- coding: utf-8 -*-
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor

from ui.tabs import asian_market_tab as asian_module
from ui.tabs.asian_market_meta import get_market_status as get_asian_market_status
from ui.models.table_models import _c
from core.market_calendar import MarketCalendar


class _Signal:
    def connect(self, _callback):
        return None


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
        initial_widths = [52, 70, 140, 90, 90, 80, 80, 120, 250, 60, 80, 80, 80]
        total_initial = sum(initial_widths)
        total_scaled = sum(tab.asian_table.columnWidth(i) for i in range(column_count))
        viewport_width = tab.asian_table.viewport().width()

        assert column_count == len(initial_widths)
        assert abs(total_scaled - viewport_width) <= column_count

        scaled_ratio = tab.asian_table.columnWidth(8) / tab.asian_table.columnWidth(1)
        initial_ratio = initial_widths[8] / initial_widths[1]
        assert abs(scaled_ratio - initial_ratio) < 0.2
        assert total_scaled != total_initial
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
        color = tab.model.data(tab.model.index(0, pct_col), Qt.ItemDataRole.ForegroundRole)

        assert isinstance(color, QColor)
        assert color.name().lower() == QColor(_c("COLOR_RISE")).name().lower()
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
