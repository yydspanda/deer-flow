# Kafka Consumer Adapter Plan

> 目标：设计真实 Kafka/Redpanda consumer adapter 的接入方案。当前已完成 `SocDaemonMessage` 和 `SocDaemonService.process_message()` decoded-message scaffold；本文件规划 broker integration，不表示已经连接生产 Kafka。

## 定位

Kafka consumer 是后台 ingestion adapter，不是新的业务系统。它只负责：

- 从 broker 拉取消息。
- 解析 transport metadata 和 payload。
- 生成 `SocDaemonMessage`。
- 调用 `SocDaemonService.process_message()`。
- 在持久化成功后提交 offset。
- 失败时进入 retry / dead-letter / metrics。

它不能直接调用 runtime pipeline、repository、normalizer、LLM、approval repository 或 action adapter。

## 推荐实现顺序

1. **配置 contract**
   - `enabled`
   - `bootstrap_servers`
   - `input_topics`
   - `group_id`
   - `client_id`
   - `security_protocol`
   - `sasl_*` / TLS secret 引用
   - `max_poll_records`
   - `commit_strategy`
   - `dead_letter_topic`
   - `poll_timeout_ms`

2. **decoded-message mapper**
   - 输入：Kafka record，包括 `topic`、`partition`、`offset`、`key`、`value`、headers。
   - 输出：`SocDaemonMessage`。
   - alert topic 默认映射为 `kind=alert`。
   - approval request topic 默认映射为 `kind=approval_request`。
   - 非法 JSON / schema mismatch 不进入 core service，直接产生 dead-letter record。

3. **consumer runner**
   - loop 只做 poll -> map -> enqueue/process -> commit。
   - 第一版可以串行处理，先保证语义正确。
   - 后续再加 bounded worker pool / semaphore。

4. **offset 与幂等**
   - `SocDaemonMessage` 必须保存 `topic + partition + offset`。
   - idempotency key 固定为 `kafka:{topic}:{partition}:{offset}`。
   - 只有 `SocDaemonService.process_message()` 成功返回后才 commit offset。
   - 失败不提交 offset；达到重试阈值后写 dead-letter 并提交原 offset。

5. **dead-letter**
   - topic：`soc.alerts.dead_letter.v1`。
   - payload 至少包含：
     - 原始 topic/partition/offset/key/headers。
     - 原始 value 的安全截断版本或对象。
     - error_type / error_message。
     - failed_at。
     - mapper_version / daemon_version。

6. **observability**
   - counters：processed、failed、dead_lettered、committed。
   - gauges：lag、inflight、last_success_at。
   - logs：message_id、topic、partition、offset、kind、run_id、approval_request_id、status。
   - readiness：DB 可用、consumer connected、topic assigned。

## Topic 初稿

| Topic | Direction | Kind | 说明 |
|---|---|---|---|
| `soc.alerts.raw.v1` | input | `alert` | 原始/标准化前预警输入 |
| `soc.approvals.requests.v1` | input | `approval_request` | 外部系统产生的高风险动作审批请求 |
| `soc.alerts.dead_letter.v1` | output | - | 无法解析或处理失败超过阈值的消息 |
| `soc.analysis.events.v1` | output | planned | 后续输出分析事件，目前先用 DB/audit |

## MVP 非目标

- 不做自动处置。
- 不做多 worker 并发。
- 不做复杂 PG-backed queue。
- 不在 consumer 内写业务判断。
- 不引入真实生产凭证。

## 下一刀建议

先做 `soc_agent/daemon/kafka_mapper.py` 和 tests：

- `KafkaRecord` 轻量 dataclass，不依赖真实 Kafka client。
- `map_kafka_record_to_daemon_message(record)`。
- alert topic -> `SocDaemonMessage(kind="alert")`。
- approval request topic -> `SocDaemonMessage(kind="approval_request")`。
- unknown topic / invalid JSON / non-object payload 明确报错。

这样下一步再接 aiokafka / confluent-kafka 时，consumer runner 只负责 IO，不碰业务映射。
