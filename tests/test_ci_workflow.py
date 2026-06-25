from pathlib import Path

CI_WORKFLOW = Path(".github/workflows/ci.yml")


def test_ci_workflow_uses_minimal_default_permissions():
    text = CI_WORKFLOW.read_text(encoding="utf-8")

    assert "\npermissions:\n  contents: read\n\n" in text


def test_ci_checkout_steps_do_not_persist_credentials():
    lines = CI_WORKFLOW.read_text(encoding="utf-8").splitlines()
    checkout_indexes = [index for index, line in enumerate(lines) if line.strip() == "- uses: actions/checkout@v4"]

    assert checkout_indexes
    for index in checkout_indexes:
        block = "\n".join(lines[index : index + 4])
        assert "with:" in block
        assert "persist-credentials: false" in block


def test_ci_default_test_job_runs_full_pytest_suite():
    text = CI_WORKFLOW.read_text(encoding="utf-8")

    assert "- name: Full regression suite" in text
    assert "run: python -m pytest -q" in text


def test_windows_smoke_uses_verified_constraints_file():
    text = CI_WORKFLOW.read_text(encoding="utf-8")

    assert "python -m pip install -r requirements-dev.txt -c constraints-py314-windows.txt" in text
