# -*- coding: utf-8 -*-
from __future__ import annotations

from dataclasses import dataclass
from functools import partial

import pandas as pd

from app.services.kline_data_service import KlineDataResult, KlineDataService
from app.services.kline_render_preparer import KlineRenderPreparer, PreparedKlineRender
from app.services.ui_market_calendar_service import MarketCalendar
from app.services.ui_quote_service import is_provider_online
from app.services.ui_task_lifecycle_service import (
    TaskCancelledError,
    TaskDeadlineExceeded,
    invoke_with_cancellation,
    raise_if_cancelled,
    reraise_task_cancellation,
    task_lifecycle_for,
)
from app.services.ui_task_service import background_job_runner as task_manager
from app.services.ui_task_service import task_registry
from ui.kline_chart_payload import build_kline_echarts_payload
from ui.kline_load_controller import KlineLoadIdentity, KlineTaskTicket
from ui.kline_window_rendering import queue_prepared_render
from ui.kline_window_state import current_kline_open_context

_MISSING = object()


def fetch_single_kline(
    name: str,
    ticker: str,
    period: str = "1y",
    session=None,
    *,
    cancellation_token=None,
):
    from app.services.asian_market_service import fetch_single_kline as fetch

    return fetch(
        name,
        ticker,
        period=period,
        session=session,
        cancellation_token=cancellation_token,
    )


def get_yf_rate_limit_status():
    from app.services.asian_market_service import get_yf_rate_limit_status as get_status

    return get_status()


def is_yf_rate_limit_error(exc):
    from app.services.asian_market_service import is_yf_rate_limit_error as is_rate_limit_error

    return is_rate_limit_error(exc)


def mark_yf_rate_limited(exc=None, cooldown_sec=None):
    from app.services.asian_market_service import mark_yf_rate_limited as mark_rate_limited

    if cooldown_sec is None:
        return mark_rate_limited(exc)
    return mark_rate_limited(exc, cooldown_sec=cooldown_sec)


def _controller_matches_request(window, controller, request_code: str, request_generation: int) -> bool:
    identity = getattr(controller, "current_identity", None)
    return bool(
        controller is not None
        and identity is not None
        and not getattr(window, "_closing", False)
        and controller.is_current(identity)
        and identity.code == request_code
        and identity.generation == request_generation
    )


def _is_current_request(window, request_code: str, request_generation: int) -> bool:
    controller = getattr(window, "_load_controller", _MISSING)
    if controller is not _MISSING:
        return _controller_matches_request(window, controller, request_code, request_generation)
    return (
        not getattr(window, "_closing", False)
        and str(getattr(window, "code", "") or "").strip() == request_code
        and int(getattr(window, "_render_generation", 0) or 0) == request_generation
    )


@dataclass(frozen=True, slots=True)
class _QueuedOwnedWindowTask:
    ticket: KlineTaskTicket
    name: str
    fn: object
    on_success: object
    on_error: object
    on_finalized: object
    on_discarded: object
    task_id: str
    timeout_sec: float


def _start_owned_window_task(window, submission: _QueuedOwnedWindowTask) -> None:
    controller = window._load_controller
    active_tickets = getattr(window, "_active_kline_task_tickets", None)
    if active_tickets is None:
        active_tickets = set()
        window._active_kline_task_tickets = active_tickets
    active_tickets.add(submission.ticket)
    window._running_kline_task_submission = submission

    def _on_task_terminated() -> None:
        active_tickets.discard(submission.ticket)
        if getattr(window, "_running_kline_task_submission", None) is submission:
            window._running_kline_task_submission = None
        next_ticket = controller.settle_task(submission.ticket)
        pending = getattr(window, "_pending_kline_task_submission", None)
        window._pending_kline_task_submission = None
        promoted = next_ticket is not None and pending is not None and pending.ticket == next_ticket
        if pending is not None and not promoted and pending.on_discarded is not None:
            pending.on_discarded()
        try:
            if submission.on_finalized is not None:
                submission.on_finalized()
        finally:
            try:
                if promoted:
                    _start_owned_window_task(window, pending)
            finally:
                _drain_realtime_backlog(window)

    try:
        token = task_lifecycle_for(window, runner=task_manager).run_background(
            submission.name,
            submission.fn,
            on_success=submission.on_success,
            on_error=submission.on_error,
            on_terminated=_on_task_terminated,
            task_id=task_registry.transient_window(submission.task_id),
            timeout_sec=submission.timeout_sec,
            runner=task_manager,
        )
        if getattr(token, "cancelled", False) and submission.on_error is not None:
            reason = str(getattr(token, "reason", "") or "submission_rejected")
            submission.on_error(f"后台任务未启动: {reason}")
    except Exception:  # noqa: BLE001 - scheduler rejection must roll back the exact ticket.
        active_tickets.discard(submission.ticket)
        if getattr(window, "_running_kline_task_submission", None) is submission:
            window._running_kline_task_submission = None
        controller.settle_task(submission.ticket)
        if submission.on_discarded is not None:
            submission.on_discarded()
        raise


def _discard_pending_owned_window_task(window) -> bool:
    pending = getattr(window, "_pending_kline_task_submission", None)
    window._pending_kline_task_submission = None
    if pending is None:
        return False
    if pending.on_discarded is not None:
        pending.on_discarded()
    return True


def _submit_owned_window_task(
    window,
    name,
    fn,
    on_success,
    task_suffix: str,
    timeout_sec: float,
    *,
    on_error=None,
    on_finalized=None,
    on_discarded=None,
    identity: KlineLoadIdentity | None = None,
) -> KlineTaskTicket | None:
    controller = getattr(window, "_load_controller", None)
    identity = identity or getattr(controller, "current_identity", None)
    if controller is None or not controller.is_current(identity):
        return None
    normalized_task_id = str(task_suffix or "").strip()
    stage = normalized_task_id.rsplit(":", 1)[-1]
    ticket, should_start = controller.request_task(identity, stage)
    submission = _QueuedOwnedWindowTask(
        ticket=ticket,
        name=name,
        fn=fn,
        on_success=on_success,
        on_error=on_error,
        on_finalized=on_finalized,
        on_discarded=on_discarded,
        task_id=normalized_task_id,
        timeout_sec=float(timeout_sec),
    )
    if should_start:
        _start_owned_window_task(window, submission)
        return ticket

    _discard_pending_owned_window_task(window)
    window._pending_kline_task_submission = submission
    running = getattr(window, "_running_kline_task_submission", None)
    if running is not None and running.ticket.identity.generation != identity.generation:
        lifecycle = getattr(window, "_task_lifecycle", None)
        if lifecycle is not None:
            lifecycle.cancel(running.name, reason="generation_superseded")
    return ticket


def _resolve_quote_trade_date(
    *,
    market: str,
    raw_quote_date,
    last_date,
    latest_trade_date,
) -> object:
    quote_trade_date = None
    if raw_quote_date:
        try:
            quote_trade_date = pd.Timestamp(raw_quote_date).date()
        except (TypeError, ValueError):
            quote_trade_date = None

    if quote_trade_date is None:
        if (
            market == "CN"
            and latest_trade_date is not None
            and MarketCalendar.is_quote_refresh_time(market)
            and last_date < latest_trade_date
        ):
            quote_trade_date = latest_trade_date
        else:
            quote_trade_date = last_date

    if latest_trade_date is None or quote_trade_date is None:
        return last_date
    if not MarketCalendar.is_trade_day(quote_trade_date, market=market):
        return last_date
    if quote_trade_date > latest_trade_date:
        return latest_trade_date
    return quote_trade_date


def _merge_cn_realtime_bar(df, quote, *, target_trade_date):
    fresh_df = df.copy()
    rt_open = float(quote.get("open", 0) or 0)
    if rt_open <= 0 or target_trade_date is None:
        return fresh_df

    rt_close = float(quote.get("close", 0) or 0)
    rt_high = float(quote.get("high", 0) or 0)
    rt_low = float(quote.get("low", 0) or 0)
    rt_vol = float(quote.get("volume", 0) or 0)

    last_date = pd.Timestamp(fresh_df.index[-1]).date()
    quote_trade_date = _resolve_quote_trade_date(
        market="CN",
        raw_quote_date=quote.get("date"),
        last_date=last_date,
        latest_trade_date=target_trade_date,
    )

    if quote_trade_date == last_date:
        fresh_df.iloc[-1, fresh_df.columns.get_loc("open")] = rt_open
        if rt_high > 0:
            fresh_df.iloc[-1, fresh_df.columns.get_loc("high")] = max(float(fresh_df.iloc[-1]["high"]), rt_high)
        if rt_low > 0:
            fresh_df.iloc[-1, fresh_df.columns.get_loc("low")] = min(float(fresh_df.iloc[-1]["low"]), rt_low)
        if rt_close > 0:
            fresh_df.iloc[-1, fresh_df.columns.get_loc("close")] = rt_close
        if "volume" in fresh_df.columns:
            fresh_df.iloc[-1, fresh_df.columns.get_loc("volume")] = rt_vol
        return fresh_df

    if (
        quote_trade_date > last_date
        and quote_trade_date <= target_trade_date
        and MarketCalendar.is_quote_refresh_time("CN")
        and rt_close > 0
    ):
        prev_row = fresh_df.iloc[-1]
        tol = 1e-8
        same_as_prev = (
            abs(float(prev_row.get("open", 0)) - rt_open) <= tol
            and abs(float(prev_row.get("high", 0)) - rt_high) <= tol
            and abs(float(prev_row.get("low", 0)) - rt_low) <= tol
            and abs(float(prev_row.get("close", 0)) - rt_close) <= tol
        )
        if not same_as_prev:
            sim_high = rt_high if rt_high > 0 else max(rt_open, rt_close)
            sim_low = rt_low if rt_low > 0 else min(rt_open, rt_close)
            new_row = pd.DataFrame(
                {
                    "open": [rt_open],
                    "high": [sim_high],
                    "low": [sim_low],
                    "close": [rt_close],
                    "volume": [rt_vol],
                },
                index=[pd.Timestamp(quote_trade_date)],
            )
            fresh_df = fresh_df[fresh_df.index != pd.Timestamp(quote_trade_date)]
            fresh_df = pd.concat([fresh_df, new_row])

    return fresh_df


def _fetch_missing_cn_quote(
    data_provider,
    request_code: str,
    fresh_df,
    target_trade_date,
    request_logger,
    cancellation_token=None,
):
    if (
        not is_provider_online(data_provider)
        or target_trade_date is None
        or not MarketCalendar.is_quote_refresh_time("CN")
    ):
        return None
    last_dt = None
    if fresh_df is not None and not fresh_df.empty:
        last_dt = pd.Timestamp(fresh_df.index[-1]).date()
    if last_dt is not None and last_dt >= target_trade_date:
        return None
    try:
        quotes = invoke_with_cancellation(
            data_provider.fetch_realtime_quotes_batch,
            cancellation_token,
            [request_code],
        )
    except (AttributeError, KeyError, OSError, RuntimeError, TypeError, ValueError) as exc:
        reraise_task_cancellation(exc)
        request_logger.warning(f"[K线] {request_code} 实时行情合并失败: {exc}")
        return None
    return quotes.get(request_code) if quotes else None


@dataclass(frozen=True, slots=True)
class _PreparedHistoryLoad:
    data_result: KlineDataResult
    frame: pd.DataFrame | None
    prepared: PreparedKlineRender | None
    fetched_asian_quote: dict | None = None
    quote_error: Exception | None = None


@dataclass(frozen=True, slots=True)
class _HistoryLoadRequest:
    context: object
    identity: KlineLoadIdentity
    snapshot_version: int
    data_provider: object
    target_trade_date: object
    market: str
    asian_cache_path: str
    cached_asian_quote: dict | None
    asian_quote_fetcher: object
    chart_theme: dict


def _merge_cn_initial_quote(frame, *, data_provider, code, target_trade_date, cancellation_token=None):
    quote = _fetch_missing_cn_quote(
        data_provider,
        code,
        frame,
        target_trade_date,
        request_logger=_SilentKlineLogger(),
        cancellation_token=cancellation_token,
    )
    if quote is None:
        return frame
    return _merge_cn_realtime_bar(frame, quote, target_trade_date=target_trade_date)


def _merge_asian_initial_quote(
    frame,
    *,
    result,
    code,
    target_trade_date,
    cached_quote,
    quote_fetcher,
    cancellation_token,
):
    quote = cached_quote
    fetched_quote = None
    quote_error = None
    needs_fetch = quote is None and quote_fetcher is not None and result.latest_trade_date != target_trade_date
    if needs_fetch:
        try:
            raise_if_cancelled(cancellation_token)
            quote = invoke_with_cancellation(quote_fetcher, cancellation_token, code)
            fetched_quote = quote
        except (TaskCancelledError, TaskDeadlineExceeded):
            raise
        except Exception as exc:
            quote_error = exc
    if quote is not None:
        from ui.kline_window_asian import apply_asian_live_quote

        frame = apply_asian_live_quote(frame, quote, market=result.market)
    return frame, fetched_quote, quote_error


def _merge_initial_quote(
    frame,
    *,
    result,
    context,
    data_provider,
    target_trade_date,
    cached_asian_quote,
    asian_quote_fetcher,
    cancellation_token,
):
    if result.market == "CN":
        merged = _merge_cn_initial_quote(
            frame,
            data_provider=data_provider,
            code=context.code,
            target_trade_date=target_trade_date,
            cancellation_token=cancellation_token,
        )
        return merged, None, None
    return _merge_asian_initial_quote(
        frame,
        result=result,
        code=context.code,
        target_trade_date=target_trade_date,
        cached_quote=cached_asian_quote,
        quote_fetcher=asian_quote_fetcher,
        cancellation_token=cancellation_token,
    )


def _prepare_history_load(
    *,
    context,
    identity,
    snapshot_version: int,
    data_provider,
    target_trade_date,
    asian_cache_path: str,
    cached_asian_quote: dict | None,
    asian_quote_fetcher,
    chart_theme: dict,
    cancellation_token,
) -> _PreparedHistoryLoad:
    service = KlineDataService(data_provider)
    result = service.load(
        context,
        asian_cache_path=asian_cache_path,
        target_trade_date=target_trade_date,
        cancellation_token=cancellation_token,
    )
    frame = result.frame
    if not result.has_data or frame is None:
        return _PreparedHistoryLoad(result, frame, None)
    if result.market != "CN" and result.degraded:
        return _PreparedHistoryLoad(result, frame, None)
    raise_if_cancelled(cancellation_token)
    frame, fetched_quote, quote_error = _merge_initial_quote(
        frame,
        result=result,
        context=context,
        data_provider=data_provider,
        target_trade_date=target_trade_date,
        cached_asian_quote=cached_asian_quote,
        asian_quote_fetcher=asian_quote_fetcher,
        cancellation_token=cancellation_token,
    )
    raise_if_cancelled(cancellation_token)
    prepared = KlineRenderPreparer(build_kline_echarts_payload).prepare(
        frame,
        context=context,
        owner_id=identity.window_id,
        generation=identity.generation,
        snapshot_version=snapshot_version,
        source=result.source,
        degraded=result.degraded,
        degradation_reason=result.degradation_reason,
        payload_kwargs={"theme": chart_theme},
        cancellation_token=cancellation_token,
    )
    return _PreparedHistoryLoad(result, frame, prepared, fetched_quote, quote_error)


class _SilentKlineLogger:
    @staticmethod
    def warning(_message) -> None:
        return None


def _report_cn_history_error(window, request_code: str, request_generation: int, message: str) -> None:
    if not _is_current_request(window, request_code, request_generation):
        return
    try:
        window._set_status_message("日线加载失败，请重试", tone="error")
        window._log.warning(f"[K线] {request_code} 日线加载失败: {message}")
    except (AttributeError, RuntimeError, TypeError, ValueError):
        return


def load_and_draw(window, identity=None):
    """Queue one history load through the per-window latest-only task controller."""
    if getattr(window, "_closing", False):
        return
    controller = getattr(window, "_load_controller", None)
    identity = identity or getattr(controller, "current_identity", None)
    if controller is None or not controller.is_current(identity):
        return
    _start_history_load(window, identity)


def _build_history_load_request(window, identity: KlineLoadIdentity) -> _HistoryLoadRequest:
    context = current_kline_open_context(window)
    request_code = identity.code
    snapshot_version = int(getattr(window, "_snapshot_version", 0) or 0) + 1
    window._snapshot_version = snapshot_version
    market = MarketCalendar.infer_market(request_code)
    target_trade_date = (
        window._get_cn_target_trade_date()
        if market == "CN"
        else MarketCalendar.get_latest_completed_trade_date(market)
    )
    asian_cache_path = ""
    cached_asian_quote = None
    asian_quote_fetcher = None
    if market != "CN":
        from ui.tabs.asian_market_tab import GLOBAL_ASIAN_RT_CACHE, JSON_CACHE
        from ui.tabs.asian_market_workers import fetch_asian_realtime_quote

        asian_cache_path = JSON_CACHE
        cached_asian_quote = dict(GLOBAL_ASIAN_RT_CACHE.get(request_code) or {}) or None
        asian_quote_fetcher = fetch_asian_realtime_quote
    data_provider = window.data_provider
    from ui.theme import theme_manager

    chart_theme = dict(theme_manager.current_theme)
    return _HistoryLoadRequest(
        context=context,
        identity=identity,
        snapshot_version=snapshot_version,
        data_provider=data_provider,
        target_trade_date=target_trade_date,
        market=market,
        asian_cache_path=asian_cache_path,
        cached_asian_quote=cached_asian_quote,
        asian_quote_fetcher=asian_quote_fetcher,
        chart_theme=chart_theme,
    )


def _run_history_load(cancellation_token, *, request: _HistoryLoadRequest) -> _PreparedHistoryLoad:
    return _prepare_history_load(
        context=request.context,
        identity=request.identity,
        snapshot_version=request.snapshot_version,
        data_provider=request.data_provider,
        target_trade_date=request.target_trade_date,
        asian_cache_path=request.asian_cache_path,
        cached_asian_quote=request.cached_asian_quote,
        asian_quote_fetcher=request.asian_quote_fetcher,
        chart_theme=request.chart_theme,
        cancellation_token=cancellation_token,
    )


def _schedule_missing_asian_history(window) -> None:
    from ui.kline_window_asian import schedule_asian_history_backfill

    schedule_asian_history_backfill(
        window,
        task_manager=task_manager,
        fetch_single_kline=fetch_single_kline,
        submit_owned_task=_submit_owned_window_task,
    )


def _apply_history_load_result(result, *, window, request: _HistoryLoadRequest) -> None:
    identity = request.identity
    if not _is_current_request(window, identity.code, identity.generation) or result is None:
        return
    if result.fetched_asian_quote:
        from ui.tabs.asian_market_tab import GLOBAL_ASIAN_RT_CACHE

        GLOBAL_ASIAN_RT_CACHE[identity.code] = result.fetched_asian_quote
    if result.quote_error is not None:
        _handle_asian_quote_error(window, identity.code, result.quote_error)
    if request.market != "CN" and result.data_result.degraded:
        _schedule_missing_asian_history(window)
        return
    if result.prepared is not None and result.frame is not None:
        queue_prepared_render(window, result.prepared, loading=False)
        return
    if request.market != "CN":
        _schedule_missing_asian_history(window)
    else:
        window._set_status_message("未获取到可用日线数据，请检查网络后重试", tone="error")


def _start_history_load(window, identity) -> None:
    controller = getattr(window, "_load_controller", None)
    if (
        controller is None
        or getattr(window, "_closing", False)
        or not controller.is_current(identity)
    ):
        return
    request = _build_history_load_request(window, identity)
    window._set_status_message("正在准备日线数据...", tone="loading")
    _submit_owned_window_task(
        window,
        "history_load",
        partial(_run_history_load, request=request),
        partial(_apply_history_load_result, window=window, request=request),
        controller.task_id("history", identity=identity),
        120.0,
        on_error=partial(
            _report_cn_history_error,
            window,
            identity.code,
            identity.generation,
        ),
        identity=identity,
    )


def _handle_asian_quote_error(window, request_code: str, exc: Exception) -> None:
    if is_yf_rate_limit_error(exc):
        remaining_sec = mark_yf_rate_limited(exc)
        window._log.warning(
            f"[K线] {request_code} 盘后补足亚洲报价触发 Yahoo Finance 限流，冷却 {remaining_sec:.0f}s: {exc}"
        )
        return
    window._log.warning(f"[K线] {request_code} 盘后补足亚洲报价失败: {exc}")


def prepare_and_render_frame(window, frame, *, loading: bool = False, source: str = "runtime") -> None:
    controller = getattr(window, "_load_controller", None)
    identity = getattr(controller, "current_identity", None)
    if controller is None or not controller.is_current(identity):
        return
    context = current_kline_open_context(window)
    snapshot_version = int(getattr(window, "_snapshot_version", 0) or 0) + 1
    window._snapshot_version = snapshot_version
    from ui.theme import theme_manager

    chart_theme = dict(theme_manager.current_theme)

    def _bg_prepare(cancellation_token):
        return KlineRenderPreparer(build_kline_echarts_payload).prepare(
            frame,
            context=context,
            owner_id=identity.window_id,
            generation=identity.generation,
            snapshot_version=snapshot_version,
            source=source,
            payload_kwargs={"theme": chart_theme},
            cancellation_token=cancellation_token,
        )

    def _on_success(prepared):
        if prepared is not None and controller.is_current(identity):
            queue_prepared_render(window, prepared, loading=loading)

    _submit_owned_window_task(
        window,
        "render_prepare",
        _bg_prepare,
        _on_success,
        controller.task_id("render", identity=identity),
        30.0,
        identity=identity,
    )


_EXPECTED_REALTIME_ERRORS = (
    AttributeError,
    ImportError,
    KeyError,
    OSError,
    RuntimeError,
    TypeError,
    ValueError,
)


def _stop_realtime_for_closed_market(window, market: str) -> bool:
    if MarketCalendar.is_quote_refresh_time(market):
        return False
    apply_market_state = getattr(window, "_apply_chart_market_state", None)
    if callable(apply_market_state):
        apply_market_state()
    timer = getattr(window, "_rt_timer", None)
    if timer is not None:
        timer.stop()
        window._log.debug(f"[K线] {window.code} 已收盘，停止实时刷新")
    return True


def _use_cached_asian_quote(window, market: str) -> bool:
    if market == "CN":
        return False
    quote = window._build_asian_rt_quote()
    if quote is not None:
        refresh_last_bar(window, quote)
        return True
    return bool(get_yf_rate_limit_status()["active"])


def _fetch_realtime_quote(cancellation_token, *, market: str, request_code: str, data_provider):
    try:
        raise_if_cancelled(cancellation_token)
        if market != "CN":
            from ui.tabs.asian_market_workers import fetch_asian_realtime_quote

            result = invoke_with_cancellation(
                fetch_asian_realtime_quote,
                cancellation_token,
                request_code,
            )
        else:
            quotes = invoke_with_cancellation(
                data_provider.fetch_realtime_quotes_batch,
                cancellation_token,
                [request_code],
            )
            result = quotes.get(request_code) if quotes else None
        raise_if_cancelled(cancellation_token)
        return result
    except (TaskCancelledError, TaskDeadlineExceeded):
        raise
    except Exception as exc:
        return exc


def _report_realtime_error(window, request_code: str, exc: Exception) -> None:
    if is_yf_rate_limit_error(exc):
        remaining_sec = mark_yf_rate_limited(exc)
        window._log.warning(
            f"[K线] {request_code} 实时刷新遇到 Yahoo Finance 限流，冷却 {remaining_sec:.0f}s: {exc}"
        )
        return
    if isinstance(exc, _EXPECTED_REALTIME_ERRORS):
        window._log.warning(f"[K线] {request_code} 实时刷新异常: {exc}")
        return
    raise exc


def _apply_realtime_quote_result(
    result,
    *,
    window,
    request_code: str,
    request_generation: int,
) -> None:
    if not _is_current_request(window, request_code, request_generation) or result is None:
        return
    if isinstance(result, Exception):
        _report_realtime_error(window, request_code, result)
        return
    try:
        refresh_last_bar(window, result)
    except RuntimeError:
        return


def poll_rt_update(window):
    """定时器回调：后台拉取报价，随后重建完整版本化快照。"""
    if getattr(window, "_closing", False) or not getattr(window, "_runtime_active", True):
        return
    market = window._get_market()
    if _stop_realtime_for_closed_market(window, market):
        return
    controller = getattr(window, "_load_controller", None)
    identity = getattr(controller, "current_identity", None)
    if controller is None or not controller.is_current(identity):
        return
    if _use_cached_asian_quote(window, market):
        return
    _submit_owned_window_task(
        window,
        "realtime_quote",
        partial(
            _fetch_realtime_quote,
            market=market,
            request_code=identity.code,
            data_provider=window.data_provider,
        ),
        partial(
            _apply_realtime_quote_result,
            window=window,
            request_code=identity.code,
            request_generation=identity.generation,
        ),
        controller.task_id("realtime-quote", identity=identity),
        20.0,
        identity=identity,
    )


def _quote_fingerprint(quote: dict) -> tuple:
    return tuple(str(quote.get(key, "")) for key in ("date", "open", "high", "low", "close", "volume"))


def _merge_realtime_frame(frame, quote: dict, *, market: str):
    latest_trade_date = MarketCalendar.get_latest_trade_date(market)
    if market == "CN":
        return _merge_cn_realtime_bar(frame, quote, target_trade_date=latest_trade_date)
    from ui.kline_window_asian import apply_asian_live_quote

    return apply_asian_live_quote(frame, quote, market=market)


@dataclass(frozen=True, slots=True)
class _RealtimePrepareRequest:
    controller: object
    identity: KlineLoadIdentity
    quote: dict
    fingerprint: tuple
    frame: pd.DataFrame
    context: object
    market: str
    snapshot_version: int
    chart_theme: dict

    @property
    def owner(self) -> tuple[str, int, int]:
        return self.identity.window_id, self.identity.generation, self.snapshot_version


@dataclass(frozen=True, slots=True)
class _RealtimeQuoteBacklog:
    identity: KlineLoadIdentity
    quote: dict
    fingerprint: tuple


def _realtime_prepare_busy(window) -> bool:
    return bool(
        getattr(window, "_closing", False)
        or not getattr(window, "_runtime_active", True)
        or getattr(window, "_rt_prepare_inflight", False)
    )


def _controller_owns_realtime_frame(controller, identity) -> bool:
    if controller is None or not controller.is_current(identity):
        return False
    return controller.owns_current_frame(identity.code, identity.generation)


def _take_current_realtime_backlog(window, identity) -> _RealtimeQuoteBacklog | None:
    backlog = getattr(window, "_latest_rt_quote", None)
    if not isinstance(backlog, _RealtimeQuoteBacklog):
        if backlog is not None:
            window._latest_rt_quote = None
        return None
    if backlog.identity != identity:
        window._latest_rt_quote = None
        return None
    return backlog


def _begin_realtime_prepare(window) -> _RealtimePrepareRequest | None:
    controller = getattr(window, "_load_controller", None)
    identity = getattr(controller, "current_identity", None)
    if _realtime_prepare_busy(window) or not _controller_owns_realtime_frame(controller, identity):
        return None
    backlog = _take_current_realtime_backlog(window, identity)
    if backlog is None:
        return None
    if window.df is None or window.df.empty:
        return None
    history_frame = getattr(window, "_history_frame", None)
    source_frame = history_frame if history_frame is not None and not history_frame.empty else window.df
    snapshot_version = int(getattr(window, "_snapshot_version", 0) or 0) + 1
    from ui.theme import theme_manager

    request = _RealtimePrepareRequest(
        controller=controller,
        identity=identity,
        quote=dict(backlog.quote),
        fingerprint=backlog.fingerprint,
        frame=source_frame.copy(),
        context=current_kline_open_context(window),
        market=window._get_market(),
        snapshot_version=snapshot_version,
        chart_theme=dict(theme_manager.current_theme),
    )
    window._latest_rt_quote = None
    window._rt_prepare_inflight = True
    window._rt_prepare_owner = request.owner
    window._snapshot_version = snapshot_version
    return request


def _prepare_realtime_snapshot(cancellation_token, *, request: _RealtimePrepareRequest):
    merged = _merge_realtime_frame(request.frame, request.quote, market=request.market)
    return KlineRenderPreparer(build_kline_echarts_payload).prepare(
        merged,
        context=request.context,
        owner_id=request.identity.window_id,
        generation=request.identity.generation,
        snapshot_version=request.snapshot_version,
        source="realtime",
        payload_kwargs={"theme": request.chart_theme},
        cancellation_token=cancellation_token,
    )


def _finish_realtime_prepare(window, request: _RealtimePrepareRequest) -> None:
    if getattr(window, "_rt_prepare_owner", None) != request.owner:
        return
    window._rt_prepare_owner = None
    window._rt_prepare_inflight = False
    if request.controller.is_current(request.identity) and getattr(window, "_latest_rt_quote", None) is not None:
        resume_realtime_updates(window)


def _discard_realtime_prepare(window, request: _RealtimePrepareRequest) -> None:
    if getattr(window, "_rt_prepare_owner", None) != request.owner:
        return
    window._rt_prepare_owner = None
    window._rt_prepare_inflight = False
    if request.controller.is_current(request.identity) and getattr(window, "_latest_rt_quote", None) is None:
        window._latest_rt_quote = _RealtimeQuoteBacklog(
            identity=request.identity,
            quote=dict(request.quote),
            fingerprint=request.fingerprint,
        )


def _apply_realtime_prepared(prepared, *, window, request: _RealtimePrepareRequest) -> None:
    try:
        if prepared is not None and request.controller.is_current(request.identity):
            queue_prepared_render(window, prepared, loading=False)
    finally:
        _finish_realtime_prepare(window, request)


def _retry_realtime_prepare(_message: str, *, window, request: _RealtimePrepareRequest) -> None:
    if request.controller.is_current(request.identity) and getattr(window, "_latest_rt_quote", None) is None:
        window._latest_rt_quote = _RealtimeQuoteBacklog(
            identity=request.identity,
            quote=dict(request.quote),
            fingerprint=request.fingerprint,
        )
    _finish_realtime_prepare(window, request)


def refresh_last_bar(window, quote):
    """合并报价后在后台重算 MA/VOL-MA20/MACD，并提交完整快照。"""
    if not isinstance(quote, dict) or not quote or getattr(window, "_closing", False):
        return
    controller = getattr(window, "_load_controller", None)
    identity = getattr(controller, "current_identity", None)
    if controller is None or not controller.is_current(identity):
        return
    fingerprint = _quote_fingerprint(quote)
    owned_fingerprint = (identity, fingerprint)
    if owned_fingerprint == getattr(window, "_last_rt_quote_fingerprint", None):
        return
    window._last_rt_quote_fingerprint = owned_fingerprint
    window._latest_rt_quote = _RealtimeQuoteBacklog(
        identity=identity,
        quote=dict(quote),
        fingerprint=fingerprint,
    )
    resume_realtime_updates(window)


def _clear_realtime_generation_state(window) -> None:
    """Invalidate all quote/preparation state owned by the previous load identity."""
    window._latest_rt_quote = None
    window._last_rt_quote_fingerprint = None
    window._rt_prepare_owner = None
    window._rt_prepare_inflight = False


def _drain_realtime_backlog(window) -> None:
    if getattr(window, "_latest_rt_quote", None) is not None:
        resume_realtime_updates(window)


def resume_realtime_updates(window) -> None:
    request = _begin_realtime_prepare(window)
    if request is None:
        return
    _submit_owned_window_task(
        window,
        "realtime_prepare",
        partial(_prepare_realtime_snapshot, request=request),
        partial(_apply_realtime_prepared, window=window, request=request),
        request.controller.task_id("realtime", identity=request.identity),
        30.0,
        on_error=partial(_retry_realtime_prepare, window=window, request=request),
        on_finalized=partial(_finish_realtime_prepare, window, request),
        on_discarded=partial(_discard_realtime_prepare, window, request),
        identity=request.identity,
    )
