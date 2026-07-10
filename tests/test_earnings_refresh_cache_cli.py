# -*- coding: utf-8 -*-
from __future__ import annotations

import json

from domains.earnings import refresh_cache


class _FakeEngine:
    def __init__(self):
        self.local_records = [{"股票代码": "000001"}]
        self.last_sync_date = "2026-04-14"
        self.last_scan_result = {}
        self.fetch_calls = []

    @staticmethod
    def get_cached_record_rows():
        return [{"股票代码": "000001"}, {"股票代码": "000002"}]

    def fetch_daily_surprises(self, target_publish_date=None):
        self.fetch_calls.append(target_publish_date)
        self.last_scan_result = {"status": "success"}
        return [{"股票代码": "000003"}]


def test_startup_gap_fill_summary_uses_recent_missing_trade_dates(monkeypatch):
    engine = _FakeEngine()
    monkeypatch.setattr(
        refresh_cache.MarketCalendar,
        "get_recent_trade_dates",
        staticmethod(lambda _days: ["20260414", "20260415", "20260416"]),
    )

    summary = refresh_cache.run_startup_gap_fill(engine)

    assert summary == {
        "status": "success",
        "job_key": "earnings_startup_gap_fill",
        "records": 4,
        "cached": 2,
        "gap": 2,
        "missing_dates": ["2026-04-15", "2026-04-16"],
    }
    assert engine.fetch_calls == ["2026-04-15", "2026-04-16"]


def test_routine_summary_preserves_degraded_state():
    engine = _FakeEngine()

    def _degraded_fetch(target_publish_date=None):
        engine.last_scan_result = {"status": "degraded", "error": "provider timeout"}
        return []

    engine.fetch_daily_surprises = _degraded_fetch

    summary = refresh_cache.run_routine(engine, "2026-07-10T08:30:00+08:00")

    assert summary == {
        "status": "degraded",
        "job_key": "earnings_routine",
        "records": 0,
        "routine_time": "2026-07-10T08:30:00+08:00",
        "error": "provider timeout",
    }


def test_cli_prints_stable_json_as_last_line(monkeypatch, capsys):
    expected = {
        "status": "success",
        "job_key": "earnings_startup_gap_fill",
        "records": 0,
        "cached": 0,
        "gap": 0,
        "missing_dates": [],
    }
    monkeypatch.setattr(refresh_cache, "EarningsEngine", lambda: object())
    monkeypatch.setattr(refresh_cache, "run_startup_gap_fill", lambda _engine: expected)

    return_code = refresh_cache.main(["startup-gap-fill"])
    output = capsys.readouterr().out.strip().splitlines()

    assert return_code == 0
    assert json.loads(output[-1]) == expected


def test_cli_exception_prints_failed_json_and_returns_nonzero(monkeypatch, capsys):
    def _raise():
        raise RuntimeError("provider failed\nretry later")

    monkeypatch.setattr(refresh_cache, "EarningsEngine", _raise)

    return_code = refresh_cache.main(["routine", "--routine-time", "08:30"])
    output = capsys.readouterr().out.strip().splitlines()
    payload = json.loads(output[-1])

    assert return_code == 1
    assert payload == {
        "status": "failed",
        "job_key": "earnings_routine",
        "records": 0,
        "routine_time": "08:30",
        "error": "provider failed | retry later",
    }
