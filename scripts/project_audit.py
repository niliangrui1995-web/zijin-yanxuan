# -*- coding: utf-8 -*-
from __future__ import annotations

import argparse
import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

REPO_ROOT = Path(__file__).resolve().parents[1]

PYTHON_TARGETS = (
    "app",
    "core",
    "domains",
    "infra",
    "ui",
    "vcp",
    "earnings",
    "scripts",
    "tests",
)

PERF_REPORT_OPTIONS = (
    "gbbq_report",
    "tab_report",
    "kline_report",
    "kline_lifecycle_report",
    "soak_report",
    "round4_report",
    "round5_report",
    "runtime_health_report",
)

RUNTIME_HEALTH_SHORT_OUTPUT = "tmp/runtime_health_stability_short.json"
RUNTIME_HEALTH_SHORT_SAMPLE_OUTPUT_DIR = "tmp/runtime_health_stability_short_samples"
COMPLEXITY_HOTSPOT_AUDIT_OUTPUT = "tmp/complexity_hotspot_audit.json"
DEPENDENCY_AUDIT_OUTPUT = "tmp/dependency_audit.json"
HTTP_SAFETY_AUDIT_OUTPUT = "tmp/http_safety_audit.json"
COVERAGE_REPORT_OUTPUT = "tmp/coverage.json"
TYPE_CHECK_TARGETS = (
    "app/services/asian_market_service.py",
    "app/services/http_client_service.py",
    "app/services/ui_diagnostics_service.py",
    "domains/quotes/dispatcher.py",
    "domains/quotes/snapshot.py",
    "domains/quotes/tdx_name_map.py",
    "domains/runtime/fault_tolerance.py",
    "infra/features/service_toggle_registry.py",
    "infra/http_safety.py",
    "infra/market_data/asian_kline_provider.py",
    "infra/market_data/provider_ports.py",
    "infra/market_data/yfinance_session.py",
    "infra/tasks/typed_task_registry.py",
    "infra/tasks/process_runner.py",
)

UI_STALL_SMOKE_TESTS = (
    "tests/test_workspace_quote_codes.py::test_workspace_collect_stock_context_schedules_lhb_snapshot_without_blocking",
    "tests/test_workspace_quote_codes.py::test_workspace_fund_holding_context_schedules_snapshot_without_blocking",
    "tests/test_workspace_quote_codes.py::test_workspace_background_prewarm_primes_context_without_forcing_current_tab",
    "tests/test_workspace_quote_codes.py::test_workspace_background_prewarm_can_preload_whitelisted_current_tab",
    "tests/test_workspace_quote_codes.py::test_workspace_background_prewarm_creates_whitelisted_current_tab_first_without_restore",
    "tests/test_stock_candidate_tab.py::test_stock_candidate_refresh_collects_context_without_lhb_compute",
    "tests/test_fund_holdings_tab.py::test_fund_holdings_update_event_reloads_current_scope_async",
)

EXTENDED_RUFF_SELECT = (
    "B006",
    "B011",
    "B013",
    "B014",
    "B015",
    "B017",
    "B020",
    "B026",
    "B904",
    "SIM101",
    "SIM109",
    "SIM115",
    "C400",
    "C401",
    "C402",
    "C404",
    "C405",
    "C409",
    "C410",
    "C411",
    "C413",
    "C415",
    "C416",
    "C417",
    "C418",
    "C419",
    "RUF006",
    "RUF007",
    "RUF008",
    "RUF015",
    "RUF016",
    "RUF017",
    "RUF018",
    "RUF019",
    "RUF020",
    "RUF021",
    "RUF024",
    "RUF026",
    "RUF028",
    "RUF030",
    "RUF032",
    "RUF033",
    "RUF034",
    "RUF040",
    "RUF041",
    "RUF043",
    "RUF048",
    "RUF049",
    "RUF053",
    "RUF057",
    "RUF058",
    "RUF060",
    "RUF064",
)


@dataclass(frozen=True)
class AuditCommand:
    label: str
    command: list[str]


def _python(args: argparse.Namespace) -> str:
    return str(args.python or sys.executable)


def _has_perf_reports(args: argparse.Namespace) -> bool:
    return any(getattr(args, name) for name in PERF_REPORT_OPTIONS)


def build_audit_commands(args: argparse.Namespace) -> list[AuditCommand]:
    python = _python(args)
    commands = [
        AuditCommand("ruff", [python, "-m", "ruff", "check", "."]),
        AuditCommand("utf8", [python, "scripts/check_utf8.py"]),
        AuditCommand("git-diff-check", ["git", "diff", "--check"]),
        AuditCommand("compileall", [python, "-m", "compileall", "-q", *PYTHON_TARGETS]),
        AuditCommand("pip-check", [python, "-m", "pip", "check"]),
        AuditCommand(
            "architecture-boundaries",
            [python, "-m", "pytest", "-q", "tests/test_architecture_boundaries.py"],
        ),
        AuditCommand(
            "ui-stall-smoke",
            [python, "-m", "pytest", "-q", *UI_STALL_SMOKE_TESTS],
        ),
        AuditCommand(
            "complexity-hotspots",
            [python, "scripts/complexity_hotspot_audit.py", "--output", COMPLEXITY_HOTSPOT_AUDIT_OUTPUT],
        ),
        AuditCommand(
            "http-safety-audit",
            [python, "scripts/http_safety_audit.py", "--output", HTTP_SAFETY_AUDIT_OUTPUT],
        ),
    ]
    if args.skip_ruff:
        commands = [command for command in commands if command.label != "ruff"]

    if args.extended_ruff:
        commands.append(
            AuditCommand(
                "extended-ruff",
                [
                    python,
                    "-m",
                    "ruff",
                    "check",
                    *PYTHON_TARGETS,
                    "--select",
                    ",".join(EXTENDED_RUFF_SELECT),
                ],
            )
        )

    if not args.quick:
        commands = [command for command in commands if command.label != "ui-stall-smoke"]

    if not args.quick and not args.skip_full_pytest:
        commands.append(AuditCommand("full-pytest", [python, "-m", "pytest", "-q"]))

    if not args.skip_runtime_self_check:
        runtime_command = [python, "scripts/runtime_env_self_check.py"]
        if args.quick or args.skip_webengine_preflight:
            runtime_command.append("--skip-webengine-preflight")
        commands.append(AuditCommand("runtime-self-check", runtime_command))

    if args.runtime_health_short:
        commands.append(
            AuditCommand(
                "runtime-health-short",
                [
                    python,
                    "scripts/runtime_health_stability_suite.py",
                    "--mode",
                    "short",
                    "--fail-on-budget",
                    "--output",
                    RUNTIME_HEALTH_SHORT_OUTPUT,
                    "--sample-output-dir",
                    RUNTIME_HEALTH_SHORT_SAMPLE_OUTPUT_DIR,
                ],
            )
        )

    if args.ui_stall_budget:
        commands.append(
            AuditCommand(
                "ui-stall-budget",
                [python, "scripts/capture_ui_audit_screenshots.py", "--offscreen", "--strict"],
            )
        )

    if args.dependency_audit:
        commands.append(
            AuditCommand(
                "dependency-audit",
                [python, "scripts/dependency_audit.py", "--strict", "--output", DEPENDENCY_AUDIT_OUTPUT],
            )
        )

    if args.type_check:
        commands.append(AuditCommand("type-check", [python, "-m", "pyright", *TYPE_CHECK_TARGETS]))

    if args.coverage_report:
        commands.append(
            AuditCommand(
                "coverage-report",
                [
                    python,
                    "-m",
                    "pytest",
                    "-q",
                    "--cov=app",
                    "--cov=domains",
                    "--cov=infra",
                    "--cov-report=term-missing",
                    f"--cov-report=json:{COVERAGE_REPORT_OUTPUT}",
                    "--cov-fail-under=0",
                ],
            )
        )

    if _has_perf_reports(args):
        perf_command = [python, "scripts/perf_budget_check.py"]
        for name in PERF_REPORT_OPTIONS:
            path = getattr(args, name)
            if path:
                perf_command.extend([f"--{name.replace('_', '-')}", str(path)])
        commands.append(AuditCommand("performance-budget", perf_command))

    return commands


def _display_command(command: Iterable[str]) -> str:
    return " ".join(str(part) for part in command)


def _configure_stdout() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except AttributeError, OSError, ValueError:
        return


def _audit_subprocess_env() -> dict[str, str]:
    child_env = dict(os.environ)
    child_env["PYTHONIOENCODING"] = "utf-8"
    child_env["PYTHONUTF8"] = "1"
    return child_env


def run_audit_commands(commands: list[AuditCommand]) -> int:
    child_env = _audit_subprocess_env()
    for item in commands:
        print(f"[audit] {item.label}: {_display_command(item.command)}", flush=True)
        completed = subprocess.run(item.command, cwd=REPO_ROOT, env=child_env)  # noqa: S603
        if completed.returncode != 0:
            print(f"[audit] failed: {item.label} ({completed.returncode})", flush=True)
            return int(completed.returncode)
    print("[audit] all checks passed", flush=True)
    return 0


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run the standard Zijin Yanxuan project audit gate.",
    )
    parser.add_argument("--python", type=Path, default=None, help="Python executable to use for child checks.")
    parser.add_argument("--quick", action="store_true", help="Skip full pytest and WebEngine preflight.")
    parser.add_argument("--list", action="store_true", help="Print planned commands without running them.")
    parser.add_argument("--skip-ruff", action="store_true", help="Skip the Ruff style/lint gate.")
    parser.add_argument("--skip-full-pytest", action="store_true")
    parser.add_argument("--skip-runtime-self-check", action="store_true")
    parser.add_argument("--skip-webengine-preflight", action="store_true")
    parser.add_argument(
        "--extended-ruff",
        action="store_true",
        help="Run phased-in Bugbear, simplify, comprehensions, and Ruff rules that currently pass.",
    )
    parser.add_argument(
        "--runtime-health-short",
        action="store_true",
        help="Run the short runtime health stability suite with its budget gate.",
    )
    parser.add_argument(
        "--ui-stall-budget",
        action="store_true",
        help="Run strict offscreen UI screenshots and fail when the UI stall budget is exceeded.",
    )
    parser.add_argument(
        "--dependency-audit",
        action="store_true",
        help="Run the optional dependency supply-chain audit report.",
    )
    parser.add_argument(
        "--http-safety-audit",
        action="store_true",
        help="Compatibility flag; the direct external HTTP safety-wrapper audit now runs in the standard gate.",
    )
    parser.add_argument(
        "--type-check",
        action="store_true",
        help="Run gradual pyright checking for app/, domains/, and infra/.",
    )
    parser.add_argument(
        "--coverage-report",
        action="store_true",
        help="Generate an observation-only pytest coverage report with no minimum threshold.",
    )
    parser.add_argument("--gbbq-report", type=Path, default=None)
    parser.add_argument("--tab-report", type=Path, default=None)
    parser.add_argument("--kline-report", type=Path, default=None)
    parser.add_argument("--kline-lifecycle-report", type=Path, default=None)
    parser.add_argument("--soak-report", type=Path, default=None)
    parser.add_argument("--round4-report", type=Path, default=None)
    parser.add_argument("--round5-report", type=Path, default=None)
    parser.add_argument("--runtime-health-report", type=Path, default=None)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    _configure_stdout()
    args = _parse_args(argv or sys.argv[1:])
    commands = build_audit_commands(args)
    if args.list:
        for item in commands:
            print(f"{item.label}: {_display_command(item.command)}")
        return 0
    return run_audit_commands(commands)


if __name__ == "__main__":
    raise SystemExit(main())
