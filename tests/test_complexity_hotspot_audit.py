# -*- coding: utf-8 -*-
from __future__ import annotations

from scripts.complexity_hotspot_audit import (
    HOTSPOT_BUDGETS,
    MCCABE_COMPLEXITY_BUDGETS,
    REPO_ROOT,
    _collect_functions,
    build_report,
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

    report = build_report(scan_hotspots(tmp_path, {"app/worker.py": {"Worker.run": 5}}))

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
    rps_node = rps_functions["RPSPrecomputer.run_f5_pipeline"]
    rps_line_count = int(getattr(rps_node, "end_lineno", rps_node.lineno)) - int(rps_node.lineno) + 1

    assert rps_line_count == 176
    assert HOTSPOT_BUDGETS["core/rps_precomputer.py"]["RPSPrecomputer.run_f5_pipeline"] == 176
    assert HOTSPOT_BUDGETS["core/startup_orchestrator.py"]["StartupOrchestrator.deferred_data_load"] == 187
    assert HOTSPOT_BUDGETS["scripts/perf_budget_check.py"]["_parse_args"] == 194
    assert HOTSPOT_BUDGETS["ui/kline_window_qt.py"]["KLineChartWindow.__init__"] == 246
    assert HOTSPOT_BUDGETS["ui/kline_chart_payload.py"]["build_kline_html"] == 35
    assert HOTSPOT_BUDGETS["ui/tabs/asian_market_tab.py"]["build_asian_market_local_cache_payload"] == 183
    assert HOTSPOT_BUDGETS["ui/theme_tokens.py"]["build_ui_tokens"] == 192
    assert HOTSPOT_BUDGETS["ui/workers/central_quotes_worker.py"]["CentralQuotesService._trigger_fetch_for_reason"] == 176
    assert HOTSPOT_BUDGETS["ui/workers/scan_worker.py"]["ScanWorker.run"] == 70
    assert HOTSPOT_BUDGETS["ui/workspaces/classic_workspace.py"]["ClassicWorkspace.__init__"] == 186
    assert HOTSPOT_BUDGETS["vcp/fetchers/asian_kline_fetcher.py"]["sync_asian_kline_cache"] == 216


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
    assert len(MCCABE_COMPLEXITY_BUDGETS) == 4
