from PyQt6.QtCore import QObject, pyqtSignal

class GlobalEventBus(QObject):
    """
    紫金研选 全局核心事件总线 (Event Bus) - 单例模式
    用于彻底解耦各个 UI 组件与数据层，取代直接调用与信号混乱传递。
    """
    _instance = None

    # ====== [定义全局通讯信号] ======

    # 1. 基础系统与日志事件
    # 参数: level ('info', 'warn', 'error'), msg_text
    sig_system_log = pyqtSignal(str, str)
    
    # 2. 网络状态与资源变动事件
    # 参数: status_is_online (bool), msg_detail (str)
    sig_network_status_changed = pyqtSignal(bool, str)

    # 3. 核心大盘/池子数据变动
    # 参数: data_type (如 'scan_results', 'rt_quotes', 'ai_diag'), data_payload (任意对象/字典)
    sig_data_updated = pyqtSignal(str, object)

    # 4. 后台任务控制与进度广播
    # 参数: task_id (str), progress_pct (int), status_msg (str)
    sig_task_progress = pyqtSignal(str, int, str)
    
    # 5. 用户操作触发全局动作
    # 例如发出一个终止所有后台计算的号角
    sig_action_cancel_all = pyqtSignal()
    # 快速跳转至某只股票的K线图 (code, name)
    sig_action_open_kline = pyqtSignal(str, str)

    # 6. K线图请求（仅 code，由接收方自行查名称）
    sig_show_kline = pyqtSignal(str)

    # 7. 关注池变更通知（action: 'add'/'remove', code）
    sig_watchlist_changed = pyqtSignal(str, str)

    # 8. AI 诊断请求（code, auto_start_mode: 'local'/'ai'/''）
    sig_open_ai_diag = pyqtSignal(str, str)

    # 9. 应用关闭通知（各组件监听此信号保存自身缓存）
    sig_app_closing = pyqtSignal()

    def __new__(cls, *args, **kwargs):
        if not cls._instance:
            cls._instance = super(GlobalEventBus, cls).__new__(cls, *args, **kwargs)
        return cls._instance

# 提供一个全局直接可用的单例实例
event_bus = GlobalEventBus()
