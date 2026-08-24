# SOC Memory Held-out Evaluation

`pingan_profile_v6_simulation_v1.json` is the current simulation-only wiring baseline. Its
Shell Memory is an analyst-readable business lesson: an exact AskBob internal LLM
endpoint caused a reviewed reverse-connection false positive. It records the
conclusion, business rationale, applicability, cross-IP boundary, invalidation
conditions, and handling guidance instead of a generic recurrence sentence.
Those sections use the same `soc.memory_business_lesson.v1` stored by production
`SocMemoryService`; this fixture supplies reviewed truth only for held-out evaluation.
The production service, not this test file, owns lesson validation, rendering,
persistence, and retrieval. A fixture-provided lesson is input truth, not an
implementation of those behaviors.

`pingan_profile_v4_simulation_v1.json` remains a historical frozen artifact. It is
not the default and must fail closed under Profile v6 instead of being silently
reinterpreted with newer behavior features.

The fixture contains frozen, retrieval-active Memory records and disjoint held-out
queries:

- an exact cross-IP AskBob match that may apply the reviewed `false_positive`
  directive because the exact canonical URL is still present;
- the same AskBob service and detector with a different fingerprint and one shared
  strong behavior component, which is context-only;
- the same detector and behavior fingerprint against an unreviewed service URL,
  which must not retrieve or change the decision;
- unrelated records that must not be retrieved.

Run the baseline:

```bash
cd backend
uv run soc eval memory run --pretty
```

Prepare a real pending-review fixture from frozen `AnalysisRun` artifacts and a
reviewed Memory export:

```bash
cd backend
uv run soc eval memory prepare .deer-flow/soc-validation/RUNS \
  --glob '*.json' \
  --memory-records .deer-flow/soc-validation/memory-records.json \
  --description 'PingAn held-out Memory review set v1' \
  --tenant-id pingan \
  --environment prd \
  --data-class desensitized_real \
  --source-ref 'internal:memory-eval:v1' \
  --output .deer-flow/soc-validation/memory-eval-v1.pending.json \
  --pretty
```

An analyst must fill each case's `truth` object. Every accepted case labels all
frozen Memory records as `decision_applicable`, `context_only`, or `unrelated`,
and records verdict, review requirement, reviewer provenance, plus optional
scenario, boundary-direction, and role truth. Training/source alert IDs may not
overlap held-out alert IDs.

The report measures actual Retrieval v2 and Base-to-Memory decision behavior.
It is always read-only and never grants rollout or external-action authority.
