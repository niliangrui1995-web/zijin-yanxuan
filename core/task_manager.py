import uuid
from PyQt6.QtCore import QObject, QRunnable, QThreadPool

class GlobalTaskManager(QObject):
    """
    紫金研选 统一任务调度管理中心 (Task Manager)
    托管所有底层的异步计算、大模型网络请求，提供彻底的「一键拦截」「并发池」等核心控制。
    """
    _instance = None
    
    def __new__(cls, *args, **kwargs):
        if not cls._instance:
            cls._instance = super(GlobalTaskManager, cls).__new__(cls, *args, **kwargs)
        return cls._instance

    def __init__(self):
        super().__init__()
        # 初始化只执行一次
        if hasattr(self, '_initialized'):
            return
        self._initialized = True
        
        # 使用 Qt 官方线程池
        self.thread_pool = QThreadPool.globalInstance()
        # 将最大线程数设置为当前 CPU 核心数 * 2 比较稳妥
        self.thread_pool.setMaxThreadCount(self.thread_pool.maxThreadCount() + 10)
        
        # 未来可注册用于状态追踪的字典
        self.active_workers = {}

    def submit_task(self, worker: QRunnable, task_id: str = None) -> str:
        """
        抛入一个 QRunnable 的 Worker 用于后台排队执行。
        """
        if not task_id:
            task_id = str(uuid.uuid4())[:8]
            
        self.active_workers[task_id] = worker
        self.thread_pool.start(worker)
        return task_id
        
    def cancel_all(self):
        """
        终极清退指令：清除所有排队但还没启动的任务；
        强制停止现有的 Worker（要求 Worker 内部响应 _is_cancelled 标志）。
        """
        self.thread_pool.clear()
        
        # 广播取消标记到每一个托管的 Worker
        for task_id, worker in list(self.active_workers.items()):
            if hasattr(worker, 'cancel'):
                worker.cancel()
        
        # 清空记录
        self.active_workers.clear()

# 全局单例任务调度池
task_manager = GlobalTaskManager()
