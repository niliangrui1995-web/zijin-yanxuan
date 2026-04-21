# 紫金研选 Architecture Baseline 2026-04-20

## 目标

本基线用于定义 2026-04-20 起仓库允许的主干分层、跨层协作方式和 CI 闸门。后续演进默认以此为约束，不再接受“先从 UI 里摸进去、以后再整理”的回退式改动。

## 当前主干分层

### `ui/`

- 负责 Qt 组件、页面状态展示、交互事件接线。
- 运行时业务依赖统一经 `app/` 暴露的稳定入口接入；`ui/` 内部模块之间可直接协作，通用日志等基础工具可保留轻量依赖。
- 不允许直接 import `domains/*`、`infra/*`、`vcp/*`，以及 `core.app_config`、`core.background_job_runner`、`core.domain_events`、`core.ui_signals`、`core.market_calendar` 这类运行时兼容入口。
- 不允许直接管理系统进程、窗口句柄、裸 `QSettings`、跨 tab 私有方法。

### `app/`

- 负责跨页面、跨用例的应用服务编排。
- 当前关键入口：`app/services/kline_open_service.py`、`app/services/ui_runtime_service.py`、`app/services/runtime_services.py`、`app/services/scan_runtime_service.py`。
- 只组织请求上下文，不直接持有 Qt 控件生命周期。

### `domains/`

- 负责领域规则与稳定入口，当前包含 `scan / earnings / quotes / watchlist / fund_holdings / market_calendar / runtime`。
- `domains/*` 由 `app/*` 统一编排接入；UI 不直接 import 领域模块。
- 领域事件真实实现位于 `domains/runtime/domain_events.py`。

### `infra/`

- 负责系统边界适配：`settings / navigation / tasks / market_data / storage`。
- 与外部系统、操作系统、进程/窗口自动化相关逻辑必须优先落在这里。

### `ui/signals`

- 承载 UI 导航与任务进度等界面信号。
- 真实实现位于 `ui/signals/ui_signal_bus.py`。

## 兼容层与过渡层

### `core/`

- 负责公共工具与历史兼容门面。
- `core/app_config.py` 是版本化设置仓储的兼容入口，不再是自由扩展的状态黑洞。
- `core/task_manager.py`、`core/market_calendar.py`、`core/quote_snapshot.py`、`core/quote_dispatcher.py`、`core/domain_events.py`、`core/ui_signals.py` 仅保留兼容导出，不再承载新增真实实现。

### `vcp/`

- 负责 VCP 聚合门面与兼容入口。
- 真实扫描规则已迁入 `domains/scan`，行情 provider 子服务已迁入 `infra/market_data`。
- 不允许直接依赖 UI 信号或任务编排细节。

## 已收口的关键边界

- Settings 统一经 `infra/settings` + `core/app_config` 访问。
- 终端/系统进程调用统一经 `infra/navigation`、`infra/tasks` 暴露。
- `ApplicationBootstrap` 和 `WindowCommandService` 已改为依赖主窗口公开接口，不再直接 import UI 具体实现或私有方法。
- `ClassicWorkspace` 不再向外暴露 tab 私有细节作为协作契约。
- `ClassicWorkspace` 的表格遍历、跨页定位、刷新编排已下沉到 `workspace_facade + workspace_*_service`。
- `MainWindowQT` 的命令面板和 K 线打开链路改为调用 workspace/app service 公共入口。
- 观察池雷达与实时报价汇总改为通过 `WorkspaceFacade` / capability 协议聚合，不再摸 tab 私有表格或 `model.row_data`。
- 关注池、基金持仓、业绩调度、报价快照等主路径已经通过 `app/services/ui_runtime_service.py`、`app/services/*` 收口，再由 app 层委托 `domains/*` 与 `infra/*`。
- 领域事件与 UI 信号的真实实现分别迁入 `domains/runtime/domain_events.py` 与 `ui/signals/ui_signal_bus.py`；`core/event_bus.py`、`core/domain_events.py`、`core/ui_signals.py` 仅保留兼容桥接。
- `vcp/data_provider.py`、`vcp/engine.py` 已退化为兼容 alias shim，真实实现分别落在 `infra/market_data/tdx_data_provider.py` 与 `app/services/scan_engine_facade.py`。

## 禁止事项

- `ui/` 直接 import `subprocess`、`pyautogui`、`win32con`、`win32gui`
- `ui/` 直接 import `core.event_bus`、`core.task_manager`
- `ui/` 直接 import `domains/*`、`infra/*`、`vcp/*`
- `ui/` 直接 import `core.app_config`、`core.background_job_runner`、`core.domain_events`、`core.ui_signals`、`core.market_calendar`
- `ui/` 直接 import `QSettings`
- 主窗口直接调用 tab 私有方法，如 `_show_scan_settings`、`_manual_refresh`
- 新增 `workspace.tab_xxx._private_call()` 形式的跨页访问

## 当前治理闸门

- `tests/test_architecture_boundaries.py`
- `tests/test_app_config.py`
- `tests/test_workspace_quote_codes.py`
- `tests/test_kline_open_service.py`
- `tests/test_main_window_shell.py`
- `tests/test_provider_services.py`
- `tests/test_engine_services.py`
- `tests/test_background_job_runner.py`
- `tests/test_application_bootstrap.py`
- `tests/test_domain_entrypoints.py`
- `tests/test_event_bus_layers.py`
- `tests/test_market_data_ports.py`
- `tests/test_task_manager.py`
- `scripts/check_utf8.py`

## 后续演进要求

1. 新增跨页面能力时，先判断应落在 `app/services` 还是 `ui/workspaces/workspace_facade.py`。
2. 新增持久化键时，必须同步更新 `docs/qsettings-key-registry.md`。
3. 新增边界规则时，优先写成 `tests/test_architecture_boundaries.py` 中的自动化检查。
4. 兼容桥仅用于历史入口兜底；新增真实调用方应先落到 `app/*` 统一编排，再由 app 委托 `domains/*` 或 `infra/*`。
5. 迁移完成的兼容桥必须在同一批或下一批任务内删除，禁止长期保留。
