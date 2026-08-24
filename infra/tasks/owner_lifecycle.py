# -*- coding: utf-8 -*-
"""Owner-bound cooperative task lifecycle primitives."""

from __future__ import annotations

import inspect
import logging
import math
import sys
import threading
import time
from collections.abc import Iterable
from dataclasses import dataclass
from enum import Enum
from typing import Protocol

from infra.tasks.lifecycle import (
    CancellationToken,
    TaskCancelledError,
    TaskDeadlineExceeded,
    accepts_cancellation_token,
    bounded_wait_for_tasks_status,
    call_with_supported_kwargs,
    invoke_with_cancellation,
    task_unsettled_status,
)

_RUNNER_OPERATION_ERRORS = (AttributeError, OSError, RuntimeError, TypeError, ValueError)


class _BackgroundRunner(Protocol):
    def run_in_background(self, fn, *args, **kwargs) -> object: ...


@dataclass(frozen=True)
class _OwnedTask:
    token: CancellationToken
    task_id: str
    runner: object | None


class TaskSubmissionStatus(Enum):
    """Identity-backed submission result for callers that require certainty."""

    ACCEPTED = "accepted"
    REJECTED = "rejected"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class TaskSubmissionReceipt:
    token: CancellationToken
    task_id: str
    status: TaskSubmissionStatus


def _default_background_runner():
    from core.background_job_runner import background_job_runner

    return background_job_runner


def _submit_owned_task(runner, fn, submit_kwargs: dict):
    submit = runner.run_in_background
    try:
        parameters = inspect.signature(submit).parameters.values()
        accepts_kwargs = any(parameter.kind is inspect.Parameter.VAR_KEYWORD for parameter in parameters)
        accepted_names = {parameter.name for parameter in parameters}
    except (TypeError, ValueError):
        accepts_kwargs = True
        accepted_names = set()
    if not accepts_kwargs:
        submit_kwargs = {key: value for key, value in submit_kwargs.items() if key in accepted_names}
    token_forwarded = accepts_kwargs or "cancellation_token" in accepted_names
    return submit(fn, **submit_kwargs), token_forwarded


def _run_owned_callable(token: CancellationToken, fn):
    token.raise_if_cancelled()
    result = fn(token)
    token.raise_if_cancelled()
    return result


def _owned_delivery_callbacks(lifecycle, name, token, on_success, on_error):
    def _deliver(callback, *args) -> None:
        is_current = lifecycle.complete(name, token)
        if not is_current or token.cancelled:
            return
        if callback is not None:
            try:
                callback(*args)
            except Exception as exc:  # noqa: BLE001 - Qt queued callbacks must not escape into PyQt's fatal hook.
                callback_name = getattr(callback, "__qualname__", getattr(callback, "__name__", type(callback).__name__))
                logging.getLogger(__name__).exception(
                    "[任务生命周期][%s] 主线程回调异常: %s",
                    name,
                    callback_name,
                )
                try:
                    sys.excepthook(type(exc), exc, exc.__traceback__)
                except Exception:  # noqa: BLE001 - the reporting hook cannot be allowed back into Qt.
                    logging.getLogger(__name__).exception(
                        "[任务生命周期][%s] 异常上报失败: %s",
                        name,
                        callback_name,
                    )

    def _deliver_success(result) -> None:
        _deliver(on_success, result)

    def _deliver_error(error_message) -> None:
        if on_error is not None:
            _deliver(on_error, error_message)
            return
        if lifecycle.complete(name, token) and not token.cancelled:
            logging.getLogger(__name__).error(
                "[任务生命周期][%s] 后台任务异常: %s",
                name,
                error_message,
            )

    return _deliver_success, _deliver_error


def _cleanup_failed_submission(lifecycle, name: str, token: CancellationToken, on_terminated) -> None:
    with lifecycle._lock:
        current = lifecycle._tasks.get(name)
        if current is not None and current.token is token:
            lifecycle._tasks.pop(name, None)
    token.cancel("submission_failed")
    on_terminated()


class TaskLifecycleGroup:
    """Own named cooperative tasks for one UI component or service.

    Each name has at most one current token. Replacing a name cancels and
    abandons the previous scheduler slot before the new task is submitted.
    """

    def __init__(self, runner: _BackgroundRunner | None = None) -> None:
        self._runner = runner
        self._lock = threading.RLock()
        self._condition = threading.Condition(self._lock)
        self._closed = False
        self._tasks: dict[str, _OwnedTask] = {}
        self._retired_tasks: dict[str, list[_OwnedTask]] = {}
        self._submissions_inflight: dict[str, set[CancellationToken]] = {}
        self._completed_during_submission: set[CancellationToken] = set()

    def _resolve_runner(self):
        return self._runner or _default_background_runner()

    @staticmethod
    def _normalize_name(name: str) -> str:
        normalized = str(name or "").strip()
        if not normalized:
            raise ValueError("task lifecycle name must not be blank")
        return normalized

    def begin(
        self,
        name: str,
        *,
        timeout_sec: float | None = None,
        task_id: str = "",
        runner=None,
    ) -> CancellationToken:
        """Create and retain a token, cancelling the previous named lease."""
        return self._begin_owned(
            name,
            timeout_sec=timeout_sec,
            task_id=task_id,
            runner=runner,
            submission_inflight=False,
        )

    def _begin_owned(
        self,
        name: str,
        *,
        timeout_sec: float | None,
        task_id: str,
        runner,
        submission_inflight: bool,
    ) -> CancellationToken:
        normalized = self._normalize_name(name)
        resolved_runner = runner
        previous = None
        token = CancellationToken.with_timeout(timeout_sec)
        with self._lock:
            if self._closed:
                token.cancel("owner_shutdown")
                return token
            previous = self._tasks.pop(normalized, None)
            if previous is not None:
                self._retire_owned_locked(normalized, previous)
            self._tasks[normalized] = _OwnedTask(
                token=token,
                task_id=str(task_id or "").strip(),
                runner=resolved_runner,
            )
            if submission_inflight:
                self._submissions_inflight.setdefault(normalized, set()).add(token)
        if previous is not None:
            self._cancel_owned(previous, reason="replaced", abandon=True)
        return token

    def _finish_submission(self, name: str, token: CancellationToken) -> None:
        with self._condition:
            tokens = self._submissions_inflight.get(name)
            if tokens is not None:
                tokens.discard(token)
                if not tokens:
                    self._submissions_inflight.pop(name, None)
            self._completed_during_submission.discard(token)
            self._condition.notify_all()

    @staticmethod
    def _call_runner(runner, method_name: str, *args, **kwargs):
        method = getattr(runner, method_name, None)
        if not callable(method):
            return False
        return call_with_supported_kwargs(method, *args, **kwargs)

    def _cancel_owned(self, owned: _OwnedTask, *, reason: str, abandon: bool) -> bool:
        cancelled = owned.token.cancel(reason)
        if owned.runner is None or not owned.task_id:
            return cancelled
        method_name = "abandon_task" if abandon else "cancel_task"
        self._call_runner(owned.runner, method_name, owned.task_id, reason=reason)
        return cancelled

    @classmethod
    def _owned_unsettled(cls, owned: _OwnedTask) -> bool:
        if owned.runner is None or not owned.task_id:
            return False
        return task_unsettled_status(owned.runner, owned.task_id) is not False

    def _retire_owned_locked(self, name: str, owned: _OwnedTask) -> None:
        if self._owned_unsettled(owned):
            self._retired_tasks.setdefault(name, []).append(owned)

    @staticmethod
    def _submission_accepted(
        owned: _OwnedTask,
        *,
        token_forwarded: bool,
    ) -> bool | None:
        if not token_forwarded:
            return None
        probe = getattr(owned.runner, "is_task_token_active", None)
        if not callable(probe) or not owned.task_id:
            return None
        try:
            result = probe(owned.task_id, owned.token)
            return result if type(result) is bool else None
        except _RUNNER_OPERATION_ERRORS:
            return None

    def run_background(
        self,
        name: str,
        fn,
        *,
        task_id,
        timeout_sec: float | None,
        on_success=None,
        on_error=None,
        runner=None,
        **scheduler_kwargs,
    ) -> CancellationToken:
        """Compatibility API returning only the owned cancellation token."""
        return self.submit_background(
            name,
            fn,
            task_id=task_id,
            timeout_sec=timeout_sec,
            on_success=on_success,
            on_error=on_error,
            runner=runner,
            **scheduler_kwargs,
        ).token

    def submit_background(
        self,
        name: str,
        fn,
        *,
        task_id,
        timeout_sec: float | None,
        on_success=None,
        on_error=None,
        runner=None,
        **scheduler_kwargs,
    ) -> TaskSubmissionReceipt:
        """Submit ``fn(token)`` and report whether token identity was registered."""
        normalized = self._normalize_name(name)
        resolved_runner = runner or self._resolve_runner()
        normalized_task_id = str(getattr(task_id, "task_id", task_id) or "").strip()
        token = self._begin_owned(
            normalized,
            timeout_sec=timeout_sec,
            task_id=normalized_task_id,
            runner=resolved_runner,
            submission_inflight=True,
        )

        termination_callback = scheduler_kwargs.get("on_terminated")
        termination_lock = threading.Lock()
        termination_delivered = False

        def deliver_terminated_once() -> None:
            nonlocal termination_delivered
            if not callable(termination_callback):
                return
            with termination_lock:
                if termination_delivered:
                    return
                termination_delivered = True
            try:
                termination_callback()
            except Exception:  # noqa: BLE001 - terminal cleanup must not escape.
                logging.getLogger(__name__).exception(
                    "[任务生命周期][%s] 终态回调异常",
                    normalized,
                )

        if callable(termination_callback):
            scheduler_kwargs = {**scheduler_kwargs, "on_terminated": deliver_terminated_once}
        if token.reason == "owner_shutdown":
            deliver_terminated_once()
            return TaskSubmissionReceipt(
                token=token,
                task_id=normalized_task_id,
                status=TaskSubmissionStatus.REJECTED,
            )

        def run_owned(*_args, **_kwargs):
            return _run_owned_callable(token, fn)

        deliver_success, deliver_error = _owned_delivery_callbacks(
            self,
            normalized,
            token,
            on_success,
            on_error,
        )

        submit_kwargs = {
            "on_success": deliver_success,
            "on_error": deliver_error,
            "task_id": task_id,
            "cancellation_token": token,
            "timeout_sec": timeout_sec,
            **scheduler_kwargs,
        }
        submission_completed = False
        try:
            submitted_task_id, token_forwarded = _submit_owned_task(
                resolved_runner,
                run_owned,
                submit_kwargs,
            )
            returned_task_id = (
                submitted_task_id
                if isinstance(submitted_task_id, str)
                else getattr(submitted_task_id, "task_id", "")
            )
            actual_task_id = str(returned_task_id or normalized_task_id).strip()
            submitted = _OwnedTask(
                token=token,
                task_id=actual_task_id,
                runner=resolved_runner,
            )
            accepted = self._submission_accepted(
                submitted,
                token_forwarded=token_forwarded,
            )
            cancel_late_submission = False
            with self._lock:
                current = self._tasks.get(normalized)
                completed_during_submission = token in self._completed_during_submission
                self._completed_during_submission.discard(token)
                if not completed_during_submission:
                    if current is not None and current.token is token:
                        if accepted is False:
                            self._tasks.pop(normalized, None)
                        else:
                            self._tasks[normalized] = submitted
                    elif accepted is not False:
                        self._retire_owned_locked(normalized, submitted)
                        cancel_late_submission = True
            if not completed_during_submission:
                if accepted is False:
                    token.cancel("submission_rejected")
                    deliver_terminated_once()
                elif cancel_late_submission:
                    self._call_runner(
                        resolved_runner,
                        "cancel_task",
                        actual_task_id,
                        reason="cancelled_during_submit",
                    )
            submission_completed = True
        finally:
            if not submission_completed:
                _cleanup_failed_submission(self, normalized, token, deliver_terminated_once)
            self._finish_submission(normalized, token)
        if completed_during_submission:
            submission_status = TaskSubmissionStatus.ACCEPTED
        elif accepted is True:
            submission_status = TaskSubmissionStatus.ACCEPTED
        elif accepted is False:
            submission_status = TaskSubmissionStatus.REJECTED
        else:
            submission_status = TaskSubmissionStatus.UNKNOWN
        return TaskSubmissionReceipt(
            token=token,
            task_id=actual_task_id,
            status=submission_status,
        )

    def complete(self, name: str, token: CancellationToken) -> bool:
        normalized = self._normalize_name(name)
        with self._lock:
            current = self._tasks.get(normalized)
            if current is None or current.token is not token:
                return False
            self._tasks.pop(normalized, None)
            if token in self._submissions_inflight.get(normalized, ()):
                self._completed_during_submission.add(token)
            else:
                self._retire_owned_locked(normalized, current)
            return True

    def is_current(self, name: str, token: CancellationToken) -> bool:
        normalized = self._normalize_name(name)
        with self._lock:
            current = self._tasks.get(normalized)
            return current is not None and current.token is token and not token.cancelled

    def cancel(self, name: str, *, reason: str = "cancelled") -> bool:
        normalized = self._normalize_name(name)
        with self._lock:
            owned = self._tasks.pop(normalized, None)
            if owned is not None:
                self._retire_owned_locked(normalized, owned)
        if owned is None:
            return False
        self._cancel_owned(owned, reason=reason, abandon=False)
        return True

    def shutdown(self, *, timeout_ms: int = 750) -> bool:
        deadline = time.monotonic() + max(0, int(timeout_ms or 0)) / 1000.0
        with self._lock:
            self._closed = True
            owned_items = list(self._tasks.items())
            for name, owned in owned_items:
                self._retire_owned_locked(name, owned)
            self._tasks.clear()

        for _name, owned in owned_items:
            self._cancel_owned(owned, reason="owner_shutdown", abandon=False)

        with self._condition:
            while self._submissions_inflight:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                self._condition.wait(remaining)
            submissions_completed = not self._submissions_inflight

        with self._lock:
            tracked_items = [
                (name, owned)
                for name, tasks in self._retired_tasks.items()
                for owned in tasks
            ]

        candidates: list[tuple[str, _OwnedTask]] = []
        seen: set[tuple[int, int, str]] = set()
        for name, owned in (*owned_items, *tracked_items):
            identity = (id(owned.runner), id(owned.token), owned.task_id)
            if identity in seen:
                continue
            seen.add(identity)
            candidates.append((name, owned))

        runner_tasks: dict[int, tuple[object, list[str]]] = {}
        for _name, owned in candidates:
            if owned.runner is None or not owned.task_id:
                continue
            key = id(owned.runner)
            runner, task_ids = runner_tasks.setdefault(key, (owned.runner, []))
            task_ids.append(owned.task_id)

        completed = submissions_completed
        settled_by_wait: set[tuple[int, str]] = set()
        for runner, task_ids in runner_tasks.values():
            remaining_ms = max(0, math.ceil((deadline - time.monotonic()) * 1000.0))
            waited = bounded_wait_for_tasks_status(
                runner,
                tuple(dict.fromkeys(task_ids)),
                timeout_ms=remaining_ms,
            )
            proved_settled = waited is True
            completed = proved_settled and completed
            if proved_settled:
                settled_by_wait.update((id(runner), task_id) for task_id in task_ids)

        with self._lock:
            for name, tasks in tuple(self._retired_tasks.items()):
                unsettled = [
                    owned
                    for owned in tasks
                    if (id(owned.runner), owned.task_id) not in settled_by_wait
                    and self._owned_unsettled(owned)
                ]
                if unsettled:
                    self._retired_tasks[name] = unsettled
                else:
                    self._retired_tasks.pop(name, None)
            retained = bool(self._retired_tasks)
        return completed and not retained

    @property
    def active_names(self) -> tuple[str, ...]:
        with self._lock:
            return tuple(sorted(self._tasks))

    def task_ids_for(self, names: Iterable[str]) -> tuple[str, ...]:
        """Snapshot the current scheduler ids owned by the requested lifecycle names."""
        normalized_names = tuple(dict.fromkeys(self._normalize_name(name) for name in names))
        with self._lock:
            task_ids: list[str] = []
            for name in normalized_names:
                current = self._tasks.get(name)
                if current is not None and current.task_id:
                    task_ids.append(current.task_id)
                retired = [
                    owned
                    for owned in self._retired_tasks.get(name, ())
                    if self._owned_unsettled(owned)
                ]
                if retired:
                    self._retired_tasks[name] = retired
                    task_ids.extend(owned.task_id for owned in retired if owned.task_id)
                else:
                    self._retired_tasks.pop(name, None)
            return tuple(dict.fromkeys(task_ids))

    def submissions_settled_for(self, names: Iterable[str]) -> bool:
        """Report whether runner submission handshakes have completed for names."""
        normalized_names = tuple(dict.fromkeys(self._normalize_name(name) for name in names))
        with self._lock:
            return not any(self._submissions_inflight.get(name) for name in normalized_names)


def task_lifecycle_for(owner, *, runner=None, attr_name: str = "_task_lifecycle") -> TaskLifecycleGroup:
    """Return the lifecycle group owned by one UI component, creating it lazily."""
    lifecycle = getattr(owner, attr_name, None)
    if lifecycle is not None:
        return lifecycle
    lifecycle = TaskLifecycleGroup(runner)
    setattr(owner, attr_name, lifecycle)
    return lifecycle


def shutdown_task_lifecycle_for_owner(
    owner,
    *,
    timeout_ms: int = 750,
    attr_name: str = "_task_lifecycle",
) -> bool:
    lifecycle = getattr(owner, attr_name, None)
    if lifecycle is None:
        return True
    return bool(lifecycle.shutdown(timeout_ms=timeout_ms))


__all__ = [
    "CancellationToken",
    "TaskCancelledError",
    "TaskDeadlineExceeded",
    "TaskLifecycleGroup",
    "TaskSubmissionReceipt",
    "TaskSubmissionStatus",
    "accepts_cancellation_token",
    "invoke_with_cancellation",
    "shutdown_task_lifecycle_for_owner",
    "task_lifecycle_for",
]
