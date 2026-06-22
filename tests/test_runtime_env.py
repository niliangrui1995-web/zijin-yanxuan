import os
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

from core.runtime_env import (
    WINDOWS_APP_USER_MODEL_ID,
    append_bootstrap_event,
    collect_runtime_env_issues,
    configure_qt_webengine_runtime,
    relaunch_into_project_venv_if_needed,
    resolve_project_python,
    set_windows_app_user_model_id,
    should_relaunch_into_project_venv,
)


def test_append_bootstrap_event_writes_early_process_evidence(tmp_path):
    log_path = append_bootstrap_event(
        str(tmp_path),
        "process.start",
        extra={
            "reason": "unit\nnewline",
            "empty": "",
            "none": None,
        },
    )

    assert log_path
    log_text = Path(log_path).read_text(encoding="utf-8")
    assert "[bootstrap] process.start" in log_text
    assert "pid=" in log_text
    assert "reason=unit\\nnewline" in log_text
    assert "empty=" not in log_text
    assert "none=" not in log_text


def test_import_runtime_env_does_not_create_main_log(tmp_path):
    env = dict(os.environ)
    env["VCP_HUNTER_LOG_DIR"] = str(tmp_path / "logs")
    env["PYTHONPATH"] = str(Path(__file__).resolve().parents[1])

    result = subprocess.run(
        [sys.executable, "-c", "import core.runtime_env"],
        capture_output=True,
        env=env,
        text=True,
        timeout=10,
    )

    assert result.returncode == 0, result.stderr
    assert not list((tmp_path / "logs").glob("vcp_*.log"))


def test_collect_runtime_env_issues_detects_project_venv_drift(tmp_path):
    preferred_python = tmp_path / ".venv" / "Scripts" / "python.exe"
    preferred_python.parent.mkdir(parents=True)
    preferred_python.write_text("", encoding="utf-8")

    with patch("core.runtime_env._is_windows", return_value=True):
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

    with patch("core.runtime_env._is_windows", return_value=True):
        issues = collect_runtime_env_issues(
            str(tmp_path),
            executable=str(preferred_pythonw),
            import_module=lambda _: object(),
            package_version=lambda _: "",
        )

    assert not any("project .venv" in issue for issue in issues)


def test_collect_runtime_env_issues_detects_requests_and_runtime_dependency_conflicts(tmp_path):
    versions = {
        "requests": "2.34.1",
        "yfinance": "1.4.0",
        "curl_cffi": "0.14.0",
        "lxml": "6.1.0",
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
        "requests": "2.34.2",
        "yfinance": "1.4.1",
        "curl_cffi": "0.15.0",
        "lxml": "6.1.1",
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

    with patch("core.runtime_env._is_windows", return_value=True):
        resolved = resolve_project_python(str(tmp_path), executable="C:/Python310/pythonw.exe")

    assert resolved == str(preferred_pythonw)


def test_relaunch_pyw_prefers_project_pythonw(tmp_path):
    preferred_python = tmp_path / ".venv" / "Scripts" / "python.exe"
    preferred_pythonw = tmp_path / ".venv" / "Scripts" / "pythonw.exe"
    preferred_python.parent.mkdir(parents=True)
    preferred_python.write_text("", encoding="utf-8")
    preferred_pythonw.write_text("", encoding="utf-8")

    exec_calls = []

    def fake_execve(executable, argv, env):
        exec_calls.append((executable, list(argv), dict(env)))

    with patch("core.runtime_env._is_windows", return_value=True):
        relaunched = relaunch_into_project_venv_if_needed(
            str(tmp_path),
            executable="C:/Python310/python.exe",
            argv=["vcp_hunter_qt.pyw"],
            env={},
            script_path=str(tmp_path / "vcp_hunter_qt.pyw"),
            execve=fake_execve,
        )

    assert relaunched is True
    assert len(exec_calls) == 1
    executable, argv, env = exec_calls[0]
    assert executable == str(preferred_pythonw)
    assert argv == [str(preferred_pythonw), str(tmp_path / "vcp_hunter_qt.pyw")]
    assert env["VCP_ALREADY_RELAUNCHED"] == "1"


def test_relaunch_pyw_switches_project_python_to_pythonw(tmp_path):
    preferred_python = tmp_path / ".venv" / "Scripts" / "python.exe"
    preferred_pythonw = tmp_path / ".venv" / "Scripts" / "pythonw.exe"
    preferred_python.parent.mkdir(parents=True)
    preferred_python.write_text("", encoding="utf-8")
    preferred_pythonw.write_text("", encoding="utf-8")

    exec_calls = []

    def fake_execve(executable, argv, env):
        exec_calls.append((executable, list(argv), dict(env)))

    with patch("core.runtime_env._is_windows", return_value=True):
        relaunched = relaunch_into_project_venv_if_needed(
            str(tmp_path),
            executable=str(preferred_python),
            argv=["vcp_hunter_qt.pyw"],
            env={},
            script_path=str(tmp_path / "vcp_hunter_qt.pyw"),
            execve=fake_execve,
        )

    assert relaunched is True
    assert exec_calls[0][0] == str(preferred_pythonw)


def test_should_relaunch_into_project_venv_skips_when_already_relaunched(tmp_path):
    preferred_python = tmp_path / ".venv" / "Scripts" / "python.exe"
    preferred_python.parent.mkdir(parents=True)
    preferred_python.write_text("", encoding="utf-8")

    with patch("core.runtime_env._is_windows", return_value=True):
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

    with patch("core.runtime_env._is_windows", return_value=True):
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


def test_configure_qt_webengine_runtime_merges_flags_without_duplicates():
    env = {
        "QTWEBENGINE_CHROMIUM_FLAGS": "--disable-gpu --user-flag",
    }

    result = configure_qt_webengine_runtime(env)

    flags = result["QTWEBENGINE_CHROMIUM_FLAGS"].split()
    assert result["QT_OPENGL"] == "software"
    assert flags.count("--disable-gpu") == 1
    assert "--disable-gpu-compositing" in flags
    assert "--disable-extensions" in flags
    assert "--disable-background-networking" in flags
    assert "--user-flag" in flags
