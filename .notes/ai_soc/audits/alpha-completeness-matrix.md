# SOC Alpha Completeness Matrix and Blocker Register / 完整性矩阵与阻塞台账

Status: AUD-03 baseline frozen; Stage 3 execution status current

Updated: 2026-07-18

Inputs:

- `alpha-journey-inventory.md` for the executable as-is journey.
- `alpha-consistency-audit.md` for `CONS-01..24` factual differences.
- `soc-agent-solution.md`, `alert-lifecycle-flow.md`, engineering contracts and mock/real register
  for intended boundaries.

This is the only SOC Alpha completeness matrix. It replaces chat-based task selection. It does not
replace the product solution or the historical progress ledger.

## 1. Classification Contract / 分类契约

| State | Exact meaning in this matrix |
|---|---|
| `Complete` | Executable local/test implementation exists and has enough evidence for the Alpha boundary stated in the row |
| `Gap` | Code-controlled behavior required for SOC Alpha is missing, unsafe, inconsistent, or not application-reachable |
| `Mock` | Contract and flow exist, but the external fact/result comes from a local fixture or fake provider |
| `Data-gated` | Completion requires external credentials, authoritative data, production infrastructure, or sufficient human labels |
| `Deferred` | Deliberately excluded from SOC Alpha; absence must not block Stage 3 |

Priority applies only to current work:

| Priority | Meaning |
|---|---|
| `P0` | Alpha correctness/security blocker; Stage 3 addresses it first |
| `P1` | Alpha journey, reproducibility, or contract blocker; Stage 3 addresses it after P0 |
| `P2` | Production integration or later product work; not admitted into Stage 3 |
| `-` | Already complete for the stated Alpha boundary |

`Complete` never means production-ready. A row can be complete for local Alpha while a separate
provider/infrastructure row remains `Mock` or `Data-gated`.

## 2. Unique Completeness Matrix / 唯一完整性矩阵

### 2.1 Ingress, API, and Kafka

| ID | Capability / 能力 | State | Priority | Current truth / 当前事实 | Target |
|---|---|---|---|---|---|
| `AC-01` | Vendor normalization and bounded evidence | Complete | - | Generic/PingAn adapters, raw preservation, provenance, coverage, drift and grounding are executable | Maintain |
| `AC-02` | CLI analyze/show/replay/correct | Complete | - | Thin command entry reaches core services and persistent repository | Maintain |
| `AC-03` | Kafka alert and approval-request consumption | Complete | - | Consumer, mapper, worker result, commit/DLQ, finite and long-running runners are executable | Maintain |
| `AC-04` | Versioned Kafka alert input envelope | Complete | - | `SocAlertRawEnvelope` strictly validates `soc.alert.raw.v1`, required source/event metadata, bounded raw/hints and reserved metadata before controlled unwrapping; APT/EDR/HIDS and real broker commit/DLQ smoke pass | Maintain |
| `AC-05` | Kafka result/review/event output topics | Deferred | P2 | Documented topic names have no producer; DB/API are current result channels | Parking Lot |
| `AC-06` | Kafka worker pool, partition-aware concurrency and backpressure | Deferred | P2 | Current runner is intentionally serial; throughput/capacity work belongs to production integration | PI |
| `AC-07` | External disposition canonical service and SQL persistence | Complete | - | Mapping, idempotency, target resolution, correction/candidate/outcome bridge and durable table exist | Maintain |
| `AC-08` | Generic external disposition application ingress | Complete | - | Authenticated `POST /api/soc/external-dispositions` accepts versioned canonical ingress commands and reaches the existing transactional service; admin/adapter RBAC, source-event idempotency, changed-retry conflict and failure mapping are tested | Maintain; real feeds remain `AC-09` |
| `AC-09` | Real Zeus/ITSM/SOAR disposition feed | Data-gated | P2 | Requires endpoint/topic, auth/signature, tenant mapping, replay and approved data | PI |
| `AC-10` | Gateway alert analyze/run/replay API | Deferred | P2 | CLI and Kafka are Alpha ingestion paths; no current product requirement forces a Web analyze form | Parking Lot |
| `AC-11` | Versioned SOC API transport and error/header contract | Gap | P1 | Existing `/api/soc/...` routes work, but differ from the declared v1 envelope/error/request contract | BG |

### 2.2 Runtime, Decision, and Persistence

| ID | Capability / 能力 | State | Priority | Current truth / 当前事实 | Target |
|---|---|---|---|---|---|
| `AC-12` | Fixed nine-step Runtime plus bounded LLM | Complete | - | Deterministic control flow, explicit live-model mode, parser/schema/domain/grounding/policy guards are executable | Maintain |
| `AC-13` | Durable pre-LLM run/request journal | Gap | P1 | Requested event is process-local and final run is persisted only after Runtime/model returns | BG |
| `AC-14` | Step trace and replay lineage | Complete | - | Nested trace has hashes/timing/status/error/metadata; replay creates a new run linked by `replay_of_run_id` | Maintain; docs reconcile in `AC-49` |
| `AC-15` | Atomic primary analysis bundle | Complete | - | run/summary/optional queue/audit commit or roll back together | Maintain |
| `AC-16` | Atomic correction and external-feedback mutation | Complete | - | Explicit `SocMutationUnitOfWork` wraps correction and full external-disposition commands; write-by-write fault injection proves state and buffered events roll back together, while exact retry returns one logical result | Maintain |
| `AC-17` | Human/external correction confidence provenance | Gap | P1 | Corrected decisions default to `confidence_source=unknown`; external correction injects undocumented `0.95` | BG |
| `AC-18` | Normalization maintenance loop | Complete | - | Baseline, issue dedupe/reopen, CLI/API/Web/TUI and fail-open analysis side path exist | Maintain |
| `AC-19` | Production confidence calibration | Data-gated | P2 | Offline governance and a small seed set exist; production thresholds require sufficient approved labels | PI |

### 2.3 Review, Security, and Acceptance

| ID | Capability / 能力 | State | Priority | Current truth / 当前事实 | Target |
|---|---|---|---|---|---|
| `AC-20` | ReviewQueue, InvestigationContext, Web and TUI | Complete | - | Queue/context/close/correct/outcome plus unified evidence timeline are application-reachable | Maintain |
| `AC-21` | Durable audit for Alpha state mutations | Complete | - | Migration `0018` adds append-only `soc_mutation_audit_log`; review, memory, approval and external-disposition mutations persist actor/provenance, reason, idempotency, command hash and bounded result metadata without raw action payloads or secrets | Maintain |
| `AC-22` | L3 service authorization and actor provenance | Complete | - | `ActorContext.auth_source` records the trust boundary; shared core role checks protect review, memory, normalization, governed-context and approval mutations independently of the entry surface | Maintain; durable mutation audit is complete under `AC-21` |
| `AC-23` | SOC frontend automated regression | Gap | P1 | Browser rehearsal exists, but no focused SOC frontend unit/component test suite was found | BG |
| `AC-24` | APT/EDR/HIDS Alpha end-to-end acceptance package | Gap | P1 | Separate tests/demo/validation exist; one versioned acceptance report does not yet cover CLI+Kafka+DB+UI+feedback+audit+replay together | BG |

### 2.4 Investigation, Agent, and Tools

| ID | Capability / 能力 | State | Priority | Current truth / 当前事实 | Target |
|---|---|---|---|---|---|
| `AC-25` | Similar-alert correlation and evaluation | Complete | - | Deterministic read-only retrieval, evidence lineage and same/related/unrelated evaluation exist | Maintain |
| `AC-26` | Domain/scenario findings | Complete | - | APT/EDR/HIDS/generic open-set findings, gaps, conclusion and checklist are derived in ReviewContext | Maintain |
| `AC-27` | Main orchestrator eval/demo service | Complete | - | `SocMainOrchestratorService` produces a bounded unified report in PingAn eval/tests | Maintain as eval/demo |
| `AC-28` | Live Main Orchestrator application wiring | Deferred | P2 | Live product already composes ReviewContext without this service; wiring it now could create a second orchestration path | Parking Lot |
| `AC-29` | DeerFlow SOC Lead Agent TUI path | Complete | - | Installed `soc-triage` profile, bounded review artifact, stream and proposal boundary are executable | Maintain |
| `AC-30` | Specialized APT/EDR/HIDS/Hunting Sub Agents | Deferred | P2 | Current domain handlers/skills are sufficient for Alpha; autonomous sub-agent delegation is later work | Parking Lot |
| `AC-31` | Read-only action/evidence boundary | Complete | - | Policy, dispatcher, registry, adapter/MCP and `InvestigationEvidence` return path exist | Maintain |
| `AC-32` | Local CMDB/EDR/HIDS/TI/security-tag providers | Mock | P2 | Results are explicitly local/in-memory/stdio fixtures and cannot satisfy production fact requirements | PI |
| `AC-33` | Real investigation providers | Data-gated | P2 | Requires real endpoints, credentials, payload contracts, redaction and smoke evidence | PI |

### 2.5 Approval and Side Effects

| ID | Capability / 能力 | State | Priority | Current truth / 当前事实 | Target |
|---|---|---|---|---|---|
| `AC-34` | Approval request resolution and grant integrity | Complete | - | Request lifecycle is `pending -> approved/rejected/expired`; approve accepts only a request ID, atomically resolves the persisted pending row and creates at most one grant with exact-retry idempotency | Maintain; durable mutation audit is complete under `AC-21` |
| `AC-35` | Approval dry-run/execute no-side-effect boundary | Complete | - | Role gate, token validation, adapter preflight, single-token consume and idempotent result replay exist | Maintain |
| `AC-36` | Real EDR/F5/SOAR/firewall response execution | Data-gated | P2 | Requires staging adapters, rollback/compensation, execution verification and independent rollout approval | PI |

### 2.6 Memory and Governed Context

| ID | Capability / 能力 | State | Priority | Current truth / 当前事实 | Target |
|---|---|---|---|---|---|
| `AC-37` | Memory candidate/review/record lifecycle | Complete | - | Candidate-first, human confirmation, deprecate/expire and SQL/API/CLI/Web boundaries exist | Maintain |
| `AC-38` | Bounded confirmed-memory retrieval | Complete | - | Facet/text scoring, token budget, reasons and retrieval-disabled filtering are executable | Maintain |
| `AC-39` | Governed retrieval-enable activation | Gap | P1 | No service/CLI/API policy transition can enable a confirmed record; demo writes the flag directly as a fixture | BG |
| `AC-40` | Correction and review-note candidate sources | Complete | - | Both create pending candidates through `SocMemoryService` with provenance and idempotency | Maintain |
| `AC-41` | Domain/Lead Agent/Kafka lesson capture workflow | Deferred | P2 | Domain bridge exists but is explicit; automatic capture from agent/daemon would need a product review step and noise policy | Parking Lot |
| `AC-42` | Governed fact lifecycle and authorized-activity matcher | Complete | - | Append-only lifecycle, RBAC, event-time historical matching and read-only explanations exist | Maintain |
| `AC-43` | Authorization enrichment, shadow proposal and evaluation | Complete | - | EX/DP/EV records, Web/TUI/API projections and no-auto-close gates are executable | Maintain |
| `AC-44` | Authoritative change/scanner/maintenance fact synchronization | Data-gated | P2 | Current facts are controlled fixtures; real source/version/freshness feed is unavailable | PI |
| `AC-45` | Security-exercise campaign and participant attribution | Deferred | P2 | Typed design exists; current Alpha only implements authorized activity and must not infer identity from an IP | Parking Lot |

### 2.7 Operations, Production Evidence, and Documentation

| ID | Capability / 能力 | State | Priority | Current truth / 当前事实 | Target |
|---|---|---|---|---|---|
| `AC-46` | Durable generic `SocEvent` stream and SSE | Deferred | P2 | State tables/audit cover selected paths; generic event sink remains process-local/no-op | PI |
| `AC-47` | Prometheus, SLO and operations overview | Deferred | P2 | JSONL Kafka metrics, status scripts and normalization metrics are partial signals only | PI |
| `AC-48` | Production PostgreSQL/Kafka/K8s capacity and recovery evidence | Data-gated | P2 | Deployment/config code exists; real parameters, ACLs, load and failure exercises do not | PI |
| `AC-49` | Authoritative docs, commands and mock register reconciliation | Gap | P1 | Solution/lifecycle/contracts/register contain stale service maps, states, commands, phases and “current next step” | BG |
| `AC-50` | Repeatable Boss Demo and mock disclosure | Complete | - | Resettable isolated DB, browser path, manifest, screenshots, feedback proof and disclosure are reproducible | Maintain |

### Matrix totals

| State | Count |
|---|---:|
| Complete | 27 |
| Gap | 7 |
| Mock | 1 |
| Data-gated | 6 |
| Deferred | 9 |
| **Total** | **50** |

The AUD-03 baseline admitted 13 `Gap` rows into Stage 3. Six are now closed, leaving 7 current
`Gap` rows. `Mock`, `Data-gated`, and `Deferred` rows remain visible but do not silently become
blockers.

## 3. Gap Register / 阻塞台账

### 3.1 P0 - correctness and security blockers

All frozen P0 gaps are closed. The implementation evidence is retained in Section 3.2.

### 3.2 Closed Stage 3 gaps / 已关闭缺口

| Gap | Closed by | Executable evidence | Remaining boundary |
|---|---|---|---|
| `AC-22` L3 authorization and actor provenance | `BG-P0-01`, 2026-07-18 | Shared `require_actor_roles()` rejects anonymous/unknown provenance and enforces command-specific roles; Gateway derives actor/role/auth source from authenticated state; CLI/TUI/daemon use explicit local provenance; allow/deny tests pass | Durable mutation coverage was subsequently closed by `AC-21/BG-P0-02` |
| `AC-34` Approval request/grant integrity | `BG-P0-01`, 2026-07-18 | Migration `0017`; insert-only request creation; repository CAS/row lock; one grant/request unique constraint; request-ID-only API/Web/TUI approve; reject/expire and exact retry tests | Real side-effect adapters remain data-gated under `AC-36`; audit coverage was closed by `AC-21/BG-P0-02` |
| `AC-16` Atomic correction/external mutation | `BG-P0-02`, 2026-07-18 | `SocMutationUnitOfWork` and SQLAlchemy transaction proxy; correction/external commands buffer events until commit; fault injection after every write proves run/summary/queue/candidate/decision audit/mutation audit/external record and event emission roll back together; exact retry produces one logical result | Provider calls and external side effects remain outside this database transaction and need their own compensation boundary |
| `AC-21` Durable state-mutation audit | `BG-P0-02`, 2026-07-18 | Migration `0018`; immutable `SocMutationAuditRecord`; close/note/correction, memory review, approval submit/approve/reject/expire/dry-run/execute and external disposition covered; API/TUI actor provenance and secret-redaction tests pass | Generic process event streaming remains deferred under `AC-46`; decision lineage remains in `soc_decision_audit_log` |
| `AC-04` Versioned Kafka envelope | `BG-P1-01`, 2026-07-18 | Strict `SocAlertRawEnvelope`; 900,000-byte raw and 64,000-byte hint limits; no raw values in validation errors; exact preservation of three representative source samples; bad version/malformed/reserved collision tests; Redpanda smoke proves processed+commit, DLQ+commit and post-commit idle | Real topic ACL/capacity/recovery evidence remains data-gated under `AC-48` |
| `AC-08` Generic external disposition ingress | `BG-P1-01`, 2026-07-18 | Versioned `SocExternalDispositionIngressCommand`; authenticated Gateway route; service-level `soc_admin`/adapter RBAC; source event ID required; duplicate returns one record and changed retry conflicts; mapped/unmatched/failure coverage reuses the same service | Real Zeus/ITSM/SOAR auth/signature/feed remains data-gated under `AC-09` |

### 3.3 P1 - Alpha journey and reproducibility blockers

| Gap | Owner boundary | Impact | Source evidence | Acceptance / 验收 | Target |
|---|---|---|---|---|---|
| `AC-11` SOC API v1 transport contract | Gateway SOC routers + frontend client | New SOC APIs will continue diverging in paths, envelopes, errors and request metadata | `CONS-02` | One reviewed v1 convention is implemented or the engineering contract is explicitly replaced; OpenAPI snapshot covers SOC paths/errors/headers; frontend uses the chosen contract; compatibility strategy is tested | `BG-02` |
| `AC-13` Durable pre-LLM run journal | Analysis service + persistence | Process loss during a model call leaves no durable requested/running record for recovery or cost investigation | `CONS-15` | Before provider invocation persist bounded request metadata and running state without raw prompt/secret; success/failure finalization is recoverable; crash/timeout test leaves a discoverable interrupted/running record and replay path | `BG-02` |
| `AC-17` Correction confidence provenance | Review/external disposition service + decision contract | Human/external decisions display an unexplained score and lose provenance required for audit/eval | `CONS-16` | Human correction writes `human_confirmation`; trusted external correction writes `external_disposition`; fixed confidence is removed or policy-versioned/explained; summary/audit/API tests preserve provenance and no false calibration | `BG-02` |
| `AC-23` SOC frontend regression | Frontend SOC components/API client | Browser-only rehearsal will not reliably catch state/action/render regressions | AUD-01 Web evidence; no SOC-named frontend tests | Focused tests cover queue/context render, close/correct, approval integrity flow, memory review, disposition outcome/sample and normalization actions; `pnpm test`/`pnpm check` pass | `BG-02` |
| `AC-24` Alpha E2E acceptance package | SOC QA/demo orchestration | Separate proofs do not show one versioned release satisfies the whole Alpha journey | Delivery roadmap BG-02; AUD-01 user-visible artifacts | A single reproducible command/report runs representative APT/EDR/HIDS through CLI and Kafka, DB, Review UI/API, feedback, audit and replay; report records mock/data-gated disclosures and failure semantics | `BG-02` |
| `AC-39` Governed memory activation | Memory service + policy + API/CLI/Web | Confirmed memory cannot become legitimately retrievable outside a demo repository write | `CONS-17`; memory service/router/demo | Versioned enable/disable command requires role, reason, validity/review metadata and audit; all surfaces use service; retrieval diff/replay proves only enabled confirmed records enter bounded context | `BG-02` |
| `AC-49` Docs/command/register reconciliation | SOC docs owners | Reviewers and operators can follow invalid commands or mistake service-only/old phase statements for current capability | `CONS-07,09,10,19..24` | Update solution, lifecycle, engineering contracts, mock register, AGENTS orientation and command examples in the same code slices; every current claim links to executable evidence; no parallel roadmap is created | `BG-03` |

## 4. Data-Gated Register / 外部条件台账

These rows cannot be “fixed” by adding more local mocks.

| Capability | Required external input | Ready evidence before status changes | Target |
|---|---|---|---|
| `AC-09` Real external disposition feed | Zeus/ITSM/SOAR endpoint/topic, auth/signature, tenant mapping and approved payload | dev/staging contract + replay/security smoke | PI-01 |
| `AC-19` Production confidence calibration | Sufficient desensitized analyst labels across positive/negative classes and stable model/prompt scope | validated label set, calibration report, reviewer sign-off | PI-03 |
| `AC-33` Real investigation providers | CMDB/EDR/HIDS/TI/security-tag endpoints and credentials | timeout/permission/redaction/empty/success smoke per provider | PI-01 |
| `AC-36` Real response execution | Staging EDR/F5/SOAR/firewall plus compensation and owner approval | dry-run, execute, verify, rollback and failure exercise | PI-05 |
| `AC-44` Authoritative fact sync | Change/scanner/maintenance/CMDB sources with version/freshness | source adapter replay and stale/revoked fact tests | PI-01/03 |
| `AC-48` Production infrastructure evidence | PostgreSQL/Kafka/K8s parameters, ACLs, capacity targets and recovery environment | load, lag, failure, backup/restore and rollout report | PI-02/04 |

## 5. Deferred Register / 明确后置

| Capability | Why it does not block Alpha | Re-entry condition |
|---|---|---|
| `AC-05` Kafka result topics | DB/API are the current result channel | A downstream consumer requires broker results |
| `AC-06` Worker pool/backpressure | 10k alerts/day and current Alpha can validate serial semantics first | PI capacity model shows serial path misses SLO |
| `AC-10` Gateway analyze/replay API | CLI/Kafka cover Alpha ingestion and replay | Product requires browser/API alert submission |
| `AC-28` Live Main Orchestrator wiring | ReviewContext already composes live investigation; wiring could duplicate orchestration | One explicit product journey requires the unified report service online |
| `AC-30` Specialized Sub Agents | Domain handlers/skills and Lead Agent cover Alpha | Domain eval and tool contracts stabilize; parallel delegation has a measurable benefit |
| `AC-41` Automatic domain/Lead/Kafka lesson capture | Automatic capture can create noise; analyst note/correction already closes the learning loop | Candidate quality/volume policy and review owner are defined |
| `AC-45` Exercise campaign/participant facts | Authorization alone is safe; identity attribution requires separate governed data | Real exercise roster/campaign source and privacy/RBAC review exist |
| `AC-46` Generic event stream/SSE | Required mutations will receive durable audit first | Product needs live run progress or cross-service event consumers |
| `AC-47` Prometheus/SLO overview | Partial local signals are enough for Alpha | PI deployment and SLO/capacity targets are available |

## 6. Frozen Stage 3 Work Packages / Stage 3 冻结任务

Stage 3 may implement only these packages unless the user explicitly changes the roadmap.

| Package | Included rows | Deliverable | Exit proof |
|---|---|---|---|
| `BG-P0-01` Approval integrity and L3 authorization | `AC-22`, `AC-34` | **Done 2026-07-18**: persisted request state machine, request-id grant command, one-resolution/idempotency, core RBAC and actor auth provenance | 509 SOC tests + 16 frontend API tests; forged/stale/repeated/unauthorized and Web/TUI contracts covered |
| `BG-P0-02` Transactional mutation and durable audit | `AC-16`, `AC-21` | **Done 2026-07-18**: explicit command unit-of-work, commit-buffered events and append-only secret-safe mutation audit | 516 SOC tests + 10 architecture + 6 migration-environment tests; fault-injection rollback matrix plus API/TUI audit coverage |
| `BG-P1-01` Versioned ingestion and feedback | `AC-04`, `AC-08` | **Done 2026-07-18**: strict bounded Kafka alert envelope plus authenticated canonical external-disposition Gateway ingress | 532 SOC + 16 architecture/migration tests; Redpanda processed/commit, DLQ/commit and post-commit idle; application duplicate/conflict/RBAC/failure tests |
| `BG-P1-02` API contract stabilization | `AC-11` | Versioned/explicit SOC API convention, error/request metadata and OpenAPI regression | Gateway + frontend contract tests |
| `BG-P1-03` Runtime recovery and decision provenance | `AC-13`, `AC-17` | Durable pre-call journal and correct human/external confidence source | crash/timeout recovery and correction/external replay tests |
| `BG-P1-04` Governed memory activation | `AC-39` | Role/reason/audit/version controlled retrieval enable/disable service and surfaces | retrieval replay diff and authorization tests |
| `BG-P1-05` Alpha E2E and docs reconciliation | `AC-23`, `AC-24`, `AC-49` | Frontend regression, one release-level APT/EDR/HIDS acceptance report, synchronized authoritative docs | full backend/architecture/frontend checks and versioned acceptance artifact |

Execution order is fixed:

```text
BG-P0-01
  -> BG-P0-02
  -> BG-P1-01
  -> BG-P1-02
  -> BG-P1-03
  -> BG-P1-04
  -> BG-P1-05
  -> BG-03 Alpha readiness package
```

Each package remains a reviewable slice and must not absorb P2/Data-gated work.

## 7. AA Gate Result / 审计阶段门禁

- [x] One unique 50-row completeness matrix exists.
- [x] P0/P1 blockers are separated from P2 quality/production work.
- [x] Mock, data/credential-gated, code gap and deferred work are distinct.
- [x] Every Gap has owner boundary, impact, evidence, acceptance and target stage.
- [x] Stage 3 input is frozen into seven ordered packages.
- [x] No business code was modified during AUD-03.

**AA Gate: Passed on 2026-07-18.**

The next implementation slice is `BG-P1-02 API contract stabilization`.
