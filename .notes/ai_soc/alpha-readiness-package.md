# SOC Alpha Readiness Package / Alpha 就绪评审包

Status: **Technical gates and independent review passed; accountable sign-off pending / 技术门禁与独立评审建议通过，责任人签字待完成**

Report schema: `soc.alpha_readiness_report.v1`

Task: `BG-03`

This is the Stage 3 exit packet. It packages existing acceptance evidence; it does not add another
capability backlog and does not turn local/test evidence into production proof. Capability status is
owned only by `audits/alpha-completeness-matrix.md`; stage order is owned only by
`delivery-roadmap.md`.

## 1. Decision / 当前结论

| Question / 问题 | Current answer / 当前答案 |
|---|---|
| Are code-controllable Alpha blockers closed? / 代码可控 Alpha 阻塞是否清零？ | **Yes**: authoritative matrix `Gap=0` |
| Is the local/test Alpha journey repeatable? / 本地测试闭环是否可重复？ | **Yes**, when the versioned acceptance and regression gates pass |
| May Stage 4 start automatically? / 是否自动进入 Stage 4？ | **No**: product/SOC/security/platform owner review is required |
| Is the system production or pilot ready? / 是否生产或试点就绪？ | **No**: real providers, infrastructure, labels, operations and response rollout remain external gates |

The machine report intentionally emits:

```json
{
  "alpha_candidate_ready": true,
  "release_decision": "pending_owner_review",
  "stage_transition_allowed": false,
  "production_ready": false
}
```

`alpha_candidate_ready=true` means only that the documented technical gates passed. A human owner
decision is a separate governance fact and must not be inferred by a script.

## 2. One-Command Evidence / 一键证据

From the repository root:

```bash
./scripts/soc-alpha-readiness.sh all
```

The command runs, in order:

```mermaid
flowchart LR
    A["🧹 Prepare<br/>隔离输出目录"] --> B["🧪 Alpha Acceptance<br/>Core + Kafka + Web"]
    B --> C["🐍 Backend SOC Suite<br/>全量 SOC pytest"]
    C --> D["🏗️ Architecture + Migration<br/>边界与迁移环境"]
    D --> E["📦 Readiness Report<br/>版本 + hash + gate"]
    E --> F["👥 Owner Review<br/>产品/SOC/安全/平台"]
    F -->|"approved"| G["🏭 Stage 4<br/>Real Integration"]
    F -->|"changes requested"| H["🔁 Return to numbered task<br/>带编号整改"]
```

Output is isolated and gitignored:

```text
backend/.deer-flow/soc-alpha-readiness/
├── alpha-readiness-report.json
├── acceptance.log
├── soc-alpha-acceptance/
│   ├── alpha-acceptance-report.json
│   ├── core/
│   ├── kafka/
│   └── frontend/
└── tests/
    ├── backend-soc.log
    ├── backend-soc.status.json
    ├── architecture-migrations.log
    └── architecture-migrations.status.json
```

The readiness report fails closed when any required report is missing/malformed, acceptance does not
pass, a pytest command exits non-zero, no passing tests can be parsed, the authoritative matrix has a
non-zero `Gap`, or Stage 4 work packages cannot be read from the roadmap.

## 3. Evidence Contract / 证据契约

| Gate | Source | Pass condition | Claim boundary |
|---|---|---|---|
| Alpha E2E | nested `soc.alpha_acceptance_report.v1` | Core/Kafka/frontend pass with exact APT/EDR/HIDS coverage | Deterministic analyzer, local SQLite/Redpanda, mock investigation facts and browser HTTP fixture remain disclosed |
| Backend SOC regression | `tests/test_soc_*.py` | pytest exit `0`, parsed pass count greater than zero, no failures/errors | Code regression only; it is not production load or provider validation |
| Architecture/migrations | architecture boundary + persistence migration environment tests | pytest exit `0`, parsed pass count greater than zero, no failures/errors | Import and environment wiring; not a PostgreSQL backup/failover exercise |
| Completeness | authoritative 50-row matrix + SHA-256 | `Gap=0`, counts add to `Total` | Mock, Data-gated and Deferred remain visible and non-zero |
| Stage 4 handoff | delivery roadmap + SHA-256 | `PI-01..05` remain discoverable | The report references work package IDs; it does not create a second status list |

The report records the Git commit/branch and whether the working tree is clean. A technical pass from
a dirty tree is useful during development, but the release archive must be regenerated from a clean
checkout of the reviewed commit.

## 4. Alpha Scope / Alpha 可承诺范围

The current local/test Alpha can demonstrate and regress:

- strict vendor-neutral Kafka ingress with PingAn APT/EDR/HIDS adapters;
- deterministic normalization, entity extraction, fact reconstruction and bounded evidence;
- fixed Runtime control flow with explicit deterministic/live-LLM analyzer selection;
- SQL run/summary/review/audit/replay and fail-closed decision guards;
- ReviewQueue Web/TUI, bounded Lead Agent context, correction and notes;
- approval request/grant/preflight boundaries without real response side effects;
- external disposition canonical ingress, idempotency and feedback-to-candidate path;
- confirmed-memory review and governed read-only retrieval activation;
- correlation/domain findings and normalization/governance evaluation surfaces.

It must not be presented as proof of:

- real CMDB/EDR/HIDS/TI/security-tag or Zeus/ITSM/SOAR connectivity;
- production PostgreSQL/Kafka/K8s capacity, ACL/TLS, backup, failover or disaster recovery;
- calibrated model probability, production precision/recall or representative traffic distribution;
- automatic close, suppression, host isolation, IP blocking or attack simulation;
- Prometheus/SLO operations, worker-pool throughput or autonomous specialist Sub Agents.

The exact current categories and capability IDs remain in the completeness matrix and are embedded by
reference/hash in each readiness report.

## 5. Shared Alpha Deployment Preconditions / 共享 Alpha 部署前置

The one-command gate is local/test evidence. A shared dev/staging Alpha must not silently copy those
local defaults. Before deployment, the owner must provide and review:

| Area | Required before start | Fail-closed rule |
|---|---|---|
| Database | Dedicated PostgreSQL URL, least-privilege account, backup/restore owner and migration window | Do not use SQLite for shared staging/production |
| Kafka | Versioned input topic, consumer group, DLQ, ACL/TLS/SASL secrets and retention owner | Do not accept bare vendor alerts or enable auto commit |
| Identity | Gateway authentication plus service actors/roles and trusted `auth_source` mapping | Anonymous/unknown provenance cannot perform L3 mutation |
| Models | Explicit `SOC_ANALYZER_MODE`, registered model, budget/concurrency/timeouts and secret injection | Never silently fall back from requested live mode to stub |
| Providers | Explicit registry/MCP allowlist and evidence redaction rules | Missing real provider stays mock/data-gated; LLM cannot invent facts |
| Actions | Approval policy, adapter preflight and audited owner | Keep real side effects disabled for Alpha |
| Data | Tenant mapping, retention, redaction and replay approval | Raw alert/provider payload must not enter prompts or broad logs |

Deployment sequence for a reviewed environment:

1. Freeze an image/commit and archive a clean-checkout readiness report outside Git.
2. Back up the SOC database and record current `soc_alembic_version`.
3. Inject secrets through the deployment secret boundary; do not commit `.env` or rendered manifests.
4. Run migrations as a separate controlled job:

   ```bash
   cd backend
   python -m soc_agent.cli db upgrade --database-url "$SOC_DATABASE_URL"
   ```

5. Start Gateway/Web first and validate authenticated ReviewQueue reads and one non-destructive write.
6. Run daemon readiness without consuming a business record:

   ```bash
   sh backend/scripts/soc_daemon_healthcheck.sh
   ```

7. Start one serial daemon replica in shadow/manual-review mode. Keep high-risk external adapters
   disabled and inspect lag, DLQ, Runtime failures and ReviewQueue creation.
8. Publish only approved canary envelopes; confirm offset, idempotency, audit and replay before opening
   the full input topic.

`docker/docker-compose.soc-daemon.yaml` and `docker/k8s/soc-daemon.yaml` are opt-in templates, not
validated production manifests. Image, namespace, resource limits, broker, topics, secrets and
observability must be replaced and reviewed in `PI-02/PI-04`.

## 6. Stop and Rollback / 停止与回滚

Rollback prioritizes stopping new side effects and preserving evidence:

1. **Stop ingress first**: pause/scale down the SOC Kafka daemon; do not delete topics, offsets or DLQ.
2. **Keep analyst access read-only where possible**: preserve ReviewQueue, run, audit and replay data.
3. **Capture evidence**: archive sanitized daemon metrics/errors, release image/commit, config version,
   DB migration version and affected topic partitions/offsets.
4. **Rollback application/config**: restore the last reviewed image and configuration. Keep
   high-risk adapters disabled throughout.
5. **Do not automatically downgrade schema**: all revisions have Alembic downgrade functions, but
   the public SOC CLI only exposes controlled upgrade targets. Prefer forward-compatible application
   rollback; if schema rollback is unavoidable, use an approved backup restore or separately reviewed
   Alembic procedure during downtime.
6. **Revalidate before resume**: DB readiness, broker connectivity, one canary envelope, exactly-once
   logical mutation/idempotency, ReviewQueue visibility and audit lineage must pass.
7. **Resume gradually**: one serial consumer first; monitor errors/DLQ/lag before restoring input rate.

Never “fix” a failed rollout by deleting audit rows, overwriting runs, resetting offsets without an
incident record, accepting unversioned payloads, or bypassing approval/RBAC.

## 7. Stage 4 Handoff / 下一阶段输入

This table groups required inputs for review; status remains owned by the roadmap/matrix.

| Work package | Capability references | External owner/input needed |
|---|---|---|
| `PI-01 Real providers` | `AC-09`, `AC-33`, `AC-44` | Zeus/ITSM/SOAR feed; CMDB/EDR/HIDS/TI/tag endpoints; authoritative activity sources; credentials and approved payloads |
| `PI-02 Real infrastructure` | `AC-48` | PostgreSQL/Kafka/K8s topology, ACL/TLS, SLO/capacity targets, backup/failover environment |
| `PI-03 Real labels and calibration` | `AC-19` | Desensitized labels, reviewer ownership, representative scope and offline calibration/evaluation protocol |
| `PI-04 Operations and security` | `AC-47` plus PI security gate | Metrics/SLO owner, alerting, secret rotation, audit retention, privacy and incident response process |
| `PI-05 Governed rollout` | `AC-36` | Staging response adapters, compensation/verification, owner approval and shadow-to-pilot rollback criteria |

Entry into a PI work package requires its concrete external inputs. Adding another local mock does not
satisfy the handoff.

## 8. Owner Review / 负责人评审

The independent four-lens assessment is recorded in [`alpha-gate-review.md`](alpha-gate-review.md).
It recommends approving the Stage 3 technical exit, while keeping shared deployment, pilot,
production and high-risk execution unapproved. This recommendation does not replace accountable
owner identity and sign-off.

| Reviewer role | Must answer | Status |
|---|---|---|
| Product owner | Is Alpha scope useful and honestly presented? | Advisory review: recommend approve; accountable sign-off pending |
| SOC operations | Can analysts complete review/feedback, and are operating owners named? | Advisory review: recommend approve; accountable sign-off pending |
| Security | Are data, identity, tool/action and audit boundaries acceptable? | Advisory review: recommend approve; accountable sign-off pending |
| Platform/infrastructure | Are deployment, backup, stop/rollback and PI inputs actionable? | Advisory review: recommend approve; accountable sign-off pending |

Approval criteria:

- review the generated report and nested artifact hashes from a clean reviewed commit;
- accept the explicit Mock/Data-gated/Deferred boundaries;
- assign owners for `PI-01..05` inputs;
- record approve/changes-requested, reviewer identities, time and reason in the release/change system;
- only then update `delivery-roadmap.md` and `progress.md` to make Stage 4 current.

Until that decision exists, `BG-03` remains in progress even when all technical gates pass.
