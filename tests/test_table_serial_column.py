# -*- coding: utf-8 -*-
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor

from ui.models.table_models import RtSortFilterProxyModel, StockTableModel, _qcolor_from_token
from ui.theme import theme_manager


def test_stock_table_model_prepends_serial_header():
    model = StockTableModel(["代码", "名称", "现价"])
    model.update_data([{"代码": "000001", "名称": "平安银行", "现价": "10.00"}])

    assert model.headers[0] == "序号"
    assert model.data(model.index(0, 0), Qt.ItemDataRole.DisplayRole) == "1"
    assert model.get_row_data(0)["序号"] == 1


def test_proxy_serial_column_stays_continuous_after_sort():
    model = StockTableModel(["代码", "名称", "现价"])
    model.update_data(
        [
            {"代码": "000001", "名称": "A", "现价": "10.00"},
            {"代码": "000002", "名称": "B", "现价": "30.00"},
            {"代码": "000003", "名称": "C", "现价": "20.00"},
        ]
    )
    proxy = RtSortFilterProxyModel()
    proxy.setSourceModel(model)

    proxy.sort(model.headers.index("现价"), Qt.SortOrder.DescendingOrder)

    assert [proxy.data(proxy.index(row, 0), Qt.ItemDataRole.DisplayRole) for row in range(proxy.rowCount())] == [
        "1",
        "2",
        "3",
    ]
    assert proxy.data(proxy.index(0, model.headers.index("代码")), Qt.ItemDataRole.DisplayRole) == "000002"


def test_hot_sector_display_keeps_full_text():
    model = StockTableModel(["代码", "名称", "热点板块"])
    full_text = "光通信(15d=100) | CPO概念(15d=96) | 铜连接(20d=93)"
    model.update_data([{"代码": "300308", "名称": "中际旭创", "热点板块": full_text}])

    idx = model.index(0, model.headers.index("热点板块"))
    assert model.data(idx, Qt.ItemDataRole.DisplayRole) == full_text
    assert model.data(idx, Qt.ItemDataRole.ToolTipRole) is not None


def test_terminal_alignment_uses_center_for_numeric_like_cells():
    model = StockTableModel(["代码", "名称", "现价", "时间", "涨幅%"])
    model.update_data([{"代码": "000001", "名称": "平安银行", "现价": "10.00", "时间": "09:35", "涨幅%": "1.23"}])

    serial_align = model.data(model.index(0, 0), Qt.ItemDataRole.TextAlignmentRole)
    price_align = model.data(model.index(0, model.headers.index("现价")), Qt.ItemDataRole.TextAlignmentRole)
    time_align = model.data(model.index(0, model.headers.index("时间")), Qt.ItemDataRole.TextAlignmentRole)
    pct_align = model.data(model.index(0, model.headers.index("涨幅%")), Qt.ItemDataRole.TextAlignmentRole)

    assert serial_align & Qt.AlignmentFlag.AlignCenter.value
    assert price_align & Qt.AlignmentFlag.AlignCenter.value
    assert time_align & Qt.AlignmentFlag.AlignCenter.value
    assert pct_align & Qt.AlignmentFlag.AlignCenter.value


def test_terminal_alignment_uses_left_for_pure_chinese_and_mixed_cells():
    model = StockTableModel(["代码", "名称", "市值", "外资净买入", "买点"])
    model.update_data(
        [
            {
                "代码": "AAPL",
                "名称": "苹果",
                "市值": "734亿",
                "外资净买入": "净买1200万",
                "买点": "触发",
            }
        ]
    )

    code_align = model.data(model.index(0, model.headers.index("代码")), Qt.ItemDataRole.TextAlignmentRole)
    name_align = model.data(model.index(0, model.headers.index("名称")), Qt.ItemDataRole.TextAlignmentRole)
    cap_align = model.data(model.index(0, model.headers.index("市值")), Qt.ItemDataRole.TextAlignmentRole)
    foreign_align = model.data(model.index(0, model.headers.index("外资净买入")), Qt.ItemDataRole.TextAlignmentRole)
    buypoint_align = model.data(model.index(0, model.headers.index("买点")), Qt.ItemDataRole.TextAlignmentRole)

    assert code_align & Qt.AlignmentFlag.AlignLeft.value
    assert name_align & Qt.AlignmentFlag.AlignLeft.value
    assert cap_align & Qt.AlignmentFlag.AlignLeft.value
    assert foreign_align & Qt.AlignmentFlag.AlignLeft.value
    assert buypoint_align & Qt.AlignmentFlag.AlignLeft.value


def test_stock_table_model_groups_market_cap_display_without_mutating_raw_value():
    code_key = "\u4ee3\u7801"
    name_key = "\u540d\u79f0"
    cap_key = "\u5e02\u503c"
    total_cap_key = "\u603b\u5e02\u503c"
    model = StockTableModel([code_key, name_key, cap_key, total_cap_key])
    model.update_data(
        [
            {
                code_key: "000001",
                name_key: "A",
                cap_key: "18800\u4ebf",
                total_cap_key: "1.18\u4e07\u4ebf",
            }
        ],
        hydrate_latest_quotes=False,
    )

    assert model.data(model.index(0, model.headers.index(cap_key)), Qt.ItemDataRole.DisplayRole) == "18,800\u4ebf"
    assert model.data(model.index(0, model.headers.index(total_cap_key)), Qt.ItemDataRole.DisplayRole) == "1.18\u4e07\u4ebf"
    assert model.row_data[0][cap_key] == "18800\u4ebf"


def test_stock_table_model_exposes_heatmap_and_status_badges():
    model = StockTableModel(["代码", "名称", "现价", "涨幅%", "状态"])
    model.update_data([{"代码": "000001", "名称": "平安银行", "现价": "10.00", "涨幅%": "3.20", "状态": "盘中"}])

    price_idx = model.index(0, model.headers.index("现价"))
    status_idx = model.index(0, model.headers.index("状态"))

    assert model.data(price_idx, Qt.ItemDataRole.BackgroundRole) is not None
    assert model.data(status_idx, Qt.ItemDataRole.UserRole + 2) is not None


def test_stock_table_model_exposes_accent_rail_without_row_fill():
    model = StockTableModel(["代码", "名称", "状态"])
    model.update_data([{"代码": "000001", "名称": "A", "状态": "触发", "_row_style": "breakout"}])

    first_idx = model.index(0, 0)

    assert model.data(first_idx, Qt.ItemDataRole.UserRole + 4) == theme_manager.get("COLOR_RISE_STRONG")
    assert model.data(first_idx, Qt.ItemDataRole.BackgroundRole) is None


def test_stock_table_model_keeps_foreign_net_buy_left_aligned():
    model = StockTableModel(["代码", "名称", "外资净买入"])
    model.update_data([{"代码": "000001", "名称": "平安银行", "外资净买入": "净买1200万", "外资净买(万)": 1200}])

    foreign_idx = model.index(0, model.headers.index("外资净买入"))
    foreign_align = model.data(foreign_idx, Qt.ItemDataRole.TextAlignmentRole)

    assert foreign_align & Qt.AlignmentFlag.AlignLeft.value


def test_stock_table_model_foreign_net_buy_display_keeps_full_normalized_text():
    model = StockTableModel(["\u4ee3\u7801", "\u540d\u79f0", "\u5916\u8d44\u51c0\u4e70\u5165"])
    raw_text = "\u51c0\u4e701.4\u4ebf\n\u6df1\u80a1\u901a+1.4\u4ebf\n\u6469\u6839\u58eb\u4e39\u5229+3200\u4e07"
    normalized_text = "\u51c0\u4e701.4\u4ebf | \u6df1\u80a1\u901a+1.4\u4ebf | \u6469\u6839\u58eb\u4e39\u5229+3200\u4e07"
    model.update_data(
        [
            {
                "\u4ee3\u7801": "000001",
                "\u540d\u79f0": "\u5e73\u5b89\u94f6\u884c",
                "\u5916\u8d44\u51c0\u4e70\u5165": raw_text,
            }
        ]
    )

    idx = model.index(0, model.headers.index("\u5916\u8d44\u51c0\u4e70\u5165"))

    assert model.data(idx, Qt.ItemDataRole.DisplayRole) == normalized_text
    assert model.data(idx, Qt.ItemDataRole.ToolTipRole) == raw_text


def test_stock_table_model_dates_use_center_alignment_and_secondary_text():
    model = StockTableModel(["代码", "名称", "日报时间", "交易日期", "揭晓日", "触发日期"])
    model.update_data(
        [
            {
                "代码": "000001",
                "名称": "平安银行",
                "日报时间": "2026-04-13",
                "交易日期": "2026-04-12",
                "揭晓日": "2026-04-30",
                "触发日期": "2026-04-10",
                "_report_ts": 20260413123000,
            }
        ]
    )

    expected_color = QColor(theme_manager.get("TEXT_PRIMARY")).name()
    for header in ("日报时间", "交易日期", "揭晓日", "触发日期"):
        idx = model.index(0, model.headers.index(header))
        align = model.data(idx, Qt.ItemDataRole.TextAlignmentRole)
        foreground = model.data(idx, Qt.ItemDataRole.ForegroundRole)
        assert align & Qt.AlignmentFlag.AlignCenter.value
        assert foreground.name() == expected_color


def test_recent_lhb_date_sort_uses_hidden_raw_value_instead_of_mmdd_text():
    model = StockTableModel(["代码", "名称", "最近上榜"])
    model.update_data(
        [
            {"代码": "000001", "名称": "旧日期", "最近上榜": "04-13", "_最近上榜_raw": "20260413"},
            {"代码": "000002", "名称": "新日期", "最近上榜": "04-14", "_最近上榜_raw": "20260414"},
        ]
    )
    proxy = RtSortFilterProxyModel()
    proxy.setSourceModel(model)

    proxy.sort(model.headers.index("最近上榜"), Qt.SortOrder.DescendingOrder)

    assert proxy.data(proxy.index(0, model.headers.index("代码")), Qt.ItemDataRole.DisplayRole) == "000002"
    assert proxy.data(proxy.index(1, model.headers.index("代码")), Qt.ItemDataRole.DisplayRole) == "000001"


def test_stock_table_model_uses_flat_color_for_zero_pct():
    model = StockTableModel(["代码", "名称", "涨幅%"])
    model.update_data([{"代码": "000001", "名称": "平安银行", "涨幅%": "0.00"}])

    idx = model.index(0, model.headers.index("涨幅%"))
    foreground = model.data(idx, Qt.ItemDataRole.ForegroundRole)

    assert foreground.name() == QColor(theme_manager.get("COLOR_FLAT")).name()


def test_stock_table_model_does_not_badge_catalyst_text():
    long_catalyst = "关注财报催化与平台突破共振，后续还要观察新品发布节奏和北美订单兑现。"
    model = StockTableModel(["代码", "名称", "催化剂", "状态"])
    model.update_data([{"代码": "AAPL", "名称": "Apple", "催化剂": long_catalyst, "状态": "盘中"}])

    catalyst_idx = model.index(0, model.headers.index("催化剂"))
    status_idx = model.index(0, model.headers.index("状态"))

    assert model.data(catalyst_idx, Qt.ItemDataRole.UserRole + 2) is None
    assert model.data(catalyst_idx, Qt.ItemDataRole.DisplayRole) == long_catalyst
    assert model.data(catalyst_idx, Qt.ItemDataRole.ToolTipRole) == long_catalyst
    assert model.data(status_idx, Qt.ItemDataRole.UserRole + 2) is not None


def test_qcolor_from_token_parses_rgba_strings():
    color = _qcolor_from_token("rgba(59, 130, 246, 0.06)")

    assert color.isValid()
    assert color.red() == 59
    assert color.green() == 130
    assert color.blue() == 246
    assert color.alpha() > 0


def test_stock_table_model_plain_style_headers_disable_color_and_heat():
    model = StockTableModel(["代码", "名称", "涨幅%"])
    model.set_plain_style_headers(["涨幅%"])
    model.update_data([{"代码": "000001", "名称": "平安银行", "涨幅%": "6.20"}])

    idx = model.index(0, model.headers.index("涨幅%"))

    assert model.data(idx, Qt.ItemDataRole.UserRole + 3) is True
    assert model.data(idx, Qt.ItemDataRole.ForegroundRole).name() == QColor(theme_manager.get("TEXT_PRIMARY")).name()
    assert model.data(idx, Qt.ItemDataRole.BackgroundRole) is None


def test_buy_point_badge_uses_rise_red():
    model = StockTableModel(["代码", "名称", "买点"])
    model.update_data([{"代码": "000001", "名称": "平安银行", "买点": "触发"}])

    idx = model.index(0, model.headers.index("买点"))
    badge = model.data(idx, Qt.ItemDataRole.UserRole + 2)

    assert QColor(badge).name() == QColor(theme_manager.get("COLOR_RISE_STRONG")).name()
