# -*- coding: utf-8 -*-
"""Owner-bound cooperative task lifecycle primitives."""

from __future__ import annotations

import inspect
import logging
import threading
from dataclasses import dataclass
from typing import Protocol

from infra.tasks.lifecycle import CancellationToken, TaskCancelledError, TaskDeadlineExceeded


class _BackgroundRunner(Protocol):
    def run_in_background(self, fn, *args, **kwargs) -> str: ...


@dataclass(frozen=True)
class _OwnedTask:
    token: CancellationToken
    task_id: str
    runner: object | None


def _default_background_runner():
    from core.background_job_runner import background_job_runner

    return background_job_runner


def invoke_with_cancellation(fn, cancellation_token: CancellationToken, *args, **kwargs):
    """Invoke a provider stage with a token when its public signature accepts it."""
    cancellation_token.raise_if_cancelled()
    try:
        parameters = inspect.signature(fn).parameters.values()
        accepts_token = any(
            parameter.name == "cancellation_token" or parameter.kind is inspect.Parameter.VAR_KEYWORD
            for parameter in parameters
        )
    except (TypeError, ValueError):
        accepts_token = False
    if accepts_token:
        result = fn(*args, cancellation_token=cancellation_token, **kwargs)
    else:
        result = fn(*args, **kwargs)
    cancellation_token.raise_if_cancelled()
    return result


def _submit_owned_task(runner, fn, submit_kwargs: dict) -> None:
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
    submit(fn, **submit_kwargs)


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
            callback(*args)

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


class TaskLifecycleGroup:
    """Own named cooperative tasks for one UI component or service.

    Each name has at most one current token. Replacing a name cancels and
    abandons the previous scheduler slot before the new task is submitted.
    """

    def __init__(self, runner: _BackgroundRunner | None = None) -> None:
        self._runner = runner
        self._lock = threading.RLock()
        self._tasks: dict[str, _OwnedTask] = {}

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
        normalized = self._normalize_name(name)
        resolved_runner = runner
        previous = None
        token = CancellationToken.with_timeout(timeout_sec)
        with self._lock:
            previous = self._tasks.pop(normalized, None)
            self._tasks[normalized] = _OwnedTask(
                token=token,
                task_id=str(task_id or "").strip(),
                runner=resolved_runner,
            )
        if previous is not None:
            self._cancel_owned(previous, reason="replaced", abandon=True)
        return token

    @staticmethod
    def _call_runner(runner, method_name: str, *args, **kwargs):
        method = getattr(runner, method_name, None)
        if not callable(method):
            return False
        try:
            return method(*args, **kwargs)
        except TypeError:
            return method(*args)

    def _cancel_owned(self, owned: _OwnedTask, *, reason: str, abandon: bool) -> bool:
        cancelled = owned.token.cancel(reason)
        if owned.runner is None or not owned.task_id:
            return cancelled
        method_name = "abandon_task" if abandon else "cancel_task"
        self._call_runner(owned.runner, method_name, owned.task_id, reason=reason)
        return cancelled

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
        """Submit ``fn(token)`` while retaining the same token for cancellation."""
        normalized = self._normalize_name(name)
        resolved_runner = runner or self._resolve_runner()
        normalized_task_id = str(getattr(task_id, "task_id", task_id) or "").strip()
        token = self.begin(
            normalized,
            timeout_sec=timeout_sec,
            task_id=normalized_task_id,
            runner=resolved_runner,
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
        _submit_owned_task(resolved_runner, run_owned, submit_kwargs)
        return token

    def complete(self, name: str, token: CancellationToken) -> bool:
        normalized = self._normalize_name(name)
        with self._lock:
            current = self._tasks.get(normalized)
            if current is None or current.token is not token:
                return False
            self._tasks.pop(normalized, None)
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
        if owned is None:
            return False
        self._cancel_owned(owned, reason=reason, abandon=False)
        return True

    def shutdown(self, *, timeout_ms: int = 750) -> bool:
        with self._lock:
            owned_tasks = list(self._tasks.values())
            self._tasks.clear()
        if not owned_tasks:
            return True

        runner_tasks: dict[int, tuple[object, list[str]]] = {}
        for owned in owned_tasks:
            self._cancel_owned(owned, reason="owner_shutdown", abandon=False)
            if owned.runner is None or not owned.task_id:
                continue
            key = id(owned.runner)
            runner, task_ids = runner_tasks.setdefault(key, (owned.runner, []))
            task_ids.append(owned.task_id)

        completed = True
        for runner, task_ids in runner_tasks.values():
            waited = self._call_runner(
                runner,
                "wait_for_tasks",
                tuple(dict.fromkeys(task_ids)),
                timeout_ms=max(0, int(timeout_ms or 0)),
            )
            completed = bool(waited) and completed
        return completed

    @property
    def active_names(self) -> tuple[str, ...]:
        with self._lock:
            return tuple(sorted(self._tasks))


def task_lifecycle_for(owner, *, runner=None, attr_name: str = "_task_lifecycle") -> TaskLifecycleGroup:
    """Return the lifecycle group owned by one UI component, creating it lazily."""
    lifecycle = getattr(owner, attr_name, None)
    if isinstance(lifecycle, TaskLifecycleGroup):
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
    "invoke_with_cancellation",
    "shutdown_task_lifecycle_for_owner",
    "task_lifecycle_for",
]


