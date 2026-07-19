# -*- coding: utf-8 -*-
from __future__ import annotations

import datetime as dt
from types import SimpleNamespace

from ui import kline_window_header as header


class _Metrics:
    def horizontalAdvance(self, text):
        return len(str(text)) * 5

    def elidedText(self, text, mode, width):
        return str(text) if width >= len(str(text)) * 5 else str(text)[:3] + "…"


class _Label:
    def __init__(self, width=100):
        self._width = width
        self.text = None
        self.tooltip = None
        self.visible = None

    def width(self):
        return self._width

    def fontMetrics(self):
        return _Metrics()

    def setText(self, text):
        self.text = text

    def setToolTip(self, text):
        self.tooltip = text

    def setVisible(self, visible):
        self.visible = visible


def test_header_tooltip_elision_and_signed_number_edges():
    assert header._summary_tooltip_value_html("") == ""
    assert header._summary_tooltip_value_html(" a&\n b ") == "a&amp;<br/>b"
    assert header._elide_summary_value(_Label(), "key", "") == "--"
    assert header._elide_summary_value(_Label(width=0), "key", "value") == "value"
    assert header._elide_summary_value(_Label(width=30), "key", "long-value").endswith("…")
    assert header._build_summary_tooltip("", "") == ""
    assert "span" in header._build_summary_tooltip("key", "value")
    assert f"width: {header.SUMMARY_TOOLTIP_WIDTH}px" in header._build_summary_tooltip(
        "key", "x" * header.SUMMARY_TOOLTIP_WIDE_THRESHOLD
    )
    assert header._summary_signed_number("") is None
    assert header._summary_signed_number("--") is None
    assert header._summary_signed_number("+1,234.5%") == 1234.5
    assert header._summary_signed_number("not-a-number") is None


def test_resolve_summary_value_color_all_tones():
    theme = header.theme_manager.current_theme
    window = SimpleNamespace(_summary_highlight_color="highlight", _summary_value_color="normal")
    assert header._resolve_summary_value_color(window, "x", "1", True) == "highlight"
    assert header._resolve_summary_value_color(window, "x", "1%", False) == theme["COLOR_RISE"]
    assert header._resolve_summary_value_color(window, "x", "-1%", False) == theme["COLOR_FALL"]
    assert header._resolve_summary_value_color(window, "x", "0%", False) == theme["COLOR_FLAT"]
    assert header._resolve_summary_value_color(window, "涨幅", "bad", False) == "normal"
    assert header._resolve_summary_value_color(window, "催化", "text", False) == theme["COLOR_WARNING"]
    assert header._resolve_summary_value_color(window, "name", "text", False) == "normal"


def test_refresh_header_context_populates_identity_nav_cards_and_empty_rows(monkeypatch):
    monkeypatch.setattr(header, "format_kline_market_badge", lambda code: "CN")
    badges = []
    monkeypatch.setattr(
        header, "set_header_badge", lambda window, label, text, tone: badges.append((label, text, tone))
    )
    monkeypatch.setattr(header, "apply_header_badges", lambda window: badges.append(("runtime",)))
    monkeypatch.setattr(
        header,
        "build_kline_summary_cards",
        lambda payload, fav: [
            {
                "title": "Card",
                "rows": [
                    {"label": "涨幅", "value": "1%", "raw_value": "1%"},
                    {"label": "--", "value": "--", "raw_value": "--"},
                ],
            }
        ],
    )
    identity = _Label()
    market = _Label()
    nav = _Label()
    first_labels = [_Label(width=80), _Label(width=80), _Label(width=80)]
    fallback_labels = [_Label()]
    window = SimpleNamespace(
        code="000001",
        name="Ping An",
        identity_lbl=identity,
        market_badge_lbl=market,
        nav_index_lbl=nav,
        feed_badge_lbl=_Label(),
        code_list=[1, 2],
        current_idx=0,
        summary_cards=[
            {"title": _Label(), "labels": first_labels},
            {"title": _Label(), "labels": fallback_labels},
        ],
        vcp_data={},
        is_fav=False,
        _summary_key_color="key",
        _summary_value_color="value",
    )
    header.refresh_header_context(window)
    assert identity.text == "Ping An  000001"
    assert nav.text == "1 / 2"
    assert badges[0][1:] == ("CN", "neutral")
    assert first_labels[0].visible is True and "COLOR" not in first_labels[0].text
    assert first_labels[1].visible is False and first_labels[1].text == "" and first_labels[1].tooltip == ""
    assert first_labels[2].visible is False
    assert fallback_labels[0].visible is False

    window.code_list = []
    header.refresh_header_context(window)
    assert nav.text


def test_refresh_header_context_with_no_optional_widgets(monkeypatch):
    monkeypatch.setattr(header, "format_kline_market_badge", lambda code: "CN")
    header.refresh_header_context(SimpleNamespace(code="1", name="one"))


def test_resolve_vcp_context_handles_watchlist_workspace_and_failures(monkeypatch):
    captured = []
    monkeypatch.setattr(
        header,
        "resolve_kline_vcp_context",
        lambda **kwargs: captured.append(kwargs) or {"resolved": True},
    )
    merges = []
    monkeypatch.setattr(header, "merge_workspace_kline_context", lambda **kwargs: merges.append(kwargs))

    monkeypatch.setattr(
        header.watchlist_vm,
        "get_watchlist_data",
        lambda: {"000001": {"watch": True}},
    )
    workspace = SimpleNamespace()
    window = SimpleNamespace(main_window=SimpleNamespace(_workspace=workspace))
    assert header.resolve_vcp_context(window, "000001", "Ping", {"item": True}) == {"resolved": True}
    assert captured[-1]["watchlist_entry"] == {"watch": True}
    assert captured[-1]["scan_results"] == []
    assert merges and merges[-1]["code_text"] == "000001"
    assert merges[-1]["vcp_data"] == {"resolved": True}

    monkeypatch.setattr(
        header.watchlist_vm,
        "get_watchlist_data",
        lambda: (_ for _ in ()).throw(OSError("bad")),
    )
    broken_workspace = SimpleNamespace()
    header.resolve_vcp_context(SimpleNamespace(main_window=SimpleNamespace(_workspace=broken_workspace)), "2", "Two")
    assert captured[-1]["watchlist_entry"] == {}
    assert captured[-1]["scan_results"] == []
    header.resolve_vcp_context(SimpleNamespace(main_window=None), "3", "Three")
    assert captured[-1]["scan_results"] == []


def test_get_cn_target_trade_date_calendar_boundaries(monkeypatch):
    today = dt.date(2026, 7, 15)
    monkeypatch.setattr(header.MarketCalendar, "_get_market_now", lambda market: dt.datetime(2026, 7, 15, 10, 0))
    monkeypatch.setattr(header.MarketCalendar, "get_latest_trade_date", lambda market, ref_date: None)
    assert header.get_cn_target_trade_date() is None

    monkeypatch.setattr(header.MarketCalendar, "get_latest_trade_date", lambda market, ref_date: ref_date)
    monkeypatch.setattr(header.MarketCalendar, "is_trade_day", lambda date, market: False)
    assert header.get_cn_target_trade_date() == today
    monkeypatch.setattr(header.MarketCalendar, "is_trade_day", lambda date, market: True)
    assert header.get_cn_target_trade_date() == today

    monkeypatch.setattr(header.MarketCalendar, "_get_market_now", lambda market: dt.datetime(2026, 7, 15, 9, 0))
    assert header.get_cn_target_trade_date() == dt.date(2026, 7, 14)
