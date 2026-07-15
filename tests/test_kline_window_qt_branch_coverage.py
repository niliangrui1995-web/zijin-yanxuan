# -*- coding: utf-8 -*-
from __future__ import annotations

import datetime as dt
from types import SimpleNamespace

import pandas as pd
import pytest

from ui import kline_window_qt as kline


class _Log:
    def __init__(self):
        self.debugs = []
        self.warnings = []

    def debug(self, message):
        self.debugs.append(message)

    def warning(self, message):
        self.warnings.append(message)


class _Style:
    def __init__(self):
        self.calls = []

    def unpolish(self, obj):
        self.calls.append(("unpolish", obj))

    def polish(self, obj):
        self.calls.append(("polish", obj))


class _Button:
    def __init__(self):
        self.text = None
        self.tooltip = None
        self.properties = {}
        self.enabled = None
        self._style = _Style()
        self.updated = False

    def setText(self, text):
        self.text = text

    def setToolTip(self, text):
        self.tooltip = text

    def setProperty(self, key, value):
        self.properties[key] = value

    def style(self):
        return self._style

    def update(self):
        self.updated = True

    def setEnabled(self, enabled):
        self.enabled = enabled


class _Page:
    def __init__(self, *, raises=False):
        self.calls = []
        self.raises = raises

    def runJavaScript(self, *args):
        if self.raises:
            raise RuntimeError("page gone")
        self.calls.append(args)


class _Browser:
    def __init__(self, *, page_raises=False, html_raises=False):
        self._page = _Page(raises=page_raises)
        self.html = []
        self.html_raises = html_raises

    def page(self):
        return self._page

    def setHtml(self, *args):
        if self.html_raises:
            raise RuntimeError("browser gone")
        self.html.append(args)


def _frame(periods=6):
    return pd.DataFrame(
        {
            "open": [10.0 + index for index in range(periods)],
            "high": [11.0 + index for index in range(periods)],
            "low": [9.0 + index for index in range(periods)],
            "close": [10.5 + index for index in range(periods)],
            "volume": [100.0 + index for index in range(periods)],
        },
        index=pd.date_range("2026-07-01", periods=periods),
    )


def test_favorite_status_toggle_success_and_errors(monkeypatch):
    button = _Button()
    refreshes = []
    window = SimpleNamespace(
        code="000001",
        name="Ping",
        vcp_data={},
        btn_fav=button,
        summary_cards=[],
        _refresh_header_context=lambda: refreshes.append(True),
    )
    monkeypatch.setattr(kline.watchlist_vm, "is_in_watchlist", lambda code: True)
    kline.KLineChartWindow._check_fav_status(window)
    assert window.is_fav is True and button.properties["watching"] is True and refreshes

    monkeypatch.setattr(kline.watchlist_vm, "is_in_watchlist", lambda code: (_ for _ in ()).throw(RuntimeError("bad")))
    kline.KLineChartWindow._check_fav_status(window)
    assert window.is_fav is False and button.properties["watching"] is False

    toggles = []
    monkeypatch.setattr(kline.watchlist_vm, "toggle_stock", lambda *args: toggles.append(args))
    window._check_fav_status = lambda: toggles.append("checked")
    kline.KLineChartWindow._toggle_fav(window)
    assert toggles[-1] == "checked"
    monkeypatch.setattr(kline.watchlist_vm, "toggle_stock", lambda *args: (_ for _ in ()).throw(ValueError("bad")))
    kline.KLineChartWindow._toggle_fav(window)


def test_status_and_delegate_methods(monkeypatch):
    labels = []
    styles = []
    badges = []
    contexts = []
    monkeypatch.setattr(kline, "apply_info_styles", lambda window: styles.append(window))
    monkeypatch.setattr(kline, "apply_header_badges", lambda window: badges.append(window))
    monkeypatch.setattr(kline, "refresh_header_context", lambda window: contexts.append(window))
    monkeypatch.setattr(kline, "resolve_vcp_context", lambda *args: {"resolved": args[1:]})
    monkeypatch.setattr(kline, "apply_qt_theme", lambda window: styles.append("qt"))
    window = SimpleNamespace(
        info_lbl=SimpleNamespace(setText=lambda text: labels.append(text)),
        feed_badge_lbl=object(),
    )
    kline.KLineChartWindow._set_status_message(window, " text ", tone="success")
    assert labels == ["text"] and window._info_tone == "success" and styles and badges
    kline.KLineChartWindow._refresh_header_context(window)
    assert contexts
    assert kline.KLineChartWindow._resolve_vcp_context(window, "1", "one", {})["resolved"]
    kline.KLineChartWindow._apply_qt_theme(window)
    assert styles[-1] == "qt"

    bare = SimpleNamespace()
    kline.KLineChartWindow._set_status_message(bare, None)
    assert bare._info_tone == "info"


class _Geo:
    def __init__(self, x, y, width, height, *, null=False):
        self._x, self._y, self._width, self._height, self._null = x, y, width, height, null

    def isNull(self):
        return self._null

    def x(self):
        return self._x

    def y(self):
        return self._y

    def width(self):
        return self._width

    def height(self):
        return self._height

    def left(self):
        return self._x

    def right(self):
        return self._x + self._width - 1

    def top(self):
        return self._y

    def bottom(self):
        return self._y + self._height - 1


def _snap_window(main_geo=None, own_geo=None):
    attached = []
    moves = []
    main = None
    if main_geo is not None:
        main = SimpleNamespace(
            isMinimized=lambda: False,
            isMaximized=lambda: False,
            frameGeometry=lambda: main_geo,
        )
    window = SimpleNamespace(
        _snapping_to_main_window=False,
        _closing=False,
        isFullScreen=lambda: False,
        main_window=main,
        frameGeometry=lambda: own_geo,
        _snap_threshold=15,
        _set_magnetically_attached=lambda value: attached.append(value),
        move=lambda x, y: moves.append((x, y)),
    )
    return window, attached, moves


@pytest.mark.parametrize(
    "own,expected",
    [
        (_Geo(200, 20, 50, 50), (200, 20)),
        (_Geo(-49, 20, 50, 50), (-50, 20)),
        (_Geo(5, 20, 50, 50), (0, 20)),
        (_Geo(150, 20, 50, 50), (150, 20)),
        (_Geo(20, 200, 50, 50), (20, 200)),
        (_Geo(20, -49, 50, 50), (20, -50)),
        (_Geo(20, 5, 50, 50), (20, 0)),
        (_Geo(20, 150, 50, 50), (20, 150)),
    ],
)
def test_snap_to_each_main_window_edge(own, expected):
    window, attached, moves = _snap_window(_Geo(0, 0, 200, 200), own)
    kline.KLineChartWindow._snap_to_main_window_edges(window)
    assert attached[-1] is True
    if moves:
        assert moves[-1] == expected
    assert window._snapping_to_main_window is False


def test_snap_guards_invalid_geometry_and_window_state():
    for window in (
        SimpleNamespace(_snapping_to_main_window=True, _closing=False, isFullScreen=lambda: False),
        SimpleNamespace(_snapping_to_main_window=False, _closing=True, isFullScreen=lambda: False),
        SimpleNamespace(_snapping_to_main_window=False, _closing=False, isFullScreen=lambda: True),
    ):
        kline.KLineChartWindow._snap_to_main_window_edges(window)
    window, attached, _ = _snap_window()
    kline.KLineChartWindow._snap_to_main_window_edges(window)
    assert attached == [False]
    window, attached, _ = _snap_window(_Geo(0, 0, 1, 1, null=True), _Geo(50, 50, 1, 1))
    kline.KLineChartWindow._snap_to_main_window_edges(window)
    assert attached == [False]
    main = SimpleNamespace(isMinimized=lambda: True, isMaximized=lambda: False)
    window.main_window = main
    kline.KLineChartWindow._snap_to_main_window_edges(window)
    assert attached[-1] is False
    main = SimpleNamespace(
        isMinimized=lambda: False,
        isMaximized=lambda: False,
        frameGeometry=lambda: (_ for _ in ()).throw(RuntimeError("gone")),
    )
    window.main_window = main
    kline.KLineChartWindow._snap_to_main_window_edges(window)
    assert attached[-1] is False


def test_magnetic_state_and_theme_flow():
    calls = []
    window = SimpleNamespace(
        _magnetically_attached=False,
        _apply_qt_theme=lambda: calls.append("qt"),
        _apply_chart_glass_mode=lambda: calls.append("glass"),
    )
    kline.KLineChartWindow._set_magnetically_attached(window, False)
    assert calls == []
    kline.KLineChartWindow._set_magnetically_attached(window, True)
    assert calls == ["qt", "glass"] and window._magnetically_attached
    window._refresh_header_context = lambda: calls.append("header")
    window._apply_chart_theme = lambda: calls.append("chart")
    kline.KLineChartWindow._on_theme_changed(window, "dark")
    assert calls[-3:] == ["qt", "header", "chart"]


def test_chart_theme_market_and_glass_scripts(monkeypatch):
    monkeypatch.setattr(kline, "build_kline_theme_colors", lambda: {"theme": True})
    monkeypatch.setattr(kline, "build_kline_market_state", lambda code: {"code": code})
    browser = _Browser()
    window = SimpleNamespace(_closing=False, browser=browser, code="1", _magnetically_attached=True)
    kline.KLineChartWindow._apply_chart_theme(window, animate=False)
    kline.KLineChartWindow._apply_chart_market_state(window)
    kline.KLineChartWindow._apply_chart_glass_mode(window)
    scripts = [args[0] for args in browser._page.calls]
    assert any("applyTheme" in script for script in scripts)
    assert any("applyMarketState" in script for script in scripts)
    assert any("setGlassMode" in script for script in scripts)
    window._closing = True
    before = len(browser._page.calls)
    kline.KLineChartWindow._apply_chart_theme(window)
    kline.KLineChartWindow._apply_chart_market_state(window)
    kline.KLineChartWindow._apply_chart_glass_mode(window)
    assert len(browser._page.calls) == before
    window._closing = False
    window.browser = _Browser(page_raises=True)
    kline.KLineChartWindow._apply_chart_theme(window)


class _Geometry:
    def __init__(self, null=False):
        self.null = null

    def isNull(self):
        return self.null


def test_fullscreen_enter_leave_and_toggle():
    state = {"fullscreen": False}
    calls = []
    button = _Button()
    geometry = _Geometry()
    window = SimpleNamespace(
        _closing=False,
        isFullScreen=lambda: state["fullscreen"],
        geometry=lambda: geometry,
        _set_magnetically_attached=lambda value: calls.append(("attached", value)),
        setMinimumSize=lambda *args: calls.append(("min", args)),
        setMaximumSize=lambda *args: calls.append(("max", args)),
        showFullScreen=lambda: state.update(fullscreen=True),
        showNormal=lambda: state.update(fullscreen=False),
        setGeometry=lambda value: calls.append(("geometry", value)),
        btn_fullscreen=button,
        _apply_qt_theme=lambda: calls.append(("theme",)),
        _enter_fullscreen=lambda: kline.KLineChartWindow._enter_fullscreen(window),
        _leave_fullscreen=lambda: kline.KLineChartWindow._leave_fullscreen(window),
    )
    kline.KLineChartWindow._toggle_fullscreen(window)
    assert state["fullscreen"] and window._fullscreen_geometry is geometry
    kline.KLineChartWindow._toggle_fullscreen(window)
    assert not state["fullscreen"] and ("geometry", geometry) in calls
    kline.KLineChartWindow._leave_fullscreen(window)
    window._closing = True
    kline.KLineChartWindow._enter_fullscreen(window)


def test_market_date_quote_normalize_and_load_delegates(monkeypatch):
    monkeypatch.setattr(kline.MarketCalendar, "infer_market", lambda code: "TW")
    monkeypatch.setattr(kline.MarketCalendar, "get_latest_trade_date", lambda market: dt.date(2026, 7, 15))
    monkeypatch.setattr(kline, "get_cn_target_trade_date", lambda: "target")
    monkeypatch.setattr(kline, "build_asian_rt_quote", lambda *args, **kwargs: {"args": args, "kwargs": kwargs})
    monkeypatch.setattr(kline, "normalize_daily_df_index", lambda df, logger: (df, logger))
    loads = []
    monkeypatch.setattr(kline, "load_and_draw", lambda window: loads.append(window))
    import ui.tabs.asian_market_tab as asian_tab

    monkeypatch.setattr(asian_tab, "GLOBAL_ASIAN_RT_CACHE", {"2330.TW": {"close": 1}})
    window = SimpleNamespace(code="2330.TW", _log="log", _render_generation=1)
    window._get_market = lambda: kline.KLineChartWindow._get_market(window)
    assert kline.KLineChartWindow._get_market(window) == "TW"
    assert kline.KLineChartWindow._get_cn_target_trade_date(window) == "target"
    assert kline.KLineChartWindow._build_asian_rt_quote(window)["args"][0] == "2330.TW"
    assert kline.KLineChartWindow._normalize_daily_df_index(window, "df") == ("df", "log")
    kline.KLineChartWindow._load_and_draw(window)
    assert window._render_generation == 2 and loads == [window]


def test_pending_chart_status_and_load_finished_paths():
    statuses = []
    calls = []
    window = SimpleNamespace(
        _closing=False,
        _set_status_message=lambda text, tone="info": statuses.append((text, tone)),
        _apply_chart_market_state=lambda: calls.append("market"),
        _apply_chart_glass_mode=lambda: calls.append("glass"),
    )
    kline.KLineChartWindow._set_pending_chart_status(window, " done ", "")
    assert window._pending_chart_status == ("done", "info")
    kline.KLineChartWindow._finish_pending_chart_status(window)
    assert statuses == [("done", "info")] and window._pending_chart_status is None
    kline.KLineChartWindow._finish_pending_chart_status(window)
    window._pending_chart_status = ("ok", "success")
    window._finish_pending_chart_status = lambda: kline.KLineChartWindow._finish_pending_chart_status(window)
    kline.KLineChartWindow._on_chart_load_finished(window, True)
    assert calls == ["market", "glass"] and statuses[-1] == ("ok", "success")
    window._pending_chart_status = ("bad", "info")
    kline.KLineChartWindow._on_chart_load_finished(window, False)
    assert statuses[-1][1] == "error"
    window._pending_chart_status = None
    kline.KLineChartWindow._on_chart_load_finished(window, False)
    window._closing = True
    kline.KLineChartWindow._on_chart_load_finished(window, True)


def _capture_asian_task(monkeypatch):
    calls = []
    monkeypatch.setattr(
        kline,
        "_submit_owned_kline_task",
        lambda window, name, fn, success, error, task_id, timeout: calls.append(
            SimpleNamespace(fn=fn, success=success, error=error, task_id=task_id, timeout=timeout)
        ),
    )
    return calls


def test_load_asian_chart_background_fetch_and_success_paths(monkeypatch):
    calls = _capture_asian_task(monkeypatch)
    import ui.tabs.asian_market_tab as asian_tab
    import ui.tabs.asian_market_workers as workers

    monkeypatch.setattr(asian_tab, "GLOBAL_ASIAN_RT_CACHE", {})
    monkeypatch.setattr(asian_tab, "JSON_CACHE", "cache.json")
    monkeypatch.setattr(kline.MarketCalendar, "get_latest_trade_date", lambda market: dt.date(2026, 7, 15))
    stock = {
        "klines": [
            {"date": "bad"},
            {"date": "2026-07-14", "open": 1, "high": 2, "low": 1, "close": 2},
        ]
    }
    monkeypatch.setattr(kline, "load_cached_asian_stock", lambda path, code: stock)
    monkeypatch.setattr(workers, "fetch_asian_realtime_quote", lambda code: {"date": "2026-07-15", "close": 3})
    monkeypatch.setattr(kline, "_runtime_is_current_request", lambda *args: True)
    monkeypatch.setattr(kline, "build_asian_history_df", lambda *args, **kwargs: _frame())
    monkeypatch.setattr(kline, "apply_asian_live_quote", lambda df, quote, market: df.assign(close=99))
    statuses = []
    renders = []
    window = SimpleNamespace(
        code="2330.TW",
        _render_generation=2,
        _get_market=lambda: "TW",
        _log=_Log(),
        vcp_data={},
        _refresh_header_context=lambda: None,
        _normalize_daily_df_index=lambda df: df,
        _render_chart=lambda df, loading=False: renders.append(df),
        _set_status_message=lambda text, tone="info": statuses.append((text, tone)),
    )
    kline.KLineChartWindow._load_asian_chart(window)
    result = calls[-1].fn(None)
    assert result[2] is True and result[1]["close"] == 3
    calls[-1].success(result)
    assert renders and float(renders[-1].iloc[0]["close"]) == 99
    assert asian_tab.GLOBAL_ASIAN_RT_CACHE["2330.TW"]["close"] == 3
    calls[-1].error("bad cache")
    assert statuses[-1][1] == "error"


def test_load_asian_chart_missing_history_error_and_stale_paths(monkeypatch):
    calls = _capture_asian_task(monkeypatch)
    import ui.tabs.asian_market_tab as asian_tab

    monkeypatch.setattr(asian_tab, "GLOBAL_ASIAN_RT_CACHE", {})
    monkeypatch.setattr(asian_tab, "JSON_CACHE", "cache.json")
    monkeypatch.setattr(kline.MarketCalendar, "get_latest_trade_date", lambda market: dt.date(2026, 7, 15))
    monkeypatch.setattr(kline, "load_cached_asian_stock", lambda path, code: None)
    monkeypatch.setattr(kline, "_runtime_is_current_request", lambda *args: True)
    scheduled = []
    monkeypatch.setattr(
        kline, "schedule_asian_history_backfill", lambda *args, **kwargs: scheduled.append((args, kwargs))
    )
    statuses = []
    window = SimpleNamespace(
        code="7203.T",
        _render_generation=1,
        _get_market=lambda: "T",
        _log=_Log(),
        vcp_data={},
        _refresh_header_context=lambda: None,
        _normalize_daily_df_index=lambda df: df,
        _render_chart=lambda *args, **kwargs: None,
        _set_status_message=lambda text, tone="info": statuses.append((text, tone)),
    )
    kline.KLineChartWindow._load_asian_chart(window)
    calls[-1].success((None, None, False, None))
    assert scheduled
    monkeypatch.setattr(kline, "build_asian_history_df", lambda *args, **kwargs: None)
    for exc in (RuntimeError("ordinary"), RuntimeError("429")):
        monkeypatch.setattr(kline, "is_yf_rate_limit_error", lambda value, exc=exc: value is exc and "429" in str(exc))
        monkeypatch.setattr(kline, "mark_yf_rate_limited", lambda value: 30)
        calls[-1].success(({}, None, False, exc))
    assert window._log.warnings
    calls[-1].success(({}, None, False, None))
    assert statuses[-1][1] == "warning"
    with pytest.raises(AssertionError):
        calls[-1].success(({}, None, False, AssertionError("unexpected")))
    monkeypatch.setattr(kline, "_runtime_is_current_request", lambda *args: False)
    before = len(statuses)
    calls[-1].success(None)
    calls[-1].error("late")
    assert len(statuses) == before


def test_render_chart_guards_first_incremental_and_loading(monkeypatch):
    monkeypatch.setattr(kline, "calculate_scan_indicators", lambda df: df)
    monkeypatch.setattr(kline, "build_kline_echarts_payload", lambda *args, **kwargs: {"dates": ["d"], "values": [1]})
    monkeypatch.setattr(kline, "build_kline_html", lambda **kwargs: "<html>chart</html>")
    monkeypatch.setattr(kline, "build_kline_theme_colors", lambda: {"theme": True})
    statuses = []
    pending = []
    starts = []
    browser = _Browser()
    window = SimpleNamespace(
        _closing=False,
        browser=browser,
        _normalize_daily_df_index=lambda df: df,
        _set_status_message=lambda text, tone="info": statuses.append((text, tone)),
        _set_pending_chart_status=lambda text, tone: pending.append((text, tone)),
        _start_rt_timer=lambda: starts.append(True),
        _replace_chart_data_or_reload=lambda *args, **kwargs: starts.append("replace"),
        code="1",
        name="one",
        vcp_data={},
    )
    kline.KLineChartWindow._render_chart(window, _frame(4), loading=False)
    assert statuses[-1][1] == "warning"
    statuses.clear()
    kline.KLineChartWindow._render_chart(window, _frame(), loading=True)
    assert browser.html and starts == [] and pending[-1][1] == "info"
    assert window._last_chart_points == 1 and window._last_chart_payload_bytes > 0
    kline.KLineChartWindow._render_chart(window, _frame(), loading=False)
    assert "replace" in starts and starts[-1] is True and pending[-1][1] == "success"
    window._closing = True
    before = len(browser.html)
    kline.KLineChartWindow._render_chart(window, _frame(), loading=False)
    assert len(browser.html) == before


def test_render_chart_converts_date_column_and_polars_like(monkeypatch):
    class PolarsLike:
        def to_pandas(self):
            frame = _frame().reset_index().rename(columns={"index": "date"})
            frame.index = range(len(frame))
            return frame

    monkeypatch.setattr(kline, "calculate_scan_indicators", lambda df: df)
    monkeypatch.setattr(kline, "build_kline_echarts_payload", lambda *args, **kwargs: {"dates": []})
    monkeypatch.setattr(kline, "build_kline_html", lambda **kwargs: "html")
    monkeypatch.setattr(kline, "build_kline_theme_colors", lambda: {})
    browser = _Browser()
    window = SimpleNamespace(
        _closing=False,
        browser=browser,
        _normalize_daily_df_index=lambda df: df,
        _set_status_message=lambda *args, **kwargs: None,
        _set_pending_chart_status=lambda *args: None,
        _start_rt_timer=lambda: None,
        code="1",
        name="one",
        vcp_data={},
    )
    kline.KLineChartWindow._render_chart(window, PolarsLike(), loading=False)
    assert isinstance(window.df.index, pd.DatetimeIndex)


def test_replace_chart_data_incremental_callbacks_and_fallback_errors():
    finished = []
    log = _Log()
    browser = _Browser()
    window = SimpleNamespace(
        _closing=False,
        browser=browser,
        _finish_pending_chart_status=lambda: finished.append(True),
        _log=log,
    )
    kline.KLineChartWindow._replace_chart_data_or_reload(window, "html", "url", title="t", echarts_data={})
    callback = browser._page.calls[-1][1]
    callback(True)
    callback(False)
    assert finished and browser.html[-1] == ("html", "url")
    window._closing = True
    callback(False)
    window._closing = False
    window.browser = None
    callback(False)

    window.browser = _Browser(page_raises=True)
    kline.KLineChartWindow._replace_chart_data_or_reload(window, "html", "url", title="t", echarts_data={})
    assert log.debugs
    window.browser = _Browser(page_raises=True, html_raises=True)
    kline.KLineChartWindow._replace_chart_data_or_reload(window, "html", "url", title="t", echarts_data={})
    assert len(log.debugs) >= 3


class _Timer:
    def __init__(self, *args):
        self.timeout = SimpleNamespace(connect=lambda callback: setattr(self, "callback", callback))
        self.started = []
        self.stopped = False

    def start(self, interval):
        self.started.append(interval)

    def stop(self):
        self.stopped = True


def test_realtime_timer_global_quotes_and_navigation(monkeypatch):
    monkeypatch.setattr(kline, "QTimer", _Timer)
    states = []
    monkeypatch.setattr(kline.MarketCalendar, "is_quote_refresh_time", lambda market: False)
    window = SimpleNamespace(
        code="1",
        _get_market=lambda: "TW",
        _apply_chart_market_state=lambda: states.append(True),
        _rt_timer=_Timer(),
    )
    kline.KLineChartWindow._start_rt_timer(window)
    assert window._rt_timer.stopped
    monkeypatch.setattr(kline.MarketCalendar, "is_quote_refresh_time", lambda market: True)
    window._get_market = lambda: "CN"
    window._rt_timer = _Timer()
    kline.KLineChartWindow._start_rt_timer(window)
    assert window._rt_timer.stopped
    window._get_market = lambda: "TW"
    window._rt_timer = None
    window._on_rt_timer = lambda: None
    kline.KLineChartWindow._start_rt_timer(window)
    assert window._rt_timer.started == [60000]

    refreshed = []
    monkeypatch.setattr(kline, "refresh_last_bar", lambda window, quote: refreshed.append(quote))
    kline.KLineChartWindow._on_global_rt_quotes(window, {"1": {"close": 2}})
    assert refreshed == []
    window._get_market = lambda: "CN"
    kline.KLineChartWindow._on_global_rt_quotes(window, {})
    kline.KLineChartWindow._on_global_rt_quotes(window, {"2": {}})
    kline.KLineChartWindow._on_global_rt_quotes(window, {"1": {"close": 2}})
    assert refreshed[-1]["close"] == 2
    monkeypatch.setattr(kline, "refresh_last_bar", lambda *args: (_ for _ in ()).throw(ValueError("bad")))
    kline.KLineChartWindow._on_global_rt_quotes(window, {"1": {}})

    switches = []
    window.code_list = []
    window._switching = False
    window.current_idx = 0
    window._switch_to_stock = lambda index: switches.append(index)
    kline.KLineChartWindow._nav_stock(window, 1)
    window.code_list = [1, 2]
    window._switching = True
    kline.KLineChartWindow._nav_stock(window, 1)
    window._switching = False
    kline.KLineChartWindow._nav_stock(window, 1)
    kline.KLineChartWindow._nav_stock(window, 2)
    assert switches == [1]


def test_nav_buttons_and_switch_stock_cleanup():
    prev, nxt = _Button(), _Button()
    window = SimpleNamespace(code_list=[], btn_prev=prev, btn_next=nxt, current_idx=0)
    kline.KLineChartWindow._update_nav_buttons(window)
    assert prev.enabled is False and nxt.enabled is False
    window.code_list = [1, 2]
    kline.KLineChartWindow._update_nav_buttons(window)
    assert prev.enabled is False and nxt.enabled is True

    timer = _Timer()
    calls = []
    workspace = SimpleNamespace(select_code_row=lambda *args, **kwargs: calls.append((args, kwargs)))
    window = SimpleNamespace(
        _switching=False,
        _rt_timer=timer,
        code="old",
        _render_generation=4,
        code_list=[{"代码": "new", "名称": "New", "__source_tab_index": "bad"}],
        current_idx=0,
        _abandon_render_tasks=lambda *args: calls.append(("abandon", args)),
        _resolve_vcp_context=lambda *args: {"ctx": True},
        setWindowTitle=lambda title: calls.append(("title", title)),
        _refresh_header_context=lambda: calls.append(("header",)),
        main_window=SimpleNamespace(_workspace=workspace),
        _check_fav_status=lambda: calls.append(("fav",)),
        _load_and_draw=lambda: calls.append(("load",)),
        _update_nav_buttons=lambda: calls.append(("nav",)),
    )
    kline.KLineChartWindow._switch_to_stock(window, 0)
    assert window.code == "new" and window.name == "New" and window._switching is False
    assert any(call[0] == "abandon" for call in calls)
    assert calls[-1] == ("nav",)

    window.code = ""
    window.main_window = None
    window.code_list = [{"代码": "other", "名称": "Other", "__source_tab_index": 2}]
    kline.KLineChartWindow._switch_to_stock(window, 0)


def test_abandon_and_timer_delegates(monkeypatch):
    calls = []
    monkeypatch.setattr(kline, "_abandon_owned_kline_tasks", lambda *args: calls.append(args))
    monkeypatch.setattr(kline, "poll_rt_update", lambda window: calls.append((window,)))
    window = SimpleNamespace()
    kline.KLineChartWindow._abandon_render_tasks(window, "1", 2)
    kline.KLineChartWindow._on_rt_timer(window)
    assert len(calls) == 2


def test_abandon_owned_tasks_cancels_lifecycle_and_all_transient_keys(monkeypatch):
    cancelled = []
    abandoned = []
    lifecycle = SimpleNamespace(cancel=lambda *args, **kwargs: cancelled.append((args, kwargs)))
    monkeypatch.setattr(kline.background_job_runner, "abandon", lambda key: abandoned.append(str(key)))
    window = SimpleNamespace(_task_lifecycle=lifecycle)
    kline._abandon_owned_kline_tasks(window, " 000001 ", 7)
    assert len(cancelled) == 4 and len(abandoned) == 4
    kline._abandon_owned_kline_tasks(SimpleNamespace(_task_lifecycle=None), "", 1)


def test_real_kline_widget_event_and_close_lifecycle(monkeypatch):
    from PyQt6.QtCore import QEvent, QPoint, QSize, Qt
    from PyQt6.QtGui import QMoveEvent, QResizeEvent, QShowEvent
    from PyQt6.QtWidgets import QWidget

    class Signal:
        def __init__(self):
            self.callbacks = []

        def connect(self, callback):
            self.callbacks.append(callback)

        def disconnect(self, callback):
            if callback in self.callbacks:
                self.callbacks.remove(callback)

    class Browser(QWidget):
        def __init__(self):
            super().__init__()
            self.loadFinished = Signal()
            self.stopped = False

        def stop(self):
            self.stopped = True

    class ImmediateTimer:
        @staticmethod
        def singleShot(delay, callback):
            return None

    monkeypatch.setattr(kline, "QTimer", ImmediateTimer)
    monkeypatch.setattr(kline.KLineChartWindow, "_resolve_vcp_context", lambda self, *args: {})
    monkeypatch.setattr(kline.KLineChartWindow, "_apply_qt_theme", lambda self: None)
    monkeypatch.setattr(kline.KLineChartWindow, "_check_fav_status", lambda self: setattr(self, "is_fav", False))
    monkeypatch.setattr(kline.KLineChartWindow, "_refresh_header_context", lambda self: None)
    monkeypatch.setattr(kline.KLineChartWindow, "_set_status_message", lambda self, *args, **kwargs: None)
    monkeypatch.setattr(kline, "enable_windows_native_shadow", lambda window: None)
    monkeypatch.setattr(kline, "enable_windows_system_backdrop", lambda *args, **kwargs: None)
    monkeypatch.setattr(kline, "shutdown_task_lifecycle_for_owner", lambda *args, **kwargs: None)
    monkeypatch.setattr(kline.KLineChartWindow, "_abandon_render_tasks", lambda *args: None)

    browser = Browser()
    window = kline.KLineChartWindow(
        None,
        "000001",
        "Ping",
        SimpleNamespace(is_online=lambda: False),
        vcp_data={},
        browser=browser,
    )
    try:
        window.showEvent(QShowEvent())
        window.showEvent(QShowEvent())
        window.resizeEvent(QResizeEvent(QSize(800, 600), QSize(700, 500)))
        window.moveEvent(QMoveEvent(QPoint(10, 10), QPoint(0, 0)))

        toggles = []
        window._toggle_fullscreen = lambda: toggles.append(True)

        class Event:
            accepted = False

            def type(self):
                return QEvent.Type.MouseButtonDblClick

            def button(self):
                return Qt.MouseButton.LeftButton

            def accept(self):
                self.accepted = True

        event = Event()
        assert window.eventFilter(window.title_bar, event) is True
        assert toggles and event.accepted
        assert window.eventFilter(window.title_bar, QEvent(QEvent.Type.User)) is False
        window.close()
        assert window.browser is None and browser.stopped
        assert not browser.updatesEnabled()
    finally:
        if not window._closing:
            window.close()
