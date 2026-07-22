# -*- coding: utf-8 -*-
"""UI-facing facade for owner-bound cooperative task lifecycle primitives."""

from infra.tasks.lifecycle import raise_if_cancelled, reraise_task_cancellation
from infra.tasks.owner_lifecycle import (
    CancellationToken,
    TaskCancelledError,
    TaskDeadlineExceeded,
    TaskLifecycleGroup,
    TaskSubmissionReceipt,
    TaskSubmissionStatus,
    accepts_cancellation_token,
    invoke_with_cancellation,
    shutdown_task_lifecycle_for_owner,
    task_lifecycle_for,
)

__all__ = [
    "CancellationToken",
    "TaskCancelledError",
    "TaskDeadlineExceeded",
    "TaskLifecycleGroup",
    "TaskSubmissionReceipt",
    "TaskSubmissionStatus",
    "accepts_cancellation_token",
    "invoke_with_cancellation",
    "raise_if_cancelled",
    "reraise_task_cancellation",
    "shutdown_task_lifecycle_for_owner",
    "task_lifecycle_for",
]
