# -*- coding: utf-8 -*-

from __future__ import annotations

from types import SimpleNamespace

from app.services.stock_context_model_service import (
    StockContextReadPolicy,
    StockContextSignalIndex,
    StockContextSnapshot,
    StockSignal,
)
from app.services.stock_context_query_service import StockContextQueryService
from ui.workspaces import stock_context_service as context_module
from ui.workspaces.stock_context_service import StockContextService, capture_stock_context_snapshot


def test_stock_context_query_public_contract_covers_signal_variants(monkeypatch):
    monkeypatch.setattr(
        "app.services.stock_context_query_service.load_earnings_state_payload",
        lambda: ({}, ""),
    )
    snapshot = StockContextSnapshot(
        source_rows={
            "foreign_block": (
                {"代码": "000001", "交易详情": "买入", "买方营业部": "高盛上海", "成交金额(万元)": 100},
            ),
            "earnings": (
                {"代码": "000001", "报告期": "2026Q1", "环比%": "12.5"},
                {"代码": "000002", "报告期": "2025-12-31", "环比%": -8},
            ),
            "fund_holdings": (
                {"代码": "000001", "主体": "QFII", "季度": "2026Q2", "变化类型": "新进"},
                {"代码": "000001", "主体": "QFII", "季度": "2026Q1", "变化类型": "增持"},
            ),
            "lhb": (
                {
                    "代码": "000001",
                    "最近上榜": "20260715",
                    "上榜净买额(万)": -10,
                    "机构净买(万)": -20,
                    "外资净买(万)": -30,
                },
            ),
        },
        available_sources=frozenset({"foreign_block", "earnings", "fund_holdings", "lhb"}),
        foreign_keywords=("高盛",),
    )

    context = StockContextQueryService(snapshot).query_by_code(
        StockContextReadPolicy.build(
            include_cache_fallback=False,
            include_source_cache_fallback=False,
        )
    )

    assert [(signal.source_tab, signal.summary) for signal in context["000001"]] == [
        ("foreign_block", "高盛买入100万"),
        ("earnings", "一季度 12.5%"),
        ("fund_holdings", "QFII | 新进 | 2026Q2"),
        ("lhb", "07-15 | 净卖10万 | 机构净卖20万 | 外资净卖30万"),
    ]
    assert context["000002"][0].summary == "年报 -8%"


def test_stock_context_capture_publishes_cached_rows_and_direct_signals():
    direct_signal = StockSignal(
        code="688498",
        source_tab="custom",
        signal_type="research_note",
        summary="自定义研究信号",
    )
    custom_tab = SimpleNamespace(iter_stock_signals=lambda: [direct_signal])
    workspace = SimpleNamespace(
        engine=None,
        tab_specs=lambda: [{"key": "custom", "title": "自定义"}, {"key": "fund_holdings", "title": "基金持仓"}],
        get_loaded_tab=lambda key: custom_tab if key == "custom" else None,
    )
    service = StockContextService(workspace)
    service._fund_rows_loaded = True
    service._fund_rows_snapshot = [{"代码": "000001", "主体": "QFII", "季度": "2026Q2", "变化类型": "新进"}]

    snapshot = capture_stock_context_snapshot(service)
    signals = StockContextQueryService(snapshot).query_signals(StockContextReadPolicy())

    assert snapshot.cached_rows_for("fund_holdings")[0]["代码"] == "000001"
    assert signals[0] == direct_signal
    assert snapshot.tab_titles == {"custom": "自定义", "fund_holdings": "基金持仓"}


def test_stock_context_capture_forwards_rps_capture_policy():
    calls = []
    workspace = SimpleNamespace(
        engine=SimpleNamespace(get_precomputed_rps=lambda: calls.append("rps") or {}),
        tab_specs=lambda: [],
        get_loaded_tab=lambda _key: None,
    )
    service = StockContextService(workspace)

    snapshot = capture_stock_context_snapshot(service, include_rps_bundle=False)

    assert snapshot.rps_bundle is None
    assert calls == []


def test_stock_context_service_atomically_publishes_kline_signal_index():
    service = StockContextService(SimpleNamespace())
    first = StockContextSignalIndex.from_context(
        {
            "000001": [
                StockSignal("000001", "earnings", "earnings", "一季度增长")
            ]
        }
    )
    second = StockContextSignalIndex.from_context(
        {
            "000002": [
                StockSignal("000002", "scan", "vcp_scan", "VCP")
            ]
        }
    )

    assert service.published_kline_signals("000001") is None
    assert service.publish_kline_signal_index(first) == 1
    assert service.published_kline_signals("000001") == first.signals_for("000001")
    assert service.publish_kline_signal_index(second) == 2
    assert service.published_kline_signals("000001") == ()
    assert service.published_kline_signals("000002") == second.signals_for("000002")


def test_stock_context_post_f5_defer_and_shutdown(monkeypatch):
    service = StockContextService(SimpleNamespace())
    monkeypatch.setattr(context_module.time, "monotonic", lambda: 100.0)
    service.prepare_post_f5_refresh()
    assert service._should_defer_async_snapshots()
    monkeypatch.setattr(context_module.time, "monotonic", lambda: 200.0)
    assert not service._should_defer_async_snapshots()
    service._post_f5_snapshot_defer_until = "bad"
    assert not service._should_defer_async_snapshots()
    service._fund_rows_loading = True
    service._lhb_rows_loading = True
    monkeypatch.setattr(service._task_lifecycle, "shutdown", lambda **kwargs: kwargs["timeout_ms"] == 123)
    assert service.shutdown(timeout_ms=123)
    assert service._shutdown
    assert not service._fund_rows_loading and not service._lhb_rows_loading
    assert not service.refresh_async_snapshots()


def test_stock_context_snapshot_status_and_cancellation_are_public_contracts(monkeypatch):
    service = StockContextService(SimpleNamespace())
    calls = []
    service._fund_rows_loading = True
    service._lhb_rows_loading = True
    monkeypatch.setattr(
        service._task_lifecycle,
        "cancel",
        lambda name, *, reason: calls.append((name, reason)) or True,
    )

    assert service.async_snapshots_settled() is False
    assert service.cancel_async_snapshots(reason="step_timeout") is True
    assert calls == [
        ("fund-snapshot", "step_timeout"),
        ("lhb-snapshot", "step_timeout"),
    ]
    assert service.async_snapshots_settled() is True
