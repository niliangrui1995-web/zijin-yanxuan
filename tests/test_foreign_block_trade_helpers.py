import datetime

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


def test_block_trade_tab_does_not_join_realtime_quote_universe():
    assert ForeignBlockTradeTab.get_realtime_quote_codes(None) == set()


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


def test_block_trade_exact_filters_support_multi_select():
    model = StockTableModel(["代码", "名称", "交易日期", "交易详情", "买方营业部", "卖方营业部"])
    model.update_data([
        {
            "代码": "600000",
            "名称": "浦发银行",
            "交易日期": "2026-04-10",
            "交易详情": "外资买入",
            "买方营业部": "高盛上海营业部",
            "卖方营业部": "普通营业部",
        },
        {
            "代码": "000001",
            "名称": "平安银行",
            "交易日期": "2026-04-11",
            "交易详情": "外资卖出",
            "买方营业部": "普通营业部",
            "卖方营业部": "瑞银证券上海浦东新区营业部",
        },
        {
            "代码": "000002",
            "名称": "万科A",
            "交易日期": "2026-04-12",
            "交易详情": "外资对倒",
            "买方营业部": "高盛上海营业部",
            "卖方营业部": "瑞银证券上海浦东新区营业部",
        },
    ])

    proxy = BlockTradeFilterProxyModel()
    proxy.setSourceModel(model)

    proxy.setExactFilters("交易日期", {"2026-04-10", "2026-04-12"})
    assert proxy.rowCount() == 2

    proxy.setExactFilters("交易详情", {"外资买入", "外资对倒"})
    assert proxy.rowCount() == 2

    proxy.setExactFilters("_branch", {"高盛", "瑞银"})
    assert proxy.rowCount() == 2


def test_should_trigger_auto_refresh_only_after_20_on_trade_day():
    now = datetime.datetime(2026, 4, 20, 20, 5, 0)
    before_20 = datetime.datetime(2026, 4, 20, 19, 59, 0)
    assert not ForeignBlockTradeTab._should_trigger_auto_refresh(
        before_20,
        is_trade_day=True,
        last_auto_refresh_date="",
        last_success_at=datetime.datetime(2026, 4, 20, 14, 30, 0),
        pending_auto_refresh_date="",
    )
    assert ForeignBlockTradeTab._should_trigger_auto_refresh(
        now,
        is_trade_day=True,
        last_auto_refresh_date="",
        last_success_at=datetime.datetime(2026, 4, 20, 14, 30, 0),
        pending_auto_refresh_date="",
    )


def test_should_trigger_auto_refresh_skips_when_today_after_20_already_saved_or_pending():
    now = datetime.datetime(2026, 4, 20, 20, 5, 0)
    assert not ForeignBlockTradeTab._should_trigger_auto_refresh(
        now,
        is_trade_day=True,
        last_auto_refresh_date="",
        last_success_at=datetime.datetime(2026, 4, 20, 20, 1, 0),
        pending_auto_refresh_date="",
    )
    assert not ForeignBlockTradeTab._should_trigger_auto_refresh(
        now,
        is_trade_day=True,
        last_auto_refresh_date="20260420",
        last_success_at=None,
        pending_auto_refresh_date="",
    )
    assert not ForeignBlockTradeTab._should_trigger_auto_refresh(
        now,
        is_trade_day=True,
        last_auto_refresh_date="",
        last_success_at=None,
        pending_auto_refresh_date="20260420",
    )


def test_extract_cache_filter_options_and_save_gate():
    dates, branches = ForeignBlockTradeTab._extract_cache_filter_options(
        [
            {"交易日期": "2026-04-18", "买方营业部": "高盛上海营业部", "卖方营业部": "普通营业部"},
            {"交易日期": "2026-04-20", "买方营业部": "普通营业部", "卖方营业部": "瑞银证券上海浦东新区营业部"},
            {"交易日期": "2026-04-19", "买方营业部": "机构专用", "卖方营业部": "普通营业部"},
        ]
    )
    assert dates == ["2026-04-20", "2026-04-19", "2026-04-18"]
    assert branches == ["瑞银证券上海浦东新区营业部", "高盛上海营业部"]
    assert ForeignBlockTradeTab._should_save_cache([], [])
    assert not ForeignBlockTradeTab._should_save_cache(["20260420-20260420"], [])
