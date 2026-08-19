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
- Keep detection truth, tenant disposition, Memory directives, action authorization, and
  external execution as separate decision layers with explicit lineage.

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

## Persistence And Ingress

- PostgreSQL is the production/staging SOC store. Local DeerFlow SQLite configuration
  resolves to a separate `soc_agent_dev.db`; never reuse `deerflow.db` or present SQLite
  evidence as production proof.
- Repositories implement protocols from `soc_agent/protocols.py`. Migrations live under
  `soc_agent/db/migrations/`, use `soc db upgrade`, and own `soc_alembic_version`.
- Persist an analysis run, summary, optional ReviewQueue item, journal, and audit entries
  transactionally. A failed transaction must not leave a visible partial run.
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
