# -*- coding: utf-8 -*-
from PyQt6.QtCore import QObject, pyqtSignal
from core.event_bus import event_bus

class GlobalStore(QObject):
    """
    VCP Hunter 全局状态机制 (Redux Store 架构实现)
    
    单例模式。用于拦截并持有整个应用运行时的跨组件共享快照状态。
    目前包含:
    - 实时行情快照 (最新价格)
    
    优势：
    - 数据不随组件挂载销毁而丢失。
    - 当用户切换或新建 Tab 并且表格初始化时，无需等待下一次网络底层的轮询即可立刻拿到老快照刷新出价格（实现无缝切图）。
    """
    
    # 支持向外发送细粒度状态修改信号
    sig_state_changed = pyqtSignal(str, object)
        
    def __init__(self):
        super().__init__()
        
        self.state = {
            "quotes": {},       # { "000001": {"close": 15.5, ...} }
            "watchlist": [],    # 全局关注池预留
        }
        
        self._bind_events()
        
    def _bind_events(self):
        # v4: 直接订阅专用行情信号，不再走通用路由
        event_bus.sig_rt_quotes.connect(self._on_rt_quotes)
        
    def _on_rt_quotes(self, data: dict):
        # Dict 更新（Reducer 操作）
        if isinstance(data, dict):
            self.state["quotes"].update(data)
            
    def get_latest_quotes(self) -> dict:
        """获取所有已缓存的全局最新行情快照"""
        return self.state["quotes"]

    def reset_quotes(self):
        """清空全局行情快照。"""
        self.state["quotes"].clear()

    def reset_runtime_state(self):
        """重置轻量运行态，避免跨测试和跨阶段污染。"""
        self.reset_quotes()
        self.state["watchlist"] = []

# 导出单例 (Store)
global_store = GlobalStore()
