# Architecture Upgrade Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** 按《紫金研选架构升级落地方案》完成关键热区的分层收口、边界治理和旧路径清理，让项目进入可持续演进的模块化单体状态。

**Architecture:** 先治理边界，再处理搬迁。优先落地 `infra/settings`、`infra/navigation`、`infra/tasks`、边界测试和治理资产，再继续压缩 `ClassicWorkspace` / `MainWindowQT` / `BaseStockTab` 的职责。迁移期间允许极短期兼容桥接，但每完成一段必须删旧路径。

**Tech Stack:** Python 3.10+, PyQt6, pytest, Ruff, GitHub Actions

---

### Task 1: Settings 统一收口

**Files:**
- Create: `infra/settings/__init__.py`
- Create: `infra/settings/settings_repository.py`
- Create: `infra/settings/settings_schema.py`
- Modify: `core/app_config.py`
- Modify: `ui/theme.py`
- Modify: `ui/tabs/asian_market_tab.py`
- Modify: `ui/tabs/rt_monitor_tab.py`
- Modify: `ui/tabs/scan_tab.py`
- Test: `tests/test_app_config.py`
- Test: `tests/test_architecture_boundaries.py`

**Step 1: 写失败用例**
- 为 `SettingsSchemaVersion`、legacy scope 迁移、UI 禁止裸 `QSettings` 增加测试。

**Step 2: 跑测试确认失败**
- Run: `pytest tests/test_app_config.py tests/test_architecture_boundaries.py -q`

**Step 3: 落地仓储**
- 引入 `SettingsRepository`、`SettingsSchemaVersion`、`SettingsMigrator`
- `core.app_config` 改成兼容层，内部统一委托到 repository
- 所有 UI 裸 `QSettings` 改成 `app_config.section(...)` 或 settings repository

**Step 4: 跑测试确认通过**
- Run: `pytest tests/test_app_config.py tests/test_architecture_boundaries.py -q`

### Task 2: Navigation 与进程调用下沉

**Files:**
- Create: `infra/navigation/__init__.py`
- Create: `infra/navigation/external_terminal_navigator.py`
- Create: `infra/tasks/__init__.py`
- Create: `infra/tasks/process_runner.py`
- Modify: `ui/tabs/base_stock_tab.py`
- Delete: `ui/tabs/quote_terminal_launcher.py`
- Modify: `ui/components/notification_service.py`
- Modify: `ui/tabs/foreign_block_trade_tab.py`
- Test: `tests/test_architecture_boundaries.py`

**Step 1: 写失败用例**
- 增加边界检查：`ui` 禁止直接 `subprocess` / `win32*` / `pyautogui`

**Step 2: 跑测试确认失败**
- Run: `pytest tests/test_architecture_boundaries.py -q`

**Step 3: 落地 infra**
- 把终端跳转逻辑移到 `infra/navigation`
- 把 toast / 内联 Python 子进程封装到 `infra/tasks/process_runner.py`
- UI 改为调用 adapter，不再直接触碰系统进程或窗口句柄

**Step 4: 跑测试确认通过**
- Run: `pytest tests/test_architecture_boundaries.py -q`

### Task 3: Typed Task Registry 与统一任务 ID

**Files:**
- Create: `infra/tasks/typed_task_registry.py`
- Modify: `core/background_job_runner.py`
- Modify: `core/startup_orchestrator.py`
- Modify: `ui/main_window_network.py`
- Modify: `ui/main_window_runtime.py`
- Modify: `ui/workers/central_quotes_worker.py`
- Modify: `ui/tabs/base_stock_refresh.py`
- Modify: `ui/tabs/rt_monitor_tab.py`
- Modify: `ui/tabs/scan_tab.py`
- Modify: `ui/tabs/watchlist_tab.py`
- Modify: `ui/tabs/earnings_tab.py`
- Modify: `ui/tabs/lhb_tab.py`
- Modify: `ui/tabs/foreign_block_trade_tab.py`
- Modify: `ui/tabs/fund_holdings_tab.py`
- Modify: `core/market_calendar.py`
- Test: `tests/test_background_job_runner.py`
- Test: `tests/test_architecture_boundaries.py`

**Step 1: 写失败用例**
- 让 `BackgroundJobRunner` 支持 registry key / factory
- 增加禁止散落裸 task id 的边界测试

**Step 2: 落地 registry**
- 为静态任务、动态任务、quote 刷新任务建立统一入口
- UI 侧不再拼接魔法字符串

**Step 3: 跑验证**
- Run: `pytest tests/test_background_job_runner.py tests/test_architecture_boundaries.py -q`

### Task 4: Workspace 与 Tab 解耦

**Files:**
- Modify: `ui/workspaces/workspace_facade.py`
- Modify: `ui/workspaces/classic_workspace.py`
- Modify: `ui/workspaces/quote_universe_service.py`
- Modify: `ui/workspaces/watchlist_radar_service.py`
- Modify: `ui/main_window_qt.py`
- Create: `app/services/__init__.py`
- Create: `app/services/kline_open_service.py`
- Test: `tests/test_architecture_boundaries.py`
- Test: `tests/test_main_window_shell.py`

**Step 1: 梳理跨页能力**
- 把 K 线打开、观察池雷达、盘中自动启动等跨页编排统一收口

**Step 2: 消除 Tab 私有耦合**
- 逐步去掉 `workspace.tab_xxx`、`_toggle_rt_monitor`、`_auto_refresh_realtime`、`find_scan_result` 这种跨层摸内部状态的路径

**Step 3: 跑验证**
- Run: `pytest tests/test_architecture_boundaries.py tests/test_main_window_shell.py -q`

### Task 5: 治理资产与 CI 闸门

**Files:**
- Create: `docs/adr/ADR-001-modular-monolith.md`
- Create: `docs/adr/ADR-002-settings-repository.md`
- Create: `docs/adr/ADR-003-workspace-facade.md`
- Create: `docs/adr/ADR-004-provider-ports.md`
- Create: `docs/adr/ADR-005-event-task-layering.md`
- Create: `docs/architecture-baseline-2026-04-20.md`
- Create: `docs/qsettings-key-registry.md`
- Modify: `.github/workflows/ci.yml`
- Modify: `scripts/check_utf8.py`
- Modify: `tests/test_architecture_boundaries.py`

**Step 1: 补齐资产**
- 补 ADR、baseline、配置键清单、清理清单模板

**Step 2: 补齐 CI 闸门**
- 把架构测试、UTF-8 检查、关键定向测试纳入 CI

**Step 3: 跑最终定向验证**
- Run: `pytest tests/test_architecture_boundaries.py tests/test_app_config.py tests/test_provider_services.py tests/test_engine_services.py tests/test_background_job_runner.py -q`
- Run: `python scripts/check_utf8.py`

### Task 6: 旧代码清理

**Files:**
- Delete: 已完成迁移后不再被引用的旧类、旧 shim、旧 helper
- Modify: `README.md`
- Modify: `docs/*`

**Step 1: 列删除清单**
- 用 `rg` 查兼容层剩余引用

**Step 2: 删除并验证**
- 每删一批就跑对应测试

**Step 3: 最终检查**
- Run: `git status --short`
- Run: `rg -n "QSettings\\(|subprocess\\.|win32|pyautogui|core\\.task_manager|core\\.event_bus" core ui vcp`

