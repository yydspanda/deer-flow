# PingAn Legacy Source Audit / 平安旧实现审计

> Reviewed: 2026-08-04
> Scope: internal model profile, ZEUS provider boundary, status flow, and EDR safe-path data
> Product boundary: PingAn integration only; generic SOC Runtime remains vendor-neutral

## 1. Result / 结论

旧项目提供了可复用的接口协议和租户经验，但不能把旧控制流原样搬进通用 Runtime：

- 内网模型服务是 OpenAI-compatible endpoint，可直接由 DeerFlow model profile 使用。
- ZEUS 请求签名协议已提取为无旧项目依赖的 PingAn signer；真实凭证写入本地、Git 忽略的配置。
- ZEUS 状态是外部工单生命周期，不等于检测真值；必须经 PingAn source adapter 进入通用 external-disposition ingress。
- EDR safe-path 表是历史模型输出聚合，不进入通用 Runtime，也不是通用白名单。MCP 查询仍只提供调查知识；项目负责人另行批准的默认关闭 PingAn 快速策略，可在完整路径覆盖时形成独立 `ignored` 运营判断。

```text
PingAn source/config/knowledge
  -> backend/soc_agent/integrations/pingan/*
  -> generic typed action/event/evidence contract
  -> SOC Core Service / Runtime

Never:
PingAn field/status/path rule -> generic Runtime hardcode
```

## 2. Internal Model Profile / 内网模型配置

审阅 `validation/original_works/raw_program/sec-model/sec-model/util/root_config.py` 和旧 OpenAI bridge 后确认：

| Item | Reviewed contract |
|---|---|
| Legacy environment selector | `env_profile=LOCAL` selects the reviewed local/DEV profile |
| OpenAI-compatible base | `http://localhost:4001/v1/` |
| Required API surface | `/v1/chat/completions`, `/v1/responses`, `/v1/models` |
| Provider model alias | `DeepSeek_V4_Flash` |
| DeerFlow model name | `deepseek-v4-flash` |

Tracked template: `backend/samples/pingan_dev/config.example.yaml`.

Local runnable files: `config.pingan-dev.local` and `.env.soc-dev.local`. They may contain real DEV values, but both must remain Git-ignored. The tracked template keeps only variable references.

## 3. ZEUS Signer and Workflow / 签名与工作流

旧 `util.util_tools:isec_sign` 的 wire contract is:

```text
SHA256(app_id@@timestamp_ms@@nonce@@SHA256@@json_body@@app_key)
```

It also emits the reviewed legacy headers (`App-Sign`, `App-Id`, `App-Timestamp`, `App-Nonce`, `App-Signature-Method`, `APP-key`, route environment, and company code).

直接导入旧 `util_tools.py` 不可移植：该模块在 import 时会加载 pandas、OpenAI、旧 `service.io_models` 和其他业务包。SOC therefore owns an equivalent tenant adapter at:

```text
backend/soc_agent/integrations/pingan/zeus_signing.py:isec_sign
```

It has no default App ID/App Key and is covered by a deterministic wire-contract test. The old `run_workflow` call was also audited down to its HTTP contract and is now implemented by:

```text
backend/soc_agent/integrations/pingan/agent_workflow.py:HttpPingAnAgentWorkflowPort
```

It authenticates at `/appid/auth/login`, creates an asynchronous workflow run,
and polls the bounded result endpoint. The internal Mac no longer needs the old
Agent Platform Python package, Redis token manager or injected `PYTHONPATH`.
The generic action remains `asset.locate`; neither signer nor workflow client is
imported by generic Runtime code.

Reviewed call sites invoke `run_workflow(app_id, workflow_id, query_data)` synchronously and parse a final-node `dict`, JSON string, or `None`. The new client preserves that port contract while owning the HTTP transport. IP/host fallback order is datacenter then terminal; domain uses datacenter and UM uses the user workflow. Internal smoke must still detect wire-response drift.

## 4. ZEUS Alert Status / 告警状态

The reviewed old source defines:

| Code | Source status | Meaning at integration boundary |
|---:|---|---|
| 0 | 已忽略 | terminal external workflow state; reason/trust still required |
| 1 | 待审阅 | open external workflow state |
| 2 | 退回中 | workflow transition |
| 3 | 待确认 | workflow transition |
| 4 | 处理中 | workflow transition |
| 5 | 待复核 | workflow transition |
| 6 | 待关闭 | workflow transition |
| 7 | 子单处理中 | child-case workflow state |
| 8 | 子单已关闭 | child-case workflow state |
| 9 | 已关闭 | terminal external workflow state; verdict is not implied |
| 10 | 编辑 | workflow transition |

The legacy `check_alert_handled_status()` skipped AI analysis whenever `status != 1`. That behavior is deliberately **not migrated**. A status transition can race with ingestion, be generated automatically, or lack a reliable verdict reason.

The PingAn source adapter must preserve:

```text
source_event_id + source_version/sequence
alert/case reference
raw status code + name
reason and tags
actor + actor/source trust
event time
```

It then builds `SocExternalDispositionIngressCommand` and calls the existing authenticated canonical boundary:

```text
POST /api/soc/external-dispositions
```

Intermediate lifecycle states are recorded without changing verdict. `已忽略` and `已关闭` are not mapped to true/false solely from their status code; only a reviewed status/reason/source combination may enter the existing correction and pending-memory-candidate policies. The source adapter must never write repositories directly.

## 5. EDR Safe-Path Workbook / EDR 路径知识

Audited source:

```text
validation/original_works/raw_program/
  Deepseek_Qwen_32B_EDR_Analysis_Ignored_Paths_Sup (1).xlsx
```

Observed shape:

| Measure | Value |
|---|---:|
| Rows | 3,654 |
| Rows marked `忽略` | 3,654 |
| Parsed `path_parser` rows | 3,654 |
| Rows with safe paths | 3,142 |
| Safe-path items / unique | 7,846 / 737 |
| Unique safe paths seen once | 380 |
| Other-path items / unique | 781 / 593 |

Each row contains historical `inference` and model-produced `path_parser`; it does not carry a durable human reviewer, source version, validity interval, environment scope, signer, or superseding record. The old code additionally merged `.exe` entries from `other_paths` into its matching set and used fuzzy/prefix matching. Therefore the workbook is useful, but not authoritative enough to become a direct allowlist.

Implemented boundary:

```text
workbook/source export
  -> offline compiler and quality report
  -> versioned PingAn software-path SQLite catalog
       |- exact normalized path + optional MD5
       `- conservative one-variable families from safe_paths only
  -> read-only MCP / InvestigationEvidence (decision_impact=none)
  -> optional PingAn policy signal (all relevant paths covered)
  -> Tenant Policy Decision = ignored; Runtime truth preserved
```

Implementation:

- compiler/query: `backend/scripts/soc_pingan_software_path_catalog.py`;
- tenant implementation: `backend/soc_agent/integrations/pingan/software_path_catalog.py`;
- fast-policy signal provider: `backend/soc_agent/integrations/pingan/software_path_policy.py`;
- stdio MCP: `backend/soc_agent/integrations/pingan/software_path_mcp_server.py`;
- generic action: `endpoint.software_path.lookup`;
- generated local catalog: `backend/.deer-flow/pingan-context/software-path-catalog.sqlite`.

The 2026-08-11 real compilation produced 1,329 unique path entries, 7,656
deduplicated row/bucket observations, 12 conservative path families and 136 family
members from all 3,654 rows. The build report records
the source SHA-256, malformed counts, control-zone distribution, legacy buckets,
and source dispositions. The source XLSX and generated catalog remain Git-ignored;
the catalog and report are mode `0600`.

The compiler deliberately does **not** carry over the old broad fuzzy matching. It
normalizes Windows separators/case, retains exact paths, and may infer only one
recognized variable directory segment when at least two distinct `safe_paths` and
source alerts support the same fixed prefix/file name. `other_paths` cannot create a
family. Basename-only, prefix, broad version wildcard and deleted-segment matching
remain forbidden. Optional MD5 can strengthen a match or force fast-policy failure.
Path-control context remains separate: `D:`, user-writable and temporary paths are
still high-attention in investigation output.

Governed fast-disposition boundary:

- deduplicate and normalize Windows paths without hiding the original value;
- retain row lineage, occurrence count, source model, dataset version and compile time;
- separate `safe_paths` from `other_paths`; never promote an item because it merely ends in `.exe`;
- keep the feature default-off and require the reviewed PingAn enforced policy;
- extract paths only from the canonical completed EDR run, never raw vendor aliases in generic policy;
- require every relevant process/executable path to match an exact `safe_paths` entry or safe family;
- give exact and family matches equal `ignored` authority after complete coverage;
- fail closed for partial/unknown paths, `other_paths`-only matches, invalid paths, hash conflicts or path-budget overflow;
- preserve `Base -> Memory -> Tenant Policy -> Effective` lineage and never skip Runtime or relabel technical truth.

This is not per-alert memory. The MCP output remains permanently marked
`candidate_only=true`, `automation_eligible=false`, and cannot affect a decision.
Decision authority belongs only to the separate versioned policy signal/provider
contract enabled by `SOC_PINGAN_SOFTWARE_PATH_FAST_POLICY_ENABLED=true`; changing or
querying the catalog alone never grants authority. This deliberately accepts lower
accuracy for an operator-selected high-throughput mode without contaminating the
generic Runtime.

## 6. Current Execution Pointer / 当前落点

```text
Code/config ready outside intranet:
  - DeerFlow local model profile
  - self-contained ZEUS signer
  - self-contained Agent Platform HTTP client
  - macOS arm64 offline Python/uv/dependency bundle
  - D12-B no-network preflight
  - direct asset Provider smoke entry
  - legacy YHSYS PRD profile preparer; secret output is forbidden

Still requires internal DEV:
  - run the prepared YHSYS PRD profile and verify the reviewed wire contract
  - approved asset hit/not-found/fallback/error test values
  - direct ZEUS and MCP mocked=false evidence
  - persisted InvestigationEvidence and UI/TUI/Lead Agent readback
```
