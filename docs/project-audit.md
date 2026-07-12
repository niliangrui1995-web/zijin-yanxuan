# 项目审计入口

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

短运行健康预算会覆盖默认数据 Tab 的首开、数据血缘、Timer/线程/事件订阅增长和收尾内存稳定性；其中 `foreign_block`、`fund_holdings`、`earnings` 作为重点 Tab 有更严格的首开耗时预算，用于持续盯住情报源重页面的交互退化。

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
- 可选依赖/供应链审计（严格 `pip-audit`）
- 可选性能预算报告校验
- 可选六包（`app/core/domains/infra/ui/vcp`）分支覆盖率门禁：总覆盖下限 60%，同时执行六包各自的递减预算；`vcp/realtime_quote_runtime.py` 另有 80% 的关键文件门槛

## CI 入口

CI 固定 Python 3.14.5，并分别用 `constraints-py314-linux.txt` / `constraints-py314-windows.txt` 安装测试工具。Linux test job 先跑架构、服务、运行时和性能护栏，再跑完整 `python -m pytest -q`，避免新增测试文件遗漏。Windows smoke 覆盖架构边界、运行环境自检和审计命令契约。多门禁 job 会让各质量步骤继续执行，再由末尾汇总步骤统一失败；所有项目审计入口同时启用 `--keep-going`，因此一个红点不会遮住后续门禁。手动触发或定时 workflow 还会追加供应链和短运行健康检查。

定时/手动 workflow 另设 `latest-allowed-canary`：它故意不加载 constraints，重新解析 `requirements*.txt` 当前允许的最新版本并运行快速审计。常规门禁因此保持确定版本，依赖上游的新版本兼容性又能被独立发现，不会把版本漂移直接带进每个 PR。

顶层 `earnings/` 只保留兼容导入壳，不作为第七个实现包单独计分；真实业绩实现位于 `domains/earnings/`，已经计入 `domains` 的独立覆盖预算。分包及关键文件阈值登记在 `scripts/coverage_budget_check.py`，只能随覆盖提升上调，不能用降低预算消化回归。

CI 通过 `VCP_COMPLEXITY_BASE_REF` 把 PR 与目标分支、push 与事件前 SHA 做比较；本地脏工作树默认与 `HEAD` 比较。因此 changed/new 预算既能覆盖多提交 PR，也不会把整个历史仓库一次性纳入新代码门槛。

架构边界测试还包含 `app/`、`domains/`、`infra/` 的温和代码健康基线：新增宽泛异常需要进入显式 allowlist 或改窄异常类型，类型标注比例不能低于当前基线。UI 层 PyQt 动态代码暂不强推该规则。
