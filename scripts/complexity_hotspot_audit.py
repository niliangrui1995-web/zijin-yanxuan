# -*- coding: utf-8 -*-
from __future__ import annotations

import argparse
import ast
import json
import os
import re
import subprocess
import sys
from dataclasses import asdict, dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

HOTSPOT_BUDGETS = {
    "domains/scan/breakout_monitor_service.py": {
        "BreakoutMonitorService.precompute_ready_pool": 165,
    },
    "scripts/perf_budget_check.py": {
        "_parse_args": 13,
    },
    "ui/kline_window_header.py": {
        "apply_qt_theme": 194,
    },
    "ui/kline_window_qt.py": {
        "KLineChartWindow.__init__": 222,
    },
    "ui/kline_chart_payload.py": {
        "build_kline_html": 28,
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
        "build_asian_market_local_cache_payload": 171,
    },
    "ui/theme_tokens.py": {
        "build_ui_tokens": 192,
    },
    "ui/workers/scan_worker.py": {
        "ScanWorker.run": 58,
        "ScanWorker._ensure_scan_source_data": 24,
        "ScanWorker._build_scan_matrix": 10,
        "ScanWorker._scan_matrix_candidates": 24,
        "ScanWorker._scan_candidate_for_day": 42,
        "ScanWorker._enrich_market_caps": 29,
        "ScanWorker._enrich_hot_sectors": 17,
    },
    "ui/workspaces/classic_workspace.py": {
        "ClassicWorkspace.__init__": 39,
    },
    "vcp/fetchers/asian_kline_fetcher.py": {
        "sync_asian_kline_cache": 168,
    },
}

LARGE_FUNCTION_LINE_THRESHOLD = 170
HOTSPOT_SCAN_ROOTS = ("app", "core", "domains", "infra", "scripts", "ui", "vcp")
MCCABE_COMPLEXITY_THRESHOLD = 25
CHANGED_FUNCTION_MAX_LINES = 50
CHANGED_FUNCTION_MAX_COMPLEXITY = 10
CHANGED_CLASS_MAX_LINES = 500
CHANGED_CLASS_MAX_METHODS = 20
MCCABE_COMPLEXITY_BUDGETS = {
    "domains/scan/breakout_monitor_service.py": {
        "BreakoutMonitorService.precompute_ready_pool": 29,
    },
    "scripts/native_watchlist_profile.py": {
        "_residual_repaint_acceptance": 32,
    },
}
LEGACY_SOURCE_MOVES = {
    "app/bootstrap/startup_orchestrator.py": "core/startup_orchestrator.py",
    "app/services/ui_industry_chain_service.py": "core/ai_industry_chain_pool.py",
    "app/services/ui_lhb_pool_service.py": "core/lhb_pool_manager.py",
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


@dataclass(frozen=True)
class ChangedCodeFinding:
    path: str
    qualname: str
    metric: str
    actual: int
    budget: int
    start_line: int
    reason: str


class _FunctionCollector(ast.NodeVisitor):
    def __init__(self) -> None:
        self.scope: list[str] = []
        self.functions: dict[str, ast.FunctionDef | ast.AsyncFunctionDef] = {}
        self.classes: dict[str, ast.ClassDef] = {}

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        qualname = ".".join([*self.scope, node.name])
        self.classes[qualname] = node
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
    collector = _collect_structure(path.read_text(encoding="utf-8"), filename=str(path))
    return collector.functions


def _collect_structure(source: str, *, filename: str = "<memory>") -> _FunctionCollector:
    tree = ast.parse(source, filename=filename)
    collector = _FunctionCollector()
    collector.visit(tree)
    return collector


def _node_line_count(node: ast.AST) -> int:
    start_line = int(getattr(node, "lineno", 0) or 0)
    end_line = int(getattr(node, "end_lineno", start_line) or start_line)
    return max(0, end_line - start_line + 1)


class _BranchCounter(ast.NodeVisitor):
    def __init__(self, root: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        self.root = root
        self.value = 1

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        if node is self.root:
            self.generic_visit(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        if node is self.root:
            self.generic_visit(node)

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        return

    def visit_If(self, node: ast.If) -> None:
        self.value += 1
        self.generic_visit(node)

    def visit_For(self, node: ast.For) -> None:
        self.value += 1
        self.generic_visit(node)

    visit_AsyncFor = visit_For

    def visit_While(self, node: ast.While) -> None:
        self.value += 1
        self.generic_visit(node)

    def visit_IfExp(self, node: ast.IfExp) -> None:
        self.value += 1
        self.generic_visit(node)

    def visit_BoolOp(self, node: ast.BoolOp) -> None:
        self.value += max(0, len(node.values) - 1)
        self.generic_visit(node)

    def visit_Try(self, node: ast.Try) -> None:
        self.value += len(node.handlers) + int(bool(node.orelse))
        self.generic_visit(node)

    visit_TryStar = visit_Try

    def visit_Match(self, node: ast.Match) -> None:
        self.value += max(0, len(node.cases) - 1)
        self.generic_visit(node)

    def visit_comprehension(self, node: ast.comprehension) -> None:
        self.value += 1 + len(node.ifs)
        self.generic_visit(node)


def _function_complexity(node: ast.FunctionDef | ast.AsyncFunctionDef) -> int:
    counter = _BranchCounter(node)
    counter.visit(node)
    return counter.value


def _node_intersects_ranges(node: ast.AST, ranges: list[tuple[int, int]]) -> bool:
    start_line = int(getattr(node, "lineno", 0) or 0)
    end_line = int(getattr(node, "end_lineno", start_line) or start_line)
    return any(start_line <= changed_end and changed_start <= end_line for changed_start, changed_end in ranges)


def _append_metric_finding(
    findings: list[ChangedCodeFinding],
    *,
    repo_path: str,
    qualname: str,
    metric: str,
    actual: int,
    budget: int,
    start_line: int,
    is_new: bool,
) -> None:
    if actual <= budget:
        return
    findings.append(
        ChangedCodeFinding(
            path=repo_path,
            qualname=qualname,
            metric=metric,
            actual=actual,
            budget=budget,
            start_line=start_line,
            reason=f"{'new' if is_new else 'changed'}_{metric}_budget_exceeded",
        )
    )


def _scan_changed_functions(
    repo_path: str,
    ranges: list[tuple[int, int]],
    current: _FunctionCollector,
    baseline: _FunctionCollector | None,
    *,
    max_lines: int,
    max_complexity: int,
) -> list[ChangedCodeFinding]:
    findings: list[ChangedCodeFinding] = []
    baseline_functions = baseline.functions if baseline is not None else {}
    for qualname, node in current.functions.items():
        if not _node_intersects_ranges(node, ranges):
            continue
        baseline_node = baseline_functions.get(qualname)
        is_new = baseline_node is None
        line_budget = max(max_lines, _node_line_count(baseline_node)) if baseline_node is not None else max_lines
        complexity_budget = (
            max(max_complexity, _function_complexity(baseline_node)) if baseline_node is not None else max_complexity
        )
        metrics = (
            ("line_count", _node_line_count(node), line_budget),
            ("complexity", _function_complexity(node), complexity_budget),
        )
        for metric, actual, budget in metrics:
            _append_metric_finding(
                findings,
                repo_path=repo_path,
                qualname=qualname,
                metric=metric,
                actual=actual,
                budget=budget,
                start_line=int(node.lineno),
                is_new=is_new,
            )
    return findings


def _class_method_count(node: ast.ClassDef) -> int:
    return sum(isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)) for item in node.body)


def _scan_changed_classes(
    repo_path: str,
    ranges: list[tuple[int, int]],
    current: _FunctionCollector,
    baseline: _FunctionCollector | None,
    *,
    max_lines: int,
    max_methods: int,
) -> list[ChangedCodeFinding]:
    findings: list[ChangedCodeFinding] = []
    baseline_classes = baseline.classes if baseline is not None else {}
    for qualname, node in current.classes.items():
        if not _node_intersects_ranges(node, ranges):
            continue
        baseline_node = baseline_classes.get(qualname)
        is_new = baseline_node is None
        line_budget = max(max_lines, _node_line_count(baseline_node)) if baseline_node is not None else max_lines
        method_budget = max(max_methods, _class_method_count(baseline_node)) if baseline_node is not None else max_methods
        for metric, actual, budget in (
            ("line_count", _node_line_count(node), line_budget),
            ("method_count", _class_method_count(node), method_budget),
        ):
            _append_metric_finding(
                findings,
                repo_path=repo_path,
                qualname=qualname,
                metric=metric,
                actual=actual,
                budget=budget,
                start_line=int(node.lineno),
                is_new=is_new,
            )
    return findings


def scan_changed_code_budgets(
    root: Path,
    changed_ranges: dict[str, list[tuple[int, int]]],
    *,
    baseline_sources: dict[str, str] | None = None,
    max_function_lines: int = CHANGED_FUNCTION_MAX_LINES,
    max_function_complexity: int = CHANGED_FUNCTION_MAX_COMPLEXITY,
    max_class_lines: int = CHANGED_CLASS_MAX_LINES,
    max_class_methods: int = CHANGED_CLASS_MAX_METHODS,
) -> list[ChangedCodeFinding]:
    findings: list[ChangedCodeFinding] = []
    baseline_sources = baseline_sources or {}
    for repo_path, ranges in sorted(changed_ranges.items()):
        path = root / repo_path
        if path.suffix != ".py" or not path.is_file():
            continue
        current = _collect_structure(path.read_text(encoding="utf-8"), filename=str(path))
        baseline_source = baseline_sources.get(repo_path)
        baseline = _collect_structure(baseline_source, filename=f"baseline:{repo_path}") if baseline_source else None
        findings.extend(
            _scan_changed_functions(
                repo_path,
                ranges,
                current,
                baseline,
                max_lines=max_function_lines,
                max_complexity=max_function_complexity,
            )
        )
        findings.extend(
            _scan_changed_classes(
                repo_path,
                ranges,
                current,
                baseline,
                max_lines=max_class_lines,
                max_methods=max_class_methods,
            )
        )
    return sorted(findings, key=lambda item: (item.path, item.start_line, item.qualname, item.metric))


def _run_git(root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-c", "core.quotepath=false", *args],
        cwd=root,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )


def _parse_changed_ranges(diff_text: str) -> dict[str, list[tuple[int, int]]]:
    changed_ranges: dict[str, list[tuple[int, int]]] = {}
    repo_path = ""
    hunk_pattern = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@")
    for line in diff_text.splitlines():
        if line.startswith("+++ b/"):
            repo_path = line[6:]
            continue
        match = hunk_pattern.match(line)
        if not repo_path or match is None:
            continue
        start_line = int(match.group(1))
        line_count = int(match.group(2) or 1)
        if line_count > 0:
            changed_ranges.setdefault(repo_path, []).append((start_line, start_line + line_count - 1))
    return changed_ranges


def _git_stdout(completed: subprocess.CompletedProcess[str], *, context: str) -> str:
    if completed.returncode == 0:
        return completed.stdout
    detail = str(completed.stderr or completed.stdout or "").strip()
    raise RuntimeError(f"{context}: {detail}")


def _untracked_python_ranges(root: Path) -> dict[str, list[tuple[int, int]]]:
    completed = _run_git(root, "ls-files", "--others", "--exclude-standard", "--", *HOTSPOT_SCAN_ROOTS)
    output = _git_stdout(completed, context="git untracked scan failed for complexity change gate")
    changed_ranges: dict[str, list[tuple[int, int]]] = {}
    for repo_path in filter(None, (line.strip() for line in output.splitlines())):
        path = root / repo_path
        if path.suffix == ".py" and path.is_file():
            changed_ranges[repo_path] = [(1, len(path.read_text(encoding="utf-8").splitlines()) or 1)]
    return changed_ranges


def _baseline_reference(base_ref: str | None, *, local_default: str) -> str:
    reference = str(base_ref or "").strip()
    if not reference:
        return local_default
    if set(reference) == {"0"}:
        return "HEAD^"
    return reference


def _git_changed_ranges(root: Path, base_ref: str | None) -> dict[str, list[tuple[int, int]]]:
    reference = _baseline_reference(base_ref, local_default="HEAD")
    completed = _run_git(
        root,
        "diff",
        "--unified=0",
        "--no-ext-diff",
        "--diff-filter=ACMRT",
        reference,
        "--",
        *HOTSPOT_SCAN_ROOTS,
    )
    output = _git_stdout(completed, context=f"git diff failed for complexity change gate ({reference})")
    changed_ranges = _parse_changed_ranges(output)
    changed_ranges.update(_untracked_python_ranges(root))
    return changed_ranges


def _git_baseline_sources(
    root: Path,
    repo_paths: list[str],
    base_ref: str | None,
) -> dict[str, str]:
    reference = _baseline_reference(base_ref, local_default="HEAD")
    sources: dict[str, str] = {}
    for repo_path in repo_paths:
        completed = _run_git(root, "show", f"{reference}:{repo_path}")
        if completed.returncode != 0 and repo_path in LEGACY_SOURCE_MOVES:
            completed = _run_git(root, "show", f"{reference}:{LEGACY_SOURCE_MOVES[repo_path]}")
        if completed.returncode == 0:
            sources[repo_path] = completed.stdout
    return sources


def scan_git_changed_code_budgets(
    root: Path = REPO_ROOT,
    *,
    base_ref: str | None = None,
) -> list[ChangedCodeFinding]:
    changed_ranges = _git_changed_ranges(root, base_ref)
    baseline_sources = _git_baseline_sources(root, list(changed_ranges), base_ref)
    return scan_changed_code_budgets(root, changed_ranges, baseline_sources=baseline_sources)


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
            if line_count != budget:
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
    changed_code_findings: list[ChangedCodeFinding] | None = None,
) -> dict:
    complexity_findings = list(complexity_findings or [])
    changed_code_findings = list(changed_code_findings or [])
    return {
        "status": "fail" if findings or complexity_findings or changed_code_findings else "ok",
        "policy": (
            "legacy large functions and McCabe complexity over 25 use exact ratcheting budgets; "
            "new code uses 50-line/CC10 function and 500-line/20-method class budgets; changed legacy code cannot worsen"
        ),
        "budgets": HOTSPOT_BUDGETS,
        "mccabe_complexity_budgets": MCCABE_COMPLEXITY_BUDGETS,
        "changed_code_policy": {
            "max_function_lines": CHANGED_FUNCTION_MAX_LINES,
            "max_function_complexity": CHANGED_FUNCTION_MAX_COMPLEXITY,
            "max_class_lines": CHANGED_CLASS_MAX_LINES,
            "max_class_methods": CHANGED_CLASS_MAX_METHODS,
        },
        "finding_count": len(findings) + len(complexity_findings) + len(changed_code_findings),
        "findings": [asdict(finding) for finding in findings],
        "complexity_findings": [asdict(finding) for finding in complexity_findings],
        "changed_code_findings": [asdict(finding) for finding in changed_code_findings],
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
        scan_git_changed_code_budgets(
            args.root,
            base_ref=str(os.environ.get("VCP_COMPLEXITY_BASE_REF") or "").strip() or None,
        ),
    )
    text = json.dumps(report, ensure_ascii=False, indent=2)
    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text, encoding="utf-8")
    print(text)
    return 1 if report["status"] != "ok" else 0


if __name__ == "__main__":
    raise SystemExit(main())
