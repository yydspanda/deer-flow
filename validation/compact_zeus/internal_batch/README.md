# PingAn Internal Runtime Batch

This runner replays a Pandas PKL export through the same production
`SocAnalysisService` used by `soc analyze`. It is an internal validation entry
point, not a second Runtime and not a Kafka replacement.

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
  database kind, enrichment composition, and every action-config fingerprint.
  Completed items are skipped; failed items retry unless
  `--skip-existing-failures` is set.
- Full item artifacts contain the preserved input payload and are sensitive.
  Do not copy them back outside the approved environment without review.

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
  --confirm-live \
  --resume
```

If interrupted, rerun the exact command with `--resume`. Do not change the
source file, model, sensitive-evidence mode, or persistence mode inside one
batch directory.

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

## Optional PI-01D3/D4 investigation and reporting

Validate a local composition and MCP allowlist without calling either the LLM
or a Provider:

```bash
backend/.venv/bin/python \
  validation/compact_zeus/internal_batch/run_pingan_runtime_batch.py \
  --source /approved/path/alerts-5000.pkl \
  --limit 5 \
  --plan-only \
  --enrichment-composition backend/samples/enrichment/enabled.dev-mcp.yaml \
  --enrichment-action-config backend/samples/mcp/soc_dev_action_adapters.json
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
  --persist --workers 1 \
  --confirm-live \
  --enrichment-composition /approved/path/pingan-enrichment.local.yaml \
  --enrichment-action-config backend/samples/mcp/pingan_asset/action_adapters.json \
  --enrichment-action-config backend/samples/mcp/pingan_threat_intel/action_adapters.json \
  --enrichment-action-config backend/samples/mcp/pingan_security_tag/action_adapters.json \
  --confirm-investigation
```

The local composition must bind only reviewed routes and tenant network scope,
and use `required_result_mode: real` for the internal profile. Every returned
`runtime_declared` result must contain `mocked=false`; a mismatch is a contract
failure and writes no evidence. Normal not-found is retained as evidence,
while Provider failure is retryable only within the configured budget.

Each item stores `analysis_run` and, when enabled, `investigation_workflow`,
`investigation_shadow_report`, and `investigation_addendum`. If investigation
fails, the item is failed but the successful base run remains present.
Duplicate/resumed items reuse the durable execution; completed Provider calls
and evidence are not repeated. The manifest's `summary.investigation_shadow`
aggregates plan/hit/not-found/failure/retry/provider-call counts, route and
mock/real result counts, evidence coverage, and action-attempt P50/P95/max
latency. Provider-network latency and tool cost remain explicit
`not_measured` gaps rather than fabricated zeros.

## Artifacts

```text
<output-dir>/
├── manifest.json       # lineage, aggregate status, and optional shadow telemetry
├── results.jsonl       # one compact summary per source row
├── items/              # full AnalysisRun plus optional workflow/report/addendum/error
└── .batch.lock         # advisory process lock; it may remain after exit
```

`manifest.status=completed` is a technical batch completion statement. It is
not model-accuracy evidence; analyst labels and PI-03 evaluation remain
separate.
