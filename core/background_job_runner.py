"""统一封装命名后台任务入口，避免 UI 层直接操作 ``core.task_manager``。"""

from __future__ import annotations

from infra.tasks.typed_task_registry import TaskKeyLike, task_id_of


def _resolve_default_manager():
    from core.task_manager import task_manager

    return task_manager


class BackgroundJobRunner:
    def __init__(self, manager=None):
        self._manager = manager

    def _resolve_manager(self):
        return self._manager or _resolve_default_manager()

    def run(self, task_id: TaskKeyLike, fn, *args, on_success=None, on_error=None, **kwargs) -> str:
        normalized_task_id = task_id_of(task_id)
        return self._resolve_manager().run_in_background(
            fn,
            *args,
            on_success=on_success,
            on_error=on_error,
            task_id=normalized_task_id,
            **kwargs,
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
        manager = self._resolve_manager()
        abandon_task = getattr(manager, "abandon_task", None)
        if callable(abandon_task):
            return bool(abandon_task(task_id_of(task_id)))
        return False

    def abandon_task(self, task_id: TaskKeyLike) -> bool:
        return self.abandon(task_id)

    def cancel_task(self, task_id: TaskKeyLike, *, reason: str = "cancelled") -> bool:
        manager = self._resolve_manager()
        cancel_task = getattr(manager, "cancel_task", None)
        if not callable(cancel_task):
            return False
        return bool(cancel_task(task_id_of(task_id), reason=reason))

    def wait_for_tasks(self, task_ids, *, timeout_ms: int = 750) -> bool:
        manager = self._resolve_manager()
        wait_for_tasks = getattr(manager, "wait_for_tasks", None)
        if not callable(wait_for_tasks):
            return False
        normalized = tuple(task_id_of(task_id) for task_id in task_ids)
        return bool(wait_for_tasks(normalized, timeout_ms=timeout_ms))

    def is_active(self, task_id: TaskKeyLike) -> bool:
        manager = self._resolve_manager()
        is_active_task = getattr(manager, "is_active_task", None)
        if callable(is_active_task):
            return bool(is_active_task(task_id_of(task_id)))
        return False

    def is_active_task(self, task_id: TaskKeyLike) -> bool:
        return self.is_active(task_id)

    def cancel_all(self):
        manager = self._resolve_manager()
        cancel_all = getattr(manager, "cancel_all", None)
        if callable(cancel_all):
            return cancel_all()
        return None

    def shutdown(self):
        manager = self._resolve_manager()
        shutdown = getattr(manager, "shutdown", None)
        if callable(shutdown):
            return shutdown()
        return None

    @property
    def is_shutting_down(self) -> bool:
        manager = self._resolve_manager()
        return bool(getattr(manager, "is_shutting_down", False))

    @property
    def active_count(self) -> int:
        manager = self._resolve_manager()
        return int(getattr(manager, "active_count", 0) or 0)


background_job_runner = BackgroundJobRunner()
