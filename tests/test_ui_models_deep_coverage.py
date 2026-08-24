# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import time
from contextlib import contextmanager
from dataclasses import replace

import pytest
from PyQt6.QtCore import QMimeData, QModelIndex, QRect, Qt
from PyQt6.QtGui import QColor, QFont, QImage, QPainter, QPalette, QStandardItemModel
from PyQt6.QtTest import QAbstractItemModelTester
from PyQt6.QtWidgets import QStyle, QStyleOptionViewItem, QWidget

from ui.models import rt_table_model as rt_module
from ui.models import stock_table_model as stock_module
from ui.models import table_cell_renderers as renderers
from ui.models import table_model_helpers as helpers
from ui.models.rt_table_model import RtTableModel
from ui.models.stock_table_model import StockTableModel
from ui.models.table_model_views import RtSortFilterProxyModel, StockItemDelegate


def _stock(headers, rows=()):
    model = StockTableModel(headers)
    model.update_data(list(rows), hydrate_latest_quotes=False)
    return model


def _valid_color(value) -> bool:
    return isinstance(value, QColor) and value.isValid()


def test_table_models_obey_qt_model_contract_during_mutations():
    stock = _stock(
        ["代码", "名称", "现价", "涨幅%"],
        [{"代码": "000001", "名称": "A", "现价": "10", "涨幅%": "1"}],
    )
    realtime = RtTableModel(
        [{"\u4ee3\u7801": "000001", "\u540d\u79f0": "A", "\u73b0\u4ef7": "10", "\u6da8\u5e45%": "1"}]
    )

    stock_tester = QAbstractItemModelTester(stock, QAbstractItemModelTester.FailureReportingMode.Warning)
    realtime_tester = QAbstractItemModelTester(realtime, QAbstractItemModelTester.FailureReportingMode.Warning)

    stock.update_data(
        [{"代码": "000001", "名称": "A2", "现价": "11", "涨幅%": "2"}],
        hydrate_latest_quotes=False,
    )
    realtime.update_data([{"\u4ee3\u7801": "000001", "\u540d\u79f0": "A2", "\u73b0\u4ef7": "11", "\u6da8\u5e45%": "2"}])

    assert stock_tester.model() is stock
    assert realtime_tester.model() is realtime


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (None, None),
        ("", None),
        ("--", None),
        ("abc", None),
        ("+1,234.5", 1234.5),
        ("-2.5万", -25000.0),
        ("1.2亿", 120000000.0),
    ],
)
def test_numeric_parser_covers_placeholders_units_and_signs(raw, expected):
    assert helpers._parse_numeric_value(raw) == expected


def test_color_and_font_helpers_cover_supported_token_formats():
    rgb = helpers._qcolor_from_token("rgb(1, 2, 3)")
    rgba_fraction = helpers._qcolor_from_token("rgba(4, 5, 6, 0.5)")
    rgba_byte = helpers._qcolor_from_token("rgba(7, 8, 9, 300)")
    copied = helpers._qcolor_from_token(rgb)

    assert (rgb.red(), rgb.green(), rgb.blue()) == (1, 2, 3)
    assert 126 <= rgba_fraction.alpha() <= 129
    assert rgba_byte.alpha() == 255
    assert copied.name() == rgb.name()
    assert not helpers._qcolor_from_token(None).isValid()
    assert not helpers._qcolor_from_token("  ").isValid()

    integer_font = helpers._build_qfont(["Segoe UI"], 12, bold=True, mono=True)
    fractional_font = helpers._build_qfont(["Segoe UI"], 11.5)
    fonts = helpers._build_table_model_fonts()
    assert integer_font.bold() and integer_font.fixedPitch() and integer_font.pointSize() == 12
    assert fractional_font.pointSizeF() == pytest.approx(11.5)
    assert {"base", "small", "mono", "small_mono", "bold", "bold_mono"} == set(fonts)


def test_apply_quote_metrics_updates_only_present_quote_columns(monkeypatch):
    metrics = {
        "zongguben": 1000,
        "price_text": "12.34",
        "pct": 3.5,
        "market_cap_text": "12.34亿",
    }
    monkeypatch.setattr(helpers, "resolve_quote_metrics", lambda _row, _quote: metrics)
    row = {"现价": "10", "市价": "10", "涨幅%": 1, "涨幅": 1, "市值": "10亿"}

    changed, returned_metrics = helpers._apply_quote_metrics_to_row(row, {"close": 12.34})
    unchanged, _ = helpers._apply_quote_metrics_to_row(row, {"close": 12.34})

    assert changed is True
    assert unchanged is False
    assert returned_metrics is metrics
    assert row == {
        "现价": "12.34",
        "市价": "12.34",
        "涨幅%": 3.5,
        "涨幅": 3.5,
        "市值": "12.34亿",
        "total_shares": 1000.0,
        "_zongguben": 1000.0,
    }


@pytest.mark.parametrize(
    ("header", "raw", "expected"),
    [
        ("名称", "12345", None),
        ("市值", "", ""),
        ("市值", "--", "--"),
        ("市值", "123亿", "123亿"),
        ("市值", "1234亿", "1,234亿"),
        ("总市值", "-1234567.80万", "-1,234,567.80万"),
    ],
)
def test_market_cap_formatting_is_header_scoped_and_non_destructive(header, raw, expected):
    assert helpers._format_market_cap_display(header, raw) == expected


def test_flash_helpers_cover_filtering_direction_expiry_and_cleanup(monkeypatch):
    assert helpers._build_flash_record("序号", 1, 2, now=10) is None
    assert helpers._build_flash_record("备注", "A", "B", now=10) is None
    assert helpers._build_flash_record("状态", "A", "A", now=10) is None
    assert helpers._build_flash_record("现价", "bad", "worse", now=10) == {"time": 10, "diff": 0.0}
    assert helpers._build_flash_record("现价", "10", "12", now=10) == {"time": 10, "diff": 2.0}
    assert helpers._build_flash_record("最近时间", "10", "9", now=10) == {"time": 10, "diff": -1.0}

    records = {
        0: {1: {"time": 9.8, "diff": 1}},
        1: {2: {"time": 1.0, "diff": -1}},
        2: "broken",
    }
    helpers._prune_flash_records(records, now=10.0)
    assert records == {0: {1: {"time": 9.8, "diff": 1}}}

    monkeypatch.setattr(helpers.time, "time", lambda: 10.0)
    assert helpers._active_flash_record(records, 0, 1)["diff"] == 1
    assert helpers._active_flash_record(records, 9, 9) is None
    assert helpers._active_flash_record({0: {1: {"time": 1}}}, 0, 1) is None


@pytest.mark.parametrize(
    ("header", "expected"),
    [
        ("", False),
        ("序号", False),
        ("现价", True),
        ("自定义状态", True),
        ("备注", False),
    ],
)
def test_flash_header_classification(header, expected):
    assert helpers._should_flash_header(header) is expected


def test_header_classification_and_alignment_boundaries():
    assert helpers._is_status_header("市场")
    assert not helpers._is_status_header("备注")
    assert helpers._is_date_like_header("揭晓日")
    assert helpers._is_date_like_header("更新时间")
    assert not helpers._is_date_like_header("名称")
    assert helpers._is_numeric_header("PE")
    assert helpers._is_numeric_header("RPS强度")
    assert not helpers._is_numeric_header("序号")
    assert not helpers._is_numeric_header("细分板块")
    assert helpers._is_pct_like_header("涨跌")
    assert not helpers._is_pct_like_header("名称")

    centered = int(Qt.AlignmentFlag.AlignCenter | Qt.AlignmentFlag.AlignVCenter)
    left = int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
    assert helpers._alignment_for_cell("序号", "A") == centered
    assert helpers._alignment_for_cell("现价", "--") == centered
    assert helpers._alignment_for_cell("名称", "--") == left
    assert helpers._alignment_for_cell("日报时间", "2026-07-14 10:30") == centered
    assert helpers._alignment_for_cell("名称", "中际旭创") == left
    assert helpers._alignment_for_cell("外资净买入", "1.4亿") == left
    assert helpers._is_numeric_like_text("+12.5%")
    assert not helpers._is_numeric_like_text("")
    assert not helpers._is_numeric_like_text("十二")


def test_strong_move_and_heatmap_thresholds_cover_all_bands():
    assert helpers._strong_market_pct_from_row(None) is None
    assert helpers._strong_market_pct_from_row({"涨幅%": "bad", "涨跌": "9.1"}) == 9.1
    assert helpers._is_strong_market_move("涨幅%", "9.0")
    assert not helpers._is_strong_market_move("换手率%", "12")
    assert helpers._is_strong_market_move("现价", "10", {"涨幅%": "-9.2"})
    assert not helpers._is_strong_market_move("名称", "9.2")

    for header, raw in (
        ("5日涨跌%", "1"),
        ("5日涨跌%", "-1"),
        ("现价", "2"),
        ("评分", "95"),
        ("评分", "85"),
        ("评分", "65"),
        ("RPS强度", "96"),
        ("RPS强度", "86"),
    ):
        assert _valid_color(helpers._numeric_heat_color(header, raw))

    for header, raw in (
        ("5日涨跌%", "0"),
        ("现价", "0"),
        ("评分", "59"),
        ("RPS强度", "84"),
        ("名称", "100"),
        ("评分", "bad"),
    ):
        assert helpers._numeric_heat_color(header, raw) is None


def test_tooltip_summary_status_and_row_accent_helpers():
    row = {"_\u5916\u8d44\u51c0\u4e70\u5165_tooltip": "custom", "\u72b6\u6001": "\u89e6\u53d1"}
    assert helpers._tooltip_for_cell("序号", "1", row) is None
    assert helpers._tooltip_for_cell("外资净买入", "raw", row) == "custom"
    assert helpers._tooltip_for_cell("名称", " A ", row) == "A"
    assert helpers._build_cell_tooltip_cached("") is None
    assert helpers._summarize_long_text("备注", "A\nB") == "A | B"
    assert helpers._summarize_long_text("备注", " A \r\n \r\n B ") == "A | B"
    assert helpers._tooltip_for_cell("备注", "A\n\nB", row) == "A\n\nB"
    assert helpers._summarize_long_text("交易详情", "A\n\nB") == "A | B"
    assert helpers._summarize_long_text("交易详情", "") == ""
    assert helpers._status_badge_color("--", "状态") is None
    assert helpers._status_badge_color("触发", "状态") is not None
    assert helpers._accent_rail_color_for_row_style("breakout") is not None
    assert helpers._accent_rail_color_for_row_style("unknown") is None
    assert helpers._row_accent_color({"_row_style": "warning"}, ["状态"]) is not None
    assert helpers._row_accent_color(row, ["名称", "状态"]) is not None
    assert helpers._row_accent_color({}, ["名称"]) is None


def test_serial_sync_and_range_emission_handles_sparse_and_coalesced_rows():
    rows = [{"代码": "1"}, "bad", {"代码": "2"}]
    helpers._sync_serial_values(rows)
    assert rows[0]["序号"] == 1
    assert rows[2]["序号"] == 3
    assert helpers._with_serial_header([]) == ["序号"]
    assert helpers._with_serial_header(["序号", "代码"]) == ["序号", "代码"]

    model = _stock(["代码"], [{"代码": str(i)} for i in range(5)])
    emissions = []
    model.dataChanged.connect(lambda top, bottom, roles: emissions.append((top.row(), bottom.row(), roles)))
    helpers._emit_model_row_ranges(model, [], 0, 1, [Qt.ItemDataRole.DisplayRole])
    helpers._emit_model_row_ranges(model, [0, 1, 3], 0, 1, [Qt.ItemDataRole.DisplayRole])
    helpers._emit_model_row_ranges(model, [1, 4], 0, 1, [Qt.ItemDataRole.DisplayRole], coalesce=True)
    assert [(start, end) for start, end, _roles in emissions] == [(0, 1), (3, 3), (1, 4)]


def test_stock_model_style_visual_payloads_and_identity_helpers():
    model = _stock(
        ["代码", "来源", "上榜净买额(万)", "风控", "状态", "现价", "货币"],
        [
            {
                "代码": "000001",
                "来源": "龙虎榜|算法扫描",
                "上榜净买额(万)": 100,
                "风控": "红色高风险",
                "状态": "🟢交易中",
                "现价": "10",
                "货币": "TWD",
            },
            {"代码": "000002", "上榜净买额(万)": -250},
            "not-a-row",
        ],
    )
    model.set_plain_style_headers(["来源", "", None])
    model.set_plain_background_headers(["现价"])
    model.set_muted_text_headers(["状态"])
    model.set_sparse_update_coalescing(1)

    assert model.row_data is model._data
    assert model.headers[0] == "序号"
    assert model._uses_plain_style("来源")
    assert model._uses_plain_background("现价")
    assert model._uses_muted_text("状态")
    assert model._sparse_update_coalescing is True
    assert StockTableModel._split_source_tags("", {"来源标签": ["手动", "", None]}) == ["手动"]
    assert StockTableModel._split_source_tags("自选/算法", {}) == ["自选", "算法"]
    assert StockTableModel._split_source_tags("fallback", {"来源标签": ""}) == ["fallback"]
    assert StockTableModel._split_source_tags("", {}) == []
    assert model._source_badges_payload("", {}) is None

    model.set_plain_style_headers([])
    assert model._visual_payload("来源", model.row_data[0]["来源"], model.row_data[0])["kind"] == "tag_badges"
    source_index = model.index(0, model.headers.index("来源"))
    render_payload = model.data(source_index, helpers.STOCK_CELL_RENDER_ROLE)
    assert render_payload == (
        model.data(source_index, Qt.ItemDataRole.UserRole + 4),
        model.data(source_index, Qt.ItemDataRole.UserRole + 3),
        model.data(source_index, Qt.ItemDataRole.UserRole + 1),
        model.data(source_index, Qt.ItemDataRole.UserRole + 2),
        model.data(source_index, Qt.ItemDataRole.UserRole + 5),
    )
    assert model._visual_payload("上榜净买额(万)", 100, model.row_data[0])["max_abs"] == 250.0
    assert model._money_bar_payload("名称", model.row_data[0]) is None
    assert model._money_bar_payload("上榜净买额(万)", {"上榜净买额(万)": "bad"}) is None
    assert model._visual_payload("风控", "红色高风险", model.row_data[0])["kind"] == "risk_light"
    assert model._visual_payload("状态", "🟢交易中", model.row_data[0])["pulse"] is True
    assert model._visual_payload("现价", "10", model.row_data[0]) == {"kind": "currency_stamp", "stamp": "NT$"}
    assert model._visual_payload("名称", "A", model.row_data[0]) is None

    assert StockTableModel._row_identity("bad") == ""
    assert StockTableModel._row_identity({"symbol": "AAPL"}) == "AAPL"
    assert StockTableModel._row_identity({}) == ""
    assert StockTableModel._row_id_sequence([{"code": "1"}, {"证券代码": "2"}]) == ["1", "2"]
    assert model.get_row_data(-1) is None
    assert model.get_row_data(99) is None


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("🔴异常", "error"),
        ("🟡竞价", "warning"),
        ("🟢交易中", "warning"),
        ("收盘", "offline"),
        ("未知", "neutral"),
    ],
)
def test_stock_indicator_tones(text, expected):
    assert StockTableModel._indicator_tone(text) == expected


@pytest.mark.parametrize(
    ("currency", "expected"),
    [("JPY", "¥"), ("円", "円"), ("krw", "₩"), ("HKD", "HK$"), ("usd", "$"), ("CNY", "CNY")],
)
def test_currency_stamp_mapping(currency, expected):
    assert StockTableModel._currency_stamp_text(currency) == expected


def test_stock_incremental_guards_flash_cache_and_cell_updates():
    rows = [{"代码": "1", "现价": "10"}, {"代码": "2", "现价": "20"}]
    model = _stock(["代码", "现价"], rows)

    assert not model._can_update_incrementally([])
    assert not model._can_update_incrementally(model.row_data)
    assert not model._can_update_incrementally([model.row_data[0], dict(model.row_data[1])])
    assert model._can_update_incrementally([dict(model.row_data[0]), dict(model.row_data[1])])
    assert not model._can_reorder_incrementally([dict(model.row_data[0]), dict(model.row_data[1])])
    assert model._can_reorder_incrementally([dict(model.row_data[1]), dict(model.row_data[0])])
    assert not model._can_reorder_incrementally([{"\u4ee3\u7801": "1"}, {"\u4ee3\u7801": "1"}])
    assert not model._can_reorder_incrementally([{}, {}])

    model._sort_value_cache = {(0, 2): 10, (1, 2): 20}
    assert not model._record_cell_flash(-1, 2, 1, 2)
    assert not model._record_cell_flash(0, 999, 1, 2)
    assert not model._record_cell_flash(0, 2, "10", "10")
    assert model._record_cell_flash(0, 2, "10", "11")
    assert (0, 2) not in model._sort_value_cache
    model._clear_sort_value_cache_for_rows([])
    model._clear_sort_value_cache_for_rows([1])
    assert (1, 2) not in model._sort_value_cache

    model._sort_value_cache = {(0, 0): 1, (0, 1): 2, (1, 1): 3}
    model._invalidate_sort_cache_for_changed_indexes(QModelIndex(), QModelIndex())
    model._invalidate_sort_cache_for_changed_indexes(model.index(0, 1), model.index(0, 0))
    assert model._sort_value_cache == {(1, 1): 3}
    assert not model._record_row_flashes(0, "bad", {})

    emissions = []
    model.dataChanged.connect(lambda *args: emissions.append(args))
    assert not model.set_cell_value(-1, "现价", "12")
    assert not model.set_cell_value(0, "不存在", "x")
    assert model.row_data[0]["不存在"] == "x"
    assert model.set_cell_value(0, "现价", "12", emit_signal=False)
    assert not emissions
    assert Qt.ItemDataRole.UserRole + 1 not in model._flash_roles(include_flash=False)


def test_stock_reset_reorder_mime_flags_and_drop_contract():
    model = _stock(["代码", "名称"], [{"代码": "1", "名称": "A"}, {"代码": "2", "名称": "B"}])
    reordered = []
    model.sig_rows_reordered.connect(reordered.append)

    assert model.supportedDropActions() == Qt.DropAction.MoveAction
    assert model.mimeTypes() == ["application/x-watchlist-row"]
    assert model.flags(QModelIndex()) & Qt.ItemFlag.ItemIsDropEnabled
    assert model.flags(model.index(0, 0)) & Qt.ItemFlag.ItemIsDragEnabled
    assert not model.mimeData([]).hasFormat("application/x-watchlist-row")
    mime = model.mimeData([model.index(1, 0), model.index(0, 1), model.index(1, 1)])
    assert sorted(json.loads(bytes(mime.data("application/x-watchlist-row")).decode("utf-8"))) == [0, 1]

    assert model.dropMimeData(QMimeData(), Qt.DropAction.MoveAction, 0, 0, QModelIndex()) is False
    broken = QMimeData()
    broken.setData("application/x-watchlist-row", b"not-json")
    assert model.dropMimeData(broken, Qt.DropAction.MoveAction, 0, 0, QModelIndex()) is False

    drag_first = QMimeData()
    drag_first.setData("application/x-watchlist-row", b"[0]")
    assert model.dropMimeData(drag_first, Qt.DropAction.MoveAction, -1, 0, QModelIndex()) is False
    assert reordered[-1] == ["2", "1"]

    model.update_data([], hydrate_latest_quotes=False)
    assert model.rowCount() == 0
    model._emit_reordered_rows([])


def test_stock_quote_update_covers_history_clock_and_metric_paths(monkeypatch):
    today = time.strftime("%Y-%m-%d")
    calls = []

    def fake_metrics(_row, quote):
        return {
            "rt_close": quote.get("close", 0),
            "pct": quote.get("pct"),
            "price_text": quote.get("price_text"),
            "market_cap_text": quote.get("cap"),
            "zongguben": quote.get("shares", 0),
        }

    monkeypatch.setattr(stock_module, "resolve_quote_metrics", fake_metrics)
    monkeypatch.setattr(
        stock_module,
        "calculate_buy_point_from_history",
        lambda **kwargs: calls.append(kwargs) or "触发",
    )
    from app.services.ui_market_calendar_service import MarketCalendar

    monkeypatch.setattr(MarketCalendar, "is_trade_day", lambda _date: True)
    monkeypatch.setattr(stock_module, "record_metric", lambda *args, **kwargs: calls.append((args, kwargs)))

    model = _stock(
        ["代码", "现价", "市价", "涨幅%", "涨幅", "市值", "买点"],
        [
            {
                "代码": "1",
                "现价": "10",
                "市价": "10",
                "涨幅%": 0,
                "涨幅": 0,
                "市值": "10亿",
                "买点": "",
                "_history_20": list(range(1, 21)),
                "_history_date": today,
            },
            {
                "代码": "2",
                "现价": "20",
                "市价": "20",
                "涨幅%": 0,
                "涨幅": 0,
                "市值": "20亿",
                "买点": "",
                "_history_20": list(range(2, 22)),
                "_history_date": "2000-01-01",
            },
            {"代码": "3", "现价": "30"},
            {"代码": ""},
        ],
    )

    changed = model.update_quotes(
        {
            "1": {"close": 11.0, "open": 10.5, "pct": 1.2, "price_text": "11.00", "cap": "11亿", "shares": 100},
            "2": {"close": 21.0, "pct": -1.2, "price_text": "21.00", "cap": "21亿", "shares": 200},
        }
    )

    assert changed == 2
    assert model.row_data[0]["买点"] == "触发"
    assert model.row_data[1]["_zongguben"] == 200.0
    history_calls = [call for call in calls if isinstance(call, dict)]
    assert history_calls[0]["history"][-1] == 11.0
    assert history_calls[1]["history"][-1] == 21.0
    assert model.update_quotes({}) == 0


def test_stock_quote_update_non_trade_history_and_no_quote_columns(monkeypatch):
    histories = []
    monkeypatch.setattr(
        stock_module,
        "resolve_quote_metrics",
        lambda _row, _quote: {
            "rt_close": 12,
            "pct": None,
            "price_text": None,
            "market_cap_text": None,
            "zongguben": 1,
        },
    )
    monkeypatch.setattr(
        stock_module, "calculate_buy_point_from_history", lambda **kwargs: histories.append(kwargs["history"]) or ""
    )
    monkeypatch.setattr(stock_module, "record_metric", lambda *args, **kwargs: None)
    from app.services.ui_market_calendar_service import MarketCalendar

    monkeypatch.setattr(MarketCalendar, "is_trade_day", lambda _date: False)
    model = _stock(
        ["代码", "买点"],
        [{"代码": "1", "买点": "old", "_history_20": list(range(20)), "_history_date": "2000-01-01"}],
    )

    assert model.update_quotes({"1": {"close": 12}}) == 1
    assert histories[0][-1] == 12


def test_stock_display_font_foreground_background_and_sort_boundaries(monkeypatch):
    model = _stock(
        [
            "代码",
            "名称",
            "PE",
            "风控",
            "状态",
            "评级",
            "最近上榜",
            "换手率%",
            "AI细分板块/备注",
            "现价",
            "涨幅%",
            "卖方营业部",
            "交易详情",
            "成交金额(万元)",
            "外资净买入",
            "突破状态",
            "股价弹性",
            "评分",
            "日报时间",
        ],
        [
            {
                "代码": "1",
                "名称": "A",
                "PE": "12.345",
                "风控": "高",
                "状态": "🟢交易中",
                "评级": "A",
                "最近上榜": "07-14",
                "_recent": "unused",
                "_最近上榜_raw": "20260714",
                "换手率%": "3.2",
                "AI细分板块/备注": "CPO",
                "现价": "10",
                "涨幅%": "9.5",
                "卖方营业部": "高盛席位",
                "交易详情": "对倒",
                "成交金额(万元)": "12,000",
                "外资净买入": "净买",
                "外资净买(万)": 20,
                "突破状态": "放量突破",
                "股价弹性": "高",
                "评分": "95",
                "日报时间": "2026-07-14",
                "_report_ts": 20260714120000,
                "_report_row_rank": 3,
            }
        ],
    )
    from app.services.ui_watchlist_service import watchlist_vm

    monkeypatch.setattr(watchlist_vm, "is_in_watchlist", lambda _code: True)
    row = model.row_data[0]

    assert model._display_value(0, "PE", "", row) == "--"
    assert model._display_value(0, "PE", "12.345", row) == "12.35"
    assert model._display_value(0, "PE", "bad", row) == "bad"
    assert model._display_value(0, "风控", "高", row) == ""
    assert model._display_value(0, "状态", "🟢交易中", row) == "交易中"
    assert model._display_value(0, "交易日期", "2026-07-14 12:00", row) == "20260714"
    assert StockTableModel._percent_display_value("换手率%", "3.2") == "3.20%"
    assert StockTableModel._percent_display_value("涨幅%", "3.2") == "+3.20%"
    assert StockTableModel._percent_display_value("涨幅%", "3%") == "3%"
    assert StockTableModel._percent_display_value("涨幅%", "bad") is None

    assert model._font_value("序号", 1, row) is model.mono_font
    assert model._font_value("评级", "A", row) is model.bold_mono_font
    assert model._font_value("最近上榜", "07-14", row) is model.small_mono_font
    assert model._font_value("AI细分板块/备注", "CPO", row) is model.small_font
    assert model._font_value("现价", "10", row) is model.bold_mono_font
    assert model._font_value("名称", "A", row) is model.base_font
    assert _valid_color(model._base_foreground_value("名称", "A", row))
    assert _valid_color(model._base_foreground_value("变化类型", "新进", row))
    assert _valid_color(model._base_foreground_value("评级", "A", row))

    for key, raw in (
        ("涨幅%", "10"),
        ("涨幅%", "1"),
        ("涨幅%", "-10"),
        ("涨幅%", "-1"),
        ("涨幅%", "0"),
        ("涨幅%", "bad"),
    ):
        assert _valid_color(model._market_move_foreground_value(key, raw, row))
    assert model._market_move_foreground_value("名称", "1", row) is None
    assert _valid_color(model._broker_foreground_value("卖方营业部", "高盛"))
    assert model._broker_foreground_value("买方营业部", "本地营业部") is None
    for detail in ("对倒", "买/3", "3/卖", "卖出", "买入"):
        assert _valid_color(model._trade_detail_foreground_value("交易详情", detail))
    assert model._trade_detail_foreground_value("交易详情", "普通") is None
    assert _valid_color(model._amount_foreground_value("成交金额(万元)", "10,000"))
    assert model._amount_foreground_value("成交金额(万元)", "bad") is None
    assert _valid_color(model._foreign_net_foreground_value("外资净买入", row))
    for status in ("放量", "缩量", "临近", "VCP", "其他"):
        assert _valid_color(model._status_foreground_value("突破状态", status))
    assert _valid_color(model._elasticity_foreground_value("股价弹性", "高"))
    for score in (95, 85, 65, 20):
        assert _valid_color(model._score_foreground_value("评分", score))
    assert model._score_foreground_value("评分", "bad") is None

    model.set_plain_background_headers(["涨幅%"])
    assert model._background_value("涨幅%", "9") is None
    model.set_plain_background_headers([])
    assert _valid_color(model._background_value("涨幅%", "9"))

    assert StockTableModel._text_sort_value("2026-07-14") == 20260714
    assert StockTableModel._text_sort_value("20260714") == 20260714
    assert StockTableModel._text_sort_value("1.2万") == 12000
    assert StockTableModel._text_sort_value("1.2亿") == 120000000
    assert StockTableModel._text_sort_value("abc") == "abc"
    assert StockTableModel._uncached_sort_value(0, "外资净买入", "x", {"外资净买(万)": "bad"}) == 0
    assert StockTableModel._uncached_sort_value(0, "最近上榜", "07-14", row) == 20260714
    assert StockTableModel._uncached_sort_value(0, "日报时间", "x", row) > row["_report_ts"] * 1000000


def test_stock_data_role_dispatch_header_and_badge_suppression():
    model = _stock(
        ["代码", "名称", "涨幅%", "状态", "来源"],
        [{"代码": "1", "名称": "A", "涨幅%": "3", "状态": "触发", "来源": "算法"}],
    )
    idx = model.index(0, model.headers.index("状态"))
    assert model.data(QModelIndex(), Qt.ItemDataRole.DisplayRole) is None
    for role in (
        Qt.ItemDataRole.DisplayRole,
        Qt.ItemDataRole.ToolTipRole,
        Qt.ItemDataRole.TextAlignmentRole,
        Qt.ItemDataRole.FontRole,
        Qt.ItemDataRole.ForegroundRole,
        Qt.ItemDataRole.BackgroundRole,
        Qt.ItemDataRole.UserRole,
        Qt.ItemDataRole.UserRole + 1,
        Qt.ItemDataRole.UserRole + 2,
        Qt.ItemDataRole.UserRole + 3,
        Qt.ItemDataRole.UserRole + 4,
        Qt.ItemDataRole.UserRole + 5,
    ):
        model.data(idx, role)
    assert model.data(idx, Qt.ItemDataRole.UserRole + 99) is None
    assert model.headerData(0, Qt.Orientation.Horizontal, Qt.ItemDataRole.DisplayRole) == "序号"
    assert (
        model.headerData(0, Qt.Orientation.Horizontal, Qt.ItemDataRole.TextAlignmentRole)
        & Qt.AlignmentFlag.AlignCenter.value
    )
    assert model.headerData(0, Qt.Orientation.Vertical, Qt.ItemDataRole.DisplayRole) is None
    assert model._status_badge_value("买点", stock_module.BUY_POINT_TEXT) is None
    model.set_plain_style_headers(["状态"])
    assert model._status_badge_value("状态", "触发") is None
    model.row_data[0]["_suppress_accent_rail"] = True
    assert model._accent_rail_value(model.row_data[0]) is None


def test_stock_presentation_cache_reuses_roles_invalidates_changed_row_and_preserves_live_flash(monkeypatch):
    model = _stock(["代码", "名称", "现价", "涨幅%"], [{"代码": "1", "名称": "A", "现价": "10", "涨幅%": "1"}])
    model.set_presentation_cache_enabled(True)
    index = model.index(0, model.headers.index("名称"))
    role_methods = {
        Qt.ItemDataRole.DisplayRole: "_display_value",
        Qt.ItemDataRole.ToolTipRole: "_tooltip_value",
        Qt.ItemDataRole.TextAlignmentRole: "_alignment_value",
        Qt.ItemDataRole.FontRole: "_font_value",
        Qt.ItemDataRole.ForegroundRole: "_foreground_value",
        Qt.ItemDataRole.BackgroundRole: "_background_value",
    }
    calls = {method: 0 for method in role_methods.values()}
    for method in calls:
        original = getattr(model, method)

        def counted(*args, _method=method, _original=original):
            calls[_method] += 1
            return _original(*args)

        monkeypatch.setattr(model, method, counted)

    for role in role_methods:
        model.data(index, role)
        model.data(index, role)
    assert calls == {method: 1 for method in role_methods.values()}

    model.set_cell_value(0, "名称", "B")

    assert model.data(index, Qt.ItemDataRole.DisplayRole) == "B"
    for role in role_methods:
        model.data(index, role)
    assert calls == {method: 2 for method in role_methods.values()}
    model.clear_presentation_cache()
    assert model._presentation_cache == {}

    quote_index = model.index(0, model.headers.index("现价"))
    assert model.set_cell_value(0, "现价", "11")
    assert model.data(quote_index, helpers.STOCK_CELL_RENDER_ROLE)[2] is not None
    model._flash_records[0][quote_index.column()]["time"] = time.time() - helpers.FLASH_DURATION_SECONDS - 1
    assert model.data(quote_index, helpers.STOCK_CELL_RENDER_ROLE)[2] is None


def test_rt_model_identity_update_guards_and_public_accessors():
    model = RtTableModel([{"\u4ee3\u7801": "1", "\u540d\u79f0": "A"}, {"\u4ee3\u7801": "2", "\u540d\u79f0": "B"}])
    assert model.row_data is model._data
    assert model.headers[0] == "序号"
    assert model.rowCount() == 2
    assert model.columnCount() == len(model.headers)
    assert RtTableModel._row_identity("bad") == ""
    assert RtTableModel._row_identity({"代码": " 1 "}) == "1"
    assert RtTableModel._row_id_sequence([{"\u4ee3\u7801": "1"}, {"\u4ee3\u7801": "2"}]) == ["1", "2"]
    assert not model._can_update_incrementally([])
    assert model._can_update_incrementally([{"\u4ee3\u7801": "1"}, {"\u4ee3\u7801": "2"}])
    assert not model._can_reorder_incrementally([{"\u4ee3\u7801": "1"}, {"\u4ee3\u7801": "2"}])
    assert model._can_reorder_incrementally([{"\u4ee3\u7801": "2"}, {"\u4ee3\u7801": "1"}])
    assert not model._can_reorder_incrementally([{}, {}])
    assert not model._can_reorder_incrementally([{"\u4ee3\u7801": "1"}, {"\u4ee3\u7801": "1"}])
    assert model.get_row_data(0)["代码"] == "1"
    assert model.get_row_data(99) == {}

    model._record_cell_flash(-1, 0, 1, 2)
    model._record_cell_flash(0, 999, 1, 2)
    model._record_cell_flash(0, model.headers.index("现价"), 1, 1)
    model._record_row_flashes(0, "bad", {})
    assert not model._flash_records

    assert model.update_rows_incremental(
        [{"\u4ee3\u7801": "1", "\u540d\u79f0": "A2"}, {"\u4ee3\u7801": "2", "\u540d\u79f0": "B"}]
    )
    assert model.update_rows_incremental([{"\u4ee3\u7801": "2"}, {"\u4ee3\u7801": "1"}])
    assert not model.update_rows_incremental([{"\u4ee3\u7801": "3"}])


def test_rt_quote_updates_cover_missing_columns_unmatched_rows_and_cap_optional(monkeypatch):
    assert RtTableModel().update_quotes({"1": {}}) is None
    model = RtTableModel(
        [
            {"\u4ee3\u7801": "1", "\u73b0\u4ef7": "10", "\u6da8\u5e45%": "0", "\u5e02\u503c": "10\u4ebf"},
            {"\u4ee3\u7801": ""},
        ]
    )
    monkeypatch.setattr(
        rt_module,
        "_apply_quote_metrics_to_row",
        lambda row, quote: (row.update({"现价": quote["现价"]}) is None, {}),
    )
    emissions = []
    model.dataChanged.connect(lambda *args: emissions.append(args))
    model.update_quotes({"1": {"现价": "11"}})
    assert model.row_data[0]["现价"] == "11"
    assert emissions

    model._headers.remove("市值")
    model.update_quotes({"1": {"现价": "12"}})
    model._headers.remove("涨幅%")
    assert model.update_quotes({"1": {"现价": "13"}}) is None


def test_rt_display_font_foreground_sort_and_role_dispatch():
    model = RtTableModel(
        [
            {
                "代码": "1",
                "名称": "A",
                "现价": "9.1234",
                "涨幅%": "9.5",
                "市值": "1234亿",
                "时间": "10:00",
                "评分": "95",
                "RPS强度": "96",
                "突破状态": "放量突破",
                "区间振幅": "3.2",
                "热点板块": "AI",
                "_row_style": "vcp",
            }
        ]
    )
    row = model.row_data[0]

    assert model._display_value(0, "序号", 1) == "1"
    assert model._display_value(0, "市值", "1234亿") == "1,234亿"
    assert model._display_value(0, "涨幅%", "") == ""
    assert model._display_value(0, "涨幅%", "2%") == "2%"
    assert model._display_value(0, "涨幅%", "2") == "+2.00%"
    assert model._display_value(0, "换手率%", "2") == "2.00%"
    assert model._display_value(0, "涨幅%", "bad") == "bad"
    assert model._display_value(0, "现价", 0) == "--"
    assert model._display_value(0, "现价", 9.1234) == "9.123"
    assert model._display_value(0, "现价", 12.345) == "12.35"
    assert model._display_value(0, "现价", "bad") == "bad"

    assert model._font_value("序号", 1, row) is model.mono_font
    assert model._font_value("涨幅%", "9.5", row) is model.bold_mono_font
    assert model._font_value("名称", "A", row) is model.base_font
    for raw in ("10", "1", "-10", "-1", "0", "bad"):
        assert _valid_color(model._percentage_foreground("涨幅%", raw))
    assert model._percentage_foreground("换手率%", "10") is None
    for status in ("放量突破", "缩量突破", "临近", "VCP蓄力", "异常", "其他"):
        color = model._status_foreground("突破状态", status)
        assert (color is None) == (status == "其他")
    assert model._status_foreground("名称", "放量突破") is None

    for value in (1, -1, 0, "bad"):
        item = {"外资净买(万)": value}
        model._foreign_net_buy_foreground("外资净买入", item)
        model._foreign_pool_foreground("外资潜伏池", item)
    assert model._foreign_net_buy_foreground("名称", {}) is None
    assert model._foreign_pool_foreground("名称", {}) is None
    assert _valid_color(model._foreground_value("序号", 1, row))
    assert _valid_color(model._foreground_value("名称", "A", row))
    assert model._row_accent_value(row) is not None

    assert model._sort_value(0, "序号", 1) == 1
    assert model._sort_value(0, "评分", "95") == 95
    assert model._sort_value(0, "市值", "bad") == 0
    assert model._sort_value(0, "市值", "bad万") == 0
    assert model._sort_value(0, "市值", "bad亿") == 0
    assert model._sort_value(0, "名称", "A") == "A"

    idx = model.index(0, model.headers.index("突破状态"))
    assert model.data(QModelIndex(), Qt.ItemDataRole.DisplayRole) is None
    for role in (
        Qt.ItemDataRole.DisplayRole,
        Qt.ItemDataRole.ToolTipRole,
        Qt.ItemDataRole.TextAlignmentRole,
        Qt.ItemDataRole.FontRole,
        Qt.ItemDataRole.ForegroundRole,
        Qt.ItemDataRole.BackgroundRole,
        Qt.ItemDataRole.UserRole,
        Qt.ItemDataRole.UserRole + 1,
        Qt.ItemDataRole.UserRole + 2,
        Qt.ItemDataRole.UserRole + 4,
    ):
        model.data(idx, role)
    assert model.data(idx, Qt.ItemDataRole.UserRole + 99) is None
    assert model.headerData(0, Qt.Orientation.Horizontal, Qt.ItemDataRole.DisplayRole) == "序号"
    assert (
        model.headerData(0, Qt.Orientation.Horizontal, Qt.ItemDataRole.TextAlignmentRole)
        & Qt.AlignmentFlag.AlignCenter.value
    )
    assert model.headerData(0, Qt.Orientation.Vertical, Qt.ItemDataRole.DisplayRole) is None


class _RecordingStockModel(StockTableModel):
    def __init__(self):
        super().__init__(["代码", "名称", "类型", "备注"])
        self.drops = []

    def dropMimeData(self, data, action, row, column, parent):
        self.drops.append((data, action, row, column, parent))
        return True


def test_proxy_serial_roles_sort_comparison_and_filters(monkeypatch):
    source = _stock(
        ["代码", "名称", "类型", "备注"],
        [
            {"代码": "000001", "名称": "平安银行", "类型": "A", "备注": "普通"},
            {"代码": "000002", "名称": "浦发银行", "类型": "B", "备注": "独有词"},
        ],
    )
    proxy = RtSortFilterProxyModel()
    proxy.setSourceModel(source)

    assert proxy.data(QModelIndex(), Qt.ItemDataRole.DisplayRole) is None
    serial = proxy.index(1, 0)
    assert proxy.data(serial, Qt.ItemDataRole.DisplayRole) == "2"
    assert proxy.data(serial, Qt.ItemDataRole.UserRole) == 2
    assert proxy.data(serial, Qt.ItemDataRole.ToolTipRole) is None
    assert proxy.data(serial, Qt.ItemDataRole.TextAlignmentRole) & Qt.AlignmentFlag.AlignCenter.value
    assert _valid_color(proxy.data(serial, Qt.ItemDataRole.ForegroundRole))
    before = [proxy.data(proxy.index(row, 1)) for row in range(proxy.rowCount())]
    proxy.sort(0, Qt.SortOrder.DescendingOrder)
    assert [proxy.data(proxy.index(row, 1)) for row in range(proxy.rowCount())] == before
    proxy.sort(-1)

    proxy.setColumnFilter("类型", "A")
    assert proxy.rowCount() == 1
    proxy.setColumnFilters("类型", [])
    assert proxy.rowCount() == 2
    proxy._exact_column_filters = {"不存在": {"x"}, "类型": "B"}
    proxy.invalidateFilter()
    assert proxy.rowCount() == 1
    proxy._exact_column_filters = {}

    monkeypatch.setattr(
        "ui.models.table_model_views.SearchFilter.match_pinyin_or_text", lambda query, code, name: query in code
    )
    proxy.setFilterText("000001")
    assert proxy.rowCount() == 1
    proxy.setFilterText("独有词")
    assert proxy.rowCount() == 1
    proxy.setFilterText("无匹配")
    assert proxy.rowCount() == 0
    proxy.setFilterText("")
    assert proxy.rowCount() == 2

    plain = QStandardItemModel(2, 1)
    plain.setData(plain.index(0, 0), "--", Qt.ItemDataRole.DisplayRole)
    plain.setData(plain.index(1, 0), "10", Qt.ItemDataRole.DisplayRole)
    compare_proxy = RtSortFilterProxyModel()
    compare_proxy.setSourceModel(plain)
    assert compare_proxy.lessThan(plain.index(0, 0), plain.index(1, 0))
    plain.setData(plain.index(0, 0), "beta", Qt.ItemDataRole.DisplayRole)
    plain.setData(plain.index(1, 0), "alpha", Qt.ItemDataRole.DisplayRole)
    assert not compare_proxy.lessThan(plain.index(0, 0), plain.index(1, 0))


def test_proxy_drag_mime_and_drop_coordinate_mapping():
    proxy_without_source = RtSortFilterProxyModel()
    assert not proxy_without_source.mimeData([]).hasFormat("application/x-watchlist-row")
    assert proxy_without_source.dropMimeData(QMimeData(), Qt.DropAction.MoveAction, 0, 0, QModelIndex()) is False

    source = _RecordingStockModel()
    source.update_data(
        [
            {"代码": "1", "名称": "A", "类型": "X", "备注": "a"},
            {"代码": "2", "名称": "B", "类型": "X", "备注": "b"},
        ],
        hydrate_latest_quotes=False,
    )
    proxy = RtSortFilterProxyModel()
    proxy.setSourceModel(source)

    assert proxy.mimeTypes() == ["application/x-watchlist-row"]
    assert proxy.supportedDropActions() == Qt.DropAction.MoveAction
    empty = QMimeData()
    assert not proxy.canDropMimeData(empty, Qt.DropAction.MoveAction, 0, 0, QModelIndex())
    mime = proxy.mimeData([proxy.index(1, 0), proxy.index(0, 1), QModelIndex()])
    assert proxy.canDropMimeData(mime, Qt.DropAction.MoveAction, 0, 0, QModelIndex())
    assert json.loads(bytes(mime.data("application/x-watchlist-row")).decode("utf-8")) == [0, 1]

    assert proxy.dropMimeData(mime, Qt.DropAction.MoveAction, 1, 0, QModelIndex()) is True
    assert source.drops[-1][2] == 1
    assert proxy.dropMimeData(mime, Qt.DropAction.MoveAction, proxy.rowCount(), 0, QModelIndex()) is True
    assert source.drops[-1][2] == source.rowCount()
    assert proxy.dropMimeData(mime, Qt.DropAction.MoveAction, -1, 0, proxy.index(0, 0)) is True
    assert source.drops[-1][2] == 0
    assert proxy.dropMimeData(mime, Qt.DropAction.MoveAction, -1, 0, QModelIndex()) is True
    assert source.drops[-1][2] == source.rowCount()

    proxy.sort(1)
    assert proxy.dropMimeData(mime, Qt.DropAction.MoveAction, 0, 0, QModelIndex()) is False


def test_delegate_simple_paint_path_restores_painter(qt_application):
    model = _stock(["代码"], [{"代码": "000001"}])
    widget = QWidget()
    widget.setProperty("simpleCellPaint", True)
    option = QStyleOptionViewItem()
    option.rect = QRect(0, 0, 120, 30)
    option.widget = widget
    image = QImage(120, 30, QImage.Format.Format_ARGB32)
    image.fill(Qt.GlobalColor.white)
    painter = QPainter(image)
    try:
        StockItemDelegate(widget).paint(painter, option, model.index(0, 1))
        assert painter.isActive()
    finally:
        painter.end()
        widget.deleteLater()


class _PaintWidget(QWidget):
    def __init__(self, current=QModelIndex(), sorted_column=-1):
        super().__init__()
        self._current = current
        self._sorted_column = sorted_column

    def currentIndex(self):
        return self._current

    def sorted_column(self):
        return self._sorted_column


@contextmanager
def _paint_context(
    *,
    text="value",
    tooltip=None,
    pill_color=None,
    visual_payload=None,
    flash_data=None,
    rail_color=None,
    plain_style=False,
    foreground=None,
    font=None,
    alignment=None,
    state=QStyle.StateFlag.State_None,
    width=180,
    height=32,
    sorted_column=-1,
    current=True,
    suppress_left_rails=False,
    show_current_indicator=False,
):
    model = QStandardItemModel(1, 1)
    index = model.index(0, 0)
    model.setData(index, text, Qt.ItemDataRole.DisplayRole)
    if tooltip is not None:
        model.setData(index, tooltip, Qt.ItemDataRole.ToolTipRole)
    if pill_color is not None:
        model.setData(index, pill_color, Qt.ItemDataRole.UserRole + 2)
    if visual_payload is not None:
        model.setData(index, visual_payload, Qt.ItemDataRole.UserRole + 5)
    if flash_data is not None:
        model.setData(index, flash_data, Qt.ItemDataRole.UserRole + 1)
    if rail_color is not None:
        model.setData(index, rail_color, Qt.ItemDataRole.UserRole + 4)
    model.setData(index, plain_style, Qt.ItemDataRole.UserRole + 3)
    if foreground is not None:
        model.setData(index, foreground, Qt.ItemDataRole.ForegroundRole)
    if font is not None:
        model.setData(index, font, Qt.ItemDataRole.FontRole)
    if alignment is not None:
        model.setData(index, alignment, Qt.ItemDataRole.TextAlignmentRole)

    widget = _PaintWidget(index if current else QModelIndex(), sorted_column)
    widget.setProperty("suppressLeftRails", suppress_left_rails)
    widget.setProperty("showCurrentCellIndicator", show_current_indicator)
    option = QStyleOptionViewItem()
    option.rect = QRect(0, 0, width, height)
    option.state = state
    option.widget = widget
    option.font = QFont("Segoe UI", 10)
    option.palette = widget.palette()
    option.textElideMode = Qt.TextElideMode.ElideRight
    opt = QStyleOptionViewItem(option)
    StockItemDelegate(widget).initStyleOption(opt, index)

    image = QImage(max(1, width), max(1, height), QImage.Format.Format_ARGB32)
    image.fill(Qt.GlobalColor.transparent)
    painter = QPainter(image)
    ctx = renderers.build_stock_cell_context(
        painter=painter,
        option=option,
        opt=opt,
        index=index,
        style=widget.style(),
        widget=widget,
        flash_duration=0.5,
    )
    try:
        yield ctx, image
    finally:
        painter.end()
        widget.deleteLater()


def test_renderer_context_derives_selection_sort_rail_and_current_state():
    with _paint_context(
        rail_color="#ff0000",
        sorted_column=0,
        show_current_indicator=True,
        current=True,
    ) as (ctx, _image):
        assert ctx.show_accent_rail is True
        assert ctx.rail_width > 0
        assert ctx.is_current is True
        assert ctx.sorted_overlay is not None
        assert ctx.suppress_left_rails is False

    selected = QStyle.StateFlag.State_Selected | QStyle.StateFlag.State_MouseOver
    with _paint_context(
        rail_color="#ff0000",
        sorted_column=0,
        state=selected,
        suppress_left_rails=True,
    ) as (ctx, _image):
        assert ctx.is_selected and ctx.is_hovered
        assert not ctx.show_accent_rail
        assert ctx.rail_color is None
        assert ctx.sorted_overlay is None

    with _paint_context(plain_style=True, sorted_column=0, current=False) as (ctx, _image):
        assert ctx.sorted_overlay is None
        assert not ctx.is_current


def test_renderer_native_fast_path_only_accepts_plain_cells():
    with _paint_context(text="plain", current=False) as (ctx, _image):
        assert renderers.can_use_native_cell_paint(ctx) is True

    with _paint_context(
        text="selected",
        state=QStyle.StateFlag.State_Selected,
        current=False,
    ) as (ctx, _image):
        assert renderers.can_use_native_cell_paint(ctx) is False

    with _paint_context(text="触发", pill_color="#ff0000", current=False) as (ctx, _image):
        assert renderers.can_use_native_cell_paint(ctx) is False

    with _paint_context(text="来源", visual_payload={"kind": "tag_badges"}, current=False) as (ctx, _image):
        assert renderers.can_use_native_cell_paint(ctx) is False

    with _paint_context(text="flash", flash_data={"time": time.time(), "diff": 1}, current=False) as (ctx, _image):
        assert renderers.can_use_native_cell_paint(ctx) is False


@pytest.mark.parametrize(
    ("features", "expected_role"),
    [
        (QStyleOptionViewItem.ViewItemFeature.None_, QPalette.ColorRole.Base),
        (
            QStyleOptionViewItem.ViewItemFeature.HasDisplay,
            QPalette.ColorRole.Base,
        ),
        (
            QStyleOptionViewItem.ViewItemFeature.HasDisplay
            | QStyleOptionViewItem.ViewItemFeature.Alternate,
            QPalette.ColorRole.AlternateBase,
        ),
    ],
)
def test_renderer_cell_background_role_respects_alternating_rows(features, expected_role):
    with _paint_context(current=False) as (ctx, _image):
        ctx.opt.features = features

        assert renderers._cell_background_role(ctx) == expected_role


def test_renderer_alternate_cell_base_uses_alternate_palette_pixel():
    class _NoopStyle:
        @staticmethod
        def drawControl(*_args, **_kwargs):
            return None

    with _paint_context(current=False) as (ctx, image):
        palette = QPalette(ctx.opt.palette)
        palette.setColor(QPalette.ColorRole.Base, QColor("#ffffff"))
        palette.setColor(QPalette.ColorRole.AlternateBase, QColor("#102030"))
        ctx.opt.palette = palette
        ctx.opt.features |= QStyleOptionViewItem.ViewItemFeature.Alternate
        ctx.style = _NoopStyle()

        renderers._draw_cell_base(ctx)

        assert image.pixelColor(ctx.option.rect.center()).name() == "#102030"


def test_renderer_selected_marker_composites_over_alternate_palette_pixel():
    with _paint_context(
        current=False,
        state=QStyle.StateFlag.State_Selected,
    ) as (ctx, image):
        palette = QPalette(ctx.opt.palette)
        palette.setColor(QPalette.ColorRole.Base, QColor("#ffffff"))
        palette.setColor(QPalette.ColorRole.AlternateBase, QColor("#000000"))
        ctx.opt.palette = palette
        ctx.option.palette = palette
        ctx.opt.features |= QStyleOptionViewItem.ViewItemFeature.Alternate
        ctx.table_tokens = dict(ctx.table_tokens)
        ctx.table_tokens["selected_bg"] = "rgba(255, 0, 0, 0.5)"

        renderers._clear_default_selected_left_marker(ctx)

        pixel = image.pixelColor(ctx.option.rect.left() + 1, ctx.option.rect.center().y())
        assert (pixel.red(), pixel.green(), pixel.blue()) == (128, 0, 0)


@pytest.mark.parametrize(
    ("text", "pill_color", "visual_payload", "tooltip"),
    [
        ("触发", "#ff0000", None, None),
        ("龙虎榜", None, {"kind": "tag_badges", "tags": [{"text": "龙虎榜", "color": "#ff0000"}]}, None),
        ("1200", None, {"kind": "money_bar", "value": 1200, "max_abs": 2400}, None),
        ("-1200", None, {"kind": "money_bar", "value": -1200, "max_abs": 2400}, None),
        ("", None, {"kind": "risk_light", "tone": "error"}, None),
        ("交易中", None, {"kind": "status_light", "tone": "success", "pulse": True}, None),
        ("12.34", None, {"kind": "currency_stamp", "stamp": "HK$"}, None),
        ("这是一段超过十二个字的长文本", None, None, "完整提示"),
        ("short", None, None, None),
    ],
)
def test_render_stock_cell_routes_each_visual_payload(text, pill_color, visual_payload, tooltip):
    with _paint_context(
        text=text,
        pill_color=pill_color,
        visual_payload=visual_payload,
        tooltip=tooltip,
        foreground=QColor("#123456"),
        font=QFont("Segoe UI", 10),
        alignment=int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter),
    ) as (ctx, image):
        renderers.render_stock_cell(ctx)
        assert ctx.painter.isActive()
        assert any(image.pixelColor(x, y).alpha() > 0 for x in range(image.width()) for y in range(image.height()))


def test_renderer_text_style_content_rect_and_plain_text_fallbacks():
    with _paint_context(text="abcdef", width=80, foreground=QColor("#123456"), font=QFont("Consolas", 11)) as (
        ctx,
        _image,
    ):
        ctx.rail_width = 3
        text_color, alignment = renderers._resolve_text_style(ctx)
        assert text_color.name() == "#123456"
        assert alignment & Qt.AlignmentFlag.AlignLeft.value
        assert renderers._content_rect(ctx).left() == 15
        renderers._draw_plain_text(ctx, "abcdef", fade=True)
        renderers._draw_plain_text(
            ctx,
            "abcdef",
            alignment_override=int(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter),
        )

    selected = QStyle.StateFlag.State_Selected
    with _paint_context(text="fallback", state=selected) as (ctx, _image):
        ctx.opt.font = QFont("Segoe UI", 9)
        color, alignment = renderers._resolve_text_style(ctx)
        assert color.isValid()
        assert alignment & Qt.AlignmentFlag.AlignLeft.value


def test_current_indicator_and_selected_marker_cover_size_and_alpha_branches():
    with _paint_context(
        show_current_indicator=True,
        current=True,
        state=QStyle.StateFlag.State_Selected | QStyle.StateFlag.State_MouseOver,
    ) as (ctx, _image):
        ctx.table_tokens = dict(ctx.table_tokens)
        ctx.table_tokens["selected_hover_bg"] = "rgba(10, 20, 30, 0.5)"
        renderers._draw_current_cell_indicator(ctx)
        renderers._clear_default_selected_left_marker(ctx)

    with _paint_context(show_current_indicator=False) as (ctx, _image):
        renderers._draw_current_cell_indicator(ctx)
        renderers._clear_default_selected_left_marker(ctx)

    with _paint_context(width=4, height=4, show_current_indicator=True, current=True) as (ctx, _image):
        renderers._draw_current_cell_indicator(ctx)


def test_flash_background_and_rail_cover_direction_timing_and_suppression(monkeypatch):
    monkeypatch.setattr(renderers.time, "time", lambda: 100.0)
    with _paint_context() as (ctx, _image):
        renderers._draw_flash_background(ctx)
        renderers._draw_flash_rail(ctx)

        for flash_data in (
            "invalid",
            {"time": 101.0, "diff": 1},
            {"time": 99.0, "diff": 1},
            {"time": 99.8, "diff": 1},
            {"time": 99.8, "diff": -1},
            {"time": 99.8, "diff": 0},
        ):
            flash_ctx = replace(ctx, flash_data=flash_data)
            renderers._draw_flash_background(flash_ctx)
            renderers._draw_flash_rail(flash_ctx)

        renderers._draw_flash_rail(replace(ctx, flash_data={"time": 99.8, "diff": 1}, suppress_left_rails=True))


def test_left_rail_covers_selected_hover_accent_and_invalid_width():
    with _paint_context(rail_color="#ff0000") as (ctx, _image):
        renderers._draw_left_rail(replace(ctx, show_selected_rail=False, show_accent_rail=False, show_hover_rail=False))
        renderers._draw_left_rail(replace(ctx, show_selected_rail=True, rail_width=0))
        renderers._draw_left_rail(replace(ctx, show_selected_rail=True, rail_width=3))
        renderers._draw_left_rail(
            replace(ctx, show_selected_rail=False, show_accent_rail=False, show_hover_rail=True, rail_width=3)
        )
        renderers._draw_left_rail(
            replace(ctx, show_selected_rail=False, show_accent_rail=True, show_hover_rail=False, rail_width=3)
        )


def test_money_bar_tag_badges_and_indicator_edge_inputs():
    with _paint_context(width=180) as (ctx, _image):
        renderers._draw_money_bar(ctx, {"value": "bad", "max_abs": 1})
        renderers._draw_money_bar(ctx, {"value": 0, "max_abs": 1})
        renderers._draw_money_bar(ctx, {"value": 1, "max_abs": 2})
        renderers._draw_money_bar(ctx, {"value": -1, "max_abs": 2})

        assert renderers._draw_tag_badges(ctx, {}) is False
        assert renderers._draw_tag_badges(ctx, {"tags": [{}]}) is False
        assert renderers._draw_tag_badges(
            ctx,
            {
                "tags": [
                    {"text": "A", "color": "not-a-color"},
                    {"text": "B", "color": "#00ff00"},
                    {"text": "C", "color": "#0000ff"},
                    {"text": "D", "color": "#ff00ff"},
                    {"text": "E", "color": "#ffffff"},
                ]
            },
        )
        assert renderers._draw_indicator(ctx, {"tone": "warning"}, center_only=True)
        assert renderers._draw_indicator(ctx, {"tone": "offline", "pulse": False, "label": None}, center_only=False)

    with _paint_context(width=20) as (ctx, _image):
        assert renderers._draw_tag_badges(ctx, {"tags": [{"text": "too-wide"}]}) is False

    for tone in ("success", "warning", "error", "offline", "neutral", "unknown"):
        assert _valid_color(renderers._indicator_color(tone))


def test_currency_stamp_and_pill_cover_blank_stamp_alignment_and_color_fallback(monkeypatch):
    with _paint_context(text="12.34") as (ctx, _image):
        renderers._draw_currency_stamp(ctx, {"stamp": ""})
        renderers._draw_currency_stamp(ctx, {"stamp": "NT$"})

    with _paint_context(
        text="触发",
        pill_color="#ff0000",
        alignment=int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter),
        rail_color="#00ff00",
    ) as (ctx, _image):
        original_c = renderers._c
        monkeypatch.setattr(
            renderers, "_c", lambda token: "invalid-color" if token == "INFO_BADGE_FG" else original_c(token)
        )
        renderers._draw_pill(ctx)

    with _paint_context(
        text="触发",
        pill_color="#ff0000",
        alignment=int(Qt.AlignmentFlag.AlignCenter | Qt.AlignmentFlag.AlignVCenter),
    ) as (ctx, _image):
        renderers._draw_pill(ctx)


def test_delegate_normal_paint_path_uses_renderer(qt_application):
    model = QStandardItemModel(1, 1)
    index = model.index(0, 0)
    model.setData(index, "normal", Qt.ItemDataRole.DisplayRole)
    widget = _PaintWidget(index, 0)
    option = QStyleOptionViewItem()
    option.rect = QRect(0, 0, 120, 30)
    option.widget = widget
    image = QImage(120, 30, QImage.Format.Format_ARGB32)
    image.fill(Qt.GlobalColor.transparent)
    painter = QPainter(image)
    try:
        StockItemDelegate(widget).paint(painter, option, index)
        assert painter.isActive()
    finally:
        painter.end()
        widget.deleteLater()


def test_stock_remaining_public_branches_keep_safe_coverage_margin():
    model = _stock(
        ["代码", "名称", "现价", "涨幅%", "市值", "状态", "上榜净买额(万)"],
        [
            {
                "代码": "1",
                "名称": "A",
                "现价": "10",
                "涨幅%": "1",
                "市值": "1234亿",
                "状态": "交易中",
                "上榜净买额(万)": 1,
            },
            {
                "代码": "2",
                "名称": "B",
                "现价": "20",
                "涨幅%": "2",
                "市值": "2345亿",
                "状态": "收盘",
                "上榜净买额(万)": 2,
            },
        ],
    )
    row = model.row_data[0]

    assert model.get_row_data(0) is row
    assert StockTableModel._source_badge_color("手动自选")
    assert StockTableModel._source_badge_color("其他")
    assert model._money_value_for_visual("外资净买入", {"外资净买(万)": "3"}) == 3
    assert StockTableModel._indicator_tone("绿色安全") == "success"
    assert model._currency_stamp_payload("名称", row) is None

    model._headers.append("货币")
    row["货币"] = "--"
    assert model._currency_stamp_payload("现价", row) is None
    model._headers.remove("货币")

    emissions = []
    model.dataChanged.connect(lambda *args: emissions.append(args))
    assert model.set_cell_value(0, "现价", "11", emit_signal=True)
    assert emissions
    assert model._display_value(0, "买点", stock_module.BUY_POINT_TEXT, row) == stock_module.BUY_POINT_TRIGGER_ICON
    assert model._display_value(0, "市值", "1234亿", row) == "1,234亿"
    assert model._display_value(0, "涨幅%", "1", row) == "+1.00%"
    assert StockTableModel._percent_display_value("涨幅%", "--") == "--"
    assert model._alignment_value("风控", "") & Qt.AlignmentFlag.AlignCenter.value
    assert model._font_value("代码", "1", row) is model.bold_mono_font
    assert model._font_value("评分", "80", row) is model.mono_font

    model.set_muted_text_headers(["名称"])
    assert _valid_color(model._base_foreground_value("名称", "A", row))
    model.set_plain_style_headers(["名称"])
    assert _valid_color(model._base_foreground_value("名称", "A", row))
    assert _valid_color(model._market_move_foreground_value("现价", "10", {"涨幅%": "2"}))
    assert model._amount_foreground_value("成交金额(万元)", "9999") is None

    for value in (-1, 0, "bad"):
        model._foreign_net_foreground_value("外资净买入", {"外资净买(万)": value})
    assert _valid_color(model._elasticity_foreground_value("股价弹性", "低"))
    assert _valid_color(model._foreground_value("未知列", "x", row))
    assert StockTableModel._uncached_sort_value(0, "序号", 99, row) == 1
    assert StockTableModel._uncached_sort_value(0, "最近上榜", "07-14", {}) == 7.0
    assert StockTableModel._uncached_sort_value(0, "日报时间", "x", {}) == "x"

    reordered = [dict(model.row_data[1]), dict(model.row_data[0])]
    model.update_data(reordered, hydrate_latest_quotes=False)
    assert [item["代码"] for item in model.row_data] == ["2", "1"]
    model.update_data([dict(item) for item in model.row_data], hydrate_latest_quotes=False)


def test_rt_update_data_and_remaining_role_branches():
    model = RtTableModel(
        [
            {"\u4ee3\u7801": "1", "\u73b0\u4ef7": "10", "\u6da8\u5e45%": "1"},
            {"\u4ee3\u7801": "2", "\u73b0\u4ef7": "20", "\u6da8\u5e45%": "2"},
        ]
    )
    model.update_data(
        [
            {"\u4ee3\u7801": "2", "\u73b0\u4ef7": "21", "\u6da8\u5e45%": "3"},
            {"\u4ee3\u7801": "1", "\u73b0\u4ef7": "11", "\u6da8\u5e45%": "2"},
        ]
    )
    assert [row["代码"] for row in model.row_data] == ["2", "1"]
    model.update_data([{"\u4ee3\u7801": "3"}])
    assert model.row_data[0]["代码"] == "3"
    assert model._font_value("PE", "12", {}) is model.mono_font

    model = RtTableModel([{"\u4ee3\u7801": "1", "\u6da8\u5e45%": "3", "\u7a81\u7834\u72b6\u6001": "--"}])
    pct_idx = model.index(0, model.headers.index("涨幅%"))
    assert _valid_color(model.data(pct_idx, Qt.ItemDataRole.BackgroundRole))
    status_idx = model.index(0, model.headers.index("突破状态"))
    assert model.data(status_idx, Qt.ItemDataRole.UserRole + 2) is None


def test_proxy_comparison_right_placeholder_and_empty_source_mime():
    plain = QStandardItemModel(2, 1)
    plain.setData(plain.index(0, 0), "10", Qt.ItemDataRole.DisplayRole)
    plain.setData(plain.index(1, 0), "--", Qt.ItemDataRole.DisplayRole)
    proxy = RtSortFilterProxyModel()
    proxy.setSourceModel(plain)
    assert not proxy.lessThan(plain.index(0, 0), plain.index(1, 0))
    assert not proxy.mimeData([QModelIndex()]).hasFormat("application/x-watchlist-row")


def test_renderer_unknown_payload_opaque_selection_and_pixel_font_branch():
    with _paint_context(visual_payload={"kind": "unknown"}) as (ctx, _image):
        renderers.render_stock_cell(ctx)

    with _paint_context(state=QStyle.StateFlag.State_Selected) as (ctx, _image):
        ctx.table_tokens = dict(ctx.table_tokens)
        ctx.table_tokens["selected_bg"] = "#112233"
        renderers._clear_default_selected_left_marker(ctx)

    with _paint_context(text="12.34") as (ctx, _image):
        pixel_font = QFont()
        pixel_font.setPixelSize(10)
        ctx.opt.font = pixel_font
        renderers._draw_currency_stamp(ctx, {"stamp": "HK$"})


def test_helper_config_failure_and_empty_metric_branches(monkeypatch):
    from app.services import ui_config_service

    class _BrokenConfig:
        def __getattribute__(self, name):
            if name == "table_density":
                raise RuntimeError("settings unavailable")
            return object.__getattribute__(self, name)

    monkeypatch.setattr(ui_config_service, "app_config", _BrokenConfig())
    helpers.invalidate_table_token_cache()
    assert helpers._current_table_density() is None

    monkeypatch.setattr(
        helpers,
        "resolve_quote_metrics",
        lambda _row, _quote: {
            "zongguben": 0,
            "price_text": None,
            "pct": None,
            "market_cap_text": None,
        },
    )
    assert helpers._apply_quote_metrics_to_row({}, {})[0] is False
    assert helpers._strong_market_pct_from_row({"涨幅%": "bad", "涨幅": None}) is None
    assert helpers._normalized_alignment_text("名称", "") == ""
    helpers.invalidate_table_token_cache("舒适")


def test_stock_quote_hydration_guards_and_exception(monkeypatch):
    no_code = _stock(["名称", "现价"], [{"名称": "A", "现价": "10"}])
    assert no_code._hydrate_latest_quotes_from_store() is None
    no_quote_fields = _stock(["代码", "名称"], [{"代码": "1", "名称": "A"}])
    assert no_quote_fields._hydrate_latest_quotes_from_store() is None

    from core.global_store import global_store

    monkeypatch.setattr(global_store, "get_latest_quotes", lambda: (_ for _ in ()).throw(RuntimeError("offline")))
    model = _stock(["代码", "现价"], [{"代码": "1", "现价": "10"}])
    assert model._hydrate_latest_quotes_from_store() is None


def test_proxy_serial_unknown_role_falls_back_to_source_model():
    source = _stock(["代码"], [{"代码": "1"}])
    proxy = RtSortFilterProxyModel()
    proxy.setSourceModel(source)
    assert proxy.data(proxy.index(0, 0), Qt.ItemDataRole.UserRole + 99) is None
