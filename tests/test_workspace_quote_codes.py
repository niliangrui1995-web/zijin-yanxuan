# -*- coding: utf-8 -*-
from types import SimpleNamespace

from PyQt6.QtCore import QEventLoop, QTimer
from PyQt6.QtWidgets import QApplication, QWidget

import ui.workspaces.classic_workspace as classic_workspace_module
import ui.workspaces.stock_context_service as stock_context_module
from ui.components.smooth_tab_widget import SmoothTabWidget
from ui.workspaces.classic_workspace import ClassicWorkspace
from ui.workspaces.stock_context_service import StockContextService
from ui.workspaces.stock_signal import StockSignal


def _make_workspace(*, tabs=None, engine=None):
    ordered_tabs = dict(tabs or {})
    workspace = SimpleNamespace(engine=engine)
    workspace.get_tab = lambda key: ordered_tabs.get(key)
    workspace.iter_tabs = lambda: [tab for tab in ordered_tabs.values() if tab is not None]
    return workspace


def _make_rows_tab(rows):
    return SimpleNamespace(get_row_data=lambda current_model=None: list(rows or []))


def _make_lhb_radar_tab(rows):
    return SimpleNamespace(get_watchlist_radar_rows=lambda: list(rows or []))


def _make_quote_tab(codes):
    return SimpleNamespace(get_realtime_quote_codes=lambda: set(codes or set()))


def test_workspace_collects_a_share_quote_codes_from_public_tab_apis():
    workspace = _make_workspace(
        tabs={
            "scan": _make_quote_tab({"000001"}),
            "rt_monitor": _make_quote_tab({"600000"}),
            "watchlist": _make_quote_tab({"000001", "300001"}),
            "asian_market": _make_quote_tab({"600519"}),
            "stock_candidates": _make_quote_tab({"300750"}),
            "foreign_block": _make_quote_tab({"600000", "688001"}),
            "na_daily": _make_quote_tab({"002415"}),
            "ai_industry_chain": _make_quote_tab({"688498"}),
            "earnings": _make_quote_tab({"300001"}),
            "lhb": _make_quote_tab({"601318"}),
            "fund_holdings": _make_quote_tab({"002594", "00700"}),
        }
    )

    codes = ClassicWorkspace.get_realtime_quote_codes(workspace)

    assert codes == {
        "000001",
        "600000",
        "300001",
        "300750",
        "688001",
        "002415",
        "688498",
        "601318",
    }


def test_workspace_quote_universe_skips_information_source_group_and_non_a_share_tabs():
    specs = [
        {"key": "watchlist", "group": "主工作台"},
        {"key": "asian_market", "group": "主工作台"},
        {"key": "stock_candidates", "group": "主工作台"},
        {"key": "ai_industry_chain", "group": "主工作台"},
        {"key": "lhb", "group": "主工作台"},
        {"key": "rt_monitor", "group": "主工作台"},
        {"key": "scan", "group": "情报源"},
        {"key": "foreign_block", "group": "情报源"},
        {"key": "earnings", "group": "情报源"},
        {"key": "fund_holdings", "group": "情报源"},
    ]
    workspace = _make_workspace(
        tabs={
            "watchlist": _make_quote_tab({"000001"}),
            "asian_market": _make_quote_tab({"600519"}),
            "stock_candidates": _make_quote_tab({"300750"}),
            "ai_industry_chain": _make_quote_tab({"688498"}),
            "lhb": _make_quote_tab({"601318"}),
            "rt_monitor": _make_quote_tab({"002415"}),
            "scan": _make_quote_tab({"000002"}),
            "foreign_block": _make_quote_tab({"600000"}),
            "earnings": _make_quote_tab({"300001"}),
            "fund_holdings": _make_quote_tab({"002594"}),
        }
    )
    workspace.tab_specs = lambda: list(specs)

    codes = ClassicWorkspace.get_realtime_quote_codes(workspace)

    assert codes == {"000001", "300750", "688498", "601318", "002415"}


def test_workspace_quote_universe_does_not_instantiate_lazy_tabs():
    loaded_tabs = {
        "watchlist": _make_quote_tab({"000001"}),
    }
    get_tab_calls = []
    workspace = SimpleNamespace(
        tab_specs=lambda: [
            {"key": "watchlist", "group": "主工作台"},
            {"key": "lhb", "group": "主工作台"},
            {"key": "rt_monitor", "group": "主工作台"},
        ],
        get_loaded_tab=lambda key: loaded_tabs.get(key),
        get_tab=lambda key: get_tab_calls.append(key) or _make_quote_tab({key}),
    )

    codes = ClassicWorkspace.get_realtime_quote_codes(workspace)

    assert codes == {"000001"}
    assert get_tab_calls == []


def test_workspace_primes_watchlist_with_public_startup_hook():
    called = []
    workspace = _make_workspace(
        tabs={
            "watchlist": SimpleNamespace(prime_startup_state=lambda: called.append("watchlist")),
        }
    )

    ClassicWorkspace.schedule_watchlist_special_quotes(workspace, task_manager=None)

    assert called == ["watchlist"]


def test_workspace_collects_structured_watchlist_radar_metrics():
    workspace = _make_workspace(
        engine=SimpleNamespace(get_precomputed_rps=lambda: {"cached": True}),
        tabs={
            "na_daily": _make_rows_tab([]),
            "ai_industry_chain": _make_rows_tab(
                [
                    {
                        "代码": "300750",
                        "细分板块": "液冷 / 储能链",
                    }
                ]
            ),
            "foreign_block": _make_rows_tab(
                [
                    {
                        "代码": "300750",
                        "交易详情": "买入",
                        "买方营业部": "机构专用",
                        "卖方营业部": "",
                        "成交金额(万元)": 2709,
                    }
                ]
            ),
            "earnings": _make_rows_tab(
                [
                    {
                        "代码": "300750",
                        "环比%": 32.5,
                        "报告期": "2026-03-31",
                    }
                ]
            ),
            "lhb": _make_rows_tab(
                [
                    {
                        "代码": "300750",
                        "_最近上榜_raw": "20260420",
                        "上榜净买额(万)": 1200,
                        "机构净买(万)": 800,
                        "外资净买(万)": -150,
                    }
                ]
            ),
        },
    )

    na_data, na_subsector_data, block_data, earn_data, lhb_data, rps_bundle = ClassicWorkspace.collect_watchlist_radar_data(
        workspace
    )

    assert na_data == {}
    assert na_subsector_data == {"300750": "液冷 / 储能链"}
    assert rps_bundle == {"cached": True}
    assert block_data["300750"]["text"] == "机构专用买入2709万"
    assert block_data["300750"]["amount_wan"] == 2709
    assert earn_data["300750"]["text"] == "一季度 32.5%"
    assert earn_data["300750"]["qoq_pct"] == 32.5
    assert lhb_data["300750"]["text"] == "04-20 | 净买1200万 | 机构净买800万 | 外资净卖150万"
    assert lhb_data["300750"]["net_wan"] == 1200


def test_workspace_collects_stock_context_signals_by_code():
    workspace = _make_workspace(
        tabs={
            "na_daily": _make_rows_tab(
                [
                    {
                        "代码": "300750",
                        "名称": "宁德时代",
                        "催化剂": "北美订单催化",
                        "细分板块": "储能链",
                    }
                ]
            ),
            "earnings": _make_rows_tab(
                [
                    {
                        "代码": "300750",
                        "环比%": 32.5,
                    }
                ]
            ),
        },
    )

    context = ClassicWorkspace.collect_stock_context(workspace)

    signals = context["300750"]
    assert {(signal.source_tab, signal.signal_type) for signal in signals} == {
        ("na_daily", "catalyst"),
        ("na_daily", "subsector"),
        ("earnings", "earnings"),
    }
    assert [signal.summary for signal in signals if signal.signal_type == "catalyst"] == ["北美订单催化"]


def test_workspace_earnings_context_signal_includes_report_label():
    workspace = _make_workspace(
        tabs={
            "earnings": _make_rows_tab(
                [
                    {
                        "代码": "600176",
                        "名称": "中国巨石",
                        "环比%": 45.42,
                        "报告期": "2026-03-31",
                    },
                    {
                        "代码": "603186",
                        "名称": "华正新材",
                        "环比%": 81.67,
                        "报告期": "2025-12-31",
                    },
                ]
            ),
        },
    )

    context = ClassicWorkspace.collect_stock_context(workspace)

    assert [signal.summary for signal in context["600176"]] == ["一季度 45.42%"]
    assert [signal.summary for signal in context["603186"]] == ["年报 81.67%"]


def test_workspace_collects_scan_and_fund_holding_context_signals(monkeypatch):
    monkeypatch.setattr(StockContextService, "_query_fund_holding_store_rows", lambda self: [])
    workspace = _make_workspace(
        tabs={
            "scan": SimpleNamespace(
                get_scan_results=lambda: [
                    {
                        "代码": "688498",
                        "名称": "源杰科技",
                        "触发日期": "20260423",
                        "评分": "91",
                        "RPS强度": "96",
                        "突破状态": "接近突破",
                    }
                ]
            ),
            "fund_holdings": _make_rows_tab(
                [
                    {
                        "代码": "688498",
                        "名称": "源杰科技",
                        "主体": "QFII",
                        "资金属性": "外资",
                        "主体代码": "QFII",
                        "季度": "2025Q3",
                        "变化类型": "新进",
                        "本期占比": "1.25%",
                        "持股变化": "+120.00万",
                    },
                    {
                        "代码": "688498",
                        "名称": "源杰科技",
                        "主体": "QFII",
                        "资金属性": "外资",
                        "主体代码": "QFII",
                        "季度": "2025Q4",
                        "变化类型": "减持",
                        "本期占比": "1.10%",
                        "持股变化": "-20.00万",
                    },
                    {
                        "代码": "688498",
                        "名称": "源杰科技",
                        "主体": "QFII",
                        "资金属性": "外资",
                        "主体代码": "QFII",
                        "季度": "2025Q4",
                        "变化类型": "新进",
                        "本期占比": "1.25%",
                        "持股变化": "+120.00万",
                    }
                ]
            ),
        },
    )

    context = ClassicWorkspace.collect_stock_context(workspace)

    signals = context["688498"]
    assert {(signal.source_tab, signal.signal_type) for signal in signals} == {
        ("scan", "vcp_scan"),
        ("fund_holdings", "fund_holding"),
    }
    assert any("评分91" in signal.summary for signal in signals)
    assert any("QFII" in signal.summary and "新进" in signal.summary for signal in signals)
    assert not any("2025Q3" in signal.summary for signal in signals)
    assert not any("减持" in signal.summary for signal in signals)


def test_workspace_collects_scan_context_from_cache_without_loading_lazy_tab(monkeypatch):
    import core.data_store as data_store_module

    class _FakeDataStore:
        def load_json(self, key, default=None):
            assert key == "scan_cache"
            return {
                "results": [
                    {
                        stock_context_module.KEY_CODE: "688498",
                        stock_context_module.KEY_NAME: "sample",
                        stock_context_module.KEY_TRIGGER_DATE: "20260430",
                        stock_context_module.KEY_SCORE: "91",
                        stock_context_module.KEY_RPS_STRENGTH: "96",
                    }
                ]
            }

    get_tab_calls = []
    monkeypatch.setattr(data_store_module, "DataStore", lambda: _FakeDataStore())
    workspace = SimpleNamespace(
        engine=None,
        tab_specs=lambda: [{"key": "scan", "group": "info"}],
        get_loaded_tab=lambda key: None,
        get_tab=lambda key: get_tab_calls.append(key) or None,
        iter_tabs=lambda: [],
    )

    context = ClassicWorkspace.collect_stock_context(workspace)

    assert get_tab_calls == []
    signals = context["688498"]
    assert [(signal.source_tab, signal.signal_type) for signal in signals] == [("scan", "vcp_scan")]
    assert signals[0].name == "sample"
    assert signals[0].numeric_value == 91.0
    assert signals[0].observed_at == "20260430"


def test_watchlist_radar_skips_scan_cache_fallback_on_ui_thread(monkeypatch):
    import core.data_store as data_store_module

    def fail_datastore():
        raise AssertionError("watchlist radar should not block on scan cache")

    monkeypatch.setattr(data_store_module, "DataStore", fail_datastore)
    workspace = SimpleNamespace(
        engine=None,
        tab_specs=lambda: [{"key": "scan", "group": "info"}],
        get_loaded_tab=lambda key: None,
        get_tab=lambda key: (_ for _ in ()).throw(AssertionError("lazy tab should not load")),
        iter_tabs=lambda: [],
    )

    na_data, na_subsector_data, block_data, earn_data, lhb_data, rps_bundle = (
        ClassicWorkspace.collect_watchlist_radar_data(workspace)
    )

    assert na_data == {}
    assert na_subsector_data == {}
    assert block_data == {}
    assert earn_data == {}
    assert lhb_data == {}
    assert rps_bundle is None


def test_workspace_collects_lhb_context_from_pool_cache_without_loading_lazy_tab(monkeypatch):
    import core.lhb_pool_manager as lhb_pool_module

    captured = {}

    class _FakePoolManager:
        def compute_pool(self, *, data_provider=None, engine=None):
            captured["data_provider"] = data_provider
            captured["engine"] = engine
            return [
                {
                    stock_context_module.KEY_CODE: "300750",
                    stock_context_module.KEY_NAME: "sample",
                    stock_context_module.KEY_LAST_LISTED: "20260430",
                    stock_context_module.KEY_NET_WAN: 1200,
                    stock_context_module.KEY_INST_WAN: 800,
                    stock_context_module.KEY_FOREIGN_WAN: 50,
                }
            ]

    monkeypatch.setattr(lhb_pool_module, "LhbPoolManager", _FakePoolManager)
    workspace = SimpleNamespace(
        data_provider="provider",
        engine="engine",
        tab_specs=lambda: [{"key": "lhb", "group": "info"}],
        get_loaded_tab=lambda key: None,
        get_tab=lambda key: (_ for _ in ()).throw(AssertionError("lazy tab should not load")),
        iter_tabs=lambda: [],
    )

    context = ClassicWorkspace.collect_stock_context(workspace)

    assert captured == {"data_provider": "provider", "engine": "engine"}
    signals = context["300750"]
    assert [(signal.source_tab, signal.signal_type) for signal in signals] == [("lhb", "lhb")]
    assert signals[0].numeric_value == 1200
    assert signals[0].observed_at == "20260430"


def test_workspace_collects_fund_holding_context_from_snapshot_without_open_tab(monkeypatch):
    monkeypatch.setattr(
        StockContextService,
        "_cached_fund_holding_rows",
        lambda self: [
            {
                "代码": "300750",
                "名称": "宁德时代",
                "主体": "睿远基金",
                "主体代码": "ruiyuan",
                "季度": "2025Q4",
                "变化类型": "增持",
                "本期占比": "2.30%",
                "持股变化": "+80.00",
                "_is_latest_subject_quarter": True,
            }
        ],
    )
    workspace = _make_workspace(tabs={})
    workspace.tab_specs = lambda: [{"key": "fund_holdings", "group": "情报源"}]

    context = ClassicWorkspace.collect_stock_context(workspace)

    signals = context["300750"]
    assert {(signal.source_tab, signal.signal_type) for signal in signals} == {
        ("fund_holdings", "fund_holding"),
    }
    assert signals[0].summary == "睿远基金 | 增持 | 2025Q4 | 占比2.30% | 变化+80.00"


def test_workspace_fund_holding_context_schedules_snapshot_without_blocking(monkeypatch):
    calls = []
    monkeypatch.setattr(
        StockContextService,
        "refresh_async_snapshots",
        lambda self, *, force=False: calls.append(force) or True,
    )
    monkeypatch.setattr(
        StockContextService,
        "_query_fund_holding_store_rows",
        lambda self: (_ for _ in ()).throw(AssertionError("store query should not run on UI collect")),
    )
    workspace = _make_workspace(tabs={})
    workspace.tab_specs = lambda: [{"key": "fund_holdings", "group": "情报源"}]

    context = ClassicWorkspace.collect_stock_context(workspace)

    assert context == {}
    assert calls == [False]


def test_workspace_accepts_direct_stock_signal_capability():
    workspace = _make_workspace(
        tabs={
            "custom": SimpleNamespace(
                iter_stock_signals=lambda: [
                    StockSignal(
                        code="688498",
                        source_tab="custom",
                        signal_type="research_note",
                        summary="自定义研究信号",
                    )
                ]
            )
        },
    )

    signals = ClassicWorkspace.collect_stock_signals(workspace)

    assert signals == [
        StockSignal(
            code="688498",
            source_tab="custom",
            signal_type="research_note",
            summary="自定义研究信号",
        )
    ]


def test_workspace_open_security_detail_builds_stock_dialog(monkeypatch):
    created = {}

    class FakeSignal:
        @staticmethod
        def connect(callback):
            created["destroyed_callback"] = callback

    class FakeStockDetailDialog:
        def __init__(self, code, name, signals, *, tab_titles, activate_callback, context, parent):
            created["code"] = code
            created["name"] = name
            created["signals"] = list(signals)
            created["tab_titles"] = dict(tab_titles)
            created["activate_callback"] = activate_callback
            created["context"] = dict(context)
            created["parent"] = parent
            self.destroyed = FakeSignal()

        def show(self):
            created["show"] = True

        def raise_(self):
            created["raise"] = True

        def activateWindow(self):
            created["activate"] = True

    monkeypatch.setattr("ui.components.stock_detail_dialog.StockDetailDialog", FakeStockDetailDialog)
    signal = StockSignal(
        code="300750",
        source_tab="earnings",
        signal_type="earnings",
        summary="32.5%",
    )
    workspace = SimpleNamespace(
        data_provider=SimpleNamespace(code2name={"300750": "宁德时代"}),
        collect_stock_context=lambda: {"300750": [signal]},
        tab_specs=lambda: [{"key": "earnings", "title": "业绩异动"}],
        window=lambda: None,
        _activate_stock_signal_source=lambda _signal: True,
    )

    assert ClassicWorkspace.open_security_detail(workspace, "300750", {"vcp_data": {"市价": "183.50"}}) is True

    assert created["code"] == "300750"
    assert created["name"] == "宁德时代"
    assert created["signals"] == [signal]
    assert created["tab_titles"] == {"earnings": "业绩异动"}
    assert created["context"] == {"市价": "183.50"}
    assert created["show"] is True
    assert created["raise"] is True
    assert created["activate"] is True
    assert workspace._stock_detail_dialogs["300750"] is not None


def test_workspace_open_security_detail_reuses_visible_stock_dialog(monkeypatch):
    events = []

    class ExistingDialog:
        @staticmethod
        def isVisible():
            return True

        @staticmethod
        def raise_():
            events.append("raise")

        @staticmethod
        def activateWindow():
            events.append("activate")

    class FailStockDetailDialog:
        def __init__(self, *args, **kwargs):
            raise AssertionError("visible stock detail dialog should be reused")

    monkeypatch.setattr("ui.components.stock_detail_dialog.StockDetailDialog", FailStockDetailDialog)
    existing = ExistingDialog()
    workspace = SimpleNamespace(
        data_provider=SimpleNamespace(code2name={"300750": "宁德时代"}),
        collect_stock_context=lambda: {"300750": []},
        tab_specs=lambda: [{"key": "earnings", "title": "业绩异动"}],
        window=lambda: None,
        _activate_stock_signal_source=lambda _signal: True,
        _stock_detail_dialogs={"300750": existing},
    )

    assert ClassicWorkspace.open_security_detail(workspace, "300750", {"vcp_data": {}}) is True
    assert workspace._stock_detail_dialogs["300750"] is existing
    assert events == ["raise", "activate"]


def test_workspace_activates_stock_signal_source_tab():
    selected = []

    class FakeTabs:
        @staticmethod
        def setCurrentIndex(index):
            selected.append(index)

    source_tab = SimpleNamespace(select_code_row=lambda code: selected.append(code) or True)
    workspace = _make_workspace(tabs={"earnings": source_tab})
    workspace.tabs = FakeTabs()
    workspace.tab_specs = lambda: [{"key": "earnings", "title": "业绩异动"}]

    ok = ClassicWorkspace._activate_stock_signal_source(
        workspace,
        StockSignal(
            code="300750",
            source_tab="earnings",
            signal_type="earnings",
            summary="32.5%",
        ),
    )

    assert ok is True
    assert selected == [0, "300750"]


def test_workspace_watchlist_subsector_prefers_ai_chain_over_na_daily():
    workspace = _make_workspace(
        tabs={
            "na_daily": _make_rows_tab(
                [
                    {"代码": "300750", "细分板块": "北美战报旧分类"},
                    {"代码": "002415", "细分板块": "北美战报独有分类"},
                ]
            ),
            "ai_industry_chain": _make_rows_tab(
                [
                    {"代码": "300750", "细分板块": "液冷 / 储能链"},
                    {"代码": "300750", "细分板块": "后续重复分类"},
                ]
            ),
        },
    )

    _, na_subsector_data, *_ = ClassicWorkspace.collect_watchlist_radar_data(workspace)

    assert na_subsector_data == {
        "300750": "液冷 / 储能链",
        "002415": "北美战报独有分类",
    }


def test_workspace_collects_lhb_radar_from_deferred_cache_reader():
    workspace = _make_workspace(
        tabs={
            "lhb": _make_lhb_radar_tab(
                [
                    {
                        "代码": "300750",
                        "_最近上榜_raw": "20260420",
                        "上榜净买额(万)": 1200,
                        "机构净买(万)": 800,
                        "外资净买(万)": -150,
                    }
                ]
            ),
        },
    )

    *_, lhb_data, _ = ClassicWorkspace.collect_watchlist_radar_data(workspace)

    assert lhb_data["300750"]["text"] == "04-20 | 净买1200万 | 机构净买800万 | 外资净卖150万"
    assert lhb_data["300750"]["date"] == "20260420"


def test_workspace_refreshes_all_tabs_after_f5():
    calls = []
    tabs = {
        "watchlist": SimpleNamespace(refresh_table_from_latest_snapshot=lambda: calls.append("watchlist")),
        "lhb": SimpleNamespace(refresh_table_from_latest_snapshot=lambda: calls.append("lhb")),
        "na_daily": SimpleNamespace(refresh_table_from_latest_snapshot=lambda: calls.append("na_daily")),
        "ai_industry_chain": SimpleNamespace(refresh_table_from_latest_snapshot=lambda: calls.append("ai_chain")),
        "asian_market": SimpleNamespace(refresh_table_from_latest_snapshot=lambda: calls.append("asian")),
        "rt_monitor": SimpleNamespace(refresh_table_from_latest_snapshot=lambda: calls.append("rt")),
        "foreign_block": SimpleNamespace(refresh_table_from_latest_snapshot=lambda: calls.append("foreign")),
        "fund_holdings": SimpleNamespace(refresh_table_from_latest_snapshot=lambda: calls.append("fund")),
        "earnings": SimpleNamespace(refresh_table_from_latest_snapshot=lambda: calls.append("earnings")),
        "scan": SimpleNamespace(refresh_table_from_latest_snapshot=lambda: calls.append("scan")),
        "system_log": SimpleNamespace(),
    }
    workspace = _make_workspace(tabs=tabs)
    workspace.iter_refreshable_tabs = lambda: ClassicWorkspace.iter_refreshable_tabs(workspace)

    ClassicWorkspace.refresh_all_tabs_after_f5(workspace)

    assert calls == [
        "watchlist",
        "lhb",
        "na_daily",
        "ai_chain",
        "asian",
        "rt",
        "foreign",
        "fund",
        "earnings",
        "scan",
    ]


def test_workspace_schedules_refreshes_all_tabs_after_f5():
    app = QApplication.instance() or QApplication([])
    calls = []
    done = []
    tabs = {
        "watchlist": SimpleNamespace(refresh_table_from_latest_snapshot=lambda: calls.append("watchlist")),
        "lhb": SimpleNamespace(refresh_table_from_latest_snapshot=lambda: calls.append("lhb")),
        "system_log": SimpleNamespace(),
    }
    workspace = _make_workspace(tabs=tabs)
    workspace.iter_refreshable_tabs = lambda: ClassicWorkspace.iter_refreshable_tabs(workspace)

    assert ClassicWorkspace.refresh_all_tabs_after_f5_scheduled(
        workspace,
        on_finished=lambda: done.append("done"),
        interval_ms=0,
    ) is True

    for _ in range(10):
        app.processEvents()
        if done:
            break

    assert calls == ["watchlist", "lhb"]
    assert done == ["done"]
    assert getattr(workspace, "_f5_refresh_scheduler", None) is None


def test_workspace_scheduled_f5_can_skip_cache_reload_driven_tabs():
    app = QApplication.instance() or QApplication([])
    calls = []

    class CacheSignalTab:
        def refresh_table_from_latest_snapshot(self):
            calls.append("cache_signal")

        def _on_cache_reload_completed(self):
            pass

    tabs = {
        "watchlist": SimpleNamespace(refresh_table_from_latest_snapshot=lambda: calls.append("watchlist")),
        "scan": CacheSignalTab(),
        "stock_candidates": SimpleNamespace(
            refresh_table_from_latest_snapshot=lambda: calls.append("stock_candidates"),
            _schedule_context_refresh=lambda *_args: None,
        ),
    }
    workspace = _make_workspace(tabs=tabs)
    workspace.iter_refreshable_tabs = lambda: ClassicWorkspace.iter_refreshable_tabs(workspace)

    assert ClassicWorkspace.refresh_all_tabs_after_f5_scheduled(
        workspace,
        interval_ms=0,
        skip_cache_reload_tabs=True,
    ) is True

    for _ in range(10):
        app.processEvents()
        if getattr(workspace, "_f5_refresh_scheduler", None) is None:
            break

    assert calls == ["watchlist"]


def test_workspace_f5_snapshot_refresh_uses_sync_local_snapshot():
    calls = []

    class SyncAwareTab:
        def refresh_table_from_latest_snapshot(self, *, async_local=True):
            calls.append(async_local)

    workspace = _make_workspace(tabs={"asian_market": SyncAwareTab()})
    workspace.iter_refreshable_tabs = lambda: ClassicWorkspace.iter_refreshable_tabs(workspace)

    ClassicWorkspace.refresh_all_tabs_after_f5(workspace)

    assert calls == [False]


def test_workspace_f5_snapshot_refresh_prioritizes_current_tab():
    calls = []
    current_tab = SimpleNamespace(refresh_table_from_latest_snapshot=lambda: calls.append("lhb"))
    tabs = {
        "watchlist": SimpleNamespace(refresh_table_from_latest_snapshot=lambda: calls.append("watchlist")),
        "lhb": current_tab,
        "na_daily": SimpleNamespace(refresh_table_from_latest_snapshot=lambda: calls.append("na_daily")),
    }
    workspace = _make_workspace(tabs=tabs)
    workspace.iter_refreshable_tabs = lambda: ClassicWorkspace.iter_refreshable_tabs(workspace)
    workspace.tabs = SimpleNamespace(currentWidget=lambda: current_tab)

    ClassicWorkspace.refresh_all_tabs_after_f5(workspace)

    assert calls == ["lhb", "watchlist", "na_daily"]


def test_workspace_runs_fund_holdings_auto_sync_through_public_facade():
    calls = []
    workspace = _make_workspace(
        tabs={
            "fund_holdings": SimpleNamespace(run_auto_sync_after_f5=lambda: (calls.append("fund") or True)),
        }
    )

    assert ClassicWorkspace.run_fund_holdings_auto_sync_after_f5(workspace) is True
    assert calls == ["fund"]


def test_workspace_refreshes_information_sources_after_f5():
    calls = []
    specs = [
        {"key": "watchlist", "group": "主工作台"},
        {"key": "scan", "group": "情报源"},
        {"key": "foreign_block", "group": "情报源"},
        {"key": "earnings", "group": "情报源"},
        {"key": "fund_holdings", "group": "情报源"},
        {"key": "system_log", "group": "系统"},
    ]
    workspace = _make_workspace(
        tabs={
            "watchlist": SimpleNamespace(refresh_data_after_f5=lambda: calls.append("watchlist")),
            "scan": SimpleNamespace(refresh_data_after_f5=lambda: (calls.append("scan") or True)),
            "foreign_block": SimpleNamespace(refresh_data_after_f5=lambda: (calls.append("foreign") or True)),
            "earnings": SimpleNamespace(refresh_data_after_f5=lambda: (calls.append("earnings") or True)),
            "fund_holdings": SimpleNamespace(refresh_data_after_f5=lambda: (calls.append("fund") or True)),
            "system_log": SimpleNamespace(refresh_data_after_f5=lambda: calls.append("log")),
        }
    )
    workspace.tab_specs = lambda: list(specs)

    results = ClassicWorkspace.refresh_information_sources_after_f5(workspace)

    assert calls == ["scan", "foreign", "earnings", "fund"]
    assert results == {
        "scan": True,
        "foreign_block": True,
        "earnings": True,
        "fund_holdings": True,
    }


def test_workspace_defers_heavy_tab_autoload(monkeypatch):
    ctor_kwargs = {}

    def _make_tab(name):
        class _Tab(QWidget):
            def __init__(self, *args, **kwargs):
                super().__init__()
                ctor_kwargs[name] = dict(kwargs)

        return _Tab

    monkeypatch.setattr(classic_workspace_module, "WatchlistTab", _make_tab("watchlist"))
    monkeypatch.setattr(classic_workspace_module, "AsianMarketTab", _make_tab("asian_market"))
    monkeypatch.setattr(classic_workspace_module, "NADailyTab", _make_tab("na_daily"))
    monkeypatch.setattr(classic_workspace_module, "AIIndustryChainTab", _make_tab("ai_industry_chain"))
    monkeypatch.setattr(classic_workspace_module, "RtMonitorTab", _make_tab("rt_monitor"))
    monkeypatch.setattr(classic_workspace_module, "ScanTab", _make_tab("scan"))
    monkeypatch.setattr(classic_workspace_module, "StockCandidateTab", _make_tab("stock_candidates"))
    monkeypatch.setattr(classic_workspace_module, "LhbTab", _make_tab("lhb"))
    monkeypatch.setattr(classic_workspace_module, "ForeignBlockTradeTab", _make_tab("foreign_block"))
    monkeypatch.setattr(classic_workspace_module, "EarningsTab", _make_tab("earnings"))
    monkeypatch.setattr(classic_workspace_module, "FundHoldingsTab", _make_tab("fund_holdings"))
    monkeypatch.setattr(classic_workspace_module, "LogTab", _make_tab("system_log"))

    workspace = classic_workspace_module.ClassicWorkspace(data_provider=object(), engine=object())
    try:
        assert set(ctor_kwargs) == {"watchlist"}
        assert workspace.get_loaded_tab("lhb") is None
        assert workspace.get_loaded_tab("fund_holdings") is None

        workspace.ensure_tab_loaded("lhb")
        assert ctor_kwargs["lhb"]["autoload_pool"] is False
        workspace.ensure_tab_loaded("fund_holdings")
        assert ctor_kwargs["fund_holdings"]["autoload"] is False
        groups = {spec["key"]: spec["group"] for spec in workspace.tab_specs()}
        tab_keys = [spec["key"] for spec in workspace.tab_specs()]
        assert groups["lhb"] == "主工作台"
        assert groups["ai_industry_chain"] == "主工作台"
        assert groups["stock_candidates"] == "主工作台"
        assert groups["scan"] == "情报源"
        assert tab_keys.index("na_daily") < tab_keys.index("stock_candidates") < tab_keys.index("ai_industry_chain")
        assert tab_keys.index("ai_industry_chain") < tab_keys.index("lhb") < tab_keys.index("rt_monitor")
        assert "autoload_pool" not in ctor_kwargs["watchlist"]
        assert "autoload" not in ctor_kwargs["watchlist"]
        assert isinstance(workspace.tabs, SmoothTabWidget)
        assert workspace.tabs._transition_enabled is False
    finally:
        workspace.shutdown()
        workspace.deleteLater()


def test_workspace_background_prewarm_loads_lazy_tabs_without_manual_click(monkeypatch):
    ctor_kwargs = {}
    constructed = []
    primed = []

    def _make_tab(name):
        class _Tab(QWidget):
            def __init__(self, *args, **kwargs):
                super().__init__()
                ctor_kwargs[name] = dict(kwargs)
                constructed.append(name)

            def prime_background_load(self):
                primed.append(name)

        return _Tab

    monkeypatch.setattr(classic_workspace_module, "WatchlistTab", _make_tab("watchlist"))
    monkeypatch.setattr(classic_workspace_module, "AsianMarketTab", _make_tab("asian_market"))
    monkeypatch.setattr(classic_workspace_module, "NADailyTab", _make_tab("na_daily"))
    monkeypatch.setattr(classic_workspace_module, "AIIndustryChainTab", _make_tab("ai_industry_chain"))
    monkeypatch.setattr(classic_workspace_module, "RtMonitorTab", _make_tab("rt_monitor"))
    monkeypatch.setattr(classic_workspace_module, "ScanTab", _make_tab("scan"))
    monkeypatch.setattr(classic_workspace_module, "StockCandidateTab", _make_tab("stock_candidates"))
    monkeypatch.setattr(classic_workspace_module, "LhbTab", _make_tab("lhb"))
    monkeypatch.setattr(classic_workspace_module, "ForeignBlockTradeTab", _make_tab("foreign_block"))
    monkeypatch.setattr(classic_workspace_module, "EarningsTab", _make_tab("earnings"))
    monkeypatch.setattr(classic_workspace_module, "FundHoldingsTab", _make_tab("fund_holdings"))
    monkeypatch.setattr(classic_workspace_module, "LogTab", _make_tab("system_log"))

    workspace = classic_workspace_module.ClassicWorkspace(data_provider=object(), engine=object())
    try:
        assert constructed == ["watchlist"]

        workspace.schedule_restore_last_tab(10, delay_ms=999_999)
        snapshot_primes = []
        workspace.prime_stock_context_snapshots = lambda **kwargs: snapshot_primes.append(kwargs) or True
        monkeypatch.setattr(classic_workspace_module.QTimer, "singleShot", lambda _delay, callback: callback())

        workspace._start_background_tab_prewarm()

        assert constructed[1] == "fund_holdings"
        assert set(ctor_kwargs) == {
            "watchlist",
            "asian_market",
            "na_daily",
            "stock_candidates",
            "ai_industry_chain",
            "lhb",
            "rt_monitor",
            "scan",
            "foreign_block",
            "earnings",
            "fund_holdings",
            "system_log",
        }
        assert ctor_kwargs["lhb"]["autoload_pool"] is False
        assert ctor_kwargs["fund_holdings"]["autoload"] is False
        assert snapshot_primes == [{}]
        assert "fund_holdings" in primed
    finally:
        workspace.shutdown()
        workspace.deleteLater()


def test_classic_workspace_pending_restore_timer_is_cancelled_on_shutdown(monkeypatch, qt_application):
    class _Tab(QWidget):
        def __init__(self, *args, **kwargs):
            super().__init__()

    monkeypatch.setattr(classic_workspace_module, "WatchlistTab", _Tab)

    calls = []
    monkeypatch.setattr(
        classic_workspace_module.ClassicWorkspace,
        "restore_last_tab",
        lambda self, index: calls.append(index),
    )

    workspace = classic_workspace_module.ClassicWorkspace(
        data_provider=object(),
        engine=object(),
        background_prewarm=False,
    )
    try:
        workspace.schedule_restore_last_tab(1, delay_ms=10)
        timer = workspace._restore_last_tab_timer

        assert timer is not None
        assert timer.isActive()

        workspace.shutdown()

        assert workspace._restore_last_tab_timer is None
        assert timer.isActive() is False

        loop = QEventLoop()
        QTimer.singleShot(30, loop.quit)
        loop.exec()
        qt_application.processEvents()

        assert calls == []
    finally:
        workspace.shutdown()
        workspace.deleteLater()


def test_workspace_shutdown_continues_after_tab_failure():
    calls = []

    class _BrokenTab:
        def shutdown(self):
            calls.append("broken")
            raise RuntimeError("boom")

    workspace = _make_workspace(
        tabs={
            "broken": _BrokenTab(),
            "good": SimpleNamespace(shutdown=lambda: calls.append("good")),
        }
    )

    ClassicWorkspace.shutdown(workspace)

    assert calls == ["broken", "good"]
