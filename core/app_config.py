from PyQt6.QtCore import QSettings


class SettingsSection:
    """命名空间配置视图，兼容旧 QSettings scope 的惰性迁移。"""

    def __init__(self, root_settings: QSettings, prefix: str = "", legacy_settings: QSettings | None = None):
        self._root_settings = root_settings
        self._prefix = str(prefix or "").strip("/")
        self._legacy_settings = legacy_settings

    def _full_key(self, key: str) -> str:
        key = str(key or "").strip("/")
        if not self._prefix:
            return key
        if not key:
            return self._prefix
        return f"{self._prefix}/{key}"

    def _migrate_legacy_value(self, key: str, default=None, value_type=None):
        if self._legacy_settings is None or not self._legacy_settings.contains(key):
            return None, False

        if value_type is not None:
            value = self._legacy_settings.value(key, default, type=value_type)
        else:
            value = self._legacy_settings.value(key, default)
        self._root_settings.setValue(self._full_key(key), value)
        return value, True

    def get(self, key: str, default=None, value_type=None):
        full_key = self._full_key(key)
        if self._root_settings.contains(full_key):
            if value_type is not None:
                return self._root_settings.value(full_key, default, type=value_type)
            return self._root_settings.value(full_key, default)

        migrated_value, migrated = self._migrate_legacy_value(key, default=default, value_type=value_type)
        if migrated:
            self._root_settings.sync()
            return migrated_value

        return default

    def value(self, key: str, default=None, type=None):
        return self.get(key, default=default, value_type=type)

    def set(self, key: str, value):
        self._root_settings.setValue(self._full_key(key), value)

    def setValue(self, key: str, value):
        self.set(key, value)

    def remove(self, key: str):
        self._root_settings.remove(self._full_key(key))

    def contains(self, key: str) -> bool:
        full_key = self._full_key(key)
        if self._root_settings.contains(full_key):
            return True
        return self._legacy_settings is not None and self._legacy_settings.contains(key)

    def sync(self):
        self._root_settings.sync()


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
            cls._instance._legacy_settings_cache = {}
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

    def section(self, prefix: str = "", *, legacy_scope: str | None = None) -> SettingsSection:
        """创建一个命名空间配置视图，并在需要时兼容旧 scope。"""
        legacy_settings = None
        if legacy_scope:
            legacy_settings = self._legacy_settings_cache.get(legacy_scope)
            if legacy_settings is None:
                legacy_settings = QSettings("VCPHunter", legacy_scope)
                self._legacy_settings_cache[legacy_scope] = legacy_settings
        return SettingsSection(self._settings, prefix=prefix, legacy_settings=legacy_settings)

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
