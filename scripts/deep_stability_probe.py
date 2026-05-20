# -*- coding: utf-8 -*-
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT_ROOT = PROJECT_ROOT / "tmp" / "deep_stability"


@dataclass(frozen=True)
class ProbeProfile:
    runtime_args: tuple[str, ...]
    kline_args: tuple[str, ...]
    runtime_timeout_seconds: int
    kline_timeout_seconds: int


PROFILES: dict[str, ProbeProfile] = {
    "quick": ProbeProfile(
        runtime_args=(
            "--mode",
            "short",
            "--idle-seconds",
            "5",
            "--tab-cycles",
            "1",
            "--f5-cycles",
            "1",
            "--quote-cycles",
            "1",
            "--kline-cycles",
            "1",
        ),
        kline_args=("--cycles", "3", "--open-timeout-ms", "9000", "--close-timeout-ms", "12000"),
        runtime_timeout_seconds=600,
        kline_timeout_seconds=360,
    ),
    "standard": ProbeProfile(
        runtime_args=(
            "--mode",
            "short",
            "--idle-seconds",
            "60",
            "--tab-cycles",
            "2",
            "--f5-cycles",
            "2",
            "--quote-cycles",
            "2",
            "--kline-cycles",
            "3",
        ),
        kline_args=("--cycles", "8", "--open-timeout-ms", "9000", "--close-timeout-ms", "12000"),
        runtime_timeout_seconds=1_800,
        kline_timeout_seconds=900,
    ),
    "soak30": ProbeProfile(
        runtime_args=(
            "--mode",
            "soak30",
            "--idle-minutes",
            "30",
            "--tab-cycles",
            "2",
            "--f5-cycles",
            "2",
            "--quote-cycles",
            "2",
            "--kline-cycles",
            "5",
        ),
        kline_args=("--cycles", "10", "--open-timeout-ms", "9000", "--close-timeout-ms", "12000"),
        runtime_timeout_seconds=3_600,
        kline_timeout_seconds=1_200,
    ),
    "soak60": ProbeProfile(
        runtime_args=(
            "--mode",
            "soak60",
            "--idle-minutes",
            "60",
            "--tab-cycles",
            "3",
            "--f5-cycles",
            "3",
            "--quote-cycles",
            "3",
            "--kline-cycles",
            "8",
        ),
        kline_args=("--cycles", "12", "--open-timeout-ms", "9000", "--close-timeout-ms", "12000"),
        runtime_timeout_seconds=7_200,
        kline_timeout_seconds=1_500,
    ),
}


def _safe_name(value: str) -> str:
    return re.sub(r"[^0-9A-Za-z_.-]+", "_", value).strip("_") or "probe"


def _json_load(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _run_command(
    command: list[str],
    *,
    label: str,
    output_dir: Path,
    timeout_seconds: int,
) -> dict[str, Any]:
    started = datetime.now()
    started_perf = time.perf_counter()
    stdout_path = output_dir / f"{_safe_name(label)}.stdout.txt"
    stderr_path = output_dir / f"{_safe_name(label)}.stderr.txt"
    result: dict[str, Any] = {
        "label": label,
        "command": command,
        "started_at": started.strftime("%Y-%m-%d %H:%M:%S"),
        "timeout_seconds": int(timeout_seconds),
        "stdout_path": str(stdout_path),
        "stderr_path": str(stderr_path),
    }
    try:
        completed = subprocess.run(
            command,
            cwd=PROJECT_ROOT,
            text=True,
            capture_output=True,
            timeout=max(1, int(timeout_seconds)),
        )
        result["returncode"] = int(completed.returncode)
        result["timed_out"] = False
        _write_text(stdout_path, completed.stdout or "")
        _write_text(stderr_path, completed.stderr or "")
    except subprocess.TimeoutExpired as exc:
        result["returncode"] = None
        result["timed_out"] = True
        _write_text(stdout_path, exc.stdout or "")
        _write_text(stderr_path, exc.stderr or f"Timed out after {timeout_seconds} seconds.")

    result["elapsed_seconds"] = round(time.perf_counter() - started_perf, 3)
    result["finished_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    return result


def _powershell_json(script: str, timeout_seconds: int = 30) -> Any:
    command = [
        "powershell",
        "-NoProfile",
        "-ExecutionPolicy",
        "Bypass",
        "-Command",
        "[Console]::OutputEncoding=[System.Text.Encoding]::UTF8; " + script,
    ]
    try:
        completed = subprocess.run(
            command,
            cwd=PROJECT_ROOT,
            text=True,
            capture_output=True,
            timeout=timeout_seconds,
        )
    except (OSError, subprocess.TimeoutExpired):
        return []
    if completed.returncode != 0 or not completed.stdout.strip():
        return []
    try:
        data = json.loads(completed.stdout)
    except json.JSONDecodeError:
        return []
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        return [data]
    return []


def collect_windows_crash_events(start: datetime, end: datetime | None = None) -> list[dict[str, Any]]:
    end = end or datetime.now()
    start_text = start.strftime("%Y-%m-%dT%H:%M:%S")
    end_text = end.strftime("%Y-%m-%dT%H:%M:%S")
    script = rf"""
$start = [datetime]::Parse('{start_text}')
$end = [datetime]::Parse('{end_text}')
$events = Get-WinEvent -FilterHashtable @{{LogName='Application'; StartTime=$start; EndTime=$end}} -ErrorAction SilentlyContinue |
  Where-Object {{ $_.ProviderName -in @('Application Error','Windows Error Reporting') -or $_.Message -match 'pythonw|python.exe|Qt6|QtWebEngine|0xc0000409|0xc0000005|BEX64' }} |
  Sort-Object TimeCreated |
  Select-Object TimeCreated, Id, ProviderName, Message
$events | ConvertTo-Json -Depth 5 -Compress
"""
    events = _powershell_json(script)
    normalized: list[dict[str, Any]] = []
    for event in events:
        if not isinstance(event, dict):
            continue
        message = str(event.get("Message") or "")
        normalized.append(
            {
                "time_created": str(event.get("TimeCreated") or ""),
                "id": event.get("Id"),
                "provider": str(event.get("ProviderName") or ""),
                "summary": _event_summary(message),
                "message": message,
                "report_archive": _extract_report_archive(message),
            }
        )
    return normalized


def _event_summary(message: str) -> str:
    lines = [line.strip() for line in str(message or "").splitlines() if line.strip()]
    if not lines:
        return ""
    interesting = [
        line
        for line in lines
        if "Faulting application" in line
        or "Faulting module" in line
        or "Exception code" in line
        or "Event Name:" in line
        or "P1:" in line
        or "P4:" in line
        or "P8:" in line
    ]
    return " | ".join(interesting[:4]) if interesting else lines[0]


def _extract_report_archive(message: str) -> str:
    marker = "These files may be available here:"
    if marker not in message:
        return ""
    tail = message.split(marker, 1)[1]
    for line in tail.splitlines():
        text = line.strip()
        if text:
            return text
    return ""


def _latest_log_tails(lines: int) -> list[dict[str, Any]]:
    outputs: list[dict[str, Any]] = []
    candidates = sorted((PROJECT_ROOT / "data" / "logs").glob("vcp_*.log"), key=lambda path: path.stat().st_mtime)
    if (PROJECT_ROOT / "data" / "crash_report.log").exists():
        candidates.append(PROJECT_ROOT / "data" / "crash_report.log")
    for path in candidates[-3:]:
        try:
            text_lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            continue
        outputs.append(
            {
                "path": str(path),
                "mtime": datetime.fromtimestamp(path.stat().st_mtime).strftime("%Y-%m-%d %H:%M:%S"),
                "tail": text_lines[-max(0, int(lines)) :],
            }
        )
    return outputs


def build_commands(args: argparse.Namespace, output_dir: Path) -> dict[str, list[str]]:
    profile = PROFILES[args.profile]
    python = str(args.python)
    common_kline = ("--native-qt",) if args.native_qt else ()
    runtime_report = output_dir / "runtime_health_stability_suite.json"
    runtime_samples = output_dir / "runtime_health_samples"
    runtime_command = [
        python,
        "scripts/runtime_health_stability_suite.py",
        *common_kline,
        *profile.runtime_args,
        "--kline-code",
        args.kline_code,
        "--kline-name",
        args.kline_name,
        "--output",
        str(runtime_report),
        "--sample-output-dir",
        str(runtime_samples),
    ]
    kline_report = output_dir / "kline_webengine_lifecycle_smoke.json"
    kline_command = [
        python,
        "scripts/kline_webengine_lifecycle_smoke.py",
        *common_kline,
        *profile.kline_args,
        "--code",
        args.kline_code,
        "--name",
        args.kline_name,
        "--output",
        str(kline_report),
        "--fail-on-error",
    ]
    return {"runtime_health": runtime_command, "kline_lifecycle": kline_command}


def _step_status(step: dict[str, Any], report: dict[str, Any] | None = None) -> str:
    if step.get("timed_out"):
        return "timeout"
    if step.get("returncode") not in (0, None):
        return "crash_or_error"
    if report and report.get("status") == "fail":
        return "budget_fail"
    return "ok"


def _overall_status(steps: list[dict[str, Any]], crash_events: list[dict[str, Any]], reports: dict[str, dict]) -> str:
    if crash_events:
        return "crash_detected"
    if any(step.get("timed_out") for step in steps):
        return "timeout"
    if any(step.get("returncode") not in (0, None) for step in steps):
        return "child_process_failed"
    if any(report.get("status") == "fail" for report in reports.values()):
        return "budget_or_probe_failed"
    return "ok"


def _short_dict_value(data: dict[str, Any], *keys: str) -> Any:
    value: Any = data
    for key in keys:
        if not isinstance(value, dict):
            return None
        value = value.get(key)
    return value


def build_markdown_report(report: dict[str, Any]) -> str:
    lines: list[str] = []
    lines.append(f"# 深度稳定性测试报告")
    lines.append("")
    lines.append(f"- 状态: `{report.get('status')}`")
    lines.append(f"- 模式: `{_short_dict_value(report, 'mode', 'profile')}`")
    lines.append(f"- 开始: `{report.get('started_at')}`")
    lines.append(f"- 结束: `{report.get('finished_at')}`")
    lines.append(f"- 输出目录: `{report.get('output_dir')}`")
    lines.append("")
    lines.append("## 子测试")
    lines.append("")
    lines.append("| 子测试 | 状态 | 退出码 | 耗时 |")
    lines.append("|---|---:|---:|---:|")
    reports = report.get("child_reports") or {}
    for step in report.get("steps") or []:
        child_report = reports.get(step.get("label")) if isinstance(reports, dict) else {}
        status = _step_status(step, child_report if isinstance(child_report, dict) else {})
        lines.append(
            f"| {step.get('label')} | `{status}` | `{step.get('returncode')}` | `{step.get('elapsed_seconds')}s` |"
        )
    runtime = reports.get("runtime_health") if isinstance(reports, dict) else {}
    if isinstance(runtime, dict) and runtime:
        lines.extend(_runtime_health_markdown(runtime))
    kline = reports.get("kline_lifecycle") if isinstance(reports, dict) else {}
    if isinstance(kline, dict) and kline:
        lines.extend(_kline_markdown(kline))
    lines.append("")
    lines.append("## Windows 崩溃事件")
    lines.append("")
    crash_events = report.get("windows_crash_events") or []
    if not crash_events:
        lines.append("未发现本次测试窗口内新的 Python/Qt/QtWebEngine 崩溃事件。")
    else:
        for event in crash_events:
            lines.append(
                f"- `{event.get('time_created')}` `{event.get('provider')}` `{event.get('id')}`: "
                f"{event.get('summary')}"
            )
            if event.get("report_archive"):
                lines.append(f"  - WER: `{event.get('report_archive')}`")
    lines.append("")
    lines.append("## 结论使用方式")
    lines.append("")
    lines.append("- `quick` 可以立刻跑，主要验证脚本、F5、主要标签页和 K 线关闭链路没有明显崩溃。")
    lines.append("- `standard` 更适合日常改完代码后的稳定性回归。")
    lines.append("- `soak30` / `soak60` 才能更好暴露长时间运行后的低概率闪退、线程泄漏和 WebEngine 残留。")
    return "\n".join(lines) + "\n"


def _runtime_health_markdown(runtime: dict[str, Any]) -> list[str]:
    lines = ["", "## Runtime Health 摘要", ""]
    tab_cycle = runtime.get("tab_cycle") or {}
    f5_cycle = runtime.get("f5_cycle") or {}
    quote_cycle = runtime.get("quote_cycle") or {}
    kline_cycle = runtime.get("kline_cycle") or {}
    budget = runtime.get("budget") or {}
    trend = runtime.get("budget_trend") or runtime.get("trend") or {}
    lines.append(f"- 标签页循环: `{tab_cycle.get('status')}`，访问 `{tab_cycle.get('visited')}` 次")
    lines.append(f"- F5 循环: `{f5_cycle.get('status')}`，次数 `{f5_cycle.get('cycles')}`")
    lines.append(f"- 行情循环: `{quote_cycle.get('status')}`，次数 `{quote_cycle.get('cycles')}`")
    lines.append(
        f"- K 线循环: `{kline_cycle.get('status')}`，打开 `{kline_cycle.get('opened')}`，"
        f"关闭 `{kline_cycle.get('closed')}`，最终 WebEngine 子进程 `{kline_cycle.get('final_webengine_child_count')}`"
    )
    lines.append(f"- 预算状态: `{budget.get('status')}`")
    for key in ("threads", "background_tasks", "active_timers", "webengine_children"):
        item = trend.get(key) or {}
        if item:
            lines.append(
                f"- {key}: first `{item.get('first')}` -> last `{item.get('last')}`，"
                f"delta `{item.get('net_delta')}`，basis `{item.get('basis', '')}`"
            )
    failures = budget.get("failures") or []
    if failures:
        lines.append("")
        lines.append("预算/健康检查失败项：")
        for failure in failures[:12]:
            detail = failure.get("message") or failure.get("detail") or ""
            actual = failure.get("actual")
            budget_value = failure.get("budget")
            suffix = ""
            if actual is not None or budget_value is not None:
                suffix = f" actual=`{actual}` budget=`{budget_value}`"
            lines.append(f"- `{failure.get('check')}`: {detail}{suffix}")
    return lines


def _kline_markdown(kline: dict[str, Any]) -> list[str]:
    summary = kline.get("summary") or {}
    return [
        "",
        "## K 线生命周期摘要",
        "",
        f"- 状态: `{summary.get('status')}`",
        f"- 循环: `{summary.get('cycles')}`，成功 `{summary.get('ok_cycles')}`，失败 `{summary.get('failed_cycles')}`",
        f"- WebEngine 子进程峰值: `{summary.get('max_webengine_child_count')}`",
        f"- 最终 WebEngine 子进程: `{summary.get('final_webengine_child_count')}`",
        f"- 子进程回收: `{summary.get('webengine_child_reclaimed')}`",
    ]


def run_deep_stability_probe(args: argparse.Namespace) -> dict[str, Any]:
    started = datetime.now()
    stamp = started.strftime("%Y%m%d_%H%M%S")
    output_dir = Path(args.output_dir or DEFAULT_OUTPUT_ROOT / f"{args.profile}_{stamp}").resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    commands = build_commands(args, output_dir)
    profile = PROFILES[args.profile]
    timeout_scale = max(0.1, float(args.timeout_scale))
    steps: list[dict[str, Any]] = []

    runtime_step = _run_command(
        commands["runtime_health"],
        label="runtime_health",
        output_dir=output_dir,
        timeout_seconds=int(profile.runtime_timeout_seconds * timeout_scale),
    )
    steps.append(runtime_step)

    if not args.skip_kline_lifecycle:
        kline_step = _run_command(
            commands["kline_lifecycle"],
            label="kline_lifecycle",
            output_dir=output_dir,
            timeout_seconds=int(profile.kline_timeout_seconds * timeout_scale),
        )
        steps.append(kline_step)

    finished = datetime.now()
    json_report_path = output_dir / "deep_stability_report.json"
    markdown_report_path = output_dir / "deep_stability_report.md"
    child_reports = {
        "runtime_health": _json_load(output_dir / "runtime_health_stability_suite.json"),
        "kline_lifecycle": _json_load(output_dir / "kline_webengine_lifecycle_smoke.json"),
    }
    if args.skip_kline_lifecycle:
        child_reports.pop("kline_lifecycle", None)
    crash_events = collect_windows_crash_events(started, finished)
    report = {
        "schema_version": 1,
        "report_type": "deep_stability_probe",
        "started_at": started.strftime("%Y-%m-%d %H:%M:%S"),
        "finished_at": finished.strftime("%Y-%m-%d %H:%M:%S"),
        "elapsed_seconds": round((finished - started).total_seconds(), 3),
        "output_dir": str(output_dir),
        "json_report_path": str(json_report_path),
        "markdown_report_path": str(markdown_report_path),
        "mode": {
            "profile": args.profile,
            "native_qt": bool(args.native_qt),
            "kline_code": args.kline_code,
            "kline_name": args.kline_name,
        },
        "steps": steps,
        "child_reports": child_reports,
        "windows_crash_events": crash_events,
        "log_tails": _latest_log_tails(args.log_tail_lines),
    }
    report["status"] = _overall_status(steps, crash_events, child_reports)
    _write_json(json_report_path, report)
    _write_text(markdown_report_path, build_markdown_report(report))
    return report


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="One-click deep stability probe for the PyQt desktop app.")
    parser.add_argument("--profile", choices=tuple(PROFILES), default="quick")
    parser.add_argument("--output-dir", type=Path, default=None)
    parser.add_argument("--python", default=sys.executable)
    parser.add_argument("--kline-code", default="688072")
    parser.add_argument("--kline-name", default="拓荆科技")
    parser.add_argument("--native-qt", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--skip-kline-lifecycle", action="store_true")
    parser.add_argument("--timeout-scale", type=float, default=1.0)
    parser.add_argument("--log-tail-lines", type=int, default=120)
    parser.add_argument("--fail-on-crash", action="store_true")
    parser.add_argument("--fail-on-any-issue", action="store_true")
    return parser.parse_args(argv)


def _console_summary(report: dict[str, Any]) -> dict[str, Any]:
    steps = []
    child_reports = report.get("child_reports") or {}
    for step in report.get("steps") or []:
        child_report = child_reports.get(step.get("label")) if isinstance(child_reports, dict) else {}
        steps.append(
            {
                "label": step.get("label"),
                "status": _step_status(step, child_report if isinstance(child_report, dict) else {}),
                "returncode": step.get("returncode"),
                "elapsed_seconds": step.get("elapsed_seconds"),
            }
        )
    return {
        "status": report.get("status"),
        "profile": _short_dict_value(report, "mode", "profile"),
        "elapsed_seconds": report.get("elapsed_seconds"),
        "windows_crash_event_count": len(report.get("windows_crash_events") or []),
        "markdown_report_path": report.get("markdown_report_path"),
        "json_report_path": report.get("json_report_path"),
        "steps": steps,
    }


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv or sys.argv[1:])
    report = run_deep_stability_probe(args)
    print(json.dumps(_console_summary(report), ensure_ascii=False, indent=2))
    crash_statuses = {"crash_detected", "timeout", "child_process_failed"}
    if args.fail_on_crash and report.get("status") in crash_statuses:
        return 1
    if args.fail_on_any_issue and report.get("status") != "ok":
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
