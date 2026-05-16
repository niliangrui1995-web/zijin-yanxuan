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

带性能预算报告：

```powershell
.\.venv\Scripts\python.exe scripts\project_audit.py --runtime-health-report tmp\runtime_health_report.json
```

## 检查范围

完整审计当前包括：

- Ruff 静态检查
- UTF-8 / 疑似文本异常检查
- `compileall` 编译检查
- `pip check` 依赖一致性检查
- 架构边界测试
- 完整 pytest
- 运行环境自检
- 可选性能预算报告校验

## CI 入口

CI 使用 `requirements-dev.txt` 安装测试工具。Linux job 继续跑现有架构、服务、运行时和性能护栏；Windows smoke job 覆盖架构边界、运行环境自检和审计命令契约，避免桌面端项目只在 Linux 上验证。
