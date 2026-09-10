# SOC Agent Backend Guide

This directory is the fork-owned SOC business extension. The reusable DeerFlow harness
must remain vendor-neutral. Read the repository and backend guides first, then use this
file for SOC code. The authoritative product and engineering documents are:

- `.notes/ai_soc/soc-agent-solution.md`
- `.notes/ai_soc/progress.md`
- `.notes/ai_soc/delivery-roadmap.md`
- `.notes/reference-index/soc-agent-engineering-contracts.md`

## Ownership Boundaries

- Prefer new modules, adapters, contracts, routes, and tests under `soc_agent` over
  changes to DeerFlow core. A core change must be a small generic extension point or a
  framework fix that is useful without SOC.
- Entry surfaces (CLI, Gateway, Kafka daemon, TUI, Lead Agent) call the same application
  services. They must not reimplement analysis, persistence, review, Memory, or action
  semantics.
- Public behavior is exposed through versioned Pydantic contracts and protocols. Vendor
  aliases and loose payloads stop in normalizers/integration adapters.
- Runtime owns the deterministic control flow. LLM nodes perform bounded reasoning and
  may suggest only whitelisted routes; they do not own loops, persistence, authority,
  retries, or state transitions.
- Operator prose is Simplified Chinese, including nested explanations, actions, checks,
  policy advice and repair-generated text. Reuse `prompts/operator_language.py`; preserve
  JSON keys, enums, references and raw technical values. Known system templates may be
  localized in read-only projections through `core/operator_language.py`, never by
  rewriting stored decisions or inventing translations for arbitrary model output.
  A language mismatch alone must not trigger another LLM call or degrade a usable verdict.
- Keep detection truth, tenant disposition, Memory directives, action authorization, and
  external execution as separate decision layers with explicit lineage.
- Treat `run_id` as the stable investigation identity. Every persisted Runtime run has an
  alert result and run-scoped investigation context; a `ReviewQueueItem` is an optional
  human-task attachment, not the identity of the alert or a prerequisite for Web/TUI/API
  reads.

## Runtime Contract

- Alert admission means the configured upstream detector matched. The Runtime still
  decides scenario, direction, semantic roles, attempt/effect/impact stage, verdict, and
  recommendation. Missing optional enrichment alone must not erase the detector hit or
  force review.
- Trust source fields only within adapter-declared semantics. Never globally equate
  source with attacker or destination with victim. A provider-reported connection
  initiator is a scoped session fact, not proof of compromise or action authority.
- Preserve raw input and exact provenance for audit/replay. Bounded model input is built
  from canonical typed observations and stable catalogs: current evidence `E-*`, Skill
  `S-*`, adapter semantics `A-*`, confirmed Memory `M-*`, governed context `C-*`, and
  tool evidence `T-*`.
- Process fragments may share a canonical `event_scope_id`, but consumers may combine
  them only through the same normalized process name plus the same non-null PID. An alert
  boundary or shared event scope alone does not permit inventing one process chain from
  unrelated observations.
- The compact model output uses request-local aliases. Runtime restores aliases through
  the frozen one-to-one map, validates the compact contract, hydrates stable references,
  and Grounding verifies citations. Unknown or ambiguous references fail; hydration must
  not invent security semantics.
- A valid core verdict survives malformed optional sections. Record output degradation
  and use `AnalysisMaterialityReport` to block only dependent capabilities. Retry only an
  invalid core, at most through the configured bounded repair path; transport/capacity
  failure remains a retryable Runtime failure.
- Grounding proves that cited facts/context exist and correspond to the frozen request.
  It does not rejudge valid model inference. Core-reference failure or unresolved
  decision-level conflict creates review; optional scenario/direction/target defects
  preserve the verdict and block only the affected route or action.
- Optional second-pass role verification is controlled by
  `SOC_ROLE_VERIFIER_ENABLED`. It receives a narrow claim catalog, not raw vendor input,
  and cannot authorize an action. `challenged` adds a review guard; `unresolved` preserves
  a usable first-pass conclusion while blocking direction-dependent targeting.
- Every step records start/end/duration. Provider token usage is `reported`, `estimated`,
  or `mixed`; monetary cost and accuracy stay unmeasured without reviewed pricing and
  independent truth labels.
- Operator-facing execution timelines are read-only projections of persisted
  `AnalysisRun.steps`, provider request journals, and downstream write state. Keep the
  endpoint alert-scoped and lightweight; expose bounded metrics and sanitized errors,
  never raw prompts, evidence bodies, provider responses, or credentials.
- The explicitly gated corpus DEV workbench may expose a separate, on-demand,
  `soc_admin`-only audit bundle containing the persisted raw input, canonical alert,
  bounded model context, parsed model result, validation reports, Decision lineage, and
  Pattern/Memory writes. It must never share the live polling response, re-run Runtime,
  read process secrets, mutate state, or be enabled as a production analyst endpoint.
- The workbench recommendation guide is navigation metadata only and contains exactly
  two same-rule Memory rehearsals: context-only reference and exact-match Decision reuse.
  The server validates every fixed alert against its expected current Pattern group and
  reports missing/regrouped targets as drift; the guide must not seed results, prescribe
  a verdict, bypass Runtime/Memory governance, or turn rehearsal state into quality proof.
- Corpus DEV execution uses a process-local, bounded claim registry owned by the
  workbench service. Different alert IDs may run concurrently up to the configured LLM
  admission capacity; one alert ID may have only one active claim, and a duplicate
  request fails immediately without invoking Runtime. The process mutation atomically
  reserves the claim, returns `202 Accepted`, and runs the existing synchronous service
  workflow in the service-owned bounded executor; it must not hold an HTTP request open
  for model completion. `/activity` and `/execution` are the authoritative lightweight
  polling contracts. This DEV claim/executor is not a production multi-instance lease or
  durable queue; Kafka/API production ingress continues to require durable source
  idempotency and workers.
- The corpus workbench list contract is server-filtered and paginated. Its state response
  carries only the requested alert page, recurrent group summaries, and the bounded
  rehearsal manifest; it must not project every source alert for each navigation or
  filter change. A process mutation returns only the accepted active claim, after which
  clients poll activity/execution and refetch the authoritative page instead of embedding
  a completed alert or multi-megabyte corpus snapshot in the mutation response.
- The bounded-input audit artifact must distinguish model-visible projection from the
  frozen Runtime request. Only a matching prompt/builder version may be labeled exact;
  old runs use an explicit partial reconstruction status instead of silently applying a
  current projector to historical input.
- Persist the exact canonical `AlertInput` produced by the normalize step on new
  `AnalysisRun` records. Audit projections for older runs must mark that artifact partial
  and may show a clearly named projection from the frozen analysis request; they must not
  silently re-normalize old raw data with current Adapter code.
- Strict batch/evaluation replay uses canonical timezone-aware event time, never source
  row order or alert ID as chronology. Within one Memory cohort it processes the earliest
  unobserved alert first so a later sample cannot create context for an earlier event.
  The explicitly labeled corpus DEV interactive workbench is different: it allows
  operator-selected order and versioned Runtime reruns for product exploration, must set
  `causal_evaluation_allowed=false`, and must not present its Effective Decision metrics
  as time-causal evaluation. A rerun creates a new `AnalysisRun` with replay lineage but
  does not add another Pattern observation for the same source alert. Tenant workflow
  labels remain outside model input, are revealed only after the Runtime decision, and
  may measure operational agreement only when their timestamp follows the alert.
- Pattern aggregation uses fixed UTC windows. The generic Memory Profile defaults to 24
  hours; tenant profiles may own a versioned bounded default such as PingAn's 30-day
  window. Do not infer this duration from raw vendor fields or silently rewrite old
  observations when a Profile changes.
- The optional `SocMemoryPatternPostAnalysisObserver` is the shared post-analysis entry
  for ordinary persisted analysis lanes. Enable it only with an explicit runtime
  environment plus `SOC_MEMORY_PATTERN_DATA_CLASS`; Kafka, batch, and DEV workbenches
  that already call the pattern service must disable this observer. All lanes share the
  occurrence key, so replay or transport duplication cannot increase support.

## Persistence And Ingress

- PostgreSQL is the production/staging SOC store. Local DeerFlow SQLite configuration
  resolves to a separate `soc_agent_dev.db`; never reuse `deerflow.db` or present SQLite
  evidence as production proof.
- Repositories implement protocols from `soc_agent/protocols.py`. Migrations live under
  `soc_agent/db/migrations/`, use `soc db upgrade`, and own `soc_alembic_version`.
- A brand-new local SQLite migration may remove only the artifacts created by that same
  failed initialization and retry one transient `disk I/O error`. Never delete, replace,
  stamp, or destructively retry a database that existed before migration began; preserve
  it for explicit operator recovery. PingAn Host DEV centralizes this migration before
  starting sidecars and disables their duplicate auto-migration path.
- Long-running external submissions use the vendor-neutral `ProcessingJobRepository`,
  not request-scoped background tasks. Persist before acknowledging, claim by bounded
  lease, recover expired work, and keep a stable Runtime idempotency key so a crash after
  analysis cannot trigger a second model charge. SQLite permits one coordinating worker;
  PostgreSQL claims use `FOR UPDATE SKIP LOCKED` for multiple replicas.
- A terminal processing result and its Callback Outbox entry commit atomically. Callback
  retries never rerun analysis, and every delivery/retry/dead-letter/expired-lease attempt
  is append-only audit state. Generic job contracts must not learn tenant lifecycle codes,
  legacy response envelopes, or vendor credentials.
- Persist an analysis run, summary, optional ReviewQueue item, journal, and audit entries
  transactionally. A failed transaction must not leave a visible partial run.
- `SocAlertResult` separates decision usability from operator attention. Uncertainty,
  evidence gaps, degraded optional output, and unavailable enrichment stay visible as
  advisory result metadata. Only unresolved material current-fact conflicts enter
  ReviewQueue. Do not recreate the old behavior where `needs_review=true` manufactured a
  task for nearly every alert.
- `SocCaseOutcomeView` is the deterministic operator projection of persisted Runtime,
  Memory, tenant-policy, external-feedback, and action lineage. The primary operator answer
  is `recommended_handling` (ignore/transfer); security verdict, disposition and progress
  remain separate audit fields. Failed runs cannot suggest ignore from a retained decision.
  Classify evidence gaps by material impact; an advisory gap must not erase a usable
  verdict, while a decision-blocking gap must prevent the case from appearing closed.
  Trace `needs_review` to its owning decision stage: an applied tenant policy that alone
  requires an operational handoff does not imply missing critical facts. Preserve independent
  materiality review and keep the handoff pending until execution/feedback confirms it.
  A review-only policy with no concrete disposition is also operational follow-up, not
  evidence failure. Policy `unknown` means abstention: preserve the pre-policy Base/Memory
  recommendation and keep the raw advisor output for audit. New effective decisions use
  `soc.effective_decision_policy.v4`; historical transitions are not rewritten.
  `core/handling.py` owns shared decision-level blockers, not a second evidence evaluator:
  prose gaps and optional targeting/calibration/truncation flags alone cannot require transfer.
  Unresolved decision-level reasons take priority over an ignore plan and project transfer with
  the concrete reason. Automation preserves these independent guards after Memory/policy clear:
  do not persist an adopted disposition or select/authorize actions for a decision-blocked run.
  Explicit policy authorization under a general review flag still works without those defects.
  Unadopted historical plans stay in lineage; actual external feedback is never erased.
  Base may already use retrieved context-only Memory. The later Memory stage applies eligible
  typed directives, not initial retrieval; `no_input` there never proves the model had no Memory.
  `recommended_handling` is a read-only classification shared with lightweight alert lists,
  corpus comparison and ZEUS result mapping. `core/handling.py` resolves scoped mapped
  feedback before the effective disposition and then classifies the effective verdict.
  List reads use bounded transition/feedback lookups, never full AnalysisRun/model loads.
  It is distinct from `operational_disposition`, must not close a
  case, and must not create authorization or execution.
  API and Web clients consume this projection and must not calculate a competing outcome.
  Progress uses the existing closure aggregate plus specific reason codes and server-owned
  `progress_label` / `progress_detail` from `core/case_progress.py`. Do not describe output/citation
  errors, policy application restrictions or unattributed review as missing business facts.
  Policy disposition is a plan; only mapped terminal disposition feedback can produce `closed*`.
  A single action result, including mock success, never closes the entire alert. Scope supplied
  executions and feedback to the current Run (or explicit alert-only feedback). This read model
  must not rerun LLMs, schedule work, rewrite decisions or bypass review/authority gates.
  Optional `AnalysisResult.conclusion_support` is a same-call narrative, not a new decision
  or authority: adopted Memory explains resolved business questions, optional checks and
  future reassessment triggers separately from current material gaps. References must be
  confirmed M-* items cited by decision reasoning in the frozen request. Malformed support
  is omitted with a hydration log, never a core failure or extra model call. It cannot
  clear evidence gaps or materiality guards. A successfully applied directive may supersede
  Base prose; retain it as `prior_analysis_gaps`, without rewriting the run. Source coverage
  gaps remain intact. Unused targeting limits stay in technical detail, not verdict failure.
  Workbench cohort `memory_id` means a group has a governed record; actual retrieved/cited
  Memory comes from the frozen run catalog. Link by metadata `memory_id`, not `source_id@vN`.
- Candidate review, action approval, and normalization maintenance own independent
  repositories and APIs. ReviewQueue resolution must not inline or implicitly perform
  any of those state transitions.
- Product effectiveness is a read model owned by `SocEffectivenessService` and its
  repository protocol. It selects the latest Run per alert and joins persisted Decision,
  applied Disposition, trusted final outcome, model-usage, and Memory feedback lineage.
  API/Web must not reimplement formulas. Unlabeled alerts never enter accuracy or miss
  denominators; `rule_code` remains an optional vendor alias, and every rule-improvement
  recommendation is advisory only.
- `conclusion_maintenance_rate` is a workflow signal: it counts completed latest Runs for
  which no high-trust final outcome contradicts the Effective Verdict. It includes silent,
  unverified alerts and must never be renamed to analyst approval, label coverage, or
  accuracy. Only trusted final outcomes enter quality denominators.
- The Gateway reuses each effectiveness snapshot for 30 seconds per
  `window_days/tenant_id/source_type` scope and coalesces concurrent reads in one process.
  This cache is a bounded read optimization, not business state: the repository remains
  authoritative, unavailable reads are not cached, and a refresh after expiry reruns the
  exact selected-window SQL aggregate rather than replaying alerts or invoking models.
- Rule effectiveness groups by canonical detection identity, not mutable rule display
  names. The drill-down contract is `Rule Code -> same behavior -> exact Memory version`.
  Directive outcomes are attributable; context-only Memory is non-causal. Historical
  Memory uses must not inherit the current record version's label or activation state,
  and wrong-auto-ignore requires an actually applied ignore disposition plus trusted
  final risk truth.
- Confirmed Memory context exposes typed applicability and the reviewer-confirmed verdict
  to the bounded analyzer. Fully applicable `exact_context` is a strong semantic prior,
  but remains non-authoritative without a Decision Directive: deviating requires cited
  current evidence, while Tenant Policy and action authority remain separate stages.
- Journal provider requests before invocation. Recovery may resume only when the frozen
  request and config/model lineage still match; otherwise start a new attempt.
- Kafka topic `soc.alerts.raw.v1` accepts only
  `SocAlertRawEnvelope(schema_version=soc.alert.raw.v1)`, not bare vendor payloads.
- External state/reason feedback enters through the canonical external-disposition
  command/API. Adapters translate source codes; generic Runtime never recognizes a
  tenant's lifecycle codes.
- Published SOC APIs stay under `/api/soc/*`, return typed success bodies, emit RFC
  Problem Details, and use authenticated Gateway identity as authority. Actor headers are
  attribution only. L3 mutations require trusted auth source, role policy, idempotency,
  and append-only audit.

## Agent, Skill, And Action Boundaries

- SOC Lead Agent reuses DeerFlow's `lead_agent`, middleware, Skill, MCP, and subagent
  mechanisms. `backend/soc_agent/lead_agent.py` is an adapter/configuration boundary, not
  a second agent framework.
- Specialist agents use `subagents.custom_agents` and the native `task` tool. They return
  analysis/advice to the controller and do not independently close alerts, mutate Memory,
  or execute response actions.
- Runtime Skill routing is deterministic and bounded. Generic method belongs in `S-*`,
  adapter semantics in `A-*`, confirmed historical experience in `M-*`, tenant-static
  knowledge in `C-*`, and live provider results in `T-*`.
- Read-only provider results persist as `InvestigationEvidence` with provider/mode/mock
  provenance and `decision_impact=none`. Providers never directly change verdict, close
  ReviewQueue, confirm Memory, or authorize action.
- Automatic investigation is an application bridge outside the fixed Runtime. It must
  use the action registry/dispatcher, persist evidence, expose it through shared review
  context, and preserve the original Runtime decision.
- Response automation is post-Runtime and default-off. `SocAutomationService` records
  Base -> Memory -> Tenant -> Effective -> Authorization -> Execution lineage. A high
  risk decision can be automated only under explicit policy, target coherence, provider
  configuration, and audit requirements; human approval is required only where policy
  says so.
- Runtime intentionally has no mock `endpoint.process_tree.lookup` or
  `host.event_context.lookup`. Native bounded alert evidence carries those observations
  until a real provider is explicitly approved.

## Knowledge, Tenant Policy, And Correlation

- Reviewed tenant-static knowledge is bounded, versioned, source-linked `C-*` context and
  has no direct decision authority. Dynamic authorization/exercise/maintenance facts use
  the governed-context lifecycle.
- Process-chain Playbooks may use only canonical observations and explicit direct-parent
  fields. File relation/name/path constraints must match one `FileObservationRef`; never
  assemble a pattern by mixing process images, IOC artifacts, or action targets from a
  global path set.
- Tenant operational handling is a default-off post-Runtime layer. Generic code must not
  contain `tenant == pingan` or hostname-substring safety branches. Shadow/enforced mode,
  policy version, matched signals, before/after decision, and action impact are audited.
- Phase-2 correlation is an explicit service bridge sharing the
  `AlertSummaryRepository`; it is not a hidden Runtime node. Historical evidence can
  support a unified investigation but cannot silently suppress an alert or confirm
  Memory. Evaluation labels distinguish same incident, related distinct, and unrelated.

## Development Workflow

1. Read the current `.notes/ai_soc` plan and engineering contracts.
2. Choose the smallest phase-aligned slice and verify existing APIs/call sites with
   `rg`, focused source reads, tests, or runtime traces.
3. Implement the SOC extension first; change upstream core only at a justified generic
   extension point.
4. Add focused tests proportional to the boundary touched. Full `test_soc_*.py` plus the
   architecture suite is a milestone/release gate, not the default edit loop.
5. Update solution/contracts when semantics change and append the completed slice,
   verification, and next step to `.notes/ai_soc/progress.md`.

Use Understand Anything only when explicitly requested. Existing graphs are static
snapshots and must not be updated as part of normal development.
