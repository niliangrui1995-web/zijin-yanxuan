# -*- coding: utf-8 -*-
from __future__ import annotations

import datetime as dt
import importlib
import os
import queue
import sys
import threading
import time
from dataclasses import replace
from pathlib import Path
from typing import Mapping, Protocol

from core.logger import get_logger
from core.observability import record_metric
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
from domains.global_earnings_calendar.http_utils import redact_sensitive_data, redact_sensitive_text
from domains.global_earnings_calendar.models import (
    YFINANCE_CONFLICT_STATUS as YFINANCE_CONFLICT_STATUS,
)
from domains.global_earnings_calendar.models import (
    YFINANCE_SOURCE,
    ConfirmedEventWriteError,
    EarningsCalendarEvent,
    OligarchCompany,
    _events_match_identity,
    _hydrate_event_from_company,
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
from infra.tasks.lifecycle import raise_if_cancelled as _raise_if_cancelled

log = get_logger(__name__)
EXCLUDED_OLIGARCH_TICKERS = {"6594.T"}
EXCLUDED_OLIGARCH_COMPANIES = {"Nidec"}


class CancellationTokenLike(Protocol):
    def raise_if_cancelled(self) -> None: ...


def _provider_failure_detail(provider_name: str, error: object) -> dict[str, object]:
    error_text = " | ".join(part.strip() for part in redact_sensitive_text(error).splitlines() if part.strip())
    log.warning(f"[global earnings calendar] {provider_name} refresh failed: {error_text}")
    return {
        "provider": provider_name,
        "reason": "provider_fetch_failed",
        "sample_error": error_text[:500],
        "all_failed": True,
        "retryable": True,
    }


def _provider_result(provider_name: str, provider, rows) -> tuple[list[EarningsCalendarEvent], dict | None]:
    provider_events = [event for event in rows if event is not None]
    degradation = getattr(provider, "last_degradation", None)
    if not isinstance(degradation, Mapping):
        return provider_events, None
    degradation_detail = dict(redact_sensitive_data(degradation))
    if not str(degradation_detail.get("provider", "") or "").strip():
        degradation_detail["provider"] = provider_name
    return provider_events, degradation_detail


def _provider_timeout_detail(provider_name: str, timeout_sec: float) -> dict[str, object]:
    error_text = f"provider deadline exceeded after {timeout_sec:g}s"
    log.warning(f"[global earnings calendar] {provider_name} refresh timed out: {error_text}")
    return {
        "provider": provider_name,
        "reason": "provider_timeout",
        "sample_error": error_text,
        "all_failed": True,
        "retryable": True,
    }


def _record_provider_fetch_metric(
    provider_name: str,
    elapsed_sec: float,
    *,
    status: str,
) -> None:
    tags = {"provider": provider_name, "status": status}
    record_metric(
        "global_earnings_calendar_provider_fetch_ms",
        max(0.0, elapsed_sec) * 1000.0,
        unit="ms",
        tags=tags,
    )
    if status == "timeout":
        record_metric(
            "global_earnings_calendar_provider_timeout_count",
            1,
            unit="count",
            tags={"provider": provider_name},
        )


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
        log.warning(f"[global earnings calendar] industry_dict unavailable: {redact_sensitive_text(exc)}")
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
        if company_text in EXCLUDED_OLIGARCH_COMPANIES or ticker_text in EXCLUDED_OLIGARCH_TICKERS:
            continue
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
            from infra.storage.data_store import data_store

            self._data_store = data_store
        return self._data_store

    def _load_cache_payload(self) -> Mapping:
        payload = self.data_store.load_json(CACHE_KEY, default={}) or {}
        if not isinstance(payload, Mapping):
            return {}
        sanitized = redact_sensitive_data(payload)
        if not isinstance(sanitized, Mapping):
            return {}
        if sanitized != payload:
            try:
                self.data_store.save_json(CACHE_KEY, sanitized)
            except (AttributeError, OSError, RuntimeError, TypeError, ValueError) as exc:
                log.warning(
                    "[global earnings calendar] failed to scrub historical cache credentials: "
                    f"{redact_sensitive_text(exc)}"
                )
        return sanitized

    def load_cache_status(self) -> dict[str, object]:
        state = self._load_cache_payload().get("cache_state")
        return dict(state) if isinstance(state, Mapping) else {}

    def _load_cached_events(self) -> list[EarningsCalendarEvent]:
        payload = self._load_cache_payload()
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
        return _hydrate_event_from_company(
            event,
            company,
            status=_normalize_status_value(event.status, event.source),
        )

    def _load_confirmed_events(self) -> list[EarningsCalendarEvent]:
        try:
            return self.confirmed_provider.fetch(self.universe)
        except (OSError, RuntimeError, TypeError, ValueError) as exc:
            log.warning(f"[global earnings calendar] confirmed provider failed: {redact_sensitive_text(exc)}")
            return []

    def _save_events(
        self,
        events: list[EarningsCalendarEvent],
        source: str,
        *,
        cache_state: Mapping[str, object] | None = None,
    ) -> None:
        payload = {
            "source": source,
            "updated_at": dt.datetime.now().isoformat(timespec="seconds"),
            "events": [event.to_dict() for event in sorted_events(events)],
        }
        if cache_state:
            payload["cache_state"] = dict(redact_sensitive_data(cache_state))
        self.data_store.save_json(CACHE_KEY, redact_sensitive_data(payload))

    def _save_cache_state(self, cache_state: Mapping[str, object]) -> None:
        payload = dict(self._load_cache_payload())
        payload["source"] = str(payload.get("source") or "stale_cache")
        payload["updated_at"] = dt.datetime.now().isoformat(timespec="seconds")
        payload["cache_state"] = dict(redact_sensitive_data(cache_state))
        self.data_store.save_json(CACHE_KEY, redact_sensitive_data(payload))

    def sync_unverified_yfinance_cache(self) -> int:
        payload = self._load_cache_payload()
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
            self.data_store.save_json(CACHE_KEY, redact_sensitive_data(synced_payload))
        return changed

    def upsert_confirmed_event(self, event: EarningsCalendarEvent) -> EarningsCalendarEvent:
        confirmed = self._hydrate_event_from_universe(event)
        if confirmed is None:
            raise ConfirmedEventWriteError(f"unknown_ticker: {event.ticker}")
        confirmed = replace(confirmed, status="confirmed", source="confirmed")
        self.confirmed_provider.upsert(confirmed)
        self._sync_cached_confirmed_event(confirmed)
        return confirmed

    def _sync_cached_confirmed_event(self, event: EarningsCalendarEvent) -> None:
        payload = self._load_cache_payload()
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

    @staticmethod
    def _degraded_source_failed_days(degradations: list[dict[str, object]]) -> dict[str, set[str]]:
        failed_days_by_source: dict[str, set[str]] = {}
        for degradation in degradations:
            source = str(degradation.get("provider", "") or "").strip()
            raw_days = degradation.get("failed_days")
            if not source or not isinstance(raw_days, (list, tuple, set)):
                continue
            days = {str(day or "").strip()[:10] for day in raw_days if str(day or "").strip()}
            if days:
                failed_days_by_source.setdefault(source, set()).update(days)
        return failed_days_by_source

    @staticmethod
    def _degraded_source_failed_tickers(degradations: list[dict[str, object]]) -> dict[str, set[str]]:
        failed_tickers_by_source: dict[str, set[str]] = {}
        for degradation in degradations:
            source = str(degradation.get("provider", "") or "").strip()
            raw_tickers = degradation.get("failed_tickers")
            if not source or not isinstance(raw_tickers, (list, tuple, set)):
                continue
            tickers = {str(ticker or "").strip().upper() for ticker in raw_tickers if str(ticker or "").strip()}
            if tickers:
                failed_tickers_by_source.setdefault(source, set()).update(tickers)
        return failed_tickers_by_source

    @staticmethod
    def _provider_degradation_is_total(degradation: Mapping[str, object]) -> bool:
        for key in ("all_failed", "all_days_failed", "all_tickers_failed"):
            value = degradation.get(key)
            if value is True or str(value or "").strip().lower() in {"1", "true", "yes"}:
                return True
        try:
            requested_count = max(0, int(degradation.get("requested_count", 0) or 0))
            failed_count = max(0, int(degradation.get("failed_count", 0) or 0))
            returned_events = max(0, int(degradation.get("returned_events", 0) or 0))
        except (TypeError, ValueError):
            return False
        return requested_count > 0 and failed_count >= requested_count and returned_events == 0

    @staticmethod
    def _degraded_cache_state(
        degradations: list[dict[str, object]],
        *,
        reused_event_count: int,
        provider_attempted_count: int,
        provider_total_failure_count: int,
    ) -> dict[str, object]:
        providers = sorted({str(item.get("provider", "") or "").strip() for item in degradations if item})
        failed_days = sorted(
            {
                str(day or "").strip()[:10]
                for item in degradations
                for day in (item.get("failed_days") if isinstance(item.get("failed_days"), (list, tuple, set)) else [])
                if str(day or "").strip()
            }
        )
        failed_tickers = sorted(
            {
                str(ticker or "").strip().upper()
                for item in degradations
                for ticker in (
                    item.get("failed_tickers") if isinstance(item.get("failed_tickers"), (list, tuple, set)) else []
                )
                if str(ticker or "").strip()
            }
        )
        all_providers_failed = provider_attempted_count > 0 and provider_total_failure_count >= provider_attempted_count
        return {
            "status": "failed" if all_providers_failed else "degraded",
            "reason": "all_providers_failed" if all_providers_failed else "provider_fetch_degraded",
            "degraded_at": dt.datetime.now().isoformat(timespec="seconds"),
            "providers": [provider for provider in providers if provider],
            "failed_days": failed_days,
            "failed_tickers": failed_tickers,
            "stale_cache_reused": reused_event_count > 0,
            "reused_event_count": max(0, int(reused_event_count or 0)),
            "retryable": True,
            "all_providers_failed": all_providers_failed,
            "provider_attempted_count": max(0, int(provider_attempted_count or 0)),
            "provider_total_failure_count": max(0, int(provider_total_failure_count or 0)),
            "details": [dict(redact_sensitive_data(item)) for item in degradations],
        }

    def mark_refresh_failed(self, error: object, *, reason: str = "refresh_exception") -> dict[str, object]:
        cached_events = self._load_cached_events()
        error_text = redact_sensitive_text(error).strip()
        if len(error_text) > 500:
            error_text = error_text[:497] + "..."
        cache_state = {
            "status": "degraded",
            "reason": str(reason or "refresh_exception").strip() or "refresh_exception",
            "degraded_at": dt.datetime.now().isoformat(timespec="seconds"),
            "providers": [],
            "failed_days": [],
            "failed_tickers": [],
            "stale_cache_reused": bool(cached_events),
            "reused_event_count": len(cached_events),
            "retryable": True,
            "error": error_text,
            "details": [
                {
                    "reason": str(reason or "refresh_exception").strip() or "refresh_exception",
                    "sample_error": error_text,
                }
            ],
        }
        if cached_events:
            self._save_events(cached_events, "stale_cache", cache_state=cache_state)
        else:
            self._save_cache_state(cache_state)
        return cache_state

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

    @staticmethod
    def _normalize_provider_timeout_sec(value: float | None) -> float | None:
        if value is None:
            return None
        try:
            timeout_sec = float(value)
        except (TypeError, ValueError):
            return None
        return timeout_sec if 0.0 < timeout_sec < 3600.0 else None

    def _collect_provider_fetch_results(
        self,
        provider_calls: tuple[tuple[str, object], ...],
        *,
        today: dt.date,
        lookahead_days: int,
        provider_timeout_sec: float | None,
    ) -> list[tuple[str, object, list[EarningsCalendarEvent], BaseException | None, float, bool]]:
        if provider_timeout_sec is None:
            results = []
            for provider_name, provider in provider_calls:
                started_at = time.monotonic()
                try:
                    rows = list(provider.fetch(self.universe, today=today, lookahead_days=lookahead_days) or [])
                    error = None
                except Exception as exc:  # noqa: BLE001 - isolate independent upstream providers.
                    rows = []
                    error = exc
                results.append((provider_name, provider, rows, error, time.monotonic() - started_at, False))
            return results

        completed = queue.Queue()
        started_at = time.monotonic()

        def _fetch(index: int, provider) -> None:
            call_started_at = time.monotonic()
            try:
                rows = list(provider.fetch(self.universe, today=today, lookahead_days=lookahead_days) or [])
                error = None
            except BaseException as exc:  # noqa: BLE001 - restore non-Exception failures in the caller thread.
                rows = []
                error = exc
            completed.put((index, rows, error, time.monotonic() - call_started_at))

        for index, (_provider_name, provider) in enumerate(provider_calls):
            threading.Thread(
                target=_fetch,
                args=(index, provider),
                name=f"global-earnings-provider-{index + 1}",
                daemon=True,
            ).start()

        result_by_index: dict[int, tuple[list[EarningsCalendarEvent], BaseException | None, float]] = {}
        pending = set(range(len(provider_calls)))
        deadline = started_at + provider_timeout_sec
        while pending:
            remaining_sec = deadline - time.monotonic()
            if remaining_sec <= 0:
                break
            try:
                index, rows, error, elapsed_sec = completed.get(timeout=remaining_sec)
            except queue.Empty:
                break
            if index in pending:
                result_by_index[index] = (rows, error, elapsed_sec)
                pending.remove(index)

        while True:
            try:
                index, rows, error, elapsed_sec = completed.get_nowait()
            except queue.Empty:
                break
            if index in pending:
                result_by_index[index] = (rows, error, elapsed_sec)
                pending.remove(index)

        results = []
        elapsed_sec = time.monotonic() - started_at
        for index, (provider_name, provider) in enumerate(provider_calls):
            if index in result_by_index:
                rows, error, provider_elapsed_sec = result_by_index[index]
                results.append((provider_name, provider, rows, error, provider_elapsed_sec, False))
            else:
                results.append((provider_name, provider, [], None, elapsed_sec, True))
        return results

    def refresh_events(
        self,
        *,
        today: dt.date | None = None,
        lookahead_days: int = DEFAULT_LOOKAHEAD_DAYS,
        cancellation_token: CancellationTokenLike | None = None,
        provider_timeout_sec: float | None = None,
    ) -> list[EarningsCalendarEvent]:
        _raise_if_cancelled(cancellation_token)
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
        _raise_if_cancelled(cancellation_token)
        network_events: list[EarningsCalendarEvent] = []
        provider_degradations: list[dict[str, object]] = []
        provider_total_failure_count = 0

        provider_calls = tuple(self.official_providers) + (
            ("Nasdaq", self.nasdaq_provider),
            ("Alpha Vantage", self.provider),
            ("Yahoo Finance", self.yfinance_provider),
        )
        provider_attempted_count = len(provider_calls)
        provider_timeout_sec = self._normalize_provider_timeout_sec(provider_timeout_sec)
        provider_results = self._collect_provider_fetch_results(
            provider_calls,
            today=today,
            lookahead_days=lookahead_days,
            provider_timeout_sec=provider_timeout_sec,
        )
        for provider_name, provider, rows, error, elapsed_sec, timed_out in provider_results:
            _raise_if_cancelled(cancellation_token)
            if timed_out:
                provider_degradations.append(_provider_timeout_detail(provider_name, provider_timeout_sec or 0.0))
                provider_total_failure_count += 1
                _record_provider_fetch_metric(provider_name, elapsed_sec, status="timeout")
                continue
            if error is not None:
                if not isinstance(error, Exception):
                    raise error
                provider_degradations.append(_provider_failure_detail(provider_name, error))
                provider_total_failure_count += 1
                if provider_timeout_sec is not None:
                    _record_provider_fetch_metric(provider_name, elapsed_sec, status="failure")
                continue
            provider_events, degradation_detail = _provider_result(provider_name, provider, rows)
            if degradation_detail is not None:
                provider_degradations.append(degradation_detail)
                provider_total_failure_count += int(self._provider_degradation_is_total(degradation_detail))
            if provider_timeout_sec is not None:
                _record_provider_fetch_metric(
                    provider_name,
                    elapsed_sec,
                    status="degraded" if degradation_detail is not None else "success",
                )
            network_events.extend(provider_events)

        _raise_if_cancelled(cancellation_token)
        network_events = self._filter_window(network_events, today=today, lookahead_days=lookahead_days)
        failed_days_by_source = self._degraded_source_failed_days(provider_degradations)
        failed_tickers_by_source = self._degraded_source_failed_tickers(provider_degradations)
        if network_events:
            refreshed_event_keys = {
                (str(event.ticker or "").strip().upper(), str(event.source or "").strip()) for event in network_events
            }
            cached_fallback_events = []
            stale_cache_reused = 0
            for event in cached_events:
                source = str(event.source or "").strip()
                ticker = str(event.ticker or "").strip().upper()
                failed_tickers = failed_tickers_by_source.get(source, set())
                if failed_tickers and ticker in failed_tickers:
                    cached_fallback_events.append(event)
                    stale_cache_reused += 1
                    continue
                failed_days = failed_days_by_source.get(source, set())
                if failed_days and event_calendar_date(event)[:10] in failed_days:
                    cached_fallback_events.append(event)
                    stale_cache_reused += 1
                    continue
                key = (ticker, source)
                if key not in refreshed_event_keys:
                    cached_fallback_events.append(event)
            filtered = merge_events(confirmed_events + cached_fallback_events + network_events)
            cache_state = (
                self._degraded_cache_state(
                    provider_degradations,
                    reused_event_count=stale_cache_reused,
                    provider_attempted_count=provider_attempted_count,
                    provider_total_failure_count=provider_total_failure_count,
                )
                if provider_degradations
                else None
            )
            self._save_events(filtered, "provider", cache_state=cache_state)
            return filtered

        filtered = merge_events(confirmed_events + cached_events)
        if provider_degradations:
            cache_state = self._degraded_cache_state(
                provider_degradations,
                reused_event_count=len(cached_events),
                provider_attempted_count=provider_attempted_count,
                provider_total_failure_count=provider_total_failure_count,
            )
            if filtered:
                self._save_events(filtered, "stale_cache", cache_state=cache_state)
            else:
                self._save_cache_state(cache_state)
        return filtered
