# PingAn Internal DEV Profile

This profile connects DeerFlow and the SOC extension to the legacy internal
services without putting tenant credentials into tracked files. Real DEV values
belong directly in the two local `*.local` files; they do not need to remain
redacted there.

## Files

- `config.example.yaml`: DeerFlow profile for the OpenAI-compatible LiteLLM
  endpoint exposed by the legacy `sec-model` process.
- `env.example`: shell environment for the model, PingAn `asset.locate`,
  threat intelligence, security-tag lookup, and historical software-path lookup.
- `extensions.example.json`: one DeerFlow MCP profile that registers all four
  PingAn read-only tools. It contains environment references, not credentials.
- `d12b-test-cases.example.yaml`: value-free seven-case D12-B matrix. Copy it
  into the ignored internal validation directory and replace every placeholder
  before live execution.
- `../../soc_agent/integrations/pingan/zeus_signing.py`: self-contained copy of
  the reviewed ZEUS signing protocol, without the legacy module's default key or
  import-time dependencies.
- `../mcp/pingan_asset/extensions.internal.example.json`: existing MCP server
  registration; credentials stay in the sourced environment.

## Historical EDR path catalog

The legacy XLSX is compiled locally rather than loaded for every alert. The
result is candidate investigation knowledge, never an allowlist. Exact matches
retain source lineage and freshness; directory control remains a separate
signal, so a `D:` path stays high-attention even when it appeared in historical
ignored alerts.

From the repository root, build the Git-ignored catalog and inspect one path:

```bash
backend/.venv/bin/python backend/scripts/soc_pingan_software_path_catalog.py build

backend/.venv/bin/python backend/scripts/soc_pingan_software_path_catalog.py query \
  'D:\\ps\\psexec.exe'
```

Generated files are local-only and mode `0600`:

```text
backend/.deer-flow/pingan-context/software-path-catalog.sqlite
backend/.deer-flow/pingan-context/software-path-catalog.build-report.json
```

After sourcing `.env.soc-dev.local`, `extensions.example.json` exposes
`asset.locate`, `threat_intel.ip_reputation.lookup`, and
`security_tag.lookup`, and `endpoint.software_path.lookup`. Every result writes only
`InvestigationEvidence(decision_impact=none)` through the normal action
dispatcher. It cannot skip Runtime, mark an alert benign, close a review, or
write confirmed memory.

## Local setup

From the repository root on the internal Mac:

```bash
cp backend/samples/pingan_dev/config.example.yaml config.pingan-dev.local
cp backend/samples/pingan_dev/env.example .env.soc-dev.local
chmod 600 .env.soc-dev.local config.pingan-dev.local
```

If root `extensions_config.json` is absent, create it from
`backend/samples/pingan_dev/extensions.example.json`. If it already exists,
merge only the `pingan_asset`, `pingan_threat_intel`, `pingan_security_tag`, and
`pingan_software_path` `mcpServers` entries;
do not overwrite unrelated MCP configuration. The resulting root file is
Git-ignored.

Fill the real values in `.env.soc-dev.local`, then source it from the same
repository root so `$PWD` resolves correctly:

```bash
source ./.env.soc-dev.local
```

Both local files end in `.local` and are ignored by the repository. Confirm
that before adding any real value:

```bash
git check-ignore -v .env.soc-dev.local config.pingan-dev.local
```

Prepare the private D12-B matrix separately. It contains approved IP/host/UM
test values and must remain on the intranet:

```bash
mkdir -p backend/.deer-flow/soc-internal-validation/d12b/reports
cp backend/samples/pingan_dev/d12b-test-cases.example.yaml \
  backend/.deer-flow/soc-internal-validation/d12b/test-cases.local.yaml
chmod 600 backend/.deer-flow/soc-internal-validation/d12b/test-cases.local.yaml
```

The negative authentication/timeout cases refer to environment variables from
`env.example`; the matrix never stores alternate credential or endpoint values
directly. Keep `matrix_id` and every `case_id` as opaque labels such as
`search-hit`; validation rejects identifiers that embed a query or UM value.

## Preflight

Start the legacy local model stack first. The reviewed source exposes the
LiteLLM gateway at `http://localhost:4001/v1/`; the provider model ID is
`DeepSeek_V4_Flash`.

```bash
export D12B_ASSET_KEY="<approved-internal-test-value>"

curl -fsS \
  -H "Authorization: Bearer $PINGAN_LITELLM_API_KEY" \
  "$PINGAN_LITELLM_BASE_URL/models"

backend/.venv/bin/python backend/scripts/soc_pingan_dev_preflight.py \
  --report-path backend/.deer-flow/soc-internal-validation/d12b/preflight.json

backend/.venv/bin/python backend/scripts/soc_pingan_asset_direct_smoke.py \
  --query "$D12B_ASSET_KEY" \
  --asset-type IP \
  --role victim \
  --report-path backend/.deer-flow/soc-internal-validation/d12b/direct-success.json

backend/.venv/bin/python backend/scripts/soc_pingan_d12b_matrix.py \
  --cases backend/.deer-flow/soc-internal-validation/d12b/test-cases.local.yaml \
  --plan-only

backend/.venv/bin/python backend/scripts/soc_pingan_d12b_matrix.py \
  --cases backend/.deer-flow/soc-internal-validation/d12b/test-cases.local.yaml \
  --confirm-live \
  --report-path backend/.deer-flow/soc-internal-validation/d12b/reports/direct-provider-cases.json

cd backend
./.venv/bin/python -c \
  'from deerflow.config import get_app_config; c=get_app_config(); print(c.models[0].name, c.models[0].model, c.database.backend)'
./.venv/bin/python -m soc_agent.cli mcp tools --include-schema --pretty

./.venv/bin/python -m soc_agent.cli mcp smoke \
  samples/mcp/pingan_asset/action_adapters.json \
  --route asset.locate \
  --json "{\"asset_key\":\"$D12B_ASSET_KEY\",\"asset_type\":\"IP\",\"role\":\"victim\",\"context_refs\":{\"thread_id\":\"D12-B-MCP-SUCCESS\"}}" \
  --pretty

./.venv/bin/python -m soc_agent.cli mcp smoke \
  samples/mcp/pingan_software_path/action_adapters.json \
  --route endpoint.software_path.lookup \
  --json '{"path":"D:\\ps\\psexec.exe","context_refs":{"thread_id":"PATH-CONTEXT-SMOKE"}}' \
  --pretty

export PI01A_TI_IP="<approved-dev-ip>"

./.venv/bin/python -m soc_agent.cli mcp smoke \
  samples/mcp/pingan_threat_intel/action_adapters.json \
  --route threat_intel.ip_reputation.lookup \
  --json "{\"ip\":\"$PI01A_TI_IP\",\"context_refs\":{\"thread_id\":\"PI-01A-TI-SMOKE\"}}" \
  --pretty
```

Use an approved DEV IP in `PI01A_TI_IP`. Run separate known-hit and known-miss
queries, then exercise approved invalid-auth and timeout profiles. A real
PI-01A result must show `mocked=false`, preserve label source paths and
freshness, omit the full ZEUS response, and remain investigation-only.

Then exercise PI-01B1 with an approved exact entity value:

```bash
export PI01B1_TAG_ENTITY="<approved-dev-ip-host-domain-or-account>"
export PI01B1_TAG_ENTITY_TYPE="ip"

./.venv/bin/python -m soc_agent.cli mcp smoke \
  samples/mcp/pingan_security_tag/action_adapters.json \
  --route security_tag.lookup \
  --json "{\"entity_key\":\"$PI01B1_TAG_ENTITY\",\"entity_type\":\"$PI01B1_TAG_ENTITY_TYPE\",\"context_refs\":{\"thread_id\":\"PI-01B1-TAG-SMOKE\"}}" \
  --pretty
```

Run separate exact-hit, expired, inactive/no-expiry, not-found, auth-failure,
timeout, and provider-mismatch cases. A real result must show `mocked=false`.
Missing `expireTime` stays `unknown` unless the internal ZEUS owner explicitly
confirms open-ended validity and the local setting is changed. A tag match is
still ordinary `InvestigationEvidence`; it does not complete PI-01B2 or create
an authorized-activity fact.

After an approved alert has produced an open ReviewQueue item in the same SOC
SQLite database, bind one successful private matrix case to that queue and
exercise the complete MCP -> Action Dispatcher -> InvestigationEvidence ->
Review/Lead Agent context path:

```bash
cd backend
./.venv/bin/python -m soc_agent.cli review list --pretty

export D12B_QUEUE_ID="<existing-open-review-queue-id>"
export D12B_CASE_ID="search-hit"

./.venv/bin/python scripts/soc_pingan_d12b_evidence.py \
  --cases .deer-flow/soc-internal-validation/d12b/test-cases.local.yaml \
  --case-id "$D12B_CASE_ID" \
  --queue-id "$D12B_QUEUE_ID" \
  --confirm-live \
  --report-path .deer-flow/soc-internal-validation/d12b/reports/evidence-readback.json
```

Only a case whose expected outcome is `found` is eligible for this persistence
acceptance. The script invokes MCP through `SocAgentActionDispatcher`; it does
not call the PingAn Provider directly. A pass requires `mocked=false`,
`provider_mode=internal`, `evidence_boundary=investigation_only`, persisted
evidence with request/trace provenance, shared Review Context and Lead Agent artifact visibility, and
unchanged AnalysisRun/ReviewQueue hashes. Its mode-`0600` report contains no
raw query, UM, or Provider response. It validates the shared service contract,
not an actual browser or Review TUI render; deployed surface smoke remains a
separate internal checklist item.

The expected DeerFlow model name is `deepseek-v4-flash`, while the request sent
to the internal gateway uses `DeepSeek_V4_Flash`. Local DEV persistence remains
the separate SQLite `backend/.deer-flow/data/soc_agent_dev.db` unless an
explicit SOC database URL overrides it.

The preflight performs no network request. It must pass before direct or MCP
smoke; outside the intranet, failure on the internal
`model.agent_platform.util_tools:run_workflow` import is expected.

The matrix `--plan-only` path also issues no request. `--confirm-live` can issue
real internal DEV requests and therefore refuses a non-`.local` filename,
group/world-readable permissions, unresolved case placeholders, a missing
fault-injection reference, or a missing report path. The mode-`0600` aggregate
report keeps only query hashes, expected/observed attempt stages, latency and
error classes; it excludes raw queries, UM values, Provider bodies and override
values. Passing this direct matrix does not replace the D12-B MCP, persisted
`InvestigationEvidence`, or Web/TUI/Lead Agent readback gates.
