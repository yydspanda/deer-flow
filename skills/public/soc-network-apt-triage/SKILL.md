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

## Generic Method

1. Reconstruct the traffic facts before assigning roles: protocol, source, destination, ports, URL/domain, HTTP host/path, timestamps, and asset ownership evidence.
2. Classify the direction as external-to-internal, internal-to-external, internal-to-internal, or ambiguous. Do not treat vendor `src`/`dst` labels as final attacker/victim roles.
3. Separate attack attempt, detection hit, attack success, and confirmed impact.
4. Check IOC quality: source, freshness, confidence, scope, false-positive risk, and whether the IOC matches the observed entity or only a related entity.
5. If fields conflict or evidence is thin, output uncertainty and recommend read-only queries instead of forcing a verdict.

## Attack Success Signals

- HTTP or web exploit: request payload plus response status/body, application error, sensitive data returned, system information returned, or follow-on traffic.
- File read or directory traversal: evidence that sensitive file content was actually returned, not only that a path appeared in a payload.
- Command or code execution: command arguments plus execution output, callback, new process evidence, system info echo, or other side effect.
- Webshell or upload: distinguish upload request, execution request, response echo, and later callback or persistence signal.
- Scan, weak password, or brute force: distinguish single probe, automated scan, authentication failure, authentication success, and normal business login.
- Callback/C2: repeated beaconing, suspicious domain or IP reputation, unusual user agent/protocol, and related endpoint process evidence.

## Safe Read-Only Next Queries

Recommend these as proposals only when needed:

- `asset.locate` for victim, affected asset, owner, environment, or response target ambiguity.
- `threat_intel.ip_reputation.lookup` for external IP/domain/URL quality and freshness.
- `security_tag.lookup` for authorization, testing, maintenance, or allowlist evidence.

When network traffic must be tied to a process, use process and connection facts already present in the alert's bounded native evidence. Do not assume a separate endpoint process-tree provider exists.

Do not directly block IPs/domains or suppress alerts. Route those actions through approval.

## References

- Read `references/c2-and-suspicious-communication.md` for callback, beaconing, proxy/tunnel, malicious outbound, and suspicious communication analysis.
- Read `references/exploit-success-evidence.md` when deciding whether a network detection shows an attempt, observed response, successful exploit, or confirmed impact.
