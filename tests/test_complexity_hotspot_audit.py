# -*- coding: utf-8 -*-
from __future__ import annotations

from scripts.complexity_hotspot_audit import (
    HOTSPOT_BUDGETS,
    LARGE_FUNCTION_LINE_THRESHOLD,
    LEGACY_SOURCE_MOVES,
    MCCABE_COMPLEXITY_BUDGETS,
    REPO_ROOT,
    _collect_functions,
    build_report,
    scan_changed_code_budgets,
    scan_hotspots,
    scan_large_function_budget_coverage,
    scan_mccabe_complexity_budgets,
)


def _write(path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def test_complexity_hotspot_audit_accepts_functions_within_budget(tmp_path):
    _write(
        tmp_path / "app" / "worker.py",
        """
class Worker:
    def run(self):
        return 1
""",
    )

    report = build_report(scan_hotspots(tmp_path, {"app/worker.py": {"Worker.run": 2}}))

    assert report["status"] == "ok"
    assert report["findings"] == []


def test_complexity_hotspot_audit_rejects_functions_over_budget(tmp_path):
    _write(
        tmp_path / "app" / "worker.py",
        """
class Worker:
    def run(self):
        value = 1
        value += 1
        return value
""",
    )

    report = build_report(scan_hotspots(tmp_path, {"app/worker.py": {"Worker.run": 3}}))

    assert report["status"] == "fail"
    assert report["finding_count"] == 1
    assert report["findings"][0]["qualname"] == "Worker.run"
    assert report["findings"][0]["line_count"] == 4
    assert report["findings"][0]["budget"] == 3


def test_complexity_hotspot_audit_requires_legacy_budget_to_fall_with_function(tmp_path):
    _write(
        tmp_path / "app" / "worker.py",
        """
def run():
    return 1
""",
    )

    findings = scan_hotspots(tmp_path, {"app/worker.py": {"run": 10}})

    assert len(findings) == 1
    assert findings[0].line_count == 2
    assert findings[0].budget == 10


def test_complexity_hotspot_audit_rejects_missing_hotspot_function(tmp_path):
    _write(
        tmp_path / "app" / "worker.py",
        """
class Worker:
    def other(self):
        return 1
""",
    )

    report = build_report(scan_hotspots(tmp_path, {"app/worker.py": {"Worker.run": 5}}))

    assert report["status"] == "fail"
    assert report["findings"][0]["qualname"] == "Worker.run"
    assert report["findings"][0]["line_count"] == 0


def test_default_hotspot_budgets_cover_known_refactor_targets():
    rps_functions = _collect_functions(REPO_ROOT / "core" / "rps_precomputer.py")

    assert "RPSPrecomputer.run_f5_pipeline" not in rps_functions
    assert "RPSPrecomputer.run_f5_job" in rps_functions
    assert "core/rps_precomputer.py" not in HOTSPOT_BUDGETS
    assert "app/bootstrap/startup_orchestrator.py" not in HOTSPOT_BUDGETS
    assert HOTSPOT_BUDGETS["scripts/perf_budget_check.py"]["_parse_args"] == 13
    assert (
        HOTSPOT_BUDGETS["scripts/native_watchlist_profile.py"][
            "_NativeProfileController._poll_background_prewarm_finished"
        ]
        == 204
    )
    assert HOTSPOT_BUDGETS["ui/kline_window_qt.py"]["KLineChartWindow.__init__"] == 222
    assert HOTSPOT_BUDGETS["ui/kline_chart_payload.py"]["build_kline_html"] == 28
    assert HOTSPOT_BUDGETS["ui/tabs/asian_market_tab.py"]["build_asian_market_local_cache_payload"] == 171
    assert HOTSPOT_BUDGETS["ui/theme_tokens.py"]["build_ui_tokens"] == 192
    central_functions = _collect_functions(REPO_ROOT / "ui" / "workers" / "central_quotes_worker.py")
    central_trigger = central_functions["CentralQuotesService._trigger_fetch_for_reason"]
    central_trigger_lines = int(getattr(central_trigger, "end_lineno", central_trigger.lineno)) - central_trigger.lineno + 1
    assert central_trigger_lines < LARGE_FUNCTION_LINE_THRESHOLD
    assert "ui/workers/central_quotes_worker.py" not in HOTSPOT_BUDGETS
    assert HOTSPOT_BUDGETS["ui/workers/scan_worker.py"]["ScanWorker.run"] == 58
    workspace_functions = _collect_functions(REPO_ROOT / "ui" / "workspaces" / "classic_workspace.py")
    workspace_node = workspace_functions["ClassicWorkspace.__init__"]
    workspace_line_count = int(getattr(workspace_node, "end_lineno", workspace_node.lineno)) - workspace_node.lineno + 1
    assert HOTSPOT_BUDGETS["ui/workspaces/classic_workspace.py"]["ClassicWorkspace.__init__"] == workspace_line_count
    assert HOTSPOT_BUDGETS["vcp/fetchers/asian_kline_fetcher.py"]["sync_asian_kline_cache"] == 168
    assert HOTSPOT_BUDGETS["vcp/data_provider_realtime.py"]["_fetch_realtime_quote_sources"] == 197


def test_default_hotspot_budgets_cover_current_large_functions():
    assert scan_large_function_budget_coverage() == []


def test_mccabe_budget_rejects_unbudgeted_complexity_over_25(tmp_path):
    body = ["def heavy(value):"]
    for index in range(26):
        body.extend((f"    if value == {index}:", "        value += 1"))
    body.append("    return value")
    _write(tmp_path / "app" / "worker.py", "\n".join(body))

    findings = scan_mccabe_complexity_budgets(tmp_path, {})

    assert len(findings) == 1
    assert findings[0].qualname == "heavy"
    assert findings[0].complexity > 25
    assert findings[0].reason == "unbudgeted_complexity_over_25"
    exact_budget = {"app/worker.py": {"heavy": findings[0].complexity}}
    assert scan_mccabe_complexity_budgets(tmp_path, exact_budget) == []


def test_default_mccabe_budgets_cover_current_complexity_over_25():
    assert scan_mccabe_complexity_budgets() == []
    assert MCCABE_COMPLEXITY_BUDGETS["scripts/native_watchlist_profile.py"]["_residual_repaint_acceptance"] == 30
    assert len(MCCABE_COMPLEXITY_BUDGETS) == 2


def test_legacy_source_moves_follow_canonical_app_entrypoints():
    assert LEGACY_SOURCE_MOVES == {
        "app/bootstrap/startup_orchestrator.py": "core/startup_orchestrator.py",
        "app/services/ui_industry_chain_service.py": "core/ai_industry_chain_pool.py",
        "app/services/ui_lhb_pool_service.py": "core/lhb_pool_manager.py",
    }


def test_changed_code_gate_rejects_new_long_complex_function_and_god_class(tmp_path):
    source = """
class Worker:
    def first(self, value):
        if value:
            value += 1
        if value > 2:
            value += 1
        return value

    def second(self):
        return 2
"""
    _write(tmp_path / "app" / "worker.py", source)

    findings = scan_changed_code_budgets(
        tmp_path,
        {"app/worker.py": [(1, 11)]},
        baseline_sources={},
        max_function_lines=4,
        max_function_complexity=2,
        max_class_lines=8,
        max_class_methods=1,
    )

    assert {(finding.qualname, finding.metric) for finding in findings} == {
        ("Worker", "line_count"),
        ("Worker", "method_count"),
        ("Worker.first", "complexity"),
        ("Worker.first", "line_count"),
    }


def test_changed_code_gate_ratchets_legacy_metrics_instead_of_forcing_big_bang(tmp_path):
    baseline = """
def legacy(value):
    if value:
        value += 1
    if value > 2:
        value += 1
    return value
"""
    current = """
def legacy(value):
    if value:
        value += 1
    if value > 2:
        value += 1
    if value > 3:
        value += 1
    return value
"""
    _write(tmp_path / "app" / "worker.py", current)

    findings = scan_changed_code_budgets(
        tmp_path,
        {"app/worker.py": [(2, 9)]},
        baseline_sources={"app/worker.py": baseline},
        max_function_lines=3,
        max_function_complexity=1,
    )

    assert {(finding.metric, finding.actual, finding.budget) for finding in findings} == {
        ("line_count", 8, 6),
        ("complexity", 4, 3),
    }
