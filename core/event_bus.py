# core/event_bus.py
# ================================================================================
# 紫金研选 全局核心事件总线 (Event Bus) - 单例模式
#
# v3 重构: 引入 EventType 枚举，sig_data_updated 支持 Enum 和字符串双模式。
#         新增 sig_ui_task 信号，用于从后台线程安全操作 UI。
# ================================================================================
from PyQt6.QtCore import QObject, pyqtSignal


class GlobalEventBus(QObject):
    """
    紫金研选全局事件总线 — 解耦 UI 组件与数据层的唯一通道

    事件类型说明见 core/event_types.py 的 EVENT_REGISTRY
    """
    _instance = None

    # ====== [系统级信号] ======

    # 日志事件 — level: 'info'|'warn'|'error', msg: 消息
    sig_system_log = pyqtSignal(str, str)

    # 网络状态变更 — is_online: bool, detail: str
    sig_network_status_changed = pyqtSignal(bool, str)

    # 应用关闭通知（各组件保存缓存）
    sig_app_closing = pyqtSignal()

    # ====== [数据层信号] ======

    # 通用数据更新 — data_type (str|DataEvent), data_payload (object)
    # 建议使用 DataEvent 枚举: emit(DataEvent.RT_QUOTES_BROADCAST.value, data)
    sig_data_updated = pyqtSignal(str, object)

    # ====== [任务控制信号] ======

    # 后台任务进度 — task_id (str|TaskEvent), progress_pct, status_msg
    sig_task_progress = pyqtSignal(str, int, str)

    # ====== [UI 线程安全信号] ======

    # 从后台线程安全操作 UI — callable (无参函数)
    sig_ui_task = pyqtSignal(object)

    # ====== [用户操作信号] ======

    # K线图请求 — code, name
    sig_action_open_kline = pyqtSignal(str, str)

    # K线图请求 — 仅 code
    sig_show_kline = pyqtSignal(str)

    # K线图请求带列表 — code, code_list, current_idx
    sig_show_kline_with_list = pyqtSignal(str, object, int)

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
