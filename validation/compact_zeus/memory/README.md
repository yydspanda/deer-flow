# SOC Memory Validation

This directory contains offline, replay-stable validation for memory admission
and retrieval. It does not call an LLM or an internal PingAn service.

`simulate_pattern_memory_lifecycle.py` is the complete isolated `5+1` lifecycle
smoke. It loads one completed Runtime batch item, derives five explicitly marked
`simulation` occurrences with distinct event identities, and invokes the production
Pattern, Memory, Retrieval v2, context projection, and effective-decision services.
The fifth occurrence must create exactly one pattern candidate. The script then
simulates one human confirmation and governed retrieval activation before using a
sixth held-out occurrence to prove exact Memory recall and persisted
`Base -> Memory -> Effective` decision lineage.

The reviewed outcomes injected into the first five occurrences are test fixtures,
not model output or independent analyst labels. The SQLite database is created inside
an empty output directory, all generated records retain `simulation=true`/`mocked=true`,
no LLM or Provider is called, and no external action policy is configured.

```bash
backend/.venv/bin/python \
  validation/compact_zeus/memory/simulate_pattern_memory_lifecycle.py \
  --input-item backend/.deer-flow/soc-validation/<baseline>/items/<alert>.json \
  --output-dir backend/.deer-flow/soc-validation/<experiment>/memory-lifecycle \
  --tenant-id pingan \
  --environment prd \
  --support-count 5 \
  --confirmed-verdict suspicious
```

The output separates observations, candidate, review, confirmed record, held-out
retrieval, and decision lineage into numbered JSON files plus `SUMMARY.md`. Replaying
one unchanged alert five times is intentionally not supported: production
`occurrence_key` deduplication would count it once.

`validate_pattern_memory_generalization.py` validates the applicability boundary
after one Pattern Memory has been confirmed. It keeps the reviewed detection and
behavior stable while changing source/destination IPs, then runs same-IP negative
controls with changed behavior, rule, or environment. Exact IPs remain optional
ranking facets: cross-IP cases must retain decision applicability, while semantic
changes must be context-only or not retrieved. This is a deterministic contract
matrix, not a production precision estimate.

```bash
backend/.venv/bin/python \
  validation/compact_zeus/memory/validate_pattern_memory_generalization.py \
  --input-item backend/.deer-flow/soc-validation/<experiment>/base-runtime/items/<alert>.json \
  --confirmed-memory backend/.deer-flow/soc-validation/<experiment>/lifecycle/04-confirmed-memory.json \
  --output-dir backend/.deer-flow/soc-validation/<experiment>/cross-ip-generalization
```

`seed_confirmed_memory_from_batch.py` is an explicit same-cohort experiment helper.
It converts reviewed Runtime batch outputs into one `simulation` / `eval_fixture`
record per alert, then uses the production Admission, confirmation, and
retrieval-activation services to populate an isolated database. The candidate type,
target artifact and decision impact remain `eval_fixture` / `eval_fixture` / `none`;
they are not detection lessons. It requires `--confirm-in-sample`; its records prove
Memory wiring only and must never be reported as independent truth, production
knowledge, or the expected alert-to-Memory ratio.

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

`build_pattern_memory_review.py` exercises the production pattern-level learning
boundary without confirming or activating Memory. Every eligible Runtime result becomes
an immutable observation, but only a cohort that passes recurrence, distinct-source,
conclusive-outcome, consistency and strong-anchor gates creates one `pending_review`
candidate. The report includes the candidate's analyst-facing lesson text and every
withheld reason. Equivalent lessons in later fixed windows are reported as reinforcement
cohorts and reuse the existing governed candidate instead of creating another expert task.

```bash
cd backend
PYTHONPATH=. .venv/bin/python \
  ../validation/compact_zeus/memory/build_pattern_memory_review.py \
  --input-items .deer-flow/soc-validation/<runtime-run>/items \
  --output .deer-flow/soc-validation/<experiment>/pattern-memory-review.json
```

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
