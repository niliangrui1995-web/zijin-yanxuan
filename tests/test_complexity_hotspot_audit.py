# -*- coding: utf-8 -*-
from __future__ import annotations

from scripts.complexity_hotspot_audit import HOTSPOT_BUDGETS, REPO_ROOT, _collect_functions, build_report, scan_hotspots

LARGE_FUNCTION_LINE_THRESHOLD = 170
HOTSPOT_SCAN_ROOTS = ("app", "core", "domains", "infra", "scripts", "ui", "vcp")


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
    assert HOTSPOT_BUDGETS["core/startup_orchestrator.py"]["StartupOrchestrator.deferred_data_load"] == 188
    assert HOTSPOT_BUDGETS["scripts/perf_budget_check.py"]["_parse_args"] == 194
    assert HOTSPOT_BUDGETS["ui/kline_window_qt.py"]["KLineChartWindow.__init__"] == 246
    assert HOTSPOT_BUDGETS["ui/kline_chart_payload.py"]["build_kline_html"] == 35
    assert HOTSPOT_BUDGETS["ui/tabs/asian_market_tab.py"]["build_asian_market_local_cache_payload"] == 183
    assert HOTSPOT_BUDGETS["ui/theme_tokens.py"]["build_ui_tokens"] == 192
    assert HOTSPOT_BUDGETS["ui/workers/rt_scan_worker.py"]["RtScanWorker._run_one_round"] == 30
    assert HOTSPOT_BUDGETS["ui/workers/scan_worker.py"]["ScanWorker.run"] == 70
    assert HOTSPOT_BUDGETS["vcp/fetchers/asian_kline_fetcher.py"]["sync_asian_kline_cache"] == 220


def test_default_hotspot_budgets_cover_current_large_functions():
    missing_or_stale: list[str] = []
    for root_name in HOTSPOT_SCAN_ROOTS:
        for path in sorted((REPO_ROOT / root_name).rglob("*.py")):
            repo_path = path.relative_to(REPO_ROOT).as_posix()
            budgeted_functions = HOTSPOT_BUDGETS.get(repo_path, {})
            for qualname, node in _collect_functions(path).items():
                end_line = int(getattr(node, "end_lineno", node.lineno))
                line_count = end_line - int(node.lineno) + 1
                if line_count < LARGE_FUNCTION_LINE_THRESHOLD:
                    continue
                if budgeted_functions.get(qualname) != line_count:
                    missing_or_stale.append(f"{repo_path}:{qualname}:{line_count}")

    assert not missing_or_stale, "Large functions must have exact hotspot budgets:\n" + "\n".join(missing_or_stale)
