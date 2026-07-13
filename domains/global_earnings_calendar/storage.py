# -*- coding: utf-8 -*-
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import tempfile
import uuid
from contextlib import contextmanager, suppress
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
    _hydrate_event_from_company,
)

log = get_logger(__name__)


@contextmanager
def _serialized_json_write(path: Path):
    """Serialize cross-thread/process JSON read-modify-write cycles via SQLite."""
    lock_dir = Path(tempfile.gettempdir()) / "vcp_hunter_write_locks"
    lock_dir.mkdir(parents=True, exist_ok=True)
    lock_key = hashlib.sha256(path.name.encode("utf-8")).hexdigest()[:16]
    lock_path = lock_dir / f"confirmed-events-{lock_key}.sqlite3"
    connection = sqlite3.connect(str(lock_path), timeout=30, isolation_level=None)
    try:
        connection.execute("PRAGMA busy_timeout=30000")
        connection.execute("BEGIN IMMEDIATE")
        yield
        connection.commit()
    finally:
        if connection.in_transaction:
            with suppress(sqlite3.Error):
                connection.rollback()
        connection.close()


class ConfirmedEarningsEventsProvider:
    def __init__(self, path: str | Path | None = None):
        self.path = Path(path) if path is not None else DEFAULT_CONFIRMED_EVENTS_PATH

    def fetch(self, universe: Mapping[str, OligarchCompany], **_kwargs) -> list[EarningsCalendarEvent]:
        if not self.path.is_file():
            return []
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, UnicodeDecodeError) as exc:
            log.warning(f"[global earnings calendar] confirmed events unavailable at {self.path}: {exc}")
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
                _hydrate_event_from_company(
                    event,
                    company,
                    status=event.status or "confirmed",
                    source=event.source or "confirmed",
                )
            )
        return sorted_events(events)

    def _load_rows_for_update(self) -> list[dict]:
        if not self.path.is_file():
            return []
        try:
            stored_payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise ConfirmedEventWriteError(f"confirmed_json_read_failed: {exc}") from exc
        raw_rows = stored_payload.get("events") if isinstance(stored_payload, Mapping) else stored_payload
        if not isinstance(raw_rows, list):
            raise ConfirmedEventWriteError("confirmed_json_events_not_list")
        return [dict(row) for row in raw_rows if isinstance(row, Mapping)]

    @staticmethod
    def _merge_event(rows: list[dict], event: EarningsCalendarEvent) -> None:
        event_payload = event.to_dict()
        for idx, row in enumerate(rows):
            existing = EarningsCalendarEvent.from_dict(row)
            if existing is not None and _events_match_identity(existing, event):
                rows[idx] = event_payload
                return
        rows.append(event_payload)

    def _replace_payload(self, payload: dict) -> None:
        temp_path = self.path.with_name(f"{self.path.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp")
        try:
            temp_path.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            try:
                json.loads(temp_path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, UnicodeDecodeError) as exc:
                raise ConfirmedEventWriteError(f"confirmed_json_write_validation_failed: {exc}") from exc
            temp_path.replace(self.path)
        finally:
            if temp_path.exists():
                with suppress(OSError):
                    temp_path.unlink()

    def upsert(self, event: EarningsCalendarEvent) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        try:
            with _serialized_json_write(self.path):
                rows = self._load_rows_for_update()
                self._merge_event(rows, event)
                self._replace_payload({"events": rows})
        except ConfirmedEventWriteError:
            raise
        except (OSError, sqlite3.Error) as exc:
            raise ConfirmedEventWriteError(f"confirmed_json_write_failed: {exc}") from exc
