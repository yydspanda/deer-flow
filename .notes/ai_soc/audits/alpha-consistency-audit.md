# SOC Alpha Code, Contract, and Docs Consistency Audit / 一致性审计

Status: AUD-02 complete

Updated: 2026-07-18

Scope: current repository code versus the authoritative solution, lifecycle, engineering contracts,
and mock/real register. This document records facts only. It does not fix code, rewrite the audited
documents, or assign P0/P1/P2 priority; those decisions belong to `AUD-03`.

Post-audit resolution tracking (the rows below retain the original AUD-02 evidence):

| Difference | Current status | Stage 3 evidence |
|---|---|---|
| `CONS-12` approval request/grant mismatch | **Resolved by `BG-P0-01`** | Request terminal lifecycle, request-ID-only approve, atomic request+grant persistence, unique one-grant constraint and exact retry semantics |
| `CONS-18` L3 authorization/provenance mismatch | **Resolved by `BG-P0-01`** | `ActorContext.auth_source`, shared core role gate, authenticated Gateway role mapping and explicit CLI/TUI/daemon provenance |
| `CONS-13` durable mutation audit | Open as `AC-21` | Current `BG-P0-02` |
| `CONS-14` correction/external atomicity | Open as `AC-16` | Current `BG-P0-02` |

## 1. Audit Question / 审计问题

本次审计回答四个问题：

1. 文档中描述为“当前已接通”的能力，代码是否真的有 application entry 和可达调用链？
2. 代码已经实现的 contract、状态、持久化和安全边界，文档是否仍按旧设计描述？
3. mock、fixture、shadow-only、service-only 和 real implementation 是否被准确区分？
4. 工程契约中的目标设计，是否被误读成当前实现事实？

Audited sources:

| Source | Role in this audit |
|---|---|
| `audits/alpha-journey-inventory.md` | AUD-01 as-is entry/service/state/table baseline |
| `soc-agent-solution.md` | Authoritative product and architecture intent |
| `alert-lifecycle-flow.md` | Claimed current end-to-end behavior |
| `.notes/reference-index/soc-agent-engineering-contracts.md` | Required engineering contracts and target protocols |
| `integrations/mock-and-real-register.md` | Mock/real/shadow/data-source disclosure |
| `backend/soc_agent/**`, Gateway routers, frontend SOC workbench, tests | Executable implementation evidence |

Method:

- CodeGraph index was current at 1,614 files, 33,184 nodes and 77,881 edges.
- CodeGraph was used for stable symbol location and broad relationship checks.
- Direct `rg` and source reads were used for dynamic CLI/Gateway wiring that static caller queries do
  not enumerate reliably.
- Absence claims were checked across application code, not inferred from one file.
- No reference project or Understand Anything query was needed because this is a local consistency
  audit, not a new architecture decision.

## 2. Classification Used Here / 本文分类

These are discrepancy descriptions, not the final AUD-03 completeness states.

| Audit marker | Meaning |
|---|---|
| `Aligned` | Documented behavior and current code agree within the stated boundary |
| `Current claim exceeds wiring` | A document presents a target/service as an application-ready current path |
| `Contract ahead` | Engineering contract is a valid target but current code does not yet implement it |
| `Code ahead / doc stale` | Code has advanced beyond an older phase, diagram, command, or register statement |
| `Semantic mismatch` | Both sides exist but state, security, audit, or data semantics differ |
| `Boundary ambiguous` | The document does not distinguish demo/eval/service-only behavior from the live product path |
| `Stale operator guidance` | A documented command or “current next step” no longer matches the executable interface/roadmap |

## 3. Verified Alignment / 已确认一致部分

The audit found the following important boundaries aligned. Later discrepancy rows narrow specific
exceptions; they do not invalidate these base facts.

| ID | Area | Verified fact | Evidence |
|---|---|---|---|
| `ALIGN-01` | Fixed Runtime | The base Runtime has nine ordered steps; the LLM cannot add, skip, or reorder control-flow nodes | `backend/soc_agent/core/runtime.py`; solution 5.3; lifecycle 2 |
| `ALIGN-02` | Evidence boundary | PingAn aliases stay in the normalizer; raw payload is retained; bounded evidence, grounding, repair labeling, drift, and coverage are explicit | `normalizers/pingan_platform.py`, `pipeline/evidence.py`, `pipeline/evidence_grounding.py` |
| `ALIGN-03` | Primary analysis transaction | Persisted analysis writes run, summary, optional review item, and decision audit through one `AnalysisPersistence.save_analysis_bundle()` transaction | `SocAnalysisService._analyze()` and `SqlAlchemyAlertRepository.save_analysis_bundle()` |
| `ALIGN-04` | Review context | Correlation, domain findings, evidence, external feedback, governed enrichment, outcomes, and memory are derived into one read model rather than copied into parallel sources of truth | `SocReviewService.get_investigation_context()` |
| `ALIGN-05` | Correlation safety | Correlation is deterministic and read-only; its current score cannot suppress, merge, close, or change a Runtime decision | `SocCorrelationService`, `soc eval correlation` |
| `ALIGN-06` | Governed context | GF-01, AA-01, EX-01, DP-01 and EV-01..EV-03 contracts/services/tables are real implementations; proposal and evaluation remain shadow-only and cannot auto-close | migrations `0013..0016`, governed/disposition services and tests |
| `ALIGN-07` | Live model path | Explicit `llm` mode reuses DeerFlow `create_chat_model`; deterministic stub remains the replay/test default; both pass parser/schema/domain/grounding/policy boundaries | `backend/soc_agent/llm/`, `core/runtime.py` |
| `ALIGN-08` | External facts | CMDB/EDR/HIDS/TI/security-tag defaults are clearly mock/local read-only providers, not claimed as real PingAn production integrations | `actions/adapters.py`, `actions/mcp.py`, mock/real register rows 20-25 |
| `ALIGN-09` | High-risk effects | Approval request, grant, dry-run, adapter preflight and one-time token consumption are implemented; current execute result explicitly records `external_side_effect=not_executed` | `SocAgentApprovalService.execute_approved_action()` |
| `ALIGN-10` | Storage posture | SQL repository and migrations are real; PostgreSQL remains the staging/production target and SQLite is explicitly limited to local development/demo | `backend/soc_agent/db/`, solution 9, mock/real register 2.1 |

## 4. Factual Difference Register / 事实差异台账

### 4.1 Entry and Transport / 入口与传输

| ID | Marker | Documented claim | Current code fact | Evidence and AUD-03 question |
|---|---|---|---|---|
| `CONS-01` | Current claim exceeds wiring | Solution and lifecycle list `API` beside CLI/Kafka/demo as an alert-analysis entry, and lifecycle calls the overview the “current actual closed loop” | Gateway has review, approval, memory and normalization routers, but no analyze, run-get/steps, replay, governed-context, proposal, or external-disposition ingress | `alert-lifecycle-flow.md:33-35,88`; `soc-agent-solution.md:214`; `audits/alpha-journey-inventory.md:150-163`. AUD-03 must decide which missing API entries block Alpha and which diagrams should be labeled target-only |
| `CONS-02` | Semantic mismatch | Engineering contract requires `/api/soc/v1`, `{data,meta}` success envelopes, structured error envelopes, `Idempotency-Key`, `X-Request-Id`, and `X-Actor` on writes | Current SOC routes are unversioned `/api/soc/...`, return Pydantic models directly, and use FastAPI `detail` errors. Actor comes from authenticated Gateway user or `X-SOC-Actor-Id`; `X-Request-Id` is not mapped; idempotency is required only by selected writes | Engineering contracts `1248-1305`; `routers/soc_dependencies.py:35-61`; SOC routers. AUD-03 must choose contract migration versus API migration and preserve the stronger authenticated-user actor behavior |
| `CONS-03` | Current claim exceeds wiring | Product/lifecycle diagrams show Zeus/ITSM/SOAR -> adapter -> `SocExternalDispositionService` as a current external-system entry | Service, mapper contract, SQL repository, fixture, correction/candidate/outcome bridges and tests exist, but no Gateway webhook, Kafka kind/topic, polling job, CLI import, or other application adapter calls `apply_event()` | Solution `135-142,181,689-708`; lifecycle `78-85,503-543`; `core/external_disposition.py`; application-wide caller search. Current marker is `service-only`, not an integrated external feed |
| `CONS-04` | Contract ahead | Kafka contract requires a versioned `soc.alert.raw.v1` envelope containing `source`, `alert_id`, `occurred_at`, `raw`, and related fields | `map_kafka_record_to_daemon_message()` accepts any JSON object and passes it directly as alert payload; it does not validate or unwrap the documented envelope | Engineering contracts `1353-1376`; `daemon/kafka_mapper.py:35-52`; mapper/runner tests use direct `{"alert_id":"ALT-1"}` payloads |
| `CONS-05` | Contract ahead | Engineering contract names `soc.analysis.results.v1`, `soc.analysis.review_required.v1`, and `soc.analysis.events.v1` output topics | No application producer emits those topics. Current broker output is the dead-letter topic; operational counters are JSONL process metrics | Engineering contracts `1378-1394`; `kafka_adapter.py`; `kafka_daemon.py`. AUD-03 must decide whether Alpha requires output topics or whether DB/API read models are the intended result channel |
| `CONS-06` | Semantic mismatch | Kafka contract says callback work is queued and Runtime workers process it; solution assigns background orchestration to `SocDaemonService` | Current consumer is deliberately serial and calls `SocKafkaWorker.process_record()` synchronously. A long-running `SocKafkaDaemonRunner` exists, while `SocDaemonService.start()` and its docstring still say daemon mode is a Phase 4 placeholder | Engineering contracts `1386-1394`; `kafka_runner.py:66-135`; `kafka_daemon.py:91-223`; `core/service.py:1423-1438`. The per-record contract is real; worker-pool/backpressure semantics are not |

### 4.2 Agent and Orchestration Boundaries / Agent 与编排边界

| ID | Marker | Documented claim | Current code fact | Evidence and AUD-03 question |
|---|---|---|---|---|
| `CONS-07` | Code ahead / doc stale | Solution topology routes DeerFlow Lead Agent through `SocAgentChatService`, and its service table has no distinct Lead Agent stream service | `soc chat tui --lead-agent` uses `SocLeadAgentChatService`; deterministic chat uses `SocAgentChatService`. The latter still emits text saying a future Lead Agent will be attached | Solution `155-163,181-185,267-275`; `lead_agent_chat.py:48-130`; `cli.py:2333-2345`; `core/service.py:1792-1798,2069-2075` |
| `CONS-08` | Boundary ambiguous | Solution/lifecycle describe `SocMainOrchestratorService` as the composition of analysis, actions, correlation, domain triage, and unified report | The class is instantiated only by PingAn eval and tests. Live ReviewContext independently derives correlation/domain/view after a persisted run; no CLI/Kafka/Web/Lead Agent production path calls the main orchestrator | `core/orchestrator.py`; `eval/pingan.py:399`; application caller search; `SocReviewService.get_investigation_context()`. The outputs are available, but the named orchestrator is demo/eval-only |

### 4.3 State, Persistence, Audit, and Consistency / 状态、持久化、审计与一致性

| ID | Marker | Documented claim | Current code fact | Evidence and AUD-03 question |
|---|---|---|---|---|
| `CONS-09` | Code ahead / doc stale | Lifecycle persistence map/table is presented as the current SOC write map | It omits `soc_governed_context_facts` and `soc_authorization_enrichments`, although both are implemented and used. AUD-01 identifies 17 business tables plus `soc_alembic_version` | Lifecycle `222-269`; AUD-01 `402-430`; migrations `0013`, `0014` |
| `CONS-10` | Semantic mismatch | Lifecycle state diagram shows an existing `success/needs_review/failed` run transitioning to `replayed` | Replay loads the old input and creates a new normal run with `replay_of_run_id`; neither old nor new run is assigned status `replayed`. `pending/interrupted/rolled_back/replayed` remain reserved enum values in this runner | Lifecycle `659-670`; `SocAnalysisService.replay():194-209`; `AnalysisRunStatus`; Runtime terminal assignments |
| `CONS-11` | Semantic mismatch | Engineering trace contract requires each step to carry run/alert IDs, separate error code/message, retry count, and LLM model/token fields | `PipelineStepTrace` is nested under its run and carries step name, status, hashes, timing, one `error`, warnings and metadata. The fixed runner currently produces only running -> success/failed | Engineering contracts `1190-1215`; `contracts/schemas.py:2713-2723`; `core/runtime.py:320-355`. The current trace is useful, but it is not the exact documented transport contract |
| `CONS-12` | Semantic mismatch | Approval diagrams imply a pending inbox request is approved into a one-time grant and becomes resolved work | `SocAgentApprovalRequest.status` remains the literal `pending` after grant creation. `approve()` does not load or mark the persisted request; Gateway `/grants` accepts a complete client-supplied request object. Repeated grants for one request are not prevented by request state or a unique request-to-grant constraint | Lifecycle `478-501,704-714`; `SocAgentApprovalService:1549-1612`; `routers/soc_approvals.py:17-20,82-94`; DB models. One grant token is single-use, but the request lifecycle is not single-resolution |
| `CONS-13` | Current claim exceeds wiring | Solution step 11 sends correction/close/note to “Audit + State Update”; approval sequence records request/grant/result audit; engineering principle says run, step, tool, permission, and memory activity is traceable | Durable decision audit currently covers analysis, replay, correction, and external disposition. Review close/note and approval transitions do not write `DecisionAuditRecord`; `SocEvent` defaults to `NoopEventSink` and has no table. Approval rows and evidence rows provide partial durable history, but there is no unified event/audit trail | Solution `225-230,672-680`; lifecycle `226-245`; `AuditAction`; `NoopEventSink`; `SocReviewService.close_queue_item/add_note`; `SocAgentApprovalService`; AUD-01 `454-466` |
| `CONS-14` | Contract ahead | Engineering principles require failures not to leave half-written state | The primary analysis bundle is atomic, but `SocReviewService.correct()` and `SocExternalDispositionService.apply_event()` perform ordered writes across run/summary/queue/candidate/audit/disposition/outcome without a shared unit of work. A later failure can leave earlier writes committed | Engineering contracts `9-15,1756-1772`; `core/service.py:555-628`; `core/external_disposition.py:106-214`. This does not negate ALIGN-03; it narrows atomicity to the analysis bundle |
| `CONS-15` | Contract ahead | Engineering contract says LLM request metadata must be recorded before the non-rollbackable provider call, then final decision is written | `SocAnalysisService` emits a process-local requested event, calls the entire Runtime/model, and persists the run only after Runtime returns. With the default no-op event sink, a process loss during the call leaves no durable pre-call record | Engineering contracts `1768-1772`; `SocAnalysisService._analyze():219-251`; `NoopEventSink` |

### 4.4 Decision, Memory, and Authorization Semantics / 决策、记忆与授权语义

| ID | Marker | Documented claim | Current code fact | Evidence and AUD-03 question |
|---|---|---|---|---|
| `CONS-16` | Semantic mismatch | Solution says `Decision.confidence_source` distinguishes stub, LLM, human confirmation, and external disposition | Runtime policy sets analyzer provenance, but `SocReviewService.correct()` constructs a new `Decision` without setting `confidence_source`, so it defaults to `unknown`. External disposition additionally injects a fixed `corrected_confidence=0.95`, yet the final decision still does not carry `external_disposition` provenance | Solution `517-575`; `Decision` defaults; `core/service.py:581-588`; `core/external_disposition.py:426-439` |
| `CONS-17` | Current claim exceeds wiring | Memory diagrams describe confirmed memory becoming retrieval-visible through an explicit policy gate | Retrieval scoring/filtering is implemented, and confirmed records correctly default to `retrieval_enabled=false`; however no public service method, CLI mutation, Gateway endpoint, or governed policy enables the flag. Boss Demo changes it directly through the repository as a disclosed fixture | Solution `1014-1037`; lifecycle `545-580`; `SocMemoryService`; memory CLI/router; `demo/investigation.py:315-383`. Retrieval plumbing is real, but activation governance is not an application path |
| `CONS-18` | Semantic mismatch | Security contract requires every write to carry `actor_id`, `actor_type`, `auth_source`, and `request_id`; L3 state changes require role plus service authorization | `ActorContext` has no `auth_source`. Gateway derives authenticated actor identity, but `SocReviewService.close/correct` and `SocMemoryService.review_candidate` have no role checks. Approval does check admin/approver roles, while its request-create API accepts client-supplied `requested_by` and no request context | Solution `1105-1123`; engineering contracts `1472-1501`; `contracts/common.py:27-38`; SOC review/memory/approval routers and services. Entry authentication exists, but the documented core authorization contract is only partially implemented |

### 4.5 Documentation and Register Freshness / 文档与台账新鲜度

| ID | Marker | Documented claim | Current code fact | Evidence and AUD-03 question |
|---|---|---|---|---|
| `CONS-19` | Code ahead / doc stale | Engineering project tree still says “7-step pipeline” and its phase plan says Kafka is introduced in Phase 4 | Runtime has nine fixed steps; Kafka mapper/worker/consumer/daemon/deploy/smoke are already implemented, while worker pool, output topics, SSE and Prometheus remain future work | Engineering contracts `60-85,1353-1395,1819-1857`; Runtime/Kafka source. The old technical Phase wording should not be used as current delivery status |
| `CONS-20` | Stale operator guidance | Solution examples use `alert_demo/apt-2026494.json`, `soc review note ... --memory-candidate`, `soc memory review ... --confirm`, and `soc memory search --query ...` | The root `alert_demo` path is absent; note always creates a candidate and has no such flag; review requires `--decision`; search uses `--term`, `--facet`, or `--query-json` | Solution `1131-1169`; `cli.py:462-470,723-793`; committed sample paths under `backend/samples/alerts/` |
| `CONS-21` | Code ahead / doc stale | Mock register says the current GF-01/AA-01 gap is “enrichment persistence and disposition/eval gate” | EX-01, DP-01 and EV-01..EV-03 are implemented with migrations, CLI/projection and API/Web/TUI portions | Mock register `35-45`; solution 7.4; migrations `0014..0016` |
| `CONS-22` | Code ahead / doc stale | Mock register says external disposition has only an `InMemoryExternalDispositionRepository` | `SqlAlchemyAlertRepository` implements durable external-disposition storage and migration `0009` creates the table. What remains absent is the application ingress, not SQL persistence | Mock register `90-101`; `db/repositories.py:593-624`; migration `0009`; see `CONS-03` |
| `CONS-23` | Stale operator guidance | Mock register’s “current next step” is larger labeled eval plus PA-12 credential waiting | The authoritative delivery roadmap has completed Boss Demo/AUD-01 and sets AUD-02 then AUD-03 as the only current sequence | Mock register `113-117`; `delivery-roadmap.md:88-103,155-164` |
| `CONS-24` | Contract ahead | Engineering operations section requires health/readiness/Prometheus metrics and queue/backpressure behavior without labeling them as deferred in that section | Current code has Kafka status/health scripts, normalization metrics, bounded LLM admission and JSONL daemon metrics, but no SOC Prometheus exporter, global SLO dashboard, priority queue, delayed queue, or duplicate merge worker policy. Delivery roadmap places full operations in Stage 4 | Engineering contracts `1664-1721`; daemon/status/normalization code; delivery roadmap `115-127,129-140` |

## 5. Mock, Real, Shadow, and Reachability Truth Check / 性质核对

This table prevents two opposite errors: treating a real service as a mock, and treating a local
implementation as a production integration. It is not the final AUD-03 completeness matrix.

| Capability | Verified nature now | Important boundary |
|---|---|---|
| Deterministic Runtime, parser, evidence, policy | Real implementation | Model-independent does not mean mock |
| DeerFlow-backed LLM analyzer | Real optional provider path | Default remains stub; output remains uncalibrated and review-required |
| SQL repository and migrations | Real implementation | PostgreSQL production behavior still needs real environment validation; Boss Demo uses isolated SQLite |
| Review API, Web, TUI | Real application paths | No alert analyze/replay API or dedicated analyze Web surface |
| DeerFlow SOC Lead Agent | Real TUI application path | Separate from deterministic chat; requires installed profile/model configuration |
| Kafka consumer/commit/DLQ/daemon | Real adapter code and local broker smoke | Serial process; no production ACL/capacity evidence, worker pool, result topics, or external-disposition topic |
| CMDB/EDR/HIDS/TI/security-tag actions | Mock/local read-only providers by default | Real endpoint/credentials remain PA-12/Stage 4 data-gated |
| External disposition service and SQL table | Real service/persistence implementation | Input fixture is mock and no application ingress exists |
| Governed facts and authorized-activity matcher | Real deterministic lifecycle/matcher | Current source facts are fixtures until authoritative source sync exists |
| Authorization enrichment/proposal/outcome/eval | Real persisted implementation, shadow-only behavior | No decision mutation or auto-close; a passing report only permits rollout review |
| Memory candidate/review/retrieval algorithm | Real implementation | No governed retrieval-enable application path; demo enables one record directly |
| Main orchestrator | Real eval/demo service | Not a live CLI/Kafka/Web/Lead Agent orchestration entry |
| Approval/grant/execute boundary | Real internal safety boundary | No real external side effect; request resolution and persisted-request verification differ from diagrams |
| Prometheus/global operations view | Not implemented | JSONL/normalization/status signals are partial observability, not the planned operations product |

## 6. Consistency Conclusions / 一致性结论

1. **The core detection path is not a mock.** Runtime, normalization, evidence grounding, decision
   policy, persistence, ReviewQueue and bounded Lead Agent context are executable code with tests.
2. **The product is not yet reachable through every diagrammed entry.** Alert analysis is CLI/Kafka/
   demo reachable, while Gateway analysis and external disposition ingestion are absent.
3. **Shadow governance is implemented more deeply than the mock register says.** GF/AA/EX/DP/EV
   persistence and evaluation are real, but authoritative fact sources and auto-close remain unavailable
   or deliberately disabled.
4. **The largest semantic inconsistencies are not model quality issues.** They are API contract drift,
   approval request lifecycle, audit durability, multi-write atomicity, correction confidence provenance,
   and memory retrieval activation.
5. **Several “future” labels are stale in the opposite direction.** Kafka daemon and DeerFlow Lead
   Agent exist, but old Phase/docstrings still call them future; their remaining production gaps must be
   described precisely instead of calling the whole capability absent.
6. **No audited document should be edited piecemeal before AUD-03 freezes the resolution set.** A single
   Stage 3 slice should later update code and all affected docs together for each accepted blocker.

## 7. AUD-02 Acceptance / 验收

- [x] Compared code with solution, lifecycle, engineering contracts, and mock/real register.
- [x] Separated real implementation from application reachability.
- [x] Separated mock providers from real Runtime/service/persistence code.
- [x] Kept shadow-only governance distinct from production auto-close.
- [x] Recorded code-ahead documentation drift as well as document-ahead implementation gaps.
- [x] Did not change business code or silently repair audited documents during the audit.
- [x] Did not assign final `Complete / Gap / Mock / Data-gated / Deferred` status or P0/P1/P2 priority.

## 8. Handoff to AUD-03 / 交给下一步

`AUD-03` must convert AUD-01 inventory plus `CONS-01..24` into the only completeness matrix. Every row
must contain:

```text
capability / journey step
current state: Complete | Gap | Mock | Data-gated | Deferred
priority: P0 | P1 | P2
owner boundary
user/system impact
source evidence
acceptance test or visible proof
target stage: BG | PI | Parking Lot
```

AUD-03 should group, not duplicate, the factual differences under these decision areas:

1. Entry reachability and API contract.
2. Kafka protocol, result channel, concurrency and operations.
3. Lead Agent/main-orchestrator product wiring.
4. Approval, RBAC, audit and transaction safety.
5. Decision provenance and memory activation.
6. External/provider/data-gated integration.
7. Documentation-only reconciliation.

Only after that matrix is reviewed should Stage 3 receive a frozen P0/P1 implementation set.
