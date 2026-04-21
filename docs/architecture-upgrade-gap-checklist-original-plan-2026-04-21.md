# 按原始方案口径的剩余 Gap 清单（2026-04-21）

## 判定口径

- 以外部原始方案 `C:/Users/Administrator/Desktop/紫金研选_架构升级落地方案_2026-04-20.docx` 为准。
- 本清单不沿用 2026-04-21 仓库内已放宽的“稳定入口即可”口径，而是回到原方案的三条核心目标：
  - UI 只负责显示和交互，不再直接掌管业务编排、`QSettings`、`subprocess`、外部终端跳转和跨页数据聚合。
  - 行情接入、扫描引擎、观察池、基金持仓、财报、市场日历等能力统一通过 `app/service` 层编排。
  - `ClassicWorkspace` 回到装配层；`vcp/data_provider.py`、`vcp/engine.py` 退化为兼容壳，真实实现迁往目标目录。

## 本轮作为 Plan 的 Gap 清单

| 优先级 | 原始 gap | 方案映射 | 本轮落地动作 | 当前状态 |
| --- | --- | --- | --- | --- |
| P0 | UI 仍直接 import `domains/*`、`infra/*` 以及 `core.app_config` / `core.background_job_runner` / `core.domain_events` / `core.ui_signals` 等运行时入口，绕过 `app/service` 编排 | 执行摘要最终目标一、二；Phase 1 / 4.1 | 新增 `app/services/ui_runtime_service.py` 作为 UI 运行时聚合入口；批量把 `ui/*` 的运行时依赖切到 app 层；补边界测试禁止 UI 回流到 `domains/*`、`infra/*`、`vcp/*` 与上述 `core/*` 兼容入口 | 已闭环 |
| P0 | Workspace / Tab 解耦不彻底，跨页协作仍可能回到 `table_scan`、`table_rt`、`model.row_data`、`FOREIGN_KEYWORDS` 等私有状态摸取 | Phase 2；“ClassicWorkspace 回到装配层，切断 Tab 之间的隐式耦合” | 新增 `ui/workspaces/tab_capabilities.py`；`BaseStockTab` 暴露公共 capability；`WorkspaceFacade`、`QuoteUniverseService`、`WatchlistRadarService`、`WorkspaceNavigationService`、`WorkspaceTableService` 全部改走公共能力和公共方法；补边界测试禁止 workspace 层再次摸 tab 私有状态 | 已闭环 |
| P0 | `vcp/data_provider.py` 与 `vcp/engine.py` 仍承载真实实现，不符合“迁移后只留薄兼容壳”的要求 | Phase 3；目录迁移蓝图 | 真实 provider 实现迁入 `infra/market_data/tdx_data_provider.py`；真实 engine 实现迁入 `app/services/scan_engine_facade.py`；`vcp/data_provider.py`、`vcp/engine.py` 改为 `sys.modules` alias shim；补充 shim 断言测试 | 已闭环 |
| P1 | 仓库内基线与回顾文档曾按“稳定入口即可”放宽口径自我判定完成，和原方案要求不一致 | Phase 5；治理资产与 CI 闸门 | 回写 `docs/architecture-baseline-2026-04-20.md`，明确 UI 运行时依赖必须先走 `app/*`；新增本清单作为原始方案口径验收记录；补齐更严格的 `tests/test_architecture_boundaries.py` 护栏 | 已闭环 |

## 关键证据

- UI 运行时边界：
  - `app/services/ui_runtime_service.py`
  - `tests/test_architecture_boundaries.py`
- Workspace / Tab capability：
  - `ui/workspaces/tab_capabilities.py`
  - `ui/workspaces/workspace_facade.py`
  - `ui/workspaces/quote_universe_service.py`
  - `ui/workspaces/watchlist_radar_service.py`
  - `ui/workspaces/workspace_navigation_service.py`
  - `ui/workspaces/workspace_table_service.py`
- Legacy shim：
  - `infra/market_data/tdx_data_provider.py`
  - `app/services/scan_engine_facade.py`
  - `vcp/data_provider.py`
  - `vcp/engine.py`
  - `tests/test_provider_services.py`
  - `tests/test_engine_services.py`

## 本轮验证

- `pytest tests/test_architecture_boundaries.py tests/test_workspace_quote_codes.py tests/test_engine_services.py tests/test_provider_services.py tests/test_domain_entrypoints.py tests/test_main_window_shell.py -q`
- `pytest tests/test_application_bootstrap.py tests/test_event_bus_layers.py tests/test_market_data_ports.py tests/test_background_job_runner.py tests/test_task_manager.py tests/test_kline_open_service.py tests/test_app_config.py -q`
- `python scripts/check_utf8.py app ui infra vcp tests docs`

## 结论

- 按原始方案口径，本轮进入时仍有 4 个核心 gap。
- 截至当前代码与文档状态，这 4 项已全部收口；未再发现新的 P0 / P1 级未闭环项。
- 后续若出现 UI 重新直接依赖 `domains/*`、`infra/*` 或 workspace 重新摸 tab 私有状态，应视为对原始方案的直接回退。
