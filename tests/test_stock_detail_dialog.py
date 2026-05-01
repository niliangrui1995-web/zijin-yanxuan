# -*- coding: utf-8 -*-

from PyQt6.QtCore import Qt
from PyQt6.QtTest import QSignalSpy
from PyQt6.QtWidgets import QDialog, QFrame, QLabel, QToolButton

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
    monkeypatch.setattr(
        stock_detail_module.QTimer,
        "singleShot",
        staticmethod(lambda _delay, callback: callback()),
    )

    def _toggle_stock(code, name, payload):
        toggled["code"] = code
        toggled["name"] = name
        toggled["payload"] = dict(payload)
        state["fav"] = not state["fav"]

    monkeypatch.setattr(stock_detail_module.watchlist_vm, "toggle_stock", _toggle_stock)
    spy = QSignalSpy(ui_signals.sig_show_kline_with_list)

    scan_signal = StockSignal(
        code="300750",
        source_tab="scan",
        signal_type="vcp_scan",
        summary="VCP扫描命中",
        observed_at="20260416",
        payload={"区间最高价": 251.2, "区间最低点": 218.5},
    )
    dialog = StockDetailDialog(
        "300750",
        "宁德时代",
        [scan_signal],
        context={"市价": "183.50", "涨幅%": 5.2},
    )

    dialog._open_kline()
    assert dialog.result() != QDialog.DialogCode.Accepted
    assert len(spy) == 1
    assert spy[0][0] == "300750"
    assert spy[0][1][0]["市价"] == "183.50"
    assert spy[0][1][0]["_signals"] == [scan_signal]

    dialog._toggle_watchlist()
    assert toggled["code"] == "300750"
    assert toggled["name"] == "宁德时代"
    assert toggled["payload"]["代码"] == "300750"
    assert toggled["payload"]["涨幅%"] == 5.2
    assert "_signals" not in toggled["payload"]
    assert dialog.btn_watchlist.text() == "移出关注池"


def test_stock_detail_dialog_uses_themed_frameless_shell():
    dialog = StockDetailDialog(
        "603186",
        "华正新材",
        [StockSignal(code="603186", source_tab="na_daily", signal_type="catalyst", summary="北美战报")],
    )
    try:
        assert dialog.objectName() == "stockDetailDialog"
        assert dialog.windowFlags() & Qt.WindowType.FramelessWindowHint
        assert dialog.testAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        assert dialog.findChild(QFrame, "dialogContainer") is not None
        assert dialog.findChild(QLabel, "dialogWindowTitle").text() == "股票全景 - 华正新材"
        assert dialog.findChild(QToolButton, "dialogCloseButton") is not None
    finally:
        dialog.close()


def test_stock_detail_dialog_cells_expose_full_text_tooltips():
    long_summary = "J.P.Morgan Securities PLC | 自有资金 | 新进 | 2026Q1 | 占比0.69% | 变化+104.54"
    dialog = StockDetailDialog(
        "603256",
        "宏和科技",
        [StockSignal(code="603256", source_tab="fund_holdings", signal_type="fund_holding", summary=long_summary)],
    )
    try:
        item = dialog.table.item(0, 2)
        assert item.text() == long_summary
        assert item.toolTip() == long_summary
        assert item.data(Qt.ItemDataRole.ToolTipRole) == long_summary
    finally:
        dialog.close()
