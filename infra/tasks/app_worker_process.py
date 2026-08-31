# -*- coding: utf-8 -*-
"""Command construction for controlled application worker subprocesses."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from infra.storage.f5_job_repository import F5JobRepository
from infra.tasks.process_runner import spawn_silent_process

_F5_SINGLE_THREAD_MATH_ENV = (
    "OPENBLAS_NUM_THREADS",
    "OMP_NUM_THREADS",
    "MKL_NUM_THREADS",
    "NUMEXPR_NUM_THREADS",
)


def _source_python(project_root: str) -> str:
    candidate = Path(project_root) / ".venv" / "Scripts" / "python.exe"
    if candidate.is_file():
        return str(candidate)
    executable = Path(sys.executable)
    if os.name == "nt" and executable.name.lower() == "pythonw.exe":
        console = executable.with_name("python.exe")
        if console.is_file():
            return str(console)
    return str(executable)


def build_f5_worker_command(*, project_root: str, job_dir: str) -> list[str]:
    if bool(getattr(sys, "frozen", False)):
        return [sys.executable, "--app-worker=f5", "--job-dir", str(Path(job_dir).resolve())]
    return [
        _source_python(project_root),
        "-m",
        "app.workers.f5_worker_main",
        "--job-dir",
        str(Path(job_dir).resolve()),
    ]


def build_stock_context_fund_snapshot_worker_command(
    *,
    project_root: str,
    job_dir: str,
) -> list[str]:
    if bool(getattr(sys, "frozen", False)):
        return [
            sys.executable,
            "--app-worker=stock-context-fund-snapshot",
            "--job-dir",
            str(Path(job_dir).resolve()),
        ]
    return [
        _source_python(project_root),
        "-m",
        "app.workers.stock_context_fund_snapshot_worker_main",
        "--job-dir",
        str(Path(job_dir).resolve()),
    ]


def _f5_worker_environment() -> dict[str, str]:
    worker_env = os.environ.copy()
    logical_cpu_count = max(1, int(os.cpu_count() or 1))
    worker_env["POLARS_MAX_THREADS"] = str(max(1, min(2, logical_cpu_count // 2)))
    worker_env["PYTHONFAULTHANDLER"] = "1"
    worker_env["PYTHONUNBUFFERED"] = "1"
    for variable in _F5_SINGLE_THREAD_MATH_ENV:
        worker_env[variable] = "1"
    return worker_env


def spawn_f5_worker(*, project_root: str, job_dir: str):
    creationflags = int(getattr(subprocess, "BELOW_NORMAL_PRIORITY_CLASS", 0) or 0)
    repository = F5JobRepository(job_dir)
    with repository.worker_stdout_path.open("ab") as stdout, repository.worker_stderr_path.open("ab") as stderr:
        return spawn_silent_process(
            build_f5_worker_command(project_root=project_root, job_dir=job_dir),
            cwd=str(Path(project_root).resolve()),
            creationflags=creationflags,
            env=_f5_worker_environment(),
            stdout=stdout,
            stderr=stderr,
        )


__all__ = [
    "build_f5_worker_command",
    "build_stock_context_fund_snapshot_worker_command",
    "spawn_f5_worker",
]
