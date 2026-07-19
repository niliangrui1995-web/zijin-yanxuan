from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.services.stock_context_model_service import (
    DEFAULT_SOURCE_ORDER,
    StockContextReadPolicy,
    StockContextSnapshot,
    StockSignal,
)
from app.services.stock_context_query_service import GENERAL_STOCK_CONTEXT_SOURCE_KEYS, StockContextQueryService
from ui.workspaces.stock_context_widget_adapter import (
    SOURCE_KEYS,
    StockContextWidgetSnapshotAdapter,
    capture_workspace_stock_context,
)


class _RowsTab:
    def __init__(self, rows, *, keywords=()):
        self._rows = [dict(row) for row in rows]
        self._keywords = list(keywords)

    def get_row_data(self):
        return [dict(row) for row in self._rows]

    def get_foreign_keywords(self):
        return list(self._keywords)


class _ScanTab(_RowsTab):
    def get_scan_results(self):
        return self.get_row_data()


class _LineageTab(_RowsTab):
    def __init__(self, rows, *, status: str, private_loading: bool = False):
        super().__init__(rows)
        self._status = status
        self._pool_load_in_progress = private_loading

    def get_data_lineage(self):
        return {"status": self._status}


def test_widget_snapshot_source_keys_follow_general_contract_in_default_order():
    assert SOURCE_KEYS == tuple(
        source_key for source_key in DEFAULT_SOURCE_ORDER if source_key in GENERAL_STOCK_CONTEXT_SOURCE_KEYS
    )
    assert frozenset(SOURCE_KEYS) == GENERAL_STOCK_CONTEXT_SOURCE_KEYS


def test_widget_snapshot_loading_state_uses_public_lineage_not_widget_private_flags():
    tab = _LineageTab([], status="loaded", private_loading=True)
    workspace = SimpleNamespace(
        engine=None,
        get_loaded_tab=lambda key: tab if key == "lhb" else None,
        tab_specs=lambda: [{"key": "lhb", "title": "龙虎榜"}],
    )

    snapshot = StockContextWidgetSnapshotAdapter(workspace).capture()

    assert "lhb" not in snapshot.loading_sources
    tab._status = "loading"
    assert "lhb" in StockContextWidgetSnapshotAdapter(workspace).capture().loading_sources


def test_widget_snapshot_can_omit_rps_bundle_without_reading_engine():
    calls = []
    workspace = SimpleNamespace(
        engine=SimpleNamespace(get_precomputed_rps=lambda: calls.append("rps") or {"rps250": {"000001": 90}}),
        get_loaded_tab=lambda _key: None,
        tab_specs=lambda: [],
    )

    snapshot = StockContextWidgetSnapshotAdapter(workspace).capture(include_rps_bundle=False)

    assert calls == []
    assert snapshot.rps_bundle is None


def test_workspace_snapshot_helper_forwards_rps_capture_policy():
    calls = []
    workspace = SimpleNamespace(
        capture_stock_context_snapshot=lambda *, include_rps_bundle: calls.append(include_rps_bundle)
        or StockContextSnapshot()
    )

    snapshot = capture_workspace_stock_context(workspace, include_rps_bundle=False)

    assert isinstance(snapshot, StockContextSnapshot)
    assert calls == [False]


def test_workspace_snapshot_helper_preserves_legacy_default_reader_contract():
    calls = []

    def _collect_stock_context(*, capture_snapshot):
        calls.append(capture_snapshot)
        return StockContextSnapshot()

    snapshot = capture_workspace_stock_context(
        SimpleNamespace(collect_stock_context=_collect_stock_context)
    )

    assert isinstance(snapshot, StockContextSnapshot)
    assert calls == [True]


def test_workspace_snapshot_omits_rps_across_classic_facade_service_chain():
    from ui.workspaces.classic_workspace import ClassicWorkspace
    from ui.workspaces.stock_context_service import StockContextService
    from ui.workspaces.workspace_facade import WorkspaceFacade

    calls = []

    class _Workspace:
        capture_stock_context_snapshot = ClassicWorkspace.capture_stock_context_snapshot
        engine = SimpleNamespace(
            get_precomputed_rps=lambda: calls.append("rps") or {"rps250": {"000001": 90}}
        )

        @staticmethod
        def get_loaded_tab(_key):
            return None

        @staticmethod
        def tab_specs():
            return []

    workspace = _Workspace()
    facade = object.__new__(WorkspaceFacade)
    facade._workspace = workspace
    facade._stock_context_service = StockContextService(workspace)
    workspace._workspace_facade = facade

    snapshot = capture_workspace_stock_context(workspace, include_rps_bundle=False)

    assert isinstance(snapshot, StockContextSnapshot)
    assert snapshot.rps_bundle is None
    assert calls == []


def _golden_workspace():
    rows = {
        "scan": _ScanTab([{"代码": "000001", "名称": "平安银行", "评分": "91", "触发日期": "20260715"}]),
        "ai_industry_chain": _RowsTab(
            [
                {"代码": "000001", "名称": "平安银行", "细分板块": "液冷", "备注": "首条"},
                {"代码": "000001", "名称": "平安银行", "细分环节": "铜连接", "备注": "次条"},
            ]
        ),
        "na_daily": _RowsTab([{"代码": "000001", "名称": "平安银行", "细分板块": "北美映射", "催化剂": "催化"}]),
        "foreign_block": _RowsTab(
            [
                {"代码": "000001", "交易详情": "买入", "买方营业部": "高盛北京", "成交金额(万元)": 100},
                {"代码": "000001", "交易详情": "卖出", "卖方营业部": "高盛上海", "成交金额(万元)": 100},
            ],
            keywords=("高盛",),
        ),
        "earnings": _RowsTab(
            [
                {"代码": "000001", "名称": "平安银行", "报告期": "2026Q1", "环比%": "12.5"},
                {"代码": "000001", "名称": "平安银行", "报告期": "2026Q2", "环比%": "8"},
            ]
        ),
        "fund_holdings": _RowsTab(
            [
                {"代码": "000001", "主体": "QFII", "季度": "2026Q1", "变化类型": "增持"},
                {"代码": "000001", "主体": "QFII", "季度": "2026Q2", "变化类型": "新进"},
            ]
        ),
        "lhb": _RowsTab(
            [
                {"代码": "000001", "最近上榜": "20260715", "上榜净买额(万)": 20},
                {"代码": "000001", "最近上榜": "20260714", "上榜净买额(万)": 99},
            ]
        ),
    }
    return SimpleNamespace(
        engine=None,
        get_loaded_tab=lambda key: rows.get(key),
        tab_specs=lambda: [{"key": key, "title": key} for key in rows],
        iter_tabs=lambda: list(rows.values()),
    )


def _canonical(context):
    return {
        code: [
            (
                signal.source_tab,
                signal.signal_type,
                signal.summary,
                signal.numeric_value,
                signal.observed_at,
                dict(signal.payload),
            )
            for signal in signals
        ]
        for code, signals in context.items()
    }


def test_headless_snapshot_query_matches_stock_context_golden_contract(monkeypatch):
    workspace = _golden_workspace()
    monkeypatch.setattr(
        "app.services.stock_context_query_service.load_earnings_state_payload",
        lambda: ({}, ""),
    )
    snapshot = StockContextWidgetSnapshotAdapter(workspace).capture()
    headless = StockContextQueryService(snapshot).query_by_code(
        StockContextReadPolicy.build(
            include_cache_fallback=False,
            include_source_cache_fallback=False,
        )
    )

    assert _canonical(headless) == {
        "000001": [
            (
                "scan",
                "vcp_scan",
                "触发20260715 | 评分91",
                91.0,
                "20260715",
                {"代码": "000001", "名称": "平安银行", "评分": "91", "触发日期": "20260715"},
            ),
            (
                "ai_industry_chain",
                "subsector",
                "液冷 / 铜连接",
                None,
                "",
                {"代码": "000001", "名称": "平安银行", "细分板块": "液冷 / 铜连接", "备注": "首条 / 次条"},
            ),
            (
                "na_daily",
                "catalyst",
                "催化",
                None,
                "",
                {"代码": "000001", "名称": "平安银行", "细分板块": "北美映射", "催化剂": "催化"},
            ),
            (
                "na_daily",
                "subsector",
                "北美映射",
                None,
                "",
                {"代码": "000001", "名称": "平安银行", "细分板块": "北美映射", "催化剂": "催化"},
            ),
            (
                "foreign_block",
                "block_trade",
                "高盛卖出100万",
                100.0,
                "",
                {
                    "代码": "000001",
                    "交易详情": "卖出",
                    "卖方营业部": "高盛上海",
                    "成交金额(万元)": 100,
                    "amount_wan": 100.0,
                },
            ),
            (
                "earnings",
                "earnings",
                "一季度 12.5%",
                12.5,
                "",
                {
                    "代码": "000001",
                    "名称": "平安银行",
                    "报告期": "2026Q1",
                    "环比%": "12.5",
                    "qoq_pct": 12.5,
                    "业绩异动": "一季度 12.5%",
                },
            ),
            (
                "earnings",
                "earnings",
                "半年报 8%",
                8.0,
                "",
                {
                    "代码": "000001",
                    "名称": "平安银行",
                    "报告期": "2026Q2",
                    "环比%": "8",
                    "qoq_pct": 8.0,
                    "业绩异动": "半年报 8%",
                },
            ),
            (
                "fund_holdings",
                "fund_holding",
                "QFII | 新进 | 2026Q2",
                None,
                "2026Q2",
                {"代码": "000001", "主体": "QFII", "季度": "2026Q2", "变化类型": "新进"},
            ),
            (
                "lhb",
                "lhb",
                "07-15 | 净买20万 | 机构净买0万 | 外资净买0万",
                20.0,
                "20260715",
                {
                    "代码": "000001",
                    "最近上榜": "20260715",
                    "上榜净买额(万)": 20,
                    "date": "20260715",
                    "net_wan": 20.0,
                    "inst_wan": 0.0,
                    "foreign_wan": 0.0,
                },
            ),
        ]
    }
    signals = headless["000001"]
    assert [signal.source_tab for signal in signals] == [
        "scan",
        "ai_industry_chain",
        "na_daily",
        "na_daily",
        "foreign_block",
        "earnings",
        "earnings",
        "fund_holdings",
        "lhb",
    ]
    assert signals[1].summary == "液冷 / 铜连接"
    assert signals[4].summary == "高盛卖出100万"
    assert signals[-1].observed_at == "20260715"


def test_loaded_source_replaces_whole_cache_and_empty_source_uses_cache(monkeypatch):
    cached = [{"代码": "000002", "细分板块": "缓存板块"}]
    monkeypatch.setattr(
        "app.services.stock_context_query_service.load_ai_chain_cache_rows",
        lambda: cached,
    )
    policy = StockContextReadPolicy.build(sources={"ai_industry_chain"})

    loaded_workspace = SimpleNamespace(
        engine=None,
        get_loaded_tab=lambda _key: _RowsTab([{"代码": "000001", "细分板块": "界面板块"}]),
        tab_specs=lambda: [{"key": "ai_industry_chain"}],
    )
    loaded = StockContextQueryService(StockContextWidgetSnapshotAdapter(loaded_workspace).capture()).query_by_code(policy)
    assert set(loaded) == {"000001"}

    empty_workspace = SimpleNamespace(
        engine=None,
        get_loaded_tab=lambda _key: _RowsTab([]),
        tab_specs=lambda: [{"key": "ai_industry_chain"}],
    )
    fallback = StockContextQueryService(StockContextWidgetSnapshotAdapter(empty_workspace).capture()).query_by_code(policy)
    assert set(fallback) == {"000002"}


def test_stock_context_snapshot_is_deeply_immutable_but_returns_worker_copies():
    snapshot = StockContextSnapshot(
        source_rows={"scan": ({"代码": "000001", "nested": {"items": [1]}},)},
        direct_signals=(
            StockSignal(
                code="000001",
                source_tab="scan",
                signal_type="vcp_scan",
                summary="hit",
                payload={"tags": ["a"]},
            ),
        ),
        tab_titles={"scan": "VCP扫描"},
        rps_bundle={"rps250": {"000001": 90}},
    )

    with pytest.raises(TypeError):
        snapshot.source_rows["scan"][0]["代码"] = "000002"
    with pytest.raises(TypeError):
        snapshot.direct_signals[0].payload["tags"] = ()
    with pytest.raises(TypeError):
        snapshot.rps_bundle["rps250"] = {}

    rows = snapshot.rows_for("scan")
    rows[0]["nested"]["items"].append(2)
    assert snapshot.rows_for("scan")[0]["nested"]["items"] == [1]
