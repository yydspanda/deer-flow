# SOC Memory Guide

This directory owns governed SOC experience Memory. Memory is not chat history, current
alert evidence, tenant policy, or an action grant. Read `backend/soc_agent/AGENTS.md` and
the Memory sections of `.notes/ai_soc/soc-agent-solution.md` before changing it.

## Lifecycle

- A Runtime run may create an admitted candidate or Pattern observation, never a
  confirmed Memory automatically. Replays and duplicate source events must be
  idempotent; ordinary low-value alerts must not create one Memory each.
- `MemoryAdmissionService` is the gate for candidate value. Promotion from a note or
  correction is explicit. Pattern construction requires stable similarity anchors,
  bounded windows, distinct-source support, and an auditable quality gate.
- Candidate governance is separate from the alert verdict. `confirm` persists reviewed
  Memory; `reject` means do not persist that candidate. Only the audited `reopen`
  transition may return an eligible rejected candidate to review.
- New decision-bearing confirmation requires reviewer-owned
  `soc.memory_business_lesson.v1`: conclusion, business rationale, exact applicability,
  allowed generalization, invalidation conditions, and handling guidance. A generic
  review reason or alert caption cannot substitute for it.
- Confirmation creates a retrieval-disabled record. Retrieval activation is a separate
  audited mutation with validity/review windows and optimistic version checks.

## Retrieval And Decision Use

- Persisted Runtime resolves Memory after Skill selection and before catalog
  finalization/provider journaling, only through `SocMemoryService`. Retrieval failure is
  non-blocking.
- Eligible records are confirmed, explicitly enabled, validity-current, review-current,
  and tenant/profile compatible. Alert/run IDs are lineage, not matching facets.
- Retrieval policy v2 runs exact-facet and text lanes over the complete eligible corpus,
  merges bounded candidates, and requires a Memory-type-specific strong anchor.
  Source/environment/category alone cannot admit a detection lesson or benign Pattern;
  do not regress to latest-N retrieval.
- `M-*` is historical reasoning context, not `E-*` current evidence. Free-form Memory
  never deterministically changes a decision.
- Only a human-reviewed `SocMemoryDecisionDirective` may alter the post-Runtime decision.
  The exact record version, content/facet hashes, activation, validity, review due,
  minimum score, and required facets must all match. Record Base, Memory, Tenant, and
  Effective decisions separately. A directive never directly authorizes an action.
- Pattern applicability and decision-directive eligibility are separate results. A
  context-only retrieval must never be shown or consumed as an applicable directive.

## Lesson Draft Assistance

- `SocMemoryLessonDraftService` is reviewer assistance, not authority. It runs only after
  candidate admission and after an authenticated reviewer selects the technical verdict.
- The prompt receives bounded server-owned `D-*` facts. Optional reviewer business
  context may explain tenant facts; prior model/candidate verdicts remain observations and
  cannot override the reviewer selection.
- Runtime restores applicability from the candidate contract. The model cannot widen
  scope, invent facet values, persist the draft, enable retrieval, or approve the
  candidate. Reviewers may only promote known optional facets to required.
- Validate strict JSON/references and permit at most one bounded output-repair call. The
  generated six-section lesson is read-only by default, explicitly editable, and remains
  non-persisted until the existing review command confirms it.

## Reinforcement And Revision

- New analyst outcomes may reinforce, contradict, or propose a revision to an existing
  Memory. Keep observations and proposals append-only; do not mutate an old lesson or
  silently re-enable a deprecated/expired record.
- Contradiction opens governed review and may suspend retrieval according to policy.
  A revision creates explicit supersession/version lineage so later analysis can show
  which Memory changed what decision and why.
- Pattern windows, candidate snapshots, later reinforcement, and distinct-source counts
  are separate persisted concepts. UI aggregation is a projection, not the source of
  truth.

## Evaluation And DEV Surfaces

- Memory quality uses `soc eval memory prepare|run` with held-out query alerts,
  independent analyst truth, and pairwise record relevance labels. Source alerts used to
  construct a Memory must not overlap held-out queries.
- Report retrieval, Pattern applicability, directive eligibility, decision change, and
  action authorization separately. Simulation labels cannot establish rollout quality.
- Browser workbenches are enabled only with `SOC_DEV_MEMORY_WORKBENCH_ENABLED=true`, an
  isolated SQLite database, dev environment, real LLM analyzer, authenticated admin,
  disabled tenant policy, and disabled external actions. They orchestrate official
  services; React must not construct Patterns, approve candidates, or calculate decision
  lineage.
