# 紫金研选量化终端

Windows 优先的 PyQt6 桌面看盘与选股工具，围绕 A 股 VCP（Volatility Contraction Pattern）扫描、盘中监控、关注池联动和多市场辅助观察构建。

当前代码基于本地通达信日线数据运行，盘中实时行情通过东方财富 HTTP 链路获取，并在必要时回退到新浪批量报价；海外和亚洲辅助页面使用独立数据抓取链路。

> 注意
>
> 当前仓库已经移除 `AI 诊股`、`AI 追踪`、`ai_service.py`、`ai_diag_panel.py` 等旧模块。本文档仅描述仓库当前实际存在的架构和代码。

![Python 3.10+](https://img.shields.io/badge/Python-3.10%2B-blue)
![PyQt6](https://img.shields.io/badge/UI-PyQt6-green)
![Platform](https://img.shields.io/badge/Platform-Windows-lightgrey)
![License](https://img.shields.io/badge/License-Private-red)

## 当前功能面

当前 `ClassicWorkspace` 装配了 9 个主 Tab：

| 页面 | 模块 | 说明 |
| --- | --- | --- |
| 关注池 | `ui/tabs/watchlist_tab.py` | 自选股票池，联动实时现价、涨幅、市值、催化与专题信息 |
| 龙虎榜 | `ui/tabs/lhb_tab.py` | 20 日滚动龙虎榜关注池，带上榜次数、最近上榜、净买额等字段 |
| 北美战报 | `ui/tabs/na_daily_tab.py` | 从战报产出文件中回填标的，并挂接实时行情 |
| 亚洲寡头 | `ui/tabs/asian_market_tab.py` | 多市场亚洲龙头/寡头跟踪，带本地缓存与盘中刷新 |
| 盘中监控 | `ui/tabs/rt_monitor_tab.py` | 盘中轮询待突破池，展示实时突破状态 |
| 大宗交易 | `ui/tabs/foreign_block_trade_tab.py` | 外资席位相关大宗交易监控与过滤 |
| 业绩异动 | `ui/tabs/earnings_tab.py` | 业绩预告、快报、财报高增跟踪 |
| VCP 扫描 | `ui/tabs/scan_tab.py` | 全市场 VCP 静态扫描结果页 |
| 系统日志 | `ui/tabs/log_tab.py` | 统一查看运行日志与后台任务状态 |

除此之外，还有两个贯穿全局的能力：

- K 线详情窗口：`ui/kline_window_qt.py`
- 中央行情广播与表格快照合并：`ui/workers/central_quotes_worker.py` + `core/global_store.py`

## 当前交互基线

当前仓库的主工作区已经完成一轮统一化交互收口，主要约束如下：

- 主要数据页统一采用页面级状态反馈，明确区分 `加载中 / 最新数据 / 缓存数据 / 刷新失败 / 离线`
- 各 Tab 页头统一回答“当前看的是什么数据、筛选是否生效、数据何时更新”
- 通用工具栏已经按窄宽度场景重排，优先保证筛选控件、状态摘要和动作按钮不互相挤压
- 亚洲页在远端抓取失败时会明确标记“沿用缓存”，而不是把底层抓取异常直接外泄给用户
- K 线窗口和主窗口关键按钮补齐了 tooltip 与可访问性命名，便于悬停识别和后续维护

## 技术栈

- 语言：Python 3.10+
- UI：PyQt6、PyQt6-WebEngine、QSS
- 表格模型：`QTableView` + `QAbstractTableModel`
- 数据处理：pandas、numpy、polars、pyarrow
- 拼音辅助：`pypinyin`
- A 股本地数据：通达信 `vipdoc` 日线文件
- A 股实时行情：东方财富 HTTP，异常时回退新浪批量报价
- 财务/股本补充：东方财富接口
- 海外/亚洲辅助数据：AkShare、yfinance、`curl_cffi`
- 任务调度：`infra/tasks/task_scheduler.py` + `core/background_job_runner.py`
- 全局通信：`core/event_bus.py`
- 日志：`core/logger.py`
- 配置持久化：`infra/settings` + `core/app_config.py`
- 本地缓存：`data/Cache/*.json`、`data/vcp_hunter.db`

## 架构概览

### 1. 启动链路

```text
vcp_hunter_qt.pyw
  -> MainWindowQT
  -> TdxDataProvider(offline=True)
  -> VCPEngine
  -> ClassicWorkspace
  -> StartupOrchestrator
```

关键点：

- 入口文件是 `vcp_hunter_qt.pyw`，负责单实例限制、崩溃日志和 `QApplication` 初始化。
- 程序默认先以“离线优先”启动，优先保证冷启动可用。
- `StartupOrchestrator` 在启动后异步完成：
  - 本地缓存恢复
  - RPS 预计算缓存恢复
  - 亚洲市场 JSON 缓存静默同步
  - 网络探测与在线模式切换

### 2. 工作区与页面装配

- 主窗口外壳：`ui/main_window_qt.py`
- 工作区装配：`ui/workspaces/classic_workspace.py`
- 当前仅装配 `ClassicWorkspace`
- 各 Tab 大多继承 `ui/tabs/base_stock_tab.py`，共享：
  - 右键菜单
  - K 线跳转
  - 表格行情刷新
  - 市值补齐
  - 工具栏构建

### 3. 行情与表格链路

当前仓库已经把盘中表格的 `现价 / 涨幅 / 市值` 统一到同一条实时链路：

```text
CentralQuotesService
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
│  ├─ quote_snapshot.py
│  ├─ runtime_env.py
│  └─ task_manager.py
├─ app/                          # 应用编排层（bootstrap / use cases / services）
├─ domains/                      # 领域服务稳定入口
│  ├─ earnings/
│  ├─ fund_holdings/
│  ├─ market_calendar/
│  ├─ quotes/
│  ├─ scan/
│  └─ watchlist/
├─ earnings/                     # 业绩领域兼容壳
├─ infra/                        # 基础设施适配层
│  ├─ market_data/
│  ├─ navigation/
│  ├─ settings/
│  ├─ storage/
│  └─ tasks/
├─ ui/                           # PyQt6 界面层
│  ├─ main_window_qt.py
│  ├─ kline_window_qt.py
│  ├─ startup_orchestrator.py
│  ├─ workspaces/
│  ├─ components/
│  ├─ models/
│  ├─ styles/
│  ├─ tabs/
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
├─ docs/                         # 补充说明文档
└─ data/                         # 运行时生成的数据、缓存和日志
```

## 当前分层结论

- `app/` 只依赖宿主公开接口，不再直接 import `ui.*` 具体实现。
- `domains/` 提供 `scan / earnings / quotes / watchlist / fund_holdings / market_calendar` 的稳定入口。
- `infra/` 承载 `market_data / settings / navigation / storage / tasks` 等外部边界适配。
- `ui/` 主要负责 Qt 装配、页面状态和事件接线。
- `core/` 保留事件总线、兼容门面和跨层共享能力，不再承载新增真实领域实现。

## 运行要求

推荐环境：

- Windows 10 / 11
- Python 3.10 或 3.11（64 位）
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
py -3.10 -m venv .venv
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
- `pypinyin`
- `akshare`
- `yfinance`
- `curl_cffi`
- `pywin32`

### 4. 配置通达信数据目录

推荐在项目根目录放置 `vcp_tdx_config.json`：

```json
{
  "tdx_vipdoc_root": "D:\\HT\\vipdoc"
}
```

程序启动后会用这个目录读取本地日线数据与名称映射。

### 5. 启动程序

生产式启动：

```powershell
.\.venv\Scripts\pythonw.exe .\vcp_hunter_qt.pyw
```

调试式启动（会保留控制台）：

```powershell
.\.venv\Scripts\python.exe .\vcp_hunter_qt.pyw
```

## 数据源与运行模式

### A 股主链路

- 历史 K 线：本地通达信 `vipdoc`
- 盘中实时 quote：东方财富 HTTP
- 异常回退：新浪批量报价
- 股本/财务补充：东方财富 finance 数据

### 海外 / 亚洲辅助链路

- 亚洲市场历史与缓存：`vcp/fetchers/asian_kline_fetcher.py`
- 亚洲市场盘中辅助行情：`ui/tabs/asian_market_workers.py`
- 北美 / 海外辅助数据：AkShare、yfinance

补充说明：

- 亚洲页在盘后缓存同步失败但旧缓存完整可用时，会继续沿用本地缓存并在页内明确提示缓存状态
- 外资大宗、业绩异动、VCP 扫描等页共用统一页头和工具栏基线，README 中描述的页面行为以当前 UI 为准

### 启动模式

- 冷启动：默认离线
- 启动后：`StartupOrchestrator` 异步探测网络
- 网络可用：自动切换在线模式并触发相关页面刷新

这种设计的目标是：

- 冷启动快
- 无网也能打开本地缓存
- 联网能力恢复后尽量无感切换

## 数据与缓存目录

运行过程中会在 `data/` 下生成或维护这些内容：

- `data/Cache/`
  - RPS 预计算缓存
  - 盘中监控缓存
  - 亚洲市场缓存
  - 财务/股本缓存
- `data/vcp_hunter.db`
  - 市场节假日缓存等 SQLite 数据
- `data/logs/`
  - 按天滚动的应用日志
- `data/crash_report.log`
  - `faulthandler` 写入的底层崩溃日志

这些文件属于运行时产物，不应作为业务源码理解。

## 开发与测试

### 安装开发工具

仓库当前没有单独的开发依赖文件，建议手动安装：

```powershell
python -m pip install pytest ruff pre-commit
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
```

说明：

- `tests/conftest.py` 会统一创建 `QApplication`，避免 PyQt 测试直接崩溃
- 多数表格与行情链路已经有回归测试覆盖

### 代码检查

```powershell
ruff check .
ruff format .
```

### pre-commit

```powershell
pre-commit install
pre-commit run --all-files
```

仓库当前启用了：

- `ruff-check`
- `ruff-format`

## 维护建议

如果你准备继续沿当前架构演进，建议保持下面几条不变：

- 新 Tab 尽量继承 `BaseStockTab`
- 新的盘中表格字段尽量接入 `domains/quotes/*` 稳定入口
- 高频 UI 刷新继续走中央广播链，而不是各页单独造轮子
- 需要跨页共享的运行时状态，优先进入 `GlobalStore`
- 涉及市场时间判断时，统一走 `MarketCalendar`

## 已知边界

- 这是本地桌面终端，不是 Web 服务，也没有部署到云端的标准流程
- 核心能力建立在本地通达信数据目录存在的前提上
- 海外/亚洲页面的数据质量和稳定性受外部源影响
- 某些旧文件注释中可能仍存在历史术语，但 README 已以当前代码为准

## 许可证

Private / Internal Use Only.

## Architecture Governance

- Module owner registry: `docs/module-owners.md`
