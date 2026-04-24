# -*- coding: utf-8 -*-
import datetime as dt
import json

import pandas as pd
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QWidget

from core.task_manager import task_manager
from ui.kline_chart_payload import build_kline_html, build_kline_theme_colors
from ui import kline_window_qt as kline_module
from ui.tabs import asian_market_tab as asian_module
from ui.tabs import asian_market_workers as asian_workers_module
from vcp.fetchers import asian_kline_fetcher as asian_fetcher_module


class _DummyProvider:
    _offline = True


class _LiveProvider:
    _offline = False


def test_kline_html_exposes_incremental_replace_bridge():
    payload = {
        "dates": ["2026-04-24"],
        "klines": [[10.0, 11.0, 9.8, 11.2]],
        "vols": [{"value": 1000}],
        "ma10": [10.5],
        "ma20": [10.5],
        "ma50": [10.5],
        "ma150": [10.5],
        "ma200": [10.5],
        "volMa20": [1000],
        "macd": [0.1],
        "diff": [0.1],
        "dea": [0.1],
    }

    html = build_kline_html("T", payload, __file__, build_kline_theme_colors())

    assert "let rawData =" in html
    assert "window.replaceKlineData" in html


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
        assert window.btn_prev.focusPolicy() == Qt.FocusPolicy.StrongFocus
        assert window.btn_next.focusPolicy() == Qt.FocusPolicy.StrongFocus
        assert window.btn_fav.focusPolicy() == Qt.FocusPolicy.StrongFocus
        assert window.btn_prev.accessibleDescription() == "切换到当前列表中的上一只股票"
        assert window.btn_next.accessibleDescription() == "切换到当前列表中的下一只股票"
        assert window.btn_fav.accessibleDescription() == "将当前股票加入或移出关注池"
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
        except (AttributeError, KeyError, RuntimeError, TypeError, ValueError) as exc:
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


def test_kline_load_asian_chart_falls_back_to_single_ticket_fetch(monkeypatch, tmp_path):
    cache_file = tmp_path / "asian_klines_latest.json"
    cache_file.write_text(json.dumps({"stocks": []}, ensure_ascii=False), encoding="utf-8")

    monkeypatch.setattr(kline_module, "QWebEngineView", QWidget)
    monkeypatch.setattr(kline_module.KLineChartWindow, "_load_and_draw", lambda self: None)
    monkeypatch.setattr(
        kline_module.KLineChartWindow,
        "_check_fav_status",
        lambda self: setattr(self, "is_fav", False),
    )
    monkeypatch.setattr(asian_module, "JSON_CACHE", str(cache_file))
    monkeypatch.setattr(asian_module, "GLOBAL_ASIAN_RT_CACHE", {})

    def _fake_fetch_single_kline(name, ticker, period="1y"):
        assert ticker == "2330.TW"
        return {
            "name": "TSMC",
            "ticker": "2330.TW",
            "market": "台湾",
            "track": "先进制程代工",
            "currency": "TWD",
            "klines": [
                {"date": "2026-04-14", "open": 820.0, "high": 828.0, "low": 818.0, "close": 826.0, "volume": 1000},
                {"date": "2026-04-15", "open": 826.0, "high": 835.0, "low": 824.0, "close": 833.0, "volume": 1200},
            ],
        }

    def _run_inline(fn, *args, on_success=None, on_error=None, task_id=None, **kwargs):
        try:
            result = fn(*args, **kwargs)
            if on_success:
                on_success(result)
        except (AttributeError, KeyError, RuntimeError, TypeError, ValueError) as exc:
            if on_error:
                on_error(str(exc))
            else:
                raise exc
        return task_id or "test-kline-asian-fallback"

    monkeypatch.setattr(asian_fetcher_module, "fetch_single_kline", _fake_fetch_single_kline)
    monkeypatch.setattr(task_manager, "run_in_background", _run_inline)

    window = kline_module.KLineChartWindow(
        None,
        "2330.TW",
        "台积电",
        _LiveProvider(),
        vcp_data={},
        code_list=[{"代码": "2330.TW", "名称": "台积电"}],
        current_idx=0,
    )

    captured = {}

    def _fake_render(df, loading=False):
        captured["df"] = df.copy()

    try:
        monkeypatch.setattr(window, "_render_chart", _fake_render)
        monkeypatch.setattr(window, "_set_status_message", lambda *args, **kwargs: None)

        window._load_asian_chart()

        assert "df" in captured
        assert list(captured["df"].index.strftime("%Y-%m-%d")) == ["2026-04-14", "2026-04-15"]
        assert float(captured["df"].iloc[-1]["close"]) == 833.0
        assert window.vcp_data["赛道"] == "先进制程代工"
        assert window.vcp_data["货币"] == "TWD"
    finally:
        if window._rt_timer is not None:
            window._rt_timer.stop()
        window.deleteLater()


def test_kline_load_asian_chart_fetches_realtime_quote_when_history_is_stale(monkeypatch, tmp_path):
    cache_file = tmp_path / "asian_klines_latest.json"
    cache_file.write_text(
        json.dumps(
            {
                "stocks": [
                    {
                        "name": "TSMC",
                        "ticker": "2330.TW",
                        "market": "台湾",
                        "track": "先进制程代工",
                        "currency": "TWD",
                        "klines": [
                            {"date": "2026-04-16", "open": 2000.0, "high": 2020.0, "low": 1990.0, "close": 2010.0, "volume": 1000},
                            {"date": "2026-04-17", "open": 2010.0, "high": 2030.0, "low": 2005.0, "close": 2030.0, "volume": 1100},
                        ],
                    }
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    monkeypatch.setattr(kline_module, "QWebEngineView", QWidget)
    monkeypatch.setattr(kline_module.KLineChartWindow, "_load_and_draw", lambda self: None)
    monkeypatch.setattr(
        kline_module.KLineChartWindow,
        "_check_fav_status",
        lambda self: setattr(self, "is_fav", False),
    )
    monkeypatch.setattr(asian_module, "JSON_CACHE", str(cache_file))
    monkeypatch.setattr(asian_module, "GLOBAL_ASIAN_RT_CACHE", {})
    monkeypatch.setattr(asian_workers_module, "is_cf_proxy_enabled", lambda: True)
    monkeypatch.setattr(
        asian_workers_module,
        "fetch_asian_realtime_quote",
        lambda code, use_cf_proxy=True, yf_session=None: {
            "date": "2026-04-20",
            "open": 2030.0,
            "high": 2055.0,
            "low": 2025.0,
            "close": 2025.0,
            "volume": 3456.0,
        },
    )
    monkeypatch.setattr(
        kline_module.MarketCalendar,
        "get_latest_trade_date",
        classmethod(lambda cls, market="CN", ref_date=None: dt.date(2026, 4, 20)),
    )

    window = kline_module.KLineChartWindow(
        None,
        "2330.TW",
        "台积电",
        _LiveProvider(),
        vcp_data={},
        code_list=[{"代码": "2330.TW", "名称": "台积电"}],
        current_idx=0,
    )

    captured = {}

    def _fake_render(df, loading=False):
        captured["df"] = df.copy()

    try:
        monkeypatch.setattr(window, "_render_chart", _fake_render)
        monkeypatch.setattr(window, "_set_status_message", lambda *args, **kwargs: None)

        window._load_asian_chart()

        assert "df" in captured
        assert list(captured["df"].index.strftime("%Y-%m-%d")) == [
            "2026-04-16",
            "2026-04-17",
            "2026-04-20",
        ]
        assert float(captured["df"].iloc[-1]["close"]) == 2025.0
    finally:
        if window._rt_timer is not None:
            window._rt_timer.stop()
        window.deleteLater()
