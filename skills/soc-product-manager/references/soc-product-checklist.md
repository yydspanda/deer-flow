# SOC Product Checklist

Use this checklist when reviewing a proposed SOC Agent feature.

## User And Workflow

- Who uses it: SOC analyst, shift lead, platform admin, security manager, developer?
- What decision does the user make faster or more accurately?
- What is the current manual workflow?
- How often does this happen: per alert, daily review, weekly tuning, incident-only?
- What context does the user already have outside the tool?

## SOC Risk

- False positive risk: could this waste analyst time or suppress useful alerts?
- False negative risk: could this hide malicious behavior?
- Audit risk: can a reviewer reconstruct why the agent made the decision?
- Knowledge pollution risk: can a bad correction become future “truth”?
- Automation risk: what happens if this runs unattended for 7 days?

## Product Value

- Does it reduce alert volume, reduce triage time, or improve consistency?
- Is the value visible within Phase 1/2, or only after a full platform exists?
- Can the value be measured from stored data?
- Does it help one analyst once, or the whole team repeatedly?

## MVP Boundary

- Can it be done through CLI/API before Web UI?
- Can it work with PostgreSQL before adding another service?
- Can it be reviewed by a human before affecting future decisions?
- Is it useful with 100 sample alerts, not only production-scale data?

## Metrics

Prefer metrics that can be computed:

- Mean triage time per alert.
- Auto-close rate for high-confidence false positives.
- Analyst override rate.
- Review queue precision.
- Number of confirmed `soc_facts` created from corrections.
- Duplicate-alert merge rate.
- Percentage of decisions with cited evidence.

## Red Flags

- “Make it smarter” without a measurable user outcome.
- A feature that requires Web UI before the underlying review model is stable.
- Autonomous memory writes that bypass review.
- A dashboard that displays data nobody acts on.
- A model feature with no rollback or audit trail.
