# 运行时健康与长期稳定性路线

## 本轮落地边界

- 运行时健康中心先把已有临时探针产品化：后台任务、Timer、事件订阅、线程、WebEngine 子进程、行情请求、F5 缓存和关键数据血缘都进入同一份 JSON。
- F5 完成链路保持分层：先刷新核心行情快照，再分帧刷新当前/可见表格快照，最后通过缓存完成信号让 cache-only / 情报源页面只回读本地最新快照。
- 稳定性预算复用 round4 / round5 的采样思想，新增 `runtime_health_stability_suite.py` 作为手动 suite；`perf_budget_check.py` 负责读取结构化报告并输出 ok/fail。

## 后续架构升级建议

1. 数据服务边界
   - 以“综合候选”为试点，把联网、缓存读取、调度和表格展示拆成独立服务接口。
   - 每个服务返回统一 `DataLineage`：provider/cache、交易日、是否联网、是否降级、更新时间。

2. Provider 容错层
   - 把东财、Sina、Tencent、离线缓存回退整理成统一状态机。
   - 对外只暴露 `QuoteBatchResult`，包含成功来源、缺失 codes、降级原因、短 TTL 命中和批次统计。

3. 表格刷新边界
   - 将列宽、排序、筛选、右键菜单继续沉淀到共享组件。
   - 大表刷新优先走 keyed diff / layoutChanged，避免整表 reset；高频表格再评估虚拟化。

4. K 线 WebEngine 生命周期
   - 单独做一轮 K 线窗口生命周期治理：复用/预热策略、关闭释放、白屏恢复、截图 smoke test。
   - 把 WebEngine 子进程回收纳入长模式稳定性 suite 的必跑项。

5. 长时间预算常态化
   - 短模式用于提交前验证，长模式用于人工夜间 soak。
   - 建议后续把最近一次 runtime health 报告路径显示在健康面板中，并在异常时自动附带到日志。
