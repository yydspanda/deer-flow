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
- Keep operational hierarchy explicit: each page or bounded workflow section has at most
  one filled primary command; navigation/view selectors remain segmented, read-only state
  remains a Badge, refresh/search utilities use familiar icon controls, and destructive
  governance actions are visually separated from confirmation. Pending Memory Candidate
  rows and links use the primary "审核并决定" treatment; terminal records use the
  secondary "查看治理记录" treatment.
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
- Candidate review keeps the decision command bar visually separate from proposal,
  matching scope, Business Lesson, and audit evidence. AI drafting is the primary next
  step only after a verdict is selected; final confirmation becomes primary only after a
  valid Lesson exists. Reject/deprecate actions use destructive styling and low-frequency
  expiry remains tertiary.
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
- Candidate applicability controls must distinguish server-locked required facets from
  reviewer-selectable optional narrowing. Selecting an optional facet adds it to the
  required set; clearing it removes only that reviewer-added restriction. Arbitrary
  facet keys or values require a tenant Memory Profile change, not a browser input.
- Retrieval activation sends current record version, reason, idempotency key, and
  validity/review settings. Refresh server state after mutation. Render context-only
  matches separately from applicable decision directives.
- Candidate confirmation must present retrieval and decision use as separate concepts:
  retrieval controls whether a record can be found, while the explicit future-match
  choice controls whether an exact typed match may participate in the effective verdict.
  Default the latter to “仅供模型参考，不改判”; never imply that enabling retrieval grants
  a directive.
- Manual run promotion calls the governed `/api/soc/memory/runs/{run_id}/promote`
  mutation after an explicit confirmation. The optional note only highlights material
  for the later reviewer; it is not an admission or authority field. The mutation creates
  a pending candidate only; the browser must not present it as confirmed Memory, imply
  that the current verdict changed, or ask for final verdict/business facts before the
  Candidate review page.
- A run that actually consumed a confirmed Memory exposes `纠正此 Memory` beside that
  `M-*` context. The correction page sends the current record version, source run,
  typed issue, and substantive reason to the governed revision-candidate mutation. It
  must explain that old retrieval is suspended immediately, current/historical verdicts
  are not rewritten, and the existing Candidate review flow owns the replacement
  Business Lesson and applicability. React must never edit a confirmed record in place.
  A rejected revision leaves the predecessor disabled and must not expose the generic
  `reopen` action; direct the analyst to create a fresh revision from a later exact use.
- Memory Center is list-first and consumes only the server lineage read model. One row is
  one stable Pattern across windows; observations, distinct sources, window count,
  frozen candidate snapshot, and later reinforcement remain separate values.

## DEV Validation Surfaces

- DEV Memory/corpus pages orchestrate server-owned cohorts and official services. React
  may search, filter, paginate, select, and invoke one alert; it must not load PKL files,
  derive fingerprints, decide readiness, construct Patterns, approve candidates, or
  calculate Base/Memory/Tenant/Effective decisions.
- Corpus replay order and label comparison are server-owned. Render alerts by the
  canonical sequence returned by the API; do not reorder them by readiness, ID, or
  vendor export position. Historical disposition labels stay hidden per alert until a
  Runtime decision exists, and are displayed as operational outcomes rather than
  independent detection truth.
- Corpus filter and selected-alert continuity may be retained in tab-scoped browser
  storage, but never persisted as business state. When a processed alert creates a
  Pattern Candidate, keep the current page visible and render a persistent review link;
  the prominent safety band must only project server-returned Effective Decision fields.
- The corpus execution monitor polls only the selected active alert's lightweight
  execution endpoint. Render the server projection of persisted Runtime steps, provider
  journal, durations, bounded counts, decision, and Pattern write; do not estimate phase
  progress in React or expose raw evidence, prompts, model responses, or secrets.
- Full-chain corpus auditing is a separate explicit request, never part of live polling.
  The `soc_admin`-only DEV audit bundle may show complete persisted raw alert data,
  canonical normalization, bounded model context/output, validation, Decision, and
  Pattern/Memory artifacts for demonstrations and engineering review. Render only the
  ordered server artifacts, keep the DEV/MOCK warning visible, and support JSON
  copy/download without deriving or re-running business logic in React. The bounded
  analysis artifact defaults to the server-returned model-visible projection and keeps
  the frozen Runtime request in a separate explicit view. Large JSON uses a lazily loaded,
  read-only viewer with syntax highlighting, line numbers, folding, search, wrapping and
  formatted/compact modes; do not render an unbounded full-document `<pre>`.
- Keep environment, isolated SQLite, model/reasoning, role verifier, mock/off providers,
  tenant policy, and action-execution labels visible so screenshots cannot be mistaken
  for STG/production evidence.
- Fixed GalaxyLab remains a DEV-only validation route and must not be linked from Memory
  Center or global operational navigation. Memory Center contains only production-facing
  Pattern, Candidate, Memory, and Profile governance. Pattern counts are absolute
  observation/distinct-source values, not progress fractions.
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
