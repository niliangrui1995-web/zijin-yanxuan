from pathlib import Path

from scripts import project_audit


def _labels(argv):
    args = project_audit._parse_args(argv)
    return [command.label for command in project_audit.build_audit_commands(args)]


def _commands(argv):
    args = project_audit._parse_args(argv)
    return project_audit.build_audit_commands(args)


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
    assert "runtime-health-short" not in labels
    assert "dependency-audit" not in labels


def test_project_audit_quick_gate_skips_full_pytest_and_webengine_preflight():
    args = project_audit._parse_args(["--quick"])
    commands = project_audit.build_audit_commands(args)

    labels = [command.label for command in commands]
    assert "full-pytest" not in labels
    git_diff = next(command for command in commands if command.label == "git-diff-check")
    assert git_diff.command == ["git", "diff", "--check"]
    runtime = next(command for command in commands if command.label == "runtime-self-check")
    assert "--skip-webengine-preflight" in runtime.command
    assert "runtime-health-short" not in labels
    assert "dependency-audit" not in labels


def test_project_audit_adds_runtime_health_short_only_when_requested():
    commands = _commands(["--python", "python", "--quick", "--runtime-health-short"])

    runtime_health = next(command for command in commands if command.label == "runtime-health-short")

    assert runtime_health.command == [
        "python",
        "scripts/runtime_health_stability_suite.py",
        "--mode",
        "short",
        "--fail-on-budget",
        "--output",
        project_audit.RUNTIME_HEALTH_SHORT_OUTPUT,
        "--sample-output-dir",
        project_audit.RUNTIME_HEALTH_SHORT_SAMPLE_OUTPUT_DIR,
    ]


def test_project_audit_adds_dependency_audit_only_when_requested():
    commands = _commands(["--python", "python", "--quick", "--dependency-audit"])

    dependency = next(command for command in commands if command.label == "dependency-audit")

    assert dependency.command == [
        "python",
        "scripts/dependency_audit.py",
        "--output",
        project_audit.DEPENDENCY_AUDIT_OUTPUT,
    ]


def test_project_audit_adds_performance_budget_when_reports_are_provided(tmp_path):
    report_path = tmp_path / "runtime_health.json"
    args = project_audit._parse_args(["--runtime-health-report", str(report_path)])

    perf = project_audit.build_audit_commands(args)[-1]

    assert perf.label == "performance-budget"
    assert "--runtime-health-report" in perf.command
    assert str(Path(report_path)) in perf.command


def test_project_audit_list_includes_runtime_health_short_when_requested(capsys):
    result = project_audit.main(["--python", "python", "--quick", "--runtime-health-short", "--list"])

    output = capsys.readouterr().out
    assert result == 0
    assert "runtime-health-short: python scripts/runtime_health_stability_suite.py" in output
    assert "--fail-on-budget" in output
    assert f"--output {project_audit.RUNTIME_HEALTH_SHORT_OUTPUT}" in output


def test_project_audit_list_includes_dependency_audit_when_requested(capsys):
    result = project_audit.main(["--python", "python", "--quick", "--dependency-audit", "--list"])

    output = capsys.readouterr().out
    assert result == 0
    assert "dependency-audit: python scripts/dependency_audit.py" in output
    assert f"--output {project_audit.DEPENDENCY_AUDIT_OUTPUT}" in output
