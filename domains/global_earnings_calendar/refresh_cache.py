# -*- coding: utf-8 -*-
from __future__ import annotations

import json

from domains.global_earnings_calendar.service import GlobalEarningsCalendarService


def main() -> int:
    events = GlobalEarningsCalendarService().refresh_events()
    print(json.dumps({"status": "success", "events": len(events or [])}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
