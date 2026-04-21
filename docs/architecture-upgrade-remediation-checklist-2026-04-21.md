# 架构升级未落地项整改清单（2026-04-21）

## 依据

- 外部方案：`C:/Users/Administrator/Desktop/紫金研选_架构升级落地方案_2026-04-20.docx`
- 内部计划：[2026-04-20-architecture-upgrade.md](D:/vcp_hunter/紫金研选/docs/plans/2026-04-20-architecture-upgrade.md)
- 当前基线：[architecture-baseline-2026-04-20.md](D:/vcp_hunter/紫金研选/docs/architecture-baseline-2026-04-20.md)

## 整改目标

对照完整版落地方案，将仓库从“已完成 Phase 0-2、部分完成 Phase 4”的状态，补齐到：

- `app / domains / infra / ui` 四层边界清晰；
- `MainWindowQT / ClassicWorkspace / BaseStockTab` 不再继续承载跨层基础设施细节；
- `scan / market_data / earnings / fund_holdings / quotes / market_calendar` 具备明确的目标归属目录；
- 旧兼容路径只保留必要薄封装，不再承载真实实现；
- 文档、CI、边界测试、UTF-8 护栏与整改结果同步更新。

## 当前结论

- 状态：已按本清单完成落地，旧路径已收敛为兼容 shim 或聚合门面。
- app 边界：`ApplicationBootstrap` / `WindowCommandService` 已改走主窗口公开接口。
- UI 收口：共享标题栏已抽到 `ui/components/shared_title_bar.py`，`ClassicWorkspace` 非装配逻辑已下沉到 facade/service。
- 目标目录：`domains/scan`、`domains/earnings`、`domains/quotes`、`domains/watchlist`、`domains/fund_holdings`、`domains/market_calendar` 与 `infra/market_data`、`infra/tasks` 已补齐。
- 真实调用方：关注池、基金持仓、业绩调度、报价快照、后台任务默认入口已切到新的稳定包路径。
- 治理闭环：架构测试、兼容入口测试、CI 清单、README、baseline 已同步。

## 未落地项

| 优先级 | 整改项 | 方案映射 | 当前问题 | 整改标准 |
| --- | --- | --- | --- | --- |
| P0 | `ApplicationBootstrap` 去 UI 具体实现耦合 | Phase 1 / 4.1 | `app/bootstrap/application_bootstrap.py` 直接 import `ClassicWorkspace`、`CentralQuotesService`、`install_table_copy_hooks` | `app` 不再直接 import `ui.*` 具体实现，改为依赖宿主公开工厂/绑定 |
| P0 | `WindowCommandService` 去私有窗口方法耦合 | Phase 2 / 原则 5 | `app/use_cases/window_command_service.py` 直接依赖 `_action_refresh_f5`、`_activate_workspace_tab`、`_apply_table_density`、`_on_show_kline`、`ui.theme` | 改为依赖显式公开命令接口，不直接引用私有方法名和 `ui.theme` |
| P0 | 提取共享标题栏组件 | 风险表 R9 / Phase 1 | `ui/kline_window_qt.py` 仍从 `ui.components.main_window_shell` 引入 `DraggableTitleBar` | 提取 `SharedTitleBar` / 共享标题栏组件，K 线窗口和主壳不再通过 shell helper 耦合 |
| P0 | `ClassicWorkspace` 继续瘦身 | Phase 2 / 成功标准 1 | 工作区仍承载表格遍历、跨页定位、刷新编排等非装配逻辑 | 提取 table/navigation 相关 service，由 facade 统一编排，`ClassicWorkspace` 主要保留装配与生命周期连接 |
| P0 | `task_manager` 真实实现下沉到 `infra/tasks` | Phase 1 / Phase 4 | `core/task_manager.py` 仍承载真实线程池实现，`BackgroundJobRunner` 默认回落到旧路径 | 将真实实现迁到 `infra/tasks`，`core/task_manager.py` 仅保留兼容 shim |
| P1 | `scan` 纯规则服务落到 `domains/scan` | Phase 3 / 目标蓝图 | 纯规则服务仍在 `vcp/` | `IndicatorService`、`RpsService`、`VcpScannerService`、`BreakoutMonitorService` 有明确 `domains/scan` 落点，旧路径仅兼容导出 |
| P1 | `market_data` 服务落到 `infra/market_data` | Phase 3 / 目标蓝图 | provider 子服务仍在 `vcp/` | `AdjustmentService`、`LocalHistoryProvider`、`RealtimeQuoteProvider` 有明确 `infra/market_data` 落点，旧路径仅兼容导出 |
| P1 | `earnings` 目标上下文补齐到 `domains/earnings` | Phase 3 / 目标蓝图 | `earnings/` 仍为顶层包 | `domains/earnings` 存在真实实现或主导出，旧 `earnings/` 退化为兼容壳 |
| P1 | `quotes / market_calendar / fund_holdings / watchlist` 目标上下文补齐 | 目标蓝图 | `domains/` 目录不完整 | 补齐目标目录与稳定入口，消除“只有方案没有落点”的状态 |
| P1 | App 层边界测试补齐 | Phase 0 / 4.2 | 现有架构测试主要盯 `ui`，未阻止 `app -> ui` 回流 | 新增 `app` 边界测试，禁止直接 import `ui.*` 具体实现 |
| P1 | CI 回归集补齐 | Phase 5 / 发布闸门 | CI 未纳入 `event_bus_layers`、`application_bootstrap`、`market_data_ports` | 关键结构回归测试进入 CI |
| P2 | 文档与 owner 资产补齐 | Phase 5 / 第九章 | 有 registry，但缺整改闭环说明和模块 owner | README / baseline / checklist / owner 说明同步更新 |

## 验收口径

- `pytest` 关键回归通过；
- `scripts/check_utf8.py` 通过；
- `tests/test_architecture_boundaries.py` 能阻止 app/ui 重新回流；
- 目标目录存在稳定入口，兼容层变为薄封装；
- 文档能反映整改后真实结构。

## 本轮验证记录

- `pytest tests/test_architecture_boundaries.py tests/test_application_bootstrap.py tests/test_domain_entrypoints.py tests/test_market_data_ports.py tests/test_event_bus_layers.py tests/test_provider_services.py tests/test_engine_services.py tests/test_background_job_runner.py tests/test_task_manager.py tests/test_workspace_quote_codes.py tests/test_kline_open_service.py tests/test_main_window_shell.py -q`
- `pytest tests/test_architecture_boundaries.py tests/test_app_config.py tests/test_background_job_runner.py tests/test_task_manager.py tests/test_provider_services.py tests/test_engine_services.py tests/test_market_data_ports.py tests/test_application_bootstrap.py tests/test_event_bus_layers.py tests/test_main_window_shell.py tests/test_kline_open_service.py tests/test_workspace_quote_codes.py tests/test_earnings_engine_state.py tests/test_earnings_scheduler_startup.py tests/test_fund_holdings_compare.py tests/test_fund_holdings_store.py -q`
- `python scripts/check_utf8.py core ui vcp tests scripts app infra docs .github domains earnings`

## 本轮执行原则

1. 每做完一类整改，立刻补对应测试或边界护栏。
2. 优先移动“真实实现”，旧模块只留最薄兼容壳。
3. 不做大爆炸重写；所有迁移以保持现有测试通过为前提。
