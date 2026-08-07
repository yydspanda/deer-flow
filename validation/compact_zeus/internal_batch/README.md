# PingAn Runtime Batch Validation

This runner replays a Pandas PKL export through the same production
`SocAnalysisService` used by `soc analyze`. It supports external fake-Provider
rehearsal and later internal real acceptance; it is not a second Runtime and
not a Kafka replacement.

## Safety and scope

- PKL is loaded with the repository's restricted DataFrame unpickler.
- Live LLM execution always requires `--confirm-live`.
- Read-only investigation Providers are independently default-off and require
  `--confirm-investigation` before execution.
- Default execution is non-persistent and single-worker.
- Persisted SQLite execution is restricted to one worker.
- Every item is written atomically with mode `0600`; the output directory is
  mode `0700` and Git-ignored.
- Resume validates source, payload, model, evidence mode, persistence,
  database kind, trusted tenant default, enrichment composition, every
  action-config fingerprint, the exact MCP extensions-config fingerprint, and
  optional repeated-pattern policy/scope fingerprints.
  Completed items are skipped; failed items retry unless
  `--skip-existing-failures` is set.
- Full item artifacts contain the preserved input payload and are sensitive.
  Do not copy them back outside the approved environment without review.
- `--default-tenant-id` fills only missing trusted ingress metadata. A source
  row that already declares another tenant is rejected; the value is sealed in
  the manifest and must match the enrichment-policy tenant.

By default this runner executes the fixed SOC Runtime only and calls no MCP
tool. PI-01D3 adds an explicit optional bridge after a successful persisted
`AnalysisRun`: deterministic Planner -> Policy -> Dispatcher -> exact Adapter
Registry -> durable `InvestigationEvidence`. It never routes from free text and
cannot mutate the base verdict. `endpoint.software_path.lookup` is not in the
automatic Planner allowlist and remains a governed analyst/Lead Agent action.
PI-01D4 then projects the persisted workflow into a secret-free shadow report
and a deterministic analyst addendum. Projection calls no Provider, creates no
new conclusion, and writes no second report state.

## Recommended ramp

From the repository root, source the internal DEV profile first:

```bash
source ./.env.soc-dev.local
```

Inspect the source and exact model-call count without calling a model:

```bash
backend/.venv/bin/python \
  validation/compact_zeus/internal_batch/run_pingan_runtime_batch.py \
  --source /approved/path/alerts-5000.pkl \
  --analyzer-mode llm \
  --model-name deepseek-v4-flash \
  --limit 5 \
  --default-tenant-id pingan \
  --plan-only
```

Run five live rows first:

```bash
export BATCH_DIR="$PWD/backend/.deer-flow/soc-internal-validation/runtime-batches/pingan-dev-001"

backend/.venv/bin/python \
  validation/compact_zeus/internal_batch/run_pingan_runtime_batch.py \
  --source /approved/path/alerts-5000.pkl \
  --output-dir "$BATCH_DIR" \
  --analyzer-mode llm \
  --model-name deepseek-v4-flash \
  --limit 5 \
  --default-tenant-id pingan \
  --confirm-live
```

Review `manifest.json`, `results.jsonl`, and the five `items/*.json` files. If
they are acceptable, expand the **same** batch from index zero to 50; exact
completed rows will be skipped:

```bash
backend/.venv/bin/python \
  validation/compact_zeus/internal_batch/run_pingan_runtime_batch.py \
  --source /approved/path/alerts-5000.pkl \
  --output-dir "$BATCH_DIR" \
  --analyzer-mode llm \
  --model-name deepseek-v4-flash \
  --limit 50 \
  --default-tenant-id pingan \
  --confirm-live \
  --resume
```

After reviewing the 50-row report, omit `--limit` to process the full source:

```bash
backend/.venv/bin/python \
  validation/compact_zeus/internal_batch/run_pingan_runtime_batch.py \
  --source /approved/path/alerts-5000.pkl \
  --output-dir "$BATCH_DIR" \
  --analyzer-mode llm \
  --model-name deepseek-v4-flash \
  --default-tenant-id pingan \
  --confirm-live \
  --resume
```

If interrupted, rerun the exact command with `--resume`. Do not change the
source file, model, sensitive-evidence mode, trusted tenant default, or
persistence mode inside one batch directory.

## Optional SQLite persistence

Persistence creates AnalysisRun, summary, ReviewQueue, audit, and normalization
maintenance records through the normal transaction boundary. Initialize the
schema first:

```bash
cd backend
./.venv/bin/python -m soc_agent.cli db upgrade
cd ..
```

Then add `--persist --workers 1` from the first five-row run onward. Persistence
mode cannot be changed during resume. The DEV database remains:

```text
backend/.deer-flow/data/soc_agent_dev.db
```

For 5,000 live rows, do not increase workers until the five/50-row latency,
error rate, model concurrency, and local database behavior are reviewed.

## Optional PI-03F3 repeated-pattern observations

Repeated-pattern learning is independently default-off. It runs only after a
successful persisted Runtime result, and each row is an immutable observation,
not a memory candidate. Enable it from the first run with explicit scope and
policy arguments:

```bash
backend/.venv/bin/python \
  validation/compact_zeus/internal_batch/run_pingan_runtime_batch.py \
  --source /approved/path/alerts-5000.pkl \
  --output-dir "$BATCH_DIR" \
  --analyzer-mode llm \
  --model-name deepseek-v4-flash \
  --limit 5 \
  --default-tenant-id pingan \
  --persist --workers 1 \
  --confirm-live \
  --memory-pattern-data-class operational \
  --memory-pattern-environment dev \
  --memory-pattern-window-seconds 86400 \
  --memory-pattern-minimum-support 5 \
  --memory-pattern-minimum-distinct-sources 5
```

The cohort uses one strongest available generic dimension: primary scenario,
canonical detection key, then category. Tenant, environment, and data class
never share a cohort. Its window uses canonical timezone-aware source
`event_time`; missing or naive source time produces a non-blocking
`skipped_ineligible` result rather than guessing a timezone or using batch run
time. Reaching both thresholds creates one frozen `pending_review` candidate;
later rows are replay-only. Inspect persisted state with:

```bash
cd backend
./.venv/bin/python -m soc_agent.cli memory patterns list --environment dev --data-class operational --pretty
./.venv/bin/python -m soc_agent.cli memory patterns replay AGGREGATION_KEY --pretty
cd ..
```

Changing any pattern option during `--resume` is rejected. Aggregation failure
does not change the base Runtime verdict or item completion state.

## Optional PI-01D3/D4 investigation and reporting

Validate a local composition and MCP allowlist without calling either the LLM
or a Provider:

```bash
backend/.venv/bin/python \
  validation/compact_zeus/internal_batch/run_pingan_runtime_batch.py \
  --source /approved/path/alerts-5000.pkl \
  --limit 5 \
  --plan-only \
  --default-tenant-id pingan \
  --enrichment-composition backend/samples/enrichment/enabled.dev-mcp.yaml \
  --enrichment-action-config backend/samples/mcp/soc_dev_action_adapters.json \
  --enrichment-extensions-config backend/samples/mcp/soc_dev_extensions_config.json
```

Execution requires a migrated SOC database, `--persist`, and a separate
confirmation for Provider reads:

```bash
backend/.venv/bin/python \
  validation/compact_zeus/internal_batch/run_pingan_runtime_batch.py \
  --source /approved/path/alerts-5000.pkl \
  --output-dir "$BATCH_DIR" \
  --analyzer-mode llm \
  --model-name deepseek-v4-flash \
  --limit 5 \
  --default-tenant-id pingan \
  --persist --workers 1 \
  --confirm-live \
  --enrichment-composition backend/samples/enrichment/pingan-external-simulation.yaml \
  --enrichment-action-config backend/samples/mcp/pingan_asset/action_adapters.json \
  --enrichment-action-config backend/samples/mcp/pingan_security_tag/action_adapters.json \
  --enrichment-extensions-config backend/samples/mcp/pingan_shadow/extensions.simulated.json \
  --confirm-investigation
```

Before the first LLM call, live investigation mode connects to every enabled
MCP server and requires the exact `(server, tool)` named by each action config.
Missing command/environment, server, or tool fails the batch before analysis;
`--plan-only` intentionally performs only static config validation and no MCP
discovery.

The external composition uses `required_result_mode: mock` and the combined
extensions profile starts the real PingAn MCP server code with fake transports.
Every returned `runtime_declared` result must contain `mocked=true`; a mismatch
is a contract failure and writes no evidence. For internal acceptance, switch
only to `pingan-internal-shadow.yaml` and
`pingan_shadow/extensions.internal.json`, inject reviewed environment
values, and require `mocked=false`. Normal not-found is retained as evidence,
while Provider failure is retryable only within the configured budget.

Each item stores `analysis_run` and, when enabled, `investigation_workflow`,
`investigation_shadow_report`, and `investigation_addendum`. If investigation
fails, the item is failed but the successful base run remains present.
Duplicate/resumed items reuse the durable execution; completed Provider calls
and evidence are not repeated. When explicit `--resume` encounters a persisted
failed base `AnalysisRun`, the runner uses `SocAnalysisService.replay()` to
create a linked run and records `execution.analysis_retry_of_run_id`; reusing
the original failed idempotency result is forbidden. The manifest's
`summary.investigation_shadow`
aggregates plan/hit/not-found/failure/retry/provider-call counts, route and
mock/real result counts, evidence coverage, and action-attempt P50/P95/max
latency. Provider-network latency and tool cost remain explicit
`not_measured` gaps rather than fabricated zeros.

## PI-01E paired shadow gate

Each PI-01E evidence class uses two different directories over the exact same
source cohort:

1. a Runtime compatibility batch with no enrichment arguments and no Provider
   calls;
2. a persisted investigation batch with an explicit composition, action
   configs and extensions config.

Always run `external_simulation` first with
`backend/samples/enrichment/pingan-external-simulation.yaml` and
`backend/samples/mcp/pingan_shadow/extensions.simulated.json`. It selects
`asset.locate` plus `security_tag.lookup`, explicitly leaves `asset.lookup`
disabled, and expects only `mocked=true`. The matching tracked internal files
use the same routes with `required_result_mode=real` and Provider mode
`internal`; secrets remain environment-only. Threat intelligence stays disabled
until reviewed tenant network ranges exist.

After both five-row batches complete, seal and evaluate them without calling
the LLM, MCP discovery, or any Provider:

```bash
backend/.venv/bin/python \
  validation/compact_zeus/internal_batch/evaluate_pingan_shadow.py \
  --runtime-batch-dir "$RUNTIME_BATCH_DIR" \
  --investigation-batch-dir "$INVESTIGATION_BATCH_DIR" \
  --enrichment-composition backend/samples/enrichment/pingan-external-simulation.yaml \
  --enrichment-action-config backend/samples/mcp/pingan_asset/action_adapters.json \
  --enrichment-action-config backend/samples/mcp/pingan_security_tag/action_adapters.json \
  --enrichment-extensions-config backend/samples/mcp/pingan_shadow/extensions.simulated.json \
  --acceptance-mode external_simulation \
  --ramp-stage 5 \
  --report-path "$INVESTIGATION_BATCH_DIR/pi-01e-external-simulation-5.json"
```

If the internal composition also enables threat intelligence, pass its action
config to both the investigation runner and evaluator in the same order. The
evaluator checks the sealed config hashes against the batch manifest.

After external stage 50 passes, use the fixed internal-real orchestrator. Its
default mode runs both static batch plans only: it performs no MCP discovery,
LLM call, Provider call, database migration, or output write.

```bash
source ./.env.soc-dev.local
export PI01E_ROOT="$PWD/backend/.deer-flow/soc-internal-validation/internal-real/pingan-dev-001"

backend/.venv/bin/python \
  validation/compact_zeus/internal_batch/run_pingan_internal_shadow.py \
  --source /approved/path/alerts-5000.pkl \
  --output-root "$PI01E_ROOT" \
  --ramp-stage 5
```

After reviewing that single JSON plan, execute the same source/root/stage with
all three explicit acknowledgements:

```bash
backend/.venv/bin/python \
  validation/compact_zeus/internal_batch/run_pingan_internal_shadow.py \
  --source /approved/path/alerts-5000.pkl \
  --output-root "$PI01E_ROOT" \
  --ramp-stage 5 \
  --execute --confirm-live --confirm-investigation
```

The thin orchestrator does not implement Runtime or Provider behavior. It
stops on the first failed step and invokes existing boundaries in this order:

1. PingAn no-network environment preflight;
2. actual MCP server startup and exact `list_tools()` inventory, without LLM
   or Provider tool invocation;
3. migration of a purpose-specific SQLite database inside the output root;
4. provider-free Runtime compatibility batch;
5. persisted real read-only investigation batch;
6. `evaluate_pingan_shadow.py --acceptance-mode internal_real`.

An interruption or retry uses the exact same command plus `--resume`. Only the
two batch steps receive that flag; the environment/MCP preflights and evaluator
always rerun. The orchestration report is `orchestration-<stage>.json`, mode
`0600`; it records step status and the final gate summary but never captures
environment values. Any fake Provider mode or `mocked=true` result is a
blocking failure; do not relabel the external artifacts.

Fresh live execution accepts only a missing or empty `--output-root`; it fails
before preflight if the directory already contains data. Resume requires that
the same root already contains the matching `orchestration-<stage>.json`.
These checks prevent an accidental repository-root `chmod`, evidence mixing,
or a resume against a different stage.

`soc.pingan_shadow_acceptance.v2` verifies:

- identical source rows and payload fingerprints across both batches;
- identical model/evidence profiles and deterministic pre-LLM projections;
- Runtime-only isolation, persisted investigation, exact adapter bindings and
  exact enabled MCP servers/provider modes;
- exact trusted tenant scope and composition/action/extensions fingerprints;
- mode-specific result provenance: all mock for `external_simulation`, all real
  for `internal_real`;
- no `asset.lookup`, missing evidence, verdict mutation,
  auto-close, confirmed-memory write, or high-risk action;
- at least one planned action and real terminal result for every configured
  route, so one working Provider cannot hide an unexercised binding;
- Provider hit/not-found/error rates, effective evidence rate, action-attempt
  P50/P95/max, review rate, LLM token/cost status, and schema observations;
- explicit `not_measured` Provider-network latency and monetary-cost gaps.

A passing five-row external report only makes the simulated batch eligible for
human review before expanding to external 50. It cannot close a real Provider
gate. The reviewed 2026-08-05 stage-50 report is Git-ignored at
`backend/.deer-flow/soc-internal-validation/external-simulation/pi-01e-20260805-50-v2/pi-01e-external-simulation-50.json`:
both cohorts completed 50/50, 157/157 fake results became evidence, and no
failure or unauthorized side effect occurred. All Provider results were normal
not-found, so the report warns that the hit path was not observed and retains
`closes_real_provider_gate=false`. Start separate internal-real directories at
5; do not relabel or reuse mock artifacts as real. No report expands
automatically, evaluates model accuracy, closes a Provider-specific gate, or
sets `pilot_ready=true`.

## PI-01G native specialist delegation smoke

This smoke validates the real DeerFlow Lead Agent -> native `task` -> managed
SOC specialist path against one persisted ReviewQueue item. It calls the
configured model; it does not turn simulated Provider results into real facts.

Install or refresh the managed configuration first:

```bash
cd backend
./.venv/bin/python -m soc_agent.cli agent install-profile --overwrite --pretty
./.venv/bin/python -m soc_agent.cli agent install-subagents \
  --config ../config.yaml --apply --overwrite --pretty
./.venv/bin/python -m soc_agent.cli agent doctor --pretty
cd ..
```

Run one narrow specialist expectation from the repository root:

```bash
backend/.venv/bin/python \
  validation/compact_zeus/internal_batch/validate_soc_lead_agent_delegation.py \
  --database-url sqlite:////ABSOLUTE/PATH/TO/soc-shadow.db \
  --queue-id REV-EXAMPLE \
  --model-name deepseek-v4-flash \
  --expected-specialist soc-network-specialist
```

For endpoint evidence, select `soc-endpoint-specialist` and pass a narrow
endpoint-specific `--message`; web and email use their corresponding managed
names. The command requires exactly one completed delegation to the expected
specialist. It fails when context is missing, a task fails, the graph is
capped/stopped, provenance is absent, or a different/multiple specialist set
completes.

Reports default to the Git-ignored directory:

```text
backend/.deer-flow/soc-lead-agent-validation/SOC-PI01G-SMOKE-*.json
```

Review these report fields:

- `summary.passed=true` and exactly one expected completed specialist;
- one ReviewQueue context event and at least one native `task_started` plus
  `task_completed`, with zero `task_failed` and zero capped tasks;
- one accepted `soc_specialist_delegation` provenance record with bounded
  context/task/projection lineage;
- `real_model_called=true` and `provider_acceptance_claimed=false`;
- any action evidence inside the case retains its original `mocked` status.

The reviewed 2026-08-07 local evidence includes one NIDS network case and one
EDR endpoint case. Those reports close the PI-01G product execution gate only;
they do not close D12-B, PI-01A/B, PI-03 real-quality, or production rollout
gates.

## Artifacts

```text
<output-dir>/
├── manifest.json       # lineage, aggregate status, and optional shadow telemetry
├── results.jsonl       # one compact summary per source row
├── items/              # full AnalysisRun plus optional workflow/report/addendum/error
├── pi-01e-*.json       # optional secret-free paired gate report
└── .batch.lock         # advisory process lock; it may remain after exit
```

`manifest.status=completed` is a technical batch completion statement. It is
not model-accuracy evidence; analyst labels and PI-03 evaluation remain
separate.
