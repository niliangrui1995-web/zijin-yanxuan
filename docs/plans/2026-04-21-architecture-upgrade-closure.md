# Architecture Upgrade Closure Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** 对照《紫金研选架构升级落地方案》补齐未落地项，完成收尾性的分层收口、兼容层下沉和文档/CI/测试闭环。

**Architecture:** 先处理高收益低风险的边界收口，再补齐目标上下文目录与兼容 shim。真实实现优先迁入 `domains/` / `infra/` 目标位置，旧路径仅保留兼容导出，并同步加上边界测试和 CI 闸门，防止回流。

**Tech Stack:** Python 3.10+, PyQt6, pytest, Ruff, GitHub Actions

---

### Task 1: App 层去 UI 具体实现耦合

**Files:**
- Modify: `app/bootstrap/application_bootstrap.py`
- Modify: `app/use_cases/window_command_service.py`
- Modify: `ui/main_window_qt.py`
- Test: `tests/test_application_bootstrap.py`
- Test: `tests/test_architecture_boundaries.py`

**Step 1: 写失败用例**
- 增加 `app` 层不得直接 import `ui.*` 具体实现的架构测试。
- 调整 `ApplicationBootstrap` 测试，使其依赖宿主公开工厂而不是 monkeypatch 内部 UI 类。

**Step 2: 实现最小改造**
- `ApplicationBootstrap` 改为依赖宿主公开工厂/回调。
- `WindowCommandService` 改为走公开命令接口，不引用窗口私有方法名和 `ui.theme`。
- `MainWindowQT` 暴露必要的公开命令入口和工厂方法。

**Step 3: 验证**
- Run: `pytest tests/test_application_bootstrap.py tests/test_architecture_boundaries.py -q`

### Task 2: 提取共享标题栏组件

**Files:**
- Create: `ui/components/shared_title_bar.py`
- Modify: `ui/components/main_window_shell.py`
- Modify: `ui/kline_window_qt.py`
- Modify: `ui/main_window_visuals.py`
- Modify: `ui/components/message_box.py`
- Modify: `ui/components/scan_dialogs.py`
- Modify: `ui/shell/__init__.py`

**Step 1: 提取共享组件**
- 将 `DraggableTitleBar` 从 `main_window_shell` 抽出为共享组件。

**Step 2: 清理耦合**
- K 线窗口、对话框、message box、shell 均改为依赖共享标题栏组件。

**Step 3: 验证**
- Run: `pytest tests/test_main_window_shell.py -q`

### Task 3: Workspace 继续瘦身

**Files:**
- Create: `ui/workspaces/workspace_table_service.py`
- Create: `ui/workspaces/workspace_navigation_service.py`
- Modify: `ui/workspaces/workspace_facade.py`
- Modify: `ui/workspaces/classic_workspace.py`
- Test: `tests/test_main_window_shell.py`

**Step 1: 提取服务**
- 将表格遍历、刷新编排、行定位逻辑移出 `ClassicWorkspace`。

**Step 2: 改为 facade 委托**
- `ClassicWorkspace` 保留 public wrapper，但逻辑走 facade/service。

**Step 3: 验证**
- Run: `pytest tests/test_main_window_shell.py tests/test_workspace_quote_codes.py -q`

### Task 4: 任务实现下沉到 Infra

**Files:**
- Create: `infra/tasks/task_scheduler.py`
- Modify: `infra/tasks/__init__.py`
- Modify: `core/task_manager.py`
- Modify: `core/background_job_runner.py`
- Test: `tests/test_background_job_runner.py`
- Test: `tests/test_task_manager.py`

**Step 1: 下沉实现**
- 将真实线程池和 worker 实现迁入 `infra/tasks/task_scheduler.py`。

**Step 2: 保留兼容壳**
- `core/task_manager.py` 改为兼容 re-export。
- `BackgroundJobRunner` 默认走 infra 入口。

**Step 3: 验证**
- Run: `pytest tests/test_background_job_runner.py tests/test_task_manager.py -q`

### Task 5: 补齐目标上下文目录与兼容 Shim

**Files:**
- Create: `domains/scan/*`
- Create: `infra/market_data/*`
- Create: `domains/earnings/*`
- Create: `domains/quotes/*`
- Create: `domains/watchlist/*`
- Create: `domains/fund_holdings/*`
- Create: `domains/market_calendar/*`
- Modify: `vcp/*.py`
- Modify: `earnings/*.py`
- Modify: `core/fund_holdings_*.py`
- Modify: `core/market_calendar.py`
- Test: `tests/test_provider_services.py`
- Test: `tests/test_engine_services.py`
- Test: `tests/test_market_data_ports.py`
- Test: `tests/test_earnings_engine_state.py`
- Test: `tests/test_earnings_scheduler_startup.py`
- Test: `tests/test_fund_holdings_compare.py`
- Test: `tests/test_fund_holdings_store.py`

**Step 1: 迁移真实实现**
- `scan` 纯规则迁到 `domains/scan`。
- `market_data` provider 子服务迁到 `infra/market_data`。
- `earnings` 迁到 `domains/earnings`。

**Step 2: 补齐稳定入口**
- `quotes / watchlist / fund_holdings / market_calendar` 至少补齐目标上下文包与主导出。
- 旧路径改为兼容 shim。

**Step 3: 验证**
- Run: `pytest tests/test_provider_services.py tests/test_engine_services.py tests/test_market_data_ports.py tests/test_earnings_engine_state.py tests/test_earnings_scheduler_startup.py tests/test_fund_holdings_compare.py tests/test_fund_holdings_store.py -q`

### Task 6: 文档、CI 与最终回归

**Files:**
- Modify: `tests/test_architecture_boundaries.py`
- Modify: `.github/workflows/ci.yml`
- Modify: `README.md`
- Modify: `docs/architecture-baseline-2026-04-20.md`
- Modify: `docs/architecture-upgrade-gap-checklist-2026-04-21.md`
- Modify: `docs/qsettings-key-registry.md`

**Step 1: 更新文档**
- 同步整改后的真实结构、owner 入口、兼容层策略。

**Step 2: 补齐 CI**
- 纳入 `test_event_bus_layers.py`、`test_application_bootstrap.py`、`test_market_data_ports.py`。

**Step 3: 最终验证**
- Run: `pytest tests/test_architecture_boundaries.py tests/test_app_config.py tests/test_background_job_runner.py tests/test_task_manager.py tests/test_provider_services.py tests/test_engine_services.py tests/test_market_data_ports.py tests/test_application_bootstrap.py tests/test_event_bus_layers.py tests/test_main_window_shell.py tests/test_kline_open_service.py -q`
- Run: `python scripts/check_utf8.py core ui vcp tests scripts app infra docs .github`
