# -*- coding: utf-8 -*-
from __future__ import annotations

from types import SimpleNamespace

from scripts import deep_stability_probe


def test_deep_stability_quick_profile_builds_native_commands(tmp_path):
    args = deep_stability_probe._parse_args(
        [
            "--profile",
            "quick",
            "--python",
            "python",
            "--output-dir",
            str(tmp_path),
        ]
    )

    commands = deep_stability_probe.build_commands(args, tmp_path)

    runtime = commands["runtime_health"]
    kline = commands["kline_lifecycle"]
    assert runtime[:3] == ["python", "scripts/runtime_health_stability_suite.py", "--native-qt"]
    assert "--f5-cycles" in runtime
    assert "--tab-cycles" in runtime
    assert "--kline-cycles" in runtime
    assert "--sample-output-dir" in runtime
    assert kline[:3] == ["python", "scripts/kline_webengine_lifecycle_smoke.py", "--native-qt"]
    assert "--fail-on-error" in kline
    assert "688072" in kline


def test_deep_stability_standard_profile_uses_longer_workload(tmp_path):
    args = deep_stability_probe._parse_args(
        [
            "--profile",
            "standard",
            "--python",
            "python",
            "--output-dir",
            str(tmp_path),
        ]
    )

    commands = deep_stability_probe.build_commands(args, tmp_path)

    runtime = commands["runtime_health"]
    kline = commands["kline_lifecycle"]
    assert runtime[runtime.index("--idle-seconds") + 1] == "60"
    assert runtime[runtime.index("--f5-cycles") + 1] == "2"
    assert kline[kline.index("--cycles") + 1] == "8"


def test_deep_stability_markdown_reports_crash_events():
    report = {
        "status": "crash_detected",
        "started_at": "2026-05-20 18:00:00",
        "finished_at": "2026-05-20 18:05:00",
        "output_dir": "tmp/deep_stability/quick_20260520_180000",
        "mode": {"profile": "quick"},
        "steps": [
            {"label": "runtime_health", "returncode": 0, "elapsed_seconds": 12.3, "timed_out": False},
        ],
        "child_reports": {
            "runtime_health": {
                "status": "ok",
                "tab_cycle": {"status": "ok", "visited": 12},
                "f5_cycle": {"status": "ok", "cycles": 1},
                "quote_cycle": {"status": "ok", "cycles": 1},
                "kline_cycle": {"status": "ok", "opened": 1, "closed": 1, "final_webengine_child_count": 0},
                "budget": {"status": "ok", "failures": []},
            }
        },
        "windows_crash_events": [
            {
                "time_created": "2026-05-20T18:01:02",
                "provider": "Application Error",
                "id": 1000,
                "summary": "Faulting module name: Qt6Core.dll | Exception code: 0xc0000409",
                "report_archive": "C:\\ProgramData\\Microsoft\\Windows\\WER\\ReportArchive\\AppCrash_pythonw.exe",
            }
        ],
    }

    text = deep_stability_probe.build_markdown_report(report)

    assert "crash_detected" in text
    assert "Qt6Core.dll" in text
    assert "Runtime Health 摘要" in text
    assert "WER" in text


def test_deep_stability_overall_status_prioritizes_crash_events():
    status = deep_stability_probe._overall_status(
        [{"label": "runtime_health", "returncode": 0, "timed_out": False}],
        [{"provider": "Application Error"}],
        {"runtime_health": {"status": "ok"}},
    )

    assert status == "crash_detected"


def test_deep_stability_step_status_marks_budget_failure():
    step = {"returncode": 0, "timed_out": False}

    assert deep_stability_probe._step_status(step, {"status": "fail"}) == "budget_fail"


def test_deep_stability_console_summary_keeps_report_paths():
    summary = deep_stability_probe._console_summary(
        {
            "status": "budget_or_probe_failed",
            "elapsed_seconds": 12.0,
            "markdown_report_path": "tmp/report.md",
            "json_report_path": "tmp/report.json",
            "mode": {"profile": "quick"},
            "windows_crash_events": [],
            "steps": [{"label": "runtime_health", "returncode": 0, "timed_out": False, "elapsed_seconds": 1.2}],
            "child_reports": {"runtime_health": {"status": "fail"}},
        }
    )

    assert summary["status"] == "budget_or_probe_failed"
    assert summary["windows_crash_event_count"] == 0
    assert summary["markdown_report_path"] == "tmp/report.md"
    assert summary["steps"][0]["status"] == "budget_fail"


def test_deep_stability_collects_window_events_from_json(monkeypatch):
    monkeypatch.setattr(
        deep_stability_probe,
        "_powershell_json",
        lambda _script: [
            {
                "TimeCreated": "2026-05-20T17:32:11",
                "Id": 1000,
                "ProviderName": "Application Error",
                "Message": "Faulting application name: pythonw.exe\nFaulting module name: Qt6Core.dll\nException code: 0xc0000409",
            }
        ],
    )

    events = deep_stability_probe.collect_windows_crash_events(
        SimpleNamespace(strftime=lambda _fmt: "2026-05-20T17:30:00"),
        SimpleNamespace(strftime=lambda _fmt: "2026-05-20T17:40:00"),
    )

    assert events[0]["provider"] == "Application Error"
    assert "Qt6Core.dll" in events[0]["summary"]
