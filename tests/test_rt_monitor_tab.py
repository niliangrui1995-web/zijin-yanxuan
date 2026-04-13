from PyQt6.QtWidgets import QHeaderView

from ui.tabs.rt_monitor_tab import RtMonitorTab


class DummyDataProvider:
    pass


class DummyEngine:
    pass


def test_rt_monitor_last_columns_are_interactive(monkeypatch):
    captured = {}

    def fake_bind_header_persistence(self, table, settings_key="header_state"):
        captured["settings_key"] = settings_key

    monkeypatch.setattr(
        RtMonitorTab,
        "bind_header_persistence",
        fake_bind_header_persistence,
        raising=False,
    )

    tab = RtMonitorTab(DummyDataProvider(), DummyEngine())
    try:
        header = tab.table_rt.horizontalHeader()
        for column_name in ("区间振幅", "热点板块"):
            column = tab.source_model.headers.index(column_name)
            assert header.sectionResizeMode(column) == QHeaderView.ResizeMode.Interactive

        assert captured["settings_key"] == "header_state_rt_v5"
        assert tab.table_rt.columnWidth(tab.source_model.headers.index("热点板块")) >= 220
        assert tab.btn_rt_start.text() == "启动监控"
        assert tab._format_status_text("已启动", "拉取报价") == "状态 已启动 | 下一步 拉取报价"
    finally:
        tab.deleteLater()
