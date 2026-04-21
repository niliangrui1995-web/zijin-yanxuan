# ADR-005: Separate UI Events from Task and Process Execution

- Status: Accepted
- Date: 2026-04-20

## Context

此前 UI 层同时承担：

- 发起事件；
- 拼接任务 id；
- 直接操作进程、窗口和自动化调用；
- 管理部分后台执行细节。

这会把交互层和执行层绑死。

## Decision

- UI 只负责交互触发与结果展示。
- 任务/进程执行下沉到 `infra/tasks`、`infra/navigation`。
- 架构测试持续禁止 UI 直接 import 系统自动化模块。
- 后续 task registry 继续收口任务命名与工厂装配。

## Consequences

- 好处：执行边界可测，可替换，可集中审计。
- 代价：新增执行型能力时需要先设计 adapter，而不是直接在 UI 中写实现。
