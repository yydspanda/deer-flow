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

## Output

Explain:

- request direction and target
- attacker/client candidate
- victim/service candidate
- field trust and proxy ambiguity
- whether this is noise, suspicious, or risky
- safe next action

Do not add WAF/F5 rules or blocks without approval.
