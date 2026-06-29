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
- `soc normalize inspect sample.json --pretty`
- minimal YAML mapping-file support through `soc normalize inspect sample.json --mapping vendor.yaml`
- tests for missing fields, XFF alias normalization, UM/user identity handling, and entity counts

Non-goals for this slice:

- no real LLM calls
- no drift scheduler yet
- no automatic mapping changes

## Mapping Config MVP

Mapping config is the lightweight onboarding path for simpler vendors. It maps
explicit source paths into canonical `AlertInput` paths, then reuses the same
entity extractor and report generator as every other normalization path.

Example:

```yaml
name: sample-waf
source:
  source_type: waf
  source_system: sample-waf
fields:
  alert_id: $.event.id
  detection.rule_name: $.rule.name
  classification.severity: $.risk.severity
  entities.network.source_ip: $.client.ip
  entities.http.x_forwarded_for: $.request.headers.x-forwarded-for
```

Current command:

```bash
soc normalize inspect backend/samples/alerts/mapped_waf.json \
  --mapping backend/samples/mappings/sample_waf.yaml \
  --pretty
```

Constraints:

- mapping files only move explicit fields; they do not infer fields
- source paths use a minimal `$.a.b.c` path syntax, with list indexes allowed as numeric segments
- target paths must be canonical `AlertInput` paths
- missing source paths become `NormalizationReport.warnings` and `unmapped_fields`
- report adapter is `mapping:<name>` so drift can be grouped by mapping file
- LLM may propose mapping changes later, but a human must review and commit them

## Future Work

Next increments:

1. drift aggregation over recent runs
2. LLM-assisted `soc normalize suggest sample.json`
3. human-reviewed mapping patch workflow
4. richer path syntax if real vendor samples require it
