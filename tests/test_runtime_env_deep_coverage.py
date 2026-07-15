from __future__ import annotations

from types import SimpleNamespace

from core import runtime_env as module


def test_numeric_versions_and_package_helpers(monkeypatch):
    env = {"VCP_NUMERIC_THREAD_COUNT": "   "}
    assert module.configure_numeric_thread_runtime(env)["OMP_NUM_THREADS"] == module.DEFAULT_NUMERIC_THREAD_COUNT
    assert module._parse_version_tuple("release") == ()
    assert module._parse_version_tuple("v1.2.3.4.5") == (1, 2, 3, 4)
    assert module._version_lt("1.0", "2.0")
    assert module._version_ge("2.0", "2.0")
    monkeypatch.setattr(
        module.metadata, "version", lambda _name: (_ for _ in ()).throw(module.metadata.PackageNotFoundError)
    )
    assert module._safe_package_version("missing") == ""


def test_windows_app_id_non_windows_empty_argtypes_and_error(monkeypatch):
    monkeypatch.setattr(module, "_is_windows", lambda: False)
    assert module.set_windows_app_user_model_id() is False
    monkeypatch.setattr(module, "_is_windows", lambda: True)
    assert module.set_windows_app_user_model_id("   ") is False

    class Callable:
        argtypes = None
        restype = None

        def __init__(self, result=0):
            self.result = result

        def __call__(self, value):
            self.value = value
            return self.result

    call = Callable()
    assert module.set_windows_app_user_model_id(
        "app.id", shell32=SimpleNamespace(SetCurrentProcessExplicitAppUserModelID=call)
    )
    assert call.argtypes is not None and call.restype is not None and call.value == "app.id"
    assert not module.set_windows_app_user_model_id(
        "app.id",
        shell32=SimpleNamespace(
            SetCurrentProcessExplicitAppUserModelID=lambda _value: (_ for _ in ()).throw(OSError("bad"))
        ),
    )


def test_project_python_candidates_and_resolution_on_non_windows(monkeypatch, tmp_path):
    python = tmp_path / ".venv" / "bin" / "python"
    python.parent.mkdir(parents=True)
    python.write_text("", encoding="utf-8")
    monkeypatch.setattr(module, "_is_windows", lambda: False)
    assert module.project_venv_python_candidates(str(tmp_path)) == [str(python)]
    assert module.resolve_project_python(str(tmp_path), executable="/usr/bin/python") == str(python)
    python.unlink()
    assert module.project_venv_python_candidates(str(tmp_path)) == []
    assert module.resolve_project_python(str(tmp_path)) == ""


def test_should_relaunch_all_guard_paths(monkeypatch, tmp_path):
    python = tmp_path / ".venv" / "Scripts" / "python.exe"
    python.parent.mkdir(parents=True)
    python.write_text("", encoding="utf-8")
    monkeypatch.setattr(module, "_is_windows", lambda: True)
    assert not module.should_relaunch_into_project_venv(str(tmp_path), env={"VCP_SKIP_VENV_RELAUNCH": "1"})
    assert not module.should_relaunch_into_project_venv(str(tmp_path), env={"VCP_ALREADY_RELAUNCHED": "1"})
    assert module.should_relaunch_into_project_venv(str(tmp_path), executable="C:/other/python.exe", env={})
    assert not module.should_relaunch_into_project_venv(str(tmp_path), executable=str(python), env={})
    python.unlink()
    assert not module.should_relaunch_into_project_venv(str(tmp_path), executable="C:/other/python.exe", env={})


def test_bootstrap_event_error_and_value_format(monkeypatch, tmp_path):
    assert module._format_bootstrap_value("a\r\nb") == "a\\r\\nb"
    monkeypatch.setattr(module.os, "makedirs", lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("denied")))
    assert module.append_bootstrap_event(str(tmp_path), "event") == ""


def test_relaunch_guard_and_missing_target_paths(monkeypatch, tmp_path):
    assert not module.relaunch_into_project_venv_if_needed(str(tmp_path), argv=[], script_path="")
    monkeypatch.setattr(module, "should_relaunch_into_project_venv", lambda *_args, **_kwargs: False)
    assert not module.relaunch_into_project_venv_if_needed(str(tmp_path), argv=["app.py"], script_path="app.py")
    monkeypatch.setattr(module, "should_relaunch_into_project_venv", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(module, "resolve_project_python", lambda *_args, **_kwargs: "")
    assert not module.relaunch_into_project_venv_if_needed(str(tmp_path), argv=["app.py"], script_path="app.py")


def test_relaunch_merges_explicit_environment(monkeypatch, tmp_path):
    target = str(tmp_path / "python.exe")
    monkeypatch.setattr(module, "should_relaunch_into_project_venv", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(module, "resolve_project_python", lambda *_args, **_kwargs: target)
    monkeypatch.setattr(module, "append_bootstrap_event", lambda *_args, **_kwargs: "log")
    calls = []
    result = module.relaunch_into_project_venv_if_needed(
        str(tmp_path),
        executable="C:/old/python.exe",
        argv=["app.py", "--flag"],
        env={"CUSTOM": "yes"},
        script_path=str(tmp_path / "app.py"),
        execve=lambda *args: calls.append(args),
    )
    assert result is True
    assert calls[0][0] == target
    assert calls[0][1] == [target, str(tmp_path / "app.py"), "--flag"]
    assert calls[0][2]["CUSTOM"] == "yes"


def test_collect_runtime_env_windows_import_failures_and_alternate_curl(monkeypatch, tmp_path):
    python = tmp_path / ".venv" / "Scripts" / "python.exe"
    python.parent.mkdir(parents=True)
    python.write_text("", encoding="utf-8")
    monkeypatch.setattr(module, "_is_windows", lambda: True)
    versions = {"requests": "", "yfinance": "", "curl_cffi": "", "curl-cffi": "0.16.0", "lxml": ""}
    issues = module.collect_runtime_env_issues(
        str(tmp_path),
        executable=str(python),
        import_module=lambda name: (_ for _ in ()).throw(ImportError(name)),
        package_version=lambda name: versions.get(name, ""),
    )
    assert sum("missing runtime dependency" in issue for issue in issues) == len(module._WINDOWS_RUNTIME_MODULES)
    assert any("curl_cffi version incompatible" in issue for issue in issues)


class _Logger:
    def __init__(self):
        self.info_messages = []
        self.warning_messages = []

    def info(self, message):
        self.info_messages.append(message)

    def warning(self, message):
        self.warning_messages.append(message)


def test_runtime_report_logs_warning_and_success(monkeypatch):
    logger = _Logger()
    monkeypatch.setattr(module, "_runtime_log", lambda: logger)
    monkeypatch.setattr(module, "collect_runtime_env_issues", lambda _root: ["first", "second"])
    assert module.log_runtime_env_report("root") == ["first", "second"]
    assert len(logger.warning_messages) == 2

    monkeypatch.setattr(module, "collect_runtime_env_issues", lambda _root: [])
    assert module.log_runtime_env_report("root") == []
    assert logger.info_messages
