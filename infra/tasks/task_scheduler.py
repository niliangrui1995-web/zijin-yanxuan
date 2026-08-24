# -*- coding: utf-8 -*-
"""统一任务调度管理中心 — 替代散落的 threading.Thread

提供 run_in_background() 便捷方法：
- 后台执行函数
- 结果通过 pyqtSignal 安全回传到主线程
- 自动异常捕获与日志
"""

import os
import threading
import time
import traceback
import uuid
from collections.abc import Mapping
from contextlib import suppress
from functools import partial
from types import MappingProxyType
from typing import Any

from PyQt6.QtCore import QObject, QRunnable, QThread, QThreadPool, pyqtSignal, pyqtSlot

from core.task_errors import UserFacingTaskError
from infra.tasks.lifecycle import (
    CancellationToken,
    TaskCancelledError,
    call_with_supported_kwargs,
    invoke_with_cancellation,
)

DEFAULT_TASK_THREAD_POOL_MAX = 12
_TASK_POOL_OPERATION_ERRORS = (AttributeError, OSError, RuntimeError, TypeError, ValueError)


def _task_thread_pool_max_count(env: dict[str, str] | None = None) -> int:
    source = os.environ if env is None else env
    raw = str(source.get("VCP_TASK_THREAD_POOL_MAX_THREADS") or DEFAULT_TASK_THREAD_POOL_MAX).strip()
    try:
        return max(1, int(raw))
    except ValueError:
        return DEFAULT_TASK_THREAD_POOL_MAX


class _WorkerSignals(QObject):
    """Worker 内部信号（跨线程回传结果）"""

    finished = pyqtSignal(object)  # 成功: 传回结果
    error = pyqtSignal(str)  # 失败: 传回错误信息
    progress = pyqtSignal(int, str)  # 进度: pct, msg
    terminated = pyqtSignal()  # 任意终态（成功、失败或取消）


class BackgroundWorker(QRunnable):
    """通用后台 Worker — 包装任意可调用对象"""

    def __init__(
        self,
        fn,
        *args,
        thread_priority=None,
        cancellation_token: CancellationToken | None = None,
        timeout_sec: float | None = None,
        **kwargs,
    ):
        super().__init__()
        self.fn = fn
        self.args = args
        self.kwargs = kwargs
        self.thread_priority = thread_priority
        self.cancellation_token = cancellation_token or CancellationToken.with_timeout(timeout_sec)
        self.signals = _WorkerSignals()
        self.terminated_event = threading.Event()
        self.task_id = ""
        # 不自动删除，由 active_workers 字典持有引用控制生命周期
        self.setAutoDelete(False)

    def cancel(self, reason: str = "cancelled"):
        self.cancellation_token.cancel(reason)

    def _apply_thread_priority(self):
        if self.thread_priority is None:
            return None, None
        try:
            thread = QThread.currentThread()
            if thread is None:
                return None, None
            previous_priority = thread.priority()
            thread.setPriority(self.thread_priority)
            return thread, previous_priority
        except (AttributeError, RuntimeError, TypeError, ValueError):
            return None, None

    @staticmethod
    def _restore_thread_priority(thread, previous_priority) -> None:
        if thread is None or previous_priority is None:
            return
        with suppress(AttributeError, RuntimeError, TypeError, ValueError):
            thread.setPriority(previous_priority)

    @staticmethod
    def _safe_emit(signal, *args) -> None:
        with suppress(AttributeError, RuntimeError):
            signal.emit(*args)

    @classmethod
    def _safe_emit_named(cls, signals, name: str, *args) -> None:
        with suppress(AttributeError, RuntimeError):
            cls._safe_emit(getattr(signals, name, None), *args)

    @pyqtSlot()
    def run(self):
        task_label = self.task_id or getattr(self.fn, "__name__", "worker")
        priority_thread = None
        previous_priority = None
        try:
            self.cancellation_token.raise_if_cancelled()
            priority_thread, previous_priority = self._apply_thread_priority()
            result = invoke_with_cancellation(
                self.fn,
                self.cancellation_token,
                *self.args,
                **self.kwargs,
            )
            self._safe_emit_named(self.signals, "finished", result)
        except TaskCancelledError:
            pass
        except UserFacingTaskError as e:
            if self.cancellation_token.cancelled:
                return
            from core.logger import get_logger

            get_logger(__name__).warning(f"[任务调度][{task_label}] {e.log_message}")
            self._safe_emit_named(self.signals, "error", e.user_message)
        except TimeoutError as e:
            if self.cancellation_token.cancelled and not self.cancellation_token.reason == "deadline_exceeded":
                return
            from core.logger import get_logger

            get_logger(__name__).warning(f"[任务调度][{task_label}] 后台任务超时: {e}")
            self._safe_emit_named(self.signals, "error", str(e))
        except Exception as e:
            if self.cancellation_token.cancelled:
                return
            tb = traceback.format_exc()
            from core.logger import get_logger

            get_logger(__name__).error(f"[任务调度][{task_label}] Worker 异常: {e}\n{tb}")
            # 无论是否被取消都 emit error，确保 _cleanup 触发清理 active_workers
            self._safe_emit_named(self.signals, "error", str(e))
        finally:
            self._restore_thread_priority(priority_thread, previous_priority)
            self.terminated_event.set()
            self._safe_emit_named(self.signals, "terminated")


def _connect_queued(signal: Any, callback: Any) -> None:
    """Connect through an Any boundary shared by PyQt and lightweight test fakes."""
    from PyQt6.QtCore import Qt

    signal.connect(callback, type=Qt.ConnectionType.QueuedConnection)


def _deliver_submission_terminated_callback(on_terminated, task_id: str) -> None:
    """Contain an application terminal callback after a failed task submission."""
    if on_terminated is None:
        return
    try:
        on_terminated()
    except Exception:  # noqa: BLE001 - application callback must not mask submission failure.
        from core.logger import get_logger

        get_logger(__name__).exception(f"[TaskManager] 后台任务 '{task_id}' 未启动终态回调异常")


def _is_current_worker_delivery(manager, task_id: str, worker) -> bool:
    with manager._lock:
        current = manager.active_workers.get(task_id)
        token = getattr(worker, "cancellation_token", None)
        return current is worker and not bool(getattr(token, "cancelled", False))


def _deliver_worker_success(manager, task_id: str, worker, on_success, result) -> None:
    if _is_current_worker_delivery(manager, task_id, worker) and on_success is not None:
        on_success(result)


def _deliver_worker_error(manager, task_id: str, worker, on_error, error_message: str) -> None:
    with manager._lock:
        current = manager.active_workers.get(task_id)
        token = getattr(worker, "cancellation_token", None)
        if current is not worker or bool(getattr(token, "cancelled", False)):
            return
        manager._failed_count += 1
    on_error(error_message)


def _handle_terminated(manager, task_id: str, worker, on_terminated) -> None:
    deliver_terminated = False
    with manager._lock:
        current = manager.active_workers.get(task_id)
        if current is worker:
            manager.active_workers.pop(task_id, None)
            deliver_terminated = True
        else:
            retired = manager._retired_workers.get(task_id, [])
            manager._retired_workers[task_id] = [item for item in retired if item is not worker]
            if not manager._retired_workers[task_id]:
                manager._retired_workers.pop(task_id, None)
    if not deliver_terminated:
        return
    if on_terminated is None:
        return
    try:
        on_terminated()
    except Exception:  # noqa: BLE001 - a Qt queued callback must not escape.
        from core.logger import get_logger

        get_logger(__name__).exception(f"[TaskManager] 后台任务 '{task_id}' 终态回调异常")


class GlobalTaskManager(QObject):
    """
    紫金研选 统一任务调度管理中心 (Task Manager)
    托管所有底层的异步计算、网络请求，提供「一键拦截」「并发池」等核心控制。

    v2: 新增 run_in_background() 便捷方法
    """

    _instance = None

    def __new__(cls, *args, **kwargs):
        if not cls._instance:
            cls._instance = super(GlobalTaskManager, cls).__new__(cls, *args, **kwargs)
        return cls._instance

    def __init__(self):
        super().__init__()
        if hasattr(self, "_initialized"):
            return
        self._initialized = True

        thread_pool = QThreadPool.globalInstance() or QThreadPool()
        self.thread_pool = thread_pool
        self.thread_pool.setMaxThreadCount(_task_thread_pool_max_count())

        self.active_workers: dict[str, BackgroundWorker] = {}
        self._retired_workers: dict[str, list[BackgroundWorker]] = {}
        self._lock = threading.RLock()
        self._shutting_down = False
        self._failed_count = 0

    def submit_task(self, worker: BackgroundWorker, task_id: str | None = None, *, priority: int | None = None) -> str:
        """提交 QRunnable Worker"""
        with self._lock:
            if self._shutting_down:
                return task_id or ""

            if task_id:
                existing = self.active_workers.get(task_id)
                if existing is not None:
                    return task_id
            else:
                task_id = str(uuid.uuid4())[:8]

            self.active_workers[task_id] = worker
            submitted = False
            try:
                if priority is None:
                    self.thread_pool.start(worker)
                else:
                    self.thread_pool.start(worker, int(priority))
                submitted = True
            finally:
                if not submitted:
                    if self.active_workers.get(task_id) is worker:
                        self.active_workers.pop(task_id, None)
                    worker.cancel("submission_failed")
                    worker.terminated_event.set()
            return task_id

    @staticmethod
    def _default_error_handler(task_id: str):
        def _handle(error_message: str) -> None:
            from core.logger import get_logger

            get_logger(__name__).error(f"[TaskManager] 后台任务 '{task_id}' 未捕获异常: {error_message}")

        return _handle

    def _connect_worker_callbacks(
        self,
        worker: BackgroundWorker,
        task_id: str,
        on_success,
        on_error,
        on_terminated,
    ) -> None:
        if on_error is None:
            on_error = self._default_error_handler(task_id)
        if on_success:
            _connect_queued(
                worker.signals.finished,
                partial(_deliver_worker_success, self, task_id, worker, on_success),
            )
        _connect_queued(
            worker.signals.error,
            partial(_deliver_worker_error, self, task_id, worker, on_error),
        )
        _connect_queued(
            worker.signals.terminated,
            partial(_handle_terminated, self, task_id, worker, on_terminated),
        )

    def run_in_background(
        self,
        fn,
        *args,
        on_success=None,
        on_error=None,
        on_terminated=None,
        task_id: str | None = None,
        task_priority: int | None = None,
        thread_priority=None,
        cancellation_token: CancellationToken | None = None,
        timeout_sec: float | None = None,
        **kwargs,
    ) -> str:
        """便捷方法：后台执行函数，结果通过 Qt 信号安全回传主线程
        参数:
            fn: 后台执行的函数
            *args, **kwargs: 传给 fn 的参数
            on_success: 主线程回调 fn(result)
            on_error: 主线程回调 fn(error_msg)
            task_id: 可选任务 ID
            cancellation_token: 可选共享取消令牌，调用方可在任务闭包中协作检查
            timeout_sec: 可选单调时钟截止时间；到期后不再回传成功结果

        返回:
            task_id
        """
        worker = BackgroundWorker(
            fn,
            *args,
            thread_priority=thread_priority,
            cancellation_token=cancellation_token,
            timeout_sec=timeout_sec,
            **kwargs,
        )

        # 完成后清理 active_workers
        tid = task_id or str(uuid.uuid4())[:8]
        worker.task_id = tid

        with self._lock:
            if self._shutting_down:
                return tid

            if task_id and task_id in self.active_workers:
                return tid

        self._connect_worker_callbacks(worker, tid, on_success, on_error, on_terminated)

        submitted = False
        try:
            if task_priority is None:
                result = self.submit_task(worker, tid)
            else:
                result = self.submit_task(worker, tid, priority=task_priority)
            submitted = True
            return result
        finally:
            if not submitted:
                _deliver_submission_terminated_callback(on_terminated, tid)

    def cancel_all(self, *, reason: str = "cancel_all"):
        """终极清退：取消排队任务并保留运行任务的物理终态跟踪。"""
        with self._lock:
            workers = list(self.active_workers.items())

        for _task_id, worker in workers:
            if hasattr(worker, "cancel"):
                try:
                    call_with_supported_kwargs(worker.cancel, reason=reason)
                except (AttributeError, OSError, RuntimeError, TypeError, ValueError):
                    pass

        try_take = getattr(self.thread_pool, "tryTake", None)
        if not callable(try_take):
            return

        taken: list[BackgroundWorker] = []
        for _task_id, worker in workers:
            if not isinstance(worker, BackgroundWorker):
                continue
            try:
                if try_take(worker) is True:
                    taken.append(worker)
            except _TASK_POOL_OPERATION_ERRORS:
                continue
        for worker in taken:
            worker.terminated_event.set()
            worker._safe_emit_named(worker.signals, "terminated")

    def abandon_task(self, task_id: str) -> bool:
        """放弃一个卡住任务的占位，允许同 task_id 后续重新提交。

        说明：
        - QRunnable 一旦已经进入阻塞态，Qt 线程池无法强杀。
        - 这里将其从活跃占位移入 retired 跟踪，并尽量调用 cancel()。
        - 适用于行情轮询这类需要 fail-open 的长寿命任务；旧任务若稍后返回，
          它的 cleanup 只会移除自己的 retired 记录，不会影响新任务。
        """
        with self._lock:
            worker = self.active_workers.pop(task_id, None)
            if worker is not None:
                self._retired_workers.setdefault(task_id, []).append(worker)

        if worker is None:
            return False

        if hasattr(worker, "cancel"):
            try:
                call_with_supported_kwargs(worker.cancel, reason="abandoned")
            except (AttributeError, OSError, RuntimeError, TypeError, ValueError):
                pass
        return True

    def cancel_task(self, task_id: str, *, reason: str = "cancelled") -> bool:
        """Cooperatively cancel one task without releasing its dedupe slot."""
        with self._lock:
            worker = self.active_workers.get(str(task_id or ""))
        if worker is None:
            return False
        try:
            call_with_supported_kwargs(worker.cancel, reason=reason)
        except (AttributeError, OSError, RuntimeError, ValueError):
            return False
        return True

    def wait_for_tasks(self, task_ids, *, timeout_ms: int = 750) -> bool:
        """Wait for a stable snapshot of selected workers using one total deadline."""
        normalized_ids = tuple(dict.fromkeys(str(task_id or "").strip() for task_id in task_ids))
        with self._lock:
            workers = []
            for task_id in normalized_ids:
                active = self.active_workers.get(task_id)
                if active is not None:
                    workers.append(active)
                workers.extend(self._retired_workers.get(task_id, ()))
            workers = list({id(worker): worker for worker in workers}.values())
        deadline = time.monotonic() + max(0, int(timeout_ms or 0)) / 1000.0
        for worker in workers:
            remaining = max(0.0, deadline - time.monotonic())
            terminated_event = getattr(worker, "terminated_event", None)
            if terminated_event is None or not terminated_event.wait(remaining):
                return False
        return True

    def shutdown(self, *, wait_timeout_ms: int = 750) -> bool:
        """禁止后续提交，协作取消现有任务，并执行有界线程池等待。"""
        with self._lock:
            self._shutting_down = True
        self.cancel_all(reason="manager_shutdown")
        try:
            result = self.thread_pool.waitForDone(max(0, int(wait_timeout_ms or 0)))
            return result if type(result) is bool else False
        except _TASK_POOL_OPERATION_ERRORS:
            return False

    @property
    def is_shutting_down(self) -> bool:
        with self._lock:
            return self._shutting_down

    def is_active_task(self, task_id: str) -> bool:
        with self._lock:
            return task_id in self.active_workers

    def is_task_unsettled(self, task_id: str) -> bool:
        normalized = str(task_id or "").strip()
        with self._lock:
            if normalized in self.active_workers:
                return True
            retired = self._retired_workers.get(normalized, [])
            unsettled = [
                worker
                for worker in retired
                if not bool(getattr(getattr(worker, "terminated_event", None), "is_set", lambda: False)())
            ]
            if unsettled:
                self._retired_workers[normalized] = unsettled
                return True
            self._retired_workers.pop(normalized, None)
            return False

    def is_task_token_active(self, task_id: str, cancellation_token: CancellationToken) -> bool:
        with self._lock:
            worker = self.active_workers.get(str(task_id or ""))
            return worker is not None and worker.cancellation_token is cancellation_token

    @property
    def active_count(self) -> int:
        with self._lock:
            return len(self.active_workers)

    @property
    def failed_count(self) -> int:
        """Return the cumulative number of delivered task failures."""
        with self._lock:
            return self._failed_count

    def runtime_health_snapshot(self) -> Mapping[str, Any]:
        """Return one immutable, lock-consistent task health snapshot."""
        with self._lock:
            items = tuple(sorted(self.active_workers.items(), key=lambda item: str(item[0])))
            workers = tuple(
                MappingProxyType(
                    {
                        "task_id": str(task_id),
                        "worker_class": worker.__class__.__name__,
                        "cancelled": bool(
                            getattr(getattr(worker, "cancellation_token", None), "cancelled", False)
                        ),
                    }
                )
                for task_id, worker in items
            )
            return MappingProxyType(
                {
                    "active_count": len(items),
                    "failed_count": self._failed_count,
                    "task_ids": tuple(str(task_id) for task_id, _worker in items),
                    "workers": workers,
                }
            )


# 全局单例
task_manager = GlobalTaskManager()
