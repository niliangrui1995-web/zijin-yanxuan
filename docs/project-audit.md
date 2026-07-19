# 项目审计入口

最后校验：2026-07-19

本文档登记当前仓库的统一审计命令。后续每轮结构调整、性能治理或依赖升级后，优先使用同一套入口判断是否出现 bug、功能缺失或架构边界退化。

## 常用命令

完整审计：

```powershell
.\.venv\Scripts\python.exe scripts\project_audit.py
```

快速审计：

```powershell
.\.venv\Scripts\python.exe scripts\project_audit.py --quick
```

CI 和需要一次看清全部红点的本地诊断使用 `--keep-going`；该参数会执行完所有已选门禁，最后汇总失败项并返回非零状态：

```powershell
.\.venv\Scripts\python.exe scripts\project_audit.py --quick --keep-going
```

只查看将要执行的检查项：

```powershell
.\.venv\Scripts\python.exe scripts\project_audit.py --quick --list
```

本地审计统一使用仓库内 `.\.venv\Scripts\python.exe`。如果直接用系统 `python` 运行同一命令时提示缺少 Ruff、Pyright、`pip-audit` 或其他审计工具，先按解释器选择错误处理，不计为代码门禁失败。

快速审计并显式追加短运行健康预算闸门：

```powershell
.\.venv\Scripts\python.exe scripts\project_audit.py --quick --runtime-health-short
```

短运行健康预算会先证明 11 个 Tab 按 registry 依赖顺序单步后台预载、全部加载且无失败/超时，再覆盖数据血缘、Timer/线程/事件订阅增长和收尾内存稳定性；其中 `foreign_block`、`fund_holdings`、`earnings` 作为重点 Tab 有更严格的交互耗时预算，用于持续盯住情报源重页面的退化。隐藏预载网络证据只接受唯一 `after_background_preload` 样本，覆盖必须精确为 10 个数据 Tab，`system_log` 必须且只能作为排除项；每个数据 Tab 恰好一行、loaded，`network_capable` 与实例闩锁 `triggered_network` 都必须是严格 bool。缺失/额外/重复/交叉分区、`lineage_error` 或任一实际联网都会 fail-closed。

需要核对真实 Windows 窗口、真实首帧、首个 Tab 恢复、中央行情和 K 线 WebEngine 时，运行生产等价门禁：

```powershell
.\.venv\Scripts\python.exe scripts\project_audit.py --quick --keep-going --runtime-health-production
```

该门禁使用原生 Qt 并实际显示窗口，同时以 `--real-f5` 启动真实隔离子进程，要求任务事件覆盖完整计算阶段、worker PID 与父进程不同、完整 snapshot 成功激活且 post-F5 刷新收尾。定向契约还要求阻塞 handle 操作全部归 `f5-worker-monitor`，worker Polars 配额符合 `max(1,min(4,logical_cpu_count//2))`，且只在 worker 内把 `OPENBLAS_NUM_THREADS`、`OMP_NUM_THREADS`、`MKL_NUM_THREADS`、`NUMEXPR_NUM_THREADS` 固定为 1；owner shutdown 等待 activation/terminal cleanup，工作区退出则要求 preload cancellation receipts 全部 accepted/settled。退出后还要复核 F5 磁盘回执：active 完整、generation ≤ 2、无 unfinished/ready/invalid job 与临时文件、唯一 terminal job 和 active snapshot 都等于最后成功 run_id；任何遗留或完整性不匹配均 fail-closed。普通 `--runtime-health-short` 仍适合快速、可重复的 offscreen 回归；两者不能互相替代。
启动报告保留 `startup_ready_ms`（主窗口构造到真实首帧）和 `initial_tab_ready_ms`（主窗口构造到当前业务页就绪）两个 window-only 指标；同时保留 `startup_inclusive_first_paint_ms` 与 `startup_inclusive_initial_tab_ready_ms`，从 `runtime_health_stability_suite.py` 导入 `time` 后的最早模块内标记开始，包含余下健康脚本导入、Qt 运行时配置、`QApplication`、原生 dataframe 运行时和主窗口初始化。该 script-module 口径明确不含进程创建、Python 解释器启动及 `time` 自身导入，不能表述成 OS 进程级冷启动；它作为必填诊断上界，不把健康脚本自身导入成本计入应用 SLA。`startup_app_init_*` 从 `run_suite` 入口记录真实应用初始化，生产门禁对这组字段应用固定 `3500 ms` 首帧、`5500 ms` 初始 Tab 就绪预算，并要求三层证据完整且顺序合法。Tab 首开耗时不包含固定 settle 等待。请求 K 线循环时，门禁强制要求每轮都成功打开并关闭，`blocked=0`，不能把 WebEngine 不可用误报为通过。关闭阶段还必须验证后台任务、QThreadPool、watchdog 和 WebEngine 子进程归零，允许最多 1 个已请求退出且无活跃任务的 owned QThread 短尾，且主窗口关闭不超过 5 秒。

production gate 的默认 Tab 集直接来自 registry 的 `health_probe_order`，用真实 `shell_nav` 语义覆盖全部 11 个稳定 key。每页检查首开和业务 runtime，随后等待 Tab 异步尾部归零，再执行真实 F5、行情、K 线和关闭验证。通过条件包括 11 个页面全部成功、`post_tab_idle=ok`、F5 worker PID 与父进程不同、完整 snapshot 原子激活，以及退出资源预算全部满足。

定向回归还固定三条启动竞争边界：`AutoRefreshScheduler` 在 preload 完全 settled 后必须继续等待 60 秒；AI 产业链周期涨幅正常路径只读取有限收盘尾值，窄接口异常时才回退旧批量接口；关注池 StockContext 捕获必须显式省略 RPS，且 RPS loader 只能在已排队的 worker callable 执行后调用。内嵌 RPS 与 worker loader 两条路径的完整指标结果必须一致。这些定向契约只能证明局部语义，不能替代原生 production health 或 30 分钟 soak。

`--output path.json` 同步产生 `path.checkpoint.json` 和 `path.faulthandler.log`。启动样本、每个周期 sample 和窗口可见性失败都会立即原子刷新 checkpoint。最终验收既检查聚合报告，也检查 checkpoint 为 `complete`；若 native abort，则以 checkpoint 的最后确认可见时长、`current_phase/current_tab/last_completed_phase`、逐分钟 sample 和 faulthandler 作为失败证据，不能因缺少最终 JSON 误判为通过。未处理 Qt 回调异常同样是 fail-closed 失败项。

## K 线 10 轮精确门禁

```powershell
.\.venv\Scripts\python.exe scripts\kline_webengine_lifecycle_smoke.py --native-qt --provider-mode production-local --code 000001 --name 平安银行 --switch-code 000002 --switch-name 万科A --cycles 10 --minimum-cycles 10 --fail-on-error --output tmp\kline_webengine_lifecycle_smoke.json
.\.venv\Scripts\python.exe scripts\perf_budget_check.py --kline-lifecycle-report tmp\kline_webengine_lifecycle_smoke.json --output tmp\kline_webengine_lifecycle_budget.json
```

该门禁必须使用原生可见 Qt，以实际 ECharts `rendered` 回执后的 frame owner 作为 `chart_ready`，不能用窗口可见、Browser 存在或 `setOption()` 返回代替。预算固定为：壳 P95 ≤ 120ms、预热 `browser_ready` P95 ≤ 500ms、A 股 `chart_ready` P50/P95 ≤ 800/1500ms、至少 10 个真实缓存切股样本且 P95 ≤ 300ms、单次打开最大 GUI stall ≤ 100ms、critical/event-loop critical stall 均为 0；cold-first-open、常规预热轮和正式 10 轮分别检查 stall/critical，任一阶段缺诊断即 fail-closed。10 轮后活动 Chart View、Task、Timer、Receiver、线程和 WebEngine 子进程净增长为 0，RSS 净增长 ≤ 24MB。最终 shutdown 后 WebEngine 子进程必须为 0。

完整应用 production health 另对每次真实打开生成隔离回执：打开前 reset 探针，范围必须精确为 `kline_open_to_chart_ready`，真实 `chart_ready` 后跨两个事件循环 tick 再封存。预算会同时核对打开次数、索引、scope、reset 标志、最大 stall 与 critical/event-loop critical，避免用全局 stall 报告替代用户单次打开证据。

诊断上要区分两层：逻辑 readiness 固定为 `shell_ready → browser_ready → data_ready → js_ready → chart_ready → first_interaction`；冷 Browser 的 `create → handoff/skip → attach → WebEngine shell` 是 `browser_ready` 内部的四个 owner-owned 切片，必须提供 `browser_create_ms`、`page_handoff_*`、`browser_attach_sync_ms`、`load_shell_*`、`max_sync_slice_ms` 和 `pipeline_total_ms`。完整物理窗口复用时必须同时出现 `full_window_reused=true` 与这些字段的 `0.0`，其含义是冷路径明确跳过，而不是采样缺失。功能矩阵继续保留 MA10/20/50/150/200，明确排除 MA5 与历史 B/S/T。

## 30/60 分钟原生可见长稳

```powershell
.\.venv\Scripts\python.exe scripts\runtime_health_stability_suite.py --mode soak30 --native-qt --show-window --startup-enabled --background-prewarm --kline-prewarm-enabled --central-quotes-enabled --allow-controlled-probe-tab-loads --idle-minutes 30 --tab-cycles 2 --f5-cycles 1 --real-f5 --real-f5-timeout-seconds 1800 --quote-cycles 2 --kline-cycles 1 --kline-code 000001 --kline-name 平安银行 --post-tab-idle-timeout-ms 5000 --background-preload-timeout-ms 600000 --sample-every-seconds 60 --fail-on-budget --sample-output-dir tmp\runtime_health_samples_soak30 --output tmp\runtime_health_soak30.json
.\.venv\Scripts\python.exe scripts\runtime_health_stability_suite.py --mode soak60 --native-qt --show-window --startup-enabled --background-prewarm --kline-prewarm-enabled --central-quotes-enabled --allow-controlled-probe-tab-loads --idle-minutes 60 --tab-cycles 2 --f5-cycles 1 --real-f5 --real-f5-timeout-seconds 1800 --quote-cycles 2 --kline-cycles 1 --kline-code 000001 --kline-name 平安银行 --post-tab-idle-timeout-ms 5000 --background-preload-timeout-ms 600000 --sample-every-seconds 60 --fail-on-budget --sample-output-dir tmp\runtime_health_samples_soak60 --output tmp\runtime_health_soak60.json
```

验收要求窗口全程可见且 observation 至少达到 30/60 分钟；`soak30/long` 与 `soak60` 的最低时长由模式固定为 1800/3600 秒，不能通过覆盖 idle 参数缩短，采样间隔必须 ≤ 60 秒且没有缺样、乱序或断档。11 Tab 必须在完全不点击的前提下按 `watchlist → system_log → ai_industry_chain → na_daily → scan → foreign_block → earnings → fund_holdings → lhb → asian_market → stock_candidates` 自动 ready，并发上限 1；结束时 active/cancelling key、剩余/提权队列为空，Timer inactive、active step 为 0，全部 shutdown cancellation receipts 已物理结算。真实 F5 worker PID 与父进程不同且阶段事件、snapshot 原子激活、post-F5 收尾完整；K 线达到真实 `chart_ready`；UI stall、RSS 尾部、线程、Task、Timer、Receiver、WebEngine 和 shutdown receipt 全部满足 `perf_budget_check.py` 的固定预算。checkpoint 必须为 `complete`。

## 当前证据状态

| 证据 | 当前状态 | 说明 |
| --- | --- | --- |
| `tmp/goal_validation/20260717-123322/kline_lifecycle_production_final_verified.json` + `tmp/goal_validation/20260717-123322/kline_lifecycle_budget_final_verified.json` | `ok` | 真实 `TdxDataProvider` 的 `production-local` 离线只读路径，000001/000002 各 500 行、截至 2026-07-16、网络请求 0；10/10 打开关闭、缓存切股 10/10；壳/Browser P95 24.809/43.495ms，`chart_ready` P50/P95 194.806/234.337ms，缓存切股 P95 239.184ms；冷态、预热和正式轮次最大 stall 66.698ms、critical 0；稳态 RSS -8.406MB、线程 -1，Task/Timer/Receiver/WebEngine 净增长 0，shutdown WebEngine 0 |
| `tmp/goal_validation/20260717-123322/runtime_health_short_after_ai_fix.json` | `ok` | 报告及内嵌预算均为 `ok`；首帧/初始 Tab 621.330/1004.554ms，inclusive 2237.790/2621.014ms；11 Tab 计划/开始/完成顺序一致、并发 1、无失败/超时/剩余；精确 10+1 血缘分区、隐藏联网触发 0；真实 F5 父/worker PID 12384/27984、snapshot `bec242b266d0492db70871369b6f9cde` 原子激活；K 线 `chart_ready` 537.572ms、首交互 611.528ms；37.832ms 干净退出、WebEngine 0、未处理 UI 异常 0 |
| 本轮 30 分钟原生可见窗口 soak | 待主线程回填 | 报告路径：`__FINAL_SOAK_REPORT_PATH_PENDING__`。运行结束前不得写成通过；回填时同时核对 JSON、`complete` checkpoint、逐分钟 samples、faulthandler、可见时长与预算结果 |

## 其他可选门禁

快速审计并显式追加依赖/供应链 JSON 报告：

```powershell
.\.venv\Scripts\python.exe scripts\project_audit.py --quick --dependency-audit
```

该入口会以严格模式调用 `pip-audit`：如果工具缺失、超时、运行失败或发现漏洞，命令都会失败，避免供应链审计被静默跳过。

快速审计并显式追加分阶段 Ruff 扩展规则：

```powershell
.\.venv\Scripts\python.exe scripts\project_audit.py --quick --extended-ruff
```

`--extended-ruff` 先启用当前仓库已经清零的 Bugbear、simplify、comprehensions 和部分 Ruff 规则，避免 `RUF001/RUF002/RUF003` 这类中文标点误伤进入默认门禁。后续每轮规则清零后再把对应规则加入该集合。

带性能预算报告：

```powershell
.\.venv\Scripts\python.exe scripts\project_audit.py --runtime-health-report tmp\runtime_health_report.json
```

## 依赖复现

现有 `requirements.txt` / `requirements-dev.txt` 表达允许范围；可复现安装必须追加 Python 3.14 平台 constraints：

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt -c constraints-py314-windows.txt
```

Linux CI 使用：

```bash
python -m pip install -r requirements-dev.txt -c constraints-py314-linux.txt
```

两个 constraints 文件由文件头记录的 `uv pip compile` 命令生成，升级依赖时同时刷新并分别验证。项目不再忽略标准锁文件；若后续引入 `pylock.toml` 或 `uv.lock`，应提交到仓库，而不是只保留本地副本。

## 检查范围

完整审计当前包括：

- Ruff 静态检查
- 可选分阶段 Ruff 扩展规则
- UTF-8 / 疑似文本异常检查
- `git diff --check` 差异空白错误检查
- `compileall` 编译检查
- `pip check` 依赖一致性检查
- 架构边界测试
- 复杂度递减门禁：旧热点预算必须随实现缩短，新增函数上限 50 行/CC 10，新增类上限 500 行/20 方法；改动既有代码不得劣化其基线
- 完整 pytest
- 运行环境自检
- 可选短运行健康稳定性 suite（自带预算失败闸门）
- 可选 production-equivalent 原生窗口门禁（全部 11 Tab、真实 F5/K 线、退出与旁路证据）
- 可选依赖/供应链审计（严格 `pip-audit`）
- 可选性能预算报告校验
- 显式启用 `--coverage-report` 后执行六包（`app/core/domains/infra/ui/vcp`）分支覆盖率门禁：每个包的行+分支综合覆盖率下限均为 90%；`vcp/realtime_quote_runtime.py` 另有 80% 的关键文件门槛

## CI 入口

CI 固定 Python 3.14.5，并分别用 `constraints-py314-linux.txt` / `constraints-py314-windows.txt` 安装测试工具。Linux test job 先跑架构、服务、运行时和性能护栏，再跑完整 `python -m pytest -q`，避免新增测试文件遗漏。Windows smoke 覆盖架构边界、运行环境自检和审计命令契约。多门禁 job 会让各质量步骤继续执行，再由末尾汇总步骤统一失败；所有项目审计入口同时启用 `--keep-going`，因此一个红点不会遮住后续门禁。手动触发或定时 workflow 还会追加供应链和短运行健康检查。

定时/手动 workflow 另设 `latest-allowed-canary`：它故意不加载 constraints，重新解析 `requirements*.txt` 当前允许的最新版本并运行快速审计。常规门禁因此保持确定版本，依赖上游的新版本兼容性又能被独立发现，不会把版本漂移直接带进每个 PR。

顶层 `earnings/` 只保留兼容导入壳，不作为第七个实现包单独计分；真实业绩实现位于 `domains/earnings/`，已经计入 `domains` 的独立覆盖预算。六包 90% 与关键文件阈值登记在 `scripts/coverage_budget_check.py`，只能随覆盖提升上调，不能用降低预算消化回归。

CI 通过 `VCP_COMPLEXITY_BASE_REF` 把 PR 与目标分支、push 与事件前 SHA 做比较；本地脏工作树默认与 `HEAD` 比较。因此 changed/new 预算既能覆盖多提交 PR，也不会把整个历史仓库一次性纳入新代码门槛。

架构边界测试还包含 `app/`、`domains/`、`infra/` 的温和代码健康基线：新增宽泛异常需要进入显式 allowlist 或改窄异常类型，类型标注比例不能低于当前基线。UI 层 PyQt 动态代码暂不强推该规则。
