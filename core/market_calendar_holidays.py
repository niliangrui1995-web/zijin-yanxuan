# -*- coding: utf-8 -*-
"""Holiday cache and upstream fetch helpers for ``MarketCalendar``."""

from __future__ import annotations

import datetime
import json
import os
import re
import sqlite3
from typing import Any

from core.exceptions import BusinessRuleError, CacheIOError, DataFormatError, NetworkServiceError

_ISO_DATE_PATTERN = re.compile(r"\d{4}-\d{2}-\d{2}")


def is_iso_date(text: str) -> bool:
    return bool(_ISO_DATE_PATTERN.fullmatch(str(text).strip()))


def normalize_holiday_days(days: Any) -> set[str]:
    normalized: set[str] = set()
    if not isinstance(days, (list, tuple, set)):
        return normalized
    for raw in days:
        day_text = str(raw).strip()[:10]
        if is_iso_date(day_text):
            normalized.add(day_text)
    return normalized


def _nth_weekday(year: int, month: int, weekday: int, nth: int) -> datetime.date:
    day = datetime.date(year, month, 1)
    offset = (weekday - day.weekday()) % 7
    return day + datetime.timedelta(days=offset + (nth - 1) * 7)


def _japan_vernal_equinox_day(year: int) -> int:
    if 1980 <= year <= 2099:
        return int(20.8431 + 0.242194 * (year - 1980) - ((year - 1980) // 4))
    return 20


def _japan_autumnal_equinox_day(year: int) -> int:
    if 1980 <= year <= 2099:
        return int(23.2488 + 0.242194 * (year - 1980) - ((year - 1980) // 4))
    return 23


def _japan_exchange_holiday_supplements(year: int) -> set[str]:
    """Return deterministic JPX holiday supplements for cache/source drift repair."""
    y = int(year)
    base = {
        datetime.date(y, 1, 1),
        datetime.date(y, 2, 11),
        datetime.date(y, 2, 23),
        datetime.date(y, 3, _japan_vernal_equinox_day(y)),
        datetime.date(y, 4, 29),
        datetime.date(y, 5, 3),
        datetime.date(y, 5, 4),
        datetime.date(y, 5, 5),
        datetime.date(y, 8, 11),
        datetime.date(y, 9, _japan_autumnal_equinox_day(y)),
        datetime.date(y, 11, 3),
        datetime.date(y, 11, 23),
        _nth_weekday(y, 1, 0, 2),
        _nth_weekday(y, 7, 0, 3),
        _nth_weekday(y, 9, 0, 3),
        _nth_weekday(y, 10, 0, 2),
    }

    holidays = set(base)
    for day in sorted(base):
        if day.weekday() != 6:
            continue
        substitute = day + datetime.timedelta(days=1)
        while substitute in holidays:
            substitute += datetime.timedelta(days=1)
        holidays.add(substitute)

    cursor = datetime.date(y, 1, 2)
    end = datetime.date(y, 12, 30)
    while cursor <= end:
        if (
            cursor.weekday() < 5
            and cursor not in holidays
            and cursor - datetime.timedelta(days=1) in holidays
            and cursor + datetime.timedelta(days=1) in holidays
        ):
            holidays.add(cursor)
        cursor += datetime.timedelta(days=1)

    holidays.update(
        {
            datetime.date(y, 1, 2),
            datetime.date(y, 1, 3),
            datetime.date(y, 12, 31),
        }
    )
    return {day.isoformat() for day in holidays}


def _korea_exchange_holiday_supplements(year: int) -> set[str]:
    """Return deterministic KRX holiday supplements for upstream drift repair."""
    explicit_days = {
        2026: {"2026-05-25"},
    }
    return set(explicit_days.get(int(year), set()))


def apply_market_holiday_supplements(market: str, year: int, days: Any) -> set[str]:
    normalized = normalize_holiday_days(days)
    market_code = str(market or "").strip().upper()
    if market_code in {"T", "JP", "JPN", "TYO"}:
        normalized.update(_japan_exchange_holiday_supplements(int(year)))
    if market_code in {"KS", "KQ", "KR", "KOR"}:
        normalized.update(_korea_exchange_holiday_supplements(int(year)))
    return normalized


def parse_sqlite_ts(raw: str | None) -> datetime.datetime | None:
    if not raw:
        return None
    text = str(raw).strip()
    if not text:
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S"):
        try:
            return datetime.datetime.strptime(text, fmt)
        except ValueError:
            continue
    return None


def holiday_db_path(project_root: str) -> str:
    return os.path.join(project_root, "data", "vcp_hunter.db")


def ensure_holiday_table(project_root: str) -> None:
    db_path = holiday_db_path(project_root)
    os.makedirs(os.path.dirname(db_path), exist_ok=True)
    try:
        with sqlite3.connect(db_path, timeout=10) as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS market_holiday_cache (
                    market TEXT NOT NULL,
                    year INTEGER NOT NULL,
                    days_json TEXT NOT NULL,
                    updated_at TEXT NOT NULL DEFAULT (datetime('now')),
                    PRIMARY KEY (market, year)
                )
                """
            )
            conn.commit()
    except sqlite3.Error as exc:
        raise CacheIOError("holiday cache table init failed") from exc


def load_holidays_from_store(project_root: str, market: str) -> list[tuple[int, set[str], datetime.datetime | None]]:
    ensure_holiday_table(project_root)
    try:
        with sqlite3.connect(holiday_db_path(project_root), timeout=10) as conn:
            rows = conn.execute(
                "SELECT year, days_json, updated_at FROM market_holiday_cache WHERE market = ?",
                (market,),
            ).fetchall()
    except sqlite3.Error as exc:
        raise CacheIOError(f"holiday cache read failed: {market}") from exc

    loaded: list[tuple[int, set[str], datetime.datetime | None]] = []
    for year_raw, days_json, updated_at in rows:
        try:
            year = int(year_raw)
        except (TypeError, ValueError):
            continue

        payload: Any = []
        if isinstance(days_json, str) and days_json.strip():
            try:
                payload = json.loads(days_json)
            except json.JSONDecodeError:
                payload = []

        loaded.append((year, apply_market_holiday_supplements(market, year, payload), parse_sqlite_ts(updated_at)))
    return loaded


def save_holidays_to_store(project_root: str, market: str, year: int, days: set[str]) -> None:
    ensure_holiday_table(project_root)
    normalized_days = apply_market_holiday_supplements(market, year, days)
    payload = json.dumps(sorted(normalized_days), ensure_ascii=False)
    try:
        with sqlite3.connect(holiday_db_path(project_root), timeout=10) as conn:
            conn.execute(
                """
                INSERT INTO market_holiday_cache (market, year, days_json, updated_at)
                VALUES (?, ?, ?, datetime('now'))
                ON CONFLICT(market, year)
                DO UPDATE SET days_json = excluded.days_json, updated_at = datetime('now')
                """,
                (market, int(year), payload),
            )
            conn.commit()
    except sqlite3.Error as exc:
        raise CacheIOError(f"holiday cache write failed: {market} {year}") from exc


def is_twse_holiday_row(
    name: str, desc: str, include_keywords: tuple[str, ...], exclude_keywords: tuple[str, ...]
) -> bool:
    text = f"{name} {desc}".strip()
    if not text:
        return False
    if any(keyword in text for keyword in exclude_keywords):
        return False
    return any(keyword in text for keyword in include_keywords)


def fetch_twse_holidays(year: int, include_keywords: tuple[str, ...], exclude_keywords: tuple[str, ...]) -> set[str]:
    minguo_year = year - 1911
    if minguo_year <= 0:
        raise BusinessRuleError(f"invalid TW target year: {year}")

    try:
        import requests
    except ImportError as exc:
        raise NetworkServiceError(f"twse dependency unavailable: {year}") from exc

    try:
        url = f"https://www.twse.com.tw/holidaySchedule/holidaySchedule?response=json&queryYear={minguo_year}"
        response = requests.get(url, timeout=20)
    except requests.RequestException as exc:
        raise NetworkServiceError(f"twse request failed: {year}") from exc

    if response.status_code >= 400:
        raise NetworkServiceError(f"twse http {response.status_code}: {year}")

    try:
        payload = response.json()
    except ValueError as exc:
        raise DataFormatError(f"twse payload invalid: {year}") from exc

    if not isinstance(payload, dict):
        raise DataFormatError(f"twse payload invalid: {year}")

    stat = str(payload.get("stat", "")).strip().lower()
    if stat not in {"ok", "success"}:
        raise DataFormatError(f"twse stat invalid: {year}, stat={stat or 'N/A'}")

    title = str(payload.get("title", "")).strip()
    query_year = payload.get("queryYear")
    expected_marker = str(minguo_year)
    actual_year: int | None = None
    if isinstance(query_year, int):
        actual_year = query_year
    elif isinstance(query_year, str) and query_year.strip().isdigit():
        actual_year = int(query_year.strip())
    else:
        match = re.search(r"(\d{2,4})\s*年", title)
        if match:
            maybe = int(match.group(1))
            actual_year = maybe + 1911 if maybe < 1911 else maybe

    if actual_year is not None and actual_year != year:
        raise BusinessRuleError(f"twse holiday for target year not published yet: request={year}, title={title}")
    if title and expected_marker not in title and str(year) not in title:
        raise BusinessRuleError(f"twse holiday for target year not published yet: request={year}, title={title}")

    rows = payload.get("data")
    if not isinstance(rows, list):
        raise DataFormatError(f"twse payload invalid rows: {year}")

    holidays: set[str] = set()
    for row in rows:
        if not isinstance(row, (list, tuple)) or len(row) < 2:
            continue
        date_text = str(row[0]).strip()
        name = str(row[1]).strip()
        desc = str(row[2]).strip() if len(row) >= 3 else ""
        if not is_twse_holiday_row(name, desc, include_keywords, exclude_keywords):
            continue
        if is_iso_date(date_text):
            holidays.add(date_text)
    return holidays


def fetch_public_holidays(
    market: str,
    year: int,
    nager_country: dict[str, str],
    twse_include_keywords: tuple[str, ...],
    twse_exclude_keywords: tuple[str, ...],
) -> set[str]:
    year = int(year)
    if market == "TW":
        holidays = fetch_twse_holidays(year, twse_include_keywords, twse_exclude_keywords)
        return apply_market_holiday_supplements(market, year, holidays)

    country_code = nager_country.get(market)
    if not country_code:
        raise BusinessRuleError(f"unsupported holiday market: {market}")

    try:
        import requests
    except ImportError as exc:
        raise NetworkServiceError(f"holiday api dependency unavailable: {country_code} {year}") from exc

    try:
        url = f"https://date.nager.at/api/v3/PublicHolidays/{year}/{country_code}"
        response = requests.get(url, timeout=15)
    except requests.RequestException as exc:
        raise NetworkServiceError(f"holiday api request failed: {country_code} {year}") from exc

    if response.status_code == 204:
        raise BusinessRuleError(f"holiday api has no data for target year: {country_code} {year}")
    if response.status_code == 404:
        raise BusinessRuleError(f"holiday api country/year unsupported: {country_code} {year}")
    if response.status_code >= 400:
        raise NetworkServiceError(f"holiday api http {response.status_code}: {country_code} {year}")

    try:
        payload = response.json()
    except ValueError as exc:
        raise DataFormatError(f"holiday api payload invalid: {country_code} {year}") from exc

    if not isinstance(payload, list):
        raise DataFormatError(f"holiday api payload invalid: {country_code} {year}")

    holidays: set[str] = set()
    for item in payload:
        if not isinstance(item, dict):
            continue
        date_text = str(item.get("date", "")).strip()[:10]
        if is_iso_date(date_text):
            holidays.add(date_text)
    return apply_market_holiday_supplements(market, year, holidays)
