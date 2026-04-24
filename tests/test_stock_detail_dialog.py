# -*- coding: utf-8 -*-

from ui.components.stock_detail_dialog import build_signal_rows
from ui.workspaces.stock_signal import StockSignal


def test_build_signal_rows_uses_tab_titles_and_numeric_formatting():
    rows = build_signal_rows(
        [
            StockSignal(
                code="300750",
                source_tab="earnings",
                signal_type="earnings",
                summary="32.5%",
                numeric_value=32.5,
                observed_at="20260420",
            ),
            StockSignal(
                code="300750",
                source_tab="foreign_block",
                signal_type="block_trade",
                summary="机构专用买入2709万",
                numeric_value=2709,
            ),
        ],
        tab_titles={"earnings": "业绩异动"},
    )

    assert rows[0]["source"] == "业绩异动"
    assert rows[0]["type"] == "业绩异动"
    assert rows[0]["value"] == "32.5"
    assert rows[0]["time"] == "20260420"
    assert rows[1]["source"] == "大宗交易"
    assert rows[1]["value"] == "2,709"
