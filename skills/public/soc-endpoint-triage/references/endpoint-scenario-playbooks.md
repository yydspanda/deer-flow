# Endpoint Scenario Playbooks

## Reverse Shell And Web Command

Trace parent/child/ancestor process, command line, user, host, listening or outbound connection, destination, and timing. A reverse shell commonly has affected host as wire source; confirm with process-network linkage or shell behavior rather than direction alone.

## Persistence And Privilege

Inspect service, task, registry, startup, account, scheduled execution, file placement, and privilege-change evidence. Distinguish creation attempt from persisted state and later execution. Expected administrative behavior requires scoped authorization or business context.

## Credential Access And Lateral Movement

Look for credential-store access, token/session use, remote-service protocols, source and target hosts, user identity, authentication result, and follow-on execution. A remote-management binary or port alone is insufficient.

## Malware And Suspicious Files

Evaluate path, signer, hash reputation, origin, parent process, command line, file relation, execution, persistence, and network activity. Do not treat a malformed hash, file name, path, or vendor label as proof.

## Brute Force And Remote Access

Separate repeated attempts, authentication failure, successful login, session creation, remote-service execution, and follow-on behavior. Source breadth, account breadth, timing, target criticality, and returned identity evidence matter. A remote-access tool or management port alone does not prove lateral movement.

## Security Product, Honeypot, And Administrative Activity

Security software detections, honeypot events, scanners, maintenance tools, and system-path binaries remain observations. Confirm the executable, signer/hash, invoking identity, scope, time window, parent process, and governed authorization before treating them as benign. A trusted path or familiar product name alone is insufficient.

For every scenario, state observed behavior, strongest competing benign explanation, current verdict, evidence gaps, and manual checks.
