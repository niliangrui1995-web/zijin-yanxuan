# -*- coding: utf-8 -*-
from __future__ import annotations

import datetime as dt
from types import SimpleNamespace

import pandas as pd
import pytest

from ui import kline_window_runtime as runtime


class _Log:
    def __init__(self):
        self.debugs = []
        self.warnings = []

    def debug(self, message):
        self.debugs.append(message)

    def warning(self, message):
        self.warnings.append(message)


def _frame(date: str = "2026-07-14", *, volume: bool = True) -> pd.DataFrame:
    data = {"open": [10.0], "high": [11.0], "low": [9.0], "close": [10.5]}
    if volume:
        data["volume"] = [100.0]
    return pd.DataFrame(data, index=[pd.Timestamp(date)])


def test_current_request_and_owned_task_registration(monkeypatch):
    window = SimpleNamespace(_closing=False, code=" 000001 ", _render_generation=2)
    assert runtime._is_current_request(window, "000001", 2)
    window._closing = True
    assert not runtime._is_current_request(window, "000001", 2)
    window._closing = False
    assert not runtime._is_current_request(window, "000002", 2)
    assert not runtime._is_current_request(window, "000001", 3)

    calls = []
    lifecycle = SimpleNamespace(run_background=lambda *args, **kwargs: calls.append((args, kwargs)))
    monkeypatch.setattr(runtime, "task_lifecycle_for", lambda *args, **kwargs: lifecycle)
    runtime._submit_owned_window_task(window, "name", lambda _: 1, lambda value: value, "suffix", 12.5)
    assert calls[0][0][0] == "name"
    assert calls[0][1]["timeout_sec"] == 12.5
    assert "suffix" in str(calls[0][1]["task_id"])


def test_normalize_daily_df_index_invalid_duplicate_tz_and_exception(monkeypatch):
    log = _Log()
    assert runtime.normalize_daily_df_index(None, logger=log) is None
    empty = pd.DataFrame()
    assert runtime.normalize_daily_df_index(empty, logger=log) is empty

    frame = pd.DataFrame(
        {"close": [1, 2, 3, 4]},
        index=["bad", "2026-07-15 12:00", "2026-07-14", "2026-07-15 15:00"],
    )
    normalized = runtime.normalize_daily_df_index(frame, logger=log)
    assert normalized.index.tolist() == [pd.Timestamp("2026-07-14"), pd.Timestamp("2026-07-15")]
    assert normalized["close"].tolist() == [3, 4]

    tz_frame = pd.DataFrame({"close": [1]}, index=pd.DatetimeIndex(["2026-07-15 15:00"], tz="Asia/Shanghai"))
    assert runtime.normalize_daily_df_index(tz_frame, logger=log).index.tz is None
    all_invalid = pd.DataFrame({"close": [1]}, index=["bad"])
    assert runtime.normalize_daily_df_index(all_invalid, logger=log).empty

    original = pd.DataFrame({"close": [1]}, index=["2026-07-15"])
    monkeypatch.setattr(
        runtime.pd, "to_datetime", lambda *args, **kwargs: (_ for _ in ()).throw(TypeError("bad index"))
    )
    assert runtime.normalize_daily_df_index(original, logger=log) is original
    assert log.debugs


def test_resolve_quote_trade_date_all_calendar_boundaries(monkeypatch):
    last = dt.date(2026, 7, 14)
    latest = dt.date(2026, 7, 15)
    monkeypatch.setattr(runtime.MarketCalendar, "is_quote_refresh_time", lambda market: True)
    monkeypatch.setattr(runtime.MarketCalendar, "is_trade_day", lambda date, market: True)
    assert (
        runtime._resolve_quote_trade_date(market="CN", raw_quote_date=None, last_date=last, latest_trade_date=latest)
        == latest
    )
    assert (
        runtime._resolve_quote_trade_date(market="US", raw_quote_date=None, last_date=last, latest_trade_date=latest)
        == last
    )
    assert (
        runtime._resolve_quote_trade_date(market="CN", raw_quote_date="bad", last_date=last, latest_trade_date=None)
        == last
    )
    monkeypatch.setattr(runtime.MarketCalendar, "is_trade_day", lambda date, market: False)
    assert (
        runtime._resolve_quote_trade_date(
            market="CN", raw_quote_date="2026-07-15", last_date=last, latest_trade_date=latest
        )
        == last
    )
    monkeypatch.setattr(runtime.MarketCalendar, "is_trade_day", lambda date, market: True)
    assert (
        runtime._resolve_quote_trade_date(
            market="CN", raw_quote_date="2026-07-16", last_date=last, latest_trade_date=latest
        )
        == latest
    )
    assert (
        runtime._resolve_quote_trade_date(
            market="CN", raw_quote_date="2026-07-14", last_date=last, latest_trade_date=latest
        )
        == last
    )


def test_merge_cn_realtime_bar_update_append_and_guards(monkeypatch):
    base = _frame()
    target = dt.date(2026, 7, 15)
    monkeypatch.setattr(runtime.MarketCalendar, "is_quote_refresh_time", lambda market: True)
    monkeypatch.setattr(runtime.MarketCalendar, "is_trade_day", lambda date, market: True)
    pd.testing.assert_frame_equal(runtime._merge_cn_realtime_bar(base, {}, target_trade_date=target), base)
    pd.testing.assert_frame_equal(runtime._merge_cn_realtime_bar(base, {"open": 1}, target_trade_date=None), base)

    updated = runtime._merge_cn_realtime_bar(
        base,
        {"date": "2026-07-14", "open": 10.2, "high": 12, "low": 8, "close": 11, "volume": 9},
        target_trade_date=target,
    )
    assert updated.iloc[-1].to_dict() == {"open": 10.2, "high": 12.0, "low": 8.0, "close": 11.0, "volume": 9.0}
    no_volume = runtime._merge_cn_realtime_bar(
        _frame(volume=False),
        {"date": "2026-07-14", "open": 10.2, "high": 0, "low": 0, "close": 0},
        target_trade_date=target,
    )
    assert no_volume.iloc[-1].to_dict() == {"open": 10.2, "high": 11.0, "low": 9.0, "close": 10.5}

    appended = runtime._merge_cn_realtime_bar(
        base,
        {"date": "2026-07-15", "open": 12, "high": 0, "low": 0, "close": 11, "volume": 20},
        target_trade_date=target,
    )
    assert len(appended) == 2
    assert appended.iloc[-1].to_dict() == {"open": 12.0, "high": 12.0, "low": 11.0, "close": 11.0, "volume": 20.0}
    same = runtime._merge_cn_realtime_bar(
        base,
        {"date": "2026-07-15", "open": 10, "high": 11, "low": 9, "close": 10.5},
        target_trade_date=target,
    )
    assert len(same) == 1
    monkeypatch.setattr(runtime.MarketCalendar, "is_quote_refresh_time", lambda market: False)
    assert (
        len(
            runtime._merge_cn_realtime_bar(
                base, {"date": "2026-07-15", "open": 12, "close": 12}, target_trade_date=target
            )
        )
        == 1
    )


class _Provider:
    def __init__(self, *, local=None, fresh=None, online=False, quote=None, fresh_type_error=False, quote_error=None):
        self.local = local
        self.fresh = fresh
        self.online = online
        self.quote = quote
        self.fresh_type_error = fresh_type_error
        self.quote_error = quote_error
        self.fresh_calls = []

    def get_data(self, code):
        return self.local

    def get_data_fresh_for_chart(self, code, *args, **kwargs):
        self.fresh_calls.append((args, kwargs))
        if self.fresh_type_error and kwargs:
            raise TypeError("legacy")
        return self.fresh

    def fetch_realtime_quotes_batch(self, codes):
        if self.quote_error:
            raise self.quote_error
        return {codes[0]: self.quote} if self.quote is not None else {}

    def is_online(self):
        return self.online


def _capture_submit(monkeypatch):
    calls = []
    monkeypatch.setattr(
        runtime,
        "_submit_owned_window_task",
        lambda window, name, fn, on_success, suffix, timeout: calls.append(
            SimpleNamespace(window=window, name=name, fn=fn, success=on_success, suffix=suffix, timeout=timeout)
        ),
    )
    return calls


def _load_window(provider, *, code="000001", generation=2, target=dt.date(2026, 7, 15)):
    log = _Log()
    statuses = []
    renders = []
    window = SimpleNamespace(
        _closing=False,
        code=code,
        _render_generation=generation,
        data_provider=provider,
        _log=log,
        _get_cn_target_trade_date=lambda: target,
        _set_status_message=lambda message, **kwargs: statuses.append((message, kwargs)),
        _render_chart=lambda frame, **kwargs: renders.append((frame.copy(), kwargs)),
        _load_asian_chart=lambda: statuses.append(("asian", {})),
    )
    return window, log, statuses, renders


def test_load_and_draw_closing_asian_and_sync_fallback(monkeypatch):
    calls = _capture_submit(monkeypatch)
    runtime.load_and_draw(SimpleNamespace(_closing=True))
    assert calls == []
    asian_window, _, statuses, _ = _load_window(_Provider(), code="2330.TW")
    runtime.load_and_draw(asian_window)
    assert statuses == [("asian", {})]

    monkeypatch.setattr(runtime, "is_provider_online", lambda provider: provider.online)
    monkeypatch.setattr(runtime.MarketCalendar, "is_quote_refresh_time", lambda market: True)
    monkeypatch.setattr(runtime.MarketCalendar, "is_trade_day", lambda date, market: True)
    provider = _Provider(
        local=None,
        fresh=_frame(),
        online=True,
        quote={"date": "2026-07-15", "open": 10, "high": 12, "low": 9, "close": 11, "volume": 5},
        fresh_type_error=True,
    )
    window, _, statuses, renders = _load_window(provider)
    runtime.load_and_draw(window)
    task = calls[-1]
    result = task.fn(None)
    assert len(provider.fresh_calls) == 2
    assert result[1]["close"] == 11
    task.success(result)
    assert len(renders) == 1 and len(renders[0][0]) == 2
    assert statuses[0][1]["tone"] == "loading"


def test_load_and_draw_cached_quote_error_and_callback_guards(monkeypatch):
    calls = _capture_submit(monkeypatch)
    monkeypatch.setattr(runtime, "is_provider_online", lambda provider: provider.online)
    monkeypatch.setattr(runtime.MarketCalendar, "is_quote_refresh_time", lambda market: True)
    provider = _Provider(
        local=_frame("2026-07-15"),
        fresh=_frame(),
        online=True,
        quote_error=OSError("down"),
    )
    window, log, statuses, renders = _load_window(provider)
    runtime.load_and_draw(window)
    task = calls[-1]
    result = task.fn(None)
    assert provider.fresh_calls == []
    assert result[1] is None
    assert log.warnings == []  # latest local row avoids a realtime request
    task.success(result)
    assert renders

    provider.local = _frame("2026-07-14")
    provider.fresh = _frame("2026-07-14")
    runtime.load_and_draw(window)
    result = calls[-1].fn(None)
    assert log.warnings

    before = len(renders)
    calls[-1].success(None)
    calls[-1].success((pd.DataFrame(), None, dt.date(2026, 7, 15)))
    assert statuses[-1][1]["tone"] == "error"
    window.code = "000002"
    calls[-1].success(result)
    assert len(renders) == before

    window.code = "000001"
    window._render_chart = lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("closed"))
    calls[-1].success(result)


def _poll_window(*, market="CN", code="000001", provider=None, asian_quote=None):
    provider = provider or _Provider(quote={"open": 1, "close": 2})
    log = _Log()
    timer = SimpleNamespace(stopped=False, stop=lambda: setattr(timer, "stopped", True))
    return (
        SimpleNamespace(
            _get_market=lambda: market,
            _apply_chart_market_state=lambda: setattr(timer, "state_applied", True),
            _rt_timer=timer,
            _log=log,
            code=code,
            _render_generation=1,
            data_provider=provider,
            _build_asian_rt_quote=lambda: asian_quote,
            df=_frame(),
            browser=SimpleNamespace(page=lambda: SimpleNamespace(runJavaScript=lambda script: None)),
            _set_status_message=lambda *args, **kwargs: None,
        ),
        timer,
        log,
    )


def test_poll_rt_update_closed_market_and_asian_fast_paths(monkeypatch):
    calls = _capture_submit(monkeypatch)
    window, timer, log = _poll_window()
    monkeypatch.setattr(runtime.MarketCalendar, "is_quote_refresh_time", lambda market: False)
    runtime.poll_rt_update(window)
    assert timer.stopped and timer.state_applied and log.debugs
    window._rt_timer = None
    runtime.poll_rt_update(window)
    assert calls == []

    monkeypatch.setattr(runtime.MarketCalendar, "is_quote_refresh_time", lambda market: True)
    monkeypatch.setattr(runtime.MarketCalendar, "get_latest_trade_date", lambda market: dt.date(2026, 7, 15))
    monkeypatch.setattr(runtime.MarketCalendar, "is_trade_day", lambda date, market: True)
    asian_window, _, _ = _poll_window(
        market="TW", asian_quote={"date": "2026-07-15", "open": 1, "high": 2, "low": 1, "close": 2}
    )
    runtime.poll_rt_update(asian_window)
    assert calls == []
    monkeypatch.setattr(runtime, "get_yf_rate_limit_status", lambda: {"active": True})
    limited, _, _ = _poll_window(market="TW", asian_quote=None)
    runtime.poll_rt_update(limited)
    assert calls == []


def test_poll_rt_update_background_result_and_errors(monkeypatch):
    calls = _capture_submit(monkeypatch)
    monkeypatch.setattr(runtime.MarketCalendar, "is_quote_refresh_time", lambda market: True)
    monkeypatch.setattr(runtime, "get_yf_rate_limit_status", lambda: {"active": False})
    refreshed = []
    monkeypatch.setattr(runtime, "refresh_last_bar", lambda window, quote: refreshed.append(quote))

    provider = _Provider(quote={"open": 1, "close": 2})
    window, _, log = _poll_window(provider=provider)
    runtime.poll_rt_update(window)
    task = calls[-1]
    assert task.fn(None)["close"] == 2
    task.success(None)
    assert refreshed == []
    task.success({"open": 1, "close": 2})
    assert refreshed
    window.code = "000002"
    task.success({"open": 2, "close": 3})
    assert len(refreshed) == 1

    window.code = "000001"
    provider.quote_error = ValueError("bad")
    error = task.fn(None)
    assert isinstance(error, ValueError)
    task.success(error)
    assert log.warnings

    monkeypatch.setattr(runtime, "is_yf_rate_limit_error", lambda exc: True)
    monkeypatch.setattr(runtime, "mark_yf_rate_limited", lambda exc: 42.0)
    task.success(RuntimeError("429"))
    assert "42" in log.warnings[-1]

    monkeypatch.setattr(runtime, "is_yf_rate_limit_error", lambda exc: False)
    with pytest.raises(RuntimeError, match="No active exception"):
        task.success(AssertionError("unexpected"))


def test_poll_rt_update_asian_background_fetch(monkeypatch):
    calls = _capture_submit(monkeypatch)
    monkeypatch.setattr(runtime.MarketCalendar, "is_quote_refresh_time", lambda market: True)
    monkeypatch.setattr(runtime, "get_yf_rate_limit_status", lambda: {"active": False})
    import ui.tabs.asian_market_workers as workers

    monkeypatch.setattr(workers, "fetch_asian_realtime_quote", lambda code: {"code": code})
    window, _, _ = _poll_window(market="TW", code="2330.TW")
    runtime.poll_rt_update(window)
    assert calls[-1].fn(None) == {"code": "2330.TW"}


def test_refresh_last_bar_guards_update_append_and_message(monkeypatch):
    monkeypatch.setattr(runtime.MarketCalendar, "get_latest_trade_date", lambda market: dt.date(2026, 7, 15))
    monkeypatch.setattr(runtime.MarketCalendar, "is_trade_day", lambda date, market: True)
    scripts = []
    statuses = []
    page = SimpleNamespace(runJavaScript=lambda script: scripts.append(script))
    window = SimpleNamespace(
        df=None,
        _get_market=lambda: "CN",
        browser=SimpleNamespace(page=lambda: page),
        _set_status_message=lambda message, **kwargs: statuses.append((message, kwargs)),
    )
    runtime.refresh_last_bar(window, {})
    window.df = pd.DataFrame()
    runtime.refresh_last_bar(window, {})
    window.df = _frame()
    runtime.refresh_last_bar(window, {"open": 0, "close": 1})
    assert scripts == []

    runtime.refresh_last_bar(
        window,
        {"date": "2026-07-14", "open": 10.2, "high": 12, "low": 8, "close": 11, "volume": 9},
    )
    assert window.df.iloc[-1].to_dict() == {"open": 10.2, "high": 12.0, "low": 8.0, "close": 11.0, "volume": 9.0}
    assert "updateLastBar" in scripts[-1]
    assert statuses[-1][1]["tone"] == "realtime"

    runtime.refresh_last_bar(
        window,
        {"date": "2026-07-15", "open": 12, "high": 0, "low": 0, "close": 10, "volume": 5},
    )
    assert len(window.df) == 2
    assert window.df.iloc[-1].to_dict() == {"open": 12.0, "high": 12.0, "low": 10.0, "close": 10.0, "volume": 5.0}
    assert "+" not in statuses[-1][0]

    zero_preclose = _frame()
    zero_preclose.iloc[0, zero_preclose.columns.get_loc("close")] = 0
    window.df = zero_preclose
    runtime.refresh_last_bar(
        window,
        {"date": "2026-07-15", "open": 1, "high": 2, "low": 1, "close": 2},
    )
    assert len(window.df) == 2


def test_refresh_last_bar_without_volume_and_without_high_low(monkeypatch):
    monkeypatch.setattr(runtime.MarketCalendar, "get_latest_trade_date", lambda market: dt.date(2026, 7, 14))
    monkeypatch.setattr(runtime.MarketCalendar, "is_trade_day", lambda date, market: True)
    scripts = []
    window = SimpleNamespace(
        df=_frame(volume=False),
        _get_market=lambda: "CN",
        browser=SimpleNamespace(page=lambda: SimpleNamespace(runJavaScript=lambda script: scripts.append(script))),
        _set_status_message=lambda *args, **kwargs: None,
    )
    runtime.refresh_last_bar(window, {"open": 10, "high": 0, "low": 0, "close": 9, "volume": 7})
    assert window.df.iloc[-1].to_dict() == {"open": 10.0, "high": 11.0, "low": 9.0, "close": 9.0}
    assert scripts
