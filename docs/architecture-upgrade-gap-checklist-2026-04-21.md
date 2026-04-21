# 未落地项清单回顾（2026-04-21）

## 关联方案

- `docs/plans/2026-04-20-architecture-upgrade.md`
- `docs/architecture-baseline-2026-04-20.md`
- `docs/adr/ADR-005-event-task-layering.md`

## 本轮回顾范围

| 未落地项 | 方案映射 | 落地标准 | 当前状态 |
| --- | --- | --- | --- |
| 启动阶段仍有子进程调用半改状态 | Task 2 / Task 3 | `core/startup_orchestrator.py` 统一走 `infra.tasks.run_python_module`，并接入 toggle | 已落地 |
| `MarketCalendar` 仍直接依赖旧任务管理器与旧存储入口 | Task 3 | 改走 `core.background_job_runner` 和 `infra.storage` 桥接 | 已落地 |
| runtime toggle 只注册未接线 | Task 3 | `central_quotes_service`、`silent_asian_sync`、`workspace_auto_rt_monitor` 均进入真实执行路径 | 已落地 |
| `foreign_block_trade_tab` 仍散落字面量任务 ID | Task 3 | 统一改为 typed task registry 常量 | 已落地 |
| 日志页没有任务进度可视化面板 | Task 5 | `sig_task_progress` 在 UI 上可观测，日志页直接展示最新任务状态 | 已落地 |
| 架构边界与 toggle 回归测试不足 | Task 3 / Task 5 | 补齐 startup、bootstrap、log tab、boundary、toggle 测试 | 已落地 |

## 本轮新增/收口文件

- `infra/storage/__init__.py`
- `infra/storage/data_store.py`
- `ui/presenters/__init__.py`
- `ui/presenters/task_status_presenter.py`
- `ui/components/task_status_panel.py`
- `tests/test_application_bootstrap.py`

## 验证要求

- 架构边界测试通过
- startup / bootstrap / log tab 定向测试通过
- UTF-8 检查通过
- 回顾清单各项无回退路径、无旧入口残留
