from pathlib import Path

from scripts import project_audit

CI_WORKFLOW = Path(__file__).resolve().parents[1] / ".github" / "workflows" / "ci.yml"
ASIAN_MARKET_RUFF_TARGETS = (
    "tests/test_asian_market_tab.py",
    "tests/test_asian_market_workers.py",
    "ui/services/asian_market_runtime_service.py",
    "ui/tabs/asian_market_tab.py",
    "ui/tabs/asian_market_workers.py",
)


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
        "complexity-hotspots",
        "http-safety-audit",
        "full-pytest",
        "runtime-self-check",
    ]
    assert "runtime-health-short" not in labels
    assert "ui-stall-budget" not in labels
    assert "ui-stall-smoke" not in labels
    assert "dependency-audit" not in labels
    assert "type-check" not in labels
    assert "coverage-report" not in labels


def test_project_audit_can_skip_ruff_for_ci_audit_smoke():
    labels = _labels(["--quick", "--skip-ruff"])

    assert "ruff" not in labels
    assert labels == [
        "utf8",
        "git-diff-check",
        "compileall",
        "pip-check",
        "architecture-boundaries",
        "ui-stall-smoke",
        "complexity-hotspots",
        "http-safety-audit",
        "runtime-self-check",
    ]


def test_ci_routes_asian_market_style_checks_to_regular_ruff_guardrail():
    workflow = CI_WORKFLOW.read_text(encoding="utf-8")

    assert "python scripts/project_audit.py --quick --skip-webengine-preflight --skip-ruff" in workflow
    for target in ASIAN_MARKET_RUFF_TARGETS:
        assert target in workflow


def test_project_audit_quick_gate_skips_full_pytest_and_webengine_preflight():
    args = project_audit._parse_args(["--quick"])
    commands = project_audit.build_audit_commands(args)

    labels = [command.label for command in commands]
    assert "full-pytest" not in labels
    git_diff = next(command for command in commands if command.label == "git-diff-check")
    assert git_diff.command == ["git", "diff", "--check"]
    runtime = next(command for command in commands if command.label == "runtime-self-check")
    assert "--skip-webengine-preflight" in runtime.command
    complexity = next(command for command in commands if command.label == "complexity-hotspots")
    assert complexity.command == [
        project_audit._python(args),
        "scripts/complexity_hotspot_audit.py",
        "--output",
        project_audit.COMPLEXITY_HOTSPOT_AUDIT_OUTPUT,
    ]
    ui_stall_smoke = next(command for command in commands if command.label == "ui-stall-smoke")
    assert ui_stall_smoke.command == [
        project_audit._python(args),
        "-m",
        "pytest",
        "-q",
        *project_audit.UI_STALL_SMOKE_TESTS,
    ]
    assert any("background_prewarm" in target for target in project_audit.UI_STALL_SMOKE_TESTS)
    assert any("stock_candidate" in target for target in project_audit.UI_STALL_SMOKE_TESTS)
    assert any("fund_holdings" in target for target in project_audit.UI_STALL_SMOKE_TESTS)
    assert "runtime-health-short" not in labels
    assert "ui-stall-budget" not in labels
    assert "dependency-audit" not in labels
    assert "extended-ruff" not in labels
    http_audit = next(command for command in commands if command.label == "http-safety-audit")
    assert http_audit.command == [
        project_audit._python(args),
        "scripts/http_safety_audit.py",
        "--output",
        project_audit.HTTP_SAFETY_AUDIT_OUTPUT,
    ]
    assert "type-check" not in labels
    assert "coverage-report" not in labels


def test_project_audit_adds_extended_ruff_only_when_requested():
    commands = _commands(["--python", "python", "--quick", "--extended-ruff"])

    extended = next(command for command in commands if command.label == "extended-ruff")

    assert extended.command == [
        "python",
        "-m",
        "ruff",
        "check",
        *project_audit.PYTHON_TARGETS,
        "--select",
        ",".join(project_audit.EXTENDED_RUFF_SELECT),
    ]
    assert "B006" in extended.command[-1]
    assert "SIM115" in extended.command[-1]
    assert "RUF064" in extended.command[-1]


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


def test_project_audit_adds_ui_stall_budget_only_when_requested():
    commands = _commands(["--python", "python", "--quick", "--ui-stall-budget"])

    ui_budget = next(command for command in commands if command.label == "ui-stall-budget")

    assert ui_budget.command == [
        "python",
        "scripts/capture_ui_audit_screenshots.py",
        "--offscreen",
        "--strict",
    ]


def test_project_audit_adds_dependency_audit_only_when_requested():
    commands = _commands(["--python", "python", "--quick", "--dependency-audit"])

    dependency = next(command for command in commands if command.label == "dependency-audit")

    assert dependency.command == [
        "python",
        "scripts/dependency_audit.py",
        "--strict",
        "--output",
        project_audit.DEPENDENCY_AUDIT_OUTPUT,
    ]


def test_project_audit_includes_http_safety_audit_by_default_and_keeps_flag_compatible():
    commands = _commands(["--python", "python", "--quick"])

    audit = next(command for command in commands if command.label == "http-safety-audit")

    assert audit.command == [
        "python",
        "scripts/http_safety_audit.py",
        "--output",
        project_audit.HTTP_SAFETY_AUDIT_OUTPUT,
    ]
    flagged_commands = _commands(["--python", "python", "--quick", "--http-safety-audit"])
    assert [command.label for command in flagged_commands].count("http-safety-audit") == 1


def test_project_audit_adds_type_check_only_when_requested():
    commands = _commands(["--python", "python", "--quick", "--type-check"])

    type_check = next(command for command in commands if command.label == "type-check")

    assert type_check.command == ["python", "-m", "pyright", *project_audit.TYPE_CHECK_TARGETS]


def test_project_audit_adds_observation_only_coverage_report_when_requested():
    commands = _commands(["--python", "python", "--quick", "--coverage-report"])

    coverage = next(command for command in commands if command.label == "coverage-report")

    assert coverage.command == [
        "python",
        "-m",
        "pytest",
        "-q",
        "--cov=app",
        "--cov=domains",
        "--cov=infra",
        "--cov-report=term-missing",
        f"--cov-report=json:{project_audit.COVERAGE_REPORT_OUTPUT}",
        "--cov-fail-under=0",
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


def test_project_audit_list_includes_ui_stall_budget_when_requested(capsys):
    result = project_audit.main(["--python", "python", "--quick", "--ui-stall-budget", "--list"])

    output = capsys.readouterr().out
    assert result == 0
    assert "ui-stall-budget: python scripts/capture_ui_audit_screenshots.py --offscreen --strict" in output


def test_project_audit_list_includes_dependency_audit_when_requested(capsys):
    result = project_audit.main(["--python", "python", "--quick", "--dependency-audit", "--list"])

    output = capsys.readouterr().out
    assert result == 0
    assert "dependency-audit: python scripts/dependency_audit.py" in output
    assert "--strict" in output
    assert f"--output {project_audit.DEPENDENCY_AUDIT_OUTPUT}" in output


def test_project_audit_list_includes_http_safety_audit_by_default(capsys):
    result = project_audit.main(["--python", "python", "--quick", "--list"])

    output = capsys.readouterr().out
    assert result == 0
    assert "ui-stall-smoke: python -m pytest -q tests/test_workspace_quote_codes.py" in output
    assert "complexity-hotspots: python scripts/complexity_hotspot_audit.py" in output
    assert f"--output {project_audit.COMPLEXITY_HOTSPOT_AUDIT_OUTPUT}" in output
    assert "http-safety-audit: python scripts/http_safety_audit.py" in output
    assert f"--output {project_audit.HTTP_SAFETY_AUDIT_OUTPUT}" in output


def test_project_audit_list_includes_type_check_when_requested(capsys):
    result = project_audit.main(["--python", "python", "--quick", "--type-check", "--list"])

    output = capsys.readouterr().out
    assert result == 0
    assert "type-check: python -m pyright" in output
    assert "app/services/asian_market_service.py" in output
    assert "app/services/http_client_service.py" in output
    assert "domains/quotes/snapshot.py" in output
    assert "domains/runtime/fault_tolerance.py" in output
    assert "infra/market_data/asian_kline_provider.py" in output
    assert "infra/tasks/process_runner.py" in output


def test_project_audit_list_includes_coverage_report_when_requested(capsys):
    result = project_audit.main(["--python", "python", "--quick", "--coverage-report", "--list"])

    output = capsys.readouterr().out
    assert result == 0
    assert "coverage-report: python -m pytest -q --cov=app --cov=domains --cov=infra" in output
    assert "--cov-fail-under=0" in output


def test_project_audit_list_includes_extended_ruff_when_requested(capsys):
    result = project_audit.main(["--python", "python", "--quick", "--extended-ruff", "--list"])

    output = capsys.readouterr().out
    assert result == 0
    assert "extended-ruff: python -m ruff check" in output
    assert "--select" in output
    assert "B904" in output
    assert "SIM115" in output
    assert "RUF064" in output
