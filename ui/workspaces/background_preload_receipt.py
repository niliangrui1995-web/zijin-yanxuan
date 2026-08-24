# -*- coding: utf-8 -*-
"""Cooperative cancellation receipts for serial workspace tab hydration."""

from __future__ import annotations

from collections.abc import Callable, Iterable

from app.services.ui_task_lifecycle_service import task_unsettled_status

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
        except Exception:  # noqa: BLE001 - cancellation compatibility is best effort.
            continue


def _lifecycle_task_ids_status(
    owner,
    lifecycle_names: Iterable[str],
) -> tuple[tuple[str, ...], bool]:
    names = tuple(lifecycle_names)
    if not names:
        return (), True
    lifecycle = getattr(owner, "_task_lifecycle", None)
    if lifecycle is None:
        return (), True
    task_ids_for = getattr(lifecycle, "task_ids_for", None)
    if not callable(task_ids_for):
        return (), False
    try:
        task_ids = task_ids_for(names)
        if isinstance(task_ids, (str, bytes)) or not isinstance(task_ids, Iterable):
            return (), False
        return _normalized_task_ids(task_ids), True
    except Exception:  # noqa: BLE001 - tracking failures must fail closed.
        return (), False


def _lifecycle_submissions_settled(
    owner,
    lifecycle_names: Iterable[str],
    *,
    tracking_state: dict[str, bool],
) -> Callable[[], bool] | None:
    names = tuple(lifecycle_names)
    if not names:
        return None
    lifecycle = getattr(owner, "_task_lifecycle", None)
    if lifecycle is None:
        return None
    settled_for = getattr(lifecycle, "submissions_settled_for", None)
    if not callable(settled_for):
        tracking_state["ok"] = False
        return lambda: False

    def _is_settled() -> bool:
        try:
            result = settled_for(names)
        except Exception:  # noqa: BLE001 - submission tracking must fail closed.
            tracking_state["ok"] = False
            return False
        if type(result) is not bool:
            tracking_state["ok"] = False
            return False
        return result

    return _is_settled


def _cancel_runner_tasks(cancel_task, task_ids: Iterable[str], *, reason: str) -> dict[str, bool]:
    results: dict[str, bool] = {}
    for task_id in task_ids:
        if not callable(cancel_task):
            results[task_id] = False
            continue
        try:
            result = cancel_task(task_id, reason=reason)
            results[task_id] = result is not False
        except Exception:  # noqa: BLE001 - cancellation compatibility is best effort.
            results[task_id] = False
    return results


def _task_active_status(is_active, task_id: str) -> bool | None:
    if not callable(is_active):
        return None
    try:
        result = is_active(task_id)
    except Exception:  # noqa: BLE001 - physical probe failures remain unknown.
        return None
    return result if type(result) is bool else None


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
    *settled_predicates: Callable[[], bool] | None,
) -> Callable[[], bool] | None:
    predicates = tuple(predicate for predicate in settled_predicates if callable(predicate))
    if not predicates:
        return None

    def _is_settled() -> bool:
        return all(predicate() is True for predicate in predicates)

    return _is_settled


class BackgroundPreloadCancellationReceipt:
    """Prove that cancelled preload work has actually left its worker slots."""

    def __init__(
        self,
        *,
        task_ids: Iterable[object] = (),
        task_ids_supplier: Callable[[], Iterable[object]] | None = None,
        is_task_active: Callable[[str], bool | None] | None = None,
        local_settled: Callable[[], bool] | None = None,
        tracking_ok: Callable[[], bool] | None = None,
        accepted: bool = True,
    ) -> None:
        self.task_ids = tuple(
            dict.fromkeys(str(task_id or "").strip() for task_id in task_ids if str(task_id or "").strip())
        )
        self._task_ids_supplier = task_ids_supplier
        self._is_task_active = is_task_active
        self._local_settled = local_settled
        self._tracking_ok = tracking_ok
        self.accepted = bool(accepted)

    @classmethod
    def immediate(cls) -> "BackgroundPreloadCancellationReceipt":
        return cls()

    def active_task_ids(self) -> tuple[str, ...]:
        return self._task_status_ids()[0]

    def unknown_task_ids(self) -> tuple[str, ...]:
        return self._task_status_ids()[1]

    def _task_status_ids(self) -> tuple[tuple[str, ...], tuple[str, ...]]:
        current_task_ids = self.current_task_ids()
        if self._is_task_active is None:
            return (), current_task_ids
        active: list[str] = []
        unknown: list[str] = []
        for task_id in current_task_ids:
            status = _task_active_status(self._is_task_active, task_id)
            if status is True:
                active.append(task_id)
            elif status is None:
                unknown.append(task_id)
        return tuple(active), tuple(unknown)

    def current_task_ids(self) -> tuple[str, ...]:
        dynamic_task_ids: tuple[str, ...] = ()
        if self._task_ids_supplier is not None:
            try:
                dynamic_task_ids = _normalized_task_ids(self._task_ids_supplier())
            except Exception:  # noqa: BLE001 - dynamic tracking must fail closed.
                dynamic_task_ids = ()
        return _normalized_task_ids((*self.task_ids, *dynamic_task_ids))

    def is_settled(self) -> bool:
        active_task_ids, unknown_task_ids = self._task_status_ids()
        if (
            not self.accepted
            or not self._tracking_is_ok()
            or active_task_ids
            or unknown_task_ids
        ):
            return False
        if self._local_settled is None:
            return not self.active_task_ids()
        try:
            local_settled = self._local_settled() is True
        except Exception:  # noqa: BLE001 - local settlement must fail closed.
            return False
        active_task_ids, unknown_task_ids = self._task_status_ids()
        return bool(
            local_settled
            and self._tracking_is_ok()
            and not active_task_ids
            and not unknown_task_ids
        )

    def _tracking_is_ok(self) -> bool:
        if self._tracking_ok is None:
            return True
        try:
            return self._tracking_ok() is True
        except Exception:  # noqa: BLE001 - tracking failures must fail closed.
            return False

    def status(self) -> dict:
        active_task_ids, unknown_task_ids = self._task_status_ids()
        tracking_ok = self._tracking_is_ok() and not unknown_task_ids
        local_settled = False
        if not active_task_ids and self.accepted and tracking_ok:
            if self._local_settled is None:
                local_settled = True
            else:
                try:
                    local_settled = self._local_settled() is True
                except Exception:  # noqa: BLE001 - local settlement must fail closed.
                    local_settled = False
        if local_settled:
            active_task_ids, unknown_task_ids = self._task_status_ids()
            tracking_ok = self._tracking_is_ok() and not unknown_task_ids
            local_settled = bool(not active_task_ids and not unknown_task_ids and tracking_ok)
        return {
            "accepted": self.accepted,
            "tracking_ok": tracking_ok,
            "task_ids": list(self.current_task_ids()),
            "active_task_ids": list(active_task_ids),
            "unknown_task_ids": list(unknown_task_ids),
            "local_settled": local_settled,
            "settled": bool(self.accepted and tracking_ok and not active_task_ids and local_settled),
        }


def cancel_background_preload_tasks(
    owner,
    *,
    snapshot_owner=None,
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

    if snapshot_owner is None:
        snapshot_owner = owner
    supplemental_lifecycle_names, supplemental_task_ids, cancel_supplemental, supplemental_settled = (
        _workspace_snapshot_cancellation(snapshot_owner)
    )
    lifecycle_names = tuple(lifecycle_names)
    tracking_state = {"ok": True}

    def _read_lifecycle_task_ids(lifecycle_owner, names: Iterable[str]) -> tuple[str, ...]:
        tracked_ids, tracked_ok = _lifecycle_task_ids_status(lifecycle_owner, names)
        tracking_state["ok"] = bool(tracking_state["ok"] and tracked_ok)
        return tracked_ids

    if snapshot_owner is owner:
        lifecycle_names = tuple(
            dict.fromkeys((*lifecycle_names, *supplemental_lifecycle_names))
        )
        lifecycle_task_ids = _read_lifecycle_task_ids(owner, lifecycle_names)
        _cancel_lifecycle_tasks(owner, lifecycle_names, reason=reason)
        submission_settled = _lifecycle_submissions_settled(
            owner,
            lifecycle_names,
            tracking_state=tracking_state,
        )
    else:
        lifecycle_task_ids = _normalized_task_ids(
            (
                *_read_lifecycle_task_ids(owner, lifecycle_names),
                *_read_lifecycle_task_ids(snapshot_owner, supplemental_lifecycle_names),
            )
        )
        _cancel_lifecycle_tasks(owner, lifecycle_names, reason=reason)
        _cancel_lifecycle_tasks(snapshot_owner, supplemental_lifecycle_names, reason=reason)
        submission_settled = _combined_local_settled(
            _lifecycle_submissions_settled(
                owner,
                lifecycle_names,
                tracking_state=tracking_state,
            ),
            _lifecycle_submissions_settled(
                snapshot_owner,
                supplemental_lifecycle_names,
                tracking_state=tracking_state,
            ),
        )
    normalized_task_ids = _normalized_task_ids(
        (*task_ids, *lifecycle_task_ids, *supplemental_task_ids)
    )

    def _current_lifecycle_task_ids() -> tuple[str, ...]:
        if snapshot_owner is owner:
            return _read_lifecycle_task_ids(owner, lifecycle_names)
        return _normalized_task_ids(
            (
                *_read_lifecycle_task_ids(owner, lifecycle_names),
                *_read_lifecycle_task_ids(snapshot_owner, supplemental_lifecycle_names),
            )
        )

    def is_active(task_id: str) -> bool | None:
        return task_unsettled_status(runner, task_id)

    cancel_task = getattr(runner, "cancel_task", None)
    active_before = {
        task_id: _task_active_status(is_active, task_id)
        for task_id in normalized_task_ids
    }
    cancellation_results = _cancel_runner_tasks(cancel_task, normalized_task_ids, reason=reason)
    cancellation_accepted = all(
        active_before[task_id] is False or cancellation_results.get(task_id, False)
        for task_id in normalized_task_ids
    )
    probe_confirmed = not normalized_task_ids or (
        callable(is_active)
        and all(status is not None for status in active_before.values())
    )

    reset_state()
    if cancel_supplemental is not None:
        try:
            cancel_supplemental()
        except Exception:  # noqa: BLE001 - supplemental cancellation is best effort.
            pass
    return BackgroundPreloadCancellationReceipt(
        task_ids=normalized_task_ids,
        task_ids_supplier=_current_lifecycle_task_ids,
        is_task_active=is_active if callable(is_active) else None,
        local_settled=_combined_local_settled(
            local_settled,
            supplemental_settled,
            submission_settled,
        ),
        tracking_ok=lambda: bool(tracking_state["ok"]),
        accepted=(
            tracking_state["ok"]
            and probe_confirmed
            and cancellation_accepted
        ),
    )
