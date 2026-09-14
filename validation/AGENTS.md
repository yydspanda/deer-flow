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
