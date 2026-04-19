# 紫金研选 UI/UX 审计整改关闭清单

日期：2026-04-19

对应整改方案：`C:\Users\Administrator\Desktop\紫金研选-UIUX审计整改方案-2026-04-19.md`

结论：

- 方案第 0 到第 5 阶段的代码整改项已全部收口。
- 自动验证已全部通过。
- 高频页面走查记录已补齐，见 [uiux-manual-walkthrough-2026-04-19.md](/D:/vcp_hunter/紫金研选/docs/uiux-manual-walkthrough-2026-04-19.md)。
- 按仓库规则，Git 备份提交需等待用户最终确认后执行。

## 关闭项

### 阶段 0：基线收口

- 修复 `ruff` 已知告警。
- 修复 `pytest` 已知失败用例。
- 保留 `python scripts\check_utf8.py` 作为编码护栏。

### 阶段 1：工具栏与页面头部响应式底座

- `ui/tabs/base_stock_tab.py` 已真正启用流式工具栏分组布局。
- `watchlist`、`fund_holdings`、`rt_monitor`、`scan` 页面的固定宽高已收口为更合理的最小/最大宽度与自适应策略。

### 阶段 2：可达性与键盘路径修复

- `ui/kline_window_qt.py` 恢复上一只、下一只、关注按钮键盘焦点。
- `ui/styles/global_qss.py` 补齐按钮和输入控件焦点态。
- 高频搜索与筛选控件已补 `accessibleName` / `accessibleDescription`。

### 阶段 3：主题对比度与视觉一致性

- `ui/theme.py` 已提高亮色主题 `TEXT_MUTED` / `TAB_TEXT` 对比度。
- `ui/components/toast_widget.py` 已并回主题 token。
- `warning` 等级已补明确图标和状态语义。

### 阶段 4：页面级收口与模式统一

- `ui/tabs/watchlist_tab.py` 头部摘要已统一为结果计数、筛选摘要、附加指标就绪状态、最近更新时间。
- `ui/tabs/rt_monitor_tab.py` 头部摘要已统一为结果计数、筛选摘要、待突破池规模、最近更新时间、运行状态与下一步动作。
- `ui/tabs/scan_tab.py` 保持结果计数与最近触发日期摘要。
- `ui/tabs/fund_holdings_tab.py` 保持主体季度与最近同步摘要。
- `ui/kline_window_qt.py` 头部按钮与摘要带已保持统一视觉语言。

### 阶段 5：回归保护与最终验收

- 已补针对性测试：
  - 工具栏/页面摘要
  - 焦点策略与可达性命名
  - 主题 token 对比度
  - Toast 语义与图标
- 已补高频页面走查记录。

## 本轮关键修改文件

- `ui/tabs/base_stock_tab.py`
- `ui/tabs/watchlist_tab.py`
- `ui/tabs/rt_monitor_tab.py`
- `ui/tabs/scan_tab.py`
- `ui/tabs/fund_holdings_tab.py`
- `ui/kline_window_qt.py`
- `ui/styles/global_qss.py`
- `ui/theme.py`
- `ui/components/toast_widget.py`
- `tests/test_watchlist_tab.py`
- `tests/test_rt_monitor_tab.py`
- `tests/test_scan_tab.py`
- `tests/test_fund_holdings_tab.py`
- `tests/test_kline_window_header.py`
- `tests/test_theme_tokens.py`
- `tests/test_toast_widget.py`
- `tests/test_data_provider_realtime_failover.py`

## 验证结果

- `pytest -q`
  - 结果：`271 passed in 4.95s`
- `ruff check .`
  - 结果：`All checks passed!`
- `python scripts\check_utf8.py`
  - 结果：`UTF-8 检查通过`

## 备注

- 本地 UI 自检截图目录：`C:\Users\Administrator\AppData\Local\Temp\zijin_uiux_audit_20260419`
- 该目录用于本轮走查证据，不纳入仓库版本管理。
- Git 备份记录将在用户确认“没问题 / 好了 / 可以”后按仓库规则自动执行。
