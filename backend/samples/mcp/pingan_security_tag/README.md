# PingAn ZEUS Security Tag MCP

This package replaces the local in-memory adapter for the generic
`security_tag.lookup` route with a PingAn-specific ZEUS provider. It queries
`POST /public/searchTagContent` by one exact entity value and preserves active,
expired, inactive, conflicting, unknown, and out-of-scope records.

A matching tag is investigation evidence only. It is not a governed
authorized-activity fact and cannot mark an alert benign, close a review, or
authorize an action.

The action descriptor declares `result_provenance_contract=runtime_declared`
and `result_mode_field=mocked`. Composition validates its exact adapter identity
without invoking the tool; the persistent investigation workflow must still
verify each returned `mocked` value before accepting the configured result mode.

## External-network fake smoke

Run from `backend/`:

```bash
export SOC_PINGAN_SECURITY_TAG_MCP_PYTHON="$PWD/.venv/bin/python"
export SOC_PINGAN_SECURITY_TAG_MCP_SERVER="$PWD/scripts/soc_pingan_security_tag_mcp_server.py"
export DEER_FLOW_EXTENSIONS_CONFIG_PATH="$PWD/samples/mcp/pingan_security_tag/extensions.fake.json"

./.venv/bin/python -m soc_agent.cli mcp smoke \
  samples/mcp/pingan_security_tag/action_adapters.json \
  --route security_tag.lookup \
  --json '{"entity_key":"203.0.113.10","entity_type":"ip","context_refs":{"thread_id":"TAG-FAKE-SMOKE"}}' \
  --pretty
```

Fake output always exposes `mocked=true`; it proves only the MCP/action/evidence
shape. It is not PI-01B1 real-provider evidence.

## Internal DEV profile

Use the tracked `extensions.internal.example.json` directly; inject secrets through environment variables. The
Host DEV runtime remains `dev`, but this Provider uses the shared `prd` ZEUS target and
requires `SOC_PINGAN_ZEUS_PRD_CONFIRMATION=CALL_PINGAN_ZEUS_PRD`. The internal
provider requires an HTTPS ZEUS URL whose hostname is explicitly in
`SOC_PINGAN_ZEUS_ALLOWED_HOSTS`, shared App ID/App Key credentials, and the
portable signer. Missing configuration fails closed and never falls back to
fake data.

Open-ended records are **not** active by default. Set
`SOC_PINGAN_SECURITY_TAG_ALLOW_OPEN_ENDED_VALIDITY=true` only after the internal
ZEUS owner confirms that missing `expireTime` means permanent validity.

Internal acceptance must cover exact hit, expired, inactive, no expiry,
out-of-scope response, not-found, conflicting validity, authentication failure,
timeout, source lineage, and persisted `InvestigationEvidence`. Every result
must retain:

```json
{
  "evidence_boundary": "investigation_only",
  "decision_impact": "none",
  "authorization_fact_created": false,
  "automation_eligible": false,
  "raw_response_included": false
}
```
