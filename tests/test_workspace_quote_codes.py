# -*- coding: utf-8 -*-
from types import SimpleNamespace

import pytest
from PyQt6.QtCore import QEventLoop, QTimer
from PyQt6.QtWidgets import QApplication, QWidget

import ui.workspaces.classic_workspace as classic_workspace_module
import ui.workspaces.stock_context_service as stock_context_module
from ui.components.smooth_tab_widget import SmoothTabWidget
from ui.workspaces.classic_workspace import ClassicWorkspace
from ui.workspaces.quote_universe_service import INFO_SOURCE_GROUP
from ui.workspaces.stock_context_service import StockContextService
from ui.workspaces.stock_signal import StockSignal


@pytest.fixture(autouse=True)
def _isolate_na_daily_cache_fallback(monkeypatch):
    monkeypatch.setattr(StockContextService, "_load_na_daily_cache_rows", lambda self: [])


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


def _drain_qt_events(app) -> None:
    app.processEvents()
    loop = QEventLoop()
    QTimer.singleShot(0, loop.quit)
    loop.exec()
    app.processEvents()


def _patch_lightweight_workspace_tabs(monkeypatch, constructed, ctor_kwargs=None):
    def _make_tab(name):
        class _Tab(QWidget):
            def __init__(self, *args, **kwargs):
                super().__init__()
                constructed.append(name)
                if ctor_kwargs is not None:
                    ctor_kwargs[name] = dict(kwargs)

        return _Tab

    monkeypatch.setattr(classic_workspace_module, "WatchlistTab", _make_tab("watchlist"))
    monkeypatch.setattr(classic_workspace_module, "AsianMarketTab", _make_tab("asian_market"))
    monkeypatch.setattr(classic_workspace_module, "NADailyTab", _make_tab("na_daily"))
    monkeypatch.setattr(classic_workspace_module, "AIIndustryChainTab", _make_tab("ai_industry_chain"))
    monkeypatch.setattr(classic_workspace_module, "ScanTab", _make_tab("scan"))
    monkeypatch.setattr(classic_workspace_module, "StockCandidateTab", _make_tab("stock_candidates"))
    monkeypatch.setattr(classic_workspace_module, "LhbTab", _make_tab("lhb"))
    monkeypatch.setattr(classic_workspace_module, "ForeignBlockTradeTab", _make_tab("foreign_block"))
    monkeypatch.setattr(classic_workspace_module, "EarningsTab", _make_tab("earnings"))
    monkeypatch.setattr(classic_workspace_module, "FundHoldingsTab", _make_tab("fund_holdings"))
    monkeypatch.setattr(classic_workspace_module, "LogTab", _make_tab("system_log"))


def test_workspace_collects_a_share_quote_codes_from_public_tab_apis():
    workspace = _make_workspace(
        tabs={
            "scan": _make_quote_tab({"000001"}),
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
        "601318",
    }


def test_workspace_quote_universe_skips_information_source_group_and_non_a_share_tabs():
    specs = [
        {"key": "watchlist", "group": "主工作台"},
        {"key": "asian_market", "group": "主工作台"},
        {"key": "stock_candidates", "group": "主工作台"},
        {"key": "ai_industry_chain", "group": "情报源"},
        {"key": "lhb", "group": "主工作台"},
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
            "scan": _make_quote_tab({"000002"}),
            "foreign_block": _make_quote_tab({"600000"}),
            "earnings": _make_quote_tab({"300001"}),
            "fund_holdings": _make_quote_tab({"002594"}),
        }
    )
    workspace.tab_specs = lambda: list(specs)

    codes = ClassicWorkspace.get_realtime_quote_codes(workspace)

    assert codes == {"000001", "300750", "601318"}


def test_workspace_quote_universe_does_not_instantiate_lazy_tabs():
    loaded_tabs = {
        "watchlist": _make_quote_tab({"000001"}),
    }
    get_tab_calls = []
    workspace = SimpleNamespace(
        tab_specs=lambda: [
            {"key": "watchlist", "group": "主工作台"},
            {"key": "lhb", "group": "主工作台"},
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
                        "备注": "宁德液冷主线",
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
                        "买点": "触发",
                    }
                ]
            ),
        },
    )

    remark_data, na_subsector_data, block_data, earn_data, lhb_data, rps_bundle = (
        ClassicWorkspace.collect_watchlist_radar_data(workspace)
    )

    assert remark_data == {"300750": "宁德液冷主线"}
    assert na_subsector_data == {"300750": "液冷 / 储能链"}
    assert rps_bundle == {"cached": True}
    assert block_data["300750"]["text"] == "机构专用买入2709万"
    assert block_data["300750"]["amount_wan"] == 2709
    assert earn_data["300750"]["text"] == "一季度 32.5%"
    assert earn_data["300750"]["qoq_pct"] == 32.5
    assert lhb_data["300750"]["text"] == "04-20 | 净买1200万 | 机构净买800万 | 外资净卖150万"
    assert lhb_data["300750"]["net_wan"] == 1200
    assert lhb_data["300750"]["buy_point"] == "触发"


def test_workspace_collects_na_daily_context_from_cache_without_loading_lazy_tab(monkeypatch):
    get_tab_calls = []
    monkeypatch.setattr(
        StockContextService,
        "_load_na_daily_cache_rows",
        lambda self: [
            {
                "代码": "002415",
                "名称": "海康威视",
                "催化剂": "北美订单催化",
                "细分板块": "AI安防",
            }
        ],
    )
    workspace = SimpleNamespace(
        tab_specs=lambda: [{"key": "na_daily", "group": "info"}],
        get_loaded_tab=lambda key: None,
        get_tab=lambda key: get_tab_calls.append(key) or None,
        iter_tabs=lambda: [],
    )

    context = ClassicWorkspace.collect_stock_context(workspace)

    assert get_tab_calls == []
    assert [(signal.source_tab, signal.signal_type, signal.summary) for signal in context["002415"]] == [
        ("na_daily", "catalyst", "北美订单催化"),
        ("na_daily", "subsector", "AI安防"),
    ]


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
                        "公告日期": "2026-04-17",
                        "发现时间": "2026-04-20T08:31:02",
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
    assert context["600176"][0].observed_at == "2026-04-20T08:31:02"
    assert context["600176"][0].payload["发现时间"] == "2026-04-20T08:31:02"
    assert context["600176"][0].payload["业绩日"] == "2026-04-17"


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
                    },
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


def test_watchlist_radar_passes_target_codes_into_signal_collection(monkeypatch):
    workspace = SimpleNamespace(
        engine=None,
        tab_specs=lambda: [],
        get_loaded_tab=lambda _key: None,
        iter_tabs=lambda: [],
    )
    service = StockContextService(workspace)
    calls = []
    monkeypatch.setattr(service, "iter_stock_signals", lambda **kwargs: calls.append(kwargs) or [])

    service.collect_watchlist_radar_data(target_codes=["000001", "600000", "000001"])

    assert calls == [
        {
            "include_cache_fallback": False,
            "include_source_cache_fallback": None,
            "allow_lhb_cache_compute": False,
            "target_codes": {"000001", "600000"},
        }
    ]


def test_stock_context_explicit_empty_target_codes_skip_signal_sources(monkeypatch):
    workspace = SimpleNamespace(
        engine=None,
        tab_specs=lambda: [],
        get_loaded_tab=lambda _key: None,
        iter_tabs=lambda: [],
    )
    service = StockContextService(workspace)
    monkeypatch.setattr(
        service,
        "_iter_direct_stock_signals",
        lambda: (_ for _ in ()).throw(AssertionError("empty watchlist must not scan signal sources")),
    )

    assert service.iter_stock_signals(target_codes=[]) == []


def test_watchlist_radar_preserves_none_as_unfiltered_target(monkeypatch):
    workspace = SimpleNamespace(
        engine=None,
        tab_specs=lambda: [],
        get_loaded_tab=lambda _key: None,
        iter_tabs=lambda: [],
    )
    service = StockContextService(workspace)
    calls = []
    monkeypatch.setattr(service, "iter_stock_signals", lambda **kwargs: calls.append(kwargs) or [])

    service.collect_watchlist_radar_data(target_codes=None)

    assert calls[0]["target_codes"] is None


def test_stock_context_ai_chain_fallback_is_cache_only(monkeypatch):
    import core.ai_industry_chain_pool as ai_pool_module

    monkeypatch.setattr(
        ai_pool_module,
        "load_cached_ai_industry_chain_rows",
        lambda: [{stock_context_module.KEY_CODE: "300308", stock_context_module.KEY_SUBSECTOR: "optics"}],
    )
    monkeypatch.setattr(
        ai_pool_module,
        "load_ai_industry_chain_rows",
        lambda: (_ for _ in ()).throw(AssertionError("Watchlist fallback must not open workbook")),
    )
    workspace = SimpleNamespace(
        engine=None,
        tab_specs=lambda: [],
        get_loaded_tab=lambda _key: None,
        iter_tabs=lambda: [],
    )

    rows = StockContextService(workspace)._load_ai_chain_cache_rows()

    assert rows == [{stock_context_module.KEY_CODE: "300308", stock_context_module.KEY_SUBSECTOR: "optics"}]


def test_watchlist_radar_can_use_source_cache_without_scan_fallback(monkeypatch):
    import core.data_store as data_store_module

    def fail_datastore():
        raise AssertionError("watchlist radar should not block on scan cache")

    monkeypatch.setattr(data_store_module, "DataStore", fail_datastore)
    monkeypatch.setattr(
        StockContextService,
        "_load_ai_chain_cache_rows",
        lambda self: [{"代码": "300750", "细分板块": "液冷", "备注": "液冷主线"}],
    )
    workspace = SimpleNamespace(
        engine=None,
        tab_specs=lambda: [{"key": "scan", "group": "info"}, {"key": "ai_industry_chain", "group": "info"}],
        get_loaded_tab=lambda key: None,
        get_tab=lambda key: (_ for _ in ()).throw(AssertionError("lazy tab should not load")),
        iter_tabs=lambda: [],
    )

    remark_data, na_subsector_data, block_data, earn_data, lhb_data, rps_bundle = (
        ClassicWorkspace.collect_watchlist_radar_data(
            workspace,
            include_source_cache_fallback=True,
            target_codes={"300750"},
        )
    )

    assert remark_data == {"300750": "液冷主线"}
    assert na_subsector_data == {"300750": "液冷"}
    assert block_data == {}
    assert earn_data == {}
    assert lhb_data == {}
    assert rps_bundle is None


def test_workspace_collect_stock_context_schedules_lhb_snapshot_by_default(monkeypatch):
    scheduled = []
    monkeypatch.setattr(
        StockContextService,
        "_load_lhb_pool_rows",
        lambda self: (_ for _ in ()).throw(AssertionError("default stock context should not compute LHB pool inline")),
    )
    monkeypatch.setattr(StockContextService, "_lhb_pool_cache_signature", lambda self: ("cache", 1, 2))
    monkeypatch.setattr(
        StockContextService,
        "refresh_async_snapshots",
        lambda self, *, force=False: scheduled.append(force) or True,
    )
    workspace = SimpleNamespace(
        data_provider="provider",
        engine="engine",
        tab_specs=lambda: [{"key": "lhb", "group": "info"}],
        get_loaded_tab=lambda key: None,
        get_tab=lambda key: (_ for _ in ()).throw(AssertionError("lazy tab should not load")),
        iter_tabs=lambda: [],
    )

    context = ClassicWorkspace.collect_stock_context(workspace)

    assert context == {}
    assert scheduled == [False]


def test_workspace_collect_stock_context_uses_ready_lhb_snapshot_by_default():
    workspace = SimpleNamespace(
        engine="engine",
        tab_specs=lambda: [{"key": "lhb", "group": "info"}],
        get_loaded_tab=lambda key: None,
        get_tab=lambda key: (_ for _ in ()).throw(AssertionError("lazy tab should not load")),
        iter_tabs=lambda: [],
    )
    service = StockContextService(workspace)
    service._lhb_rows_signature = ("cache", 1, 2)
    service._lhb_rows_snapshot = [
        {
            stock_context_module.KEY_CODE: "300750",
            stock_context_module.KEY_NAME: "sample",
            stock_context_module.KEY_LAST_LISTED_RAW: "20260430",
            stock_context_module.KEY_NET_WAN: 1200,
            stock_context_module.KEY_INST_WAN: 800,
            stock_context_module.KEY_FOREIGN_WAN: 50,
        }
    ]
    service._lhb_pool_cache_signature = lambda: ("cache", 1, 2)
    workspace._workspace_facade = SimpleNamespace(collect_stock_context=service.collect_signals_by_code)

    context = ClassicWorkspace.collect_stock_context(workspace)

    signals = context["300750"]
    assert [(signal.source_tab, signal.signal_type) for signal in signals] == [("lhb", "lhb")]
    assert signals[0].numeric_value == 1200
    assert signals[0].observed_at == "20260430"


def test_workspace_skips_lhb_cache_fallback_while_loaded_lhb_tab_is_loading(monkeypatch):
    monkeypatch.setattr(
        StockContextService,
        "_load_lhb_pool_rows",
        lambda self: (_ for _ in ()).throw(AssertionError("loading LHB tab should own its cache read")),
    )
    lhb_tab = SimpleNamespace(get_row_data=lambda: [], _pool_load_in_progress=True)
    workspace = SimpleNamespace(
        tab_specs=lambda: [{"key": "lhb", "group": "info"}],
        get_loaded_tab=lambda key: lhb_tab if key == "lhb" else None,
        get_tab=lambda key: (_ for _ in ()).throw(AssertionError("lazy tab should not load")),
        iter_tabs=lambda: [],
    )

    context = ClassicWorkspace.collect_stock_context(workspace)

    assert context == {}


def test_stock_context_service_reuses_lhb_fallback_cache_for_same_signature(monkeypatch):
    import core.lhb_pool_manager as lhb_pool_module

    calls = []

    class _FakePoolManager:
        def compute_pool(self, *, data_provider=None, engine=None):
            calls.append((data_provider, engine))
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
    workspace = SimpleNamespace(engine="engine")
    service = StockContextService(workspace)
    monkeypatch.setattr(service, "_lhb_pool_cache_signature", lambda: ("cache", 1, 2))

    first = service._load_lhb_pool_rows()
    first[0][stock_context_module.KEY_CODE] = "MUTATED"
    second = service._load_lhb_pool_rows()

    assert calls == [(None, "engine")]
    assert second[0][stock_context_module.KEY_CODE] == "300750"


def test_watchlist_radar_schedules_lhb_snapshot_instead_of_blocking_cache_compute(monkeypatch):
    scheduled = []
    monkeypatch.setattr(
        StockContextService,
        "_load_lhb_pool_rows",
        lambda self: (_ for _ in ()).throw(AssertionError("LHB cache compute should stay off the UI collect path")),
    )
    monkeypatch.setattr(StockContextService, "_lhb_pool_cache_signature", lambda self: ("cache", 1, 2))
    monkeypatch.setattr(
        StockContextService,
        "refresh_async_snapshots",
        lambda self, *, force=False: scheduled.append(force) or True,
    )
    workspace = SimpleNamespace(
        engine=None,
        tab_specs=lambda: [{"key": "lhb", "group": "info"}],
        get_loaded_tab=lambda key: None,
        get_tab=lambda key: (_ for _ in ()).throw(AssertionError("lazy tab should not load")),
        iter_tabs=lambda: [],
    )

    *_, lhb_data, _ = ClassicWorkspace.collect_watchlist_radar_data(
        workspace,
        include_source_cache_fallback=True,
        allow_lhb_cache_compute=False,
    )

    assert lhb_data == {}
    assert scheduled == [False]


def test_workspace_collect_stock_context_schedules_lhb_snapshot_without_blocking(monkeypatch):
    scheduled = []
    monkeypatch.setattr(
        StockContextService,
        "_load_lhb_pool_rows",
        lambda self: (_ for _ in ()).throw(AssertionError("LHB cache compute should stay off stock context refresh")),
    )
    monkeypatch.setattr(StockContextService, "_lhb_pool_cache_signature", lambda self: ("cache", 1, 2))
    monkeypatch.setattr(
        StockContextService,
        "refresh_async_snapshots",
        lambda self, *, force=False: scheduled.append(force) or True,
    )
    workspace = SimpleNamespace(
        engine=None,
        tab_specs=lambda: [{"key": "lhb", "group": "info"}],
        get_loaded_tab=lambda key: None,
        get_tab=lambda key: (_ for _ in ()).throw(AssertionError("lazy tab should not load")),
        iter_tabs=lambda: [],
    )

    context = ClassicWorkspace.collect_stock_context(workspace, allow_lhb_cache_compute=False)

    assert context == {}
    assert scheduled == [False]


def test_workspace_collect_stock_context_can_suppress_async_snapshot_refresh(monkeypatch):
    scheduled = []
    monkeypatch.setattr(
        StockContextService,
        "_load_lhb_pool_rows",
        lambda self: (_ for _ in ()).throw(AssertionError("LHB cache compute should stay off stock context refresh")),
    )
    monkeypatch.setattr(StockContextService, "_lhb_pool_cache_signature", lambda self: ("cache", 1, 2))
    monkeypatch.setattr(
        StockContextService,
        "refresh_async_snapshots",
        lambda self, *, force=False: scheduled.append(force) or True,
    )
    workspace = SimpleNamespace(
        engine=None,
        tab_specs=lambda: [
            {"key": "fund_holdings", "group": "info"},
            {"key": "lhb", "group": "info"},
        ],
        get_loaded_tab=lambda key: None,
        get_tab=lambda key: (_ for _ in ()).throw(AssertionError("lazy tab should not load")),
        iter_tabs=lambda: [],
    )

    context = ClassicWorkspace.collect_stock_context(
        workspace,
        allow_lhb_cache_compute=False,
        allow_async_snapshot_refresh=False,
    )

    assert context == {}
    assert scheduled == []


def test_stock_context_snapshot_refresh_can_skip_lhb_cache_compute(monkeypatch):
    monkeypatch.setattr(
        StockContextService,
        "_load_lhb_pool_rows",
        lambda self: (_ for _ in ()).throw(AssertionError("LHB snapshot should be skipped")),
    )
    workspace = SimpleNamespace(engine=None)
    service = StockContextService(workspace)
    service._fund_rows_loaded = True
    monkeypatch.setattr(service, "_lhb_pool_cache_signature", lambda: ("cache", 1, 2))

    assert service.refresh_async_snapshots(include_lhb=False) is False


def test_stock_context_snapshot_refresh_defers_during_post_f5_window(monkeypatch):
    monkeypatch.setattr(stock_context_module.time, "monotonic", lambda: 100.0)
    workspace = SimpleNamespace(engine=None)
    service = StockContextService(workspace)
    service.prepare_post_f5_refresh()

    assert service.refresh_async_snapshots(force=True) is False


def test_workspace_fund_holding_update_primes_only_fund_snapshot():
    calls = []
    workspace = SimpleNamespace(
        prime_stock_context_snapshots=lambda **kwargs: calls.append(dict(kwargs)) or True,
    )

    ClassicWorkspace._on_fund_holdings_source_updated(workspace)

    assert calls == [{"force": True, "include_lhb": False}]


def test_workspace_collects_fund_holding_context_from_snapshot_without_open_tab(monkeypatch):
    monkeypatch.setattr(
        StockContextService,
        "_cached_fund_holding_rows",
        lambda self, *, allow_async_refresh=True: [
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
    load_attempts = []
    workspace.get_loaded_tab = lambda key: None
    workspace.get_tab = lambda key: load_attempts.append(key) or None

    context = ClassicWorkspace.collect_stock_context(workspace)

    signals = context["300750"]
    assert load_attempts == []
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
                    {"代码": "000001", "细分板块": "非关注池分类"},
                ]
            ),
            "ai_industry_chain": _make_rows_tab(
                [
                    {"代码": "300750", "细分板块": "液冷 / 储能链", "备注": "液冷主线"},
                    {"代码": "300750", "细分板块": "后续重复分类", "备注": "补充备注"},
                    {"代码": "300750", "细分板块": "后续重复分类", "备注": "补充备注"},
                    {"代码": "688498", "细分板块": "光芯片"},
                ]
            ),
        },
    )

    remark_data, na_subsector_data, *_ = ClassicWorkspace.collect_watchlist_radar_data(
        workspace,
        target_codes={"300750", "002415"},
    )

    assert remark_data == {"300750": "液冷主线 / 补充备注"}
    assert na_subsector_data == {
        "300750": "液冷 / 储能链 / 后续重复分类",
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
                        "买点": "触发",
                    }
                ]
            ),
        },
    )

    *_, lhb_data, _ = ClassicWorkspace.collect_watchlist_radar_data(workspace)

    assert lhb_data["300750"]["text"] == "04-20 | 净买1200万 | 机构净买800万 | 外资净卖150万"
    assert lhb_data["300750"]["date"] == "20260420"
    assert lhb_data["300750"]["buy_point"] == "触发"


def test_workspace_refreshes_all_tabs_after_f5():
    calls = []
    tabs = {
        "watchlist": SimpleNamespace(refresh_table_from_latest_snapshot=lambda: calls.append("watchlist")),
        "lhb": SimpleNamespace(refresh_table_from_latest_snapshot=lambda: calls.append("lhb")),
        "na_daily": SimpleNamespace(refresh_table_from_latest_snapshot=lambda: calls.append("na_daily")),
        "ai_industry_chain": SimpleNamespace(refresh_table_from_latest_snapshot=lambda: calls.append("ai_chain")),
        "asian_market": SimpleNamespace(refresh_table_from_latest_snapshot=lambda: calls.append("asian")),
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
        "foreign",
        "fund",
        "earnings",
        "scan",
    ]


def test_workspace_prepares_tabs_before_f5_snapshot_refresh():
    calls = []
    tab = SimpleNamespace(
        prepare_post_f5_refresh=lambda: calls.append("prepare"),
        refresh_table_from_latest_snapshot=lambda: calls.append("refresh"),
    )
    workspace = _make_workspace(tabs={"lhb": tab})
    workspace.iter_refreshable_tabs = lambda: ClassicWorkspace.iter_refreshable_tabs(workspace)

    ClassicWorkspace.refresh_all_tabs_after_f5(workspace)

    assert calls == ["prepare", "refresh"]


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

    assert (
        ClassicWorkspace.refresh_all_tabs_after_f5_scheduled(
            workspace,
            on_finished=lambda: done.append("done"),
            interval_ms=0,
        )
        is True
    )

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

    assert (
        ClassicWorkspace.refresh_all_tabs_after_f5_scheduled(
            workspace,
            interval_ms=0,
            skip_cache_reload_tabs=True,
        )
        is True
    )

    for _ in range(10):
        app.processEvents()
        if getattr(workspace, "_f5_refresh_scheduler", None) is None:
            break

    assert calls == ["watchlist"]


def test_workspace_scheduled_f5_skips_post_f5_data_refresh_tabs():
    app = QApplication.instance() or QApplication([])
    calls = []

    class PostF5Tab:
        def refresh_table_from_latest_snapshot(self):
            calls.append("post_f5_snapshot")

        def refresh_data_after_f5(self):
            calls.append("post_f5_data")
            return True

    tabs = {
        "watchlist": SimpleNamespace(refresh_table_from_latest_snapshot=lambda: calls.append("watchlist")),
        "ai_industry_chain": PostF5Tab(),
    }
    workspace = _make_workspace(tabs=tabs)
    workspace.iter_refreshable_tabs = lambda: ClassicWorkspace.iter_refreshable_tabs(workspace)

    assert (
        ClassicWorkspace.refresh_all_tabs_after_f5_scheduled(
            workspace,
            interval_ms=0,
            skip_cache_reload_tabs=True,
        )
        is True
    )

    for _ in range(10):
        app.processEvents()
        if getattr(workspace, "_f5_refresh_scheduler", None) is None:
            break

    assert calls == ["watchlist"]


def test_workspace_refreshes_ai_chain_dependent_tabs_after_update():
    calls = []
    tabs = {
        "watchlist": SimpleNamespace(workspace_key="watchlist"),
        "lhb": SimpleNamespace(
            workspace_key="lhb",
            refresh_data_after_ai_industry_chain_update=lambda: calls.append("lhb") or True,
        ),
        "stock_candidates": SimpleNamespace(workspace_key="stock_candidates"),
        "foreign_block": SimpleNamespace(
            workspace_key="foreign_block",
            refresh_data_after_ai_industry_chain_update=lambda: calls.append("foreign") or True,
        ),
        "fund_holdings": SimpleNamespace(
            workspace_key="fund_holdings",
            refresh_data_after_ai_industry_chain_update=lambda: calls.append("fund") or True,
        ),
        "earnings": SimpleNamespace(
            workspace_key="earnings",
            refresh_data_after_ai_industry_chain_update=lambda: calls.append("earnings") or True,
        ),
    }
    workspace = _make_workspace(tabs=tabs)

    results = ClassicWorkspace.refresh_tabs_after_ai_industry_chain_update(workspace)

    assert calls == ["lhb", "foreign", "fund", "earnings"]
    assert results == {
        "lhb": True,
        "foreign_block": True,
        "fund_holdings": True,
        "earnings": True,
    }


def test_workspace_f5_snapshot_refresh_uses_async_local_snapshot():
    calls = []

    class SyncAwareTab:
        def refresh_table_from_latest_snapshot(self, *, async_local=True):
            calls.append(async_local)

    workspace = _make_workspace(tabs={"asian_market": SyncAwareTab()})
    workspace.iter_refreshable_tabs = lambda: ClassicWorkspace.iter_refreshable_tabs(workspace)

    ClassicWorkspace.refresh_all_tabs_after_f5(workspace)

    assert calls == [True]


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
            "fund_holdings": SimpleNamespace(run_auto_sync_after_f5=lambda: calls.append("fund") or True),
        }
    )

    assert ClassicWorkspace.run_fund_holdings_auto_sync_after_f5(workspace) is True
    assert calls == ["fund"]


def test_workspace_post_online_refresh_skips_heavy_foreign_block_startup():
    calls = []
    workspace = _make_workspace(
        tabs={
            "na_daily": SimpleNamespace(run_post_online_refresh=lambda: calls.append("na_daily") or True),
            "foreign_block": SimpleNamespace(run_post_online_refresh=lambda: calls.append("foreign") or True),
        }
    )

    ClassicWorkspace.run_post_online_refresh(workspace, task_manager=None)

    assert calls == ["na_daily"]


def test_workspace_refreshes_information_sources_after_f5():
    calls = []
    specs = [
        {"key": "watchlist", "group": "主工作台"},
        {"key": "scan", "group": "情报源"},
        {"key": "ai_industry_chain", "group": "情报源"},
        {"key": "foreign_block", "group": "情报源"},
        {"key": "earnings", "group": "情报源"},
        {"key": "fund_holdings", "group": "情报源"},
        {"key": "system_log", "group": "系统"},
    ]
    workspace = _make_workspace(
        tabs={
            "watchlist": SimpleNamespace(refresh_data_after_f5=lambda: calls.append("watchlist")),
            "scan": SimpleNamespace(refresh_data_after_f5=lambda: calls.append("scan") or True),
            "ai_industry_chain": SimpleNamespace(refresh_data_after_f5=lambda: calls.append("ai") or True),
            "foreign_block": SimpleNamespace(refresh_data_after_f5=lambda: calls.append("foreign") or True),
            "earnings": SimpleNamespace(refresh_data_after_f5=lambda: calls.append("earnings") or True),
            "fund_holdings": SimpleNamespace(refresh_data_after_f5=lambda: calls.append("fund") or True),
            "system_log": SimpleNamespace(refresh_data_after_f5=lambda: calls.append("log")),
        }
    )
    workspace.tab_specs = lambda: list(specs)

    results = ClassicWorkspace.refresh_information_sources_after_f5(workspace)

    assert calls == ["scan", "ai", "foreign", "earnings", "fund"]
    assert results == {
        "scan": True,
        "ai_industry_chain": True,
        "foreign_block": True,
        "earnings": True,
        "fund_holdings": True,
    }


def test_workspace_schedules_information_sources_after_f5():
    app = QApplication.instance() or QApplication([])
    calls = []
    specs = [
        {"key": "scan", "group": INFO_SOURCE_GROUP},
        {"key": "earnings", "group": INFO_SOURCE_GROUP},
        {"key": "fund_holdings", "group": INFO_SOURCE_GROUP},
    ]
    workspace = _make_workspace(
        tabs={
            "scan": SimpleNamespace(refresh_data_after_f5=lambda: calls.append("scan") or True),
            "earnings": SimpleNamespace(refresh_data_after_f5=lambda: calls.append("earnings") or True),
            "fund_holdings": SimpleNamespace(refresh_data_after_f5=lambda: calls.append("fund") or True),
        }
    )
    workspace.tab_specs = lambda: list(specs)

    assert ClassicWorkspace.refresh_information_sources_after_f5_scheduled(workspace, interval_ms=0) is True
    assert calls == []

    for _ in range(10):
        app.processEvents()
        if getattr(workspace, "_f5_information_source_scheduler", None) is None:
            break

    assert calls == ["scan", "earnings", "fund"]


def test_workspace_prepares_information_sources_before_scheduled_f5_refresh():
    app = QApplication.instance() or QApplication([])
    calls = []
    workspace = _make_workspace(
        tabs={
            "foreign_block": SimpleNamespace(
                prepare_post_f5_refresh=lambda: calls.append("prepare"),
                refresh_data_after_f5=lambda: calls.append("refresh") or True,
            ),
        }
    )
    workspace.tab_specs = lambda: [{"key": "foreign_block", "group": INFO_SOURCE_GROUP}]

    assert ClassicWorkspace.refresh_information_sources_after_f5_scheduled(workspace, interval_ms=0) is True
    assert calls == ["prepare"]

    for _ in range(10):
        app.processEvents()
        if getattr(workspace, "_f5_information_source_scheduler", None) is None:
            break

    assert calls == ["prepare", "refresh"]


def test_workspace_refreshes_information_sources_after_f5_skips_unloaded_scan():
    calls = []
    scan_tab = SimpleNamespace(refresh_data_after_f5=lambda: calls.append("scan") or True)
    workspace = SimpleNamespace(
        tab_specs=lambda: [{"key": "scan", "group": INFO_SOURCE_GROUP}],
        get_loaded_tab=lambda key: None,
        ensure_tab_loaded=lambda key, reason="user": calls.append(("ensure", key, reason)) or scan_tab,
    )

    results = ClassicWorkspace.refresh_information_sources_after_f5(workspace)

    assert calls == []
    assert results == {}


def test_workspace_refreshes_information_sources_after_f5_skips_noninteractive_tabs():
    calls = []
    specs = [
        {"key": "scan", "group": "情报源"},
        {"key": "foreign_block", "group": "情报源"},
        {"key": "earnings", "group": "情报源"},
        {"key": "fund_holdings", "group": "情报源"},
    ]
    workspace = _make_workspace(
        tabs={
            "scan": SimpleNamespace(refresh_data_after_f5=lambda: calls.append("scan") or True),
            "foreign_block": SimpleNamespace(
                _workspace_noninteractive_loaded=True,
                refresh_data_after_f5=lambda: calls.append("foreign") or True,
            ),
            "earnings": SimpleNamespace(
                _workspace_load_reason="perf_memory_probe",
                refresh_data_after_f5=lambda: calls.append("earnings") or True,
            ),
            "fund_holdings": SimpleNamespace(
                _workspace_noninteractive_loaded=True,
                refresh_data_after_f5=lambda: calls.append("fund") or True,
            ),
        }
    )
    workspace.tab_specs = lambda: list(specs)

    results = ClassicWorkspace.refresh_information_sources_after_f5(workspace)

    assert calls == ["scan"]
    assert results == {"scan": True}


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
    monkeypatch.setattr(classic_workspace_module, "ScanTab", _make_tab("scan"))
    monkeypatch.setattr(classic_workspace_module, "StockCandidateTab", _make_tab("stock_candidates"))
    monkeypatch.setattr(classic_workspace_module, "LhbTab", _make_tab("lhb"))
    monkeypatch.setattr(classic_workspace_module, "ForeignBlockTradeTab", _make_tab("foreign_block"))
    monkeypatch.setattr(classic_workspace_module, "EarningsTab", _make_tab("earnings"))
    monkeypatch.setattr(classic_workspace_module, "FundHoldingsTab", _make_tab("fund_holdings"))
    monkeypatch.setattr(classic_workspace_module, "LogTab", _make_tab("system_log"))

    workspace = classic_workspace_module.ClassicWorkspace(data_provider=object(), engine=object())
    try:
        assert ctor_kwargs == {}
        assert workspace.get_loaded_tab("watchlist") is None
        assert workspace.get_loaded_tab("lhb") is None
        assert workspace.get_loaded_tab("fund_holdings") is None

        workspace.ensure_tab_loaded("watchlist")
        workspace.ensure_tab_loaded("lhb")
        assert ctor_kwargs["lhb"]["autoload_pool"] is False
        workspace.ensure_tab_loaded("fund_holdings")
        assert ctor_kwargs["fund_holdings"]["autoload"] is False
        groups = {spec["key"]: spec["group"] for spec in workspace.tab_specs()}
        tab_keys = [spec["key"] for spec in workspace.tab_specs()]
        assert groups["lhb"] == "主工作台"
        assert groups["ai_industry_chain"] == "情报源"
        assert groups["stock_candidates"] == "主工作台"
        assert groups["scan"] == "情报源"
        assert tab_keys.index("watchlist") < tab_keys.index("lhb") < tab_keys.index("asian_market")
        assert tab_keys.index("na_daily") < tab_keys.index("stock_candidates")
        info_keys = [tab_keys[index] for index in workspace.tab_indices_by_group()["情报源"]]
        assert info_keys == ["scan", "ai_industry_chain", "foreign_block", "earnings", "fund_holdings"]
        assert "autoload_pool" not in ctor_kwargs["watchlist"]
        assert "autoload" not in ctor_kwargs["watchlist"]
        assert isinstance(workspace.tabs, SmoothTabWidget)
        assert workspace.tabs._transition_enabled is True
        assert ("lhb", "asian_market") in workspace.tabs._snapshot_transition_skip_pairs
    finally:
        workspace.shutdown()
        workspace.deleteLater()


def test_workspace_delays_watchlist_indicator_refresh_on_tab_switch(monkeypatch):
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
    monkeypatch.setattr(classic_workspace_module, "ScanTab", _make_tab("scan"))
    monkeypatch.setattr(classic_workspace_module, "StockCandidateTab", _make_tab("stock_candidates"))
    monkeypatch.setattr(classic_workspace_module, "LhbTab", _make_tab("lhb"))
    monkeypatch.setattr(classic_workspace_module, "ForeignBlockTradeTab", _make_tab("foreign_block"))
    monkeypatch.setattr(classic_workspace_module, "EarningsTab", _make_tab("earnings"))
    monkeypatch.setattr(classic_workspace_module, "FundHoldingsTab", _make_tab("fund_holdings"))
    monkeypatch.setattr(classic_workspace_module, "LogTab", _make_tab("system_log"))

    workspace = classic_workspace_module.ClassicWorkspace(
        data_provider=object(),
        engine=object(),
        background_prewarm=False,
    )
    try:
        workspace.ensure_tab_loaded("watchlist", reason="tab_switch")

        assert ctor_kwargs["watchlist"]["startup_indicator_refresh_delay_ms"] == (
            classic_workspace_module.ClassicWorkspace.WATCHLIST_TAB_SWITCH_INDICATOR_DELAY_MS
        )
        assert ctor_kwargs["watchlist"]["startup_followup_refresh_enabled"] is False

        workspace.ensure_tab_loaded("lhb", reason="tab_switch")
        workspace.ensure_tab_loaded("scan", reason="tab_switch")
        workspace.ensure_tab_loaded("ai_industry_chain", reason="tab_switch")
        workspace.ensure_tab_loaded("foreign_block", reason="tab_switch")
        workspace.ensure_tab_loaded("fund_holdings", reason="tab_switch")
        workspace.ensure_tab_loaded("asian_market", reason="tab_switch")
        workspace.ensure_tab_loaded("na_daily", reason="tab_switch")
        workspace.ensure_tab_loaded("stock_candidates", reason="tab_switch")
        workspace.ensure_tab_loaded("earnings", reason="tab_switch")

        delay = classic_workspace_module.ClassicWorkspace.FIRST_VISIBLE_TAB_WORK_DELAY_MS
        assert ctor_kwargs["lhb"]["initial_load_delay_ms"] == (
            classic_workspace_module.ClassicWorkspace.LHB_FIRST_VISIBLE_POOL_DELAY_MS
        )
        assert ctor_kwargs["fund_holdings"]["initial_load_delay_ms"] == delay
        assert ctor_kwargs["scan"]["initial_cache_load_delay_ms"] == delay
        assert ctor_kwargs["foreign_block"]["initial_cache_load_delay_ms"] == delay
        assert ctor_kwargs["asian_market"]["local_cache_delay_ms"] == delay
        assert ctor_kwargs["ai_industry_chain"]["runtime_start_delay_ms"] == delay
        assert ctor_kwargs["na_daily"]["runtime_start_delay_ms"] == delay
        assert ctor_kwargs["stock_candidates"]["runtime_start_delay_ms"] == delay
        assert ctor_kwargs["earnings"]["runtime_start_delay_ms"] == delay
    finally:
        workspace.shutdown()
        workspace.deleteLater()


def test_workspace_startup_guard_blocks_raw_tab_switch_sweep(monkeypatch, qt_application):
    constructed = []
    _patch_lightweight_workspace_tabs(monkeypatch, constructed)

    workspace = classic_workspace_module.ClassicWorkspace(
        data_provider=object(),
        engine=object(),
        background_prewarm=False,
    )
    try:
        tab_keys = [spec["key"] for spec in workspace.tab_specs()]
        lhb_index = tab_keys.index("lhb")
        ai_index = tab_keys.index("ai_industry_chain")

        workspace.restore_last_tab(lhb_index)
        _drain_qt_events(qt_application)

        assert constructed == ["lhb"]
        assert workspace.get_loaded_tab("lhb") is not None

        workspace.tabs.setCurrentIndex(ai_index)
        _drain_qt_events(qt_application)

        assert constructed == ["lhb"]
        assert workspace.get_loaded_tab("ai_industry_chain") is None
        assert workspace.tabs.currentIndex() == lhb_index
        assert "ai_industry_chain" in workspace._startup_suppressed_tab_switch_keys
    finally:
        workspace.shutdown()
        workspace.deleteLater()


def test_workspace_shell_activation_bypasses_startup_raw_tab_switch_guard(monkeypatch, qt_application):
    constructed = []
    ctor_kwargs = {}
    _patch_lightweight_workspace_tabs(monkeypatch, constructed, ctor_kwargs)

    workspace = classic_workspace_module.ClassicWorkspace(
        data_provider=object(),
        engine=object(),
        background_prewarm=False,
    )
    try:
        tab_keys = [spec["key"] for spec in workspace.tab_specs()]
        lhb_index = tab_keys.index("lhb")
        scan_index = tab_keys.index("scan")

        workspace.restore_last_tab(lhb_index)
        _drain_qt_events(qt_application)
        workspace.activate_tab(scan_index, reason="shell_nav")
        _drain_qt_events(qt_application)

        assert constructed == ["lhb", "scan"]
        assert workspace.get_loaded_tab("scan") is not None
        assert ctor_kwargs["scan"]["initial_cache_load_delay_ms"] == (
            classic_workspace_module.ClassicWorkspace.FIRST_VISIBLE_TAB_WORK_DELAY_MS
        )
        assert "scan" not in workspace._startup_suppressed_tab_switch_keys
    finally:
        workspace.shutdown()
        workspace.deleteLater()


def test_workspace_shell_group_rebuild_defers_lazy_load_without_changing_tab_delays(monkeypatch, qt_application):
    constructed = []
    ctor_kwargs = {}
    _patch_lightweight_workspace_tabs(monkeypatch, constructed, ctor_kwargs)

    workspace = classic_workspace_module.ClassicWorkspace(
        data_provider=object(),
        engine=object(),
        background_prewarm=False,
    )
    try:
        tab_keys = [spec["key"] for spec in workspace.tab_specs()]
        fund_index = tab_keys.index("fund_holdings")
        scheduled = []
        monkeypatch.setattr(
            classic_workspace_module.QTimer,
            "singleShot",
            lambda delay, callback: scheduled.append((delay, callback)),
        )

        workspace.prepare_shell_group_rebuild_navigation(interval_ms=5000)
        workspace.activate_tab(fund_index, reason="shell_nav")

        assert constructed == []
        assert scheduled[0][0] == classic_workspace_module.ClassicWorkspace.SHELL_GROUP_REBUILD_LOAD_DELAY_MS

        scheduled.pop(0)[1]()

        assert constructed == ["fund_holdings"]
        assert ctor_kwargs["fund_holdings"]["initial_load_delay_ms"] == (
            classic_workspace_module.ClassicWorkspace.FIRST_VISIBLE_TAB_WORK_DELAY_MS
        )
    finally:
        workspace.shutdown()
        workspace.deleteLater()


def test_workspace_shell_group_rebuild_delays_activation_callback_and_skips_stale_tab(
    monkeypatch,
    qt_application,
):
    activated = []

    class _WatchlistTab(QWidget):
        def __init__(self, *args, **kwargs):
            super().__init__()

    class _LhbTab(QWidget):
        def __init__(self, *args, **kwargs):
            super().__init__()

        def on_workspace_tab_activated(self):
            activated.append("lhb")

    monkeypatch.setattr(classic_workspace_module, "WatchlistTab", _WatchlistTab)
    monkeypatch.setattr(classic_workspace_module, "LhbTab", _LhbTab)

    workspace = classic_workspace_module.ClassicWorkspace(
        data_provider=object(),
        engine=object(),
        background_prewarm=False,
    )
    try:
        widget = workspace.ensure_tab_loaded("lhb", reason="background_prewarm")
        lhb_index = next(index for index, spec in enumerate(workspace.tab_specs()) if spec.get("key") == "lhb")
        scheduled = []
        monkeypatch.setattr(
            classic_workspace_module.QTimer,
            "singleShot",
            lambda delay, callback: scheduled.append((delay, callback)),
        )

        workspace.prepare_shell_group_rebuild_navigation(interval_ms=5000)
        workspace.tabs.setCurrentIndex(lhb_index)

        assert workspace.tabs.currentWidget() is widget
        assert scheduled[0][0] == classic_workspace_module.ClassicWorkspace.SHELL_GROUP_REBUILD_ACTIVATION_DELAY_MS

        workspace.tabs.setCurrentIndex(0)
        scheduled[0][1]()

        assert activated == []
    finally:
        workspace.shutdown()
        workspace.deleteLater()


def test_workspace_marks_system_log_shell_nav_load_for_f5_grace(monkeypatch, qt_application):
    constructed = []
    _patch_lightweight_workspace_tabs(monkeypatch, constructed)
    clock = {"now": 321.5}
    monkeypatch.setattr(classic_workspace_module.time, "perf_counter", lambda: clock["now"])

    workspace = classic_workspace_module.ClassicWorkspace(
        data_provider=object(),
        engine=object(),
        background_prewarm=False,
    )
    try:
        tab_keys = [spec["key"] for spec in workspace.tab_specs()]
        system_log_index = tab_keys.index("system_log")

        workspace.activate_tab(system_log_index, reason="shell_nav")
        _drain_qt_events(qt_application)

        assert constructed == ["system_log"]
        assert workspace.get_loaded_tab("system_log") is not None
        assert workspace._last_system_log_shell_nav_load_at == 321.5

        clock["now"] = 333.0
        workspace.activate_tab(system_log_index, reason="shell_nav")

        assert workspace._last_system_log_shell_nav_load_at == 333.0
    finally:
        workspace.shutdown()
        workspace.deleteLater()


def test_workspace_disables_foreign_block_autoload_for_noninteractive_probe(monkeypatch):
    ctor_kwargs = {}

    class _ForeignBlockTab(QWidget):
        def __init__(self, *args, **kwargs):
            super().__init__()
            ctor_kwargs["foreign_block"] = dict(kwargs)

    monkeypatch.setattr(classic_workspace_module, "ForeignBlockTradeTab", _ForeignBlockTab)

    workspace = classic_workspace_module.ClassicWorkspace(
        data_provider=object(),
        engine=object(),
        background_prewarm=False,
    )
    try:
        widget = workspace.ensure_tab_loaded("foreign_block", reason="perf_memory_probe")

        assert widget is workspace.get_loaded_tab("foreign_block")
        assert ctor_kwargs["foreign_block"]["autoload"] is False
        assert getattr(widget, "_workspace_noninteractive_loaded") is True
    finally:
        workspace.shutdown()
        workspace.deleteLater()


def test_workspace_keeps_foreign_block_autoload_for_interactive_entry(monkeypatch):
    ctor_kwargs = {}

    class _ForeignBlockTab(QWidget):
        def __init__(self, *args, **kwargs):
            super().__init__()
            ctor_kwargs["foreign_block"] = dict(kwargs)

    monkeypatch.setattr(classic_workspace_module, "ForeignBlockTradeTab", _ForeignBlockTab)

    workspace = classic_workspace_module.ClassicWorkspace(
        data_provider=object(),
        engine=object(),
        background_prewarm=False,
    )
    try:
        widget = workspace.ensure_tab_loaded("foreign_block", reason="tab_switch")

        assert widget is workspace.get_loaded_tab("foreign_block")
        assert "autoload" not in ctor_kwargs["foreign_block"]
        assert getattr(widget, "_workspace_noninteractive_loaded") is False
    finally:
        workspace.shutdown()
        workspace.deleteLater()


def test_workspace_defers_heavy_probe_loads_during_controlled_startup():
    workspace = classic_workspace_module.ClassicWorkspace(
        data_provider=object(),
        engine=object(),
        background_prewarm=False,
        controlled_startup_probe_guard=True,
    )
    try:
        assert workspace.should_defer_probe_tab_load("watchlist", reason="perf_memory_probe") is True
        assert workspace.should_defer_probe_tab_load("lhb", reason="perf_memory_probe") is True
        assert workspace.should_defer_probe_tab_load("fund_holdings", reason="perf_memory_probe") is True
        assert workspace.should_defer_probe_tab_load("fund_holdings", reason="tab_switch") is False
        assert workspace.should_defer_probe_tab_load("system_log", reason="perf_memory_probe") is False
    finally:
        workspace.shutdown()
        workspace.deleteLater()


def test_workspace_allows_probe_loads_without_controlled_startup_guard():
    workspace = classic_workspace_module.ClassicWorkspace(
        data_provider=object(),
        engine=object(),
        background_prewarm=False,
    )
    try:
        assert workspace.should_defer_probe_tab_load("fund_holdings", reason="perf_memory_probe") is False
    finally:
        workspace.shutdown()
        workspace.deleteLater()


def test_workspace_background_prewarm_primes_context_without_forcing_current_tab(monkeypatch):
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
    monkeypatch.setattr(classic_workspace_module, "ScanTab", _make_tab("scan"))
    monkeypatch.setattr(classic_workspace_module, "StockCandidateTab", _make_tab("stock_candidates"))
    monkeypatch.setattr(classic_workspace_module, "LhbTab", _make_tab("lhb"))
    monkeypatch.setattr(classic_workspace_module, "ForeignBlockTradeTab", _make_tab("foreign_block"))
    monkeypatch.setattr(classic_workspace_module, "EarningsTab", _make_tab("earnings"))
    monkeypatch.setattr(classic_workspace_module, "FundHoldingsTab", _make_tab("fund_holdings"))
    monkeypatch.setattr(classic_workspace_module, "LogTab", _make_tab("system_log"))

    workspace = classic_workspace_module.ClassicWorkspace(data_provider=object(), engine=object())
    try:
        assert constructed == []

        workspace.schedule_restore_last_tab(10, delay_ms=999_999)
        snapshot_primes = []
        workspace.prime_stock_context_snapshots = lambda **kwargs: snapshot_primes.append(kwargs) or True
        monkeypatch.setattr(classic_workspace_module.QTimer, "singleShot", lambda _delay, callback: callback())

        workspace._start_background_tab_prewarm()

        assert constructed == []
        assert primed == []
        assert ctor_kwargs == {}
        assert snapshot_primes == [{"include_lhb": False}]
    finally:
        workspace.shutdown()
        workspace.deleteLater()


def test_workspace_background_prewarm_can_preload_whitelisted_current_tab(monkeypatch):
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

    monkeypatch.setattr(
        classic_workspace_module.ClassicWorkspace,
        "BACKGROUND_PREWARM_KEYS",
        frozenset({"watchlist"}),
    )
    monkeypatch.setattr(classic_workspace_module, "WatchlistTab", _make_tab("watchlist"))
    monkeypatch.setattr(classic_workspace_module, "AsianMarketTab", _make_tab("asian_market"))
    monkeypatch.setattr(classic_workspace_module, "NADailyTab", _make_tab("na_daily"))
    monkeypatch.setattr(classic_workspace_module, "AIIndustryChainTab", _make_tab("ai_industry_chain"))
    monkeypatch.setattr(classic_workspace_module, "ScanTab", _make_tab("scan"))
    monkeypatch.setattr(classic_workspace_module, "StockCandidateTab", _make_tab("stock_candidates"))
    monkeypatch.setattr(classic_workspace_module, "LhbTab", _make_tab("lhb"))
    monkeypatch.setattr(classic_workspace_module, "ForeignBlockTradeTab", _make_tab("foreign_block"))
    monkeypatch.setattr(classic_workspace_module, "EarningsTab", _make_tab("earnings"))
    monkeypatch.setattr(classic_workspace_module, "FundHoldingsTab", _make_tab("fund_holdings"))
    monkeypatch.setattr(classic_workspace_module, "LogTab", _make_tab("system_log"))

    workspace = classic_workspace_module.ClassicWorkspace(data_provider=object(), engine=object())
    try:
        workspace.schedule_restore_last_tab(10, delay_ms=999_999)
        workspace.prime_stock_context_snapshots = lambda **kwargs: True
        monkeypatch.setattr(classic_workspace_module.QTimer, "singleShot", lambda _delay, callback: callback())

        workspace._start_background_tab_prewarm()

        assert constructed == ["watchlist"]
        assert primed == ["watchlist"]
        assert set(ctor_kwargs) == {"watchlist"}
        assert ctor_kwargs["watchlist"]["startup_indicator_refresh_enabled"] is False
    finally:
        workspace.shutdown()
        workspace.deleteLater()


def test_workspace_background_prewarm_creates_whitelisted_current_tab_first_without_restore(monkeypatch):
    constructed = []

    def _resolve_tab_class(class_name, _module_name):
        class _Tab(QWidget):
            def __init__(self, *args, **kwargs):
                super().__init__()
                constructed.append(class_name)

            def prime_background_load(self):
                pass

        return _Tab

    monkeypatch.setattr(classic_workspace_module, "_resolve_tab_class", _resolve_tab_class)
    monkeypatch.setattr(
        classic_workspace_module.ClassicWorkspace,
        "BACKGROUND_PREWARM_KEYS",
        frozenset({"watchlist"}),
    )

    workspace = classic_workspace_module.ClassicWorkspace(data_provider=object(), engine=object())
    try:
        workspace.prime_stock_context_snapshots = lambda **kwargs: True
        monkeypatch.setattr(classic_workspace_module.QTimer, "singleShot", lambda _delay, callback: callback())

        workspace._start_background_tab_prewarm()

        assert constructed[0] == "WatchlistTab"
        assert constructed == ["WatchlistTab"]
    finally:
        workspace.shutdown()
        workspace.deleteLater()


def test_workspace_activates_loaded_lazy_tab_on_selection(monkeypatch):
    activated = []

    class _WatchlistTab(QWidget):
        def __init__(self, *args, **kwargs):
            super().__init__()

    class _LhbTab(QWidget):
        def __init__(self, *args, **kwargs):
            super().__init__()

        def on_workspace_tab_activated(self):
            activated.append("lhb")

    monkeypatch.setattr(classic_workspace_module, "WatchlistTab", _WatchlistTab)
    monkeypatch.setattr(classic_workspace_module, "LhbTab", _LhbTab)

    workspace = classic_workspace_module.ClassicWorkspace(
        data_provider=object(),
        engine=object(),
        background_prewarm=False,
    )
    try:
        monkeypatch.setattr(classic_workspace_module.QTimer, "singleShot", lambda _delay, callback: callback())
        widget = workspace.ensure_tab_loaded("lhb", reason="background_prewarm")
        lhb_index = next(index for index, spec in enumerate(workspace.tab_specs()) if spec.get("key") == "lhb")

        assert widget is workspace.get_loaded_tab("lhb")
        assert activated == []

        workspace.tabs.setCurrentIndex(lhb_index)

        assert activated == ["lhb"]
    finally:
        workspace.shutdown()
        workspace.deleteLater()


def test_workspace_debounces_table_copy_hook_install(monkeypatch):
    scheduled = []

    class _Host:
        def __init__(self):
            self.install_calls = 0

        def install_workspace_table_copy_hooks(self):
            self.install_calls += 1

    def _resolve_tab_class(_class_name, _module_name):
        class _Tab(QWidget):
            def __init__(self, *args, **kwargs):
                super().__init__()

        return _Tab

    def fake_single_shot(delay, callback):
        scheduled.append((delay, callback))

    host = _Host()
    monkeypatch.setattr(classic_workspace_module, "_resolve_tab_class", _resolve_tab_class)
    monkeypatch.setattr(classic_workspace_module.QTimer, "singleShot", fake_single_shot)

    workspace = classic_workspace_module.ClassicWorkspace(
        data_provider=object(),
        engine=object(),
        host=host,
        background_prewarm=False,
    )
    try:
        workspace.ensure_tab_loaded("lhb")
        workspace.ensure_tab_loaded("scan")

        hook_callbacks = [
            callback
            for delay, callback in scheduled
            if delay == workspace.COPY_HOOK_REFRESH_DELAY_MS
        ]
        assert len(hook_callbacks) == 1
        assert host.install_calls == 0

        hook_callbacks[0]()

        assert host.install_calls == 1
    finally:
        workspace.shutdown()
        workspace.deleteLater()


def test_workspace_auto_refresh_does_not_load_daily_tabs_without_manual_click(monkeypatch):
    ctor_kwargs = {}
    constructed = []

    def _make_tab(name):
        class _Tab(QWidget):
            def __init__(self, *args, **kwargs):
                super().__init__()
                ctor_kwargs[name] = dict(kwargs)
                constructed.append(name)

        return _Tab

    monkeypatch.setattr(classic_workspace_module, "WatchlistTab", _make_tab("watchlist"))
    monkeypatch.setattr(classic_workspace_module, "AsianMarketTab", _make_tab("asian_market"))
    monkeypatch.setattr(classic_workspace_module, "NADailyTab", _make_tab("na_daily"))
    monkeypatch.setattr(classic_workspace_module, "AIIndustryChainTab", _make_tab("ai_industry_chain"))
    monkeypatch.setattr(classic_workspace_module, "ScanTab", _make_tab("scan"))
    monkeypatch.setattr(classic_workspace_module, "StockCandidateTab", _make_tab("stock_candidates"))
    monkeypatch.setattr(classic_workspace_module, "LhbTab", _make_tab("lhb"))
    monkeypatch.setattr(classic_workspace_module, "ForeignBlockTradeTab", _make_tab("foreign_block"))
    monkeypatch.setattr(classic_workspace_module, "EarningsTab", _make_tab("earnings"))
    monkeypatch.setattr(classic_workspace_module, "FundHoldingsTab", _make_tab("fund_holdings"))
    monkeypatch.setattr(classic_workspace_module, "LogTab", _make_tab("system_log"))

    workspace = classic_workspace_module.ClassicWorkspace(
        data_provider=object(),
        engine=object(),
        background_prewarm=False,
    )
    try:
        assert constructed == []
        assert not hasattr(workspace, "_start_daily_auto_tab_bootstrap")
        assert workspace.get_loaded_tab("watchlist") is None
        assert workspace.get_loaded_tab("lhb") is None
        assert workspace.get_loaded_tab("foreign_block") is None
        assert workspace.get_loaded_tab("fund_holdings") is None
    finally:
        workspace.shutdown()
        workspace.deleteLater()


def test_classic_workspace_default_restore_waits_past_first_paint_window(qt_application):
    workspace = classic_workspace_module.ClassicWorkspace(
        data_provider=object(),
        engine=object(),
        background_prewarm=False,
    )
    try:
        workspace.schedule_restore_last_tab(1)
        timer = workspace._restore_last_tab_timer

        assert timer is not None
        assert timer.interval() == classic_workspace_module.ClassicWorkspace.RESTORE_LAST_TAB_DELAY_MS
        assert timer.interval() == 2500
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
