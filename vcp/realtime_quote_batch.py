from __future__ import annotations


def normalize_quote_codes(codes) -> list[str]:
    return [str(code).strip() for code in dict.fromkeys(codes or []) if str(code or "").strip()]


def split_quote_cache_hits(normalized_codes, quote_cache, quote_times, *, now: float, dedup_window: float):
    result = {}
    pending_codes = []

    for code in normalized_codes:
        cached_time = float(quote_times.get(code, 0) or 0)
        cached_quote = quote_cache.get(code)
        if cached_quote is not None and (now - cached_time) < dedup_window:
            result[code] = dict(cached_quote)
        else:
            pending_codes.append(code)

    return result, pending_codes


def should_log_pressure(
    *, total_codes: int, pending_codes: int, cache_hits: int, now: float, last_logged_at: float
) -> bool:
    return (total_codes >= 60 or pending_codes >= 40 or cache_hits >= 20) and (
        now - float(last_logged_at or 0.0) >= 30.0
    )
