# -*- coding: utf-8 -*-
from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace

from infra.diagnostics.runtime_health import (
    DATA_LINEAGE_COVERED_TABS,
    DATA_LINEAGE_EXCLUDED_TABS,
    build_runtime_health_trend,
    collect_runtime_health,
    export_runtime_health_report,
)
from scripts.runtime_health_stability_suite import DEFAULT_TABS


class _FakeModel:
    @staticmethod
    def rowCount():
        return 2


def _fake_main_window():
    tab = SimpleNamespace(
        model=_FakeModel(),
        lbl_status=SimpleNamespace(text=lambda: "本地快照已就绪"),
        get_data_lineage=lambda: {
            "source": "unit-test-cache",
            "trade_date": "2026-05-08",
            "triggered_network": False,
            "fallback_or_degraded": False,
            "last_updated": "2026-05-08T15:00:00",
        },
    )
    workspace = SimpleNamespace(
        tab_specs=lambda: [{"key": "stock_candidates", "title": "综合候选", "group": "主工作台"}],
        get_loaded_tab=lambda key: tab if key == "stock_candidates" else None,
    )
    provider = SimpleNamespace(
        get_quote_request_stats=lambda: {
            "recent_batch_count": 1,
            "recent_codes_count": 2,
            "recent_duplicate_requested_codes": {"000001": 2},
        },
        get_realtime_runtime_stats=lambda: {
            "cooldown_until": 0.0,
            "last_error": "",
        },
        get_market_data_source_status=lambda: {
            "ok": True,
            "active_layer": "parquet_sqlite_warehouse",
            "data_status": "ok",
            "memory_symbol_count": 2,
            "memory_row_count": 4,
            "warehouse": {
                "trade_date": "2026-05-08",
                "symbol_count": 2,
                "row_count": 4,
            },
            "fallback_or_degraded": False,
            "fallback_reason": "",
        },
        _rt_eastmoney_cooldown_until=0.0,
        _rt_eastmoney_last_error="",
    )
    return SimpleNamespace(
        _workspace=workspace,
        data_provider=provider,
        central_quotes_svc=SimpleNamespace(
            _is_fetching=False,
            _fetch_generation=3,
            _circuit_breaker_cooldown=0,
            _post_cache_reload_quiet_until=0.0,
            _post_cache_reload_signature=("000001", "000002"),
        ),
        findChildren=lambda _type: [],
    )


def test_collect_runtime_health_includes_core_sections(qt_application):
    report = collect_runtime_health(_fake_main_window())

    assert report["report_type"] == "runtime_health"
    assert report["background_tasks"]["ids"] == []
    assert report["timers"]["total"] == 0
    assert "total_receivers" in report["event_bus"]
    assert "thread_count" in report["process"]
    assert "count" in report["webengine"]
    assert report["quotes"]["request_stats"]["recent_batch_count"] == 1
    assert report["quotes"]["provider_degraded"] is False
    assert report["market_data"]["active_layer"] == "parquet_sqlite_warehouse"
    assert report["market_data"]["warehouse"]["row_count"] == 4
    assert "cache_version" in report["f5_cache"]
    assert report["data_lineage"][0]["key"] == "stock_candidates"
    assert report["data_lineage"][0]["source"] == "unit-test-cache"
    asian_lineage = next(entry for entry in report["data_lineage"] if entry["key"] == "asian_market")
    assert "data/Cache/asian_rt_latest.json" in asian_lineage["cache_refs"]
    assert "data/Cache/asian_realtime_latest.json" not in asian_lineage["cache_refs"]
    lhb_lineage = next(entry for entry in report["data_lineage"] if entry["key"] == "lhb")
    assert lhb_lineage["source"] == "LhbPoolManager cache + local_quote_snapshot"
    assert "data/Cache/lhb_pool_30d.json" in lhb_lineage["cache_refs"]
    assert "data/Cache/lhb_pool_20d.json" not in lhb_lineage["cache_refs"]
    assert "local_tdx_cache" in lhb_lineage["cache_refs"]
    lineage_keys = {entry["key"] for entry in report["data_lineage"]}
    assert {"asian_market", "na_daily", "ai_industry_chain"} <= lineage_keys
    assert "system_log" not in lineage_keys
    assert report["data_lineage_coverage"]["covered"] == list(DATA_LINEAGE_COVERED_TABS)
    assert report["data_lineage_coverage"]["excluded"] == list(DATA_LINEAGE_EXCLUDED_TABS)
    assert report["data_lineage_exclusions"][0]["key"] == "system_log"
    assert report["data_lineage_exclusions"][0]["reason"] == "non_data_tab"


def test_runtime_health_short_default_tabs_match_static_lineage_coverage():
    covered = set(DATA_LINEAGE_COVERED_TABS)
    excluded = set(DATA_LINEAGE_EXCLUDED_TABS)
    short_default_tabs = set(DEFAULT_TABS)

    assert {"asian_market", "na_daily", "ai_industry_chain"} <= short_default_tabs
    assert {"asian_market", "na_daily", "ai_industry_chain"} <= covered
    assert short_default_tabs - covered - excluded == set()
    assert "system_log" not in covered
    assert DATA_LINEAGE_EXCLUDED_TABS["system_log"]["reason"] == "non_data_tab"


def test_export_runtime_health_report_writes_dated_json(tmp_path, qt_application):
    report = collect_runtime_health(_fake_main_window())
    output = export_runtime_health_report(
        _fake_main_window(),
        project_root=tmp_path,
        report=report,
        now=datetime(2026, 5, 9, 10, 11, 12, 345678),
    )

    assert output.name == "runtime_health_101112_345678.json"
    assert output.parent.name == "runtime_health_20260509"
    assert '"report_type": "runtime_health"' in output.read_text(encoding="utf-8")


def test_build_runtime_health_trend_tracks_growth():
    samples = [
        {
            "background_tasks": {"count": 0},
            "timers": {"active": 3, "total": 5},
            "event_bus": {"total_receivers": 10},
            "process": {"thread_count": 20},
            "webengine": {"count": 0, "rss_mb": 0.0, "private_mb": 0.0},
        },
        {
            "background_tasks": {"count": 1},
            "timers": {"active": 4, "total": 7},
            "event_bus": {"total_receivers": 10},
            "process": {"thread_count": 22},
            "webengine": {"count": 1, "rss_mb": 42.5, "private_mb": 33.25},
        },
    ]

    trend = build_runtime_health_trend(samples)

    assert trend["background_tasks"]["net_delta"] == 1
    assert trend["active_timers"]["net_delta"] == 1
    assert trend["total_timers"]["net_delta"] == 2
    assert trend["event_receivers"]["net_delta"] == 0
    assert trend["threads"]["net_delta"] == 2
    assert trend["webengine_children"]["last"] == 1
    assert trend["webengine_rss_mb"]["last"] == 42.5
    assert trend["webengine_private_mb"]["net_delta"] == 33.25
