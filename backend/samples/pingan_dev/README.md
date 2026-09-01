# PingAn Internal DEV Profile

This profile connects DeerFlow and the SOC extension to the legacy internal
services without putting tenant credentials into tracked files. Real DEV values
belong directly in the two local `*.local` files; they do not need to remain
redacted there.

## Files

- `config.example.yaml`: DeerFlow profile for this repository's loopback
  OpenAI-compatible model gateway. The gateway owns the internal EAGW transport;
  its `config_version` must match the root `config.example.yaml`.
- `env.example`: shell environment for the model, post-Runtime PingAn tenant
  disposition policy, `asset.locate`, threat intelligence, security-tag lookup,
  and historical software-path lookup.
- `../../soc_agent/integrations/pingan/policies/tenant-disposition-v2.json`:
  reviewed PingAn operational policy. It preserves Runtime detection truth;
  enforced rules may change effective disposition/review but cannot authorize an
  external action.
- `uv-index.env.example`: scoped PingAn-intranet uv index used by the native
  Host DEV one-time locked install and later explicit dependency maintenance;
  it is not used by the separate offline-bundle fallback.
- `extensions.example.json`: one DeerFlow MCP profile that registers all four
  PingAn read-only tools. It contains environment references, not credentials.
- `d12b-test-cases.example.yaml`: value-free seven-case D12-B matrix. Copy it
  into the ignored internal validation directory and replace every placeholder
  before live execution.
- `../../soc_agent/integrations/pingan/zeus_signing.py`: self-contained copy of
  the reviewed ZEUS signing protocol, without the legacy module's default key or
  import-time dependencies.
- `../../soc_agent/integrations/pingan/agent_workflow.py`: self-contained HTTP
  client for Agent Platform authentication, workflow creation, polling and
  bounded response parsing. It does not import the legacy Agent Platform project.
- `../../soc_agent/integrations/pingan/model_gateway_smoke.py`: one fixed-prompt,
  credential-free-report smoke for the project-owned loopback OpenAI-compatible endpoint.
- `../mcp/pingan_asset/extensions.internal.example.json`: existing MCP server
  registration; credentials stay in the sourced environment.

## Historical EDR path catalog

The legacy XLSX is compiled locally rather than loaded for every alert. The
result keeps exact candidate investigation knowledge and conservatively inferred
one-segment path families. Families are built only from repeated `safe_paths`;
`other_paths` and broad fuzzy matching never create them. Directory control
remains separate, so a `D:` path stays high-attention even when it appeared in
historical ignored alerts.

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

The MCP lookup itself remains investigation-only. For the separately approved
high-throughput operating mode, set
`SOC_PINGAN_SOFTWARE_PATH_FAST_POLICY_ENABLED=true` together with the tenant
policy settings in `env.example`. The post-Runtime PingAn policy then assigns
the same direct `ignored` disposition to complete exact-safe-path and
safe-path-family coverage. One unknown path, `other_paths`-only match, invalid
path, path-budget overflow, or hash conflict disables the aggregate signal and
returns the alert to normal triage.

## Native no-Docker Host DEV

Use this path when the internal Apple Silicon Mac already provides:

- Python `3.12+` (`3.12.7` is accepted);
- `uv` with the approved PingAn PyPI profile;
- Node.js `22+`, the repository-pinned pnpm, and an approved internal NPM registry;
- nginx `1.23+`; and
- `git`, `make`, `curl`, `tar`, `shasum`, and `lsof`.

The source and private-overlay archives deliberately exclude the three large PKLs and
the Workbench payload SQLite. Keep those files under the current user's Downloads
directory and stage them before `check` or `install`:

```text
$HOME/Downloads/source/full_alert_2026_month_forth_sample_200.pkl
$HOME/Downloads/corpus/full_alert_validation_corpus.pkl
$HOME/Downloads/corpus/full_alert_dams_labeled_merged.pkl
$HOME/Downloads/corpus/full_alert_dams_labeled_merged.workbench-payloads.sqlite
```

```bash
python3.12 scripts/soc_pingan_stage_internal_corpus.py
python3.12 scripts/soc_pingan_stage_internal_corpus.py --apply
```

The first command is a no-write dry run. The second copies only after every source
matches the corpus manifest/index shipped in the private overlay, sets targets to mode
`0600`, and fails closed on any missing or mismatched artifact. `$HOME` resolves to
`/Users/zhangjianming627` for the current DEV Mac and remains portable for coworkers.

The checked-in driver validates those prerequisites without inspecting or requiring
Docker. It installs the locked backend and frontend dependencies from the configured
internal registries, records a local mode-`0600` report, and starts the normal Gateway,
Next.js frontend, and nginx directly on the Mac:

```bash
python3.12 scripts/soc_pingan_macos_host_dev.py check
python3.12 scripts/soc_pingan_macos_host_dev.py install
python3.12 scripts/soc_pingan_macos_host_dev.py start
```

The PingAn-only host driver now detects the private IPv4 addresses on the macOS
default/`en*` interfaces and adds them to Next.js `allowedDevOrigins`. Nginx already
listens on `2026`, so authenticated coworkers on the trusted internal LAN can open the
printed `http://<mac-ip>:2026` URL. Additional DNS names or addresses remain explicit:

```bash
python3.12 scripts/soc_pingan_macos_host_dev.py start --daemon \
  --allowed-origin soc-dev.internal
```

Use `start --local-only` to disable LAN access explicitly. The resolved list is applied
after `.env.soc-dev.local` is sourced, fixing `/_next/*` hydration and the
`/_next/webpack-hmr` WebSocket without changing Gateway CORS. Automatic discovery
accepts private IPv4 only and fails closed when none is available; use
`--allowed-origin HOST` for an approved non-RFC1918 corporate address. Keep
authentication enabled and the macOS firewall scoped to the trusted network.

The native `install` command keeps the canonical PyPI-authored `backend/uv.lock`
unchanged. It uses `uv export --frozen` to derive exact versions and hashes, then
`uv pip sync --require-hashes` to download those artifacts from the scoped PingAn
mirror. Local workspace packages are installed separately as editable packages with
dependency resolution disabled. This split is intentional: changing the configured
registry changes uv's source identity, so a direct `uv sync --locked` can demand a
re-lock even when Python and every version remain unchanged, while `uv sync --frozen`
would follow the public artifact URLs embedded in the canonical lock.

A fresh Mac therefore does not need a pre-populated uv cache and must not rewrite or
accept changes to `backend/uv.lock`. It also must not run the offline lock check
(`uv lock --check --offline`); that command requires every package, including
`langchain-openviking==0.1.0`, to already exist in the local cache and belongs only to
the separately verified offline-bundle path. The generated hash-locked requirements
and their digest are retained under `backend/.deer-flow/internal-host-dev/` for audit.

The start command sources `.env.soc-dev.local`, selects
`config.pingan-dev.local`, sets `NEXT_TELEMETRY_DISABLED=1`, and delegates to the
normal host launcher with `--skip-install`. It therefore does not run `uv sync`
or `pnpm install` on each restart. Stop it with:

```bash
python3.12 scripts/soc_pingan_macos_host_dev.py stop
```

The install report is written to:

```text
backend/.deer-flow/internal-host-dev/install-report.json
```

This DEV profile deliberately uses `LocalSandboxProvider`. Agent shell commands run
on the trusted developer Mac, so this mode is not a multi-user or production sandbox.
The application profile must keep public search/tools disabled and register only
approved internal MCP/services when public egress is unavailable.

## Offline backend installation fallback

Use this fallback only when the internal Mac does not have a usable Python/uv or
approved internal Python package registry. Transfer the separately generated
`deer-flow-pingan-macos-arm64-offline-<timestamp>.tar.gz` beside the source and
private-overlay archives. After the source has been extracted to the final
checkout path, install the project-owned toolchain:

```bash
mkdir -p /tmp/deer-flow-offline
tar -xzf /approved/path/deer-flow-pingan-macos-arm64-offline-<timestamp>.tar.gz \
  -C /tmp/deer-flow-offline
cd /tmp/deer-flow-offline/deer-flow-pingan-macos-arm64-offline
TARGET_REPO="$HOME/deer-flow"
./install-offline.sh "$TARGET_REPO"
```

The installer verifies SHA-256 values, checks `Darwin arm64`, and creates only
the following project-local paths:

```text
backend/.deer-flow/toolchain/
backend/.deer-flow/offline/uv-cache/
backend/.venv/
```

It uses bundled CPython `3.12.3`, bundled `uv`, and `backend/uv.lock` in strict
offline mode. It does not use `sudo`, change the system Python, or depend on a
second project's virtual environment.

### PingAn package indexes

The current backend uses `uv`, not Poetry, so do not add a
`[[tool.poetry.source]]` block to `backend/pyproject.toml`. Native Host DEV loads
the scoped PingAn uv profile during its one-time install. For a manual maintenance
command, explicitly source the same profile:

```bash
source backend/samples/pingan_dev/uv-index.env.example
```

It maps the internal repository to `UV_DEFAULT_INDEX` and scopes the required
plain-HTTP exception with `UV_INSECURE_HOST=maven.paic.com.cn:8445`. Do not make
that index the tracked repository default: it would break external builds and
could rewrite `backend/uv.lock` with an intranet-only registry. Review any lock
change before commit. The configured pnpm registry must likewise be an approved
internal registry; the Host DEV check rejects known public NPM registries. Rebuild
the offline bundle whenever the accepted lock changes if the fallback path is used.

## Local setup

From the repository root on the internal Mac:

```bash
cp backend/samples/pingan_dev/config.example.yaml config.pingan-dev.local
cp backend/samples/pingan_dev/env.example .env.soc-dev.local
chmod 600 .env.soc-dev.local config.pingan-dev.local
```

In the preparation checkout, import the reviewed legacy `YHSYS` PRD profile
into that ignored env file before building the private overlay. The command
parses the source with `ast`; it never imports or executes the old package and
never prints the secret:

```bash
backend/.venv/bin/python \
  backend/scripts/soc_pingan_prepare_legacy_model_gateway_profile.py --apply
backend/.venv/bin/python \
  backend/scripts/soc_pingan_prepare_legacy_workflow_profile.py --apply
```

The model preparer statically selects the reviewed STG `DeepSeek_V4_Flash`
profile (DEV may use the STG gateway), migrates the old local loopback API key,
creates `.secrets/eagw-private-key.der`, and initializes lifecycle/callback modes
to `fake`. Its JSON output must show `environment=stg`,
`model_config_name=DeepSeek_V4_Flash`, `credential_present=true`,
`compatibility_key_present=true`, and `secret_in_output=false`. The workflow
output must show `environment=prd`, `app_id=YHSYS`, `operator=WANGWENBIN520`,
`credential_present=true`, and `secret_in_output=false`. Both commands are
idempotent. The old source is deliberately excluded from the source archive;
the resulting env and DER key travel only in the protected private overlay.

If root `extensions_config.json` is absent, create it from
`backend/samples/pingan_dev/extensions.example.json`. If it already exists,
merge only the `pingan_asset`, `pingan_threat_intel`, `pingan_security_tag`, and
`pingan_software_path` `mcpServers` entries;
do not overwrite unrelated MCP configuration. The resulting root file is
Git-ignored.

Resolve the checkout dynamically, fill the remaining ZEUS/model/fault-case
values in `.env.soc-dev.local`, and then source it. The configuration never
embeds one developer's `/Users/...` path:

```bash
eval "$(backend/.venv/bin/python backend/scripts/soc_pingan_local_paths.py --shell)"
source ./.env.soc-dev.local
```

Both local files end in `.local` and are ignored by the repository. In a Git
clone, confirm that before adding any real value:

```bash
git check-ignore -v .env.soc-dev.local config.pingan-dev.local
```

Before building the final internal transfer archives, commit the intended
source and require `git status --short` to be empty. The transfer builder rejects
a dirty worktree by default. `--allow-dirty` creates a development-only archive
whose report is explicitly ineligible for final handoff.
When `--include-private-overlay` is selected, it also rejects obsolete import
keys (including the retired workflow-operator override), unresolved
placeholders, developer-specific `/Users/...` paths, missing model-gateway,
ZEUS, workflow, or fault-case configuration, or local config permissions broader
than `0600` before writing any archive.

The standalone internal transfer archive intentionally excludes `.git/`, so
`git check-ignore` is unavailable after extracting that archive. On the target
Mac, verify the private-overlay permissions instead; both results must start
with `600`:

```bash
stat -f '%Lp %N' \
  .env.soc-dev.local config.pingan-dev.local .secrets/eagw-private-key.der
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

The Host DEV driver starts this repository's model gateway at
`http://127.0.0.1:4001/v1/`. DeerFlow calls its standard
`chat.completions` boundary using the stable public alias
`deepseek-v4-flash`; the PingAn gateway maps that alias to the configured EAGW
scene and upstream model. Do not start the old `sec_know_model`, LiteLLM,
Celery, or Redis processes.

The baseline model smoke matches the current SOC Runtime default: thinking is
disabled and the bounded completion budget is 128 tokens. Its report must retain
those requested settings even on failure. Enable thinking only for a separate
capability check with an explicitly larger budget; do not conflate that optional
check with basic EAGW connectivity.

The legacy queue deadline remains deliberately narrow: only alert tasks with
`executeType=1` or `executeType=3` use
`SOC_PINGAN_LEGACY_QUEUE_TTL_SECONDS` (default `1800`). An expired alert does
not call the model, but the worker still persists the old-compatible expiration
result and callback outbox entry. Other task types do not inherit this alert
deadline.

For the internal DEV handoff, the compatibility API deliberately keeps the old
network shape and listens on `0.0.0.0:8090`, while the model gateway remains
loopback-only on `127.0.0.1:4001`. Restrict inbound `8090` with the macOS
firewall to the approved ZEUS DEV/STG callers. The legacy allowed-key-set Bearer/
`app-key` authentication and bounded request body remain mandatory; `app_code`
is business routing metadata rather than a credential-map key. Do not
publish `8090` to an untrusted network.

First prove the repository-owned compatibility plane without internal network
access:

```bash
backend/.venv/bin/python backend/scripts/soc_pingan_legacy_fake_acceptance.py
```

That report is intentionally `simulated=true`. After the model gateway smoke
passes, prepare one approved alert that is still pending in ZEUS:

```bash
mkdir -p backend/.deer-flow/soc-internal-validation/legacy-compat
cp backend/samples/pingan_dev/legacy-task-request.example.json \
  backend/.deer-flow/soc-internal-validation/legacy-compat/task-request.local.json
chmod 600 backend/.deer-flow/soc-internal-validation/legacy-compat/task-request.local.json
```

Keep the old caller values `app_code=zeus` and `flow_id=alert_agent`, replace
the **entire** example `alert_data` object with the complete approved payload,
and use a new `session_id`. The Bearer/`app-key` value must be one of the values
allowed by `SOC_PINGAN_COMPAT_APP_KEYS_JSON`; its map label does not need to
equal `app_code`. Then set both legacy provider modes to
`internal` in `.env.soc-dev.local` and restart Host DEV. Run the real
submit/status/precheck/Runtime/callback gate:

```bash
export TARGET_REPO="${TARGET_REPO:-$HOME/deer-flow}"
cd "$TARGET_REPO"
eval "$(backend/.venv/bin/python backend/scripts/soc_pingan_local_paths.py --shell)"
source ./.env.soc-dev.local
backend/.venv/bin/python backend/scripts/soc_pingan_legacy_live_acceptance.py \
  --confirm-live \
  --request-file backend/.deer-flow/soc-internal-validation/legacy-compat/task-request.local.json \
  --report-path backend/.deer-flow/soc-internal-validation/legacy-compat/live-acceptance.json
```

The report passes only when the first submission is fresh, an identical replay
returns the same task, the Runtime produces a run and model name, the lifecycle
check is real and pending, and the real callback has a delivered append-only
attempt. It stores hashes and statuses, never the request, result, app key, or
callback payload. Confirm the same result in the old ZEUS UI before expanding
from one alert to 5, 50, and then the shadow corpus.

```bash
export TARGET_REPO="${TARGET_REPO:-$HOME/deer-flow}"
cd "$TARGET_REPO"
eval "$(backend/.venv/bin/python backend/scripts/soc_pingan_local_paths.py --shell)"
source ./.env.soc-dev.local
export D12B_ASSET_KEY="<approved-internal-test-value>"

backend/.venv/bin/python backend/scripts/soc_pingan_model_gateway_smoke.py \
  --confirm-live \
  --report-path backend/.deer-flow/soc-internal-validation/model/model-gateway-smoke.json

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

The expected DeerFlow and gateway alias is `deepseek-v4-flash`; the internal
upstream model and EAGW scene are operator-owned private configuration. Local DEV persistence remains
the separate SQLite `backend/.deer-flow/data/soc_agent_dev.db` unless an
explicit SOC database URL overrides it.

The model-gateway smoke sends one fixed prompt containing no alert or business data.
Its mode-`0600` report records endpoint path, model IDs, status, latency, token
usage, output length and output SHA-256. It deliberately omits the API key,
response ID and assistant text. A successful `/models` call alone is not enough;
`outcome=passed` from this chat-completion report is the model connectivity gate.

The preflight performs no network request. It validates both the ZEUS and Agent
Platform HTTPS host allowlists, required credentials, selected environment,
explicit PRD guard, local model profile, and construction of the tracked HTTP
clients. It must pass before direct or MCP smoke.

`SOC_PINGAN_WORKFLOW_APP_ID=YHSYS` identifies the reviewed legacy Agent Platform
application/tenant used by the three ownership workflows. The reviewed source
contains that credential only in its PRD profile, so the profile preparer writes
it to the ignored env file and configures the reviewed PRD endpoint. The PingAn
adapter fixes legacy `message.by` to `WANGWENBIN520`; there is no operator env
override. A PRD target is rejected unless `SOC_PINGAN_WORKFLOW_ENV=prd` and
`SOC_PINGAN_WORKFLOW_PRD_CONFIRMATION=CALL_PINGAN_PRD` are both set.

The matrix `--plan-only` path also issues no request. `--confirm-live` can issue
real internal DEV requests and therefore refuses a non-`.local` filename,
group/world-readable permissions, unresolved case placeholders, a missing
fault-injection reference, or a missing report path. The mode-`0600` aggregate
report keeps only query hashes, expected/observed attempt stages, latency and
error classes; it excludes raw queries, UM values, Provider bodies and override
values. Passing this direct matrix does not replace the D12-B MCP, persisted
`InvestigationEvidence`, or Web/TUI/Lead Agent readback gates.
