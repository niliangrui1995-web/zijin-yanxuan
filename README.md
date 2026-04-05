# 紫金研选量化终端

> VCP (Volatility Contraction Pattern) 自动化选股系统 — 基于通达信本地日线数据

![Python 3.10+](https://img.shields.io/badge/Python-3.10+-blue)
![PyQt6](https://img.shields.io/badge/UI-PyQt6-green)
![License](https://img.shields.io/badge/License-Private-red)

---

## 功能概览

| **模块** | **功能** | **说明** |
|------|------|------|
| **F5 全量扫描** | VCP 形态选股 | 按 RPS 强度 + 三高点收缩区间 + 均线粘合逻辑筛选 |
| **盘中监控** | 实时突破检测 | 预构建待突破池 → 轻量 rt_quick_check（~0.01ms/只） |
| **独立关注池** | 自治高压监控 | 彻底脱离本地静态缓存，支持盘中独立高频报价与分发 |
| **亚洲寡头监控** | 全球核心资产 | 离线静态极速加载 + CF代理直连/盘后智能防呆静默拉取 |
| **北美战报** | 美股映射监控 | 定期拉取美股核心标的与中概行情，提供 A 股映射参考 |
| **外资大宗交易** | QFII 席位跟踪 | 实时监控外资专属席位大宗交易动向，解析超额资金行为 |
| **AI 产业链** | 核心概念追踪 | 结构化追踪 AI 产业链核心标的异动与板块共振 |
| **AI 诊股** | Kimi 联网分析 | 自动联网检索利好/利空信息并结构化输出 |
| **多终端联动** | 极速跳转直达 | 右键一键穿透打开并聚焦已有「通达信/东方财富」窗口实现联动 |
| **K线图** | 专业级图表 | 彭博终端风格、高点/区间标注、技术指标叠加 |
| **板块 RPS** | 热点板块排名 | 解析通达信本地板块文件，计算多周期板块 RPS |

---

## 项目结构

```
紫金研选/
├── vcp_hunter_qt.pyw              # 应用程序入口（双击启动）
├── requirements.txt               # Python 依赖声明
├── .gitignore                     # Git 忽略规则
├── bull_icon.ico                  # 应用图标
│
├── core/                          # 核心基础设施层
│   ├── __init__.py
│   ├── event_bus.py               # 全局事件总线（PyQt 信号中转）
│   ├── task_manager.py            # 统一异步任务调度器（替代 threading.Thread）
│   ├── logger.py                  # 标准化日志系统（替代 print）
│   └── throttler.py               # 信号防抖限流器 (抵御高频刷新卡顿)
│
├── vcp/                           # 核心引擎层（策略 + 数据）
│   ├── __init__.py
│   ├── constants.py               # 全局常量、配色、策略参数
│   ├── models.py                  # 数据类（VCPParams）
│   ├── utils.py                   # 辅助工具（拼音搜索、时间判断、通达信路径）
│   ├── engine.py                  # VCP 策略中台（选股、评分、RPS）
│   ├── data_provider.py           # 数据中台（通达信/pytdx 数据获取与缓存）
│   ├── ai_service.py              # AI 诊断服务（Kimi API 封装）
│   ├── sector.py                  # 板块数据管理与板块 RPS 计算
│   └── polars_engine.py           # 高性能加速引擎（numpy/Polars 优化）
│
├── ui/                            # UI 层（PyQt6）
│   ├── __init__.py
│   ├── main_window_qt.py          # 主窗口外壳（UI 布局 + 信号转发）
│   ├── kline_window_qt.py         # K 线图窗口（彭博终端风格）
│   ├── splash_screen.py           # 启动画面
│   ├── components/                # 通用 UI 组件（标题栏、动画卡片、Toast、呼吸灯）
│   ├── workers.py                 # 后台 QThread（ScanWorker、RtScanWorker）
│   ├── theme.py                   # 主题色常量（涨跌着色、状态色）
│   ├── models/                    # UI 数据模型 (Qt Model)
│   ├── viewmodels/                # UI 视图模型 (ViewModel)
│   │
│   ├── tabs/                      # Tab 页组件（全部继承 BaseStockTab）
│   │   ├── __init__.py
│   │   ├── base_stock_tab.py      # Tab 基类（通达信跳转、着色、日志）
│   │   ├── scan_tab.py            # F5 全量扫描结果
│   │   ├── rt_monitor_tab.py      # 盘中实时监控
│   │   ├── watchlist_tab.py       # 关注池管理
│   │   ├── na_daily_tab.py        # 北美战报
│   │   ├── asian_market_tab.py    # 亚洲寡头监控
│   │   ├── foreign_block_trade_tab.py # 外资大宗交易监控
│   │   ├── ai_tracker_tab.py      # AI 追踪
│   │   └── log_tab.py             # 系统运行日志
│   │
│   ├── panels/                    # 侧边面板组件
│   │   ├── __init__.py
│   │   └── ai_diag_panel.py       # AI 诊断面板
│   │
│   ├── mixins/                    # 功能 Mixin（从 MainWindow 抽离）
│   │   ├── __init__.py
│   │   └── data_cache_mixin.py    # 数据缓存操作（F5/RPS/RT 缓存）
│   │
│   └── styles/                    # 样式管理
│       ├── __init__.py
│       └── global_qss.py          # 全局 QSS 样式表
│
├── data/                          # 数据目录（运行时生成，git 忽略）
│   ├── Cache/                     # 缓存文件（pkl/parquet/json）
│   ├── Export/                    # 导出报告
│   └── logs/                      # 运行日志
│
├── 外资动向/                      # 外资席位跟踪与验证工具
│
├── 外资研究/                      # 外资大宗交易高阶量化研究与回测
│
└── docs/                          # 文档
    ├── 项目全景介绍文档.md
    └── *.png                      # UI 截图、流程图
```

---

## 快速开始

### 1. 环境要求

- **Python 3.10+**
- **通达信客户端**（本地 K 线数据源）

### 2. 安装依赖

```bash
pip install -r requirements.txt
```

> *注：跳转东方财富等自动化剪贴板/打字填表功能由于需要操作系统的 GUI 层，需确保已正确安装 `pyautogui`。*

### 3. 配置通达信路径

在通达信安装目录或项目根目录创建 `vcp_tdx_config.json`：

```json
{
  "tdx_vipdoc_root": "D:\\HT\\vipdoc"
}
```

程序会自动查找以下候选路径：
1. `D:\vcp_qt\vcp_tdx_config.json`
2. `D:\HT\vcp_tdx_config.json`
3. 项目根目录下的 `vcp_tdx_config.json`

### 4. 配置 AI 诊股（可选）

程序首次运行时会自动在 `data/Cache/ai_diag_config.json` 中创建配置文件。
你也可以通过环境变量设置：

```bash
set KIMI_API_KEY=your-api-key-here
```

### 5. 启动程序

```bash
pythonw vcp_hunter_qt.pyw
```

或双击 `vcp_hunter_qt.pyw` 文件启动。

---

## 核心技术栈

| 组件 | 技术 | 说明 |
|------|------|------|
| UI 框架 | PyQt6 (MVC) | 采用 QTableView+StockTableModel 标准MVC架构、极致紫金全局主题、悬浮玻璃拟态 |
| 数据源 | 通达信本地 `.day` + pytdx | 本地优先、联网增量补全 |
| 行情接口 | pytdx (通达信协议) | 动态测速池、多线程同步 |
| 加速引擎 | numpy + Polars | 向量化 pct_change/rank、Parquet 缓存 |
| AI 诊股 | Moonshot (Kimi) API | 内置联网搜索、利好/利空结构化输出 |
| 系统 RPA | pyautogui / win32gui | 无缝桥接外部桌面端软件（通达信/东方财富进程激活与注入） |
| 板块分析 | 通达信 tdxhy.cfg + infoharbor_block.dat | 行业+概念板块 RPS |
| 任务调度 | core/task_manager (QThreadPool) | 统一后台任务管理 |
| 日志系统 | core/logger (RotatingFileHandler) | 文件+控制台双输出 |
| 事件总线 | core/event_bus (PyQt Signal) | 组件间解耦通信 |

---

## 数据流

```
通达信本地 .day 文件
        │
        ▼
  TdxDataProvider（data_provider.py）
  ├── 本地读取 → read_tdx_day_file()
  ├── 联网增量 → pytdx API
  └── 前复权    → gbbq 股本变迁数据
        │
        ▼
  VCPEngine（engine.py）
  ├── 技术指标 → SMA50/150/200, ATR, MACD, RSI, 布林带
  ├── RPS 矩阵 → 50/120/250 日相对强度排名
  ├── VCP 形态 → 三高点区间 + 弹性峰计算
  └── 综合评分 → 均线/量价/突破状态/板块 RPS
        │
        ▼
  MainWindowQT（main_window_qt.py）
  ├── F5 全量扫描结果 → 表格展示
  ├── 盘中监控信号   → 实时刷新
  └── K线图/AI诊断   → 详情窗口
```

---

## 盘中实时数据流与多标签页联动

系统在盘中（交易时段）实现了高性能的 **非阻塞式实时双轨数据驱动**，确保多面板丝滑刷新且主线程不卡顿：

1. **核心驱动引擎 (`RtScanWorker`)：** 按照自定义间隔（如30秒），高频轮询待突破池与自选池（关注池），在后台直接完成行情拉取与内存微计算。
2. **全局总线广播 (`Event Bus`)：** 计算结果被打成快照，通过 `sig_data_updated("rt_quotes_refreshed")` 全局无差别广播发射。
3. **渲染防抖护城河 (`Throttler`)：** 高频信号被 `SignalThrottler` 组件限流，保障主 UI 线程的平滑渲染，抵御爆量并发假死。
4. **多级自适应刷新：**
   - **🟢 盘中监控 & 关注池：** 监听公屏广播。数据命中即触发局部变色与幅度重绘，毫无卡顿感。
   - **🟢 北美战报 & AI 产业链：** 享受**双轨制**刷新。既监听公屏广播借势更新，又启动独立后台协程定期拉取专有板块标的数据。
   - **🟡 亚洲寡头监控：** 首创**“动静分离式架构”**。冷启动秒开读取 250 日长序列静态缓存，盘中仅在 RAM 内存测算毫秒级跳动；同时自带休市/漏抓防呆回溯算法（遇到周末也会智能倒退至周五比对校验），一旦侦测到日线收盘断层，隐形协程自动化发起极速补漏，无损硬盘寿命。
   - **⏹ 全盘扫描 (`ScanTab`)：** 刻意设计为**防乱跳静态锚点**。保留每日首次 F5 全盘体检的快照结果，方便盘中及盘后沉浸式心流复盘。

---

## 性能优化

项目包含三层性能优化：

1. **numpy 向量化**：`pct_change` + `rank` 替代 pandas，加速 3-5x
2. **Parquet 缓存**：替代 pickle，读取速度提升 2-3x
3. **增量 RPS**：磁盘缓存价格矩阵，二次运行自动增量复用

---

## 许可证

本项目为私有项目，未经授权不得分发。
