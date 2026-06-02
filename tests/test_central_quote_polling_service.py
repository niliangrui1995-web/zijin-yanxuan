# -*- coding: utf-8 -*-
from __future__ import annotations

from app.services import central_quote_polling_service as polling_service
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


def test_central_quote_poller_prefers_local_tdx_capital(monkeypatch):
    from app.services import central_quote_polling_service as polling_service

    class DummyProvider:
        tdx_vipdoc = "D:/HT/vipdoc"

        def fetch_realtime_quotes_batch(self, codes):
            return {codes[0]: {"close": 10.5, "last_close": 10.0, "source": "local_cache"}}

        def get_realtime_runtime_stats(self):
            return {"consecutive_failures": 0}

    monkeypatch.setattr(
        polling_service,
        "load_local_tdx_capital_snapshot",
        lambda codes, tdx_vipdoc: {"000001": {"zongguben": 2_000_000_000, "source": "tdx_base"}},
    )
    monkeypatch.setattr(
        polling_service,
        "batch_get_finance_info",
        lambda codes: (_ for _ in ()).throw(AssertionError("local TDX capital should be used first")),
    )

    poller = CentralQuotePoller(
        DummyProvider(),
        missing_finance_codes=lambda codes: sorted(codes),
    )

    payload = poller.fetch_payload({"000001"})

    assert payload["finance_data"]["000001"]["source"] == "tdx_base"
    assert payload["quotes"]["000001"]["_zongguben"] == 2_000_000_000
    assert payload["quotes"]["000001"]["market_cap"] == 21_000_000_000


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


def test_central_quote_poller_handles_invalid_share_capital_and_empty_lookup():
    class DummyProvider:
        pass

    assert polling_service._has_valid_share_capital({"zongguben": object()}) is False

    poller = CentralQuotePoller(DummyProvider())

    assert poller._lookup_finance_with_local_tdx(["bad", "abcde"]) == {}
    assert poller.missing_finance_codes({"000001"}) == []


def test_central_quote_poller_falls_back_when_local_tdx_lookup_fails(monkeypatch):
    class DummyProvider:
        tdx_vipdoc = "D:/HT/vipdoc"

    monkeypatch.setattr(
        polling_service,
        "load_local_tdx_capital_snapshot",
        lambda *_args: (_ for _ in ()).throw(RuntimeError("local cache failed")),
    )
    monkeypatch.setattr(
        polling_service,
        "batch_get_finance_info",
        lambda codes: {codes[0]: {"zongguben": 1_000_000, "source": "network"}},
    )

    result = CentralQuotePoller(DummyProvider())._lookup_finance_with_local_tdx(["1"])

    assert result == {"000001": {"zongguben": 1_000_000, "source": "network"}}


def test_central_quote_poller_fetch_payload_survives_callback_failures():
    class DummyProvider:
        def fetch_realtime_quotes_batch(self, codes):
            return {codes[0]: {"close": 10.0}}

    poller = CentralQuotePoller(
        DummyProvider(),
        missing_finance_codes=lambda _codes: (_ for _ in ()).throw(RuntimeError("gap failed")),
        finance_lookup=lambda _codes: (_ for _ in ()).throw(RuntimeError("finance failed")),
        quote_enricher=lambda quotes, finance: {"quotes": quotes, "finance": finance},
    )

    assert poller.missing_finance_codes({"000001"}) == []

    poller._missing_finance_codes = lambda _codes: ["000001"]
    payload = poller.fetch_payload({"000001"})

    assert payload["finance_data"] == {}
    assert payload["quotes"]["finance"] == {}


def test_central_quote_poller_runtime_guard_failures_return_defaults():
    class DummyProvider:
        def get_realtime_runtime_stats(self):
            raise RuntimeError("stats failed")

        def compact_runtime_caches(self):
            raise RuntimeError("compact failed")

        def protect_against_thread_anomaly(self, count):
            raise RuntimeError(f"protect failed {count}")

        def enter_realtime_cooldown(self, reason, *, cooldown_sec):
            raise RuntimeError(f"cooldown failed {reason} {cooldown_sec}")

    poller = CentralQuotePoller(DummyProvider())

    assert poller.get_runtime_stats() == {}
    assert poller.compact_runtime_caches() == {}
    assert poller.protect_against_thread_anomaly(3) is False
    poller.enter_realtime_cooldown("bad source", cooldown_sec=300)
