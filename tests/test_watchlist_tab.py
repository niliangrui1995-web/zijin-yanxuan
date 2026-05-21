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

    def is_online(self):
        return False


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

    def _fake_add_stock(code, name, vcp_data=None, source_tags=None):
        added_calls.append((code, name, dict(vcp_data or {}), list(source_tags or [])))
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
                ["手动"],
            )
        ]
        assert tab.add_stock_input.text() == ""
    finally:
        tab.deleteLater()


def test_watchlist_can_disable_startup_tasks_for_controlled_window_smoke(monkeypatch):
    load_calls = []
    monkeypatch.setattr(watchlist_module.WatchlistTab, "subscribe_global_quotes", lambda self: None)
    monkeypatch.setattr(watchlist_module.WatchlistTab, "_load_special_data", lambda self: load_calls.append("load"))
    monkeypatch.setattr(
        watchlist_module.WatchlistTab,
        "bind_header_persistence",
        lambda self, table, settings_key="header_state": None,
        raising=False,
    )

    tab = watchlist_module.WatchlistTab(_DummyProvider(), startup_tasks_enabled=False)
    try:
        assert load_calls == []
        assert tab._delayed_special_timer is None
    finally:
        tab.shutdown()
        tab.deleteLater()


def test_watchlist_render_uses_store_only_when_live_quotes_unavailable(monkeypatch):
    monkeypatch.setattr(watchlist_module.WatchlistTab, "subscribe_global_quotes", lambda self: None)
    monkeypatch.setattr(
        watchlist_module.WatchlistTab,
        "bind_header_persistence",
        lambda self, table, settings_key="header_state": None,
        raising=False,
    )

    tab = watchlist_module.WatchlistTab(_DummyProvider(), startup_tasks_enabled=False)
    refresh_calls = []
    snapshot_calls = []
    monkeypatch.setattr(tab, "_refresh_quotes_async_local", lambda **kwargs: refresh_calls.append(kwargs))
    monkeypatch.setattr(
        tab, "_apply_quote_store_snapshot", lambda *args, **kwargs: snapshot_calls.append((args, kwargs))
    )
    monkeypatch.setattr(tab, "_request_vcp_calc", lambda *args, **kwargs: None)

    try:
        tab._render_table(["600519"], {"600519": {"名称": "贵州茅台"}}, {})

        assert len(tab.model.row_data) == 1
        assert refresh_calls == []
        assert len(snapshot_calls) == 1
    finally:
        tab.shutdown()
        tab.deleteLater()


def test_watchlist_render_keeps_live_refresh_during_quote_window(monkeypatch):
    provider = _DummyProvider()
    provider.is_online = lambda: True
    monkeypatch.setattr(watchlist_module.MarketCalendar, "is_quote_refresh_time", lambda: True)
    monkeypatch.setattr(watchlist_module.WatchlistTab, "subscribe_global_quotes", lambda self: None)
    monkeypatch.setattr(
        watchlist_module.WatchlistTab,
        "bind_header_persistence",
        lambda self, table, settings_key="header_state": None,
        raising=False,
    )

    tab = watchlist_module.WatchlistTab(provider, startup_tasks_enabled=False)
    refresh_calls = []
    snapshot_calls = []
    monkeypatch.setattr(tab, "_refresh_quotes_async_local", lambda **kwargs: refresh_calls.append(kwargs))
    monkeypatch.setattr(
        tab, "_apply_quote_store_snapshot", lambda *args, **kwargs: snapshot_calls.append((args, kwargs))
    )
    monkeypatch.setattr(tab, "_request_vcp_calc", lambda *args, **kwargs: None)

    try:
        tab._render_table(["600519"], {"600519": {"名称": "贵州茅台"}}, {})

        assert len(snapshot_calls) == 1
        assert len(refresh_calls) == 1
        assert refresh_calls[0]["quote_task_id"]
    finally:
        tab.shutdown()
        tab.deleteLater()


def test_watchlist_lineage_and_signature_skip_unchanged_render(monkeypatch):
    monkeypatch.setattr(watchlist_module.WatchlistTab, "subscribe_global_quotes", lambda self: None)
    monkeypatch.setattr(
        watchlist_module.WatchlistTab,
        "bind_header_persistence",
        lambda self, table, settings_key="header_state": None,
        raising=False,
    )
    monkeypatch.setattr(watchlist_module.MarketCalendar, "is_quote_refresh_time", lambda: False)

    tab = watchlist_module.WatchlistTab(_DummyProvider(), startup_tasks_enabled=False)
    update_calls = []
    original_update_data = tab.model.update_data
    monkeypatch.setattr(tab, "_refresh_quotes_from_store_or_live", lambda **_kwargs: None)
    monkeypatch.setattr(tab, "_request_vcp_calc", lambda *args, **kwargs: None)

    def _spy_update_data(rows):
        update_calls.append(len(rows))
        original_update_data(rows)

    monkeypatch.setattr(tab.model, "update_data", _spy_update_data)

    try:
        data = {"600519": {"名称": "贵州茅台", "现价": "1500.00"}}
        tab._render_table(["600519"], data, {})
        tab._render_table(["600519"], data, {})
        lineage = tab.get_data_lineage()

        assert update_calls == [1]
        assert lineage["key"] == "watchlist"
        assert lineage["provider"] == "watchlist_vm/global_store"
        assert lineage["triggered_network"] is False
        assert lineage["row_count"] == 1
        assert lineage["last_table_update"]
        assert "provider_fault_tolerance" in lineage
    finally:
        tab.shutdown()
        tab.deleteLater()


def test_watchlist_shutdown_stops_timers_and_disconnects_runtime_signals(monkeypatch):
    monkeypatch.setattr(watchlist_module.WatchlistTab, "subscribe_global_quotes", lambda self: None)
    monkeypatch.setattr(watchlist_module.WatchlistTab, "_load_special_data", lambda self: None)
    monkeypatch.setattr(
        watchlist_module.WatchlistTab,
        "bind_header_persistence",
        lambda self, table, settings_key="header_state": None,
        raising=False,
    )

    tab = watchlist_module.WatchlistTab(_DummyProvider())
    try:
        tab._request_vcp_calc(delay_ms=1000)
        assert tab._delayed_special_timer.isActive() is True
        assert tab._vcp_calc_timer.isActive() is True

        calc_calls = []
        tab._request_vcp_calc = lambda *args, **kwargs: calc_calls.append("calc")
        tab.shutdown()
        event_bus.sig_cache_bootstrap_ready.emit()

        assert tab._closing is True
        assert tab._delayed_special_timer.isActive() is False
        assert tab._vcp_calc_timer.isActive() is False
        assert calc_calls == []
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

        assert "结果 1/1只" in summary
        assert "时效 10:21" in summary
        assert "来源 战报｜龙虎｜业绩｜大宗" in summary
    finally:
        tab.deleteLater()


def test_watchlist_requests_recalc_after_ai_chain_update(monkeypatch):
    monkeypatch.setattr(watchlist_module.WatchlistTab, "subscribe_global_quotes", lambda self: None)
    monkeypatch.setattr(watchlist_module.WatchlistTab, "_load_special_data", lambda self: None)
    monkeypatch.setattr(watchlist_module.QTimer, "singleShot", staticmethod(lambda *_args, **_kwargs: None))

    calls = []
    current = {}
    monkeypatch.setattr(
        watchlist_module.WatchlistTab,
        "_request_vcp_calc",
        lambda self, delay_ms=500: calls.append(delay_ms) if current.get("tab") is self else None,
    )

    tab = watchlist_module.WatchlistTab(_DummyProvider())
    current["tab"] = tab
    try:
        watchlist_module.event_bus.sig_ai_industry_chain_updated.emit()

        assert calls == [500]
    finally:
        tab.deleteLater()


def test_watchlist_clears_stale_special_columns_when_current_round_has_no_signal(monkeypatch):
    monkeypatch.setattr(watchlist_module.WatchlistTab, "subscribe_global_quotes", lambda self: None)
    monkeypatch.setattr(watchlist_module.WatchlistTab, "_load_special_data", lambda self: None)
    monkeypatch.setattr(
        watchlist_module.WatchlistTab,
        "bind_header_persistence",
        lambda self, table, settings_key="header_state": None,
        raising=False,
    )
    monkeypatch.setattr(watchlist_module.QTimer, "singleShot", staticmethod(lambda *_args, **_kwargs: None))

    captured = {}

    def _fake_bulk_patch_entries(payload, remove_keys=None):
        captured["payload"] = payload
        captured["remove_keys"] = list(remove_keys or [])
        return True

    monkeypatch.setattr(watchlist_vm, "bulk_patch_entries", _fake_bulk_patch_entries)

    tab = watchlist_module.WatchlistTab(_DummyProvider())
    try:
        tab.model.update_data(
            [
                {
                    "代码": "600519",
                    "名称": "贵州茅台",
                    "来源": "手动",
                    "现价": "--",
                    "涨幅%": "--",
                    "市值": "--",
                    "RPS强度": "95/93",
                    "细分板块": "",
                    "催化剂": "",
                    "业绩异动": "预增25%",
                    "业绩环比%": 25.0,
                    "大宗交易": "机构专用买入2709万",
                    "大宗交易金额(万)": 2709,
                    "龙虎榜": "04-20 | 净买1200万",
                    "龙虎榜日期": "20260420",
                    "龙虎榜净额(万)": 1200,
                    "来源标签": ["手动"],
                }
            ]
        )

        tab._apply_vcp_indicators_ui(
            {
                "600519": {
                    "rps": "95/93",
                    "subsector": "",
                    "na_catalyst": "",
                    "block_trade": "",
                    "block_trade_amount_wan": "",
                    "earnings": "",
                    "earnings_qoq_pct": "",
                    "lhb": "",
                }
            }
        )

        row = tab.model.row_data[0]

        assert row["大宗交易"] == ""
        assert row["大宗交易金额(万)"] == ""
        assert row["业绩异动"] == ""
        assert row["业绩环比%"] == ""
        assert row["龙虎榜"] == ""
        assert row["龙虎榜日期"] == ""
        assert row["龙虎榜净额(万)"] == ""
        assert captured["payload"]["600519"]["大宗交易"] == ""
        assert captured["payload"]["600519"]["大宗交易金额(万)"] == ""
        assert captured["payload"]["600519"]["业绩异动"] == ""
        assert captured["payload"]["600519"]["业绩环比%"] == ""
        assert captured["payload"]["600519"]["龙虎榜"] == ""
        assert captured["payload"]["600519"]["龙虎榜日期"] == ""
        assert captured["payload"]["600519"]["龙虎榜净额(万)"] == ""
    finally:
        tab.deleteLater()


def test_watchlist_indicator_apply_batches_model_update(monkeypatch):
    monkeypatch.setattr(watchlist_module.WatchlistTab, "subscribe_global_quotes", lambda self: None)
    monkeypatch.setattr(watchlist_module.WatchlistTab, "_load_special_data", lambda self: None)
    monkeypatch.setattr(
        watchlist_module.WatchlistTab,
        "bind_header_persistence",
        lambda self, table, settings_key="header_state": None,
        raising=False,
    )
    monkeypatch.setattr(watchlist_module.QTimer, "singleShot", staticmethod(lambda *_args, **_kwargs: None))
    monkeypatch.setattr(watchlist_vm, "bulk_patch_entries", lambda *_args, **_kwargs: True)

    tab = watchlist_module.WatchlistTab(_DummyProvider())
    try:
        tab.model.update_data(
            [
                {
                    "代码": "600519",
                    "名称": "贵州茅台",
                    "来源": "手动",
                    "现价": "--",
                    "涨幅%": "--",
                    "市值": "--",
                    "RPS强度": "",
                    "细分板块": "",
                    "催化剂": "",
                    "业绩异动": "",
                    "大宗交易": "",
                    "龙虎榜": "",
                },
                {
                    "代码": "000001",
                    "名称": "平安银行",
                    "来源": "手动",
                    "现价": "--",
                    "涨幅%": "--",
                    "市值": "--",
                    "RPS强度": "",
                    "细分板块": "",
                    "催化剂": "",
                    "业绩异动": "",
                    "大宗交易": "",
                    "龙虎榜": "",
                },
            ]
        )
        spy = QSignalSpy(tab.model.dataChanged)

        tab._apply_vcp_indicators_ui(
            {
                "600519": {"rps": "95", "subsector": "白酒"},
                "000001": {"rps": "80", "subsector": "银行"},
            }
        )

        assert len(spy) == 1
        assert tab.model.row_data[0]["RPS强度"] == "95"
        assert tab.model.row_data[1]["细分板块"] == "银行"
    finally:
        tab.deleteLater()


def test_watchlist_writes_earnings_report_label_to_column(monkeypatch):
    monkeypatch.setattr(watchlist_module.WatchlistTab, "subscribe_global_quotes", lambda self: None)
    monkeypatch.setattr(watchlist_module.WatchlistTab, "_load_special_data", lambda self: None)
    monkeypatch.setattr(
        watchlist_module.WatchlistTab,
        "bind_header_persistence",
        lambda self, table, settings_key="header_state": None,
        raising=False,
    )
    monkeypatch.setattr(watchlist_module.QTimer, "singleShot", staticmethod(lambda *_args, **_kwargs: None))

    captured = {}

    def _fake_bulk_patch_entries(payload, remove_keys=None):
        captured["payload"] = payload
        return True

    monkeypatch.setattr(watchlist_vm, "bulk_patch_entries", _fake_bulk_patch_entries)

    tab = watchlist_module.WatchlistTab(_DummyProvider())
    try:
        tab.model.update_data(
            [
                {
                    "代码": "600519",
                    "名称": "贵州茅台",
                    "来源": "手动",
                    "现价": "--",
                    "涨幅%": "--",
                    "市值": "--",
                    "RPS强度": "",
                    "细分板块": "",
                    "催化剂": "",
                    "业绩异动": "",
                    "业绩环比%": "",
                    "大宗交易": "",
                    "大宗交易金额(万)": "",
                    "龙虎榜": "",
                    "龙虎榜日期": "",
                    "龙虎榜净额(万)": "",
                    "来源标签": ["手动"],
                }
            ]
        )

        tab._apply_vcp_indicators_ui(
            {
                "600519": {
                    "rps": "",
                    "subsector": "",
                    "na_catalyst": "",
                    "block_trade": "",
                    "block_trade_amount_wan": "",
                    "earnings": "一季度 32.5%",
                    "earnings_qoq_pct": 32.5,
                    "lhb": "",
                }
            }
        )

        row = tab.model.row_data[0]
        assert row["业绩异动"] == "一季度 32.5%"
        assert row["业绩环比%"] == 32.5
        assert captured["payload"]["600519"]["业绩异动"] == "一季度 32.5%"
        assert captured["payload"]["600519"]["业绩环比%"] == 32.5
    finally:
        tab.deleteLater()
