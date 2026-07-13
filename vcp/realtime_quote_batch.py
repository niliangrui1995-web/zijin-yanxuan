from __future__ import annotations


def normalize_error_text(exc_or_text) -> str:
    if isinstance(exc_or_text, BaseException):
        parts = [str(exc_or_text or "").strip()]
        reason = getattr(exc_or_text, "reason", None)
        if reason:
            parts.append(str(reason).strip())
        if getattr(exc_or_text, "__cause__", None):
            parts.append(str(exc_or_text.__cause__).strip())
        if getattr(exc_or_text, "__context__", None):
            parts.append(str(exc_or_text.__context__).strip())
        text = " | ".join(part for part in parts if part)
    else:
        text = str(exc_or_text or "").strip()
    return " ".join(text.lower().split())


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
