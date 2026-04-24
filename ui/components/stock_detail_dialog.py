# -*- coding: utf-8 -*-
from __future__ import annotations

from collections.abc import Callable, Iterable

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QDialog,
    QHeaderView,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from app.services.ui_runtime_service import ui_signals, watchlist_vm
from ui.theme import theme_manager
from ui.theme_tokens import build_ui_tokens
from ui.workspaces.stock_signal import StockSignal

SOURCE_LABELS = {
    "na_daily": "北美战报",
    "ai_industry_chain": "AI产业链",
    "foreign_block": "大宗交易",
    "earnings": "业绩异动",
    "lhb": "龙虎榜",
    "fund_holdings": "基金持仓",
    "scan": "VCP扫描",
    "watchlist": "关注池",
    "rt_monitor": "盘中监控",
}

SIGNAL_LABELS = {
    "catalyst": "催化剂",
    "subsector": "细分板块",
    "block_trade": "大宗交易",
    "earnings": "业绩异动",
    "lhb": "龙虎榜",
    "vcp_scan": "VCP扫描",
    "fund_holding": "基金持仓",
}


def signal_source_label(signal: StockSignal, tab_titles: dict[str, str] | None = None) -> str:
    source_key = str(signal.source_tab or signal.source_label or "").strip()
    tab_titles = tab_titles or {}
    return str(tab_titles.get(source_key) or SOURCE_LABELS.get(source_key) or source_key or "--")


def signal_type_label(signal: StockSignal) -> str:
    signal_type = str(signal.signal_type or "").strip()
    return SIGNAL_LABELS.get(signal_type, signal_type or "--")


def format_signal_value(signal: StockSignal) -> str:
    value = signal.numeric_value
    if value is None:
        return ""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return str(value)
    if abs(number) >= 100:
        return f"{number:,.0f}"
    return f"{number:.2f}".rstrip("0").rstrip(".")


def build_signal_rows(
    signals: Iterable[StockSignal],
    *,
    tab_titles: dict[str, str] | None = None,
) -> list[dict]:
    rows = []
    for signal in signals or []:
        rows.append(
            {
                "source": signal_source_label(signal, tab_titles),
                "type": signal_type_label(signal),
                "summary": str(signal.summary or "").strip(),
                "value": format_signal_value(signal),
                "time": str(signal.observed_at or signal.refreshed_at or "").strip(),
                "signal": signal,
            }
        )
    return rows


class StockDetailDialog(QDialog):
    def __init__(
        self,
        code: str,
        name: str,
        signals: Iterable[StockSignal],
        *,
        tab_titles: dict[str, str] | None = None,
        activate_callback: Callable[[StockSignal], bool] | None = None,
        context: dict | None = None,
        parent: QWidget | None = None,
    ):
        super().__init__(parent)
        self._code = str(code or "").strip()
        self._name = str(name or "").strip()
        self._rows = build_signal_rows(signals, tab_titles=tab_titles)
        self._activate_callback = activate_callback
        self._context = dict(context or {})

        self.setWindowTitle(f"股票全景 - {self._name or self._code}")
        self.resize(820, 460)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 14, 16, 14)
        layout.setSpacing(12)

        header_layout = QHBoxLayout()
        header_layout.setContentsMargins(0, 0, 0, 0)
        header_layout.setSpacing(8)

        self.lbl_title = QLabel(self._build_title(), self)
        self.lbl_title.setObjectName("stockDetailTitle")
        header_layout.addWidget(self.lbl_title, 1)

        self.lbl_meta = QLabel(self._build_meta(), self)
        self.lbl_meta.setObjectName("stockDetailMeta")
        self.lbl_meta.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        header_layout.addWidget(self.lbl_meta)
        layout.addLayout(header_layout)

        self.table = QTableWidget(self)
        self.table.setObjectName("stockDetailTable")
        self.table.setColumnCount(5)
        self.table.setHorizontalHeaderLabels(["来源", "类型", "摘要", "数值", "时间"])
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setAlternatingRowColors(True)
        self.table.itemSelectionChanged.connect(self._sync_actions)
        self.table.cellDoubleClicked.connect(lambda _row, _col: self._activate_selected_source())
        self._populate_table()
        layout.addWidget(self.table, 1)

        action_layout = QHBoxLayout()
        action_layout.setContentsMargins(0, 0, 0, 0)

        self.btn_kline = QPushButton("打开K线", self)
        self.btn_kline.clicked.connect(self._open_kline)
        action_layout.addWidget(self.btn_kline)

        self.btn_watchlist = QPushButton(self._watchlist_button_text(), self)
        self.btn_watchlist.clicked.connect(self._toggle_watchlist)
        action_layout.addWidget(self.btn_watchlist)

        action_layout.addStretch(1)

        self.btn_activate = QPushButton("定位来源", self)
        self.btn_activate.clicked.connect(self._activate_selected_source)
        action_layout.addWidget(self.btn_activate)

        self.btn_close = QPushButton("关闭", self)
        self.btn_close.clicked.connect(self.accept)
        action_layout.addWidget(self.btn_close)
        layout.addLayout(action_layout)

        self._apply_theme()
        self._sync_actions()

    def _build_title(self) -> str:
        if self._name and self._name != self._code:
            return f"{self._name}  {self._code}"
        return self._code or "股票全景"

    def _build_meta(self) -> str:
        sources = {str(row.get("source") or "") for row in self._rows if row.get("source")}
        return f"{len(self._rows)} 条信号 | {len(sources)} 个来源"

    def _detail_payload(self) -> dict:
        payload = dict(self._context)
        payload.setdefault("代码", self._code)
        payload.setdefault("名称", self._name)
        return payload

    def _watchlist_button_text(self) -> str:
        return "移出关注池" if watchlist_vm.is_in_watchlist(self._code) else "加入关注池"

    def _populate_table(self) -> None:
        self.table.setRowCount(max(1, len(self._rows)))
        if not self._rows:
            item = QTableWidgetItem("暂无跨 Tab 信号")
            item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self.table.setSpan(0, 0, 1, 5)
            self.table.setItem(0, 0, item)
        else:
            for row_idx, row in enumerate(self._rows):
                values = [
                    row["source"],
                    row["type"],
                    row["summary"],
                    row["value"],
                    row["time"],
                ]
                for col_idx, value in enumerate(values):
                    item = QTableWidgetItem(str(value or ""))
                    item.setData(Qt.ItemDataRole.UserRole, row["signal"])
                    if col_idx in (3, 4):
                        item.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
                    self.table.setItem(row_idx, col_idx, item)

        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(4, QHeaderView.ResizeMode.ResizeToContents)

    def selected_signal(self) -> StockSignal | None:
        row = self.table.currentRow()
        if row < 0 or row >= len(self._rows):
            return None
        item = self.table.item(row, 0)
        signal = item.data(Qt.ItemDataRole.UserRole) if item is not None else None
        return signal if isinstance(signal, StockSignal) else None

    def _sync_actions(self) -> None:
        self.btn_activate.setEnabled(self.selected_signal() is not None and self._activate_callback is not None)
        self.btn_kline.setEnabled(bool(self._code))
        self.btn_watchlist.setEnabled(bool(self._code))
        self.btn_watchlist.setText(self._watchlist_button_text())

    def _activate_selected_source(self) -> None:
        signal = self.selected_signal()
        if signal is None or self._activate_callback is None:
            return
        self._activate_callback(signal)

    def _open_kline(self) -> None:
        if not self._code:
            return
        ui_signals.sig_show_kline_with_list.emit(self._code, [self._detail_payload()], 0)

    def _toggle_watchlist(self) -> None:
        if not self._code:
            return
        clean_name = self._name.replace("⭐ ", "").replace("★ ", "")
        watchlist_vm.toggle_stock(self._code, clean_name, self._detail_payload())
        self._sync_actions()

    def _apply_theme(self) -> None:
        theme = theme_manager.current_theme
        tokens = build_ui_tokens(theme)
        self.setStyleSheet(
            f"""
            QDialog {{
                background: {tokens["surface"]["panel"]};
                color: {theme["TEXT_PRIMARY"]};
            }}
            QLabel#stockDetailTitle {{
                color: {theme["TEXT_PRIMARY"]};
                font-size: {tokens["font"]["size_xl"]}px;
                font-weight: {tokens["font"]["weight_semibold"]};
            }}
            QLabel#stockDetailMeta {{
                color: {theme["TEXT_SECONDARY"]};
                font-size: {tokens["font"]["size_sm"]}px;
            }}
            QTableWidget#stockDetailTable {{
                background: {theme["BG_TABLE_BASE"]};
                alternate-background-color: {theme["BG_TABLE_ALT_ROW"]};
                color: {theme["TEXT_PRIMARY"]};
                gridline-color: {theme["BORDER_SUBTLE"]};
                border: 1px solid {theme["BORDER_DEFAULT"]};
                border-radius: {tokens["radius"]["sm"]}px;
                selection-background-color: {theme["SELECTION_BG"]};
            }}
            QHeaderView::section {{
                background: {tokens["surface"]["toolbar"]};
                color: {theme["TEXT_HEADER"]};
                border: none;
                border-bottom: 1px solid {theme["BORDER_DEFAULT"]};
                padding: 4px 8px;
                font-size: {tokens["font"]["size_sm"]}px;
            }}
            QPushButton {{
                min-height: {tokens["control"]["button_height"]}px;
                padding: 0 {tokens["control"]["button_padding_x"]}px;
                border-radius: {tokens["radius"]["sm"]}px;
                border: 1px solid {theme["BORDER_DEFAULT"]};
                background: {theme["BG_BUTTON"]};
                color: {theme["TEXT_PRIMARY"]};
            }}
            QPushButton:hover {{
                background: {theme["BG_BUTTON_HOVER"]};
            }}
            QPushButton:disabled {{
                color: {theme["TEXT_DISABLED"]};
            }}
            """
        )
