# -*- coding: utf-8 -*-
from PyQt6.QtTest import QSignalSpy
from PyQt6.QtWidgets import QApplication, QLabel, QLineEdit, QToolButton, QWidget

from core.event_bus import event_bus
from core.quote_dispatcher import publish_rt_quotes
from ui.tabs.base_stock_tab import BaseStockTab


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
        assert subtitle.objectName() == "tabStatusLabel"
        assert subtitle.property("toolbarRole") == "status"
        assert filter_label.property("toolbarRole") == "meta"
        assert filter_input.property("inToolbar") is True
        assert action_btn.property("class") == "toolbarGhost"
        assert action_btn.property("inToolbar") is True
        assert toolbar.findChild(QWidget, "tabToolbarFilters") is not None
        assert toolbar.findChild(QWidget, "tabToolbarActions") is not None
    finally:
        toolbar.deleteLater()
        tab.deleteLater()


def test_base_stock_status_summary_skips_empty_segments():
    assert (
        BaseStockTab.format_status_summary("状态 已启动", "", None, "下一步 拉取报价")
        == "状态 已启动 | 下一步 拉取报价"
    )


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

        assert tab.model.calls == [snapshot]
        assert tab._deferred_quote_refresh is False
    finally:
        tab.close()
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

        def run_in_background(self, fn, on_success=None, on_error=None, task_id=None):
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

        def run_in_background(self, fn, on_success=None, on_error=None, task_id=None):
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

        def run_in_background(self, fn, on_success=None, on_error=None, task_id=None):
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

    try:
        tab.refresh_table_quotes_and_market_caps(quote_task_id="new_codes_quotes")
        assert provider.requested_codes == [["000002"]]
        assert tab.model.row_data[0]["现价"] == "10.80"
        assert tab.model.row_data[1]["现价"] == "11.20"
    finally:
        tab.deleteLater()
