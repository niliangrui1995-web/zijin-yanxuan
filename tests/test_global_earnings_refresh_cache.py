# -*- coding: utf-8 -*-
import json

from domains.global_earnings_calendar import refresh_cache


def test_refresh_cache_main_prints_success_summary(monkeypatch, capsys):
    class FakeService:
        def refresh_events(self):
            return [{"symbol": "NVDA"}, {"symbol": "TSM"}]

        def load_cache_status(self):
            return {}

    monkeypatch.setattr(refresh_cache, "GlobalEarningsCalendarService", FakeService)

    assert refresh_cache.main() == 0
    payload = json.loads(capsys.readouterr().out)

    assert payload == {"status": "success", "events": 2}


def test_refresh_cache_main_prints_degraded_summary(monkeypatch, capsys):
    class FakeService:
        def refresh_events(self):
            return [{"symbol": "NVDA"}, {"symbol": "TSM"}]

        def load_cache_status(self):
            return {
                "status": "degraded",
                "providers": ["MOPS"],
                "failed_tickers": ["3711.TW"],
                "reused_event_count": 1,
            }

    monkeypatch.setattr(refresh_cache, "GlobalEarningsCalendarService", FakeService)

    assert refresh_cache.main() == 0
    payload = json.loads(capsys.readouterr().out)

    assert payload == {
        "status": "degraded",
        "events": 2,
        "providers": ["MOPS"],
        "failed_tickers": ["3711.TW"],
        "reused_event_count": 1,
    }


def test_refresh_cache_main_marks_exception_as_retryable_degraded(monkeypatch, capsys):
    marked = []

    class FakeService:
        def refresh_events(self):
            raise RuntimeError("sqlite busy\nretry later")

        def mark_refresh_failed(self, exc):
            marked.append(str(exc))
            return {
                "status": "degraded",
                "reason": "refresh_exception",
                "error": "sqlite busy",
                "retryable": True,
                "reused_event_count": 2,
            }

        def load_events(self, **kwargs):
            assert kwargs == {"allow_network": False}
            return [{"symbol": "NVDA"}, {"symbol": "TSM"}]

    monkeypatch.setattr(refresh_cache, "GlobalEarningsCalendarService", FakeService)

    assert refresh_cache.main() == 0
    output_lines = capsys.readouterr().out.splitlines()
    payload = json.loads([line for line in output_lines if line.startswith("{")][-1])

    assert marked == ["sqlite busy\nretry later"]
    assert payload["status"] == "degraded"
    assert payload["events"] == 2
    assert payload["reason"] == "refresh_exception"
    assert payload["error"] == "sqlite busy | retry later"
    assert payload["retryable"] is True
    assert payload["reused_event_count"] == 2
