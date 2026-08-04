# PingAn ZEUS Threat Intelligence MCP

This package replaces the local in-memory adapter for the generic
`threat_intel.ip_reputation.lookup` route with a PingAn-specific ZEUS provider.
The provider maps reviewed fields from `POST /public/indicatorSearch` into
bounded `SocThreatIntelReputationRecord` evidence.

It deliberately does **not** migrate the legacy hardcoded risk formula,
geographic multipliers, whitelist decisions, blocking rules, or raw provider
response. `score` and `confidence` stay unset unless a future reviewed provider
contract gives those fields stable semantics.

## External-network fake smoke

Run from `backend/`:

```bash
export SOC_PINGAN_THREAT_INTEL_MCP_PYTHON="$PWD/.venv/bin/python"
export SOC_PINGAN_THREAT_INTEL_MCP_SERVER="$PWD/scripts/soc_pingan_threat_intel_mcp_server.py"
export DEER_FLOW_EXTENSIONS_CONFIG_PATH="$PWD/samples/mcp/pingan_threat_intel/extensions.fake.json"

./.venv/bin/python -m soc_agent.cli mcp smoke \
  samples/mcp/pingan_threat_intel/action_adapters.json \
  --route threat_intel.ip_reputation.lookup \
  --json '{"ip":"203.0.113.10","context_refs":{"thread_id":"TI-FAKE-SMOKE"}}' \
  --pretty
```

Fake output always exposes `mocked=true`; it proves only the MCP/action/evidence
shape. It is not PI-01A real-provider evidence.

## Internal DEV profile

Use `extensions.internal.example.json` from a Git-ignored local profile. The
internal provider requires an HTTPS ZEUS URL whose hostname is explicitly in
`SOC_PINGAN_ZEUS_ALLOWED_HOSTS`, shared App ID/App Key credentials, and the
reviewed portable signer. Missing or invalid configuration fails closed and
never falls back to fake data.

Internal acceptance must cover a real hit, explicit not-found, authentication
failure, timeout, malformed response, freshness, field trimming, source
lineage, and persisted `InvestigationEvidence`. A valid result remains:

```json
{
  "evidence_boundary": "investigation_only",
  "decision_impact": "none",
  "automation_eligible": false,
  "raw_response_included": false
}
```
