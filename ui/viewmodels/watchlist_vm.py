import os
import json
from vcp.constants import SPECIAL_LATEST_DATA
from core.event_bus import event_bus
from core.logger import get_logger
import threading

log = get_logger(__name__)

class WatchlistViewModel:
    """
    负责维护“关注池”的幕后管家 (ViewModel)。
    为什么要有这个类？
    为了避免各个 UI 窗口各自为战，频繁且重复地去读取和写入 JSON 文件导致文件损坏或界面卡顿。
    所有的读写、校验、状态维护统一在这里进行。
    """
    _instance = None
    
    def __new__(cls, *args, **kwargs):
        if not cls._instance:
            cls._instance = super().__new__(cls)
            cls._instance._lock = threading.RLock()
            with cls._instance._lock:
                cls._instance._cache = {}
                cls._instance._load_data()
            
        return cls._instance
        
    def _load_data(self):
        """将硬盘里的数据缓存到内存中，避免每次读盘"""
        if not os.path.exists(SPECIAL_LATEST_DATA):
            self._cache = {}
            return
            
        try:
            with open(SPECIAL_LATEST_DATA, 'r', encoding='utf-8') as f:
                data = json.load(f)
                if isinstance(data, dict):
                    self._cache = data
                else:
                    self._cache = {}
        except Exception as e:
            log.error(f"[WatchlistVM] 读取关注池失败，退化为空: {e}")
            self._cache = {}
            
    def _save_data(self):
        """安全地将内存中的数据刷入硬盘（原子写入，防止写到一半崩溃导致文件损坏）"""
        try:
            with self._lock:
                save_data = self._cache.copy()
            os.makedirs(os.path.dirname(SPECIAL_LATEST_DATA), exist_ok=True)
            # 先写 tmp 文件，再原子替换，避免断电/崩溃导致 JSON 损坏
            tmp_path = SPECIAL_LATEST_DATA + '.tmp'
            with open(tmp_path, 'w', encoding='utf-8') as f:
                json.dump(save_data, f, ensure_ascii=False, indent=4)
            os.replace(tmp_path, SPECIAL_LATEST_DATA)
        except Exception as e:
            log.error(f"[WatchlistVM] 写入关注池失败: {e}")

    def is_in_watchlist(self, stock_code: str) -> bool:
        """判断特定股票是否在关注池中"""
        with self._lock:
            return stock_code in self._cache

    def get_all_codes(self) -> list:
        """返回所有被关注的股票代码列表"""
        with self._lock:
            return list(self._cache.keys())
        
    def get_watchlist_data(self) -> dict:
        """获取整个关注池的详尽数据拷贝，防止外部直接引用修改污染缓存"""
        with self._lock:
            return self._cache.copy()

    def toggle_stock(self, stock_code: str, name: str, vcp_data: dict = None):
        """
        翻转股票的关注状态。
        如果是关注，就移除；如果没关注，就添加并保留当时的 VCP 分析数据。
        """
        with self._lock:
            is_fav = stock_code in self._cache

            if is_fav:
                self._cache.pop(stock_code, None)
                event_bus.sig_system_log.emit("info", f"[{name}] 已移出关注池")
            else:
                entry = {"现价": 0, "涨幅%": 0, "评分": ""}
                # 如果有传入当时的扫描数据，把数据保留下来供以后参考
                if vcp_data and isinstance(vcp_data, dict):
                    for k, v in vcp_data.items():
                        if hasattr(v, 'item'):
                            entry[k] = v.item()
                        elif isinstance(v, (str, int, float, bool, list, dict, type(None))):
                            entry[k] = v
                        else:
                            entry[k] = str(v)
                self._cache[stock_code] = entry
                event_bus.sig_system_log.emit("info", f"[{name}] 已加入关注池")

        self._save_data()
        
        # 触发全局广播，让所有的 UI 界面自己去更新星星图标或重新加载数据
        event_bus.sig_watchlist_changed.emit("toggle", stock_code)

# 全局单例
watchlist_vm = WatchlistViewModel()
