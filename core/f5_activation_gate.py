# -*- coding: utf-8 -*-
"""Process-wide version boundary for F5 snapshot readers and activation."""

from __future__ import annotations

import threading
from collections.abc import Callable, Generator
from contextlib import contextmanager
from functools import wraps
from typing import ParamSpec, TypeVar

_P = ParamSpec("_P")
_R = TypeVar("_R")

_F5_SNAPSHOT_LOCK = threading.RLock()


@contextmanager
def f5_snapshot_read_boundary() -> Generator[None, None, None]:
    with _F5_SNAPSHOT_LOCK:
        yield


@contextmanager
def f5_snapshot_activation_boundary() -> Generator[None, None, None]:
    with _F5_SNAPSHOT_LOCK:
        yield


def f5_snapshot_read_locked(function: Callable[_P, _R]) -> Callable[_P, _R]:
    @wraps(function)
    def _locked(*args: _P.args, **kwargs: _P.kwargs) -> _R:
        with f5_snapshot_read_boundary():
            return function(*args, **kwargs)

    return _locked


def wait_for_f5_snapshot_activation(timeout_seconds: float) -> bool:
    acquired = _F5_SNAPSHOT_LOCK.acquire(timeout=max(0.0, float(timeout_seconds or 0.0)))
    if acquired:
        _F5_SNAPSHOT_LOCK.release()
    return acquired


__all__ = [
    "f5_snapshot_activation_boundary",
    "f5_snapshot_read_boundary",
    "f5_snapshot_read_locked",
    "wait_for_f5_snapshot_activation",
]
