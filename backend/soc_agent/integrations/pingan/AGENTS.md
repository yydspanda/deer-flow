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

## Tenant Knowledge And Playbooks

- Stable, reviewed PingAn facts and first-alert playbooks live in versioned profiles under
  `knowledge/` and project only bounded `C-*` context. They are not public Skills,
  confirmed Memory, Tenant Policy, or action authorization.
- Profile selectors consume canonical typed entities only. Host/process/path/account/URI
  and command-line selectors must not scan raw payloads or vendor rule text. Non-empty
  selector groups are combined with AND; broad single-key rules are not acceptable for a
  benign playbook.
- A reviewed playbook may help the LLM choose the Base Decision on a first-seen alert, but
  every statement must include current-evidence requirements and invalidation conditions.
  It always projects `decision_authority=none`; operational ignore/transfer remains a
  separate Tenant Policy decision.
- Multi-edge process playbooks may combine fragments only when the Adapter assigned the
  same canonical `event_scope_id` and the fragments form a connected process component
  through the same normalized process name plus the same non-null PID. A repeated common
  process name with missing PID is not a cross-observation identity.
  Exact commands such as read-only `net share` use exact-command selectors; substring
  matching must not grant the same context to mutating variants.
- Product update and installer Playbooks must retain product-specific identity instead
  of declaring an installer framework broadly benign. Direct-parent constraints consume
  canonical parent fields, and file relation/name/path constraints must match one
  canonical file observation; do not join `str_suspicious_file`, `str_ioc_value`, or
  unrelated `detailsN` paths after normalization.
- `str_ioc_value` is polymorphic. The EDR Adapter may project an absolute, file-shaped
  value as a distinct `observed_artifact` with exact provenance; it must not overwrite
  the process image from `str_suspicious_file`, and a non-path IOC must not be invented as
  a file. This evidence projection alone does not classify the artifact as benign or
  malicious.
- Repeated, analyst-confirmed case outcomes belong to governed Memory rather than static
  profiles. Event-time authorization, red-team identity, maintenance windows, and current
  asset state belong to governed context or a read-only provider.

## Internal DEV And Handoff

- The internal model is accepted through the loopback OpenAI-compatible smoke in
  `backend/scripts/soc_pingan_model_gateway_smoke.py`. The loopback service is owned by
  this repository and maps the stable `deepseek-v4-flash` alias to an operator-owned
  EAGW/OpenAI-compatible upstream. The baseline smoke must match the default SOC Runtime
  mode (`thinking=false`) and use a bounded but sufficient completion budget; reasoning
  capability is an explicit opt-in check, not part of basic connectivity. Success and
  failure reports retain the requested thinking/effort/token settings but must not retain
  keys, fixed prompts/responses, or business payloads.
- Old ZEUS compatibility uses a separate PingAn API/worker composition over the generic
  `ProcessingJobRepository`: persist before accepting, claim with a lease, reuse the
  stable Runtime idempotency key after recovery, and commit terminal result plus Callback
  Outbox atomically. Callback attempts are append-only and callback retries must never
  rerun analysis. Do not restore Celery, Redis, old LlamaIndex flows, or old model routing.
- Preserve the old `8090` ingress only in the PingAn Host DEV/private deployment profile;
  keep per-`app_code` authentication and body limits, and restrict the port to approved
  ZEUS callers. Only `executeType=1/3` receives the legacy 30-minute queue deadline.
  Expiration skips LLM but still persists a legacy expiration result and Callback Outbox;
  the generic queue must not learn this tenant rule.
- Treat the fake execution-plane acceptance and the live compatibility acceptance as
  separate gates. Live acceptance must use a fresh approved pending alert, prove identical
  replay returns one job, read non-mocked lifecycle evidence and Runtime lineage from the
  same database, and require a delivered non-mocked callback attempt. Never place the
  request, result, callback payload, or app key in its report.
- The model gateway currently uses one process-local capacity semaphore; launch exactly
  one gateway process. Scale processing workers only after the internal model service has
  a reviewed shared admission mechanism or measured capacity increase.
- Internal Apple Silicon handoff uses
  `scripts/build_pingan_macos_offline_bundle.py`. Resolve checkout paths at runtime; do
  not commit fixed `/Users/...` paths. Private overlay builders reject placeholders,
  stale legacy import keys, permissive local config, and secrets in source archives.
- Before a final private-overlay build, run both legacy profile preparers in the external
  preparation checkout. They parse reviewed source with AST only, never import old code,
  and write the Git-ignored env plus `.secrets/eagw-private-key.der` at mode `0600`.
  Reports expose only hashes/presence flags. The model preparer starts lifecycle and
  callback modes as `fake`; only internal live acceptance may switch both to `internal`.
- Source and private-data/config archives have independent manifests and hashes. Never
  package `*.local`, PKL, XLSX, SQLite, credentials, or generated internal results in the
  source archive.
- Keep the three large corpus PKLs and Workbench payload SQLite outside both transfer
  archives. The private overlay owns their frozen manifest/index only. On the target Mac,
  `scripts/soc_pingan_stage_internal_corpus.py` verifies the separately supplied files
  from `$HOME/Downloads` before placing them at canonical paths; a missing or mismatched
  artifact must fail closed before Host DEV starts.
- Live matrices/runners require explicit confirmation, protected local case files, and
  redacted reports. Prove direct provider behavior, MCP/action dispatch, persisted
  evidence, and Review/Lead-Agent readback separately. A direct smoke does not prove the
  full business path.
