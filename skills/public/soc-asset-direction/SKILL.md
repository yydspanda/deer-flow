---
name: soc-asset-direction
description: Use when alert direction, attacker/victim role, asset ownership, affected asset, suppression target, or vendor field conflict must be resolved before judgement or response.
allowed-tools:
  - ask_clarification
  - present_files
  - read_file
  - task
---

# SOC Asset Direction

Use this skill when asset ownership, attacker/victim role, traffic direction, or response target is ambiguous.

## Focus

- Decide what each entity represents: attacker, victim, affected asset, source, destination, proxy, relay, scanner, or suppression target.
- Prefer raw message and explicitly trusted fields over processed fields when the normalized conflict report says fields disagree.
- Do not assume `src` means attacker or `dst` means victim. Validate by source type, event meaning, network direction, and business asset ownership.
- If raw message is missing and only processed fields remain, lower confidence and ask for review.

## Knowledge Boundary

- Keep role-assignment principles generic. Do not embed customer-specific source/destination field names, internal network ranges, internal business units, or vendor-specific direction rules as universal truth.
- Customer-specific direction fixes and environment exceptions belong in tenant memory, field-trust policy, or normalizer tests.
- If a customer adapter provides role candidates, treat them as evidence with confidence, not as final truth.

## Output

Return a role assignment summary:

- entity
- candidate role
- evidence path
- confidence
- conflict or uncertainty

When role assignment is unresolved, recommend `needs_review` rather than allowing automated suppression or response.
