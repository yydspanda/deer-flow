# SOC Agent Deferred Work Index

这个目录保存已经认可价值、但明确不进入当前执行队列的工作。它们不是当前路线图，也不能从这里
直接启动开发；权威执行顺序仍由 `.notes/ai_soc/delivery-roadmap.md` 决定。

| Deferred item | Target / 目标阶段 | 重新进入队列的条件 | 激活后的第一刀 |
|---|---|---|---|
| [Kafka worker pool / concurrency](kafka-worker-pool-concurrency-plan.md) | `PI-02` | 有真实 Kafka、DB、LLM P50/P95、lag 和失败数据，串行模式实测不达标 | 在默认 concurrency=`1` 下加入 bounded queue/poller，先验证顺序提交、背压和优雅退出，不直接打开多 worker |
| [SOC operations overview](operations-overview-deferred.md) | `PI-04-B/C` | `PI-01E` 已产生真实 shadow telemetry，并明确时间窗、阈值和运营 owner | `PI-04-B` 只做现有 snapshot 的薄 Web 消费页；Prometheus/SLO 作为后续独立切片，不在前端重算健康度 |
| [Correlation label corpus expansion](correlation-label-corpus-expansion.md) | `PI-03` | 有获批脱敏 pair、分析师 reviewer 和版本化标签流程 | 先建立 immutable manifest + reviewer/rationale/provenance/supersede contract，再扩 corpus 和跑 scorer replay diff |
| Feedback-derived Skill candidates | `PI-03C` | 真实 external reason/分析师 correction 已形成重复 failure cohort，并有 Skill owner/reviewer | 先定义 `SkillImprovementCandidate` contract、幂等聚合键、source refs 和只读 backlog；不自动编辑或发布 Skill |
| PingAn software-path governed promotion | `PI-03D` / optional | 有人工标签、scope/validity owner、正反例和离线 replay gate；产品明确需要 decision impact | 从现有 immutable catalog 生成独立 promotion proposal；默认目录和 MCP 继续永久 investigation-only |
| [DB memory to Wiki/OKF projection](wiki-okf-memory-projection.md) | `PI-03` 之后 | DB 生命周期和检索价值已被真实使用验证，且分析师提出明确协作需求 | 先做 DB -> versioned read-only export；Wiki 编辑只能回流 proposal，不允许直接改变 active memory |
| [Adaptive normalization and parser evolution](adaptive-normalization-parser-evolution.md) | `PI-03` 或独立治理切片 | `PI-01E`/5000+ 批跑产生可重复 drift cohort，并有 owner、review/replay/rollback 流程 | 先实现按 tenant/source/parser/fingerprint 聚合的 cohort report + candidate bundle；不自动生成或上线 parser |

Feedback-derived Skill candidates 的详细来源边界见
[`../../../ai_soc/integrations/external-disposition-sync.md`](../../../ai_soc/integrations/external-disposition-sync.md)；
路径目录的现有性质和未来治理前置见
[`../../../ai_soc/integrations/pingan-legacy-source-audit.md`](../../../ai_soc/integrations/pingan-legacy-source-audit.md)。

## 使用规则

- 新想法只有在用户明确认可其价值、同时明确暂不实施时，才进入本目录。
- 每项必须写清触发条件、非目标和验收边界，不能只写“以后做”。
- 启动某项前，先把它迁回 `delivery-roadmap.md` 的明确 Stage/Task，并更新 `progress.md` 当前指针。
- Deferred 不等于 Done、Mock 或 Data-gated；没有经过重新排期，不得插入当前切片。
