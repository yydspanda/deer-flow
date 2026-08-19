# PingAn SOC Integration Guide

This directory contains PingAn-only source adapters, providers, policies, and internal
handoff helpers. Generic Runtime sees only canonical contracts. Never put PingAn field
names, lifecycle codes, credentials, environment assumptions, or policy shortcuts in
generic `soc_agent` code.

## Input And Normalization

- `zeusRawLogs[].message` is parsed only here. When a parseable message exists it is the
  primary analysis source; outer ZEUS fields remain raw audit/provenance and may provide
  only explicitly reviewed metadata. If message is absent, use the reviewed structured
  fallback for that source/topic and preserve all raw fields.
- Multiple raw logs stay in raw input. Bounded analysis may choose representative
  messages but must record compaction, omissions, and provenance; never combine
  independent value distributions into an invented event.
- Message and nested JSON-in-string decoders are allowlisted and size bounded. Failed
  nested decode preserves the original string plus a warning. Only conservative,
  lossless repair is accepted and every repair is audited.
- Encoded/binary-like values are compacted to typed length/hash placeholders only in the
  bounded LLM projection. Raw input is unchanged. Encoding compaction alone is not an
  evidence gap.
- Adapters emit generic role claims, scenario signals, typed observations, source-field
  semantics, trust, and provenance. Do not infer attacker/victim globally from aliases
  such as `sip`, `source_ip`, `dst_addr`, or `str_attack_ip`.
- Keep host, asset, account, process, file, network, HTTP, and detector observations
  distinct. A source-specific default/sentinel value must be excluded with an explicit
  reason, not silently treated as a real entity.

## Source Families

- SIEM structured fallback is high trust only for the reviewed ZEUS SIEM topic/profile;
  the same field names in other sources remain low/unknown until reviewed.
- EDR mapping owns nested `detailsN`, process trees, command lines, users, hashes, paths,
  hosts, network observations, and MITRE fields. Ambiguous `device__ip` or
  `str_attack_ip` values stay candidates until semantics establish their role.
- NDR/APT mapping owns each parsed message as one observation and preserves five-tuple,
  HTTP, file, IOC, detector, and provider-reported session direction. Reverse connection
  does not imply source=attacker.
- HIDS mapping owns endpoint identity, process ancestry, command/path/user/hash and event
  network observations. Known source sentinels are retained in raw input and excluded
  from semantic facts with provenance.
- Threat-intelligence source normalization keeps provider IOC labels and freshness
  separate from live TI provider results.

## Provider Rules

- Internal dependencies unavailable outside PingAn use explicit fake/mock modes with
  `mocked=true`. A simulated pass never closes Real Integration Debt. Internal mode must
  fail closed and must never fall back to fake data after configuration/provider failure.
- `asset.locate` uses the self-contained PingAn provider and the reviewed
  `searchAssetInfo -> asset_to_bu -> UM` fallback chain. Only explicit `not_found`
  advances the chain; auth, timeout, malformed response, or provider failure stops it.
- Agent Platform workflows use `agent_workflow.py`, not legacy import roots. For the
  reviewed ownership workflows, `message.by` remains the adapter-owned protocol value
  `WANGWENBIN520`. Credentials stay in private overlay/environment.
- Threat Intel exposes only the generic `threat_intel.ip_reputation.lookup` action and a
  reviewed subset of source fields. Do not migrate legacy hardcoded risk/geo/whitelist or
  blocking logic without a reviewed contract.
- Security Tag exposes only `security_tag.lookup`. Preserve active, expired, inactive,
  conflicting, unknown, out-of-scope, and unusable records. Missing expiry is unknown
  unless a reviewed tenant rule says otherwise. A tag result is investigation evidence,
  not a benign verdict or authorization fact.
- Software path lookup compiles the historical workbook offline into a protected catalog
  and exposes `endpoint.software_path.lookup`. Keep exact/family source lineage. `D:`,
  user-writable, temporary, partial-coverage, and hash-conflict cases do not gain ignore
  authority from a historical safe path.
- Every read-only provider result is an `InvestigationEvidence` record with provider
  mode, mock flag, freshness/hash, and `decision_impact=none`. It cannot directly change
  verdict, close review, write Memory, or execute response.

## Tenant Policy And External Lifecycle

- ZEUS status/reason values are translated into
  `SocExternalDispositionIngressCommand`; generic Runtime never recognizes PingAn codes
  or copies legacy short-circuit behavior.
- PingAn disposition is a separate, versioned, operator-selected post-Runtime policy in
  `off|shadow|enforced` mode. It consumes generic signals and records the before/after
  decision. Do not encode uncertain rules such as HTTP 200/non-200 semantics without a
  reviewed canonical field contract.
- Tenant environment exemptions, authorized tests, red/blue/white-team facts,
  maintenance windows, and internal automation are governed context/policy facts. Topic
  names alone do not prove environment or safety.
- A policy decision does not itself authorize an external action. Automation policy,
  target coherence, approval requirements, provider mode, idempotency, and execution
  audit remain separate gates.

## Internal DEV And Handoff

- The internal model is accepted through the loopback OpenAI-compatible smoke in
  `backend/scripts/soc_pingan_litellm_smoke.py`. Reports must not retain keys, fixed
  prompts/responses, or business payloads.
- Internal Apple Silicon handoff uses
  `scripts/build_pingan_macos_offline_bundle.py`. Resolve checkout paths at runtime; do
  not commit fixed `/Users/...` paths. Private overlay builders reject placeholders,
  stale legacy import keys, permissive local config, and secrets in source archives.
- Source and private-data/config archives have independent manifests and hashes. Never
  package `*.local`, PKL, XLSX, SQLite, credentials, or generated internal results in the
  source archive.
- Live matrices/runners require explicit confirmation, protected local case files, and
  redacted reports. Prove direct provider behavior, MCP/action dispatch, persisted
  evidence, and Review/Lead-Agent readback separately. A direct smoke does not prove the
  full business path.
