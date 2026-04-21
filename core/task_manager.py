# -*- coding: utf-8 -*-
"""Legacy compatibility shim for the task scheduler implementation."""

from infra.tasks.task_scheduler import BackgroundWorker, GlobalTaskManager, UserFacingTaskError, task_manager

__all__ = [
    "BackgroundWorker",
    "GlobalTaskManager",
    "UserFacingTaskError",
    "task_manager",
]
