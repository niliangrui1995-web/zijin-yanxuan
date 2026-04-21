# -*- coding: utf-8 -*-
"""
tests/test_app_config.py — AppConfig 单例行为验证

验证目标：
    1. 单例模式确实生效（两次实例化拿到同一个对象）
    2. 全局导出的 app_config 与手动 AppConfig() 是同一个实例
    3. get/set 读写正确性
"""
from PyQt6.QtCore import QSettings

from core.app_config import AppConfig, app_config
from infra.settings.settings_schema import SettingsMigrator, SettingsSchemaVersion


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
        legacy = QSettings("VCPHunter", legacy_scope)
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
        settings = QSettings("VCPHunter", "SchemaMigratorTest")
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
