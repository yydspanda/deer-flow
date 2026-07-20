# SOC Alpha Gate Review / Alpha 门禁评审记录

Status: **Independent review recommends approval; accountable owner sign-off pending / 独立评审建议通过，责任人签字待完成**

Task: `BG-03`

Review date: `2026-07-20`

Technical baseline: `4631f9fd2c0934891019e950e17fff9c8edbc660`

This document records the product, SOC operations, security, and platform assessment of the Alpha
readiness evidence. It is a review record, not another roadmap or capability register. Stage status is
owned by `delivery-roadmap.md`; capability status is owned by
`audits/alpha-completeness-matrix.md`.

## 1. Recommendation / 评审建议

| Decision item / 决策项 | Assessment / 评审结论 |
|---|---|
| Stage 3 technical exit / Stage 3 技术退出 | **Recommend approve / 建议通过** |
| Stage 4 real-integration planning / Stage 4 真实集成准备 | **Eligible after explicit owner approval / 正式签字后可开始** |
| Shared dev/staging deployment / 共享开发或测试环境部署 | **Not yet approved / 尚未批准** |
| Limited pilot / 有限试点 | **Not ready / 尚未就绪** |
| Production or GA / 生产或正式发布 | **Not ready / 尚未就绪** |
| Auto-close or high-risk action / 自动关单或高风险动作 | **Disabled; no approval to enable / 保持禁用，未授权开放** |

The evidence supports one narrow claim: the local/test SOC Alpha journey is repeatable, code-controlled
P0/P1 gaps are closed, and the system has explicit boundaries for the real integration work. It does
not prove provider quality, production capacity, model calibration, operations readiness, or safe
external side effects.

The generated `soc.alpha_readiness_report.v1` remains authoritative for the technical run and correctly
keeps `release_decision=pending_owner_review`, `stage_transition_allowed=false`, and
`production_ready=false`. This review does not rewrite those machine facts or impersonate accountable
owners.

## 2. Evidence Reviewed / 已审证据

| Evidence / 证据 | Result / 结果 | Review use / 用途 |
|---|---|---|
| `backend/.deer-flow/soc-alpha-readiness/alpha-readiness-report.json` | Passed; `alpha_candidate_ready=true` | Technical gate summary and artifact hashes |
| Nested Alpha acceptance | Core, Kafka and frontend passed | APT/EDR/HIDS local/test journey repeatability |
| Backend SOC regression | `558 passed` | SOC code regression baseline |
| Architecture and migration tests | `16 passed` | Dependency and persistence-boundary regression |
| Alpha completeness matrix | Complete 34, Gap 0, Mock 1, Data-gated 6, Deferred 9 | Honest capability boundary and Stage 4 inputs |
| `alpha-readiness-package.md` | Deployment, stop/rollback and handoff documented | Operational handoff review |
| CodeGraph and source cross-check | `SocDecisionPolicy`, `SocReviewService`, `SocMutationUnitOfWork`, `SocKafkaConsumerRunner` located | Fixed decision policy, service mutation, transaction/audit and ingestion ownership |

The technical report records a dirty worktree because unrelated local changes were present. That does
not invalidate the development result, but a shareable release archive must be regenerated from a
clean checkout of the reviewed commit before deployment approval.

## 3. Four-Lens Review / 四方评审意见

### 3.1 Product Owner / 产品负责人视角

**Recommendation: approve the Alpha scope as an engineering-complete, reviewable product loop.**

- The primary analyst journey is coherent: ingest, triage, evidence, review, feedback, candidate
  memory, audit and replay are connected across CLI/Kafka/Gateway/Web/TUI surfaces.
- The product makes deterministic, live-LLM, mock, fixture, shadow-only and data-gated behavior
  distinguishable; this reduces the risk of presenting a demo as production capability.
- The next customer-value risk is no longer another local feature. It is whether real provider data,
  analyst labels and operational ownership improve investigation quality and time-to-decision.
- Do not position the Alpha as an autonomous multi-agent SOC, automatic response platform, or
  production-calibrated detection system.

**Stage 4 product evidence required:** analyst task-completion rate, median review time, correction
rate, evidence-usefulness feedback, and the percentage of investigations blocked by unavailable
providers.

### 3.2 SOC Operations / 安全运营视角

**Recommendation: approve the workflow baseline; do not approve an operational pilot yet.**

- Analysts can inspect the decision and provenance, add notes/corrections, review knowledge
  candidates, and use bounded Lead Agent context without bypassing core services.
- Retryable failures, non-retryable failures, ReviewQueue creation, Kafka offset handling and replay
  have explicit behavior in the acceptance evidence.
- Real Zeus/ITSM feedback, CMDB/EDR/HIDS/TI lookups, authoritative operational facts and enough
  analyst labels are not available. Their absence must remain visible instead of being filled with
  model guesses or additional mocks.
- Before a pilot, name on-call and data owners, rehearse one failure/rollback path, and define who
  handles provider outage, parser drift, queue backlog, DLQ records and disputed model conclusions.

**Stage 4 operating evidence required:** representative alert replay, analyst-reviewed outcomes,
provider degradation drills, backlog/lag thresholds, escalation paths and a documented support rota.

### 3.3 Security / 安全视角

**Recommendation: approve the current control boundaries for Stage 4 integration work only.**

- The Runtime owns control flow; bounded LLM output is schema/domain validated and grounded before
  decision policy runs.
- L3 mutations require trusted authentication plus role checks inside core services. Review,
  approval, memory and external-disposition mutations use transaction/audit boundaries.
- LLM-discovered knowledge remains a candidate until human confirmation. Read-only evidence cannot
  silently change a verdict, and high-risk external actions remain disabled.
- Production secrets, raw sensitive provider payloads, tenant isolation, retention, real identity
  mapping and external action compensation have not been validated in a target environment.

**Stage 4 security evidence required:** threat model, data-flow/redaction review, least-privilege
credentials, secret rotation, tenant and actor mapping tests, audit-retention policy, provider payload
review, and independent approval for every side-effect rollout level.

### 3.4 Platform and Infrastructure / 平台与基础设施视角

**Recommendation: approve reproducible local evidence; do not approve shared deployment yet.**

- One command reproduces acceptance, backend regression, architecture/migration checks and a hashed
  readiness report. Kafka smoke uses a real local broker protocol and exposes its local-only claim.
- PostgreSQL repositories, daemon lifecycle, Kafka runner, migration and deployment templates exist,
  but production topology and failure characteristics are not proven.
- Shared deployment requires real PostgreSQL/Kafka/K8s parameters, ACL/TLS/SASL, backup/restore,
  capacity targets, SLOs, metrics, alerting and recovery exercises.
- Begin with one serial shadow consumer. Do not increase concurrency or enable side effects until lag,
  idempotency, DLQ, shutdown and recovery evidence is available.

**Stage 4 platform evidence required:** clean build provenance, migration rehearsal, backup/restore,
broker failover, load/latency report, resource limits, readiness/liveness behavior and rollback record.

## 4. Risk Triage / 风险分级

This table prioritizes review conditions without becoming a second blocker register.

| Priority | Risk / 风险 | Required control / 控制要求 | Owned work package |
|---|---|---|---|
| Act now | No accountable names for real-system inputs | Assign one named accountable owner for each `PI-01..05` package | `PI-01..05` |
| Act now | Development evidence was generated with unrelated worktree changes present | Regenerate the release archive from a clean checkout of the approved baseline | Alpha Gate sign-off |
| Act now | Real data/provider/security constraints are unknown | Complete environment, data classification, credential and provider intake before implementation | `PI-01`, `PI-02`, `PI-04` |
| Act now | No production-quality label baseline | Define a representative, desensitized, reviewer-audited corpus and evaluation protocol | `PI-03` |
| Track | Analyst value may differ from fixture behavior | Track review time, correction rate, evidence usefulness and manual escalation | `PI-03`, `PI-04` |
| Track | Provider/schema drift can degrade evidence | Track parser/coverage issues, provider failures and stale governed facts | `PI-01`, `PI-04` |
| Track | Scale and model cost are not production-proven | Track throughput, lag, latency, queue depth, model use and unit cost | `PI-02`, `PI-04` |
| Track | Automation pressure may exceed evidence | Preserve shadow/limited-pilot gates and measure overrides before any auto-close/action | `PI-05` |

## 5. Stage 4 Accountability / Stage 4 责任分配

Role boundaries are proposed below so assignment is unambiguous. A role label is not a named owner;
the `Named owner` cells must be completed in the release/change system before the Alpha Gate closes.

| Work package | Accountable role / 责任角色 | Named owner / 具名责任人 | First controlled deliverable / 首个受控交付物 |
|---|---|---|---|
| `PI-01 Real providers` | SOC Integration Owner | **Pending** | Provider inventory plus one approved read-only dev/staging contract and smoke |
| `PI-02 Real infrastructure` | Platform/SRE Owner | **Pending** | Target topology, security parameters, capacity/SLO targets and recovery test plan |
| `PI-03 Real labels and calibration` | SOC Quality/Evaluation Owner | **Pending** | Versioned label policy, reviewer roster and representative seed corpus |
| `PI-04 Operations and security` | SOC Operations Owner, with Security reviewer | **Pending** | Threat model, observability/SLO plan, secrets/audit/retention and incident ownership |
| `PI-05 Governed rollout` | SOC Product/Risk Owner, with response-system owner | **Pending** | Shadow policy, promotion metrics, rollback triggers and independent action approval |

The five packages may be planned after gate approval, but implementation starts only when the package's
external inputs and named owner are available. Local mock expansion does not satisfy a package entry
condition.

## 6. Accountable Sign-Off / 正式签字

The independent review above is complete. The following record is intentionally blank because an AI
assistant cannot invent reviewer identity, authority, timestamp, or change-ticket approval.

| Required reviewer | Named reviewer | Decision | Timestamp | Reason / change record |
|---|---|---|---|---|
| Product owner | **Pending** | `pending` | - | - |
| SOC operations | **Pending** | `pending` | - | - |
| Security | **Pending** | `pending` | - | - |
| Platform/infrastructure | **Pending** | `pending` | - | - |

Allowed decisions are `approve` or `changes_requested`. Approval means all four reviewers accept the
scope and claim boundaries, `PI-01..05` have named accountable owners, and a clean-checkout release
archive is required before any shared deployment. A changes-requested decision must reference a
numbered roadmap/matrix item rather than silently creating an untracked implementation stream.

Until this table is completed in the authoritative release/change record, `BG-03` remains in progress,
Stage 4 remains non-current, and `stage_transition_allowed` remains false.
