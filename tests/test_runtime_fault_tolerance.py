from __future__ import annotations

from types import SimpleNamespace

from app.services.stock_candidates_service import StockCandidatesDataService
from app.services.tab_data_lineage_service import TabDataLineageService
from domains.runtime import fault_tolerance as fault_tolerance_module
from domains.runtime.fault_tolerance import provider_fault_tolerance
from infra.diagnostics.runtime_health import _quote_snapshot

NOW = 1_800_000_000.0


def _provider_status() -> dict:
    return {
        "request_stats": {
            "recent_triggered_network": True,
            "recent_cache_hit_count": 4,
            "recent_pending_count": 2,
            "recent_status": "network_failed_offline_fallback",
            "recent_source_layers": ["runtime_cache", "vipdoc_stale_fallback"],
        },
        "runtime_stats": {
            "cooldown_until": NOW + 30,
            "last_error": "runtime down",
        },
        "eastmoney_cooldown_until": NOW + 45,
        "eastmoney_last_error": "eastmoney down",
    }


def test_provider_fault_tolerance_normalizes_fault_tokens_and_cooldowns(monkeypatch):
    monkeypatch.setattr(fault_tolerance_module.time, "time", lambda: NOW)

    result = provider_fault_tolerance(_provider_status())

    assert result == {
        "provider_degraded": True,
        "fallback_or_degraded": True,
        "last_network_error": "runtime down",
        "cooldown_seconds_left": 30,
        "eastmoney_cooldown_seconds_left": 45,
        "recent_triggered_network": True,
        "recent_cache_hit_count": 4,
        "recent_pending_count": 2,
        "recent_status": "network_failed_offline_fallback",
        "recent_source_layers": ["runtime_cache", "vipdoc_stale_fallback"],
    }


def test_provider_fault_tolerance_accepts_runtime_health_provider_runtime_shape(monkeypatch):
    monkeypatch.setattr(fault_tolerance_module.time, "time", lambda: NOW)
    status = _provider_status()
    status["provider_runtime"] = status.pop("runtime_stats")

    result = provider_fault_tolerance(status)

    assert result["provider_degraded"] is True
    assert result["fallback_or_degraded"] is True
    assert result["cooldown_seconds_left"] == 30
    assert result["eastmoney_cooldown_seconds_left"] == 45


def test_provider_fault_tolerance_handles_invalid_numeric_values(monkeypatch):
    monkeypatch.setattr(fault_tolerance_module.time, "time", lambda: NOW)

    result = provider_fault_tolerance(
        {
            "request_stats": {
                "recent_cache_hit_count": object(),
                "recent_pending_count": object(),
                "recent_source_layers": [None, "online"],
            },
            "runtime_stats": {"cooldown_until": object()},
            "eastmoney_cooldown_until": object(),
        }
    )

    assert result["cooldown_seconds_left"] == 0
    assert result["eastmoney_cooldown_seconds_left"] == 0
    assert result["recent_cache_hit_count"] == 0
    assert result["recent_pending_count"] == 0


def test_fault_tolerance_status_is_consistent_across_consumers(monkeypatch):
    monkeypatch.setattr(fault_tolerance_module.time, "time", lambda: NOW)
    status = _provider_status()
    expected = provider_fault_tolerance(status)

    tab_lineage = (
        TabDataLineageService(
            key="scan",
            source="unit-cache",
            provider="unit-provider",
            cache_refs=("cache-a",),
            provider_status_reader=lambda: status,
            clock=lambda: "2026-05-16T10:00:00",
        )
        .describe([])
        .lineage.as_dict()
    )

    stock_lineage = (
        StockCandidatesDataService(
            context_reader=lambda: {},
            row_builder=lambda _context: [],
            provider_status_reader=lambda: status,
            clock=lambda: "2026-05-16T10:00:00",
        )
        .load()
        .lineage.as_dict()
    )

    runtime_provider = SimpleNamespace(
        get_quote_request_stats=lambda: status["request_stats"],
        get_realtime_runtime_stats=lambda: status["runtime_stats"],
        _rt_eastmoney_cooldown_until=status["eastmoney_cooldown_until"],
        _rt_eastmoney_last_error=status["eastmoney_last_error"],
    )
    runtime_quotes = _quote_snapshot(SimpleNamespace(data_provider=runtime_provider, central_quotes_svc=None))

    assert tab_lineage["provider_fault_tolerance"] == expected
    assert stock_lineage["provider_fault_tolerance"] == expected
    assert runtime_quotes["fault_tolerance"] == expected
    assert tab_lineage["fallback_or_degraded"] is True
    assert stock_lineage["fallback_or_degraded"] is True
    assert runtime_quotes["fallback_or_degraded"] is True
