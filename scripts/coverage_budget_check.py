# -*- coding: utf-8 -*-
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

COVERAGE_PACKAGE_BUDGETS = {
    "app": 90.0,
    "core": 90.0,
    "domains": 90.0,
    "infra": 90.0,
    "ui": 90.0,
    "vcp": 90.0,
}
COVERAGE_FILE_BUDGETS = {
    "app/services/watchlist_indicator_service.py": 50.0,
    "vcp/realtime_quote_runtime.py": 80.0,
}


def _normalize_path(value: str) -> str:
    return str(value or "").replace("\\", "/").removeprefix("./")


def _coverage_counts(summary: dict) -> tuple[int, int]:
    covered = int(summary.get("covered_lines") or 0) + int(summary.get("covered_branches") or 0)
    total = int(summary.get("num_statements") or 0) + int(summary.get("num_branches") or 0)
    return covered, total


def _measurement(covered: int, total: int, budget: float) -> dict:
    actual = round((100.0 * covered / total), 3) if total else 0.0
    return {"actual": actual, "budget": float(budget), "covered": covered, "total": total}


def _package_counts(files: dict[str, dict], package: str) -> tuple[int, int]:
    covered = 0
    total = 0
    prefix = f"{package}/"
    for path, payload in files.items():
        if not _normalize_path(path).startswith(prefix):
            continue
        file_covered, file_total = _coverage_counts(dict(payload.get("summary") or {}))
        covered += file_covered
        total += file_total
    return covered, total


def _file_counts(files: dict[str, dict], target_path: str) -> tuple[int, int]:
    normalized_target = _normalize_path(target_path)
    for path, payload in files.items():
        if _normalize_path(path) == normalized_target:
            return _coverage_counts(dict(payload.get("summary") or {}))
    return 0, 0


def _evaluate_target(target: str, covered: int, total: int, budget: float) -> tuple[dict, dict | None]:
    measurement = _measurement(covered, total, budget)
    if total <= 0:
        return measurement, {"target": target, "reason": "coverage_target_missing", **measurement}
    if float(measurement["actual"]) < float(budget):
        return measurement, {"target": target, "reason": "coverage_below_budget", **measurement}
    return measurement, None


def build_report(
    payload: dict,
    *,
    package_budgets: dict[str, float] = COVERAGE_PACKAGE_BUDGETS,
    file_budgets: dict[str, float] = COVERAGE_FILE_BUDGETS,
) -> dict:
    files = dict(payload.get("files") or {})
    package_results = {}
    file_results = {}
    failures = []
    for package, budget in package_budgets.items():
        measurement, failure = _evaluate_target(package, *_package_counts(files, package), budget)
        package_results[package] = measurement
        if failure is not None:
            failures.append(failure)
    for path, budget in file_budgets.items():
        measurement, failure = _evaluate_target(path, *_file_counts(files, path), budget)
        file_results[path] = measurement
        if failure is not None:
            failures.append(failure)
    return {
        "status": "fail" if failures else "ok",
        "packages": package_results,
        "files": file_results,
        "failure_count": len(failures),
        "failures": failures,
    }


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Check per-package and critical-file coverage ratchets.")
    parser.add_argument("--input", type=Path, required=True, help="coverage.py JSON report")
    parser.add_argument("--output", type=Path, default=None)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv or sys.argv[1:])
    report = build_report(json.loads(args.input.read_text(encoding="utf-8")))
    text = json.dumps(report, ensure_ascii=False, indent=2)
    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text, encoding="utf-8")
    print(text)
    return 1 if report["status"] != "ok" else 0


if __name__ == "__main__":
    raise SystemExit(main())
