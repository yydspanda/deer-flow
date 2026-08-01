# Email Evidence Playbook

## Identity And Delivery

Compare envelope sender, header sender, display name, reply-to, return path, message ID, received chain, SPF/DKIM/DMARC results, forwarding, recipients, and delivery/quarantine state. Missing authentication data is a gap, not a pass or failure.

## Content And Intent

Look for impersonation, urgency, credential or payment requests, conversation hijack, unusual language, and mismatch with known business context. Customer VIP and trusted-sender knowledge must come from governed context.

## Links, Attachments, And QR

Inspect visible text versus destination, redirects, domain age/reputation, attachment type/name/hash, archive nesting, macros/scripts, and decoded QR target. Each extracted indicator needs an evidence path. A suspicious indicator is not proof of interaction.

## Recipient Impact

Separate delivered, opened, clicked, downloaded, executed, credential-submitted, mailbox-rule-created, account-used, and endpoint-affected states. Correlate mail, identity, endpoint, and network evidence before claiming impact.
