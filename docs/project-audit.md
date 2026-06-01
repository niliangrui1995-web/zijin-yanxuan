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

只查看将要执行的检查项：

```powershell
.\.venv\Scripts\python.exe scripts\project_audit.py --quick --list
```

快速审计并显式追加短运行健康预算闸门：

```powershell
.\.venv\Scripts\python.exe scripts\project_audit.py --quick --runtime-health-short
```

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

现有 `requirements.txt` / `requirements-dev.txt` 继续表达运行下限。需要复现当前 Windows + Python 3.14 验证环境时，追加 constraints 文件：

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt -c constraints-py314-windows.txt
```

## 检查范围

完整审计当前包括：

- Ruff 静态检查
- 可选分阶段 Ruff 扩展规则
- UTF-8 / 疑似文本异常检查
- `git diff --check` 差异空白错误检查
- `compileall` 编译检查
- `pip check` 依赖一致性检查
- 架构边界测试
- 完整 pytest
- 运行环境自检
- 可选短运行健康稳定性 suite（自带预算失败闸门）
- 可选依赖/供应链审计（严格 `pip-audit`）
- 可选性能预算报告校验

## CI 入口

CI 使用 `requirements-dev.txt` 安装测试工具。Linux job 继续跑现有架构、服务、运行时和性能护栏；Windows smoke job 覆盖架构边界、运行环境自检和审计命令契约，避免桌面端项目只在 Linux 上验证。PR 默认不运行桌面短稳和供应链审计；手动触发或定时 workflow 会追加 `--dependency-audit --runtime-health-short`。
