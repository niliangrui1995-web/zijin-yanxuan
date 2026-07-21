# 生产级稳定性增强重构报告

最后验证：2026-07-22

## 范围与结论

本次改造完成了不可变行情快照、CentralQuotes 集中状态机、统一取消协议、K 线窗口池状态机、可双向迁移的 SQLite schema、亚洲行情 Provider 拆分及可持久化的运行时健康监控。既有功能、UI 布局、用户操作流程、行情算法和旧模块入口均保留。

用户列出的六项后续技术债均已落实为代码、门禁或实机验证：全仓 Mypy “只减不增”基线、Qt 动态对象 Protocol、真实 Windows WebEngine 100 轮 soak、migration downgrade 与自动备份、版本化 Provider 兼容 facade、运行时历史/阈值/趋势/告警。

代码质量门禁已通过，但发布状态不能表述为全绿：真实 WebEngine soak 的 100/100 功能循环、QObject 所有权和进程数量均通过，Chromium 进程树 RSS 高水位增长 `473.712 MB`，超过 `48 MB` 严格预算。尾部 20 轮已经平台化，但该内存门禁仍为红色，应在发布前完成根因分析或明确风险豁免。

最终验收结果：

- `pytest -q`：`3478 passed, 2 warnings`
- `ruff check .`：通过
- `mypy`：`Success: no issues found in 37 source files`
- 全仓 Mypy 诊断基线：`baseline=4305 current=4305 new=0 resolved=0`
- Pyright：`0 errors, 0 warnings, 0 informations`
- `scripts/project_audit.py --quick --keep-going --type-check --extended-ruff`：全部通过

## 1. 修改文件列表

以下列表按职责归类；跨阶段共享文件只列一次。

### Phase 1：不可变行情快照

- `core/state/__init__.py`
- `core/state/quote_snapshot.py`
- `core/global_store.py`
- `domains/quotes/__init__.py`
- `domains/quotes/snapshot.py`
- `ui/tabs/watchlist_tab.py`
- `tests/test_quote_snapshot_atomic.py`

### Phase 2：CentralQuotes 状态机

- `app/services/quote_runtime_state.py`
- `ui/workers/central_quotes_worker.py`
- `scripts/runtime_health_stability_suite.py`
- `tests/test_central_quotes_worker.py`
- `tests/test_quote_runtime_state.py`

### Phase 3：统一任务取消协议

- `app/services/central_quote_polling_service.py`
- `infra/tasks/lifecycle.py`
- `infra/tasks/owner_lifecycle.py`
- `infra/tasks/task_scheduler.py`
- `tests/test_central_quote_polling_service.py`
- `tests/test_task_cancel_race.py`
- `tests/test_workspace_quote_codes.py`

### Phase 4：KLine 生命周期、类型协议与实机 soak

- `ui/kline_pool_state.py`
- `ui/kline_typing.py`
- `ui/kline_window_pool_lifecycle.py`
- `ui/kline_window_qt.py`
- `ui/kline_window_state.py`
- `ui/kline_window_recovery.py`
- `ui/components/kline_window_manager.py`
- `scripts/kline_webengine_pool_soak.py`
- `tests/test_kline_pool_lifecycle.py`
- `tests/test_kline_webengine_pool_soak.py`
- `tests/test_kline_full_window_pool.py`
- `tests/test_kline_window_header.py`
- `tests/test_kline_window_manager_branch_coverage.py`
- `tests/test_kline_window_recovery.py`

### Phase 5：DataStore migration 与备份恢复

- `infra/storage/data_store.py`
- `infra/storage/migrations/__init__.py`
- `infra/storage/migrations/backup.py`
- `infra/storage/migrations/identity.py`
- `infra/storage/migrations/models.py`
- `infra/storage/migrations/registry.py`
- `infra/storage/migrations/runner.py`
- `infra/storage/migrations/v001_init.py`
- `infra/storage/migrations/v002_datastore_identity.py`
- `tests/test_database_migration.py`

### Phase 6：行情 Provider 拆分与兼容退场

- `app/services/asian_market_quote_service.py`
- `infra/market_data/asian_quote_provider.py`
- `infra/market_data/asian_realtime_provider.py`
- `infra/market_data/providers/__init__.py`
- `infra/market_data/providers/asian_http_provider.py`
- `infra/market_data/providers/yfinance_provider.py`
- `infra/market_data/normalize/__init__.py`
- `infra/market_data/normalize/quote_normalizer.py`
- `infra/market_data/policies/__init__.py`
- `infra/market_data/policies/fallback_policy.py`
- `tests/test_asian_provider_split.py`
- `tests/test_asian_market_workers.py`
- `tests/test_asian_provider_branch_coverage.py`

### Phase 7：可持久化运行时健康监控

- `infra/runtime_monitor/__init__.py`
- `infra/runtime_monitor/health_report.py`
- `infra/runtime_monitor/monitor.py`
- `infra/diagnostics/runtime_health.py`
- `tests/test_runtime_monitor.py`
- `tests/test_runtime_health.py`

### 类型基线、架构守卫与项目配置

- `.gitignore`
- `config/mypy_baseline.json`
- `pyproject.toml`
- `requirements-dev.txt`
- `scripts/mypy_baseline.py`
- `scripts/project_audit.py`
- `tests/test_mypy_baseline.py`
- `tests/test_project_audit.py`
- `tests/test_architecture_boundaries.py`
- `docs/refactor_report.md`

`data/Cache/ai_industry_chain_rows.json` 在本次工作开始前已有未提交改动；本次未修改，也不属于本次重构或后续备份范围。

## 2. 架构变化

| 领域 | 改造前 | 改造后 |
| --- | --- | --- |
| 行情共享状态 | 多方共享可变 `dict` | `QuoteSnapshot` 为冻结 dataclass，内部映射和嵌套内置容器不可变；`GlobalStore` 在锁内创建并原子替换快照 |
| CentralQuotes 运行状态 | `_fetching`、`_generation` 等字段分散写入 | `QuoteRuntimeState.update()` 统一验证并替换状态，计数增量也在同一锁内原子完成，对外只返回一致快照 |
| 后台任务 | Worker 存在额外取消布尔值，旧任务延迟回调可能覆盖新任务 | 统一使用 `CancellationToken`；通过 `inspect.signature` 兼容旧零参任务；交付回调前校验任务身份和 token |
| KLine 生命周期 | 多个布尔值可以组成矛盾状态 | `KLinePoolState` 统一为 `ACTIVE/CLOSING/IDLE/TAINTED/DISPOSED`，所有生产转换只经过 `transition()` |
| Qt 动态类型 | mixin 与大型窗口依赖隐式动态属性 | `ui/kline_typing.py` 定义最小 Protocol，并在生命周期、恢复和窗口管理边界显式标注 |
| Mypy 治理 | 无全仓诊断基线，扩大检查范围容易被历史噪声阻断 | 固化全仓诊断指纹；门禁要求与当前基线完全相等，诊断减少时必须同步收紧基线，新增诊断只能显式批准；已类型化生产范围扩大到 37 个文件 |
| SQLite schema | 只有幂等建表和单向升级 | 连续 migration registry 支持升级与 downgrade；v1 会验证既有 `kv_store`，v2 写入 DataStore `application_id`；版本变化在 `BEGIN IMMEDIATE` 单事务中完成，迁移回调不能自行提交或回滚 |
| 数据库安全 | schema 变化前无统一备份 | SQLite online backup 配套 SHA-256 manifest，并绑定原数据库路径身份、schema 与 application id；恢复前再保存目标库安全快照，恢复旧版本后自动升级至当前 schema |
| 亚洲行情 Provider | HTTP、yfinance、归一化和回退策略集中在旧模块 | 稳定实现位于 `asian_quote_provider.py`，职责拆入 `providers/`、`normalize/`、`policies/`；旧模块为版本化兼容 facade |
| 旧 Provider 注入点 | 私有 monkeypatch 接口没有退场契约 | 私有访问继续双向委托并发出 `DeprecationWarning`；16 个拆分后 hook 会同步到实际运行时消费位置；固定自 `1.8.8` 弃用、`2.0.0` 删除，并有版本到期与依赖方向守卫 |
| 运行健康 | 只有即时、分散诊断 | `runtime_health_report()` 统一采集 Task、KLine 和 RSS；有界 JSONL 历史使用跨进程文件锁，支持阈值告警、趋势判断、压缩和进程会话隔离；默认持久监控器首次采样时才创建 |

实现依据遵循 [Mypy 既有代码接入指南](https://mypy.readthedocs.io/en/stable/existing_code.html)、[Python Protocol 文档](https://docs.python.org/3/library/typing.html)、[Python sqlite3 backup 文档](https://docs.python.org/3/library/sqlite3.html)、[Qt WebEngine 进程模型](https://doc.qt.io/qt-6/qtwebengine-overview.html)及 [QWebEnginePage 生命周期文档](https://doc.qt.io/qt-6.11/qwebenginepage.html)。

## 3. 风险说明

- `GlobalStore.get_latest_quotes()` 现在返回实现 `Mapping` 的不可变快照。迭代、索引、长度和与 `dict` 比较等读取行为保持兼容；旧代码若尝试就地修改会明确失败。
- 行情快照递归冻结内置 `dict/list/set`。第三方自定义可变对象不做任意深拷贝；当前行情载荷约束为标量和内置容器。
- `QuoteRuntimeState` 的替换和计数增量均在锁内完成；并发写入不再依赖 Qt owner-thread 的读改写时序。
- 旧零参后台任务继续可用；新任务可声明 `cancellation_token`。取消或同 ID 覆盖后，旧任务成功/失败回调不会再交付。
- KLine 非法转换会抛出 `RuntimeError`；`TAINTED` 窗口不能回池，关闭时进入 `DISPOSED`，用于尽早暴露所有权错误。
- 真实 100 轮 soak 没有发现 QObject、窗口身份或 WebEngine 子进程数量泄漏，但 Chromium RSS 高水位显著超过预算。尾部趋稳不能替代总增长预算，当前发布内存门禁仍失败。
- migration 支持向前与向后链式迁移并在失败时回滚；备份通过 manifest 绑定数据库身份并校验内容，恢复前保留当前库安全快照。manifest 用于防误用和损坏检测，不是抵抗本机恶意篡改的签名机制；每个破坏性 migration 仍须单独验证 downgrade 的数据语义。
- 旧 Provider 私有注入点在 `2.0.0` 前必须保留，因此兼容 facade 仍是受控技术债；提前删除会破坏 1.x 测试及外部 monkeypatch。
- 运行健康对负数、布尔值、空值和非有限数值按采集不可用处理，诊断自身不会因 `inf` 转换而崩溃。历史是本机有界 JSONL，适合诊断和门禁，不等同于远程监控平台；告警目前随报告返回，不主动发送到外部通知渠道。

## 4. 测试结果

| 验证项 | 结果 |
| --- | --- |
| QuoteSnapshot | 通过；覆盖并发读取、原子替换、空数据、异常数据、所有 reader 确实观察发布值及可变二进制叶子隔离 |
| QuoteRuntimeState / CentralQuotes | 通过；所有状态变化经 `update()`，并覆盖成功、失败、过期 generation、并发读取及原子计数增量 |
| CancellationToken 竞态 | 通过；覆盖 submit、cancel、延迟终态回调、同 ID 新任务覆盖、新旧函数签名及 Poller/Provider token 透传 |
| KLine 状态机与 QObject | 通过；自动测试执行 100 次 open/close/reuse，并验证 physical/host/browser/page 释放 |
| Windows WebEngine 实机 soak | `100/100` 功能循环通过；physical/browser/page/renderer 各保持一个身份，WebEngine 进程增长为 0；严格 RSS 预算失败 |
| Migration / backup / restore | `19 passed`；覆盖实际 DataStore upgrade/downgrade、异常旧表拒绝、回调越权提交回滚、路径身份绑定、内容/manifest 篡改、空 v0 自动升级及恢复前安全快照 |
| Provider 兼容 | 最终定向回归 `155 passed`；覆盖公开导出一致、16 个拆分 hook 的实际目标同步、真实短路行为、弃用/到期守卫、依赖方向及拆分模块行为 |
| Runtime monitor | `37 passed`；覆盖并发写入、跨实例锁、无效/非有限指标降级、惰性默认实例、会话隔离、阈值、趋势及告警 |
| 全量 Pytest | `3478 passed, 2 warnings`；退出阶段无遗留 StockContext worker 错误 |
| Ruff | `All checks passed!` |
| Mypy 已类型化生产范围 | `Success: no issues found in 37 source files` |
| Mypy 全仓只减不增基线 | `baseline=4305 current=4305 new=0 resolved=0` |
| Pyright | `0 errors, 0 warnings, 0 informations` |
| 项目审计 | UTF-8、`git diff --check`、compileall、pip check、55 项架构守卫、9 项 UI stall smoke、复杂度、冷导入、HTTP 安全、扩展 Ruff、运行环境自检全部通过 |

两条 Pytest warning 均来自第三方 `pypinyin` 内部使用已弃用的 `codecs.open()`，不是本次修改引入。

真实 soak 报告：`tmp/kline_webengine_pool_soak_100_real_20260722.json`。关键数据如下：

- Python RSS：`351.055 → 347.770 MB`
- Chromium RSS：`158.023 → 635.020 MB`
- 进程树 RSS：`509.078 → 982.790 MB`，增长 `473.712 MB`
- 尾部 20 轮范围：`7.917 MB`
- 尾部斜率：`-0.153268 MB/cycle`
- shutdown：active/keeper/WebEngine child 均为 0

## 5. 剩余技术债

- 全仓 Mypy 基线仍含 `4305` 条历史诊断。现在已建立“只减不增”门禁并扩大零错误类型范围，但不能将其表述为历史全仓 Mypy 零错误；后续应分模块持续消减基线。
- WebEngine 实机 soak 暴露 Chromium 高水位 RSS 问题。尾部已稳定且退出清理完整，但在解释并控制首次增长、调整资源缓存策略或获得明确发布豁免前，严格内存门禁保持失败。
- `asian_realtime_provider.py` 的旧私有注入点按兼容契约保留到 `2.0.0`，并已有版本到期门禁。主版本升级时仍需按替代映射删除 facade 私有入口和对应兼容测试。
- `runtime_health_report()` 已具备本地持久化、阈值、趋势和告警，但尚无远程时序后端及主动通知投递；若生产运维需要集中观测，应另行接入监控系统。

## 提交与备份状态

当前未创建 commit、未推送远程。待用户完成本地验收并确认后，再按小步边界提交并推送当前分支的 `origin` 上游分支；不会纳入原有的 `data/Cache/ai_industry_chain_rows.json` 改动。

计划提交边界：

1. `refactor: make quote snapshots immutable`
2. `refactor: centralize quote runtime state`
3. `refactor: unify task cancellation protocol`
4. `refactor: model kline pool lifecycle`
5. `feat: add datastore schema migrations`
6. `refactor: split Asian quote providers`
7. `feat: add runtime health monitoring`
8. `chore: add stability quality gates`
9. `docs: record stability refactor verification`
