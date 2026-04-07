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
        """将数据加载到内存中，优先读SQLite数据库，不存在则兼容读JSON"""
        try:
            from core.data_store import DataStore
            data = DataStore().load_json("watchlist_special")
            
            if not data:
                # 兼容旧 JSON 迁移
                if os.path.exists(SPECIAL_LATEST_DATA):
                    with open(SPECIAL_LATEST_DATA, 'r', encoding='utf-8') as f:
                        data = json.load(f)
                    if isinstance(data, dict):
                        DataStore().save_json("watchlist_special", data)
                        try:
                            # 迁移后标记旧文件，留作备用30天后自动清除
                            os.rename(SPECIAL_LATEST_DATA, SPECIAL_LATEST_DATA + ".migrated")
                            log.info("[WatchlistVM] 关注池数据已自动迁移入 SQLite")
                        except OSError as _e:
                            log.debug(f"[WatchlistVM] 迁移旧 JSON 文件重命名失败: {_e}")

            if isinstance(data, dict):
                self._cache = data
            else:
                self._cache = {}
        except Exception as e:
            log.error(f"[WatchlistVM] 读取关注池失败，退化为空: {e}")
            self._cache = {}
            
    def _save_data(self):
        """安全地将内存中的数据刷入 SQLite（原子写入）"""
        try:
            with self._lock:
                save_data = self._cache.copy()
            from core.data_store import DataStore
            DataStore().save_json("watchlist_special", save_data)
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

    def pin_to_top(self, stock_code: str):
        """将股票排到关注池最前排（置顶）"""
        with self._lock:
            if stock_code in self._cache:
                data = self._cache.pop(stock_code)
                new_cache = {stock_code: data}
                new_cache.update(self._cache)
                self._cache = new_cache
                
        self._save_data()
        event_bus.sig_watchlist_changed.emit("toggle", stock_code)

    def reorder(self, new_codes_list: list):
        """
        根据新的代码列表重新排序关注池，并持久化保存。
        如果有新列表里没有但在旧缓存里存在的代码（防丢），会追加到末尾。
        """
        with self._lock:
            new_cache = {}
            # 1. 按照传入的新顺序依次构建
            for code in new_codes_list:
                if code in self._cache:
                    new_cache[code] = self._cache[code]
            
            # 2. 安全兜底：如果有些在老缓存里的代码没能在新列表出现，加到最后
            for code, data in self._cache.items():
                if code not in new_cache:
                    new_cache[code] = data
            
            self._cache = new_cache
            
        self._save_data()

# 全局单例
watchlist_vm = WatchlistViewModel()
