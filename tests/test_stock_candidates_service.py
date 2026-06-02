from __future__ import annotations

import time
from types import SimpleNamespace

from app.services.stock_candidates_service import KEY_RECENT_TIME, StockCandidatesDataService, _iter_signals


def test_stock_candidates_service_returns_rows_and_lineage():
    signal = SimpleNamespace(
        code="300750",
        source_tab="na_daily",
        signal_type="catalyst",
        summary="order catalyst",
        observed_at="2026-05-08",
        refreshed_at="",
        payload={"trade_date": "2026-05-08"},
    )
    context = {"300750": [signal]}
    service = StockCandidatesDataService(
        context_reader=lambda: context,
        row_builder=lambda value: [{"code": "300750", "_signals": value["300750"]}],
        provider_status_reader=lambda: {
            "request_stats": {
                "recent_triggered_network": False,
                "recent_cache_hit_count": 2,
                "recent_status": "runtime_cache_hit",
                "recent_source_layers": ["runtime_cache"],
            },
            "runtime_stats": {
                "cooldown_until": 0.0,
                "last_error": "",
            },
        },
        clock=lambda: "2026-05-09T10:00:00",
    )

    result = service.load()
    lineage = result.lineage.as_dict()

    assert result.rows[0]["code"] == "300750"
    assert result.signature
    assert lineage["key"] == "stock_candidates"
    assert lineage["provider"] == "workspace_stock_context"
    assert lineage["trade_date"] == "2026-05-08"
    assert lineage["triggered_network"] is False
    assert lineage["fallback_or_degraded"] is False
    assert lineage["updated_at"] == "2026-05-09T10:00:00"
    assert lineage["signal_count"] == 1
    assert lineage["source_tabs"] == ["na_daily"]
    assert lineage["provider_fault_tolerance"]["recent_cache_hit_count"] == 2


def test_stock_candidates_service_marks_provider_fallback_or_degraded():
    signal = SimpleNamespace(
        code="300750",
        source_tab="scan",
        signal_type="vcp_scan",
        summary="scan",
        observed_at="20260508",
        refreshed_at="",
        payload={},
    )
    service = StockCandidatesDataService(
        context_reader=lambda: {"300750": [signal]},
        row_builder=lambda value: [{"code": "300750", "_signals": value["300750"]}],
        provider_status_reader=lambda: {
            "request_stats": {
                "recent_triggered_network": True,
                "recent_status": "network_failed_offline_fallback",
                "recent_source_layers": ["offline_missing_fallback"],
            },
            "runtime_stats": {
                "cooldown_until": time.time() + 60,
                "last_error": "network down",
            },
        },
        clock=lambda: "2026-05-09T10:00:00",
    )

    lineage = service.load().lineage.as_dict()

    assert lineage["fallback_or_degraded"] is True
    assert lineage["triggered_network"] is True
    assert lineage["provider_fault_tolerance"]["provider_degraded"] is True
    assert lineage["provider_fault_tolerance"]["recent_triggered_network"] is True
    assert lineage["provider_fault_tolerance"]["last_network_error"] == "network down"


def test_stock_candidates_service_parses_mapping_signals_and_empty_rows_warning():
    signal = {
        "code": "300750",
        "source_tab": "watchlist",
        "signal_type": "manual",
        "summary": "watch",
        "payload": {KEY_RECENT_TIME: "2026-05-10"},
    }
    context = {"300750": [signal], "noise": "not-a-signal-list"}
    service = StockCandidatesDataService(
        context_reader=lambda: context,
        row_builder=lambda value: [],
        clock=lambda: "2026-05-10T10:00:00",
    )

    result = service.load()
    payload = result.as_dict()

    assert payload["rows"] == []
    assert payload["trade_date"] == "2026-05-10"
    assert payload["source_tabs"] == ["watchlist"]
    assert payload["warnings"] == ["no_rows_after_candidate_filter"]
    assert _iter_signals(None) == []


def test_stock_candidates_service_records_reader_builder_and_provider_errors():
    def _raise_runtime():
        raise RuntimeError("boom")

    service = StockCandidatesDataService(
        context_reader=_raise_runtime,
        row_builder=lambda context: (_ for _ in ()).throw(TypeError("bad rows")),
        provider_status_reader=lambda: (_ for _ in ()).throw(ValueError("bad provider")),
        clock=lambda: "2026-05-10T10:00:00",
    )

    lineage = service.load().lineage.as_dict()

    assert lineage["fallback_or_degraded"] is True
    assert lineage["errors"] == [
        "context_reader_failed:RuntimeError",
        "row_builder_failed:TypeError",
        "provider_status_failed:ValueError",
    ]


def test_stock_candidates_service_handles_non_mapping_context_and_empty_lineage():
    service = StockCandidatesDataService(
        context_reader=lambda: ["bad"],
        row_builder=lambda context: [],
        clock=lambda: "2026-05-10T10:00:00",
    )

    result = service.load()
    empty = service.empty_lineage(row_count=-5).as_dict()

    assert result.lineage.as_dict()["warnings"] == ["context_reader_returned_non_mapping"]
    assert result.lineage.as_dict()["provider_fault_tolerance"]["recent_cache_hit_count"] == 0
    assert empty["row_count"] == 0
