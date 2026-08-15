---
name: soc-architecture-reviewer
description: Review architecture decisions and implementation boundaries for the DeerFlow SOC Agent. Use when proposing, reviewing, or implementing new modules, services, APIs, schemas, events, database migrations, Runtime or Agent control flow, middleware, Skill/MCP integrations, persistence, concurrency, deployment, or cross-module contracts; also use for extensibility, reliability, security, upstream-sync, and technical-debt decisions. Do not trigger for isolated local bug fixes, routine refactors with no boundary impact, or product prioritization without a system-design question.
---

# SOC Architecture Reviewer

Act as the architecture review workflow for this repository, not as a generic
"senior architect" persona. Apply general engineering judgment to current repository
facts and explicit contracts. Do not copy project status or architecture facts into this
Skill; resolve them from their authoritative sources on every use.

## Source Order

Read only what the task needs, in this order:

1. Read root `AGENTS.md`, then the nearest module `AGENTS.md` for files in scope.
2. Read `.notes/ai_soc/soc-agent-solution.md` for product and system direction.
3. Read `.notes/reference-index/soc-agent-engineering-contracts.md` for engineering,
   protocol, authority, persistence, and test rules.
4. Read `.notes/ai_soc/architecture/runtime-and-agent-architecture.md` when the change
   concerns Runtime, Agent Graph, Lead/Sub Agent, or control-flow ownership.
5. Read `.notes/ai_soc/alert-lifecycle-flow.md` when state or end-to-end flow changes.
6. Read `.notes/ai_soc/delivery-roadmap.md` and `.notes/ai_soc/progress.md` only when
   sequencing or completion status matters.
7. Inspect the actual code, contracts, migrations, tests, and configuration. Treat code
   as the as-is implementation and surface any divergence from the documented intent.

Do not load entire large documents by default. Use headings and targeted `rg` searches,
then read the relevant sections around each match.

## Review Workflow

### 1. Establish the review mode

Classify the request before proposing a design:

- `discussion`: compare approaches without changing files;
- `architecture_review`: inspect a proposal or current implementation and report findings;
- `implementation`: design, implement, test, and update documentation end to end;
- `ADR`: record a durable architectural decision and its consequences.

Honor the user's requested mode. Do not turn a discussion into an implementation.

### 2. Locate the ownership boundary

Classify each affected behavior into exactly one primary ownership layer:

- upstream DeerFlow generic framework;
- SOC entry/transport adapter;
- SOC Core Service;
- deterministic SOC Analysis Runtime;
- DeerFlow SOC Lead/Sub Agent profile or middleware;
- SOC contract/protocol;
- persistence/repository/migration;
- Memory, knowledge, governed context, or policy;
- external provider/action adapter/MCP;
- tenant-specific integration such as PingAn;
- frontend/TUI/operations surface.

Prefer an existing public service, protocol, extension point, registry, middleware, or
adapter. Add an abstraction only when it removes real duplication, preserves a stable
boundary, or is required by a second implementation.

### 3. Build an evidence-backed boundary map

Before recommending a change:

- identify current callers and consumers;
- trace input, state transition, persistence, emitted event, and output;
- identify the authoritative schema and version;
- find current tests, migrations, config, and operational commands;
- distinguish current implementation, target design, mock/simulation, and data-gated work;
- cite repository paths and line numbers for material architecture claims.

Do not infer architecture from filenames alone. Do not present a proposed target as
already implemented.

### 4. Apply the architecture gates

Review every relevant gate. Mark irrelevant gates explicitly rather than inventing work.

| Gate | Questions |
| --- | --- |
| Product fit | Does this solve the stated SOC workflow without creating a second product path? |
| Upstream isolation | Can this remain a SOC extension? Is a DeerFlow core change small, generic, and sync-friendly? |
| Ownership | Is there one owner for business state and one stable public service entry? |
| Control flow | Does deterministic Runtime retain mandatory steps? Is Agent autonomy bounded to open-ended investigation? |
| Contracts | Are schemas, APIs, events, protocols, versions, and compatibility rules explicit? |
| Evidence and trust | Are raw, canonical, derived, tenant, memory, tool, and human claims kept distinct with provenance? |
| Authority | Can model, Skill, Memory, MCP, middleware, or caller input bypass server policy, RBAC, approval, or audit? |
| Persistence | Are transaction ownership, migration, uniqueness, optimistic concurrency, and append-only lineage clear? |
| Reliability | Are timeout, retry class, idempotency, backpressure, cancellation, recovery, replay, and partial failure defined? |
| Scale and cost | Is the scaling unit known? Are model/tool calls, concurrency, queue depth, burst behavior, and budgets bounded? |
| Security and privacy | Are identity, tenant scope, secrets, raw evidence, prompt projection, retention, and external egress governed? |
| Extensibility | Can a new vendor/provider/topic use an adapter or protocol without changing generic Runtime semantics? |
| Observability | Can operators explain state, latency, failure, model lineage, action lineage, and degraded behavior? |
| Verification | Are focused tests, architecture tests, contract snapshots, migration tests, replay/eval, and live gates separated? |
| Delivery | Are rollout, compatibility, feature flag, rollback, and documentation impacts explicit? |

### 5. Preserve SOC-specific invariants

Enforce these invariants unless the authoritative plan is deliberately changed in the
same reviewed change set:

- Keep SOC work incremental to the DeerFlow fork; do not duplicate DeerFlow's Agent,
  Skill, MCP, checkpoint, stream, sandbox, or subagent runtime.
- Route API, Kafka, CLI, Web, TUI, and Agent mutations through shared SOC Core Services.
- Keep mandatory alert processing under the SOC Analysis Runtime. Treat an LLM as a
  bounded node and the SOC Lead Agent as a selected-case investigation surface.
- Keep vendor and tenant semantics in adapters, profiles, governed context, or policy;
  do not hard-code PingAn fields or `rule_code` assumptions into generic contracts.
- Keep current-alert evidence, model reasoning, Skill guidance, adapter semantics,
  confirmed Memory, governed context, and tool results in their declared namespaces.
- Treat Agent and specialist output as advisory until a typed service command or
  evidence-producing tool result crosses the governed boundary.
- Treat action proposal, authorization, approval, execution, and audit as separate states.
- Never let mock or simulated evidence close a real provider, infrastructure, label,
  pilot, or production gate.
- Preserve raw input and replay lineage while bounding model-visible context.
- Do not create a parallel roadmap, progress ledger, persistence stack, or execution path.

### 6. Choose the smallest durable design

Return one verdict:

- `Accept`: fits existing boundaries and has complete verification.
- `Accept with conditions`: sound direction with named blockers or contract changes.
- `Spike`: uncertainty requires a bounded experiment before committing architecture.
- `Reject`: duplicates ownership, violates authority, or creates unjustified complexity.

Compare at least one credible alternative for material decisions. State why the chosen
design is simpler or safer in this codebase. Do not recommend microservices, queues,
event sourcing, a new framework, or a new Agent merely because they are common patterns.

### 7. Implement only when requested

For implementation mode:

1. Reuse existing protocols and services before adding a new public surface.
2. Keep edits scoped to SOC modules unless a generic DeerFlow extension point is required.
3. Add or update typed contracts before wiring entry surfaces.
4. Add focused tests proportional to the changed boundary; include architecture and
   migration tests when ownership or persistence changes.
5. Run the narrowest meaningful checks, then broaden only when blast radius requires it.
6. Update the authoritative solution, engineering contract, lifecycle, index, and progress
   documents only where their owned facts changed.
7. Report unrun live, broker, browser, PostgreSQL, or tenant-internal gates explicitly.

## Output Contracts

### Architecture Review

Lead with findings when reviewing existing code or a proposal:

```markdown
**Verdict**
Accept / Accept with conditions / Spike / Reject

**Findings**
- [Severity] Finding with file/line evidence and concrete impact.

**Boundary Map**
Current owner -> proposed owner -> callers/consumers.

**Recommended Design**
The smallest durable design and why it fits existing patterns.

**Contract Impact**
Schemas, APIs, events, protocols, persistence, compatibility.

**Reliability And Authority**
Failure, recovery, scale, permission, audit, and action boundaries.

**Verification**
Focused tests, architecture gates, replay/eval, and external acceptance still required.

**Documentation Impact**
Authoritative documents that must change, or `none`.
```

### Architecture Decision Record

Use this shape only when an ADR is requested or the decision is expensive to reverse:

```markdown
# ADR: Decision title

Status: Proposed / Accepted / Superseded

## Context
## Decision
## Alternatives Considered
## Consequences
## Contracts And Migration
## Verification And Rollback
## References
```

### Implementation Handoff

For an approved design that should now be built, state:

- files/modules to own the change;
- public interfaces and version changes;
- persistence/migration impact;
- failure, idempotency, security, and observability behavior;
- focused and broader test commands;
- rollout, rollback, documentation, and data-gated acceptance.

## Collaboration Rules

- Challenge weak assumptions with repository evidence, then keep momentum.
- Separate required blockers from optional improvements and future hardening.
- Ask only when an answer cannot be found locally and a wrong assumption would change a
  public contract, authority boundary, irreversible migration, or production integration.
- Keep review output concise enough to drive a decision. Link to source documents instead
  of reproducing them.

