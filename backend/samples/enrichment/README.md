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
- `pingan-external-simulation.yaml` is the required first PI-01E profile. It
  uses the PingAn MCP adapters with fake transports and requires every result
  to be `mocked=true`.
- `pingan-internal-shadow.yaml` is the matching secret-free real
  profile. It keeps the same PingAn `asset.locate` and `security_tag.lookup`
  bindings, requires `mocked=false`, and can be used directly because endpoint
  and credentials live in environment variables rather than this file.

The composition and the action-adapter registry are independent allowlists. At
startup, `build_soc_main_orchestrator_service()` requires every enabled route to
match one exact `route`, `action`, `adapter_id`, and `adapter_kind`. It rejects
write-capable adapters, unsupported required inputs, and mock/real provenance
mismatches.

PingAn external rehearsal and internal acceptance must use their separate
tracked compositions. Their `runtime_declared` contract means D3 verifies each
returned `mocked` value before persisting evidence; the paired gate also checks
the exact composition, action-config and extensions-config fingerprints.
The tracked PI-01E example does not enable threat intelligence because no
tenant network ranges belong in a public sample. To enable
`threat_intel.ip_reputation.lookup`, the internal owner must add that route,
its exact binding, and reviewed `internal_networks`, then include the PingAn TI
action config. Do not insert guessed RFC1918 or PingAn ranges merely to satisfy
schema validation.

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
