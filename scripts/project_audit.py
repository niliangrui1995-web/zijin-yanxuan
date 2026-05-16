# -*- coding: utf-8 -*-
from __future__ import annotations

import argparse
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
DEPENDENCY_AUDIT_OUTPUT = "tmp/dependency_audit.json"


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
    ]

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

    if args.dependency_audit:
        commands.append(
            AuditCommand(
                "dependency-audit",
                [python, "scripts/dependency_audit.py", "--output", DEPENDENCY_AUDIT_OUTPUT],
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


def run_audit_commands(commands: list[AuditCommand]) -> int:
    for item in commands:
        print(f"[audit] {item.label}: {_display_command(item.command)}", flush=True)
        completed = subprocess.run(item.command, cwd=REPO_ROOT)  # noqa: S603
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
    parser.add_argument("--skip-full-pytest", action="store_true")
    parser.add_argument("--skip-runtime-self-check", action="store_true")
    parser.add_argument("--skip-webengine-preflight", action="store_true")
    parser.add_argument(
        "--runtime-health-short",
        action="store_true",
        help="Run the short runtime health stability suite with its budget gate.",
    )
    parser.add_argument(
        "--dependency-audit",
        action="store_true",
        help="Run the optional dependency supply-chain audit report.",
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
    args = _parse_args(argv or sys.argv[1:])
    commands = build_audit_commands(args)
    if args.list:
        for item in commands:
            print(f"{item.label}: {_display_command(item.command)}")
        return 0
    return run_audit_commands(commands)


if __name__ == "__main__":
    raise SystemExit(main())
