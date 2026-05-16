import json

from scripts import dependency_audit


def _completed(command, *, returncode=0, stdout="", stderr=""):
    return {
        "command": command,
        "status": "ok" if returncode == 0 else "failed",
        "returncode": returncode,
        "timeout": False,
        "timeout_seconds": dependency_audit.DEFAULT_TIMEOUT_SECONDS,
        "stdout": stdout,
        "stderr": stderr,
    }


def test_dependency_audit_command_builders_use_selected_python():
    assert dependency_audit.build_pip_version_command("python") == ["python", "-m", "pip", "--version"]
    assert dependency_audit.build_pip_check_command("python") == ["python", "-m", "pip", "check"]
    assert dependency_audit.build_pip_inspect_command("python") == ["python", "-m", "pip", "inspect", "--local"]
    assert dependency_audit.build_pip_audit_command("python") == [
        "python",
        "-m",
        "pip_audit",
        "--local",
        "--format",
        "json",
        "--progress-spinner",
        "off",
    ]


def test_collect_report_summarizes_pip_inspect_and_skips_missing_pip_audit(tmp_path, monkeypatch):
    (tmp_path / "pyproject.toml").write_text("[tool.ruff]\n", encoding="utf-8")
    (tmp_path / "requirements.txt").write_text("pytest>=8\n", encoding="utf-8")

    pip_inspect = json.dumps(
        {
            "installed": [
                {"metadata": {"name": "ruff", "version": "0.15.9"}},
                {"metadata": {"name": "pytest", "version": "8.4.2"}},
            ],
            "environment": {"python_version": "3.12"},
        }
    )

    def fake_run(command, cwd, timeout_seconds):
        assert cwd == tmp_path
        if command == dependency_audit.build_pip_version_command("python"):
            return _completed(command, stdout="pip 25.3 from site-packages\n")
        if command == dependency_audit.build_pip_check_command("python"):
            return _completed(command, stdout="No broken requirements found.\n")
        if command == dependency_audit.build_pip_inspect_command("python"):
            return _completed(command, stdout=pip_inspect)
        raise AssertionError(command)

    monkeypatch.setattr(dependency_audit, "_run_command", fake_run)
    monkeypatch.setattr(dependency_audit.importlib.util, "find_spec", lambda name: None)

    report = dependency_audit.collect_report(tmp_path, python="python")

    assert [manifest["path"] for manifest in report["manifests"]] == ["pyproject.toml", "requirements.txt"]
    assert report["pip"]["version"]["status"] == "ok"
    assert report["pip"]["check"]["status"] == "ok"
    assert report["pip"]["inspect"]["package_count"] == 2
    assert report["pip"]["inspect"]["packages"] == [
        {"name": "pytest", "version": "8.4.2"},
        {"name": "ruff", "version": "0.15.9"},
    ]
    assert report["pip_audit"]["status"] == "skipped"


def test_pip_audit_findings_are_reported_as_nonzero_exit(tmp_path, monkeypatch):
    payload = {
        "dependencies": [
            {
                "name": "demo",
                "version": "1.0.0",
                "vulns": [{"id": "PYSEC-0000-0", "aliases": ["CVE-0000-0000"], "fix_versions": ["1.0.1"]}],
            }
        ]
    }

    def fake_run(command, cwd, timeout_seconds):
        return _completed(command, returncode=1, stdout=json.dumps(payload))

    monkeypatch.setattr(dependency_audit, "_run_command", fake_run)
    monkeypatch.setattr(dependency_audit.importlib.util, "find_spec", lambda name: object())

    pip_audit = dependency_audit.collect_pip_audit("python", tmp_path)

    assert pip_audit["status"] == "findings"
    assert pip_audit["finding_count"] == 1
    assert dependency_audit.audit_exit_code({"pip_audit": pip_audit}) == 1


def test_pip_audit_runtime_failure_is_recorded_without_nonzero_exit(tmp_path, monkeypatch):
    def fake_run(command, cwd, timeout_seconds):
        return _completed(command, returncode=2, stdout="", stderr="network unavailable")

    monkeypatch.setattr(dependency_audit, "_run_command", fake_run)
    monkeypatch.setattr(dependency_audit.importlib.util, "find_spec", lambda name: object())

    pip_audit = dependency_audit.collect_pip_audit("python", tmp_path)

    assert pip_audit["status"] == "failed"
    assert pip_audit["finding_count"] == 0
    assert dependency_audit.audit_exit_code({"pip_audit": pip_audit}) == 0
    assert dependency_audit.audit_exit_code({"pip_audit": pip_audit}, strict=True) == 1


def test_pip_audit_transient_parse_failure_is_retried(tmp_path, monkeypatch):
    attempts = []
    clean_payload = json.dumps({"dependencies": []})

    def fake_run(command, cwd, timeout_seconds):
        attempts.append(command)
        if len(attempts) == 1:
            return _completed(command, returncode=1, stdout="", stderr="ssl eof")
        return _completed(command, returncode=0, stdout=clean_payload, stderr="No known vulnerabilities found")

    monkeypatch.setattr(dependency_audit, "_run_command", fake_run)
    monkeypatch.setattr(dependency_audit.importlib.util, "find_spec", lambda name: object())

    pip_audit = dependency_audit.collect_pip_audit("python", tmp_path)

    assert pip_audit["status"] == "ok"
    assert pip_audit["finding_count"] == 0
    assert pip_audit["attempts"] == 2
    assert dependency_audit.audit_exit_code({"pip_audit": pip_audit}, strict=True) == 0


def test_dependency_audit_main_writes_json_report(tmp_path, monkeypatch):
    report = {"schema_version": 1, "pip_audit": {"status": "skipped"}}
    output = tmp_path / "dependency_audit.json"

    monkeypatch.setattr(dependency_audit, "collect_report", lambda: report)

    result = dependency_audit.main(["--output", str(output)])

    assert result == 0
    assert json.loads(output.read_text(encoding="utf-8")) == report


def test_dependency_audit_strict_mode_fails_on_skipped_pip_audit(tmp_path, monkeypatch):
    report = {"schema_version": 1, "pip_audit": {"status": "skipped"}}
    output = tmp_path / "dependency_audit.json"

    monkeypatch.setattr(dependency_audit, "collect_report", lambda: report)

    result = dependency_audit.main(["--strict", "--output", str(output)])

    assert result == 1
    assert json.loads(output.read_text(encoding="utf-8")) == report
