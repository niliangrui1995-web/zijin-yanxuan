# 紫金研选 UX 审计整改计划

## Goal
把 [2026-04-16-ux-audit-report.md](/D:/vcp_hunter/紫金研选/docs/plans/2026-04-16-ux-audit-report.md) 中的全部问题整改完毕，做到“问题有归属、改动可验证、结项无遗漏”。

## Hard Rules
- 不接受“先跳过”“后面再看”的灰区项，审计发现必须全部有对应整改动作。
- 每次开始实施前，先重读审计报告和本计划，确认当前批次覆盖范围。
- 每完成一批整改，都要跑对应验证；未验证通过不得勾选完成。
- 所有改动必须保持原文件编码与中文显示正常，不得引入乱码。
- 整改过程中同步清理废弃状态分支、废旧 UI 代码和无用文件，保持目录干净。
- 所有实质性代码改动完成后，先汇报结果，等待用户确认后再统一 `git add .` / `git commit`。

## 审计问题全量覆盖清单
- [x] F01 页面级异步状态闭环缺失：加载中 / 最新数据 / 缓存数据 / 刷新失败 / 离线 未统一落地
- [x] F02 外资大宗页超时后仍停留在被动加载卡片，缺少失败说明、缓存说明、重试动作
- [x] F03 亚洲寡头页刷新失败后页面内未明确提示“本次失败”，缓存与最新数据边界不清
- [x] F04 通用工具条长期采用单行横向堆叠，窄宽度下压缩、截断、拥挤
- [x] F05 业绩异动页为当前最明显的窄宽度受害页
- [x] F06 龙虎榜 / 美股日报 / 盘中监控等复用工具条页面存在同类密度风险
- [x] F07 顶部自定义标题栏当前可用但扩展余量不足，后续新增入口存在拥堵风险
- [x] F08 关注池页右上行动区扩展余量一般，需要控制继续堆叠按钮
- [x] F09 搜索/筛选反馈风格不统一，缺少统一摘要、清空、匹配数、更新时间、失败说明
- [x] F10 自定义窗口控制按钮和关键图标按钮缺少 tooltip / accessibleName / 更明确悬停反馈
- [x] F11 K 线窗口顶部交互与结构需纳入同一套可发现性整改
- [x] F12 亚洲寡头页暴露 `CF隧道`、直连/VPN 等技术术语，业务语义不友好
- [x] F13 Toast 成功/警告/错误/信息语义层级不够强
- [x] F14 表格 tooltip 溢出判断存在回归信号，对应失败测试需修复
- [x] F15 VCP 扫描页应被提炼为通用模板，而不是孤立的优质页面
- [x] F16 系统日志页承担过多业务页问题解释责任，需要把一线解释前移到业务页
- [x] F17 K 线窗口需补运行态验证证据，不能长期停留在代码层结论
- [x] N01 亚洲页远端 K 线源空响应时需保留旧缓存并显式标记“沿用缓存”，不能把底层抓取失败直接外泄成页面级异常

## 审计问题 -> 文件 -> 验证项追踪表
| ID | 实施文件 | 核心动作 | 验证证据 |
| --- | --- | --- | --- |
| F01 | `ui/components/__init__.py` `ui/tabs/asian_market_tab.py` `ui/tabs/foreign_block_trade_tab.py` `ui/tabs/asian_market_runtime.py` | 统一页面级五态容器，补齐加载/最新/缓存/失败/离线与重试入口 | [tab-04.png](/D:/vcp_hunter/紫金研选/docs/ux_audit_assets/real/tab-04.png) [tab-06.png](/D:/vcp_hunter/紫金研选/docs/ux_audit_assets/real/tab-06.png) |
| F02 | `ui/tabs/foreign_block_trade_tab.py` | 超时/失败/缓存结果都在页内解释，保留重试闭环与结果摘要 | [tab-06.png](/D:/vcp_hunter/紫金研选/docs/ux_audit_assets/real/tab-06.png) [blocktrade-1000.png](/D:/vcp_hunter/紫金研选/docs/ux_audit_assets/real-narrow/blocktrade-1000.png) |
| F03 | `ui/tabs/asian_market_tab.py` `ui/tabs/asian_market_runtime.py` `vcp/fetchers/asian_kline_fetcher.py` | 失败时明确标记本次失败，成功沿用旧缓存时给出页面内说明而非模糊成功态 | [tab-04.png](/D:/vcp_hunter/紫金研选/docs/ux_audit_assets/real/tab-04.png) `tests/test_asian_kline_fetcher.py` `tests/test_asian_market_tab.py` |
| F04 | `ui/tabs/base_stock_tab.py` `ui/styles/global_qss.py` | 通用工具条改为分组、换行、摘要区联动的弹性布局 | [blocktrade-1000.png](/D:/vcp_hunter/紫金研选/docs/ux_audit_assets/real-narrow/blocktrade-1000.png) [scan-1000.png](/D:/vcp_hunter/紫金研选/docs/ux_audit_assets/real-narrow/scan-1000.png) |
| F05 | `ui/tabs/earnings_tab.py` `ui/tabs/base_stock_tab.py` | 业绩异动页工具条重排，窄宽度下保留完整筛选与动作能力 | [earnings-1000.png](/D:/vcp_hunter/紫金研选/docs/ux_audit_assets/real-narrow/earnings-1000.png) |
| F06 | `ui/tabs/base_stock_tab.py` `ui/tabs/watchlist_tab.py` `ui/tabs/foreign_block_trade_tab.py` `ui/tabs/asian_market_tab.py` | 把同类工具条风险统一收敛到基类和页头反馈模板 | [scan-1000.png](/D:/vcp_hunter/紫金研选/docs/ux_audit_assets/real-narrow/scan-1000.png) [blocktrade-1000.png](/D:/vcp_hunter/紫金研选/docs/ux_audit_assets/real-narrow/blocktrade-1000.png) |
| F07 | `ui/components/main_window_shell.py` | 标题栏入口分区与容量治理，避免继续无序横向堆叠 | `tests/test_main_window_shell.py` |
| F08 | `ui/tabs/watchlist_tab.py` | 关注池行动区收敛为更稳定的组合，不再继续叠加零散按钮 | `tests/test_watchlist_tab.py` |
| F09 | `ui/tabs/base_stock_tab.py` `ui/tabs/asian_market_tab.py` `ui/tabs/foreign_block_trade_tab.py` `ui/tabs/watchlist_tab.py` `ui/tabs/earnings_tab.py` | 统一筛选摘要、匹配数、更新时间、清空动作和失败说明 | [tab-04.png](/D:/vcp_hunter/紫金研选/docs/ux_audit_assets/real/tab-04.png) [tab-06.png](/D:/vcp_hunter/紫金研选/docs/ux_audit_assets/real/tab-06.png) |
| F10 | `ui/components/main_window_shell.py` `ui/kline_window_qt.py` | 窗口控制和关键图标按钮补齐 tooltip、accessibleName 和更明确悬停语义 | `tests/test_main_window_shell.py` `tests/test_kline_window_header.py` |
| F11 | `ui/kline_window_qt.py` | K 线窗口顶部结构纳入同一套可发现性与摘要规则 | [kline-window.png](/D:/vcp_hunter/紫金研选/docs/ux_audit_assets/real/kline-window.png) `tests/test_kline_window_header.py` |
| F12 | `ui/tabs/asian_market_tab.py` `ui/tabs/asian_market_runtime.py` | 把技术术语改写为业务语义，隐藏 CF/直连/VPN 等实现细节 | [tab-04.png](/D:/vcp_hunter/紫金研选/docs/ux_audit_assets/real/tab-04.png) |
| F13 | `ui/components/toast_widget.py` `ui/theme_tokens.py` | 强化成功/警告/错误/信息等级差异和语义色板 | `tests/test_toast_widget.py` `tests/test_theme_tokens.py` |
| F14 | `ui/components/__init__.py` `tests/test_data_provider_history_mixin.py` | 修复 tooltip 溢出判断回归并补稳定验证 | `tests/test_data_provider_history_mixin.py` |
| F15 | `ui/tabs/base_stock_tab.py` `ui/components/__init__.py` `ui/styles/global_qss.py` | 将 VCP 页头/状态/表格交互抽象为可复用模板，下沉到通用实现 | [scan-1000.png](/D:/vcp_hunter/紫金研选/docs/ux_audit_assets/real-narrow/scan-1000.png) `tests/test_base_stock_tab.py` `tests/test_scan_tab.py` |
| F16 | `ui/tabs/asian_market_tab.py` `ui/tabs/foreign_block_trade_tab.py` `ui/tabs/watchlist_tab.py` `ui/tabs/earnings_tab.py` | 把失败、缓存、筛选解释前移到业务页页头，系统日志退回深层诊断层 | [tab-04.png](/D:/vcp_hunter/紫金研选/docs/ux_audit_assets/real/tab-04.png) [tab-06.png](/D:/vcp_hunter/紫金研选/docs/ux_audit_assets/real/tab-06.png) |
| F17 | `ui/kline_window_qt.py` `vcp/data_provider_local.py` `tests/test_data_provider_local.py` | 修复本地历史前复权运行时异常，补齐 K 线真实运行截图与回归测试 | [kline-window.png](/D:/vcp_hunter/紫金研选/docs/ux_audit_assets/real/kline-window.png) `tests/test_data_provider_local.py` |
| N01 | `vcp/fetchers/asian_kline_fetcher.py` `ui/tabs/asian_market_runtime.py` | 远端源空响应时保留旧缓存并明确展示“沿用缓存”状态，避免误报页面失败 | `tests/test_asian_kline_fetcher.py` `tests/test_asian_market_tab.py` |

## Tasks
- [x] Task 1: 建立“审计问题 -> 文件 -> 验证项”追踪表，给 F01-F17 全部绑定实施目标文件和验收条件
  Verify: 形成一份可勾选的映射台账，任一审计项都能追溯到具体实现位置和验证方式

- [x] Task 2: 重构通用状态容器与异步反馈协议，统一 `加载中/成功/缓存/失败/离线` 五态，并把“最后成功时间、失败摘要、重试动作”做成页面级标准能力
  Target files: `ui/components/__init__.py`, `ui/tabs/asian_market_tab.py`, `ui/tabs/foreign_block_trade_tab.py`, 其他使用状态容器的 Tab
  Verify: 亚洲寡头与外资大宗先完成五态闭环；页面内能直接看懂当前数据是否最新、为什么失败、如何重试

- [x] Task 3: 把通用工具条改造成可弹性伸缩的双层/分组布局，拆分“筛选条件”和“动作按钮”，为窄宽度建立稳定基线
  Target files: `ui/tabs/base_stock_tab.py`, `ui/tabs/earnings_tab.py`, `ui/tabs/foreign_block_trade_tab.py`, `ui/tabs/watchlist_tab.py`, `ui/tabs/asian_market_tab.py`, `ui/tabs/scan_tab.py`
  Verify: 至少在 1000 宽度下，业绩异动、外资大宗、VCP 扫描页面不再出现明显截断、拥挤和不可读控件

- [x] Task 4: 统一筛选反馈和数据摘要模式，补齐“当前筛选摘要、一键清空、匹配数/总数、更新时间、失败/缓存说明”
  Target files: `ui/tabs/base_stock_tab.py`, `ui/tabs/log_tab.py`, 以及各具体 Tab
  Verify: 所有主要数据页都能在页头明确回答“当前看的是哪批数据、筛选是否生效、数据何时更新”

- [x] Task 5: 完成导航与可发现性整改，包括标题栏容量治理、窗口控制按钮/K 线按钮的 tooltip 与 accessibility、危险操作悬停态，以及关注池行动区节制
  Target files: `ui/components/main_window_shell.py`, `ui/kline_window_qt.py`, `ui/workspaces/classic_workspace.py`, `ui/tabs/watchlist_tab.py`
  Verify: 所有窗口控制与关键图标按钮可被悬停识别、可被辅助技术命名；顶部导航有新增入口下沉策略，不再默认继续横向堆叠

- [x] Task 6: 完成页面专项整改，逐页消灭报告里的局部问题
  Scope:
  `亚洲寡头`：失败提示、缓存透明度、业务化通道文案
  `外资大宗`：失败卡片、超时说明、重试闭环
  `业绩异动`：窄宽度工具条重排
  `龙虎榜/美股日报/盘中监控`：同类工具条风险一起收敛
  `关注池`：行动区容量治理
  `VCP 扫描`：抽象为可复用页头/状态/表格模板
  `系统日志`：从“主解释层”退回“深层诊断层”
  `K线窗口`：纳入统一可发现性和运行态验证
  Verify: 每个页面都有“整改前问题 -> 整改后证据”的对应记录，不留未处理模块

- [x] Task 7: 提升反馈组件语义质量，补强 Toast 视觉等级，并修复 tooltip 溢出判断回归
  Target files: `ui/components/toast_widget.py`, `ui/components/__init__.py`, `tests/test_toast_widget.py`, `tests/test_theme_tokens.py`
  Verify: Toast 在成功/警告/错误/信息之间可一眼区分；失败测试 `test_vcp_table_view_tooltip_only_shows_when_text_is_elided` 通过

- [x] Task 8: 做最终回归收口，补 K 线运行态证据，重拍关键截图，重跑测试，并用本计划逐项核销 F01-F17
  Verify: 审计清单全部勾选完成；真实运行截图更新；全量测试无 UX 相关失败；最终汇报中明确“哪些问题已关闭、依据是什么”

## Review Protocol
- 每次实施新批次前，先重读：
  - [审计报告](/D:/vcp_hunter/紫金研选/docs/plans/2026-04-16-ux-audit-report.md)
  - [本整改计划](/D:/vcp_hunter/紫金研选/ux-audit-remediation-plan.md)
- 每次只允许从 F01-F17 中挑选有明确边界的一组问题实施，完成后立即更新勾选状态。
- 若某项整改引出新的结构性问题，必须补充到本计划的“覆盖清单”，不能只留在口头说明里。
- 未完成 F01-F17 全部核销前，不得宣称“审计整改完成”。

## Done When
- [x] F01-F17 全部完成并勾选
- [x] 亚洲寡头、外资大宗、业绩异动、关注池、VCP 扫描、K 线窗口都完成专项验证
- [x] 标题栏、工具条、Toast、tooltip、筛选反馈、状态容器全部完成统一化整改
- [x] 真实桌面截图和窄宽度截图能证明整改结果
- [x] 全量测试通过，至少不再存在已知 UX 失败用例
- [x] 最终汇报能把每条审计发现对应到具体改动和验证证据

## Notes
- 当前关键基线文件：
  - [ui/components/main_window_shell.py](/D:/vcp_hunter/紫金研选/ui/components/main_window_shell.py)
  - [ui/components/__init__.py](/D:/vcp_hunter/紫金研选/ui/components/__init__.py)
  - [ui/components/toast_widget.py](/D:/vcp_hunter/紫金研选/ui/components/toast_widget.py)
  - [ui/tabs/base_stock_tab.py](/D:/vcp_hunter/紫金研选/ui/tabs/base_stock_tab.py)
  - [ui/tabs/watchlist_tab.py](/D:/vcp_hunter/紫金研选/ui/tabs/watchlist_tab.py)
  - [ui/tabs/asian_market_tab.py](/D:/vcp_hunter/紫金研选/ui/tabs/asian_market_tab.py)
  - [ui/tabs/foreign_block_trade_tab.py](/D:/vcp_hunter/紫金研选/ui/tabs/foreign_block_trade_tab.py)
  - [ui/tabs/earnings_tab.py](/D:/vcp_hunter/紫金研选/ui/tabs/earnings_tab.py)
  - [ui/tabs/scan_tab.py](/D:/vcp_hunter/紫金研选/ui/tabs/scan_tab.py)
  - [ui/tabs/log_tab.py](/D:/vcp_hunter/紫金研选/ui/tabs/log_tab.py)
  - [ui/kline_window_qt.py](/D:/vcp_hunter/紫金研选/ui/kline_window_qt.py)
- 当前关键测试文件：
  - [tests/test_main_window_shell.py](/D:/vcp_hunter/紫金研选/tests/test_main_window_shell.py)
  - [tests/test_base_stock_tab.py](/D:/vcp_hunter/紫金研选/tests/test_base_stock_tab.py)
  - [tests/test_asian_market_tab.py](/D:/vcp_hunter/紫金研选/tests/test_asian_market_tab.py)
  - [tests/test_watchlist_tab.py](/D:/vcp_hunter/紫金研选/tests/test_watchlist_tab.py)
  - [tests/test_scan_tab.py](/D:/vcp_hunter/紫金研选/tests/test_scan_tab.py)
  - [tests/test_toast_widget.py](/D:/vcp_hunter/紫金研选/tests/test_toast_widget.py)
  - [tests/test_theme_tokens.py](/D:/vcp_hunter/紫金研选/tests/test_theme_tokens.py)
  - [tests/test_asian_kline_fetcher.py](/D:/vcp_hunter/紫金研选/tests/test_asian_kline_fetcher.py)
  - [tests/test_data_provider_local.py](/D:/vcp_hunter/紫金研选/tests/test_data_provider_local.py)
  - [tests/test_kline_window_header.py](/D:/vcp_hunter/紫金研选/tests/test_kline_window_header.py)
- 关键运行截图：
  - [亚洲寡头-运行态](/D:/vcp_hunter/紫金研选/docs/ux_audit_assets/real/tab-04.png)
  - [外资大宗-运行态](/D:/vcp_hunter/紫金研选/docs/ux_audit_assets/real/tab-06.png)
  - [K线窗口-运行态](/D:/vcp_hunter/紫金研选/docs/ux_audit_assets/real/kline-window.png)
  - [外资大宗-1000宽度](/D:/vcp_hunter/紫金研选/docs/ux_audit_assets/real-narrow/blocktrade-1000.png)
  - [业绩异动-1000宽度](/D:/vcp_hunter/紫金研选/docs/ux_audit_assets/real-narrow/earnings-1000.png)
  - [VCP扫描-1000宽度](/D:/vcp_hunter/紫金研选/docs/ux_audit_assets/real-narrow/scan-1000.png)
- 本轮关键验证：
  - `pytest tests\\test_asian_kline_fetcher.py tests\\test_asian_market_tab.py tests\\test_data_provider_local.py tests\\test_data_provider_history_mixin.py tests\\test_kline_window_header.py -q` => `24 passed`
  - `.\\.venv\\Scripts\\pytest -q` => `199 passed, 2 warnings`
- 运行态补充说明：
  - 本轮新增关闭项 `N01`，用于覆盖亚洲页远端源空响应时的缓存保留策略，避免把抓取层异常直接升级成页面级 UX 失败。
  - K 线窗口已补上真实运行态截图，同时修复本地历史数据前复权时对 `int64` 成交量列写回浮点值导致的运行时异常。
