# core/app_config.py
# ================================================================================
# 紫金研选 统一配置管理中心 (Singleton)
#
# 为什么需要这个: 之前每个 Tab/组件各自创建 QSettings("VCPHunter", "XXXTab")，
# 配置项散落在 4+ 个命名空间里，新人根本找不清楚有哪些可调参数。
# 现在统一为一个入口，用 section/key 命名空间管理。
# ================================================================================
from PyQt6.QtCore import QSettings


class AppConfig:
    """
    全局配置单例 — 所有 QSettings 的唯一入口

    使用方式:
        from core.app_config import app_config
        val = app_config.get("scan/rps_threshold", 80, int)
        app_config.set("scan/rps_threshold", 90)

    命名空间约定:
        window/   → 主窗口几何、Tab 状态
        scan/     → 扫描策略参数
        rt/       → 盘中监控参数
        header/   → 各表格列宽持久化
        network/  → 网络相关
        cache/    → 缓存策略
    """
    _instance = None

    def __new__(cls, *args, **kwargs):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._settings = QSettings("VCPHunter", "Main")
        return cls._instance

    # ======================== 通用读写 ========================

    def get(self, key: str, default=None, value_type=None):
        """读取配置项，支持类型转换"""
        if value_type is not None:
            return self._settings.value(key, default, type=value_type)
        return self._settings.value(key, default)

    def set(self, key: str, value):
        """写入配置项"""
        self._settings.setValue(key, value)

    def remove(self, key: str):
        """删除配置项"""
        self._settings.remove(key)

    def contains(self, key: str) -> bool:
        return self._settings.contains(key)

    def sync(self):
        """立即将更改写入存储"""
        self._settings.sync()

    # ======================== 便捷属性 ========================

    # --- 扫描策略参数 ---
    @property
    def scan_rps_threshold(self) -> int:
        return self.get("scan/rps_threshold", 80, int)

    @scan_rps_threshold.setter
    def scan_rps_threshold(self, value: int):
        self.set("scan/rps_threshold", value)

    @property
    def scan_amp_threshold(self) -> float:
        return self.get("scan/amp_threshold", 0.45, float)

    @scan_amp_threshold.setter
    def scan_amp_threshold(self, value: float):
        self.set("scan/amp_threshold", value)

    @property
    def scan_ma_bind_threshold(self) -> float:
        return self.get("scan/ma_bind_threshold", 0.05, float)

    @scan_ma_bind_threshold.setter
    def scan_ma_bind_threshold(self, value: float):
        self.set("scan/ma_bind_threshold", value)

    @property
    def scan_min_amount(self) -> float:
        return self.get("scan/min_amount", 0.8, float)

    @scan_min_amount.setter
    def scan_min_amount(self, value: float):
        self.set("scan/min_amount", value)

    @property
    def scan_high250_threshold(self) -> float:
        return self.get("scan/high_250_threshold", 0.10, float)

    @scan_high250_threshold.setter
    def scan_high250_threshold(self, value: float):
        self.set("scan/high_250_threshold", value)

    # --- 窗口状态 ---
    @property
    def window_geometry(self):
        return self.get("window/geometry")

    @window_geometry.setter
    def window_geometry(self, value):
        self.set("window/geometry", value)

    @property
    def window_state(self):
        return self.get("window/state")

    @window_state.setter
    def window_state(self, value):
        self.set("window/state", value)

    @property
    def last_active_tab(self) -> int:
        return self.get("window/last_active_tab", 0, int)

    @last_active_tab.setter
    def last_active_tab(self, value: int):
        self.set("window/last_active_tab", value)

    # --- UI 设置 ---
    @property
    def table_density(self) -> str:
        return self.get("ui/table_density", "舒适", str)

    @table_density.setter
    def table_density(self, value: str):
        self.set("ui/table_density", value)

    # --- 网络配置 ---
    @property
    def network_offline_mode(self) -> bool:
        return self.get("network/offline_mode", False, bool)

    @network_offline_mode.setter
    def network_offline_mode(self, value: bool):
        self.set("network/offline_mode", value)

# 全局单例
app_config = AppConfig()
