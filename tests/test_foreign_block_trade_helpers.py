import pandas as pd

from ui.models.table_models import StockTableModel
from ui.tabs.foreign_block_trade_tab import (
    BlockTradeFilterProxyModel,
    ForeignBlockTradeTab,
    _normalize_trade_date_series,
    _normalize_trade_date_value,
)


def test_normalize_trade_date_value_handles_epoch_ms():
    assert _normalize_trade_date_value("1775779200000") == "2026-04-10"


def test_normalize_trade_date_series_handles_iso_and_plain_text():
    series = pd.Series(["2026-04-10T00:00:00.000", "20260411", "2026-04-08"])
    result = _normalize_trade_date_series(series).tolist()
    assert result == ["2026-04-10", "2026-04-11", "2026-04-08"]


def test_should_include_row_only_matches_foreign_branches():
    assert ForeignBlockTradeTab._should_include_row(None, "高盛上海营业部", "普通营业部")
    assert ForeignBlockTradeTab._should_include_row(None, "普通营业部", "瑞银证券上海浦东新区营业部")
    assert not ForeignBlockTradeTab._should_include_row(None, "机构专用", "普通营业部")


def test_determine_direction_keeps_only_foreign_actions():
    assert ForeignBlockTradeTab._determine_direction(None, "高盛上海营业部", "普通营业部")[0] == "外资买入"
    assert ForeignBlockTradeTab._determine_direction(None, "普通营业部", "瑞银证券上海浦东新区营业部")[0] == "外资卖出"
    assert ForeignBlockTradeTab._determine_direction(None, "高盛上海营业部", "瑞银证券上海浦东新区营业部")[0] == "外资对倒"
    assert ForeignBlockTradeTab._determine_direction(None, "机构专用", "普通营业部")[0] == "--"


def test_block_trade_search_only_matches_code_name_and_foreign_branch():
    model = StockTableModel(["代码", "名称", "交易详情", "买方营业部", "卖方营业部"])
    model.update_data([
        {
            "代码": "600000",
            "名称": "浦发银行",
            "交易详情": "外资买入",
            "买方营业部": "高盛上海营业部",
            "卖方营业部": "普通营业部",
        }
    ])

    proxy = BlockTradeFilterProxyModel()
    proxy.setSourceModel(model)

    proxy.setFilterText("高盛")
    assert proxy.rowCount() == 1

    proxy.setFilterText("浦发")
    assert proxy.rowCount() == 1

    proxy.setFilterText("买入")
    assert proxy.rowCount() == 0
