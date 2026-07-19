# 紫金研选技术架构文档

最后校验：2026-07-19（Asia/Shanghai 工作区环境）

本文档按当前源码重新整理，目标读者是后续维护者、二次开发者和需要判断架构演进方向的技术协作者。它不复述每个文件的实现细节，而是说明系统定位、启动链路、模块边界、关键数据流、质量护栏和当前架构升级判断。

## 1. 系统定位

紫金研选是 Windows 优先的 PyQt6 桌面量化终端，核心围绕 A 股 VCP 扫描、关注池、情报源联动、K 线复盘和多市场辅助观察展开。

它不是 Web 服务，也没有后端 API 服务器。当前主要运行形态是单机桌面应用：

- 入口：`vcp_hunter_qt.pyw`
- UI 框架：PyQt6、PyQt6-WebEngine、自研动态 QSS 主题系统
- 本地历史数据：Parquet/SQLite-first 本地仓库，通达信 `vipdoc` 保留为生产源和 fallback
- 盘中行情：东方财富 HTTP，异常时依次回退新浪、腾讯批量报价
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

当前架构治理的关键约束是：UI 不直接依赖 `domains/`、`infra/`、`vcp/` 的具体实现；UI 通过 `app.services.ui_*` 窄服务、其他明确 `app.services.*` 入口和工作区能力协议访问跨层能力。原 `app.services.ui_runtime_service` 宽泛兼容桶在确认仓内生产调用为 0 后已删除。对应约束由 `tests/test_architecture_boundaries.py` 回归保护。

## 3. 启动链路

主启动路径如下：

```text
vcp_hunter_qt.pyw
  -> MainWindowQT
     -> ApplicationBootstrap
        -> ClassicWorkspace
     -> 真实首帧
        -> post-paint stages
           -> owner 后台创建 TdxDataProvider 并预载 StartupOrchestrator 模块
           -> GUI 线程挂接 provider / 创建 VCPEngine / 安装 CentralQuotesService
           -> 稳定 Tab 恢复 / F5 retention / 自动刷新
           -> StartupOrchestrator.schedule_startup()
```

关键行为：

- `vcp_hunter_qt.pyw` 负责项目虚拟环境重启、崩溃日志、Qt WebEngine 运行时配置、`QApplication` 初始化、单实例互斥锁、主题和启动页。
- `QApplication` 创建后立即安装应用级有界日志缓冲；系统日志 Tab 延迟创建也能回放此前日志。
- 启动页显示后，入口在主线程按 `pyarrow -> pyarrow.compute -> pandas -> polars` 顺序幂等初始化原生数据运行时；线程池首次装载会被明确拒绝，避免原生 DLL 在线程池线程初始化。
- `MainWindowQT` 是主窗口外壳，负责无边框窗口、标题栏、快捷键、状态栏、工作区挂载、全局事件接线、F5 入口和运行时健康入口。
- `ApplicationBootstrap` 负责装配工作区，并通过 `service_toggle_registry` 决定是否安装中央行情广播服务。
- `create_data_provider(offline=True)` 默认离线优先，并在真实首帧后的 owner 级后台任务中创建；结果只在 GUI 线程挂接到主窗口和工作区。
- 11 个页面先挂载轻量占位；provider、扫描引擎和启动编排器就绪后，才按稳定 Tab key 创建当前真实页面。
- post-paint stage 每次只推进一个依赖阶段并把下一阶段交回事件循环；随后执行 Tab 恢复、F5 运行产物保留清理、自动刷新初始化和 `StartupOrchestrator` 调度。自动刷新可以先初始化，但 staged eager preload 未完全 settled 时不提交任务；首次 settled 后再保留 60 秒稳定窗口。
- 各 post-paint stage 独立记录完成状态并按上限退避重试；依赖尚未就绪时，Tab、F5 和网络入口安全等待或提示，不会捕获空 provider/engine。
- `StartupOrchestrator` 默认只恢复轻量 RPS 缓存，历史行情按需读取；行情网络探测共享 2 秒总截止时间。16:30 前的启动期亚洲缓存同步还要求亚洲页当前可见，隐藏或仅被 staged eager preload 构造时直接跳过；16:30 后由 `AutoRefreshScheduler` 唯一调度。

### 3.1 退出与异常隔离

`closeEvent()` 先设置 closing/cancel 标记并停止 post-paint Timer、断开主窗口运行时信号，再按 F5 子进程、K 线窗口、owner 后台任务、交易日历、启动编排、自动刷新、亚洲/业绩服务、中央行情、工作区和全局 `TaskManager` 的顺序有界关闭。工作区 shutdown 幂等清除待加载 Tab、断开事件、关闭详情窗口，并停止 StockContext 生命周期和所有已加载 Tab；随后保存 UI 状态、重置全局行情快照并停止 watchdog。退出门禁要求主窗口在 5 秒内结束，后台任务、QThreadPool、watchdog 和 WebEngine 子进程归零；F5 还必须提供只读磁盘回执，证明 active bundle 完整、generation 不超过 active + previous、无未完成/待激活/无效 job、无临时文件且终态 job 有界。

入口在日志缓冲之后安装 `UiExceptionHookHandle`。Qt 回调中未捕获的 Python 异常会写入 stderr 与 `data/crash_report.log`，hook 正常返回，避免 PyQt 调用 Qt native fast-fail；owner-bound 后台任务的 UI 回调还会在自身交付边界捕获并记录异常。该机制负责留痕和阻断异常逃逸，不承诺发生异常后的业务状态仍然完整；原生访问冲突等故障仍由 `faulthandler` 负责。

## 4. 应用层服务入口

`app/services/` 是 UI 与领域/基础设施之间的稳定入口。当前主要服务包括：

| 服务 | 主要职责 |
| --- | --- |
| `runtime_services.py` | 管理主线程原生数据运行时边界，创建 `TdxDataProvider`、`StartupOrchestrator`，并封装本地股本快照读取 |
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
| `earnings_refresh_process_service.py` | 封装业绩自动刷新子进程参数、输出协议、deadline 与取消检查 |
| `ai_industry_chain_period_return_service.py` | 计算 AI 产业链 5/10/20 日派生涨幅；优先消费有效收盘尾值，窄读取失败时保持旧批量路径兼容 |
| `stock_context_*_service.py` | 给 UI 暴露纯数据快照、按股票窄查询、信号构建和无 QWidget 的候选/雷达计算 |
| `f5_job_contract.py` / `f5_job_runner.py` / `f5_snapshot_installer.py` | 定义 F5 任务协议、独立进程执行、完整 bundle 校验与父进程激活 |
| `ui_fund_holdings_service.py` / `ui_watchlist_service.py` / `ui_navigation_service.py` | 给 UI 暴露基金持仓、关注池和外部终端跳转入口 |
| 原 `ui_runtime_service.py` | 已在确认仓内生产调用为 0 后删除；架构测试继续禁止 UI 和脚本导入该宽泛桶 |

所有调用均按能力导入更窄的 `ui_*_service.py`。删除前的全仓调用图只发现测试自检，没有生产或脚本消费者，因此不再保留无效转发层。

## 5. 主工作区与 Tab 装配

当前唯一工作区 `ClassicWorkspace` 装配 11 个主 Tab，代码位置为 `ui/workspaces/classic_workspace.py`。不可变 `ui/workspaces/tab_registry.py` 是这些 Tab 的唯一声明源，工作区只把声明转换为运行时 spec：

| key | 页面 | 模块 | 分组 |
| --- | --- | --- | --- |
| `watchlist` | 关注池 | `ui/tabs/watchlist_tab.py` | 主工作台 |
| `lhb` | 龙虎榜 | `ui/tabs/lhb_tab.py` | 主工作台 |
| `asian_market` | 亚洲寡头 | `ui/tabs/asian_market_tab.py` | 主工作台 |
| `na_daily` | 北美战报 | `ui/tabs/na_daily_tab.py` | 主工作台 |
| `stock_candidates` | 综合候选 | `ui/tabs/stock_candidate_tab.py` | 主工作台 |
| `ai_industry_chain` | AI产业链 | `ui/tabs/ai_industry_chain_tab.py` | 情报源 |
| `scan` | VCP扫描 | `ui/tabs/scan_tab.py` | 情报源 |
| `foreign_block` | 大宗交易 | `ui/tabs/foreign_block_trade_tab.py` | 情报源 |
| `earnings` | 业绩异动 | `ui/tabs/earnings_tab.py` | 情报源 |
| `fund_holdings` | 基金持仓 | `ui/tabs/fund_holdings_tab.py` | 情报源 |
| `system_log` | 系统日志 | `ui/tabs/log_tab.py` | 系统 |

当前工作区的启动策略是：

- 所有 Tab 启动时先挂轻量页面壳，启动阶段不创建真实业务 Tab；轻量壳只隔离首屏构造成本，产品语义明确为 staged eager preload，而不是等待用户点击的数据按需加载。
- 首屏、provider、扫描引擎和当前真实页面就绪后，工作区自动按 registry 的 `startup_order` 单步创建并预载全部 11 个真实 Tab；后台调度不改变当前页。
- 预载顺序是 `watchlist → system_log → ai_industry_chain → na_daily → scan → foreign_block → earnings → fund_holdings → lhb → asian_market → stock_candidates`。系统日志提前用于观测后续步骤；综合候选最后消费所有上游 StockContext 信号。
- 每个 Tab 通过 `prime_background_load()` 与 `is_background_preload_complete()` 构成完成驱动的串行契约。当前步骤完成后才继续下一步；用户点击未来 Tab 时，占位页可以立即选中，目标进入当前 active key 之后的下一串行槽，构造、本地读取和 prime 仍由同一协调步骤各执行一次。
- `BackgroundTabPreloadCoordinator` 同时最多持有一个 active key；单页失败会记录诊断并继续。步骤超时先请求协作取消，取消没有物理结算时保持 cancellation-blocked，禁止启动下一步骤，settled 后由协调器自动恢复。运行时报告必须证明计划、开始、完成顺序一致、并发上限 1、11 页全部 loaded、active/cancelling key 为空、队列清空、Timer inactive、`active_step_count=0`、全部 shutdown cancellation receipts 已结算且无失败/超时。
- 被用户提权的预载即使失败或超时，只要真实 widget 已构造且仍为当前页，也会在保存失败诊断后只提升激活一次，再继续队列；构造本身失败时保留占位错误，不伪造 widget 或激活事件。
- 首开队列只读取本地文件、SQLite、缓存快照和已有内存行情，并计算必要派生字段；大宗 30 日联网抓取、基金全量同步、龙虎榜回补、亚洲远程刷新和全市场扫描不在队列中。
- registry 的 `network_capable` 只声明页面存在远端能力；各真实 Tab 的 `_runtime_network_triggered` 是实例级单向闩锁，只有提交远端请求/worker 时才置为 `true`。运行健康在唯一 `after_background_preload` 样本中要求精确 10 数据 Tab + `system_log` 排除分区、每个数据 Tab 恰好一行且 loaded；缺失/重复/额外/交叉分区、非严格 bool、`lineage_error` 或任一实际联网均 fail-closed。启用生产启动编排时还必须证明全局 `asian_data_sync_bg` 没有出现在隐藏预载窗口的 observed task IDs 中。
- 上次页面以稳定 key 保存，旧 index 仅兼容历史设置；首帧后等待运行依赖就绪即恢复页面，不再依赖固定延时。
- registry 同时声明视觉/启动顺序、动态导入、构造 profile、构造默认值、非交互默认值、首开延迟参数、行情订阅、F5 快照、post-F5、健康探针和数据血缘策略；主工作区只解释这些声明，不再按 Tab key 维护构造或延迟分支，视觉分组也不再隐式决定业务行为。

跨 Tab 编排不直接访问具体 Tab 的私有字段，而是通过 `WorkspaceFacade` 和能力协议聚合：

- `WorkspaceNavigationService`：分组导航、跨 Tab 定位和选中。
- `WorkspaceTableService`：表格集合、F5 后快照回灌和分帧刷新。
- `QuoteUniverseService`：聚合需要订阅实时行情的 A 股代码。
- `StockContextService`：管理基金持仓与龙虎榜有界异步快照的生命周期，并以锁内指针替换原子发布不可变 `StockContextSignalIndex`；GUI 适配器只在主线程复制已加载页面的公开数据。
- `StockContextQueryService` 与 `domains/stock_context/*`：在无 Qt 的纯数据快照上汇总扫描、战报、AI 产业链、大宗、业绩、基金持仓、龙虎榜等个股信号。
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
- 通用 SQLite `DataStore` 的真实实现位于 `infra/storage/data_store.py`；`core/data_store.py` 只保留历史导入别名。

本地历史数据读取顺序为：

1. `load_cache_from_disk()` 优先通过 `MarketDataWarehouse.read_full_market()` 读取 SQLite manifest 指向的实际 Parquet；普通仓库可指向固定兼容路径，已激活 F5 时指向对应 generation。
2. 如果 manifest 缺失但旧 Parquet 可读，会兼容加载旧 `vcp.polars_engine.load_cache_parquet()`，并补登记 manifest，完成平滑迁移。
3. `TdxDataProvider.get_data(code)` 优先读内存 `cache_data`，其次读仓库单票 Parquet，最后回退 `vipdoc`。
4. 周期涨幅等只需尾值的消费者调用 `get_close_tail_batch()`：先复用内存，再由仓库只投影 `_code/datetime/close` 并逐码保留有限尾值，最后才走单票本地或既有 `get_data_batch()` 回退。
5. 普通 `sync_market_data()` 通过 `save_cache_parquet()` 写入仓库并更新 SQLite manifest；F5 只写 `f5_generations/<run_id>/market.parquet`，完整校验后再用事务切换 active manifest。
6. 缺 manifest、缺 Parquet、schema 不兼容、manifest 与 Parquet 行数/股票数不匹配时，仓库返回明确状态并走 fallback，不让 UI 崩溃。

### 7.2 VCP 与 RPS

扫描引擎对外入口是 `app.services.scan_engine_facade.VCPEngine`：

- `IndicatorService` 计算基础指标。
- `RpsService` 维护 RPS 矩阵和预计算结果。
- `VcpScannerService` 评估 VCP 条件。
- `BreakoutMonitorService` 预计算待突破池并执行实时突破判断。
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

这些信号先由 GUI 线程捕获为普通数据快照，再统一由 `StockContextQueryService` 和 `domains/stock_context/signal_builders.py` 转换成 `StockSignal`，生产路径不再保留第二套 legacy builder。综合候选 worker 从同一输入快照同时生成候选行和不可变 `StockContextSignalIndex`；GUI 成功回调先原子发布索引，再提交候选表格。工作区提供该生产能力后，K 线只按当前代码做 O(1) 查询；未发布、读取异常或无匹配信号均 fail-closed 为空，不触发整工作区快照或 Widget 遍历。下游范围是显式策略，不是所有信号无差别广播：综合候选和通用个股详情消费扫描、AI 产业链、北美战报、大宗、业绩、基金持仓、龙虎榜 7 类信号；个股详情额外把 `target_codes` 收窄到当前代码；关注池雷达只消费 AI 产业链、北美战报、大宗、业绩、龙虎榜并叠加 RPS；K 线的 StockContext 补充只消费扫描与业绩。StockContext 默认仍携带 RPS；只有关注池 VCP/雷达任务显式以 `include_rps_bundle=False` 捕获，避免 GUI 线程复制并冻结整份 RPS，worker 随后通过 `load_active_rps_payload()` 在 F5 snapshot read boundary 内读取当前 active bundle。后台任务只接收不可变快照、代码和纯配置，不得持有 QWidget、Qt model 或 UI 绑定方法。AI 产业链与龙虎榜的纯规则分别位于 `domains/industry_chain/pool_service.py`、`domains/lhb/pool_service.py`，持久化分别由 `infra/storage/industry_chain_repository.py`、`infra/storage/lhb_pool_repository.py` 负责；龙虎榜 AkShare 调用由 `infra/market_data/lhb_provider.py` 承担，并经 `app.services.lhb_market_data_service` 暴露。大宗交易的 AkShare 子进程位于 `infra/market_data/foreign_block_provider.py`，分段、deadline 与字段兼容编排位于 `app.services.foreign_block_market_data_service`。UI 只保留任务生命周期和展示，不直接持有业务抓取进程。`core/ai_industry_chain_pool.py`、`core/lhb_pool_manager.py` 仅保留兼容别名。

## 8. K 线打开链路

K 线请求通过应用层服务构造，避免主窗口直接拼装跨页数据：

```text
Tab emits ui_signal_hub.sig_show_kline_with_list
  -> MainWindowQT._on_show_kline_with_list()
  -> app.services.kline_open_service.build_kline_open_request()
  -> KLineWindowManager.open_chart()
  -> ui/kline_window_qt.py
```

`build_kline_open_request()` 会一次性构造不可变 `KlineOpenContext`，合并：

- 当前代码、名称和紧凑导航列表（只保留 code/name/source）
- 来源 Tab 的 key/index
- 当前行 payload
- 扫描结果中的补充字段

扫描与业绩补充来自已发布 `StockContextSignalIndex` 的单代码查询。该索引与综合候选行由同一 worker 结果产生，并在表格提交前原子发布；因此标题、摘要卡、覆盖层和图表消费同一代不可变上下文，同时避免在每次 K 线打开时捕获完整工作区。

### 8.1 窗口、数据与渲染流水线

K 线窗口由 `KLineWindowManager` 管理，应用层数据与快照准备分别位于 `app/services/kline_data_service.py`、`app/services/kline_render_preparer.py`，owner 控制位于 `ui/kline_load_controller.py`，首开阶段位于 `ui/kline_window_stages.py`，渲染提交/确认位于 `ui/kline_window_rendering.py` 与 `ui/kline_render_bridge.py`，亚洲市场补充逻辑位于 `ui/kline_window_asian.py`。

```text
KlineOpenContext (immutable, compact navigation)
  -> KlineLoadController(window UUID, generation)
     -> static shell / Browser preparation
     -> KlineDataService(A-share or Asian, cache/source/degradation/cancellation)
        -> KlineRenderPreparer(background DataFrame/indicator/payload/JSON)
  -> applySnapshot() / ECharts setOption(lazyUpdate=false)
  -> chart.on('rendered')
  -> strict identity acknowledgement
  -> claim_frame()
  -> chart_ready
```

核心约束：

- 每个物理窗口在 `KlineLoadController` 中生成稳定 UUID；每次新租约或切股递增 generation。历史、亚洲回补、实时行情和 render 任务 ID 均使用 `kline:{window_id}:{generation}:{stage}`，回调还校验 code、snapshotVersion、当前 Browser epoch 与关闭状态。同股多窗口因此不会互相去重、取消或覆盖。
- `KlineDataService` 统一 A 股与亚洲历史读取，并显式返回 source、最新交易日、降级状态和错误原因；cancellation token 进入 provider/cache 调用。亚洲缓存服务按文件 mtime 复用 ticker 索引，避免每次打开全文件线性扫描。
- `KlineRenderPreparer` 在后台完成 DataFrame 规范化、最后 250 根截取、MA10/20/50/150/200、VOL-MA20、MACD/DIFF/DEA、现有业务覆盖层、payload 和唯一 JSON 序列化；GUI 线程只消费不可变 `PreparedKlineRender`。正常路径复用静态 HTML/CSS/JS 壳并调用 `applySnapshot()`，仅提交/确认失败时构造受控回退页。
- 快速切股立即让旧 frame 只读；render hand-off 只接受当前 window/generation/code，新的 snapshotVersion 覆盖旧 pending 和 inflight。隐藏或最小化时暂停实时提交、粒子与高频交互，恢复后只重放最新完整快照；无变化报价、相同索引和相同快照版本不重复 `setOption()`。
- 实时更新先在后台把最新 bar 合入完整 history，再重算 MA、VOL-MA20、MACD、DIFF、DEA 并提交小快照，避免只有 OHLC 更新而指标滞后。
- JS 只注册一次 ECharts `rendered` 监听器。Python 对 `windowId + generation + code + points + snapshotVersion` 进行严格匹配，并通过 8ms 轮询和 2 秒 watchdog 等待真实 rendered 状态；只有确认成功才 `claim_frame()` 并记录 `chart_ready`。窗口可见、Browser 存在或 `setOption()` 返回都不是打开成功。
- 打开阶段统一为 `shell_ready → browser_ready → data_ready → js_ready → chart_ready → first_interaction`，生产 health/smoke 必须按真实 `chart_ready` 判定。
- 完整应用 production health 在每次真实 `open_chart()` 前 reset stall 探针，只采集 `kline_open_to_chart_ready` 范围，并在 `chart_ready` 后跨两个事件循环 tick 再封存回执；预算同时校验 scope、reset、样本完整性、最大 stall ≤ 100ms 和 critical/event-loop critical = 0，避免把打开前后的其它阶段混入或漏采。
- 六阶段之前没有隐藏的同步“大块初始化”：冷创建和受控恢复把 Browser 物理准备拆成四个由窗口拥有的单次 `Qt.PreciseTimer` 切片：`create` 创建 View，`handoff/skip` 接管预热 Page 或明确跳过，`attach` 挂入 `chart_host` 并安装 owner 信号/恢复保护，`WebEngine shell` 延后派发静态页面壳。该子流水线结束后记录 `browser_ready`，随后才由准备结果、JS API 探测、rendered 回执和真实交互依次推进其余 readiness。
- `browser_create_ms`、`page_handoff_ms/page_handoff_slice_ms`、`browser_attach_sync_ms`、`load_shell_*`、`max_sync_slice_ms` 与 `pipeline_total_ms` 用于定位 `browser_ready` 内部成本。完整物理窗口出池时 Browser/Page/层级/静态壳均保留，诊断写入 `full_window_reused=true` 且所有冷路径耗时为 `0.0`；零值表示明确跳过，不允许解释成缺失采样。

### 8.2 完整物理 `KLineChartWindow` 单槽池

首帧与 11 Tab 后台预载结束后，主窗口等待空闲尾部再异步执行 WebEngine preflight，并创建至多 1 个完整物理 `KLineChartWindow` 作为隐藏 idle keeper。生产路径复用整个顶层窗口、Browser、Page、`chart_host` 和布局层级；不会把 keeper View/Page 重挂接到另一个正式窗口，也不会移动 parent/page。

租约流程如下：

1. idle 物理窗口出池，保持 UUID，`reopen_lease()` 后递增 generation，并重新连接行情、主题和 render-process 信号。
2. 用户关闭时停止本窗口 Timer/任务、取消 rendered 确认、断开信号并清空租约专属 frame/quote。
3. manager 调用 JS `resetForLease()`；只有 `ok=true + reset=true` 且 Browser/Page/parent/静态壳仍健康时才回到 idle 单槽。
4. 回收上限固定 2 秒。超时、断信号失败、层级变化、渲染进程异常、已有 idle 窗口或任一 cleanup 失败都 fail-closed：销毁完整物理窗口，不把污染资源送回池。

WebEngine 尚在 preflight/预热时，`PendingKlineOpenRequest` 只保存最新请求并在 GUI 线程自动恢复；仍遵守最多 5 个可见 K 线窗口。`renderProcessTerminated` 由每个活动窗口监听：第一次异常允许受控重建 Browser 并重放最新有效不可变快照，替换 Browser 再次异常即关闭 controller/runtime、显示终止状态并要求重新打开，不进行无限重启。

应用退出时 `KLineWindowManager.shutdown()` 返回严格结构化 receipt，分别记录活动窗口关闭、fallback dispose、池窗口销毁、预热资源、return Timer、idle termination guard、preflight、活动窗口数、managed keeper、pending-open 和主窗口引用。任一字段不干净都会使性能预算 fail-closed；runtime health 还要求 WebEngine 子进程、Task、QThreadPool 和 watchdog 归零。

### 8.3 功能边界

当前保留日线最后 250 根、K 线/成交量/MACD 三面板、十字光标、OHLC/涨跌/量价工具栏、滚轮缩放、拖动平移、MA10/20/50/150/200、均线收敛淡化、MA200 穿越强调、VOL-MA20、MACD/DIFF/DEA、VCP 箱体/趋势线/突破、业绩标记与 tooltip、量能分型和粒子效果，以及来源摘要卡、A 股/亚洲数据、实时更新、主题/Mica/玻璃、磁吸、F11、标题栏双击全屏、Esc、状态恢复和关注池联动。

明确不补回 MA5，也不补回历史 B/S/T 账户交易标记；当前不增加周线、分钟线和周期切换，不替换 PyQt6/WebEngine/ECharts 技术栈。

WebEngine 问题优先通过 `app/services/kline_webengine_preflight.py`、`scripts/kline_webengine_lifecycle_smoke.py`、runtime health 报告和 manager shutdown receipt 定位。

## 9. F5 与缓存刷新链路

全局 F5 不是单纯刷新当前页面，而是一次分层的缓存和快照更新：

```text
MainWindowQT._action_refresh_f5()
  -> ui.main_window_runtime.start_f5_precompute()
  -> F5JobRequest
  -> ProcessF5JobRunner
  -> app.workers.f5_worker_main
  -> RPSPrecomputer.run_f5_job()
  -> job-local market / RPS120+250 / sector RPS / optional GBBQ bundle
  -> F5JobController reads READY_TO_ACTIVATE result
  -> F5SnapshotInstaller validates the complete bundle
  -> activation gate: SQLite active pointers + parent memory
  -> MainWindowQT._on_f5_done()
  -> ui.main_window_runtime.finish_f5_reload()
  -> CentralQuotesService.refresh_after_cache_reload()
  -> WorkspaceTableService.refresh_all_tabs_after_f5_scheduled()
  -> domain_events.sig_cache_reload_completed
  -> WorkspaceFacade.refresh_information_sources_after_f5()
```

关键约束：

- 子进程只写自己的 job/generation，不覆盖当前可用快照；父进程只激活路径、schema、来源、行数、股票数、有效值数量和交易日全部通过的 bundle。
- `requested_date` 是请求日期，`effective_trade_date` 是行情 Parquet 的实际最大交易日；RPS120、RPS250 和板块 RPS 必须与 effective date 一致，周末/节假日允许 requested 与 effective 不同。
- 激活在同一进程级读写 gate 内发布 SQLite active 指针并替换父进程行情/RPS内存；中途失败、取消或超时会回滚指针、内存和 GBBQ，继续使用旧 active bundle。
- F5 成功后先刷新核心行情快照，再按 registry 策略分帧处理已加载表格，避免一次性阻塞 UI；占位页不会因为 F5 被构造。
- 支持 `_on_cache_reload_completed` 或 `_schedule_context_refresh` 的已加载 Tab 可以在缓存完成信号后回读最新快照；情报源刷新由 `WorkspaceFacade.refresh_information_sources_after_f5()` 汇总触发。
- active、previous 和尚未被父进程消费的 `ready_to_activate` 任务受 retention 保护；失败 generation 删除，已终态旧 job 在下一次 retention 立即清理，只允许当前终态 job 短暂保留。生产真实 F5 的退出回执还要求磁盘 active snapshot 与唯一 terminal job 都等于最后一次成功 run_id。固定 RPS 文件只是兼容镜像，已有 active bundle 时不是真相源。
- 超时或窗口退出先协作取消，超过宽限期再 terminate/kill 并回收子进程；任务以 `status + error_code` 区分超时、启动、计算和激活失败。
- F5 使用独立的 `F5JobController` 和隔离子进程，不进入全局 `TaskManager` 任务注册表。
- `F5JobController` 的 Qt Timer 只消费线程安全消息；所有可能阻塞的 handle 轮询、deadline、取消、terminate/kill 和 wait/reap 都由 `f5-worker-monitor` 独占，避免 GUI 线程被子进程 I/O 卡住。
- `spawn_f5_worker()` 使用 below-normal priority，并只向子进程注入 `POLARS_MAX_THREADS=max(1,min(4,logical_cpu_count//2))`，同时把 `OPENBLAS_NUM_THREADS`、`OMP_NUM_THREADS`、`MKL_NUM_THREADS`、`NUMEXPR_NUM_THREADS` 固定为 1；这不会修改父进程的全局 Polars/BLAS 配置。
- terminal result 持久化后，失败 generation 和旧 job 的清理由 controller-owned `f5-terminal-cleanup` 执行；`is_running` 包含该线程，shutdown 在同一 deadline 内等待 monitor、activation 和 cleanup。SQLite/父内存原子边界只负责 active pointer 安装与回滚，retention cleanup 由独立磁盘回执验收。

## 10. 事件、任务、服务开关与配置

### 10.1 事件

领域/应用事件总线位于 `domains/runtime/domain_events.py`，承载缓存、行情、刷新、网络状态、数据源更新等事件。UI 导航类请求通过 `infra/events/ui_signal_hub.py` 处理，避免领域事件总线混入 UI 导航语义。

### 10.2 应用级日志缓冲

`LogBufferService` 保留原 stdout/stderr 输出，同时把 stdout、stderr 和 `sig_system_log` 收敛到 3000 条有界 deque。每条记录带 generation/sequence；清空后旧代次已经排队的消息不能重新出现。`LogTab` 按首开后台预加载顺序创建后分批回放，应用退出时恢复原始流并断开订阅。

### 10.3 后台任务

后台任务统一使用 `core/background_job_runner.py` 和 `infra/tasks/typed_task_registry.py`。任务 ID 不应在 UI 层硬编码字符串，应通过 `task_registry` 或已注册的 `TaskKey` 取得。

当前已注册的关键任务包括：

- `STARTUP_DEFERRED_LOAD`
- `STARTUP_F5_RETENTION`
- `STARTUP_ASIAN_DATA_SYNC`
- `STARTUP_SMART`
- `NETWORK_GO_ONLINE`
- `NETWORK_FORCE_RECONNECT`
- `CENTRAL_QUOTES_POLL`
- `SHARED_MARKET_CAPS`

### 10.4 服务开关

服务开关位于 `infra/features/service_toggle_registry.py`，支持 `VCP_TOGGLE_...` 环境变量覆盖。当前关键开关包括：

- `central_quotes_service`
- `silent_asian_sync`
- `daily_global_earnings_calendar_sync`
- `startup_history_cache_load`

新增可选运行能力应先进入服务开关，而不是在 UI 或启动编排器里散落布尔变量。

### 10.5 配置

配置入口分两类：

- 应用配置：`core/app_config.py` 以及 `infra/settings/*`
- 表格状态：`TableViewStateStore` 管理列宽、排序、可视状态等持久化

新增直接使用 QSettings 的代码应优先放入 `infra/settings`，不要分散在 UI 组件中。

## 11. 运行时健康与稳定性诊断

当前仓库已经把临时性能探针逐步产品化：

- `infra/diagnostics/runtime_health.py` 采集后台任务、Timer、事件订阅、线程、WebEngine 子进程、行情请求、F5 缓存、本地市场数据源状态和关键数据血缘；数据血缘保留 registry `network_capable` 声明，并合并实例实际 `triggered_network` 闩锁。
- `infra/diagnostics/ui_stall_probe.py` 记录 UI 事件循环和关键 UI 方法的卡顿跨度；UI 代码通过 `app/services/ui_diagnostics_service.py` 引入，避免直接跨到 `infra`。
- `app/services/runtime_health_service.py` 给 UI 暴露采集和导出入口。
- `ui/components/runtime_health_dialog.py` 在系统菜单中提供运行时健康面板。
- `scripts/runtime_health_stability_suite.py` 可以执行原生可见 production 门禁或 30/60 分钟 soak；启用 `--background-prewarm` 时会在 Tab 轮询前等待首开队列，并校验 11 页计划/启动/完成顺序、全部加载、单步串行、失败与超时。生产门禁使用 `--real-f5` 记录不同于父进程的 worker PID、`prepare/gbbq/market_sync/market_stage/rps/sector_rps/validate` 阶段事件、激活 snapshot 和 post-F5 收尾，不再用 UI 完成回调代替完整 F5。
- 默认探针集合直接读取 registry 的 `health_probe_order` 并覆盖全部 11 个稳定 key。指定 `--output` 时还会在启动样本、每个周期样本和可见性失败时原子更新同名 `.checkpoint.json`，记录最后确认的窗口可见时长、当前 phase/Tab、最后完成边界和样本路径；独立 `.faulthandler.log` 的句柄保活到套件结束。未处理 Qt 回调异常会进入报告并强制预算失败。
- `scripts/perf_budget_check.py` 可以读取 runtime health、K 线生命周期和历史性能探针报告并输出预算结果。
- K 线专用原生门禁至少运行 10 轮，并以 ECharts rendered 后的 frame owner 为提交标准。固定预算是壳 P95 ≤ 120ms、预热 `browser_ready` P95 ≤ 500ms、A 股 `chart_ready` P50/P95 ≤ 800/1500ms、缓存切股 P95 ≤ 300ms、打开最大 GUI stall ≤ 100ms 且 critical stall = 0；cold-first-open、常规预热轮和正式轮次分别执行 stall/critical 门禁。稳态 RSS 净增长 ≤ 24MB，Chart View、Task、Timer、Receiver 与 WebEngine 子进程净增长为 0。
- 启动诊断同时保留 window-only、`run_suite` 应用初始化 inclusive、脚本模块 inclusive 三层口径。脚本模块时钟在导入 `time` 后立即开始，覆盖后续模块导入、Qt/`QApplication`、原生 dataframe 运行时及窗口阶段，但明确不覆盖进程创建、Python 解释器启动和 `time` 导入；它是必填的诊断上界，不把健康脚本自身导入成本计入应用 SLA。固定 `3500 ms` 首帧、`5500 ms` 初始 Tab 就绪预算约束 `run_suite` 入口开始的真实应用初始化口径；门禁同时要求三层字段完整、有限、非负且满足脚本模块 ≥ 应用初始化 ≥ window-only。
- 探针使用与真实侧栏一致的 `shell_nav` 激活路径；该原因属于真实交互首开，页面显示后必须启动业务 runtime，重型工作则按 registry 声明的首开延迟异步执行；Tab 异步尾部在阶段 reset 前单独采样，F5、行情和后台 idle 超时都会向顶层传播并触发预算失败。

`TabDataLineageService` 是后续稳定性治理的重要接口。当前覆盖分区固定为 10 个数据 Tab，`system_log` 以 `non_data_tab` 明确排除。隐藏预载的网络门禁要求唯一 `after_background_preload` 样本、精确分区和每个数据 Tab 恰好一行；`network_capable`/`triggered_network` 缺失或非严格 bool、getter 异常产生的 `lineage_error`、以及隐藏阶段实际联网都会直接失败。新数据页必须先更新 registry 的覆盖/排除声明，再提供 `get_data_lineage()` 说明来源、缓存、是否联网、是否降级、更新时间、行数和签名。

当前短门禁实测证据以 `tmp/goal_validation/20260717-123322/runtime_health_short_after_ai_fix.json` 和 `tmp/goal_validation/20260717-123322/kline_lifecycle_production_final_verified.json`（预算为同目录 `kline_lifecycle_budget_final_verified.json`）为准，两组报告与预算均为 `ok`。前者证明 11 Tab 固定顺序、并发 1、精确 10+1 血缘分区且隐藏联网触发 0，并验证真实隔离进程 F5；后者通过真实 `TdxDataProvider` 的 `production-local` 离线只读路径证明 10/10 完整窗口复用、缓存切股与资源归零。最终长稳结果以 `runtime_health_soak30_final.json` 及 `final_perf_budget.json` 为准。

runtime health 的 `market_data` 段会展示当前 active layer，例如 `memory_cache`、`parquet_sqlite_warehouse`、`legacy_parquet_bootstrap`、`vipdoc_fallback_ready` 或 `vipdoc_fallback`，并包含 trade_date、row_count、symbol_count、是否降级以及降级原因。

## 12. 本地数据和运行时产物

运行时会生成或维护：

- `data/Cache/`：RPS 兼容镜像、亚洲市场、财务/股本、全球财报日历等缓存
- `data/Cache/parquet/market_data.parquet`：普通仓库/首次激活前兼容的全市场历史日线明细；`meta.parquet` 保留兼容元数据
- `data/Cache/f5_jobs/<run_id>/`：F5 的 request、events、result、cancel request 和 worker log
- `data/Cache/f5_generations/<run_id>/`：不可变的 market Parquet、RPS、板块 RPS 和可选 GBBQ bundle
- `data/vcp_hunter.db`：SQLite 数据，例如交易日、基金持仓、扫描缓存、`market_data_manifest`，以及 F5 snapshot manifest/active 指针
- `data/logs/`：应用日志
- `data/crash_report.log`：生产入口的 Qt 回调异常与 `faulthandler` 原生崩溃日志
- `tmp/runtime_health_*`：运行时健康报告和稳定性采样；指定 output 时包含 `.checkpoint.json` 与 `.faulthandler.log` 旁路证据
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
- K 线上下文与 P1 服务：`tests/test_kline_open_service.py`、`tests/test_kline_summary_cards.py`、`tests/test_kline_load_controller.py`、`tests/test_kline_p1_services.py`
- K 线真实 rendered、恢复和完整窗口池：`tests/test_kline_js_runtime_contract.py`、`tests/test_kline_window_rendering.py`、`tests/test_kline_window_recovery.py`、`tests/test_kline_full_window_pool.py`
- 工作区聚合：`tests/test_workspace_quote_codes.py`、`tests/test_stock_candidate_tab.py`
- Tab 与生命周期契约：`tests/test_tab_registry.py`、`tests/test_workspace_lifecycle.py`
- 首帧边界：`tests/test_post_paint_startup_boundary.py`
- 股票上下文纯数据契约：`tests/test_stock_context_golden_contract.py`
- F5 子进程、校验、取消与原子激活：`tests/test_f5_process_pipeline.py`
- 应用级日志缓冲：`tests/test_log_tab.py`
- Qt 回调异常隔离：`tests/test_ui_exception_boundary.py`、`tests/test_ui_task_lifecycle_service.py`
- 运行时健康：`tests/test_runtime_health.py`、`tests/test_perf_probe_scripts.py`、`tests/test_perf_budget_check.py`
- 本地行情仓库：`tests/test_market_data_warehouse_manifest.py`、`tests/test_market_data_warehouse.py`

PyQt 相关测试优先使用 offscreen smoke test。WebEngine 生命周期检查需要原生 Qt/可视桌面时，应使用专门脚本，不要把不稳定窗口自动化混入普通单元测试。

后续审计时应把 `.github/workflows/ci.yml` 纳入边界核对，避免 Ruff、UTF-8、架构回归和运行时健康门禁与真实源码漂移。

## 14. 新增能力的维护规则

新增 Tab、数据源或跨页联动时，优先遵守以下规则：

1. 新 Tab 只在 `ui/workspaces/tab_registry.py` 注册，并声明稳定 key、标题、顺序、构造 profile 和各项运行策略。
2. 表格型 Tab 尽量继承或复用 `BaseStockTab` 的行情刷新、K 线跳转、右键菜单和工具栏基线。
3. 新的盘中字段接入 `domains/quotes/*`，不要在每个 Tab 单独解析 quote payload。
4. 需要跨页共享的运行时状态优先进入 `GlobalStore`；股票上下文通过不可变 `StockContextSnapshot` 和 `StockContextQueryService` 聚合，`StockContextService` 仅负责异步快照生命周期。
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
- 主要性能治理已经落到共享路径：首屏优先且全 Tab 有序后台预加载的工作区、中央行情广播、表格增量刷新、运行时健康采样和预算检查。

本阶段架构收口已经完成：首屏优先与全 Tab 依赖有序后台预加载、稳定 Tab key、不可变 registry、纯数据 StockContext、隔离 F5 和统一关闭生命周期均已进入生产路径与契约测试。后续工作属于常规持续维护，重点仍是收窄边界和拆分剩余热点：

- 原 `app/services/ui_runtime_service.py` 已删除；后续新增 UI 或脚本跨层依赖必须落到更窄的 `ui_*_service.py`，架构门禁禁止恢复宽泛桶导入。
- `domains/global_earnings_calendar/service.py`、`ui/tabs/fund_holdings_tab.py`、`ui/kline_chart_payload.py`、`vcp/fetchers/asian_kline_fetcher.py`、`ui/tabs/asian_market_workers.py` 等历史大文件可继续按数据获取、转换、缓存、展示渐进拆分，但不得为了文件尺寸进行无收益重写。
- UI 卡顿探针真实实现位于 `infra/diagnostics/ui_stall_probe.py`，UI 侧通过 `app/services/ui_diagnostics_service.py` 访问；`core/ui_stall_probe.py` 仅保留历史导入兼容门面，新代码不应继续从 `core` 引入该诊断能力。
- `vcp/` 和 `core/` 仍有兼容入口。新增真实实现不应继续落入这些目录。
- 数据后端应继续向 Parquet/SQLite-first 读模型演进，`vipdoc` 保留为本地生产者和兜底源。

推荐演进路线：继续保留桌面模块化单体，以测试保护下的渐进维护替代微服务化、Web 化或大规模 UI 重写。

## 16. 已知边界

- 项目以 Windows 桌面为主，Linux/macOS 未按完整可运行目标维护。
- 核心历史数据依赖本地通达信 `vipdoc` 目录。
- 外部源可用性会影响亚洲市场、北美战报、财务补充、全球财报日历和盘中行情质量。
- `core/`、`vcp/`、`earnings/` 中仍有历史兼容入口，新增真实实现应优先落到 `app/`、`domains/` 或 `infra/`。
- README 适合放快速导览；更细的技术说明以本文档和 `docs/module-owners.md` 为准。
