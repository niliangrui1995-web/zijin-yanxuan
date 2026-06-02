# -*- coding: utf-8 -*-
from core.quote_snapshot import (
    build_finance_quote_payload,
    coerce_number,
    enrich_quotes_with_finance,
    get_missing_a_share_finance_codes,
    merge_quote_snapshot_inplace,
    resolve_quote_metrics,
)


def test_merge_quote_snapshot_inplace_preserves_finance_fields():
    snapshot = {
        "000001": {
            "close": 10.0,
            "last_close": 9.8,
            "zongguben": 1_000_000_000,
        }
    }

    merge_quote_snapshot_inplace(
        snapshot,
        {"000001": {"close": 10.5, "last_close": 10.0}},
    )

    assert snapshot["000001"]["close"] == 10.5
    assert snapshot["000001"]["last_close"] == 10.0
    assert snapshot["000001"]["zongguben"] == 1_000_000_000


def test_get_missing_a_share_finance_codes_only_returns_missing_a_share_codes():
    snapshot = {
        "000001": {"zongguben": 1_000_000_000},
        "600000": {"close": 11.2},
        "AAPL": {"close": 188.0},
    }

    missing = get_missing_a_share_finance_codes(
        ["000001", "600000", "AAPL", "BABA", "600000"],
        snapshot,
    )

    assert missing == ["600000"]


def test_enrich_quotes_with_finance_builds_dynamic_market_cap():
    enriched = enrich_quotes_with_finance(
        {"000001": {"close": 12.5, "last_close": 12.0}},
        {"000001": {"zongguben": 1_000_000_000, "source": "eastmoney"}},
    )

    assert enriched["000001"]["zongguben"] == 1_000_000_000
    assert enriched["000001"]["_zongguben"] == 1_000_000_000
    assert enriched["000001"]["market_cap"] == 12_500_000_000
    assert enriched["000001"]["finance_source"] == "eastmoney"


def test_build_finance_quote_payload_creates_mergeable_payload():
    payload = build_finance_quote_payload({"000001": {"zongguben": 2_000_000_000, "market_cap": 20_000_000_000}})

    assert payload["000001"]["zongguben"] == 2_000_000_000
    assert payload["000001"]["market_cap"] == 20_000_000_000


def test_resolve_quote_metrics_prefers_live_price_times_zongguben():
    metrics = resolve_quote_metrics(
        {"_zongguben": 1_500_000_000},
        {"close": 8.0, "last_close": 7.5},
    )

    assert metrics["price_text"] == "8.00"
    assert round(metrics["pct"], 4) == round((8.0 / 7.5 - 1.0) * 100.0, 4)
    assert metrics["market_cap_text"] == "120亿"


def test_quote_snapshot_handles_empty_none_and_invalid_values():
    assert coerce_number(None) == 0.0
    assert coerce_number(object()) == 0.0

    snapshot = {"000001": {"close": 10.0, "market_cap": 100}}
    merge_quote_snapshot_inplace(snapshot, {"000001": {"close": None, "last_close": 9.5}})
    assert snapshot["000001"]["close"] == 10.0
    assert snapshot["000001"]["last_close"] == 9.5

    enriched = enrich_quotes_with_finance(
        {"000001": {"close": 12.0}},
        {
            "": {"zongguben": 1_000},
            "000001": {"market_cap": 20_000_000_000, "price_base": 10.0, "source": ""},
            "000002": None,
        },
    )

    assert "" not in enriched
    assert enriched["000001"]["market_cap"] == 24_000_000_000
    assert enriched["000001"]["price_base"] == 10.0
    assert enriched["000002"] == {}


def test_resolve_quote_metrics_falls_back_to_last_close_pct_and_static_market_cap():
    metrics = resolve_quote_metrics(
        {},
        {"close": 0, "last_close": 7.5, "pct": "bad", "market_cap": 15_000_000_000},
    )

    assert metrics["rt_close"] == 7.5
    assert metrics["pct"] == 0.0
    assert metrics["market_cap_value"] == 15_000_000_000

    metrics = resolve_quote_metrics({}, {"close": 0, "last_close": 0, "pct": "3.5"})
    assert metrics["pct"] == 3.5

    metrics = resolve_quote_metrics({}, {"close": 0, "last_close": 0, "pct": object()})
    assert metrics["pct"] is None
