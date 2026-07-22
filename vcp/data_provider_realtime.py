from __future__ import annotations

import time
from contextlib import suppress
from datetime import datetime, timedelta, timezone

from core.market_calendar import MarketCalendar
from infra.tasks.lifecycle import (
    raise_if_cancelled,
    reraise_task_cancellation,
    wait_with_cancellation,
)
from infra.tasks.owner_lifecycle import invoke_with_cancellation
from vcp.realtime_quote_batch import (
    normalize_error_text,
    normalize_quote_codes,
    should_log_pressure,
    split_quote_cache_hits,
)

FALLBACK_PRESSURE_FETCH_LIMIT = 20
FALLBACK_PRESSURE_MIN_PENDING = 40
_EASTMONEY_FAST_FAIL_ATTR = "_rt_eastmoney_fast_fail_on_edge_error"
_CN_TZ = timezone(timedelta(hours=8))
_QUOTE_FRESHNESS_VALUES = ("network", "cache", "stale")
_OPENING_WARMUP_STATUSES = frozenset(
    (
        "\u5f00\u76d8\u96c6\u5408\u7ade\u4ef7",
        "\u5f00\u5e02\u524d\u65f6\u6bb5",
    )
)


def _call_quote_source(fn, cancellation_token, *args):
    return invoke_with_cancellation(fn, cancellation_token, *args)


def summarize_probe_error(exc: Exception) -> str:
    text = str(exc or "").strip() or exc.__class__.__name__
    text = " ".join(text.split())
    if len(text) > 120:
        text = text[:117] + "..."
    return text


def is_disconnect_like_error(exc_or_text) -> bool:
    normalized = normalize_error_text(exc_or_text)
    if not normalized:
        return False

    keywords = (
        "remote end closed connection without response",
        "connection aborted",
        "connectionabortederror",
        "connection reset",
        "connectionreseterror",
        "connection closed abruptly",
        "unexpected eof",
        "badstatusline",
        "10053",
        "10054",
        "http error 502",
        "bad gateway",
        "decryption_failed_or_bad_record_mac",
        "bad record mac",
        "handshake operation timed out",
        "read operation timed out",
        "timed out",
    )
    return any(keyword in normalized for keyword in keywords)


def _is_opening_warmup_quote_window() -> bool:
    try:
        market_status = str(MarketCalendar.get_market_status("CN") or "").strip()
    except (AttributeError, RuntimeError, TypeError, ValueError):
        return False
    return market_status in _OPENING_WARMUP_STATUSES


def _duplicate_counts(codes: list[str]) -> dict[str, int]:
    counts = {}
    for code in codes:
        counts[code] = counts.get(code, 0) + 1
    return {code: count for code, count in counts.items() if count > 1}


def _batch_signature(codes: list[str]) -> str:
    return "|".join(sorted(dict.fromkeys(codes or [])))


def _record_quote_request(provider, stats: dict) -> None:
    recorder = getattr(provider, "_record_realtime_quote_request", None)
    if not callable(recorder):
        return
    try:
        recorder(stats)
    except (AttributeError, RuntimeError, TypeError, ValueError):
        return


def _iso_received_quote_time(received_at: float) -> str:
    try:
        return datetime.fromtimestamp(float(received_at), tz=_CN_TZ).isoformat(timespec="seconds")
    except (OSError, OverflowError, TypeError, ValueError):
        return ""


def _marked_quote(raw_quote, freshness: str, received_at: float | None) -> dict:
    quote = dict(raw_quote or {})
    quote["quote_freshness"] = freshness
    if not str(quote.get("quote_time") or "").strip():
        if freshness == "network" and received_at is not None:
            quote["quote_time"] = _iso_received_quote_time(received_at)
        elif quote.get("date"):
            quote["quote_time"] = str(quote.get("date") or "").strip()
    if freshness == "network" and received_at is not None:
        quote.setdefault("quote_received_at", float(received_at))
    return quote


def _mark_quote_freshness(quotes: dict, freshness: str, *, received_at: float | None = None) -> dict:
    if freshness not in _QUOTE_FRESHNESS_VALUES:
        raise ValueError(f"unsupported quote freshness: {freshness}")
    for code, raw_quote in list((quotes or {}).items()):
        quotes[code] = _marked_quote(raw_quote, freshness, received_at)
    return quotes


def _normalized_result_quote(raw_quote) -> tuple[dict, str]:
    quote = dict(raw_quote or {})
    freshness = str(quote.get("quote_freshness") or "").strip().lower()
    if freshness in _QUOTE_FRESHNESS_VALUES:
        return quote, freshness
    quote["quote_freshness"] = "stale"
    if not str(quote.get("quote_time") or "").strip() and quote.get("date"):
        quote["quote_time"] = str(quote.get("date") or "").strip()
    return quote, "stale"


def _final_quote_result_stats(result: dict | None) -> tuple[dict[str, int], str]:
    counts = {freshness: 0 for freshness in _QUOTE_FRESHNESS_VALUES}
    quote_times = []
    for code, raw_quote in list((result or {}).items()):
        quote, freshness = _normalized_result_quote(raw_quote)
        if quote != raw_quote:
            result[code] = quote
        counts[freshness] += 1
        quote_time = str(quote.get("quote_time") or "").strip()
        if quote_time:
            quote_times.append(quote_time)
    return counts, max(quote_times, default="")


def _new_quote_request_stats(normalized_codes: list[str], *, raw_codes: list[str], started_at: float) -> dict:
    return {
        "started_at": started_at,
        "requested_count": len(raw_codes),
        "unique_requested_count": len(dict.fromkeys(normalized_codes)),
        "duplicate_requested_codes": _duplicate_counts(raw_codes),
        "pending_count": 0,
        "cache_hit_count": 0,
        "batch_count": 0,
        "recent_codes_count": 0,
        "triggered_network": False,
        "network_attempted_count": 0,
        "network_throttled": False,
        "network_throttle_reason": "",
        "source_layers": [],
        "batches": [],
        "status": "started",
    }


def _finish_quote_request(provider, stats: dict, *, status: str, result: dict | None = None) -> None:
    ended_at = time.time()
    payload = dict(stats or {})
    payload["status"] = status
    payload["ended_at"] = ended_at
    try:
        payload["elapsed_ms"] = round((ended_at - float(payload.get("started_at") or ended_at)) * 1000.0, 3)
    except (TypeError, ValueError):
        payload["elapsed_ms"] = None
    if result is not None:
        try:
            payload["result_count"] = len(result or {})
        except TypeError:
            payload["result_count"] = 0
        counts, latest_quote_time = _final_quote_result_stats(result)
        payload["network_result_count"] = counts["network"]
        payload["cache_hit_count"] = counts["cache"]
        payload["stale_result_count"] = counts["stale"]
        payload["missing_result_count"] = max(
            0,
            int(payload.get("unique_requested_count") or 0) - int(payload.get("result_count") or 0),
        )
        payload["latest_quote_time"] = latest_quote_time
    _record_quote_request(provider, payload)


def _add_quote_source(stats: dict, source: str) -> None:
    source_text = str(source or "").strip()
    if not source_text:
        return
    layers = list(stats.get("source_layers") or [])
    if source_text not in layers:
        layers.append(source_text)
    stats["source_layers"] = layers


def _fallback_pressure_fetch_limit(provider, pending_count: int) -> int:
    pending_count = int(pending_count or 0)
    if pending_count < FALLBACK_PRESSURE_MIN_PENDING:
        return 0
    try:
        limit = int(
            getattr(
                provider,
                "_rt_fallback_pressure_fetch_limit",
                FALLBACK_PRESSURE_FETCH_LIMIT,
            )
            or 0
        )
    except (TypeError, ValueError):
        limit = FALLBACK_PRESSURE_FETCH_LIMIT
    if limit <= 0 or pending_count <= limit:
        return 0
    return max(1, limit)


def fetch_eastmoney_quotes_with_split_retry(
    provider,
    codes,
    inferred_trade_date: str,
    min_batch_size: int,
    *,
    cancellation_token=None,
):
    raise_if_cancelled(cancellation_token)
    normalized_codes = normalize_quote_codes(codes)
    if not normalized_codes:
        return {}, []

    try:
        quotes = _call_quote_source(
            provider._request_eastmoney_quote_batch, cancellation_token, normalized_codes, inferred_trade_date
        )
        return quotes, []
    except (OSError, RuntimeError, TimeoutError, ValueError) as exc:
        reraise_task_cancellation(exc)
        if len(normalized_codes) <= min_batch_size or is_disconnect_like_error(exc):
            return {}, [str(exc)]

    mid = len(normalized_codes) // 2
    left_quotes, left_failures = fetch_eastmoney_quotes_with_split_retry(
        provider,
        normalized_codes[:mid],
        inferred_trade_date,
        min_batch_size,
        cancellation_token=cancellation_token,
    )
    right_quotes, right_failures = fetch_eastmoney_quotes_with_split_retry(
        provider,
        normalized_codes[mid:],
        inferred_trade_date,
        min_batch_size,
        cancellation_token=cancellation_token,
    )
    merged_quotes = dict(left_quotes)
    merged_quotes.update(right_quotes)
    return merged_quotes, left_failures + right_failures


def _infer_quote_trade_date() -> str:
    try:
        latest_trade_date = MarketCalendar.get_latest_trade_date("CN")
        return (
            latest_trade_date.strftime("%Y-%m-%d")
            if latest_trade_date is not None
            else MarketCalendar.today("CN").strftime("%Y-%m-%d")
        )
    except (RuntimeError, TypeError, ValueError):
        return MarketCalendar.today("CN").strftime("%Y-%m-%d")


def _marked_runtime_cache_hits(provider, normalized_codes: list[str], now: float, dedup_window: float):
    with provider._rt_quote_lock:
        result, dedup_codes = split_quote_cache_hits(
            normalized_codes,
            provider._rt_quote_cache,
            provider._rt_quote_time,
            now=now,
            dedup_window=dedup_window,
        )
    return _mark_quote_freshness(result, "cache"), dedup_codes


def _apply_realtime_quote_cache_gate(provider, normalized_codes: list[str], request_stats: dict, *, log) -> dict:
    try:
        quote_refreshable = MarketCalendar.is_quote_refresh_time()
    except (RuntimeError, TypeError, ValueError) as exc:
        log.debug(f"[报价] 市场日历查询失败，默认开市: {exc}")
        quote_refreshable = True

    if not quote_refreshable:
        result = _mark_quote_freshness(provider._build_offline_quotes(normalized_codes), "stale")
        request_stats["recent_codes_count"] = len(normalized_codes)
        _add_quote_source(request_stats, "offline_market_closed")
        _finish_quote_request(provider, request_stats, status="market_closed_offline", result=result)
        return {"done": True, "result": result}

    inferred_trade_date = _infer_quote_trade_date()
    now = time.time()
    provider._prune_rt_quote_cache(now=now)
    dedup_window = float(provider._rt_runtime_dedup_window_sec or 0.5)
    result, dedup_codes = _marked_runtime_cache_hits(provider, normalized_codes, now, dedup_window)
    request_stats["cache_hit_count"] = len(result)
    request_stats["pending_count"] = len(dedup_codes)

    if not dedup_codes:
        _add_quote_source(request_stats, "runtime_cache")
        _finish_quote_request(provider, request_stats, status="runtime_cache_hit", result=result)
        return {"done": True, "result": result}

    if now < float(provider._rt_runtime_cooldown_until or 0):
        fallback_res = _mark_quote_freshness(provider._build_offline_quotes(dedup_codes), "stale")
        result.update(fallback_res)
        request_stats["recent_codes_count"] = len(dedup_codes)
        _add_quote_source(request_stats, "offline_runtime_cooldown")
        _finish_quote_request(provider, request_stats, status="runtime_cooldown_offline", result=result)
        return {"done": True, "result": result}

    if provider._offline:
        fallback_res = _mark_quote_freshness(provider._build_offline_quotes(dedup_codes), "stale")
        result.update(fallback_res)
        request_stats["recent_codes_count"] = len(dedup_codes)
        _add_quote_source(request_stats, "offline_mode")
        _finish_quote_request(provider, request_stats, status="offline_mode", result=result)
        return {"done": True, "result": result}

    return {
        "done": False,
        "result": result,
        "dedup_codes": dedup_codes,
        "inferred_trade_date": inferred_trade_date,
        "now": now,
        "dedup_window": dedup_window,
    }


def _fetch_realtime_quote_batch_sources(
    provider, batch: list[str], *, inferred_trade_date: str, min_batch_size: int,
    eastmoney_available: bool,
    fast_fail_eastmoney_edge_error: bool = False,
    cancellation_token=None,
) -> tuple[dict, list[str], bool, bool, bool]:
    quotes, failures = {}, []
    used_sina_fallback = False
    used_tencent_fallback = False
    fast_fail_sentinel = object()
    previous_fast_fail = fast_fail_sentinel
    if fast_fail_eastmoney_edge_error:
        previous_fast_fail = getattr(provider, _EASTMONEY_FAST_FAIL_ATTR, fast_fail_sentinel)
        setattr(provider, _EASTMONEY_FAST_FAIL_ATTR, True)
    try:
        if eastmoney_available:
            quotes, failures = _call_quote_source(
                provider._fetch_eastmoney_quotes_with_split_retry,
                cancellation_token,
                batch,
                inferred_trade_date,
                min_batch_size,
            )
            if failures and any(is_disconnect_like_error(reason) for reason in failures):
                provider._enter_eastmoney_cooldown(failures[0])
                eastmoney_available = False
    finally:
        if fast_fail_eastmoney_edge_error:
            if previous_fast_fail is fast_fail_sentinel:
                with suppress(AttributeError):
                    delattr(provider, _EASTMONEY_FAST_FAIL_ATTR)
            else:
                setattr(provider, _EASTMONEY_FAST_FAIL_ATTR, previous_fast_fail)
    if (not eastmoney_available) or failures or len(quotes) < len(batch):
        missing_batch = [code for code in batch if code not in quotes]
        if missing_batch:
            try:
                sina_quotes = _call_quote_source(
                    provider._request_sina_quote_batch, cancellation_token, missing_batch, inferred_trade_date
                )
                if sina_quotes:
                    quotes.update(sina_quotes)
                    used_sina_fallback = True
            except (OSError, RuntimeError, TimeoutError, ValueError) as sina_exc:
                reraise_task_cancellation(sina_exc)
                if not failures:
                    failures = [str(sina_exc)]
                else:
                    failures.append(str(sina_exc))
        missing_batch = [code for code in batch if code not in quotes]
        if missing_batch:
            try:
                tencent_quotes = _call_quote_source(
                    provider._request_tencent_quote_batch, cancellation_token, missing_batch, inferred_trade_date
                )
                if tencent_quotes:
                    quotes.update(tencent_quotes)
                    used_tencent_fallback = True
            except (OSError, RuntimeError, TimeoutError, ValueError) as tencent_exc:
                reraise_task_cancellation(tencent_exc)
                if not failures:
                    failures = [str(tencent_exc)]
                else:
                    failures.append(str(tencent_exc))
    return quotes, failures, eastmoney_available, used_sina_fallback, used_tencent_fallback


def _record_realtime_batch_sources(
    provider,
    request_stats: dict,
    batch_record: dict,
    batch: list[str],
    quotes: dict,
    failures: list[str],
    *,
    used_sina_fallback: bool,
    used_tencent_fallback: bool,
) -> None:
    batch_fully_covered = all(code in quotes for code in batch)
    batch_record["used_sina_fallback"] = bool(used_sina_fallback)
    batch_record["used_tencent_fallback"] = bool(used_tencent_fallback)
    batch_record["failure_count"] = len(failures)
    batch_record["returned_count"] = len(quotes)
    if batch_fully_covered:
        batch_record["status"] = "ok"
    elif quotes:
        batch_record["status"] = "partial"
    else:
        batch_record["status"] = "failed"
    if quotes:
        sources = {
            str(quote.get("source") or "").strip()
            for quote in quotes.values()
            if isinstance(quote, dict) and str(quote.get("source") or "").strip()
        }
        for source in sorted(sources):
            _add_quote_source(request_stats, source)
    if used_sina_fallback:
        fallback_msg = provider._rt_eastmoney_last_error or "东方财富链路异常"
        provider._log_quote_fallback(
            f"[实时行情] 已切换新浪批量报价，覆盖 {len(quotes)}/{len(batch)} 只: {fallback_msg}",
            warning=False,
        )
    if used_tencent_fallback:
        fallback_msg = provider._rt_eastmoney_last_error or "eastmoney realtime quote unavailable"
        provider._log_quote_fallback(
            f"[realtime quotes] switched to Tencent fallback, covered {len(quotes)}/{len(batch)} codes: {fallback_msg}",
            warning=False,
        )


def _cache_network_quote_results(provider, result: dict, new_fetch: dict, fetch_time: float) -> None:
    _mark_quote_freshness(new_fetch, "network", received_at=fetch_time)
    with provider._rt_quote_lock:
        for code, quote_data in new_fetch.items():
            provider._rt_quote_cache[code] = quote_data
            provider._rt_quote_time[code] = fetch_time
            result[code] = dict(quote_data)


def _fetch_realtime_quote_sources(
    provider,
    normalized_codes: list[str],
    dedup_codes: list[str],
    result: dict,
    request_stats: dict,
    *,
    inferred_trade_date: str,
    now: float,
    dedup_window: float,
    log,
    batch_size_default: int,
    min_batch_size_default: int,
    batch_pause_default: float,
    cancellation_token=None,
) -> dict:
    raise_if_cancelled(cancellation_token)
    batch_size = int(getattr(provider, "_rt_quote_batch_size", batch_size_default) or batch_size_default)
    min_batch_size = int(
        getattr(provider, "_rt_quote_min_batch_size", min_batch_size_default) or min_batch_size_default
    )
    batch_pause_sec = float(getattr(provider, "_rt_quote_batch_pause_sec", batch_pause_default) or batch_pause_default)
    batch_failures = 0
    failure_reasons = []
    new_fetch = {}
    cache_hits = len(result)
    disconnect_failure_reason_logged = None
    eastmoney_available = time.time() >= float(provider._rt_eastmoney_cooldown_until or 0.0)
    pressure_fetch_limit = _fallback_pressure_fetch_limit(provider, len(dedup_codes))
    opening_warmup_pressure = bool(pressure_fetch_limit and _is_opening_warmup_quote_window())
    network_codes = list(dedup_codes)
    request_stats["triggered_network"] = True
    if pressure_fetch_limit and not eastmoney_available:
        network_codes = dedup_codes[:pressure_fetch_limit]
        request_stats["network_throttled"] = True
        request_stats["network_throttle_reason"] = "eastmoney_cooldown"
        _add_quote_source(request_stats, "network_throttled_fallback_pressure")
        log.info(
            "[实时行情] 东方财富回退冷却中，本轮联网限量 "
            f"{len(network_codes)}/{len(dedup_codes)} 只；剩余标的使用缓存/离线兜底"
        )
    pressure_log_due = should_log_pressure(
        total_codes=len(normalized_codes),
        pending_codes=len(dedup_codes),
        cache_hits=cache_hits,
        now=now,
        last_logged_at=float(getattr(provider, "_rt_last_pressure_log_at", 0.0) or 0.0),
    )
    if pressure_log_due:
        provider._rt_last_pressure_log_at = now
        log.info(
            f"[实时行情] 本轮总数={len(normalized_codes)} "
            f"缓存命中={cache_hits} 待联网={len(dedup_codes)} "
            f"batch={batch_size} dedup={dedup_window:.1f}s"
        )
    for start in range(0, len(network_codes), batch_size):
        raise_if_cancelled(cancellation_token)
        batch = network_codes[start : start + batch_size]
        batch_record = {
            "index": int(start // batch_size) + 1,
            "codes_count": len(batch),
            "unique_codes_count": len(dict.fromkeys(batch)),
            "duplicate_codes": _duplicate_counts(batch),
            "signature": _batch_signature(batch),
            "eastmoney_available_at_start": bool(eastmoney_available),
            "used_sina_fallback": False,
            "used_tencent_fallback": False,
            "failure_count": 0,
            "returned_count": 0,
            "status": "started",
        }
        request_stats["batches"].append(batch_record)
        request_stats["network_attempted_count"] = int(request_stats.get("network_attempted_count") or 0) + len(batch)
        quotes, failures, eastmoney_available, used_sina_fallback, used_tencent_fallback = (
            _fetch_realtime_quote_batch_sources(
                provider,
                batch,
                inferred_trade_date=inferred_trade_date,
                min_batch_size=min_batch_size,
                eastmoney_available=eastmoney_available,
                fast_fail_eastmoney_edge_error=opening_warmup_pressure,
                cancellation_token=cancellation_token,
            )
        )

        new_fetch.update(quotes)
        _record_realtime_batch_sources(
            provider,
            request_stats,
            batch_record,
            batch,
            quotes,
            failures,
            used_sina_fallback=used_sina_fallback,
            used_tencent_fallback=used_tencent_fallback,
        )

        batch_fully_covered = all(code in quotes for code in batch)
        if failures and not batch_fully_covered:
            batch_failures += len(failures)
            failure_reasons.extend(failures)
            if not quotes and disconnect_failure_reason_logged is None:
                disconnect_failure_reason_logged = next(
                    (reason for reason in failures if is_disconnect_like_error(reason)),
                    None,
                )
                if disconnect_failure_reason_logged:
                    log.warning(
                        "[实时行情] 检测到断连型失败，本批次转兜底，"
                        f"后续批次继续尝试备用源: {disconnect_failure_reason_logged}"
                    )
        if batch_pause_sec > 0 and (start + batch_size) < len(dedup_codes):
            wait_with_cancellation(batch_pause_sec, cancellation_token)

        fallback_pressure_active = (not eastmoney_available) or used_sina_fallback or used_tencent_fallback
        attempted_count = int(request_stats.get("network_attempted_count") or 0)
        if (
            pressure_fetch_limit
            and fallback_pressure_active
            and attempted_count >= pressure_fetch_limit
            and attempted_count < len(dedup_codes)
        ):
            request_stats["network_throttled"] = True
            request_stats["network_throttle_reason"] = "fallback_pressure"
            _add_quote_source(request_stats, "network_throttled_fallback_pressure")
            log.info(
                "[实时行情] 外部报价源回退压力中，本轮提前停止后续联网 "
                f"{attempted_count}/{len(dedup_codes)} 只；剩余标的使用缓存/离线兜底"
            )
            break

    request_stats["batch_count"] = len(request_stats.get("batches") or [])
    request_stats["recent_codes_count"] = (
        int((request_stats.get("batches") or [{}])[-1].get("codes_count") or 0) if request_stats.get("batches") else 0
    )

    if new_fetch:
        fetch_time = time.time()
        _cache_network_quote_results(provider, result, new_fetch, fetch_time)
        provider._prune_rt_quote_cache(now=fetch_time)
        provider._register_realtime_success()
        if batch_failures:
            log.warning(f"[实时行情] {batch_failures} 个批次抓取失败: {failure_reasons[0]}")
    elif batch_failures:
        provider._register_realtime_failure(failure_reasons[0] if failure_reasons else "全部实时行情批次失败")

    return {
        "batch_failures": batch_failures,
        "failure_reasons": failure_reasons,
        "new_fetch": new_fetch,
    }


def _apply_realtime_quote_fallbacks(
    provider,
    result: dict,
    dedup_codes: list[str],
    request_stats: dict,
    *,
    inferred_trade_date: str,
    cancellation_token=None,
) -> list[str]:
    raise_if_cancelled(cancellation_token)
    missing_codes = [code for code in dedup_codes if code not in result]
    if missing_codes:
        stale_quotes = {}
        with provider._rt_quote_lock:
            for code in missing_codes:
                raise_if_cancelled(cancellation_token)
                cached = provider._rt_quote_cache.get(code)
                if cached:
                    quote = dict(cached)
                    quote.setdefault("date", inferred_trade_date)
                    stale_quotes[code] = quote
        if stale_quotes:
            _mark_quote_freshness(stale_quotes, "stale")
            result.update(stale_quotes)
            missing_codes = [code for code in missing_codes if code not in stale_quotes]
            _add_quote_source(request_stats, "stale_runtime_cache")

    if missing_codes:
        fallback_res = provider._build_offline_quotes(missing_codes)
        _mark_quote_freshness(fallback_res, "stale")
        result.update(fallback_res)
        _add_quote_source(request_stats, "offline_missing_fallback")

    return missing_codes


def _finalize_realtime_quote_stats(
    provider,
    result: dict,
    request_stats: dict,
    *,
    batch_failures: int,
    new_fetch: dict,
    missing_codes: list[str],
) -> None:
    final_status = "network_ok"
    if request_stats.get("network_throttled") or (batch_failures and new_fetch):
        final_status = "network_partial_with_fallback"
    elif batch_failures:
        final_status = "network_failed_offline_fallback" if missing_codes else "network_failed"
    _finish_quote_request(provider, request_stats, status=final_status, result=result)


def fetch_realtime_quotes_batch(
    provider,
    codes,
    *,
    log,
    batch_size_default: int,
    min_batch_size_default: int,
    batch_pause_default: float,
    cancellation_token=None,
):
    provider._ensure_eastmoney_quote_state()
    raw_codes = [str(code).strip() for code in (codes or []) if str(code or "").strip()]
    normalized_codes = normalize_quote_codes(codes)
    if not normalized_codes:
        return {}
    request_stats = _new_quote_request_stats(normalized_codes, raw_codes=raw_codes, started_at=time.time())
    gate = _apply_realtime_quote_cache_gate(provider, normalized_codes, request_stats, log=log)
    if gate["done"]:
        return gate["result"]
    result, dedup_codes = gate["result"], gate["dedup_codes"]
    fetch_state = _fetch_realtime_quote_sources(
        provider,
        normalized_codes,
        dedup_codes,
        result,
        request_stats,
        inferred_trade_date=gate["inferred_trade_date"],
        now=gate["now"],
        dedup_window=gate["dedup_window"],
        log=log,
        batch_size_default=batch_size_default,
        min_batch_size_default=min_batch_size_default,
        batch_pause_default=batch_pause_default,
        cancellation_token=cancellation_token,
    )
    missing_codes = _apply_realtime_quote_fallbacks(
        provider,
        result,
        dedup_codes,
        request_stats,
        inferred_trade_date=gate["inferred_trade_date"],
        cancellation_token=cancellation_token,
    )
    _finalize_realtime_quote_stats(
        provider,
        result,
        request_stats,
        batch_failures=fetch_state["batch_failures"],
        new_fetch=fetch_state["new_fetch"],
        missing_codes=missing_codes,
    )
    return result
