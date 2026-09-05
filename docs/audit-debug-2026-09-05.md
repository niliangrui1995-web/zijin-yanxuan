# 紫金研选审计与调试记录（2026-09-05）

本轮从 `master` 的 `cd69c91` 开始，审阅 `app/core/domains/infra/ui/vcp` 主链路、项目审计入口、依赖与近期运行日志。使用仓库 Python 3.14.5、PyQt6/Qt 6.11.0。下文区分已复现并修复的缺陷与仍未达到门槛的运行性能问题，不将静态检查通过等同于整体性能合格。

## 已复现并修复

| 问题 | 影响及修复 |
| --- | --- |
| HTTP 首次失败被二次异常掩盖 | 无重试 GET 或 POST 的连接错误/超时原来会继续访问未赋值的 `response`。现在保留原异常，只对允许的幂等 GET 执行一次重试。 |
| 共享 Session 凭证随跨域重定向发送 | 使用真实 Requests Session 与本地 Adapter 复现默认凭证重新合并。跨域时拒绝带会话凭证的跳转，剥离单次请求凭证与客户端证书；保留 TLS、主机名单及重定向校验。没有证据表明实际凭证已经泄露。 |
| K 线可见性回调覆盖最新快照 | 快速显示/隐藏与延迟 JS 回执会把 v2 回滚到 v1，或丢失恢复帧。最新快照保留在队列，回执按当前可见性代际消费；重排与提交均校验版本。 |
| K 线渲染恢复遗留 Page 与进程 | 恢复只销毁旧 View，预热 Page 的父对象仍是 QApplication，导致孤立 Page 和渲染进程退出后继续存活。现在按实际 QObject 所有权释放坏页，禁止坏页退回预热池；由 View 所有的页随 View 释放，避免重复销毁。 |
| 北美战报增量签名遗漏 JSON | 实际优先加载 JSON，原签名仅看 MD 秒级时间。签名现覆盖完整路径、MD/JSON 纳秒时间与大小、JSON 创建/删除状态。 |
| QFII 去重吞并不同证券 | 原键只看机构与相同持仓数值。现在加入证券身份，仅保留已由原始披露核验的两条历史代码映射，未知映射不猜测。 |
| RPS 价格及结果缓存复用旧输入 | 原价格矩阵只校验日期覆盖，内存版本也无法识别历史价格修正。现绑定股票集合、完整日期/收盘价内容，并将来源版本写入同一 Parquet 的元数据；旧无版本缓存自动失效，计算中输入变更不落缓存。 |
| RPS 后端结果不一致 | 同收益原来按股票顺序分配不同秩。SciPy/NumPy 统一 pandas 平均秩，覆盖单个有效值；停牌向前填充统一为 5 日。 |
| RPS 加速器遗漏日期索引 | 仅保留名为 `datetime` 的 pandas 索引，`date`、`trade_date` 和无名日期索引导致整股被跳过。现规范化日期索引，覆盖 ms/us/ns 精度，输入数据不变。 |
| 架构及静态检查失败 | 恢复 UI→应用服务→领域的导入边界，同时保留基金持仓 SQLite/同步服务延迟初始化。补足延迟导出类型信息，整理导入；F5 显式处理真实仓库包装异常。仅为必须收口 SystemExit/MemoryError 的 worker 终态边界登记精确例外。 |
| 诊断脚本误判 | 行情探针在采样前等待 VCP 指标任务、Timer、待应用数据真正结算；原生 K 线退出由固定约 200ms 采样改为沿用既有 8 秒上限的有界观察，保留即时与终态资源证据，零子进程门槛未放宽。 |

复杂度超额的预载检查函数仅拆出原有前台等待逻辑，AST 对照确认行为不变；原 219 行预算随函数缩短下调为 204，未放宽门禁。

## 当前验证证据

- 基线：全量 `4210 passed, 3 failed, 1 skipped`；快速审计的 Ruff、分层/异常边界、复杂度及 Pyright 失败。
- 最终全量：`4279 passed, 1 skipped, 2 warnings`，106.35 秒，0 失败。日志与 JUnit：`tmp/audit_debug_20260905/pytest_final.log`、`pytest_final.xml`；相对基线新增 66 个测试。跳过项为 `tests/test_watchlist_tab.py:280` 的原生 QWidget backing store 场景，另有原生窗口验证；两条弃用警告来自第三方 pypinyin 的 `codecs.open()`。
- 最终 `scripts/project_audit.py --quick --keep-going --type-check` 退出码 0，`all checks passed`。Ruff、UTF-8、diff、编译、依赖一致性、Mypy 基线、分层、UI 冒烟、复杂度、冷导入及 HTTP 门禁、运行环境自检、Pyright 均通过；Pyright 为 0 errors/0 warnings，复杂度 finding_count 为 0。日志：`tmp/audit_debug_20260905/project_audit_final.log`。此快速审计不包含原生性能门禁，后者按下面的独立报告判断。
- 本次 PyPI `pip-audit` 扫描退出码 0，结果 `No known vulnerabilities found`；`pip check` 无依赖冲突。`tmp/audit_debug_20260905/pip_audit_execution_summary.json` 是保留工具结果的执行摘要，未保留原始扫描日志或包数量；没有将 8 月的旧依赖报告当作本次结果。
- 原生工作区：11 页全部 ready，无预载失败/超时，最大并发步骤为 1；两轮 `stock_candidates→watchlist` 回切与修复后行情局部绘制均通过。截图确认列表真实绘出。
- 最终原生 K 线：10/10 打开、关闭、缓存切股，以及同股票独立窗口、隐藏/最小化恢复、渲染进程恢复功能均通过；生产本地只读 provider 网络请求为 0。退出即时及 DeferredDelete 后 Page/View/浏览器子进程均为 0，管理器清理通过；10 轮稳定阶段 RSS 净增 1.805MB（预算 24MB），任务、Timer、接收者和浏览器进程无净增。报告 `tmp/audit_debug_20260905/kline_native_final.json` 的功能及资源检查通过，整体状态仍因冷启动性能超标为 fail。
- RPS 内容版本按当前矩阵规模（5439 股票×336 行）用合成数据测得中位约 626ms；调用位于扫描/F5/显式重建，不在行情 tick 中。真实文件仅只读取样，未触发全市场重算。
- 真实 Parquet 的 000001/000002/300308/600000 共 4 股×336 行，RPS50/120/250 与 pandas 逐值一致，第二次缓存复用仍一致；生产文件验证前后 SHA-256 均为 `3f5035aa20d6d20a5ce0842011b6cdb09306d3e59139bc5bf4db86c772005544`，见 `tmp/audit_debug_20260905/rps_real_sample_verified.json`。

## 尚未通过的性能门禁

1. 龙虎榜原生首次绘制约 152ms，随后观察到 98ms/136ms 的整帧绘制，事件循环最高约 240ms。事件追踪的下一轮未重复出现后两帧，尚不能归因到 Scan 构造。不得吞掉必要首帧或放宽性能阈值。启用展示缓存的诊断 A/B 未解决首次绘制成本，因此未写入生产代码。
2. K 线冷启动最终一次 `setPage` 阶段 148.843ms，冷轮事件循环 155.66ms，超过既有 100ms 门槛；层级挂载 0.108ms、激活 1.673ms。[Qt 6.11 源码](https://raw.githubusercontent.com/qt/qtwebengine/6.11/src/webenginewidgets/api/qwebengineview.cpp)与隔离验证表明视图初始没有默认 Page，不能以“重复删除默认页面”为理由改写生命周期；主要阻塞已定位到首次页面与 View 绑定，首次 QuickWidget/图形初始化仍是待验证的更细分原因。提前构造并常驻隐藏 View 会改变当前仅预热 Page 的约定，需要单独评估首屏、焦点、资源和冷启动成本；简单调整 Timer 或构造函数不能拆分同步 `setPage`。

补充绘制实验使用真实龙虎榜缓存行、当前 Qt/QSS/model/proxy/delegate，在 QImage 上绘制 308 个单元格。仅复用 payload 的中位收益约 0.5%–1.5%，复用 style option 约 7.6%–8.7%；正常、选中、悬停三状态共六组像素相同。该实验未覆盖所有 Decoration/CheckState/字体/DPI/实时投影，也没有原生布局与冷字体成本，不足以证明首帧能达到门槛，因此候选仅保留在诊断目录，未修改生产绘制代码。结果：`tmp/audit_debug_20260905/lhb_delegate_feasibility/report.json`。

原始失败报告、后续诊断、逐次资源清单及截图统一保留在 `tmp/audit_debug_20260905/`，不以覆盖旧失败报告的方式伪装通过。

## 验证原则及边界

按 [Qt 线程与对象所有权](https://doc.qt.io/qt-6/threads-qobject.html)、[QWebEngineView 页面所有权](https://doc.qt.io/qt-6/qwebengineview.html#setPage)、[pytest 测试隔离](https://docs.pytest.org/en/stable/explanation/flaky.html)、[Requests 会话参数](https://requests.readthedocs.io/en/stable/user/advanced/)和 [pandas 平均秩](https://pandas.pydata.org/pandas-docs/stable/reference/api/pandas.DataFrame.rank.html)核对实现与验证路径。

保留开始时已有的 `data/Cache/ai_industry_chain_rows.json`、`domains/global_earnings_calendar/confirmed_events.json` 变更。未停止用户运行中的实例，未提交或推送。代码验收需重启应用；历史 QFII/RPS 业务结果未自动全量重建，不能声称旧产物已全部修正。30/60 分钟长稳和盘中实时源全链路未在本轮执行。
