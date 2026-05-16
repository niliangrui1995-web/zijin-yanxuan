from scripts import runtime_env_self_check
from scripts.runtime_env_self_check import build_report


def _patch_ok_imports(monkeypatch):
    def ok_import_snapshot(module_name):
        return {"module": module_name, "ok": True, "version": ""}

    monkeypatch.setattr(runtime_env_self_check, "_import_snapshot", ok_import_snapshot)


def test_runtime_env_self_check_report_has_required_sections():
    report = build_report(skip_webengine_preflight=True)

    assert report["report_type"] == "runtime_env_self_check"
    assert report["python"]["executable"]
    assert report["app"]["version"]
    assert "PyQt6" in report["imports"]
    assert "PyQt6-WebEngine" in report["imports"]
    assert report["qt_webengine_preflight"]["skipped"] is True
    assert "tdx_vipdoc" in report
    assert "rps_cache" in report["cache_files"]
    assert "sqlite_state" in report["cache_files"]


def test_runtime_env_self_check_reports_runtime_env_warnings_without_failing(monkeypatch):
    _patch_ok_imports(monkeypatch)
    monkeypatch.setattr(
        runtime_env_self_check,
        "_collect_runtime_env_issues",
        lambda *_args, **_kwargs: ["current executable is not project .venv"],
    )

    report = build_report(skip_webengine_preflight=True)

    assert report["status"] == "ok"
    assert report["failures"] == []
    assert report["runtime_env_issues"] == [
        {
            "code": "",
            "message": "current executable is not project .venv",
            "severity": "warning",
            "hard_failure": False,
        }
    ]


def test_runtime_env_self_check_promotes_hard_runtime_env_issues_to_failures(monkeypatch):
    class FakeRuntimeEnvIssue:
        code = "runtime_env.hard"
        message = "hard runtime failure"
        severity = "error"
        hard_failure = True

    _patch_ok_imports(monkeypatch)
    monkeypatch.setattr(
        runtime_env_self_check,
        "_collect_runtime_env_issues",
        lambda *_args, **_kwargs: [FakeRuntimeEnvIssue()],
    )

    report = build_report(skip_webengine_preflight=True)

    assert report["status"] == "fail"
    assert "runtime_env.hard" in report["failures"]
    assert report["runtime_env_issues"] == [
        {
            "code": "runtime_env.hard",
            "message": "hard runtime failure",
            "severity": "error",
            "hard_failure": True,
        }
    ]
