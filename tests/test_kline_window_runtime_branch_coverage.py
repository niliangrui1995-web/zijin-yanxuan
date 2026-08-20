# -*- coding: utf-8 -*-
from __future__ import annotations

import datetime as dt
import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pandas as pd
import pytest

from app.services.kline_open_context import KlineOpenContext
from app.services.ui_task_lifecycle_service import CancellationToken
from ui import kline_window_runtime as runtime
from ui.kline_load_controller import KlineLoadController
from ui.kline_runtime_lifecycle import KLineRuntimeLifecycleController


def test_cn_runtime_import_does_not_eagerly_load_asian_network_stack():
    probe = """
import json
import sys
import ui.kline_window_runtime

print("__KLINE_IMPORT_CONTRACT__" + json.dumps({
    "asian_service": "app.services.asian_market_service" in sys.modules,
    "asian_fetcher": "vcp.fetchers.asian_kline_fetcher" in sys.modules,
    "curl_cffi": "curl_cffi" in sys.modules,
}))
"""
    completed = subprocess.run(
        [sys.executable, "-c", probe],
        cwd=Path(__file__).resolve().parents[1],
        capture_output=True,
        check=True,
        encoding="utf-8",
        errors="replace",
        timeout=30,
    )
    marker = "__KLINE_IMPORT_CONTRACT__"
    payload = next(line.removeprefix(marker) for line in completed.stdout.splitlines() if line.startswith(marker))

    assert json.loads(payload) == {
        "asian_service": False,
        "asian_fetcher": False,
        "curl_cffi": False,
    }


class _Log:
    def __init__(self):
        self.debugs = []
        self.warnings = []

    def debug(self, message):
        self.debugs.append(message)

    def warning(self, message):
        self.warnings.append(message)


class _Stages:
    def __init__(self):
        self.calls = []

    def record(self, stage):
        self.calls.append(stage)


class _Page:
    def __init__(self):
        self.calls = []

    def runJavaScript(self, script, callback=None):
        self.calls.append((script, callback))


def _frame(date: str = "2026-07-14", *, volume: bool = True) -> pd.DataFrame:
    data = {"open": [10.0], "high": [11.0], "low": [9.0], "close": [10.5]}
    if volume:
        data["volume"] = [100.0]
    return pd.DataFrame(data, index=[pd.Timestamp(date)])


def _history_frame(rows: int = 260, *, end: str = "2026-07-14") -> pd.DataFrame:
    closes = [10.0 + index * 0.037 + (index % 7) * 0.011 for index in range(rows)]
    return pd.DataFrame(
        {
            "open": [value - 0.12 for value in closes],
            "high": [value + 0.31 for value in closes],
            "low": [value - 0.29 for value in closes],
            "close": closes,
            "volume": [10_000.0 + index * 13 for index in range(rows)],
        },
        index=pd.date_range(end=end, periods=rows, freq="D"),
    )


def _context(code: str, name: str = "平安银行") -> KlineOpenContext:
    return KlineOpenContext(
        code=code,
        name=name,
        vcp_data={"代码": code, "名称": name, "来源": "关注池"},
        source_tab_key="watchlist",
        source_tab_index=0,
    )


def test_current_request_and_owned_task_registration(monkeypatch):
    controller = KlineLoadController(window_id="identity-window")
    identity = controller.begin("000001")
    window = SimpleNamespace(_closing=False, code="000001", _load_controller=controller)

    assert runtime._is_current_request(window, identity.code, identity.generation)
    window._closing = True
    assert not runtime._is_current_request(window, identity.code, identity.generation)
    window._closing = False
    assert not runtime._is_current_request(window, "000002", identity.generation)
    assert not runtime._is_current_request(window, identity.code, identity.generation + 1)

    controller.begin("000002")
    assert not runtime._is_current_request(window, identity.code, identity.generation)

    calls = []
    lifecycle = SimpleNamespace(
        run_background=lambda *args, **kwargs: calls.append((args, kwargs)),
        cancel=lambda *_args, **_kwargs: True,
    )
    monkeypatch.setattr(runtime, "task_lifecycle_for", lambda *args, **kwargs: lifecycle)
    task_id = controller.task_id("history")
    runtime._submit_owned_window_task(window, "history_load", lambda _: 1, lambda value: value, task_id, 12.5)

    assert calls[0][0][0] == "history_load"
    assert calls[0][1]["timeout_sec"] == 12.5
    assert str(calls[0][1]["task_id"]) == "kline:identity-window:2:history"
    assert len(window._active_kline_task_tickets) == 1
    running_ticket = next(iter(window._active_kline_task_tickets))
    assert running_ticket.identity == controller.current_identity
    assert running_ticket.stage == "history"
    calls[0][1]["on_terminated"]()
    assert window._active_kline_task_tickets == set()


def test_rejected_owned_task_reports_error_instead_of_staying_loading(monkeypatch):
    controller = KlineLoadController(window_id="rejected-window")
    identity = controller.begin("002156")
    window = SimpleNamespace(_closing=False, code="002156", _load_controller=controller)
    token = CancellationToken()
    token.cancel("owner_shutdown")

    def _reject(*_args, **kwargs):
        kwargs["on_terminated"]()
        return token

    lifecycle = SimpleNamespace(run_background=_reject)
    monkeypatch.setattr(runtime, "task_lifecycle_for", lambda *args, **kwargs: lifecycle)
    errors: list[str] = []

    runtime._submit_owned_window_task(
        window,
        "history_load",
        lambda _token: None,
        lambda _result: None,
        controller.task_id("history", identity=identity),
        120.0,
        on_error=errors.append,
        identity=identity,
    )

    assert errors == ["后台任务未启动: owner_shutdown"]
    assert window._active_kline_task_tickets == set()
    assert controller.running_task is None


def test_resolve_quote_trade_date_all_calendar_boundaries(monkeypatch):
    last = dt.date(2026, 7, 14)
    latest = dt.date(2026, 7, 15)
    monkeypatch.setattr(runtime.MarketCalendar, "is_quote_refresh_time", lambda market: True)
    monkeypatch.setattr(runtime.MarketCalendar, "is_trade_day", lambda date, market: True)
    assert (
        runtime._resolve_quote_trade_date(
            market="CN",
            raw_quote_date=None,
            last_date=last,
            latest_trade_date=latest,
        )
        == latest
    )
    assert (
        runtime._resolve_quote_trade_date(
            market="US",
            raw_quote_date=None,
            last_date=last,
            latest_trade_date=latest,
        )
        == last
    )
    assert (
        runtime._resolve_quote_trade_date(
            market="CN",
            raw_quote_date="bad",
            last_date=last,
            latest_trade_date=None,
        )
        == last
    )
    monkeypatch.setattr(runtime.MarketCalendar, "is_trade_day", lambda date, market: False)
    assert (
        runtime._resolve_quote_trade_date(
            market="CN",
            raw_quote_date="2026-07-15",
            last_date=last,
            latest_trade_date=latest,
        )
        == last
    )
    monkeypatch.setattr(runtime.MarketCalendar, "is_trade_day", lambda date, market: True)
    assert (
        runtime._resolve_quote_trade_date(
            market="CN",
            raw_quote_date="2026-07-16",
            last_date=last,
            latest_trade_date=latest,
        )
        == latest
    )
    assert (
        runtime._resolve_quote_trade_date(
            market="CN",
            raw_quote_date="2026-07-14",
            last_date=last,
            latest_trade_date=latest,
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
    assert updated.iloc[-1].to_dict() == {
        "open": 10.2,
        "high": 12.0,
        "low": 8.0,
        "close": 11.0,
        "volume": 9.0,
    }
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
    assert appended.iloc[-1].to_dict() == {
        "open": 12.0,
        "high": 12.0,
        "low": 11.0,
        "close": 11.0,
        "volume": 20.0,
    }
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
                base,
                {"date": "2026-07-15", "open": 12, "close": 12},
                target_trade_date=target,
            )
        )
        == 1
    )


class _Provider:
    def __init__(self, *, local=None, fresh=None, online=False, quote=None, quote_error=None):
        self.local = local
        self.fresh = fresh
        self.online = online
        self.quote = quote
        self.quote_error = quote_error
        self.fresh_calls = []

    def get_data(self, code, *, cancellation_token=None):
        return self.local

    def get_data_fresh_for_chart(self, code, *args, cancellation_token=None, **kwargs):
        self.fresh_calls.append((args, kwargs, cancellation_token))
        return self.fresh

    def fetch_realtime_quotes_batch(self, codes):
        if self.quote_error:
            raise self.quote_error
        return {codes[0]: self.quote} if self.quote is not None else {}

    def is_online(self):
        return self.online


def _capture_submit(monkeypatch):
    calls = []

    def _capture(
        window,
        name,
        fn,
        on_success,
        task_id,
        timeout,
        *,
        on_error=None,
        on_terminated=None,
        on_finalized=None,
        on_discarded=None,
        identity=None,
    ):
        calls.append(
            SimpleNamespace(
                window=window,
                name=name,
                fn=fn,
                success=on_success,
                error=on_error,
                terminated=on_terminated,
                finalized=on_finalized,
                discarded=on_discarded,
                task_id=task_id,
                timeout=timeout,
                identity=identity,
            )
        )

    monkeypatch.setattr(runtime, "_submit_owned_window_task", _capture)
    return calls


def _runtime_window(
    provider,
    *,
    code="000001",
    name="平安银行",
    market=None,
    target=dt.date(2026, 7, 14),
    frame=None,
    asian_quote=None,
    window_id="runtime-window",
):
    log = _Log()
    statuses = []
    pending_statuses = []
    starts = []
    stages = _Stages()
    page = _Page()
    timer = SimpleNamespace(stopped=False)
    timer.stop = lambda: setattr(timer, "stopped", True)
    controller = KlineLoadController(window_id=window_id)
    identity = controller.begin(code)
    if frame is not None:
        assert controller.claim_frame(identity)
    context = _context(code, name)
    window = SimpleNamespace(
        _closing=False,
        _runtime_active=True,
        _render_generation=identity.generation,
        _load_controller=controller,
        _open_context=context,
        _snapshot_version=0,
        _runtime_lifecycle=KLineRuntimeLifecycleController(),
        _snapshot_inflight=None,
        _snapshot_inflight_browser=None,
        _snapshot_inflight_epoch=None,
        _pending_frame=None,
        _pending_prepared_render=None,
        _last_prepared_render=None,
        _last_rt_quote_fingerprint=None,
        _latest_rt_quote=None,
        _rt_prepare_inflight=False,
        _shell_loaded=True,
        _browser_epoch=1,
        _first_render_done=False,
        _open_stages=stages,
        _rt_timer=timer,
        code=code,
        name=name,
        current_idx=0,
        code_list=[{"代码": code, "名称": name, "__source_tab_key": "watchlist"}],
        vcp_data=context.mutable_vcp_data(),
        data_provider=provider,
        df=frame,
        browser=SimpleNamespace(page=lambda: page),
        _log=log,
        _get_cn_target_trade_date=lambda: target,
        _build_asian_rt_quote=lambda: asian_quote,
        _set_status_message=lambda message, tone="info": statuses.append((message, tone)),
        _set_pending_chart_status=lambda message, tone: pending_statuses.append((message, tone)),
        _finish_pending_chart_status=lambda: pending_statuses.append(("finished", "success")),
        _apply_chart_market_state=lambda: setattr(timer, "state_applied", True),
        _apply_chart_glass_mode=lambda: setattr(timer, "glass_applied", True),
        _start_rt_timer=lambda: starts.append(True),
    )
    window._get_market = lambda: market or runtime.MarketCalendar.infer_market(window.code)
    return SimpleNamespace(
        window=window,
        identity=identity,
        controller=controller,
        log=log,
        statuses=statuses,
        pending_statuses=pending_statuses,
        starts=starts,
        stages=stages,
        page=page,
        timer=timer,
    )


def _capture_queue(monkeypatch):
    queued = []

    def _queue(window, prepared, *args, **kwargs):
        frame = args[0] if args else prepared.display_frame
        queued.append(SimpleNamespace(window=window, prepared=prepared, frame=frame, kwargs=kwargs))
        return True

    monkeypatch.setattr(runtime, "queue_prepared_render", _queue)
    return queued


def _payload(prepared) -> dict:
    return json.loads(prepared.payload_json)


def _realtime_result_parts(result):
    if isinstance(result, tuple):
        return result
    return result.display_frame, result


def _ack_for(prepared) -> dict:
    return {
        "ok": True,
        "applied": True,
        "windowId": prepared.owner_id,
        "generation": prepared.generation,
        "snapshotVersion": prepared.snapshot_version,
        "code": prepared.code,
        "points": prepared.point_count,
    }


def test_load_and_draw_executes_data_service_and_preparer_off_thread(monkeypatch):
    calls = _capture_submit(monkeypatch)
    queued = _capture_queue(monkeypatch)
    source = _history_frame(260, end="2026-07-14")
    fixture = _runtime_window(
        _Provider(local=source),
        target=dt.date(2026, 7, 14),
        window_id="history-window",
    )

    runtime.load_and_draw(fixture.window, identity=fixture.identity)

    assert len(calls) == 1
    task = calls[0]
    assert task.name == "history_load"
    assert task.task_id == "kline:history-window:1:history"
    assert task.timeout == 120.0
    result = task.fn(None)
    assert result.data_result.code == "000001"
    assert result.prepared.identity == ("history-window", 1, "000001")
    assert result.prepared.point_count == 250
    assert _payload(result.prepared)["data"]["dates"][-1] == "2026-07-14"
    assert fixture.window.df is None

    task.success(result)

    assert len(queued) == 1
    assert queued[0].prepared is result.prepared
    assert fixture.window.df is None
    with pytest.raises(TypeError):
        fixture.window._open_context.vcp_data["来源"] = "changed"


def test_owned_window_queue_serializes_mixed_stages_and_keeps_only_latest_pending(monkeypatch):
    controller = KlineLoadController(window_id="serial-window")
    first = controller.begin("000001")
    calls = []
    cancellations = []
    discarded = []

    class _Lifecycle:
        def run_background(self, *args, **kwargs):
            calls.append((args, kwargs))

        def cancel(self, name, **kwargs):
            cancellations.append((name, kwargs))
            return True

    lifecycle = _Lifecycle()
    window = SimpleNamespace(_closing=False, _load_controller=controller, _task_lifecycle=lifecycle)
    monkeypatch.setattr(runtime, "task_lifecycle_for", lambda *_args, **_kwargs: lifecycle)

    runtime._submit_owned_window_task(
        window,
        "history_load",
        lambda _token: "history",
        lambda _result: None,
        controller.task_id("history", identity=first),
        120.0,
        identity=first,
    )
    second = controller.begin("000002")
    runtime._submit_owned_window_task(
        window,
        "render_prepare",
        lambda _token: "render",
        lambda _result: None,
        controller.task_id("render", identity=second),
        30.0,
        on_discarded=lambda: discarded.append("render"),
        identity=second,
    )
    latest = controller.begin("000003")
    runtime._submit_owned_window_task(
        window,
        "realtime_quote",
        lambda _token: "quote",
        lambda _result: None,
        controller.task_id("realtime-quote", identity=latest),
        20.0,
        identity=latest,
    )

    assert len(calls) == 1
    assert controller.running_task.identity == first
    assert controller.pending_task.identity == latest
    assert controller.pending_task.stage == "realtime-quote"
    assert cancellations[-1][0] == "history_load"
    assert discarded == ["render"]

    calls[0][1]["on_terminated"]()

    assert len(calls) == 2
    assert str(calls[-1][1]["task_id"]) == "kline:serial-window:3:realtime-quote"
    assert controller.running_task.identity == latest
    assert controller.pending_task is None

    calls[-1][1]["on_terminated"]()
    assert controller.running_task is None
    assert window._active_kline_task_tickets == set()


def test_owned_window_queue_close_drops_pending_until_running_task_really_terminates(monkeypatch):
    controller = KlineLoadController(window_id="closing-window")
    first = controller.begin("000001")
    calls = []
    discarded = []
    lifecycle = SimpleNamespace(
        run_background=lambda *args, **kwargs: calls.append((args, kwargs)),
        cancel=lambda *_args, **_kwargs: True,
    )
    window = SimpleNamespace(_closing=False, _load_controller=controller, _task_lifecycle=lifecycle)
    monkeypatch.setattr(runtime, "task_lifecycle_for", lambda *_args, **_kwargs: lifecycle)

    runtime._submit_owned_window_task(
        window,
        "history_load",
        lambda _token: "history",
        lambda _result: None,
        controller.task_id("history", identity=first),
        120.0,
        identity=first,
    )
    latest = controller.begin("000002")
    runtime._submit_owned_window_task(
        window,
        "realtime_quote",
        lambda _token: "quote",
        lambda _result: None,
        controller.task_id("realtime-quote", identity=latest),
        20.0,
        on_discarded=lambda: discarded.append("quote"),
        identity=latest,
    )

    controller.close()
    assert runtime._discard_pending_owned_window_task(window) is True
    assert controller.running_task is not None
    assert len(calls) == 1

    calls[0][1]["on_terminated"]()

    assert len(calls) == 1
    assert controller.running_task is None
    assert controller.pending_task is None
    assert window._active_kline_task_tickets == set()
    assert discarded == ["quote"]


def test_discarded_pending_realtime_prepare_is_requeued_after_promoted_render(monkeypatch):
    fixture = _runtime_window(
        _Provider(),
        frame=_history_frame(),
        window_id="discard-realtime",
    )
    controller = fixture.controller
    identity = fixture.identity
    calls = []
    lifecycle = SimpleNamespace(run_background=lambda *args, **kwargs: calls.append((args, kwargs)))
    window = fixture.window
    window._task_lifecycle = lifecycle
    monkeypatch.setattr(runtime, "task_lifecycle_for", lambda *_args, **_kwargs: lifecycle)

    runtime._submit_owned_window_task(
        window,
        "history_load",
        lambda _token: "history",
        lambda _result: None,
        controller.task_id("history", identity=identity),
        120.0,
        identity=identity,
    )
    quote = {
        "date": "2026-07-14",
        "open": 20,
        "high": 21,
        "low": 19,
        "close": 20.5,
        "volume": 1_000,
    }
    runtime.refresh_last_bar(window, quote)
    assert controller.pending_task.stage == "realtime"
    assert window._rt_prepare_inflight is True

    runtime._submit_owned_window_task(
        window,
        "render_prepare",
        lambda _token: "render",
        lambda _result: None,
        controller.task_id("render", identity=identity),
        30.0,
        identity=identity,
    )
    assert len(calls) == 1
    assert controller.pending_task.stage == "render"
    assert window._rt_prepare_inflight is False
    assert window._rt_prepare_owner is None
    assert window._latest_rt_quote.quote == quote

    calls[0][1]["on_terminated"]()

    assert len(calls) == 2
    assert controller.running_task.stage == "render"
    assert controller.pending_task.stage == "realtime"
    assert window._latest_rt_quote is None

    calls[1][1]["on_terminated"]()
    assert len(calls) == 3
    assert controller.running_task.stage == "realtime"


def test_realtime_backlog_and_terminal_callbacks_cannot_cross_generation(monkeypatch):
    monkeypatch.setattr(runtime.MarketCalendar, "get_latest_trade_date", lambda market: dt.date(2026, 7, 14))
    monkeypatch.setattr(runtime.MarketCalendar, "is_trade_day", lambda date, market: True)
    original = _history_frame(250, end="2026-07-14")
    fixture = _runtime_window(_Provider(), frame=original, window_id="cross-generation")
    calls = []
    lifecycle = SimpleNamespace(
        run_background=lambda *args, **kwargs: calls.append((args, kwargs)),
        cancel=lambda *_args, **_kwargs: True,
    )
    fixture.window._task_lifecycle = lifecycle
    queued = _capture_queue(monkeypatch)
    monkeypatch.setattr(runtime, "task_lifecycle_for", lambda *_args, **_kwargs: lifecycle)

    quote_a1 = {"date": "2026-07-14", "open": 20, "high": 21, "low": 19, "close": 20.5, "volume": 1_000}
    quote_a2 = {**quote_a1, "close": 20.8, "volume": 1_100}
    runtime.refresh_last_bar(fixture.window, quote_a1)
    runtime.refresh_last_bar(fixture.window, quote_a2)
    result_a = calls[0][0][1](None)

    runtime._clear_realtime_generation_state(fixture.window)
    identity_b = fixture.controller.begin("000002")
    fixture.window.code = "000002"
    fixture.window._render_generation = identity_b.generation
    frame_b = _history_frame(250, end="2026-07-14")
    frame_b.loc[:, "close"] = frame_b["close"] + 100
    fixture.window.df = frame_b
    fixture.window._history_frame = frame_b
    assert fixture.controller.claim_frame(identity_b)
    quote_b = {"date": "2026-07-14", "open": 120, "high": 122, "low": 119, "close": 121.5, "volume": 2_000}
    runtime.refresh_last_bar(fixture.window, quote_b)

    calls[0][1]["on_success"](result_a)
    assert queued == []
    calls[0][1]["on_terminated"]()
    assert len(calls) == 2
    result_b = calls[1][0][1](None)
    calls[1][1]["on_success"](result_b)

    assert len(queued) == 1
    assert queued[0].prepared.code == "000002"
    assert queued[0].prepared.display_frame.iloc[-1]["close"] == pytest.approx(121.5)
    assert fixture.window._latest_rt_quote is None


def test_closed_controller_never_falls_back_to_code_generation_match():
    controller = KlineLoadController(window_id="closed-controller")
    identity = controller.begin("000001")
    window = SimpleNamespace(
        _closing=False,
        _load_controller=controller,
        code=identity.code,
        _render_generation=identity.generation,
    )
    controller.close()

    assert runtime._is_current_request(window, identity.code, identity.generation) is False


def test_load_and_draw_rejects_stale_success_and_error_callbacks(monkeypatch):
    calls = _capture_submit(monkeypatch)
    queued = _capture_queue(monkeypatch)
    fixture = _runtime_window(
        _Provider(local=_history_frame()),
        target=dt.date(2026, 7, 14),
        window_id="stale-history",
    )

    runtime.load_and_draw(fixture.window, identity=fixture.identity)
    task = calls[-1]
    result = task.fn(None)
    task.error("provider failed")
    assert fixture.statuses[-1] == ("日线加载失败，请重试", "error")
    assert "provider failed" in fixture.log.warnings[-1]

    status_count = len(fixture.statuses)
    fixture.controller.begin("000002")
    task.success(result)
    task.error("stale failure")

    assert queued == []
    assert len(fixture.statuses) == status_count


def test_load_and_draw_empty_cn_history_reports_error(monkeypatch):
    calls = _capture_submit(monkeypatch)
    fixture = _runtime_window(_Provider(local=None), window_id="empty-history")

    runtime.load_and_draw(fixture.window, identity=fixture.identity)
    result = calls[-1].fn(None)
    assert result.prepared is None
    calls[-1].success(result)

    assert fixture.statuses[-1] == ("未获取到可用日线数据，请检查网络后重试", "error")


def test_load_and_draw_asian_uses_same_background_snapshot_pipeline(monkeypatch, tmp_path):
    calls = _capture_submit(monkeypatch)
    queued = _capture_queue(monkeypatch)
    import ui.tabs.asian_market_tab as asian_tab

    rows = []
    for index, date in enumerate(pd.date_range(end="2026-07-14", periods=30, freq="D")):
        close = 100.0 + index
        rows.append(
            {
                "date": date.strftime("%Y-%m-%d"),
                "open": close - 1,
                "high": close + 2,
                "low": close - 2,
                "close": close,
                "volume": 5_000 + index,
            }
        )
    cache_path = tmp_path / "asian.json"
    cache_path.write_text(
        json.dumps({"stocks": [{"ticker": "2330.TW", "klines": rows}]}),
        encoding="utf-8",
    )
    monkeypatch.setattr(asian_tab, "JSON_CACHE", str(cache_path))
    asian_tab.GLOBAL_ASIAN_RT_CACHE.pop("2330.TW", None)
    monkeypatch.setattr(runtime.MarketCalendar, "get_latest_completed_trade_date", lambda market: dt.date(2026, 7, 14))
    fixture = _runtime_window(
        _Provider(),
        code="2330.TW",
        name="台积电",
        market="TW",
        target=dt.date(2026, 7, 14),
        window_id="asian-window",
    )

    runtime.load_and_draw(fixture.window, identity=fixture.identity)
    task = calls[-1]
    assert task.task_id == "kline:asian-window:1:history"
    result = task.fn(None)
    assert result.data_result.market == "TW"
    assert result.data_result.source == "asian_json_cache"
    assert result.prepared.point_count == 30
    task.success(result)

    assert queued[-1].prepared is result.prepared
    assert _payload(result.prepared)["data"]["klines"][-1][1] == 129.0


def test_apply_history_load_result_backfills_degraded_asian_history(monkeypatch):
    queued = _capture_queue(monkeypatch)
    fixture = _runtime_window(_Provider(), code="2330.TW", name="台积电", market="TW")
    backfills = []
    monkeypatch.setattr(runtime, "_schedule_missing_asian_history", lambda window: backfills.append(window))
    prepared = SimpleNamespace(display_frame=_frame("2026-08-14"))
    result = runtime._PreparedHistoryLoad(
        runtime.KlineDataResult(
            code="2330.TW",
            market="TW",
            data=_frame("2026-08-14"),
            source="asian_json_cache",
            degraded=True,
            degradation_reason="asian_history_stale",
            latest_trade_date=dt.date(2026, 8, 14),
        ),
        _frame("2026-08-14"),
        prepared,
    )
    request = SimpleNamespace(identity=fixture.identity, market="TW")

    runtime._apply_history_load_result(result, window=fixture.window, request=request)

    assert queued == []
    assert backfills == [fixture.window]


def test_prepare_history_load_skips_realtime_quote_for_stale_asian_history(monkeypatch):
    fixture = _runtime_window(_Provider(), code="2330.TW", name="台积电", market="TW")
    frame = _frame("2026-08-14")
    stale_result = runtime.KlineDataResult(
        code="2330.TW",
        market="TW",
        data=frame,
        source="asian_json_cache",
        degraded=True,
        degradation_reason="asian_history_stale",
        latest_trade_date=dt.date(2026, 8, 14),
    )

    class _StaleService:
        def __init__(self, _provider):
            return None

        def load(self, *_args, **_kwargs):
            return stale_result

    monkeypatch.setattr(runtime, "KlineDataService", _StaleService)
    quote_calls = []
    result = runtime._prepare_history_load(
        context=fixture.window._open_context,
        identity=fixture.identity,
        snapshot_version=1,
        data_provider=fixture.window.data_provider,
        target_trade_date=dt.date(2026, 8, 19),
        asian_cache_path="cache.json",
        cached_asian_quote=None,
        asian_quote_fetcher=lambda code: quote_calls.append(code) or {"close": 100},
        chart_theme={},
        cancellation_token=None,
    )

    pd.testing.assert_frame_equal(result.frame, frame)
    assert result.prepared is None
    assert quote_calls == []


def test_prepare_and_render_frame_uses_owned_render_task_and_drops_stale_result(monkeypatch):
    calls = _capture_submit(monkeypatch)
    queued = _capture_queue(monkeypatch)
    frame = _history_frame()
    fixture = _runtime_window(_Provider(), frame=frame, window_id="render-window")

    runtime.prepare_and_render_frame(fixture.window, frame, loading=True, source="cache")
    task = calls[-1]
    assert task.name == "render_prepare"
    assert task.task_id == "kline:render-window:1:render"
    prepared = task.fn(None)
    assert prepared.source == "cache"
    task.success(prepared)
    assert queued[-1].prepared is prepared

    before = len(queued)
    fixture.controller.begin("000002")
    task.success(prepared)
    assert len(queued) == before


def test_poll_rt_update_closed_market_and_asian_fast_paths(monkeypatch):
    calls = _capture_submit(monkeypatch)
    refreshed = []
    monkeypatch.setattr(runtime, "refresh_last_bar", lambda window, quote: refreshed.append(quote))
    fixture = _runtime_window(_Provider(), frame=_history_frame(), window_id="poll-closed")
    monkeypatch.setattr(runtime.MarketCalendar, "is_quote_refresh_time", lambda market: False)

    runtime.poll_rt_update(fixture.window)

    assert fixture.timer.stopped
    assert fixture.timer.state_applied
    assert fixture.log.debugs
    assert calls == []

    monkeypatch.setattr(runtime.MarketCalendar, "is_quote_refresh_time", lambda market: True)
    asian_quote = {"date": "2026-07-14", "open": 1, "high": 2, "low": 1, "close": 2}
    asian = _runtime_window(
        _Provider(),
        code="2330.TW",
        name="台积电",
        market="TW",
        frame=_history_frame(),
        asian_quote=asian_quote,
        window_id="poll-asian-fast",
    )
    runtime.poll_rt_update(asian.window)
    assert refreshed == [asian_quote]
    assert calls == []

    monkeypatch.setattr(runtime, "get_yf_rate_limit_status", lambda: {"active": True})
    limited = _runtime_window(
        _Provider(),
        code="2330.TW",
        name="台积电",
        market="TW",
        frame=_history_frame(),
        window_id="poll-asian-limited",
    )
    runtime.poll_rt_update(limited.window)
    assert calls == []


def test_poll_rt_update_background_result_errors_and_identity(monkeypatch):
    calls = _capture_submit(monkeypatch)
    monkeypatch.setattr(runtime.MarketCalendar, "is_quote_refresh_time", lambda market: True)
    monkeypatch.setattr(runtime, "get_yf_rate_limit_status", lambda: {"active": False})
    refreshed = []
    monkeypatch.setattr(runtime, "refresh_last_bar", lambda window, quote: refreshed.append(quote))
    provider = _Provider(quote={"open": 1, "close": 2})
    fixture = _runtime_window(provider, frame=_history_frame(), window_id="quote-window")

    runtime.poll_rt_update(fixture.window)
    task = calls[-1]
    assert task.name == "realtime_quote"
    assert task.task_id == "kline:quote-window:1:realtime-quote"
    assert task.fn(None)["close"] == 2
    task.success(None)
    assert refreshed == []
    task.success({"open": 1, "close": 2})
    assert refreshed == [{"open": 1, "close": 2}]

    fixture.controller.begin("000002")
    task.success({"open": 2, "close": 3})
    assert len(refreshed) == 1

    error_fixture = _runtime_window(
        _Provider(quote_error=ValueError("bad")),
        frame=_history_frame(),
        window_id="quote-error",
    )
    runtime.poll_rt_update(error_fixture.window)
    error_task = calls[-1]
    error = error_task.fn(None)
    assert isinstance(error, ValueError)
    error_task.success(error)
    assert "bad" in error_fixture.log.warnings[-1]

    monkeypatch.setattr(runtime, "is_yf_rate_limit_error", lambda exc: True)
    monkeypatch.setattr(runtime, "mark_yf_rate_limited", lambda exc: 42.0)
    error_task.success(RuntimeError("429"))
    assert "42" in error_fixture.log.warnings[-1]

    monkeypatch.setattr(runtime, "is_yf_rate_limit_error", lambda exc: False)
    with pytest.raises(AssertionError, match="unexpected"):
        error_task.success(AssertionError("unexpected"))


def test_poll_rt_update_asian_background_fetch_uses_window_scoped_task(monkeypatch):
    calls = _capture_submit(monkeypatch)
    monkeypatch.setattr(runtime.MarketCalendar, "is_quote_refresh_time", lambda market: True)
    monkeypatch.setattr(runtime, "get_yf_rate_limit_status", lambda: {"active": False})
    import ui.tabs.asian_market_workers as workers

    monkeypatch.setattr(workers, "fetch_asian_realtime_quote", lambda code: {"code": code})
    fixture = _runtime_window(
        _Provider(),
        code="2330.TW",
        name="台积电",
        market="TW",
        frame=_history_frame(),
        window_id="asian-quote-window",
    )

    runtime.poll_rt_update(fixture.window)

    assert calls[-1].task_id == "kline:asian-quote-window:1:realtime-quote"
    assert calls[-1].fn(None) == {"code": "2330.TW"}


def _assert_complete_indicator_snapshot(prepared, prepared_frame) -> None:
    chart = _payload(prepared)["data"]
    closes = prepared_frame["close"].astype(float)
    volumes = prepared_frame["volume"].astype(float)
    for period, key in ((10, "ma10"), (20, "ma20"), (50, "ma50"), (150, "ma150"), (200, "ma200")):
        expected = round(float(closes.iloc[-period:].mean()), 2)
        assert chart[key][-1] == pytest.approx(expected)
    assert chart["volMa20"][-1] == pytest.approx(round(float(volumes.iloc[-20:].mean()), 0))

    diff = closes.ewm(span=12, adjust=False).mean() - closes.ewm(span=26, adjust=False).mean()
    dea = diff.ewm(span=9, adjust=False).mean()
    histogram = diff - dea
    assert chart["diff"][-1] == pytest.approx(round(float(diff.iloc[-1]), 4))
    assert chart["dea"][-1] == pytest.approx(round(float(dea.iloc[-1]), 4))
    assert chart["macd"][-1]["value"] == pytest.approx(float(histogram.iloc[-1]))


def test_refresh_last_bar_builds_full_snapshot_and_commits_only_after_ack(monkeypatch):
    calls = _capture_submit(monkeypatch)
    monkeypatch.setattr(runtime.MarketCalendar, "get_latest_trade_date", lambda market: dt.date(2026, 7, 15))
    monkeypatch.setattr(runtime.MarketCalendar, "is_trade_day", lambda date, market: True)
    monkeypatch.setattr(runtime.MarketCalendar, "is_quote_refresh_time", lambda market: True)
    original = _history_frame(250, end="2026-07-14")
    fixture = _runtime_window(
        _Provider(),
        frame=original,
        window_id="realtime-window",
    )
    quote = {
        "date": "2026-07-14",
        "open": 21.2,
        "high": 22.5,
        "low": 20.8,
        "close": 22.1,
        "volume": 55_000,
    }

    runtime.refresh_last_bar(fixture.window, quote)

    assert fixture.window.df is original
    assert len(calls) == 1
    task = calls[0]
    assert task.name == "realtime_prepare"
    assert task.task_id == "kline:realtime-window:1:realtime"
    result = task.fn(None)
    prepared_frame, prepared = _realtime_result_parts(result)
    assert prepared.source == "realtime"
    assert prepared.identity == ("realtime-window", 1, "000001")
    last_bar = prepared_frame.iloc[-1]
    assert last_bar["open"] == pytest.approx(21.2)
    assert last_bar["high"] == pytest.approx(22.5)
    assert last_bar["low"] == pytest.approx(min(float(original.iloc[-1]["low"]), 20.8))
    assert last_bar["close"] == pytest.approx(22.1)
    assert last_bar["volume"] == pytest.approx(55_000.0)
    _assert_complete_indicator_snapshot(prepared, prepared_frame.iloc[-250:])

    task.success(result)

    assert fixture.window.df is original
    assert len(fixture.page.calls) == 1
    script, callback = fixture.page.calls[-1]
    assert "window.applySnapshot" in script
    assert "updateLastBar" not in script
    assert callback is not None
    callback(_ack_for(prepared))
    assert fixture.window.df is original
    assert len(fixture.page.calls) == 2
    render_script, render_callback = fixture.page.calls[-1]
    assert "window.getSnapshotRenderState" in render_script
    assert render_callback is not None
    render_callback(dict(_ack_for(prepared), applied=False, rendered=True))

    pd.testing.assert_frame_equal(fixture.window.df, prepared_frame)
    assert fixture.controller.owns_current_frame("000001", 1)
    assert fixture.stages.calls[-1] == "chart_ready"


def test_realtime_snapshot_ack_from_stale_generation_cannot_commit(monkeypatch):
    calls = _capture_submit(monkeypatch)
    monkeypatch.setattr(runtime.MarketCalendar, "get_latest_trade_date", lambda market: dt.date(2026, 7, 14))
    monkeypatch.setattr(runtime.MarketCalendar, "is_trade_day", lambda date, market: True)
    original = _history_frame(250, end="2026-07-14")
    fixture = _runtime_window(
        _Provider(),
        frame=original,
        window_id="stale-realtime",
    )

    runtime.refresh_last_bar(
        fixture.window,
        {"date": "2026-07-14", "open": 20, "high": 21, "low": 19, "close": 20.5, "volume": 900},
    )
    result = calls[-1].fn(None)
    _prepared_frame, prepared = _realtime_result_parts(result)
    calls[-1].success(result)
    _script, callback = fixture.page.calls[-1]
    fixture.controller.begin("000002")
    fixture.window.code = "000002"

    callback(_ack_for(prepared))

    assert fixture.window.df is original
    assert not fixture.controller.owns_current_frame("000001", 1)
    assert "chart_ready" not in fixture.stages.calls


def test_realtime_updates_coalesce_to_latest_complete_snapshot(monkeypatch):
    calls = _capture_submit(monkeypatch)
    queued = _capture_queue(monkeypatch)
    monkeypatch.setattr(runtime.MarketCalendar, "get_latest_trade_date", lambda market: dt.date(2026, 7, 14))
    monkeypatch.setattr(runtime.MarketCalendar, "is_trade_day", lambda date, market: True)
    original = _history_frame(250, end="2026-07-14")
    fixture = _runtime_window(
        _Provider(),
        frame=original,
        window_id="latest-realtime",
    )
    first_quote = {
        "date": "2026-07-14",
        "open": 20,
        "high": 21,
        "low": 19,
        "close": 20.5,
        "volume": 1_000,
    }
    latest_quote = {**first_quote, "high": 23, "close": 22.5, "volume": 2_000}

    runtime.refresh_last_bar(fixture.window, first_quote)
    runtime.refresh_last_bar(fixture.window, latest_quote)
    assert len(calls) == 1

    first_result = calls[0].fn(None)
    calls[0].success(first_result)
    assert len(calls) == 2
    assert [task.task_id for task in calls] == [
        "kline:latest-realtime:1:realtime",
        "kline:latest-realtime:1:realtime",
    ]

    latest_result = calls[1].fn(None)
    latest_frame, latest_prepared = _realtime_result_parts(latest_result)
    calls[1].success(latest_result)

    assert len(queued) == 2
    assert _payload(latest_prepared)["data"]["klines"][-1][1] == 22.5
    _assert_complete_indicator_snapshot(latest_prepared, latest_frame.iloc[-250:])
    assert fixture.window.df is original


def test_refresh_last_bar_guards_invalid_and_duplicate_quotes(monkeypatch):
    calls = _capture_submit(monkeypatch)
    fixture = _runtime_window(_Provider(), frame=None, window_id="guard-realtime")
    runtime.refresh_last_bar(fixture.window, {})
    runtime.refresh_last_bar(fixture.window, {"close": 1})
    assert calls == []

    fixture.window.df = _history_frame()
    assert fixture.controller.claim_frame(fixture.identity)
    quote = {"date": "2026-07-14", "open": 10, "high": 11, "low": 9, "close": 10.5, "volume": 7}
    runtime.refresh_last_bar(fixture.window, quote)
    runtime.refresh_last_bar(fixture.window, dict(quote))

    assert len(calls) == 1
    assert fixture.window.df.iloc[-1]["close"] != quote["close"]
