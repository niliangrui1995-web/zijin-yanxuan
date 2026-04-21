# ADR-002: Centralize Persistent Settings Behind a Repository

- Status: Accepted
- Date: 2026-04-20

## Context

多个 UI 组件直接创建 `QSettings`，导致：

- key 命名无统一规范；
- migration 难以实施；
- 无法自动检查边界；
- 同义配置容易重复生成。

## Decision

引入 `infra/settings`：

- `SettingsRepository` 作为统一存储入口；
- `SettingsMigrator` 管理 schema version；
- `core/app_config.py` 作为兼容 facade；
- UI 只能通过 `app_config` 或 repository section 访问配置。

## Consequences

- 好处：可以做版本迁移、key registry 和边界测试。
- 代价：新增配置需要登记 section 和 key 文档。
