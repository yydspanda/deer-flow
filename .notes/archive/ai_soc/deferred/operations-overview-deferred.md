# SOC Operations Overview Deferred Requirement

> 历史状态：Deferred。2026-08-02 已激活最小 `PI-04-A Operations Snapshot`；本文件描述的完整 Web、
> Prometheus、lag/算力指标和生产 SLO 仍后置。当前契约以 `delivery-roadmap.md` 为准。

## 背景

SOC Agent 后续上线后，运营同事会频繁询问线上预警和系统运行状态，例如：

- 现在系统是否正常工作？
- Kafka 是否积压？
- 今天处理了多少预警，失败了多少？
- 哪些预警卡在复核或审批？
- 模型/算力是否有异常、超时或成本突增？
- 某条预警为什么还没出结果？

这类问题不能只靠开发人员查日志或临时跑 SQL。长期应该有一个面向运营和研发共用的运行态观察入口。

## Product Verdict

**Defer, but keep as a named roadmap item.**

这个能力很有价值，但它不是当前最小闭环的前置条件。现在更重要的是先把告警接入、分析、复核、审批、Kafka daemon、持久化和回放链路跑通。观察台应基于稳定的运行数据和事件模型建设，避免过早做成漂亮但数据口径不稳的 dashboard。

## 目标用户

- SOC 运营同事：查看系统整体运行情况、预警处理情况、卡点和异常。
- 安全分析师：定位某条预警为什么未完成、是否进入复核/审批、是否被 dead-letter。
- 研发/运维：排查 Kafka、DB、daemon、LLM、worker、接口延迟和错误率。

## 未来产品形态

建议命名为 **SOC Operations Overview / 运营观察台**。

它不是单纯 Kafka 监控，也不是 Prometheus 面板的替代品，而是 SOC Agent 的业务运行视图：

```text
Kafka / daemon / runtime / LLM / review queue / approval / DB
  -> normalized operational metrics
  -> API / CLI / Web operations overview
  -> optional Prometheus exporter / logs / alerts
```

## 需要回答的问题

### 1. 系统健康

- Kafka broker 是否可连。
- SOC database 是否可连。
- daemon 是否在运行。
- 最近一次成功处理时间。
- 连续错误次数和最近错误类型。
- 当前是否处于 backoff。

### 2. 预警处理

- 最近 5 分钟、1 小时、24 小时处理量。
- 成功、失败、dead-letter 数量。
- open review queue 数量。
- pending approval request 数量。
- 按预警来源统计：APT、EDR、HIDS、NIDS、F5、其他供应商。
- 按 rule_code / detection_key / category 统计高频项。

### 3. 延迟

- Kafka message 到达后到被消费的延迟。
- 告警进入 SOC Agent 到完成分析的延迟。
- 进入 review queue 到被关闭的延迟。
- approval request 到审批完成的延迟。
- dead-letter 产生时间和重放时间。

### 4. 负载与吞吐

- daemon poll 频率。
- 每轮处理耗时。
- idle / processed / error / dead-letter counters。
- worker pool 并发数和队列深度。
- 未来 sub-agent 并行任务数。

### 5. 模型与算力

- LLM 调用次数。
- stub / LLM analyzer 命中比例。
- parse success / repair success / parse failed 数量。
- token 使用量和成本估算。
- 模型超时、限流、异常数量。
- 不同 prompt_version / model_name 的效果和错误率。

### 6. 运营问答入口

未来可支持通过 CLI/Web/TUI/Agent 询问：

- “这条预警为什么还没处理？”
- “今天 Zeus APT 告警失败多不多？”
- “现在是不是 Kafka 堵了？”
- “哪个 rule_code 最近一直误报？”
- “哪些审批卡住了？”

## MVP 形态建议

等主链路跑通后，先做最小可用的后端 contract，而不是直接做大屏：

- `SocOperationsOverviewService`
- `soc ops overview`
- `/api/soc/operations/overview`
- Web 页面：`/workspace/soc/ops`

MVP 数据源优先复用已有持久化和 daemon 事件：

- `soc_alert_summaries`
- review queue repository
- approval request / grant repository
- decision audit log
- daemon JSONL metrics
- `soc daemon status`

## 非目标

当前阶段不做：

- 不先做完整 BI 大屏。
- 不直接实现 Prometheus exporter。
- 不把 Kafka consumer 写成业务监控系统。
- 不为了观察台改变当前 runtime 主流程。
- 不在指标口径未稳定时做复杂图表和告警规则。

## 与 Prometheus 的关系

Prometheus 是采集和告警手段之一，不是运营观察台本身。

当前策略：

- 先使用 `SOC_DAEMON_METRIC_JSONL=stderr` 作为最低可用观测面。
- 等 daemon、runtime、review、approval 的数据口径稳定后，再决定是否补 `/metrics` exporter。
- Prometheus 指标应服务于系统告警和 SRE，不替代业务运营视图。

## 进入实现的触发条件

满足以下条件后再进入实现：

- Kafka daemon 能稳定消费真实预警。
- review queue / approval inbox 有真实或接近真实的数据流。
- daemon metrics、audit log、summary 表的字段口径稳定。
- 运营同事已经开始频繁询问线上处理状态，且人工查日志/SQL 明显低效。

## 初步成功标准

- 运营同事不用找研发查日志，就能看到系统是否正常、是否积压、是否有失败。
- 研发可以从一个入口定位 Kafka、DB、daemon、LLM、review、approval 哪一段出问题。
- 任何展示的指标都能追溯到明确的数据源和时间窗口。
- 观察台不影响 SOC Agent 主链路吞吐和可靠性。
