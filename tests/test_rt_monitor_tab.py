from PyQt6.QtWidgets import QHeaderView

from ui.models.table_models import StockItemDelegate
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
        assert isinstance(tab.table_rt.itemDelegate(), StockItemDelegate)
        assert tab.btn_rt_start.text() == "开始监控"
        assert tab.rt_search.accessibleName() == "盘中监控筛选"
        assert tab.format_status_summary("状态 已启动", "下一步 拉取报价") == "状态 已启动 | 下一步 拉取报价"
    finally:
        tab.deleteLater()


def test_rt_monitor_header_summary_includes_filter_count_and_recent_time(monkeypatch):
    monkeypatch.setattr(
        RtMonitorTab,
        "bind_header_persistence",
        lambda self, table, settings_key="header_state": None,
        raising=False,
    )

    tab = RtMonitorTab(DummyDataProvider(), DummyEngine())
    try:
        tab._set_status("realtime", "第2轮 完成(0.8s)", "30s后第3轮")
        tab._on_scan_count_updated(2, 12)
        tab._do_update_rt_table(
            [
                {
                    "代码": "600519",
                    "名称": "贵州茅台",
                    "现价": "1500.00",
                    "涨幅%": "+2.30%",
                    "市值": "18800亿",
                    "时间": "10:32",
                    "评分": "A",
                    "RPS强度": "95/93",
                    "突破状态": "临近突破",
                    "区间振幅": "12%",
                    "热点板块": "白酒",
                },
                {
                    "代码": "300750",
                    "名称": "宁德时代",
                    "现价": "200.00",
                    "涨幅%": "+1.10%",
                    "市值": "8800亿",
                    "时间": "10:31",
                    "评分": "B",
                    "RPS强度": "90/88",
                    "突破状态": "跟踪中",
                    "区间振幅": "10%",
                    "热点板块": "储能",
                },
            ]
        )
        tab.rt_search.setText("贵州")

        summary = tab.lbl_rt_info.text()
        assert "结果 1/2只" in summary
        assert "筛选 贵州" in summary
        assert "待突破池 12只" in summary
        assert "最近 10:32" in summary
        assert "数据 实时" in summary
        assert "说明 第2轮 完成(0.8s)" in summary
        assert "下一步 30s后第3轮" in summary
    finally:
        tab.deleteLater()
