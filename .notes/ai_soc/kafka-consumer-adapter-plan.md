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

已完成 `soc daemon consume` disabled-by-default CLI wiring：

- CLI 从 `SOC_KAFKA_*` 读取 `KafkaConsumerSettings`。
- 默认 `--max-records 1`，不会在本地/CI 长期挂住。
- `SOC_KAFKA_ENABLED=false` 或未设置时，`NullKafkaConsumerPort.poll()` 返回 idle，CLI 输出 `soc.kafka_consume_result.v1`。
- `SOC_KAFKA_ENABLED=true` 但未接真实 adapter 时，CLI fail-fast，避免误以为已经连接 broker。
- disabled idle 不要求数据库连接；未来真实 broker adapter 启用时才需要完整 repository-backed `SocDaemonService` wiring。

已完成 `confluent-kafka` broker adapter：

- 依赖选择：优先使用 `confluent-kafka`，原因是当前 runner 是同步模型，且该 SDK 生产成熟度更高。
- 依赖形态：`backend[kafka]` optional extra；普通开发/CI 不强制安装 Kafka SDK。
- `build_kafka_consumer_port(settings)`：
  - disabled -> `NullKafkaConsumerPort`。
  - enabled -> `ConfluentKafkaConsumerPort`。
- `ConfluentKafkaConsumerPort`：
  - `subscribe()` 订阅 alert topics + approval request topics。
  - `poll()` 将 SDK message 转为 `KafkaRecord`。
  - consumer error / empty value 直接抛 `KafkaAdapterError`，不进入 mapper/core。
  - `commit()` 使用 `TopicPartition(topic, partition, offset + 1)` 同步提交。
  - `send_dead_letter()` 写 `soc.kafka_dead_letter.v1` payload 到 dead-letter topic，并同步 `flush()`。
- CLI 顺序：`SOC_KAFKA_ENABLED=true` 时先校验 database-backed `SocDaemonService`，再构造 Kafka client，避免数据库配置错误时先连接 broker。

已完成 local smoke runner：

- 脚本：`backend/scripts/soc_kafka_smoke.py`。
- 前提：已有 Kafka/Redpanda broker 可连接；脚本不负责启动 Docker。
- 默认 broker：`localhost:9092`。
- 默认使用带时间戳后缀的临时 smoke topics，避免复用固定 topic 时被历史消息污染；`--stable-topics` 可使用固定 SOC topic 名。
- 脚本会创建/确认 input topics 和 dead-letter topic。
- 脚本会发布 sample alert，调用真实 `soc daemon consume` CLI path，并验证 `soc list` 中出现 summary。
- `--include-dead-letter` 会额外发布坏 JSON，验证 dead-letter topic 中出现 `soc.kafka_dead_letter.v1`。
- 脚本会用同一 consumer group 再 poll 一次，验证 post-commit idle，避免 offset commit 失效导致重复消费。

已完成 live Redpanda smoke：

- Docker container：`soc-redpanda-smoke`
- broker：`localhost:9092`
- group_id：`soc-smoke-1783064070`
- alert topic：`soc.alerts.raw.v1.smoke.1783064070`
- processed run：`RUN-C140EB6BEB70`
- alert_id：`ALT-SAMPLE-FP-001`
- summary_count：`1`
- review queue：empty，符合 approved scanner false positive / no review 预期。
- dead-letter：`soc.kafka_dead_letter.v1`，key `smoke-bad-1783064071`，error_type `KafkaMapperError`。
- post-commit consume：`idle`，说明同一 group 不会重复处理已提交 offset。
- smoke 过程中发现并修复：
  - `ConfluentKafkaConsumerPort` 能订阅自定义 topic，但 `SocKafkaConsumerRunner` mapper 仍使用默认 topic set。
  - 修复后 runner 接收 configured `alert_topics` / `approval_request_topics`，CLI 从 `KafkaConsumerSettings` 传入。

已完成 bounded runner loop counters：

- `SocKafkaConsumerRunner.run(max_records=..., stop_on_idle=True)` 下沉有限 poll loop。
- `KafkaRunnerLoopResult` 暴露：
  - `processed_count`
  - `dead_lettered_count`
  - `idle_count`
  - `committed_count`
- `soc daemon consume` 输出 `counters` JSON。
- 这不是生产长驻 daemon，只是把 loop 语义集中到 runner，为 readiness、metrics、supervisor 和 graceful shutdown 做准备。

已完成 daemon status/readiness contract：

- `soc_agent.daemon.kafka_status` 提供 readiness/status 结构：
  - `KafkaDaemonStatus`
  - `KafkaDaemonDatabaseStatus`
  - `KafkaDaemonBrokerStatus`
- `soc daemon status` 输出 versioned JSON：`soc.kafka_daemon_status.v1`。
- 默认检查 database readiness：
  - database URL 必须能解析。
  - 默认执行 `SELECT 1`。
  - 输出中的 database URL 必须隐藏 password。
- 默认不连接 broker：
  - 未传 `--check-broker` 时只展示 Kafka adapter 配置状态。
  - 传 `--check-broker` 时才构造真实 broker adapter 并做一次轻量 `poll()`。
- `SOC_KAFKA_ENABLED=false` 时 status 可用于本地/CI readiness dry-run，不要求 broker 存在。
- `SOC_KAFKA_ENABLED=true --check-broker` 时可验证本地 Redpanda/Kafka broker 可达；该检查不处理业务消息、不提交 offset、不写业务 DB。
- CLI exit code：
  - ready -> `0`
  - unready -> `1`

已完成 long-running daemon run loop：

- `soc_agent.daemon.kafka_daemon` 提供长驻 loop controller：
  - `KafkaDaemonStopSignal`
  - `KafkaDaemonRunResult`
  - `SocKafkaDaemonRunner`
- `SocKafkaDaemonRunner` 只包装现有 `SocKafkaConsumerRunner.process_next()`：
  - 不重写 mapper。
  - 不重写 commit/dead-letter 语义。
  - 不直接调用 core service。
- CLI 新增 `soc daemon run`：
  - 默认 `max_loops=None`，表达长驻 daemon 语义。
  - `--max-loops` 只用于测试、本地验收和 smoke。
  - `--idle-sleep-ms` 控制 idle poll 后 sleep，测试可设为 `0`。
  - `--include-results` 才输出每轮结果，避免长驻进程输出无限增长。
  - 输出 schema：`soc.kafka_daemon_run_result.v1`。
- graceful shutdown：
  - CLI 安装 `SIGINT` / `SIGTERM` handler。
  - handler 只设置 stop flag，不在 signal handler 中做 DB/Kafka/IO 工作。
  - 当前 poll 返回后退出 loop。
  - controller 在 `finally` 中 close runner/consumer port。

已完成 daemon metrics/backoff：

- `KafkaDaemonRunResult` 暴露最小运行 metrics：
  - `started_at`
  - `stopped_at`
  - `error_count`
  - `consecutive_error_count`
  - `last_success_at`
  - `last_error_at`
  - `last_error_type`
  - `last_error_message`
- `soc daemon run` 输出 `metrics` JSON 节点，schema 仍为 `soc.kafka_daemon_run_result.v1`。
- `SocKafkaDaemonRunner` 对 poll/runtime 层异常做 backoff：
  - 默认 `error_backoff_seconds=1.0`。
  - 默认 `max_consecutive_errors=3`。
  - 达到阈值后停止，`stop_reason=max_consecutive_errors_reached`。
- CLI 参数：
  - `--error-backoff-ms`
  - `--max-consecutive-errors`
- 约束：
  - backoff 只处理 daemon loop / adapter runtime 异常。
  - mapper/service failure 的 dead-letter + commit 语义仍属于 `SocKafkaConsumerRunner`，不在 daemon controller 重写。
  - `--error-backoff-ms 0` 和 `--max-consecutive-errors 0` 只应用于测试/本地验证或明确的外部 supervisor 托管场景。

已完成 production entrypoint / healthcheck：

- `backend/scripts/soc_daemon_entrypoint.sh`
  - 生产启动脚本。
  - 默认要求 `SOC_KAFKA_ENABLED=true`。
  - `SOC_DAEMON_ALLOW_DISABLED=true` 只允许测试/本地验证。
  - 可选 `SOC_DAEMON_UPGRADE_DB=true`。
  - 可选 `SOC_DAEMON_PRESTART_STATUS_CHECK=true`。
  - 最终 exec `python -m soc_agent.cli daemon run ...`。
- `backend/scripts/soc_daemon_healthcheck.sh`
  - 默认执行 `soc daemon status --check-broker`。
  - 不处理业务消息，不 commit offset，不写 DB。
- 运行说明：
  - `.notes/ai_soc/soc-daemon-production-runbook.md`
  - 当前不修改 DeerFlow 主 docker-compose；SOC daemon 后续通过独立 overlay 或生产模板接入。

已完成 isolated run-mode smoke：

- `backend/scripts/soc_kafka_smoke.py` 支持 `--mode {consume,run}`。
- `consume` 模式保持原有 `soc daemon consume --max-records 1` smoke。
- `run` 模式验证生产入口：
  - `soc daemon run --max-loops 1`
  - `--idle-sleep-ms 0`
  - `--error-backoff-ms 0`
  - `--include-results`
- 默认继续使用 timestamped isolated topics，避免消费默认 topic 历史消息。
- post-commit idle 检查继续使用同一 group 的 bounded consume，验证 run-mode 已提交 offset。
- 已用 Redpanda 验证：
  - sample alert processed。
  - summary 落库。
  - bad JSON 进入 dead-letter。
  - post-commit idle。

已完成 JSONL metric sink：

- `KafkaDaemonMetricSink` 是 daemon runtime event sink 协议。
- `JsonLineKafkaDaemonMetricSink` 输出一行一个 JSON object。
- `soc daemon run --metric-jsonl stdout|stderr` 可开启运行中事件。
- `backend/scripts/soc_daemon_entrypoint.sh` 支持 `SOC_DAEMON_METRIC_JSONL=stdout|stderr`。
- 事件 schema：`soc.kafka_daemon_metric.v1`。
- 事件类型：
  - `start`
  - `result`
  - `error`
  - `stop`
- 默认关闭，保持 CLI/smoke JSON summary 兼容。
- 生产建议输出到 stderr，stdout 保留最终 run summary。
- result event 只输出 record metadata 和 daemon_result 摘要，不输出完整告警 payload。

已完成 production compose overlay：

- `docker/docker-compose.soc-daemon.yaml` 提供显式 opt-in SOC daemon service。
- 默认不进入 DeerFlow 主 docker flow，不影响 `make docker-start`。
- overlay 启动命令：
  - `docker compose -p deer-flow-dev -f docker-compose-dev.yaml -f docker-compose.soc-daemon.yaml up -d soc-daemon`
- service contract：
  - command：`backend/scripts/soc_daemon_entrypoint.sh`
  - healthcheck：`backend/scripts/soc_daemon_healthcheck.sh`
  - default metric sink：`SOC_DAEMON_METRIC_JSONL=stderr`
- 当前 build contract：
  - `backend/Dockerfile` 支持 comma/whitespace 分隔的 `UV_EXTRAS`，例如 `postgres,kafka` 或 `postgres kafka`。
  - overlay 默认 `SOC_DAEMON_UV_EXTRAS=postgres,kafka`，生产镜像同时具备 PostgreSQL 和 Kafka 依赖。

## 下一刀建议

进入 deployment hardening / K8s template planning：

- 当前 `soc daemon consume` 是有限 poll，适合 smoke/手工验证。
- 当前 `soc daemon run` 是长驻 loop shell，已具备 graceful stop、结构化 counters、metrics 和 error backoff。
- 当前生产入口和 healthcheck 已固定。
- 当前 run-mode smoke 已覆盖真实 broker path。
- 当前 JSONL metric sink 已能被日志系统采集。
- 当前 production compose overlay 已是显式 opt-in。
- 当前 Dockerfile multi-extra support 已完成。
- 当前 K8s opt-in template 已提供 deployment contract 示例。
- 后续建议：
  - 用真实环境参数验证 K8s manifest：image、namespace、secret manager、日志采集标签、resource sizing。
  - worker pool / concurrency 已先做规划，见 `.notes/ai_soc/kafka-worker-pool-concurrency-plan.md`；实现前先补 partition commit tracker 和幂等写入边界。

## Prometheus Deferred Plan

当前暂不实现 Prometheus exporter，先用 `SOC_DAEMON_METRIC_JSONL=stderr` 接日志系统。原因：

- 部署形态尚未最终确定：Docker Compose、K8s、公司内部平台的服务发现方式不同。
- `/metrics` exporter 需要额外 HTTP 端口、生命周期管理和 scrape 配置，当前会提前增加运行复杂度。
- 现有 JSONL 已能覆盖排障和初期告警：start/result/error/stop、错误类型、topic/partition/offset、处理计数。

后续触发条件：

- 测试环境或生产环境已明确接入 Prometheus/Grafana。
- 需要跨实例聚合 daemon 状态，而日志平台查询不足以稳定支撑。
- 需要基于 counters/gauges 做标准化 SLO/告警规则。

候选指标：

| Metric | Type | Labels | 说明 |
|---|---|---|---|
| `soc_kafka_processed_total` | counter | `topic`, `kind` | 成功处理消息数 |
| `soc_kafka_dead_lettered_total` | counter | `topic`, `error_type` | dead-letter 数 |
| `soc_kafka_committed_total` | counter | `topic` | offset commit 数 |
| `soc_kafka_idle_total` | counter | `group_id` | idle poll 次数 |
| `soc_kafka_errors_total` | counter | `error_type` | loop-level adapter/runtime error 数 |
| `soc_kafka_consecutive_errors` | gauge | `group_id` | 当前连续错误数 |
| `soc_kafka_last_success_timestamp` | gauge | `group_id` | 最近成功处理时间 |
| `soc_kafka_last_error_timestamp` | gauge | `group_id`, `error_type` | 最近错误时间 |
| `soc_kafka_run_up` | gauge | `group_id` | daemon run loop 是否存活 |

实现边界：

- Prometheus exporter 只能读取 daemon metrics snapshot 或订阅 metric sink，不能参与 Kafka 消费、commit、dead-letter 或业务判断。
- exporter 不暴露 alert raw payload、raw_message、DB URL、Kafka secret、SASL/TLS secret。
- JSONL sink 保留为最低可用观测面；Prometheus 是增量能力，不替代日志审计。
