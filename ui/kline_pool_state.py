# -*- coding: utf-8 -*-
"""Pure lifecycle state model for reusable K-line windows."""

from __future__ import annotations

from enum import Enum
from typing import Any


class KLinePoolState(Enum):
    """Lifecycle states for one reusable physical K-line window."""

    ACTIVE = "active"
    CLOSING = "closing"
    IDLE = "idle"
    TAINTED = "tainted"
    DISPOSED = "disposed"


_ALLOWED_POOL_TRANSITIONS: dict[KLinePoolState, frozenset[KLinePoolState]] = {
    KLinePoolState.ACTIVE: frozenset(
        {
            KLinePoolState.CLOSING,
            KLinePoolState.IDLE,
            KLinePoolState.TAINTED,
            KLinePoolState.DISPOSED,
        }
    ),
    KLinePoolState.CLOSING: frozenset(
        {KLinePoolState.IDLE, KLinePoolState.TAINTED, KLinePoolState.DISPOSED}
    ),
    KLinePoolState.IDLE: frozenset(
        {KLinePoolState.ACTIVE, KLinePoolState.TAINTED, KLinePoolState.DISPOSED}
    ),
    KLinePoolState.TAINTED: frozenset(
        {KLinePoolState.CLOSING, KLinePoolState.DISPOSED}
    ),
    KLinePoolState.DISPOSED: frozenset(),
}


def _legacy_pool_state(window: Any) -> KLinePoolState:
    values = getattr(window, "__dict__", {})
    if bool(values.get("_force_dispose", False)):
        return KLinePoolState.DISPOSED
    if bool(values.get("_pool_tainted", False)):
        return KLinePoolState.TAINTED
    if bool(values.get("_pool_idle", False)):
        return KLinePoolState.IDLE
    if bool(values.get("_closing", False)):
        return KLinePoolState.CLOSING
    return KLinePoolState.ACTIVE


def kline_pool_state_of(window: Any) -> KLinePoolState:
    """Return the enum state, with a compatibility read for lightweight test doubles."""
    state = getattr(window, "_pool_state", None)
    if isinstance(state, KLinePoolState):
        return state
    return _legacy_pool_state(window)


def initialize_kline_pool_state(
    window: Any, state: KLinePoolState = KLinePoolState.ACTIVE
) -> KLinePoolState:
    """Initialize lifecycle storage before any window work starts."""
    if not isinstance(state, KLinePoolState):
        raise TypeError("state must be a KLinePoolState")
    window._pool_state = state
    window._pool_transition_reason = "initialize"
    return state


def transition_kline_pool_state(
    window: Any,
    target: KLinePoolState,
    *,
    reason: str,
) -> KLinePoolState:
    """Validate and atomically replace the lifecycle state for any pool participant."""
    if not isinstance(target, KLinePoolState):
        raise TypeError("target must be a KLinePoolState")
    current = kline_pool_state_of(window)
    if current is target:
        return current
    if target not in _ALLOWED_POOL_TRANSITIONS[current]:
        raise RuntimeError(f"invalid KLine pool transition: {current.name} -> {target.name}")
    window._pool_state = target
    window._pool_transition_reason = str(reason or "unspecified")
    return target
