# -*- coding: utf-8 -*-
from copy import deepcopy

from PyQt6.QtTest import QSignalSpy
from PyQt6.QtWidgets import QPushButton

from core.event_bus import event_bus
from ui.tabs import watchlist_tab as watchlist_module
from ui.viewmodels.watchlist_vm import watchlist_vm


class _DummyProvider:
    def __init__(self):
        self.code2name = {"600519": "贵州茅台"}


def test_watchlist_vm_add_stock_emits_add_signal(monkeypatch):
    original_cache = deepcopy(watchlist_vm._cache)
    spy = QSignalSpy(event_bus.sig_watchlist_changed)

    monkeypatch.setattr(watchlist_vm, "_save_data", lambda: None)
    watchlist_vm._cache = {}

    try:
        added = watchlist_vm.add_stock("600519", "贵州茅台", {"现价": 123.45, "细分板块": "白酒"})

        assert added is True
        assert watchlist_vm._cache["600519"]["名称"] == "贵州茅台"
        assert watchlist_vm._cache["600519"]["现价"] == "--"
        assert watchlist_vm._cache["600519"]["细分板块"] == "白酒"
        assert len(spy) == 1
        assert spy[0][0] == "add"
        assert spy[0][1] == "600519"
    finally:
        watchlist_vm._cache = original_cache


def test_watchlist_vm_get_watchlist_data_returns_deep_copy(monkeypatch):
    original_cache = deepcopy(watchlist_vm._cache)
    watchlist_vm._cache = {
        "600519": {"名称": "贵州茅台", "标签": {"来源": "自选"}},
    }

    try:
        snapshot = watchlist_vm.get_watchlist_data()
        snapshot["600519"]["标签"]["来源"] = "外部修改"

        assert watchlist_vm._cache["600519"]["标签"]["来源"] == "自选"
    finally:
        watchlist_vm._cache = original_cache


def test_watchlist_vm_public_patch_interfaces(monkeypatch):
    original_cache = deepcopy(watchlist_vm._cache)
    monkeypatch.setattr(watchlist_vm, "_save_data", lambda: None)
    watchlist_vm._cache = {
        "600519": {"名称": "贵州茅台", "旧字段": "待清理"},
        "000001": {"名称": "平安银行"},
    }

    try:
        assert watchlist_vm.patch_entry("600519", {"备注": "核心观察"}) is True
        assert watchlist_vm._cache["600519"]["备注"] == "核心观察"

        changed = watchlist_vm.bulk_patch_entries(
            {
                "600519": {"RPS强度": "95/93"},
                "000001": {"RPS强度": "88/80"},
            },
            remove_keys=["旧字段"],
        )
        assert changed is True
        assert "旧字段" not in watchlist_vm._cache["600519"]
        assert watchlist_vm._cache["000001"]["RPS强度"] == "88/80"

        replaced = watchlist_vm.replace_watchlist_data(
            {
                "000001": {"名称": "平安银行"},
                "600519": {"名称": "贵州茅台", "备注": "核心观察"},
            }
        )
        assert replaced is True
        assert list(watchlist_vm._cache.keys()) == ["000001", "600519"]
    finally:
        watchlist_vm._cache = original_cache


def test_watchlist_toolbar_uses_add_stock_button_and_accepts_a_share_code(monkeypatch):
    monkeypatch.setattr(watchlist_module.WatchlistTab, "subscribe_global_quotes", lambda self: None)
    monkeypatch.setattr(watchlist_module.WatchlistTab, "_load_special_data", lambda self: None)
    monkeypatch.setattr(
        watchlist_module.WatchlistTab,
        "bind_header_persistence",
        lambda self, table, settings_key="header_state": None,
        raising=False,
    )
    monkeypatch.setattr(watchlist_module, "show_toast", lambda *args, **kwargs: None)
    monkeypatch.setattr(watchlist_module.QTimer, "singleShot", staticmethod(lambda *_args, **_kwargs: None))
    monkeypatch.setattr(watchlist_vm, "is_in_watchlist", lambda code: False)

    added_calls = []

    def _fake_add_stock(code, name, vcp_data=None):
        added_calls.append((code, name, dict(vcp_data or {})))
        return True

    monkeypatch.setattr(watchlist_vm, "add_stock", _fake_add_stock)

    tab = watchlist_module.WatchlistTab(_DummyProvider())
    try:
        button_texts = [btn.text() for btn in tab.findChildren(QPushButton)]
        assert tab.sp_search.accessibleName() == "关注池筛选"
        assert tab.add_stock_input.accessibleName() == "添加自选股输入框"
        assert "📄 导出" not in button_texts
        assert "添加自选股" in button_texts

        tab.add_stock_input.setText("sh600519")
        tab._add_custom_stock()

        assert added_calls == [
            (
                "600519",
                "贵州茅台",
                {"代码": "600519", "名称": "贵州茅台", "code": "600519", "name": "贵州茅台"},
            )
        ]
        assert tab.add_stock_input.text() == ""
    finally:
        tab.deleteLater()


def test_watchlist_status_summary_includes_recent_refresh(monkeypatch):
    monkeypatch.setattr(watchlist_module.WatchlistTab, "subscribe_global_quotes", lambda self: None)
    monkeypatch.setattr(
        watchlist_module.WatchlistTab,
        "bind_header_persistence",
        lambda self, table, settings_key="header_state": None,
        raising=False,
    )
    monkeypatch.setattr(watchlist_module.QTimer, "singleShot", staticmethod(lambda *_args, **_kwargs: None))
    monkeypatch.setattr(
        watchlist_module.WatchlistTab,
        "_request_vcp_calc",
        lambda self, delay_ms=500: None,
        raising=False,
    )
    monkeypatch.setattr(
        watchlist_module.WatchlistTab,
        "refresh_table_quotes_and_market_caps",
        lambda self, current_model=None, force_quotes=False, quote_task_id=None: None,
        raising=False,
    )
    monkeypatch.setattr(
        watchlist_vm,
        "get_watchlist_data",
        lambda: {
            "600519": {
                "名称": "贵州茅台",
                "RPS强度": "95/93",
                "美股日报": "行业提价",
                "业绩异动": "预增",
                "大宗交易": "机构买入",
                "龙虎榜": "净买入",
            }
        },
    )

    tab = watchlist_module.WatchlistTab(_DummyProvider())
    try:
        tab._touch_watchlist_update("10:21")
        tab._update_status_summary()
        summary = tab.lbl_sp_status.text()

        assert "池内1只" in summary
        assert "匹配 1/1" in summary
        assert "最近 10:21" in summary
    finally:
        tab.deleteLater()
