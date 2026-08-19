# SOC Workspace Frontend Guide

SOC pages are thin operational clients for server-owned contracts. Read the root,
`frontend/AGENTS.md`, and `frontend/src/AGENTS.md` first. All SOC HTTP calls belong in
`src/core/soc`; React must not query persistence, parse vendor payloads, calculate
Runtime decisions, construct Memory, or infer action authority.

## API And Navigation

- Use `/api/soc/*`, typed success bodies, `X-SOC-API-Version: 1`, and RFC Problem Details
  mapped to `SocApiError`. Authenticated Gateway identity is authoritative; actor headers
  are attribution only.
- State-changing calls send stable idempotency keys and invalidate the owning query
  namespace after success. Do not optimistically mutate governed state.
- All `/workspace/soc/*` pages use `SocWorkspaceHeader` for stable second-level
  navigation. Page headers contain only local actions such as refresh/filter.
- Review routes are ownership-specific: alerts under `/review/alerts`, Memory candidates
  under `/review/memory-candidates[/candidate_id]`, and quality samples under
  `/review/samples`. A candidate deep link must not fabricate an alert queue item.
- Enable queries only for the active surface. List pages do not preload the first detail
  or attach all related analyses to navigation; detail and source observations are
  bounded, explicit requests.

## Alert Review And Investigation

- Structured disposition capture is separate from ReviewQueue close. Require the
  server-owned proposal/queue state plus explicit operational disposition, review lane,
  and reason; send append-only revisions with supersession lineage.
- Sample review opens only server-returned manifest items and reuses the existing capture
  command. It never creates a second write path or enables auto-close.
- Investigation addenda are read-only projections. Display execution status, evidence
  coverage, and attention flags but do not infer a new verdict/provider quality/action
  permission. Preserve `shadow_only`, `decision_impact=none`, and
  `new_conclusion_produced=false` labels.
- A ReviewQueue-bound Lead Agent thread carries only `queue_id` as an identity hint. The
  Gateway owns immutable queue binding, context hash, actor, run, and alert lineage. The
  browser cannot reuse a bound thread for a different queue.

## Memory Governance

- Candidate inventory is all-status by default. Confirmed, rejected, superseded,
  expired, and deprecated records remain discoverable for audit; terminal history may be
  an explicit server filter.
- Candidate detail always shows proposed content and evidence lineage. Only editable
  states show the review workflow. Confirmed candidates show the persisted six-part
  Business Lesson from the related Memory record.
- Reviewer-selected alert verdict is separate from candidate governance. `confirm`
  persists reviewed Memory; `reject` abandons the candidate. Only a successful server
  `reopen` response unlocks an eligible rejected candidate.
- `AI generate Memory` is available only after the reviewer selects the technical
  verdict. Optional business context is separate. A verdict change invalidates the
  browser draft.
- The generated Lesson is read-only until explicit Edit. Applicability is server-derived;
  the browser may only promote known optional facets to required and must submit the
  complete narrowed contract. It cannot widen scope, persist the draft automatically,
  enable retrieval, or infer directive eligibility.
- Retrieval activation sends current record version, reason, idempotency key, and
  validity/review settings. Refresh server state after mutation. Render context-only
  matches separately from applicable decision directives.
- Memory Center is list-first and consumes only the server lineage read model. One row is
  one stable Pattern across windows; observations, distinct sources, window count,
  frozen candidate snapshot, and later reinforcement remain separate values.

## DEV Validation Surfaces

- DEV Memory/corpus pages orchestrate server-owned cohorts and official services. React
  may search, filter, paginate, select, and invoke one alert; it must not load PKL files,
  derive fingerprints, decide readiness, construct Patterns, approve candidates, or
  calculate Base/Memory/Tenant/Effective decisions.
- Keep environment, isolated SQLite, model/reasoning, role verifier, mock/off providers,
  tenant policy, and action-execution labels visible so screenshots cannot be mistaken
  for STG/production evidence.
- Fixed GalaxyLab is a visibly DEV-only tool linked from Memory Center, never global
  operational navigation. Pattern counts are absolute observation/distinct-source
  values, not progress fractions.
- Dynamic routes are warmed only by the SOC development script. Do not patch browser
  `performance`, change normal DeerFlow dev commands, or add business-page error
  suppression for a development bundler issue.

## Operations

- `/workspace/soc/operations` is a read-only consumer of
  `soc.operations_snapshot.v1`. It may refresh the passive endpoint and translate
  server-owned availability values.
- Do not actively probe Kafka, recompute aggregates, infer overall health, or turn
  `not_measured` into healthy zero. Label SQLite and fixture/Playwright evidence as
  local/test, separate from deployed Gateway, production telemetry, and SLO proof.
