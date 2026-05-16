# -*- coding: utf-8 -*-
"""
tests/test_app_config.py — AppConfig 单例行为验证

验证目标：
    1. 单例模式确实生效（两次实例化拿到同一个对象）
    2. 全局导出的 app_config 与手动 AppConfig() 是同一个实例
    3. get/set 读写正确性
"""
import os
from pathlib import Path

from PyQt6.QtCore import QSettings

from core.app_config import AppConfig, app_config
from infra.settings.settings_repository import SettingsRepository
from infra.settings.settings_schema import SettingsMigrator, SettingsSchemaVersion

TEST_SETTINGS_ORGANIZATION = os.environ.get("VCP_HUNTER_SETTINGS_ORGANIZATION", "VCPHunterTests")
TEST_SETTINGS_APPLICATION = os.environ.get("VCP_HUNTER_SETTINGS_APPLICATION", "MainTest")


def _test_qsettings(application: str) -> QSettings:
    return QSettings(
        QSettings.Format.IniFormat,
        QSettings.Scope.UserScope,
        TEST_SETTINGS_ORGANIZATION,
        application,
    )


class TestAppConfigSingleton:
    """验证 AppConfig 的单例模式不会被破坏"""

    def test_singleton_identity(self):
        """两次 AppConfig() 必须返回完全相同的对象"""
        instance_a = AppConfig()
        instance_b = AppConfig()
        assert instance_a is instance_b, "AppConfig 单例模式失效：两次实例化得到了不同对象"

    def test_global_instance_is_singleton(self):
        """模块级 app_config 必须与 AppConfig() 是同一个对象"""
        assert app_config is AppConfig(), (
            "全局 app_config 与 AppConfig() 不是同一个实例，"
            "说明模块导出的单例创建时机有问题"
        )

    def test_app_config_uses_test_settings_store(self):
        root_settings = app_config._repository.root_settings
        assert root_settings.organizationName() == TEST_SETTINGS_ORGANIZATION
        assert root_settings.applicationName() == TEST_SETTINGS_APPLICATION

        settings_root = os.environ.get("VCP_HUNTER_TEST_QSETTINGS_DIR")
        assert settings_root
        settings_file = root_settings.fileName().replace("\\", "/")
        settings_root_path = str(Path(settings_root).resolve()).replace("\\", "/")
        assert settings_file.startswith(f"{settings_root_path}/")

    def test_get_set_roundtrip(self):
        """验证 get/set 读写闭环"""
        test_key = "_test_/roundtrip_check"
        test_value = 42

        app_config.set(test_key, test_value)
        result = app_config.get(test_key, default=0, value_type=int)
        assert result == test_value, f"写入 {test_value}，读回 {result}"

        # 清理测试痕迹
        app_config.remove(test_key)

    def test_get_default_value(self):
        """读取不存在的 key 时，应返回 default 值"""
        result = app_config.get("_test_/nonexistent_key_xyz", default="fallback")
        assert result == "fallback", "不存在的 key 应返回 default 值"

    def test_section_roundtrip(self):
        """section 视图应复用统一入口而不是创建新 scope"""
        section = app_config.section("_test_/section_roundtrip")
        section.set("value", "ok")

        try:
            assert section.value("value", "", type=str) == "ok"
            assert app_config.get("_test_/section_roundtrip/value", "", str) == "ok"
        finally:
            app_config.remove("_test_/section_roundtrip")

    def test_section_migrates_legacy_scope(self):
        """读取旧 scope 时应惰性迁移到统一 Main 配置下"""
        legacy_scope = "LegacyConfigSectionTest"
        legacy = _test_qsettings(legacy_scope)
        legacy.setValue("legacy_key", 7)
        legacy.sync()

        section = app_config.section("_test_/legacy_section", legacy_scope=legacy_scope)

        try:
            assert section.value("legacy_key", 0, type=int) == 7
            assert app_config.get("_test_/legacy_section/legacy_key", 0, int) == 7
        finally:
            app_config.remove("_test_/legacy_section")
            legacy.remove("legacy_key")
            legacy.sync()

    def test_repository_writes_current_schema_version(self):
        assert app_config.schema_version == SettingsSchemaVersion.CURRENT
        assert app_config.get(SettingsSchemaVersion.KEY, 0, int) == SettingsSchemaVersion.CURRENT

    def test_settings_migrator_upgrades_legacy_version_marker(self):
        settings = _test_qsettings("SchemaMigratorTest")
        settings.setValue(SettingsSchemaVersion.KEY, 0)
        settings.sync()

        try:
            migrator = SettingsMigrator(settings)
            assert migrator.current_version() == 0
            assert migrator.migrate() == SettingsSchemaVersion.CURRENT
            assert settings.value(SettingsSchemaVersion.KEY, 0, type=int) == SettingsSchemaVersion.CURRENT
        finally:
            settings.remove(SettingsSchemaVersion.KEY)
            settings.sync()

    def test_settings_migrator_records_schema_migration_event(self):
        settings = _test_qsettings("SchemaMigratorTelemetryTest")
        telemetry = []
        settings.setValue(SettingsSchemaVersion.KEY, 0)
        settings.sync()

        try:
            migrator = SettingsMigrator(
                settings,
                telemetry_writer=lambda event, **fields: telemetry.append((event, fields)),
            )
            assert migrator.migrate() == SettingsSchemaVersion.CURRENT
        finally:
            settings.remove(SettingsSchemaVersion.KEY)
            settings.sync()

        assert telemetry == [
            (
                "settings.schema_migrated",
                {
                    "organization": TEST_SETTINGS_ORGANIZATION,
                    "application": "SchemaMigratorTelemetryTest",
                    "from_version": 0,
                    "to_version": SettingsSchemaVersion.CURRENT,
                    "description": "bootstrap centralized settings repository",
                },
            )
        ]

    def test_repository_records_legacy_scope_migration_event(self):
        telemetry = []
        repo = SettingsRepository(
            TEST_SETTINGS_ORGANIZATION,
            "SettingsRepositoryTelemetryTest",
            telemetry_writer=lambda event, **fields: telemetry.append((event, fields)),
        )
        legacy_scope = "LegacyConfigSectionTelemetryTest"
        legacy = _test_qsettings(legacy_scope)
        legacy.setValue("legacy_key", 7)
        legacy.sync()

        section = repo.section("_test_/legacy_telemetry", legacy_scope=legacy_scope)

        try:
            assert section.value("legacy_key", 0, type=int) == 7
        finally:
            repo.remove("_test_/legacy_telemetry")
            legacy.remove("legacy_key")
            legacy.sync()

        assert (
            "settings.legacy_key_migrated",
            {
                "organization": TEST_SETTINGS_ORGANIZATION,
                "application": "SettingsRepositoryTelemetryTest",
                "legacy_scope": legacy_scope,
                "legacy_key": "legacy_key",
                "target_key": "_test_/legacy_telemetry/legacy_key",
            },
        ) in telemetry

    def test_workspace_mode_configuration_has_been_removed(self):
        assert not hasattr(app_config, "workspace_mode")
        assert not hasattr(app_config, "classic_last_active_tab")
        assert not hasattr(app_config, "research_last_section")
