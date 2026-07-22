"""统一封装命名后台任务入口，避免 UI 层直接操作 ``core.task_manager``。"""

from __future__ import annotations

from infra.tasks.lifecycle import bounded_wait_for_tasks_status
from infra.tasks.typed_task_registry import TaskKeyLike, task_id_of


def _resolve_default_manager():
    from core.task_manager import task_manager

    return task_manager


class BackgroundJobRunner:
    def __init__(self, manager=None):
        self._manager = manager

    def _resolve_manager(self):
        return self._manager or _resolve_default_manager()

    def _manager_method(self, method_name: str):
        method = getattr(self._resolve_manager(), method_name, None)
        return method if callable(method) else None

    def run(self, task_id: TaskKeyLike, fn, *args, on_success=None, on_error=None, **kwargs) -> str:
        return BackgroundJobRunner.run_in_background(
            self, fn, *args, on_success=on_success, on_error=on_error, task_id=task_id, **kwargs
        )

    def run_in_background(
        self,
        fn,
        *args,
        on_success=None,
        on_error=None,
        task_id: TaskKeyLike = None,
        **kwargs,
    ) -> str:
        normalized_task_id = task_id_of(task_id)
        return self._resolve_manager().run_in_background(
            fn,
            *args,
            on_success=on_success,
            on_error=on_error,
            task_id=normalized_task_id,
            **kwargs,
        )

    def abandon(self, task_id: TaskKeyLike) -> bool:
        method = self._manager_method("abandon_task")
        return bool(method(task_id_of(task_id))) if method is not None else False

    def abandon_task(self, task_id: TaskKeyLike) -> bool:
        return self.abandon(task_id)

    def cancel_task(self, task_id: TaskKeyLike, *, reason: str = "cancelled") -> bool:
        method = self._manager_method("cancel_task")
        return bool(method(task_id_of(task_id), reason=reason)) if method is not None else False

    def wait_for_tasks(self, task_ids, *, timeout_ms: int = 750) -> bool | None:
        manager = self._resolve_manager()
        if not callable(getattr(manager, "wait_for_tasks", None)):
            return None
        return bounded_wait_for_tasks_status(
            manager,
            tuple(task_id_of(task_id) for task_id in task_ids),
            timeout_ms=timeout_ms,
        )

    def is_active(self, task_id: TaskKeyLike) -> bool:
        method = self._manager_method("is_active_task")
        return bool(method(task_id_of(task_id))) if method is not None else False

    def is_active_task(self, task_id: TaskKeyLike) -> bool:
        return self.is_active(task_id)

    def is_task_unsettled(self, task_id: TaskKeyLike) -> bool | None:
        method = self._manager_method("is_task_unsettled")
        if method is None:
            return None
        try:
            result = method(task_id_of(task_id))
        except Exception:  # noqa: BLE001 - capability failures remain unknown.
            return None
        return result if type(result) is bool else None

    def is_task_token_active(self, task_id: TaskKeyLike, cancellation_token) -> bool | None:
        method = self._manager_method("is_task_token_active")
        if method is None:
            return None
        try:
            result = method(task_id_of(task_id), cancellation_token)
        except Exception:  # noqa: BLE001 - capability failures remain unknown.
            return None
        return result if type(result) is bool else None

    def cancel_all(self):
        method = self._manager_method("cancel_all")
        return method() if method is not None else None

    def shutdown(self):
        method = self._manager_method("shutdown")
        return method() if method is not None else None

    @property
    def is_shutting_down(self) -> bool:
        manager = self._resolve_manager()
        return bool(getattr(manager, "is_shutting_down", False))

    @property
    def active_count(self) -> int:
        manager = self._resolve_manager()
        return int(getattr(manager, "active_count", 0) or 0)


background_job_runner = BackgroundJobRunner()
