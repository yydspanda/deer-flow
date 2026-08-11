---
name: pingan-soc-disposition-policy
description: Apply reviewed PingAn SOC operational handling experience after generic Runtime analysis; use only for tenant policy advice, never detection truth or action authorization.
metadata:
  version: v1.2.0
---

# PingAn SOC Disposition Policy

This Skill produces an independent PingAn operational recommendation after the
generic SOC Runtime has completed. Preserve the Runtime verdict and confidence.
Return `no_match` when the conditions below are incomplete or contradictory.

## Decision Order

1. Trust exact current-alert facts (`E-*`) and resolved governed context
   (`M-*`, `C-*`, `T-*`) only. A name, hostname suffix, rule label, or old
   workflow state alone is not authorization.
2. Treat provider outcome assertions such as `攻击成功` and `失陷` as important
   evidence, not deterministic dispositions. Combine them with response content,
   observed effect, contradictions, and governed context. An exact forced-transfer
   rule remains deterministic outside this Skill.
3. Treat only `canonical_entities.http.status_code` and
   `canonical_entities.http.observations[].status_code` as HTTP status. Never
   reinterpret workflow, ticket, forwarding, rule, suppression, or disposition
   fields named `status` as an HTTP response.
4. Separate technical detection from operational handling. A real attack or
   scan can still receive a benign operational disposition when it is exactly
   authorized, but its technical verdict remains unchanged.
5. This Skill never authorizes blocking, isolation, suppression, assignment,
   closure, or another external side effect.

## PingAn HTTP And Request-Outcome Semantics

- `200` means only that the HTTP request succeeded. It does not by itself mean
  transfer, ignore, exploit success, command execution, file write, login, data
  disclosure, or compromise. Preserve the Runtime decision unless another
  exact PingAn rule or observed effect changes operational handling.
- A canonical HTTP status other than `200` is normally handled by deterministic
  `canonical-http-non-200-ignore` before this Skill runs. That rule abstains when
  an explicit provider success/compromise label is present, so this Skill can
  resolve the contradiction from the complete bounded evidence.
- If no canonical HTTP status exists, an exact current-alert request-failure
  result such as `请求失败`, `攻击失败`, `失败`, or an unambiguous failed response may
  recommend `ignored`. An unrelated business, workflow, forwarding, ticket, or
  disposition code is not a request result. Do not recommend ignore when an
  explicit success/forced-transfer assertion or an observed effect is present.
- `企图`, `尝试`, or an attack-result value meaning attempt is not by itself a
  request-failure result. Preserve the Runtime analysis and inspect response
  effect, provider outcome, and governed context before changing disposition.
- A body-level word such as `error` or `failed` is not enough when it represents
  error-based SQL disclosure, command output, business data, or another material
  result. Contradictory request outcomes require review.

## Reviewed Operational Recommendations

### Explicit request failure without canonical HTTP status

Recommend `ignored` with `review_effect=clear` when exact cited current-alert
facts establish request/attack failure and no explicit success, forced-transfer,
or material-effect fact exists. Use signal key `request_failure` and state that
the technical detection of an attempted behavior is retained.

If failure and success signals conflict, use `manual_validation_required`,
`review_effect=require`, no closed/ignored disposition, and signal key
`conflicting_request_outcome`.

### Successful or material effect

Recommend `escalated` with `review_effect=require` when exact evidence shows
one or more of the following, regardless of a test-looking hostname:

- an explicit `攻击成功` / `失陷` provider assertion corroborated by current
  response, session, command, file, process, or other material-effect evidence;
- command output, webshell execution, file write/upload, persistence, or a new
  endpoint process caused by the request;
- credentials, session/token material, sensitive files, source code, SQL data,
  or other material disclosure in the response;
- a complete proxy/tunnel, reverse-shell, C2, lateral-movement, or unauthorized
  access behavior with observed effect.

Use stable signal keys such as `provider_success_assertion`,
`material_response_effect`, `endpoint_effect`, or `confirmed_tunnel_behavior`.
Do not treat a rule name or `200` alone as one of these effects.

### Scenario-specific legacy experience

Old ZEUS APT/NIDS/EDR/HIDS prompts contain useful scenario checks for XFF/CDN
direction, NPS/FRP tunnels, JDWP/Actuator/Swagger exposure, sensitive-file
reads, file upload/webshell, weak-password login, suspicious process chains,
unsafe writable paths, honeypots, and reverse shells. Apply those checks only
through the already selected generic triage Skill and exact current evidence.
They may reinforce `escalated` or support `ignored`, but a keyword, rule name,
missing field, or stale example must never decide disposition by itself.

### Reviewed known behavior

Known scanners, red/blue/white-team activity, security products, business
automation, safe paths, and recurring benign process chains may affect handling
only when an exact governed fact (`C-*`), confirmed Memory (`M-*`), or tool result
(`T-*`) resolves the current subject, target, behavior, environment, and event
time. Plain string resemblance is insufficient.

- Exact authorized activity should normally have been handled by the
  deterministic policy before this Skill.
- A confirmed reusable benign pattern may recommend
  `closed_benign_true_positive` or `ignored`, while preserving the Runtime
  detection verdict and citing both current facts and governed context.
- Conflicting or stale context requires review.

### Credible attack or unresolved effect

Recommend `escalated` and `review_effect=require` when cited evidence contains a
successful effect, explicit provider success assertion, material impact, or a
credible attack whose operational scope is unresolved. A non-production
hostname does not override these signals, while `200` alone is not enough.

### Evidence insufficiency

Do not migrate the legacy rule "missing fields means ignore". When a critical
field is absent, truncated, contradictory, or not projected, preserve or require
review and identify the gap. Lack of evidence is not evidence that the request
failed.

## Output Discipline

- `evidence_refs` must contain exact current-alert `E-*` IDs.
- `reasoning_refs` may cite existing Runtime `R-*` items.
- `context_refs` may cite exact `S/A/M/C/T-*` governed context IDs.
- Keep `policy_signal_keys` stable but open-vocabulary.
- Use `no_match`, `review_effect=preserve`, no disposition, and no suggested
  action when this Skill has no defensible PingAn-specific handling change.
