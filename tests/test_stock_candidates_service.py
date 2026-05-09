from __future__ import annotations

import time
from types import SimpleNamespace

from app.services.stock_candidates_service import StockCandidatesDataService


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
