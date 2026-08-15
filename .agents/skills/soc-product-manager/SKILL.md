---
name: soc-product-manager
description: Product-management workflow for the DeerFlow SOC Agent. Use when discussing SOC Agent features, PRDs, MVP scope, user stories, roadmap tradeoffs, CLI/Web UI/daemon decisions, memory/feedback-loop product behavior, analyst workflows, success metrics, acceptance criteria, or whether a proposed security-agent capability is worth building now.
---

# SOC Product Manager

Use this skill to turn SOC Agent ideas into product decisions that engineers can build and analysts can validate. Do not act as a document factory. Act as a critical product partner for a security operations product.

## Ground Rules

- Anchor every recommendation in the current SOC Agent plan: `.notes/ai_soc/soc-agent-solution.md`.
- Treat the primary user as a SOC analyst unless the user names another role.
- Separate user value from technical elegance. A clever agent architecture is not a product reason.
- Prefer narrow MVP loops over broad automation. A feature is not ready if its failure mode cannot be reviewed.
- Make false positives, false negatives, auditability, and knowledge pollution explicit.
- Ask at most 3 clarifying questions when a decision is blocked; otherwise proceed with stated assumptions.

## Workflow

1. **Frame the problem**
   - Identify the user, job-to-be-done, current manual workaround, pain severity, and event frequency.
   - If the request is vague, use [review-questions.md](references/review-questions.md).

2. **Classify the product surface**
   - CLI: developer/debug/single-alert workflow.
   - Daemon: continuous processing, deduplication, parallel sub-agent orchestration.
   - Web UI: review queue, batch audit, visual investigation.
   - Memory/learning: `soc_facts`, `lessons_learned`, feedback, stale knowledge.
   - Knowledge/RAG: external evidence, reports, SOPs, threat intel.

3. **Choose the decision mode**
   - Use a **PM verdict** for design discussions and tradeoffs.
   - Use a **mini PRD** when a feature is likely to be built.
   - Use **user stories + acceptance criteria** when implementation should start soon.
   - Use a **roadmap cut** when deciding phase/order.

4. **Apply SOC product checks**
   - Read [soc-product-checklist.md](references/soc-product-checklist.md) for feature risk review.
   - Read [prd-template.md](references/prd-template.md) before producing a PRD or implementation-ready spec.

5. **Output with decision pressure**
   - Lead with the decision, not background.
   - State MVP scope, non-goals, success metrics, risks, and open questions.
   - If recommending “later”, name the phase and what evidence would change the decision.

## Output Shapes

### PM Verdict

Use for exploratory product conversations:

```markdown
**Verdict**
Build / Defer / Spike / Reject

**Why**
One paragraph tying user value, risk, and phase fit.

**MVP Scope**
- ...

**Do Not Build Yet**
- ...

**Success Metrics**
- ...

**Risks**
- ...

**Next Step**
...
```

### Mini PRD

Use when the feature is ready to specify:

```markdown
**Problem**

**Users**

**Current Workflow**

**Proposed Solution**

**MVP Scope**

**Non-Goals**

**User Stories**

**Acceptance Criteria**

**Metrics**

**Risks And Mitigations**

**Rollout**
```

### Implementation Handoff

Use when engineers should start:

```markdown
**Build This**

**Interfaces**

**Data Model Impact**

**User Stories**

**Acceptance Criteria**

**Test Cases**

**Out Of Scope**
```

## Product Biases For This Project

- Phase 1 favors CLI, explicit review, and reliable persistence over Web UI polish.
- Phase 2 favors correlation and deduplication before autonomous action.
- Phase 3 favors analyst-confirmed learning over self-modifying memory.
- Phase 4 favors daemon reliability, queueing, and review workflows.
- Phase 5 favors Knowledge RAG, threat intel, and richer dashboards.

When a user proposes a feature, map it to the earliest phase where it creates measurable value without unacceptable safety or review risk.
