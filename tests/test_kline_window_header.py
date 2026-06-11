# -*- coding: utf-8 -*-
import datetime as dt
import json

import pandas as pd
from PyQt6.QtCore import QEvent, Qt, QUrl
from PyQt6.QtWidgets import QApplication, QSizePolicy, QWidget

from core.task_manager import task_manager
from ui import kline_window_header as header_module
from ui import kline_window_qt as kline_module
from ui.components import kline_window_manager as manager_module
from ui.components.kline_window_manager import KLineWindowManager
from ui.kline_chart_payload import build_kline_html, build_kline_theme_colors
from ui.tabs import asian_market_tab as asian_module
from ui.tabs import asian_market_workers as asian_workers_module
from ui.theme import THEME_YAOHEI, theme_manager
from ui.theme_tokens import build_ui_tokens
from vcp.fetchers import asian_kline_fetcher as asian_fetcher_module


class _DummyProvider:
    _offline = True


class _LiveProvider:
    _offline = False


def _dispose_kline_window(window):
    if window._rt_timer is not None:
        window._rt_timer.stop()
    window.close()
    app = QApplication.instance()
    if app is not None:
        app.processEvents()
    try:
        window.deleteLater()
    except RuntimeError:
        return
    if app is not None:
        app.processEvents()


class _FakeSignal:
    def __init__(self):
        self.callbacks = []

    def connect(self, callback):
        self.callbacks.append(callback)

    def disconnect(self, callback):
        if callback in self.callbacks:
            self.callbacks.remove(callback)


class _FakeMouseEvent:
    def __init__(self, event_type, button):
        self._event_type = event_type
        self._button = button
        self.accepted = False

    def type(self):
        return self._event_type

    def button(self):
        return self._button

    def accept(self):
        self.accepted = True


class _FakeWebPage:
    def __init__(self):
        self.deleted = False
        self.script_callback = None

    def deleteLater(self):
        self.deleted = True

    def runJavaScript(self, _script, callback):
        self.script_callback = callback


class _FakeWebEngineView(QWidget):
    last_instance = None

    def __init__(self):
        super().__init__()
        type(self).last_instance = self
        self.loadFinished = _FakeSignal()
        self._page = _FakeWebPage()
        self.html_calls = []
        self.url_calls = []
        self.stopped = False
        self.deleted = False

    def page(self):
        return self._page

    def stop(self):
        self.stopped = True

    def setHtml(self, html, base_url=None):
        url_text = base_url.toString() if hasattr(base_url, "toString") else str(base_url)
        self.html_calls.append((html, url_text))

    def setUrl(self, url):
        self.url_calls.append(url.toString() if hasattr(url, "toString") else str(url))

    def deleteLater(self):
        self.deleted = True


def _build_fake_webengine_kline(monkeypatch):
    monkeypatch.setattr(kline_module, "QWebEngineView", _FakeWebEngineView)
    monkeypatch.setattr(kline_module.KLineChartWindow, "_load_and_draw", lambda self: None)
    monkeypatch.setattr(
        kline_module.KLineChartWindow,
        "_check_fav_status",
        lambda self: setattr(self, "is_fav", False),
    )
    return kline_module.KLineChartWindow(
        None,
        "000001",
        "平安银行",
        _DummyProvider(),
        vcp_data={},
        code_list=[{"代码": "000001", "名称": "平安银行"}],
        current_idx=0,
    )


def test_kline_close_stops_webengine_view_without_manual_delete(monkeypatch):
    window = _build_fake_webengine_kline(monkeypatch)
    browser = _FakeWebEngineView.last_instance
    page = browser.page()
    abandoned = []

    class _Runner:
        def abandon(self, task_key):
            abandoned.append(str(task_key))
            return True

    monkeypatch.setattr(kline_module, "background_job_runner", _Runner())
    browser.html_calls.clear()
    browser.url_calls.clear()

    try:
        window.close()
        app = QApplication.instance()
        if app is not None:
            app.processEvents()

        assert window.browser is None
        assert browser.stopped is True
        assert browser.deleted is False
        assert page.deleted is False
        assert browser.html_calls == []
        assert browser.url_calls == []
        assert "kline_000001" in abandoned
        assert "kline_asian_000001" in abandoned
    finally:
        _dispose_kline_window(window)


def test_kline_late_render_callback_is_ignored_after_close(monkeypatch):
    window = _build_fake_webengine_kline(monkeypatch)
    browser = _FakeWebEngineView.last_instance
    browser.html_calls.clear()
    df = pd.DataFrame(
        {
            "open": [10.0, 10.1, 10.2, 10.3, 10.4, 10.5],
            "high": [10.2, 10.3, 10.4, 10.5, 10.6, 10.7],
            "low": [9.9, 10.0, 10.1, 10.2, 10.3, 10.4],
            "close": [10.1, 10.2, 10.3, 10.4, 10.5, 10.6],
            "volume": [1000.0, 1100.0, 1200.0, 1300.0, 1400.0, 1500.0],
        },
        index=pd.date_range("2026-04-01", periods=6),
    )

    try:
        window._closing = True
        window._render_chart(df, loading=False)

        assert browser.html_calls == []
    finally:
        _dispose_kline_window(window)


def test_kline_js_fallback_ignores_callback_after_close(monkeypatch):
    window = _build_fake_webengine_kline(monkeypatch)
    browser = _FakeWebEngineView.last_instance
    browser.html_calls.clear()

    try:
        window._replace_chart_data_or_reload(
            "<html>reload</html>",
            QUrl("about:blank"),
            title="平安银行 (000001) 日线",
            echarts_data={"dates": []},
        )

        callback = browser.page().script_callback
        assert callback is not None

        window._closing = True
        callback(False)

        assert browser.html_calls == []
    finally:
        _dispose_kline_window(window)


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
    assert "window.applyTheme" in html
    assert "themeState.crosshair_line" in html
    assert "themeState.datazoom_bg" in html
    assert "scrollbar_handle" in html
    assert "themeState.mono_font_family" in html
    assert 'font-feature-settings: "tnum" 1' in html
    assert "window.replaceKlineData" in html
    build_option_body = html[html.index("function buildOption()") : html.index("chart.setOption(buildOption());")]
    assert "const data = splitData(rawData);" in build_option_body
    assert html.count("const data = splitData(rawData);") == 1


def test_yaohei_kline_theme_colors_bind_terminal_chart_tokens(monkeypatch):
    monkeypatch.setattr(theme_manager, "_current_name", "曜黑")

    colors = build_kline_theme_colors()

    assert colors["bg_canvas"] == THEME_YAOHEI["KLINE_BG_CANVAS"]
    assert colors["bg_canvas"] == THEME_YAOHEI["BG_CANVAS"]
    assert colors["grid_line"] == THEME_YAOHEI["KLINE_GRID_LINE"]
    assert colors["crosshair_line"] == THEME_YAOHEI["KLINE_CROSSHAIR_LINE"]
    assert colors["pointer_bg"] == THEME_YAOHEI["KLINE_POINTER_BG"]
    assert colors["datazoom_fill"] == THEME_YAOHEI["KLINE_DATAZOOM_FILL"]
    assert colors["datazoom_handle"] == THEME_YAOHEI["SCROLLBAR_HANDLE"]
    assert colors["tooltip_bg"] == THEME_YAOHEI["KLINE_TOOLTIP_BG"]
    assert colors["up_gradient_top"] == THEME_YAOHEI["KLINE_UP_GRADIENT_TOP"]
    assert colors["down_gradient_bottom"] == THEME_YAOHEI["KLINE_DOWN_GRADIENT_BOTTOM"]
    assert colors["volume_spike"] == THEME_YAOHEI["KLINE_VOLUME_SPIKE"]
    assert colors["depth_line"] == THEME_YAOHEI["KLINE_DEPTH_LINE"]


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
        _dispose_kline_window(window)


def test_kline_summary_cards_expand_and_elide_long_watchlist_note(monkeypatch):
    monkeypatch.setattr(kline_module, "QWebEngineView", QWidget)
    monkeypatch.setattr(kline_module.KLineChartWindow, "_load_and_draw", lambda self: None)
    monkeypatch.setattr(
        kline_module.KLineChartWindow,
        "_check_fav_status",
        lambda self: setattr(self, "is_fav", True),
    )

    long_note = "光纤光缆、特种光缆和通信网络平台，公开资料披露AI算力带动高性能特种光纤光缆需求"
    window = kline_module.KLineChartWindow(
        None,
        "600522",
        "中天科技",
        _DummyProvider(),
        vcp_data={
            "__source_tab_key": "watchlist",
            "摘要": long_note,
            "业绩异动": "净买204.005.83万",
        },
        code_list=[{"代码": "600522", "名称": "中天科技"}],
        current_idx=0,
    )
    try:
        assert [window.summary_widget.layout().stretch(idx) for idx in range(3)] == [20, 48, 24]
        assert all(card["frame"].maximumWidth() > 10000 for card in window.summary_cards)
        assert all(
            label.sizePolicy().horizontalPolicy() == QSizePolicy.Policy.Ignored
            for card in window.summary_cards
            for label in card["labels"]
        )
        tooltip = window.summary_cards[1]["labels"][0].toolTip()
        assert "width: 560px" in tooltip
        assert "font-size: 14px" in tooltip
        assert "line-height: 1.55" in tooltip
        assert "<br/>" not in tooltip
        assert "光纤光缆、特种光缆和通信网络平台" in tooltip
    finally:
        _dispose_kline_window(window)


def test_kline_summary_cards_use_uniform_value_typography(monkeypatch):
    monkeypatch.setattr(kline_module, "QWebEngineView", QWidget)
    monkeypatch.setattr(kline_module.KLineChartWindow, "_load_and_draw", lambda self: None)
    monkeypatch.setattr(
        kline_module.KLineChartWindow,
        "_check_fav_status",
        lambda self: setattr(self, "is_fav", True),
    )

    window = kline_module.KLineChartWindow(
        None,
        "601138",
        "工业富联",
        _DummyProvider(),
        vcp_data={
            "__source_tab_key": "watchlist",
            "来源": "手动",
            "摘要": "证券强：2025年报披露公司与全球头部客户协同下一代AI服务器及液冷技术",
            "RPS强度": "97/78",
        },
        code_list=[{"代码": "601138", "名称": "工业富联"}],
        current_idx=0,
    )
    try:
        tokens = build_ui_tokens(theme_manager.current_theme)
        expected_size = tokens["font"]["size_sm"]
        expected_weight = tokens["font"]["weight_semibold"]
        expected_family = tokens["font"]["family"]

        assert window._summary_value_size == expected_size
        assert window._summary_value_weight == expected_weight
        assert window._summary_value_font == expected_family

        card_text = "".join(
            label.text()
            for card in window.summary_cards
            for label in card["labels"]
            if label.text()
        )
        assert f"font-size:{expected_size}px" in card_text
        assert f"font-weight:{expected_weight}" in card_text
        assert "font-size:16px" not in card_text
        assert "font-weight:700" not in card_text
        assert tokens["font"]["mono_family"] not in card_text
    finally:
        _dispose_kline_window(window)


def test_kline_summary_tooltip_uses_natural_wrap_for_long_watchlist_note():
    note = (
        "电力电子平台，2025年报披露数据中心通用及AI服务器电源和系统产品拓展顺利，"
        "并聚焦AI算力基础设施供电系统；网络电源可支持通信、交换机、通用服务器和AI服务器场景。"
        "主板块保留数据中心电源，风险是AI电源研发投入压制利润、海外客户导入节奏和800VDC/BBU/Power Shelf规模收入验证。"
        "证据A/B。"
    )

    tooltip = header_module._build_summary_tooltip("备注", note)

    assert "width: 560px" in tooltip
    assert ">备注</div>" in tooltip
    assert "<br/>" not in tooltip
    assert "AI" in tooltip
    assert "拓展" in tooltip
    assert "顺利" in tooltip
    assert "800VDC/BBU/" in tooltip
    assert "Power Shelf规模收入验证" in tooltip


def test_kline_summary_tooltip_preserves_source_newlines_only():
    note = "第一行是原文自带换行。\n第二行仍然按悬浮框宽度自然换行。"

    tooltip = header_module._build_summary_tooltip("备注", note)

    assert "<br/>" in tooltip
    assert "第一行是原文自带换行" in tooltip
    assert "第二行仍然按悬浮框宽度自然换行" in tooltip


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
        badge_widgets = (window.market_badge_lbl, window.session_badge_lbl, window.feed_badge_lbl)
        assert len({badge.minimumHeight() for badge in badge_widgets}) == 1
        assert len({badge.maximumHeight() for badge in badge_widgets}) == 1
        assert all("border-radius" in badge.styleSheet() for badge in badge_widgets)
        assert all("max-height" in badge.styleSheet() for badge in badge_widgets)
        assert "background-color" in window.session_badge_lbl.styleSheet()
        assert "background-color" in window.feed_badge_lbl.styleSheet()
        window._set_status_message("chart updated 500 bars", tone="success")
        assert window.info_lbl.minimumHeight() >= window.market_badge_lbl.minimumHeight()
        assert "padding: 0 10px;" in window.info_lbl.styleSheet()
        assert "max-height" in window.info_lbl.styleSheet()
    finally:
        _dispose_kline_window(window)


def test_kline_magnetic_attach_switches_transparent_shell(monkeypatch):
    window = _build_fake_webengine_kline(monkeypatch)
    glass_calls = []
    monkeypatch.setattr(window, "_apply_chart_glass_mode", lambda: glass_calls.append(window._magnetically_attached))

    try:
        window._set_magnetically_attached(True)

        assert window._magnetically_attached is True
        assert "border: 1px solid rgba(0, 0, 0, 0)" in window.container.styleSheet()
        assert "background-color: rgba(0, 0, 0, 0)" in window.container.styleSheet()
        assert glass_calls == [True]

        window._set_magnetically_attached(False)

        assert window._magnetically_attached is False
        assert glass_calls == [True, False]
    finally:
        _dispose_kline_window(window)


def test_kline_fullscreen_toggle_uses_window_fullscreen_mode(monkeypatch):
    window = _build_fake_webengine_kline(monkeypatch)
    fullscreen_state = {"active": False}

    monkeypatch.setattr(window, "isFullScreen", lambda: fullscreen_state["active"])
    monkeypatch.setattr(window, "showFullScreen", lambda: fullscreen_state.__setitem__("active", True))
    monkeypatch.setattr(window, "showNormal", lambda: fullscreen_state.__setitem__("active", False))

    try:
        window._toggle_fullscreen()

        assert fullscreen_state["active"] is True
        assert window.btn_fullscreen.text() == "▣"
        assert "退出全屏" in window.btn_fullscreen.toolTip()

        window._toggle_fullscreen()

        assert fullscreen_state["active"] is False
        assert window.btn_fullscreen.text() == "□"
        assert "F11" in window.btn_fullscreen.toolTip()
    finally:
        _dispose_kline_window(window)


def test_kline_title_bar_double_click_toggles_fullscreen(monkeypatch):
    window = _build_fake_webengine_kline(monkeypatch)
    fullscreen_calls = []
    monkeypatch.setattr(window, "_toggle_fullscreen", lambda: fullscreen_calls.append("fullscreen"))

    try:
        event = _FakeMouseEvent(QEvent.Type.MouseButtonDblClick, Qt.MouseButton.LeftButton)

        assert window.eventFilter(window.title_bar, event) is True
        assert event.accepted is True
        assert fullscreen_calls == ["fullscreen"]
    finally:
        _dispose_kline_window(window)


def test_kline_window_has_no_widget_pill_mode(monkeypatch):
    window = _build_fake_webengine_kline(monkeypatch)

    try:
        assert not hasattr(window, "widget_mode_panel")
        assert not hasattr(window, "_toggle_widget_mode")
        assert not hasattr(window, "_enter_widget_mode")
        assert not hasattr(window, "_leave_widget_mode")
    finally:
        _dispose_kline_window(window)


def test_kline_window_defers_initial_load_until_next_event_turn(monkeypatch):
    scheduled = []
    load_calls = []
    monkeypatch.setattr(kline_module, "QWebEngineView", QWidget)
    monkeypatch.setattr(kline_module.QTimer, "singleShot", lambda delay, callback: scheduled.append((delay, callback)))
    monkeypatch.setattr(kline_module.KLineChartWindow, "_load_and_draw", lambda self: load_calls.append(self.code))
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
        assert load_calls == []
        assert any(delay == 0 and callback == window._load_and_draw for delay, callback in scheduled)
        assert window.info_lbl.text() == "正在准备图表..."
    finally:
        _dispose_kline_window(window)


def test_kline_manager_consumes_prewarm_but_uses_fresh_browser(monkeypatch):
    captured = {}

    class _WarmBrowser:
        def __init__(self):
            self.parent = "old"
            self.hidden = False
            self.deleted = False

        def hide(self):
            self.hidden = True

        def setParent(self, parent):
            self.parent = parent

        def deleteLater(self):
            self.deleted = True

    class _Chart:
        def __init__(self, **kwargs):
            captured.update(kwargs)
            self._visible = True

        def show(self):
            self._visible = True

        def raise_(self):
            return None

        def activateWindow(self):
            return None

        def isVisible(self):
            return self._visible

    manager = KLineWindowManager()
    manager._charts = []
    manager._prewarm_started = True
    manager._prewarm_cancelled = False
    manager._webengine_available = True
    manager._webengine_failure = ""
    warm_browser = _WarmBrowser()
    manager._prewarm_view = warm_browser
    monkeypatch.setattr(kline_module, "KLineChartWindow", _Chart)

    try:
        chart = manager.open_chart(
            main_window=None,
            code="000001",
            name="平安银行",
            data_provider=_DummyProvider(),
            vcp_data={},
            code_list=[],
            current_idx=0,
        )

        assert chart is manager._charts[-1]
        assert captured["browser"] is None
        assert warm_browser.hidden is True
        assert warm_browser.parent is None
        assert warm_browser.deleted is True
        assert manager._prewarm_view is None
        assert manager._prewarm_started is False
    finally:
        manager._charts = []
        manager._prewarm_view = None
        manager._prewarm_started = False
        manager._prewarm_cancelled = False
        manager._prewarm_expire_timer = None
        manager._webengine_available = None
        manager._webengine_failure = ""
        manager._webengine_preflight_started = False


def test_kline_manager_expires_unused_prewarm_browser():
    class _WarmBrowser:
        def __init__(self):
            self.parent = "old"
            self.hidden = False
            self.deleted = False

        def hide(self):
            self.hidden = True

        def setParent(self, parent):
            self.parent = parent

        def deleteLater(self):
            self.deleted = True

    manager = KLineWindowManager()
    manager._charts = []
    manager._prewarm_started = True
    manager._prewarm_cancelled = False
    manager._prewarm_expire_timer = None
    warm_browser = _WarmBrowser()
    manager._prewarm_view = warm_browser

    try:
        manager._expire_prewarm()

        assert warm_browser.hidden is True
        assert warm_browser.parent is None
        assert warm_browser.deleted is True
        assert manager._prewarm_view is None
        assert manager._prewarm_started is False
        assert manager._prewarm_cancelled is False
    finally:
        manager._charts = []
        manager._prewarm_view = None
        manager._prewarm_started = False
        manager._prewarm_cancelled = False
        manager._prewarm_expire_timer = None


def test_kline_manager_removes_destroyed_chart_reference(monkeypatch):
    captured = {}

    class _Signal:
        def __init__(self):
            self.callback = None

        def connect(self, callback):
            self.callback = callback

    class _Chart:
        def __init__(self, **kwargs):
            captured.update(kwargs)
            self.destroyed = _Signal()
            self._visible = True

        def show(self):
            self._visible = True

        def raise_(self):
            return None

        def activateWindow(self):
            return None

        def isVisible(self):
            return self._visible

    manager = KLineWindowManager()
    manager._charts = []
    manager._prewarm_view = None
    manager._prewarm_started = False
    manager._webengine_available = True
    manager._webengine_failure = ""
    monkeypatch.setattr(kline_module, "KLineChartWindow", _Chart)
    scheduled = []
    monkeypatch.setattr(manager, "_schedule_post_close_collect", lambda: scheduled.append(True))

    try:
        chart = manager.open_chart(
            main_window=None,
            code="000001",
            name="平安银行",
            data_provider=_DummyProvider(),
            vcp_data={},
            code_list=[],
            current_idx=0,
        )

        assert chart in manager._charts
        assert captured["browser"] is None

        chart._visible = False
        chart.destroyed.callback()

        assert manager._charts == []
        assert scheduled == [True]
    finally:
        manager._charts = []
        manager._prewarm_view = None
        manager._prewarm_started = False
        manager._prewarm_cancelled = False
        manager._prewarm_expire_timer = None
        manager._webengine_available = None
        manager._webengine_failure = ""
        manager._webengine_preflight_started = False


def test_kline_manager_post_close_cleanup_skips_forced_gc(monkeypatch):
    metrics = []

    manager = KLineWindowManager()
    manager._charts = []
    manager._post_close_collect_scheduled = True
    monkeypatch.setattr(
        manager_module,
        "record_metric",
        lambda metric, value, unit="", tags=None: metrics.append((metric, value, unit, tags or {})),
    )

    try:
        manager._run_post_close_collect()

        assert manager._post_close_collect_scheduled is False
        assert metrics == [("kline_post_close_gc_skipped", 1, "count", {"active_windows": "0"})]
    finally:
        manager._charts = []
        manager._post_close_collect_scheduled = False


def test_kline_manager_starts_async_preflight_before_prewarm(monkeypatch):
    started = []
    manager = KLineWindowManager()
    manager._charts = []
    manager._prewarm_view = None
    manager._prewarm_started = True
    manager._prewarm_cancelled = False
    manager._webengine_available = None
    manager._webengine_failure = ""
    manager._webengine_preflight_started = False
    monkeypatch.setattr(manager, "_start_webengine_preflight_async", lambda: started.append(True) or True)

    try:
        manager._run_prewarm()

        assert manager._prewarm_view is None
        assert manager._prewarm_started is False
        assert started == [True]
    finally:
        manager._charts = []
        manager._prewarm_view = None
        manager._prewarm_started = False
        manager._prewarm_cancelled = False
        manager._prewarm_expire_timer = None
        manager._webengine_available = None
        manager._webengine_failure = ""
        manager._webengine_preflight_started = False


def test_kline_manager_default_prewarm_is_preflight_only():
    manager = KLineWindowManager()
    manager._charts = []
    manager._prewarm_view = None
    manager._prewarm_started = True
    manager._prewarm_cancelled = False
    manager._prewarm_expire_timer = None
    manager._prewarm_hidden_view_enabled = False
    manager._webengine_available = True
    manager._webengine_failure = ""
    manager._webengine_preflight_started = False

    try:
        manager._run_prewarm()

        assert manager._prewarm_view is None
        assert manager._prewarm_started is False
    finally:
        manager._charts = []
        manager._prewarm_view = None
        manager._prewarm_started = False
        manager._prewarm_cancelled = False
        manager._prewarm_expire_timer = None
        manager._prewarm_hidden_view_enabled = False
        manager._webengine_available = None
        manager._webengine_failure = ""
        manager._webengine_preflight_started = False


def test_kline_manager_blocks_open_when_webengine_preflight_fails(monkeypatch):
    notified = []

    manager = KLineWindowManager()
    manager._charts = []
    manager._prewarm_view = None
    manager._prewarm_started = False
    manager._webengine_available = False
    manager._webengine_failure = "returncode=3221226505 0xc0000409"
    monkeypatch.setattr(
        manager,
        "_notify_webengine_unavailable",
        lambda main_window, code, name: notified.append((code, name)),
    )

    try:
        chart = manager.open_chart(
            main_window=None,
            code="000001",
            name="平安银行",
            data_provider=_DummyProvider(),
            vcp_data={},
            code_list=[],
            current_idx=0,
        )

        assert chart is None
        assert manager._charts == []
        assert notified == [("000001", "平安银行")]
    finally:
        manager._charts = []
        manager._prewarm_view = None
        manager._prewarm_started = False
        manager._prewarm_cancelled = False
        manager._prewarm_expire_timer = None
        manager._webengine_available = None
        manager._webengine_failure = ""
        manager._webengine_preflight_started = False


def test_kline_manager_does_not_block_when_webengine_preflight_is_running(monkeypatch):
    unavailable = []
    preparing = []
    ensure_calls = []

    manager = KLineWindowManager()
    manager._charts = []
    manager._prewarm_view = None
    manager._prewarm_started = False
    manager._webengine_available = None
    manager._webengine_failure = ""
    manager._webengine_preflight_started = True
    monkeypatch.setattr(manager, "_ensure_webengine_available", lambda: ensure_calls.append(True) or False)
    monkeypatch.setattr(
        manager,
        "_notify_webengine_unavailable",
        lambda main_window, code, name: unavailable.append((code, name, manager._webengine_failure)),
    )
    monkeypatch.setattr(
        manager,
        "_notify_webengine_preparing",
        lambda main_window, code, name: preparing.append((code, name)),
    )

    try:
        chart = manager.open_chart(
            main_window=None,
            code="000001",
            name="平安银行",
            data_provider=_DummyProvider(),
            vcp_data={},
            code_list=[],
            current_idx=0,
        )

        assert chart is None
        assert manager._charts == []
        assert ensure_calls == []
        assert unavailable == []
        assert preparing == [("000001", "平安银行")]
    finally:
        manager._charts = []
        manager._prewarm_view = None
        manager._prewarm_started = False
        manager._prewarm_cancelled = False
        manager._prewarm_expire_timer = None
        manager._webengine_available = None
        manager._webengine_failure = ""
        manager._webengine_preflight_started = False


def test_kline_manager_starts_async_preflight_when_opening_unknown_webengine(monkeypatch):
    started = []
    preparing = []

    manager = KLineWindowManager()
    manager._charts = []
    manager._prewarm_view = None
    manager._prewarm_started = False
    manager._webengine_available = None
    manager._webengine_failure = ""
    manager._webengine_preflight_started = False
    monkeypatch.setattr(
        manager,
        "_ensure_webengine_available",
        lambda: (_ for _ in ()).throw(AssertionError("open_chart should not block on preflight")),
    )
    monkeypatch.setattr(manager, "_start_webengine_preflight_async", lambda: started.append(True) or True)
    monkeypatch.setattr(
        manager,
        "_notify_webengine_preparing",
        lambda main_window, code, name: preparing.append((code, name)),
    )

    try:
        chart = manager.open_chart(
            main_window=None,
            code="000001",
            name="平安银行",
            data_provider=_DummyProvider(),
            vcp_data={},
            code_list=[],
            current_idx=0,
        )

        assert chart is None
        assert manager._charts == []
        assert started == [True]
        assert preparing == [("000001", "平安银行")]
    finally:
        manager._charts = []
        manager._prewarm_view = None
        manager._prewarm_started = False
        manager._prewarm_cancelled = False
        manager._prewarm_expire_timer = None
        manager._webengine_available = None
        manager._webengine_failure = ""
        manager._webengine_preflight_started = False


def test_kline_manager_webengine_preflight_caches_failure(monkeypatch):
    calls = []

    def _check(*args, **kwargs):
        calls.append((args, kwargs))
        return {"ok": False, "reason": "returncode=3221226505 0xc0000409"}

    manager = KLineWindowManager()
    manager._webengine_available = None
    manager._webengine_failure = ""
    monkeypatch.setattr(manager_module, "check_qt_webengine_available", _check)

    try:
        assert manager._ensure_webengine_available() is False
        assert manager._ensure_webengine_available() is False
        assert len(calls) == 1
        assert "0xc0000409" in manager._webengine_failure
    finally:
        manager._webengine_available = None
        manager._webengine_failure = ""
        manager._webengine_preflight_started = False


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
        _dispose_kline_window(window)


def test_kline_load_and_draw_ignores_stale_switch_result(monkeypatch):
    original_load = kline_module.KLineChartWindow._load_and_draw

    class _SwitchingProvider:
        _offline = True

        def __init__(self):
            self.window = None

        def get_data(self, _code):
            return None

        def get_data_fresh_for_chart(self, _code, force_sync=False):
            self.window.code = "000002"
            self.window._render_generation += 1
            return pd.DataFrame(
                {
                    "open": [10.0, 10.1, 10.2, 10.3, 10.4, 10.5],
                    "high": [10.2, 10.3, 10.4, 10.5, 10.6, 10.7],
                    "low": [9.9, 10.0, 10.1, 10.2, 10.3, 10.4],
                    "close": [10.1, 10.2, 10.3, 10.4, 10.5, 10.6],
                    "volume": [1000.0, 1100.0, 1200.0, 1300.0, 1400.0, 1500.0],
                },
                index=pd.date_range("2026-04-01", periods=6),
            )

    monkeypatch.setattr(kline_module, "QWebEngineView", QWidget)
    monkeypatch.setattr(kline_module.KLineChartWindow, "_load_and_draw", lambda self: None)
    monkeypatch.setattr(
        kline_module.KLineChartWindow,
        "_check_fav_status",
        lambda self: setattr(self, "is_fav", False),
    )

    provider = _SwitchingProvider()
    window = kline_module.KLineChartWindow(
        None,
        "000001",
        "骞冲畨閾惰",
        provider,
        vcp_data={},
        code_list=[{"浠ｇ爜": "000001", "鍚嶇О": "骞冲畨閾惰"}],
        current_idx=0,
    )
    provider.window = window
    rendered = []

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
        return task_id or "test-kline-stale"

    monkeypatch.setattr(window, "_render_chart", lambda df, loading=False: rendered.append((df, loading)))
    monkeypatch.setattr(window, "_set_status_message", lambda *args, **kwargs: None)
    monkeypatch.setattr(window, "_get_cn_target_trade_date", lambda: dt.date(2026, 4, 14))
    monkeypatch.setattr(task_manager, "run_in_background", _run_inline)

    try:
        original_load(window)

        assert rendered == []
    finally:
        _dispose_kline_window(window)


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
        _dispose_kline_window(window)


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
                            {
                                "date": "2026-04-16",
                                "open": 2000.0,
                                "high": 2020.0,
                                "low": 1990.0,
                                "close": 2010.0,
                                "volume": 1000,
                            },
                            {
                                "date": "2026-04-17",
                                "open": 2010.0,
                                "high": 2030.0,
                                "low": 2005.0,
                                "close": 2030.0,
                                "volume": 1100,
                            },
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
    monkeypatch.setattr(
        asian_workers_module,
        "fetch_asian_realtime_quote",
        lambda code, **kwargs: {
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
        _dispose_kline_window(window)
