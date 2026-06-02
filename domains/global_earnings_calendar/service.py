# -*- coding: utf-8 -*-
from __future__ import annotations

import datetime as dt
import importlib
import os
import sys
from pathlib import Path
from typing import Mapping

import requests

from core.logger import get_logger
from domains.global_earnings_calendar.constants import (
    CACHE_KEY,
    DEFAULT_LOOKAHEAD_DAYS,
)
from domains.global_earnings_calendar.event_ops import (
    _PRIORITY_RANK as _PRIORITY_RANK,
)
from domains.global_earnings_calendar.event_ops import (
    _SOURCE_RANK as _SOURCE_RANK,
)
from domains.global_earnings_calendar.event_ops import (
    COMPANY_IR_SOURCE as COMPANY_IR_SOURCE,
)
from domains.global_earnings_calendar.event_ops import (
    DART_SOURCE as DART_SOURCE,
)
from domains.global_earnings_calendar.event_ops import (
    JPX_SOURCE as JPX_SOURCE,
)
from domains.global_earnings_calendar.event_ops import (
    KIND_SOURCE as KIND_SOURCE,
)
from domains.global_earnings_calendar.event_ops import (
    MOPS_SOURCE as MOPS_SOURCE,
)
from domains.global_earnings_calendar.event_ops import (
    SEC_6K_SOURCE as SEC_6K_SOURCE,
)
from domains.global_earnings_calendar.event_ops import (
    TDNET_SOURCE as TDNET_SOURCE,
)
from domains.global_earnings_calendar.event_ops import (
    _date_text as _date_text,
)
from domains.global_earnings_calendar.event_ops import (
    _is_us_after_hours_event as _is_us_after_hours_event,
)
from domains.global_earnings_calendar.event_ops import (
    _mark_yfinance_date_conflicts as _mark_yfinance_date_conflicts,
)
from domains.global_earnings_calendar.event_ops import (
    _time_label_rank as _time_label_rank,
)
from domains.global_earnings_calendar.event_ops import (
    build_demo_events as build_demo_events,
)
from domains.global_earnings_calendar.event_ops import (
    event_calendar_date as event_calendar_date,
)
from domains.global_earnings_calendar.event_ops import (
    event_sort_key as event_sort_key,
)
from domains.global_earnings_calendar.event_ops import (
    events_by_date as events_by_date,
)
from domains.global_earnings_calendar.event_ops import (
    merge_events as merge_events,
)
from domains.global_earnings_calendar.event_ops import (
    sorted_events as sorted_events,
)
from domains.global_earnings_calendar.models import (
    YFINANCE_CONFLICT_STATUS as YFINANCE_CONFLICT_STATUS,
)
from domains.global_earnings_calendar.models import (
    YFINANCE_SOURCE,
    ConfirmedEventWriteError,
    EarningsCalendarEvent,
    OligarchCompany,
    _events_match_identity,
    _normalize_status_value,
)
from domains.global_earnings_calendar.models import (
    is_yfinance_date_conflict_event as is_yfinance_date_conflict_event,
)
from domains.global_earnings_calendar.models import (
    is_yfinance_estimate_event as is_yfinance_estimate_event,
)
from domains.global_earnings_calendar.models import (
    normalize_event_status as normalize_event_status,
)
from domains.global_earnings_calendar.providers import (
    AlphaVantageEarningsCalendarProvider,
    CompanyIrEarningsCalendarProvider,
    DartEarningsDisclosureProvider,
    JpxFinancialAnnouncementProvider,
    KindEarningsDisclosureProvider,
    MopsEarningsDisclosureProvider,
    NasdaqEarningsCalendarProvider,
    SecSixKEarningsProvider,
    TdnetEarningsDisclosureProvider,
    YFinanceEarningsCalendarProvider,
)
from domains.global_earnings_calendar.rules import (
    label_matches_company as _label_matches_company,
)
from domains.global_earnings_calendar.rules import (
    market_from_ticker,
)
from domains.global_earnings_calendar.storage import ConfirmedEarningsEventsProvider

log = get_logger(__name__)


def _ensure_industry_module_path() -> None:
    project_root = Path(__file__).resolve().parents[2]
    pipeline_dir = project_root.parent / "\u6bcf\u65e5\u6218\u62a5" / "\u6bcf\u65e5\u6218\u62a5"
    pipeline_text = str(pipeline_dir)
    if pipeline_dir.is_dir() and pipeline_text not in sys.path:
        sys.path.insert(0, pipeline_text)


def _load_industry_module():
    _ensure_industry_module_path()
    try:
        return importlib.import_module("industry_dict")
    except (ImportError, OSError, RuntimeError, TypeError, ValueError) as exc:
        log.warning(f"[global earnings calendar] industry_dict unavailable: {exc}")
        return None


def build_oligarch_universe(industry_module=None) -> dict[str, OligarchCompany]:
    module = industry_module or _load_industry_module()
    if module is None:
        return {}

    oligarch_dict = getattr(module, "OLIGARCH_DICT", {}) or {}
    tickers = getattr(module, "VANGUARD_TICKERS", {}) or {}
    super_giants = set(getattr(module, "SUPER_GIANTS", set()) or set())
    strategic_giants = set(getattr(module, "STRATEGIC_GIANTS", set()) or set())

    sector_by_company: dict[str, str] = {}
    for sector, companies in dict(oligarch_dict).items():
        for raw_company in list(companies or []):
            for ticker_company in tickers:
                if _label_matches_company(str(raw_company), str(ticker_company)):
                    sector_by_company.setdefault(str(ticker_company), str(sector))

    universe: dict[str, OligarchCompany] = {}
    for company, ticker in dict(tickers).items():
        ticker_text = str(ticker or "").strip().upper()
        if not ticker_text:
            continue
        company_text = str(company or "").strip()
        if company_text in super_giants:
            priority = "super_giant"
        elif company_text in strategic_giants:
            priority = "strategic_giant"
        else:
            priority = "normal"
        universe[ticker_text] = OligarchCompany(
            company=company_text,
            ticker=ticker_text,
            sector=sector_by_company.get(company_text, ""),
            priority=priority,
            market=market_from_ticker(ticker_text),
        )
    return universe


class GlobalEarningsCalendarService:
    def __init__(
        self,
        *,
        data_store=None,
        provider: AlphaVantageEarningsCalendarProvider | None = None,
        api_key: str | None = None,
        universe: Mapping[str, OligarchCompany] | None = None,
        confirmed_provider: ConfirmedEarningsEventsProvider | None = None,
        nasdaq_provider: NasdaqEarningsCalendarProvider | None = None,
        yfinance_provider: YFinanceEarningsCalendarProvider | None = None,
        jpx_provider: JpxFinancialAnnouncementProvider | None = None,
        tdnet_provider: TdnetEarningsDisclosureProvider | None = None,
        dart_provider: DartEarningsDisclosureProvider | None = None,
        kind_provider: KindEarningsDisclosureProvider | None = None,
        mops_provider: MopsEarningsDisclosureProvider | None = None,
        sec_provider: SecSixKEarningsProvider | None = None,
        company_ir_provider: CompanyIrEarningsCalendarProvider | None = None,
        official_providers: list[tuple[str, object]] | None = None,
    ):
        self._data_store = data_store
        self.universe = dict(universe or build_oligarch_universe())
        resolved_api_key = (
            api_key
            if api_key is not None
            else os.environ.get("ALPHAVANTAGE_API_KEY") or os.environ.get("ALPHA_VANTAGE_API_KEY") or ""
        )
        self.confirmed_provider = confirmed_provider or ConfirmedEarningsEventsProvider()
        self.nasdaq_provider = nasdaq_provider or NasdaqEarningsCalendarProvider()
        self.provider = provider or AlphaVantageEarningsCalendarProvider(resolved_api_key)
        self.yfinance_provider = yfinance_provider or YFinanceEarningsCalendarProvider()
        if official_providers is None:
            self.company_ir_provider = company_ir_provider or CompanyIrEarningsCalendarProvider()
            self.jpx_provider = jpx_provider or JpxFinancialAnnouncementProvider()
            self.tdnet_provider = tdnet_provider or TdnetEarningsDisclosureProvider()
            self.dart_provider = dart_provider or DartEarningsDisclosureProvider()
            self.kind_provider = kind_provider or KindEarningsDisclosureProvider()
            self.mops_provider = mops_provider or MopsEarningsDisclosureProvider()
            self.sec_provider = sec_provider or SecSixKEarningsProvider()
            self.official_providers = [
                (COMPANY_IR_SOURCE, self.company_ir_provider),
                (JPX_SOURCE, self.jpx_provider),
                (TDNET_SOURCE, self.tdnet_provider),
                (DART_SOURCE, self.dart_provider),
                (KIND_SOURCE, self.kind_provider),
                (MOPS_SOURCE, self.mops_provider),
                (SEC_6K_SOURCE, self.sec_provider),
            ]
        else:
            self.official_providers = list(official_providers or [])

    @property
    def data_store(self):
        if self._data_store is None:
            from core.data_store import data_store

            self._data_store = data_store
        return self._data_store

    def _load_cached_events(self) -> list[EarningsCalendarEvent]:
        payload = self.data_store.load_json(CACHE_KEY, default={}) or {}
        rows = payload.get("events") if isinstance(payload, Mapping) else None
        events = [event for event in (EarningsCalendarEvent.from_dict(row) for row in rows or []) if event is not None]
        return sorted_events(
            event for event in (self._hydrate_event_from_universe(event) for event in events) if event is not None
        )

    def _hydrate_event_from_universe(self, event: EarningsCalendarEvent) -> EarningsCalendarEvent | None:
        ticker = event.ticker.strip().upper()
        company = self.universe.get(ticker)
        if company is None:
            return None
        status = _normalize_status_value(event.status, event.source)
        return EarningsCalendarEvent(
            company=company.company,
            ticker=ticker,
            sector=company.sector or event.sector,
            report_date=event.report_date,
            fiscal_period=event.fiscal_period,
            time_label=event.time_label,
            beijing_time=event.beijing_time,
            status=status,
            source=event.source,
            priority=company.priority or event.priority,
            conference_url=event.conference_url,
            market=company.market or event.market,
            original_call_time_text=event.original_call_time_text,
            original_timezone=event.original_timezone,
            call_time_source_url=event.call_time_source_url,
            call_time_source_type=event.call_time_source_type,
        )

    def _load_confirmed_events(self) -> list[EarningsCalendarEvent]:
        try:
            return self.confirmed_provider.fetch(self.universe)
        except (OSError, RuntimeError, TypeError, ValueError) as exc:
            log.warning(f"[global earnings calendar] confirmed provider failed: {exc}")
            return []

    def _save_events(self, events: list[EarningsCalendarEvent], source: str) -> None:
        self.data_store.save_json(
            CACHE_KEY,
            {
                "source": source,
                "updated_at": dt.datetime.now().isoformat(timespec="seconds"),
                "events": [event.to_dict() for event in sorted_events(events)],
            },
        )

    def sync_unverified_yfinance_cache(self) -> int:
        payload = self.data_store.load_json(CACHE_KEY, default={}) or {}
        rows = payload.get("events") if isinstance(payload, Mapping) else []
        if not isinstance(rows, list):
            return 0

        changed = 0
        synced_rows = []
        for row in rows:
            if not isinstance(row, Mapping):
                synced_rows.append(row)
                continue
            row_dict = dict(row)
            source = str(row_dict.get("source", "") or "").strip()
            current_status = str(row_dict.get("status", "") or "").strip()
            normalized_status = _normalize_status_value(current_status, source)
            if source == YFINANCE_SOURCE and normalized_status != current_status:
                row_dict["status"] = normalized_status
                changed += 1
            synced_rows.append(row_dict)

        if changed:
            synced_payload = dict(payload) if isinstance(payload, Mapping) else {}
            synced_payload["events"] = synced_rows
            synced_payload["yfinance_estimate_synced_at"] = dt.datetime.now().isoformat(timespec="seconds")
            self.data_store.save_json(CACHE_KEY, synced_payload)
        return changed

    def upsert_confirmed_event(self, event: EarningsCalendarEvent) -> EarningsCalendarEvent:
        confirmed = self._hydrate_event_from_universe(event)
        if confirmed is None:
            raise ConfirmedEventWriteError(f"unknown_ticker: {event.ticker}")
        confirmed = EarningsCalendarEvent(
            company=confirmed.company,
            ticker=confirmed.ticker,
            sector=confirmed.sector,
            report_date=confirmed.report_date,
            fiscal_period=confirmed.fiscal_period,
            time_label=confirmed.time_label,
            beijing_time=confirmed.beijing_time,
            status="confirmed",
            source="confirmed",
            priority=confirmed.priority,
            conference_url=confirmed.conference_url,
            market=confirmed.market,
            original_call_time_text=confirmed.original_call_time_text,
            original_timezone=confirmed.original_timezone,
            call_time_source_url=confirmed.call_time_source_url,
            call_time_source_type=confirmed.call_time_source_type,
        )
        self.confirmed_provider.upsert(confirmed)
        self._sync_cached_confirmed_event(confirmed)
        return confirmed

    def _sync_cached_confirmed_event(self, event: EarningsCalendarEvent) -> None:
        payload = self.data_store.load_json(CACHE_KEY, default={}) or {}
        rows = payload.get("events") if isinstance(payload, Mapping) else []
        cached_events: list[EarningsCalendarEvent] = []
        for row in rows or []:
            cached_event = EarningsCalendarEvent.from_dict(row)
            if cached_event is None:
                continue
            hydrated = self._hydrate_event_from_universe(cached_event)
            if hydrated is not None:
                cached_events.append(hydrated)

        updated = False
        merged_events: list[EarningsCalendarEvent] = []
        for cached_event in cached_events:
            if _events_match_identity(cached_event, event):
                merged_events.append(event)
                updated = True
            else:
                merged_events.append(cached_event)
        if not updated:
            merged_events.append(event)

        source = "confirmed_writeback"
        if isinstance(payload, Mapping):
            source = str(payload.get("source") or source)
        self._save_events(merge_events(merged_events), source)

    @staticmethod
    def _filter_window(
        events: list[EarningsCalendarEvent],
        *,
        today: dt.date,
        lookahead_days: int,
    ) -> list[EarningsCalendarEvent]:
        end = today + dt.timedelta(days=max(0, int(lookahead_days)))
        filtered = []
        for event in events:
            try:
                day = dt.date.fromisoformat(event_calendar_date(event)[:10])
            except ValueError:
                continue
            if today <= day <= end:
                filtered.append(event)
        return sorted_events(filtered)

    def load_events(
        self,
        *,
        today: dt.date | None = None,
        lookahead_days: int = DEFAULT_LOOKAHEAD_DAYS,
        allow_network: bool = False,
    ) -> list[EarningsCalendarEvent]:
        today = today or dt.date.today()
        local = merge_events(
            self._filter_window(self._load_confirmed_events(), today=today, lookahead_days=lookahead_days)
            + self._filter_window(self._load_cached_events(), today=today, lookahead_days=lookahead_days)
        )
        if local:
            return local

        if allow_network:
            refreshed = self.refresh_events(today=today, lookahead_days=lookahead_days)
            if refreshed:
                return refreshed

        return []

    def refresh_events(
        self,
        *,
        today: dt.date | None = None,
        lookahead_days: int = DEFAULT_LOOKAHEAD_DAYS,
    ) -> list[EarningsCalendarEvent]:
        today = today or dt.date.today()
        lookahead_days = max(0, int(lookahead_days))
        confirmed_events = self._filter_window(
            self._load_confirmed_events(),
            today=today,
            lookahead_days=lookahead_days,
        )
        cached_events = self._filter_window(
            self._load_cached_events(),
            today=today,
            lookahead_days=lookahead_days,
        )
        network_events: list[EarningsCalendarEvent] = []
        refreshed_sources: set[str] = set()

        provider_calls = tuple(self.official_providers) + (
            ("Nasdaq", self.nasdaq_provider),
            ("Alpha Vantage", self.provider),
            ("Yahoo Finance", self.yfinance_provider),
        )
        for provider_name, provider in provider_calls:
            try:
                provider_events = list(provider.fetch(self.universe, today=today, lookahead_days=lookahead_days) or [])
            except (ImportError, requests.RequestException, OSError, RuntimeError, TypeError, ValueError) as exc:
                log.warning(f"[global earnings calendar] {provider_name} refresh failed: {exc}")
                continue
            network_events.extend(provider_events)
            refreshed_sources.update(
                str(event.source or provider_name or "").strip() for event in provider_events if event is not None
            )

        network_events = self._filter_window(network_events, today=today, lookahead_days=lookahead_days)
        if network_events:
            cached_fallback_events = [
                event for event in cached_events if str(event.source or "").strip() not in refreshed_sources
            ]
            filtered = merge_events(confirmed_events + cached_fallback_events + network_events)
            self._save_events(filtered, "provider")
            return filtered

        return merge_events(confirmed_events + cached_events)
