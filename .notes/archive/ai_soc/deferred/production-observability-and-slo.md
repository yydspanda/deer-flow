# Production Observability, Prometheus and SLO

> 状态：Deferred / real-telemetry-dependent。`PI-04A` 的 Operations Snapshot CLI/API 和
> `PI-04B` 的薄 Web 页面已经完成；本项只保留真实 Kafka、Runtime、LLM、Provider、算力、Prometheus、
> SLO 和告警治理，不再把已完成的运营页面列为未来工作。

## 当前已有

- `SocOperationsService` 和版本化 operations snapshot。
- `/api/soc/operations/snapshot` 与 `/workspace/soc/operations`。
- SQLite/local simulation 数据性质和 `not_measured` 缺口的明确展示。
- Kafka JSONL、持久化计数、ReviewQueue、Approval、Normalization 等已有观测数据源。

以上能力属于已完成产品切片，不在本 Deferred 项中重做。

## 尚未完成

- 真实 Kafka consumer lag、吞吐、重平衡和 DLQ/replay 指标。
- Runtime 各步骤 P50/P95/P99、失败类别、重试、积压与端到端延迟。
- LLM 调用量、并发、provider latency、token、失败率和经过审核的成本口径。
- PingAn/其他 Provider 的成功、查无、超时、schema drift 和 freshness 指标。
- 生产 PostgreSQL、K8s worker、资源利用率和容量证据。
- 有界低基数 Prometheus exporter、Dashboard、SLO、告警规则、owner 和 runbook。

## 实施顺序

1. 冻结真实 telemetry contract、标签基数、时间窗口、tenant 隔离和保留策略。
2. 将真实 daemon/Runtime/Provider/DB 指标接入现有 `SocOperationsService`，不在前端重算健康度。
3. 增加 Prometheus exporter；禁止把 alert ID、rule code、IP 等高基数字段直接作为 label。
4. 基于真实 baseline 定义 SLI/SLO、告警阈值、owner、升级路径和 runbook。
5. 让 Web 继续消费同一服务投影；Prometheus 是采集/告警面，不成为第二业务事实源。

## 重新启动条件

- 已部署真实 Kafka/PostgreSQL/K8s 或至少一个可持续运行的 staging 环境。
- 已有足够时间窗口的真实吞吐、延迟、错误和资源数据。
- 明确平台/SOC 运营 owner、告警接收人和处置 runbook。
- 路线图将生产 telemetry/SLO 排为当前切片。

## 验收标准

- 每个 SLI 都能追溯到唯一数据源、窗口、环境和版本。
- Dashboard、API 和告警对同一指标使用一致口径。
- exporter 失败不影响告警分析主链路，指标采集有明确资源上限。
- 无凭证、原始告警、IP、账号或无限 rule/source 值进入 Prometheus labels。
- SLO 阈值来自真实 baseline 和 owner 审核，不由本地 simulation 或模型自行生成。

