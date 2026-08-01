# AGENTS.md

This file provides guidance to AI coding agents (Claude Code, Codex, and others) when working with code in this repository. It is the source of truth; the sibling `CLAUDE.md` imports it via `@AGENTS.md`.

It is the **monorepo orientation layer**: it maps the whole repo and points to the
module guides that own the depth. For anything inside a module, read that module's
guide rather than expecting full detail here:

- **[backend/AGENTS.md](backend/AGENTS.md)** — backend depth: harness/app split, agent and
  middleware chain, sandbox, MCP, skills, memory, IM channels, persistence/migrations,
  config system, test layout.
- **[frontend/AGENTS.md](frontend/AGENTS.md)** — frontend depth: Next.js App Router layout,
  thread/streaming data flow, code style, commands.

## What is DeerFlow

DeerFlow is a LangGraph-based AI super-agent system with a full-stack architecture. The
backend runs a "super agent" with sandboxed execution, persistent memory, subagent
delegation, and extensible tools (built-in, MCP, community), all per-thread isolated. The
frontend is a Next.js chat UI. External IM platforms (Feishu, Slack, Telegram, Discord,
DingTalk) bridge into the same agent through the Gateway.

## Service Topology

A single `make dev` / Docker stack runs four cooperating services:

| Service | Port | Role |
| --- | --- | --- |
| **Nginx** | `2026` | Unified reverse-proxy entry point — open this in the browser |
| **Gateway API** | `8001` | FastAPI REST API + embedded LangGraph-compatible agent runtime |
| **Frontend** | `3000` | Next.js web interface |
| **Provisioner** | `8002` | Optional — only when sandbox is configured for provisioner/K8s mode |

Nginx is the single public entry: it serves the frontend and proxies `/api/langgraph/*`
to the Gateway's LangGraph runtime, rewriting it to Gateway's native `/api/*` routes; all
other `/api/*` go straight to the Gateway REST routers. See
[backend/AGENTS.md](backend/AGENTS.md) for the runtime and router detail.

## Repository Map

```text
deer-flow/
├── Makefile                        # Root orchestration: drives the full stack
├── config.example.yaml             # Template -> copy to config.yaml
├── extensions_config.example.json  # Template -> copy to extensions_config.json
├── backend/                        # Python backend — see backend/AGENTS.md
├── frontend/                       # Next.js frontend — see frontend/AGENTS.md
├── docker/                         # docker-compose files, nginx config, provisioner
├── skills/                         # Agent skills: public/ (committed), custom/ (gitignored)
│                                    # Managed integration skill packs are global at .deer-flow/integrations/skills/{provider}/
│                                    # Integration credentials and enabled state remain per-user
├── contracts/                      # Cross-component JSON contracts (e.g. subagent status, skill review)
├── scripts/                        # Root orchestration scripts invoked by the Makefile (check, configure, doctor, support_bundle, serve, nginx, docker, deploy, setup_wizard)
├── tests/                          # Root-level tests (currently tests/skills/ — public skill tests)
├── validation/                     # Local/offline validation builders; generated sensitive artifacts are gitignored
└── docs/                           # Cross-cutting docs, plans, and design notes
```

Runtime config lives at the **repo root**: copy `config.example.yaml` → `config.yaml`
(main app config) and `extensions_config.example.json` → `extensions_config.json` (MCP
servers + skills). Both real files are gitignored and may be edited at runtime via the
Gateway API. Config schema and resolution order are documented in
[backend/AGENTS.md](backend/AGENTS.md).

Skill quality review note:
- `skills/public/skill-reviewer/` is the built-in read-only skill quality reviewer.
  It uses the harness-layer `review_skill_package` tool and contracts in
  `contracts/skill_review/`. Model-visible review data is compact and
  tag-neutralized; full raw payloads stay in tool artifacts. See
  [backend/AGENTS.md](backend/AGENTS.md) for the non-activation, SkillScan, and
  `skill-creator` ownership boundaries.

Scheduled-task note:
- The scheduled-task MVP adds a workspace page at `/workspace/scheduled-tasks` plus a background scheduler service gated by `config.yaml -> scheduler.enabled`.
- Scheduled background runs are intentionally non-interactive: they execute through the normal run lifecycle, but the lead-agent toolset excludes `ask_clarification` when `context.non_interactive=true`. The key is honored only for internally-authenticated callers (the scheduler launch path); client-supplied `context.non_interactive` is dropped.

## Commands: Root vs. Module

Root `make` targets drive the whole stack:

```bash
make setup
make doctor
make config
make check
make install
make dev
make start
make stop
make up / down
make docker-start / docker-stop / docker-logs
```

Per-module commands drive a single module:

```bash
cd backend && make dev
cd backend && make test
cd backend && make lint
cd backend && make format

cd frontend && pnpm dev
cd frontend && pnpm check
cd frontend && pnpm test
```

Rule of thumb: **root `make` = the full application**; **`backend/Makefile` and
`frontend` (`pnpm`) = per-module work.**

Host-side pnpm consumers, including the root/frontend Makefiles and local diagnostic scripts, must run through `scripts/pnpm.py`. The runner preserves direct `pnpm`/`pnpm.cmd` priority, falls back to `corepack pnpm`, and is invoked from `frontend/` so Corepack honors the package-manager version pinned by that project.

## Where to Go Next

- Backend work → **[backend/AGENTS.md](backend/AGENTS.md)**
- Frontend work → **[frontend/AGENTS.md](frontend/AGENTS.md)**
- Setup & install → **[Install.md](Install.md)**, **[CONTRIBUTING.md](CONTRIBUTING.md)**
- Project overview & usage → **[README.md](README.md)** and translations
- Security policy → **[SECURITY.md](SECURITY.md)**
- Changes → **[CHANGELOG.md](CHANGELOG.md)**
- Cutting a release → **[RELEASING.md](RELEASING.md)**

## Cross-Cutting Conventions

These apply repo-wide; module guides own the module-specific detail.

- **Documentation update policy** — keep docs in sync with code: update `README.md` for
  user-facing changes and the relevant `AGENTS.md` for development/architecture changes in
  the same change set.
- **Test-driven development** — features and bug fixes ship with tests. Backend tests live
  in `backend/tests/`; frontend tests live in `frontend/tests/`.
- **Format before pushing** — run `make format` for backend changes and `pnpm check` for
  frontend changes. Backend CI enforces `ruff format --check`.

## SOC Agent Branch Context

This branch is used to build a SOC alert triage agent on top of DeerFlow + LangGraph.
The current authoritative plan is
[.notes/ai_soc/soc-agent-solution.md](.notes/ai_soc/soc-agent-solution.md).

Current SOC direction:

- This repo is a fork of upstream DeerFlow. Keep SOC Agent work as incremental business
  extension whenever possible: add SOC-specific modules, adapters, schemas, routes, and
  docs instead of modifying upstream core code. Only touch existing DeerFlow core files
  for small, generic extension points or framework fixes that are clearly useful beyond
  SOC, and keep those changes easy to explain for future upstream sync.
- PostgreSQL is the production/staging SOC business store. Local tests, demos, Runtime validation and
  Alpha acceptance may use purpose-specific isolated SQLite files under `backend/.deer-flow/`; do not
  reuse DeerFlow's generic `alerts.db` or claim SQLite evidence as PostgreSQL production evidence.
- SOC persistence code lives under `backend/soc_agent/db/` and implements repository
  protocols from `backend/soc_agent/protocols.py`; keep it separate from DeerFlow harness
  persistence unless a generic upstream extension point is genuinely needed.
- SOC schema migrations live under `backend/soc_agent/db/migrations/` and are applied with
  `soc db upgrade`; the migration version table is `soc_alembic_version`.
- Kafka/Redpanda daemon ingestion is implemented; local broker default is `localhost:9092`, while
  production ACL/capacity/recovery evidence remains data-gated. Topic `soc.alerts.raw.v1` accepts only
  strict `SocAlertRawEnvelope(schema_version=soc.alert.raw.v1)` payloads, not bare vendor alerts.
- Phase 1 target is CLI + Runtime reliability: fixed pipeline, schema/domain validation,
  step trace, audit logging, basic rate limiting, and `analyze` / `correct` / `replay`.
- LLMs do not own the main control flow. Runtime owns the deterministic pipeline; LLM nodes
  handle bounded reasoning and can only suggest soft routes from a whitelist.
- SOC Runtime live analysis reuses DeerFlow `create_chat_model` through `backend/soc_agent/llm/`.
  Select it explicitly with `SOC_ANALYZER_MODE=llm` / `SOC_LLM_MODEL` or CLI
  `--analyzer-mode llm --model-name NAME`; the default remains deterministic for tests and replay.
- SOC model calls have independent process-local admission controls (`SOC_LLM_MAX_CONCURRENCY`,
  optional requests-per-minute, admission timeout, and `SOC_LLM_CALL_TIMEOUT_SECONDS`). Analyzer evidence is deterministically
  grounded against the exact bounded prompt projection before `SocDecisionPolicy` runs.
- New live analyzer responses use `soc.analysis_result.v2`: open-vocabulary
  `TriageScenarioAssessment` items distinguish upstream/inferred/hybrid origin and
  detection/attempt/effect/impact stage, cite zero-based indexes into the same result's evidence,
  retain competing explanations, gaps and executable manual checks, and have exactly one primary
  when non-empty. `soc-analysis-v8` / `soc-analysis-json-parser-v5` reject unknown fields and
  malformed references. This is reasoning output only; Grounding and `SocDecisionPolicy` still own
  evidence admission and operational decision guards.
- Evidence Grounding uses `soc.analysis_evidence_grounding.v2`. A source/value match is not enough:
  if an evidence description imports a distinctive bounded fact outside its quoted value, the item
  becomes `description_context_leakage` and remains ungrounded. Preserve matched and foreign paths,
  never semantically repair the model output. An exact visible encoded-omission marker may ground
  only field presence, encoding shape and boundary omission; its hidden bytes, private sidecar hash,
  validity and outcome implications remain ungrounded.
- Persisted analysis writes run/summary/optional review/audit as one `AnalysisPersistence` transaction.
  Retryable Runtime failures do not commit Kafka offsets or immediately create analyst queue noise;
  non-retryable failures are recorded, reviewed, and dead-lettered.
- Persisted Runtime calls use `analyze_journaled()`: immediately before analyzer/provider invocation,
  `SocAnalysisService` commits the same run as `running` with bounded `AnalysisRequestJournal`
  metadata. Process loss or final-bundle rollback leaves that row discoverable; `soc recover` marks
  it `interrupted` after a stale window and creates a linked replay. Never put rendered prompts,
  provider headers/responses, credentials, tokens, or evidence values in the journal.
- Human correction confidence uses `human_confirmation`; only the trusted external-disposition
  service path uses `external_disposition`. Both are uncalibrated confirmation strength under
  `soc.correction_policy.v1`, not probabilities; preserve source, explicit/default state and
  explanation through run, summary and audit.
- External status/reason feedback has an authenticated canonical Gateway ingress at
  `POST /api/soc/external-dispositions`; callers submit `SocExternalDispositionIngressCommand` with a
  stable source event ID, while vendor mapping/trust configuration remains server-owned. Real
  Zeus/ITSM/SOAR source feeds and credentials remain data-gated and must reuse this service boundary.
- Published SOC Gateway routes keep the compatible `/api/soc/*` paths and direct typed success bodies.
  They must use `app.gateway.routers.soc_transport.create_soc_router()` for
  `X-SOC-API-Version: 1`, request/trace correlation and sanitized Problem Details. Update
  `contracts/soc_api/openapi-v1.snapshot.json` only as an intentional reviewed API contract change;
  authenticated Gateway identity always wins over caller actor headers.
- L3 SOC state changes require both a trusted `ActorContext.auth_source` and a command-specific role
  inside the core service; Gateway/router checks alone are insufficient. Approval requests follow
  `pending -> approved|rejected|expired`; approve accepts only a persisted request ID and atomically
  creates at most one grant. `SocMutationUnitOfWork` atomically commits Alpha L3 business changes and
  append-only `SocMutationAuditRecord` rows; process events are emitted only after commit.
- LLM-discovered knowledge is candidate knowledge only. It must be confirmed by a human before
  it can affect future decisions.
- Confirmation creates a retrieval-disabled SOC memory record. Only
  `SocMemoryService.set_retrieval_activation()` may enable/disable retrieval, with
  `soc_memory_reviewer|soc_admin`, trusted auth provenance, reason, expected version, validity/review
  bounds, idempotency and atomic mutation audit. Direct/legacy boolean flags, expired activation and
  overdue review are excluded from bounded retrieval; CLI/API/Web/Boss Demo use the same service.
- Governed operational context facts are separate from evidence, memory, action approval, and
  detection truth. GF-01 uses typed contracts plus append-only fact versions through
  `SocGovernedContextService` / `GovernedContextFactRepository` and migration
  `0013_governed_context_facts`. AA-01 uses `SocAuthorizedActivityService` plus vendor-neutral
  `AuthorizationQuery` / deterministic event-time matching; `soc context match` is read-only and
  must not affect Runtime, ReviewQueue, disposition, or close alerts. EX-01 persists append-only
  `AuthorizationEnrichmentRecord` rows through migration `0014_authorization_enrichments` and
  projects them into InvestigationContext/Web/TUI/Lead Agent. `soc context enrich` and
  `soc context enrichment list|get|replay` use `SocAuthorizationEnrichmentService`; records remain
  `shadow_only` with no decision impact. DP-01 uses `SocDispositionProposalService` to persist
  append-only `SocDispositionProposalRecord` rows through migration `0015_disposition_proposals`.
  Only an exact enrichment linked to an open ReviewQueue plus current `true_positive` detection truth may propose
  `closed_benign_true_positive`; proposals remain shadow-only, require human review, and cannot
  mutate the run, close ReviewQueue, or authorize an action. EV-01 uses
  `SocDispositionEvaluationService` plus append-only sample manifests/outcomes through migration
  `0016_disposition_evaluation`. `soc disposition sample|outcome|evaluate` supports reproducible
  hash-ranked sampling, explicit superseding labels, and read-only precision/override/freshness/fan-out
  gates. EV-02 routes authenticated API/Web labels, Review TUI `/outcome` and `/sample-outcome`, and
  eligible trusted external-disposition labels through that same service. Labels are never inferred from
  `close_reason`. EV-03 derives a paginated sample-review inbox from immutable manifests plus current
  proposal/ReviewQueue/outcome records; the Web campaign view can open only manifest-selected work and
  still writes through EV-02. A passed report is only eligible for governed rollout review and
  `auto_close_allowed` remains false.
- PingAn `zeusRawLogs[].message` values are parsed only inside the PingAn normalizer. If at least one
  message parses deterministically, parsed fields are the only analysis source: Zeus sibling fields
  remain in immutable raw evidence and cannot enter canonical mapping, role/scenario facts, conflicts,
  or LLM evidence. Structured fallback is allowed only when zero messages parse. PingAn
  `T_GBD_zeus_data` is the sole current exact-topic exception: its first structured event is
  high-trust fallback evidence; every other PingAn structured fallback defaults to low trust.
  Source type, missing `message`, similar topic names, and topic prefixes do not grant this
  exception. Preserve the complete original payload for replay/audit. The first parsed message plus
  at most four full supplementary messages enter bounded evidence; exact-path, adapter-declared
  high-value fields outside that budget may enter generic `BoundedEvidenceHighlight` records under
  the same sensitive-evidence mode. Repeated highlights expose at most five representative paths;
  complete path accounting remains in `EvidenceCoverageReport`. They must never reopen structured
  fallback. Adapter fields with `participates_in_reasoning=false` are hard-filtered from model
  projections while remaining in immutable raw/audit evidence.
- PingAn Threat Intel mapping lives in `normalizers/pingan_threat_intel.py`: nested `net.*` is the
  observed wire session, while `attacker` / `victim` remain separate provider role assertions.
  `assets.ip` may be a CIDR/range scope and must not become a host IP; provider `result`, reputation,
  severity and score fields are typed source semantics, not Runtime truth/confidence.
- PingAn trusted structured SIEM mapping lives in `normalizers/pingan_siem.py`. Only reviewed
  subtypes are projected: `suspicious_email` emits typed email entities and
  `standard_machine_copy` emits host candidates without inventing network direction. Upstream model
  narratives/scores and pipeline identities are not analyst truth or event actors; unknown subtypes
  stay bounded evidence and produce mapping gaps rather than guessed entities.
- PingAn EDR nested `detailsN` handling lives in `backend/soc_agent/normalizers/pingan_edr.py`.
  Keep one canonical endpoint/process/file summary and preserve each usable detail as an exact-path
  process/file observation. `iplist`, `str_source_ip`, and `device__ip` are endpoint identity and
  impacted-host evidence, not network source/destination. `str_attack_ip` may become only a
  validated, non-endpoint vendor attacker/peer candidate; endpoint exclusion must compare parsed
  message and structured fallback identities within the same raw-event observation scope.
  `str_threat_value` and `str_activity_id`
  are polymorphic vendor values and must not become network endpoints or hashes by string shape.
  Without an explicit directional connection contract, canonical EDR source/destination and network
  observations remain empty. Malformed process hashes do not enter entities. Child process, file,
  registry, task, existence and MITRE fields remain typed investigation context and never prove
  maliciousness or success by themselves.
- PingAn NDR/APT mapping lives in `backend/soc_agent/normalizers/pingan_ndr.py`. Every parsed
  `sip/dip` message remains an independent wire observation; HTTP and network-content file metadata
  remain per-message observations. The reviewed source's `ioc` field is a vendor detection
  descriptor, not a typed IOC. `file_name/file_md5` never prove an endpoint write or compromise.
  Reviewed `rule_name`, `attack_type`, `host_state`, and `rule_labels` values are emitted as generic
  provider detection assertions by this adapter. They are trusted upstream assertions when present
  in selected high-trust bounded evidence, but the adapter must not directly set Runtime verdicts.
- PingAn HIDS mapping lives in `backend/soc_agent/normalizers/pingan_hids.py`. Endpoint identity is
  separate from packet direction, `external_ip=1.1.1.1` is a non-reasoning vendor placeholder, and
  process/file evidence remains per-message. Only explicit `bounce_shell`, `honeypot`, and
  `malic_opera` contracts create event-scoped network observations; canonical source/destination
  remain empty.
- Nested JSON-in-string and HTTP fields use allowlisted, size-bounded decoders. Raw bodies, headers,
  tokens, cookies, and credentials default to redaction before `BoundedAnalysisEvidence`. An
  explicitly approved model environment may set `SOC_LLM_SENSITIVE_EVIDENCE_MODE=full`; this mode
  must be visible in the evidence contract and audit, must preserve selected values unchanged, and
  must never become the generic deployment default.
- Nested decode failure keeps the original string plus a warning. Conservatively validated repair
  may enter a separately labeled `repaired_fields` projection, but never strict `decoded_fields` or
  source fact. `MessageSchemaObservation` and accepted-baseline fingerprints expose parser drift,
  while `EvidenceCoverageReport` exposes parsed fields that were used, sanitized, omitted, or left
  outside canonical/fact/scenario mappings.
- `MessageSchemaObservation.recognized` means the outer message parser succeeded; nested decode/repair
  warnings do not turn the whole message schema into degraded. Under `soc.decision_policy.v3`, encoded
  compaction alone is informational, routine bounded omission/truncation without a high-value gap is
  at most partial, degraded/unsupported outer schema or high-value/ungrounded evidence is degraded,
  and fact conflicts remain conflicted. The old truncation review reason is historical compatibility.
- Persisted CLI/Kafka analysis injects `SocNormalizationMaintenanceService` after normal business
  writes. It creates deduplicated baseline/schema/coverage maintenance issues without changing the
  verdict. Baselines require explicit engineer/admin acceptance; mapping suggestions and confidence
  calibration are offline-only and never auto-apply. Operators use `soc normalize issues`, Review TUI
  `/normalization`, or `/workspace/soc/normalization`.
- Vendor aliases stop in source adapters. Adapters emit generic `RoleClaim` / `ScenarioSignal`
  contracts; generic fact reconstruction must not recognize PingAn field names, assume
  attacker=source or victim=destination, choose a response target, or enable automation.
- Generic extraction keeps `ExtractedEntities.hosts` and `.assets` separate: only `EntityKind.HOST`
  enters hosts, while asset IDs/groups enter assets. Skill routing uses canonical typed HTTP/email,
  network and endpoint evidence; an arbitrary IP, file value or business asset name must not imply a
  network session or endpoint event.
- SOC Lead Agent work must reuse DeerFlow's existing `lead_agent` custom-agent mechanism
  wherever possible. Do not create a second SOC LangGraph runtime unless a future design
  explicitly proves DeerFlow's custom-agent/profile/skills/MCP path cannot satisfy the need.
  Current SOC profile/chat/skill helpers live in `backend/soc_agent/lead_agent.py`,
  `backend/soc_agent/agent_profile.py`, `backend/soc_agent/lead_agent_chat.py`,
  and `backend/soc_agent/skills.py`; DeerFlow-loadable SOC skills live under
  `skills/public/soc-*`.
- Runtime `SocSkillContext.v2` is a deterministic, bounded projection from those same public Skill
  packages. `SocSkillResolver` selects allowlisted package names; `build_soc_skill_context()` validates
  packages with DeerFlow's parser and projects only `references/runtime-guidance.md` with package and
  guidance hashes/token accounting. Do not restore a separate hardcoded summary table or inject full
  `SKILL.md`/legacy prompts into every analysis call.
- Read-only SOC action results such as `asset.lookup` / `asset.locate` are investigation
  evidence, not memory or verdict changes. They must flow through `InvestigationEvidence`
  and `InvestigationEvidenceRepository`, then re-enter analyst/Lead Agent context through
  `SocReviewService.get_investigation_context()`; do not let entry layers write evidence
  or mutate decisions directly.
- `endpoint.process_tree.lookup` is currently a read-only in-memory/mock EDR investigation
  adapter used to validate process-tree evidence flow before real EDR MCP credentials exist;
  do not treat it as production EDR integration.
- Phase 2 correlation is an explicit service bridge, not a hidden Runtime node.
  `SocAnalysisService` and `SocCorrelationService` must share the same
  `AlertSummaryRepository`; `CorrelationResult` enters `UnifiedInvestigationReport` and
  `SocDomainTriageRequest` as typed data. Metadata counts are projections only. Reusable
  evidence is loaded by the matched historical `run_id` and cannot change the Runtime
  decision, close ReviewQueue, confirm memory, or suppress an alert.
- Correlation scoring carries an explicit `scoring_policy_version`. Validate changes with
  `soc eval correlation`, whose labels distinguish `same_incident`, `related_distinct`, and
  `unrelated`. Retrieval metrics and offline duplicate-identity metrics are separate; an
  evaluation threshold is never a production suppression rule. `shadow_dedup_allowed`
  remains false until a later governed rollout explicitly changes that boundary.
- The browser-first Boss Demo uses the isolated local database
  `backend/.deer-flow/data/soc_boss_demo.db`. Run `./scripts/soc-boss-demo.sh start --reset`
  for deterministic rehearsal or add `--analyzer-mode llm --model-name NAME` for an explicit
  live-model run. If Docker is unavailable in WSL, start Docker Desktop, wait for Engine Ready,
  and enable the current distribution under WSL Integration.
- Reproduce local SOC Runtime review artifacts with `./scripts/soc-runtime-validation.sh
  core|live|evaluations|finalize|all`. Outputs under
  `backend/.deer-flow/soc-runtime-validation/` contain real-alert-derived data, are gitignored,
  and must not be committed. Steps 7 and 9-12 are maintenance/evaluation/governance tracks,
  not extra fixed Runtime nodes. A rejected LLM evidence citation is safe only when decision
  policy forces degraded evidence, human review, and `automation_allowed=false`.
- Reproduce deterministic Checkpoint D0-D6 with
  `./scripts/soc-runtime-validation.sh checkpoint-d`; run the explicit-cost live D7 boundary with
  `./scripts/soc-runtime-validation.sh checkpoint-d-live`, then run deterministic D8 with
  `./scripts/soc-runtime-validation.sh checkpoint-d-grounding` and deterministic D9 with
  `./scripts/soc-runtime-validation.sh checkpoint-d-decision`; run the explicit-cost live D10 with
  `./scripts/soc-runtime-validation.sh checkpoint-d-cross-source`. D5 validates one sample's bounded
  Skill package projection, D6 is an offline 212-row route/package coverage audit, D7 validates one
  real model's typed Analyzer output, D8 applies production Grounding, and D9 applies production
  Decision Policy without another model call, tenant disposition reconciliation or persistence.
  D10 replays one representative per known topic plus every D0 known input gap through the configured
  real model and complete production Runtime; it records model/parser provenance, token usage,
  Grounding and Decision guards, but is not a model-accuracy evaluation without human labels. D6-D10
  are not additional Runtime nodes. Run deterministic D11 with
  `./scripts/soc-runtime-validation.sh checkpoint-d-full-corpus`: it executes every D0 row twice
  through the non-persistent stub Runtime, compares semantic outputs while excluding run IDs,
  timestamps, durations and ingestion-only `received_at`, and must not call an LLM, DB, MCP, tenant
  policy or action. D11 is a 212-row compatibility/reexecution-stability gate, not model evaluation or
  persisted `SocAnalysisService.replay(run_id)`. A blocked D8 must
  become degraded/conflicted evidence, human review and `automation_allowed=false` in D9. An input
  with no bounded raw/highlight or provenance-backed canonical/fact/scenario evidence must emit the
  vendor-neutral critical `analysis_evidence.unavailable` coverage gap and fail closed.
- Tenant environment exemptions such as a PingAn rule for confirmed `dev/local/staging` assets are
  versioned tenant disposition policy, not detection truth, LLM memory or a Runtime short-circuit.
  Vendor adapters emit only provenance-backed generic context candidates; governed resolution and
  tenant policy reconciliation happen after the full detection Runtime. Generic core code must not
  contain `tenant == pingan` or hostname-substring safety branches, and rollout starts shadow-only.
- Build the local real-alert validation corpus with
  `backend/.venv/bin/python validation/compact_zeus/corpus/build_alert_validation_corpus.py`.
  The authoritative source PKL lives under `datas/source/`; exact JSON demos under
  `datas/legacy_demos/` add lineage, conflicts remain explicit variants, and missing demos append
  one canonical row. Generated outputs stay under gitignored `validation/compact_zeus/data/`, split
  into `corpus/`, `audits/`, `reviews/`, `compaction/`, and `exploration/`. Raw inputs, generated
  PKL/manifest and rich HTML/Excel outputs are sensitive and gitignored.
  Historical `agent_response` values are model output, not analyst ground truth.
- Reproduce the release-level local Alpha gate with `./scripts/soc-alpha-acceptance.sh all`.
  It covers representative APT/EDR/HIDS across CLI, SQL, registered Gateway handlers/services,
  real local Kafka protocol, Review Web regression, feedback, audit and replay, then writes
  `backend/.deer-flow/soc-alpha-acceptance/alpha-acceptance-report.json`. The output is gitignored.
  A pass is local/test Alpha evidence only: deterministic analyzer, SQLite, mock investigation
  providers, local Redpanda and mocked browser transport remain explicitly disclosed. See
  `.notes/ai_soc/alpha-acceptance-runbook.md`.
- Package the Stage 3 exit evidence with `./scripts/soc-alpha-readiness.sh all`. It reuses the
  acceptance report, runs the full SOC backend and architecture/migration gates, and writes the
  gitignored `soc.alpha_readiness_report.v1`. A technical pass keeps
  `release_decision=pending_owner_review`, `stage_transition_allowed=false`, and
  `production_ready=false`; see `.notes/ai_soc/alpha-readiness-package.md`.

SOC delivery plan (the only execution order is `.notes/ai_soc/delivery-roadmap.md`):

| Stage | Current status and goal |
| --- | --- |
| `BD` Boss Demo v0.1 | Done: browser-first repeatable golden path |
| `AA` SOC Alpha Completeness Audit | Done: unique 50-row matrix and frozen blocker set |
| `BG` Close Blocking Gaps | Done: Alpha Gate passed 2026-07-20 |
| `PI` Real Data & Production Integration | Current: `PI-01` real provider and approved-payload intake in progress |

### SOC Agent Development Workflow

SOC Agent work is a long-running product and engineering effort. Do not start from code
search alone. Use this order:

1. Read the current plan in `.notes/ai_soc/` and the relevant engineering/tooling
   contracts in `.notes/reference-index/`.
2. Derive the smallest Phase-aligned implementation slice from those docs.
3. Use CodeGraph after the slice is clear, to verify DeerFlow code locations,
   reusable APIs, and low-intrusion integration points.
   Default to CodeGraph and direct source reads for both local and cross-project
   reference work. Do not run Understand Anything as part of the normal workflow; it is
   token-heavy and currently optional. The repository root `.understand-anything` and
   reference-project `.understand-anything` directories are static snapshots only: do
   not update them. Use Understand only when the user explicitly asks for it, and then
   verify any result with CodeGraph/source before using it as a design fact.
4. Implement SOC-specific behavior as incremental modules/adapters first; avoid changing
   upstream DeerFlow core unless a small generic extension point is required.
5. If the slice changes product direction, runtime pipeline, contract semantics, phase
   scope, or next-step sequencing, update `.notes/ai_soc/soc-agent-solution.md` in the
   same change set; keep `.notes/reference-index/soc-agent-engineering-contracts.md`
   aligned for engineering rules.
6. After code changes, run `codegraph sync .` from the repo root so the local
   CodeGraph index includes newly added or edited SOC symbols before the next slice.
7. Update `.notes/ai_soc/progress.md` after each completed slice with status, changed
   files, verification, and next step.

Progress is not tracked in chat history. The durable task ledger is
`.notes/ai_soc/progress.md`; keep it current whenever SOC Agent work advances.

## Notes Entry Points

[.notes/README.md](.notes/README.md) is the notes index. Main active docs:

| Path | Purpose |
| --- | --- |
| `.notes/ai_soc/soc-agent-solution.md` | Current SOC Agent design |
| `.notes/ai_soc/progress.md` | Durable SOC Agent progress ledger |
| `.notes/ai_soc/alpha-acceptance-runbook.md` | One-command local Alpha acceptance and evidence boundaries |
| `.notes/ai_soc/alpha-readiness-package.md` | BG-03 technical gate, deployment/rollback and Stage 4 handoff review |
| `.notes/ai_soc/audits/alpha-completeness-matrix.md` | Unique capability status and closed/open blocker register |
| `.notes/reference-index/soc-agent-engineering-contracts.md` | Engineering contracts: style, API, events, Kafka, permissions, tests |
| `.notes/reference/cross-project-workflow.md` | Cross-project reference workflow |
| `.notes/research/hermes-vs-deerflow-agent-patterns.md` | Referenced Claude Code/Hermes design-pattern research |
| `.notes/archive/` | Historical or low-frequency notes; not development source of truth |

## Reference Projects

Reference projects are read-only. Use CodeGraph and minimal source reads when consulting them;
do not directly modify files in those projects.

| Project | Path | Use |
| --- | --- | --- |
| claude-code-sourcemap | `/home/yydspei/projects/claude-code-sourcemap` | Claude Code source and agent architecture patterns |
| claude-mem | `/home/yydspei/projects/claude-mem` | Memory-system implementation reference |
| hermes-agent | `/home/yydspei/projects/hermes-agent` | Hermes Agent framework and interaction patterns |
| openclaw | `/home/yydspei/projects/openclaw` | Personal AI assistant and multi-platform agent reference |

Cross-project rules:

- Prefer CodeGraph for architecture lookup, exact symbol/function/class lookup, callers,
  callees, and impact analysis.
- Use Understand Anything only when the user explicitly requests it; treat existing
  `.understand-anything` graphs as static snapshots and do not update them.
- Consult reference projects only when the current slice needs a design pattern
  that is not already settled locally, such as memory lifecycle, approval policy,
  multi-agent orchestration, stream protocol, context compaction, or tool runtime.
- Record reusable findings in `.notes/reference-index/` with the question, project,
  concrete file/symbol, and what was reused or rejected.
- Re-implement design ideas in this repo; do not copy reference-project code directly.
