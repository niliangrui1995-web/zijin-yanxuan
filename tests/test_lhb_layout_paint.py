"""Exercise delivered Qt paints after LHB's quote-driven source-row reorder."""

from PyQt6.QtGui import QRegion
from PyQt6.QtTest import QTest

import ui.tabs.lhb_tab as lhb_module
from ui.components.table_controls import VCPTableView
from ui.styles.global_qss import generate_global_qss


def test_lhb_quote_reorder_delivers_one_structural_frame(qt_application, monkeypatch):
    paints = []
    recording = False

    class MeasuredTable(VCPTableView):
        def _screen_width_limit(self):
            # Keep the real stretched last column visible on the offscreen QPA.
            return 2560

        def paintEvent(self, event):
            reason = str((self._pending_paint_metric or {}).get("reason", "other"))
            full = QRegion(self.viewport().rect()).subtracted(event.region()).isEmpty()
            super().paintEvent(event)
            if recording:
                paints.append((reason, full))

    class Settings:
        def contains(self, _key):
            return False

        def setValue(self, *_args):
            pass

        def sync(self):
            pass

    monkeypatch.setattr(lhb_module, "VCPTableView", MeasuredTable)
    monkeypatch.setattr(lhb_module.LhbTab, "_settings_section", lambda self: Settings())
    monkeypatch.setattr(lhb_module.LhbTab, "_should_start_pool_on_show", lambda self: False)
    monkeypatch.setattr(lhb_module.LhbTab, "_prime_visible_local_quote_snapshot", lambda self: None)
    previous_style_sheet = qt_application.styleSheet()
    qt_application.setStyleSheet(generate_global_qss())
    tab = lhb_module.LhbTab(object(), autoload_pool=False)
    try:
        tab.resize(1800, 900)
        tab.model.update_data(
            [
                {
                    "代码": f"{row:06d}",
                    "名称": f"股票{row}",
                    "现价": "10.00",
                    "涨幅%": 0.0,
                    "市值": "30亿",
                    "买点": "",
                    "上榜次数": 3,
                    "最近上榜": "09-04",
                    "上榜净买额(万)": 10000,
                    "机构净买(万)": 5000,
                    "外资净买入": 1000,
                    "换手率%": 3,
                }
                for row in range(42)
            ],
            hydrate_latest_quotes=False,
        )
        tab.table_state.show_table()
        tab.show()
        QTest.qWait(150)
        assert tab.table.horizontalHeader().stretchLastSection()
        recording = True

        tab._apply_quote_snapshot_now(
            {
                f"{row:06d}": {
                    "close": 20 + row / 100,
                    "last_close": 20,
                    "open": 20,
                    "zongguben": 100000000,
                }
                for row in range(42)
            },
            defer_sort=True,
        )
        QTest.qWait(850)

        structural_frames = [
            reason for reason, full in paints
            if full and reason not in {"quote_data_changed", "flash_expiry"}
        ]
        assert structural_frames == ["model_layout_changed"], paints
        assert tab.model.row_data[0]["代码"] == "000041"
        assert any(reason == "flash_expiry" for reason, _full in paints), paints
    finally:
        recording = False
        tab.close()
        tab.deleteLater()
        if qt_application.styleSheet() != previous_style_sheet:
            qt_application.setStyleSheet(previous_style_sheet)
