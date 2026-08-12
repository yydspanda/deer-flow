# SOC Memory Validation

This directory contains offline, replay-stable validation for memory admission
and retrieval. It does not call an LLM or an internal PingAn service.

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
