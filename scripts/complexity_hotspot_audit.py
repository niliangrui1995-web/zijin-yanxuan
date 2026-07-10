# -*- coding: utf-8 -*-
from __future__ import annotations

import argparse
import ast
import json
import re
import subprocess
import sys
from dataclasses import asdict, dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

HOTSPOT_BUDGETS = {
    "core/rps_precomputer.py": {
        "RPSPrecomputer.run_f5_pipeline": 176,
    },
    "core/startup_orchestrator.py": {
        "StartupOrchestrator.deferred_data_load": 187,
    },
    "domains/scan/breakout_monitor_service.py": {
        "BreakoutMonitorService.precompute_ready_pool": 175,
    },
    "scripts/perf_budget_check.py": {
        "_parse_args": 194,
    },
    "ui/kline_window_header.py": {
        "apply_qt_theme": 199,
    },
    "ui/kline_window_qt.py": {
        "KLineChartWindow.__init__": 246,
    },
    "ui/kline_chart_payload.py": {
        "build_kline_html": 35,
    },
    "ui/styles/global_qss.py": {
        "generate_global_qss": 240,
    },
    "ui/styles/global_qss_sections.py": {
        "build_button_qss": 161,
        "build_dialog_log_scrollbar_qss": 174,
        "build_toolbar_status_qss": 175,
    },
    "ui/tabs/asian_market_tab.py": {
        "build_asian_market_local_cache_payload": 183,
    },
    "ui/tabs/lhb_tab.py": {
        "LhbTab._start_backfill": 232,
    },
    "ui/theme_tokens.py": {
        "build_ui_tokens": 192,
    },
    "ui/workers/central_quotes_worker.py": {
        "CentralQuotesService._trigger_fetch_for_reason": 176,
    },
    "ui/workers/scan_worker.py": {
        "ScanWorker.run": 70,
        "ScanWorker._ensure_scan_source_data": 35,
        "ScanWorker._build_scan_matrix": 20,
        "ScanWorker._scan_matrix_candidates": 35,
        "ScanWorker._scan_candidate_for_day": 55,
        "ScanWorker._enrich_market_caps": 35,
        "ScanWorker._enrich_hot_sectors": 25,
    },
    "ui/workspaces/classic_workspace.py": {
        "ClassicWorkspace.__init__": 186,
    },
    "vcp/fetchers/asian_kline_fetcher.py": {
        "sync_asian_kline_cache": 216,
    },
}

LARGE_FUNCTION_LINE_THRESHOLD = 170
HOTSPOT_SCAN_ROOTS = ("app", "core", "domains", "infra", "scripts", "ui", "vcp")
MCCABE_COMPLEXITY_THRESHOLD = 25
MCCABE_COMPLEXITY_BUDGETS = {
    "core/startup_orchestrator.py": {
        "StartupOrchestrator.deferred_data_load": 26,
    },
    "domains/scan/breakout_monitor_service.py": {
        "BreakoutMonitorService.precompute_ready_pool": 33,
    },
    "ui/tabs/lhb_tab.py": {
        "LhbTab._start_backfill": 28,
    },
    "vcp/fetchers/asian_kline_fetcher.py": {
        "sync_asian_kline_cache": 26,
    },
}


@dataclass(frozen=True)
class HotspotFinding:
    path: str
    qualname: str
    line_count: int
    budget: int
    start_line: int
    end_line: int


@dataclass(frozen=True)
class ComplexityFinding:
    path: str
    qualname: str
    complexity: int
    budget: int
    start_line: int
    reason: str


class _FunctionCollector(ast.NodeVisitor):
    def __init__(self) -> None:
        self.scope: list[str] = []
        self.functions: dict[str, ast.FunctionDef | ast.AsyncFunctionDef] = {}

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self.scope.append(node.name)
        self.generic_visit(node)
        self.scope.pop()

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._record_function(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._record_function(node)

    def _record_function(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        qualname = ".".join([*self.scope, node.name])
        self.functions[qualname] = node
        self.scope.append(node.name)
        self.generic_visit(node)
        self.scope.pop()


def _to_repo_path(path: Path, root: Path) -> str:
    return path.resolve().relative_to(root.resolve()).as_posix()


def _collect_functions(path: Path) -> dict[str, ast.FunctionDef | ast.AsyncFunctionDef]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    collector = _FunctionCollector()
    collector.visit(tree)
    return collector.functions


def scan_hotspots(root: Path = REPO_ROOT, budgets: dict[str, dict[str, int]] = HOTSPOT_BUDGETS) -> list[HotspotFinding]:
    findings: list[HotspotFinding] = []
    for repo_path, function_budgets in budgets.items():
        path = root / repo_path
        functions = _collect_functions(path) if path.exists() else {}
        for qualname, budget in function_budgets.items():
            node = functions.get(qualname)
            if node is None:
                findings.append(
                    HotspotFinding(
                        path=repo_path,
                        qualname=qualname,
                        line_count=0,
                        budget=budget,
                        start_line=0,
                        end_line=0,
                    )
                )
                continue
            end_line = int(getattr(node, "end_lineno", node.lineno))
            line_count = end_line - int(node.lineno) + 1
            if line_count > budget:
                findings.append(
                    HotspotFinding(
                        path=repo_path,
                        qualname=qualname,
                        line_count=line_count,
                        budget=budget,
                        start_line=int(node.lineno),
                        end_line=end_line,
                    )
                )
    return findings


def scan_large_function_budget_coverage(
    root: Path = REPO_ROOT,
    budgets: dict[str, dict[str, int]] = HOTSPOT_BUDGETS,
) -> list[HotspotFinding]:
    findings: list[HotspotFinding] = []
    for root_name in HOTSPOT_SCAN_ROOTS:
        scan_root = root / root_name
        if not scan_root.exists():
            continue
        for path in sorted(scan_root.rglob("*.py")):
            repo_path = _to_repo_path(path, root)
            function_budgets = budgets.get(repo_path, {})
            for qualname, node in _collect_functions(path).items():
                end_line = int(getattr(node, "end_lineno", node.lineno))
                line_count = end_line - int(node.lineno) + 1
                if line_count < LARGE_FUNCTION_LINE_THRESHOLD:
                    continue
                budget = int(function_budgets.get(qualname, 0) or 0)
                if budget == line_count:
                    continue
                findings.append(
                    HotspotFinding(
                        path=repo_path,
                        qualname=qualname,
                        line_count=line_count,
                        budget=budget,
                        start_line=int(node.lineno),
                        end_line=end_line,
                    )
                )
    return findings


def _ruff_complexities(root: Path) -> list[tuple[str, str, int, int]]:
    targets = [root_name for root_name in HOTSPOT_SCAN_ROOTS if (root / root_name).exists()]
    if not targets:
        return []
    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "ruff",
            "check",
            *targets,
            "--select",
            "C901",
            "--output-format",
            "json",
            "--exit-zero",
            "--no-cache",
        ],
        cwd=root,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    if completed.returncode != 0:
        detail = str(completed.stderr or completed.stdout or "").strip()
        raise RuntimeError(f"Ruff C901 scan failed: {detail}")
    payload = json.loads(completed.stdout or "[]")
    function_cache: dict[str, dict[int, str]] = {}
    rows: list[tuple[str, str, int, int]] = []
    for item in payload:
        message = str(item.get("message", "") or "")
        matched = re.search(r"`([^`]+)` is too complex \((\d+) > \d+\)", message)
        if matched is None:
            continue
        complexity = int(matched.group(2))
        if complexity <= MCCABE_COMPLEXITY_THRESHOLD:
            continue
        path = Path(str(item.get("filename", "") or ""))
        if not path.is_absolute():
            path = root / path
        try:
            repo_path = _to_repo_path(path, root)
        except ValueError:
            continue
        start_line = int((item.get("location") or {}).get("row") or 0)
        line_map = function_cache.get(repo_path)
        if line_map is None:
            line_map = {int(node.lineno): qualname for qualname, node in _collect_functions(path).items()}
            function_cache[repo_path] = line_map
        qualname = line_map.get(start_line, matched.group(1))
        rows.append((repo_path, qualname, complexity, start_line))
    return rows


def scan_mccabe_complexity_budgets(
    root: Path = REPO_ROOT,
    budgets: dict[str, dict[str, int]] = MCCABE_COMPLEXITY_BUDGETS,
) -> list[ComplexityFinding]:
    actual = {(path, qualname): (complexity, start_line) for path, qualname, complexity, start_line in _ruff_complexities(root)}
    findings: list[ComplexityFinding] = []
    for (repo_path, qualname), (complexity, start_line) in sorted(actual.items()):
        budget = int(budgets.get(repo_path, {}).get(qualname, 0) or 0)
        if budget == complexity:
            continue
        if budget <= 0:
            reason = "unbudgeted_complexity_over_25"
        elif complexity > budget:
            reason = "complexity_budget_exceeded"
        else:
            reason = "complexity_budget_must_decrease"
        findings.append(
            ComplexityFinding(
                path=repo_path,
                qualname=qualname,
                complexity=complexity,
                budget=budget,
                start_line=start_line,
                reason=reason,
            )
        )

    for repo_path, function_budgets in budgets.items():
        for qualname, budget in function_budgets.items():
            if (repo_path, qualname) in actual:
                continue
            findings.append(
                ComplexityFinding(
                    path=repo_path,
                    qualname=qualname,
                    complexity=0,
                    budget=int(budget),
                    start_line=0,
                    reason="stale_complexity_budget",
                )
            )
    return findings


def _merge_line_findings(*finding_groups: list[HotspotFinding]) -> list[HotspotFinding]:
    merged = {}
    for finding in (finding for group in finding_groups for finding in group):
        merged[(finding.path, finding.qualname)] = finding
    return [merged[key] for key in sorted(merged)]


def build_report(
    findings: list[HotspotFinding],
    complexity_findings: list[ComplexityFinding] | None = None,
) -> dict:
    complexity_findings = list(complexity_findings or [])
    return {
        "status": "fail" if findings or complexity_findings else "ok",
        "policy": "large functions and McCabe complexity over 25 use exact ratcheting budgets",
        "budgets": HOTSPOT_BUDGETS,
        "mccabe_complexity_budgets": MCCABE_COMPLEXITY_BUDGETS,
        "finding_count": len(findings) + len(complexity_findings),
        "findings": [asdict(finding) for finding in findings],
        "complexity_findings": [asdict(finding) for finding in complexity_findings],
    }


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Audit known complexity hotspots against line-count budgets.")
    parser.add_argument("--root", type=Path, default=REPO_ROOT)
    parser.add_argument("--output", type=Path, default=None)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv or sys.argv[1:])
    line_findings = _merge_line_findings(
        scan_hotspots(args.root),
        scan_large_function_budget_coverage(args.root),
    )
    report = build_report(
        line_findings,
        scan_mccabe_complexity_budgets(args.root),
    )
    text = json.dumps(report, ensure_ascii=False, indent=2)
    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text, encoding="utf-8")
    print(text)
    return 1 if report["status"] != "ok" else 0


if __name__ == "__main__":
    raise SystemExit(main())
