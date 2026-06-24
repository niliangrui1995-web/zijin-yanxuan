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
