# -*- coding: utf-8 -*-
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QWidget

from ui import kline_window_qt as kline_module


class _DummyProvider:
    _offline = True


def test_kline_header_action_controls_share_same_height(monkeypatch):
    monkeypatch.setattr(kline_module, "QWebEngineView", QWidget)
    monkeypatch.setattr(kline_module.KLineChartWindow, "_load_and_draw", lambda self: None)
    monkeypatch.setattr(
        kline_module.KLineChartWindow,
        "_check_fav_status",
        lambda self: setattr(self, "is_fav", False),
    )

    window = kline_module.KLineChartWindow(
        None,
        "000001",
        "平安银行",
        _DummyProvider(),
        vcp_data={},
        code_list=[{"代码": "000001", "名称": "平安银行"}],
        current_idx=0,
    )
    try:
        action_widgets = (
            window.btn_prev,
            window.nav_index_lbl,
            window.btn_next,
            window.btn_fav,
        )
        heights = {widget.minimumHeight() for widget in action_widgets}
        max_heights = {widget.maximumHeight() for widget in action_widgets}

        assert len(heights) == 1
        assert len(max_heights) == 1
        assert all(height > 0 for height in heights)
        assert window.btn_prev.focusPolicy() == Qt.FocusPolicy.NoFocus
        assert window.btn_next.focusPolicy() == Qt.FocusPolicy.NoFocus
        assert window.btn_fav.focusPolicy() == Qt.FocusPolicy.NoFocus
    finally:
        window.deleteLater()
