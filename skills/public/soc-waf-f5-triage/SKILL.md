---
name: soc-waf-f5-triage
description: Use for WAF, F5, HTTP, x-forwarded-for, web attack, SQL injection, XSS, webshell, and external-to-internal web traffic investigation.
allowed-tools:
  - ask_clarification
  - present_files
  - read_file
  - task
---

# SOC WAF/F5 Triage

Use this skill when the alert involves WAF/F5, HTTP request evidence, proxy headers, web attack signatures, SQL injection, XSS, webshell attempts, URL paths, host headers, or client IP attribution.

## Focus

- Client IP attribution: source IP, `x-forwarded-for`, proxy chain, and whether the apparent source is trustworthy.
- Target application/asset: host header, URL, URI path, service, and business ownership.
- Attack type: scan, exploit attempt, injection, webshell, credential attack, false positive.
- Direction and suppression target: avoid confusing protected service, client IP, and intermediate proxy.

## Knowledge Boundary

- Keep WAF/F5 reasoning generic: HTTP evidence, proxy attribution, web attack success signals, target service, and suppression target.
- Customer-specific URI exceptions, internal domains, F5 policy names, VIPs, route tables, and suppression templates belong in tenant memory, adapter config, or policy.
- This skill may propose read-only investigation or approval-gated response, but must not add rules, block clients, or suppress alerts directly.

## Output

Explain:

- request direction and target
- attacker/client candidate
- victim/service candidate
- field trust and proxy ambiguity
- whether this is noise, suspicious, or risky
- safe next action

## Generic Method

1. Reconstruct the HTTP path: client candidate, proxy chain, target service, host header, URL/path, method, parameters, body, response status, response body, and timestamp.
2. Attribute the client carefully. `x-forwarded-for` and similar headers are evidence, not guaranteed truth; use trusted proxy context when available.
3. Separate protected service, intermediate proxy/load balancer, client IP, and suppression target.
4. Distinguish blocked attempt, exploit attempt, exploit success, and confirmed impact.
5. If the target service or owner is unclear, propose read-only asset lookup instead of choosing a response target.

## Web Attack Success Signals

- SQL injection/XSS/RCE/path traversal: payload plus response behavior, data returned, application error, command output, callback, or state change.
- Webshell: upload request, execution request, response echo, file persistence, and later callback should be evaluated separately.
- Credential attack: failed attempts, successful login evidence, unusual session creation, and source reputation should be separated.
- Scans and probes: volume, breadth, response status, and absence of success evidence should lower confidence but not erase the event.

## Safe Read-Only Next Queries

Recommend these as proposals only when needed:

- `asset.locate` for target service ownership and response target.
- `threat_intel.ip_reputation.lookup` for client IP/domain reputation and freshness.
- `security_tag.lookup` for authorized testing, maintenance, or allowlist evidence.

Do not add WAF/F5 rules or blocks without approval.
