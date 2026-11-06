# SOC Agent Backend Guide

This directory is the fork-owned SOC business extension. The reusable DeerFlow harness
must remain vendor-neutral. Read the repository and backend guides first, then use this
file for SOC code. The authoritative product and engineering documents are:

- `.notes/ai_soc/soc-agent-solution.md`
- `.notes/ai_soc/progress.md`
- `.notes/ai_soc/delivery-roadmap.md`
- `.notes/reference-index/soc-agent-engineering-contracts.md`

Detailed implementation rules live in
[IMPLEMENTATION_CONTRACTS.md](IMPLEMENTATION_CONTRACTS.md). Read the applicable
section **before editing the corresponding behavior**; these are mandatory module
rules, not optional historical notes. Keep shared boundaries here and detailed rules
in that reference so unrelated tasks do not inherit the full corpus-workbench history.
Memory and PingAn work also follow their own nearest `AGENTS.md`.

| Work area | Required detailed guidance |
| --- | --- |
| Corpus preparation, rounds/jobs, draft jobs, list projections, model admission, fact-only successors | [Ownership Boundaries](IMPLEMENTATION_CONTRACTS.md#ownership-boundaries) |
| Normalization/facts, model requests, direct resolution, policy, execution/audit projections | [Runtime Contract](IMPLEMENTATION_CONTRACTS.md#runtime-contract) |
| SQL, repositories, migration, review results, Kafka/HTTP ingress, callback outbox | [Persistence And Ingress](IMPLEMENTATION_CONTRACTS.md#persistence-and-ingress) |
| Lead/specialist agents, Skills, providers, approval and actions | [Agent, Skill, And Action Boundaries](IMPLEMENTATION_CONTRACTS.md#agent-skill-and-action-boundaries) |
| Tenant knowledge/policy and correlation | [Knowledge, Tenant Policy, And Correlation](IMPLEMENTATION_CONTRACTS.md#knowledge-tenant-policy-and-correlation) |
| Continuous accumulation, batch Continue/retry/report behavior | [Continuous corpus accumulation](IMPLEMENTATION_CONTRACTS.md#continuous-corpus-accumulation) and [Batch operations](IMPLEMENTATION_CONTRACTS.md#batch-operations) |

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
- SOC model-bound structured JSON uses `utils/model_json.py:model_json` (UTF-8 text,
  compact separators). Apply it to contexts, schemas, examples and repair requests;
  never strip whitespace inside raw logs, commands, strings or invalid model output.
  Keep storage/audit formatting, business hashes and provider signing independent.
  Prompt wire-format changes bump Prompt versions, not Memory/profile/schema versions.
  Lead/sub-agent SOC context already uses compact JSON; preserve that boundary without
  modifying upstream DeerFlow formatting. `test_soc_compact_model_json.py` pins this.
- Operator prose is Simplified Chinese, including nested explanations, actions, checks,
  policy advice and repair-generated text. Reuse `prompts/operator_language.py`; preserve
  JSON keys, enums, references and raw technical values. Known system templates may be
  localized in read-only projections through `core/operator_language.py`, never by
  rewriting stored decisions or inventing translations for arbitrary model output.
  A language mismatch alone must not trigger another LLM call or degrade a usable verdict.
- Keep detection truth, tenant disposition, Memory directives, action authorization, and
  external execution as separate decision layers with explicit lineage.
- Authorization behavior values retain their 1–512-character selector contract. If a behavior
  candidate is empty after whitespace/entity normalization or exceeds the limit, the query
  builder records typed `unavailable_dimensions` and a source-path/length warning while
  preserving the complete evidence. The matcher returns
  `unavailable` before evaluating any remaining selectors; never clip or hash a command
  into an authorization match. Aggregate repeated omissions by reason to bound warnings.
  Empty unavailable dimensions are omitted from serialization
  to preserve existing enrichment query hashes and idempotent replay.
- Treat `run_id` as the stable investigation identity. Every persisted Runtime run has an
  alert result and run-scoped investigation context; a `ReviewQueueItem` is an optional
  human-task attachment, not the identity of the alert or a prerequisite for Web/TUI/API
  reads.

## Corpus And Background Work

- `demo/corpus_batches.py` and `corpus_batch_preview.py` own pure, label-blind
  chronological preparation. Preview is not execution authorization. Preserve source
  identities, full log content, hashes, immutable plans and historical versions.
- `demo/corpus_experiments.py` dispatches immutable experiment rounds through shared
  durable Processing Jobs. The CLI never starts a second Runtime. Once an experiment
  exists, manual execution uses its durable controls too.
- Learning, validation and facts-only work have distinct evidence/Memory boundaries.
  Freeze validation Memory; pending candidates never become current-round authority.
  Preserve scoped generation, snapshot, configuration and dataset fences on every
  resume/retry. Read the detailed rules before changing these comparisons.
- Corpus browse/activity/report endpoints are read-only, bounded projections of durable
  state. Select current generation/latest job before pagination; an earlier success
  cannot hide a newer failed/queued attempt. Never load every full Run into a polling
  response or backfill observations on reads.
- Drafting and analysis share the existing configured dispatcher/model budget.
  Lease-based claims own work; model calls never hold DB locks. A timeout does not
  release an admission slot until its uncancellable local future actually finishes.
- Explicit batch restart/discard/continuation operations have separate authority,
  idempotency, quiescence and immutable lineage rules. They must not be reimplemented
  as blanket resets, incidental schema creation, or silent job replacement.
- Read the complete
  [corpus/job ownership rules](IMPLEMENTATION_CONTRACTS.md#ownership-boundaries)
  for exact source selection, capacity ordering, attempt budgets and restart behavior.

## Runtime Contract

- Runtime owns normalization, facts, bounded analysis, Memory/policy decisions and
  durable state transitions. Shared application services own entry and persistence;
  model outputs do not become authority merely by passing JSON/schema checks.
- DEV execution options are immutable request snapshots, not process-global environment
  mutation. Paths, models, tenant scope, provider modes and action authority are never
  client analysis options. Resume keeps saved settings; fresh corpus calls use the
  corpus defaults rather than changing generic Runtime defaults.
- Policy-first direct resolution may skip the primary analyzer only under its complete
  deterministic policy/eligible exact-Memory contract. Record `analysis=None`, nullable
  confidence, skipped steps and frozen provenance; never fabricate a base verdict.
  The final bundle, callback and action-authority boundaries still apply.
- Optional normalization review has its own versioned request, journal and acceptance
  policy. Recovery can reuse only a matching successful saved fact review, without
  charging historical usage again. A changed merger policy invalidates reuse; facts
  and current decisions remain distinct. A failed/partial provider response must not
  become fabricated evidence or an implicit success.
- Preserve raw/canonical/current evidence, model reasoning, tenant knowledge and Memory
  as distinct namespaces. Never silently clip a command into a policy match, rewrite
  prior Runs with today's parser, or relabel a reconstructed prompt as actual input.
- Audit/execution views are projections of persisted Run identities and steps, not new
  model calls. Raw DEV audit bundles remain separately gated and are never ordinary
  polling or production analyst responses.
- Ordinary replay/evaluation is chronological and label-blind. Operator-ordered DEV
  exploration explicitly disclaims causal evaluation; rerunning the same alert must
  not invent independent Pattern support.
- The complete [Runtime Contract](IMPLEMENTATION_CONTRACTS.md#runtime-contract)
  owns detailed normalization, request-size, confidence, evidence, provider transport,
  projection and Pattern rules. Read it for any change to those paths.

## Persistence And Ingress

- PostgreSQL is the production/staging SOC store; local SQLite uses a separate
  `soc_agent_dev.db`, never DeerFlow's `deerflow.db`.
- Repositories implement `soc_agent/protocols.py`; SOC migrations live under
  `soc_agent/db/migrations/`, use `soc db upgrade`, and own `soc_alembic_version`.
  Never create schema on reads, delete an existing DB after a migration failure, or
  treat SQLite test results as PostgreSQL production verification.
- `db.create_soc_engine` owns SQLite connection/journal behavior. Respect its SQLite
  patch-level WAL gate, bounded BUSY/LOCKED persistence retries, and caller-owned
  transaction exceptions. A DB retry must never rerun a model or external effect.
- Persist the analysis bundle atomically. Durable jobs are committed before ingress
  acknowledgment and claimed by fenced leases; callbacks/outbox commit with terminal
  results, retry independently, and never rerun analysis.
- Result usability, operator attention, security verdict, disposition and progress are
  different fields. Only material unresolved current-fact conflicts create ReviewQueue
  tasks; ordinary gaps must not recreate the old review-every-alert behavior.
- `run_id` identifies investigation and audit. Preserve tenant/environment scope,
  source idempotency, run-specific decisions and append-only delivery/feedback lineage.
- SOC APIs stay under `/api/soc/*` and use authenticated Gateway authority. Actor
  headers are attribution only. Approval execution consumes grants by repository CAS;
  exact retries reuse the committed result rather than executing again.
- Read [Persistence And Ingress](IMPLEMENTATION_CONTRACTS.md#persistence-and-ingress)
  for exact mutation, SQL/Kafka, retry, outbox, outcome-projection and feedback contracts.

## Agent, Skill, And Action Boundaries

- Trace integration is a default-disabled external investigation adapter in
  `integrations/trace_agent/`; shared API types live in the `soc-trace-agent`
  workspace package. Follow [the developer handoff](../../docs/soc-trace-agent-development.md).
  The scaffold has no dispatcher, ZEUS callback or real engine. Preserve effective
  outcome provenance; never regenerate gaps or treat all manual checks as tool work.

- SOC Lead Agent reuses DeerFlow's `lead_agent`, middleware, Skill, MCP, and subagent
  mechanisms. `backend/soc_agent/lead_agent.py` is an adapter/configuration boundary, not
  a second agent framework.
- Upstream sync must preserve the server-owned review-thread binding and message
  provenance filters alongside upstream project metadata protection. Embedded graph
  reuse must include effective user identity as well as the selected profile's skills
  and middleware configuration.
- Specialist agents use `subagents.custom_agents` and the native `task` tool. They return
  analysis/advice to the controller and do not independently close alerts, mutate Memory,
  or execute response actions.
- Runtime Skill routing is deterministic and bounded. Generic method belongs in `S-*`,
  adapter semantics in `A-*`, confirmed historical experience in `M-*`, tenant-static
  knowledge in `C-*`, and live provider results in `T-*`.
  Skill Context v3 loads complete selected `SKILL.md` and package-local `references/*.md`
  methods with hashes. Legacy token budgets are allocation metadata, not clipping limits.
  Model projection sends the complete method once in S-*; Skill metadata points there.
  S/A/M/C/T context prose and condition comparisons bypass raw-evidence string clipping.
  Overall Prompt size guards still fail explicitly; never silently remove an exception.
  Primary analysis's inclusive 1,500,000-character guard is not a Token budget;
  input accounting and overflow rules follow the engineering contracts.
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
  Supplementary clue selectors match all constraints on one typed fact, not free-form
  names or a join of unrelated logs. URL fragment routes may identify an application,
  but never rewrite canonical HTTP paths, network destinations or security roles.
  Service identifiers and their meaning belong in tenant profiles, not generic code.
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
