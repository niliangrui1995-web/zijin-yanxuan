# core/event_types.py
# ================================================================================
# 紫金研选 事件类型注册表
#
# 为什么需要这个: 之前 sig_data_updated.emit("rt_quotes_broadcast", data)
# 用裸字符串标识事件类型，谁发什么、谁听什么全靠 grep 搜索。
# 现在用 Enum 明确枚举所有事件，并附带 payload 格式说明。
# ================================================================================
from enum import Enum


class DataEvent(Enum):
    """数据层事件 — 用于 sig_data_updated 信号的 event_type 参数"""

    # 实时行情广播 — payload: list[dict] (每只股票的实时报价)
    RT_QUOTES_BROADCAST = "rt_quotes_broadcast"

    # 盘中监控刷新完成 — payload: list[dict] (含突破状态的完整结果)
    RT_QUOTES_REFRESHED = "rt_quotes_refreshed"

    # 关注池 VCP 数据就绪 — payload: list[dict]
    VCP_WATCHLIST_READY = "vcp_watchlist_ready"

    # 本地缓存加载完成 — payload: None
    CACHE_LOADED = "cache_loaded"

    # 业绩 Tab 数据更新完成 — payload: None
    EARNINGS_UPDATED = "earnings_updated"

    # 亚洲K线数据就绪 — payload: None
    ASIAN_KLINES_READY = "asian_klines_ready"


class TaskEvent(Enum):
    """任务进度事件 — 用于 sig_task_progress 信号的 task_id 参数"""

    SCAN = "scan"
    RT_MONITOR = "rt_monitor"


# ======================== 事件注册表 (文档化) ========================
EVENT_REGISTRY = {
    # 数据事件
    DataEvent.RT_QUOTES_BROADCAST: {
        "emitter": "central_quotes_worker.py",
        "listeners": ["scan_tab", "rt_monitor_tab", "watchlist_tab",
                       "foreign_block_tab", "main_window"],
        "payload": "list[dict] — 每只股票的 sina/pytdx 实时报价",
    },
    DataEvent.RT_QUOTES_REFRESHED: {
        "emitter": "rt_monitor_tab.py",
        "listeners": ["main_window (盘中按钮状态)"],
        "payload": "list[dict] — 含 VCP 评分、突破状态的完整结果",
    },
    DataEvent.VCP_WATCHLIST_READY: {
        "emitter": "watchlist_tab.py",
        "listeners": ["main_window"],
        "payload": "list[dict] — 关注池 VCP 评估结果",
    },
    DataEvent.CACHE_LOADED: {
        "emitter": "startup_loader.py",
        "listeners": ["main_window"],
        "payload": "None",
    },
    DataEvent.ASIAN_KLINES_READY: {
        "emitter": "startup_loader.py",
        "listeners": ["asian_market_tab"],
        "payload": "None",
    },
    DataEvent.EARNINGS_UPDATED: {
        "emitter": "earnings_tab.py",
        "listeners": ["watchlist_tab"],
        "payload": "None — 通知关注池重新拉取业绩异动列",
    },
}
