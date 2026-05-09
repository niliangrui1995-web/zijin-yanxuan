from __future__ import annotations

import time

from app.services.tab_data_lineage_service import TabDataLineageService


def test_tab_data_lineage_service_returns_rows_signature_and_lineage():
    service = TabDataLineageService(
        key="scan",
        source="unit-cache",
        provider="unit-provider",
        cache_refs=("cache-a", "cache-b"),
        provider_status_reader=lambda: {
            "request_stats": {
                "recent_triggered_network": False,
                "recent_cache_hit_count": 3,
                "recent_status": "local_cache_hit",
                "recent_source_layers": ["local_cache"],
            },
            "runtime_stats": {"cooldown_until": 0.0, "last_error": ""},
        },
        clock=lambda: "2026-05-10T10:00:00",
    )

    result = service.describe([{"code": "300750"}], trade_date="2026-05-09", triggered_network=False)
    lineage = result.lineage.as_dict()

    assert result.rows == [{"code": "300750"}]
    assert result.signature
    assert lineage["key"] == "scan"
    assert lineage["provider"] == "unit-provider"
    assert lineage["cache_refs"] == ["cache-a", "cache-b"]
    assert lineage["trade_date"] == "2026-05-09"
    assert lineage["updated_at"] == "2026-05-10T10:00:00"
    assert lineage["triggered_network"] is False
    assert lineage["fallback_or_degraded"] is False
    assert lineage["provider_fault_tolerance"]["recent_cache_hit_count"] == 3


def test_tab_data_lineage_service_marks_provider_degraded():
    service = TabDataLineageService(
        key="watchlist",
        source="unit-cache",
        provider="unit-provider",
        cache_refs=("cache-a",),
        provider_status_reader=lambda: {
            "request_stats": {
                "recent_triggered_network": True,
                "recent_status": "network_failed_offline_fallback",
                "recent_source_layers": ["offline_fallback"],
            },
            "runtime_stats": {"cooldown_until": time.time() + 60, "last_error": "network down"},
        },
    )

    lineage = service.describe([], warnings=("empty",)).lineage.as_dict()

    assert lineage["fallback_or_degraded"] is True
    assert lineage["provider_fault_tolerance"]["provider_degraded"] is True
    assert lineage["provider_fault_tolerance"]["last_network_error"] == "network down"
    assert lineage["warnings"] == ["empty"]
