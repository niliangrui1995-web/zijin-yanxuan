# 紫金研选 全项目综合审计报告

- 审计日期：2026-08-22
- 审计范围：app/、core/、domains/、infra/、ui/、vcp/、earnings/、scripts/、tests/、CI 配置（约 414 个源码文件 + 267 个测试文件）
- 审计方式：6 路并行深度审查（core/app、domains/vcp/earnings、infra+安全、ui、测试与 CI、全仓横切安全扫描），所有问题均基于具体代码证据核实，非猜测

## 总体结论

先说公道话：这是一个**工程纪律显著高于平均水平**的桌面项目。审计确认了多项同类项目罕见的良好实践：

- 全仓 0 处裸 `except:`，仅 3 处真·吞异常（清理路径）；97 处 `except Exception` 中绝大多数带日志或 `# noqa: BLE001` 理由注释
- 0 处硬编码密钥、0 处 SQL 拼接不可信输入、0 处 `eval/exec/pickle.load/shell=True/verify=False`
- `infra/http_safety.py`（强制 https + 私网阻断 + 逐跳重定向校验）+ `scripts/http_safety_audit.py` 静态自审计 + `tests/test_http_safety_audit.py` 防回归，网络出口单点管控
- `infra/tasks/process_runner.py` argv-only 子进程边界（显式拒绝 shell=True、剥离代理环境变量、terminate→kill→reap 有界回收）
- 17 处 tmp+`os.replace` 原子写盘；`redact_sensitive_data` 递归脱敏体系
- 3508 个测试、15131 条断言、73% 文件使用 mock；`test_architecture_boundaries.py` AST 级分层扫描
- 表格模型已有增量 diff 渲染、单飞行行情 worker、失败熔断等设计

**真正的问题集中在五个方向**：① 分层纪律系统性倒置（依赖图已不可分层）；② 行情/表格热路径的性能浪费；③ 兼容门面黑魔法（`sys.modules` 替换）；④ UI 层完全游离于类型检查之外 + 覆盖率被包级平均稀释；⑤ 巨石文件（6 个 2000+ 行源文件 + 4 个万行级脚本）。

---

## 一、高严重度问题（11 项）

### H1.【安全】以 `-ExecutionPolicy Bypass` 静默执行用户可写目录下的 PowerShell 脚本
- **位置**：`app/services/ui_navigation_service.py:17, 440-451`
- **证据**：`CODEX_LOCAL_LAUNCHER = Path.home() / ".codex" / "local-tools" / "open-codex-project.ps1"`，随后 `spawn_silent_process([powershell, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", launcher_path, url])`
- **影响**：`%USERPROFILE%\.codex\local-tools\` 任何低权限进程可写，无签名/哈希校验。恶意软件写入该路径即可借本应用以 Bypass 策静默执行任意 PowerShell（无窗口、无输出，完全不可见）。这是全清单中唯一可能构成**本地权限提升链**的点。
- **建议**：改为执行仓库内置并随安装分发的脚本，或复用项目已有的 `infra/storage/file_integrity.py` 的 `verify_file_fingerprint` 校验指纹；移除 Bypass，改用 RemoteSigned + 签名。

### H2.【架构】分层系统性倒置，依赖图已不可分层
- **位置与证据**（跨层汇总）：
  - core → 上层：`core/cache_manager.py:9` import `app.services.f5_snapshot_service`；`core/rps_precomputer.py:12`；`core/sector_rps_helper.py:15,21`；`core/global_store.py:13`、`core/logger.py:126,324`、`core/market_calendar.py:1` 等 10 处 import domains；`core/ui_signals.py:4` 重定向到 ui
  - infra → 上层：`infra/diagnostics/runtime_health.py:22` import `ui.workspaces.tab_registry`（最严重，且在模块 import 时执行 UI 注册表计算）；`infra/runtime_monitor/health_report.py:127` import `ui.components.kline_window_manager`
  - domains → app：`domains/earnings/scheduler.py:10` `from app.services.ui_earnings_service import EarningsRefreshService`，并被 `domains/__init__.py:32` 再导出
  - vcp ↔ infra 循环：`infra/market_data/tdx_data_provider.py:18-22` import `vcp.data_provider_*`，而 vcp 门面又指向 infra（`tests/test_architecture_boundaries.py:470-485` 靠白名单豁免）
- **影响**：任何上层重构都波及"基础层"；infra 无法脱离 Qt/UI 测试复用；循环 import 靠函数内延迟 import 苟活；`import vcp.engine` 这种"轻量"动作立即拉起 pandas/polars 重量级链路。
- **建议**：① 把 `f5_job_contract` 等纯契约下沉到 core 或独立 contracts 包；② `domains/earnings/scheduler.py` 的 re-export 立即从 `domains/__init__.py` 导出表移除；③ runtime_health 的 tab lineage 数据改构造注入；④ 制定一次性迁移：`vcp/data_provider_*`、`sector.py`、`polars_engine.py`、`engine_external.py`、`fetchers/` 迁入 `infra/market_data/`，vcp/ 只留 re-export 壳并设删除期限。

### H3.【性能】GlobalStore.merge_quotes 每次行情合并做 2~3 次全市场深拷贝+冻结
- **位置**：`core/global_store.py:71-90` → `domains/quotes/snapshot.py:44`（全量浅拷贝）→ `core/state/quote_snapshot.py:146-172`（`_freeze_quotes` 全量遍历冻结）；`_freeze_value`（:58-143）每标量 6+ 次 isinstance + 每字符串 `_canonical_text`
- **影响**：每 30 秒一轮行情推送，即使只更新 10 只股票也要付 O(全市场 ~5000 股 × 全字段) 的 2~3 次遍历，未知叶子隔离路径再翻倍。这是实时行情吞吐的最主要 CPU/GC 热点。
- **建议**：合并只对 incoming code 做 entry 级合并；已冻结 payload 加"可复用"标记避免重复 freeze；未知叶子检测只对 incoming 做。

### H4.【性能】表格渲染热路径三重浪费
- **位置**：`ui/models/stock_table_model.py`
  - (a) `_foreground_value`（:943-1090）per-cell 执行 10 个 resolver + 每次 `from app.services.ui_watchlist_service import watchlist_vm` + 每次拿 `threading.Lock`；`set_presentation_cache_enabled(True)` 仅 `watchlist_tab.py:1117` 一处调用——LHB/基金持仓等大表每帧全量重算
  - (b) 重排路径（:544-557）：`layoutChanged` 之后再发全表 `dataChanged`，排序表每 30 秒两次全量委托重绘（`rt_table_model.py:153-163` 同病）
  - (c) `update_data`（:586-642）增量路径后再 `_hydrate_latest_quotes_from_store()` 做第二次全行扫描，所有 Tab 刷新成本翻倍
- **影响**：大表（数千行 × 20+ 列）滚动与行情刷新时 GUI 线程 CPU 显著升高，是排序模式下卡顿的最可能来源。
- **建议**：在 `VCPTableView.setModel` 或 Tab 基类默认启用 presentation cache；顶部一次性 import；预解析关注池 code set；删除 layoutChanged 后的整表 dataChanged；增量路径只做选择性水合。

### H5.【性能/正确性】GLOBAL_ASIAN_RT_CACHE 全局 dict 无锁跨线程读写
- **位置**：定义 `app/services/asian_market_cache_service.py:21`；写点 `ui/tabs/asian_market_workers.py:409`（线程池 worker）、`ui/kline_window_runtime.py:593`（K线 worker）、`ui/tabs/asian_market_tab.py:599-601`（GUI 线程原地 `.update()` 内层 dict）；读点 `ui/kline_window_qt.py:856` 等
- **影响**：GUI 可读到"半更新"的行情 dict；`asian_market_tab.py:1350` 的整体拷贝与 worker 写入并发可触发 `RuntimeError: dictionary changed size during iteration`；`ui/kline_window_qt.py` 反向 import Tab 模块还有循环导入风险。
- **建议**：收口为带锁的 `AsianQuoteCacheStore` service（读写走方法），kline 侧只 import service；Tab 与 kline 通过事件总线交换数据。同类问题：`domains/earnings/engine.py:100-101,472-490,691-711` 两级模块级缓存无锁，3 个 worker 线程的 LRU 淘汰迭代存在竞态，加 `threading.Lock` 或改 `functools.lru_cache`。

### H6.【性能】板块 RPS 全市场计算用 Python 级逐行循环定位日期
- **位置**：`vcp/polars_engine.py:603`（同构写法 `vcp/sector.py:247`）
- **证据**：`valid_indices = [i for i, v in enumerate((dates_col <= target_dt).to_list()) if v]`——对每只股票把整个日期列物化成 Python list 再逐元素枚举，仅为了找"最后一个 ≤ 目标日的下标"
- **影响**：250+ 行 × ~5000 只 = 百万级 Python 迭代，是 `build_sector_rps_pl` 的首要瓶颈。
- **建议**：改 `dates_col.search_sorted(target_dt, side="right") - 1`（O(log n)）；numpy 兜底路径用 `np.searchsorted`。顺带：`:620-627` 每条收益记录 bare/prefixed 双写导致数据量翻倍，改为 join 前对成分股表做一次别名展开。

### H7.【性能】中央行情 worker 每 30 秒 tick 在 GUI 线程执行 sys._current_frames() 全线程栈扫描
- **位置**：`ui/workers/central_quotes_worker.py:1271-1284`（`_run_maintenance` 无条件调用）→ `:982-1002`（`_collect_thread_health`）
- **影响**：GUI 线程 QTimer 回调里遍历全部线程调用栈回溯 "pytdx" 文件名，每 30 秒引入一次不可预测停顿。
- **建议**：把 `_collect_thread_health` 移入 `_heartbeat_every_ticks`（60s）分支内，或下沉到后台线程。一行移动的修复。

### H8.【可维护性】`sys.modules[__name__]` 运行时模块替换黑魔法遍布兼容门面
- **位置**：`core/startup_orchestrator.py:5`、`core/ui_signals.py:4`、`core/domain_events.py:4`、`core/json_cache.py:5`、`core/lhb_pool_manager.py:5`、`core/ai_industry_chain_pool.py:5`、`core/fund_holdings_sync.py:5`；`vcp/engine.py:1-5`、`vcp/data_provider.py:1-5`、`earnings/engine.py:1-5`、`earnings/scheduler.py:1-5`；另有 `infra/market_data/asian_realtime_provider.py:148-191` 用 `sys.modules[__name__].__class__ = ...` 全属性劫持
- **影响**：模块身份运行时被偷换——静态分析、IDE 跳转、coverage 归因、`inspect.getsource`、pickle 全部失真；`import vcp.engine` 立即触发重量级导入链破坏懒加载；导入顺序稍变即可能循环导入。
- **建议**：全部改为普通 re-export（`from x import A, B` + `__all__`），顶层 `earnings/` 已无逻辑残留且仓库内引用数为 0，可直接删除。

### H9.【工程】mypy/pyright 均不含 ui/ 层，最大最复杂的代码完全无静态类型护栏
- **位置**：`pyproject.toml:33-57`（mypy files 仅 23 项，ui/ 整包缺席）；`pyrightconfig.json`（basic 模式 + `reportUnknown*` 全关，范围也不含 ui/）
- **影响**：2053 行的 `watchlist_tab.py`、2032 行的 `lhb_tab.py`、2199 行的 `kline_window_manager.py` 全部零检查；`app/bootstrap/startup_orchestrator.py` 的 19 个 `getattr(host, "xxx", None)` 鸭子接口（:476-576）在静态检查下零告警，主窗口方法改名即静默故障——形成"检查最严的地方代码最好、最烂的地方没人看"的倒挂。
- **建议**：复用已有的 `mypy_baseline` 棘轮机制，把 ui/tabs、ui/components、app/services 分批纳入，只增不减；宿主适配器定义 `Protocol`（项目已有 `CachedEarningsRowsPort` 先例）。

### H10.【工程】覆盖率门禁按包级平均，单文件空洞被稀释
- **位置**：`scripts/coverage_budget_check.py:9-19`（仅 6 个包预算，单文件预算只有 1 个）+ `ci.yml:159`
- **影响**：2000 行的大文件测 60% 也能被同包小文件稀释到 90% 通过。交叉比对确认的零覆盖模块：`app/services/watchlist_indicator_service.py`（178 行核心业务）、`ui/components/frame_task_scheduler.py`（UI 线程调度）、`app/services/http_client_service.py`、`ui/components/vector_icons.py`、`infra/market_data/vcp_scan_adapter.py`。
- **建议**：给行数前 25 名热点文件设单文件预算；或引入 diff-cover 做 PR 级增量门禁；优先补 `watchlist_indicator_service` 行为测试。

### H11.【工程】pytest 零配置 + CI 回归子集硬编码文件清单
- **位置**：`pyproject.toml` 无 `[tool.pytest.ini_options]`；`ci.yml:98-153` 四个 suite 手工列举约 45 个测试路径
- **影响**：① 测试改名/移动即静默脱离门禁；② DeprecationWarning 不被拦截，PyQt6 升级成本累积；③ 无法支撑分层回归选择。
- **建议**：注册 marker（arch/service/runtime/perf/smoke）替代文件清单；`filterwarnings = ["error"]` 配 ignore 白名单；测试目录按 `tests/{app,core,domains,infra,ui,vcp}/` 分层归组。

---

## 二、中严重度问题（20 项）

### M1.【安全】SSRF 防护不解析 DNS，私网阻断可被域名绕过
- **位置**：`infra/http_safety.py:32-49`（`_is_blocked_host` 仅对字面量 IP 判断，`except ValueError: return False`）
- **影响**：A 记录指向 127.0.0.1/169.254.169.254 的域名可直接通过。当前 URL 均硬编码，可利用性低，故定级中。
- **建议**：连接前解析域名并对全部 A/AAAA 记录复检；补充 100.64.0.0/10 CGNAT 段。

### M2.【安全】亚洲行情 HTTP 未启用 allowed_hosts 白名单
- **位置**：`infra/market_data/asian_market_http.py:55-60`（未传 `allowed_hosts`，而 `http_safety.py:62-64` 空集即跳过白名单校验）；对照 `lhb_provider.py:20` 是正确示范
- **建议**：为各 provider 传入固定主机集合。一行改动，收益大。

### M3.【安全】pyautogui / Ctrl+V 注入存在前台窗口错配时间窗
- **位置**：`infra/navigation/external_terminal_navigator.py:249-255`（`sleep(0.3/0.08)` 后不再复检前台 hwnd 即 `pyautogui.write`）；`app/services/ui_navigation_service.py:380-405`（0.45s 固定延时后 Ctrl+V，且无条件覆写用户剪贴板）
- **影响**：用户在时间窗内切换窗口（如密码框）时，键击/粘贴内容落入错误窗口。
- **建议**：注入前重校验 `GetForegroundWindow() == 目标 hwnd`；粘贴后恢复原剪贴板；优先保留 WM_SETTEXT/WM_PASTE 消息路径，pyautogui 降为最后手段。

### M4.【架构】core/logger.py 日志热路径反向 import domains 并发射 Qt 信号
- **位置**：`core/logger.py:324`（每条 INFO 日志执行 `from domains.runtime import domain_events` + `event_bus.sig_system_log.emit`）
- **影响**：最基础设施耦合领域事件总线，是 core→domains 最危险的边；子进程/无 Qt 环境行为不确定。
- **建议**：`logger.set_frontend_sink(callable)` 注册回调反转依赖，由 app 启动时注入。

### M5.【架构】domains/runtime 事件总线耦合 PyQt6 且模块导入期实例化单例
- **位置**：`domains/runtime/domain_events.py:3`（`from PyQt6.QtCore import QObject, pyqtSignal`，导入即实例化）；同类：`core/global_store.py:134`（导入即实例化 QObject 单例）、`infra/storage/data_store.py:316-317`（import 即建库跑迁移）
- **影响**：headless/worker 进程导入即需 Qt/SQLite；测试隔离困难。
- **建议**：改惰性 `get_xxx()` + 显式生命周期；或引入纯 Python Observer 接口，Qt 适配器放 app 层。

### M6.【性能】东财行情二分重试无总次数上限，存在请求放大
- **位置**：`vcp/data_provider_realtime.py:231-268`（非断连类错误无条件二分递归到 `min_batch_size`）
- **影响**：上游持续 4xx/5xx 时一次 N 只请求最多放大为约 `2N/min_batch` 次，可能触发风控雪崩；与冷却机制的启用条件存在空档。
- **建议**：加"连续 3 批失败即整批放弃并进入冷却"的总预算。

### M7.【性能】RPS 内存缓存键不含数据版本
- **位置**：`vcp/polars_engine.py:310-314,431-434`（`cache_key = (start, end)`）
- **影响**：同交易日重新同步/除权更新后仍命中旧矩阵，RPS 不反映新数据；命中日志用 warning 级别污染告警。
- **建议**：缓存键加 `snapshot_trade_date`；命中日志降 debug。

### M8.【性能】LHB 缓存命中仍 deepcopy 整个 payload 且缓存无上限
- **位置**：`infra/storage/lhb_pool_repository.py:69-84`
- **建议**：返回只读视图/不可变结构；缓存按 LRU 管理。同类：`infra/runtime_monitor/monitor.py:70-80` append 后全量重读文件（max 4096 条），改内存缓冲 + 定期 compaction；`vcp/data_provider_history_mixin.py:711` 命中即 `res.copy()` 全帧复制。

### M9.【代码质量】`domains/earnings/engine.py` 1909 行上帝模块 + 全局猴子补丁劫持 tqdm
- **位置**：`domains/earnings/engine.py`（混合 7 种职责：HTTP 抓取/纯计算/重试/编排/SQLite 持久化）；`:52-85` 猴子补丁 `tqdm.__init__/update`，同进程任何库的进度条被强制静默
- **建议**：按 providers/metrics/pipeline/state 拆分；tqdm 补丁改为 stderr 重定向上下文管理器。

### M10.【代码质量】巨石文件群
- **位置**：`ui/components/kline_window_manager.py`（2199 行窗口池状态机，15+ 布尔状态位，单例非 QObject 却持有 QWebEnginePage/QTimer——任一 fail-closed 分支遗漏即泄漏一个 ~50-100MB 渲染进程）、`ui/components/table_controls.py`（2191 行上帝类，每张表 4 个 QTimer）、`ui/tabs/watchlist_tab.py`（2053 行、16 个 QTimer）、`ui/tabs/lhb_tab.py`（2032 行）；scripts/ 另有 4 个 2000-5651 行单体脚本
- **建议**：kline 池收敛为显式状态机 + 单一 QObject owner；table_controls 拆 RepaintGuard/FlashAnimator/TooltipPolicy/ViewStateRestore 组合对象；Tab 拆出刷新 controller；scripts 拆为 `scripts/perf/` 包并合并 round4/round5。

### M11.【代码质量】拼音/中文/转义字符串作为数据层契约 key
- **位置**：`domains/quotes/snapshot.py:82-83`（`zongguben` 与 `_zongguben` 双 key 同值双写）、`app/services/ui_lhb_pool_service.py:500-501`（`rec.get("上榜净买额(万)")` 显示文案做数据键）、`ui/models/stock_table_model.py:43-72`（`LEGACY_MOJIBAKE_CODE_KEY = "\u6d60\uff47\u721c"` 证明历史上已发生 GBK/UTF-8 编码腐化）、`app/services/kline_open_service.py:10-16`（`\u4ee3\u7801` 等 6 个转义中文常量）
- **影响**：表头改名即断数据链路；grep 中文名搜不到；编码腐化已发生过一次。
- **建议**：数据层统一 ASCII key（code/total_shares/net_buy_wan），中文仅留展示层映射，读取侧双读兼容过渡。

### M12.【代码质量】重复代码多处漂移
- **位置**：原子写盘 6 处近似实现（`asian_market_cache.py` vs `json_cache_repository.py` 逐行相同，缺陷要改 6 处）；可取消子进程 2 套（`foreign_block_provider.py:74-117` vs `process_runner.py:108-154`，已漂移且前者缺 `wait()` 兜底）；RPS 覆盖率阈值/统计函数 3 处复制（`core/cache_manager.py:55-63` vs `app/services/ui_lhb_pool_service.py:314-324`）；`RtTableModel` 与 `StockTableModel` 渲染 resolver 大段近似复制且实现微异；`vcp/data_provider_history_mixin.py:349,559` 同一映射定义两遍（一处 unicode 转义一处明文）；`asian_kline_fetcher.py:100-153` 与 `global_earnings_calendar/service.py:122-123` 排除名单重复定义
- **建议**：分别收敛到单一实现/单一常量模块。

### M13.【代码质量】超长函数与闭包嵌套
- **位置**：`app/bootstrap/startup_orchestrator.py:762-869`（108 行 `deferred_data_load`，内嵌 4 层闭包共享状态，全项目最长函数）；`app/services/ui_lhb_pool_service.py:338-397`（方法体内定义适配器类，每次调用重建）
- **建议**：拆为阶段方法化的类；适配器类提升为模块级。

### M14.【可维护性】宽泛异常元组泛滥（约 80+ 处）
- **位置**：core+app 21 处、domains/vcp/earnings 58 处形如 `except (AttributeError, OSError, RuntimeError, TypeError, ValueError)`；多数捕获点未带 `exc_info`
- **影响**：TypeError/ValueError 往往是真 bug（字段拼错表现为静默降级），与 Qt 对象失效错误混在同一元组无法区分。
- **建议**：各模块仿照 `vcp/polars_engine.py:21-48` 的 `_POLARS_DATA_ERRORS` 定义具名异常常量；Qt 失效（RuntimeError/AttributeError）可 suppress，业务异常至少 warning + exc_info；利用已存在但使用率低的 `core/exceptions.py` 建立异常基类体系。

### M15.【可维护性】日志级别误用与降级无埋点
- **位置**：`vcp/data_provider_history_mixin.py:817`（成功路径打 `_log.error`，运维面板会把每次正常同步计为故障）；`domains/earnings/engine.py` 15+ 处 emoji 日志（GBK 控制台乱码）；`infra/storage/industry_chain_repository.py:158-159`（缓存写失败静默 return）；`ui/components/stock_context_menu.py:132-133`（失败静默返回 False）；fallback/降级路径无计数器
- **建议**：`:817` 改 info；去装饰性 emoji；降级路径接入 `emit_structured_log` 事件计数。

### M16.【可维护性】跨仓库 sys.path 注入 + 硬编码本机路径
- **位置**：`domains/global_earnings_calendar/service.py:187-201`（`sys.path.insert(0, project_root.parent / "每日战报" / "每日战报")` 后 import 外部 `industry_dict`）；`vcp/utils.py:1-2,26`（`D:\vcp_qt\vcp_tdx_config.json` 候选路径，注释自称"零逻辑变更"迁移）
- **影响**：机器布局强假设；同名模块可反向遮蔽本项目；缺失时静默返回空 universe（用户只见"日历变空"）。
- **建议**：外部映射数据导出为 JSON 放入 `data/`；`insert(0,...)` 至少改 `append`。

### M17.【架构】`StartupHostAdapter` 134 处 getattr 鸭子接口 + `sys.modules` 外的另一类反射 hack
- **位置**：`app/bootstrap/startup_orchestrator.py:476-576`（19 个 property 全部 `getattr(self._main_window, "xxx", None)`）
- **影响**：主窗口方法改名零告警，运行时静默返回 None（如 workspace 取不到即静默跳过），排障成本极高。
- **建议**：定义 `Protocol` + TYPE_CHECKING 导入真实类型，getattr 仅保留给可选能力探测。

### M18.【测试】flaky 风险点
- **位置**：18 个测试文件使用 `time.sleep`/`QThread.msleep`；6 个文件用 `date.today() + timedelta` 构造日期（周五与周日运行行为不同）；`test_f5_process_pipeline.py:515` 起真实 30 秒子进程
- **建议**：引入 freezegun/注入时钟；等待改条件轮询；跨年用例显式固定日期。

### M19.【CI】Windows（实际发布平台）只跑 3 个测试文件；无 pip 缓存/artifact 上传/concurrency 取消
- **位置**：`ci.yml:222-240`（windows-smoke 仅 3 文件）、全文无 cache/upload/concurrency
- **建议**：Windows job 加平台强相关子集（windows_autostart/build_windows_script/single_instance），考虑 nightly 全量；`setup-python` cache: pip；上传 coverage.json；加 concurrency group。

### M20.【工程配置】ruff 规则过宽 + coverage 配置残缺 + 本地依赖漂移
- **位置**：`pyproject.toml:12-19`（ignore E402/E501/E701/E702，无 B/UP/SIM 规则组）；`[tool.coverage.run]` 仅一行（无 source/omit/exclude_lines，本地与 CI 覆盖率不可比）；`requirements-dev.txt` 全 `>=` 而 uv.lock 未在 CI 使用
- **建议**：E402 改 per-file-ignores 精确豁免（项目冷启动预算确实需要延迟导入）；加 B/UP/SIM；补全 coverage 配置；README 明示本地安装带 `-c constraints-py314-windows.txt`。

---

## 三、低严重度问题（12 项）

| # | 问题 | 位置 | 建议 |
|---|---|---|---|
| L1 | akshare 模块 monkeypatch 只覆盖 `.get`，升级即静默失效回落无防护路径 | `infra/market_data/lhb_provider.py:17-23` | 补 post/head；加自检断言 |
| L2 | ticker 未做字符白名单即拼 URL 路径（`../` 可注入路径） | `infra/market_data/providers/asian_http_provider.py:58,127,180`、`quote_normalizer.py:52` | `ticker_base` 加 `[A-Za-z0-9]` 白名单或 `urllib.parse.quote` |
| L3 | 硬编码东财公开 web token 两处重复 + 15+ 处浏览器 UA 散落 | `vcp/engine_external.py:78`、`data_provider_quotes.py:198` 等 | 提取常量并注释"公开参数非机密"；UA 集中到 user_agents.py |
| L4 | SEC EDGAR 默认 UA 用占位邮箱 contact@example.com | `domains/global_earnings_calendar/providers/sec.py:53` | 引导配置 SEC_USER_AGENT，避免被 SEC 限流 |
| L5 | 全仓仅有的 3 处 `except Exception: pass` 在 F5 清理路径，无痕 | `ui/services/f5_job_controller.py:197-221` | 加 `log.debug` 区分正常终止与 terminate 失败 |
| L6 | earnings 子进程结果输出未过脱敏器（姊妹模块已过） | `domains/earnings/refresh_cache.py:134` vs `global_earnings_calendar/refresh_cache.py:99` | 统一走 `redact_sensitive_data`（建议该函数上移 infra） |
| L7 | `_utils.py` 工作线程吞 `BaseException`（连 KeyboardInterrupt 一起吞） | `domains/global_earnings_calendar/providers/_utils.py:35-38` | 收窄为 `except Exception` |
| L8 | 无 parent 的 QTimer 两处 + 2 处顺序不确定的 `list(set(...))` | `ui/theme.py:546`、`kline_window_manager.py:1948`；`stock_table_model.py:666` | 传 parent；改 `sorted(set(...))`（拖拽插入位置偶发偏移一行） |
| L9 | `spawn_detached_process` 名不符实（未真正 detach） | `infra/tasks/process_runner.py:171-172` | 实现或删除别名 |
| L10 | `_run_async_local_quote_refresh` 靠异常消息文本做兼容回退 | `ui/tabs/watchlist_tab.py:1282-1290` | 改 `inspect.signature` 一次性探测 |
| L11 | viewmodels/presenters/shell 空壳目录误导分层认知；ui/services 与 app/services 双层并存 | `ui/viewmodels/`、`ui/shell/`（空）、`ui/services/` | 补齐或删空壳并在架构文档写明真实分层 |
| L12 | conftest 的 Qt 检测靠 7 个硬编码前缀，ui.presenters/signals/styles/services 不在列，漏判即 segfault | `tests/conftest.py:34-49` | 改 `ui.` 全覆盖 + 豁免清单，或引入 pytest-qt |

另记一处**特别值得肯定**、审计中确认不建议改动的亮点（供重构时保留）：`vcp/polars_engine.py` 的原子 Parquet 写、`foreign_block_provider` 的 AkShare 隔离子进程、`central_quotes_worker` 的单飞行+熔断+盘后离线快照设计、conftest 的 QSettings/DB/日志三重临时目录隔离、"审计审计者"式的 `test_ci_workflow.py`/`test_coverage_budget_check.py`。

---

## 四、优化行动清单（按优先级排序）

### 第一批：立即执行（本周内，均为小改动大收益）

| # | 行动 | 对应问题 | 预估工作量 |
|---|---|---|---|
| 1 | 修复 PowerShell Bypass 执行可写脚本：加文件指纹校验（复用 `file_integrity.py`）或改仓库内置脚本 | H1 | 0.5 天 |
| 2 | `_collect_thread_health` 移入心跳分支（一行移动） | H7 | 10 分钟 |
| 3 | `asian_market_http.py` 各 provider 补 `allowed_hosts` 白名单 | M2 | 0.5 天 |
| 4 | `data_provider_history_mixin.py:817` error→info；f5_job_controller 三处 pass 加 debug 日志；stock_context_menu 加 warning | M15/L5 | 1 小时 |
| 5 | `domains/__init__.py` 移除 `EarningsScheduler` re-export（切断 domains→app 最显眼的倒置边） | H2（部分） | 30 分钟 |
| 6 | RPS 缓存键加 snapshot 日期；二分重试加总失败预算 | M6/M7 | 1 天 |
| 7 | GLOBAL_ASIAN_RT_CACHE 与 earnings 两级缓存加锁 | H5 | 1 天 |

### 第二批：本迭代（1-2 周，热路径性能专项）

| # | 行动 | 对应问题 | 预估工作量 |
|---|---|---|---|
| 8 | 表格模型三重优化：默认启用 presentation cache、删除 layoutChanged 后整表 dataChanged、增量路径选择性水合——用 `recordMetric` 做 A/B 验证 | H4 | 3 天 |
| 9 | GlobalStore.merge_quotes 改 entry 级合并 + 已冻结复用 | H3 | 3 天 |
| 10 | 板块 RPS 用 `search_sorted` 替换逐行循环 + 别名展开消除双写 | H6 | 2 天 |
| 11 | pyautogui/Ctrl+V 注入前重校验前台 hwnd；粘贴后恢复剪贴板 | M3 | 1 天 |

### 第三批：短期（本季度，工程护栏补强）

| # | 行动 | 对应问题 | 预估工作量 |
|---|---|---|---|
| 12 | pytest 配置补齐：marker 注册 + filterwarnings=error + 目录分层；CI 硬编码清单 marker 化 | H11/M18 | 2 天 |
| 13 | mypy/pyright 棘轮扩围：ui/tabs → ui/components → app/services 分批纳入 | H9 | 持续 |
| 14 | 覆盖率：前 25 热点文件设单文件预算；补 watchlist_indicator_service、frame_task_scheduler 行为测试 | H10 | 3 天 |
| 15 | 一次性消灭 `sys.modules` 替换：7 个 core shim + vcp/earnings 壳改真 re-export，删除顶层 earnings/ | H8 | 2 天 |
| 16 | 重复代码收敛：原子写盘收敛到 json_cache_repository、可取消子进程复用 process_runner、RPS 阈值抽 domains/rps/policy.py、渲染 resolver 抽 CellStyleResolver | M12 | 4 天 |
| 17 | CI 效率：pip cache、coverage artifact、concurrency；Windows job 加平台子集 | M19 | 1 天 |
| 18 | 异常处理规范化：各模块定义具名异常常量，warning 级捕获统一加 exc_info | M14 | 持续 |

### 第四批：中期（季度级，结构性重构，按迭代逐步偿还）

| # | 行动 | 对应问题 |
|---|---|---|
| 19 | 依赖方向修复：f5 契约下沉 core；logger 改回调注入；runtime_health 构造注入；global_store/data_store/domain_events 惰性化 | H2/M4/M5 |
| 20 | vcp 真实逻辑一次性迁入 infra/market_data/，消除 vcp↔infra 循环 | H2 |
| 21 | 巨石文件拆分：earnings/engine.py 按 providers/metrics/pipeline/state；kline_window_manager 状态机化；table_controls/watchlist_tab/lhb_tab 职责拆分；scripts/perf 拆包合并 round4/5 | M9/M10 |
| 22 | 数据契约 ASCII 化：列枚举 + 中文表头映射层，zongguben→total_shares 双读过渡 | M11 |
| 23 | tqdm 猴子补丁、跨仓库 sys.path、硬编码本机路径等外部依赖数据化 | M9/M16 |
| 24 | SSRF 补 DNS 解析复检 + CGNAT 段；ticker 字符白名单 | M1/L2 |

### 度量与验收建议

- 第一/二批的每项性能改动，用项目自带的 `recordMetric`/`perf_budget_check.py` 做前后对比（特别是 paint 次数与行情合并耗时）
- 每完成一批，跑一次 `scripts/runtime_health_stability_suite.py --mode short` 确认无行为回归
- 分层修复（第三、四批）每步都应让 `test_architecture_boundaries.py` 的白名单**变短**而不是变长——白名单长度本身就是架构债务的仪表盘

---

*本报告由 6 路并行深度审计（core/app、domains/vcp/earnings、infra+安全、ui、测试与CI、全仓横切安全）汇总去重而成，所有问题均附文件级证据。*
