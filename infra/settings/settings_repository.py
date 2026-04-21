from __future__ import annotations

from PyQt6.QtCore import QSettings

from infra.settings.settings_schema import SettingsMigrator


class SettingsSection:
    """Namespaced view over the root settings store with lazy legacy migration."""

    def __init__(
        self,
        root_settings: QSettings,
        prefix: str = "",
        legacy_settings: QSettings | None = None,
    ) -> None:
        self._root_settings = root_settings
        self._prefix = str(prefix or "").strip("/")
        self._legacy_settings = legacy_settings

    def _full_key(self, key: str) -> str:
        clean_key = str(key or "").strip("/")
        if not self._prefix:
            return clean_key
        if not clean_key:
            return self._prefix
        return f"{self._prefix}/{clean_key}"

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

        migrated_value, migrated = self._migrate_legacy_value(
            key,
            default=default,
            value_type=value_type,
        )
        if migrated:
            self._root_settings.sync()
            return migrated_value

        return default

    def value(self, key: str, default=None, type=None):
        return self.get(key, default=default, value_type=type)

    def set(self, key: str, value) -> None:
        self._root_settings.setValue(self._full_key(key), value)

    def setValue(self, key: str, value) -> None:
        self.set(key, value)

    def remove(self, key: str) -> None:
        self._root_settings.remove(self._full_key(key))

    def contains(self, key: str) -> bool:
        full_key = self._full_key(key)
        if self._root_settings.contains(full_key):
            return True
        return self._legacy_settings is not None and self._legacy_settings.contains(key)

    def sync(self) -> None:
        self._root_settings.sync()


class SettingsRepository:
    def __init__(self, organization: str = "VCPHunter", application: str = "Main") -> None:
        self._settings = QSettings(organization, application)
        self._legacy_settings_cache: dict[str, QSettings] = {}
        self._migrator = SettingsMigrator(self._settings)
        self._schema_version = self._migrator.migrate()
        self._organization = organization
        self._application = application

    @property
    def root_settings(self) -> QSettings:
        return self._settings

    @property
    def schema_version(self) -> int:
        return self._schema_version

    def get(self, key: str, default=None, value_type=None):
        if value_type is not None:
            return self._settings.value(key, default, type=value_type)
        return self._settings.value(key, default)

    def set(self, key: str, value) -> None:
        self._settings.setValue(key, value)

    def remove(self, key: str) -> None:
        self._settings.remove(key)

    def contains(self, key: str) -> bool:
        return self._settings.contains(key)

    def sync(self) -> None:
        self._settings.sync()

    def section(self, prefix: str = "", *, legacy_scope: str | None = None) -> SettingsSection:
        legacy_settings = None
        if legacy_scope:
            legacy_settings = self._legacy_settings_cache.get(legacy_scope)
            if legacy_settings is None:
                legacy_settings = QSettings(self._organization, legacy_scope)
                self._legacy_settings_cache[legacy_scope] = legacy_settings
        return SettingsSection(self._settings, prefix=prefix, legacy_settings=legacy_settings)
