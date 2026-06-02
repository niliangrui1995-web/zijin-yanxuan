# -*- coding: utf-8 -*-
from __future__ import annotations

from pathlib import Path

CACHE_KEY = "global_earnings_calendar"
DEFAULT_HORIZON = "3month"
DEFAULT_LOOKAHEAD_DAYS = 45
DEFAULT_CONFIRMED_EVENTS_PATH = Path(__file__).with_name("confirmed_events.json")
DEFAULT_COMPANY_IR_SOURCES_PATH = Path(__file__).with_name("company_ir_sources.json")

