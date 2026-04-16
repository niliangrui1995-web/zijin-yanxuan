# -*- coding: utf-8 -*-
"""统一任务调度管理中心 — 替代散落的 threading.Thread

提供 run_in_background() 便捷方法：
- 后台执行函数
- 结果通过 pyqtSignal 安全回传到主线程
- 自动异常捕获与日志
"""

import threading
import traceback
import uuid

from PyQt6.QtCore import QObject, QRunnable, QThreadPool, pyqtSignal, pyqtSlot


class _WorkerSignals(QObject):
    """Worker 内部信号（跨线程回传结果）"""
    finished = pyqtSignal(object)   # 成功: 传回结果
    error = pyqtSignal(str)         # 失败: 传回错误信息
    progress = pyqtSignal(int, str) # 进度: pct, msg


class UserFacingTaskError(Exception):
    """预期内、可恢复的后台任务失败。

    用途：
    - 日志层按 warning 输出，不打印整段 Traceback 污染终端
    - UI 层收到更友好的 user_message
    """

    def __init__(self, user_message: str, log_message: str | None = None):
        super().__init__(user_message)
        self.user_message = str(user_message or "").strip()
        self.log_message = str(log_message or self.user_message).strip()


class BackgroundWorker(QRunnable):
    """通用后台 Worker — 包装任意可调用对象"""

    def __init__(self, fn, *args, **kwargs):
        super().__init__()
        self.fn = fn
        self.args = args
        self.kwargs = kwargs
        self.signals = _WorkerSignals()
        self._is_cancelled = False
        self.task_id = ""
        # 不自动删除，由 active_workers 字典持有引用控制生命周期
        self.setAutoDelete(False)

    def cancel(self):
        self._is_cancelled = True

    @pyqtSlot()
    def run(self):
        if self._is_cancelled:
            return
        task_label = self.task_id or getattr(self.fn, "__name__", "worker")
        try:
            result = self.fn(*self.args, **self.kwargs)
            if not self._is_cancelled:
                try:
                    self.signals.finished.emit(result)
                except RuntimeError:
                    pass  # 信号对象已被销毁，安全忽略
        except UserFacingTaskError as e:
            from core.logger import get_logger
            get_logger(__name__).warning(f"[任务调度][{task_label}] {e.log_message}")
            try:
                self.signals.error.emit(e.user_message)
            except RuntimeError:
                pass
        except TimeoutError as e:
            from core.logger import get_logger
            get_logger(__name__).warning(f"[任务调度][{task_label}] 后台任务超时: {e}")
            try:
                self.signals.error.emit(str(e))
            except RuntimeError:
                pass
        except (AttributeError, KeyError, OSError, RuntimeError, TypeError, ValueError) as e:
            tb = traceback.format_exc()
            from core.logger import get_logger
            get_logger(__name__).error(f"[任务调度][{task_label}] Worker 异常: {e}\n{tb}")
            # 无论是否被取消都 emit error，确保 _cleanup 触发清理 active_workers
            try:
                self.signals.error.emit(str(e))
            except RuntimeError:
                pass  # 信号对象已被销毁，安全忽略


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
        if hasattr(self, '_initialized'):
            return
        self._initialized = True

        self.thread_pool = QThreadPool.globalInstance()
        self.thread_pool.setMaxThreadCount(
            max(self.thread_pool.maxThreadCount(), 8)
        )

        self.active_workers: dict[str, BackgroundWorker] = {}
        self._lock = threading.RLock()
        self._shutting_down = False

    def submit_task(self, worker: QRunnable, task_id: str = None) -> str:
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
            self.thread_pool.start(worker)
            return task_id

    def run_in_background(self, fn, *args,
                          on_success=None,
                          on_error=None,
                          task_id: str = None,
                          **kwargs) -> str:
        """便捷方法：后台执行函数，结果通过 Qt 信号安全回传主线程

        参数:
            fn: 后台执行的函数
            *args, **kwargs: 传给 fn 的参数
            on_success: 主线程回调 fn(result)
            on_error: 主线程回调 fn(error_msg)
            task_id: 可选任务 ID

        返回:
            task_id
        """
        worker = BackgroundWorker(fn, *args, **kwargs)

        # 完成后清理 active_workers
        tid = task_id or str(uuid.uuid4())[:8]
        worker.task_id = tid

        with self._lock:
            if self._shutting_down:
                return tid

            if task_id and task_id in self.active_workers:
                return tid

        # 防静默死亡：如果调用方没有传 on_error，注入默认兜底闭包
        # 这样后台线程崩了，至少日志里有痕迹，不会让 UI 永远卡在"加载中"
        if on_error is None:
            def _default_error_handler(error_message: str):
                from core.logger import get_logger
                get_logger(__name__).error(
                    f"[TaskManager] 后台任务 '{tid}' 未捕获异常: {error_message}"
                )
            on_error = _default_error_handler

        from PyQt6.QtCore import Qt
        if on_success:
            worker.signals.finished.connect(on_success, type=Qt.ConnectionType.QueuedConnection)
        if on_error:
            worker.signals.error.connect(on_error, type=Qt.ConnectionType.QueuedConnection)

        def _cleanup(_):
            with self._lock:
                current = self.active_workers.get(tid)
                if current is worker:
                    self.active_workers.pop(tid, None)

        worker.signals.finished.connect(_cleanup)
        worker.signals.error.connect(_cleanup)

        return self.submit_task(worker, tid)

    def cancel_all(self):
        """终极清退：停止所有排队和运行中的任务"""
        self.thread_pool.clear()
        with self._lock:
            workers = list(self.active_workers.items())
            self.active_workers.clear()

        for task_id, worker in workers:
            if hasattr(worker, 'cancel'):
                worker.cancel()

    def abandon_task(self, task_id: str) -> bool:
        """放弃一个卡住任务的占位，允许同 task_id 后续重新提交。

        说明：
        - QRunnable 一旦已经进入阻塞态，Qt 线程池无法强杀。
        - 这里做的是“忘记它仍在 active_workers 中的占位”，并尽量调用 cancel()。
        - 适用于行情轮询这类需要 fail-open 的长寿命任务；旧任务若稍后返回，
          它的 cleanup 只会再次 pop 同名 key，不会影响新任务。
        """
        with self._lock:
            worker = self.active_workers.pop(task_id, None)

        if worker is None:
            return False

        if hasattr(worker, 'cancel'):
            try:
                worker.cancel()
            except (AttributeError, OSError, RuntimeError, TypeError, ValueError):
                pass
        return True

    def shutdown(self):
        """应用关闭时调用：禁止后续任务提交，并清理排队中的任务。"""
        with self._lock:
            self._shutting_down = True
        self.cancel_all()

    def is_active_task(self, task_id: str) -> bool:
        with self._lock:
            return task_id in self.active_workers

    @property
    def active_count(self) -> int:
        with self._lock:
            return len(self.active_workers)


# 全局单例
task_manager = GlobalTaskManager()
