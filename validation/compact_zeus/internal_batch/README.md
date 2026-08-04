# PingAn Internal Runtime Batch

This runner replays a Pandas PKL export through the same production
`SocAnalysisService` used by `soc analyze`. It is an internal validation entry
point, not a second Runtime and not a Kafka replacement.

## Safety and scope

- PKL is loaded with the repository's restricted DataFrame unpickler.
- Live LLM execution always requires `--confirm-live`.
- Default execution is non-persistent and single-worker.
- Persisted SQLite execution is restricted to one worker.
- Every item is written atomically with mode `0600`; the output directory is
  mode `0700` and Git-ignored.
- Resume validates source, payload, model, evidence mode, persistence, and
  database-kind fingerprints. Completed items are skipped; failed items retry
  unless `--skip-existing-failures` is set.
- Full item artifacts contain the preserved input payload and are sensitive.
  Do not copy them back outside the approved environment without review.

This runner executes the fixed SOC Runtime only. It does not autonomously call
MCP tools. `asset.locate` and `endpoint.software_path.lookup` remain governed
Lead Agent/Action Dispatcher enrichments whose results enter
`InvestigationEvidence` separately.

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

## Artifacts

```text
<output-dir>/
├── manifest.json       # source/model/persistence lineage and aggregate status
├── results.jsonl       # one compact summary per source row
├── items/              # full AnalysisRun or bounded error per row
└── .batch.lock         # advisory process lock; it may remain after exit
```

`manifest.status=completed` is a technical batch completion statement. It is
not model-accuracy evidence; analyst labels and PI-03 evaluation remain
separate.
