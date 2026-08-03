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

Use this skill when the alert depends on endpoint evidence such as process trees, command lines, host identity, file hashes, user or enterprise accounts, parent-child process relationships, persistence, privilege use, or lateral movement.

## Focus

- Process ancestry, suspicious command-line arguments, script interpreters, LOLBins, dropped files, and network callbacks from endpoint processes.
- Host and user identity: host name, asset id/group, username, src/dst user, user_id, and enterprise account identifiers.
- Whether the endpoint is the attacker, victim, relay, or simply a scanner/jump host.
- Whether proposed response requires analyst approval: isolate host, kill process, quarantine file, disable account.

## Knowledge Boundary

- Keep endpoint reasoning generic: process ancestry, command line, parent/child behavior, persistence, privilege use, user-writable paths, and network callbacks.
- Customer-specific safe paths, security tools, department exceptions, account formats, allowlists, and approved admin groups belong in tenant memory or policy/config.
- Vendor-specific EDR/HIDS field names belong in adapters/normalizers. This skill should consume canonical process, host, user, file, and network evidence.

## Generic Method

1. Reconstruct the execution chain: process, parent, ancestors, user, host, path, command line, hash, network connections, and timestamp.
2. Score each dimension separately: path trust, command-line risk, parent/child plausibility, user privilege, file reputation, persistence behavior, and network callback.
3. Do not ignore an event solely because the path looks trusted. A trusted path with risky arguments, unusual parent process, or privileged user still requires review.
4. Treat rule names and vendor detection IDs as aliases for a behavior, not as the behavior itself.
5. If host context or process ancestry is incomplete, recommend read-only evidence collection instead of inventing context.

## Endpoint And HIDS Indicators

Suspicious indicators:

- User-writable or temporary paths, download directories, script interpreters, Office child processes, archive/extractor chains, encoded commands, credential access, privilege changes, lateral movement tools, persistence artifacts, and unexpected network callbacks.

Benign or lowering indicators:

- Returned authorization evidence, maintenance evidence, known business process context, expected parent process, expected signer/hash, and analyst-confirmed prior handling.

Keep benign indicators as review context unless policy and evidence explicitly support suppression.

## Safe Read-Only Next Queries

Recommend these as proposals only when needed:

- `asset.locate` for ownership, criticality, and response-target ambiguity.
- `security_tag.lookup` for authorization, testing, maintenance, or allowlist evidence.

Process ancestry, command lines, login context, hashes, and network activity must come from the alert's bounded native evidence. Do not propose an external process-tree or host-context query when no such provider is configured.

## Output

Explain the endpoint story in analyst terms:

- affected host/user
- suspicious process or behavior
- evidence confidence
- missing context
- safe next query/action

Do not claim host isolation, process kill, account disablement, or quarantine unless an approved tool result proves it.

## References

Read `references/endpoint-scenario-playbooks.md` for reverse shell, web command, process-chain, persistence, privilege, credential-access, malware, and lateral-movement evidence checks.
