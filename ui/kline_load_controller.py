# -*- coding: utf-8 -*-
"""Pure-Python ownership contract for one K-line window load stream."""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass

KLINE_OPEN_STAGE_ORDER = (
    "shell_ready",
    "browser_ready",
    "data_ready",
    "js_ready",
    "chart_ready",
    "first_interaction",
)

_TASK_STAGE_PATTERN = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")


@dataclass(frozen=True, slots=True)
class KlineLoadIdentity:
    """Immutable identity for one symbol generation inside one window."""

    window_id: str
    generation: int
    code: str


@dataclass(frozen=True, slots=True)
class KlineTaskTicket:
    """Unique receipt for one submitted or latest-pending window task."""

    identity: KlineLoadIdentity
    stage: str
    sequence: int


class KlineLoadController:
    """Own generation, task IDs and the committed frame for one window."""

    def __init__(self, *, window_id: str | None = None) -> None:
        normalized_id = str(window_id or uuid.uuid4().hex).strip()
        if not normalized_id or ":" in normalized_id:
            raise ValueError("window_id must be non-empty and must not contain ':'")
        self._window_id = normalized_id
        self._generation = 0
        self._current_identity: KlineLoadIdentity | None = None
        self._frame_owner: KlineLoadIdentity | None = None
        self._task_sequence = 0
        self._running_task: KlineTaskTicket | None = None
        self._pending_task: KlineTaskTicket | None = None
        self._closed = False

    @property
    def window_id(self) -> str:
        return self._window_id

    @property
    def current_identity(self) -> KlineLoadIdentity | None:
        return self._current_identity

    @property
    def frame_owner(self) -> KlineLoadIdentity | None:
        return self._frame_owner

    @property
    def running_task(self) -> KlineTaskTicket | None:
        return self._running_task

    @property
    def pending_task(self) -> KlineTaskTicket | None:
        return self._pending_task

    @property
    def closed(self) -> bool:
        return self._closed

    @property
    def generation(self) -> int:
        return self._generation

    def begin(self, code: str) -> KlineLoadIdentity:
        if self._closed:
            raise RuntimeError("K-line load controller is closed")
        normalized_code = str(code or "").strip()
        if not normalized_code:
            raise ValueError("code must not be blank")
        self._generation += 1
        identity = KlineLoadIdentity(self._window_id, self._generation, normalized_code)
        self._current_identity = identity
        self._frame_owner = None
        self._pending_task = None
        return identity

    def is_current(self, identity: KlineLoadIdentity | None) -> bool:
        return not self._closed and identity is not None and identity == self._current_identity

    def claim_frame(self, identity: KlineLoadIdentity) -> bool:
        if not self.is_current(identity):
            return False
        self._frame_owner = identity
        return True

    def owns_current_frame(self, code: str, generation: int) -> bool:
        owner = self._frame_owner
        return bool(
            not self._closed
            and owner is not None
            and owner == self._current_identity
            and owner.code == str(code or "").strip()
            and owner.generation == int(generation)
        )

    def request_task(self, identity: KlineLoadIdentity, stage: str) -> tuple[KlineTaskTicket, bool]:
        """Claim the sole running slot or replace the sole latest-pending task."""
        if identity.window_id != self._window_id:
            raise ValueError("task identity belongs to another K-line window")
        normalized_stage = self._normalize_stage(stage)
        if not self.is_current(identity):
            raise RuntimeError("cannot queue a stale or closed K-line task")
        self._task_sequence += 1
        ticket = KlineTaskTicket(identity, normalized_stage, self._task_sequence)
        if self._running_task is None:
            self._running_task = ticket
            self._pending_task = None
            return ticket, True
        self._pending_task = ticket
        return ticket, False

    def settle_task(self, ticket: KlineTaskTicket) -> KlineTaskTicket | None:
        """Release an exact terminated receipt and promote only the latest pending task."""
        if ticket.identity.window_id != self._window_id:
            raise ValueError("task ticket belongs to another K-line window")
        if self._running_task != ticket:
            return None
        self._running_task = None
        pending = self._pending_task
        self._pending_task = None
        if self._closed or pending is None or not self.is_current(pending.identity):
            return None
        self._running_task = pending
        return pending

    @staticmethod
    def _normalize_stage(stage: str) -> str:
        normalized_stage = str(stage or "").strip().lower()
        if _TASK_STAGE_PATTERN.fullmatch(normalized_stage) is None:
            raise ValueError("task stage must contain lowercase letters, digits or single hyphens")
        return normalized_stage

    def task_id(self, stage: str, *, identity: KlineLoadIdentity | None = None) -> str:
        normalized_stage = self._normalize_stage(stage)
        target = identity or self._current_identity
        if target is None:
            raise RuntimeError("a K-line generation must begin before building a task id")
        if target.window_id != self._window_id:
            raise ValueError("task identity belongs to another K-line window")
        return f"kline:{self._window_id}:{target.generation}:{normalized_stage}"

    def close(self) -> None:
        self._closed = True
        self._current_identity = None
        self._frame_owner = None
        self._pending_task = None

    def reopen_lease(self) -> None:
        """Reuse one physical window without reusing an old task identity."""
        if self._running_task is not None:
            raise RuntimeError("cannot reopen K-line lease while a task is running")
        self._closed = False
        self._current_identity = None
        self._frame_owner = None
        self._pending_task = None
