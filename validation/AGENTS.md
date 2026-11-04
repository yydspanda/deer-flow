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
