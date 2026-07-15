# -*- coding: utf-8 -*-
from __future__ import annotations

import datetime as dt
from types import SimpleNamespace

import pandas as pd
import pytest

from ui import kline_window_asian as asian
from ui import main_window_network as network


def _daily_frame(date: str = "2026-07-14") -> pd.DataFrame:
    return pd.DataFrame(
        {
            "open": [10.0],
            "high": [11.0],
            "low": [9.0],
            "close": [10.5],
            "volume": [100.0],
        },
        index=[pd.Timestamp(date)],
    )


def _vendor_frame(*, tz: str | None = None, volume: bool = True) -> pd.DataFrame:
    index = pd.DatetimeIndex(["2026-07-15 09:30", "2026-07-15 15:00"])
    if tz:
        index = index.tz_localize(tz)
    data = {
        "Open": [10, 11],
        "High": [12, 13],
        "Low": [9, 10],
        "Close": [11, 12],
    }
    if volume:
        data["Volume"] = [100, 200]
    return pd.DataFrame(data, index=index)


def test_asian_cache_dataframe_and_context_helpers(monkeypatch):
    monkeypatch.setattr(asian, "_load_cached_asian_stock", lambda path, code: {"path": path, "code": code})
    assert asian.load_cached_asian_stock("cache.json", "2330.TW") == {
        "path": "cache.json",
        "code": "2330.TW",
    }
    assert asian.build_asian_df_from_klines([], lambda frame: frame) is None
    assert asian.build_asian_df_from_klines(iter(()), lambda frame: frame) is None

    normalized = []
    frame = asian.build_asian_df_from_klines(
        [
            {"date": "2026-07-14 12:00", "open": "10", "high": "11", "low": "9", "close": "10.5", "volume": "7"},
            {"date": "2026-07-15 00:00", "open": "bad", "high": 12, "low": 10, "close": 11, "ignored": "x"},
        ],
        lambda value: normalized.append(value.copy()) or value,
    )
    assert list(frame.index) == [pd.Timestamp("2026-07-14"), pd.Timestamp("2026-07-15")]
    assert float(frame.iloc[0]["volume"]) == 7.0
    assert pd.isna(frame.iloc[1]["open"])
    assert normalized

    vcp_data = {}
    refreshes = []
    asian.merge_asian_context_payload(vcp_data, {}, lambda: refreshes.append(True))
    assert refreshes == []
    asian.merge_asian_context_payload(
        vcp_data,
        {"track": "Foundry ", "market": " Taiwan", "currency": "TWD"},
        lambda: refreshes.append(True),
    )
    assert vcp_data["track"] == "Foundry"
    assert vcp_data["market"] == "Taiwan"
    assert vcp_data["currency"] == "TWD"
    assert refreshes == [True]
    assert (
        asian.build_asian_history_df(
            None,
            vcp_data=vcp_data,
            refresh_header_context=lambda: None,
            normalize_daily_df_index=lambda value: value,
        )
        is None
    )


def test_render_asian_history_payload_all_outcomes():
    statuses = []
    renders = []
    base = dict(
        vcp_data={},
        _refresh_header_context=lambda: None,
        _normalize_daily_df_index=lambda frame: frame,
        _set_status_message=lambda message, **kwargs: statuses.append((message, kwargs)),
        _render_chart=lambda frame, **kwargs: renders.append((frame, kwargs)),
    )
    assert asian.render_asian_history_payload(SimpleNamespace(_closing=True, **base), {}) is False
    assert asian.render_asian_history_payload(SimpleNamespace(_closing=False, **base), None) is False
    assert asian.render_asian_history_payload(SimpleNamespace(_closing=False, **base), {"klines": []}) is False
    assert (
        asian.render_asian_history_payload(
            SimpleNamespace(_closing=False, **base),
            {"track": "AI", "klines": [{"date": "2026-07-15", "open": 1, "high": 2, "low": 1, "close": 2}]},
        )
        is True
    )
    assert len(renders) == 1
    assert renders[0][1] == {"loading": False}
    assert statuses[-1][1]["tone"] == "success"


class _CapturedLifecycle:
    def __init__(self):
        self.kwargs = None

    def run_background(self, name, fn, **kwargs):
        self.name = name
        self.fn = fn
        self.kwargs = kwargs


def test_schedule_asian_backfill_success_error_and_stale(monkeypatch):
    lifecycle = _CapturedLifecycle()
    monkeypatch.setattr(asian, "task_lifecycle_for", lambda *args, **kwargs: lifecycle)
    statuses = []
    rendered = []
    window = SimpleNamespace(
        _closing=False,
        code=" 2330.TW ",
        name=" TSMC ",
        _render_generation=3,
        vcp_data={},
        _refresh_header_context=lambda: None,
        _normalize_daily_df_index=lambda frame: frame,
        _set_status_message=lambda message, **kwargs: statuses.append((message, kwargs)),
        _render_chart=lambda frame, **kwargs: rendered.append(frame),
    )

    def fetch(name, code, period):
        return {"klines": [{"date": "2026-07-15", "open": 1, "high": 2, "low": 1, "close": 2}]}

    asian.schedule_asian_history_backfill(window, task_manager=object(), fetch_single_kline=fetch)
    assert lifecycle.name == "asian_history_backfill"
    assert lifecycle.fn(None)["klines"]
    assert lifecycle.kwargs["timeout_sec"] == 120.0
    lifecycle.kwargs["on_success"](lifecycle.fn(None))
    assert rendered
    lifecycle.kwargs["on_error"]("network")
    assert statuses[-1][1]["tone"] == "error"

    window.code = "7203.T"
    before = len(statuses)
    lifecycle.kwargs["on_success"](None)
    lifecycle.kwargs["on_error"]("late")
    assert len(statuses) == before
    asian.schedule_asian_history_backfill(
        SimpleNamespace(_closing=True), task_manager=object(), fetch_single_kline=fetch
    )


def test_build_asian_rt_df_and_coerce_trade_date():
    assert asian.build_asian_rt_df(None) is None
    assert asian.build_asian_rt_df(pd.DataFrame()) is None
    frame = asian.build_asian_rt_df(_vendor_frame(tz="Asia/Shanghai"))
    assert frame.index.tz is None
    assert list(frame.columns) == ["open", "high", "low", "close", "volume"]
    no_volume = asian.build_asian_rt_df(_vendor_frame(volume=False))
    assert no_volume["volume"].tolist() == [0.0, 0.0]
    assert asian._coerce_trade_date(None) is None
    assert asian._coerce_trade_date("invalid") is None
    assert asian._coerce_trade_date("2026-07-15") == dt.date(2026, 7, 15)


def test_build_asian_rt_quote_prefers_intraday_and_handles_fallbacks(monkeypatch):
    latest = dt.date(2026, 7, 15)
    assert asian.build_asian_rt_quote("2330.TW", {}, market="TW", latest_trade_date=latest) is None
    assert asian.build_asian_rt_quote("2330.TW", {"close": 1}, market="TW", latest_trade_date=None) is None

    intraday = _vendor_frame(tz="Asia/Taipei")
    result = asian.build_asian_rt_quote("2330.TW", {"df_today": intraday}, market="TW", latest_trade_date=latest)
    assert result == {
        "date": "2026-07-15",
        "open": 11.0,
        "high": 13.0,
        "low": 10.0,
        "close": 12.0,
        "volume": 200.0,
    }

    malformed = pd.DataFrame({"Open": [object()]}, index=[pd.Timestamp("2026-07-15")])
    assert (
        asian.build_asian_rt_quote(
            "2330.TW", {"df_today": malformed, "close": 0}, market="TW", latest_trade_date=latest
        )
        is None
    )
    assert (
        asian.build_asian_rt_quote(
            "2330.TW", {"close": 2, "open": 0, "date": "2026-07-15"}, market="TW", latest_trade_date=latest
        )
        is None
    )
    assert (
        asian.build_asian_rt_quote("2330.TW", {"close": 2, "date": "bad"}, market="TW", latest_trade_date=latest)
        is None
    )
    monkeypatch.setattr(asian.MarketCalendar, "is_trade_day", lambda *args, **kwargs: False)
    assert (
        asian.build_asian_rt_quote(
            "2330.TW", {"open": 1, "close": 2, "date": "2026-07-15"}, market="TW", latest_trade_date=latest
        )
        is None
    )
    monkeypatch.setattr(asian.MarketCalendar, "is_trade_day", lambda *args, **kwargs: True)
    capped = asian.build_asian_rt_quote(
        "2330.TW",
        {"open": 10, "close": 12, "high": -1, "low": -1, "date": "2026-07-16", "volume": 9},
        market="TW",
        latest_trade_date=latest,
    )
    assert capped == {"date": "2026-07-15", "open": 10.0, "high": 12.0, "low": 10.0, "close": 12.0, "volume": 9.0}


def test_apply_asian_live_quote_merge_update_append_and_reject(monkeypatch):
    base = _daily_frame()
    monkeypatch.setattr(asian.MarketCalendar, "get_latest_trade_date", lambda market: dt.date(2026, 7, 15))
    monkeypatch.setattr(asian.MarketCalendar, "is_quote_refresh_time", lambda market: False)

    assert asian.apply_asian_live_quote(base.iloc[:0], {}, market="TW").empty
    unchanged = asian.apply_asian_live_quote(base, {"close": None}, market="TW")
    pd.testing.assert_frame_equal(unchanged, base)

    merged = asian.apply_asian_live_quote(
        base,
        {"df_today": _vendor_frame(), "close": 12, "date": None},
        market="TW",
    )
    assert list(merged.index) == [pd.Timestamp("2026-07-14"), pd.Timestamp("2026-07-15")]
    assert float(merged.iloc[-1]["close"]) == 12.0

    updated = asian.apply_asian_live_quote(
        base,
        {"open": 10.2, "high": 12.0, "low": 8.0, "close": 11.0, "date": "2026-07-14"},
        market="TW",
    )
    assert updated.iloc[-1].to_dict() == {"open": 10.2, "high": 12.0, "low": 8.0, "close": 11.0, "volume": 100.0}

    not_refreshing = asian.apply_asian_live_quote(base, {"close": 12, "date": "2026-07-16"}, market="TW")
    assert len(not_refreshing) == 1
    appended = asian.apply_asian_live_quote(base, {"close": 12, "date": "2026-07-15"}, market="TW")
    assert len(appended) == 2
    assert appended.iloc[-1].to_dict() == {"open": 12.0, "high": 12.0, "low": 12.0, "close": 12.0, "volume": 0.0}
    zero = asian.apply_asian_live_quote(base, {"close": 0, "date": "2026-07-15"}, market="TW")
    assert len(zero) == 1


class _NetworkLifecycle:
    def __init__(self):
        self.calls = []

    def run_background(self, name, fn, **kwargs):
        self.calls.append((name, fn, kwargs))


def test_network_online_toggle_and_failure_callback(monkeypatch):
    lifecycle = _NetworkLifecycle()
    monkeypatch.setattr(network, "task_lifecycle_for", lambda *args, **kwargs: lifecycle)
    states = []

    class Provider:
        online = False

        def is_online(self):
            return self.online

        def set_online_mode(self, enabled):
            self.online = enabled

    window = SimpleNamespace(
        data_provider=Provider(),
        _call_in_ui=lambda callback: callback(),
        _update_network_ui=lambda online: states.append(online),
    )
    network.toggle_network(window)
    name, fn, kwargs = lifecycle.calls[0]
    assert name == "network_mode"
    assert fn(None) is True
    kwargs["on_success"](1)
    assert states == [True]
    kwargs["on_error"]("offline")
    assert states == [True, False]

    window.data_provider.online = True
    network.toggle_network(window)
    assert states[-1] is False


def test_network_ui_updates_action_status_bar_and_dot():
    texts = []
    tones = []
    colors = []
    window = SimpleNamespace(
        act_network=SimpleNamespace(setText=lambda text: texts.append(text)),
        _status_bar_widget=SimpleNamespace(set_status_tone=lambda tone: tones.append(tone)),
        status_dot=SimpleNamespace(set_color=lambda color: colors.append(color)),
    )
    network.update_network_ui(window, True, detail="connected")
    network.update_network_ui(window, False)
    assert tones == ["online", "offline"]
    assert colors == ["#22C55E", "#EF4444"]
    assert len(texts) == 2


def test_network_ui_guards_and_force_reconnect_outcomes(monkeypatch):
    assert network._resolve_status_dot_color("busy") == "#F59E0B"
    assert network._resolve_status_dot_color("unknown") == "#EF4444"
    network.update_network_ui(SimpleNamespace(), True)

    lifecycle = _NetworkLifecycle()
    monkeypatch.setattr(network, "task_lifecycle_for", lambda *args, **kwargs: lifecycle)
    toasts = []
    import ui.components.toast_widget as toast_module

    monkeypatch.setattr(toast_module, "show_toast", lambda *args, **kwargs: toasts.append((args, kwargs)))

    class Provider:
        def __init__(self, online=True, ok=True, raises=False):
            self.online, self.ok, self.raises = online, ok, raises

        def is_online(self):
            return self.online

        def force_reconnect_servers(self):
            if self.raises:
                raise OSError("down")

        def test_network(self, timeout):
            assert timeout == 2
            return self.ok

    tones = []
    states = []
    window = SimpleNamespace(
        data_provider=Provider(online=False),
        _status_bar_widget=SimpleNamespace(set_status_tone=lambda tone: tones.append(tone)),
        _call_in_ui=lambda callback: callback(),
        _update_network_ui=lambda online: states.append(online),
    )
    network.force_reconnect(window)
    assert lifecycle.calls == []

    for provider, expected_ok in [
        (Provider(ok=True), True),
        (Provider(ok=False), False),
        (Provider(raises=True), False),
    ]:
        window.data_provider = provider
        network.force_reconnect(window)
        _, fn, kwargs = lifecycle.calls[-1]
        assert fn(None) is expected_ok
        kwargs["on_success"](expected_ok)
    assert tones == ["busy", "busy", "busy"]
    assert states == [True, True, True]
    assert len(toasts) == 3
    assert "success" in toasts[0][0]
    assert all("error" in item[0] for item in toasts[1:])


@pytest.mark.parametrize("status_bar,status_dot", [(None, None), (object(), object())])
def test_set_status_tone_tolerates_absent_capabilities(status_bar, status_dot):
    network._set_status_tone(
        SimpleNamespace(_status_bar_widget=status_bar, status_dot=status_dot),
        "busy",
    )
