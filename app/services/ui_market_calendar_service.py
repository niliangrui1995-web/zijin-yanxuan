# -*- coding: utf-8 -*-
"""UI-facing market-calendar entrypoints."""

from __future__ import annotations

from domains.market_calendar import MarketCalendar
from domains.market_calendar.calendar_service import shutdown_market_calendar_tasks

__all__ = ["MarketCalendar", "shutdown_market_calendar_tasks"]
