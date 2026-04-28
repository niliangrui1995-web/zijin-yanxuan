# 紫金研选技术架构文档

本文档基于 2026-04-29 的当前源码结构整理，目标读者是后续维护者、二次开发者和需要理解系统边界的技术协作者。本文不复述每个文件的实现细节，而是说明系统如何启动、如何分层、核心数据链路如何流转，以及新增能力时应该遵守哪些边界。

## 1. 系统定位

紫金研选是 Windows 优先的 PyQt6 桌面量化终端，核心围绕 A 股 VCP 扫描、盘中监控、关注池、情报源联动、K 线复盘和多市场辅助观察展开。

系统不是 Web 服务，也没有后端 API 服务器。它的主要运行形态是单机桌面应用：

- 入口：`vcp_hunter_qt.pyw`
- UI 框架：PyQt6、PyQt6-WebEngine、QSS
- 本地历史数据：通达信 `vipdoc`
- 盘中行情：东方财富 HTTP，异常时回退新浪批量报价
- 海外/亚洲辅助数据：AkShare、yfinance、`curl_cffi`
- 本地持久化：`data/Cache/`、`data/vcp_hunter.db`、QSettings

## 2. 总体分层

当前代码按“应用编排、领域服务、基础设施、界面、兼容门面”分层维护：

| 层级 | 目录 | 主要职责 |
| --- | --- | --- |
| 应用编排层 | `app/` | 给 UI 提供稳定入口，封装数据提供者、扫描引擎、K 线打开请求、运行时服务导出 |
| 领域服务层 | `domains/` | 承载 quotes、scan、earnings、fund_holdings、watchlist、market_calendar 等稳定领域能力 |
| 基础设施层 | `infra/` | 承载 market_data、settings、storage、tasks、navigation、events 等外部边界适配 |
| UI 层 | `ui/` | PyQt 主窗口、工作区、Tab、组件、模型、样式、worker 和窗口交互 |
| 兼容门面 | `vcp/`、`core/`、`earnings/` | 保留历史入口，逐步委托到 `app/`、`domains/`、`infra/` 的真实实现 |

当前架构治理的关键约束是：UI 不直接依赖 `domains/`、`infra/`、`vcp/` 的具体实现；UI 通过 `app.services.ui_runtime_service` 和工作区能力协议访问跨层能力。对应约束由 `tests/test_architecture_boundaries.py` 回归保护。

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

- `vcp_hunter_qt.pyw` 负责虚拟环境重启、崩溃日志、`QApplication` 初始化、单实例互斥锁、主题和启动页。
- `MainWindowQT` 是主窗口外壳，负责无边框窗口、标题栏、快捷键、状态栏、工作区挂载和全局事件接线。
- `ApplicationBootstrap` 负责把主窗口和工作区服务装配起来，并安装中央行情广播服务。
- `create_data_provider(offline=True)` 默认离线优先，冷启动时先保证本地缓存和 UI 可用。
- `StartupOrchestrator` 延迟恢复历史缓存、RPS 缓存、实时缓存，并异步检测网络；网络可用后切换在线模式并触发后续刷新。

## 4. 主工作区与 Tab 装配

当前唯一工作区是 `ClassicWorkspace`，代码位置为 `ui/workspaces/classic_workspace.py`。它通过 `_tab_specs` 装配 12 个主 Tab：

| key | 页面 | 模块 | 分组 |
| --- | --- | --- | --- |
| `watchlist` | 关注池 | `ui/tabs/watchlist_tab.py` | 主工作台 |
| `asian_market` | 亚洲寡头 | `ui/tabs/asian_market_tab.py` | 主工作台 |
| `na_daily` | 北美战报 | `ui/tabs/na_daily_tab.py` | 主工作台 |
| `stock_candidates` | 综合候选 | `ui/tabs/stock_candidate_tab.py` | 主工作台 |
| `ai_industry_chain` | AI产业链 | `ui/tabs/ai_industry_chain_tab.py` | 主工作台 |
| `lhb` | 龙虎榜 | `ui/tabs/lhb_tab.py` | 主工作台 |
| `rt_monitor` | 盘中监控 | `ui/tabs/rt_monitor_tab.py` | 主工作台 |
| `scan` | VCP扫描 | `ui/tabs/scan_tab.py` | 情报源 |
| `foreign_block` | 大宗交易 | `ui/tabs/foreign_block_trade_tab.py` | 情报源 |
| `earnings` | 业绩异动 | `ui/tabs/earnings_tab.py` | 情报源 |
| `fund_holdings` | 基金持仓 | `ui/tabs/fund_holdings_tab.py` | 情报源 |
| `system_log` | 系统日志 | `ui/tabs/log_tab.py` | 系统 |

跨 Tab 编排不直接访问具体 Tab 的私有字段，而是通过 `WorkspaceFacade` 和能力协议聚合：

- `ui/workspaces/tab_capabilities.py` 定义公开能力协议。
- `WorkspaceTableService` 聚合表格和 F5 后快照刷新能力。
- `QuoteUniverseService` 聚合需要订阅实时行情的 A 股代码。
- `StockContextService` 汇总扫描、战报、AI 产业链、大宗、业绩、基金持仓、龙虎榜等来源的个股信号。
- `WorkspaceNavigationService` 处理跨页选中和定位。

## 5. 实时行情链路

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

- `CentralQuotesService` 30 秒轮询一次，但同一时刻只允许一个抓取任务在飞。
- 非报价刷新时段不会持续拉取盘中行情，只在必要时构建一次盘后快照。
- 连续失败达到阈值后进入冷却，避免 pytdx 或外部源异常拖垮 UI。
- 缺失股本的 A 股会按需补东方财富 finance 数据，用于动态计算市值。
- `GlobalStore` 对 quote 逐股深合并，避免一次不完整 payload 覆盖已有字段。

## 6. 数据与策略链路

### 6.1 历史数据与实时数据

`TdxDataProvider` 位于 `infra/market_data/tdx_data_provider.py`，同时组合历史数据、复权处理、实时行情和运行时缓存能力：

- `LocalHistoryProvider` 读取本地通达信日线。
- `AdjustmentService` 处理复权和股本变更相关逻辑。
- `RealtimeQuoteProvider` 管理实时请求、冷却、重连、防线程异常和运行态统计。
- `vcp/data_provider.py` 是兼容入口，真实 provider 已迁入 `infra/market_data`。

### 6.2 VCP 与 RPS

扫描引擎对外入口是 `app.services.scan_engine_facade.VCPEngine`：

- `IndicatorService` 计算基础指标。
- `RpsService` 维护 RPS 矩阵和预计算结果。
- `VcpScannerService` 评估 VCP 条件。
- `BreakoutMonitorService` 预计算待突破池并执行盘中快速判断。

`vcp/engine.py` 当前只是兼容别名，内部已委托到应用层 facade。

### 6.3 情报源和个股上下文

工作区内不同来源最终会汇总为个股上下文：

- 北美战报提供催化剂和细分板块。
- AI 产业链提供产业链环节。
- 大宗交易提供席位和金额信号。
- 业绩异动提供业绩增速信号。
- 基金持仓提供主体、资金属性、季度和持仓变化。
- 龙虎榜提供最近上榜、净买、机构和外资信号。
- VCP 扫描提供评分、RPS、距突破和触发日。

这些信号由 `StockContextService` 汇总成 `StockSignal`，供关注池雷达、综合候选、个股详情和 K 线摘要使用。

## 7. K 线打开链路

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

这样 K 线窗口可以展示来源上下文、摘要卡片和前后切换，而不需要知道各 Tab 的内部表格结构。

## 8. 事件、任务与配置

### 8.1 事件

领域/应用事件总线位于 `domains/runtime/domain_events.py`，承载缓存、行情、刷新、网络状态、数据源更新等事件。UI 导航类请求则通过 `infra/events/ui_signal_hub.py` 处理，避免领域事件总线混入 UI 导航语义。

### 8.2 后台任务

后台任务统一使用 `core/background_job_runner.py` 和 `infra/tasks/typed_task_registry.py`。任务 ID 不应在 UI 层硬编码字符串，应通过 `task_registry` 或已注册的 `TaskKey` 取得。

已注册的关键任务包括：

- `STARTUP_DEFERRED_LOAD`
- `STARTUP_ASIAN_DATA_SYNC`
- `STARTUP_SMART`
- `WINDOW_F5_PRECOMPUTE`
- `CENTRAL_QUOTES_POLL`
- `SHARED_MARKET_CAPS`

### 8.3 配置

配置入口分两类：

- 应用配置：`core/app_config.py` 以及 `infra/settings/*`
- 表格状态：`TableViewStateStore` 管理列宽、排序、可视状态等持久化

新增直接使用 QSettings 的代码应优先放入 `infra/settings`，不要分散在 UI 组件中。

## 9. 本地数据和运行时产物

运行时会生成或维护：

- `data/Cache/`：RPS、盘中监控、亚洲市场、财务/股本等缓存
- `data/vcp_hunter.db`：SQLite 数据，例如交易日、基金持仓等持久化数据
- `data/logs/`：应用日志
- `data/crash_report.log`：`faulthandler` 崩溃日志

这些内容是运行时产物，不应作为源码架构的一部分来维护。调试缓存问题时可以读取它们，但不要把生成文件纳入正常代码变更。

## 10. 测试与质量门槛

常用命令：

```powershell
pytest -q
ruff check .
ruff format .
python scripts/check_utf8.py
```

更适合日常局部验证的测试入口：

- 架构边界：`pytest tests/test_architecture_boundaries.py -q`
- 行情快照：`pytest tests/test_quote_snapshot.py tests/test_global_store_quote_merge.py -q`
- 中央报价：`pytest tests/test_central_quotes_worker.py tests/test_central_quotes_finance.py -q`
- K 线上下文：`pytest tests/test_kline_open_service.py tests/test_kline_summary_cards.py -q`
- 工作区聚合：`pytest tests/test_workspace_quote_codes.py tests/test_stock_candidate_tab.py -q`

PyQt 相关测试优先使用 offscreen smoke test，避免把窗口显示问题和业务逻辑问题混在一起。

## 11. 新增能力的维护规则

新增 Tab、数据源或跨页联动时，优先遵守以下规则：

1. 新 Tab 尽量通过 `ClassicWorkspace._tab_specs` 注册，并声明稳定的 `key`、`title`、`group`。
2. 表格型 Tab 尽量继承或复用 `BaseStockTab` 的行情刷新、K 线跳转、右键菜单和工具栏基线。
3. 新的盘中字段尽量接入 `domains/quotes/*`，不要在每个 Tab 单独解析 quote payload。
4. 需要跨页共享的运行时状态优先进入 `GlobalStore` 或通过 `StockContextService` 聚合。
5. UI 层不要直接 import `domains`、`infra`、`vcp` 的具体实现，应通过 `app.services.*` 或能力协议访问。
6. 后台任务使用 `TaskKey` 注册，不要在 UI 层散落字符串 task id。
7. 涉及市场时间、交易日、报价刷新窗口时统一走 `MarketCalendar`。
8. 涉及 Windows 自动化、外部终端、进程调用时放在 `infra/` 或 `app/use_cases/`，不要直接放入 Tab。

## 12. 已知边界

- 项目以 Windows 桌面为主，Linux/macOS 未按完整可运行目标维护。
- 核心历史数据依赖本地通达信 `vipdoc` 目录。
- 外部源可用性会影响亚洲市场、北美战报、财务补充和盘中行情质量。
- `core/`、`vcp/`、`earnings/` 中仍有历史兼容入口，新增真实实现应优先落到 `app/`、`domains/` 或 `infra/`。
- README 中适合放快速导览；更细的技术说明以本文档和 `docs/module-owners.md` 为准。
