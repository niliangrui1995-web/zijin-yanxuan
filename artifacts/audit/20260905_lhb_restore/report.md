# 龙虎榜恢复后连续整表重绘修复

日期：2026-09-05。范围：LHB 恢复加载、排序、模型更新和视口脏区投递。未提交、未推送。

## 原始证据

来源：`data/logs/vcp_20260905.log`，按结构化 `lhb_table_paint_ms` 计数，未把同一次绘制的 warning 和 metric 重复相加。

- 03:08:26（含）至 03:08:37（不含）：16 次整视口绘制，累计 3456.052 ms；首帧 400.336 ms，延续窗口内最大 511.940 ms（第 115 行）。
- 事件循环最大卡顿 1216.373 ms（第 75 行）。
- 第 69 行：42 行、168 个 changed indexes、`update_threshold=10000`，未超阈值；行情预计脏区约 12.68%，闪烁投递约 10.73%，最终收到整视口结构绘制。
- 第 70 行 `paint_delay_ms=31477213.6668`，约 8.74 小时。待处理标签可能包含长时间隐藏期间积累的工作；`workspace_load_reason=restore_last_tab` 是保留属性，不能据此声称 03:08:26 当刻重新发起了所有 reset/sort。
- 原 LHB 未安装原生窗口来源监听，空的 `native_window_*` 字段不能用于排除窗口恢复、激活或父窗口背景刷新。

## 修复范围

- LHB 使用现有 palette Base 背景填充契约，在包装、预热挂载、主题重设后保持有效，避免透明视口接受父窗口的背景全刷；保留非 opaque 首帧绘制。
- 首次表头恢复只恢复列宽与列顺序，跳过 LHB 随后本来就会取消的历史列排序；默认排序初始化幂等，池更新保留用户当前选择的列排序。
- 排序指示器只投递旧、新高亮列的区域；同列方向变化交给模型的真实排序通知。没有新增 PaintEvent 吞帧逻辑。
- 对可见 LHB 的无方向布局通知，在同一轮调用表头无参数 `resizeSections()`，完成既有伸展列的延迟计算。代理会丢弃源布局 hint；Qt 因而把水平表头也作为一般布局处理，伸展列先临时收缩、后延迟展开，造成结构帧之后第二次 `other` 整屏绘制。原生单变量实验验证表头计算提前完成后第二帧消失。各列 resize mode、用户列宽、隐藏列、真实排序和模型信号均保留。
- 池输入签名与行情更新后的展示签名分离，原样池回放不再覆盖新报价或再次重排。真实池字段、成员变化仍进入模型更新。
- 模型按股票身份迁移未变值单元格的闪烁；静默覆盖和重排覆盖变值时清除过时的闪烁方向。过期刷新继续按持久索引投递局部脏区。
- 持久索引在 `layoutAboutToBeChanged` 回调结束后采集，覆盖 Qt 视图和代理在回调中创建的索引。
- LHB 选择和当前项已由持久索引恢复时不再重复清空、重设；首次可见时重新开始绘制延迟计时，隐藏等待单独保留为 `hidden_pending_age_ms`，同时补齐原生窗口来源及快速完整首帧指标。

`_restore_refresh_state`、`setCurrentIndex`、表头 `restoreState` 都做过独立禁用对照；单独禁用不能消除该第二结构帧。最终修复针对 Qt 表头的延迟伸展计算，没有引入通用表头状态比较器、代理 hint 伪造或强制同步整个表格布局。

## 验证方法

新增 `scripts/native_lhb_restore_profile.py`。使用项目 `.venv`、Windows 原生 Qt 6.11.0、真实 `MainWindowQT` 和 `schedule_restore_last_tab('lhb')`，读取隔离复制的本地缓存，保留 42 行及真实的 11 页后台预热。

对比使用修改前源码快照和最终工作区代码，两个进程顺序运行。QSettings、SQLite、缓存和日志隔离；报价为可重复的本地回放，网络连接禁止。直接包裹真实 `VCPTableView.paintEvent` 计时，以 `QRegion` 覆盖关系判断是否整屏，不以事件过滤器次数代替真实绘制。截图只在全部测量结束后通过 `QScreen.grabWindow` 获取。

原生启动前的窗口构造成本、后台预热、恢复、静止、池回放、行情与闪烁、真实排序、重复指示器和父背景更新分别计量。

可复现命令：

```powershell
.venv\Scripts\python.exe scripts\native_lhb_restore_profile.py --source-root tmp\native_lhb_restore_profile\20260905\baseline_source --output-dir tmp\native_lhb_restore_profile\20260905\before
.venv\Scripts\python.exe scripts\native_lhb_restore_profile.py --output-dir tmp\native_lhb_restore_profile\20260905\after
```

修改前源码快照、原始运行日志、隔离运行状态和定位过程留在上述任务专属 `tmp` 目录；最终原生 JSON、截图和比较摘要位于本报告所在目录。

## 最终原生对比

详见 [comparison.json](comparison.json)、[修改前原始记录](before/native_lhb_restore_profile.json)、[修改后原始记录](after/native_lhb_restore_profile.json) 和 [单变量因果实验](diagnostic_details.json)。两轮探针脚本 SHA-256 相同，四份输入缓存 SHA-256 相同，股票代码顺序一致；修改前七个生产文件与初始 HEAD 内容核对一致，修改后七个文件分别记录 SHA-256。

| 场景 | 修改前整表帧 | 修改后整表帧 | 说明 |
| --- | ---: | ---: | --- |
| 恢复 LHB | 3 | 1 | 必要首帧保留 |
| 行情默认重排 | 2 | 1 | 报价和闪烁到期另有两次局部绘制 |
| 用户真实价格排序 | 1 | 1 | 行顺序改变仍正常整表绘制 |
| 相同排序指示器重复通知 | 1 | 0 | 无新增外观变化 |
| 父窗口背景更新 | 1 | 0 | 不再透传成 LHB 全刷 |
| 静止、相同池回放 | 0 | 0 | 无新增绘制 |

恢复阶段绘制累计 **442.513 → 184.552 ms**；同阶段事件循环卡顿峰值 **222.193 → 171.611 ms**。全程真实绘制 **10 → 6 次**、整表绘制 **8 → 4 次**，累计 **1129.196 → 797.369 ms**。

上述总数包含修改后在预热阶段额外收到的一次真实 `WindowActivate` 完整帧（116.067 ms）。原生父窗口事件与 `native_window_requires_full_paint=true` 均有记录，未丢弃或从总数扣除。此帧属于实际窗口激活；其余可见 LHB 并未被后台预热反复全刷。

两轮均通过：42 行、11/11 页完成后台预热、0 次网络尝试、127 个活跃闪烁单元格及过期局部交付、最终价格降序正确。修改后截图 [native_lhb.png](after/native_lhb.png) 已查看，表格完整可见；报价为测试回放。

这是单次同协议前后对比，耗时受机器负载影响；本次行情阶段必要结构帧仍为 218.184 ms，不能据此声称每个帧或每次事件循环都低于 50 ms。修复目标是删除已证实的额外全刷，保留必要首帧、真实排序、窗口激活、闪烁和后台预热；并未把原日志 1216.373 ms 与不同阶段的启动指标直接相减。

## 最终回归与交付检查

- **535 passed，2 warnings，96.69 秒**。覆盖 LHB、报价模型、持久索引、视图状态、实际绘制、闪烁、排序、基础刷新、workspace 生命周期和后台预热；两条 warning 来自既有 pypinyin 的 `codecs.open` 弃用提示。
- 新增模型与池签名回归先复现失败后修复。真实 Qt 绘制回归使用 42 行、生产委托、QSS、默认排序计时器及闪烁到期计时器；替换回旧布局回调的负控明确多出第二次 `other` 结构全刷，最终生产回调通过。
- Ruff、指定修改文件 UTF-8 检查、`git diff --check` 通过。
- 最终原生报告内七个生产文件 SHA-256 与最终工作区逐一一致；原生测量结束后未再改生产代码。
- 可复现的最终测试命令与机器可读结果见 [validation.json](validation.json)。

## Qt 原始资料

- [QWidget：背景传播、自动填充与 update 合并](https://doc.qt.io/qt-6/qwidget.html)。
- [QAbstractItemView：dataChanged 与 updateThreshold](https://doc.qt.io/qt-6/qabstractitemview.html)。
- [Qt 6.11 QHeaderView 源码](https://raw.githubusercontent.com/qt/qtbase/v6.11.0/src/widgets/itemviews/qheaderview.cpp)：结构布局通知与 stretchLastSection 的延迟 resize。
- [Qt 6.11 QSortFilterProxyModel 源码](https://raw.githubusercontent.com/qt/qtbase/v6.11.0/src/corelib/itemmodels/qsortfilterproxymodel.cpp)：代理因过滤语义不直接转发源布局 hint。

工作区初始已有 `data/Cache/ai_industry_chain_rows.json` 与 `domains/global_earnings_calendar/confirmed_events.json` 修改，本任务未改写它们。
