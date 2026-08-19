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

## Canonical Workflows

- Build the corpus with
  `backend/.venv/bin/python validation/compact_zeus/corpus/build_alert_validation_corpus.py`.
  Generated data is grouped under gitignored `validation/compact_zeus/data/` by corpus,
  audit, review, compaction, and exploration purpose.
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
