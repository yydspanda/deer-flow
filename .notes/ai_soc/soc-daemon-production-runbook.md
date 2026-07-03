# SOC Kafka Daemon Production Runbook

> 目标：固定 SOC Kafka daemon 的生产运行方式。本文是运行约定，不表示当前已经接入正式生产 Kafka、正式凭证或自动处置。

## 入口

生产 daemon 使用独立入口，不复用 Gateway API 进程：

```bash
cd backend
SOC_KAFKA_ENABLED=true \
SOC_DATABASE_URL=postgresql+psycopg://... \
SOC_KAFKA_BOOTSTRAP_SERVERS=kafka-1:9092,kafka-2:9092 \
SOC_KAFKA_GROUP_ID=soc-agent-daemon \
SOC_KAFKA_ALERT_TOPICS=soc.alerts.raw.v1 \
SOC_KAFKA_APPROVAL_REQUEST_TOPICS=soc.approvals.requests.v1 \
SOC_KAFKA_DEAD_LETTER_TOPIC=soc.alerts.dead_letter.v1 \
./scripts/soc_daemon_entrypoint.sh
```

`soc daemon consume` 仍只用于 smoke / bounded poll；生产使用 `soc daemon run`，推荐通过 `scripts/soc_daemon_entrypoint.sh` 启动。

## Entrypoint

脚本：`backend/scripts/soc_daemon_entrypoint.sh`

默认行为：

- 默认要求 `SOC_KAFKA_ENABLED=true`。
- 未显式设置时，`SOC_KAFKA_ENABLED` 默认设为 `true`，避免生产容器空转在 null adapter 上。
- 只有测试/本地验证可以设置 `SOC_DAEMON_ALLOW_DISABLED=true`。
- 可选 `SOC_DAEMON_UPGRADE_DB=true` 在启动前执行 `soc db upgrade`；生产更推荐由独立 migration job 执行。
- 可选 `SOC_DAEMON_PRESTART_STATUS_CHECK=true` 在启动前执行 healthcheck。
- 最终 exec：
  - `python -m soc_agent.cli daemon run`
  - `--idle-sleep-ms`
  - `--error-backoff-ms`
  - `--max-consecutive-errors`

## Healthcheck

脚本：`backend/scripts/soc_daemon_healthcheck.sh`

默认行为：

- 执行 `soc daemon status --check-broker`。
- 检查 DB 可达。
- 检查 broker 可 poll。
- 不处理业务消息，不提交 offset，不写 dead-letter，不写业务 DB。

本地/测试可以关闭 broker check：

```bash
SOC_DAEMON_HEALTHCHECK_BROKER=false ./scripts/soc_daemon_healthcheck.sh
```

生产不建议关闭 DB check。`SOC_DAEMON_HEALTHCHECK_DATABASE=false` 只用于配置排障，不应作为 readiness。

## Environment Contract

| Env | 默认值 | 说明 |
|---|---|---|
| `SOC_DATABASE_URL` | 无 | SOC repository 数据库；生产必须是 PostgreSQL |
| `SOC_KAFKA_ENABLED` | `true` in entrypoint | 生产 daemon 必须启用真实 Kafka adapter |
| `SOC_KAFKA_BOOTSTRAP_SERVERS` | `localhost:9092` | Kafka/Redpanda broker 列表 |
| `SOC_KAFKA_ALERT_TOPICS` | `soc.alerts.raw.v1` | 预警输入 topic |
| `SOC_KAFKA_APPROVAL_REQUEST_TOPICS` | `soc.approvals.requests.v1` | 审批请求输入 topic |
| `SOC_KAFKA_GROUP_ID` | `soc-agent-daemon` | Consumer group |
| `SOC_KAFKA_CLIENT_ID` | `soc-agent-consumer` | Consumer client id |
| `SOC_KAFKA_DEAD_LETTER_TOPIC` | `soc.alerts.dead_letter.v1` | Dead-letter output topic |
| `SOC_KAFKA_SECURITY_PROTOCOL` | `PLAINTEXT` | `PLAINTEXT` / `SSL` / `SASL_PLAINTEXT` / `SASL_SSL` |
| `SOC_KAFKA_SASL_PASSWORD_ENV` | 无 | 只保存 secret env 名；不要把 secret 写入 config/notes/DB |
| `SOC_DAEMON_IDLE_SLEEP_MS` | `1000` | idle poll 后 sleep |
| `SOC_DAEMON_ERROR_BACKOFF_MS` | `1000` | adapter/runtime error 后 backoff |
| `SOC_DAEMON_MAX_CONSECUTIVE_ERRORS` | `3` | 连续错误停止阈值；`0` 表示交给外部 supervisor |
| `SOC_DAEMON_MAX_LOOPS` | 无 | 仅用于本地/smoke，不用于生产 |
| `SOC_DAEMON_INCLUDE_RESULTS` | `false` | 生产保持 false，避免输出无限增长 |

## Docker Compose Sketch

不要直接改 DeerFlow 主 compose 作为默认行为。SOC daemon 是业务扩展进程，建议用独立 overlay 或生产部署模板接入：

```yaml
services:
  soc-daemon:
    build:
      context: ..
      dockerfile: backend/Dockerfile
      target: runtime
      args:
        UV_EXTRAS: "postgres kafka"
    command: ["sh", "backend/scripts/soc_daemon_entrypoint.sh"]
    env_file:
      - ../.env
    environment:
      SOC_KAFKA_ENABLED: "true"
      SOC_DAEMON_IDLE_SLEEP_MS: "1000"
      SOC_DAEMON_ERROR_BACKOFF_MS: "1000"
      SOC_DAEMON_MAX_CONSECUTIVE_ERRORS: "3"
    healthcheck:
      test: ["CMD", "sh", "backend/scripts/soc_daemon_healthcheck.sh"]
      interval: 15s
      timeout: 10s
      retries: 4
      start_period: 30s
    restart: unless-stopped
```

## Logging

当前最小约定：

- `soc daemon run` 正常退出时输出 `soc.kafka_daemon_run_result.v1` JSON。
- `stderr` 输出启动配置错误、adapter error 上抛或 CLI 参数错误。
- 长驻运行过程中的持续 metrics sink 暂未落地；下一步可以加 `SocDaemonMetricSink`，先输出 JSON lines，再接 Prometheus。
- 生产日志采集应保留 stdout/stderr，并按 `schema_version`、`stop_reason`、`metrics.error_count`、`metrics.last_error_type` 建索引。

## Smoke

本地 broker smoke 继续使用隔离 topic：

```bash
cd backend
./.venv/bin/python scripts/soc_kafka_smoke.py --include-dead-letter
```

不要用固定默认 topic + 新 group 做验收，否则可能消费历史消息。

## Non-goals

- 不在 daemon 中执行自动处置。
- 不把 daemon 变成用户入口。
- 不让 daemon 绕过 `SocDaemonService.process_message()`。
- 不在 entrypoint 中保存或打印 secret。
