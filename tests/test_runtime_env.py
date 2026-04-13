from core.runtime_env import collect_runtime_env_issues


def test_collect_runtime_env_issues_detects_project_venv_drift(tmp_path):
    preferred_python = tmp_path / ".venv" / "Scripts" / "python.exe"
    preferred_python.parent.mkdir(parents=True)
    preferred_python.write_text("", encoding="utf-8")

    issues = collect_runtime_env_issues(
        str(tmp_path),
        executable=str(tmp_path / "other_python.exe"),
        import_module=lambda _: object(),
        package_version=lambda _: "",
    )

    assert any("项目 .venv" in issue for issue in issues)


def test_collect_runtime_env_issues_detects_requests_and_curl_conflict(tmp_path):
    versions = {
        "requests": "2.32.5",
        "yfinance": "1.2.0",
        "curl_cffi": "0.14.0",
    }

    issues = collect_runtime_env_issues(
        str(tmp_path),
        executable=str(tmp_path / "python.exe"),
        import_module=lambda _: object(),
        package_version=lambda name: versions.get(name, ""),
    )

    assert any("requests 版本过低" in issue for issue in issues)
    assert any("curl_cffi 与 yfinance 1.2.x 不兼容" in issue for issue in issues)
