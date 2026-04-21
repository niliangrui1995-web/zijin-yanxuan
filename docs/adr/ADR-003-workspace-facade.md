# ADR-003: Use Workspace Facade and Public Tab APIs for Cross-Tab Orchestration

- Status: Accepted
- Date: 2026-04-20

## Context

`ClassicWorkspace` 与 `MainWindowQT` 曾直接访问：

- `workspace.tab_xxx`
- tab 私有方法
- tab 私有状态字段

这种模式让页面内部实现细节外溢，任何 tab 重构都会影响主窗口和其他页。

## Decision

- `ClassicWorkspace` 只暴露公共能力，如 `get_tab()`、`run_incremental_scan()`、`toggle_rt_monitor()`。
- `WorkspaceFacade` 组织跨 tab 聚合逻辑。
- tab 间协作通过公共 API，例如 `get_row_data()`、`shutdown()`、`refresh_watchlist_names()`。
- K 线打开上下文收口到 `app/services/kline_open_service.py`。

## Consequences

- 好处：主窗口不再绑定具体 tab 内部结构。
- 代价：tab 需要维护少量稳定 public wrapper。
