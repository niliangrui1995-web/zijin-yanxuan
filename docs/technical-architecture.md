# 紫金研选技术架构文档

最后校验：2026-05-12（Asia/Shanghai 工作区环境）

本文档按当前源码重新整理，目标读者是后续维护者、二次开发者和需要判断架构演进方向的技术协作者。它不复述每个文件的实现细节，而是说明系统定位、启动链路、模块边界、关键数据流、质量护栏和当前架构升级判断。

## 1. 系统定位

紫金研选是 Windows 优先的 PyQt6 桌面量化终端，核心围绕 A 股 VCP 扫描、盘中监控、关注池、情报源联动、K 线复盘和多市场辅助观察展开。

它不是 Web 服务，也没有后端 API 服务器。当前主要运行形态是单机桌面应用：

- 入口：`vcp_hunter_qt.pyw`
- UI 框架：PyQt6、PyQt6-WebEngine、自研动态 QSS 主题系统
- 本地历史数据：Parquet/SQLite-first 本地仓库，通达信 `vipdoc` 保留为生产源和 fallback
- 盘中行情：东方财富 HTTP，异常时回退新浪批量报价
- 海外/亚洲辅助数据：AkShare、yfinance、`curl_cffi`
- 本地持久化：`data/Cache/`、`data/vcp_hunter.db`、QSettings；大规模日线明细放 Parquet，索引/状态/健康信息放 SQLite

架构判断必须围绕这个形态展开。当前项目不适合按 Web 后端、微服务或云原生服务拆分来评估。

## 2. 总体分层

当前代码按“应用编排、领域服务、基础设施、界面、兼容门面”分层维护：

| 层级 | 目录 | 主要职责 |
| --- | --- | --- |
| 应用编排层 | `app/` | 给 UI 提供稳定入口，封装启动装配、数据提供者、扫描引擎、K 线请求、运行时健康和跨层服务导出 |
| 领域服务层 | `domains/` | 承载 quotes、scan、earnings、fund_holdings、watchlist、market_calendar、global_earnings_calendar 等稳定领域能力 |
| 基础设施层 | `infra/` | 承载 market_data、settings、storage、tasks、navigation、events、features、diagnostics 等外部边界适配 |
| UI 层 | `ui/` | PyQt 主窗口、工作区、Tab、组件、模型、样式、worker、窗口交互和可视化诊断面板 |
| 兼容门面 | `vcp/`、`core/`、`earnings/` | 保留历史入口并逐步委托到 `app/`、`domains/`、`infra/` 的真实实现 |

当前架构治理的关键约束是：UI 不直接依赖 `domains/`、`infra/`、`vcp/` 的具体实现；UI 通过 `app.services.ui_*` 窄服务、其他明确 `app.services.*` 入口和工作区能力协议访问跨层能力。`app.services.ui_runtime_service` 仅保留为非 UI 历史脚本和迁移期兼容门面。对应约束由 `tests/test_architecture_boundaries.py` 回归保护。

## 3. 启动链路

主启动路径如下：

```text
vcp_hunter_qt.pyw
  -> MainWindowQT
  -> ApplicationBootstrap
  -> TdxDataProvider(offline=True)
  -> VCPEngine
  -> ClassicWorkspace
  -> StartupOrchestrator
  -> CentralQuotesService
```

关键行为：

- `vcp_hunter_qt.pyw` 负责项目虚拟环境重启、崩溃日志、Qt WebEngine 运行时配置、`QApplication` 初始化、单实例互斥锁、主题和启动页。
- `MainWindowQT` 是主窗口外壳，负责无边框窗口、标题栏、快捷键、状态栏、工作区挂载、全局事件接线、F5 入口和运行时健康入口。
- `ApplicationBootstrap` 负责装配工作区，并通过 `service_toggle_registry` 决定是否安装中央行情广播服务。
- `create_data_provider(offline=True)` 默认离线优先，冷启动时先保证本地缓存和 UI 可用。
- `StartupOrchestrator` 延迟恢复历史缓存、RPS 缓存、实时缓存，异步探测网络，并按服务开关调度亚洲缓存、全球财报日历和盘中监控启动。

## 4. 应用层服务入口

`app/services/` 是 UI 与领域/基础设施之间的稳定入口。当前主要服务包括：

| 服务 | 主要职责 |
| --- | --- |
| `runtime_services.py` | 创建 `TdxDataProvider`、`StartupOrchestrator`，并封装本地股本快照读取 |
| `scan_engine_facade.py` | 对外提供 `VCPEngine`，内部组合 `domains/scan/*` 和兼容的 VCP 模型/外部检查 |
| `scan_runtime_service.py` | 给扫描相关 UI 提供指标计算、RPS、财务检查、待突破池等运行时能力 |
| `kline_open_service.py` | 构造 K 线打开请求，合并来源 Tab、当前行和扫描上下文 |
| `kline_webengine_preflight.py` | 封装 K 线 WebEngine 可用性预检 |
| `asian_market_service.py` | 封装亚洲市场缓存、yfinance 会话和限流状态 |
| `runtime_health_service.py` | 对 UI 暴露运行时健康采集和导出 |
| `ui_diagnostics_service.py` | 对 UI 暴露卡顿探针安装和 `ui_stall_span`，隔离底层 `infra.diagnostics` 实现 |
| `tab_data_lineage_service.py` | 为关键 Tab 返回统一数据血缘：来源、缓存、是否联网、是否降级、更新时间和签名 |
| `ui_config_service.py` / `ui_event_service.py` / `ui_task_service.py` | 给 UI 暴露配置、事件总线、后台任务和进程执行入口 |
| `ui_quote_service.py` / `ui_market_calendar_service.py` | 给 UI 暴露行情标准化、广播、指标解析和交易日历入口 |
| `ui_earnings_calendar_service.py` / `ui_earnings_service.py` | 给 UI 暴露全球财报日历和业绩调度入口 |
| `ui_fund_holdings_service.py` / `ui_watchlist_service.py` / `ui_navigation_service.py` | 给 UI 暴露基金持仓、关注池和外部终端跳转入口 |
| `ui_runtime_service.py` | 迁移期兼容门面，只转发上述窄服务；新 UI 代码不再从这里导入 |

`ui_runtime_service.py` 已从 UI 主调用面退到兼容门面。新 UI 代码应按能力导入更窄的 `ui_*_service.py`，旧脚本或外部探针可以继续通过该门面过渡。

## 5. 主工作区与 Tab 装配

当前唯一工作区是 `ClassicWorkspace`，代码位置为 `ui/workspaces/classic_workspace.py`。它通过 `_tab_specs` 装配 12 个主 Tab：

| key | 页面 | 模块 | 分组 |
| --- | --- | --- | --- |
| `watchlist` | 关注池 | `ui/tabs/watchlist_tab.py` | 主工作台 |
| `asian_market` | 亚洲寡头 | `ui/tabs/asian_market_tab.py` | 主工作台 |
| `na_daily` | 北美战报 | `ui/tabs/na_daily_tab.py` | 主工作台 |
| `stock_candidates` | 综合候选 | `ui/tabs/stock_candidate_tab.py` | 主工作台 |
| `ai_industry_chain` | AI产业链 | `ui/tabs/ai_industry_chain_tab.py` | 情报源 |
| `lhb` | 龙虎榜 | `ui/tabs/lhb_tab.py` | 主工作台 |
| `rt_monitor` | 盘中监控 | `ui/tabs/rt_monitor_tab.py` | 主工作台 |
| `scan` | VCP扫描 | `ui/tabs/scan_tab.py` | 情报源 |
| `foreign_block` | 大宗交易 | `ui/tabs/foreign_block_trade_tab.py` | 情报源 |
| `earnings` | 业绩异动 | `ui/tabs/earnings_tab.py` | 情报源 |
| `fund_holdings` | 基金持仓 | `ui/tabs/fund_holdings_tab.py` | 情报源 |
| `system_log` | 系统日志 | `ui/tabs/log_tab.py` | 系统 |

当前工作区的启动策略是：

- 首屏只真实加载 `watchlist`。
- 其他 Tab 先挂载 `LazyTabPlaceholder`。
- 用户切换到某个 Tab 时通过 `ensure_tab_loaded()` 按需加载。
- 后台通过 `BACKGROUND_PREWARM_DELAY_MS` 和 `BACKGROUND_PREWARM_INTERVAL_MS` 分批预热未加载 Tab。
- 上次 Tab 恢复通过 `RESTORE_LAST_TAB_DELAY_MS` 延后执行，避免挤占首屏响应。

跨 Tab 编排不直接访问具体 Tab 的私有字段，而是通过 `WorkspaceFacade` 和能力协议聚合：

- `WorkspaceNavigationService`：分组导航、跨 Tab 定位和选中。
- `WorkspaceTableService`：表格集合、F5 后快照回灌和分帧刷新。
- `QuoteUniverseService`：聚合需要订阅实时行情的 A 股代码。
- `StockContextService`：汇总扫描、战报、AI 产业链、大宗、业绩、基金持仓、龙虎榜等来源的个股信号。
- `tab_capabilities.py`：定义公开能力协议，避免工作区服务触碰 Tab 私有结构。

## 6. 实时行情链路

盘中表格的 `现价 / 涨幅 / 市值` 统一走中央行情链路：

```text
QuoteUniverseService.collect_realtime_quote_codes()
  -> CentralQuotesService
  -> TdxDataProvider.fetch_realtime_quotes_batch()
  -> domains.quotes.enrich_quotes_with_finance()
  -> domains.quotes.publish_rt_quotes()
  -> GlobalStore.merge_quotes()
  -> domain_events.sig_rt_quotes
  -> BaseStockTab / TableModel.update_quotes()
```

关键设计：

- `CentralQuotesService` 默认 30 秒轮询一次，但同一时刻只允许一个抓取任务在飞。
- 非报价刷新时段不会持续拉取盘中行情，只在必要时构建一次盘后快照。
- 连续失败达到阈值后进入冷却，避免外部源异常拖垮 UI。
- 缺失股本的 A 股会按需补东方财富 finance 数据，用于动态计算市值。
- `GlobalStore` 对 quote 逐股深合并，避免一次不完整 payload 覆盖已有字段。
- 表格模型优先使用增量刷新，减少全表 reset 对 UI 的影响。

## 7. 数据与策略链路

### 7.1 本地数据与实时数据

`TdxDataProvider` 位于 `infra/market_data/tdx_data_provider.py`，同时组合历史数据、复权处理、实时行情和运行时缓存能力：

- `LocalHistoryProvider` 读取本地通达信日线。
- `AdjustmentService` 处理复权和股本变更相关逻辑。
- `RealtimeQuoteProvider` 管理实时请求、冷却、重连、防线程异常和运行态统计。
- `MarketDataWarehouse` 负责 Parquet/SQLite-first 的全市场和单票读取、写入登记、schema 与 manifest 校验。
- `WarehouseManifest` 在 `data/vcp_hunter.db` 中维护 `market_data_manifest`，只记录 dataset、trade_date、schema_version、source、parquet_path、symbol_count、row_count、updated_at、data_status、error 等状态字段，不保存大规模行情明细。
- `provider_ports.py` 给测试和服务层提供更窄的数据端口视图。
- `vcp/data_provider.py` 是兼容入口，真实 provider 子服务已经迁入 `infra/market_data`。

本地历史数据读取顺序为：

1. `load_cache_from_disk()` 优先通过 `MarketDataWarehouse.read_full_market()` 读取带 SQLite manifest 的 `data/Cache/parquet/market_data.parquet`。
2. 如果 manifest 缺失但旧 Parquet 可读，会兼容加载旧 `vcp.polars_engine.load_cache_parquet()`，并补登记 manifest，完成平滑迁移。
3. `TdxDataProvider.get_data(code)` 优先读内存 `cache_data`，其次读仓库单票 Parquet，最后回退 `vipdoc`。
4. `sync_market_data()` 和 F5 重新读取 vipdoc 后仍写入 Parquet；`save_cache_parquet()` 成功后同步更新 SQLite manifest。
5. 缺 manifest、缺 Parquet、schema 不兼容、manifest 与 Parquet 行数/股票数不匹配时，仓库返回明确状态并走 fallback，不让 UI 崩溃。

### 7.2 VCP 与 RPS

扫描引擎对外入口是 `app.services.scan_engine_facade.VCPEngine`：

- `IndicatorService` 计算基础指标。
- `RpsService` 维护 RPS 矩阵和预计算结果。
- `VcpScannerService` 评估 VCP 条件。
- `BreakoutMonitorService` 预计算待突破池并执行盘中快速判断。
- `vcp/engine.py` 当前是兼容别名，内部已委托到应用层 facade。

### 7.3 情报源和个股上下文

工作区内不同来源最终会汇总为个股上下文：

- 北美战报提供催化剂和细分板块。
- AI 产业链提供产业链环节。
- 大宗交易提供席位和金额信号。
- 业绩异动提供业绩增速信号。
- 基金持仓提供主体、资金属性、季度和持仓变化。
- 龙虎榜提供最近上榜、净买、机构和外资信号。
- VCP 扫描提供评分、RPS、距突破和触发日。

这些信号由 `StockContextService` 汇总成 `StockSignal`，供关注池雷达、综合候选、个股详情和 K 线摘要使用。

## 8. K 线打开链路

K 线请求通过应用层服务构造，避免主窗口直接拼装跨页数据：

```text
Tab emits ui_signal_hub.sig_show_kline_with_list
  -> MainWindowQT._on_show_kline_with_list()
  -> app.services.kline_open_service.build_kline_open_request()
  -> KLineWindowManager.open_chart()
  -> ui/kline_window_qt.py
```

`build_kline_open_request()` 会合并：

- 当前代码、名称和列表上下文
- 来源 Tab 的 key/index
- 当前行 payload
- 扫描结果中的补充字段

K 线窗口由 `KLineWindowManager` 管理生命周期，图表 payload 位于 `ui/kline_chart_payload.py`，亚洲市场 K 线补充逻辑位于 `ui/kline_window_asian.py`。WebEngine 相关问题应优先通过 `kline_webengine_preflight.py`、`scripts/kline_webengine_lifecycle_smoke.py` 和 runtime health 报告定位。

## 9. F5 与缓存刷新链路

全局 F5 不是单纯刷新当前页面，而是一次分层的缓存和快照更新：

```text
MainWindowQT._action_refresh_f5()
  -> ui.main_window_runtime.start_f5_precompute()
  -> RPSPrecomputer.run_f5_pipeline()
  -> MainWindowQT._on_f5_done()
  -> finish_f5_reload()
  -> CentralQuotesService.refresh_after_cache_reload()
  -> WorkspaceTableService.refresh_all_tabs_after_f5_scheduled()
  -> domain_events.sig_cache_reload_completed
  -> WorkspaceFacade.refresh_information_sources_after_f5()
```

关键约束：

- F5 后先刷新核心行情快照，再分帧回灌当前/可见表格，避免一次性阻塞 UI。
- 阶段 1 优先尝试从 Parquet/SQLite 本地仓库断点续算；全量重读时仍从 `vipdoc` 生产日线数据，然后写回 Parquet 并更新 SQLite manifest。
- 支持 `_on_cache_reload_completed` 或 `_schedule_context_refresh` 的 Tab 可以在缓存完成信号后自己回读本地最新快照。
- 情报源页面的数据刷新由 `WorkspaceFacade.refresh_information_sources_after_f5()` 汇总触发。
- 后台任务 ID 使用 `WINDOW_F5_PRECOMPUTE`，不要在 UI 层硬编码字符串。

## 10. 事件、任务、服务开关与配置

### 10.1 事件

领域/应用事件总线位于 `domains/runtime/domain_events.py`，承载缓存、行情、刷新、网络状态、数据源更新等事件。UI 导航类请求通过 `infra/events/ui_signal_hub.py` 处理，避免领域事件总线混入 UI 导航语义。

### 10.2 后台任务

后台任务统一使用 `core/background_job_runner.py` 和 `infra/tasks/typed_task_registry.py`。任务 ID 不应在 UI 层硬编码字符串，应通过 `task_registry` 或已注册的 `TaskKey` 取得。

当前已注册的关键任务包括：

- `STARTUP_DEFERRED_LOAD`
- `STARTUP_ASIAN_DATA_SYNC`
- `STARTUP_SMART`
- `NETWORK_GO_ONLINE`
- `NETWORK_FORCE_RECONNECT`
- `WINDOW_F5_PRECOMPUTE`
- `CENTRAL_QUOTES_POLL`
- `SHARED_MARKET_CAPS`

### 10.3 服务开关

服务开关位于 `infra/features/service_toggle_registry.py`，支持 `VCP_TOGGLE_...` 环境变量覆盖。当前关键开关包括：

- `central_quotes_service`
- `silent_asian_sync`
- `daily_global_earnings_calendar_sync`
- `workspace_auto_rt_monitor`
- `startup_history_cache_load`

新增可选运行能力应先进入服务开关，而不是在 UI 或启动编排器里散落布尔变量。

### 10.4 配置

配置入口分两类：

- 应用配置：`core/app_config.py` 以及 `infra/settings/*`
- 表格状态：`TableViewStateStore` 管理列宽、排序、可视状态等持久化

新增直接使用 QSettings 的代码应优先放入 `infra/settings`，不要分散在 UI 组件中。

## 11. 运行时健康与稳定性诊断

当前仓库已经把临时性能探针逐步产品化：

- `infra/diagnostics/runtime_health.py` 采集后台任务、Timer、事件订阅、线程、WebEngine 子进程、行情请求、F5 缓存、本地市场数据源状态和关键数据血缘。
- `infra/diagnostics/ui_stall_probe.py` 记录 UI 事件循环和关键 UI 方法的卡顿跨度；UI 代码通过 `app/services/ui_diagnostics_service.py` 引入，避免直接跨到 `infra`。
- `app/services/runtime_health_service.py` 给 UI 暴露采集和导出入口。
- `ui/components/runtime_health_dialog.py` 在系统菜单中提供运行时健康面板。
- `scripts/runtime_health_stability_suite.py` 可以执行短模式或 30/60 分钟 soak。
- `scripts/perf_budget_check.py` 可以读取 runtime health、K 线生命周期和历史性能探针报告并输出预算结果。

`TabDataLineageService` 是后续稳定性治理的重要接口。新数据页应尽量提供 `get_data_lineage()`，说明数据来源、缓存、是否联网、是否降级、更新时间、行数和签名。

runtime health 的 `market_data` 段会展示当前 active layer，例如 `memory_cache`、`parquet_sqlite_warehouse`、`legacy_parquet_bootstrap`、`vipdoc_fallback_ready` 或 `vipdoc_fallback`，并包含 trade_date、row_count、symbol_count、是否降级以及降级原因。

## 12. 本地数据和运行时产物

运行时会生成或维护：

- `data/Cache/`：RPS、盘中监控、亚洲市场、财务/股本、全球财报日历等缓存
- `data/Cache/parquet/market_data.parquet`：全市场历史日线明细；`meta.parquet` 保留兼容元数据
- `data/vcp_hunter.db`：SQLite 数据，例如交易日、基金持仓、扫描缓存，以及 `market_data_manifest` 仓库 manifest
- `data/logs/`：应用日志
- `data/crash_report.log`：`faulthandler` 崩溃日志
- `tmp/runtime_health_*`：运行时健康报告和稳定性采样
- `tmp/perf_*`：性能探针输出

这些内容是运行时产物，不应作为源码架构的一部分来维护。调试缓存问题时可以读取它们，但不要把生成文件纳入正常代码变更。

## 13. 测试与质量门槛

常用命令：

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_architecture_boundaries.py -q
.\.venv\Scripts\python.exe scripts/check_utf8.py core ui vcp tests scripts app infra docs .github
.\.venv\Scripts\python.exe -m pytest tests/test_runtime_health.py tests/test_tab_data_lineage_service.py -q
.\.venv\Scripts\python.exe -m pytest tests/test_application_bootstrap.py tests/test_service_toggle_registry.py -q
```

更适合日常局部验证的测试入口：

- 架构边界：`tests/test_architecture_boundaries.py`
- 启动装配：`tests/test_application_bootstrap.py`、`tests/test_startup_orchestrator.py`
- 行情快照：`tests/test_quote_snapshot.py`、`tests/test_global_store_quote_merge.py`
- 中央报价：`tests/test_central_quotes_worker.py`、`tests/test_central_quotes_finance.py`
- K 线上下文：`tests/test_kline_open_service.py`、`tests/test_kline_summary_cards.py`
- 工作区聚合：`tests/test_workspace_quote_codes.py`、`tests/test_stock_candidate_tab.py`
- 运行时健康：`tests/test_runtime_health.py`、`tests/test_perf_probe_scripts.py`、`tests/test_perf_budget_check.py`
- 本地行情仓库：`tests/test_market_data_warehouse_manifest.py`、`tests/test_market_data_warehouse.py`

PyQt 相关测试优先使用 offscreen smoke test。WebEngine 生命周期检查需要原生 Qt/可视桌面时，应使用专门脚本，不要把不稳定窗口自动化混入普通单元测试。

CI 当前不再引用已删除的 `ui/workspaces/watchlist_radar_service.py`。后续审计时仍应把 `.github/workflows/ci.yml` 纳入边界核对，避免 Ruff、UTF-8、架构回归和运行时健康门禁与真实源码漂移。

## 14. 新增能力的维护规则

新增 Tab、数据源或跨页联动时，优先遵守以下规则：

1. 新 Tab 通过 `ClassicWorkspace._tab_specs` 注册，并声明稳定的 `key`、`title`、`group`。
2. 表格型 Tab 尽量继承或复用 `BaseStockTab` 的行情刷新、K 线跳转、右键菜单和工具栏基线。
3. 新的盘中字段接入 `domains/quotes/*`，不要在每个 Tab 单独解析 quote payload。
4. 需要跨页共享的运行时状态优先进入 `GlobalStore` 或通过 `StockContextService` 聚合。
5. UI 层不要直接 import `domains`、`infra`、`vcp` 的具体实现，应通过 `app.services.*` 或能力协议访问。
6. 后台任务使用 `TaskKey` 注册，不要在 UI 层散落字符串 task id。
7. 涉及市场时间、交易日、报价刷新窗口时统一走 `MarketCalendar`。
8. 涉及 Windows 自动化、外部终端、进程调用时放在 `infra/` 或 `app/use_cases/`。
9. 新增可开关的运行能力先注册到 `service_toggle_registry`。
10. 新数据页尽量提供 `get_data_lineage()`，便于运行时健康面板和稳定性 suite 追踪。

## 15. 当前架构健康结论

当前顶层架构是可继续演进的模块化单体，不是需要推倒重写的失控结构。支撑这个判断的事实是：

- `app / domains / infra / ui / vcp/core` 分层已经真实存在。
- UI 直接跨层依赖由 `tests/test_architecture_boundaries.py` 约束。
- 启动、工作区、中央行情、F5、K 线、任务和运行时健康都有明确控制点。
- 主要性能治理已经落到共享路径：懒加载工作区、中央行情广播、表格增量刷新、运行时健康采样和预算检查。

但当前也确实需要第二阶段架构升级，重点不是换技术栈，而是收窄边界和拆分热点：

- `app/services/ui_runtime_service.py` 已退为兼容门面；后续新增 UI 跨层依赖应继续落到更窄的 `ui_*_service.py`。
- `domains/global_earnings_calendar/service.py`、`ui/tabs/fund_holdings_tab.py`、`ui/kline_chart_payload.py`、`vcp/fetchers/asian_kline_fetcher.py`、`ui/tabs/asian_market_workers.py` 等大文件应继续按数据获取、转换、缓存、展示拆分；当前第一刀已先抽出财报日历模型、基金持仓过滤代理、K 线摘要转换和亚洲 K 线缓存写入。
- UI 卡顿探针真实实现位于 `infra/diagnostics/ui_stall_probe.py`，UI 侧通过 `app/services/ui_diagnostics_service.py` 访问；`core/ui_stall_probe.py` 仅保留历史导入兼容门面，新代码不应继续从 `core` 引入该诊断能力。
- `vcp/` 和 `core/` 仍有兼容入口。新增真实实现不应继续落入这些目录。
- 数据后端应继续向 Parquet/SQLite-first 读模型演进，`vipdoc` 保留为本地生产者和兜底源。

推荐升级路线：继续保留桌面模块化单体，做第二阶段架构收口。不要改成微服务，不要迁成 Web 服务，也不要大规模重写 UI。

## 16. 已知边界

- 项目以 Windows 桌面为主，Linux/macOS 未按完整可运行目标维护。
- 核心历史数据依赖本地通达信 `vipdoc` 目录。
- 外部源可用性会影响亚洲市场、北美战报、财务补充、全球财报日历和盘中行情质量。
- `core/`、`vcp/`、`earnings/` 中仍有历史兼容入口，新增真实实现应优先落到 `app/`、`domains/` 或 `infra/`。
- README 适合放快速导览；更细的技术说明以本文档和 `docs/module-owners.md` 为准。
