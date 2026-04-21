# ADR-001: Adopt a Modular Monolith Baseline

- Status: Accepted
- Date: 2026-04-20

## Context

仓库长期存在 UI 直接摸系统边界、跨 tab 私有方法耦合、配置散落和兼容层残留的问题。继续在现状上堆功能会快速放大维护成本。

## Decision

项目采用“`app / domains / infra / ui` 四层”作为长期目标架构：

- `ui/` 负责界面与交互；
- `app/` 负责跨用例应用服务；
- `domains/` 负责领域规则、领域事件与稳定入口；
- `infra/` 负责系统边界适配。

`core/` 与 `vcp/` 不再视为长期主干层：

- `core/` 仅保留公共工具与历史兼容门面；
- `vcp/` 仅保留聚合门面与兼容入口，真实规则与 provider 实现继续迁往 `domains/` / `infra/`。

跨层协作优先通过稳定入口完成，不允许新的“跨层直摸私有状态”。

## Consequences

- 好处：边界清晰，便于逐步迁移而非一次性重写。
- 代价：短期内要补 facade、service、文档与测试护栏。
