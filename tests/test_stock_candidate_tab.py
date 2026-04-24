# -*- coding: utf-8 -*-
from types import SimpleNamespace

from app.services.ui_runtime_service import domain_events as event_bus
from ui.tabs.stock_candidate_tab import StockCandidateTab
from ui.workspaces.stock_signal import StockSignal


def test_stock_candidate_rows_keep_multi_source_names_and_rank_score():
    class DummyTab:
        @staticmethod
        def _workspace():
            return SimpleNamespace(
                tab_specs=lambda: [
                    {"key": "na_daily", "title": "北美战报"},
                    {"key": "earnings", "title": "业绩异动"},
                ]
            )

    rows = StockCandidateTab._build_candidate_rows(
        DummyTab(),
        {
            "300750": [
                StockSignal(
                    code="300750",
                    name="宁德时代",
                    source_tab="na_daily",
                    signal_type="catalyst",
                    summary="北美订单催化",
                    payload={"现价": "183.50", "涨幅%": 5.2, "市值": "1.18万亿"},
                ),
                StockSignal(
                    code="300750",
                    source_tab="earnings",
                    signal_type="earnings",
                    summary="32.5%",
                    observed_at="20260420",
                ),
            ],
            "000001": [
                StockSignal(
                    code="000001",
                    source_tab="na_daily",
                    signal_type="catalyst",
                    summary="单一信号",
                )
            ],
        },
    )

    assert len(rows) == 1
    row = rows[0]
    assert row["代码"] == "300750"
    assert row["名称"] == "宁德时代"
    assert row["市价"] == "183.50"
    assert row["涨幅%"] == "5.2"
    assert row["市值"] == "1.18万亿"
    assert row["来源数"] == 2
    assert row["信号数"] == 2
    assert row["来源"] == "北美战报｜业绩异动"
    assert row["最近时间"] == "20260420"
    assert "北美订单催化" in row["核心信号"]


def test_stock_candidate_rows_compact_vcp_scan_core_signal():
    class DummyTab:
        @staticmethod
        def _workspace():
            return SimpleNamespace(tab_specs=lambda: [{"key": "scan", "title": "VCP扫描"}])

    rows = StockCandidateTab._build_candidate_rows(
        DummyTab(),
        {
            "688498": [
                StockSignal(
                    code="688498",
                    source_tab="scan",
                    signal_type="vcp_scan",
                    summary="触发20260423 | 评分91 | RPS96 | 接近突破 | CPO",
                    payload={
                        "触发日期": "20260423",
                        "RPS强度": "96",
                    },
                ),
                StockSignal(
                    code="688498",
                    source_tab="scan",
                    signal_type="vcp_scan",
                    summary="触发20260423 | 评分91 | RPS96 | 接近突破 | CPO",
                    payload={
                        "触发日期": "20260423",
                        "RPS强度": "96",
                    },
                ),
            ],
        },
    )

    assert len(rows) == 1
    assert rows[0]["核心信号"] == "触发日期 20260423 | RPS 96"


def test_stock_candidate_listens_to_global_quote_updates(monkeypatch):
    monkeypatch.setattr("ui.tabs.stock_candidate_tab.QTimer.singleShot", lambda *_args, **_kwargs: None)
    tab = StockCandidateTab(data_provider=SimpleNamespace())
    try:
        tab.model.update_data(
            [
                {
                    "代码": "300750",
                    "名称": "宁德时代",
                    "市价": "--",
                    "涨幅%": "--",
                    "市值": "--",
                    "共振分": 22,
                    "来源数": 2,
                    "信号数": 2,
                    "来源": "VCP扫描｜基金持仓",
                    "核心信号": "触发日期 20260423 | RPS 96",
                    "最近时间": "20260423",
                }
            ]
        )

        tab.show()
        event_bus.sig_rt_quotes.emit(
            {
                "300750": {
                    "close": 183.5,
                    "last_close": 175.0,
                    "zongguben": 6_400_000_000,
                }
            }
        )

        row = tab.model.row_data[0]
        assert row["市价"] == "183.50"
        assert round(float(row["涨幅%"]), 2) == 4.86
        assert row["市值"] == "11744亿"
        assert tab.get_realtime_quote_codes() == {"300750"}
    finally:
        tab.close()
