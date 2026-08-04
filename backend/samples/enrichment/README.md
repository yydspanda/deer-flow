# SOC Enrichment Composition Samples

These files configure the application-level `SocEnrichmentPlanner`; they do not
configure MCP transport or credentials.

- `disabled.yaml` is the production-safe default. It cannot plan or invoke any
  automatic investigation action.
- `enabled.mock.yaml` binds an example tenant to the three in-memory adapters.
  It is for tests and local demos only.
- `enabled.dev-mcp.yaml` binds one local development route to the exact
  `asset-lookup-soc-dev-mcp` adapter in
  `backend/samples/mcp/soc_dev_action_adapters.json`. It exercises the D3
  persistent MCP workflow while still requiring `required_result_mode: mock`.

The composition and the action-adapter registry are independent allowlists. At
startup, `build_soc_main_orchestrator_service()` requires every enabled route to
match one exact `route`, `action`, `adapter_id`, and `adapter_kind`. It rejects
write-capable adapters, unsupported required inputs, and mock/real provenance
mismatches.

PingAn internal composition must be created from reviewed tenant network scope
and the existing MCP configs under `backend/samples/mcp/pingan_*`. Do not copy
the mock sample into an internal real profile: use `required_result_mode: real`
and bind the exact PingAn MCP adapter IDs. Their `runtime_declared` contract
means D3 must also verify each returned `mocked` value before persisting evidence.

Kafka daemon commands accept repeated `--enrichment-action-config` arguments so
an internal profile can combine the separately reviewed PingAn asset, threat
intelligence, and security-tag allowlists. The internal PKL runner exposes the
same options, but additionally requires `--persist` and
`--confirm-investigation`. Omitting all enrichment options keeps the fixed SOC
Runtime independently usable and invokes no investigation Provider.

Inspect or explicitly replay durable executions with:

```bash
cd backend
uv run python -m soc_agent.cli investigation get EEXEC-ID --pretty
uv run python -m soc_agent.cli investigation replay EEXEC-ID \
  --reason "operator replay after reviewed provider recovery" \
  --idempotency-key "operator:replay:unique-key" \
  --enrichment-composition samples/enrichment/enabled.dev-mcp.yaml \
  --enrichment-action-config samples/mcp/soc_dev_action_adapters.json \
  --confirm-investigation --pretty
```

Replay creates a new linked execution under the current reviewed composition;
it never mutates the source execution or the base `AnalysisRun`.
