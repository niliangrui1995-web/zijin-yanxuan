from __future__ import annotations

import os
from pathlib import Path

from PyQt6.QtCore import QSettings

from core.observability import emit_structured_log
from infra.settings.settings_schema import SettingsMigrator


def _configure_test_settings_path_from_env() -> bool:
    settings_root = os.environ.get("VCP_HUNTER_TEST_QSETTINGS_DIR", "").strip()
    if not settings_root:
        return False

    root = Path(settings_root)
    root.mkdir(parents=True, exist_ok=True)
    QSettings.setDefaultFormat(QSettings.Format.IniFormat)
    QSettings.setPath(QSettings.Format.IniFormat, QSettings.Scope.UserScope, str(root))
    QSettings.setPath(QSettings.Format.IniFormat, QSettings.Scope.SystemScope, str(root))
    return True


def _settings_store(organization: str, application: str) -> QSettings:
    if _configure_test_settings_path_from_env():
        return QSettings(
            QSettings.Format.IniFormat,
            QSettings.Scope.UserScope,
            organization,
            application,
        )
    return QSettings(organization, application)


class SettingsSection:
    """Namespaced view over the root settings store with lazy legacy migration."""

    def __init__(
        self,
        root_settings: QSettings,
        prefix: str = "",
        legacy_settings: QSettings | None = None,
        legacy_scope: str = "",
        telemetry_writer=None,
    ) -> None:
        self._root_settings = root_settings
        self._prefix = str(prefix or "").strip("/")
        self._legacy_settings = legacy_settings
        self._legacy_scope = str(legacy_scope or "").strip()
        self._telemetry_writer = telemetry_writer

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
        full_key = self._full_key(key)
        self._root_settings.setValue(full_key, value)
        self._record_legacy_migration(key, full_key)
        return value, True

    def _record_legacy_migration(self, key: str, full_key: str) -> None:
        if not callable(self._telemetry_writer):
            return
        self._telemetry_writer(
            "settings.legacy_key_migrated",
            organization=str(self._root_settings.organizationName() or "").strip(),
            application=str(self._root_settings.applicationName() or "").strip(),
            legacy_scope=self._legacy_scope,
            legacy_key=str(key or "").strip(),
            target_key=str(full_key or "").strip(),
        )

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
    def __init__(
        self,
        organization: str = "VCPHunter",
        application: str = "Main",
        telemetry_writer=None,
    ) -> None:
        self._settings = _settings_store(organization, application)
        self._legacy_settings_cache: dict[str, QSettings] = {}
        self._telemetry_writer = telemetry_writer or emit_structured_log
        self._migrator = SettingsMigrator(
            self._settings,
            telemetry_writer=self._telemetry_writer,
        )
        self._schema_version = self._migrator.migrate()
        self._organization = organization

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
                legacy_settings = _settings_store(self._organization, legacy_scope)
                self._legacy_settings_cache[legacy_scope] = legacy_settings
        return SettingsSection(
            self._settings,
            prefix=prefix,
            legacy_settings=legacy_settings,
            legacy_scope=legacy_scope or "",
            telemetry_writer=self._telemetry_writer,
        )
