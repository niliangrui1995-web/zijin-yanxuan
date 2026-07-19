# 模块归属与边界登记

最后校验：2026-07-19

本文档登记当前仓库的逻辑模块归属。这里的“owner”不是具体人员，而是维护责任边界：后续改动应优先落到对应模块，不要绕过稳定入口直接修改下游私有实现。

## 模块归属表

| 模块 | 主要路径 | 责任边界 | 稳定入口 |
| --- | --- | --- | --- |
| 应用启动 | `vcp_hunter_qt.pyw`、`ui/main_window_qt.py`、`app/bootstrap/` | 进程启动、主窗口外壳、工作区装配、全局快捷键、状态栏、启动页 | `MainWindowQT`、`ApplicationBootstrap` |
| 运行时服务导出 | `app/services/` | 给 UI 提供稳定应用层 API，隔离 `domains`、`infra`、`vcp` 的真实实现；调用使用按能力拆分的 `ui_*_service.py` | `app.services.ui_*`、`app.services.*` |
| 应用诊断服务 | `app/services/runtime_health_service.py`、`app/services/tab_data_lineage_service.py`、`app/services/ui_diagnostics_service.py` | 给 UI 和稳定性脚本提供运行时健康、数据血缘、降级状态、UI 卡顿探针和导出入口；区分 registry `network_capable` 与实例闩锁 `triggered_network`，校验精确 10 数据 Tab + `system_log` 排除分区 | `collect_runtime_health`、`export_runtime_health_report`、`TabDataLineageService`、`ui_stall_span` |
| 工作区编排 | `ui/workspaces/tab_registry.py`、`ui/workspaces/tab_capabilities.py`、`ui/workspaces/classic_workspace.py`、`ui/workspaces/background_tab_preload.py`、`ui/workspaces/background_preload_receipt.py`、`ui/workspaces/workspace_*_service.py` | registry 统一声明 11 个 Tab、依赖和运行策略；工作区负责固定顺序、并发 1 的 staged eager preload；点击未来占位只调整下一串行槽，构造/本地读取/prime 各一次；超时取消未物理结算时阻断下一串行槽，结算后自动恢复，退出提供 Timer、队列、active step 和 accepted/settled cancellation receipts 的结构化回执；facade 负责导航、表格和跨 Tab 编排 | `TAB_DEFINITIONS`、`DataLineageCapability`、`background_preload_status()`、`BackgroundTabPreloadCoordinator.prioritize`、`ClassicWorkspace`、`WorkspaceFacade` |
| 股票上下文 | `domains/stock_context/`、`app/services/stock_context_*_service.py`、`ui/workspaces/stock_context_service.py`、`ui/workspaces/stock_context_widget_adapter.py` | domain 做纯行到信号转换；综合候选 worker 从同一快照生成候选行与不可变索引；工作区原子发布索引；adapter 只在 GUI 线程通过公开 capability 捕获普通数据。默认快照携带 RPS，关注池定向省略并在 worker 原子读取 active RPS；关注池 UI 命令由 `WorkspaceFacade` 承担 | `StockContextSnapshot`、`StockContextSignalIndex`、`StockContextQueryService`、`StockContextWidgetSnapshotAdapter`、`publish_kline_signal_index` |
| UI 组件 | `ui/components/`、`ui/shell/`、`ui/styles/` | 可复用控件、主窗口壳、主题、QSS、通知、命令面板 | 组件公开方法和信号 |
| Tab 页面 | `ui/tabs/` | 各业务页面的展示、筛选、用户操作和页面级刷新 | `BaseStockTab`、各 Tab 的公开 capability 方法 |
| 表格模型 | `ui/models/` | Qt 表格模型、增量刷新、列展示和 quote 回灌 | `StockTableModel`、`RtTableModel` |
| K 线打开契约 | `app/services/kline_open_service.py`、`app/services/kline_open_context.py`、`ui/kline_load_controller.py` | 从已发布索引按当前代码 O(1) 读取补充，一次构造不可变股票上下文和紧凑导航；稳定 window UUID、递增 generation、任务 ID 和 frame owner 隔离 | `build_kline_open_request`、`get_published_stock_context_signals`、`KlineOpenContext`、`KlineLoadController` |
| K 线数据与准备 | `app/services/kline_data_service.py`、`app/services/kline_render_preparer.py`、`ui/kline_window_runtime.py`、`ui/kline_window_asian.py` | 统一 A 股/亚洲读取、取消/降级、后台 250 根指标/payload/JSON 准备和实时完整小快照重算；只维护 MA10/20/50/150/200、VOL-MA20、MACD/DIFF/DEA，不补 MA5 或历史 B/S/T | `KlineDataService`、`KlineRenderPreparer`、`PreparedKlineRender` |
| K 线渲染与窗口 | `ui/kline_window_qt.py`、`ui/kline_window_stages.py`、`ui/kline_window_rendering.py`、`ui/kline_render_bridge.py`、`ui/kline_window_recovery.py`、`ui/assets/kline/` | `create → handoff/skip → attach → WebEngine shell` 子诊断与六阶段 readiness、latest-only、实际 ECharts rendered 回执、一次崩溃恢复/二次终止、交互与功能矩阵 | `KLineChartWindow`、`KLineOpenStageCoordinator`、`submit_pending_snapshot` |
| K 线物理窗口池 | `ui/kline_window_pool_lifecycle.py`、`ui/components/kline_window_manager.py` | 完整物理 `KLineChartWindow` 单槽池；稳定 UUID/generation、不搬 parent/page、2 秒 reset fail-closed、最新 pending-open、严格 shutdown receipt；复用诊断以 `full_window_reused=true` 和冷路径零耗时表示明确跳过 | `KLineWindowManager`、`KLineWindowPoolLifecycleMixin`、`shutdown_diagnostics` |
| 行情快照 | `domains/quotes/`、`core/global_store.py` | quote payload 标准化、深合并、市值补齐、全局快照存储和广播 | `publish_rt_quotes`、`GlobalStore` |
| 实时行情抓取 | `ui/workers/central_quotes_worker.py`、`infra/market_data/realtime_quote_provider.py` | 中央轮询、单飞行任务、失败冷却、provider 运行态保护 | `CentralQuotesService`、`RealtimeQuoteProvider` |
| 本地行情数据 | `infra/market_data/`、`vcp/data_provider.py`、`vcp/data_provider_history_mixin.py`、`vcp/polars_engine.py` | Parquet/SQLite-first 仓库、SQLite manifest、通达信历史数据生产/fallback、复权、名称映射、运行时缓存，以及只投影 code/date/close 的有限收盘尾值读取 | `TdxDataProvider`、`MarketDataWarehouse`、`WarehouseManifest`、`get_close_tail_batch` |
| F5 原子快照 | `app/services/f5_*`、`app/workers/f5_worker_main.py`、`ui/services/f5_job_controller.py`、`infra/storage/f5_*`、`infra/tasks/app_worker_process.py` | 版本化任务协议、below-normal 隔离 worker；只向 worker 注入 Polars 上限 4/半逻辑核和 BLAS/OMP 单线程环境；专属 monitor 线程、Qt 消息消费、job-local staging、完整 bundle 校验、active 指针/父进程内存原子切换与失败回滚、controller-owned terminal cleanup、active + previous 有界保留和退出磁盘清理回执 | `F5JobRequest`、`F5JobEvent`、`ProcessF5JobRunner`、`F5JobController`、`F5SnapshotInstaller`、`F5SnapshotRepository`、`inspect_f5_runtime` |
| 通用本地存储 | `infra/storage/data_store.py` | SQLite `kv_store` 的读写、连接和事务边界；`core/data_store.py` 仅兼容导出 | `DataStore` |
| JSON 缓存 | `infra/storage/json_cache_repository.py`、`app/services/ui_json_cache_service.py` | UTF-8 读取、结构错误、原子写和文件元数据；`core/json_cache.py` 仅兼容别名 | `load_json_file`、`save_json_file` |
| VCP/RPS 扫描 | `domains/scan/`、`app/services/scan_engine_facade.py` | 指标计算、RPS、VCP 条件、待突破池、实时突破判断 | `VCPEngine`、`IndicatorService`、`BreakoutMonitorService` |
| AI 产业链池 | `domains/industry_chain/`、`infra/storage/industry_chain_repository.py`、`app/services/ui_industry_chain_service.py`、`app/services/ai_industry_chain_period_return_service.py` | domain 只保留规范化/过滤/上下文规则；XLSX、JSON、签名缓存与原子写归 infra；5/10/20 日派生涨幅优先消费有限收盘尾值；`core/ai_industry_chain_pool.py` 仅兼容导出 | `ui_industry_chain_service`、`build_period_return_rows` |
| 龙虎榜池 | `domains/lhb/`、`infra/storage/lhb_pool_repository.py`、`app/services/ui_lhb_pool_service.py` | domain 只保留滚动池纯策略；锁、差量合并、JSON 原子写和旧缓存迁移归 infra；`core/lhb_pool_manager.py` 仅兼容导出 | `LhbPoolManager`、`ui_lhb_pool_service` |
| 龙虎榜市场数据 | `infra/market_data/lhb_provider.py`、`app/services/lhb_market_data_service.py` | AkShare 端点调用、机构/外资席位聚合与协作取消归 infra；UI 和自动刷新只依赖 app 窄入口 | `fetch_lhb_pool_for_date`、`probe_lhb_detail_count_for_date` |
| 业绩异动 | `domains/earnings/`、`app/services/ui_earnings_service.py`、`app/services/earnings_refresh_process_service.py`、`ui/tabs/earnings_tab.py` | 业绩数据扫描、去重、owner 级取消/截止时间和页面展示；自动刷新子进程协议归 app；deprecated `domains.earnings.scheduler.EarningsScheduler` 仅别名到 `EarningsRefreshService` | `EarningsRefreshService`、`run_earnings_refresh`、`EarningsTab` |
| 基金持仓 | `domains/fund_holdings/`、`ui/tabs/fund_holdings_tab.py` | 基金/QFII 持仓同步、存储、对比、信号输出 | `fund_holdings_store`、`fund_holdings_sync_service` |
| 大宗交易缓存 | `infra/storage/foreign_block_repository.py`、`app/services/foreign_block_cache_service.py` | 大宗交易快照路径、schema 校验、原子读写和 UI 视图过滤 | `load_foreign_block_cache`、`save_foreign_block_cache` |
| 大宗交易市场数据 | `infra/market_data/foreign_block_provider.py`、`app/services/foreign_block_market_data_service.py` | AkShare 子进程归 infra；分段、deadline、重试、字段兼容和外资席位过滤归 app；UI 只保留 lifecycle 与展示 | `fetch_foreign_block_payload`、`fetch_foreign_block_records` |
| 北美战报 | `domains/na_daily/`、`infra/storage/na_daily_repository.py`、`app/services/na_daily_service.py` | 纯内容解析、兄弟项目产物读取、缓存及刷新事件编排；`ui/services/na_daily_service.py` 仅兼容别名 | `NADailyRefreshService` |
| 关注池 | `domains/watchlist/`、`app/services/watchlist_indicator_service.py`、`ui/tabs/watchlist_tab.py` | 自选池状态、来源标签和雷达展示；GUI 快照不复制 RPS，纯 worker 在 F5 read boundary 内读取当前 active RPS 并生成指标 | `watchlist_vm`、`WatchlistTab`、`build_watchlist_indicator_results` |
| 市场日历 | `domains/market_calendar/` | 交易日、交易时段、报价刷新窗口、多市场时间判断 | `MarketCalendar` |
| 后台任务 | `infra/tasks/`、`app/services/ui_task_lifecycle_service.py`、`core/background_job_runner.py` | Typed task registry、后台执行、子进程封装、协作取消、截止时间和 owner 级有界关闭 | `task_registry`、`TaskLifecycleGroup`、`background_job_runner` |
| 自动刷新调度 | `ui/services/auto_refresh_scheduler.py`、`ui/services/auto_refresh_tasks.py` | scheduler 负责时刻、去重、重试、生命周期及 preload settled 后 60 秒稳定窗口；task service 执行具体刷新，二者不与首开预载争用启动窗口 | `AutoRefreshScheduler`、`AutoRefreshTaskService` |
| 服务开关 | `infra/features/` | 可选运行能力的稳定开关、环境变量覆盖和启动编排门控 | `service_toggle_registry` |
| 运行诊断 | `infra/diagnostics/`、`ui/components/runtime_health_dialog.py`、`scripts/runtime_health_stability_suite.py`、`scripts/kline_webengine_lifecycle_smoke.py`、`scripts/perf_budget_check.py` | 原生 production、10 轮 K 线精确门禁、30/60 分钟 soak；真实打开按 `kline_open_to_chart_ready` 独立 reset/封存 stall receipt，并采集 RSS、线程、Task/Timer/Receiver、WebEngine、F5 与严格 shutdown receipt | `collect_runtime_health`、`UiStallProbe`、`KLINE_OPEN_UI_STALL_SCOPE`、`perf_budget_check.py` |
| 配置持久化 | `infra/settings/`、`core/app_config.py` | QSettings、表格状态、窗口状态、配置 schema | `app_config`、`TableViewStateStore` |
| 事件与日志通道 | `domains/runtime/domain_events.py`、`infra/events/ui_signal_hub.py`、`ui/services/log_buffer_service.py`、`infra/diagnostics/ui_exception_boundary.py` | 领域事件、UI 导航事件分流；应用级日志缓冲独立于延迟创建的日志 Tab；Qt 回调异常留痕并阻断 PyQt native fast-fail | `domain_events`、`ui_signal_hub`、`LogBufferService`、`install_ui_exception_hook` |
| 兼容门面 | `core/`、`vcp/`、`earnings/` | 历史 import 兼容；新增真实实现不应优先落在这里 | 兼容导出 |
| 测试与质量 | `tests/`、`scripts/check_utf8.py`、`pyproject.toml` | 架构边界、单元回归、编码检查、Ruff 配置 | `pytest`、`ruff`、`check_utf8.py` |

## 变更落点规则

- UI 新交互优先落在 `ui/tabs/`、`ui/components/` 或 `ui/workspaces/`，跨层依赖通过按能力拆分的 `app.services.ui_*` 或其他明确 `app.services.*` 入口暴露。原 `ui_runtime_service.py` 在确认仓内生产调用为 0 后已删除，架构门禁继续禁止重新引入宽泛桶导入。
- UI 需要诊断能力时通过 `app/services/ui_diagnostics_service.py` 访问；真实采集实现落在 `infra/diagnostics/`，不要让 UI 直接 import `infra`。
- 新领域规则优先落在 `domains/`，不要塞进主窗口或 Tab 私有方法。
- 新外部数据源、文件读写、进程调用优先落在 `infra/`。
- 大规模历史行情明细优先落在 `data/Cache/parquet/` 的 Parquet 文件；索引、manifest、健康状态和数据质量标记落在 `data/vcp_hunter.db` 的 SQLite 表，不要把明细行情塞进 SQLite。
- 本地行情读写规则优先维护 `infra/market_data/market_data_warehouse.py` 和 `infra/market_data/warehouse_manifest.py`；`vcp/polars_engine.py` 只保留兼容加速入口和 Parquet 物理写入兼容。
- 新的实时行情字段优先扩展 `domains/quotes/`，由表格模型统一消费。
- 新后台任务必须在 `infra/tasks/typed_task_registry.py` 注册，UI 层不要硬编码 task id。
- 新可选运行能力应注册到 `infra/features/service_toggle_registry.py`，不要把布尔开关散落在 UI 或启动编排器中。
- 新数据页必须在 registry 中声明数据血缘或明确排除，并提供 `get_data_lineage()` 所需证据；`network_capable` 与实例 `triggered_network` 不得混用，缺失/非 bool/`lineage_error` 必须 fail-closed。
- 新跨 Tab 能力优先通过 `ui/workspaces/tab_capabilities.py` 声明协议，再由 `WorkspaceFacade` 聚合。
- 新增或调整 Tab 必须只改 `ui/workspaces/tab_registry.py` 的声明和对应页面实现，不得在其他服务复制 key 列表或用视觉分组推断业务策略。
- StockContext 后台计算只接收普通数据快照；不得把 QWidget、Qt model 或 UI 绑定方法传入 worker。
- 新 F5 产物必须进入同一个版本化 bundle，并提供校验、取消/超时、active/previous 保留和失败回滚。
- K 线异步阶段必须使用当前 `window_id + generation + code` owner；`chart_ready` 只能来自严格匹配的 ECharts rendered 回执。冷 Browser 路径的 create/handoff/attach/shell 与六阶段 readiness 必须分别留痕；完整窗口复用的零耗时表示明确跳过。物理窗口回池不得移动 Browser/Page 层级，2 秒 reset 未确认或 cleanup 不干净时必须销毁。
- 新文档应放在 `docs/`，README 只保留快速入口和关键索引。
