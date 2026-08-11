# Governed SOC Automation Samples

These samples describe the post-Runtime automation boundary. They do not grant
the model authority and do not enable external actions by themselves.

## Files

- `policy.shadow.example.yaml`: a vendor-neutral, shadow-only policy. Replace
  tenant, environment, validity, rule, and pinned adapter identity under change
  control before use.
- `memory-decision-directive.example.json`: an optional typed directive that a
  memory reviewer may attach while confirming one candidate.

## Runtime configuration

```bash
export SOC_AUTOMATION_POLICY_PATH=backend/samples/automation/policy.shadow.example.yaml
export SOC_AUTOMATION_ENVIRONMENT=dev
export SOC_AUTOMATION_EXECUTE_AUTHORIZED_ACTIONS=false
```

The default is disabled when `SOC_AUTOMATION_POLICY_PATH` is absent. Shadow mode
records decision/disposition/authorization lineage but cannot authorize an
external action. Enforced mode additionally requires `reviewed_by` and
`reviewed_at`; automatic action rules must explicitly match verdict, evidence
state, model name, Prompt version, Decision Policy version, minimum confidence,
and `needs_review`. The normal path matches
`needs_review=false`. A reviewed tenant may deliberately match
`needs_review=true`, but that rule must also provide a separate
`review_required_override_reason`; this authorizes the exact action without
deleting the ReviewQueue or pretending that a human reviewed the alert.

Actual execution also requires a programmatically supplied, exact-match
`SocActionAdapterRegistry` descriptor with `execute_supported=true`, a
`write|destructive` side effect, and required idempotency. The sample adapter ID
is intentionally not registered by default.

Replay runs may evaluate and record the policy, but automatic external actions
are always denied so historical replay cannot repeat a production side effect.

Inspect persisted lineage:

```bash
soc automation lineage --run-id RUN_ID --database-url sqlite:///soc_agent_dev.db --pretty
```

Attach a reviewed decision directive only while confirming a candidate:

```bash
soc memory review MC_ID \
  --decision confirm \
  --reason "Reviewed scoped detection lesson" \
  --decision-directive backend/samples/automation/memory-decision-directive.example.json \
  --database-url sqlite:///soc_agent_dev.db \
  --pretty
```

Confirmation still creates a retrieval-disabled record. A separate governed
retrieval activation is required before the record can be selected as `M-*`.
