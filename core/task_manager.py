# -*- coding: utf-8 -*-
"""统一任务调度管理中心 — 替代散落的 threading.Thread

提供 run_in_background() 便捷方法：
- 后台执行函数
- 结果通过 pyqtSignal 安全回传到主线程
- 自动异常捕获与日志
"""

import uuid
import traceback
from PyQt6.QtCore import QObject, QRunnable, QThreadPool, pyqtSignal, pyqtSlot


class _WorkerSignals(QObject):
    """Worker 内部信号（跨线程回传结果）"""
    finished = pyqtSignal(object)   # 成功: 传回结果
    error = pyqtSignal(str)         # 失败: 传回错误信息
    progress = pyqtSignal(int, str) # 进度: pct, msg


class BackgroundWorker(QRunnable):
    """通用后台 Worker — 包装任意可调用对象"""

    def __init__(self, fn, *args, **kwargs):
        super().__init__()
        self.fn = fn
        self.args = args
        self.kwargs = kwargs
        self.signals = _WorkerSignals()
        self._is_cancelled = False
        # 不自动删除，由 active_workers 字典持有引用控制生命周期
        self.setAutoDelete(False)

    def cancel(self):
        self._is_cancelled = True

    @pyqtSlot()
    def run(self):
        try:
            result = self.fn(*self.args, **self.kwargs)
            if not self._is_cancelled:
                try:
                    self.signals.finished.emit(result)
                except RuntimeError:
                    pass  # 信号对象已被销毁，安全忽略
        except Exception as e:
            tb = traceback.format_exc()
            from core.logger import get_logger
            get_logger(__name__).error(f"[任务调度] Worker 异常: {e}\n{tb}")
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

    def submit_task(self, worker: QRunnable, task_id: str = None) -> str:
        """提交 QRunnable Worker"""
        if not task_id:
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

        from PyQt6.QtCore import Qt
        if on_success:
            worker.signals.finished.connect(on_success, type=Qt.ConnectionType.QueuedConnection)
        if on_error:
            worker.signals.error.connect(on_error, type=Qt.ConnectionType.QueuedConnection)

        # 完成后清理 active_workers
        tid = task_id or str(uuid.uuid4())[:8]

        def _cleanup(_):
            self.active_workers.pop(tid, None)

        worker.signals.finished.connect(_cleanup)
        worker.signals.error.connect(_cleanup)

        return self.submit_task(worker, tid)

    def cancel_all(self):
        """终极清退：停止所有排队和运行中的任务"""
        self.thread_pool.clear()
        for task_id, worker in list(self.active_workers.items()):
            if hasattr(worker, 'cancel'):
                worker.cancel()
        self.active_workers.clear()

    @property
    def active_count(self) -> int:
        return len(self.active_workers)


# 全局单例
task_manager = GlobalTaskManager()
