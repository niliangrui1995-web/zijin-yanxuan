# -*- coding: utf-8 -*-
"""Cooperative cancellation and deadline primitives for background work."""

from __future__ import annotations

import inspect
import threading
import time
from collections.abc import Callable, Mapping
from functools import partial
from typing import TypeVar, cast

CANCELLABLE_IO_MAX_SLICE_SECONDS = 2.0
_MIN_IO_TIMEOUT_SECONDS = 0.001
_CANCELLATION_TOKEN_MARKER = "__accepts_cancellation_token__"
_CallableT = TypeVar("_CallableT", bound=Callable[..., object])


class TaskCancelledError(RuntimeError):
    """Raised when a cooperative task has been cancelled."""


class TaskDeadlineExceeded(TimeoutError):
    """Raised when a cooperative task has exceeded its deadline."""


def accepts_cancellation_token(fn: _CallableT) -> _CallableT:
    """Explicitly opt a variadic callable into cancellation-token injection."""
    try:
        setattr(fn, _CANCELLATION_TOKEN_MARKER, True)
    except (AttributeError, TypeError) as exc:
        raise TypeError("cancellation-token marker requires a mutable callable") from exc
    return fn


def _callable_signature(fn) -> inspect.Signature | None:
    try:
        return inspect.signature(fn)
    except (TypeError, ValueError):
        return None


def _cancellation_token_is_bound(
    signature: inspect.Signature,
    call_args: list,
    call_kwargs: dict,
) -> bool:
    try:
        bound = signature.bind_partial(*call_args, **call_kwargs)
    except TypeError:
        return "cancellation_token" in call_kwargs
    return "cancellation_token" in bound.arguments


def _partial_binds_cancellation_token(fn) -> bool:
    current = fn
    while isinstance(current, partial):
        if "cancellation_token" in (current.keywords or {}):
            return True
        current = current.func
    return False


def _variadic_token_injection_enabled(fn) -> bool:
    candidates = [fn, getattr(fn, "__func__", None), getattr(type(fn), "__call__", None)]
    current = fn
    while isinstance(current, partial):
        current = current.func
        candidates.extend(
            (
                current,
                getattr(current, "__func__", None),
                getattr(type(current), "__call__", None),
            )
        )
    return any(bool(getattr(candidate, _CANCELLATION_TOKEN_MARKER, False)) for candidate in candidates if candidate)


def _append_positional_token(
    parameters: Mapping[str, inspect.Parameter],
    token_parameter: inspect.Parameter,
    cancellation_token: CancellationToken,
    call_args: list,
    call_kwargs: dict,
) -> None:
    positional_parameters = [
        parameter
        for parameter in parameters.values()
        if parameter.kind in (inspect.Parameter.POSITIONAL_ONLY, inspect.Parameter.POSITIONAL_OR_KEYWORD)
    ]
    token_index = positional_parameters.index(token_parameter)
    if len(call_args) > token_index:
        return
    for parameter in positional_parameters[len(call_args) : token_index]:
        if parameter.name in call_kwargs:
            call_args.append(call_kwargs.pop(parameter.name))
        elif parameter.default is not inspect.Parameter.empty:
            call_args.append(parameter.default)
        else:
            return
    if len(call_args) == token_index:
        call_args.append(cancellation_token)


def _inject_cancellation_token(
    signature: inspect.Signature,
    cancellation_token: CancellationToken,
    call_args: list,
    call_kwargs: dict,
    *,
    inject_variadic: bool,
) -> None:
    parameters = signature.parameters
    if _cancellation_token_is_bound(signature, call_args, call_kwargs):
        return
    token_parameter = parameters.get("cancellation_token")
    if token_parameter is None:
        if inject_variadic and any(
            parameter.kind is inspect.Parameter.VAR_KEYWORD
            for parameter in parameters.values()
        ):
            call_kwargs["cancellation_token"] = cancellation_token
        return
    if token_parameter.kind is inspect.Parameter.POSITIONAL_ONLY:
        _append_positional_token(parameters, token_parameter, cancellation_token, call_args, call_kwargs)
        return
    if token_parameter.kind in (inspect.Parameter.POSITIONAL_OR_KEYWORD, inspect.Parameter.KEYWORD_ONLY):
        call_kwargs["cancellation_token"] = cancellation_token


def _prepare_cancellation_call(fn, cancellation_token: CancellationToken, args: tuple, kwargs: dict) -> tuple[list, dict]:
    call_args = list(args)
    call_kwargs = dict(kwargs)
    if _partial_binds_cancellation_token(fn):
        return call_args, call_kwargs
    signature = _callable_signature(fn)
    inject_variadic = _variadic_token_injection_enabled(fn)
    if signature is None:
        if inject_variadic and "cancellation_token" not in call_kwargs:
            call_kwargs["cancellation_token"] = cancellation_token
    else:
        _inject_cancellation_token(
            signature,
            cancellation_token,
            call_args,
            call_kwargs,
            inject_variadic=inject_variadic,
        )
    return call_args, call_kwargs


def invoke_with_cancellation(fn, cancellation_token: CancellationToken | None, *args, **kwargs):
    """Invoke a task once, injecting its token when the signature supports it."""
    if cancellation_token is None:
        return fn(*args, **kwargs)

    cancellation_token.raise_if_cancelled()
    call_args, call_kwargs = _prepare_cancellation_call(fn, cancellation_token, args, kwargs)
    result = fn(*call_args, **call_kwargs)
    cancellation_token.raise_if_cancelled()
    return result


def call_with_supported_kwargs(fn, *args, **kwargs):
    """Call once after dropping keyword arguments unsupported by an old API."""
    try:
        parameters = inspect.signature(fn).parameters
    except (TypeError, ValueError):
        return fn(*args, **kwargs)
    if any(parameter.kind is inspect.Parameter.VAR_KEYWORD for parameter in parameters.values()):
        return fn(*args, **kwargs)
    supported = {
        name
        for name, parameter in parameters.items()
        if parameter.kind is not inspect.Parameter.POSITIONAL_ONLY
    }
    return fn(*args, **{key: value for key, value in kwargs.items() if key in supported})


def bounded_wait_for_tasks_status(
    runner,
    task_ids,
    *,
    timeout_ms: int,
) -> bool | None:
    """Call a runner's bounded physical wait without weakening its signature."""
    wait_for_tasks = getattr(runner, "wait_for_tasks", None)
    if not callable(wait_for_tasks):
        return None
    signature = _callable_signature(wait_for_tasks)
    if signature is None:
        return None
    parameters = signature.parameters
    accepts_timeout = "timeout_ms" in parameters or any(
        parameter.kind is inspect.Parameter.VAR_KEYWORD
        for parameter in parameters.values()
    )
    if not accepts_timeout:
        return None
    try:
        result = wait_for_tasks(
            tuple(task_ids),
            timeout_ms=max(0, int(timeout_ms or 0)),
        )
    except Exception:  # noqa: BLE001 - physical settlement must fail closed.
        return None
    return result if type(result) is bool else None


def task_unsettled_status(runner, task_id: str) -> bool | None:
    """Return physical termination state without falling back to a dedupe slot.

    ``True`` means the task is proven unsettled, ``False`` means it is proven
    terminated, and ``None`` means the runner cannot provide that proof.
    """
    probe = getattr(runner, "is_task_unsettled", None)
    if callable(probe):
        try:
            result = probe(task_id)
        except Exception:  # noqa: BLE001 - physical probe failures remain unknown.
            result = None
        if type(result) is bool:
            return result
    waited = bounded_wait_for_tasks_status(
        runner,
        (task_id,),
        timeout_ms=0,
    )
    if waited is True:
        return False
    if waited is False:
        return True
    return None


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
    "accepts_cancellation_token",
    "bounded_wait_for_tasks_status",
    "bounded_io_timeout",
    "call_with_supported_kwargs",
    "invoke_with_cancellation",
    "raise_if_cancelled",
    "reraise_task_cancellation",
    "task_unsettled_status",
    "wait_with_cancellation",
]
