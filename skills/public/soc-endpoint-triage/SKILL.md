---
name: soc-endpoint-triage
description: Use for EDR, XDR, HIDS, endpoint, host, process, command-line, file hash, user account, enterprise account, lateral movement, and terminal attack investigation.
allowed-tools:
  - ask_clarification
  - present_files
  - read_file
  - task
---

# SOC Endpoint Triage

Use this skill when the alert depends on endpoint evidence such as process trees, command lines, host identity, file hashes, user or UM accounts, parent-child process relationships, persistence, privilege use, or lateral movement.

## Focus

- Process ancestry, suspicious command-line arguments, script interpreters, LOLBins, dropped files, and network callbacks from endpoint processes.
- Host and user identity: host name, asset id/group, username, src/dst user, user_id, enterprise account, UM-like account.
- Whether the endpoint is the attacker, victim, relay, or simply a scanner/jump host.
- Whether proposed response requires analyst approval: isolate host, kill process, quarantine file, disable account.

## Knowledge Boundary

- Keep endpoint reasoning generic: process ancestry, command line, parent/child behavior, persistence, privilege use, user-writable paths, and network callbacks.
- Customer-specific safe paths, security tools, department exceptions, account formats, allowlists, and approved admin groups belong in tenant memory or policy/config.
- Vendor-specific EDR/HIDS field names belong in adapters/normalizers. This skill should consume canonical process, host, user, file, and network evidence.

## Output

Explain the endpoint story in analyst terms:

- affected host/user
- suspicious process or behavior
- evidence confidence
- missing context
- safe next query/action

Do not claim host isolation, process kill, account disablement, or quarantine unless an approved tool result proves it.
