# ADR-004: Treat Data Provider and Engine as Stable Application Ports

- Status: Accepted
- Date: 2026-04-20

## Context

UI 页面普遍依赖 `data_provider` 和 `engine`，但过去缺少明确约束，容易把底层数据/执行细节继续往 UI 层泄露。

## Decision

- `data_provider` 与 `engine` 被视为应用端口，而不是 UI 可任意扩展的实现细节。
- UI 可以消费它们的稳定读接口，但不应引入新的 vendor/network/system 依赖。
- 跨页面组合逻辑优先落在 `app/` 或 `workspace_facade`，而不是直接堆到 provider/engine 上。

## Consequences

- 好处：后续替换 provider/engine 实现时，UI 影响面可控。
- 代价：需要持续识别并抽离 UI 中的非端口职责。
