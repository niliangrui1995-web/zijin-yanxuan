# -*- coding: utf-8 -*-
from PyQt6.QtWidgets import QApplication

from ui.tabs.scan_tab import ScanTab


def test_scan_tab_idle_status_summary_is_not_blank(monkeypatch):
    app = QApplication.instance() or QApplication([])
    monkeypatch.setattr("ui.tabs.scan_tab.QTimer.singleShot", lambda *_args, **_kwargs: None)

    tab = ScanTab(data_provider=None, engine=None)
    try:
        assert tab.lbl_scan_status.text()
        assert "RPS" in tab.lbl_scan_status.text()
    finally:
        tab.deleteLater()


def test_merge_scan_results_prefers_newer_trade_date(monkeypatch):
    app = QApplication.instance() or QApplication([])
    monkeypatch.setattr("ui.tabs.scan_tab.QTimer.singleShot", lambda *_args, **_kwargs: None)

    tab = ScanTab(data_provider=None, engine=None)
    try:
        merged, stats = tab._merge_scan_results(
            [
                {"代码": "000001", "名称": "平安银行", "触发日期": "2026-04-11", "评分": 81},
                {"代码": "000002", "名称": "万科A", "触发日期": "2026-04-10", "评分": 77},
            ],
            [
                {"代码": "000001", "名称": "平安银行", "触发日期": "2026-04-14", "评分": 92},
                {"代码": "000004", "名称": "国华网安", "触发日期": "2026-04-14", "评分": 88},
            ],
        )

        merged_map = {row["代码"]: row for row in merged}
        assert merged_map["000001"]["触发日期"] == "2026-04-14"
        assert merged_map["000001"]["评分"] == 92
        assert merged_map["000004"]["名称"] == "国华网安"
        assert stats["新增"] == 1
        assert stats["更新"] == 1
        assert stats["原始命中"] == 2
    finally:
        tab.deleteLater()


def test_merge_scan_results_keeps_existing_rows_when_incremental_scan_has_no_hits(monkeypatch):
    app = QApplication.instance() or QApplication([])
    monkeypatch.setattr("ui.tabs.scan_tab.QTimer.singleShot", lambda *_args, **_kwargs: None)

    tab = ScanTab(data_provider=None, engine=None)
    try:
        base_rows = [
            {"代码": "000001", "名称": "平安银行", "触发日期": "2026-04-11", "评分": 81},
            {"代码": "000002", "名称": "万科A", "触发日期": "2026-04-10", "评分": 77},
        ]
        merged, stats = tab._merge_scan_results(base_rows, [])

        assert merged == base_rows
        assert stats["原始命中"] == 0
        assert stats["新增"] == 0
        assert stats["更新"] == 0
        assert stats["刷新"] == 0
    finally:
        tab.deleteLater()
