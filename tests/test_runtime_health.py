# -*- coding: utf-8 -*-
from __future__ import annotations

import builtins
from datetime import datetime
from types import SimpleNamespace

import infra.diagnostics.runtime_health as runtime_health
from infra.diagnostics.runtime_health import (
    DATA_LINEAGE_COVERED_TABS,
    DATA_LINEAGE_EXCLUDED_TABS,
    build_runtime_health_trend,
    collect_runtime_health,
    export_runtime_health_report,
)
from infra.market_data.provider_ports import ProviderHealthSnapshot
from scripts.runtime_health_stability_suite import DEFAULT_TABS
from ui.tabs.base_stock_tab import mark_runtime_network_activity


class _FakeModel:
    @staticmethod
    def rowCount():
        return 2


class _LenRaises:
    def __bool__(self):
        return True

    def __len__(self):
        raise TypeError("length unavailable")


def _raise_runtime(*_args, **_kwargs):
    raise RuntimeError("boom")


def _fake_main_window():
    tab = SimpleNamespace(
        model=_FakeModel(),
        lbl_status=SimpleNamespace(text=lambda: "本地快照已就绪"),
        get_data_lineage=lambda: {
            "key": "tab-owned-key",
            "view": "tab-owned-view",
            "source": "unit-test-cache",
            "provider": "tab-owned-provider",
            "cache_refs": ["tab-owned-cache"],
            "trade_date": "2026-05-08",
            "network_capable": True,
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
        read_provider_health=lambda: ProviderHealthSnapshot(
            request_stats={
                "recent_batch_count": 1,
                "recent_codes_count": 2,
                "recent_duplicate_requested_codes": {"000001": 2},
            },
            runtime_stats={
                "cooldown_until": 0.0,
                "last_error": "",
            },
        ),
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


def test_collect_runtime_health_includes_core_sections(qt_application, monkeypatch):
    from core.background_job_runner import background_job_runner

    monkeypatch.setattr(background_job_runner, "_resolve_manager", lambda: SimpleNamespace(active_workers={}))

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
    candidate_lineage = report["data_lineage"][0]
    assert candidate_lineage["source"] == "workspace_stock_context"
    assert candidate_lineage["provider"] == "workspace_stock_context"
    assert candidate_lineage["cache_refs"] == ["global_store.quotes", "workspace.collect_stock_context"]
    assert candidate_lineage["trade_date"] == "2026-05-08"
    assert candidate_lineage["network_capable"] is False
    assert candidate_lineage["lineage_error"] is True
    assert candidate_lineage["static_override_rejected"] == [
        "cache_refs",
        "key",
        "network_capable",
        "provider",
        "source",
        "view",
    ]
    asian_lineage = next(entry for entry in report["data_lineage"] if entry["key"] == "asian_market")
    assert asian_lineage["network_capable"] is True
    assert asian_lineage["triggered_network"] is False
    assert "data/Cache/asian_rt_latest.json" in asian_lineage["cache_refs"]
    assert "data/Cache/asian_realtime_latest.json" not in asian_lineage["cache_refs"]
    lhb_lineage = next(entry for entry in report["data_lineage"] if entry["key"] == "lhb")
    assert lhb_lineage["network_capable"] is True
    assert lhb_lineage["triggered_network"] is False
    assert lhb_lineage["source"] == "LhbPoolManager cache + local_quote_snapshot"
    assert "data/Cache/lhb_pool_30d.json" in lhb_lineage["cache_refs"]
    assert "data/Cache/lhb_pool_20d.json" not in lhb_lineage["cache_refs"]
    assert "local_tdx_cache" in lhb_lineage["cache_refs"]
    ai_lineage = next(entry for entry in report["data_lineage"] if entry["key"] == "ai_industry_chain")
    assert ai_lineage["network_capable"] is False
    assert ai_lineage["triggered_network"] is False
    lineage_keys = {entry["key"] for entry in report["data_lineage"]}
    assert {"asian_market", "na_daily", "ai_industry_chain"} <= lineage_keys
    assert "system_log" not in lineage_keys
    assert report["data_lineage_coverage"]["covered"] == list(DATA_LINEAGE_COVERED_TABS)
    assert report["data_lineage_coverage"]["excluded"] == list(DATA_LINEAGE_EXCLUDED_TABS)
    assert report["data_lineage_exclusions"][0]["key"] == "system_log"
    assert report["data_lineage_exclusions"][0]["reason"] == "non_data_tab"


def test_workspace_lineage_does_not_mask_missing_network_evidence_after_getter_error():
    failing_tab = SimpleNamespace(get_data_lineage=_raise_runtime)
    workspace = SimpleNamespace(
        tab_specs=lambda: [{"key": "asian_market", "title": "亚洲市场", "group": "情报源"}],
        get_loaded_tab=lambda key: failing_tab if key == "asian_market" else None,
    )

    lineage = runtime_health._workspace_lineage(SimpleNamespace(_workspace=workspace))
    asian_lineage = next(entry for entry in lineage if entry["key"] == "asian_market")

    assert asian_lineage["loaded"] is True
    assert asian_lineage["network_capable"] is True
    assert asian_lineage["triggered_network"] is None
    assert asian_lineage["lineage_error"] is True


def test_unloaded_network_capable_lineage_reports_no_runtime_network_activity():
    workspace = SimpleNamespace(
        tab_specs=lambda: [{"key": "asian_market", "title": "亚洲市场", "group": "情报源"}],
        get_loaded_tab=lambda _key: None,
    )

    lineage = runtime_health._workspace_lineage(SimpleNamespace(_workspace=workspace))
    asian_lineage = next(entry for entry in lineage if entry["key"] == "asian_market")

    assert asian_lineage["loaded"] is False
    assert asian_lineage["network_capable"] is True
    assert asian_lineage["triggered_network"] is False


def test_runtime_health_exposes_central_quote_universe_coverage():
    central = SimpleNamespace(
        get_quote_coverage_snapshot=lambda: {
            "total_unique": 225,
            "duplicate_dropped": 17,
            "by_source": {
                "watchlist": {"added_unique": 53, "origin": "loaded_tab"},
                "lhb": {"added_unique": 47, "origin": "headless_cache"},
            },
            "degraded_reasons": ["lhb:lhb_rps_unavailable_keep_base_pool"],
        }
    )

    snapshot = runtime_health._central_quotes_snapshot(central)

    assert snapshot["quote_coverage"] == {
        "total_unique": 225,
        "duplicate_dropped": 17,
        "by_source": {
            "watchlist": {"added_unique": 53, "origin": "loaded_tab"},
            "lhb": {"added_unique": 47, "origin": "headless_cache"},
        },
        "degraded_reasons": ["lhb:lhb_rps_unavailable_keep_base_pool"],
    }


def test_workspace_lineage_uses_latched_runtime_network_evidence():
    tab = SimpleNamespace(_runtime_network_triggered=False)
    workspace = SimpleNamespace(
        tab_specs=lambda: [{"key": "asian_market", "title": "亚洲市场", "group": "情报源"}],
        get_loaded_tab=lambda key: tab if key == "asian_market" else None,
    )
    main_window = SimpleNamespace(_workspace=workspace)

    first = next(entry for entry in runtime_health._workspace_lineage(main_window) if entry["key"] == "asian_market")
    mark_runtime_network_activity(tab)
    second = next(entry for entry in runtime_health._workspace_lineage(main_window) if entry["key"] == "asian_market")

    assert first["triggered_network"] is False
    assert second["triggered_network"] is True


def test_runtime_health_short_default_tabs_match_static_lineage_coverage():
    covered = set(DATA_LINEAGE_COVERED_TABS)
    excluded = set(DATA_LINEAGE_EXCLUDED_TABS)
    short_default_tabs = set(DEFAULT_TABS)

    assert {"asian_market", "na_daily", "ai_industry_chain"} <= short_default_tabs
    assert {"asian_market", "na_daily", "ai_industry_chain"} <= covered
    assert short_default_tabs == covered | excluded
    assert short_default_tabs - covered - excluded == set()
    assert "system_log" not in covered
    assert "system_log" in short_default_tabs
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


def test_runtime_health_helpers_fall_back_on_invalid_inputs():
    class _RowsFallback:
        row_data = [1, 2, 3]

        @staticmethod
        def rowCount():
            raise RuntimeError("bad model")

    table_tab = SimpleNamespace(
        model=None,
        source_model=None,
        proxy_model=None,
        get_primary_table=lambda: SimpleNamespace(model=lambda: _FakeModel()),
    )
    bad_table_tab = SimpleNamespace(
        model=None,
        source_model=None,
        proxy_model=None,
        get_primary_table=lambda: SimpleNamespace(model=_raise_runtime),
    )

    assert runtime_health._iso_from_timestamp(object()) == ""
    assert runtime_health._safe_len(1) is None
    assert runtime_health._safe_row_count(None) is None
    assert runtime_health._safe_row_count(_RowsFallback()) == 3
    assert runtime_health._tab_row_count(table_tab) == 2
    assert runtime_health._tab_row_count(bad_table_tab) is None
    assert runtime_health._timer_snapshot(None)["total"] == 0
    assert runtime_health._trend_one([])["count"] == 0

    trend = build_runtime_health_trend([{"process": object()}])
    assert trend["threads"]["count"] == 0


def test_runtime_health_active_task_snapshot_handles_workers_and_resolve_errors(monkeypatch):
    from core.background_job_runner import background_job_runner

    worker = SimpleNamespace(cancellation_token=SimpleNamespace(cancelled=True))
    manager = SimpleNamespace(active_workers={"b": worker, "a": object()})
    monkeypatch.setattr(background_job_runner, "_resolve_manager", lambda: manager)

    snapshot = runtime_health._active_task_snapshot()

    assert snapshot["available"] is True
    assert snapshot["count"] == 2
    assert snapshot["ids"] == ["a", "b"]
    assert snapshot["workers"][1]["cancelled"] is True

    monkeypatch.setattr(background_job_runner, "_resolve_manager", _raise_runtime)
    unavailable = runtime_health._active_task_snapshot()
    assert unavailable["available"] is False
    assert unavailable["count"] is None
    assert unavailable["diagnostic_error"] == "RuntimeError"


def test_runtime_health_timer_snapshot_filters_bad_timers():
    class _Timer:
        @staticmethod
        def parent():
            return SimpleNamespace()

        @staticmethod
        def objectName():
            return "heartbeat"

        @staticmethod
        def isActive():
            return True

        @staticmethod
        def interval():
            return 1000

        @staticmethod
        def isSingleShot():
            return False

    class _BadTimer:
        @staticmethod
        def parent():
            raise RuntimeError("bad timer")

    root = SimpleNamespace(findChildren=lambda _type: [_Timer(), _BadTimer()])
    bad_root = SimpleNamespace(findChildren=_raise_runtime)

    snapshot = runtime_health._timer_snapshot(root)

    assert snapshot["total"] == 1
    assert snapshot["active"] == 1
    assert snapshot["key_intervals"][0]["object_name"] == "heartbeat"
    assert runtime_health._timer_snapshot(bad_root)["total"] == 0


def test_runtime_health_event_bus_snapshot_handles_missing_and_bad_receivers(monkeypatch):
    from domains.runtime import domain_events

    signal = object()
    monkeypatch.setattr(runtime_health, "EVENT_SIGNAL_NAMES", ("present_signal", "missing_signal"))
    monkeypatch.setattr(domain_events, "present_signal", signal, raising=False)
    monkeypatch.setattr(domain_events, "receivers", _raise_runtime, raising=False)

    snapshot = runtime_health._event_bus_snapshot()

    assert snapshot["signals"] == {"present_signal": None}

    real_import = builtins.__import__

    def _blocked_import(name, globals=None, locals=None, fromlist=(), level=0):
        if name == "domains.runtime" and "domain_events" in fromlist:
            raise ImportError("blocked")
        return real_import(name, globals, locals, fromlist, level)

    monkeypatch.setattr(builtins, "__import__", _blocked_import)
    assert runtime_health._event_bus_snapshot()["signals"] == {}


def test_runtime_health_process_and_webengine_snapshots_cover_psutil_edges(monkeypatch):
    class _PsutilError(Exception):
        pass

    class _Memory:
        rss = 2 * 1024 * 1024
        vms = 3 * 1024 * 1024
        private = 4 * 1024 * 1024
        wset = 5 * 1024 * 1024

    class _ChildProcess:
        pid = 123

        @staticmethod
        def memory_info():
            return _Memory()

        @staticmethod
        def name():
            return "QtWebEngineProcess"

        @staticmethod
        def num_threads():
            return 7

    class _BadProcess:
        @staticmethod
        def memory_info():
            raise _PsutilError("bad process")

    class _ParentProcess:
        def __init__(self, _pid):
            pass

        @staticmethod
        def children(recursive=False):
            return [_ChildProcess(), _BadProcess()]

    fake_psutil = SimpleNamespace(Error=_PsutilError, Process=_ParentProcess)
    monkeypatch.setattr(runtime_health, "psutil", fake_psutil)

    child = runtime_health._process_info(_ChildProcess())
    webengine = runtime_health._webengine_snapshot()

    assert child["rss_mb"] == 2.0
    assert child["private_mb"] == 4.0
    assert child["working_set_mb"] == 5.0
    assert runtime_health._process_info(_BadProcess()) is None
    assert webengine["count"] == 1
    assert webengine["rss_mb"] == 2.0

    class _BrokenParentProcess:
        def __init__(self, _pid):
            pass

        @staticmethod
        def children(recursive=False):
            raise _PsutilError("bad children")

    monkeypatch.setattr(runtime_health, "psutil", SimpleNamespace(Error=_PsutilError, Process=_BrokenParentProcess))
    broken = runtime_health._webengine_snapshot()
    assert broken["available"] is False
    assert broken["count"] is None

    monkeypatch.setattr(runtime_health, "psutil", None)
    assert runtime_health._process_info(object()) is None
    assert runtime_health._webengine_snapshot()["available"] is False


def test_runtime_health_quote_and_market_data_fallbacks():
    provider = SimpleNamespace(
        read_provider_health=_raise_runtime,
    )
    quote = runtime_health._quote_snapshot(SimpleNamespace(data_provider=provider, central_quotes_svc=None))
    assert quote["request_stats"] == {}
    assert quote["provider_runtime"] == {}
    central_snapshot = runtime_health._central_quotes_snapshot(
        SimpleNamespace(get_quote_coverage_snapshot=_raise_runtime)
    )
    assert central_snapshot["quote_coverage"] == {
        "available": False,
        "degraded_reasons": ["coverage_snapshot_unavailable"],
    }

    assert runtime_health._market_data_snapshot(SimpleNamespace())["data_status"] == "provider_missing"

    bad_status_provider = SimpleNamespace(get_market_data_source_status=_raise_runtime)
    status_error = runtime_health._market_data_snapshot(SimpleNamespace(data_provider=bad_status_provider))
    assert status_error["data_status"] == "status_error"

    cache_provider = SimpleNamespace(cache_data=_LenRaises())
    cache_snapshot = runtime_health._market_data_snapshot(SimpleNamespace(data_provider=cache_provider))
    assert cache_snapshot["memory_symbol_count"] == 0
    assert cache_snapshot["fallback_or_degraded"] is True


def test_runtime_health_f5_and_cache_snapshots_cover_error_paths(tmp_path, monkeypatch):
    scheduler = SimpleNamespace(
        _tasks=_LenRaises(),
        is_running=lambda: True,
        _interval_ms=16,
        _frame_budget_ms=8,
        _max_tasks_per_frame=3,
    )
    host = SimpleNamespace(
        _workspace=SimpleNamespace(_f5_refresh_scheduler=scheduler),
        _f5_job_controller=SimpleNamespace(is_running=True),
    )
    f5_snapshot = runtime_health._f5_scheduler_snapshot(host)
    assert f5_snapshot["scheduler_active"] is True
    assert f5_snapshot["pending_tasks"] is None
    assert f5_snapshot["job_controller_present"] is True
    assert f5_snapshot["job_controller_diagnostics_available"] is True
    assert f5_snapshot["job_controller_running"] is True

    missing_controller = runtime_health._f5_job_controller_snapshot(SimpleNamespace())
    assert missing_controller == {
        "job_controller_present": False,
        "job_controller_diagnostics_available": True,
        "job_controller_running": False,
    }
    invalid_controller = runtime_health._f5_job_controller_snapshot(
        SimpleNamespace(_f5_job_controller=SimpleNamespace(is_running="yes"))
    )
    assert invalid_controller["job_controller_diagnostics_available"] is False
    assert invalid_controller["job_controller_running"] is None

    import core.runtime_paths as runtime_paths

    monkeypatch.setattr(runtime_health, "_active_f5_rps_path", lambda fallback: fallback)
    missing_cache = tmp_path / "missing.json"
    monkeypatch.setattr(runtime_paths, "RPS_CACHE_FILE", str(missing_cache))
    assert runtime_health._f5_cache_snapshot()["exists"] is False

    bad_cache = tmp_path / "bad.json"
    bad_cache.write_text("{", encoding="utf-8")
    monkeypatch.setattr(runtime_paths, "RPS_CACHE_FILE", str(bad_cache))
    assert runtime_health._f5_cache_snapshot()["read_error"] is True

    real_import = builtins.__import__

    def _blocked_import(name, globals=None, locals=None, fromlist=(), level=0):
        if name == "core.runtime_paths":
            raise ImportError("blocked")
        return real_import(name, globals, locals, fromlist, level)

    monkeypatch.setattr(builtins, "__import__", _blocked_import)
    assert runtime_health._f5_cache_snapshot()["path"] == ""


def test_runtime_health_lineage_and_ui_stall_fallbacks(monkeypatch):
    assert runtime_health._workspace_lineage(SimpleNamespace()) == []

    tab = SimpleNamespace(
        model=None,
        source_model=None,
        proxy_model=None,
        get_data_lineage=_raise_runtime,
        lbl_status=SimpleNamespace(text=_raise_runtime),
    )
    workspace = SimpleNamespace(
        tab_specs=lambda: [{"key": "stock_candidates", "title": "Candidates", "group": "Main"}],
        get_loaded_tab=lambda key: tab if key == "stock_candidates" else None,
    )
    lineage = runtime_health._workspace_lineage(SimpleNamespace(_workspace=workspace))
    first = next(entry for entry in lineage if entry["key"] == "stock_candidates")
    assert first["lineage_error"] is True
    assert "status_text" not in first

    monkeypatch.setattr(runtime_health, "get_ui_stall_probe", lambda: None)
    assert runtime_health._ui_stall_snapshot() == {"installed": False}

    monkeypatch.setattr(runtime_health, "get_ui_stall_probe", lambda: SimpleNamespace(stall_snapshot=_raise_runtime))
    assert runtime_health._ui_stall_snapshot()["error"] == "snapshot_failed"


def test_collect_runtime_health_uses_active_qapplication_window(monkeypatch):
    root = SimpleNamespace(findChildren=lambda _type: [], data_provider=None, _workspace=None)
    fake_app = SimpleNamespace(activeWindow=lambda: None, topLevelWidgets=lambda: [root])

    class _FakeQApplication:
        @staticmethod
        def instance():
            return fake_app

    monkeypatch.setattr(runtime_health, "QApplication", _FakeQApplication)
    monkeypatch.setattr(runtime_health, "collect_process_snapshot", lambda: {"thread_count": 1})

    report = collect_runtime_health()

    assert report["timers"]["total"] == 0
    assert report["market_data"]["data_status"] == "provider_missing"
