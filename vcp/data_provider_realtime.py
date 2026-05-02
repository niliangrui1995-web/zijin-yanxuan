from __future__ import annotations

import time

from core.market_calendar import MarketCalendar
from vcp.realtime_quote_batch import (
    normalize_quote_codes,
    should_log_pressure,
    split_quote_cache_hits,
)


def summarize_probe_error(exc: Exception) -> str:
    text = str(exc or "").strip() or exc.__class__.__name__
    text = " ".join(text.split())
    if len(text) > 120:
        text = text[:117] + "..."
    return text


def is_disconnect_like_error(exc_or_text) -> bool:
    if isinstance(exc_or_text, BaseException):
        text_parts = [str(exc_or_text or "").strip()]
        reason = getattr(exc_or_text, "reason", None)
        if reason:
            text_parts.append(str(reason).strip())
        if getattr(exc_or_text, "__cause__", None):
            text_parts.append(str(exc_or_text.__cause__).strip())
        if getattr(exc_or_text, "__context__", None):
            text_parts.append(str(exc_or_text.__context__).strip())
        text = " | ".join(part for part in text_parts if part)
    else:
        text = str(exc_or_text or "").strip()

    normalized = " ".join(text.lower().split())
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
    )
    return any(keyword in normalized for keyword in keywords)


def fetch_eastmoney_quotes_with_split_retry(
    provider,
    codes,
    inferred_trade_date: str,
    min_batch_size: int,
):
    normalized_codes = normalize_quote_codes(codes)
    if not normalized_codes:
        return {}, []

    try:
        return provider._request_eastmoney_quote_batch(normalized_codes, inferred_trade_date), []
    except (OSError, RuntimeError, TimeoutError, ValueError) as exc:
        if len(normalized_codes) <= min_batch_size or is_disconnect_like_error(exc):
            return {}, [str(exc)]

    mid = len(normalized_codes) // 2
    left_quotes, left_failures = fetch_eastmoney_quotes_with_split_retry(
        provider,
        normalized_codes[:mid],
        inferred_trade_date,
        min_batch_size,
    )
    right_quotes, right_failures = fetch_eastmoney_quotes_with_split_retry(
        provider,
        normalized_codes[mid:],
        inferred_trade_date,
        min_batch_size,
    )
    merged_quotes = dict(left_quotes)
    merged_quotes.update(right_quotes)
    return merged_quotes, left_failures + right_failures


def fetch_realtime_quotes_batch(
    provider,
    codes,
    *,
    log,
    batch_size_default: int,
    min_batch_size_default: int,
    batch_pause_default: float,
):
    provider._ensure_eastmoney_quote_state()
    normalized_codes = normalize_quote_codes(codes)
    if not normalized_codes:
        return {}

    try:
        quote_refreshable = MarketCalendar.is_quote_refresh_time()
    except (RuntimeError, TypeError, ValueError) as exc:
        log.debug(f"[报价] 市场日历查询失败，默认开市: {exc}")
        quote_refreshable = True

    if not quote_refreshable:
        return provider._build_offline_quotes(normalized_codes)

    try:
        latest_trade_date = MarketCalendar.get_latest_trade_date("CN")
        inferred_trade_date = (
            latest_trade_date.strftime("%Y-%m-%d")
            if latest_trade_date is not None
            else MarketCalendar.today("CN").strftime("%Y-%m-%d")
        )
    except (RuntimeError, TypeError, ValueError):
        inferred_trade_date = MarketCalendar.today("CN").strftime("%Y-%m-%d")

    now = time.time()
    provider._prune_rt_quote_cache(now=now)
    dedup_window = float(provider._rt_runtime_dedup_window_sec or 0.5)
    with provider._rt_quote_lock:
        result, dedup_codes = split_quote_cache_hits(
            normalized_codes,
            provider._rt_quote_cache,
            provider._rt_quote_time,
            now=now,
            dedup_window=dedup_window,
        )

    if not dedup_codes:
        return result

    if now < float(provider._rt_runtime_cooldown_until or 0):
        fallback_res = provider._build_offline_quotes(dedup_codes)
        result.update(fallback_res)
        return result

    if provider._offline:
        fallback_res = provider._build_offline_quotes(dedup_codes)
        result.update(fallback_res)
        return result

    batch_size = int(getattr(provider, "_rt_quote_batch_size", batch_size_default) or batch_size_default)
    min_batch_size = int(
        getattr(provider, "_rt_quote_min_batch_size", min_batch_size_default)
        or min_batch_size_default
    )
    batch_pause_sec = float(
        getattr(provider, "_rt_quote_batch_pause_sec", batch_pause_default)
        or batch_pause_default
    )
    batch_failures = 0
    failure_reasons = []
    new_fetch = {}
    cache_hits = len(result)
    fatal_failure_reason = None
    eastmoney_available = time.time() >= float(provider._rt_eastmoney_cooldown_until or 0.0)

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
            f"缓存命中={cache_hits} 实际联网={len(dedup_codes)} "
            f"batch={batch_size} dedup={dedup_window:.1f}s"
        )

    for start in range(0, len(dedup_codes), batch_size):
        batch = dedup_codes[start:start + batch_size]
        quotes = {}
        failures = []
        used_sina_fallback = False
        used_tencent_fallback = False

        if eastmoney_available:
            quotes, failures = provider._fetch_eastmoney_quotes_with_split_retry(
                batch,
                inferred_trade_date,
                min_batch_size,
            )
            if failures and any(is_disconnect_like_error(reason) for reason in failures):
                provider._enter_eastmoney_cooldown(failures[0])
                eastmoney_available = False

        if (not eastmoney_available) or failures or len(quotes) < len(batch):
            missing_batch = [code for code in batch if code not in quotes]
            if missing_batch:
                try:
                    sina_quotes = provider._request_sina_quote_batch(missing_batch, inferred_trade_date)
                    if sina_quotes:
                        quotes.update(sina_quotes)
                        used_sina_fallback = True
                except (OSError, RuntimeError, TimeoutError, ValueError) as sina_exc:
                    if not failures:
                        failures = [str(sina_exc)]
                    else:
                        failures.append(str(sina_exc))

            missing_batch = [code for code in batch if code not in quotes]
            if missing_batch:
                try:
                    tencent_quotes = provider._request_tencent_quote_batch(missing_batch, inferred_trade_date)
                    if tencent_quotes:
                        quotes.update(tencent_quotes)
                        used_tencent_fallback = True
                except (OSError, RuntimeError, TimeoutError, ValueError) as tencent_exc:
                    if not failures:
                        failures = [str(tencent_exc)]
                    else:
                        failures.append(str(tencent_exc))

        new_fetch.update(quotes)
        batch_fully_covered = all(code in quotes for code in batch)
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

        if failures and not batch_fully_covered:
            batch_failures += len(failures)
            failure_reasons.extend(failures)
            if not quotes and fatal_failure_reason is None:
                fatal_failure_reason = next(
                    (reason for reason in failures if is_disconnect_like_error(reason)),
                    None,
                )
                if fatal_failure_reason:
                    log.warning(
                        f"[实时行情] 检测到断连型失败，停止本轮后续批次: {fatal_failure_reason}"
                    )
                    break
        if batch_pause_sec > 0 and (start + batch_size) < len(dedup_codes):
            time.sleep(batch_pause_sec)

    if new_fetch:
        fetch_time = time.time()
        with provider._rt_quote_lock:
            for code, quote_data in new_fetch.items():
                provider._rt_quote_cache[code] = quote_data
                provider._rt_quote_time[code] = fetch_time
                result[code] = dict(quote_data)
        provider._prune_rt_quote_cache(now=fetch_time)
        provider._register_realtime_success()
        if batch_failures:
            log.warning(f"[实时行情] {batch_failures} 个批次抓取失败: {failure_reasons[0]}")
    elif batch_failures:
        provider._register_realtime_failure(
            failure_reasons[0] if failure_reasons else "全部实时行情批次失败"
        )

    missing_codes = [code for code in dedup_codes if code not in result]
    if missing_codes:
        stale_quotes = {}
        with provider._rt_quote_lock:
            for code in missing_codes:
                cached = provider._rt_quote_cache.get(code)
                if cached:
                    quote = dict(cached)
                    quote.setdefault("date", inferred_trade_date)
                    stale_quotes[code] = quote
        if stale_quotes:
            result.update(stale_quotes)
            missing_codes = [code for code in missing_codes if code not in stale_quotes]

    if missing_codes:
        fallback_res = provider._build_offline_quotes(missing_codes)
        result.update(fallback_res)

    return result
