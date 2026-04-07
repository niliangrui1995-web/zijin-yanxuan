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

    # ==== [v4 专属：高速点对点数据通讯专线] ====
    # 代替老旧的巨石信号 sig_data_updated (消除 if-else 地狱)

    # 1. 实时行情广播 — payload: dict { code: {close, pct, ...} }
    # 接收方：scan_tab, rt_monitor_tab, watchlist_tab, main_window 等
    sig_rt_quotes = pyqtSignal(object)

    # 2. 盘中监控刷新完成 — payload: list[dict] (含 VCP 评分完整结果)
    # 接收方：main_window 等
    sig_rt_quotes_refreshed = pyqtSignal(object)

    # 3. 关注池 VCP 数据就绪 — payload: list[dict]
    sig_vcp_watchlist_ready = pyqtSignal(object)

    # 4. 本地缓存加载完成
    # 接收方：main_window 等
    sig_cache_loaded = pyqtSignal()

    # 5. 业绩异动数据更新完成
    # 接收方：watchlist_tab 等
    sig_earnings_updated = pyqtSignal()

    # 6. 亚洲 K 线离线缓存就绪
    # 接收方：asian_market_tab
    sig_asian_klines_ready = pyqtSignal()

    # ====== [任务控制信号] ======

    # 后台任务进度 — task_id (str|TaskEvent), progress_pct, status_msg
    sig_task_progress = pyqtSignal(str, int, str)

    # ====== [UI 线程安全信号] ======

    # 从后台线程安全操作 UI — callable (无参函数)
    sig_ui_task = pyqtSignal(object)

    # ====== [用户操作信号] ======

    # K线图请求 — 仅 code
    sig_show_kline = pyqtSignal(str)

    # K线图请求带列表 — code, code_list, current_idx
    sig_show_kline_with_list = pyqtSignal(str, object, int)

    # 关注池变更 — action: 'add'/'remove', code
    sig_watchlist_changed = pyqtSignal(str, str)

    def __new__(cls, *args, **kwargs):
        if not cls._instance:
            cls._instance = super(GlobalEventBus, cls).__new__(cls, *args, **kwargs)
        return cls._instance


# 全局单例
event_bus = GlobalEventBus()
