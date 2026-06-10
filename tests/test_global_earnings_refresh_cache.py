# -*- coding: utf-8 -*-
import json

from domains.global_earnings_calendar import refresh_cache


def test_refresh_cache_main_prints_success_summary(monkeypatch, capsys):
    class FakeService:
        def refresh_events(self):
            return [{"symbol": "NVDA"}, {"symbol": "TSM"}]

    monkeypatch.setattr(refresh_cache, "GlobalEarningsCalendarService", FakeService)

    assert refresh_cache.main() == 0
    payload = json.loads(capsys.readouterr().out)

    assert payload == {"status": "success", "events": 2}
