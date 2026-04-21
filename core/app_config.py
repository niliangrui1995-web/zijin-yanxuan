from __future__ import annotations

from infra.settings import SettingsRepository, SettingsSchemaVersion, SettingsSection


class AppConfig:
    """
    Global configuration facade.

    The underlying storage is centralized in a versioned settings repository.
    UI code should access namespaced sections instead of creating ad-hoc QSettings
    instances directly.
    """

    _instance = None

    def __new__(cls, *args, **kwargs):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._repository = SettingsRepository("VCPHunter", "Main")
        return cls._instance

    @property
    def schema_version(self) -> int:
        return self._repository.schema_version

    @property
    def settings_schema_key(self) -> str:
        return SettingsSchemaVersion.KEY

    def get(self, key: str, default=None, value_type=None):
        return self._repository.get(key, default=default, value_type=value_type)

    def set(self, key: str, value) -> None:
        self._repository.set(key, value)

    def remove(self, key: str) -> None:
        self._repository.remove(key)

    def contains(self, key: str) -> bool:
        return self._repository.contains(key)

    def sync(self) -> None:
        self._repository.sync()

    def section(self, prefix: str = "", *, legacy_scope: str | None = None) -> SettingsSection:
        return self._repository.section(prefix=prefix, legacy_scope=legacy_scope)

    @property
    def scan_rps_threshold(self) -> int:
        return self.get("scan/rps_threshold", 80, int)

    @scan_rps_threshold.setter
    def scan_rps_threshold(self, value: int) -> None:
        self.set("scan/rps_threshold", value)

    @property
    def scan_amp_threshold(self) -> float:
        return self.get("scan/amp_threshold", 0.45, float)

    @scan_amp_threshold.setter
    def scan_amp_threshold(self, value: float) -> None:
        self.set("scan/amp_threshold", value)

    @property
    def scan_ma_bind_threshold(self) -> float:
        return self.get("scan/ma_bind_threshold", 0.05, float)

    @scan_ma_bind_threshold.setter
    def scan_ma_bind_threshold(self, value: float) -> None:
        self.set("scan/ma_bind_threshold", value)

    @property
    def scan_min_amount(self) -> float:
        return self.get("scan/min_amount", 0.8, float)

    @scan_min_amount.setter
    def scan_min_amount(self, value: float) -> None:
        self.set("scan/min_amount", value)

    @property
    def scan_high250_threshold(self) -> float:
        return self.get("scan/high_250_threshold", 0.10, float)

    @scan_high250_threshold.setter
    def scan_high250_threshold(self, value: float) -> None:
        self.set("scan/high_250_threshold", value)

    @property
    def window_geometry(self):
        return self.get("window/geometry")

    @window_geometry.setter
    def window_geometry(self, value) -> None:
        self.set("window/geometry", value)

    @property
    def window_state(self):
        return self.get("window/state")

    @window_state.setter
    def window_state(self, value) -> None:
        self.set("window/state", value)

    @property
    def last_active_tab(self) -> int:
        return self.get("window/last_active_tab", 0, int)

    @last_active_tab.setter
    def last_active_tab(self, value: int) -> None:
        self.set("window/last_active_tab", value)

    @property
    def table_density(self) -> str:
        return self.get("ui/table_density", "舒适", str)

    @table_density.setter
    def table_density(self, value: str) -> None:
        self.set("ui/table_density", value)

    @property
    def network_offline_mode(self) -> bool:
        return self.get("network/offline_mode", False, bool)

    @network_offline_mode.setter
    def network_offline_mode(self, value: bool) -> None:
        self.set("network/offline_mode", value)


app_config = AppConfig()
