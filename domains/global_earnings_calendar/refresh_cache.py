# -*- coding: utf-8 -*-
from __future__ import annotations

import json

from core.logger import get_logger
from domains.global_earnings_calendar.service import GlobalEarningsCalendarService

log = get_logger(__name__)


def _event_count(events) -> int:
    try:
        return len(events or [])
    except TypeError:
        return 0


def _normalize_error(error: object, limit: int = 500) -> str:
    text = str(error or "").strip() or error.__class__.__name__
    text = " | ".join(part.strip() for part in text.splitlines() if part.strip()) or text
    if len(text) <= limit:
        return text
    return text[: limit - 3] + "..."


def _attach_degraded_cache_status(summary: dict[str, object], cache_status: dict[str, object]) -> None:
    if str(cache_status.get("status", "") or "").strip() != "degraded":
        return
    summary["status"] = "degraded"
    for key in ("providers", "failed_days", "failed_tickers"):
        value = cache_status.get(key)
        if isinstance(value, (list, tuple, set)):
            summary[key] = [str(item or "").strip() for item in value if str(item or "").strip()]
    for key in ("reason", "error"):
        value = str(cache_status.get(key) or "").strip()
        if value:
            summary[key] = value
    if bool(cache_status.get("retryable")):
        summary["retryable"] = True
    try:
        summary["reused_event_count"] = max(0, int(cache_status.get("reused_event_count", 0) or 0))
    except (TypeError, ValueError):
        summary["reused_event_count"] = 0


def main() -> int:
    service = None
    try:
        service = GlobalEarningsCalendarService()
        events = service.refresh_events()
        cache_status = service.load_cache_status()
        summary: dict[str, object] = {"status": "success", "events": _event_count(events)}
        _attach_degraded_cache_status(summary, cache_status)
    except Exception as exc:  # noqa: BLE001 - CLI boundary converts refresh failures to a degraded cache state.
        log.exception("[global earnings calendar] refresh cache failed; reusing local cache")
        events = []
        cache_status: dict[str, object] = {}
        if service is not None:
            try:
                cache_status = service.mark_refresh_failed(exc)
            except Exception as state_exc:  # noqa: BLE001 - keep the startup subprocess reportable.
                log.warning(f"[global earnings calendar] failed to mark degraded cache state: {state_exc}")
            try:
                events = service.load_events(allow_network=False)
            except Exception as load_exc:  # noqa: BLE001 - report the original refresh failure below.
                log.warning(f"[global earnings calendar] failed to reload stale cache after refresh failure: {load_exc}")
        summary = {
            "status": "degraded",
            "events": _event_count(events),
            "reason": "refresh_exception",
            "error": _normalize_error(exc),
            "retryable": True,
        }
        _attach_degraded_cache_status(summary, cache_status)
        summary["reason"] = str(summary.get("reason") or "refresh_exception")
        summary["error"] = _normalize_error(exc)
        summary["retryable"] = True
    print(json.dumps(summary, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
