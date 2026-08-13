---
name: soc-email-phishing-triage
description: Use for suspicious email, phishing, business email compromise, sender spoofing, malicious links, attachments, QR codes, delivery, recipient exposure, and mailbox response investigation.
allowed-tools:
  - ask_clarification
  - present_files
  - read_file
  - task
---

# SOC Email Phishing Triage

Use this skill when bounded evidence contains an email entity or the detection concerns phishing, suspicious mail, sender impersonation, links, attachments, QR codes, or mailbox activity.

## Focus

- Message identity: sender, reply-to, recipients, subject, message ID, authentication results, and observed delivery path.
- Social-engineering intent: impersonation, urgency, credential request, payment request, and brand or executive abuse.
- Payloads: URLs, attachment names/types/hashes, QR targets, macros, scripts, archives, and redirect chains.
- Exposure: delivered recipients, user interaction evidence, credential submission, endpoint execution, and follow-on activity.

## Knowledge Boundary

- Customer executives, trusted senders, internal domains, mailbox IDs, VIP lists, and campaign exceptions belong in governed tenant context or memory.
- Vendor mail-gateway fields and EML parsing aliases belong in adapters and typed email entities.
- Sender display name, domain resemblance, a suspicious URL, or attachment name alone does not prove compromise.

## Method

1. Reconstruct who sent what to whom and how it was delivered.
2. Separate header identity from displayed identity; record authentication and forwarding gaps.
3. Analyze message intent and every available URL, attachment, and QR target independently.
4. Distinguish blocked, quarantined, delivered, opened, clicked, credential-submitted, and endpoint-executed states.
5. Correlate recipients and indicators with endpoint/network evidence and similar campaigns when available.
6. Give a current verdict even when evidence is incomplete, then state confidence, evidence gaps, and concrete manual checks.

## Safe Boundaries

Read-only parsing, reputation, mailbox-state, and endpoint-context queries may be proposed. Mail deletion, sender blocking, account reset, session revocation, and endpoint containment require deterministic policy authorization and may require human approval. Never claim those actions completed without returned action evidence.

## References

Read `references/email-evidence-playbook.md` for sender, content, URL, attachment, QR, delivery, and recipient-impact checks.
