# SOC Agent PRD Template

Use this for implementation-ready PRDs. Keep it short enough for engineers to act on.

## Problem

State the analyst pain in operational terms. Include alert volume, delay, confusion, or error cost when known.

## Users

List primary and secondary users:

- Primary:
- Secondary:
- Non-users affected:

## Current Workflow

Describe what happens today without this feature:

1. Trigger:
2. Analyst action:
3. Decision:
4. Pain point:

## Proposed Solution

Describe the smallest useful version. Name whether it lives in CLI, API, daemon, Web UI, memory, or knowledge retrieval.

## MVP Scope

- Must have:
- Should have:
- Can defer:

## Non-Goals

Name attractive things that are intentionally excluded from this phase.

## User Stories

Use this format:

```text
As a <SOC role>,
I want <capability>,
so that <operational outcome>.
```

Each story must include acceptance criteria.

## Acceptance Criteria

Use testable criteria:

```text
Given <initial state>,
When <action/event>,
Then <observable outcome>.
```

Include negative cases and audit cases.

## Data And Interfaces

- Inputs:
- Outputs:
- Tables touched:
- CLI commands/API endpoints:
- Events or Kafka topics:

## Metrics

Include at least one leading metric and one quality/safety metric.

## Risks And Mitigations

| Risk | Mitigation |
|---|---|
| False positive | |
| False negative | |
| Bad memory update | |
| Analyst mistrust | |
| Operational failure | |

## Rollout

State phase, sample data requirement, feature flag/review gate, and rollback path.
