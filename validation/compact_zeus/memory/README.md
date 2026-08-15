# SOC Memory Validation

This directory contains offline, replay-stable validation for memory admission
and retrieval. It does not call an LLM or an internal PingAn service.

`seed_confirmed_memory_from_batch.py` is an explicit same-cohort experiment helper.
It converts reviewed Runtime batch outputs into `simulation` / `eval_fixture`
candidates, then uses the production Admission, confirmation, and retrieval-activation
services to populate an isolated database. It requires `--confirm-in-sample`; its
records prove Memory wiring only and must never be reported as independent truth or
production knowledge.

```bash
cd backend
PYTHONPATH=. .venv/bin/python \
  ../validation/compact_zeus/memory/seed_confirmed_memory_from_batch.py \
  --input-items .deer-flow/soc-validation/<baseline>/items \
  --database-url sqlite:////absolute/path/to/soc-memory-eval.sqlite \
  --output .deer-flow/soc-validation/<experiment>/memory-seed-report.json \
  --init-db \
  --confirm-in-sample
```

`compare_role_memory_batches.py` compares a Role-Verifier baseline against the
same frozen cohort with governed confirmed Memory enabled. It replays the
production Retrieval v2 selector against the isolated database, verifies that
the selected record IDs match the `M-*` catalog frozen into each Runtime
request, and reports per alert:

- retrieval rank, score, exact match reasons, and strong-anchor facets;
- source alert and whether the selected record came from the same alert;
- whether accepted model output merely received or explicitly cited the Memory;
- verdict, review, direction, role, output-quality, token, and latency changes.

```bash
cd backend
PYTHONPATH=. .venv/bin/python \
  ../validation/compact_zeus/memory/compare_role_memory_batches.py \
  --baseline-dir .deer-flow/soc-validation/<role-verifier-baseline> \
  --current-dir .deer-flow/soc-validation/<role-verifier-memory-run> \
  --seed-report .deer-flow/soc-validation/<role-verifier-memory-run>/memory-seed-report.json \
  --database-url sqlite:////absolute/path/to/soc-memory-eval.sqlite
```

This is a wiring and consistency experiment. Same-alert Memory is intentional
in-sample leakage, separate LLM calls remain stochastic, and a selected Memory
without an accepted `M-*` citation was available but was not explicitly used by
the model. Use held-out alerts plus independent analyst labels for quality
claims.

`build_memory_retrieval_v2_review.py` reads persisted real-alert Runtime batch
items, rebuilds each pre-LLM memory query, and compares:

- `soc.memory_retrieval_policy.v1`: broad scored retrieval.
- `soc.memory_retrieval_policy.v2`: the same recall lanes plus a
  memory-type-specific exact anchor gate.

The comparison corpus is controlled: each real alert query receives one
matching reviewed-memory fixture and one same-source-only fixture. This proves
query and gate semantics over real normalized alerts; it is not a production
precision measurement and does not claim that the fixtures are real confirmed
memory.

```bash
cd backend
PYTHONPATH=. .venv/bin/python ../validation/compact_zeus/memory/build_memory_retrieval_v2_review.py \
  --input .deer-flow/soc-validation/e2e-ten-current/runtime-batch/items \
  --output .deer-flow/soc-validation/e2e-ten-current/memory-retrieval-v2-review.json
```
