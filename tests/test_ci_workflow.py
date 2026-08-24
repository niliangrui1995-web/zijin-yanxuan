from pathlib import Path

CI_WORKFLOW = Path(".github/workflows/ci.yml")


def test_ci_workflow_uses_minimal_default_permissions():
    text = CI_WORKFLOW.read_text(encoding="utf-8")

    assert "\npermissions:\n  contents: read\n\n" in text


def test_ci_checkout_steps_do_not_persist_credentials():
    lines = CI_WORKFLOW.read_text(encoding="utf-8").splitlines()
    checkout_indexes = [
        index
        for index, line in enumerate(lines)
        if line.strip()
        == "- uses: actions/checkout@de0fac2e4500dabe0009e67214ff5f5447ce83dd # v6.0.2"
    ]

    assert checkout_indexes
    for index in checkout_indexes:
        block = "\n".join(lines[index : index + 5])
        assert "with:" in block
        assert "persist-credentials: false" in block
        assert "fetch-depth: 0" in block


def test_ci_pins_setup_python_v6_to_verified_release_sha():
    text = CI_WORKFLOW.read_text(encoding="utf-8")

    assert "actions/setup-python@v5" not in text
    assert text.count("actions/setup-python@a309ff8b426b58ec0e2a45f0f869d46889d02405 # v6.2.0") == 5


def test_ci_default_test_job_runs_full_pytest_with_coverage_budgets():
    text = CI_WORKFLOW.read_text(encoding="utf-8")

    assert "- name: Full regression suite with coverage budgets" in text
    assert "--cov-branch --cov=app --cov=core --cov=domains --cov=infra --cov=ui --cov=vcp" in text
    assert "--cov-fail-under=90" in text
    assert "python scripts/coverage_budget_check.py --input tmp/coverage.json" in text


def test_ci_default_test_job_collects_all_gate_failures_before_failing():
    text = CI_WORKFLOW.read_text(encoding="utf-8")

    gate_ids = (
        "ruff_guardrail",
        "root_entrypoint",
        "utf8_guardrail",
        "architecture_regression",
        "service_regression",
        "runtime_dependency",
        "performance_runtime",
        "full_regression",
        "ui_type_check",
        "project_audit",
    )
    for gate_id in gate_ids:
        assert f"id: {gate_id}" in text
    assert text.count("continue-on-error: true") >= len(gate_ids) + 2
    assert "- name: Fail if any test gate failed" in text
    assert "steps.full_regression.outcome" in text
    assert "steps.ui_type_check.outcome" in text
    assert "steps.project_audit.outcome" in text
    assert "pytest_status=$?" in text
    assert "budget_status=$?" in text


def test_ci_windows_smoke_reports_audit_and_runtime_failures_together():
    text = CI_WORKFLOW.read_text(encoding="utf-8")

    assert "id: windows_audit" in text
    assert "id: windows_runtime_smoke" in text
    assert "- name: Fail if any Windows gate failed" in text
    assert "steps.windows_audit.outcome" in text
    assert "steps.windows_runtime_smoke.outcome" in text


def test_windows_smoke_uses_verified_constraints_file():
    text = CI_WORKFLOW.read_text(encoding="utf-8")

    assert "python -m pip install -r requirements-dev.txt -c constraints-py314-windows.txt" in text


def test_linux_jobs_use_verified_constraints_and_keep_auditing_after_failures():
    text = CI_WORKFLOW.read_text(encoding="utf-8")

    assert text.count("python -m pip install -r requirements-dev.txt -c constraints-py314-linux.txt") == 3
    audit_lines = [line.strip() for line in text.splitlines() if "scripts/project_audit.py" in line]
    assert audit_lines
    assert all("--keep-going" in line for line in audit_lines)
    assert "--coverage-report" not in next(line for line in audit_lines if "--runtime-health-short" in line)
    assert "github.event.pull_request.base.sha || github.event.before || 'HEAD^'" in text


def test_scheduled_latest_allowed_canary_resolves_without_constraints():
    text = CI_WORKFLOW.read_text(encoding="utf-8")

    assert "latest-allowed-canary:" in text
    canary = text.split("latest-allowed-canary:", maxsplit=1)[1]
    assert "if: github.event_name == 'workflow_dispatch' || github.event_name == 'schedule'" in canary
    assert "python -m pip install -r requirements-dev.txt\n" in canary
    assert "constraints-py314" not in canary
    assert "--keep-going" in canary


def test_ci_uses_marker_based_regression_groups_without_dropping_existing_coverage():
    text = CI_WORKFLOW.read_text(encoding="utf-8")

    for marker in ("arch", "service", "runtime", "perf", "windows"):
        assert f"python -m pytest -q -m {marker}" in text

    assert (
        "python -m pyright --warnings ui/kline_pool_state.py ui/kline_typing.py ui/kline_window_recovery.py"
        in text
    )

    grouped_steps = (
        "Architecture regression suite",
        "Service regression suite",
        "Runtime dependency guardrail",
        "Performance and runtime guardrail",
        "Windows architecture and runtime smoke",
    )
    for name in grouped_steps:
        step = text.split(f"- name: {name}", maxsplit=1)[1].split("\n      - name:", maxsplit=1)[0]
        assert "tests/test_" not in step


def test_ci_caches_pip_uploads_coverage_and_cancels_superseded_runs():
    text = CI_WORKFLOW.read_text(encoding="utf-8")

    assert "concurrency:\n  group: ${{ github.workflow }}-${{ github.ref }}\n  cancel-in-progress: true" in text
    assert text.count("cache: pip") == 5
    assert "actions/upload-artifact@ea165f8d65b6e75b540449e92b4886f43607fa02 # v4.6.2" in text
    assert "name: coverage-report" in text
    assert "path: tmp/coverage.json" in text
    assert "retention-days: 7" in text
