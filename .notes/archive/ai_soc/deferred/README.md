# SOC Agent Deferred Work Index

这个目录保存已经认可价值、但明确不进入当前执行队列的工作。它们不是当前路线图，也不能从这里
直接启动开发；权威执行顺序仍由 `.notes/ai_soc/delivery-roadmap.md` 决定。

| Deferred item | 当前状态 | 重新进入执行队列的主要条件 |
|---|---|---|
| [Kafka worker pool / concurrency](kafka-worker-pool-concurrency-plan.md) | 基础契约已完成，并发实现暂缓 | 有真实 Kafka、DB、LLM 吞吐和延迟数据 |
| [SOC operations overview](operations-overview-deferred.md) | 局部指标已有，全局观察台暂缓 | 主链路和指标口径稳定，运营查询形成真实需求 |
| [Correlation label corpus expansion](correlation-label-corpus-expansion.md) | 8-pair 工程基线已有，真实标签扩充暂缓 | 有获批脱敏数据、分析师 reviewer 和版本化标签流程 |
| [DB memory to Wiki/OKF projection](wiki-okf-memory-projection.md) | DB-first memory 已建立，展示投影暂缓 | DB 生命周期稳定并出现明确协作/审阅需求 |
| [Adaptive normalization and parser evolution](adaptive-normalization-parser-evolution.md) | deterministic 监控与离线 suggestion 已有，自动候选治理暂缓 | 有真实漂移 cohort、owner、review/replay/rollback 流程 |

## 使用规则

- 新想法只有在用户明确认可其价值、同时明确暂不实施时，才进入本目录。
- 每项必须写清触发条件、非目标和验收边界，不能只写“以后做”。
- 启动某项前，先把它迁回 `delivery-roadmap.md` 的明确 Stage/Task，并更新 `progress.md` 当前指针。
- Deferred 不等于 Done、Mock 或 Data-gated；没有经过重新排期，不得插入当前切片。
