# -*- coding: utf-8 -*-
"""Compatibility checks for the retired raw-QThread earnings scheduler."""

from __future__ import annotations

import app.services.ui_earnings_service as ui_earnings_service
import domains.earnings.scheduler as legacy_scheduler
import domains.market_calendar as market_calendar_module
from app.services.ui_earnings_service import EarningsRefreshService
from domains.market_calendar import MarketCalendar
from earnings.scheduler import EarningsScheduler


class _QueuedRunner:
    def __init__(self):
        self.jobs = []

    @staticmethod
    def is_active_task(_task_id):
        return False

    def run_in_background(self, fn, **kwargs):
        self.jobs.append((fn, dict(kwargs)))
        return str(kwargs.get("task_id") or "")

    @staticmethod
    def cancel_task(*_args, **_kwargs):
        return True

    @staticmethod
    def wait_for_tasks(*_args, **_kwargs):
        return True


def test_legacy_scheduler_is_a_deprecated_alias_to_owner_service():
    assert legacy_scheduler.__deprecated__ is True
    assert EarningsScheduler is EarningsRefreshService


def test_legacy_start_patrol_uses_owner_lifecycle_and_deadline(qt_application):
    runner = _QueuedRunner()
    scheduler = EarningsScheduler(engine=object(), job_runner=runner)

    assert scheduler.start_patrol() is True

    _run, kwargs = runner.jobs[-1]
    assert kwargs["timeout_sec"] == scheduler.STARTUP_GAP_TIMEOUT_SECONDS
    assert scheduler.shutdown(timeout_ms=25) is True


def test_build_startup_scan_dates_returns_recent_window_when_cache_is_empty(monkeypatch):
    class FakeMarketCalendar:
        get_recent_trade_dates = classmethod(
            lambda cls, n=20, ref_date=None: ["20260416", "20260415", "20260414"]
        )

    monkeypatch.setattr(market_calendar_module, "MarketCalendar", FakeMarketCalendar)
    monkeypatch.setattr(
        ui_earnings_service,
        "_normalize_trade_dates",
        lambda _values: ["2026-04-14", "2026-04-15", "2026-04-16"],
    )

    dates = EarningsScheduler._build_startup_scan_dates("2026-04-16", has_cached_records=False)

    assert dates == ["2026-04-14", "2026-04-15", "2026-04-16"]


def test_build_startup_scan_dates_only_returns_missing_trade_days(monkeypatch):
    monkeypatch.setattr(
        MarketCalendar,
        "get_recent_trade_dates",
        classmethod(lambda cls, n=20, ref_date=None: ["20260416", "20260415", "20260414", "20260411"]),
    )

    dates = EarningsScheduler._build_startup_scan_dates("2026-04-14", has_cached_records=True)

    assert dates == ["2026-04-15", "2026-04-16"]


def test_build_startup_scan_dates_returns_recent_window_when_last_sync_is_missing(monkeypatch):
    monkeypatch.setattr(
        MarketCalendar,
        "get_recent_trade_dates",
        classmethod(lambda cls, n=20, ref_date=None: ["20260416", "20260415"]),
    )

    dates = EarningsScheduler._build_startup_scan_dates("", has_cached_records=True)

    assert dates == ["2026-04-15", "2026-04-16"]


def test_build_startup_scan_dates_returns_empty_when_window_is_already_covered(monkeypatch):
    monkeypatch.setattr(
        MarketCalendar,
        "get_recent_trade_dates",
        classmethod(lambda cls, n=20, ref_date=None: ["20260416", "20260415", "20260414"]),
    )

    dates = EarningsScheduler._build_startup_scan_dates("2026-04-16", has_cached_records=True)

    assert dates == []


def test_normalize_trade_dates_deduplicates_and_ignores_invalid_values():
    assert EarningsScheduler._normalize_trade_dates(
        [None, "", "20260401", "2026-04-02T15:00", "20260401", "bad"]
    ) == ["2026-04-01", "2026-04-02"]
    assert EarningsScheduler._normalize_trade_dates(None) == []
