# SOC Validation Guide

This directory contains offline/local validation builders, legacy reference source, and
corpus tooling. It is not production Runtime code. Generated alert-derived artifacts are
sensitive, gitignored evidence and must never become application imports.

## Data And Safety

- Authoritative raw corpora live under gitignored `datas/source/`; legacy exact demos add
  lineage and remain explicitly marked. Historical model responses are not analyst truth.
- Load pickle inputs only through the restricted loader. Keep raw payloads, PKL, XLSX,
  SQLite, rich HTML/Excel reports, credentials, and generated results out of Git.
- Production modules may copy a reviewed generic algorithm from validation, but must not
  import `validation.*` at runtime.
- Live LLM/provider runs require explicit confirmation and must preserve model/config,
  prompt, parser, Grounding, timing, token, mock/live, and data-source provenance.
  Structural success is not model accuracy without independent labels.
- The isolated `compact_zeus/audits/normalization_assistance_trial.py` spike may compare
  one message's extracted facts through production canonical consumers. It is not an
  online normalization service: never write its experimental facts to operational
  Memory/index stores or present its small target whitelist as complete source coverage.
  Check provider truncation before JSON parsing. Wire-level reasoning diagnostics must
  distinguish requested thinking settings from returned fields; offline SDK tests do
  not prove remote provider behavior. Do not print credentials or reasoning text.
  Its `--max-tokens` option is an explicit per-experiment output budget, not a global
  SOC default. Record exact controls and never escalate the budget or retry silently.
- `normalization_assistance_consumers.py` is the associated offline consumer audit.
  Frozen model answers and literal source-field test controls must be labeled separately;
  controls do not measure LLM extraction quality. A shared fingerprint with different
  detector labels reveals feature blindness, not an observed incorrect Memory decision.
  File/path variation alone may be valid generalization. Never turn audit controls into
  production mappings or modify the live Profile/index/Memory to make a test pass.
- `quoted_kv_parser_audit.py` compares the frozen v2 matcher with current production
  parsing and bounded evidence for at most 20 selected messages. It is read-only and
  makes no model or Memory calls. The older spike's KV inspection remains explicitly
  a v2 baseline; current syntax coverage comes from the production parser, not validation.

## Canonical Workflows

- `compact_zeus/audits/memory_scope_coverage.py` reads frozen successful apply runs
  through SQLite `mode=ro` and compares the historical/current PingAn projections.
  Its reference scope is synthetic, not an approved Memory. It never submits alerts,
  invokes models, or changes operational records; matching structure is not accuracy.
  Keep the generated report in ignored validation storage with source-request hashes.

- `normalization_workbench_review.py` submits up to ten explicitly approved alerts to
  the loopback DEV Workbench in shadow mode. These are real operational DEV runs;
  the frozen supplements are applied only in a separate offline consumer comparison.
  Read complete persisted requests for hash checks, not the null-pruned Web DTO. Wait
  for the new run ID and released execution lease, not a previous completed trace.
  `--collect-only` never resubmits. Distinguish Profile-only changes from model additions,
  use the complete model projection for field visibility, and do not claim apply-mode
  decision accuracy or silently migrate Memory/index groups.

- `normalization_runtime_review.py` exercises the production semantic-review node on one
  explicitly selected real alert. `--confirm-live` permits one review call, not automatic
  output-budget escalation. Downstream analysis is deliberately stubbed to verify wiring;
  its verdict is not quality evidence. Save request/result/timing and source hashes in a new
  protected directory, with no operational Memory/index writes. Provider `length` remains
  a failed review even when the existing Runtime path can finish.
  Export the current review output JSON Schema beside the prompt. Record opt-in PingAn
  feature facets separately; structural presence is not fingerprint quality or evidence
  that an operational Memory was retrieved. Include merger/contract/consumer hashes.

- Build the corpus with
  `backend/.venv/bin/python validation/compact_zeus/corpus/build_alert_validation_corpus.py`.
  Generated data is grouped under gitignored `validation/compact_zeus/data/` by corpus,
  audit, review, compaction, and exploration purpose.
- The full labeled DAMS corpus uses `corpus/build_dams_labeled_dataset.py` under
  `validation/compact_zeus/`. It discovers all extracted batches under
  `datas/source/dams_exports/` by CSV header; unknown schemas fail explicitly.
  Stage expanded PKL/index/payload-store outputs separately, verify old-row retention
  and chronology, then replace the complete artifact set without resetting SOC Memory.
  Include `--supplement-pickle` with the curated 212-case corpus to retain special-test
  alerts in the Web. `--existing-dataset` supports append-only supplementation without
  re-reading CSVs; existing IDs win and new special cases receive no operational labels.
- `./scripts/soc-runtime-validation.sh checkpoint-d` covers deterministic D0-D6.
  D7/D10 are explicit-cost live boundaries; D8 Grounding and D9 Decision are
  deterministic; D11 is full-corpus deterministic compatibility/reexecution stability.
  D6-D11 are evaluation/maintenance stages, not extra Runtime nodes.
- `validation/compact_zeus/e2e/run_ten_alert_e2e.py` is the canonical one-directory
  ten-alert journey. It uses production services with isolated SQLite and explicit model
  confirmation. Its knowledge-review output is inert and is never auto-promoted.
- Internal PKL scale validation uses
  `validation/compact_zeus/internal_batch/run_pingan_runtime_batch.py`, the production
  `SocAnalysisService`, restricted loading, explicit live confirmation, protected output
  modes, source/payload/model/config hash resume keys, and staged expansion `5 -> 50 -> all`.
  It must not silently invoke MCP enrichment.
- `./scripts/soc-alpha-acceptance.sh all` is local/test Alpha evidence only.
  `./scripts/soc-alpha-readiness.sh all` packages the technical gate but keeps release
  decision and production readiness pending owner review.

## Evaluation Boundaries

- Rule-refinement validation keeps a structurally readable proposal with unknown
  filter fields as `needs_revision`, preserving its text and all field issues.
  Only derived field names from the actually displayed logs enter the prompt catalog;
  do not guess aliases or promote such a draft to a ready recommendation. Saved-answer
  recovery makes no model calls and does not establish semantic correctness or reduction.
  A frozen-evidence independent reassessment excludes previous model proposals and
  answers from its request, preserves the original logs, and must not claim the original
  two-phase review completed. Record requested JSON mode separately from verified
  provider enforcement; a parseable answer does not establish semantic correctness.
- `rule_refine/implementation/step07_surge_runtime/` ships the legacy LOCAL
  background integration as an offline installer against reviewed 6W hashes. Keep
  the previously delivered payloads unchanged. Runtime modules are copied into the
  target app; they never import validation tooling. Historical comparisons use
  original alert business timestamps and nearest 20 distinct prior alerts, not task
  insertion times or previous LLM verdicts. DEV seeding writes only explicitly
  synthetic, fixed-ID rows in the existing project SQLite; reruns verify content and
  remain idempotent. Real model acceptance is a separate LIVE command. No program
  filter evaluation or automatic rule application belongs in this path. Input
  budget is operator-configurable; do not add an unsolicited output-token quota.
- `rule_refine/implementation/step07b_real_csv_acceptance/` replays the real
  surge CSV plus genuine available history through the installed legacy Runtime.
  LOCAL source rows remain read-only; imports enter a disposable SQLite, not the
  project task table. Preserve original IDs/business times, exclude synthetic
  fixtures, and disclose historical gaps and source coverage. A counterfactual
  rule removes the previously added exclusion only in memory; the removed clause
  audit and expected answers must never enter model prompts. Offline transport
  fixtures do not prove live recommendation quality or Celery dispatch.
- `rule_refine/implementation/step07c_review_sampling_fix/` patches review
  selection without raising context budgets or adding model calls. Find a whole
  unseen alert that fits before filling with old citations; then interleave new
  and cited originals. Preserve the full draft and explicitly mark citations
  whose original logs are not displayed. Final references must still be grounded
  in this request's visible logs. Frozen old selections/results and report-byte
  recovery remain compatible; do not silently replay completed model work.
- `rule_refine/implementation/step07d_exclusion_scope_fix/` changes the two AI
  tasks without a topic/field-name denylist, mandatory condition count, or new
  output schema. Generate concrete behavior exclusions from actual originals;
  review only the candidate filter/status/references plus raw evidence, keeping
  generation prose/confidence/extra explanations out of that review context.
  Full answers remain saved. Prompted scope reasoning is not a programmatic
  guarantee of semantic quality; report readiness accordingly. New report text
  is gated by frozen request metadata so old completed/interrupted publications
  remain byte-compatible. The combined installer/retest stops on install failure
  and then uses the original 7B manual LIVE command, without automatic dispatch,
  rule application, extra model requests, or whole-corpus filter execution.
- `rule_refine/implementation/step07e_event_field_boundary/` separates visible
  evidence from candidate condition fields using exact, reviewed source roles.
  Known detection-result metadata stays in original logs for context but cannot
  become a new exclusion predicate. Keep topic/path provenance; never infer roles
  from name fragments or restrict candidates to fields already used by a rule.
  Unclassified candidates are not verified pre-detection or engine-supported
  fields. Preserve invalid drafts for the existing review stage; an unresolved
  metadata condition remains manual review without deleting/rewriting clauses.
  Freeze the usage contract in new requests and recompute it during validation;
  old request/parser/report semantics remain byte-compatible on recovery. Do not
  claim the named-field check proves semantic scope or detects every embedded
  detector label in arbitrary strings. The original real-CSV LIVE tool still
  controls the same two model requests and preserves prior results.
- `rule_refine/implementation/step07f_repair_first_review/` keeps the reviewed
  metadata boundary while clarifying repair-before-withdrawal. Do not force a
  proposal or inject a case's expected filter; missing counterexamples constrain
  claims, not automatically every advisory candidate. Its review-only tool verifies
  a complete saved Runtime job and generation-to-review link, then reuses the exact
  prior review evidence with a fresh prompt. Old review prose never enters the new
  request. No re-sampling, DB recall, budget escalation, or generation request is
  permitted; at most one new SDK request writes a separate report and preserves
  raw output before validation. Current online configuration is not reverified by
  this saved-evidence tool and must not be claimed. Preserve frozen E request and
  D/E completed or interrupted report compatibility.
- `rule_refine/implementation/context_input_probe/` is a standalone synthetic
  capacity experiment, not a rule-refinement run. It reads the selected upstream
  route as AST literals and, after LIVE confirmation, makes one direct HTTP POST
  without importing the application, using business data, or changing configuration.
  Disclose that local adapters/proxies are bypassed. Synthetic unit counts are not
  measured tokens; acceptance above 262144 requires successful provider usage above
  that value. Preserve explicit input/context errors separately from auth, transport,
  or missing-usage outcomes. No automatic retries, size escalation, or output quota.
  Never save authorization headers or credentials with diagnostic artifacts.
  The separately requested `RUN-WITH-MAX-INPUT.sh` adds an actual top-level
  max_input_tokens=1048576 to the same baseline request; keep the original probe
  unchanged. Test the transmitted bytes, not just preview flags. Never remove a
  rejected parameter and retry, or claim upstream enforcement from HTTP success.
  `RUN-MULTITURN.sh` is a separate explicitly requested three-turn experiment.
  Each request includes full prior originals and actual assistant replies, with
  max_input_tokens=1048576 retained. New-message estimates are not full-request
  counts; only one successful request's reported input above 512000 proves the
  500K acceptance target. Never sum repeated-history usage as context capacity,
  truncate/summarize history, fabricate a failed turn's reply, or continue after
  failure. Preserve the earlier single-request probes and reports unchanged.
- `rule_refine/implementation/step07g_release_disable/` prepares the legacy
  deployment launcher for a release that excludes rule monitoring. Patch only the
  reviewed `run.sh` by exporting `RULE_REFINE_MONITOR_ENABLED=0` before any child
  process starts; preserve all other bytes and permissions. Keep original backup,
  exact-source checks and idempotence; reject unknown source rather than replacing
  the whole launcher. Do not run the launcher in tests: it contains existing live
  task cleanup and service startup commands. Keep LOCAL launchers/configuration and
  the previous delivered payloads unchanged. This establishes source-level inherited
  defaults, not production process state or a guarantee against external launch
  overrides; complete task-registration dependencies still belong in the release.
- `rule_refine/implementation/step08_pub_queue/` is historical legacy queue work,
  not DeerFlow Runtime. Its installer is withdrawn: do not recommend, rebuild, or
  execute `INSTALL-PUB-QUEUE-27.sh`. The user switched to reviewed manual steps.
  Its Shell, Python installer and builder entry points now exit immediately;
  preserve that retirement guard and the unreachable historical payload.
  Consolidate PRD/LOCAL automatic alerts on Pub with production capacity 27; preserve
  STG configuration and its existing routing. STG ordinarily queues V32, retains
  V32 capacity/bookkeeping, and uses the user's controller conversion to Qwen38_27B;
  its original priority-zero Pub exception remains. Do not introduce a Qwen alias
  on the R queue or change STG capacities (V32/My 6, Pub 15).
  Keep shared `celery_task.conf` / `celery_task.detector.conf` for their existing
  consumers; use separate PRD configurations for the consolidated Pub worker at 27.
  Preserve LOCAL worker capacity 2, priority/circuit/pending bookkeeping, 6F task
  registration, and any installed 7G production-disable marker. Inspect model
  defaults as well as explicit task arguments; preserve the user's STG controller
  branch. Never execute a legacy launcher or cleanup as an offline test.
  User-returned evidence currently includes ten Python syntax checks, static checks
  of four worker definitions, Mac/Flower startup, and manual inspection of
  `get_selected_model`. These do not verify production alert assessment/callbacks
  or effective model/Celery capacity. Keep backups and private evidence outside
  publishable source; do not migrate or purge queue tasks as part of validation.
- `rule_refine/implementation/model_concurrency_probe/` is the separate requested
  model-interface speed probe; see its `README.md` and `probe.py`. The planned test
  sends 27 concurrent requests with approximately 5000 input tokens each. Report
  measured provider usage/timing separately from estimates and retain failures.
  User-returned live evidence contains 26 successes and one HTTP 502 in a 27-request
  batch (5045 input tokens, OK-only output); client in-flight peak was 27.
  Direct model-interface results do not prove Celery worker/semaphore capacity,
  workflow assessment, or callback delivery.
- `rule_refine/implementation/step09_operator_contract/` upgrades the user's
  2026-11-05 deployed legacy source snapshot to the six new small-rule operators:
  `Eq`, `Ne`, `Contains`, `NotContains`, `StartsWith`, `EndsWith`. Update prompts,
  schemas, comparison consumers, sync/async submission validation, and offline
  fixtures together; do not merely normalize at the HTTP boundary. New replies
  and submissions reject old spellings. Only version-bound saved-evidence reading
  may use historical names, preserving raw bytes and report lineage; a fresh
  review uses a copied canonical draft. Unsent frozen old prompts must not silently
  run after upgrade. Negative comparisons never turn missing/null into matches.
  The installer checks the updated ZIP's source hashes, runs isolated offline
  regressions before backup/write, and retains rollback/idempotence. Never alter
  queues, STG, monitor-disable settings, private credentials or deployed services
  as part of this contract update. Keep old delivered installer payloads frozen.
  The supplied API document still differs on flat terms versus `terms` nesting;
  preserve the deployed client's flat payload and gateway signing, and distinguish
  offline contract tests from live service/engine acceptance.
- `rule_refine/implementation/step09a_complete_output/` builds on installed step09
  sources. Put the candidate filter and references before prose and add a compact
  output reminder after evidence within the same budgeted, hashed request. A stop
  finish reason does not prove the required response fields exist. Keep missing
  conditions/references blocked; never synthesize them from prose or add model calls.
  New output-contract requests show structured errors and inert saved-answer excerpts
  in failed reports; hide reasoning in the display and preserve the raw response.
  Gate diagnostics by frozen request version so earlier artifacts remain compatible.
  Combined install/retest must stop on install failure, use the same project root,
  and require the existing LIVE input before up to two new SDK requests.
- `rule_refine/implementation/step10_topic_filter/` adds an explicitly opted-in
  legacy topic-ingress advisory path. It is not DeerFlow Runtime. A per-rule surge
  still recalls current originals and the nearest 20 earlier same-rule alerts,
  but the new AI generation/review must assess event removal before **all** rules
  on that topic. Keep the original per-rule prompts, saved artifacts, and LOCAL
  test flow compatible. Do not present same-rule evidence as all-topic coverage.
  Preserve the reviewed detection-metadata field boundary; the separate topic
  review records field-role reasoning and impact on other detectors. These checks
  are not engine compatibility proof or a semantic safety guarantee.
  Derive one executeType (0/1/2/3) from all recalled current alert envelopes;
  mixed/missing/invalid values require review, never infer it from PRD/LOCAL or
  widen it to -1. Submit a batch list with flat A/B terms through the existing
  signed `syncSmallRule` client. The user authorized direct submission of reviewed
  proposals and delegated activation to ZEUS; do not require or assert a default
  disabled server state, and do not call a separate enable endpoint.
  PRD/LOCAL opt in through `config/rule_refine.topic.json`; STG is unchanged.
  PostgreSQL reads only original task columns in a read-only transaction, without
  schema changes or prior model responses. Use `root_config.DATA_DIR` for compact
  NAS records: references, bounded excerpts, final advice, hashes, and receipts,
  not full cases/messages/model replies. Persist the submission marker before POST,
  bind resumed candidates to their reviewed hashes, and do not automatically resend
  an ambiguous request. Exact-payload deduplication is scoped to the shared NAS ledger;
  it is not a server-side exactly-once guarantee.
  Installation runs isolated regressions before backup/write and never enables
  monitoring. The separate enable command changes the reviewed PRD launcher marker
  only on explicit invocation and starts no service. The manual real-CSV command
  requires LIVE before two new model requests and at most one client submission;
  unlike older rule-advice tests, it can create an actual ZEUS small rule. Preserve
  historical-replay/counterfactual provenance and share the target delivery ledger.
- `rule_refine/implementation/step10a_topic_json_fix/` builds on frozen installed
  Step10 after the first intranet topic generation rejected duplicate JSON keys.
  Only identical JSON values under the same object key may be collapsed; compare
  exact types, array order and numeric literals. Conflicting members reject the
  entire answer, never choose first/last or reconstruct conditions from prose.
  Preserve original fence/tail validation and all field/reference/topic checks.
  Compact diagnostics retain bounded key paths/counts, hash unknown keys, and never
  persist values or raw replies. The old failed answer was not saved, so its exact
  duplicate location/conflict is unknown. Keep the original reply hash, no extra
  model calls, no submission retry, and no rewriting completed/failed jobs. The
  original manual CHECK uses the updated runtime; activation needs the new ENABLE
  command because its source-hash manifest changes. Installer-only operation still
  starts no services and leaves monitor settings, queues, and STG untouched.
- `rule_refine/implementation/step10b_topic_expression_review/` handles topic
  expression failures without a third model call. Explicitly constrain expression
  grammar in the topic prompt. Only generation may opt into preserving an invalid
  expression as an inert draft; every condition, reference and field-role check
  must still pass before the existing independent review can attempt a correction.
  Never guess operators, invent missing terms, delete conditions, or turn invalid
  expressions into executable ones in code. Review, delivery and legacy callers
  remain strict by default. Save bounded alias/position diagnostics, not condition
  values or raw replies, and keep old failed jobs terminal. The observed intranet
  error can mean either an undefined alias or a missing operator; without the old
  raw reply its actual cause is unknown. Keep frozen Step10/10A deliveries intact.
- `rule_refine/implementation/step10c_topic_condition_tree/` removes parallel
  model-written aliases, expressions and field-role lists from new topic replies.
  Each leaf binds its predicate to its own field-role judgment; explicit `all`/`any`
  groups determine the boolean structure. A bounded compiler assigns flat aliases
  and fully parenthesized expressions for the unchanged ZEUS payload, preserving
  every leaf, literal and group. Never infer a missing group, drop/merge a leaf or
  accept old flat replies under the new prompt. Repeated fields require consistent
  roles and every occurrence's basis is checked. The review sees predicates/groups
  only, excluding generation role judgments and prose. Compiled replies still pass
  all original event-field/reference/topic and delivery checks; role judgments are
  not engine availability or safety proof. Keep old TopicProcessor default behavior
  for compatibility; production background and the **new** CHECK command explicitly
  select `topic-condition-tree-v1`. Frozen old CHECK uses the old contract. Record
  source-tree/compiled-filter hashes in compact records, no extra SDK calls or POSTs.
  New ENABLE updates source hashes only. Offline truth-table/transport tests do not
  prove that the live model reliably emits the new format; report that separately.
- `rule_refine/implementation/step10d_topic_reply_diagnostic/` is a standalone
  diagnostic after Step10C review returned 59 conflicting duplicate members.
  Keep current prompts/sampling/strict parsing and frozen delivered files unchanged.
  Capture final content before parsing, at most 64 KiB per phase, with original
  byte count/hash and explicit truncation; never save separate reasoning or full
  request messages. Raw final content can echo sensitive logs and stays internal.
  Metadata uses nested allowlists. Override the entire delivery entry point before
  ledger/submission handling; even valid reviews must not POST or touch shared
  delivery state. LIVE permits at most two SDK calls, with failure stopping progress;
  no automatic retry or service restart. Save in an isolated DATA_DIR diagnostic
  directory with restrictive permissions. Old unsaved failures cannot be recovered;
  a new run is not proof of the old cause or a production fix.
- `rule_refine/implementation/step10e_topic_advisory/` implements the user's later
  clarification: draft a small rule from surge commonalities for operations review,
  typically two or three useful conditions, with a single sufficiently specific
  condition allowed. Do not require proof of all-topic or future-event harmlessness,
  invent a fixed minimum condition count, or import example fields/type scopes.
  Only the tree AI prompt, trailing reminder and prompt version change; parser,
  compiler, event-field boundary, operator spellings and submission remain intact.
  Review original evidence and actual condition scope briefly; operational acceptance
  belongs to ZEUS/operators. The combined command runs offline tests/backup/install
  before the existing LIVE-gated real-CSV path; installation failure must prevent
  that path. No automatic monitor enabling or restart. Frozen deliveries and prior
  diagnosis results remain unchanged; old hash-pinned diagnostics reject new sources.
- `rule_refine/implementation/step10f_topic_receipt_array/` accepts the batch
  creation API's single-element ID list for the one-rule topic POST. Normalize
  that sole element through the original scalar validator, retain code/success
  checks, and keep the persisted receipt scalar. Never choose among multiple IDs
  or infer old missing IDs. The observed Step10E response was code 200 / `[153]`,
  but the old ledger discarded the list and stored null. Old unknown/job artifacts
  stay immutable and prevent resends; success logs are external evidence, not an
  ID recovered from saved NAS state. The user subsequently confirmed rule 153 is
  visible in ZEUS STG; creation is verified by that report. The user subsequently
  reported executing the receipt installation step; activation remains unreported.
  The installer does not rerun generation or
  POST. Any later historical reconciliation requires explicit evidence binding.
- `rule_refine/implementation/step10g_production_prepare/` prepares the legacy
  release on the intranet Mac before the user's ordinary full-project packaging.
  Validate the existing rule-configuration catalog, not alert-test CSVs; copy its
  original bytes to a project-relative `.snapshot` so the legacy `*.csv` ignore
  rule cannot omit it. PRD selects a separate topic config; LOCAL remains unchanged
  and STG does not enter this topic path. Only PRD enables the launcher monitor
  block. Preserve every other launcher byte and never execute its live cleanup or
  startup commands. Run isolated regressions before backups/writes, retain rollback
  and idempotence, and fail on missing/conflicting catalogs or unknown source.
  Never import private root_config for preparation or require a production NAS
  mount on the Mac. NAS persistence still uses the existing PRD DATA_DIR at runtime.
  Prepared source/configuration is not evidence of actual production triggering,
  remote availability or the catalog's online freshness. Old enable scripts remain
  frozen and are not the activation path after this release preparation.
- `rule_refine/implementation/step10h_macos_prepare_fix/` fixes the preparation
  tool's comparison of aliased macOS temporary paths. Canonicalize the temporary
  inspection root before using both settings readers; do not remove configuration
  checks or alter application payloads. Reproduce the old failure with a symlinked
  TMPDIR, then test the exact command and its idempotent repeat with that alias.
  Keep Step10G artifacts frozen. The reported failure occurred before release
  writes; do not request model/ZEUS resubmission or change production business
  settings to work around a local preparation bug.
- `rule_refine/implementation/step10i_poll_interval/` changes only the LOCAL/PRD
  legacy topic tick interval from 30 to 120 seconds and its isolated test expectation.
  Preserve expiry=25, queue/model capacity, STG, surge thresholds, and other schedule
  bytes. Validate the registration AST and run staged offline tests before backup/write;
  keep rollback and idempotence. Do not rewrite frozen older deliveries or start services.
  Celery Beat must reload the configuration through ordinary deployment; worker-only
  restart is insufficient. Installer success is not proof of a live 120-second cadence.
- Label governance uses sealed manifests and independent analyst truth. Quality reports
  must separate parser/structure, retrieval, scenario/role, decision, automation, and
  human-label metrics.
- Correlation labels distinguish `same_incident`, `related_distinct`, and `unrelated`;
  retrieval metrics and duplicate-identity metrics are separate. Evaluation thresholds
  never become production suppression rules.
- Memory evaluation keeps construction alerts disjoint from held-out query alerts and
  requires pairwise relevance labels. Pending/simulation labels cannot prove rollout.
- Rollout rehearsals are simulations unless deployed telemetry, accountable owners,
  cohort enforcement, and executable rollback exist. A simulated pass leaves real gates
  open, stage `not_started`, and production effects disabled.

Keep a short README in each generated-output builder directory explaining inputs,
outputs, sensitivity, and the exact command. Do not hand-edit generated evidence.
