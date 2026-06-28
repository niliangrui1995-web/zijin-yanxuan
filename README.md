# 紫金研选量化终端

Windows 优先的 PyQt6 桌面看盘与选股工具，围绕 A 股 VCP（Volatility Contraction Pattern）扫描、盘中监控、关注池联动和多市场辅助观察构建。

最后校验：2026-06-14（按当前源码、`docs/technical-architecture.md` 和 `docs/module-owners.md` 重新核对）。

当前代码基于本地通达信日线数据和 Parquet/SQLite 本地仓库运行，盘中实时行情通过东方财富 HTTP 链路获取，并在必要时回退到新浪、腾讯批量报价；海外、亚洲、龙虎榜、大宗交易、业绩和基金持仓页面各自维护独立抓取、清洗、缓存和展示链路。

> 注意
>
> 当前仓库已经移除 `AI 诊股`、`AI 追踪`、`ai_service.py`、`ai_diag_panel.py` 等旧模块。本文档仅描述仓库当前实际存在的架构和代码。

![Python 3.14](https://img.shields.io/badge/Python-3.14-blue)
![PyQt6](https://img.shields.io/badge/UI-PyQt6-green)
![Platform](https://img.shields.io/badge/Platform-Windows-lightgrey)
![License](https://img.shields.io/badge/License-Private-red)

## 当前功能面

当前 `ClassicWorkspace` 装配了 12 个主 Tab：

| 页面 | 模块 | 说明 |
| --- | --- | --- |
| 关注池 | `ui/tabs/watchlist_tab.py` | 自选股票池，联动实时现价、涨幅、市值、催化与专题信息 |
| 亚洲寡头 | `ui/tabs/asian_market_tab.py` | 多市场亚洲龙头/寡头跟踪，带本地缓存与盘中刷新 |
| 北美战报 | `ui/tabs/na_daily_tab.py` | 从战报产出文件中回填标的，并挂接实时行情 |
| 综合候选 | `ui/tabs/stock_candidate_tab.py` | 汇总扫描、战报、AI 产业链、业绩、基金持仓等多源候选 |
| AI产业链 | `ui/tabs/ai_industry_chain_tab.py` | AI 产业链标的、细分环节与上下文信号跟踪 |
| 龙虎榜 | `ui/tabs/lhb_tab.py` | 30 日滚动龙虎榜关注池，带上榜次数、最近上榜、净买额等字段 |
| 盘中监控 | `ui/tabs/rt_monitor_tab.py` | 盘中轮询待突破池，展示实时突破状态 |
| VCP 扫描 | `ui/tabs/scan_tab.py` | 全市场 VCP 静态扫描结果页 |
| 大宗交易 | `ui/tabs/foreign_block_trade_tab.py` | 外资席位相关大宗交易监控与过滤 |
| 业绩异动 | `ui/tabs/earnings_tab.py` | 业绩预告、快报、财报高增跟踪 |
| 基金持仓 | `ui/tabs/fund_holdings_tab.py` | 基金/QFII 持仓同步、对比与最新变动跟踪 |
| 系统日志 | `ui/tabs/log_tab.py` | 统一查看运行日志与后台任务状态 |

除此之外，还有几条贯穿全局的能力：

- K 线详情窗口：`ui/kline_window_qt.py`
- 中央行情广播与表格快照合并：`ui/workers/central_quotes_worker.py` + `core/global_store.py`
- 标题栏全局导航、同步与交易日历入口：`ui/components/main_window_shell.py` + `ui/main_window_visuals.py`
- A 股交易日历与全球寡头财报面板：`ui/components/trade_calendar.py` + `domains/global_earnings_calendar/service.py`
- 统一股票右键菜单与 Codex 投研跳转：`ui/components/stock_context_menu.py` + `ui/workspaces/stock_context_service.py`
- Windows 系统菜单开机自启动：`ui/components/main_window_shell.py` + `infra/navigation/windows_autostart.py`

## 技术文档

- 技术架构文档：`docs/technical-architecture.md`
- 模块归属与边界登记：`docs/module-owners.md`
- 项目审计入口：`docs/project-audit.md`
- 产品演示与截图脚本说明：`docs/promo-video-demo.md`

## 当前交互基线

当前仓库的主工作区已经完成一轮统一化交互收口，主要约束如下：

- 主窗口标题栏集成 Tab 分组导航、全局 F5 同步、交易日历入口和同步状态摘要
- 除关注池外，各 Tab 默认以轻量占位页挂载，进入时按需加载，并在后台分批预热
- 主要数据页统一采用页面级状态反馈，明确区分 `加载中 / 最新数据 / 缓存数据 / 刷新失败 / 离线`
- 各 Tab 页头统一回答“当前看的是什么数据、筛选是否生效、数据何时更新”
- 通用工具栏已经按窄宽度场景重排，优先保证筛选控件、状态摘要和动作按钮不互相挤压
- 表格刷新会尽量保持当前行、选中代码、滚动位置和列状态，行情变动以轻量闪烁提示
- 亚洲页在远端抓取失败时会明确标记“沿用缓存”，而不是把底层抓取异常直接外泄给用户
- 股票右键菜单统一提供 K 线、股票全景、关注池、通达信/东方财富和 Codex 产业链投研入口
- K 线窗口和主窗口关键按钮补齐了 tooltip 与可访问性命名，便于悬停识别和后续维护

## 技术栈

- 语言：Python 3.14
- UI：PyQt6、PyQt6-WebEngine、自研动态 QSS 主题系统
- 表格模型：`QTableView` + `QAbstractTableModel`
- 数据处理：pandas、numpy、polars、pyarrow、openpyxl、lxml
- 拼音辅助：`pypinyin`
- 桌面/系统联动：`pywin32`、`pyautogui`、`codex://` 深链接
- A 股本地数据：通达信 `vipdoc` 日线文件
- A 股实时行情：东方财富 HTTP，异常时回退新浪、腾讯批量报价
- 财务/股本补充：东方财富接口
- 海外/亚洲辅助数据：AkShare、yfinance、Yahoo Japan、TWSE/TPEX、Naver、腾讯港股、requests、`curl_cffi`
- 任务调度：`infra/tasks/task_scheduler.py` + `core/background_job_runner.py`
- 服务开关：`infra/features/service_toggle_registry.py`
- HTTP 与子进程边界：`infra/http_safety.py` + `infra/tasks/process_runner.py`
- 全局通信：`domains/runtime/domain_events.py` + `infra/events/ui_signal_hub.py`
- 日志：`core/logger.py`
- 配置持久化：`infra/settings` + `core/app_config.py`
- 本地缓存：`data/Cache/*.json`、`data/vcp_hunter.db`

## 架构概览

### 1. 启动链路

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

关键点：

- 入口文件是 `vcp_hunter_qt.pyw`，负责单实例限制、崩溃日志和 `QApplication` 初始化。
- `ApplicationBootstrap` 负责装配数据 provider、扫描引擎、工作区和中央行情服务。
- 程序默认先以“离线优先”启动，优先保证冷启动可用。
- `StartupOrchestrator` 在启动后异步完成：
  - 本地缓存恢复
  - RPS 预计算缓存恢复
  - 亚洲市场 JSON 缓存静默同步
  - 全球寡头财报日历静默同步
  - 网络探测与在线模式切换

### 2. 工作区与页面装配

- 主窗口外壳：`ui/main_window_qt.py`
- 工作区装配：`ui/workspaces/classic_workspace.py`
- 当前仅装配 `ClassicWorkspace`
- 首屏先挂载 `LazyTabPlaceholder` 占位，所有页面再按需加载或进入后台预热队列逐个创建
- 跨 Tab 表格聚合、实时订阅代码收集、个股信号汇总和导航定位统一通过 `WorkspaceFacade` 及能力协议完成
- 各 Tab 大多继承 `ui/tabs/base_stock_tab.py`，共享：
  - 右键菜单
  - K 线跳转
  - 表格行情刷新
  - 市值补齐
  - 工具栏构建

### 3. 行情与表格链路

当前仓库已经把盘中表格的 `现价 / 涨幅 / 市值` 统一到同一条实时链路：

```text
QuoteUniverseService.collect_realtime_quote_codes()
  -> CentralQuotesService
  -> event_bus.sig_rt_quotes
  -> GlobalStore(quotes snapshot)
  -> BaseStockTab / TableModel.update_quotes()
  -> 各表格页面刷新
```

对应模块：

- 中央广播：`ui/workers/central_quotes_worker.py`
- 全局快照存储：`core/global_store.py`
- 快照合并与指标解析：`domains/quotes/*`（`core/quote_snapshot.py` / `core/quote_dispatcher.py` 仅兼容导出）
- 表格模型：`ui/models/table_models.py`

这条链路的当前行为是：

- 实时 quote 到达后先进入中央广播器
- 缺失 A 股股本时按需补一次 finance 数据
- `GlobalStore` 对 quote 做逐股深合并，而不是简单覆盖
- 表格统一解析 `close / last_close / _zongguben / market_cap`
- 市值按最新价动态重算，而不是依赖静态写死值
- 表格模型优先使用增量 `dataChanged` / `layoutChanged`，减少全表重置造成的闪烁

### 4. 数据层与策略层

- `vcp/data_provider.py`
  - 作为聚合门面，编排 `infra/market_data/*` 读取本地日线、实时行情和复权处理
  - 保留历史兼容入口，但真实 provider 子服务已迁入 `infra/market_data`
- `vcp/engine.py`
  - 作为聚合门面，统一调度 `domains/scan/*` 完成 VCP 指标、RPS 和待突破池计算
- `domains/earnings/*`
  - 负责业绩高增扫描与巡检调度；顶层 `earnings/*` 已退化为兼容壳
- `domains/fund_holdings/*`
  - 负责基金持仓比对、存储与同步
- `domains/watchlist/*`
  - 负责关注池视图模型与来源标签规则
- `domains/quotes/*`
  - 负责实时报价 payload 标准化、快照合并、市值补齐
- `domains/market_calendar/*`
  - 负责多市场交易日、交易时段、报价刷新时段判断；`core/market_calendar.py` 仅保留兼容导出
- `domains/global_earnings_calendar/*`
  - 负责全球寡头财报事件抓取、确认事件合并、冲突标记、缓存和交易日历叠加展示

## 目录结构

```text
.
├─ vcp_hunter_qt.pyw              # 应用入口
├─ requirements.txt              # 运行时依赖
├─ pyproject.toml                # Ruff 配置
├─ assets/                       # 前端静态资源（如 echarts）
├─ core/                         # 核心基础设施
│  ├─ app_config.py
│  ├─ cache_manager.py
│  ├─ event_bus.py
│  ├─ global_store.py
│  ├─ logger.py
│  ├─ market_calendar.py
│  ├─ startup_orchestrator.py
│  ├─ quote_snapshot.py
│  ├─ runtime_env.py
│  └─ task_manager.py
├─ app/                          # 应用编排层（bootstrap / use cases / services）
├─ domains/                      # 领域服务稳定入口
│  ├─ earnings/
│  ├─ fund_holdings/
│  ├─ global_earnings_calendar/
│  ├─ market_calendar/
│  ├─ quotes/
│  ├─ runtime/
│  ├─ scan/
│  └─ watchlist/
├─ earnings/                     # 业绩领域兼容壳
├─ infra/                        # 基础设施适配层
│  ├─ events/
│  ├─ features/
│  ├─ market_data/
│  ├─ navigation/
│  ├─ settings/
│  ├─ storage/
│  └─ tasks/
├─ ui/                           # PyQt6 界面层
│  ├─ main_window_qt.py
│  ├─ kline_window_qt.py
│  ├─ main_window_runtime.py
│  ├─ workspaces/
│  ├─ components/
│  ├─ models/
│  ├─ presenters/
│  ├─ shell/
│  ├─ signals/
│  ├─ styles/
│  ├─ tabs/
│  ├─ viewmodels/
│  └─ workers/
├─ vcp/                          # VCP 聚合门面与兼容入口
│  ├─ constants.py
│  ├─ data_provider.py
│  ├─ data_provider_local.py
│  ├─ engine.py
│  ├─ engine_external.py
│  ├─ fetchers/
│  ├─ models.py
│  ├─ polars_engine.py
│  ├─ sector.py
│  └─ utils.py
├─ tests/                        # pytest 回归测试
├─ scripts/                      # Windows 打包、UTF-8 检查、UI 截图审计脚本
├─ .github/workflows/            # CI 护栏
├─ docs/                         # 补充说明文档
└─ data/                         # 运行时生成的数据、缓存和日志
```

## 当前分层结论

- `app/` 只依赖宿主公开接口，不再直接 import `ui.*` 具体实现。
- `domains/` 提供 `scan / earnings / quotes / watchlist / fund_holdings / market_calendar / global_earnings_calendar` 的稳定入口。
- `infra/` 承载 `market_data / settings / navigation / storage / tasks / events / features` 等外部边界适配。
- `ui/` 主要负责 Qt 装配、页面状态、事件接线、主题外壳和表格交互。
- `core/` 保留事件总线、兼容门面和跨层共享能力，不再承载新增真实领域实现。

## 运行要求

推荐环境：

- Windows 10 / 11
- Python 3.14（64 位）
- 已安装通达信，并可访问本地 `vipdoc` 数据目录
- 可访问东方财富、AkShare、Yahoo Finance 相关数据源

说明：

- 项目明显是 Windows 优先设计：
  - 入口使用 `pythonw`
  - 存在单实例互斥锁
  - 依赖 `pywin32`
  - 部分联动能力默认面向本地桌面应用
- Linux / macOS 未见完整适配代码，不建议按“可直接运行”理解

## 快速开始

### 1. 克隆仓库

```powershell
git clone <your-repo-url>
cd 紫金研选
```

### 2. 创建虚拟环境

```powershell
py -3.14 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
```

如果本机没有 `py` 启动器，请直接指定 Python 解释器路径。

### 3. 安装运行时依赖

```powershell
python -m pip install -r requirements.txt
```

`requirements.txt` 当前包含的关键运行时依赖有：

- `PyQt6`
- `PyQt6-WebEngine`
- `pytdx`
- `pandas`
- `numpy`
- `polars`
- `pyarrow`
- `requests`
- `openpyxl`
- `pypinyin`
- `pyautogui`
- `akshare`
- `yfinance`
- `curl_cffi`
- `lxml`
- `pywin32`

### 4. 配置通达信数据目录

推荐在项目根目录放置 `vcp_tdx_config.json`：

```json
{
  "tdx_vipdoc_root": "D:\\HT\\vipdoc"
}
```

程序启动后会用这个目录读取本地日线数据与名称映射。

### 5. 可选外部数据密钥

全球寡头财报日历可以在无密钥时使用公开源和本地确认事件；如果需要补充 Alpha Vantage 或 DART 数据，可在当前终端会话设置：

```powershell
$env:ALPHAVANTAGE_API_KEY = "<your-alpha-vantage-key>"
$env:OPENDART_API_KEY = "<your-opendart-key>"
```

### 6. 启动程序

生产式启动：

```powershell
.\.venv\Scripts\pythonw.exe .\vcp_hunter_qt.pyw
```

调试式启动（会保留控制台）：

```powershell
.\.venv\Scripts\python.exe .\vcp_hunter_qt.pyw
```

### 7. 可选：开机自启动

Windows 环境下可以在标题栏的系统菜单中勾选 `开机自启动`。

该开关写入当前用户的 Windows Run 注册表项，并按顺序选择可用启动命令：

1. 打包后的 `dist/紫金研选/紫金研选.exe`
2. 本机静默启动器 `%LOCALAPPDATA%\ZijinResearch\Launcher\ZijinResearchLauncher.exe`
3. 当前项目 `.venv\Scripts\pythonw.exe .\vcp_hunter_qt.pyw`

## 数据源与运行模式

当前项目的数据链路遵循“先获取原始数据，再本地加工，最后进入 Tab 展示”的顺序。外部接口只负责取数；去重、字段标准化、口径合并、缓存、降级提示和跨 Tab 信号汇总都在本地完成。

东方财富妙想 skill 目前没有接入紫金研选的主数据链路。当前 Tab 的真实来源仍是下面这些本地文件、公开接口、AkShare 封装和项目内缓存。

### A 股行情与基础数据

| 能力 | 主来源 | 本地处理与缓存 | 主要代码 |
| --- | --- | --- | --- |
| 历史 K 线 | 本地通达信 `vipdoc` 日线文件 | 优先读取内存和 Parquet/SQLite 仓库，缺失时回退 `vipdoc`，F5 后写回仓库 manifest | `infra/market_data/tdx_data_provider.py`、`infra/market_data/market_data_warehouse.py`、`vcp/data_provider_local.py` |
| 盘中实时报价 | 东方财富 `push2.eastmoney.com/api/qt/ulist/get` | 按中央报价站 30 秒轮询，运行时去重、冷却、单飞行任务；东方财富失败后回退新浪 `hq.sinajs.cn`，再回退腾讯 `qt.gtimg.cn` | `ui/workers/central_quotes_worker.py`、`vcp/data_provider_quotes.py`、`vcp/data_provider_realtime.py` |
| 股本/市值补充 | 本地通达信股本快照优先，东方财富 `push2` 补充 | 用于表格市值动态重算和缺失 `_zongguben` 补齐，带磁盘缓存 | `app/services/central_quote_polling_service.py`、`vcp/engine_external.py`、`ui/tabs/base_stock_refresh.py` |
| 十大流通股东/机构股东 | 东方财富 F10 `PC_HSF10/ShareholderResearch/PageAjax` | 本地识别机构关键字，结果缓存 90 天 | `vcp/engine_external.py` |
| 交易日历 | AkShare 新浪交易日历、本地市场日历规则 | 统一判断报价刷新窗口、F5 日期、Tab 数据日期 | `domains/market_calendar/calendar_service.py`、`core/market_calendar.py` |

### A 股情报源 Tab

| Tab | 原始数据来源 | 本地加工逻辑 | 主要代码 |
| --- | --- | --- | --- |
| 龙虎榜 | AkShare 东方财富龙虎榜封装：`stock_lhb_detail_em`、`stock_lhb_jgmmtj_em`、`stock_lhb_hyyyb_em`、`stock_lhb_stock_detail_em` | 按日期取每日原始榜单，合并多上榜原因，匹配机构、外资/知名席位，计算净买额和 30 日滚动关注池 | `ui/workers/lhb_worker.py`、`ui/tabs/lhb_tab.py` |
| 大宗交易 | AkShare 东方财富大宗交易：`stock_dzjy_mrmx`；交易日历用 `tool_trade_date_hist_sina` | 子进程隔离 AkShare 抓取，按外资席位关键字过滤，聚合对倒/买入/卖出方向，写入本地 JSON 缓存 | `ui/tabs/foreign_block_trade_tab.py` |
| 业绩预告/快报/财报 | AkShare 东方财富：`stock_yjyg_em`、`stock_yjkb_em`、`stock_yjbb_em`；同花顺利润表接口补充单季度口径 | 按公告日期过滤候选，去重，估算单季度环比，必要时用快报净利润回填，写入 SQLite 状态 | `domains/earnings/engine.py`、`ui/tabs/earnings_tab.py` |
| 基金持仓/QFII | 东方财富数据中心 `datacenter-web.eastmoney.com/api/data/v1/get`；东方财富基金档案 `FundArchivesDatas.aspx` | 同步 QFII 和睿远成长价值混合A，规范季度、主体、资金属性，生成快照和变动缓存，落到 SQLite | `domains/fund_holdings/sync.py`、`domains/fund_holdings/store.py`、`ui/tabs/fund_holdings_tab.py` |
| AI 产业链 | 本地产业链股票池、上下文映射和行业字典 | 作为跨 Tab 股票池和概念上下文来源，供大宗、基金、综合候选和个股上下文过滤/展示 | `core/ai_industry_chain_pool.py`、`ui/tabs/ai_industry_chain_tab.py` |
| 北美战报 | 兄弟项目“每日战报”的本地输出文件 | 读取最近战报产物并回填标的、细分板块、催化描述，再挂接实时/海外辅助行情 | `ui/services/na_daily_service.py`、`ui/tabs/na_daily_tab.py` |
| 综合候选 | VCP 扫描、关注池、龙虎榜、大宗、业绩、基金持仓、AI 产业链、北美战报等本地信号 | 汇总成候选池和个股上下文，不直接抓新数据源 | `ui/tabs/stock_candidate_tab.py`、`ui/workspaces/stock_context_service.py` |

### 海外 / 亚洲辅助链路

| 能力 | 原始数据来源 | 本地处理与缓存 | 主要代码 |
| --- | --- | --- | --- |
| 亚洲寡头历史 K 线 | 台湾 TWSE/TPEX、韩国 Naver、日本 Yahoo Japan、港股腾讯，必要时 yfinance 回退 | 从“每日战报”行业字典和本地覆盖表生成亚洲标的池，抓取约 250 日 OHLCV，写入 `data/Cache/asian_klines_latest.json` | `vcp/fetchers/asian_kline_fetcher.py`、`vcp/fetchers/asian_kline_cache.py` |
| 亚洲寡头盘中行情 | 台湾 TWSE MIS、韩国 Naver、港股腾讯、日本 Yahoo Japan，必要时 yfinance 回退 | 维护 `GLOBAL_ASIAN_RT_CACHE` 和 `asian_rt_latest.json`，对 Yahoo/yfinance 限流做冷却和降级提示 | `ui/tabs/asian_market_workers.py`、`app/services/asian_market_service.py` |
| 亚洲估值补充 | TWSE/TPEX 本益比、Naver PER、Yahoo Japan/Kabutan PER | 12 小时刷新一次，失败时沿用缓存 | `ui/tabs/asian_market_workers.py` |
| 全球寡头财报日历 | Company IR、JPX、TDnet、DART、KIND、MOPS、SEC 6-K、Nasdaq、Alpha Vantage、Yahoo Finance | 官方源优先，Yahoo Finance 标记为估算/冲突候选；结果合并到 SQLite/JSON 缓存并叠加到交易日历 | `domains/global_earnings_calendar/service.py`、`domains/global_earnings_calendar/providers/*` |

补充说明：

- 亚洲页在盘后缓存同步失败但旧缓存完整可用时，会继续沿用本地缓存并在页内明确提示缓存状态。
- 外部源抓取失败时，Tab 应优先展示“缓存数据 / 刷新失败 / 离线”等页面级状态，而不是把底层异常直接暴露给用户。
- 外资大宗、业绩异动、VCP 扫描等页共用统一页头和工具栏基线，README 中描述的页面行为以当前 UI 为准。

### 启动模式

- 冷启动：默认离线
- 启动后：`StartupOrchestrator` 异步探测网络
- 网络可用：自动切换在线模式并触发相关页面刷新
- 运行期间：按服务开关定时刷新全球寡头财报日历，失败时沿用本地缓存

这种设计的目标是：

- 冷启动快
- 无网也能打开本地缓存
- 联网能力恢复后尽量无感切换

### 服务开关与环境变量

运行期开关集中在 `infra/features/service_toggle_registry.py`，可以用 `VCP_TOGGLE_...` 环境变量临时覆盖。常用开关如下：

| 开关 key | 环境变量 | 默认 | 作用 |
| --- | --- | --- | --- |
| `central_quotes_service` | `VCP_TOGGLE_CENTRAL_QUOTES_SERVICE` | 开 | 中央 A 股实时报价轮询 |
| `silent_asian_sync` | `VCP_TOGGLE_SILENT_ASIAN_SYNC` | 开 | 启动后静默同步亚洲 K 线缓存 |
| `daily_global_earnings_calendar_sync` | `VCP_TOGGLE_DAILY_GLOBAL_EARNINGS_CALENDAR_SYNC` | 开 | 运行期间定时刷新全球寡头财报日历 |
| `workspace_auto_rt_monitor` | `VCP_TOGGLE_WORKSPACE_AUTO_RT_MONITOR` | 开 | 满足交易时段和数据条件时自动启动盘中监控 |
| `startup_history_cache_load` | `VCP_TOGGLE_STARTUP_HISTORY_CACHE_LOAD` | 开 | 启动时预加载本地历史行情缓存 |

其他重要环境变量：

- `ALPHAVANTAGE_API_KEY` / `ALPHA_VANTAGE_API_KEY`：全球财报日历的 Alpha Vantage 数据源。
- `OPENDART_API_KEY` / `DART_API_KEY`：韩国 DART 披露数据源。
- `SEC_USER_AGENT`：SEC EDGAR 请求头，默认使用项目内通用标识。
- `VCP_HUNTER_SETTINGS_ORGANIZATION` / `VCP_HUNTER_SETTINGS_APPLICATION`：覆盖 QSettings 命名空间，测试或多实例隔离时使用。
- `VCP_KLINE_WEBENGINE_PREFLIGHT` / `VCP_KLINE_HIDDEN_PREWARM`：K 线 WebEngine 预检与隐藏预热相关诊断开关。

## 数据与缓存目录

运行过程中会在 `data/` 下生成或维护这些内容：

- `data/Cache/`
  - RPS 预计算缓存
  - 盘中监控缓存
  - 亚洲市场缓存
  - 财务/股本缓存
  - 全球寡头财报日历缓存
- `data/Cache/parquet/market_data.parquet`
  - 全市场历史日线明细，配合 SQLite manifest 使用
- `data/vcp_hunter.db`
  - `kv_store`
  - `market_data_manifest`
  - 基金持仓原始表、快照表和变动缓存
  - 市场节假日、全球财报日历等 SQLite 数据
- `data/logs/`
  - 按天滚动的应用日志
- `data/crash_report.log`
  - `faulthandler` 写入的底层崩溃日志
- `tmp/runtime_health_*`、`tmp/perf_*`
  - 运行时健康、长稳、性能预算和 WebEngine 探针报告

这些文件属于运行时产物，不应作为业务源码理解。

## 开发与测试

### 安装开发工具

仓库当前提供单独的开发与审计依赖文件：

```powershell
python -m pip install -r requirements-dev.txt
```

Windows Python 3.14 的已验证依赖组合可以使用约束文件安装：

```powershell
python -m pip install -r requirements-dev.txt -c constraints-py314-windows.txt
```

### 运行测试

全量：

```powershell
pytest -q
```

定向回归示例：

```powershell
pytest tests/test_quote_snapshot.py -q
pytest tests/test_global_store_quote_merge.py -q
pytest tests/test_central_quotes_finance.py -q
pytest tests/test_workspace_quote_codes.py -q
pytest tests/test_global_earnings_calendar.py tests/test_trade_calendar.py -q
pytest tests/test_table_refresh_state.py tests/test_rt_table_model_incremental.py tests/test_stock_table_model_quotes.py -q
```

说明：

- `tests/conftest.py` 会统一创建 `QApplication`，避免 PyQt 测试直接崩溃
- 多数表格与行情链路已经有回归测试覆盖
- CI 当前覆盖 Python 3.14 快速审计、主测试、Windows smoke、Ruff、UTF-8、架构边界、启动编排、服务边界和工作区聚合等核心护栏，配置位于 `.github/workflows/ci.yml`

### 代码检查

```powershell
ruff check .
ruff format .
python scripts/check_utf8.py
```

### 运行时健康与 WebEngine 探针

提交前可以用短模式验证主窗口运行时健康、`stock_candidates / scan / watchlist / rt_monitor / lhb / fund_holdings` DataLineage、后台任务、Timer、事件订阅和 WebEngine 子进程预算：

```powershell
.\.venv\Scripts\python.exe scripts\runtime_health_stability_suite.py --mode short --tabs stock_candidates scan watchlist rt_monitor lhb fund_holdings --sample-output-dir tmp\runtime_health_samples_short --output tmp\runtime_health_stability_short.json
.\.venv\Scripts\python.exe scripts\perf_budget_check.py --runtime-health-report tmp\runtime_health_stability_short.json
```

用户环境问题先导出本地自检 JSON，覆盖 Python、PyQt6 / WebEngine、QtWebEngine preflight、通达信 `vipdoc`、关键缓存、psutil/runtime diagnostics、当前 Git 提交和应用版本：

```powershell
.\.venv\Scripts\python.exe scripts\runtime_env_self_check.py --output tmp\runtime_env_self_check.json
```

夜间或人工长稳验证使用 30/60 分钟 soak；长模式会周期性导出 runtime health sample，并在聚合报告中输出 `trend` 和 `budget_trend`：

```powershell
.\.venv\Scripts\python.exe scripts\runtime_health_stability_suite.py --mode soak30 --native-qt --fail-on-budget --sample-every-seconds 60 --sample-output-dir tmp\runtime_health_samples_soak30 --output tmp\runtime_health_soak30.json
.\.venv\Scripts\python.exe scripts\runtime_health_stability_suite.py --mode soak60 --native-qt --fail-on-budget --sample-every-seconds 60 --sample-output-dir tmp\runtime_health_samples_soak60 --output tmp\runtime_health_soak60.json
```

K 线 WebEngine 生命周期 smoke 需要原生 Qt / 可视桌面环境；默认 offscreen 会输出跳过原因和手动命令，避免把不稳定 WebEngine 自动化塞进 CI：

```powershell
.\.venv\Scripts\python.exe scripts\kline_webengine_lifecycle_smoke.py --native-qt --cycles 5 --output tmp\kline_webengine_lifecycle_smoke.json
.\.venv\Scripts\python.exe scripts\perf_budget_check.py --kline-lifecycle-report tmp\kline_webengine_lifecycle_smoke.json
```

Post-F5 网络同步完整回归需要覆盖情报源刷新路径；如果只做核心行情链路隔离诊断，可保留 round5 默认隔离参数，但完整门禁应显式关闭隔离：

```powershell
.\.venv\Scripts\python.exe scripts\perf_round5_probe.py --no-isolate-info-source-refresh --stub-quote-provider --output tmp\perf_round5_full.json
.\.venv\Scripts\python.exe scripts\perf_budget_check.py --round5-report tmp\perf_round5_full.json
```

### pre-commit

```powershell
pre-commit install
pre-commit run --all-files
```

仓库当前启用了：

- `check-utf8`
- `ruff-check`
- `ruff-format`

### Windows 打包与 UI 截图审计

打包脚本会优先使用 `.venv\Scripts\python.exe`，并把图标和 `assets/` 一起带入 PyInstaller：

```powershell
.\scripts\build_windows.ps1 -DryRun
.\scripts\build_windows.ps1
```

UI 审计截图脚本用于主窗口、Tab、命令面板、对话框和可选 K 线窗口的视觉回归：

```powershell
python scripts/capture_ui_audit_screenshots.py --offscreen --strict
```

## 维护建议

如果你准备继续沿当前架构演进，建议保持下面几条不变：

- 新 Tab 尽量继承 `BaseStockTab`
- 新的盘中表格字段尽量接入 `domains/quotes/*` 稳定入口
- 高频 UI 刷新继续走中央广播链，而不是各页单独造轮子
- 需要跨页共享的运行时状态，优先进入 `GlobalStore`
- 需要跨 Tab 汇总或导航的能力，优先扩展 `ui/workspaces/tab_capabilities.py` 和 `WorkspaceFacade`
- 涉及市场时间判断时，统一走 `MarketCalendar`
- 涉及外部 HTTP 或子进程调用时，沿用 `infra/http_safety.py` 与 `infra/tasks/process_runner.py` 的边界约束
- 新增外部数据源时，先在 `domains/` 或 `infra/` 建立“原始抓取 -> 规范化 -> 缓存/持久化”的独立链路，再让 Tab 读取本地结果
- 新数据页尽量实现 `get_data_lineage()`，至少说明来源、缓存、是否联网、是否降级、更新时间和行数
- 新的可选后台能力先注册到 `service_toggle_registry`，不要把布尔开关散落在 UI 或启动编排器里
- 大规模明细优先落 Parquet；索引、manifest、状态和变动缓存落 SQLite；不要把全市场日线明细塞进 SQLite
- `core/`、`vcp/`、`earnings/` 主要是兼容门面，新真实实现优先进入 `app/`、`domains/` 或 `infra/`
- 修改中文文档、QSS 或脚本后，至少运行一次 `python scripts/check_utf8.py ...`

## 已知边界

- 这是本地桌面终端，不是 Web 服务，也没有部署到云端的标准流程
- 核心能力建立在本地通达信数据目录存在的前提上
- 海外/亚洲页面的数据质量和稳定性受外部源影响
- 全球寡头财报日历会优先合并已确认事件和可用公开源；无外部密钥时部分事件只能作为估算或待确认
- 东方财富妙想 skill 当前不是本项目的主数据接口；如后续接入，应作为补充查询层或人工验证层单独标注血缘
- Codex 投研跳转依赖本机已注册 `codex://` 深链接，并默认打开 `D:\vcp_hunter\产业链投研`
- 某些旧文件注释中可能仍存在历史术语，但 README 已以当前代码为准

## 许可证

Private / Internal Use Only.

## 架构治理索引

- Technical architecture: `docs/technical-architecture.md`
- Module owner registry: `docs/module-owners.md`
