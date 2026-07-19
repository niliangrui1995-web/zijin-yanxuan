from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Mapping


@dataclass(frozen=True, slots=True)
class KLineSnapshot:
    """Immutable hand-off value for the latest complete chart state."""

    window_id: str
    code: str
    generation: int
    points: int
    version: int | str
    _payload_json: str

    @property
    def payload_json(self) -> str:
        return self._payload_json

    def payload(self) -> dict[str, Any]:
        """Return a fresh payload so consumers cannot mutate the saved snapshot."""
        return json.loads(self._payload_json)


@dataclass(frozen=True, slots=True)
class RecoveryDecision:
    allowed: bool
    reason: str


class KLineRuntimeLifecycleController:
    """Pure-Python visibility, snapshot and single-shot recovery state machine."""

    def __init__(self) -> None:
        self._hidden = False
        self._minimized = False
        self._closing = False
        self._recovery_used = False
        self._latest_snapshot: KLineSnapshot | None = None
        self._pending_snapshot: KLineSnapshot | None = None

    @property
    def runtime_active(self) -> bool:
        return not (self._closing or self._hidden or self._minimized)

    @property
    def latest_snapshot(self) -> KLineSnapshot | None:
        return self._latest_snapshot

    def record_snapshot(
        self,
        payload: Mapping[str, Any],
        *,
        code: str,
        generation: int,
        points: int,
        version: int | str,
        window_id: str = "",
    ) -> KLineSnapshot | None:
        return self.record_snapshot_json(
            json.dumps(
                payload,
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
            ),
            window_id=window_id,
            code=code,
            generation=generation,
            points=points,
            version=version,
        )

    def record_snapshot_json(
        self,
        payload_json: str,
        *,
        window_id: str,
        code: str,
        generation: int,
        points: int,
        version: int | str,
    ) -> KLineSnapshot | None:
        if self._closing:
            return None
        if not isinstance(payload_json, str):
            raise TypeError("payload_json must be a string")
        snapshot = KLineSnapshot(
            window_id=str(window_id),
            code=str(code),
            generation=int(generation),
            points=max(0, int(points)),
            version=version,
            _payload_json=payload_json,
        )
        self._latest_snapshot = snapshot
        self._pending_snapshot = snapshot
        return snapshot

    def take_pending_submission(self) -> KLineSnapshot | None:
        if not self.runtime_active:
            return None
        snapshot = self._pending_snapshot
        self._pending_snapshot = None
        return snapshot

    def set_visibility(
        self,
        *,
        hidden: bool | None = None,
        minimized: bool | None = None,
    ) -> KLineSnapshot | None:
        if hidden is not None:
            self._hidden = bool(hidden)
        if minimized is not None:
            self._minimized = bool(minimized)
        return self.take_pending_submission()

    def request_recovery(self, _browser_token: object) -> RecoveryDecision:
        if self._closing:
            return RecoveryDecision(False, "closing")
        if self._recovery_used:
            return RecoveryDecision(False, "recovery_already_used")
        self._recovery_used = True
        if self._latest_snapshot is not None:
            self._pending_snapshot = self._latest_snapshot
        return RecoveryDecision(True, "recovery_scheduled")

    def begin_close(self) -> None:
        self._closing = True
        self._pending_snapshot = None
        self._latest_snapshot = None
