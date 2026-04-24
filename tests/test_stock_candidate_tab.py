# -*- coding: utf-8 -*-
from types import SimpleNamespace

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
    assert row["来源数"] == 2
    assert row["信号数"] == 2
    assert row["来源"] == "北美战报｜业绩异动"
    assert row["最近时间"] == "20260420"
    assert "北美订单催化" in row["核心信号"]
