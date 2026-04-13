# -*- coding: utf-8 -*-
from ui.tabs import asian_market_tab as asian_module


class _Signal:
    def connect(self, _callback):
        return None


class _DummyWorker:
    def __init__(self, codes):
        self.codes = list(codes)
        self.progress = _Signal()
        self.result_ready = _Signal()

    def start(self):
        return None

    def isRunning(self):
        return False

    def stop(self):
        return None

    def wait(self, _timeout=0):
        return True

    def trigger_refresh(self):
        return None


def test_asian_market_table_scales_columns_to_fill_view(monkeypatch):
    monkeypatch.setattr(asian_module, "AsianMarketWorker", _DummyWorker)
    monkeypatch.setattr(
        asian_module.AsianMarketTab,
        "_load_local_cache",
        lambda self: setattr(self, "row_data", []),
    )
    monkeypatch.setattr(asian_module.AsianMarketTab, "_check_auto_cache", lambda self: None)
    monkeypatch.setattr(
        asian_module.AsianMarketTab,
        "bind_header_persistence",
        lambda self, table, settings_key="header_state": None,
        raising=False,
    )

    tab = asian_module.AsianMarketTab()
    try:
        tab.resize(1200, 720)
        tab.show()
        tab._fit_asian_columns_to_viewport()

        column_count = tab.asian_table.model().columnCount()
        initial_widths = [52, 70, 140, 90, 90, 80, 80, 120, 250, 60, 80, 80, 80]
        total_initial = sum(initial_widths)
        total_scaled = sum(tab.asian_table.columnWidth(i) for i in range(column_count))
        viewport_width = tab.asian_table.viewport().width()

        assert column_count == len(initial_widths)
        assert abs(total_scaled - viewport_width) <= column_count

        scaled_ratio = tab.asian_table.columnWidth(8) / tab.asian_table.columnWidth(1)
        initial_ratio = initial_widths[8] / initial_widths[1]
        assert abs(scaled_ratio - initial_ratio) < 0.2
        assert total_scaled != total_initial
    finally:
        tab.deleteLater()


def test_asian_market_status_column_uses_plain_style(monkeypatch):
    monkeypatch.setattr(asian_module, "AsianMarketWorker", _DummyWorker)
    monkeypatch.setattr(
        asian_module.AsianMarketTab,
        "_load_local_cache",
        lambda self: setattr(self, "row_data", []),
    )
    monkeypatch.setattr(asian_module.AsianMarketTab, "_check_auto_cache", lambda self: None)
    monkeypatch.setattr(
        asian_module.AsianMarketTab,
        "bind_header_persistence",
        lambda self, table, settings_key="header_state": None,
        raising=False,
    )

    tab = asian_module.AsianMarketTab()
    try:
        assert "状态" in tab.model._plain_style_headers
    finally:
        tab.deleteLater()
