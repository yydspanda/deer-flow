# PingAn Asset Location MCP

This package is the Checkpoint D12 handoff for the legacy PingAn lookup order:

```text
ZEUS searchAssetInfo -> asset-to-BU workflow -> optional UM workflow
```

It does not extract assets, choose a response target, change a verdict, or
authorize an action. The generic SOC route remains `asset.locate`; this MCP
server only returns bounded `InvestigationEvidence` data.

The action descriptor declares `result_provenance_contract=runtime_declared`
and `result_mode_field=mocked`. PI-01D2 composition may therefore bind it to a
mock or real profile, but the later execution workflow must verify the returned
`mocked` value on every call; startup validation alone is not real-provider
evidence.

## D12-A: external-network fake smoke

Run from `backend/`:

```bash
export SOC_PINGAN_ASSET_MCP_PYTHON="$PWD/.venv/bin/python"
export SOC_PINGAN_ASSET_MCP_SERVER="$PWD/scripts/soc_pingan_asset_mcp_server.py"
export DEER_FLOW_EXTENSIONS_CONFIG_PATH="$PWD/samples/mcp/pingan_asset/extensions.fake.json"
REPORT_DIR="$PWD/.deer-flow/soc-runtime-validation/checkpoint-d/step-d12-pingan-asset-provider"
mkdir -p "$REPORT_DIR"

./.venv/bin/python -m soc_agent.cli mcp smoke \
  samples/mcp/pingan_asset/action_adapters.json \
  --route asset.locate \
  --json '{"asset_key":"10.10.1.5","asset_type":"IP","role":"victim","context_refs":{"thread_id":"D12-FAKE"}}' \
  --report-path "$REPORT_DIR/d12-a-fake-smoke.json" \
  --pretty
```

Expected evidence contains:

```json
{
  "mocked": true,
  "provider_mode": "fake",
  "decision_impact": "none"
}
```

This proves only code shape, MCP transport, fallback orchestration, and result
mapping. It is not PA-12 or PI-01 real-provider evidence.

## D12-B: internal real smoke

Use `extensions.internal.example.json` as the template. Export the ZEUS base
URL, app ID, app key, operator, Python path, server path, and (when the legacy
modules are not installed) `SOC_PINGAN_PROVIDER_IMPORT_PATHS` in the internal
shell. Multiple import roots use the platform path separator. Do not write the
secret into this repository.

The defaults retained from the reviewed legacy implementation are:

- signer import: `util.util_tools:isec_sign`
- workflow runner: `model.agent_platform.util_tools:run_workflow`
- ZEUS path: `/public/searchAssetInfo`
- terminal workflow: `1087710`
- datacenter workflow: `1087787`
- user workflow: `1092332`
- workflow app ID: `YHSYS`
- reviewed legacy ownership alias: `云桌面分组 -> PA011 / 平安科技`, supplied through
  `SOC_PINGAN_ASSET_OWNERSHIP_OVERRIDES_JSON` rather than generic Runtime code

Before retaining these values, verify them with the internal service owner.
Then rerun the same `soc mcp smoke` command using the internal extensions
config, changing `DEER_FLOW_EXTENSIONS_CONFIG_PATH` to
`extensions.internal.example.json` (or its operator-owned copy) and the report
name to an explicit D12-B case such as `d12-b-success-smoke.json`. A real pass
must contain `mocked=false` and must separately capture:

- successful ownership resolution;
- a legitimate not-found response;
- authentication/authorization failure;
- timeout or provider-unavailable behavior;
- persistence as `InvestigationEvidence` through the normal action service.

Until those reports exist, `D12-B`, `PA-12`, and the first PI-01 real provider
remain incomplete.
