# -*- coding: utf-8 -*-
"""Cooperative cancellation receipts for serial workspace tab hydration."""

from __future__ import annotations

from collections.abc import Callable, Iterable

_WORKSPACE_SNAPSHOT_LIFECYCLE = "workspace_background_snapshot"


def _normalized_task_ids(task_ids: Iterable[object]) -> tuple[str, ...]:
    normalized = (
        str(getattr(task_id, "task_id", task_id) or "").strip()
        for task_id in task_ids
    )
    return tuple(dict.fromkeys(task_id for task_id in normalized if task_id))


def _cancel_lifecycle_tasks(owner, lifecycle_names: Iterable[str], *, reason: str) -> None:
    lifecycle = getattr(owner, "_task_lifecycle", None)
    cancel = getattr(lifecycle, "cancel", None)
    if not callable(cancel):
        return
    for name in lifecycle_names:
        try:
            cancel(str(name), reason=reason)
        except (AttributeError, RuntimeError, TypeError, ValueError):
            continue


def _cancel_runner_tasks(cancel_task, task_ids: Iterable[str], *, reason: str) -> None:
    if not callable(cancel_task):
        return
    for task_id in task_ids:
        try:
            cancel_task(task_id, reason=reason)
        except (AttributeError, RuntimeError, TypeError, ValueError):
            continue


def _workspace_snapshot_cancellation(owner) -> tuple[tuple[str, ...], tuple[object, ...], Callable[[], None] | None, Callable[[], bool] | None]:
    task_id = str(getattr(owner, "_workspace_background_snapshot_task_id", "") or "").strip()
    started = bool(getattr(owner, "_workspace_background_snapshot_started", False))
    if not started and not task_id:
        return (), (), None, None
    cancel_local = getattr(owner, "_cancel_workspace_background_snapshot_preload", None)
    local_settled = getattr(owner, "_workspace_background_snapshot_preload_settled", None)
    return (
        (_WORKSPACE_SNAPSHOT_LIFECYCLE,),
        (task_id,) if task_id else (),
        cancel_local if callable(cancel_local) else None,
        local_settled if callable(local_settled) else None,
    )


def _combined_local_settled(
    primary: Callable[[], bool] | None,
    supplemental: Callable[[], bool] | None,
) -> Callable[[], bool] | None:
    predicates = tuple(predicate for predicate in (primary, supplemental) if callable(predicate))
    if not predicates:
        return None

    def _is_settled() -> bool:
        return all(bool(predicate()) for predicate in predicates)

    return _is_settled


class BackgroundPreloadCancellationReceipt:
    """Prove that cancelled preload work has actually left its worker slots."""

    def __init__(
        self,
        *,
        task_ids: Iterable[object] = (),
        is_task_active: Callable[[str], bool] | None = None,
        local_settled: Callable[[], bool] | None = None,
        accepted: bool = True,
    ) -> None:
        self.task_ids = tuple(
            dict.fromkeys(str(task_id or "").strip() for task_id in task_ids if str(task_id or "").strip())
        )
        self._is_task_active = is_task_active
        self._local_settled = local_settled
        self.accepted = bool(accepted)

    @classmethod
    def immediate(cls) -> "BackgroundPreloadCancellationReceipt":
        return cls()

    def active_task_ids(self) -> tuple[str, ...]:
        if self._is_task_active is None:
            return ()
        active: list[str] = []
        for task_id in self.task_ids:
            try:
                if self._is_task_active(task_id):
                    active.append(task_id)
            except (AttributeError, RuntimeError, TypeError, ValueError):
                active.append(task_id)
        return tuple(active)

    def is_settled(self) -> bool:
        if not self.accepted or self.active_task_ids():
            return False
        if self._local_settled is None:
            return True
        try:
            return bool(self._local_settled())
        except (AttributeError, RuntimeError, TypeError, ValueError):
            return False

    def status(self) -> dict:
        active_task_ids = self.active_task_ids()
        local_settled = False
        if not active_task_ids and self.accepted:
            if self._local_settled is None:
                local_settled = True
            else:
                try:
                    local_settled = bool(self._local_settled())
                except (AttributeError, RuntimeError, TypeError, ValueError):
                    local_settled = False
        return {
            "accepted": self.accepted,
            "task_ids": list(self.task_ids),
            "active_task_ids": list(active_task_ids),
            "local_settled": local_settled,
            "settled": bool(self.accepted and not active_task_ids and local_settled),
        }


def cancel_background_preload_tasks(
    owner,
    *,
    lifecycle_names: Iterable[str],
    task_ids: Iterable[object],
    reason: str,
    reset_state: Callable[[], None],
    local_settled: Callable[[], bool] | None = None,
    runner=None,
) -> BackgroundPreloadCancellationReceipt:
    """Cancel owned preload leases and return a non-blocking settlement receipt."""
    if runner is None:
        from app.services.ui_task_service import background_job_runner as runner

    supplemental_lifecycle_names, supplemental_task_ids, cancel_supplemental, supplemental_settled = (
        _workspace_snapshot_cancellation(owner)
    )
    combined_lifecycle_names = tuple(
        dict.fromkeys((*lifecycle_names, *supplemental_lifecycle_names))
    )
    _cancel_lifecycle_tasks(owner, combined_lifecycle_names, reason=reason)
    normalized_task_ids = _normalized_task_ids((*task_ids, *supplemental_task_ids))
    is_active = getattr(runner, "is_active_task", None)
    cancel_task = getattr(runner, "cancel_task", None)
    _cancel_runner_tasks(cancel_task, normalized_task_ids, reason=reason)

    reset_state()
    if cancel_supplemental is not None:
        try:
            cancel_supplemental()
        except (AttributeError, RuntimeError, TypeError, ValueError):
            pass
    return BackgroundPreloadCancellationReceipt(
        task_ids=normalized_task_ids,
        is_task_active=is_active if callable(is_active) else None,
        local_settled=_combined_local_settled(local_settled, supplemental_settled),
        accepted=callable(is_active) or not normalized_task_ids,
    )
