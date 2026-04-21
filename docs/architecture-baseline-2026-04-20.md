# 紫金研选 Architecture Baseline 2026-04-20

## 目标

本基线用于定义 2026-04-20 起仓库允许的主干分层、跨层协作方式和 CI 闸门。后续演进默认以此为约束，不再接受“先从 UI 里摸进去、以后再整理”的回退式改动。

## 当前分层

### `ui/`

- 负责 Qt 组件、页面状态展示、交互事件接线。
- 允许依赖 `app/`、`core/`、`infra/` 暴露出的稳定入口。
- 不允许直接管理系统进程、窗口句柄、裸 `QSettings`、跨 tab 私有方法。

### `app/`

- 负责跨页面、跨用例的应用服务编排。
- 当前新增服务：`app/services/kline_open_service.py`。
- 只组织请求上下文，不直接持有 Qt 控件生命周期。

### `core/`

- 负责应用级公共能力、事件总线、兼容门面、全局配置门面。
- `core/app_config.py` 是版本化设置仓储的兼容入口，不再是自由扩展的状态黑洞。
- `core/task_manager.py`、`core/market_calendar.py`、`core/quote_snapshot.py`、`core/quote_dispatcher.py` 仅保留兼容导出，不再承载新增真实实现。

### `domains/`

- 负责领域规则与稳定入口，当前包含 `scan / earnings / quotes / watchlist / fund_holdings / market_calendar`。
- UI 和 app 应优先依赖 `domains/*` 暴露的稳定契约，而不是继续摸旧兼容层。

### `infra/`

- 负责系统边界适配：`settings / navigation / tasks / market_data / storage`。
- 与外部系统、操作系统、进程/窗口自动化相关逻辑必须优先落在这里。

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
- 观察池雷达与实时报价汇总改为通过 `get_tab()` + tab 公共方法聚合。
- 关注池、基金持仓、业绩调度、报价快照等主路径已经切换到 `domains/*` 稳定入口。

## 禁止事项

- `ui/` 直接 import `subprocess`、`pyautogui`、`win32con`、`win32gui`
- `ui/` 直接 import `core.event_bus`、`core.task_manager`
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
4. 兼容桥仅用于历史入口兜底；新增真实调用方必须直接依赖 `domains/*` 或 `infra/*` 稳定入口。
5. 迁移完成的兼容桥必须在同一批或下一批任务内删除，禁止长期保留。
