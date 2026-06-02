from pathlib import Path

CI_WORKFLOW = Path(".github/workflows/ci.yml")


def test_ci_default_test_job_runs_full_pytest_suite():
    text = CI_WORKFLOW.read_text(encoding="utf-8")

    assert "- name: Full regression suite" in text
    assert "run: python -m pytest -q" in text


def test_windows_smoke_uses_verified_constraints_file():
    text = CI_WORKFLOW.read_text(encoding="utf-8")

    assert "python -m pip install -r requirements-dev.txt -c constraints-py314-windows.txt" in text
