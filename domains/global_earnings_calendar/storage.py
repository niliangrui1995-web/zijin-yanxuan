# -*- coding: utf-8 -*-
from __future__ import annotations

import json
from pathlib import Path
from typing import Mapping

from core.logger import get_logger
from domains.global_earnings_calendar.constants import DEFAULT_CONFIRMED_EVENTS_PATH
from domains.global_earnings_calendar.event_ops import sorted_events
from domains.global_earnings_calendar.models import (
    ConfirmedEventWriteError,
    EarningsCalendarEvent,
    OligarchCompany,
    _events_match_identity,
)

log = get_logger(__name__)


class ConfirmedEarningsEventsProvider:
    def __init__(self, path: str | Path | None = None):
        self.path = Path(path) if path is not None else DEFAULT_CONFIRMED_EVENTS_PATH

    def fetch(self, universe: Mapping[str, OligarchCompany], **_kwargs) -> list[EarningsCalendarEvent]:
        if not self.path.is_file():
            return []
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, UnicodeDecodeError) as exc:
            log.warning(f"[global earnings calendar] confirmed events unavailable: {exc}")
            return []

        rows = payload.get("events") if isinstance(payload, Mapping) else payload
        events: list[EarningsCalendarEvent] = []
        for row in rows or []:
            event = EarningsCalendarEvent.from_dict(row)
            if event is None:
                continue
            ticker = event.ticker.strip().upper()
            company = universe.get(ticker)
            if company is None:
                continue
            events.append(
                EarningsCalendarEvent(
                    company=company.company,
                    ticker=ticker,
                    sector=company.sector or event.sector,
                    report_date=event.report_date,
                    fiscal_period=event.fiscal_period,
                    time_label=event.time_label,
                    beijing_time=event.beijing_time,
                    status=event.status or "confirmed",
                    source=event.source or "confirmed",
                    priority=company.priority or event.priority,
                    conference_url=event.conference_url,
                    market=company.market or event.market,
                    original_call_time_text=event.original_call_time_text,
                    original_timezone=event.original_timezone,
                    call_time_source_url=event.call_time_source_url,
                    call_time_source_type=event.call_time_source_type,
                )
            )
        return sorted_events(events)

    def upsert(self, event: EarningsCalendarEvent) -> None:
        rows: list[dict] = []
        if self.path.is_file():
            try:
                payload = json.loads(self.path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError, UnicodeDecodeError) as exc:
                raise ConfirmedEventWriteError(f"confirmed_json_read_failed: {exc}") from exc
            raw_rows = payload.get("events") if isinstance(payload, Mapping) else payload
            if not isinstance(raw_rows, list):
                raise ConfirmedEventWriteError("confirmed_json_events_not_list")
            rows = [dict(row) for row in raw_rows if isinstance(row, Mapping)]

        event_payload = event.to_dict()
        updated = False
        for idx, row in enumerate(rows):
            existing = EarningsCalendarEvent.from_dict(row)
            if existing is not None and _events_match_identity(existing, event):
                rows[idx] = event_payload
                updated = True
                break
        if not updated:
            rows.append(event_payload)

        payload = {"events": rows}
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temp_path = self.path.with_name(f"{self.path.name}.tmp")
        try:
            temp_path.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            temp_path.replace(self.path)
        except OSError as exc:
            raise ConfirmedEventWriteError(f"confirmed_json_write_failed: {exc}") from exc
