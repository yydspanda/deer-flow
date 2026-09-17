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
  at promotion time. Automatic and manual learning share `memory/learning.py` scope
  resolution under the existing governance transaction lock. Open work is reused; approved
  scope comes from the reviewed Record, not the original Candidate. A narrowed IP scope
  must not suppress uncovered observations or mere partial/context-only matches. An open
  revision is the preferred review destination. Paused/expired records link to governed
  review, never automatic reactivation. Rejected, expired or deprecated candidates suppress
  only their own aggregation window; a later independent window can qualify again.
  `SocMemoryLearningView` owns the action shown by Web surfaces. Normalize legacy manual
  and automatic data-class locations without rewriting history or changing the Profile.
  Never reopen the rejected snapshot automatically. Equivalent automatic lessons use the
  same boundary, preserving idempotency within each window. Workbench list, execution,
  and audit projections show the candidate actually resolved by Pattern replay, including
  manual and cross-window candidates. Compare frozen evidence integrity only within its
  origin window; a different window is reinforcement, not missing source evidence. Reopening, revision, or
  supersession remains an explicit human-governed transition. Memory Center may reconcile older manual
  candidates by exact `source.run_id` plus tenant/Profile/environment compatibility at
  read time. This projection must not rewrite storage, increase Pattern support, or use
  fuzzy alert similarity.
- Candidate governance is separate from the alert verdict. `confirm` persists reviewed
  Memory; `reject` means do not persist that candidate. Only the audited `reopen`
  transition may return an eligible rejected candidate to review.
- Candidate inventory review-stage grouping is read-only and does not change stored
  statuses. `SocMemoryService.list_candidates(review_stage=...)` merges bounded,
  independently filtered status lanes before the global limit. Pending includes
  `confirmed_candidate`; closed includes rejected/superseded/expired/deprecated.
  HTTP callers must explicitly request `review_stage=all`; no-filter callers retain
  the legacy pending-only default. Exact `status` and grouped `review_stage` are exclusive.
- New decision-bearing confirmation requires reviewer-owned
  `soc.memory_business_lesson.v2`: detection scenario, observed business event,
  conclusion, business rationale, exact applicability, allowed generalization,
  invalidation conditions, and handling guidance. V1 remains read-compatible. A generic
  review reason or alert caption cannot substitute for it.
- Confirmation creates a retrieval-disabled record. Retrieval activation is a separate
  audited mutation with validity/review windows and optimistic version checks.

## Retrieval And Decision Use

- Applicability policy v4 freezes both selected behaviors (required presence) and the
  complete reviewed behavior coverage. Unchecking a known behavior makes it optional;
  it does not authorize unreviewed new behaviors. Uncovered current behavior stays
  context-only with a concrete comparison for the model, never an automatic risk verdict.
  Query object bindings keep source/destination and process host/account conditions
  together; an unprovable multi-object match cannot directly reuse a whole-alert verdict.
- Scope precedence is determined before directive authority and budgets, using proven
  containment, not score or condition count. Full inventory reads share the governance
  lock only while loading the snapshot. Previously published narrower scopes remain
  boundaries after suspension/expiry/deprecation. Restoring a broad answer requires
  `release_scope_boundary`, both versions and an audited reason; generic metadata cannot
  grant restoration. No new relational table or unbounded parent-child editor is used.
- Frozen source observations provide a separate, paged optional-entity directory.
  The UI does not receive their entire union. Preview, draft and review validate chosen
  values server-side. Refinement preserves its parent, freezes only covered sources and
  creates a pending candidate. Lesson drafting must not borrow excluded source narratives.
- Direct Memory reuse retains a v4 observation with `lesson=None` and
  `conclusion_origin=memory_reuse`: useful recurrence evidence, never an independent
  verdict confirmation. Tenant-policy-only direct results still do not create observations.
  Both DEV Workbenches must forward direct Memory results to the shared Pattern service
  and expose the persisted observation in trace/audit. Do not blanket-skip direct results
  in entrypoint or read-side filtering. Historical missing observations stay missing until
  an explicit subsequent processing action; a read must not backfill them.

- Model-visible reviewed prose preserves the complete business lesson, including tail
  boundaries and invalidation conditions. Compare full facet values before selecting
  bounded comparison items; never compare truncated prefixes. Whole-record retrieval
  budgets and final Prompt size checks still apply. Lesson drafting retains complete
  selected source values and rejects an oversized source catalog rather than slicing it.

- Exact approved override directives may resolve a run before the primary analyzer, after
  enterprise policy checks. `find_directive_records` evaluates enabled tenant-scoped
  inventory without prompt Top-K/token limits and reuses normal validity, applicability
  and selected-behavior gates. Opposite exact answers must survive downstream as a
  conflict, not disappear when the later model context is bounded. Similar/context-only
  and reinforce-only records cannot take this shortcut.
- Persist direct use as `direct_reused`, with `base_model_evaluated=false`, frozen record
  version/hash and matched conditions. It is usage, not a new independent model opinion,
  Pattern support, helpful-vs-Base measurement or human confirmation. Existing explicit
  correction/revision and manual promotion remain available; no automatic approval.

- `scope_view.py` is a read-only detail projection. Registered Profiles may explain
  stored scope ingredients; only verified same-version expansions can hide redundant
  optional controls. HTTP detail DTOs add the view without changing stored records,
  applicability, retrieval, activation, or Prompt input. Unknown/older profiles retain
  opaque fingerprints. Remove only redundant values, not entire mixed groups. Review may
  select a subset of an optional group's values via `promoted_facet_values`; drafting and
  governance preview share the narrowing helper, and confirmation validates the complete
  narrowed contract. New selections use applicability policy v2 `reuse_conditions`:
  AND between conditions, OR within values; split entity/role prefixes (source AND destination).
  These limits gate direct verdict reuse only; a missed limit must not block otherwise eligible
  context-only reference. Base scope, tenant/profile/exclusions and saved legacy v1 restrictions
  remain unchanged. Record scope identity, governance comparisons and model comparison metadata
  include the new limits. Core/strong/weak aliases are not separate editable controls. Never silently
  widen saved conditions or reinterpret old mixed-OR groups as AND.
  Policy v3 `selected_behavior_components` is an explicit reviewer-selected subset of
  Profile-verified fingerprint ingredients. All selected values must occur, independently
  of order or duplicates. The original required fingerprint remains lineage only for v3;
  directive keys, scope identity, overlap and model comparison use the selected behavior.
  Null preserves legacy full-hash matching. Drafting, governance preview and confirmation
  share the server validation; at least one verified behavior must remain. Fixed scope,
  versions and exclusions cannot change. Exact reviewed v3 scopes take precedence over
  Profile filters derived from now-unselected source behavior; reference-only retrieval
  keeps the existing filters. Never silently activate or widen an existing record.
  A `rule:<16-hex>` entity is the shared hash of the canonical detection key,
  not an independent vendor rule ID. PingAn omits that duplicate optional value
  when constructing a scope with one required detector; verified old scope views
  mark it covered. Keep extracted/query/indexed entities and existing required or
  excluded conditions intact. Multi-detector OR scopes and unknown hashes are not
  implicitly redundant; do not strip every `rule:` value.

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
  PingAn normalization `apply` selects Profile 9 / v7 semantic observation
  features; `off/shadow` use Profile 7 / v5. The shared Memory kernel does not parse
  detector vendor labels or create semantic facts. Keep old records/indexes untouched;
  the offline corpus index remains an Adapter-only navigation index, not an authority for
  a completed run's Memory scope. Workbench readers use the run's saved review mode and
  actual signature to select observations; new apply candidates use the actual v7 facets.
  New requests freeze `memory_profile`; historical unmarked apply runs use Profile 8 / v6.
  Restore known historical projectors for read/replay without rewriting or reactivating old Memory.
  Replaying the same alert/signature does not increase support. New semantic signatures
  must not be attached to old observations or silently widen reviewed Memory scopes.
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
- Before freezing the M-* catalog, `ConfirmedMemoryAnalysisRequestEnricher` applies
  `soc.memory_context_precedence.v1` to the bounded eligible results. An exactly
  applicable, fingerprint-scoped reviewed true/false-positive lesson excludes opposing
  partial lessons related through matched canonical detection/scenario anchors from
  model reasoning. Keep them in `LLMAnalysisRequest.memory_context_exclusions` with
  identity, hashes, comparison and preferred sources, never as citable M-* or MemoryUse
  records. This is run-local selection, not deactivation or a new matching rule.
  No exact answer, unknown/suspicious exact outcomes, unrelated scopes, or contradictory
  exact answers must not trigger that exclusion. Never pick a winner by score/recency.
  Audit-only comparisons are not model-visible gaps and must not degrade a decision.
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
  candidate. Reviewers may select verified core behaviors and add known optional
  reuse-only limits; generated prose cannot edit these typed conditions.
- Human-facing applicability prose uses localized labels plus original facet keys/values;
  the typed applicability object remains authoritative. Legacy prose may be localized
  only at read time and must not be rewritten in storage.
- Validate strict JSON/references and permit at most one bounded output-repair call. The
  generated lesson opens with editable business fields and read-only applicability, and remains
  non-persisted until the existing review command confirms it.

## Reinforcement And Revision

- Before activation, compare the full eligible record inventory, not retrieval top-K.
  Same strong typed scopes must not publish duplicate answers; opposing same-scope
  lessons cannot both be enabled, including reference-only lessons. Opposing overlapping
  directives are rejected. Profiles may declare `exclusive_scope_facet_keys`; disjoint
  entity sets do not prove disjoint applicability. Unknown overlap is conservative.
- Candidate review exposes a read-only governance comparison and accepts an explicit
  predecessor ID/version for atomic replacement. Reuse revision lineage and supersession;
  preserve old runs and history. A model verdict or tenant-policy handoff is not authority
  to replace a reviewed lesson. Reject stale versions without any partial mutation.
- Pending strong-scope proposals coalesce independently of risk class. Preserve their
  original content/quality snapshot, bounded additional source references, and events.
  Legacy candidates are compared read-only without a Profile bump or database reset.
- Governance check-and-write must share the UoW lock: SQLite write transaction or
  PostgreSQL transaction advisory lock. In-memory repositories are test-only and cannot
  perform atomic predecessor replacement.

- New analyst outcomes may reinforce, contradict, or propose a revision to an existing
  Memory. Keep observations and proposals append-only; do not mutate an old lesson or
  silently re-enable a deprecated/expired record.
- `SocMemoryService.propose_revision_candidate()` is the inventory correction
  boundary; candidate review may explicitly attach the same lineage at confirmation.
  The inventory command supports two explicit provenance modes. `observed_use` carries an exact
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
- Pending revisions remain discoverable through the candidate list's server-side
  `revision_of_memory_id` filter over persisted predecessor lineage, before pagination.
  This read path must work for existing records without a metadata backfill or new write.
- A revision candidate reuses the normal Business Lesson and applicability review. On
  confirmation, create a new record and mark the predecessor record/candidate as
  superseded/deprecated without rewriting their content. Retrieval activation for the
  successor remains a separate governed choice. While the revision is open, the
  predecessor carries `revision_pending=true` and the normal retrieval service must
  reject attempts to re-enable it. Rejecting or expiring the revision closes that flag
  but leaves the predecessor disabled; an explicit activation mutation is required.
  Candidate review may explicitly combine `reject` with `restore_predecessor`, expected
  predecessor version, activation validity and review scheduling. Close and activation
  must share one audited transaction, preserving content, scope and directive. This also
  supports the latest already-rejected revision while its predecessor is unchanged;
  stale, replaced, expired or subsequently modified records cannot be restored this way.
  Ordinary rejection remains disable-only. Restoration retries must never undo a later pause.
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
