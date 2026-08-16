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
It compresses HTML and configured textual assets, while deliberately leaving SSE,
fonts, images, audio, and video uncompressed at the proxy layer.

Both compose files publish that entry as `"${BIND_HOST:-127.0.0.1}:${PORT:-2026}:2026"`
— **loopback by default**, matching the README's documented deployment model. A bare
`"${PORT}:2026"` binds `0.0.0.0`, which does not.
Nginx itself listens `default_server` on IPv4+IPv6 and the
Gateway binds `0.0.0.0:8001` inside the container on purpose — both are container-
internal; the published nginx port is the entire external surface, and the Gateway's
`8001` is deliberately not published. Any new published port needs an explicit bind
address; `backend/tests/test_compose_default_bind_host.py` pins this for every service
in both compose files.

## Repository Map

```text
deer-flow/
├── Makefile                        # Root orchestration: drives the full stack
├── config.example.yaml             # Template -> copy to config.yaml
├── extensions_config.example.json  # Template -> copy to extensions_config.json
├── backend/                        # Python backend — see backend/AGENTS.md
│   ├── Makefile                    # Per-module backend commands (dev, gateway, test, lint, migrate-rev)
│   ├── packages/extension-api/     # deerflow-extension-api package (import: deerflow_extension_api.*) — public extension contract
│   ├── packages/harness/           # deerflow-harness package (import: deerflow.*) — agent framework
│   └── app/                        # FastAPI Gateway + IM channels (import: app.*)
├── frontend/                       # Next.js frontend (pnpm) — see frontend/AGENTS.md
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

Third-party extensions are loaded from a top-level `plugins:` list in `config.yaml`
(operator-controlled on purpose — that list causes code to be imported, so it is deliberately
kept out of the API-writable `extensions_config.json`). See the Extension System section in
[backend/AGENTS.md](backend/AGENTS.md).

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
- `.agents/skills/soc-product-manager/` is the repository-scoped product workflow for
  SOC feature value, MVP scope, analyst journeys, acceptance criteria, metrics, and
  roadmap tradeoffs. Keep generic reusable PM skills user-global; keep this SOC overlay
  in the repository because it depends on project-owned plans and terminology.
- `.agents/skills/soc-architecture-reviewer/` is the repository-scoped architecture
  review workflow for SOC boundary changes. Use it for new services, protocols,
  schemas, persistence, Runtime/Agent control flow, middleware, integrations, or
  cross-module reliability and authority decisions. It reads current authoritative
  docs and code on demand; it does not own roadmap status or duplicate architecture facts.

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

Docker log and restart commands resolve `DEER_FLOW_ROOT` from the current
checkout before invoking Compose, matching the start and stop commands.

Run `make help` for the full list.

**Per-module commands drive a single module** (run inside that module):

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
- PostgreSQL is the production/staging SOC business store. When DeerFlow uses
  `database.backend: sqlite`, SOC persistence resolves automatically to the separate
  `{database.sqlite_dir}/soc_agent_dev.db`; explicit `--database-url` and `SOC_DATABASE_URL`
  still take precedence. Local tests, demos, Runtime validation and Alpha acceptance may use
  other purpose-specific isolated SQLite files under `backend/.deer-flow/`; never reuse
  DeerFlow's `deerflow.db` or claim SQLite evidence as PostgreSQL production evidence.
- SOC persistence code lives under `backend/soc_agent/db/` and implements repository
  protocols from `backend/soc_agent/protocols.py`; keep it separate from DeerFlow harness
  persistence unless a generic upstream extension point is genuinely needed.
- SOC schema migrations live under `backend/soc_agent/db/migrations/` and are applied with
  `soc db upgrade`; the migration version table is `soc_alembic_version`.
- PingAn DEV dependencies do not block the product-completion track. When live internal access is
  unavailable, use only explicit fake/mock configurations whose outputs retain `mocked=true`, finish
  the frozen product workflow, and record the corresponding `mocked=false` acceptance as separate
  Real Integration Debt. A simulated pass never closes a real Provider, infrastructure, label-quality,
  pilot, or production gate; an unfrozen source contract remains data-gated rather than invented.
- PingAn Agent Platform ownership workflows use the self-contained HTTP client in
  `backend/soc_agent/integrations/pingan/agent_workflow.py`; do not depend on the legacy
  `model.agent_platform` package or inject legacy import roots. Internal Apple Silicon handoff uses a
  separate offline CPython/uv/lock-cache bundle generated by
  `scripts/build_pingan_macos_offline_bundle.py`; checkout paths are resolved at runtime, while
  credentials remain only in the private overlay/environment.
  The private-overlay builder must reject stale legacy import keys, placeholders, fixed `/Users/...`
  paths, or permissive local-config modes before writing any archive.
  For the three reviewed ownership workflows, the legacy source is the current PingAn protocol
  authority: `message.by` is fixed inside the PingAn adapter as `WANGWENBIN520`, not supplied by an
  environment variable. `soc_pingan_prepare_legacy_workflow_profile.py` may statically extract the
  sole legacy `YHSYS` PRD profile into the Git-ignored mode-`0600` env before private-overlay freeze;
  it must never import/execute the old package or print/hash the secret. PRD construction and live
  execution keep their separate explicit confirmation guards.
- PingAn internal model connectivity is accepted only through the loopback
  `backend/scripts/soc_pingan_litellm_smoke.py` chat-completion smoke; its report must never retain the
  API key, fixed prompt response, or business data. The first install remains offline. The optional
  `backend/samples/pingan_dev/uv-index.env.example` is scoped to later intranet dependency maintenance
  and must not become the repository's default package index or rewrite the canonical lock unnoticed.
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
  Local live validation uses `globalai-deepseek-v4-flash-0731` when `SOC_VALIDATION_MODEL` is unset;
  the fixed-cohort entry disables reasoning by default for relay latency and pins conditional role verification to
  `globalai-deepseek-v4-pro`. Saved artifacts retain the model and reasoning provenance from the run
  that produced them; historical artifacts are never relabeled.
- The current first configured model is `globalai-deepseek-v4-flash-0731`, so unpinned SOC Lead Agent
  runs inherit GlobalAI Flash. Web requests may select another registered model, and `soc chat tui --lead-agent
  --model-name NAME` provides the same explicit per-run override for the SOC TUI.
- SOC model calls have independent process-local admission controls (`SOC_LLM_MAX_CONCURRENCY`,
  optional requests-per-minute, admission timeout, and `SOC_LLM_CALL_TIMEOUT_SECONDS`). Analyzer evidence is deterministically
  grounded against the exact bounded prompt projection before `SocDecisionPolicy` runs. Reasoning is
  opt-in at the generic settings layer through `SOC_LLM_THINKING_ENABLED`; model-call metadata records
  both `thinking_enabled_requested` and whether reasoning content was observable in the provider
  response, because an intranet-compatible gateway may honor reasoning without returning it.
  Provider JSON-object mode is separately opt-in through `SOC_LLM_JSON_MODE_ENABLED`; it defaults off
  for intranet compatibility and every call records `json_mode_requested`. JSON-object mode guarantees
  neither the complete SOC schema nor semantic validity, so parser/schema/domain validation and the
  single bounded core-repair path remain mandatory.
- Fixed-cohort SOC validation reports model use by independent lane: primary analysis, optional role
  verifier, and tenant-policy advisor. A logical verifier review may contain many atomic `RC-*`
  claims and may use an additional bounded output-repair provider invocation; these counts must not
  be conflated. Missing provider usage makes the aggregate a measured lower bound. Monetary cost and
  model accuracy remain `not_measured` until a reviewed price table and independent human truth labels
  exist. E2E knowledge-review artifacts are not database Memory and are never auto-promoted.
- New live analyzer responses use the compact provider-owned
  `soc.analysis_model_output.v4`; Runtime hydrates the accepted result into internal
  `soc.analysis_result.v4`. Before the model call, Runtime builds a
  vendor-neutral `EvidenceCompactionReport` from canonical typed observations. Repeated messages are
  represented as stable facts, value-frequency distributions, and correlated behavior profiles;
  dominant and rare profiles choose the bounded full-message representatives. Raw payloads, parsed
  messages and exact provenance stay unchanged for audit/replay. Never revert this to source-order
  first-N selection or combine independent value distributions into an invented event.
  Runtime then builds a
  replay-stable current-alert fact catalog (`E-*`) and governed context catalogs: Skill (`S-*`),
  adapter contract (`A-*`), confirmed memory (`M-*`), governed context (`C-*`), and tool result
  (`T-*`). The model-owned core returns only verdict/confidence/summary/reason/action plus request-local
  aliases such as `E-001` and optional `S/A/M/C/T-001`. Runtime restores each alias through the frozen
  one-to-one map before validation, then materializes exact stable references, path/value evidence and
  the core reasoning item `R-00`; the model never copies those tuples or their long hash IDs. Stable
  IDs remain the only persisted/Grounding/replay identity. Exact alias restoration is hydration rather
  than repair; an unknown alias still fails. Optional detailed
  reasoning, scenario, direction, role and guidance blocks are independently validated.
  `reference_catalogs.role_entities` exposes only Runtime-typed canonical/extracted entities; raw
  vendor field names and ports remain ordinary evidence. `soc-analysis-v34` /
  `soc-analysis-json-parser-v24` reject unresolved or ambiguous references.
  The v34 prompt keeps stable trust/method/reference rules in the system message, places bounded alert
  context before the task, and ends the user message with the exact response shape and final checklist.
  It requires complete scenario/direction/role section shapes and exact role-catalog alias mapping;
  it selects exactly one machine-validated complete synthetic example (`network_roles`, `non_network`,
  or `conflicted`) and records `prompt_example_id`. Example-only `EX-*` references must never enter a
  model response, and example conclusions are shape-only rather than current evidence. Do not add
  partial pseudo-examples that omit required fields or inject all examples.
  The parser may perform only auditable mechanical repairs: restore a missing top-level
  `soc.analysis_model_output.v4` version only when the complete field set is unambiguously the compact
  model-owned contract, map
  an exact path/value to its unique `E-*`, materialize a valid cited catalog fact, remove an exact
  duplicate fact/reference, normalize a strict JSON boolean string, remove an explicit empty context
  sentinel, normalize a strict decimal confidence string, retain/deduplicate/bound exact
  catalog-backed core or optional references, use an explicit `scenario_key` as a missing display
  name, mark missing/invalid model scenario provenance conservatively as `inferred`, materialize a
  missing optional rationale from that object's already explicit fields, copy an already valid
  `reason` verbatim into a missing/empty display-only `summary` when it fits the summary bound, derive a role entity only
  when its cited typed facts collapse to one unique value, or discard one malformed
  optional item/section without changing the accepted core. A generic event ID is not a
  typed attacker/victim entity, and ambiguous candidates are never guessed.
  Direction/role objects may directly cite exact request-catalog context IDs without duplicating them
  in `R-*`; dangling context IDs still fail. It must not infer security semantics.
  `LLMAnalysisRequest.v6` also carries Runtime-owned `role_coherence`: a scenario-specific consistency
  check between semantic roles and observed network roles. A coherent reverse-connection mapping
  prevents the model from inventing an attacker/victim conflict merely because duplicate PCAP/CMDB/
  endpoint corroboration is absent; exact contradictory values still produce a conflicted assessment.
  High-trust message highlights preserve their reviewed source trust when compaction projects them into
  `E-*`. Neither mechanism proves compromise, changes the verdict, or grants action authority.
  A primary or verifier model node may make at most one separately journaled output-repair call under
  `SOC_LLM_OUTPUT_RETRY_ATTEMPTS=1`. A valid primary core is never retried merely because an optional
  item or section is malformed: Runtime retains valid sections, substitutes inert defaults, records
  `analysis_output_quality=degraded`, and lets `AnalysisMaterialityReport.v1` block only dependent
  capabilities. Only an invalid core can consume the bounded repair call; an irrecoverable core uses
  the deterministic stub as an explicit fail-closed result. Provider transport/capacity failures
  remain retryable Runtime failures.
  For primary analysis, `SOC_LLM_OUTPUT_FALLBACK_MODEL` may select a stronger registered repair model.
  Every repair receives
  only the invalid candidate or section, validation error, allowed catalogs and response schema, not
  raw vendor input, and cannot add security facts.
- `AnalysisResult.v4` separates observed wire flow, organization-boundary direction and semantic
  roles. Runtime derives action-specific targets only from accepted typed roles plus governed policy;
  the current compact model contract no longer emits response-target proposals. Never equate source with attacker or
  destination with victim globally. A derived target never grants action authority. Human role confirmation is an
  append-only `RoleAdjudicationRevisionRecord` through `SocReviewService`, not a model-output rewrite.
- Trust a source field only within its reviewed adapter-declared meaning. An exact
  `provider_reported_session_initiator|responder` semantic is sufficient for that scoped upstream
  session fact without duplicate SYN/PCAP proof, unless the current alert explicitly reports an
  ambiguous direction, proxy/NAT/forwarding leg, or same-observation contradiction. It never implies
  attacker/victim identity, compromise, verdict, response target, or action authority. Generic Runtime
  code must not infer this contract from names such as `source_ip`, `sip`, `src`, or `client_ip`.
- Alert admission is a trusted scoped fact: the configured upstream rule/detector/model matched and
  emitted the alert. Runtime and the bounded analyzer must still decide the current scenario,
  direction, semantic roles, attempt/effect/impact stage, verdict and recommendation. Optional
  CMDB/PCAP/TI/endpoint/history enrichment may improve scope or response targeting, but its absence
  alone must not erase the detector hit, suppress a current conclusion, or force ReviewQueue.
  `soc.analysis_materiality.v1` runs after Grounding/optional role verification and before
  `soc.decision_policy.v7`. It distinguishes an unusable core, unresolved decision-level conflict,
  and core-reference failure from optional section defects or target ambiguity. The former create
  review; the latter preserve the verdict and block only scenario routing, direction, typed targeting,
  or response execution as applicable. `suspicious`, `false_positive`, raw model confidence and
  uncalibrated confidence are diagnostic values, not standalone review reasons. Base Runtime still
  sets `automation_allowed=false`; governed post-Runtime policy separately authorizes and executes
  actions, with human approval only where that policy requires it.
- Conditional second-pass role verification is a default-off Runtime node controlled by
  `SOC_ROLE_VERIFIER_ENABLED`. Trigger policy v2 reviews up to four atomic network-direction fields
  (`observed_flow`, `boundary_direction`, `semantic_direction`, `connection_initiator`) plus
  non-placeholder attacker/victim claims. Inferred/tentative state, generic evidence gaps,
  intermediaries, response-target proposals and confidence alone never trigger a second call;
  confidence is only diagnostic after a core conflict/indeterminate state, upstream role conflict,
  or core-reference Grounding failure has already triggered. The narrow
  `soc-role-verification-v4` Prompt receives only those `RC-ND-01..04` and attacker/victim `RC-R-*`
  claims plus a claim-relevant subset of the frozen catalogs; the verifier never sees raw vendor
  payload, first-pass rationale, or confidence. It must independently return
  `supported|challenged|unresolved` with polarity-specific exact `E-*` and `S/A/M/C/T-*` references
  plus an explicit counterevidence assessment. Typed governed `network_scope` may establish matched
  organization ownership, but provider GeoIP/address-location enrichment cannot override it or prove
  roles, verdict, or action authority. When both canonical endpoints carry the typed
  `network_scope_membership=organization_controlled` marker, Runtime exposes an
  `internal_to_internal` boundary constraint and rejects a verifier status/alternative/reference set
  that contradicts it; this invariant remains limited to organization-boundary direction.
  A `challenged` result adds a fail-closed Decision review guard under `soc.decision_policy.v7`.
  `unresolved` or provider/parser `unavailable` preserves a usable first-pass conclusion but blocks
  direction and semantic-target capabilities; confirmation never authorizes an action or removes
  another review reason. `SOC_ROLE_VERIFIER_MODEL` may select a stronger
  configured model; otherwise the primary model is reused and that lineage is explicit. Each provider
  invocation has its own ordered `AnalysisRequestJournal`, while `request_journal` remains the active/
  latest recovery pointer. A configured verifier run uses `pipeline_version=soc-runtime-v8` even when
  its gate does not trigger; the default verifier-free pipeline uses `soc-runtime-v7`.
  The 2026-08-12 fixed ten-alert v2 live baseline triggered 5/10 alerts and sent 14 actual claims in
  five provider calls, down from the historical v1 10/10 trigger rate. Gate-projected candidates for
  non-triggered alerts are audit material and must not be counted as reviewed claims. This remains a
  structural/safety baseline until independent analyst direction/role labels are recorded.
- Every Runtime step records start/end/duration and `AnalysisRun.total_duration_ms` records fixed-
  pipeline wall time. Provider-complete token usage is `reported`; missing intranet usage is
  explicitly `estimated` from visible request/response content, and partial provider usage completed
  locally is `mixed`. Monetary cost remains unmeasured without a reviewed price table.
- Reviewed tenant-static knowledge is selected through strict versioned profiles and projected as
  bounded, source-linked `C-*` with no decision authority. Generic method belongs in `S-*`, adapter
  semantics in `A-*`, confirmed historical experience in `M-*`, live tool results in `T-*`, and
  disposition/action rules in their separate policy layers. Dynamic authorization, exercise and
  maintenance facts still use the governed-context fact lifecycle.
- Persisted Runtime resolves confirmed Memory after Skill selection and before reference-catalog
  finalization/provider journaling. It queries only through `SocMemoryService`, projects at most the
  bounded retrieval result as `M-*`, and treats retrieval failure as non-blocking. Only confirmed,
  explicitly retrieval-enabled, validity-current and review-current records are eligible; alert/run
  IDs are lineage metadata rather than match facets. SQL candidate selection runs independent exact-
  facet and text lanes across the complete eligible corpus, merges them with the scoped fallback, then
  applies shared scoring and candidate/top-K budgets. Runtime defaults to
  `soc.memory_retrieval_policy.v2`, which additionally requires a memory-type-specific exact strong
  anchor before projection; source/environment/category alone cannot admit a detection lesson or benign
  pattern. It must not regress to latest-N-only retrieval.
  `M-*` is reasoning context, never `E-*`
  current evidence. Free-form Memory has no deterministic decision or action authority. Only a
  human-reviewed `SocMemoryDecisionDirective` attached during candidate confirmation may change the
  post-Runtime effective decision, and only when the exact record version/content/facets hashes, activation, validity,
  review due, minimum score, and required-facet matches all pass. It never directly authorizes an
  action.
- Single-alert correction, review-note and domain-finding sources must pass `MemoryAdmissionService`
  before a candidate is created. Admission requires an explicit human promotion/acceptance signal, a
  substantive reason and a reusable facet; otherwise the result remains `observed_only`. Alert/run IDs
  are lineage metadata and must never become candidate or query facets. Ordinary notes use the typed
  `promote_to_memory` flag; accepted Lead Agent conclusions must pass the reason gate using the human
  `acceptance_reason`, never assistant-message length or free-form metadata.
- Governed response automation is a separate default-off post-Runtime layer. `SocAutomationService`
  preserves the immutable base `AnalysisRun.decision`, writes an append-only before/after
  `SocDecisionTransitionRecord`, then evaluates a tenant/environment/version/validity-bound
  server-owned `SocAutomationPolicy`. It may produce disposition, action authorization, and action
  execution records even when no Memory matched. Automatic rules require explicit verdict,
  evidence-state, model name, prompt version, Decision Policy version, confidence, and `needs_review`
  matches plus an exact pinned idempotent write or destructive adapter. Replay runs never receive
  automatic external-action authorization. A rule that executes while `needs_review=true` must carry a separately reviewed
  `review_required_override_reason`; this does not remove the ReviewQueue or impersonate human review.
  Memory, model output, Skills, tools, or an adapter result cannot grant this authority. No policy path
  means no automation; shadow mode never authorizes; enforced execution additionally requires
  `SOC_AUTOMATION_EXECUTE_AUTHORIZED_ACTIONS=true` and an injected reviewed registry. Migration `0023`
  stores the Memory facet index and decision/disposition/authorization/execution lineage; inspect it
  with `soc automation lineage`. The existing human Approval flow remains supported, but its Alpha
  execute boundary must not be described as a completed production side effect until both authorization
  modes converge on one external execution service.
- Evidence Grounding uses `soc.analysis_evidence_grounding.v3`. It first proves each `E-*` reference,
  exact source path and typed scalar value, then verifies every `R-*` reference and governed-context
  namespace. A grounded reasoning item proves reference integrity, not that its model inference is
  literal telemetry or automatically correct. An exact visible encoded-omission marker proves only
  field presence, encoding shape and boundary omission; hidden bytes, validity and outcome remain
  ungrounded. Grounding does not re-judge whether a supported model inference is semantically correct.
  `AnalysisMaterialityReport.v1` decides whether a failed reference belongs to the decision core or
  only to an optional capability. Model-generated `K-*` knowledge candidates are inert review suggestions and must never
  directly write Memory, modify a Skill/adapter/policy, or affect the current decision.
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
- A Lead Agent response must never be persisted as memory automatically. PI-03F1 treats an analyst's
  explicit acceptance as a `review_note` source: queue/thread/message/reason lineage is required and
  the result remains `pending_review`. The non-Lead-Agent TUI cannot perform this mutation. CLI/TUI
  lineage is captured/attested provenance only. PI-03F2 Web/Gateway acceptance accepts no assistant
  body: it requires authenticated thread ownership and idempotency, then resolves the latest visible
  terminal `soc-triage` assistant message from the current server-owned checkpoint branch and records
  checkpoint/text-hash provenance. A closed ReviewQueue rejects a new source. Direct Web
  `soc-triage` runs treat `context.soc_review_queue_id` only as an identity hint: Gateway authenticates
  the actor, validates queue/run/alert/tenant lineage, atomically binds the owned DeerFlow thread to
  that queue, and rebuilds the bounded artifact through `SocReviewService` on every run. The binding
  is server-reserved and immutable; a different queue requires a new thread. The profile's review-
  context middleware injects the artifact transiently under a 48,000-character cap and stamps the
  exact context hash/lineage on the assistant message. Web acceptance must match both the thread
  binding and that message provenance, then records the accepted snapshot hash; it must not compare
  against a newly rebuilt mutable context after the acceptance mutation. Client-facing thread-state
  mutation strips this reserved provenance from submitted messages, so a manual checkpoint rewrite
  invalidates acceptance instead of forging trusted model lineage. Existing installed
  `soc-triage` profiles gain this middleware only after `soc agent install-profile --overwrite`.
  PI-03F3 persists opt-in Kafka/batch results as immutable
  `MemoryPatternObservation` rows through `SocMemoryPatternService` and migration
  `0021_memory_pattern_observations`; never create one memory candidate per alert, run, finding, or
  offset. A server-owned `SocMemoryProfileRegistry` chooses same-class, occurrence and applicability
  semantics from canonical fields; generic fallback remains vendor-neutral, while PingAn prefers
  a versioned compound of canonical detection key and behavior fingerprint when both exist. A
  detection-only PingAn cohort is rule-level context and cannot own a future verdict; behavior-only
  remains the ruleless fallback. PingAn detection keys may derive from stable rule code/name but
  never from alert/run lineage. Cohorts use canonical
  timezone-aware source event time and strict
  tenant/environment/`simulation|operational` isolation. Under
  `soc.memory_pattern_aggregation.v3`, the default fixed 24-hour UTC policy requires 5 observations,
  5 distinct sources, 5 conclusive outcomes, at least 80% risk/benign consistency, and a consensus
  strong retrieval anchor. Every alert remains an immutable observation; only a cohort that passes
  all gates creates one frozen `pending_review` pattern lesson. Conflicted, unresolved, weak-anchor,
  and low-support cohorts do not enter expert review. Candidate content must summarize applicability,
  verdict distribution, representative conclusions, and exceptions rather than copy one alert.
  A stable lesson fingerprint suppresses equivalent candidates across later fixed windows; those
  windows remain reinforcement observations. A changed risk class or strong-anchor scope is a new
  reviewable lesson, while supersession remains manual. Later observations are replay-only. Missing/naive event time or
  aggregation failure is reported without failing base Runtime processing. `soc memory patterns
  list|replay` are read-only operator surfaces; recurrence never changes verdict, enables retrieval,
  confirms memory, or authorizes an action.
- Pattern candidates and confirmed records may carry a typed `SocMemoryApplicabilitySpec`. Retrieval
  must match the server-selected profile/version/feature schema, required facets, exclusions and
  strong-anchor threshold before a reviewed directive can affect the effective decision. Every
  projected `M-*` is retained as `SocMemoryUseRecord`; final analyst correction or canonical external
  disposition creates append-only feedback and versioned health. A high-trust risk outcome that
  contradicts an active benign directive disables retrieval through the disable-only safety monitor
  and creates a revision proposal. No feedback edits a confirmed record in place, and Memory never
  grants action authority. A decision-authoritative PingAn compound record requires exact environment,
  detection key and behavior fingerprint. Same-rule behavior similarity may enter only as explicit
  `partial/context-only` `M-*` reasoning context when a reviewed canonical behavior component overlaps;
  Automation must reject its directive. Legacy records without typed applicability may remain bounded
  reasoning context, but can never apply a directive; deterministic Memory decisions additionally require
  `decision_impact=detection_decision` and an exact `applicable` projection. Reviewers may narrow applicability by promoting candidate
  optional facets to required, but cannot remove anchors, expand values or widen context-only scope.
  This prevents PRD lessons from affecting STG by broad source/category similarity. The
  operator-owned environment is bound before Memory query/profile selection; explicit batch/daemon,
  Memory, tenant-policy and automation environment settings must agree. Revision
  proposal accept/reject only resolves the review task; it never mutates or re-enables the old Memory.
  Migration `0025_memory_evolution` owns this lineage.
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
- Tenant operational handling is a default-off post-Runtime decision layer. Generic contracts/evaluation live in
  `soc_agent.contracts.tenant_policy`, `soc_agent.tenant_policy`, and
  `SocTenantPolicyEvaluationService`; migration `0022_tenant_policy_decisions` stores append-only
  `TenantPolicyDecision` rows after the main analysis transaction, while migration `0024_decision_stages`
  indexes `Base -> Memory -> Tenant Policy -> Effective` lineage. Enable it only with
  `SOC_TENANT_POLICY_ENABLED=true`, an explicit policy path, and an environment. Policy resolution uses
  alert event time; a naive vendor timestamp needs an explicit IANA timezone and records assumed
  lineage. PingAn CIDRs/host patterns/rules stay in its JSON policy pack, never generic evaluator
  code. Exact deterministic rules run first; deterministic no-match may invoke a separately enabled,
  reviewed policy Skill through `SOC_TENANT_POLICY_ADVISOR_MODE=llm` and
  `SOC_TENANT_POLICY_SKILL_PATH`. Advisor output must be strict, reference-grounded and fully hashed;
  failure persists a fail-closed no-match. Shadow policy only records a proposal; reviewed enforced
  policy may change effective review/disposition but never Runtime detection truth or action authority.
  PingAn's reviewed policy treats HTTP `200` as request success only: it does not itself escalate or
  ignore. A deterministic rule may ignore an alert only when at least one canonical `100..599` HTTP
  status exists and every canonical HTTP transaction is non-`200`; workflow, ticket, forwarding,
  suppression and disposition status fields are excluded. Exact forced-transfer codes outrank that
  rule. Adapter-normalized provider success/compromise assertions make deterministic non-`200`
  handling abstain, but do not themselves set a disposition; the bounded Policy Skill must combine
  them with current effect evidence. Explicit provider failure may be ignored, while attempt-only
  values remain subject to Runtime/Policy-Skill analysis. Exact governed authorization may produce
  `closed_benign_true_positive` while preserving technical truth. Use
  `soc tenant-policy evaluate|list|get` and `soc automation lineage` for replay and inspection.
  Generic rules may also consume versioned `TenantPolicySignal` values from explicitly injected,
  read-only providers; provider failure is recorded and fails closed. PingAn's separately gated
  `SOC_PINGAN_SOFTWARE_PATH_FAST_POLICY_ENABLED=true` provider emits an aggregate signal only when
  every canonical EDR process/executable path matches an exact `safe_paths` entry or a conservatively
  inferred safe-path family. Its reviewed enforced rule gives exact and family matches equal direct
  operational `ignored` effect while preserving Runtime detection truth and zero action authority.
- PingAn `zeusRawLogs[].message` values are parsed only inside the PingAn normalizer. If at least one
  message parses deterministically, parsed fields are the only analysis source: Zeus sibling fields
  remain in immutable raw evidence and cannot enter canonical mapping, role/scenario facts, conflicts,
  or LLM evidence. Structured fallback is allowed only when zero messages parse. PingAn
  `T_GBD_zeus_data` is the sole current exact-topic exception: its first structured event is
  high-trust fallback evidence; every other PingAn structured fallback defaults to low trust.
  Source type, missing `message`, similar topic names, and topic prefixes do not grant this
  exception. Preserve the complete original payload for replay/audit. The first parsed message stays
  primary; generic typed-observation compaction selects dominant/rare behavior profiles for at most
  four full supplementary messages instead of taking the first four by source order. Stable facts,
  varying-value frequencies, correlated profiles, occurrence counts and omission state enter
  `EvidenceCompactionReport`; exact-path, adapter-declared high-value overflow may also enter
  `BoundedEvidenceHighlight`. Complete path accounting remains in `EvidenceCoverageReport`. Neither
  projection may reopen structured fallback. Adapter fields with `participates_in_reasoning=false`
  are hard-filtered from model projections while remaining in immutable raw/audit evidence.
- PingAn normalization preserves trusted generic ingress metadata such as top-level `tenant_id` in
  canonical `AlertInput` and `LLMAnalysisRequest`. Offline batch `--default-tenant-id` may fill only a
  missing tenant; it must reject, not overwrite, a conflicting source tenant.
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
  remain per-message observations. Message-first `sip/dip` are respectively the reviewed provider-
  reported session initiator/responder for that observation; this scoped meaning does not apply to
  processed sibling fields, EDR endpoint identity, F5/SNAT half-flows, or another vendor merely because
  it uses similar names. The reviewed source's `ioc` field is a vendor detection
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
  warnings do not turn the whole message schema into degraded. Under `soc.decision_policy.v7`, encoded
  compaction alone is informational, routine bounded omission/truncation without a high-value gap is
  at most partial, and degraded/unsupported outer schema remains partial unless it creates a high-value
  gap. Only unusable decision-core references or fallback degrade the whole Decision; only critical
  unresolved fact conflicts become conflicted. Optional failures remain capability-scoped. The old
  truncation review reason is historical compatibility.
- Persisted CLI/Kafka analysis injects `SocNormalizationMaintenanceService` after normal business
  writes. It creates deduplicated baseline/schema/coverage maintenance issues without changing the
  verdict. Baselines require explicit engineer/admin acceptance; mapping suggestions and confidence
  calibration are offline-only and never auto-apply. Operators use `soc normalize issues`, Review TUI
  `/normalization`, or `/workspace/soc/normalization`.
- PI-03A label governance uses `soc eval labels prepare|seal|verify`. The corpus manifest seals the
  exact label-set/sample identity, tenant/environment, data class, reviewer summary, source/rationale
  and supersession lineage. `simulation` always means `mocked=true` and
  `real_quality_claim_allowed=false`; integrity verification is separate from review completion and
  calibration readiness, and simulation/real manifests use separate supersession chains.
- PI-03B uses `soc eval quality` to compose the existing offline Runtime/parser, scenario, correlation
  and manifest-bound confidence evaluators. Reviewed labels declare `human_review` or
  `simulation_fixture`; a real corpus rejects synthetic labels. The stable replay report may pass its
  engineering flow but always keeps real-quality claims, profile publication, rollout and automation
  disabled. `soc eval confidence` now requires `--corpus-manifest`.
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
- PI-01G SOC specialists reuse DeerFlow `subagents.custom_agents`, the native `task` tool, model
  inheritance and task events. Definitions and the explicit root-config installer
  live in `backend/soc_agent/subagents.py`; use `soc agent install-subagents` for a dry-run and
  `--apply` only after review. Profiles are capability-oriented: network, endpoint (EDR/HIDS), web and
  email. They have no tools and no dynamic Skill loader; the trusted Lead Agent middleware projects the
  matching reviewed `references/runtime-guidance.md` plus bounded ReviewQueue evidence. Delegation
  requires that server-owned case context, allows at most two distinct managed specialists per chat
  run, and records stable lineage. Stopped/capped output and action markers fail closed. Specialist
  text is advisory only and must never create evidence, change a verdict, write memory,
  approve/execute an action, or replace the fixed Runtime. PI-01G1..G3 closed AC-30 on 2026-08-07;
  real Provider and real-label quality gates remain separate debt.
- SOC Lead Agent profile v2 installs the trusted per-agent
  `SocLeadAgentApprovalMiddleware`. Standard Web/Gateway `soc-triage` runs therefore route explicit
  `<soc_action_proposal>` output through the shared SOC action policy and Approval Inbox; the embedded
  `soc chat tui --lead-agent` path keeps its existing outer `SocLeadAgentChatService` proposal bridge.
  Existing installed profiles gain this middleware only after explicit
  `soc agent install-profile --overwrite`. Model output may supply only route/action/reason/payload/
  confidence: proposal IDs, actor, source, request identity and context references are server-owned,
  stable across replay, and cannot be forged by the model. The middleware never executes high-risk
  actions. High-risk adapters must not be exposed as unrestricted DeerFlow/MCP tools; approved
  execution continues through the SOC approval/action service boundary.
- Runtime `SocSkillContext.v2` is a deterministic, bounded projection from those same public Skill
  packages. `SocSkillResolver` selects allowlisted package names; `build_soc_skill_context()` validates
  packages with DeerFlow's parser and projects only `references/runtime-guidance.md` with package and
  guidance hashes/token accounting. Do not restore a separate hardcoded summary table or inject full
  `SKILL.md`/legacy prompts into every analysis call. Source type and typed canonical evidence are
  strong routing signals. Free-text keywords are fallback signals only: ambiguous behavior words must
  not create a cross-domain Skill for a known endpoint/network/web source, while typed cross-domain
  evidence remains eligible. D6 v2 pins this with a full-corpus keyword-only cross-domain gate.
- Read-only SOC action results such as `asset.lookup` / `asset.locate` are investigation
  evidence, not memory or verdict changes. They must flow through `InvestigationEvidence`
  and `InvestigationEvidenceRepository`, then re-enter analyst/Lead Agent context through
  `SocReviewService.get_investigation_context()`; do not let entry layers write evidence
  or mutate decisions directly. New Dispatcher-created evidence copies the current
  `ServiceRequestContext.request_id/trace_id` for Action/MCP/Provider correlation.
- PI-01D automatic investigation is an application-level bridge outside the fixed Runtime.
  `SocEnrichmentPlanner` consumes only typed entity mentions, role resolutions, completed-run status,
  and an explicit versioned `SocEnrichmentPolicy`; it must not inspect vendor aliases, call a Provider,
  parse free-text scenario/gap output, or mutate the run. The default policy enables no route. V1 allows
  only exact `asset.lookup|asset.locate|threat_intel.ip_reputation.lookup|security_tag.lookup` routes,
  at most one asset route, tenant/network scope, and bounded per-route/total budgets. Planned actions
  still pass through Capability Router, Action Policy, Dispatcher, and exact Adapter Registry;
  successful results use the injected evidence repository. PI-01D1 is this contract and optional Main
  Orchestrator bridge. PI-01D2 adds default-off `soc.enrichment_composition.v1` and
  `build_soc_main_orchestrator_service()`: exact route/action/adapter ID/kind, read-only execution,
  Planner inputs, and `mock_only|runtime_declared|real_only` result provenance must validate at startup
  without discovering or invoking MCP tools. Enabled composition requires an explicit evidence
  repository. PI-01D3 adds `SocInvestigationWorkflowService` plus migration
  `0019_enrichment_executions`: it starts from an existing persisted `AnalysisRun`, stores the
  immutable plan and per-action attempts, validates every actual result against the configured
  `mock_only|runtime_declared|real_only` mode before writing deterministic
  `InvestigationEvidence`, distinguishes normal not-found from Provider failure, and supports
  bounded retry, stale-attempt recovery and linked replay without mutating the base run. Kafka
  daemon and internal PKL batch wiring are explicit opt-in through one composition plus one or more
  action-adapter config files; omitting them preserves Runtime-only behavior. Duplicate Kafka/batch
  identities reuse the durable execution instead of repeating completed Provider calls. PI-01D4 adds
  `SocInvestigationReportingService` as a pure read-only projection over that ledger plus validated
  `InvestigationEvidence`: `soc.investigation_shadow_report.v1` exposes secret-free plan/action/result,
  retry, evidence-coverage and action-attempt latency telemetry, while
  `soc.investigation_addendum.v1` exposes a deterministic analyst summary with
  `reasoning_status=not_requested` and no new conclusion. Reports are recomputable and are not stored
  in another truth table; referenced evidence must match the execution/run/alert/thread/route/action
  lineage, and its content hash participates in report identity. Provider-network latency and cost
  remain explicit `not_measured` gaps until a real source exists. Review/Web/TUI/Lead Agent context
  may display the addendum, but it cannot overwrite the base verdict, close work, confirm memory or
  authorize an action. Operators use `soc investigation get|report|replay`; replay requires a new
  idempotency key, a reason, explicit config and confirmation.
- PI-01E uses two separate outputs from
  `validation/compact_zeus/internal_batch/run_pingan_runtime_batch.py`: one Runtime-only compatibility
  batch and one explicitly persisted investigation batch over the exact same cohort. Seal each
  `5 -> 50 -> all` stage with `evaluate_pingan_shadow.py`. Every internal dependency must first pass
  `external_simulation` with the same production Provider/MCP/action code plus explicit fake transport;
  only a separate `internal_real` run may later use `mocked=false`. The evaluator performs no
  LLM/MCP/Provider call and validates source/cohort/tenant plus composition/action/extensions config
  fingerprints, deterministic pre-LLM compatibility, exact Provider modes, no PingAn `asset.lookup`,
  complete evidence, and zero base-run mutation/auto-close/confirmed-memory/high-risk flags. Its
  `soc.pingan_shadow_acceptance.v2` report is secret-free and review-gated; a simulated pass always
  retains `real_provider_evidence=false` and cannot close a real gate. Before a live investigation
  batch invokes the LLM, the runner must discover every configured `(server, tool)` through actual MCP
  `list_tools()`; static config validation alone is insufficient. Explicit resume of a persisted failed
  base `AnalysisRun` must use `SocAnalysisService.replay()` and preserve the prior run lineage, while
  completed investigation executions continue to reuse their durable identity. Missing source/wire
  contracts remain data-gated and must not be invented as fake Providers. External simulation stages 5
  and 50 have passed; the current next stage is a fresh internal-real stage 5. The 50-row fake cohort had
  no Provider hit, so each real Provider still needs separate hit/not-found/error acceptance. Internal
  operators use `run_pingan_internal_shadow.py`: default mode is static-only; live mode requires
  `--execute --confirm-live --confirm-investigation` and fail-closed orchestration of existing preflight,
  batch, migration and evaluator CLIs. Fresh live output roots must be missing or empty; resume requires
  the matching stage orchestration report. It is validation tooling, not a second Runtime.
- PingAn asset-provider code lives only under `backend/soc_agent/integrations/pingan/` and uses the
  existing generic `asset.locate` MCP/action boundary. Checkpoint D12-A is production-shaped code with
  a fake transport and must always expose `mocked=true`; it is not PA-12 or PI-01 real-provider
  evidence. Only the internal D12-B smoke with real endpoint/credentials/imports, approved payloads,
  persisted `InvestigationEvidence`, and `mocked=false` may close that gate. Internal mode must fail
  closed when configuration is missing and must never silently fall back to fake data.
  The reviewed ZEUS signer is reimplemented without legacy import-time dependencies at
  `soc_agent.integrations.pingan.zeus_signing:isec_sign`; do not restore the old module's default key
  or import all of `util.util_tools`. Use `backend/samples/pingan_dev/`,
  `soc_pingan_dev_preflight.py`, `soc_pingan_asset_direct_smoke.py`, and
  `soc_pingan_d12b_matrix.py` for D12-B. After direct/MCP semantics pass, use
  `soc_pingan_d12b_evidence.py` to route one approved successful matrix case through the MCP action
  adapter and `SocAgentActionDispatcher`, persist `InvestigationEvidence`, and read it through the
  shared Review/Lead Agent context. Real DEV values may live in verified Git-ignored `*.local`
  files. Both live runners require explicit `--confirm-live`; the matrix requires a mode-`0600`
  `*.local.yaml|yml|json` case file and an explicit report path; its aggregate report must omit raw
  query/UM values, Provider responses and override values. A provider failure is not a normal miss
  and must stop the fallback chain; only explicit `not_found` can advance from ZEUS to workflow/UM.
  The evidence runner also requires an existing open ReviewQueue item and must prove the base
  AnalysisRun and ReviewQueue item remain unchanged. Its service/context readback does not claim an
  actual Web/TUI render. A passed direct matrix does not replace MCP, persisted-evidence or
  UI/context readback evidence.
- PingAn threat-intelligence provider code lives under
  `backend/soc_agent/integrations/pingan/threat_intel.py` and uses only the generic
  `threat_intel.ip_reputation.lookup` MCP/action route. It reuses the portable ZEUS signer and shared
  App ID/App Key, requires HTTPS plus an explicit host allowlist in internal mode, and never falls
  back to fake data after an internal configuration/provider failure. Only reviewed
  `ipAnalyseReport`/`ipReputationReport` fields leave the provider; label source paths, freshness,
  response hash and omitted-field-name warnings are preserved while the full response stays private.
  Do not migrate the legacy hardcoded risk score, geo multiplier, whitelist or blocking logic; absent
  stable source semantics, provider `score`, `confidence` and `last_seen` remain unset. MCP results
  persist as investigation-only evidence and generic consumers must use the common typed
  `mcp_result` envelope rather than adding PingAn branches.
- PingAn security-tag provider code lives under
  `backend/soc_agent/integrations/pingan/security_tag.py` and uses only the generic
  `security_tag.lookup` MCP/action route. It reuses ZEUS signing/credentials but keeps
  `/public/searchTagContent` fields inside the PingAn integration. Preserve active, expired, inactive,
  conflicting, unknown, out-of-scope and unusable records; never discard them into not-found. Missing
  `expireTime` is unknown unless an explicit reviewed tenant setting allows open-ended validity.
  Response hash is observation provenance, not provider version. Every result remains
  investigation-only with `decision_impact=none`, `authorization_fact_created=false` and no automatic
  benign verdict, close, action authorization or PI-01B2 governed-fact creation.
- PingAn ZEUS lifecycle codes and reasons belong in a PingAn source adapter that emits
  `SocExternalDispositionIngressCommand`; generic Runtime must not recognize those status codes or
  copy the legacy `status != 1 -> skip AI` behavior. The historical EDR safe-path workbook is
  model-derived source knowledge, not a generic allowlist. Its implemented offline compiler, exact
  lookup and conservative `safe_paths`-only one-segment families live under
  `backend/soc_agent/integrations/pingan/software_path_catalog.py`; the stdio MCP
  uses the generic `endpoint.software_path.lookup` action. Build it with
  `backend/scripts/soc_pingan_software_path_catalog.py build`; generated SQLite/report files stay
  Git-ignored and mode `0600`. Historical ignored disposition never overrides current path-control
  context: `D:`, user-writable, and temporary paths remain high-attention. MCP results preserve source
  hash/row/family lineage and freshness, emit investigation-only evidence, and can never directly skip
  Runtime, mark false positive, close a review, authorize an action, or write confirmed memory. Only
  the separate post-Runtime PingAn policy-signal provider may convert complete exact/family coverage
  into `ignored`; `other_paths`, partial coverage and hash conflicts never receive that authority.
- Internal PingAn PKL scale validation uses
  `validation/compact_zeus/internal_batch/run_pingan_runtime_batch.py`. It must build the shared
  `SocAnalysisService` through `soc_agent.application.build_soc_analysis_service`, load DataFrame
  pickles with the restricted loader, require `--confirm-live` for LLM calls, and write only
  Git-ignored mode-`0700/0600` artifacts. Keep it resumable by source/payload/model/config hashes and
  expand runs `5 -> 50 -> all`; batch completion is not model-accuracy evidence. It runs the fixed
  Runtime and must not silently invoke MCP enrichment tools.
- Build internal handoff artifacts with `scripts/build_pingan_internal_transfer.py`. Source and
  private data/configuration are separate archives with independent manifests and SHA-256
  inspection. Never put `*.local`, PKL, XLSX, SQLite, credentials, or generated internal results in
  the source archive or Git. The builder rejects a dirty worktree by default; `--allow-dirty` is
  development-only and any such report is ineligible for final internal handoff.
- PI-04-A operational visibility uses `SocOperationsService` and the versioned
  `soc.operations_snapshot.v1` contract. `soc ops snapshot` and
  `GET /api/soc/operations/snapshot` expose exact, unpaginated SOC persistence aggregates plus a
  secret-free Kafka readiness projection. The Gateway endpoint is passive; only the CLI's explicit
  `--check-broker` may perform a connectivity probe. The snapshot must not infer an overall health
  verdict or claim lag, model compute, or production SLO evidence when those signals are not measured.
- PI-04-B adds the thin `/workspace/soc/operations` Web consumer. Frontend code must call only the
  typed `core/soc` snapshot client/hook, must not query SOC tables, actively probe Kafka, aggregate
  counts, or infer overall health. It must preserve backend availability and `not_measured` values,
  label SQLite as local/test evidence, and keep Playwright fixture evidence separate from deployed
  Gateway/auth/telemetry evidence.
- PI-05A uses `soc_agent.contracts.rollout` plus the pure `SocRolloutRehearsalService` and
  `soc rollout rehearse`. The V1 simulation must retain all five owner roles, seven real gates, a
  bounded cohort, and the ordered rollback procedure. Simulation evidence cannot close a real gate;
  every report keeps the real stage at `not_started`, real transitions/effects at zero, and production
  approval, auto-close, external mutation and high-risk actions disabled. It does not call Provider,
  Kafka, persistence mutation, feature-flag, Zeus or response systems. Real cohort enforcement remains
  separate PI-05C integration debt.
- PI-05B lives in `soc_agent.eval.completion` and is exposed by `soc rollout completion`. It reads the
  explicit PI-01E, PI-03B/C, PI-04 and PI-05A artifact set without invoking Runtime or external systems.
  Missing/malformed artifacts or simulation reports that overclaim a real gate fail closed. A pass means
  only that the product simulation track is complete and replayable: all seven real rollout gates stay
  open, the real stage remains `not_started`, and Pilot/Production/side-effect flags remain false. Do not
  implement PI-05C as a disconnected fake controller while deployed telemetry, owners, cohort
  enforcement and executable rollback are absent.
- SOC Runtime does not expose `endpoint.process_tree.lookup` or `host.event_context.lookup`.
  Process trees, commands, login context, and host events must come from the alert's bounded native
  evidence. Do not reintroduce mock routes or degrade a decision merely because those external
  providers do not exist; a future real provider requires an explicit product decision and contract.
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
- Use `validation/compact_zeus/e2e/run_ten_alert_e2e.py` as the canonical one-directory
  full-journey review for ten complete alerts. Its fixed cohort intentionally excludes D10's known
  input gaps `1965452` and `1965795`, replacing them with complete NDR/EDR cases `2025642` and
  `1980502`. It persists through the production service into an isolated SQLite database, invokes
  the configured live model only with explicit confirmation, uses simulated read-only PingAn
  Providers, and writes chronological per-alert artifacts under
  `backend/.deer-flow/soc-validation/e2e-ten-current/`. Its `knowledge-review/` package is an inert
  human-review surface and cannot write or activate knowledge. Historical validation roots remain
  specialized evidence, not inputs that must be manually joined for this review. The optional
  governed-automation simulation adds `12-effective-decision-and-automation.json` through the real
  `SocAutomationService` and a validation-only `mocked=true` adapter; it never calls an external
  response system. Use `compare_ten_alert_e2e.py` to compare same-cohort reports, keeping live-model
  base-output drift separate from append-only effective-decision transitions.
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
  contain `tenant == pingan` or hostname-substring safety branches. The layer defaults off; operators
  choose reviewed `shadow|enforced` policy explicitly, and external actions still require a separate
  Automation Policy or human Approval Grant.
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
| `PI` Real Data & Production Integration | Product simulation track complete through `PI-05B`; PingAn `mocked=false`, production infrastructure, real labels, telemetry/SLO, accountable owners and PI-05C rollout controls remain open Real Integration Debt |

### SOC Agent Development Workflow

SOC Agent work is a long-running product and engineering effort. Do not start from code
search alone. Use this order:

1. Read the current plan in `.notes/ai_soc/` and the relevant engineering/tooling
   contracts in `.notes/reference-index/`.
2. Derive the smallest Phase-aligned implementation slice from those docs.
3. After the slice is clear, use `rg --files`, `rg`, and direct source reads to verify
   DeerFlow code locations, reusable APIs, call sites, and low-intrusion integration
   points. For dynamic registration or runtime wiring, confirm the relationship with
   focused tests, configuration, or runtime traces rather than relying on static search
   alone. Use the same source-first approach for cross-project reference work. Do not run
   Understand Anything as part of the normal workflow; it is
   token-heavy and currently optional. The repository root `.understand-anything` and
   reference-project `.understand-anything` directories are static snapshots only: do
   not update them. Use Understand only when the user explicitly asks for it, and then
   verify any result against source and, when relevant, tests or runtime behavior before
   using it as a design fact.
4. Implement SOC-specific behavior as incremental modules/adapters first; avoid changing
   upstream DeerFlow core unless a small generic extension point is required.
5. If the slice changes product direction, runtime pipeline, contract semantics, phase
   scope, or next-step sequencing, update `.notes/ai_soc/soc-agent-solution.md` in the
   same change set; keep `.notes/reference-index/soc-agent-engineering-contracts.md`
   aligned for engineering rules.
6. After code changes, run verification proportional to the slice: focused tests,
   formatting/static checks, contract fixtures, and runtime replay where applicable.
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

Reference projects are read-only. Use targeted `rg` searches and minimal source reads when
consulting them; do not directly modify files in those projects.

| Project | Path | Use |
| --- | --- | --- |
| claude-code-sourcemap | `/home/yydspei/projects/claude-code-sourcemap` | Claude Code source and agent architecture patterns |
| claude-mem | `/home/yydspei/projects/claude-mem` | Memory-system implementation reference |
| hermes-agent | `/home/yydspei/projects/hermes-agent` | Hermes Agent framework and interaction patterns |
| openclaw | `/home/yydspei/projects/openclaw` | Personal AI assistant and multi-platform agent reference |

Cross-project rules:

- Prefer `rg --files` for inventory and `rg` for exact symbols, registrations, call sites,
  tests, and configuration. Confirm dynamic callers and impact through focused source
  reads, tests, or runtime traces.
- Use Understand Anything only when the user explicitly requests it; treat existing
  `.understand-anything` graphs as static snapshots and do not update them.
- Consult reference projects only when the current slice needs a design pattern
  that is not already settled locally, such as memory lifecycle, approval policy,
  multi-agent orchestration, stream protocol, context compaction, or tool runtime.
- Record reusable findings in `.notes/reference-index/` with the question, project,
  concrete file/symbol, and what was reused or rejected.
- Re-implement design ideas in this repo; do not copy reference-project code directly.
