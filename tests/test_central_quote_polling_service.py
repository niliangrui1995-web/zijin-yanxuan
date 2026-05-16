# -*- coding: utf-8 -*-
from __future__ import annotations

from app.services.central_quote_polling_service import CentralQuotePoller


def test_central_quote_poller_builds_enriched_payload_with_finance_gap():
    class DummyProvider:
        def fetch_realtime_quotes_batch(self, codes):
            return {codes[0]: {"close": 10.5, "last_close": 10.0, "source": "eastmoney"}}

        def get_realtime_runtime_stats(self):
            return {"consecutive_failures": 0}

    poller = CentralQuotePoller(
        DummyProvider(),
        missing_finance_codes=lambda codes: sorted(codes),
        finance_lookup=lambda codes: {"000001": {"zongguben": 1_000_000_000, "source": "eastmoney"}},
    )

    payload = poller.fetch_payload({"000001"})

    assert payload["finance_data"]["000001"]["zongguben"] == 1_000_000_000
    assert payload["provider_stats"] == {"consecutive_failures": 0}
    assert payload["quotes"]["000001"]["market_cap"] == 10_500_000_000


def test_central_quote_poller_runtime_guards_delegate_to_provider():
    calls = []

    class DummyProvider:
        def compact_runtime_caches(self):
            calls.append("compact")
            return {"rt_quote_cache_size": 2}

        def protect_against_thread_anomaly(self, count):
            calls.append(("protect", count))
            return True

        def enter_realtime_cooldown(self, reason, *, cooldown_sec):
            calls.append(("cooldown", reason, cooldown_sec))

        def is_online(self):
            return True

    poller = CentralQuotePoller(DummyProvider())

    assert poller.compact_runtime_caches() == {"rt_quote_cache_size": 2}
    assert poller.protect_against_thread_anomaly(3) is True
    poller.enter_realtime_cooldown("bad source", cooldown_sec=300)
    assert poller.is_online() is True
    assert calls == [
        "compact",
        ("protect", 3),
        ("cooldown", "bad source", 300),
    ]
