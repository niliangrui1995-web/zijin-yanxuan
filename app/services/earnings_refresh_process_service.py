# -*- coding: utf-8 -*-
"""Application boundary for the earnings refresh worker process protocol."""

from __future__ import annotations

import json

from infra.tasks import run_python_module
from infra.tasks.lifecycle import raise_if_cancelled as _raise_if_cancelled

EARNINGS_REFRESH_TIMEOUT_SECONDS = 15 * 60


def _process_timeout(cancellation_token=None) -> float:
    _raise_if_cancelled(cancellation_token)
    if cancellation_token is None:
        return float(EARNINGS_REFRESH_TIMEOUT_SECONDS)
    remaining = cancellation_token.remaining_seconds()
    if remaining is None:
        return float(EARNINGS_REFRESH_TIMEOUT_SECONDS)
    if remaining <= 0:
        cancellation_token.raise_if_cancelled()
    return max(0.1, min(float(EARNINGS_REFRESH_TIMEOUT_SECONDS), remaining))


def parse_earnings_refresh_output(stdout: str | bytes | None, *, expected_job_key: str) -> dict:
    text = stdout.decode("utf-8", errors="replace") if isinstance(stdout, bytes) else str(stdout or "")
    for line in reversed(text.splitlines()):
        line = line.strip()
        if not line.startswith("{"):
            continue
        payload = json.loads(line)
        if not isinstance(payload, dict):
            continue
        status = str(payload.get("status") or "").strip()
        job_key = str(payload.get("job_key") or "").strip()
        if status in {"success", "degraded"} and job_key == expected_job_key:
            return dict(payload)
    raise ValueError(f"earnings refresh result missing for {expected_job_key}")


def run_earnings_refresh(
    mode: str,
    *,
    routine_time: str = "",
    cancellation_token=None,
) -> dict:
    _raise_if_cancelled(cancellation_token)
    normalized_mode = str(mode or "").strip()
    expected_job_key = "earnings_startup_gap_fill" if normalized_mode == "startup-gap-fill" else "earnings_routine"
    module_args = [normalized_mode]
    if normalized_mode == "routine" and str(routine_time or "").strip():
        module_args.extend(["--routine-time", str(routine_time).strip()])
    completed = run_python_module(
        "domains.earnings.refresh_cache",
        module_args,
        no_window=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=True,
        timeout=_process_timeout(cancellation_token),
    )
    _raise_if_cancelled(cancellation_token)
    return parse_earnings_refresh_output(
        getattr(completed, "stdout", ""),
        expected_job_key=expected_job_key,
    )


__all__ = [
    "EARNINGS_REFRESH_TIMEOUT_SECONDS",
    "parse_earnings_refresh_output",
    "run_earnings_refresh",
]
