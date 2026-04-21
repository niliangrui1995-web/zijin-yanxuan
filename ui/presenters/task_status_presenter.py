# -*- coding: utf-8 -*-
"""Formatting helpers for the log tab task-status panel."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class TaskStatusEntry:
    task_name: str
    progress: int
    message: str
    state: str
    updated_at: datetime


def _normalize_task_name(task_name: str) -> str:
    normalized = str(task_name or "").strip()
    return normalized or "unknown"


def _normalize_progress(progress: int) -> int:
    try:
        value = int(progress)
    except (TypeError, ValueError):
        value = 0
    return max(0, min(100, value))


def _normalize_message(message: str) -> str:
    normalized = str(message or "").strip()
    return normalized or "running"


def _detect_state(progress: int, message: str) -> str:
    normalized = str(message or "").strip().lower()
    if progress >= 100 or normalized in {"done", "complete", "completed", "ready"}:
        return "done"
    if normalized in {"stop", "stopped", "idle", "skipped", "failed", "error", "timeout"}:
        return "idle"
    return "running"


def build_task_status_entry(task_name: str, progress: int, message: str) -> TaskStatusEntry:
    normalized_task_name = _normalize_task_name(task_name)
    normalized_progress = _normalize_progress(progress)
    normalized_message = _normalize_message(message)
    return TaskStatusEntry(
        task_name=normalized_task_name,
        progress=normalized_progress,
        message=normalized_message,
        state=_detect_state(normalized_progress, normalized_message),
        updated_at=datetime.now(),
    )


def render_task_status_summary(entries: list[TaskStatusEntry]) -> str:
    if not entries:
        return "Task status: 0"

    running_count = sum(1 for entry in entries if entry.state == "running")
    done_count = sum(1 for entry in entries if entry.state == "done")
    return f"Task status: {len(entries)} | running {running_count} | done {done_count}"


def render_task_status_lines(entries: list[TaskStatusEntry]) -> str:
    if not entries:
        return "No background task activity yet."

    lines = []
    for entry in entries:
        timestamp = entry.updated_at.strftime("%H:%M:%S")
        lines.append(f"{timestamp} | {entry.task_name:<16} | {entry.progress:>3}% | {entry.message}")
    return "\n".join(lines)
