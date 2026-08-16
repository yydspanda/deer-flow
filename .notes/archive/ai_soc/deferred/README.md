# SOC Agent Deferred Work Index

这个目录只保存已经认可价值、仍未完成、且明确不进入当前执行队列的工作。它不是完成历史，也不能从
这里直接启动开发；权威执行顺序仍由 `.notes/ai_soc/delivery-roadmap.md` 决定。

2026-08-15 已按代码、`delivery-roadmap.md` 和 `progress.md` 重新审计：已完成的 `PI-03C`
Skill-improvement backlog、`PI-04A/B` Operations Snapshot/Web，以及已落地的 PingAn software-path
fast policy 均已从本索引移除。真实 provider/基础设施/rollout 等 Data-gated 债务继续由
`.notes/ai_soc/integrations/` 管理，不在这里复制第二套台账。

| Deferred item | Target / 目标阶段 | 重新进入队列的条件 | 激活后的第一刀 |
|---|---|---|---|
| [Native Agent Tool Call + trusted target binding](native-agent-tool-call-and-target-binding.md) | 独立 architecture hardening；高风险无人值守执行前置 | Lead Agent proposal 准备进入自动写入/高风险链，或 marker/目标漂移已形成可测问题 | 增加只创建 proposal 的 `propose_soc_action` native tool，并以 frozen context 的 typed `target_ref` 替代自由 payload |
| [Kafka worker pool / concurrency](kafka-worker-pool-concurrency-plan.md) | `PI-02` | 有真实 Kafka、DB、LLM P50/P95、lag 和失败数据，串行模式实测不达标 | 在默认 concurrency=`1` 下加入 bounded queue/poller，先验证顺序提交、背压和优雅退出，不直接打开多 worker |
| [Production observability, Prometheus and SLO](production-observability-and-slo.md) | 真实 `PI-04` / `PI-05C` | staging/production 已产生真实 telemetry，并明确窗口、阈值、owner 和 runbook | 先冻结低基数 telemetry contract，再接现有 Operations Service；不重做已完成的 Snapshot/Web |
| [Correlation label corpus expansion](correlation-label-corpus-expansion.md) | `PI-03` | 有获批脱敏 pair、分析师 reviewer 和版本化标签流程 | 先建立 immutable manifest + reviewer/rationale/provenance/supersede contract，再扩 corpus 和跑 scorer replay diff |
| [DB memory to Wiki/OKF projection](wiki-okf-memory-projection.md) | `PI-03` 之后 | DB 生命周期和检索价值已被真实使用验证，且分析师提出明确协作需求 | 先做 DB -> versioned read-only export；Wiki 编辑只能回流 proposal，不允许直接改变 active memory |
| [Adaptive normalization and parser evolution](adaptive-normalization-parser-evolution.md) | `PI-03` 或独立治理切片 | `PI-01E`/5000+ 批跑产生可重复 drift cohort，并有 owner、review/replay/rollback 流程 | 先实现按 tenant/source/parser/fingerprint 聚合的 cohort report + candidate bundle；不自动生成或上线 parser |
| [Asset/business context for Memory applicability](asset-business-context-memory-applicability.md) | `PI-03` Memory precision 优化 | 获得稳定 CMDB/资产标签契约、canonical taxonomy 和能证明资产维度影响结论的人工样本 | 增加版本化 `AssetBusinessContext`，由 Adapter/Provider 投影 canonical facets，并先做 shadow replay diff |

## 使用规则

- 新想法只有在用户明确认可其价值、同时明确暂不实施时，才进入本目录。
- 每项必须写清触发条件、非目标和验收边界，不能只写“以后做”。
- 启动某项前，先把它迁回 `delivery-roadmap.md` 的明确 Stage/Task，并更新 `progress.md` 当前指针。
- Deferred 不等于 Done、Mock 或 Data-gated；没有经过重新排期，不得插入当前切片。
- 已完成、被替代或转入正式路线图的项目应从本目录索引删除；完成事实由 `progress.md` 保留。
