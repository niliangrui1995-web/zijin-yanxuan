# -*- coding: utf-8 -*-
from __future__ import annotations

import json

from domains.global_earnings_calendar.service import GlobalEarningsCalendarService


def main() -> int:
    service = GlobalEarningsCalendarService()
    events = service.refresh_events()
    cache_status = service.load_cache_status()
    summary: dict[str, object] = {"status": "success", "events": len(events or [])}
    if str(cache_status.get("status", "") or "").strip() == "degraded":
        summary["status"] = "degraded"
        for key in ("providers", "failed_days", "failed_tickers"):
            value = cache_status.get(key)
            if isinstance(value, (list, tuple, set)):
                summary[key] = [str(item or "").strip() for item in value if str(item or "").strip()]
        try:
            summary["reused_event_count"] = max(0, int(cache_status.get("reused_event_count", 0) or 0))
        except (TypeError, ValueError):
            summary["reused_event_count"] = 0
    print(json.dumps(summary, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
