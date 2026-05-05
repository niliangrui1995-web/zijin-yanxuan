# -*- coding: utf-8 -*-
from __future__ import annotations

import csv
import datetime as dt
import importlib
import io
import json
import os
import re
import shutil
import sys
import tempfile
from concurrent import futures
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Mapping

import requests

from core.logger import get_logger

log = get_logger(__name__)

CACHE_KEY = "global_earnings_calendar"
DEFAULT_HORIZON = "3month"
DEFAULT_LOOKAHEAD_DAYS = 45
DEFAULT_CONFIRMED_EVENTS_PATH = Path(__file__).with_name("confirmed_events.json")
_ALNUM_RE = re.compile(r"[a-z0-9]+")
_BEIJING_DATE_RE = re.compile(r"(?:(?P<year>\d{4})[-/])?(?P<month>\d{1,2})[-/](?P<day>\d{1,2})")
_BEIJING_TIME_RE = re.compile(r"(?P<hour>\d{1,2}):(?P<minute>\d{2})")
_PRIORITY_RANK = {"super_giant": 0, "strategic_giant": 1, "normal": 2}
_MARKET_SUFFIXES = (
    (".TWO", "TW"),
    (".TW", "TW"),
    (".KS", "KR"),
    (".T", "JP"),
    (".HK", "HK"),
    (".AS", "NL"),
    (".PA", "FR"),
    (".L", "UK"),
    (".DE", "DE"),
    (".MI", "IT"),
    (".ST", "SE"),
)


@dataclass(frozen=True)
class OligarchCompany:
    company: str
    ticker: str
    sector: str = ""
    priority: str = "normal"
    market: str = "US"


@dataclass(frozen=True)
class EarningsCalendarEvent:
    company: str
    ticker: str
    sector: str
    report_date: str
    fiscal_period: str = ""
    time_label: str = ""
    beijing_time: str = ""
    status: str = "estimated"
    source: str = ""
    priority: str = "normal"
    conference_url: str = ""
    market: str = ""
    original_call_time_text: str = ""
    original_timezone: str = ""
    call_time_source_url: str = ""
    call_time_source_type: str = ""

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, payload: Mapping | None) -> "EarningsCalendarEvent | None":
        if not isinstance(payload, Mapping):
            return None
        ticker = str(payload.get("ticker", "") or "").strip()
        report_date = str(payload.get("report_date", "") or "").strip()
        if not ticker or not report_date:
            return None
        return cls(
            company=str(payload.get("company", "") or "").strip() or ticker,
            ticker=ticker,
            sector=str(payload.get("sector", "") or "").strip(),
            report_date=report_date[:10],
            fiscal_period=str(payload.get("fiscal_period", "") or "").strip(),
            time_label=str(payload.get("time_label", "") or "").strip(),
            beijing_time=str(payload.get("beijing_time", "") or "").strip(),
            status=str(payload.get("status", "") or "").strip() or "estimated",
            source=str(payload.get("source", "") or "").strip(),
            priority=str(payload.get("priority", "") or "").strip() or "normal",
            conference_url=str(payload.get("conference_url", "") or "").strip(),
            market=str(payload.get("market", "") or "").strip(),
            original_call_time_text=str(payload.get("original_call_time_text", "") or "").strip(),
            original_timezone=str(payload.get("original_timezone", "") or "").strip(),
            call_time_source_url=str(payload.get("call_time_source_url", "") or "").strip(),
            call_time_source_type=str(payload.get("call_time_source_type", "") or "").strip(),
        )


class ConfirmedEventWriteError(RuntimeError):
    pass


def _events_match_identity(existing: EarningsCalendarEvent, candidate: EarningsCalendarEvent) -> bool:
    if existing.ticker.strip().upper() != candidate.ticker.strip().upper():
        return False
    if existing.report_date[:10] != candidate.report_date[:10]:
        return False
    existing_period = str(existing.fiscal_period or "").strip()
    candidate_period = str(candidate.fiscal_period or "").strip()
    return not existing_period or not candidate_period or existing_period == candidate_period


def _normalize_text(value: str) -> str:
    return "".join(_ALNUM_RE.findall(str(value or "").lower()))


def _label_matches_company(label: str, company: str) -> bool:
    label_norm = _normalize_text(label)
    company_norm = _normalize_text(company)
    if not label_norm or not company_norm:
        return False
    if label_norm == company_norm:
        return True
    label_head = str(label or "").split("(")[0].strip()
    head_norm = _normalize_text(label_head)
    return bool(head_norm and (head_norm == company_norm or label_norm.startswith(company_norm)))


def market_from_ticker(ticker: str) -> str:
    ticker_text = str(ticker or "").strip().upper()
    for suffix, market in _MARKET_SUFFIXES:
        if ticker_text.endswith(suffix):
            return market
    return "US"


def _date_from_any(value) -> dt.date | None:
    if value is None:
        return None
    if isinstance(value, dt.datetime):
        return value.date()
    if isinstance(value, dt.date):
        return value
    date_method = getattr(value, "date", None)
    if callable(date_method):
        try:
            result = date_method()
            if isinstance(result, dt.date):
                return result
        except (TypeError, ValueError):
            pass
    text = str(value or "").strip()[:10]
    if not text:
        return None
    try:
        return dt.date.fromisoformat(text)
    except ValueError:
        return None


def _date_from_beijing_time(value: str, report_date: str) -> dt.date | None:
    text = str(value or "").strip()
    if not text:
        return None
    match = _BEIJING_DATE_RE.search(text)
    if match is None:
        return None
    month = int(match.group("month"))
    day = int(match.group("day"))
    year_text = match.group("year")
    if year_text:
        try:
            return dt.date(int(year_text), month, day)
        except ValueError:
            return None

    base = _date_from_any(report_date)
    if base is None:
        return None
    candidates = []
    for year in (base.year - 1, base.year, base.year + 1):
        try:
            candidates.append(dt.date(year, month, day))
        except ValueError:
            continue
    if not candidates:
        return None
    return min(candidates, key=lambda candidate: abs((candidate - base).days))


def _datetime_from_beijing_time(value: str, report_date: str) -> dt.datetime | None:
    day = _date_from_beijing_time(value, report_date)
    if day is None:
        return None
    match = _BEIJING_TIME_RE.search(str(value or ""))
    if match is None:
        return None
    hour = int(match.group("hour"))
    minute = int(match.group("minute"))
    try:
        return dt.datetime.combine(day, dt.time(hour=hour, minute=minute))
    except ValueError:
        return None


def _is_us_after_hours_event(event: EarningsCalendarEvent) -> bool:
    market = str(event.market or "").strip().upper()
    if market not in {"US", "NASDAQ", "NYSE", "AMEX"}:
        return False
    time_label = str(event.time_label or "").strip()
    time_label_lower = time_label.lower()
    return time_label == "盘后" or "after" in time_label_lower


def event_calendar_date(event: EarningsCalendarEvent) -> str:
    beijing_day = _date_from_beijing_time(event.beijing_time, event.report_date)
    if beijing_day is not None:
        return beijing_day.isoformat()
    report_day = _date_from_any(event.report_date)
    if report_day is not None and _is_us_after_hours_event(event):
        return (report_day + dt.timedelta(days=1)).isoformat()
    return str(event.report_date or "").strip()[:10]


def _time_label_rank(event: EarningsCalendarEvent) -> int:
    time_label = str(event.time_label or "").strip()
    time_label_lower = time_label.lower()
    if time_label == "盘前" or "pre" in time_label_lower:
        return 0
    if time_label == "盘中" or "during" in time_label_lower:
        return 1
    if time_label == "盘后" or "after" in time_label_lower:
        return 2
    return 3


def event_sort_key(event: EarningsCalendarEvent) -> tuple:
    calendar_day = event_calendar_date(event)
    exact_time = _datetime_from_beijing_time(event.beijing_time, event.report_date)
    priority = _PRIORITY_RANK.get(event.priority, 9)
    if exact_time is not None:
        return (calendar_day, 0, exact_time.hour * 60 + exact_time.minute, priority, event.ticker)
    time_label = str(event.time_label or "").strip()
    if time_label and time_label not in {"待确认", "未知", "-"}:
        return (calendar_day, 1, _time_label_rank(event), priority, event.ticker)
    return (calendar_day, 2, 0, priority, event.ticker)


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


class AlphaVantageEarningsCalendarProvider:
    def __init__(
        self,
        api_key: str | None = None,
        *,
        session=None,
        base_url: str = "https://www.alphavantage.co/query",
        timeout: tuple[int, int] = (5, 20),
    ):
        self.api_key = (api_key or "").strip()
        self.session = session or requests
        self.base_url = base_url
        self.timeout = timeout

    def fetch(
        self,
        universe: Mapping[str, OligarchCompany],
        *,
        horizon: str = DEFAULT_HORIZON,
        **_kwargs,
    ) -> list[EarningsCalendarEvent]:
        if not self.api_key:
            return []
        response = self.session.get(
            self.base_url,
            params={
                "function": "EARNINGS_CALENDAR",
                "horizon": horizon,
                "apikey": self.api_key,
            },
            timeout=self.timeout,
        )
        response.raise_for_status()
        return self.parse_csv(response.text or "", universe)

    def parse_csv(
        self,
        csv_text: str,
        universe: Mapping[str, OligarchCompany],
    ) -> list[EarningsCalendarEvent]:
        if not csv_text.strip():
            return []
        reader = csv.DictReader(io.StringIO(csv_text))
        events: list[EarningsCalendarEvent] = []
        for row in reader:
            symbol = str(row.get("symbol", "") or "").strip().upper()
            if not symbol or symbol not in universe:
                continue
            report_date = str(row.get("reportDate", "") or "").strip()[:10]
            if not report_date:
                continue
            company = universe[symbol]
            events.append(
                EarningsCalendarEvent(
                    company=company.company,
                    ticker=symbol,
                    sector=company.sector,
                    report_date=report_date,
                    fiscal_period=str(row.get("fiscalDateEnding", "") or "").strip()[:10],
                    time_label="\u5f85\u786e\u8ba4",
                    status="estimated",
                    source="Alpha Vantage",
                    priority=company.priority,
                    market=getattr(company, "market", market_from_ticker(symbol)),
                )
            )
        return sorted_events(events)


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


class NasdaqEarningsCalendarProvider:
    _TIME_LABELS = {
        "time-after-hours": "\u76d8\u540e",
        "time-pre-market": "\u76d8\u524d",
        "time-during-market": "\u76d8\u4e2d",
        "time-not-supplied": "\u5f85\u786e\u8ba4",
    }

    def __init__(
        self,
        *,
        session=None,
        base_url: str = "https://api.nasdaq.com/api/calendar/earnings",
        timeout: tuple[int, int] = (5, 20),
        max_workers: int = 8,
    ):
        self.session = session or requests
        self.base_url = base_url
        self.timeout = timeout
        self.max_workers = max(1, int(max_workers or 1))

    def fetch(
        self,
        universe: Mapping[str, OligarchCompany],
        *,
        today: dt.date | None = None,
        lookahead_days: int = DEFAULT_LOOKAHEAD_DAYS,
        **_kwargs,
    ) -> list[EarningsCalendarEvent]:
        today = today or dt.date.today()
        us_symbols = {ticker for ticker, company in universe.items() if company.market == "US"}
        if not us_symbols:
            return []

        days = [
            today + dt.timedelta(days=offset)
            for offset in range(max(0, int(lookahead_days)) + 1)
        ]
        events: list[EarningsCalendarEvent] = []
        with futures.ThreadPoolExecutor(max_workers=min(self.max_workers, len(days))) as executor:
            future_map = {
                executor.submit(self._fetch_day, day, universe, us_symbols): day
                for day in days
            }
            for future in futures.as_completed(future_map):
                day = future_map[future]
                try:
                    events.extend(future.result())
                except (requests.RequestException, OSError, RuntimeError, TypeError, ValueError) as exc:
                    log.debug(f"[global earnings calendar] Nasdaq skip {day}: {exc}")
        return sorted_events(events)

    def _fetch_day(
        self,
        day: dt.date,
        universe: Mapping[str, OligarchCompany],
        us_symbols: set[str],
    ) -> list[EarningsCalendarEvent]:
        response = self.session.get(
            self.base_url,
            params={"date": day.strftime("%Y-%m-%d")},
            headers={
                "User-Agent": "Mozilla/5.0",
                "Accept": "application/json, text/plain, */*",
                "Origin": "https://www.nasdaq.com",
                "Referer": "https://www.nasdaq.com/",
            },
            timeout=self.timeout,
        )
        response.raise_for_status()
        return self._parse_payload(response.json(), day, universe, us_symbols)

    def _parse_payload(
        self,
        payload,
        day: dt.date,
        universe: Mapping[str, OligarchCompany],
        allowed_symbols: set[str],
    ) -> list[EarningsCalendarEvent]:
        data = payload.get("data") if isinstance(payload, Mapping) else {}
        rows = data.get("rows") if isinstance(data, Mapping) else []
        events: list[EarningsCalendarEvent] = []
        for row in rows or []:
            if not isinstance(row, Mapping):
                continue
            symbol = str(row.get("symbol", "") or "").strip().upper()
            if symbol not in allowed_symbols:
                continue
            company = universe[symbol]
            time_code = str(row.get("time", "") or "").strip()
            events.append(
                EarningsCalendarEvent(
                    company=company.company,
                    ticker=symbol,
                    sector=company.sector,
                    report_date=day.strftime("%Y-%m-%d"),
                    fiscal_period=str(row.get("fiscalQuarterEnding", "") or "").strip(),
                    time_label=self._TIME_LABELS.get(time_code, "\u5f85\u786e\u8ba4"),
                    status="estimated",
                    source="Nasdaq",
                    priority=company.priority,
                    market=company.market,
                )
            )
        return events


class YFinanceEarningsCalendarProvider:
    def __init__(
        self,
        *,
        ticker_factory=None,
        include_us: bool = False,
        max_workers: int = 8,
    ):
        self.ticker_factory = ticker_factory
        self.include_us = include_us
        self.max_workers = max(1, int(max_workers or 1))

    def fetch(
        self,
        universe: Mapping[str, OligarchCompany],
        *,
        today: dt.date | None = None,
        lookahead_days: int = DEFAULT_LOOKAHEAD_DAYS,
        **_kwargs,
    ) -> list[EarningsCalendarEvent]:
        today = today or dt.date.today()
        companies = [
            company
            for company in universe.values()
            if self.include_us or company.market != "US"
        ]
        if not companies:
            return []

        ticker_factory = self.ticker_factory or self._load_yfinance_ticker_factory()
        events: list[EarningsCalendarEvent] = []
        with futures.ThreadPoolExecutor(max_workers=min(self.max_workers, len(companies))) as executor:
            future_map = {
                executor.submit(self._fetch_one, ticker_factory, company, today, lookahead_days): company
                for company in companies
            }
            for future in futures.as_completed(future_map):
                company = future_map[future]
                try:
                    events.extend(future.result())
                except (ImportError, OSError, RuntimeError, TypeError, ValueError, AttributeError) as exc:
                    log.debug(f"[global earnings calendar] yfinance skip {company.ticker}: {exc}")
        return sorted_events(events)

    @staticmethod
    def _load_yfinance_ticker_factory():
        import yfinance as yf

        try:
            from vcp.fetchers.yf_session import build_yf_session

            session = build_yf_session()
        except (ImportError, OSError, RuntimeError, TypeError, ValueError) as exc:
            log.debug(f"[global earnings calendar] unable to build project yfinance session: {exc}")
            _ensure_ascii_ca_bundle()
            session = None

        if session is None:
            return yf.Ticker
        return lambda ticker: yf.Ticker(ticker, session=session)

    @staticmethod
    def _fetch_one(ticker_factory, company: OligarchCompany, today: dt.date, lookahead_days: int) -> list[EarningsCalendarEvent]:
        ticker = ticker_factory(company.ticker)
        calendar = getattr(ticker, "calendar", None)
        if callable(calendar):
            calendar = calendar()
        if not isinstance(calendar, Mapping):
            get_calendar = getattr(ticker, "get_calendar", None)
            calendar = get_calendar() if callable(get_calendar) else {}
        if not isinstance(calendar, Mapping):
            return []

        raw_dates = calendar.get("Earnings Date") or calendar.get("Earnings Dates") or []
        if not isinstance(raw_dates, (list, tuple, set)):
            raw_dates = [raw_dates]
        end = today + dt.timedelta(days=max(0, int(lookahead_days)))
        events: list[EarningsCalendarEvent] = []
        for raw_date in raw_dates:
            day = _date_from_any(raw_date)
            if day is None or not (today <= day <= end):
                continue
            events.append(
                EarningsCalendarEvent(
                    company=company.company,
                    ticker=company.ticker,
                    sector=company.sector,
                    report_date=day.strftime("%Y-%m-%d"),
                    time_label="\u5f85\u786e\u8ba4",
                    status="estimated",
                    source="Yahoo Finance",
                    priority=company.priority,
                    market=company.market,
                )
            )
        return events


def _ensure_ascii_ca_bundle() -> None:
    try:
        import certifi
    except ImportError:
        return
    ca_path = certifi.where()
    try:
        ca_path.encode("ascii")
        return
    except UnicodeEncodeError:
        pass

    target_dir = Path(tempfile.gettempdir()) / "codex_certifi"
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / "cacert.pem"
    try:
        if not target.is_file() or target.stat().st_size != Path(ca_path).stat().st_size:
            shutil.copyfile(ca_path, target)
    except OSError as exc:
        log.debug(f"[global earnings calendar] unable to prepare ascii CA bundle: {exc}")
        return
    os.environ.setdefault("CURL_CA_BUNDLE", str(target))
    os.environ.setdefault("REQUESTS_CA_BUNDLE", str(target))


def sorted_events(events: list[EarningsCalendarEvent]) -> list[EarningsCalendarEvent]:
    return sorted(list(events or []), key=event_sort_key)


def events_by_date(events: list[EarningsCalendarEvent]) -> dict[str, list[EarningsCalendarEvent]]:
    grouped: dict[str, list[EarningsCalendarEvent]] = {}
    for event in sorted_events(events):
        day = event_calendar_date(event)
        if day:
            grouped.setdefault(day, []).append(event)
    return grouped


def merge_events(events: list[EarningsCalendarEvent]) -> list[EarningsCalendarEvent]:
    source_rank = {
        "Lumentum IR": 0,
        "confirmed": 0,
        "Nasdaq": 1,
        "Alpha Vantage": 2,
        "Yahoo Finance": 3,
    }
    selected: list[EarningsCalendarEvent] = []
    for event in sorted_events(events):
        event_day = _date_from_any(event.report_date)
        replace_index = None
        for idx, existing in enumerate(selected):
            if existing.ticker != event.ticker:
                continue
            existing_day = _date_from_any(existing.report_date)
            same_window = (
                event_day is not None
                and existing_day is not None
                and abs((event_day - existing_day).days) <= 1
            )
            if existing.report_date == event.report_date or same_window:
                existing_rank = source_rank.get(existing.source, 9)
                event_rank = source_rank.get(event.source, 9)
                if event_rank < existing_rank:
                    replace_index = idx
                else:
                    replace_index = -1
                break
        if replace_index is None:
            selected.append(event)
        elif replace_index >= 0:
            selected[replace_index] = event
    return sorted_events(selected)


def _date_text(day: dt.date) -> str:
    return day.strftime("%Y-%m-%d")


def build_demo_events(today: dt.date | None = None) -> list[EarningsCalendarEvent]:
    base = today or dt.date.today()
    samples = [
        (3, "NVIDIA", "NVDA", "AI加速芯片与定制ASIC", "\u76d8\u540e", "05-08 05:00", "confirmed", "super_giant"),
        (5, "TSMC", "TSM", "先进制程代工", "\u76d8\u524d", "05-09 14:30", "confirmed", "super_giant"),
        (9, "Applied Materials", "AMAT", "前道晶圆设备与量测", "\u76d8\u540e", "05-14 05:30", "estimated", "normal"),
        (16, "Synopsys", "SNPS", "EDA与底层IP", "", "05-20 06:00", "estimated", "normal"),
    ]
    events = []
    for offset, company, ticker, sector, time_label, beijing_time, status, priority in samples:
        events.append(
            EarningsCalendarEvent(
                company=company,
                ticker=ticker,
                sector=sector,
                report_date=_date_text(base + dt.timedelta(days=offset)),
                time_label=time_label,
                beijing_time=beijing_time,
                status=status,
                source="\u793a\u4f8b",
                priority=priority,
            )
        )
    return events


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
            event
            for event in (self._hydrate_event_from_universe(event) for event in events)
            if event is not None
        )

    def _hydrate_event_from_universe(self, event: EarningsCalendarEvent) -> EarningsCalendarEvent | None:
        ticker = event.ticker.strip().upper()
        company = self.universe.get(ticker)
        if company is None:
            return None
        return EarningsCalendarEvent(
            company=company.company,
            ticker=ticker,
            sector=company.sector or event.sector,
            report_date=event.report_date,
            fiscal_period=event.fiscal_period,
            time_label=event.time_label,
            beijing_time=event.beijing_time,
            status=event.status or "estimated",
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
        end = today + dt.timedelta(days=lookahead_days)
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
        fetched: list[EarningsCalendarEvent] = []
        fetched.extend(self._load_confirmed_events())

        provider_calls = (
            ("Nasdaq", self.nasdaq_provider),
            ("Alpha Vantage", self.provider),
            ("Yahoo Finance", self.yfinance_provider),
        )
        for provider_name, provider in provider_calls:
            try:
                fetched.extend(provider.fetch(self.universe, today=today, lookahead_days=lookahead_days))
            except (ImportError, requests.RequestException, OSError, RuntimeError, TypeError, ValueError) as exc:
                log.warning(f"[global earnings calendar] {provider_name} refresh failed: {exc}")

        filtered = merge_events(self._filter_window(fetched, today=today, lookahead_days=lookahead_days))
        if filtered:
            self._save_events(filtered, "provider")
            return filtered

        local = merge_events(
            self._filter_window(self._load_confirmed_events(), today=today, lookahead_days=lookahead_days)
            + self._filter_window(self._load_cached_events(), today=today, lookahead_days=lookahead_days)
        )
        return local
