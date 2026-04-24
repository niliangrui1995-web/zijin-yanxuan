# -*- coding: utf-8 -*-

from PyQt6.QtTest import QSignalSpy

from app.services.ui_runtime_service import ui_signals
from ui.components import stock_detail_dialog as stock_detail_module
from ui.components.stock_detail_dialog import StockDetailDialog, build_signal_rows
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


def test_stock_detail_dialog_actions_emit_kline_and_toggle_watchlist(monkeypatch):
    toggled = {}
    state = {"fav": False}

    monkeypatch.setattr(stock_detail_module.watchlist_vm, "is_in_watchlist", lambda _code: state["fav"])

    def _toggle_stock(code, name, payload):
        toggled["code"] = code
        toggled["name"] = name
        toggled["payload"] = dict(payload)
        state["fav"] = not state["fav"]

    monkeypatch.setattr(stock_detail_module.watchlist_vm, "toggle_stock", _toggle_stock)
    spy = QSignalSpy(ui_signals.sig_show_kline_with_list)

    dialog = StockDetailDialog(
        "300750",
        "宁德时代",
        [StockSignal(code="300750", source_tab="scan", signal_type="vcp_scan", summary="VCP扫描命中")],
        context={"市价": "183.50", "涨幅%": 5.2},
    )

    dialog._open_kline()
    assert len(spy) == 1
    assert spy[0][0] == "300750"
    assert spy[0][1][0]["市价"] == "183.50"

    dialog._toggle_watchlist()
    assert toggled["code"] == "300750"
    assert toggled["name"] == "宁德时代"
    assert toggled["payload"]["代码"] == "300750"
    assert toggled["payload"]["涨幅%"] == 5.2
    assert dialog.btn_watchlist.text() == "移出关注池"
