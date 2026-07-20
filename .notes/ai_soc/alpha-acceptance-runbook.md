# SOC Alpha Acceptance Runbook / Alpha 验收手册

Status: `BG-P1-05` executable acceptance contract

Acceptance schema: `soc.alpha_acceptance_report.v1`

This runbook explains how to prove the local/test SOC Alpha boundary with one command. It does not
replace the delivery roadmap or completeness matrix, and a passing report does not claim production
readiness.

## 1. One-Command Acceptance / 一键验收

Run from the repository root:

```bash
./scripts/soc-alpha-acceptance.sh all
```

The command resets only its isolated, gitignored output directory and then runs four components in
order:

| Component | Executable proof | Boundary |
|---|---|---|
| `core` | APT/EDR/HIDS through public SOC CLI, SQL persistence, registered Gateway handlers/services, external feedback, durable audits and replay | Uses deterministic analyzer and local SQLite for a repeatable baseline |
| `kafka` | Strict `SocAlertRawEnvelope` through a real local Redpanda broker, consumer, offset commit and poison-message DLQ | Proves Kafka protocol semantics, not production ACL/TLS/capacity/recovery |
| `frontend` | SOC API client regression, rendered Review Web workflows in Chromium, and `pnpm check` | Browser transport is deterministic mocked HTTP; backend business behavior is proven separately by the real service/SQL journey |
| `finalize` | Validates all component artifacts, exact APT/EDR/HIDS coverage, failure gates and hashes, then seals one report | Missing or failed components make the aggregate report fail |

Docker is required for the default Kafka component. If Docker is unavailable in WSL, start Docker
Desktop, wait for **Engine Ready**, and ensure the current distribution is enabled under WSL
Integration before rerunning the command.

## 2. Prerequisites / 前置条件

- Backend virtual environment exists at `backend/.venv` with the SOC/Kafka dependencies installed.
- Frontend dependencies and Playwright Chromium are installed.
- Docker daemon is available, unless an existing Kafka-compatible broker is explicitly supplied.
- Port `3100` is available for the isolated auth-disabled frontend, unless another URL/port is
  configured.

The runner never reads or writes the normal SOC development database. Its default databases and all
evidence live under `backend/.deer-flow/soc-alpha-acceptance/`.

## 3. Component Commands / 分步命令

```bash
./scripts/soc-alpha-acceptance.sh core
./scripts/soc-alpha-acceptance.sh kafka
./scripts/soc-alpha-acceptance.sh frontend
./scripts/soc-alpha-acceptance.sh finalize
```

Useful overrides:

```bash
SOC_ALPHA_KAFKA_BOOTSTRAP_SERVERS=localhost:9092 \
  ./scripts/soc-alpha-acceptance.sh kafka

SOC_ALPHA_FRONTEND_BASE_URL=http://127.0.0.1:3000 \
  ./scripts/soc-alpha-acceptance.sh frontend

SOC_ALPHA_ACCEPTANCE_OUTPUT_DIR=/tmp/soc-alpha-acceptance \
  ./scripts/soc-alpha-acceptance.sh all
```

For deletion safety, the output directory basename must contain `soc-alpha`; `prepare/all` refuses
broader paths such as a repository root or `/tmp` itself.

When no frontend URL is supplied, the script starts an isolated auth-disabled Next.js development
server on `127.0.0.1:3100`, waits for a `200` response, runs Chromium, and removes the whole process
group on exit. When no Kafka bootstrap server is supplied, it starts and removes an ephemeral
Redpanda container on `localhost:19092`.

## 4. Evidence Package / 证据包

```text
backend/.deer-flow/soc-alpha-acceptance/
├── alpha-acceptance-report.json       # aggregate pass/fail report + artifact hashes
├── soc_alpha_core.db                  # isolated core journey database
├── soc_alpha_kafka.db                 # isolated Kafka journey database
├── core/
│   ├── core-result.json
│   ├── cli-demo.json
│   ├── cli-runs.json
│   ├── cli-review-contexts.json
│   ├── cli-replays.json
│   ├── gateway-feedback-journey.json
│   └── persistence-audit.json
├── kafka/
│   ├── status.json
│   ├── apt.json
│   ├── edr.json
│   ├── hids.json
│   └── kafka.log
└── frontend/
    ├── status.json
    ├── api-unit.log
    ├── browser.log
    ├── check.log
    └── server.log
```

The aggregate report is the first file to review. A valid pass requires:

- all three component statuses are `passed`;
- core fixtures cover APT, EDR and HIDS and each creates a run, ReviewQueue item and linked replay;
- external feedback applies one correction/close, an exact retry is idempotent, and a changed retry
  is rejected with `409`;
- decision and mutation audits are persisted;
- Kafka consumes and commits exactly alert IDs `2026494`, `1965810` and `HIDS-2026-0001`;
- malformed Kafka JSON is dead-lettered and committed;
- browser workflows cover queue/context, close/correct, approval, memory review/activation,
  disposition sample/outcome and normalization maintenance;
- every evidence file listed in `artifact_manifest` has a SHA-256 digest.

## 5. Claim Boundary / 结论边界

| Report evidence | What it proves | What it does not prove |
|---|---|---|
| Sanitized PingAn fixtures | Canonical contracts handle representative APT/EDR/HIDS inputs | Current production data distribution or every vendor schema |
| Deterministic analyzer | Repeatable Runtime, policy, persistence and replay behavior | Live-model quality, latency, cost or provider availability |
| Mock read-only actions | Action/policy/evidence return path is wired | Real CMDB/EDR/TI/security-tag facts |
| Local SQLite | Repository/UoW behavior in local acceptance | PostgreSQL capacity, backup, failover or connection-pool behavior |
| Local Redpanda | Real Kafka wire protocol, consume/commit/DLQ behavior | Production ACL, TLS, lag, capacity or broker recovery |
| Mocked browser transport | Real React rendering and request contracts | One deployed network stack with real authentication/backend transport |
| Registered Gateway handlers + SQL | Real feedback/service/dependency behavior | Shared Starlette/httpx transport compatibility; that remains covered by the backend API transport suite |
| Approval/preflight flow | High-risk action remains gated and inspectable | Any real external response side effect |

The report must remain honest about these boundaries. More local mocks cannot complete `PA-12`,
production infrastructure evidence, production calibration, or real response execution.

## 6. Failure Handling / 失败处理

- A component command writes its status/log even when it fails; rerun that component after fixing the
  cause, then run `finalize`.
- `finalize` fails if an artifact is missing, a component is not passed, Kafka source coverage is
  incomplete, or a required semantic check is false.
- A changed retry for the same external source event must remain a conflict, not a second mutation.
- Replay must create a new run linked by `replay_of_run_id`; it must never overwrite the source run.
- The output directory is gitignored and can contain alert-derived data. Do not commit it.

The 2026-07-20 baseline completed with aggregate status `passed`. Re-run the command for each release
candidate; do not reuse an old report as evidence for changed code.
