# SOC Kafka Worker Pool / Concurrency Plan

> 状态：Planning + first primitive implemented。当前不立即实现并发代码；先固定语义边界，避免后续为了吞吐破坏 Kafka offset、dead-letter、审批和审计可靠性。

## 背景

当前 Kafka daemon 已经具备：

- 真实 broker adapter：`ConfluentKafkaConsumerPort`
- 单条串行 runner：`SocKafkaConsumerRunner.process_next()`
- 长驻 daemon shell：`SocKafkaDaemonRunner`
- manual commit
- dead-letter 后 commit
- JSONL metric sink
- Docker Compose / K8s opt-in deployment contract

当前处理链路是：

```text
poll one record
  -> map KafkaRecord to SocDaemonMessage
  -> SocDaemonService.process_message()
  -> success: commit offset
  -> mapper/service failure: send dead-letter, then commit offset
```

这个串行模型适合 Phase 1，因为最容易验证、不容易丢消息，也方便排查。但后续真实流量接入后，可能需要并发处理来降低积压和端到端延迟。

## Product Verdict

**Plan now, implement later behind flags.**

不要在还没有真实 Kafka/DB/K8s 参数时直接上 worker pool。先保留串行作为默认生产安全模式，等真实吞吐、延迟、LLM 成本和 DB 连接池数据出现后，再按计划打开有限并发。

## 核心原则

1. **Kafka adapter 仍只做 ingestion**
   - 并发不能把业务逻辑写进 Kafka consumer。
   - 业务仍只进入 `SocDaemonService.process_message()`。

2. **poller owns Kafka consumer**
   - Kafka consumer poll/commit/pause/resume 只能由 poller/controller 拥有。
   - worker 只处理已经解码/映射后的 work item，不直接调用 Kafka consumer。

3. **commit 必须 partition-aware**
   - 同一 partition 内不能因为 offset 10 先完成就提交到 11，如果 offset 9 还没完成。
   - commit 只能推进到该 partition 已连续完成的最大 offset + 1。

4. **bounded in-flight**
   - 不能无限 poll 后丢进内存队列。
   - 必须有 `max_in_flight` / `queue_depth` 上限和 backpressure。

5. **idempotency before concurrency**
   - 当前 daemon idempotency key 是 `kafka:{topic}:{partition}:{offset}`。
   - 真正并发前，需要让 run/approval/audit/summary 的写入能用该 key 做幂等保护，避免 retry 或重启导致重复数据。

6. **LLM 和 DB 是主要瓶颈**
   - 并发上限不能只看 Kafka 吞吐。
   - 要受 DB connection pool、LLM rate limit、token cost、分析耗时、review queue 写入能力共同约束。

## 不推荐的实现

不要让多个 worker 直接 poll Kafka、处理并各自 commit offset。

问题：

- 同一 consumer 多线程 poll/commit 风险高。
- offset 可能越过未完成消息。
- backpressure 和 graceful shutdown 难以验证。
- dead-letter 失败时容易错 commit。
- 后续 replay / audit 很难解释。

也不要一开始多 replica 跑同一个 daemon deployment。多 replica 依赖 Kafka consumer group rebalance、partition 数、DB 幂等和 worker shutdown 语义；这些要等单进程 worker pool 做稳后再考虑。

## 推荐架构

### 阶段 0：当前串行模式

当前默认模式保持：

```text
SocKafkaDaemonRunner
  -> SocKafkaConsumerRunner.process_next()
    -> KafkaConsumerPort.poll()
    -> map
    -> SocDaemonService.process_message()
    -> dead-letter or commit
```

默认生产模式仍是 `worker_concurrency=1`。

### 阶段 1：poller + bounded work queue

新增 worker pool controller，但默认关闭：

```text
Kafka poller/controller
  -> poll record
  -> assign WorkItem(topic, partition, offset, key, message)
  -> bounded queue
  -> N workers call SocDaemonService.process_message()
  -> workers return success/dead_letter_required/failure
  -> controller writes dead-letter if needed
  -> controller advances partition-safe commit
```

关键点：

- controller 保持 Kafka consumer ownership。
- worker 不直接 commit。
- worker 不直接 dead-letter。
- controller 根据 worker result 执行 dead-letter 和 commit。
- shutdown 时停止 poll，新任务不入队，等待 in-flight 完成或超时。

### 阶段 2：partition-aware commit tracker

需要维护：

```text
PartitionState:
  topic
  partition
  next_committable_offset
  completed_offsets
  failed_offsets_pending_dead_letter
  in_flight_offsets
```

commit 推进规则：

```text
while next_committable_offset is completed:
  next_committable_offset += 1
commit(topic, partition, next_committable_offset)
```

对于 mapper/service failure：

- 先 dead-letter 成功。
- 再将该 offset 标记为 completed。
- 如果 dead-letter 失败，该 offset 不能 completed，也不能 commit 越过它。

### 阶段 3：pause/resume backpressure

当队列或 in-flight 达到上限：

- controller 暂停 poll 或对具体 partitions pause。
- 队列下降后 resume。
- metric 输出 queue depth / in-flight / paused partitions。

第一版可以先不实现 partition pause，只靠 poll 节奏和 bounded queue 控制。

## 并发配置草案

| Env | 默认值 | 说明 |
|---|---|---|
| `SOC_KAFKA_WORKER_CONCURRENCY` | `1` | worker 数；`1` 等价当前串行安全模式 |
| `SOC_KAFKA_MAX_IN_FLIGHT` | `1` | 未完成消息上限；必须 >= concurrency |
| `SOC_KAFKA_PARTITION_ORDERED_COMMIT` | `true` | 必须保持 true；不提供关闭选项 |
| `SOC_KAFKA_WORKER_SHUTDOWN_TIMEOUT_MS` | `30000` | graceful shutdown 等待 in-flight 完成时间 |
| `SOC_KAFKA_BACKPRESSURE_SLEEP_MS` | `100` | 队列满时 controller backoff |
| `SOC_LLM_MAX_CONCURRENT_CALLS` | `1` | LLM 分析节点并发上限，默认保守 |

## 需要新增的指标

在现有 `soc.kafka_daemon_metric.v1` 基础上增加：

- `worker_concurrency`
- `queue_depth`
- `max_in_flight`
- `in_flight_count`
- `active_worker_count`
- `paused_partition_count`
- `record_process_latency_ms`
- `record_end_to_end_latency_ms`
- `dead_letter_latency_ms`
- `commit_lag_by_partition`
- `oldest_in_flight_age_ms`
- `llm_in_flight_count`
- `db_write_latency_ms`

这些指标先输出 JSONL；Prometheus exporter 仍后置。

## 必须先补的工程前置

1. **幂等写入边界**
   - `SocDaemonService.process_message()` 已能生成 `kafka:{topic}:{partition}:{offset}`。
   - 并发前需要确认 analyze / approval request / audit / summary 对同一 idempotency key 重放不会重复污染数据。
   - 当前状态：Done，`SocAnalysisService` 会通过 decision audit 的 `idempotency_key` 索引复用既有 run，避免重复写 summary / review queue / audit。

2. **worker result contract**
   - worker 返回结构化结果，不直接操作 Kafka：
     - `processed`
     - `dead_letter_required`
     - `retryable_error`
     - `fatal_error`

3. **partition commit tracker 单测**
   - offset 2 先完成、offset 1 未完成时不能提交 3。
   - offset 1 dead-letter 成功后才能推进。
   - dead-letter 失败时不能 commit。

4. **graceful shutdown 单测**
   - 收到 stop signal 后停止 poll。
   - 等待 in-flight。
   - timeout 后不 commit 未完成 offset。

5. **resource sizing**
   - DB pool size >= worker concurrency + admin/API 余量。
   - LLM concurrency 独立限流，不能等于 Kafka worker 数。

## 何时打开并发

满足以下条件再实现并默认可配置：

- 真实 Kafka/DB smoke 能稳定跑串行 daemon。
- 能测到平均处理耗时和 P95/P99 延迟。
- review queue / approval inbox 幂等语义明确。
- dead-letter topic 可用且告警可回放。
- LLM analyzer 如果开启，已有独立 rate limit。

## 初始实现切片建议

1. **PartitionCommitTracker**
   - 纯内存、纯单测，不接 Kafka。
   - 验证 partition ordered commit 语义。
   - 当前状态：Done，已落地 `backend/soc_agent/daemon/kafka_commit_tracker.py`。

2. **WorkerPoolResult contract**
   - dataclass/Pydantic schema，先不启动线程。
   - 明确 worker 不 commit、不 dead-letter。
   - 当前状态：Next。

3. **Bounded worker pool behind flag**
   - `SOC_KAFKA_WORKER_CONCURRENCY=1` 默认保持旧行为。
   - `>1` 才进入新 controller。

4. **JSONL metrics**
   - 输出 queue/in-flight/latency。
   - 不做 Prometheus。

5. **Local stress smoke**
   - 用 fake Kafka port / in-memory records 验证并发 ordering。
   - 再用 Redpanda 做小流量 smoke。

## 暂不做

- 不做自动扩缩容。
- 不做多 deployment replica。
- 不做跨进程分布式 worker。
- 不让 worker 直接操作 Kafka consumer。
- 不把 LLM 并发和 Kafka worker 并发绑死。
- 不为了吞吐绕过 review / approval / audit。
