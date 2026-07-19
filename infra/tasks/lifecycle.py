# -*- coding: utf-8 -*-
"""Cooperative cancellation and deadline primitives for background work."""

from __future__ import annotations

import threading
import time
from typing import cast

CANCELLABLE_IO_MAX_SLICE_SECONDS = 2.0
_MIN_IO_TIMEOUT_SECONDS = 0.001


class TaskCancelledError(RuntimeError):
    """Raised when a cooperative task has been cancelled."""


class TaskDeadlineExceeded(TimeoutError):
    """Raised when a cooperative task has exceeded its deadline."""


def raise_if_cancelled(cancellation_token=None) -> None:
    if cancellation_token is not None:
        cancellation_token.raise_if_cancelled()


def bounded_io_timeout(
    default_seconds: float,
    cancellation_token=None,
    *,
    max_slice_seconds: float = CANCELLABLE_IO_MAX_SLICE_SECONDS,
) -> float:
    """Return an I/O timeout bounded by both owner cancellation and its deadline.

    A running requests/urlopen/yfinance call cannot be interrupted by cancelling a
    Python ``Future``.  Owner-bound calls therefore use a short socket/client
    timeout slice, then re-check the token before entering another provider stage.
    Calls without a token keep their historical timeout unchanged.
    """

    raise_if_cancelled(cancellation_token)
    timeout = max(_MIN_IO_TIMEOUT_SECONDS, float(default_seconds or 0.0))
    if cancellation_token is None:
        return timeout

    remaining_reader = getattr(cancellation_token, "remaining_seconds", None)
    remaining = cast(float | None, remaining_reader() if callable(remaining_reader) else None)
    if remaining is not None:
        if float(remaining) <= 0:
            raise_if_cancelled(cancellation_token)
        timeout = min(timeout, max(_MIN_IO_TIMEOUT_SECONDS, float(remaining)))
    timeout = min(timeout, max(_MIN_IO_TIMEOUT_SECONDS, float(max_slice_seconds or 0.0)))
    return timeout


def reraise_task_cancellation(exc: BaseException) -> None:
    if isinstance(exc, (TaskCancelledError, TaskDeadlineExceeded)):
        raise exc


def wait_with_cancellation(seconds: float, cancellation_token=None) -> None:
    wait_seconds = max(0.0, float(seconds or 0.0))
    if cancellation_token is None:
        time.sleep(wait_seconds)
    elif cancellation_token.wait(wait_seconds):
        cancellation_token.raise_if_cancelled()


class CancellationToken:
    """Thread-safe cancellation token with an optional monotonic deadline."""

    def __init__(self, *, deadline_monotonic: float | None = None) -> None:
        self._deadline_monotonic = float(deadline_monotonic) if deadline_monotonic is not None else None
        self._event = threading.Event()
        self._lock = threading.Lock()
        self._reason = ""

    @classmethod
    def with_timeout(cls, timeout_seconds: float | None) -> CancellationToken:
        if timeout_seconds is None:
            return cls()
        timeout = max(0.0, float(timeout_seconds))
        return cls(deadline_monotonic=time.monotonic() + timeout)

    @property
    def deadline_monotonic(self) -> float | None:
        return self._deadline_monotonic

    @property
    def cancelled(self) -> bool:
        return self._event.is_set() or self._deadline_exceeded()

    @property
    def reason(self) -> str:
        if self._event.is_set():
            with self._lock:
                return self._reason or "cancelled"
        if self._deadline_exceeded():
            return "deadline_exceeded"
        return ""

    def cancel(self, reason: str = "cancelled") -> bool:
        with self._lock:
            if self._event.is_set():
                return False
            self._reason = str(reason or "cancelled").strip() or "cancelled"
            self._event.set()
            return True

    def remaining_seconds(self) -> float | None:
        if self._deadline_monotonic is None:
            return None
        return max(0.0, self._deadline_monotonic - time.monotonic())

    def wait(self, timeout: float | None = None) -> bool:
        if self.cancelled:
            return True
        remaining = self.remaining_seconds()
        if timeout is None:
            wait_seconds = remaining
        else:
            wait_seconds = max(0.0, float(timeout))
            if remaining is not None:
                wait_seconds = min(wait_seconds, remaining)
        self._event.wait(wait_seconds)
        return self.cancelled

    def raise_if_cancelled(self) -> None:
        if self._event.is_set():
            raise TaskCancelledError(f"任务已取消: {self.reason}")
        if self._deadline_exceeded():
            raise TaskDeadlineExceeded("任务已超过截止时间")

    def _deadline_exceeded(self) -> bool:
        return self._deadline_monotonic is not None and time.monotonic() >= self._deadline_monotonic


__all__ = [
    "CANCELLABLE_IO_MAX_SLICE_SECONDS",
    "CancellationToken",
    "TaskCancelledError",
    "TaskDeadlineExceeded",
    "bounded_io_timeout",
    "raise_if_cancelled",
    "reraise_task_cancellation",
    "wait_with_cancellation",
]
