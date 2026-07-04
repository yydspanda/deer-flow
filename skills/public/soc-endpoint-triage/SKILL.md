---
name: soc-endpoint-triage
description: Use for EDR, XDR, HIDS, endpoint, host, process, command-line, file hash, user account, lateral movement, and terminal attack investigation.
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
- Host and user identity: host name, asset id/group, username, src/dst user, user_id, UM account.
- Whether the endpoint is the attacker, victim, relay, or simply a scanner/jump host.
- Whether proposed response requires analyst approval: isolate host, kill process, quarantine file, disable account.

## Output

Explain the endpoint story in analyst terms:

- affected host/user
- suspicious process or behavior
- evidence confidence
- missing context
- safe next query/action

Do not claim host isolation, process kill, account disablement, or quarantine unless an approved tool result proves it.
