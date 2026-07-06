---
name: soc-alert-triage
description: Use for SOC alert triage, analyst-facing evidence summaries, verdict review, and safe next-step planning. Trigger when investigating security alerts, review queue items, false-positive reasoning, suspicious events, or alert disposition.
allowed-tools:
  - ask_clarification
  - present_files
  - read_file
  - task
---

# SOC Alert Triage

Use this skill for the general SOC triage loop.

## Operating Rules

- Treat an alert as a hypothesis backed by evidence, not as ground truth.
- Separate facts, inferred facts, conflicts, and missing context.
- Preserve evidence paths and field names in the explanation.
- Prefer the SOC runtime's normalized fields, field trust, conflict reports, and review context over raw vendor fields.
- Do not perform or claim response actions. Propose actions and route high-risk actions through approval.
- Keep conclusions reviewable: verdict, confidence, evidence, uncertainty, recommended next step.

## Knowledge Boundary

- Keep this skill tenant-neutral. It may describe general SOC triage method, but must not embed customer-specific rule codes, internal domains, department names, account patterns, allowlists, suppression templates, or response IDs.
- Customer-specific operational knowledge belongs in tenant-scoped memory, policy/config, adapter mappings, or eval fixtures.
- Vendor field names should be handled by normalizers/adapters before this skill sees bounded context.

## Triage Shape

1. Identify source, rule/detection, severity, and affected entities.
2. State the strongest evidence and the weakest evidence.
3. Call out conflicts, especially attacker/victim role conflicts and asset ownership ambiguity.
4. Decide whether this is likely true positive, false positive, suspicious, unknown, or needs review.
5. Propose the next safe action: gather context, open review, replay, correct, escalate, or request approval.

## Safety Boundary

Never bypass SOC core services, review queue, approval inbox, or audit records. If a user asks for blocking, isolation, account disablement, suppression, rule edits, or production changes, produce an approval-oriented plan rather than claiming execution.

When running under the SOC Lead Agent and a concrete action should enter SOC policy/approval handling, emit only a structured candidate in `<soc_action_proposal>{...}</soc_action_proposal>` with `route`, `action`, `reason`, `payload`, and `confidence`. High-risk proposals enter approval. Read-only proposals such as `asset.lookup` enter the guarded SOC runtime bridge. Do not infer execution from the proposal, and do not claim lookup results until a SOC action result is returned.
