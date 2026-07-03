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

1. **配置 contract** `Done`
   - `enabled`
   - `bootstrap_servers`
   - `alert_topics`
   - `approval_request_topics`
   - `group_id`
   - `client_id`
   - `security_protocol`
   - `sasl_*` / TLS secret 引用；secret 只保存环境变量名
   - `max_poll_records`
   - `dead_letter_topic`
   - `poll_timeout_ms`

2. **decoded-message mapper** `Done`
   - 输入：Kafka record，包括 `topic`、`partition`、`offset`、`key`、`value`、headers。
   - 输出：`SocDaemonMessage`。
   - alert topic 默认映射为 `kind=alert`。
   - approval request topic 默认映射为 `kind=approval_request`。
   - 非法 JSON / schema mismatch 不进入 core service，直接产生 dead-letter record。

3. **consumer runner** `Done`
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

## 当前状态

已完成 `soc_agent/daemon/kafka_mapper.py` 和 tests：

- `KafkaRecord` 轻量 dataclass，不依赖真实 Kafka client。
- `map_kafka_record_to_daemon_message(record)`。
- alert topic -> `SocDaemonMessage(kind="alert")`。
- approval request topic -> `SocDaemonMessage(kind="approval_request")`。
- unknown topic / invalid JSON / non-object payload 明确报错。

已完成 `soc_agent/daemon/kafka_runner.py` 和 tests：

- `KafkaConsumerPort` protocol：`poll()`, `commit(record)`, `send_dead_letter(record, error)`, `close()`。
- `SocKafkaConsumerRunner`：串行处理一条 record，流程为 map -> `SocDaemonService.process_message()` -> commit。
- mapper error / service error 进入 dead-letter，不 commit 原 offset 直到 dead-letter 成功。
- tests 用 fake consumer port 覆盖 success、idle、mapper failure、service failure、dead-letter failure、close。

已完成 `soc_agent/daemon/kafka_config.py` / `kafka_adapter.py` 和 tests：

- `KafkaConsumerSettings` 是 broker adapter 配置 contract，默认 `enabled=False`。
- `from_env()` 支持 `SOC_KAFKA_*` 环境变量：
  - `SOC_KAFKA_ENABLED`
  - `SOC_KAFKA_BOOTSTRAP_SERVERS`
  - `SOC_KAFKA_ALERT_TOPICS`
  - `SOC_KAFKA_APPROVAL_REQUEST_TOPICS`
  - `SOC_KAFKA_GROUP_ID`
  - `SOC_KAFKA_CLIENT_ID`
  - `SOC_KAFKA_DEAD_LETTER_TOPIC`
  - `SOC_KAFKA_SECURITY_PROTOCOL`
  - `SOC_KAFKA_SASL_MECHANISM`
  - `SOC_KAFKA_SASL_USERNAME`
  - `SOC_KAFKA_SASL_PASSWORD_ENV`
  - `SOC_KAFKA_SSL_CA_LOCATION`
  - `SOC_KAFKA_POLL_TIMEOUT_MS`
  - `SOC_KAFKA_MAX_POLL_RECORDS`
- `sasl_password_env` 只保存环境变量名，避免 secret 进入配置文件、notes、DB 或 run payload。
- `NullKafkaConsumerPort` 用于 disabled-by-default 本地/测试空跑；如果 `enabled=True` 但没有真实 broker adapter，会 fail-fast。

## 下一刀建议

先做 broker adapter 依赖选择和 disabled-by-default consume command wiring：

- 依赖选择：
  - `aiokafka`：async 友好，适合后续 FastAPI/async runtime，但项目当前 daemon runner 是 sync skeleton。
  - `confluent-kafka`：性能和生产成熟度更强，但本地安装和测试依赖更重。
- 建议：优先让 `soc daemon consume` 能读取 `KafkaConsumerSettings` 并在 disabled 时明确 idle；真实 broker client behind flag 接入，避免本地开发和 CI 被 Kafka 依赖阻塞。
