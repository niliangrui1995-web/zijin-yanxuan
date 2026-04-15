# 紫金研选 Full Remediation Implementation Plan

> **For Codex:** Execute this plan in the current session task-by-task. Do not commit automatically until the user confirms testing is OK, per repository instructions.

**Goal:** 一次性完成当前项目的 P0/P1/P2 整改，修复回归、降低耦合、补齐工程护栏，并保持仓库目录整洁。

**Architecture:** 先稳基线，再拆结构，最后补护栏。P0 聚焦测试回归、全局状态隔离、环境一致性和运行缓存治理；P1 聚焦 `ui/core/vcp` 解耦和千行文件拆分；P2 聚焦规范、配置、清理和持续验证。

**Tech Stack:** Python 3.10/3.11, PyQt6, pytest, ruff, PowerShell, Git

---

### Task 1: 基线计划与边界确认

**Files:**
- Create: `docs/plans/2026-04-15-full-remediation-plan.md`
- Modify: `tests/conftest.py`
- Check: `git status --short`

**Step 1: 记录整改计划**

- 明确 P0/P1/P2 的目标、涉及模块和验证命令。

**Step 2: 建立测试隔离基线**

- 在 `tests/conftest.py` 增加全局状态重置夹具，避免 `global_store` 与事件总线跨测试污染。

**Step 3: 验证测试隔离有效**

Run: `pytest tests/test_table_serial_column.py -q`
Expected: 全部通过，排序结果不受其他测试副作用影响。

---

### Task 2: 修复 Asian 市场页初始化副作用

**Files:**
- Modify: `ui/tabs/asian_market_tab.py`
- Test: `tests/test_asian_market_tab.py`

**Step 1: 抽出 worker 运行态包装方法**

- 新增安全调用封装，统一处理 `resume_auto_refresh`、`pause_for_cache_sync`、`trigger_refresh` 等可选 worker 能力。

**Step 2: 降低构造函数副作用**

- 构造阶段不直接假定 worker 具备完整运行态接口。
- 保证测试替身只实现最小能力也能构造成功。

**Step 3: 跑 Asian 相关测试**

Run: `pytest tests/test_asian_market_tab.py -q`
Expected: 相关 5 个用例全部通过。

---

### Task 3: 修复龙虎榜买点语义分层

**Files:**
- Modify: `core/lhb_pool_manager.py`
- Modify: `ui/models/table_models.py`
- Test: `tests/test_lhb_pool_manager.py`
- Test: `tests/test_table_serial_column.py`

**Step 1: 区分业务态与显示态**

- 为“买点已触发”建立统一语义。
- `core/lhb_pool_manager.py` 输出面向列表展示的买点文案。
- `ui/models/table_models.py` 兼容 `"触发"`、`"确认"`、`"✅"` 等显示态。

**Step 2: 确保排序与展示兼容**

- 不能破坏已有表格着色、排序和对齐逻辑。

**Step 3: 跑相关测试**

Run: `pytest tests/test_lhb_pool_manager.py tests/test_table_serial_column.py -q`
Expected: 全部通过。

---

### Task 4: 修复全局报价快照污染

**Files:**
- Modify: `tests/conftest.py`
- Modify: `ui/models/table_models.py`
- Modify: `core/global_store.py`
- Test: `tests/test_stock_table_model_quotes.py`
- Test: `tests/test_base_stock_tab.py`

**Step 1: 收敛 `global_store` 的测试副作用**

- 增加显式重置接口，或在测试夹具中清空 `quotes` 状态。

**Step 2: 控制 `StockTableModel.update_data()` 的快照补齐行为**

- 保留生产环境“新开 Tab 立即吃快照”的能力。
- 同时避免它在测试和无关数据更新时造成状态漂移。

**Step 3: 跑关联测试**

Run: `pytest tests/test_stock_table_model_quotes.py tests/test_base_stock_tab.py tests/test_table_serial_column.py -q`
Expected: 全部通过。

---

### Task 5: 统一解释器与开发环境入口

**Files:**
- Create: `.venv`（本地环境，不入库）
- Modify: `README.md`
- Create or Modify: `.editorconfig`
- Create: `pyproject.toml`

**Step 1: 明确项目基准解释器**

- 以项目本地 `.venv` 为唯一推荐入口。
- 在 README 中收敛到单一启动、测试、检查命令。

**Step 2: 建立工程工具配置**

- 在 `pyproject.toml` 中补充 `ruff` 基线与编码/行尾策略。
- 在 `.editorconfig` 中约束 UTF-8、CRLF/LF 策略，避免中文乱码和换行漂移。

**Step 3: 验证工具可运行**

Run: `python -m pip check`
Run: `ruff check tests/conftest.py`
Expected: 命令可运行，新增配置不破坏现有测试入口。

---

### Task 6: P1 解耦第一阶段，拆表格模型职责

**Files:**
- Modify: `ui/models/table_models.py`
- Create: `ui/models/table_roles.py`
- Create: `ui/models/table_proxy.py`
- Adjust imports in: `ui/tabs/*.py`, `ui/components/*.py`
- Test: `tests/test_table_serial_column.py`
- Test: `tests/test_stock_table_model_quotes.py`

**Step 1: 提取纯函数职责**

- 把角色颜色、对齐、状态 badge、数值热力等纯逻辑迁出。

**Step 2: 提取 ProxyModel**

- 将 `RtSortFilterProxyModel` 从大文件中拆出，降低耦合面。

**Step 3: 保持对外 API 不变**

- `from ui.models.table_models import StockTableModel, RtSortFilterProxyModel` 仍可用，避免大面积调用方同时崩。

**Step 4: 跑回归测试**

Run: `pytest tests/test_table_serial_column.py tests/test_stock_table_model_quotes.py -q`
Expected: 全部通过。

---

### Task 7: P1 解耦第二阶段，收敛 Asian 页运行编排

**Files:**
- Modify: `ui/tabs/asian_market_tab.py`
- Create: `ui/tabs/asian_market_runtime.py`
- Modify: `ui/tabs/asian_market_workers.py`
- Test: `tests/test_asian_market_tab.py`

**Step 1: 提取运行态控制器**

- 将“分钟 tick / 缓存同步 / worker pause-resume / 健康日志”迁到独立运行编排模块。

**Step 2: 保留 UI 层职责**

- Tab 只负责视图组件、表格、按钮和事件绑定。

**Step 3: 跑 Asian 相关测试**

Run: `pytest tests/test_asian_market_tab.py -q`
Expected: 全部通过。

---

### Task 8: P1 解耦第三阶段，收敛主窗口与实时刷新入口

**Files:**
- Modify: `ui/main_window_qt.py`
- Create: `ui/components/main_window_runtime.py`
- Modify: `ui/components/main_window_shell.py`
- Modify: `ui/workers/central_quotes_worker.py`
- Test: `tests/test_central_quotes_worker.py`
- Test: `tests/test_startup_loader.py`

**Step 1: 抽离主窗口运行编排**

- 分离网络检测、Tab 联动刷新、状态灯刷新、强制重连等流程。

**Step 2: 保持 UI 外壳轻量**

- `main_window_qt.py` 只保留装配逻辑。

**Step 3: 跑主窗口相关测试**

Run: `pytest tests/test_central_quotes_worker.py tests/test_startup_loader.py -q`
Expected: 全部通过。

---

### Task 9: P2 工程护栏补齐

**Files:**
- Create: `.editorconfig`
- Create: `pyproject.toml`
- Create: `.gitattributes`
- Create: `.pre-commit-config.yaml`（如不引入则在 README 说明）
- Modify: `README.md`

**Step 1: 统一编码、行尾与工具行为**

- `utf-8`
- Python 文件统一换行策略
- 明确 `ruff` 规则边界，先收敛高风险项，不做一次性 176 全清。

**Step 2: 约束运行时文件**

- 通过 `.gitattributes` 与 `.gitignore` 让缓存、日志、数据库文件不再持续污染工作区。

**Step 3: 给出团队执行入口**

- README 补充“开发、测试、静态检查、清理缓存”的固定命令。

---

### Task 10: 目录清理与运行垃圾治理

**Files:**
- Modify: `.gitignore`
- Delete from index intent: `data/Cache/*.json` runtime artifacts
- Remove dead files if确认无用: `scripts/rollback_old_ui.cmd`, `scripts/rollback_old_ui.ps1`
- Review: `ui/workspaces/`

**Step 1: 清点已跟踪运行垃圾**

- 确认哪些缓存和运行态文件不应该继续入库。

**Step 2: 保持文件夹干净**

- 删除确定无用的旧脚本和遗留文件。
- 只保留必要占位文件和样例。

**Step 3: 验证 Git 状态**

Run: `git status --short`
Expected: 仅剩真实源码与测试变更，不再有运行时缓存噪音。

---

### Task 11: 全量验证

**Files:**
- Verify whole repo

**Step 1: 运行目标测试集**

Run: `pytest -q`
Expected: 全部通过。

**Step 2: 运行静态检查**

Run: `ruff check .`
Expected: 至少新增/核心整改区域无阻塞问题；若全量未清零，要明确剩余范围和原因。

**Step 3: 人工核查**

- 检查 `git diff --stat`
- 检查关键文件编码未损坏
- 检查运行时缓存是否仍被跟踪

**Step 4: 汇报并等待用户确认**

- 按仓库规则，修改完成后先汇报结果，不自动提交。
- 仅在用户明确回复“没问题 / 可以 / 好了”后，才执行：

```powershell
git add .
git commit -m "refactor: stabilize runtime baseline and reduce UI coupling"
```
