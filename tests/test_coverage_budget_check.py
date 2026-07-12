from __future__ import annotations

from scripts.coverage_budget_check import (
    COVERAGE_FILE_BUDGETS,
    COVERAGE_PACKAGE_BUDGETS,
    build_report,
)


def _summary(*, covered_lines: int, statements: int, covered_branches: int = 0, branches: int = 0) -> dict:
    return {
        "covered_lines": covered_lines,
        "num_statements": statements,
        "covered_branches": covered_branches,
        "num_branches": branches,
    }


def test_coverage_budgets_cover_six_canonical_packages_and_realtime_runtime():
    assert set(COVERAGE_PACKAGE_BUDGETS) == {"app", "core", "domains", "infra", "ui", "vcp"}
    assert COVERAGE_FILE_BUDGETS["vcp/realtime_quote_runtime.py"] == 80.0


def test_coverage_budget_report_aggregates_lines_and_branches_by_package():
    payload = {
        "files": {
            "app\\one.py": {"summary": _summary(covered_lines=8, statements=10, covered_branches=1, branches=2)},
            "app\\two.py": {"summary": _summary(covered_lines=9, statements=10, covered_branches=2, branches=2)},
            "vcp\\realtime_quote_runtime.py": {
                "summary": _summary(covered_lines=9, statements=10, covered_branches=3, branches=4)
            },
        }
    }

    report = build_report(
        payload,
        package_budgets={"app": 80.0},
        file_budgets={"vcp/realtime_quote_runtime.py": 80.0},
    )

    assert report["status"] == "ok"
    assert report["packages"]["app"]["actual"] == 83.333
    assert report["files"]["vcp/realtime_quote_runtime.py"]["actual"] == 85.714


def test_coverage_budget_report_fails_missing_and_under_budget_targets():
    payload = {
        "files": {
            "app/one.py": {"summary": _summary(covered_lines=5, statements=10)},
        }
    }

    report = build_report(
        payload,
        package_budgets={"app": 60.0, "core": 1.0},
        file_budgets={"vcp/realtime_quote_runtime.py": 1.0},
    )

    assert report["status"] == "fail"
    assert {(item["target"], item["reason"]) for item in report["failures"]} == {
        ("app", "coverage_below_budget"),
        ("core", "coverage_target_missing"),
        ("vcp/realtime_quote_runtime.py", "coverage_target_missing"),
    }
