# Normalization, Drift, and LLM Usage Strategy

> Decision record for SOC Agent alert normalization and entity extraction.
> This complements `soc-agent-solution.md`; it narrows how LLMs should and should not be used around vendor log parsing.

## Decision

Do not run an LLM on every alert for normalization or entity extraction by default.

Production hot path stays deterministic:

```text
alert
-> deterministic normalizer
-> deterministic entity extractor
-> AnalysisRun reports
-> AlertSummary / similar search / review queue
```

LLMs are used off the hot path, or on explicitly bounded exception paths:

- new vendor onboarding
- mapping suggestion
- drift report analysis
- low-volume review queue enrichment
- offline sample analysis and test generation

In short: LLM acts as a mapping engineer and drift analyst, not as the parser for every alert.

## Why

SOC normalization is a fact layer. If a model confuses an analyst handler account with an attacker account, or maps a device owner to the login user, every later step becomes polluted:

- similar-alert retrieval
- review queue priority
- deduplication
- memory candidates
- future automation policy

Per-alert LLM parsing is also too expensive for daemon ingestion. The default path must be cheap, repeatable, auditable, and replayable.

## Normalization Layers

| Layer | Purpose | Runtime Cost | Who Confirms |
|---|---|---:|---|
| Generic normalizer | Common aliases such as `src_ip`, `x-forwarded-for`, `userName` | Low | Tests |
| Mapping config | JSONPath/YAML mapping for simpler vendors | Low | Developer / analyst |
| Python adapter | Complex vendor envelopes, nested raw logs, mixed payloads | Low-medium | Developer / analyst |
| LLM-assisted suggestion | Propose mapping or adapter changes from samples/drift reports | Offline / low frequency | Human required |

## LLM Allowed Uses

LLM may:

- inspect new vendor samples and propose a mapping file
- explain unknown fields in a drift report
- generate candidate golden tests
- suggest adapter changes for review
- enrich selected review queue items with candidate `EntityMention`s

LLM must not:

- dynamically decide production field mapping for every alert
- write `AlertSummary`, review queue, memory, or verdict directly
- turn candidate knowledge into confirmed facts without human review
- hide uncertainty when mapping evidence is weak

## Drift Signals

The runtime should record report fields that allow cheap drift detection:

- adapter used
- missing important normalized fields
- extracted entity counts
- extractor warnings
- source type and source system
- unmapped or unexpected fields when available
- sample payload hash / run id for replay

These reports let the system ask: "Did this vendor format change?" before involving an LLM.

## MVP Implementation

Phase 1 implements:

- `NormalizationReport` on `AnalysisRun`
- `ExtractionReport` on `AnalysisRun`
- deterministic report generation in runtime
- tests for missing fields, XFF alias normalization, UM/user identity handling, and entity counts

Non-goals for this slice:

- no real LLM calls
- no YAML mapping engine yet
- no drift scheduler yet
- no automatic mapping changes

## Future Work

Next increments:

1. `soc normalize inspect sample.json --pretty`
2. mapping-file support for simpler vendors
3. drift aggregation over recent runs
4. LLM-assisted `soc normalize suggest sample.json`
5. human-reviewed mapping patch workflow

