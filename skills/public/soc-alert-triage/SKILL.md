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

- Trust alert admission as detection provenance: the configured upstream rule, detector, or model matched and emitted the alert. Do not demand a second source to prove that hit occurred.
- Trust reviewed adapter field semantics within their exact declared scope. Separately adjudicate scenario correctness, effect, material impact, disposition, and response authority.
- Separate facts, inferred facts, conflicts, and missing context.
- Preserve evidence paths and field names in the explanation.
- Prefer the SOC runtime's normalized fields, field trust, conflict reports, and review context over raw vendor fields.
- Do not perform or claim response actions. Propose actions and route them through deterministic policy and authorization; use human approval when the active policy requires it.
- Keep conclusions reviewable: verdict, confidence, evidence, uncertainty, recommended next step.

## Knowledge Boundary

- Keep this skill tenant-neutral. It may describe general SOC triage method, but must not embed customer-specific rule codes, internal domains, department names, account patterns, allowlists, suppression templates, or response IDs.
- Customer-specific operational knowledge belongs in tenant-scoped memory, policy/config, adapter mappings, or eval fixtures.
- Vendor field names should be handled by normalizers/adapters before this skill sees bounded context.

## Triage Shape

1. Identify the trusted detection hit, source, rule/detection, severity, and affected entities.
2. State the strongest evidence and the weakest evidence.
3. Call out conflicts, especially attacker/victim role conflicts and asset ownership ambiguity.
4. Decide whether this is likely true positive, false positive, suspicious, unknown, or needs review.
5. Propose the next safe action: gather context, open review, replay, correct, escalate, or request approval.

Before finalizing, answer four questions explicitly:

1. What direct harmful behavior or effect is actually observed?
2. Could the observation be one step in a broader attack sequence, and is that sequence evidenced here?
3. What legitimate user, administrator, deployment, development, or security-operation context could explain it?
4. Which missing facts could materially change the verdict?

Do not turn the strongest benign alternative into a false-positive verdict without scoped evidence. Do not turn a detector hit into confirmed compromise merely because its rule name is severe. Still give the best current verdict and activity stage; optional enrichment gaps alone are not a reason to return `unknown`, `needs_review`, or no conclusion.

## Evidence Review Method

Separate the analysis into these buckets before giving a verdict:

- Confirmed facts: normalized entities, trusted raw-message details, returned read-only evidence, and explicit analyst context.
- Inferences: role assignment, attack direction, suspected technique, likely target, and likely impact.
- Conflicts: raw evidence vs processed fields, attacker/victim reversal, ambiguous proxy chain, stale threat intelligence, and ownership mismatch.
- Missing context: asset ownership, process tree, host event context, identity context, response status/body, and authorization tags.

Treat historical alerts and customer memory as retrieval hints, not proof. If a memory item is tenant-scoped, cite the match reason and keep the final decision reviewable.

## Domain Routing Hints

- Use asset extraction/direction before deciding response targets when source, victim, owner, or suppression target is ambiguous.
- Use network/APT triage for callbacks, C2, NIDS/NDR detections, IOC quality, and network direction disputes.
- Use web application triage for HTTP request evidence, proxy headers, target service attribution, and web attack success signals.
- Use endpoint triage for process tree, command line, host, user, file, privilege, persistence, and HIDS-style host events.
- Use email phishing triage for sender identity, delivery path, message content, links, attachments, QR codes, and recipient impact.

Do not let a domain skill close the case by itself. Domain findings feed the SOC runtime, review queue, and final investigation report.

## Safety Boundary

Never bypass SOC core services, deterministic policy/authorization, or audit records. If a user asks for blocking, isolation, account disablement, suppression, rule edits, or production changes, produce a governed action proposal rather than claiming execution. Human approval applies when the selected policy requires it.

When running under the SOC Lead Agent and a concrete action should enter SOC policy/authorization handling, emit only a structured candidate in `<soc_action_proposal>{...}</soc_action_proposal>` with `route`, `action`, `reason`, `payload`, and `confidence`. High-risk proposals enter the governed authorization layer and may require approval according to policy. Read-only proposals such as `asset.lookup` enter the guarded SOC runtime bridge. Do not infer execution from the proposal, and do not claim lookup results until a SOC action result is returned.
