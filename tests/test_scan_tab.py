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
