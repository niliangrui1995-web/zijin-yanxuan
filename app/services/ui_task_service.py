# -*- coding: utf-8 -*-
"""UI-facing background task and process entrypoints."""

from __future__ import annotations

from core.background_job_runner import background_job_runner
from infra.tasks import (
    CENTRAL_QUOTES_POLL,
    NETWORK_FORCE_RECONNECT,
    NETWORK_GO_ONLINE,
    SHARED_MARKET_CAPS,
    WINDOW_F5_PRECOMPUTE,
    ProcessExecutionError,
    ProcessSubprocessError,
    ProcessTimeoutError,
    build_domestic_process_env,
    run_process,
    task_id_of,
    task_registry,
    windows_no_window_creationflags,
    windows_no_window_kwargs,
)

__all__ = [
    "CENTRAL_QUOTES_POLL",
    "NETWORK_FORCE_RECONNECT",
    "NETWORK_GO_ONLINE",
    "ProcessExecutionError",
    "ProcessSubprocessError",
    "ProcessTimeoutError",
    "SHARED_MARKET_CAPS",
    "WINDOW_F5_PRECOMPUTE",
    "background_job_runner",
    "build_domestic_process_env",
    "run_process",
    "task_id_of",
    "task_registry",
    "windows_no_window_kwargs",
    "windows_no_window_creationflags",
]
