from unittest.mock import patch

from core.runtime_env import (
    WINDOWS_APP_USER_MODEL_ID,
    collect_runtime_env_issues,
    relaunch_into_project_venv_if_needed,
    resolve_project_python,
    set_windows_app_user_model_id,
    should_relaunch_into_project_venv,
)


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

    assert any("project .venv" in issue for issue in issues)


def test_collect_runtime_env_issues_accepts_project_pythonw(tmp_path):
    preferred_python = tmp_path / ".venv" / "Scripts" / "python.exe"
    preferred_pythonw = tmp_path / ".venv" / "Scripts" / "pythonw.exe"
    preferred_python.parent.mkdir(parents=True)
    preferred_python.write_text("", encoding="utf-8")
    preferred_pythonw.write_text("", encoding="utf-8")

    issues = collect_runtime_env_issues(
        str(tmp_path),
        executable=str(preferred_pythonw),
        import_module=lambda _: object(),
        package_version=lambda _: "",
    )

    assert not any("project .venv" in issue for issue in issues)


def test_collect_runtime_env_issues_detects_requests_and_runtime_dependency_conflicts(tmp_path):
    versions = {
        "requests": "2.32.5",
        "yfinance": "1.2.0",
        "curl_cffi": "0.14.0",
        "lxml": "6.0.4",
    }

    issues = collect_runtime_env_issues(
        str(tmp_path),
        executable=str(tmp_path / "python.exe"),
        import_module=lambda _: object(),
        package_version=lambda name: versions.get(name, ""),
    )

    assert any("requests version too old" in issue for issue in issues)
    assert any("yfinance version incompatible" in issue for issue in issues)
    assert any("curl_cffi version incompatible" in issue for issue in issues)
    assert any("lxml version too old" in issue for issue in issues)


def test_collect_runtime_env_issues_accepts_security_dependency_floors(tmp_path):
    versions = {
        "requests": "2.33.0",
        "yfinance": "1.3.0",
        "curl_cffi": "0.15.0",
        "lxml": "6.1.0",
    }

    issues = collect_runtime_env_issues(
        str(tmp_path),
        executable=str(tmp_path / "python.exe"),
        import_module=lambda _: object(),
        package_version=lambda name: versions.get(name, ""),
    )

    assert not any("version incompatible" in issue for issue in issues)
    assert not any("version too old" in issue for issue in issues)


def test_resolve_project_python_prefers_pythonw_for_pythonw_callers(tmp_path):
    preferred_python = tmp_path / ".venv" / "Scripts" / "python.exe"
    preferred_pythonw = tmp_path / ".venv" / "Scripts" / "pythonw.exe"
    preferred_python.parent.mkdir(parents=True)
    preferred_python.write_text("", encoding="utf-8")
    preferred_pythonw.write_text("", encoding="utf-8")

    resolved = resolve_project_python(str(tmp_path), executable="C:/Python310/pythonw.exe")

    assert resolved == str(preferred_pythonw)


def test_should_relaunch_into_project_venv_skips_when_already_relaunched(tmp_path):
    preferred_python = tmp_path / ".venv" / "Scripts" / "python.exe"
    preferred_python.parent.mkdir(parents=True)
    preferred_python.write_text("", encoding="utf-8")

    should_relaunch = should_relaunch_into_project_venv(
        str(tmp_path),
        executable="C:/Python310/python.exe",
        env={"VCP_ALREADY_RELAUNCHED": "1"},
    )

    assert should_relaunch is False


def test_relaunch_into_project_venv_execs_target_python(tmp_path):
    preferred_python = tmp_path / ".venv" / "Scripts" / "python.exe"
    preferred_python.parent.mkdir(parents=True)
    preferred_python.write_text("", encoding="utf-8")

    exec_calls = []

    def fake_execve(executable, argv, env):
        exec_calls.append((executable, list(argv), dict(env)))

    relaunched = relaunch_into_project_venv_if_needed(
        str(tmp_path),
        executable="C:/Python310/python.exe",
        argv=["vcp_hunter_qt.pyw", "--flag"],
        env={},
        script_path=str(tmp_path / "vcp_hunter_qt.pyw"),
        execve=fake_execve,
    )

    assert relaunched is True
    assert len(exec_calls) == 1
    executable, argv, env = exec_calls[0]
    assert executable == str(preferred_python)
    assert argv == [
        str(preferred_python),
        str(tmp_path / "vcp_hunter_qt.pyw"),
        "--flag",
    ]
    assert env["VCP_ALREADY_RELAUNCHED"] == "1"


def test_set_windows_app_user_model_id_calls_windows_api():
    calls = []

    class FakeShell32:
        def SetCurrentProcessExplicitAppUserModelID(self, value):
            calls.append(value)
            return 0

    with patch("core.runtime_env._is_windows", return_value=True):
        applied = set_windows_app_user_model_id(shell32=FakeShell32())

    assert applied is True
    assert calls == [WINDOWS_APP_USER_MODEL_ID]


def test_set_windows_app_user_model_id_handles_windows_api_failure():
    class FakeShell32:
        def SetCurrentProcessExplicitAppUserModelID(self, value):
            return 5

    with patch("core.runtime_env._is_windows", return_value=True):
        applied = set_windows_app_user_model_id(shell32=FakeShell32())

    assert applied is False
