# SOC Workspace Frontend Guide

SOC pages are thin operational clients for server-owned contracts. Read the root,
`frontend/AGENTS.md`, and `frontend/src/AGENTS.md` first. All SOC HTTP calls belong in
`src/core/soc`; React must not query persistence, parse vendor payloads, calculate
Runtime decisions, construct Memory, or infer action authority.

Memory review keeps fixed scope read-only and all core behaviors visible as individual
checkboxes (new draft: all checked; revision: restore saved selection). Submit the same
`selected_behavior_components` to lesson drafting, governance preview and the reviewed
applicability. Do not compute hashes or match scores in React. At least one core behavior
remains selected; clearing a behavior is an explicit scope change, not an exclusion.
Additional conditions gate direct reuse only. Reference copy must explain that a model
can adopt a reviewed lesson, while the program does not directly copy its historical verdict.

## API And Navigation

- Corpus `后续运行设置` is a per-run snapshot, not global configuration. The four switches cover
  semantic review, enterprise policy and its safe-path/advisor children. Store the local selection
  in session storage, mask unavailable capabilities and submit it with each process request.
  Requests carrying settings must declare `Content-Type: application/json`; keep bodyless
  calls compatible with deployment defaults. Pin the header in both API and browser tests.
  Turning the parent off also turns children off. The trace's `本次运行配置` reads only saved
  execution options; toggling controls must not relabel historical or in-flight results. Provider
  modes and real external execution remain read-only. No new client-owned decision or authority.
  Place each switch immediately before its associated clickable label, with wider spacing between
  options. Never distribute a label and switch to opposite ends of a column; this makes switches
  appear to belong to the next option on wide screens. Preserve the pairing at responsive widths.

- Candidate governance comparison is server-owned. Show the old business conclusion,
  scope relation/differences and an explicit replacement selection in the existing review
  page. Selection alone does not mutate Memory. Submit predecessor ID/version with the
  existing confirm command; no second mandatory reason. Clear a stale selection after
  scope/version changes and show server conflicts, never silently pick the newest Memory.

- Use `/api/soc/*`, typed success bodies, `X-SOC-API-Version: 1`, and RFC Problem Details
  mapped to `SocApiError`. Authenticated Gateway identity is authoritative; actor headers
  are attribution only.
- State-changing calls send stable idempotency keys and invalidate the owning query
  namespace after success. Do not optimistically mutate governed state.
- All `/workspace/soc/*` pages use `SocWorkspaceHeader` for stable second-level
  navigation. The persistent SOC layout exposes an immediate, non-blocking top progress
  indicator for internal route clicks; target pages then take over with route/local
  loading shells. Page headers contain only local actions such as refresh/filter. Heavy
  SOC workspaces use route-level lazy shells so development compilation never looks like
  an ignored click.
- Keep operational hierarchy explicit: each page or bounded workflow section has at most
  one filled primary command; navigation/view selectors remain segmented, read-only state
  remains a Badge, refresh/search utilities use familiar icon controls, and destructive
  governance actions are visually separated from confirmation. Pending Memory Candidate
  rows and links use the primary "审核并决定" treatment; terminal records use the
  secondary "查看治理记录" treatment.
- Operational routes are ownership-specific: every Runtime result is listed under
  `/alerts`; the rare unresolved-fact task is under `/review/alerts`; Memory candidates
  are governed under `/review/memory-candidates[/candidate_id]`; high-risk actions are
  authorized under `/approvals`; quality samples remain under `/review/samples`. A
  candidate or approval deep link must not fabricate an alert queue item.
- Enable queries only for the active surface. List pages do not preload the first detail
  or attach all related analyses to navigation; detail and source observations are
  bounded, explicit requests.

## Alert Results And Human Intervention

- Render server `processing_path=tenant_policy|memory` as direct handling with an explicit
  "主模型未调用" explanation. These completed runs have no model result or confidence;
  skipped Base means "未生成判断", never 0% or a failed run. Memory use `direct_reused`
  means adoption, not a before/after model comparison. Old model runs keep their existing
  projection; do not synthesize a direct source in React.

- `/alerts` is the primary **告警研判** workspace and is keyed by `run_id`. It lists every
  persisted Runtime result, including usable, degraded, failed, and corrected runs. A
  `ReviewQueueItem` is optional and must never be required to open the alert result.
- Present model uncertainty and evidence gaps as visible advisory information on the
  result. Do not turn `unknown`, low confidence, missing enrichment, provider failure, or
  a suggested manual check into analyst work. Only the server-owned `required` attention
  classification may link to `/review/alerts`.
- Use the server-owned `SocCaseOutcomeView.recommended_handling` as the one primary
  handling conclusion: **忽略 / 转交**, followed by its reason and necessary next steps.
  Security verdict, confidence, disposition and progress remain distinct data but belong
  in default-collapsed details, not competing headline cards. Failed/unusable results must
  show an explicit exception, never a fabricated ignore or completed handoff.
  Explain whether a gap is advisory, limits only a dependent capability, or blocks the
  decision. Base/Memory/Tenant/Effective lineage belongs in collapsed technical audit;
  never render those stages as four peer conclusions or derive closure state in React.
  Render the server's `tenant_policy_handoff_pending` reason as a policy-selected handoff,
  with `handling_reason` beside the handling conclusion. Never label that requirement as missing facts
  or imply that the handoff has already executed.
  Render `tenant_policy_review_pending` as `待按建议排查`. When no concrete
  disposition exists, display the server's `recommended_handling` as a recommendation,
  not `处置未明确`; keep `handling_recommendation` visible even when policy was applied.
  Show same-call `conclusion_support` as resolved business questions and future reassessment
  triggers, not new mandatory tasks. Keep non-blocking supplementary information and unused
  targeting guards in expandable records. Actual current blockers remain prominent. Do not
  invent resolved questions for saved runs without this field. In the corpus workbench,
  show frozen run Memory separately from cohort-owned Memory; retrieval is not adoption,
  and record/revision links use `memory_id`, never a version-qualified source reference.
- Progress text comes from the server-owned
  `progress_label/progress_detail`; React supplies only aggregate styles and neutral fallbacks.
  Keep normal progress in details; show current material blockers and execution failures
  beside the main conclusion. Lists and filters use `SocAlertResult.recommended_handling`,
  not the immutable Base verdict. The audit table includes every stage's disposition,
  review requirement and suggested action, not just verdict/confidence. Applied policy means
  applied to a decision, not executed externally; original summaries remain expandable.
- The result page owns two explicit optional commands: correct this run and promote this
  run into the governed Memory Candidate flow. Correction preserves decision lineage;
  promotion creates at most a pending Candidate and does not review it inline.
- `/review/alerts` is the rare **需人工介入** inbox. It resolves only critical current-fact
  conflicts that Runtime could not adjudicate. Put the conflict, current conclusion,
  evidence gaps, manual checks, and final analyst judgment on one focused surface. The
  correction mutation records the judgment and closes the task; do not require a second
  close command.
- Memory Candidate governance and action approval are separate jobs. Never embed their
  forms in a human-intervention task. Link back to the run result, Candidate page, or
  `/approvals` when cross-workflow navigation is useful. Keep raw contracts and full
  technical artifacts collapsed behind an explicit audit control.
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

- One Memory Center owns three route-backed views: confirmed experiences at `/memory`
  (`/memory/records` remains a compatible alias), review and its history at
  `/review/memory-candidates`, and accumulation at `/memory/patterns`. Use shared
  `SocMemoryNavigation` with owning-tab highlighting on details/revisions. Do not expose
  a second "inventory/ledger center" to analysts. Each view loads only its own list;
  entering the default view must not fetch Pattern detail or candidate review data.
  Keep explicit back links to confirmed experiences or review records; preserve old deep links.

- A record with `revision_pending=true` links to its existing pending Candidate from
  both the record and revision pages; never show a second creation form. Resolve via
  the server-side `revision_of_memory_id` candidate filter, not a latest-N browser scan.
  Missing, failed or ambiguous lookup exposes recovery, never enables another revision.

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
- Generated Lessons open directly as editable fields for both new and revision candidates;
  preview is optional and returning to editing preserves every draft value. Regeneration
  requires explicit overwrite confirmation, keeps the current draft until success, and
  disables editing, verdict/scope changes and confirmation while generation is in flight.
  Optional business facts remain human input; never fill them from the selected verdict.
  Applicability is server-derived. The browser may select a nonempty subset of verified core
  behaviors and add known optional values as direct-reuse limits; it submits the complete
  reviewed contract. It cannot change fixed scope, persist the draft automatically, enable
  retrieval, or infer directive eligibility. Removing a core behavior widens only that reviewed
  direct-use requirement and must pass the same governance/confirmation checks.
- Candidate applicability controls must distinguish server-locked required facets from
  reviewer-selectable core behavior and optional narrowing. Extra selections go into `reuse_conditions`,
  not required/optional/context facet groups; clearing removes only unsaved extra limits. Arbitrary
  facet keys or values require a tenant Memory Profile change, not a browser input.
- Candidate and record details share `SocMemoryScope`. Only server-verified `scope_view`
  may explain fingerprint contents or classify redundant narrowing options. Do not hash,
  parse tenant detection identities, or infer applicability in React. Keep raw contracts
  and original Lesson applicability prose in collapsed audit; unknown projections retain
  explicit unresolved fingerprint labels. Show business rule/behavior rows once, with every verified
  core component visible; only raw hashes and technical metadata collapse. Unselected extra limits are offered under an add control
  only while reviewing, not in read-only detail. Remove only verified
  redundant values, never an entire mixed group. Review sends `promoted_facet_values` to
  drafting/preview and the same narrowed `record_applicability` to confirmation. Multiple
  selected extra values within one semantic group remain OR; source and destination are separate AND
  groups even when stored under role_entity. Limits affect direct verdict reuse only, not otherwise
  eligible reference recall. Saved v1 shared scope limits retain their meaning and are marked.
  Similarity/core/strong/weak aliases stay internal. Never mutate scope on read or invent values.
- Retrieval activation sends current record version, reason, idempotency key, and
  validity/review settings. Refresh server state after mutation. Render context-only
  matches separately from applicable decision directives.
- Candidate confirmation must present retrieval and decision use as separate concepts:
  retrieval controls whether a record can be found, while the explicit future-match
  choice controls whether an exact typed match may participate in the effective verdict.
  Default the latter to “仅供研判参考”; explain that the model may adopt the lesson in its judgment,
  but the program does not directly copy its verdict. Never imply that enabling retrieval grants a directive.
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
  The Memory record detail may also start an `operator_direct` revision without a source
  use. It must identify that provenance explicitly and never fabricate a run/use pair.
  Ordinary rejection leaves the predecessor disabled and must not expose generic `reopen`.
  Revision review links directly to the predecessor and offers explicit cancel-and-restore,
  including restore from the latest already-rejected revision. Show the original use mode
  and renewed activation/review dates before confirmation; send one review command with
  `restore_predecessor` and the displayed expected version. Navigate to the old record only
  on confirmed restoration. Do not change lessons, matching scope, directives or historical runs.
- The accumulation view is list-first and consumes only the server lineage read model. One row is
  one stable Pattern across windows; observations, distinct sources, window count,
  frozen candidate snapshot, and later reinforcement remain separate values.
- Render Pattern lifecycle and future-alert use as separate, icon-labelled states; do not
  collapse them into one compound status or force them into rigid table columns that make
  the Pattern name unreadable. Lifecycle/use filters must be evaluated by the server over
  the complete result set before pagination. A pending Candidate may be opened from its
  Pattern detail or the batch review inbox, but both links must address the same
  server-owned Candidate and must not create parallel review state.
- Keep Pattern inventory and confirmed Memory record inventory distinct. `/memory`
  manages Pattern lineages; `/memory/records` searches actual `MEM-*` records by IDs,
  lesson text, source lineage, and facets. Record detail owns Business Lesson,
  applicability, retrieval activation, usage history, read-only match diagnostics, and
  versioned revision entry. A confirmed Candidate/alert action must link to this record
  detail rather than presenting a terminal Candidate as still awaiting review.

## DEV Validation Surfaces

- DEV Memory/corpus pages orchestrate server-owned cohorts and official services. React
  may search, filter, paginate, select, and invoke one alert; it must not load PKL files,
  derive fingerprints, decide readiness, construct Patterns, approve candidates, or
  calculate Base/Memory/Tenant/Effective decisions.
- Corpus execution mode, ordering metadata, rerun eligibility, and label comparison are
  server-owned. Render alerts by the canonical display sequence returned by the API; do
  not reorder them by readiness, ID, or vendor export position. In
  `interactive_exploration`, any non-running alert may be selected and a completed alert
  exposes an explicit rerun command; show that reruns create a new Runtime Run, reuse the
  same source Pattern observation, and are not valid time-causal evaluation. Strict
  chronological evaluation remains a separate batch/eval path. Historical disposition
  labels stay hidden per alert until a Runtime decision exists, and are displayed as
  operational outcomes rather than independent detection truth.
- Corpus filter continuity may be retained in tab-scoped browser storage, but selected
  alert detail is page-local and must not be restored or auto-opened on navigation. The
  unprocessed-only switch defaults off. Ignore its legacy saved default without clearing
  other filters; preserve subsequent explicit choices across navigation and refresh. The
  list query is server-filtered and paginated; React must not fetch the complete corpus
  and repeat those filters locally. When a processed alert creates a
  Pattern Candidate, keep the current page visible and render a persistent review link;
  the prominent safety band must only project server-returned Effective Decision fields.
- Alert-to-group navigation uses the server's `group_id`, clears conflicting alert/search/
  status filters to show the complete group, and offers return to the prior filter/page.
  Group search uses only the supplied group catalog (including singletons and missing
  fingerprints); keep rendered options bounded without truncating the searchable catalog.
  Navigation must not start analysis or open an alert's execution detail implicitly.
- The corpus execution monitor polls only the selected active alert's lightweight
  execution endpoint. Render the server projection of persisted Runtime steps, provider
  journal, durations, bounded counts, decision, and Pattern write; do not estimate phase
  progress in React or expose raw evidence, prompts, model responses, or secrets.
- Do not use one mutation's global pending state to lock the corpus table. Keep local
  pending state by alert ID and poll the server's lightweight `/activity` projection so
  different alerts can run concurrently while duplicate clicks across browser sessions
  remain disabled. Poll quickly only while executions are active and back off while the
  workbench is idle. A process response is only a `202 Accepted` claim acknowledgement;
  it does not contain the final analysis. Keep the alert visibly running, use
  `/activity` and `/execution` for progress, and refetch the authoritative page after the
  claim disappears rather than treating the mutation response as completion.
  A fresh terminal execution also triggers one final page refresh if activity polling missed
  the claim. Compare with the pre-submit Run ID so an old completed Run cannot finish a rerun.
  Terminal status must not remain a local spinner merely because the row is absent or the
  final list read fails. Keep the explicitly focused result through readiness/comparison changes
  caused by processing; changing the user's filters clears that temporary focus.
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
- The user-facing corpus route is the **告警研判演练** workspace. Keep the stable
  `/corpus-validation` route for compatibility, but do not expose “语料验证” as the
  primary analyst concept.
- Its recommended rehearsal manifest contains exactly two server-owned, result-oriented
  stories over the same vendor rule: one context-only Memory use and one exact-match
  Decision reuse. Keep full-corpus search below those recommendations; do not turn the
  recommendation panel back into a general capability catalog.
- Rehearsal metadata may only set existing filters and selected alert. It must not run an
  alert, seed Candidate/Memory state, predict a verdict, or hide a server-reported
  missing/regrouped target. Full behavior-group selectors keep the stable Group ID and
  add a canonical behavior summary so repeated vendor rule names remain distinguishable.
- Running an alert must keep the analyst at the initiating row. Show inline/live status
  and expose an explicit **查看结果** command after completion; never auto-scroll to the
  lower execution trace.
- Keep environment, isolated SQLite, model/reasoning, role verifier, mock/off providers,
  tenant policy, and action-execution labels visible so screenshots cannot be mistaken
  for STG/production evidence. If the explicit PingAn DEV launcher enables all tenant
  policy layers, show deterministic/advisor/software-path status from the server safety
  contract; never infer it from client configuration, and continue to show external
  actions as disabled.
- A Memory record is not permanently classified as exact or context-only. Show whether
  it owns a reviewed Decision Directive; exact applicability may use that Directive,
  while a partial retrieval of the same record remains context-only. Record and revision
  pages must state this distinction.
- Keep implementation vocabulary out of primary analyst surfaces. For one alert, render
  Memory use as `未使用历史经验`, `仅作研判参考`, or `已复用审核结论`. Render record
  availability as `已开放给新告警` or `暂停用于新告警`; disabled retrieval means the
  record is not used at all, not context-only. Call a tenant Memory Profile a `匹配规则版本`
  in user-facing detail and hide the healthy/current profile state from primary lists;
  preserve Profile, Directive, retrieval, and context-only terms only in explicit technical
  audit payloads.
- Memory Center must keep lifecycle and future use separate. Lifecycle says only whether
  samples are accumulating, a candidate awaits review, or an experience has been persisted.
  Future use says `尚未开放`, `仅供研判参考`, or `精确匹配可复用结论`; an exact-capable
  record may still be context-only for a partial match. Pattern inventory is ordered by
  latest observed sample, while confirmed-record inventory is ordered by latest update;
  show those sort rules in the UI.
- Record and revision surfaces must make the future-use mode a visually primary state,
  especially `仅供研判参考`. Candidate confirmation may open retrieval immediately with
  a server-audited reason; any empty reason input shown afterward belongs only to the next
  pause/reopen transition and must be labeled that way.
- Keep pause/reopen controls collapsed behind an explicit `管理使用状态` command so they
  do not look like unfinished confirmation fields. Deprecation is the terminal
  `废止这条经验` action: require an explicit reason and confirmation, and keep it distinct
  from temporary retrieval pause. Make the source-Candidate review a visible command on
  the Memory record page rather than a low-emphasis audit link.
- In DEV Runtime traces, name the first phase `来源适配与标准化 / Adapter & Normalize`:
  the active tenant/vendor Adapter parses and projects source data into the canonical SOC
  contract; the label must not imply generic field cleanup only or hard-code PingAn.
- The optional semantic-review phase follows Adapter normalization. Keep the server-owned
  phase status and full request/result in the existing JSON audit viewer; do not add a separate
  before/after comparison component. Completed reviews show a checkmark and the server's adopted count; notes and coverage
  limits remain expandable. Raw `partial` is preserved in JSON and does not by itself mean
  execution failed. Failed/skipped reviews retain their unavailable state and explanation.
  Shadow proposals are observation-only,
  failed review means the Adapter path continues. Early policy-only runs retain a gray skipped
  semantic phase and audit entry with a server-owned skip reason, never a fabricated review result.
  Unrelated legacy runs without a saved review keep their existing phase list.
  Direct handling displays matched enterprise rule/disposition or the reused Memory separately;
  the result-card source summary and trace use the same enterprise-rule terminology, with
  ignore/transfer wording taken from server `recommended_handling`, never a fixed transfer label.
  no unknown verdict/partial-evidence metrics masquerade as a failed model judgment.
  Viewing the audit never invokes a model or applies changes.
  Apply results feed the standard alert and downstream Memory conditions; render the server's
  saved effect, not the current rollout flag over historical runs. Semantic matching components
  (detected file, detector subject, process relation) have Chinese labels; preserve raw typed
  values and the existing per-behavior checkboxes. Do not display arbitrary metadata as required
  behavior merely because a model extracted it.
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
- Effectiveness formulas and relationships come from the server read model. The primary
  drill-down is `Rule Code -> 同类行为 -> Memory`; use canonical detection identity when
  no Rule Code exists. Keep Pattern/Profile terminology in technical audit views.
- Keep the operations summary decision-oriented: show processed volume and render unavailable
  metric values as `--`. Do not add a statistics-explanation banner or expose
  conclusion-maintenance and verification-coverage terminology in the primary UI. Group the eight
  server metrics under triage quality, automation safety, transfer quality, and workload reduction;
  exact coverage, availability, and denominators remain available through the API and technical
  audit.
- Show `context-only` as “仅供研判参考” and directive use as “直接复用结论”. Only the
  latter may display attributable accuracy. Show final-outcome coverage and denominators,
  and never infer Memory value from retrieval count alone.
- Do not actively probe Kafka, recompute aggregates, infer overall health, or turn
  `not_measured` into healthy zero. Label SQLite and fixture/Playwright evidence as
  local/test, separate from deployed Gateway, production telemetry, and SLO proof.
- Candidate and Memory technical IDs are secondary audit identifiers, never the primary
  completion message. A completed run must link directly to the pending Candidate review
  or confirmed Memory record. When Tenant Policy changes the operational action without
  changing the technical verdict, show the model verdict, base action, policy reason, and
  final action as separate steps; do not compress them into an unexplained slash pair.
