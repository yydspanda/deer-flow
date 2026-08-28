# SOC Memory Guide

This directory owns governed SOC experience Memory. Memory is not chat history, current
alert evidence, tenant policy, or an action grant. Read `backend/soc_agent/AGENTS.md` and
the Memory sections of `.notes/ai_soc/soc-agent-solution.md` before changing it.

## Lifecycle

- A Runtime run may create an admitted candidate or Pattern observation, never a
  confirmed Memory automatically. Replays and duplicate source events must be
  idempotent; ordinary low-value alerts must not create one Memory each.
- `Pattern observation`, `lineage`, and `profile` remain internal contract terms. The
  analyst UI presents them as alert samples and same-behavior groups, using a
  deterministic canonical behavior label rather than a Candidate summary.
- Fixed-window duration is a versioned cohort semantic. The generic profile defaults
  to 24 hours; a tenant profile may declare another bounded default. An explicitly
  supplied operator/evaluation policy overrides the profile default and is frozen on
  every observation.
- `MemoryAdmissionService` is the gate for candidate value. Promotion from a note or
  correction is explicit. Pattern construction requires stable similarity anchors,
  bounded windows, distinct-source support, and an auditable quality gate.
- An authenticated analyst may explicitly promote any completed `AnalysisRun` through
  `SocReviewService.promote_run_to_memory()`. This bypasses the automatic recurrence
  threshold, not governance: the authenticated explicit action plus exact run/alert
  lineage is the promotion signal; an analyst note is optional and never grants authority.
  It still requires a reusable facet, creates only a `manual_note` `pending_review`
  candidate, and must not alter the run, ReviewQueue, retrieval state, or action authority.
  Its candidate identity is stable per run/alert; a later optional note must reuse the
  existing review task rather than creating duplicate candidates. Candidate review asks
  for the final verdict and optional business fact once; the service generates audit prose
  from the reviewed outcome and future-use mode. A separate reason is required only for a
  later retrieval enable/disable mutation.
- Explicit run promotion and correction must resolve the tenant `SocMemoryProfile`, project
  facets from the exact persisted run, and build applicability through that Profile. When a
  Pattern observation exists, its server-owned environment overrides caller metadata. Do not
  fall back to generic facets merely because the action was initiated manually.
- If that run already has a persisted `MemoryPatternObservation`, manual promotion must
  carry its exact Pattern lineage metadata and a frozen snapshot of the cohort visible
  at promotion time. That manual candidate governs the whole exact Pattern lineage:
  later observations remain reinforcement/replay evidence and automatic aggregation
  must not create a second candidate for the same lineage. Reopening, revision, or
  supersession remains an explicit human-governed transition. Memory Center may reconcile older manual
  candidates by exact `source.run_id` plus tenant/Profile/environment compatibility at
  read time. This projection must not rewrite storage, increase Pattern support, or use
  fuzzy alert similarity.
- Candidate governance is separate from the alert verdict. `confirm` persists reviewed
  Memory; `reject` means do not persist that candidate. Only the audited `reopen`
  transition may return an eligible rejected candidate to review.
- New decision-bearing confirmation requires reviewer-owned
  `soc.memory_business_lesson.v2`: detection scenario, observed business event,
  conclusion, business rationale, exact applicability, allowed generalization,
  invalidation conditions, and handling guidance. V1 remains read-compatible. A generic
  review reason or alert caption cannot substitute for it.
- Confirmation creates a retrieval-disabled record. Retrieval activation is a separate
  audited mutation with validity/review windows and optimistic version checks.

## Retrieval And Decision Use

- Persisted Runtime resolves Memory after Skill selection and before catalog
  finalization/provider journaling, only through `SocMemoryService`. Retrieval failure is
  non-blocking.
- Eligible records are confirmed, explicitly enabled, validity-current, review-current,
  and tenant/profile compatible. Alert/run IDs are lineage, not matching facets.
- Canonical destination transport/port and public CVE IDs are vendor-neutral facets. A
  tenant Profile may combine them with a versioned behavior-family policy, but IPs and
  ephemeral source ports remain occurrence/entity context. Any component-policy change
  requires a feature-schema/Profile bump, fail-closed old records, and a pattern-facet
  projection that stays within the 20-group signature contract.
- Retrieval policy v2 runs exact-facet and text lanes over the complete eligible corpus,
  merges bounded candidates, and requires a Memory-type-specific strong anchor.
  Source/environment/category alone cannot admit a detection lesson or benign Pattern;
  do not regress to latest-N retrieval.
- A tenant Profile may reject canonical semantic conflicts before either exact or
  context-only applicability is accepted. Compatibility readers may recognize older
  canonical component encodings, but they must not read tenant raw aliases or silently
  reinterpret an unrelated behavior under the same detection key.
- `M-*` is historical reasoning context, not `E-*` current evidence. Free-form Memory
  never deterministically changes a decision.
- Only a human-reviewed `SocMemoryDecisionDirective` may alter the post-Runtime decision.
  The exact record version, content/facet hashes, activation, validity, review due,
  minimum score, and required facets must all match. Record Base, Memory, Tenant, and
  Effective decisions separately. A directive never directly authorizes an action.
- Pattern applicability and decision-directive eligibility are separate results. A
  context-only retrieval must never be shown or consumed as an applicable directive.
- Within one `AnalysisRun`, one immutable `(memory_id, memory_version)` has exactly one
  final use effect: `context_only`, `reinforced`, `overridden`, or `conflicted`. Duplicate
  `M-*` projections must collapse to that identity in both the Decision contributor list
  and the persisted use record; prefer the projection that actually contributed to the
  decision and never increment Memory health twice.

## Lesson Draft Assistance

- `SocMemoryLessonDraftService` is reviewer assistance, not authority. It runs only after
  candidate admission and after an authenticated reviewer selects the technical verdict.
- The prompt receives bounded server-owned `D-*` facts. Optional reviewer business
  context may explain tenant facts; prior model/candidate verdicts remain observations and
  cannot override the reviewer selection.
- Runtime restores applicability from the candidate contract. The model cannot widen
  scope, invent facet values, persist the draft, enable retrieval, or approve the
  candidate. Reviewers may only promote known optional facets to required.
- Human-facing applicability prose uses localized labels plus original facet keys/values;
  the typed applicability object remains authoritative. Legacy prose may be localized
  only at read time and must not be rewritten in storage.
- Validate strict JSON/references and permit at most one bounded output-repair call. The
  generated eight-section lesson is read-only by default, explicitly editable, and remains
  non-persisted until the existing review command confirms it.

## Reinforcement And Revision

- New analyst outcomes may reinforce, contradict, or propose a revision to an existing
  Memory. Keep observations and proposals append-only; do not mutate an old lesson or
  silently re-enable a deprecated/expired record.
- `SocMemoryService.propose_revision_candidate()` is the only manual correction
  boundary and supports two explicit provenance modes. `observed_use` carries an exact
  source run and must verify the persisted `SocMemoryUseRecord` plus content/facet
  hashes. `operator_direct` starts from the Memory inventory without inventing a use;
  it freezes the current predecessor version/hashes and retains the predecessor's
  source run/alert when available. Both modes require typed issue, substantive reason,
  authenticated actor, idempotency key, and expected record version, then atomically
  suspend retrieval and create one `memory_revision` candidate.
- An `applicability_too_broad` revision must reload the traceable source `AnalysisRun` and
  rebuild facets/applicability through the current resolved Profile. Copying the predecessor
  scope would preserve the bug and is forbidden. If the run or sufficient canonical scope is
  unavailable, fail closed and leave the predecessor unchanged.
- One Memory may have only one open revision. A second request against a predecessor
  carrying `revision_pending=true` must fail with a conflict rather than creating a
  parallel candidate.
- A revision candidate reuses the normal Business Lesson and applicability review. On
  confirmation, create a new record and mark the predecessor record/candidate as
  superseded/deprecated without rewriting their content. Retrieval activation for the
  successor remains a separate governed choice. While the revision is open, the
  predecessor carries `revision_pending=true` and the normal retrieval service must
  reject attempts to re-enable it. Rejecting or expiring the revision closes that flag
  but leaves the predecessor disabled; an explicit activation mutation is required.
  Rejected revision candidates cannot be reopened with stale lineage. Start a new
  revision through a later exact Memory use or a new authenticated inventory review.
- Final-outcome comparison uses the explicit reviewer verdict even when a Memory was used
  as context-only. Exact applicable matches can support or contradict the lesson; partial
  context-only matches are `not_applicable` and do not punish it. A high-trust risk outcome
  contradicting a retrievable benign lesson suspends retrieval even when no directive was
  applied in that Run.
- Contradiction opens governed review and may suspend retrieval according to policy.
  A revision creates explicit supersession/version lineage so later analysis can show
  which Memory changed what decision and why.
- Pattern windows, candidate snapshots, later reinforcement, and distinct-source counts
  are separate persisted concepts. UI aggregation is a projection, not the source of
  truth.
- Operator inventory search is not Runtime retrieval. The record list may search Memory,
  Alert, Run, Candidate, Business Lesson, and typed facet fields, while
  `SocMemoryService.find_relevant_records()` remains the only production retrieval
  policy. A record match test evaluates one persisted run through that same retrieval
  gate in an isolated read-only projection; it must not call the LLM or write state.

## Evaluation And DEV Surfaces

- Memory quality uses `soc eval memory prepare|run` with held-out query alerts,
  independent analyst truth, and pairwise record relevance labels. Source alerts used to
  construct a Memory must not overlap held-out queries.
- Report retrieval, Pattern applicability, directive eligibility, decision change, and
  action authorization separately. Simulation labels cannot establish rollout quality.
- Browser workbenches are enabled only with `SOC_DEV_MEMORY_WORKBENCH_ENABLED=true`, an
  isolated SQLite database, dev environment, real LLM analyzer, authenticated admin,
  and disabled external actions. Tenant policy remains disabled by default; the local
  PingAn acceptance launcher may enable deterministic rules, the reviewed bounded policy
  advisor, and the software-path catalog only with
  `SOC_DEV_WORKBENCH_ALLOW_TENANT_POLICY=true` and an explicit `dev` policy environment.
  This exception never enables external action execution. Workbenches orchestrate
  official services; React must not construct Patterns, approve candidates, or calculate
  decision lineage.
