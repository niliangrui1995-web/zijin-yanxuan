# -*- coding: utf-8 -*-
import datetime as dt

import pandas as pd
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QWidget

from core.task_manager import task_manager
from ui import kline_window_qt as kline_module


class _DummyProvider:
    _offline = True


class _LiveProvider:
    _offline = False


class _LunchQuoteProvider:
    _offline = False

    def __init__(self):
        self.fetch_calls = 0

    def _build_df(self):
        return pd.DataFrame(
            {
                "open": [10.0],
                "high": [10.2],
                "low": [9.9],
                "close": [10.1],
                "volume": [1000.0],
            },
            index=[pd.Timestamp("2026-04-13")],
        )

    def get_data(self, code):
        return self._build_df()

    def get_data_fresh_for_chart(self, code, force_sync=False):
        return self._build_df()

    def fetch_realtime_quotes_batch(self, codes):
        self.fetch_calls += 1
        return {
            codes[0]: {
                "date": "2026-04-14",
                "open": 10.3,
                "high": 10.8,
                "low": 10.2,
                "close": 10.6,
                "volume": 3456.0,
            }
        }


def test_kline_header_action_controls_share_same_height(monkeypatch):
    monkeypatch.setattr(kline_module, "QWebEngineView", QWidget)
    monkeypatch.setattr(kline_module.KLineChartWindow, "_load_and_draw", lambda self: None)
    monkeypatch.setattr(
        kline_module.KLineChartWindow,
        "_check_fav_status",
        lambda self: setattr(self, "is_fav", False),
    )

    window = kline_module.KLineChartWindow(
        None,
        "000001",
        "平安银行",
        _DummyProvider(),
        vcp_data={},
        code_list=[{"代码": "000001", "名称": "平安银行"}],
        current_idx=0,
    )
    try:
        action_widgets = (
            window.btn_prev,
            window.nav_index_lbl,
            window.btn_next,
            window.btn_fav,
        )
        heights = {widget.minimumHeight() for widget in action_widgets}
        max_heights = {widget.maximumHeight() for widget in action_widgets}

        assert len(heights) == 1
        assert len(max_heights) == 1
        assert all(height > 0 for height in heights)
        assert window.btn_prev.focusPolicy() == Qt.FocusPolicy.NoFocus
        assert window.btn_next.focusPolicy() == Qt.FocusPolicy.NoFocus
        assert window.btn_fav.focusPolicy() == Qt.FocusPolicy.NoFocus
    finally:
        window.deleteLater()


def test_kline_header_exposes_session_and_feed_badges(monkeypatch):
    monkeypatch.setattr(kline_module, "QWebEngineView", QWidget)
    monkeypatch.setattr(kline_module.KLineChartWindow, "_load_and_draw", lambda self: None)
    monkeypatch.setattr(
        kline_module.KLineChartWindow,
        "_check_fav_status",
        lambda self: setattr(self, "is_fav", False),
    )

    window = kline_module.KLineChartWindow(
        None,
        "000001",
        "平安银行",
        _LiveProvider(),
        vcp_data={},
        code_list=[{"代码": "000001", "名称": "平安银行"}],
        current_idx=0,
    )
    try:
        window._set_status_message("实时刷新中", tone="realtime")

        assert window.market_badge_lbl.text()
        assert window.session_badge_lbl.text()
        assert window.feed_badge_lbl.text()
        assert "background-color" in window.session_badge_lbl.styleSheet()
        assert "background-color" in window.feed_badge_lbl.styleSheet()
    finally:
        window.deleteLater()


def test_kline_load_and_draw_appends_today_bar_during_lunch_break(monkeypatch):
    original_load = kline_module.KLineChartWindow._load_and_draw

    monkeypatch.setattr(kline_module, "QWebEngineView", QWidget)
    monkeypatch.setattr(kline_module.KLineChartWindow, "_load_and_draw", lambda self: None)
    monkeypatch.setattr(
        kline_module.KLineChartWindow,
        "_check_fav_status",
        lambda self: setattr(self, "is_fav", False),
    )

    provider = _LunchQuoteProvider()
    window = kline_module.KLineChartWindow(
        None,
        "000001",
        "平安银行",
        provider,
        vcp_data={},
        code_list=[{"代码": "000001", "名称": "平安银行"}],
        current_idx=0,
    )

    captured = {}

    def _fake_render(df, loading=False):
        window.df = df.copy()
        if not loading:
            captured["df"] = df.copy()

    def _run_inline(fn, *args, on_success=None, on_error=None, task_id=None, **kwargs):
        try:
            result = fn(*args, **kwargs)
            if on_success:
                on_success(result)
        except Exception as exc:
            if on_error:
                on_error(str(exc))
            else:
                raise exc
        return task_id or "test-kline-lunch"

    monkeypatch.setattr(window, "_render_chart", _fake_render)
    monkeypatch.setattr(window, "_set_status_message", lambda *args, **kwargs: None)
    monkeypatch.setattr(window, "_get_cn_target_trade_date", lambda: dt.date(2026, 4, 14))
    monkeypatch.setattr(
        kline_module.MarketCalendar,
        "is_quote_refresh_time",
        classmethod(lambda cls, market="CN": True),
    )
    monkeypatch.setattr(
        kline_module.MarketCalendar,
        "is_market_active",
        classmethod(lambda cls, market="CN": False),
    )
    monkeypatch.setattr(task_manager, "run_in_background", _run_inline)

    try:
        original_load(window)

        assert provider.fetch_calls == 1
        assert "df" in captured
        assert list(captured["df"].index.strftime("%Y-%m-%d")) == ["2026-04-13", "2026-04-14"]
        assert float(captured["df"].iloc[-1]["close"]) == 10.6
    finally:
        if window._rt_timer is not None:
            window._rt_timer.stop()
        window.deleteLater()
