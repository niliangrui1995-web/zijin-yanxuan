# 紫金研选全库多技能审计整改报告

审计时间：2026-07-06 21:45-21:54 Asia/Shanghai
仓库：`D:\vcp_hunter\紫金研选`
分支/提交：`master` / `276e021 fix: reduce off-hours refresh pressure`
工作方式：只读审计为主；未修改业务代码。报告文件是本轮唯一版本化产物。

## 审计基准与技能

本轮按多个 skill 分视角审计：

- `codebase-audit-pre-push`：仓库卫生、依赖、测试、上线前门禁。
- `security-best-practices`：凭证、供应链、HTTP 边界、静态安全扫描和可利用性分级。
- `architect-review`：PyQt 桌面应用分层、模块边界、复杂度热点。
- `code-reviewer`：正确性、测试质量、可维护性、反模式。
- `lint-and-validate`：Ruff、Pyright、pytest、compileall、pip check。
- `codex-review`：以只读 L2 深度审计方式组织结论；未调用外部模型，源码未上传。

外部最佳实践参考：

- OWASP ASVS 5.0：安全验证要有可追溯版本和控制项边界。
- NIST SSDF SP 800-218：安全开发实践应嵌入 SDLC 和验证门禁。
- PyPA `pip-audit`：扫描 Python 环境已知漏洞。
- PyCQA Bandit：基于 AST 查找 Python 常见安全问题。
- Ruff 官方文档：lint/format 作为快速质量门禁。

## 总体结论

当前仓库没有发现可报告的高危安全漏洞、硬编码真实密钥、已知依赖漏洞或直接绕过 `infra.http_safety` 的外部 HTTP 调用。编码、基础 Ruff、compileall、pip check、架构边界烟测、UI 卡顿烟测、运行环境自检、依赖审计、Bandit、短运行健康预算均有通过证据。

但全库当前不是“可直接绿灯”状态，存在 3 个需要优先处理的阻断/准阻断问题：

1. 全量 pytest 失败 2 项，当前门禁结果为 `2 failed, 1590 passed, 2 warnings`。
2. 复杂度热点预算漂移，`ClassicWorkspace.__init__` 超预算，同时 `StartupOrchestrator.refresh_global_earnings_calendar` 达到大函数阈值但未登记。
3. 扩展 Ruff 规则命中 `B006`：闭包默认参数使用可变 `dict`。

## 阻断项与整改建议

### P1-REG-001：全量 pytest 失败，基金持仓快照测试替身签名滞后

证据：

- 命令：`.\.venv\Scripts\python.exe -m pytest -q`
- 结果：`2 failed, 1590 passed, 2 warnings`
- 失败测试：`tests/test_workspace_quote_codes.py::test_workspace_collects_fund_holding_context_from_snapshot_without_open_tab`
- 触发位置：`tests/test_workspace_quote_codes.py:742`，monkeypatch 的 `_cached_fund_holding_rows` lambda 只接收 `self`。
- 实现位置：`ui/workspaces/stock_context_service.py:1055`，真实方法签名为 `_cached_fund_holding_rows(self, *, allow_async_refresh: bool = True)`；调用位置 `ui/workspaces/stock_context_service.py:1069` 会传入 `allow_async_refresh=...`。

判断：

这是测试契约滞后，当前代码路径在测试替身处抛 `TypeError`。它会阻断 full pytest，不应忽略。

建议整改：

- 将测试替身改为接受关键字参数，例如 `lambda self, *, allow_async_refresh=True: [...]`。
- 同步补一个断言，确认该用例仍验证“不打开 fund_holdings Tab 也能从 snapshot 采集信号”，避免只为适配签名而削弱测试含义。
- 验证：先跑该单测，再跑 `tests/test_workspace_quote_codes.py`，最后跑全量 pytest。

### P1-ARCH-001：复杂度热点门禁漂移

证据：

- 命令：`.\.venv\Scripts\python.exe scripts\project_audit.py --quick --skip-webengine-preflight --extended-ruff --type-check --dependency-audit`
- 结果：停在 `complexity-hotspots`
- `tmp/complexity_hotspot_audit.json`：`ui/workspaces/classic_workspace.py:ClassicWorkspace.__init__` 当前 190 行，预算 186 行，起止行 `188-377`。
- 全量 pytest 额外失败：`tests/test_complexity_hotspot_audit.py::test_default_hotspot_budgets_cover_current_large_functions`
- 该测试还发现 `core/startup_orchestrator.py:StartupOrchestrator.refresh_global_earnings_calendar` 当前 185 行，达到 `170` 行阈值但没有登记预算。

判断：

这是架构/可维护性红点，也是 CI/本地审计红点。它不是功能崩溃，但会阻断质量门禁。

建议整改：

- 优先拆分，而不是单纯抬预算。
- `ClassicWorkspace.__init__`：把 `_tab_specs` 构造抽成 `_build_tab_specs(watchlist_kwargs)` 或若干分组 builder，保留现有 Tab key、group、lazy/prewarm 语义。
- `StartupOrchestrator.refresh_global_earnings_calendar`：抽出 refresh result 归一化、degraded 标记、retry 调度、日志/metric 组装等小函数，避免启动编排器继续膨胀。
- 如果短期只做守门同步，必须同时更新 `scripts/complexity_hotspot_audit.py` 与 `tests/test_complexity_hotspot_audit.py`，但报告建议把这作为临时兜底，不作为最终整改。

### P2-QUAL-001：扩展 Ruff 命中可变默认参数

证据：

- 命令：扩展 Ruff 规则集，包含 `B006`
- 结果：`ui/tabs/stock_candidate_tab.py:216:39`
- 代码：`def _slot(*args, _options=dict(options)):`

判断：

这里的 `dict(options)` 主要用于闭包捕获，当前未见被修改导致的直接 bug，但它违反 Python 审查基线；后续维护者也可能误以为 `_options` 可安全改写。

建议整改：

- 使用小工厂函数捕获不可变或局部副本：

```python
def _make_slot(options):
    refresh_options = dict(options)

    def _slot(*args):
        self._schedule_context_refresh(*args, **refresh_options)

    return _slot
```

- 验证：重跑扩展 Ruff，并跑 `tests/test_stock_candidate_tab.py`、`tests/test_workspace_quote_codes.py`。

## 建议项与观察项

### P3-TYPE-001：Pyright 0 error 但有 15 个第三方导入解析 warning

证据：

- 命令：`.\.venv\Scripts\python.exe -m pyright app/services domains/quotes domains/runtime infra/features/service_toggle_registry.py infra/http_safety.py infra/market_data infra/tasks`
- 结果：`0 errors, 15 warnings`
- warning 集中在 `PyQt6.QtCore`、`polars`、`pytdx.hq` 等已安装或条件依赖上。

建议：

- 在 `pyrightconfig.json` 中显式配置本地 venv，例如验证 `venvPath` / `venv` 是否能消除误报。
- 不建议把 warning 直接降级为忽略；应先确认 Pyright 是否实际使用 `.venv` 解释器环境。

### P3-RUNTIME-001：短运行健康预算通过，但 offscreen 受控启动仍记录 stall 观测

证据：

- 命令：`scripts/runtime_health_stability_suite.py --mode short --fail-on-budget ...`
- 结果：`status=ok`，`budget.status=ok`
- 尾部资源稳定：RSS 净增约 `0.004 MB`，private memory 净增 `0.0 MB`，后台任务归零，线程/Timer/事件订阅无净增长。
- 观察：offscreen controlled probe 中记录了 startup/event-loop stall 日志，但 suite 未判定预算失败；Tab cycle 因受控探针策略 `visited=0`。

建议：

- 本轮不把它列为失败。
- 如果后续要评估真实用户首开交互，应跑更接近真实窗口/Tab 首开的 runtime health 场景，不要只看受控 offscreen 短测。

## 已通过项

- `git status --short`：审计前干净；写入本报告前没有源码改动。
- `ruff check .`：通过。
- `scripts/check_utf8.py`：通过。
- `git diff --check`：通过。
- `compileall -q app core domains infra ui vcp earnings scripts tests`：通过。
- `pip check`：通过。
- `tests/test_architecture_boundaries.py`：35 passed。
- UI stall smoke subset：7 passed。
- `scripts/http_safety_audit.py`：`status=ok`，无未授权直接外部 HTTP 调用。
- `scripts/runtime_env_self_check.py --skip-webengine-preflight`：`status=ok`。
- `scripts/dependency_audit.py --strict`：`pip_audit.status=ok`，91 个依赖，0 个已知漏洞。
- Bandit：扫描 `app core domains earnings infra scripts ui vcp`，0 个 reportable finding。
- 强特征密钥搜索：无 AWS/OpenAI/Google/Slack/private key 命中。
- Git 跟踪敏感文件搜索：未发现 `.env`、私钥、证书或常见凭据文件被跟踪。
- Vulture 高置信死代码候选：无输出。

## 推荐修复顺序

1. 先修 `P1-REG-001` 测试替身签名，恢复全量 pytest 的非架构失败。
2. 修 `P2-QUAL-001`，让扩展 Ruff 继续往后跑。
3. 对 `P1-ARCH-001` 做小步拆分；若必须临时恢复 CI，预算表和测试断言一起同步，但要留下拆分 TODO 或 issue。
4. 调整 Pyright venv 配置，确认 warning 是工具环境问题还是真实类型导入问题。
5. 回归命令建议：

```powershell
.\.venv\Scripts\python.exe -m pytest -q tests\test_workspace_quote_codes.py::test_workspace_collects_fund_holding_context_from_snapshot_without_open_tab
.\.venv\Scripts\python.exe -m pytest -q tests\test_complexity_hotspot_audit.py
.\.venv\Scripts\python.exe -m ruff check app core domains infra ui vcp earnings scripts tests --select B006,B011,B013,B014,B015,B017,B020,B026,B904,SIM101,SIM109,SIM115,C400,C401,C402,C404,C405,C409,C410,C411,C413,C415,C416,C417,C418,C419,RUF006,RUF007,RUF008,RUF015,RUF016,RUF017,RUF018,RUF019,RUF020,RUF021,RUF024,RUF026,RUF028,RUF030,RUF032,RUF033,RUF034,RUF040,RUF041,RUF043,RUF048,RUF049,RUF053,RUF057,RUF058,RUF060,RUF064
.\.venv\Scripts\python.exe scripts\project_audit.py --quick --skip-webengine-preflight --extended-ruff --type-check --dependency-audit
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe scripts\runtime_health_stability_suite.py --mode short --fail-on-budget --output tmp\runtime_health_stability_short.json --sample-output-dir tmp\runtime_health_stability_short_samples
```

## 结论

本轮审计结论是：安全和供应链基线健康，运行时短稳预算健康；当前主要风险集中在质量门禁红点、复杂度债务和一个测试契约滞后。建议先用最小补丁恢复全量测试和扩展 Ruff，再对两个大函数做受控拆分，避免把复杂度预算表变成“只登记不治理”的清单。

## 整改执行记录

整改时间：2026-07-06 22:00-22:12 Asia/Shanghai

状态：报告内阻断项、建议项与观察项均已完成整改或复核验证。

### 已完成整改

- `P1-REG-001`：已修复 `tests/test_workspace_quote_codes.py` 中 `_cached_fund_holding_rows` monkeypatch 替身签名，补充 `get_loaded_tab`/`get_tab` 哨兵断言，确认基金持仓信号可从 snapshot 采集且不会触发懒加载 Tab。
- `P1-ARCH-001`：已拆分 `ClassicWorkspace.__init__` 的 Tab spec 构造逻辑到 `_build_tab_specs()`；已拆分 `StartupOrchestrator.refresh_global_earnings_calendar` 的后台刷新、成功处理、失败降级、日志与 retry 调度逻辑。复杂度热点审计已恢复 `finding_count=0`。
- `P2-QUAL-001`：已将 `StockCandidateTab` 自动刷新闭包改为 `_make_auto_refresh_slot()` 工厂方法，消除 Ruff `B006`。
- `P3-TYPE-001`：已在 `pyrightconfig.json` 显式配置 `"venvPath": "."` 与 `"venv": ".venv"`。配置生效后暴露的真实类型问题已同步修复：`LocalHistoryProvider` 的 Polars/Pandas 收窄、`MarketDataWarehouse` 的可选 Polars 异常类型、`TaskScheduler` 的线程池非空与信号连接类型。
- `P3-RUNTIME-001`：已复跑短运行健康套件，维持 `status=ok`、`budget.status=ok`；offscreen 受控探针仍记录 startup/event-loop stall 观察日志，但未构成预算失败。

### 回归验证结果

- `.\.venv\Scripts\python.exe -m pytest -q tests\test_workspace_quote_codes.py::test_workspace_collects_fund_holding_context_from_snapshot_without_open_tab`：`1 passed`。
- `.\.venv\Scripts\python.exe -m pytest -q tests\test_complexity_hotspot_audit.py`：`5 passed`。
- `.\.venv\Scripts\python.exe -m pytest -q tests\test_startup_orchestrator.py`：`38 passed`。
- `.\.venv\Scripts\python.exe -m pytest -q tests\test_task_manager.py tests\test_market_runtime_services.py tests\test_market_data_warehouse.py`：`72 passed`。
- 扩展 Ruff 规则集：`All checks passed`。
- `.\.venv\Scripts\python.exe -m pyright app/services domains/quotes domains/runtime infra/features/service_toggle_registry.py infra/http_safety.py infra/market_data infra/tasks`：`0 errors, 0 warnings`。
- `.\.venv\Scripts\python.exe scripts\project_audit.py --quick --skip-webengine-preflight --extended-ruff --type-check --dependency-audit`：`all checks passed`。
- `.\.venv\Scripts\python.exe -m pytest -q`：`1592 passed, 2 warnings`。两个 warning 均来自第三方 `pypinyin` 的 `codecs.open()` 弃用提示。
- `.\.venv\Scripts\python.exe scripts\runtime_health_stability_suite.py --mode short --fail-on-budget --output tmp\runtime_health_stability_short.json --sample-output-dir tmp\runtime_health_stability_short_samples`：`status=ok`、`budget.status=ok`。

### 整改后结论

当前整改分支已恢复质量门禁：扩展 Ruff、复杂度热点、Pyright、项目级审计、全量 pytest 与短运行健康预算全部通过。剩余可观察事项仅为受控 offscreen 探针下的 startup/event-loop stall 日志；它不阻断本轮整改，但若后续要评估真实首开交互，应另跑更接近真实窗口和真实 Tab 首开的 runtime health 场景。
