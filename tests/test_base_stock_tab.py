# -*- coding: utf-8 -*-
from types import SimpleNamespace

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QStandardItemModel
from PyQt6.QtTest import QSignalSpy, QTest
from PyQt6.QtWidgets import QApplication, QHBoxLayout, QLabel, QLineEdit, QPushButton, QTableView, QToolButton, QWidget

import ui.tabs.base_stock_tab as base_stock_tab_module
from core.event_bus import event_bus
from core.quote_dispatcher import publish_rt_quotes
from ui.tabs.base_stock_tab import BaseStockTab
from ui.theme_tokens import build_ui_tokens


class DummyQuotePublisher:
    def publish_external_quotes(self, payload, *, source: str, require_valid: bool = False):
        return publish_rt_quotes(payload, source=source, require_valid=require_valid)


def test_base_stock_toolbar_applies_shell_object_names_and_toolbutton_style():
    tab = BaseStockTab()
    subtitle = QLabel("已连接")
    filter_label = QLabel("筛选")
    filter_input = QLineEdit()
    action_btn = QToolButton()

    toolbar = tab.build_tab_toolbar("示例", subtitle, [filter_label, filter_input], [action_btn])
    try:
        assert toolbar.objectName() == "tabToolbar"
        assert isinstance(toolbar.layout(), QHBoxLayout)
        assert subtitle.objectName() == "tabStatusLabel"
        assert subtitle.property("toolbarRole") == "status"
        assert filter_label.property("toolbarRole") == "meta"
        assert filter_input.property("inToolbar") is True
        assert action_btn.property("class") == "toolbarGhost"
        assert action_btn.property("inToolbar") is True
        assert action_btn.cursor().shape() == Qt.CursorShape.PointingHandCursor
        title_wrap = toolbar.findChild(QWidget, "tabToolbarTitleWrap")
        assert title_wrap is not None
        assert title_wrap.minimumHeight() == build_ui_tokens()["control"]["toolbar_button_height"] + 1
        assert toolbar.findChild(QWidget, "tabStatusChipBar") is not None
        assert toolbar.findChild(QWidget, "tabToolbarFilters") is not None
        assert toolbar.findChild(QWidget, "tabToolbarActions") is not None
    finally:
        toolbar.deleteLater()
        tab.deleteLater()


def test_base_stock_toolbar_shows_single_overflow_action_directly():
    tab = BaseStockTab()
    subtitle = QLabel("ready")
    add_input = QLineEdit()
    primary = QPushButton("Add")
    primary.setObjectName("primaryButton")
    reset = QPushButton("Reset sort")
    reset.setProperty("toolbarOverflow", True)

    toolbar = tab.build_tab_toolbar("Example", subtitle, [], [add_input, primary, reset])
    try:
        action_wrap = toolbar.findChild(QWidget, "tabToolbarActions")
        overflow_buttons = [button for button in action_wrap.findChildren(QToolButton) if button.menu() is not None]

        assert overflow_buttons == []
        assert reset.isHidden() is False
        assert reset.parent() is action_wrap
    finally:
        toolbar.deleteLater()
        tab.deleteLater()


def test_base_stock_toolbar_status_uses_semantic_chips():
    tab = BaseStockTab()
    subtitle = QLabel(
        BaseStockTab.format_workspace_status(
            "已就绪",
            result="30/30只",
            freshness="09:31",
            current_filter="全部",
            next_step="右键查看详情",
        )
    )

    toolbar = tab.build_tab_toolbar("示例", subtitle, [], [])
    try:
        chip_bar = toolbar.findChild(QWidget, "tabStatusChipBar")
        assert chip_bar is not None
        assert subtitle.isHidden()

        primary = chip_bar.findChild(QLabel, "tabStatusPrimaryChip")
        chips = [chip for chip in chip_bar.findChildren(QLabel, "tabStatusChip") if not chip.isHidden()]

        assert primary.text() == "已就绪"
        assert [chip.text() for chip in chips[:4]] == [
            "结果 30/30只",
            "时效 09:31",
            "筛选 全部",
            "下一步 右键查看详情",
        ]
        assert "右键查看详情" in chips[3].toolTip()

        subtitle.setText(BaseStockTab.format_workspace_status("等待刷新", result="0只", freshness="待加载"))

        chips = [chip for chip in chip_bar.findChildren(QLabel, "tabStatusChip") if not chip.isHidden()]
        assert primary.text() == "等待刷新"
        assert [chip.text() for chip in chips[:2]] == ["结果 0只", "时效 待加载"]
    finally:
        toolbar.deleteLater()
        tab.deleteLater()


def test_base_stock_proxy_filter_debounces_visible_tabs():
    app = QApplication.instance() or QApplication([])

    class DummyProxy:
        def __init__(self):
            self.values = []

        def setFilterText(self, text):
            self.values.append(text)

    tab = BaseStockTab()
    proxy = DummyProxy()
    try:
        tab.set_proxy_filter_text(proxy, "cold")
        assert proxy.values == ["cold"]

        tab.show()
        app.processEvents()
        tab.set_proxy_filter_text(proxy, "a", debounce_ms=40)
        tab.set_proxy_filter_text(proxy, "ab", debounce_ms=40)

        assert proxy.values == ["cold"]
        QTest.qWait(70)
        assert proxy.values == ["cold", "ab"]
    finally:
        tab.close()
        tab.deleteLater()


def test_base_stock_status_summary_skips_empty_segments():
    assert (
        BaseStockTab.format_status_summary("状态 已启动", "", None, "下一步 拉取报价")
        == "状态 已启动 | 下一步 拉取报价"
    )


def test_base_stock_external_quote_jumps_delegate_to_terminal_launcher(monkeypatch):
    tab = BaseStockTab()
    calls = []

    monkeypatch.setattr(tab._quote_terminal_launcher, "launch_tdx", lambda code: calls.append(("tdx", code)))
    monkeypatch.setattr(
        tab._quote_terminal_launcher,
        "launch_eastmoney",
        lambda code: calls.append(("eastmoney", code)),
    )

    try:
        tab.launch_tdx("600519")
        tab.launch_eastmoney("000001")
        assert calls == [("tdx", "600519"), ("eastmoney", "000001")]
    finally:
        tab.deleteLater()


def test_base_stock_header_persistence_delegates_to_view_state_binding(monkeypatch):
    tab = BaseStockTab()
    table = object()
    settings = object()
    captured = {}

    monkeypatch.setattr(tab, "_settings_section", lambda: settings)
    monkeypatch.setattr(
        base_stock_tab_module,
        "bind_table_view_state",
        lambda owner, bound_table, bound_settings, savers, settings_key="header_state": (
            captured.update(
                {
                    "owner": owner,
                    "table": bound_table,
                    "settings": bound_settings,
                    "savers": savers,
                    "settings_key": settings_key,
                }
            )
            or True
        ),
    )

    try:
        assert tab.bind_header_persistence(table, "header_state_watchlist_v8") is True
        assert captured == {
            "owner": tab,
            "table": table,
            "settings": settings,
            "savers": tab._header_state_savers,
            "settings_key": "header_state_watchlist_v8",
        }
    finally:
        tab.deleteLater()


def test_base_stock_close_stops_pending_header_save_timers(monkeypatch):
    class FakeSettings:
        def __init__(self):
            self.values = {}

        def contains(self, key):
            return key in self.values

        def value(self, key, default=None):
            return self.values.get(key, default)

        def setValue(self, key, value):
            self.values[key] = value

        def sync(self):
            return None

    tab = BaseStockTab()
    table = QTableView(tab)
    table.setModel(QStandardItemModel(1, 2, table))
    monkeypatch.setattr(tab, "_settings_section", lambda: FakeSettings())

    try:
        assert tab.bind_header_persistence(table, "header_state_test") is False
        timers = getattr(tab, "_header_save_timers", [])

        assert timers
        timers[0].start()
        assert any(timer.isActive() for timer in timers)

        tab.close()

        assert all(not timer.isActive() for timer in timers)
    finally:
        tab.deleteLater()


def test_base_stock_tab_defers_quote_refresh_until_visible(monkeypatch):
    app = QApplication.instance() or QApplication([])

    class DummyModel:
        def __init__(self):
            self.calls = []

        def update_quotes(self, quotes):
            self.calls.append(dict(quotes))

    class DummyTab(BaseStockTab):
        def __init__(self):
            super().__init__()
            self.model = DummyModel()

    from core.global_store import global_store

    snapshot = {"000001": {"close": 10.5}}
    monkeypatch.setattr(global_store, "get_latest_quotes", lambda: snapshot)

    tab = DummyTab()
    try:
        assert not tab.isVisible()

        tab.subscribe_global_quotes()

        assert tab.model.calls == []
        assert tab._deferred_quote_refresh is True

        tab.show()
        app.processEvents()

        assert tab.model.calls
        assert tab.model.calls[0] == snapshot
        assert tab._deferred_quote_refresh is False
    finally:
        tab.close()
        tab.deleteLater()


def test_base_stock_quote_snapshot_skips_irrelevant_payload():
    from ui.tabs.tab_quote_bridge import apply_quote_snapshot

    code_key = "\u4ee3\u7801"

    class DummyModel:
        def __init__(self):
            self.row_data = [{code_key: "000001"}, {code_key: "AAPL"}]
            self.calls = []

        def update_quotes(self, quotes):
            self.calls.append(dict(quotes))

    class DummyTab(BaseStockTab):
        def __init__(self):
            super().__init__()
            self.model = DummyModel()

    tab = DummyTab()
    try:
        apply_quote_snapshot(tab, {"600000": {"close": 12.3}})
        assert tab.model.calls == []

        apply_quote_snapshot(
            tab,
            {
                "000001": {"close": 10.5},
                "AAPL": {"close": 188.0},
                "600000": {"close": 12.3},
            },
        )
        assert tab.model.calls == [{"000001": {"close": 10.5}, "AAPL": {"close": 188.0}}]
    finally:
        tab.deleteLater()


def test_base_stock_refresh_table_market_data_only_fetches_blank_quotes(monkeypatch):
    app = QApplication.instance() or QApplication([])

    class DummyModel:
        def __init__(self):
            self.headers = ["代码", "现价", "涨幅%", "市值"]
            self.row_data = [
                {"代码": "000001", "现价": "10.00", "涨幅%": "+1.00%", "市值": "100亿"},
                {"代码": "000002", "现价": "--", "涨幅%": "--", "市值": "--"},
            ]
            self.quote_calls = []

        def update_quotes(self, quotes):
            self.quote_calls.append(dict(quotes))

    class DummyProvider:
        def __init__(self):
            self.requested_codes = []

        def fetch_realtime_quotes_batch(self, codes):
            self.requested_codes.append(list(codes))
            return {"000002": {"close": 11.2, "last_close": 11.0}}

    class DummyTaskManager:
        def __init__(self):
            self.task_ids = []

        def is_active_task(self, task_id):
            self.task_ids.append(("active", task_id))
            return False

        def run_in_background(self, fn, on_success=None, on_error=None, task_id=None, **_kwargs):
            self.task_ids.append(("run", task_id))
            result = fn()
            if on_success:
                on_success(result)

    class DummyTab(BaseStockTab):
        def __init__(self, provider):
            super().__init__(data_provider=provider)
            self.model = DummyModel()

    import core.task_manager as task_manager_module
    from core.global_store import global_store

    snapshot = {
        "000001": {"close": 10.8, "last_close": 10.0},
        "000002": {"close": 11.0, "last_close": 10.5},
    }
    provider = DummyProvider()
    task_manager = DummyTaskManager()
    cap_calls = []

    monkeypatch.setattr(global_store, "get_latest_quotes", lambda: snapshot)
    monkeypatch.setattr(task_manager_module, "task_manager", task_manager)

    tab = DummyTab(provider)
    tab._quote_publisher = DummyQuotePublisher()
    spy = QSignalSpy(event_bus.sig_rt_quotes)
    monkeypatch.setattr(tab, "async_update_market_caps", lambda: cap_calls.append("caps"))
    monkeypatch.setattr(
        tab,
        "_load_cached_finance_snapshot",
        staticmethod(lambda codes: {}),
        raising=False,
    )

    try:
        tab.refresh_table_quotes_and_market_caps(quote_task_id="dummy_quotes")
        app.processEvents()

        assert cap_calls == ["caps"]
        assert tab.model.quote_calls == [
            snapshot,
            {"000002": {"close": 11.2, "last_close": 11.0}},
        ]
        assert provider.requested_codes == [["000002"]]
        assert len(spy) == 1
        assert task_manager.task_ids == [("active", "dummy_quotes"), ("run", "dummy_quotes")]
    finally:
        tab.deleteLater()


def test_base_stock_refresh_table_market_data_can_force_full_quote_refresh(monkeypatch):
    class DummyModel:
        def __init__(self):
            self.headers = ["代码", "现价", "涨幅%", "市值"]
            self.row_data = [
                {"代码": "000001", "现价": "10.00", "涨幅%": "+1.00%", "市值": "100亿"},
                {"代码": "000002", "现价": "11.00", "涨幅%": "+2.00%", "市值": "120亿"},
            ]

        def update_quotes(self, quotes):
            pass

    class DummyProvider:
        def __init__(self):
            self.requested_codes = []

        def fetch_realtime_quotes_batch(self, codes):
            self.requested_codes.append(list(codes))
            return {}

    class DummyTaskManager:
        def is_active_task(self, task_id):
            return False

        def run_in_background(self, fn, on_success=None, on_error=None, task_id=None, **_kwargs):
            fn()
            if on_success:
                on_success({})

    class DummyTab(BaseStockTab):
        def __init__(self, provider):
            super().__init__(data_provider=provider)
            self.model = DummyModel()

    import core.task_manager as task_manager_module
    from core.global_store import global_store

    provider = DummyProvider()
    tab = DummyTab(provider)

    monkeypatch.setattr(global_store, "get_latest_quotes", lambda: {})
    monkeypatch.setattr(task_manager_module, "task_manager", DummyTaskManager())
    monkeypatch.setattr(tab, "async_update_market_caps", lambda: None)
    monkeypatch.setattr(
        tab,
        "_load_cached_finance_snapshot",
        staticmethod(lambda codes: {}),
        raising=False,
    )

    try:
        tab.refresh_table_quotes_and_market_caps(
            force_quotes=True,
            quote_task_id="force_quotes",
        )
        assert provider.requested_codes == [["000001", "000002"]]
    finally:
        tab.deleteLater()


def test_base_stock_refresh_table_market_data_fetches_newly_added_blank_rows(monkeypatch):
    class DummyModel:
        def __init__(self):
            self.headers = ["代码", "现价", "涨幅%", "市值"]
            self.row_data = [
                {"代码": "000001", "现价": "--", "涨幅%": "--", "市值": "--"},
                {"代码": "000002", "现价": "--", "涨幅%": "--", "市值": "--"},
            ]

        def update_quotes(self, quotes):
            for row in self.row_data:
                code = row.get("代码")
                if code not in quotes:
                    continue
                close = float(quotes[code].get("close", 0) or 0)
                last_close = float(quotes[code].get("last_close", 0) or 0)
                row["现价"] = f"{close:.2f}" if close > 0 else "--"
                if close > 0 and last_close > 0:
                    row["涨幅%"] = ((close / last_close) - 1) * 100

    class DummyProvider:
        def __init__(self):
            self.requested_codes = []

        def fetch_realtime_quotes_batch(self, codes):
            self.requested_codes.append(list(codes))
            return {"000002": {"close": 11.2, "last_close": 11.0}}

    class DummyTaskManager:
        def is_active_task(self, task_id):
            return False

        def run_in_background(self, fn, on_success=None, on_error=None, task_id=None, **_kwargs):
            result = fn()
            if on_success:
                on_success(result)

    class DummyTab(BaseStockTab):
        def __init__(self, provider):
            super().__init__(data_provider=provider)
            self.model = DummyModel()

    import core.task_manager as task_manager_module
    from core.global_store import global_store

    provider = DummyProvider()
    tab = DummyTab(provider)

    monkeypatch.setattr(
        global_store,
        "get_latest_quotes",
        lambda: {"000001": {"close": 10.8, "last_close": 10.0}},
    )
    monkeypatch.setattr(task_manager_module, "task_manager", DummyTaskManager())
    monkeypatch.setattr(tab, "async_update_market_caps", lambda: None)
    tab._quote_publisher = DummyQuotePublisher()
    monkeypatch.setattr(
        tab,
        "_load_cached_finance_snapshot",
        staticmethod(lambda codes: {}),
        raising=False,
    )

    try:
        tab.refresh_table_quotes_and_market_caps(quote_task_id="new_codes_quotes")
        assert provider.requested_codes == [["000002"]]
        assert tab.model.row_data[0]["现价"] == "10.80"
        assert tab.model.row_data[1]["现价"] == "11.20"
    finally:
        tab.deleteLater()


def test_base_stock_refresh_from_latest_snapshot_primes_local_cache_for_new_codes(monkeypatch):
    class DummyModel:
        def __init__(self):
            self.headers = ["代码", "现价", "涨幅%", "市值"]
            self.row_data = [
                {"代码": "000001", "现价": "--", "涨幅%": "--", "市值": "--"},
            ]

        def update_quotes(self, quotes):
            for row in self.row_data:
                code = row.get("代码")
                payload = dict(quotes.get(code) or {})
                if not payload:
                    continue
                close = float(payload.get("close", 0) or 0)
                last_close = float(payload.get("last_close", 0) or 0)
                zongguben = float(payload.get("_zongguben") or payload.get("zongguben") or 0)
                if close > 0:
                    row["现价"] = f"{close:.2f}"
                if close > 0 and last_close > 0:
                    row["涨幅%"] = ((close / last_close) - 1) * 100
                if close > 0 and zongguben > 0:
                    row["市值"] = f"{(close * zongguben) / 1e8:.0f}亿"

    class DummyProvider:
        def __init__(self):
            self.offline_calls = []

        def build_offline_quotes(self, codes):
            self.offline_calls.append(list(codes))
            return {"000001": {"close": 10.5, "last_close": 10.0}}

    class DummyTab(BaseStockTab):
        def __init__(self, provider):
            super().__init__(data_provider=provider)
            self.model = DummyModel()

    from core.global_store import global_store

    provider = DummyProvider()
    tab = DummyTab(provider)
    monkeypatch.setattr(tab, "isVisible", lambda: True)

    global_store.reset_quotes()
    monkeypatch.setattr(
        tab,
        "_load_cached_finance_snapshot",
        staticmethod(lambda codes: {"000001": {"zongguben": 1_000_000_000}} if codes == ["000001"] else {}),
        raising=False,
    )

    try:
        tab.refresh_table_from_latest_snapshot(async_local=False)

        assert provider.offline_calls == [["000001"]]
        assert tab.model.row_data[0]["现价"] == "10.50"
        assert round(float(tab.model.row_data[0]["涨幅%"]), 2) == 5.0
        assert tab.model.row_data[0]["市值"] == "105亿"
    finally:
        global_store.reset_quotes()
        tab.deleteLater()


def test_prime_visible_local_quote_snapshot_skips_noninteractive_probe(monkeypatch):
    class DummyTab(BaseStockTab):
        def __init__(self):
            super().__init__()
            self.calls = []

        def refresh_table_from_latest_snapshot(self, current_model=None, *, async_local=True):
            self.calls.append((current_model, async_local))

    tab = DummyTab()
    monkeypatch.setattr(tab, "isVisible", lambda: True)

    try:
        tab._workspace_load_reason = "screenshot"
        tab._workspace_noninteractive_loaded = True

        assert tab._prime_visible_local_quote_snapshot(object()) is False
        assert tab.calls == []

        tab._workspace_load_reason = "tab_switch"
        tab._workspace_noninteractive_loaded = False
        assert tab._prime_visible_local_quote_snapshot("model") is True
        assert tab.calls == [("model", True)]
    finally:
        tab.deleteLater()


def test_current_workspace_tab_uses_window_workspace_tabs(qt_application):
    host = QWidget()
    current_widget = QWidget(host)
    tab = BaseStockTab(parent=host)
    host._workspace = SimpleNamespace(tabs=SimpleNamespace(currentWidget=lambda: current_widget))

    try:
        assert tab._is_current_workspace_tab() is False

        host._workspace = SimpleNamespace(tabs=SimpleNamespace(currentWidget=lambda: tab))
        assert tab._is_current_workspace_tab() is True
    finally:
        tab.deleteLater()
        current_widget.deleteLater()
        host.deleteLater()


def test_show_event_reenables_snapshot_prime_after_background_prewarm(monkeypatch, qt_application):
    from PyQt6.QtGui import QShowEvent

    class DummyTab(BaseStockTab):
        def __init__(self):
            super().__init__()
            self.calls = []

        def refresh_table_from_latest_snapshot(self, current_model=None, *, async_local=True):
            self.calls.append((current_model, async_local))

    tab = DummyTab()
    monkeypatch.setattr(tab, "isVisible", lambda: True)
    tab._workspace_load_reason = ""
    tab._workspace_noninteractive_loaded = True

    try:
        tab.showEvent(QShowEvent())

        assert tab._workspace_noninteractive_loaded is False
        assert tab.calls == [(None, True)]
    finally:
        tab.deleteLater()


def test_show_event_keeps_noninteractive_probe_snapshot_gate(monkeypatch, qt_application):
    from PyQt6.QtGui import QShowEvent

    class DummyTab(BaseStockTab):
        def __init__(self):
            super().__init__()
            self.calls = []

        def refresh_table_from_latest_snapshot(self, current_model=None, *, async_local=True):
            self.calls.append((current_model, async_local))

    tab = DummyTab()
    monkeypatch.setattr(tab, "isVisible", lambda: True)
    tab._workspace_load_reason = "screenshot"
    tab._workspace_noninteractive_loaded = True

    try:
        tab.showEvent(QShowEvent())

        assert tab._workspace_noninteractive_loaded is True
        assert tab.calls == []
    finally:
        tab.deleteLater()


def test_base_stock_refresh_table_market_data_primes_local_f5_snapshot_for_new_rows(monkeypatch):
    class DummyModel:
        def __init__(self):
            self.headers = ["代码", "现价", "涨幅%", "市值"]
            self.row_data = [
                {"代码": "000001", "现价": "--", "涨幅%": "--", "市值": "--"},
            ]

        def update_quotes(self, quotes):
            for row in self.row_data:
                code = row.get("代码")
                payload = dict(quotes.get(code) or {})
                if not payload:
                    continue
                close = float(payload.get("close", 0) or 0)
                last_close = float(payload.get("last_close", 0) or 0)
                zongguben = float(payload.get("_zongguben") or payload.get("zongguben") or 0)
                if close > 0:
                    row["现价"] = f"{close:.2f}"
                if close > 0 and last_close > 0:
                    row["涨幅%"] = ((close / last_close) - 1) * 100
                if close > 0 and zongguben > 0:
                    row["市值"] = f"{(close * zongguben) / 1e8:.0f}亿"

    class DummyProvider:
        def __init__(self):
            self.offline_calls = []

        def build_offline_quotes(self, codes):
            self.offline_calls.append(list(codes))
            return {"000001": {"close": 10.5, "last_close": 10.0}}

    class DummyTab(BaseStockTab):
        def __init__(self, provider):
            super().__init__(data_provider=provider)
            self.model = DummyModel()

    from core.global_store import global_store

    provider = DummyProvider()
    tab = DummyTab(provider)
    monkeypatch.setattr(tab, "isVisible", lambda: True)

    global_store.reset_quotes()
    monkeypatch.setattr(tab, "async_update_market_caps", lambda: None)
    monkeypatch.setattr(
        tab,
        "_load_cached_finance_snapshot",
        staticmethod(lambda codes: {"000001": {"zongguben": 1_000_000_000}} if codes == ["000001"] else {}),
        raising=False,
    )

    try:
        tab.refresh_table_quotes_and_market_caps(quote_task_id="local_f5_quotes")

        assert provider.offline_calls == [["000001"]]
        assert tab.model.row_data[0]["现价"] == "10.50"
        assert round(float(tab.model.row_data[0]["涨幅%"]), 2) == 5.0
        assert tab.model.row_data[0]["市值"] == "105亿"
    finally:
        global_store.reset_quotes()
        tab.deleteLater()
