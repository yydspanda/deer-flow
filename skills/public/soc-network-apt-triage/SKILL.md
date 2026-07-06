---
name: soc-network-apt-triage
description: Use for APT, NIDS/NDR, malicious outbound, callback, C2, IOC, domain/IP/URL, attack-chain, and network-direction investigation.
allowed-tools:
  - ask_clarification
  - present_files
  - read_file
  - task
---

# SOC Network And APT Triage

Use this skill when the alert depends on network evidence, APT-style detection, IOC matching, malicious outbound, callbacks, C2, beaconing, suspicious domains, URLs, IPs, or attack-chain reasoning.

## Focus

- Direction: internal-to-external, external-to-internal, internal-to-internal, or ambiguous.
- Role assignment: attacker, victim, affected asset, relay, scanner, suppression target.
- IOC quality: domain/IP/URL/file hash, source of intelligence, freshness, and false-positive risk.
- Evidence conflicts from vendor direction fields, processed fields, or historical parsing.
- Similar historical alerts by detection key, rule code, entity keys, and time window.

## Knowledge Boundary

- Keep APT/network reasoning generic: traffic direction, callback/C2, IOC quality, exploit evidence, attack success evidence, and role confidence.
- Customer-specific APT product field names, internal domains, business allowlists, rule codes, and exception patterns belong in tenant memory, adapter mapping, or eval fixtures.
- If customer memory says a pattern is common benign behavior, cite it as scoped memory with match reason, not as universal security knowledge.

## Output

Provide a concise network narrative:

- observed communication
- likely direction
- asset role confidence
- IOC and rule evidence
- unresolved conflicts
- safe next investigation query

Do not directly block IPs/domains or suppress alerts. Route those actions through approval.
