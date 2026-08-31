from __future__ import annotations

import threading
from types import MappingProxyType, SimpleNamespace

import pytest

import domains.stock_context.models as stock_context_models
from app.services.stock_context_model_service import (
    DEFAULT_SOURCE_ORDER,
    StockContextReadPolicy,
    StockContextSnapshot,
    StockSignal,
)
from app.services.stock_context_query_service import (
    GENERAL_STOCK_CONTEXT_SOURCE_KEYS,
    RADAR_SOURCE_KEYS,
    StockContextQueryService,
)
from ui.workspaces.stock_context_service import capture_stock_context_snapshot
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


class _ForbiddenDeepcopy:
    def __deepcopy__(self, _memo):
        raise AssertionError("non-target row was deep-copied")


class _ForbiddenIteration:
    def __len__(self):
        return 1

    def __iter__(self):
        raise AssertionError("empty target iterated cached rows")


def _cached_stock_context_service(source: str, rows):
    from ui.workspaces.stock_context_service import StockContextService

    workspace = SimpleNamespace(
        engine=None,
        get_loaded_tab=lambda _key: None,
        tab_specs=lambda: [{"key": source, "title": source}],
    )
    service = StockContextService(workspace)
    if source == "fund_holdings":
        service._fund_rows_loaded = True
        service._fund_rows_snapshot = rows
    elif source == "lhb":
        signature = ("lhb", 1, 1)
        service._lhb_rows_signature = signature
        service._lhb_rows_snapshot = rows
        service._lhb_pool_cache_signature = lambda: signature
    else:
        raise AssertionError(f"unsupported cached source: {source}")
    return service


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


def test_widget_snapshot_session_copies_only_one_configured_row_chunk_per_advance():
    class _CopyBudgetProbe:
        copied_in_current_advance = 0

        def __deepcopy__(self, _memo):
            type(self).copied_in_current_advance += 1
            if type(self).copied_in_current_advance > 2:
                raise AssertionError("one snapshot advance copied more than its row chunk")
            return type(self)()

    rows = [
        {"代码": f"00000{index}", "payload": _CopyBudgetProbe()}
        for index in range(1, 6)
    ]
    tab = _RowsTab(rows)
    workspace = SimpleNamespace(
        engine=None,
        get_loaded_tab=lambda key: tab if key == "ai_industry_chain" else None,
        tab_specs=lambda: [{"key": "ai_industry_chain", "title": "AI产业链"}],
    )

    session = StockContextWidgetSnapshotAdapter(workspace).begin_capture(
        include_rps_bundle=False,
        sources={"ai_industry_chain"},
        row_chunk_size=2,
    )

    for expected_codes in (("000001", "000002"), ("000003", "000004"), ("000005",)):
        _CopyBudgetProbe.copied_in_current_advance = 0
        assert session.advance() is False
        assert _CopyBudgetProbe.copied_in_current_advance == len(expected_codes)

    _CopyBudgetProbe.copied_in_current_advance = 0
    assert session.advance() is True
    assert _CopyBudgetProbe.copied_in_current_advance == 0
    assert [row["代码"] for row in session.snapshot().rows_for("ai_industry_chain")] == [
        "000001",
        "000002",
        "000003",
        "000004",
        "000005",
    ]


def test_widget_snapshot_session_finalization_does_not_refreeze_all_rows(monkeypatch):
    tab = _RowsTab(
        [
            {"代码": "000001", "细分板块": "AI"},
            {"代码": "000002", "细分板块": "算力"},
        ]
    )
    workspace = SimpleNamespace(
        engine=None,
        get_loaded_tab=lambda key: tab if key == "ai_industry_chain" else None,
        tab_specs=lambda: [{"key": "ai_industry_chain", "title": "AI产业链"}],
    )
    session = StockContextWidgetSnapshotAdapter(workspace).begin_capture(
        include_rps_bundle=False,
        sources={"ai_industry_chain"},
    )

    assert session.advance() is False
    monkeypatch.setattr(
        stock_context_models,
        "_freeze_source_rows",
        lambda _rows: (_ for _ in ()).throw(AssertionError("finalization re-froze all source rows")),
    )

    assert session.advance() is True
    assert [row["代码"] for row in session.snapshot().rows_for("ai_industry_chain")] == [
        "000001",
        "000002",
    ]


def test_snapshot_frozen_parts_finalization_does_not_walk_large_pre_frozen_rows():
    class _NoFinalizationRowWalk(tuple):
        def __iter__(self):
            raise AssertionError("assemble_snapshot walked every already-frozen row")

    rows = _NoFinalizationRowWalk(
        [
            MappingProxyType({"代码": f"{index:06d}", "payload": MappingProxyType({"rank": index})})
            for index in range(4096)
        ]
    )

    snapshot = StockContextSnapshot._from_frozen_parts(
        source_rows={"ai_industry_chain": rows},
        cached_source_rows={},
        source_row_counts={"ai_industry_chain": len(rows)},
    )

    assert snapshot.source_rows["ai_industry_chain"] is rows
    assert snapshot.source_row_counts["ai_industry_chain"] == 4096


def test_widget_snapshot_prefers_lazy_row_iterators_over_compatibility_getters():
    class _LazyRowsTab:
        @staticmethod
        def iter_stock_context_rows():
            return iter(({"代码": "000001", "来源": "lazy"},))

        @staticmethod
        def get_row_data():
            raise AssertionError("adapter called eager get_row_data despite lazy iterator")

    class _LazyScanTab:
        @staticmethod
        def iter_scan_results():
            return iter(({"代码": "000002", "来源": "scan_lazy"},))

        @staticmethod
        def get_scan_results():
            raise AssertionError("adapter called eager get_scan_results despite lazy iterator")

        @staticmethod
        def get_row_data():
            raise AssertionError("adapter called scan fallback getter despite lazy iterator")

    tabs = {
        "ai_industry_chain": _LazyRowsTab(),
        "scan": _LazyScanTab(),
    }
    workspace = SimpleNamespace(
        engine=None,
        get_loaded_tab=lambda key: tabs.get(key),
        tab_specs=lambda: [
            {"key": "ai_industry_chain", "title": "AI产业链"},
            {"key": "scan", "title": "扫描"},
        ],
    )

    snapshot = StockContextWidgetSnapshotAdapter(workspace).capture(
        include_rps_bundle=False,
        sources={"ai_industry_chain", "scan"},
        row_chunk_size=1,
    )

    assert snapshot.rows_for("ai_industry_chain") == [{"代码": "000001", "来源": "lazy"}]
    assert snapshot.rows_for("scan") == [{"代码": "000002", "来源": "scan_lazy"}]


def test_workspace_snapshot_helper_forwards_rps_capture_policy():
    calls = []
    workspace = SimpleNamespace(
        capture_stock_context_snapshot=lambda *, include_rps_bundle: calls.append(include_rps_bundle)
        or StockContextSnapshot()
    )

    snapshot = capture_workspace_stock_context(workspace, include_rps_bundle=False)

    assert isinstance(snapshot, StockContextSnapshot)
    assert calls == [False]


def test_workspace_snapshot_helper_forwards_source_scope():
    calls = []
    workspace = SimpleNamespace(
        capture_stock_context_snapshot=lambda *, include_rps_bundle, sources: calls.append(
            (include_rps_bundle, frozenset(sources))
        )
        or StockContextSnapshot()
    )

    snapshot = capture_workspace_stock_context(
        workspace,
        include_rps_bundle=False,
        sources=RADAR_SOURCE_KEYS,
    )

    assert isinstance(snapshot, StockContextSnapshot)
    assert calls == [(False, frozenset(RADAR_SOURCE_KEYS))]


def test_workspace_snapshot_helper_forwards_target_scope():
    calls = []
    workspace = SimpleNamespace(
        capture_stock_context_snapshot=lambda *, include_rps_bundle, sources, target_codes: calls.append(
            (include_rps_bundle, frozenset(sources), tuple(target_codes))
        )
        or StockContextSnapshot()
    )

    snapshot = capture_workspace_stock_context(
        workspace,
        include_rps_bundle=False,
        sources=RADAR_SOURCE_KEYS,
        target_codes=("000001",),
    )

    assert isinstance(snapshot, StockContextSnapshot)
    assert calls == [(False, frozenset(RADAR_SOURCE_KEYS), ("000001",))]


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


def test_scoped_widget_snapshot_never_reads_unrequested_tabs():
    accessed = []
    tabs = {
        key: _RowsTab([{"代码": "000001", "来源": key}])
        for key in SOURCE_KEYS
    }

    def _get_loaded_tab(key):
        accessed.append(key)
        if key in {"scan", "fund_holdings"}:
            raise AssertionError(f"unrequested source was read: {key}")
        return tabs.get(key)

    workspace = SimpleNamespace(
        engine=None,
        get_loaded_tab=_get_loaded_tab,
        tab_specs=lambda: [{"key": key, "title": key} for key in SOURCE_KEYS],
    )

    snapshot = StockContextWidgetSnapshotAdapter(workspace).capture(
        include_rps_bundle=False,
        sources=RADAR_SOURCE_KEYS,
    )

    assert set(accessed) == set(RADAR_SOURCE_KEYS)
    assert set(snapshot.source_rows) == set(RADAR_SOURCE_KEYS)
    assert snapshot.available_sources == frozenset(RADAR_SOURCE_KEYS)


def test_scoped_service_snapshot_skips_unrequested_cached_sources():
    workspace = SimpleNamespace(
        engine=None,
        get_loaded_tab=lambda _key: None,
        tab_specs=lambda: [],
    )
    service = SimpleNamespace(
        _workspace=workspace,
        _fund_rows_lock=threading.Lock(),
        _fund_rows_loaded=True,
        _fund_rows_loading=False,
        _fund_rows_snapshot=[object()],
        _lhb_rows_lock=threading.Lock(),
        _lhb_rows_loading=False,
        _lhb_rows_signature=None,
        _lhb_rows_snapshot=[object()],
        _lhb_pool_cache_signature=lambda: (_ for _ in ()).throw(
            AssertionError("unrequested LHB cache signature was read")
        ),
    )

    snapshot = capture_stock_context_snapshot(
        service,
        include_rps_bundle=False,
        sources={"earnings"},
    )

    assert snapshot.cached_source_rows == {}
    assert snapshot.loading_sources == frozenset()


@pytest.mark.parametrize("source", ["fund_holdings", "lhb"])
def test_target_scoped_adapter_filters_cached_rows_before_deepcopy(source):
    other_source = "lhb" if source == "fund_holdings" else "fund_holdings"
    rows = [
        {"代码": "000001", "payload": _ForbiddenDeepcopy()},
        {"代码": "000002", "payload": {"items": [2]}},
    ]
    workspace = SimpleNamespace(
        engine=None,
        get_loaded_tab=lambda _key: None,
        tab_specs=lambda: [{"key": source, "title": source}],
    )

    snapshot = StockContextWidgetSnapshotAdapter(workspace).capture(
        cached_source_rows={source: rows, other_source: [{"代码": "000003"}]},
        include_rps_bundle=False,
        sources={source},
        target_codes={"000002"},
    )

    assert [row["代码"] for row in snapshot.cached_rows_for(source)] == ["000002"]
    assert snapshot.cached_source_row_counts == {source: 2}


@pytest.mark.parametrize("source", ["fund_holdings", "lhb"])
def test_target_scoped_service_filters_cached_rows_before_adapter(monkeypatch, source):
    rows = [
        {"代码": "000001", "payload": {"items": [1]}},
        {"代码": "000002", "payload": {"items": [2]}},
    ]
    service = _cached_stock_context_service(source, rows)
    captured = {}

    def _capture(_adapter, **kwargs):
        captured.update(kwargs)
        return StockContextSnapshot()

    monkeypatch.setattr(
        "ui.workspaces.stock_context_service.StockContextWidgetSnapshotAdapter.capture",
        _capture,
    )

    capture_stock_context_snapshot(
        service,
        include_rps_bundle=False,
        sources={source},
        target_codes={"000002"},
    )

    published_rows = captured["cached_source_rows"][source]
    assert [row["代码"] for row in published_rows] == ["000002"]
    assert published_rows[0] is not rows[1]
    assert captured["cached_source_row_counts"][source] == 2
    stored_rows = (
        service._fund_rows_snapshot
        if source == "fund_holdings"
        else service._lhb_rows_snapshot
    )
    assert stored_rows is rows
    assert rows[0]["代码"] == "000001"


@pytest.mark.parametrize("source", ["fund_holdings", "lhb"])
def test_empty_target_service_snapshot_preserves_cached_key_without_iterating_rows(source):
    service = _cached_stock_context_service(source, _ForbiddenIteration())

    snapshot = capture_stock_context_snapshot(
        service,
        include_rps_bundle=False,
        sources={source},
        target_codes=set(),
    )

    assert source in snapshot.cached_source_rows
    assert snapshot.cached_source_rows[source] == ()
    assert snapshot.cached_source_row_counts[source] == 1


@pytest.mark.parametrize("source", ["fund_holdings", "lhb"])
def test_target_miss_in_published_cache_does_not_fall_back_to_lower_store(monkeypatch, source):
    service = _cached_stock_context_service(source, [{"代码": "000001"}])
    snapshot = capture_stock_context_snapshot(
        service,
        include_rps_bundle=False,
        sources={source},
        target_codes={"000002"},
    )
    if source == "fund_holdings":
        monkeypatch.setattr(
            "app.services.stock_context_query_service.load_fund_holding_snapshot",
            lambda **_kwargs: (_ for _ in ()).throw(AssertionError("fund store fallback was called")),
        )
    else:
        monkeypatch.setattr(
            "app.services.stock_context_query_service.load_lhb_pool_rows",
            lambda **_kwargs: (_ for _ in ()).throw(AssertionError("LHB compute fallback was called")),
        )

    policy = StockContextReadPolicy.build(
        allow_lhb_cache_compute=True,
        target_codes={"000002"},
        sources={source},
    )

    assert source in snapshot.cached_source_rows
    assert snapshot.cached_source_rows[source] == ()
    assert snapshot.cached_source_row_counts[source] == 1
    assert StockContextQueryService(snapshot).query_by_code(policy) == {}


def test_originally_empty_fund_cache_still_falls_back_to_store(monkeypatch):
    calls = []
    service = _cached_stock_context_service("fund_holdings", [])
    snapshot = capture_stock_context_snapshot(
        service,
        include_rps_bundle=False,
        sources={"fund_holdings"},
        target_codes={"000002"},
    )
    monkeypatch.setattr(
        "app.services.stock_context_query_service.load_fund_holding_snapshot",
        lambda **kwargs: calls.append(kwargs) or ({}, []),
    )
    policy = StockContextReadPolicy.build(
        target_codes={"000002"},
        sources={"fund_holdings"},
    )

    assert snapshot.cached_source_rows["fund_holdings"] == ()
    assert snapshot.cached_source_row_counts["fund_holdings"] == 0
    assert StockContextQueryService(snapshot).query_by_code(policy) == {}
    assert calls == [{"stock_codes": frozenset({"000002"})}]


def test_originally_empty_lhb_cache_still_allows_compute(monkeypatch):
    calls = []
    service = _cached_stock_context_service("lhb", [])
    snapshot = capture_stock_context_snapshot(
        service,
        include_rps_bundle=False,
        sources={"lhb"},
        target_codes={"000002"},
    )
    monkeypatch.setattr(
        "app.services.stock_context_query_service.load_lhb_pool_rows",
        lambda **kwargs: calls.append(kwargs) or [],
    )
    policy = StockContextReadPolicy.build(
        allow_lhb_cache_compute=True,
        target_codes={"000002"},
        sources={"lhb"},
    )

    assert snapshot.cached_source_rows["lhb"] == ()
    assert snapshot.cached_source_row_counts["lhb"] == 0
    assert StockContextQueryService(snapshot).query_by_code(policy) == {}
    assert calls == [{"engine": None}]


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


def test_workspace_snapshot_forwards_target_scope_across_classic_facade_service_chain():
    from ui.workspaces.classic_workspace import ClassicWorkspace
    from ui.workspaces.stock_context_service import StockContextService
    from ui.workspaces.workspace_facade import WorkspaceFacade

    rows_tab = _RowsTab(
        [
            {"代码": "000001", "报告期": "2026Q1"},
            {"代码": "000002", "报告期": "2026Q2"},
        ]
    )

    class _Workspace:
        capture_stock_context_snapshot = ClassicWorkspace.capture_stock_context_snapshot
        engine = None

        @staticmethod
        def get_loaded_tab(key):
            return rows_tab if key == "earnings" else None

        @staticmethod
        def tab_specs():
            return [{"key": "earnings", "title": "业绩"}]

    workspace = _Workspace()
    facade = object.__new__(WorkspaceFacade)
    facade._workspace = workspace
    facade._stock_context_service = StockContextService(workspace)
    workspace._workspace_facade = facade

    snapshot = capture_workspace_stock_context(
        workspace,
        include_rps_bundle=False,
        sources={"earnings"},
        target_codes={"000002"},
    )

    assert [row["代码"] for row in snapshot.rows_for("earnings")] == ["000002"]
    assert snapshot.source_row_counts == {"earnings": 2}


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


def test_scoped_snapshot_preserves_watchlist_radar_results(monkeypatch):
    workspace = _golden_workspace()
    monkeypatch.setattr(
        "app.services.stock_context_query_service.load_earnings_state_payload",
        lambda: ({}, ""),
    )
    adapter = StockContextWidgetSnapshotAdapter(workspace)

    full_result = StockContextQueryService(
        adapter.capture(include_rps_bundle=False)
    ).query_watchlist_radar(
        target_codes={"000001"},
        include_source_cache_fallback=False,
    )
    scoped_result = StockContextQueryService(
        adapter.capture(
            include_rps_bundle=False,
            sources=RADAR_SOURCE_KEYS,
        )
    ).query_watchlist_radar(
        target_codes={"000001"},
        include_source_cache_fallback=False,
    )

    assert scoped_result == full_result


def test_scoped_snapshot_keeps_loaded_source_over_cache_precedence(monkeypatch):
    monkeypatch.setattr(
        "app.services.stock_context_query_service.load_ai_chain_cache_rows",
        lambda: [{"代码": "000002", "细分板块": "缓存板块"}],
    )
    workspace = SimpleNamespace(
        engine=None,
        get_loaded_tab=lambda key: (
            _RowsTab([{"代码": "000001", "细分板块": "界面板块"}])
            if key == "ai_industry_chain"
            else None
        ),
        tab_specs=lambda: [{"key": "ai_industry_chain", "title": "AI产业链"}],
    )
    snapshot = StockContextWidgetSnapshotAdapter(workspace).capture(
        include_rps_bundle=False,
        sources=RADAR_SOURCE_KEYS,
    )
    policy = StockContextReadPolicy.build(
        include_cache_fallback=False,
        include_source_cache_fallback=True,
        target_codes={"000002"},
        sources={"ai_industry_chain"},
    )

    assert StockContextQueryService(snapshot).query_by_code(policy) == {}


def test_target_scoped_snapshot_prefilters_before_deepcopy_and_keeps_loaded_precedence(monkeypatch):
    target_workspace = SimpleNamespace(
        engine=None,
        get_loaded_tab=lambda key: (
            _RowsTab(
                [
                    {"代码": "000001", "payload": _ForbiddenDeepcopy()},
                    {"代码": "000002", "细分板块": "界面板块"},
                ]
            )
            if key == "ai_industry_chain"
            else None
        ),
        tab_specs=lambda: [{"key": "ai_industry_chain", "title": "AI产业链"}],
    )

    snapshot = StockContextWidgetSnapshotAdapter(target_workspace).capture(
        include_rps_bundle=False,
        sources={"ai_industry_chain"},
        target_codes={"000002"},
    )

    assert [row["代码"] for row in snapshot.rows_for("ai_industry_chain")] == ["000002"]
    assert snapshot.source_row_counts == {"ai_industry_chain": 2}

    monkeypatch.setattr(
        "app.services.stock_context_query_service.load_ai_chain_cache_rows",
        lambda: [{"代码": "000002", "细分板块": "缓存板块"}],
    )
    non_target_workspace = SimpleNamespace(
        engine=None,
        get_loaded_tab=lambda key: (
            _RowsTab([{"代码": "000001", "细分板块": "界面板块"}])
            if key == "ai_industry_chain"
            else None
        ),
        tab_specs=lambda: [{"key": "ai_industry_chain", "title": "AI产业链"}],
    )
    non_target_snapshot = StockContextWidgetSnapshotAdapter(non_target_workspace).capture(
        include_rps_bundle=False,
        sources={"ai_industry_chain"},
        target_codes={"000002"},
    )
    policy = StockContextReadPolicy.build(
        include_source_cache_fallback=True,
        target_codes={"000002"},
        sources={"ai_industry_chain"},
    )

    assert non_target_snapshot.rows_for("ai_industry_chain") == []
    assert non_target_snapshot.source_row_counts == {"ai_industry_chain": 1}
    assert StockContextQueryService(non_target_snapshot).query_by_code(policy) == {}


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


def test_cached_source_row_counts_are_immutable_normalized_and_backward_compatible():
    default_snapshot = StockContextSnapshot()
    snapshot = StockContextSnapshot(
        cached_source_rows={"fund_holdings": ({"代码": "000001"},)},
        cached_source_row_counts={
            "fund_holdings": 0,
            "lhb": "2",
            "": 7,
            "invalid": "not-a-count",
        },
    )

    assert default_snapshot.cached_source_row_counts == {}
    assert snapshot.cached_source_row_counts == {"fund_holdings": 1, "lhb": 2}
    with pytest.raises(TypeError):
        snapshot.cached_source_row_counts["lhb"] = 3
