# PingAn Internal DEV Profile

This profile connects DeerFlow and the SOC extension to the legacy internal
services without putting tenant credentials into tracked files. Real DEV values
belong directly in the two local `*.local` files; they do not need to remain
redacted there.

## Files

- `config.example.yaml`: DeerFlow profile for the OpenAI-compatible LiteLLM
  endpoint exposed by the legacy `sec-model` process.
- `env.example`: shell environment for the model, PingAn `asset.locate`, and
  historical software-path lookup.
- `extensions.example.json`: one DeerFlow MCP profile that registers both
  PingAn read-only tools. It contains environment references, not credentials.
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

After sourcing `.env.soc-dev.local`, `extensions.example.json` exposes both
`asset.locate` and `endpoint.software_path.lookup`. The latter writes only
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

cd backend
./.venv/bin/python -c \
  'from deerflow.config import get_app_config; c=get_app_config(); print(c.models[0].name, c.models[0].model, c.database.backend)'
./.venv/bin/python -m soc_agent.cli mcp tools --include-schema --pretty

./.venv/bin/python -m soc_agent.cli mcp smoke \
  samples/mcp/pingan_software_path/action_adapters.json \
  --route endpoint.software_path.lookup \
  --json '{"path":"D:\\ps\\psexec.exe","context_refs":{"thread_id":"PATH-CONTEXT-SMOKE"}}' \
  --pretty
```

The expected DeerFlow model name is `deepseek-v4-flash`, while the request sent
to the internal gateway uses `DeepSeek_V4_Flash`. Local DEV persistence remains
the separate SQLite `backend/.deer-flow/data/soc_agent_dev.db` unless an
explicit SOC database URL overrides it.

The preflight performs no network request. It must pass before direct or MCP
smoke; outside the intranet, failure on the internal
`model.agent_platform.util_tools:run_workflow` import is expected.
