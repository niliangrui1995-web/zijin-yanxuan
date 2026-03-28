from PyQt6.QtCore import QObject, pyqtSignal

class GlobalEventBus(QObject):
    """
    紫金研选 全局核心事件总线 (Event Bus) - 单例模式
    用于彻底解耦各个 UI 组件与数据层，取代直接调用与信号混乱传递。

    v2: 拆分 sig_data_updated 为语义明确的专用信号
    """
    _instance = None

    # ====== [系统级信号] ======

    # 日志事件 — level: 'info'|'warn'|'error', msg: 消息
    sig_system_log = pyqtSignal(str, str)

    # 网络状态变更 — is_online: bool, detail: str
    sig_network_status_changed = pyqtSignal(bool, str)

    # 应用关闭通知（各组件保存缓存）
    sig_app_closing = pyqtSignal()

    # ====== [数据层信号 - 从 sig_data_updated 拆分] ======

    # 实时报价刷新完成 — payload: list[dict]
    sig_rt_quotes = pyqtSignal(list)

    # Parquet/pkl 缓存加载完成
    sig_cache_ready = pyqtSignal()

    # VCP 指标计算完成 — tab_id: str, results: dict
    sig_vcp_indicators = pyqtSignal(str, object)

    # 扫描完成 — results: list, elapsed: float
    sig_scan_complete = pyqtSignal(list, float)

    # 通用数据更新（向后兼容，逐步弃用）
    # 参数: data_type (str), data_payload (object)
    sig_data_updated = pyqtSignal(str, object)

    # ====== [任务控制信号] ======

    # 后台任务进度 — task_id, progress_pct, status_msg
    sig_task_progress = pyqtSignal(str, int, str)

    # 终止所有后台计算
    sig_action_cancel_all = pyqtSignal()

    # ====== [用户操作信号] ======

    # K线图请求 — code, name
    sig_action_open_kline = pyqtSignal(str, str)

    # K线图请求 — 仅 code
    sig_show_kline = pyqtSignal(str)

    # 关注池变更 — action: 'add'/'remove', code
    sig_watchlist_changed = pyqtSignal(str, str)

    # AI 诊断请求 — code, mode: 'local'/'ai'/''
    sig_open_ai_diag = pyqtSignal(str, str)

    def __new__(cls, *args, **kwargs):
        if not cls._instance:
            cls._instance = super(GlobalEventBus, cls).__new__(cls, *args, **kwargs)
        return cls._instance

# 全局单例
event_bus = GlobalEventBus()
