from pathlib import Path

from scripts import project_audit


def _labels(argv):
    args = project_audit._parse_args(argv)
    return [command.label for command in project_audit.build_audit_commands(args)]


def test_project_audit_full_gate_includes_required_checks():
    labels = _labels([])

    assert labels == [
        "ruff",
        "utf8",
        "git-diff-check",
        "compileall",
        "pip-check",
        "architecture-boundaries",
        "full-pytest",
        "runtime-self-check",
    ]


def test_project_audit_quick_gate_skips_full_pytest_and_webengine_preflight():
    args = project_audit._parse_args(["--quick"])
    commands = project_audit.build_audit_commands(args)

    labels = [command.label for command in commands]
    assert "full-pytest" not in labels
    git_diff = next(command for command in commands if command.label == "git-diff-check")
    assert git_diff.command == ["git", "diff", "--check"]
    runtime = next(command for command in commands if command.label == "runtime-self-check")
    assert "--skip-webengine-preflight" in runtime.command


def test_project_audit_adds_performance_budget_when_reports_are_provided(tmp_path):
    report_path = tmp_path / "runtime_health.json"
    args = project_audit._parse_args(["--runtime-health-report", str(report_path)])

    perf = project_audit.build_audit_commands(args)[-1]

    assert perf.label == "performance-budget"
    assert "--runtime-health-report" in perf.command
    assert str(Path(report_path)) in perf.command
