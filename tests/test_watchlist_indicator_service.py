from __future__ import annotations

from app.services import watchlist_indicator_service as service
from core.exceptions import CacheIOError


def test_watchlist_indicator_service_builds_display_rows_and_persistence_patch():
    radar_data = (
        {"000001": "关注原因"},
        {"000001": "半导体"},
        {"000001": {"text": "大宗买入", "amount_wan": 1200}},
        {"000001": {"text": "业绩向上", "qoq_pct": 15.5}},
        {"000001": {"buy_point": "B", "date": "2026-08-25", "net_wan": 88}},
        {"rps120": {"000001": 80}, "rps250": {"000001": 95}},
    )

    results = service.build_watchlist_indicator_results([(0, "000001"), (1, "000002")], radar_data=radar_data)

    assert results == {
        "000001": {
            "rps": "95/80",
            "subsector": "半导体",
            "remark": "关注原因",
            "block_trade": "大宗买入",
            "block_trade_amount_wan": 1200,
            "earnings": "业绩向上",
            "earnings_qoq_pct": 15.5,
            "lhb": {"buy_point": "B", "date": "2026-08-25", "net_wan": 88},
        },
        "000002": {
            "rps": "--",
            "subsector": "",
            "remark": "",
            "block_trade": "",
            "block_trade_amount_wan": "",
            "earnings": "",
            "earnings_qoq_pct": "",
            "lhb": "",
        },
    }
    assert service.build_watchlist_metric_patch(results, buy_point_text="买点") == {
        "000001": {
            "RPS强度": "95/80",
            "备注": "关注原因",
            "大宗交易": "大宗买入",
            "大宗交易金额(万)": 1200,
            "业绩异动": "业绩向上",
            "业绩环比%": 15.5,
            "细分板块": "半导体",
            "龙虎榜": "买点",
            "龙虎榜日期": "2026-08-25",
            "龙虎榜净额(万)": 88,
        },
        "000002": {
            "RPS强度": "--",
            "备注": "",
            "大宗交易": "",
            "大宗交易金额(万)": "",
            "业绩异动": "",
            "业绩环比%": "",
            "龙虎榜": "",
            "龙虎榜日期": "",
            "龙虎榜净额(万)": "",
        },
    }


def test_watchlist_indicator_service_handles_missing_or_cancelled_input_without_loading_data():
    class _Cancelled:
        cancelled = True

    def unavailable_loader():
        raise CacheIOError("cache unavailable")

    assert service.build_watchlist_indicator_results(None) is None
    assert service.build_watchlist_indicator_results(
        [(0, "000001")],
        radar_data=({}, {}, {}, {}, {}),
        rps_loader=unavailable_loader,
        cancellation_token=_Cancelled(),
    ) == {}
