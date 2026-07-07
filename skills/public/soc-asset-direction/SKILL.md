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

## Direction Method

1. Build an entity table first: each IP/domain/host/user/service, all observed roles, evidence paths, and field trust.
2. Reconstruct the event meaning from source type and behavior: connection attempt, inbound exploit, outbound callback, internal lateral movement, scan, authentication, file/process event, or proxy observation.
3. Compare raw evidence, normalized entities, adapter role candidates, field trust, conflict report, and read-only asset evidence.
4. Assign attacker/victim/affected asset/relay/proxy/scanner/response target only when evidence supports it.
5. Keep role as `unknown` when the same entity has competing meanings or when raw evidence is missing.

## Conflict Handling

- Raw message and trusted observed evidence usually outrank processed role labels when conflict reports say they disagree.
- Asset ownership can identify affected business assets, but ownership alone does not prove attacker/victim role.
- Proxy and load-balancer addresses should not become response targets unless evidence shows they are the malicious actor.
- For internal-to-internal events, avoid assuming both sides are equally risky; evaluate initiating behavior, target role, authentication, and host context.
- For outbound callbacks, the internal asset is often affected, while the external endpoint may be IOC or C2; still keep confidence explicit.

When role assignment is unresolved, recommend `needs_review` rather than allowing automated suppression or response.
