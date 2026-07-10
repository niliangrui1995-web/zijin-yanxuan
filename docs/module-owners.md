# 模块归属与边界登记

本文档登记当前仓库的逻辑模块归属。这里的“owner”不是具体人员，而是维护责任边界：后续改动应优先落到对应模块，不要绕过稳定入口直接修改下游私有实现。

## 模块归属表

| 模块 | 主要路径 | 责任边界 | 稳定入口 |
| --- | --- | --- | --- |
| 应用启动 | `vcp_hunter_qt.pyw`、`ui/main_window_qt.py`、`app/bootstrap/` | 进程启动、主窗口外壳、工作区装配、全局快捷键、状态栏、启动页 | `MainWindowQT`、`ApplicationBootstrap` |
| 运行时服务导出 | `app/services/` | 给 UI 提供稳定应用层 API，隔离 `domains`、`infra`、`vcp` 的真实实现；新 UI 调用优先使用按能力拆分的 `ui_*_service.py` | `app.services.ui_*`、`app.services.*`、兼容门面 `app.services.ui_runtime_service` |
| 应用诊断服务 | `app/services/runtime_health_service.py`、`app/services/tab_data_lineage_service.py`、`app/services/ui_diagnostics_service.py` | 给 UI 和稳定性脚本提供运行时健康、数据血缘、降级状态、UI 卡顿探针和导出入口 | `collect_runtime_health`、`export_runtime_health_report`、`TabDataLineageService`、`ui_stall_span` |
| 工作区编排 | `ui/workspaces/` | Tab 注册、跨 Tab 导航、表格聚合、实时订阅代码集合、个股信号聚合 | `ClassicWorkspace`、`WorkspaceFacade` |
| UI 组件 | `ui/components/`、`ui/shell/`、`ui/styles/` | 可复用控件、主窗口壳、主题、QSS、通知、命令面板 | 组件公开方法和信号 |
| Tab 页面 | `ui/tabs/` | 各业务页面的展示、筛选、用户操作和页面级刷新 | `BaseStockTab`、各 Tab 的公开 capability 方法 |
| 表格模型 | `ui/models/` | Qt 表格模型、增量刷新、列展示和 quote 回灌 | `StockTableModel`、`RtTableModel` |
| K 线窗口 | `ui/kline_*.py`、`ui/components/kline_window_manager.py` | K 线窗口生命周期、图表 payload、前后切换、摘要卡片 | `KLineWindowManager`、`build_kline_open_request` |
| 行情快照 | `domains/quotes/`、`core/global_store.py` | quote payload 标准化、深合并、市值补齐、全局快照存储和广播 | `publish_rt_quotes`、`GlobalStore` |
| 实时行情抓取 | `ui/workers/central_quotes_worker.py`、`infra/market_data/realtime_quote_provider.py` | 中央轮询、单飞行任务、失败冷却、provider 运行态保护 | `CentralQuotesService`、`RealtimeQuoteProvider` |
| 本地行情数据 | `infra/market_data/`、`vcp/data_provider.py`、`vcp/polars_engine.py` | Parquet/SQLite-first 仓库、SQLite manifest、通达信历史数据生产/fallback、复权、名称映射、运行时缓存 | `TdxDataProvider`、`MarketDataWarehouse`、`WarehouseManifest` |
| VCP/RPS 扫描 | `domains/scan/`、`app/services/scan_engine_facade.py` | 指标计算、RPS、VCP 条件、待突破池、实时突破判断 | `VCPEngine`、`IndicatorService`、`BreakoutMonitorService` |
| 业绩异动 | `domains/earnings/`、`ui/tabs/earnings_tab.py` | 业绩数据扫描、去重、调度和页面展示 | `EarningsScheduler`、`EarningsTab` |
| 基金持仓 | `domains/fund_holdings/`、`ui/tabs/fund_holdings_tab.py` | 基金/QFII 持仓同步、存储、对比、信号输出 | `fund_holdings_store`、`fund_holdings_sync_service` |
| 关注池 | `domains/watchlist/`、`ui/tabs/watchlist_tab.py` | 自选池状态、来源标签、关注池雷达展示 | `watchlist_vm`、`WatchlistTab` |
| 市场日历 | `domains/market_calendar/` | 交易日、交易时段、报价刷新窗口、多市场时间判断 | `MarketCalendar` |
| 后台任务 | `infra/tasks/`、`core/background_job_runner.py` | Typed task registry、后台执行、子进程封装、任务取消与错误表示 | `task_registry`、`background_job_runner` |
| 服务开关 | `infra/features/` | 可选运行能力的稳定开关、环境变量覆盖和启动编排门控 | `service_toggle_registry` |
| 运行诊断 | `infra/diagnostics/`、`ui/components/runtime_health_dialog.py`、`scripts/runtime_health_stability_suite.py` | Runtime health 采集、WebEngine/进程/Timer/事件订阅观测、UI 卡顿探针、长稳采样和预算报告 | `collect_runtime_health`、`UiStallProbe`、`RuntimeHealthDialog`、`perf_budget_check.py` |
| 配置持久化 | `infra/settings/`、`core/app_config.py` | QSettings、表格状态、窗口状态、配置 schema | `app_config`、`TableViewStateStore` |
| 事件通道 | `domains/runtime/domain_events.py`、`infra/events/ui_signal_hub.py` | 领域事件、应用事件、UI 导航事件分流 | `domain_events`、`ui_signal_hub` |
| 兼容门面 | `core/`、`vcp/`、`earnings/` | 历史 import 兼容；新增真实实现不应优先落在这里 | 兼容导出 |
| 测试与质量 | `tests/`、`scripts/check_utf8.py`、`pyproject.toml` | 架构边界、单元回归、编码检查、Ruff 配置 | `pytest`、`ruff`、`check_utf8.py` |

## 变更落点规则

- UI 新交互优先落在 `ui/tabs/`、`ui/components/` 或 `ui/workspaces/`，跨层依赖通过按能力拆分的 `app.services.ui_*` 或其他明确 `app.services.*` 入口暴露，不再新增 `ui_runtime_service.py` 依赖。
- UI 需要诊断能力时通过 `app/services/ui_diagnostics_service.py` 访问；真实采集实现落在 `infra/diagnostics/`，不要让 UI 直接 import `infra`。
- 新领域规则优先落在 `domains/`，不要塞进主窗口或 Tab 私有方法。
- 新外部数据源、文件读写、进程调用优先落在 `infra/`。
- 大规模历史行情明细优先落在 `data/Cache/parquet/` 的 Parquet 文件；索引、manifest、健康状态和数据质量标记落在 `data/vcp_hunter.db` 的 SQLite 表，不要把明细行情塞进 SQLite。
- 本地行情读写规则优先维护 `infra/market_data/market_data_warehouse.py` 和 `infra/market_data/warehouse_manifest.py`；`vcp/polars_engine.py` 只保留兼容加速入口和 Parquet 物理写入兼容。
- 新的实时行情字段优先扩展 `domains/quotes/`，由表格模型统一消费。
- 新后台任务必须在 `infra/tasks/typed_task_registry.py` 注册，UI 层不要硬编码 task id。
- 新可选运行能力应注册到 `infra/features/service_toggle_registry.py`，不要把布尔开关散落在 UI 或启动编排器中。
- 新数据页尽量提供数据血缘或 `get_data_lineage()`，便于运行时健康面板和稳定性 suite 追踪。
- 新跨 Tab 能力优先通过 `ui/workspaces/tab_capabilities.py` 声明协议，再由 `WorkspaceFacade` 聚合。
- 新文档应放在 `docs/`，README 只保留快速入口和关键索引。
