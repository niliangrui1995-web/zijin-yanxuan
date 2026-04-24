# -*- coding: utf-8 -*-
from types import SimpleNamespace

from PyQt6.QtWidgets import QWidget

import ui.workspaces.classic_workspace as classic_workspace_module
from ui.workspaces.classic_workspace import ClassicWorkspace
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
            "foreign_block": _make_quote_tab({"600000", "688001"}),
            "na_daily": _make_quote_tab({"002415"}),
            "ai_industry_chain": _make_quote_tab({"688498"}),
            "earnings": _make_quote_tab({"300001"}),
            "lhb": _make_quote_tab({"601318"}),
            "fund_holdings": _make_quote_tab({"002594", "00700"}),
        }
    )

    codes = ClassicWorkspace.get_realtime_quote_codes(workspace)

    assert codes == {"000001", "600000", "300001", "688001", "002415", "688498", "601318"}


def test_workspace_quote_universe_skips_information_source_group():
    specs = [
        {"key": "watchlist", "group": "主工作台"},
        {"key": "ai_industry_chain", "group": "主工作台"},
        {"key": "lhb", "group": "主工作台"},
        {"key": "scan", "group": "情报源"},
        {"key": "foreign_block", "group": "情报源"},
        {"key": "earnings", "group": "情报源"},
        {"key": "fund_holdings", "group": "情报源"},
    ]
    workspace = _make_workspace(
        tabs={
            "watchlist": _make_quote_tab({"000001"}),
            "ai_industry_chain": _make_quote_tab({"688498"}),
            "lhb": _make_quote_tab({"601318"}),
            "scan": _make_quote_tab({"000002"}),
            "foreign_block": _make_quote_tab({"600000"}),
            "earnings": _make_quote_tab({"300001"}),
            "fund_holdings": _make_quote_tab({"002594"}),
        }
    )
    workspace.tab_specs = lambda: list(specs)

    codes = ClassicWorkspace.get_realtime_quote_codes(workspace)

    assert codes == {"000001", "688498", "601318"}


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
    assert earn_data["300750"]["text"] == "32.5%"
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

    class FakeStockDetailDialog:
        def __init__(self, code, name, signals, *, tab_titles, activate_callback, parent):
            created["code"] = code
            created["name"] = name
            created["signals"] = list(signals)
            created["tab_titles"] = dict(tab_titles)
            created["activate_callback"] = activate_callback
            created["parent"] = parent

        @staticmethod
        def exec():
            created["exec"] = True

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

    assert ClassicWorkspace.open_security_detail(workspace, "300750", {}) is True

    assert created["code"] == "300750"
    assert created["name"] == "宁德时代"
    assert created["signals"] == [signal]
    assert created["tab_titles"] == {"earnings": "业绩异动"}
    assert created["exec"] is True


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
        assert ctor_kwargs["lhb"]["autoload_pool"] is False
        assert ctor_kwargs["fund_holdings"]["autoload"] is False
        groups = {spec["key"]: spec["group"] for spec in workspace.tab_specs()}
        tab_keys = [spec["key"] for spec in workspace.tab_specs()]
        assert groups["lhb"] == "主工作台"
        assert groups["ai_industry_chain"] == "主工作台"
        assert groups["stock_candidates"] == "主工作台"
        assert groups["scan"] == "情报源"
        assert tab_keys.index("na_daily") < tab_keys.index("ai_industry_chain") < tab_keys.index("lhb")
        assert tab_keys.index("lhb") < tab_keys.index("stock_candidates") < tab_keys.index("rt_monitor")
        assert "autoload_pool" not in ctor_kwargs["watchlist"]
        assert "autoload" not in ctor_kwargs["watchlist"]
    finally:
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
