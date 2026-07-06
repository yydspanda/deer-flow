---
name: soc-asset-extraction
description: Use when SOC triage must extract IP, domain, URL, host, user, enterprise account, or UM-like assets from an alert and prepare safe read-only asset lookup/location proposals.
allowed-tools:
  - ask_clarification
  - present_files
  - read_file
  - task
---

# SOC Asset Extraction

Use this skill before asset ownership lookup, BU location, attack direction judgement, or disposal target selection.

## Scope

- Extract all observable assets from the bounded alert context: IP, DOMAIN, WEB/URL, HOST, USER, enterprise account, UM-like account, process-linked host, and HTTP host.
- Assign roles only when evidence supports them: attacker, target, victim, impacted_asset, response_target, proxy, relay, scanner, unknown.
- Separate extraction from remote lookup. This skill does not query CMDB, external case systems, EDR, SOAR, or any production system.
- Prefer normalized SOC runtime fields, raw-message evidence, field trust, and conflict reports. Do not rely on vendor processed fields when conflict reports say they disagree.

## Knowledge Boundary

- Keep extraction patterns generic. Customer-specific account formats, BU names, PA/company codes, asset ownership rules, and CMDB semantics belong in tenant memory, adapter config, or read-only lookup results.
- This skill can propose `asset.lookup` or `asset.locate`; it must not claim owner, BU, company code, environment, or disposal owner without a returned SOC action result.
- Vendor field aliases belong in normalizers/adapters, not in this skill.

## Output Shape

When asked to extract assets, return a compact structure:

```json
{
  "assets": [
    {"type": "IP", "value": "10.10.1.5", "role": "target", "evidence_path": "entities.network.destination_ip", "confidence": 0.82}
  ],
  "role_assignments": {
    "attacker": [],
    "target": [],
    "unknown": []
  },
  "disposal_target": "target",
  "recommended_lookup_order": ["target", "attacker", "unknown"],
  "uncertainty": []
}
```

## Proposal Boundary

If a read-only ownership or BU lookup is needed, emit a SOC action proposal instead of claiming results:

```text
<soc_action_proposal>{"route":"asset.locate","action":"asset.locate","reason":"Locate the target asset owner before assigning disposal target.","payload":{"asset_key":"10.10.1.5","asset_type":"IP","role":"target"},"confidence":0.74}</soc_action_proposal>
```

Use `asset.lookup` only when the needed result is a simple asset record. Use `asset.locate` when the needed result is business ownership, company code, BU, or disposal ownership.

## Rules

- Do not execute or imply remote calls from this skill.
- Do not claim company code, business group, owner, or endpoint isolation status unless SOC runtime returns an action result.
- If attacker/victim direction is ambiguous, mark role as `unknown` and pair this skill with `soc-asset-direction`.
- If multiple assets are plausible response targets, recommend review rather than automated suppression.
