# -*- coding: utf-8 -*-
from __future__ import annotations

import argparse
import ast
import json
import sys
from dataclasses import asdict, dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

HOTSPOT_BUDGETS = {
    "core/lhb_pool_manager.py": {
        "LhbPoolManager.compute_pool": 180,
    },
    "core/rps_precomputer.py": {
        "RPSPrecomputer.run_f5_pipeline": 172,
    },
    "core/startup_orchestrator.py": {
        "StartupOrchestrator.deferred_data_load": 188,
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
    "ui/workers/rt_scan_worker.py": {
        "RtScanWorker._run_one_round": 30,
    },
    "ui/workers/lhb_worker.py": {
        "fetch_lhb_data_for_date": 193,
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
        "sync_asian_kline_cache": 220,
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
        functions = _collect_functions(path)
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


def build_report(findings: list[HotspotFinding]) -> dict:
    return {
        "status": "fail" if findings else "ok",
        "policy": "known complexity hotspots must stay within their line-count budgets",
        "budgets": HOTSPOT_BUDGETS,
        "finding_count": len(findings),
        "findings": [asdict(finding) for finding in findings],
    }


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Audit known complexity hotspots against line-count budgets.")
    parser.add_argument("--root", type=Path, default=REPO_ROOT)
    parser.add_argument("--output", type=Path, default=None)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv or sys.argv[1:])
    report = build_report(scan_hotspots(args.root))
    text = json.dumps(report, ensure_ascii=False, indent=2)
    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text, encoding="utf-8")
    print(text)
    return 1 if report["status"] != "ok" else 0


if __name__ == "__main__":
    raise SystemExit(main())
