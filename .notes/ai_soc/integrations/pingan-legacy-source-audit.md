# PingAn Legacy Source Audit / 平安旧实现审计

> Reviewed: 2026-08-04
> Scope: internal model profile, ZEUS provider boundary, status flow, and EDR safe-path data
> Product boundary: PingAn integration only; generic SOC Runtime remains vendor-neutral

## 1. Result / 结论

旧项目提供了可复用的接口协议和租户经验，但不能把旧控制流原样搬进通用 Runtime：

- 内网模型服务是 OpenAI-compatible endpoint，可直接由 DeerFlow model profile 使用。
- ZEUS 请求签名协议已提取为无旧项目依赖的 PingAn signer；真实凭证写入本地、Git 忽略的配置。
- ZEUS 状态是外部工单生命周期，不等于检测真值；必须经 PingAn source adapter 进入通用 external-disposition ingress。
- EDR safe-path 表是历史模型输出聚合，不是人工确认的权威白名单；只能作为版本化、可审阅的租户调查知识，不能命中即忽略。

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

It has no default App ID/App Key and is covered by a deterministic wire-contract test. `run_workflow` remains an injected internal dependency:

```text
model.agent_platform.util_tools:run_workflow
```

The workflow implementation and its dependencies must exist on the internal Mac. The generic action remains `asset.locate`; neither signer nor workflow module is imported by generic Runtime code.

Reviewed call sites invoke `run_workflow(app_id, workflow_id, query_data)` synchronously and parse a final-node `dict`, JSON string, or `None`. IP/host fallback order is datacenter then terminal; domain uses datacenter and UM uses the user workflow. Internal smoke must still detect any installed-version drift.

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
  -> versioned PingAn software-path candidate SQLite catalog
  -> exact normalized path + optional MD5 match with provenance/freshness
  -> InvestigationEvidence (tenant knowledge, decision_impact=none)
  -> Review/Web/TUI/Lead Agent bounded investigation context
```

Implementation:

- compiler/query: `backend/scripts/soc_pingan_software_path_catalog.py`;
- tenant implementation: `backend/soc_agent/integrations/pingan/software_path_catalog.py`;
- stdio MCP: `backend/soc_agent/integrations/pingan/software_path_mcp_server.py`;
- generic action: `endpoint.software_path.lookup`;
- generated local catalog: `backend/.deer-flow/pingan-context/software-path-catalog.sqlite`.

The first real compilation produced 1,329 unique path entries and 7,656
deduplicated row/bucket observations from all 3,654 rows. The build report records
the source SHA-256, malformed counts, control-zone distribution, legacy buckets,
and source dispositions. The source XLSX and generated catalog remain Git-ignored;
the catalog and report are mode `0600`.

The compiler deliberately does **not** carry over the old fuzzy matching. It
normalizes Windows separators/case and then requires an exact path; an optional
MD5 can strengthen the match or expose a hash mismatch. It separately classifies
path-control context. `D:`, user-writable and temporary paths remain high-attention
even after a historical ignored match; a managed `C:` path can still be a LOLBin.

Required before any future decision impact:

- deduplicate and normalize Windows paths without hiding the original value;
- retain row lineage, occurrence count, source model, dataset version and compile time;
- separate `safe_paths` from `other_paths`; never promote an item because it merely ends in `.exe`;
- define host/application/environment scope and validity/review dates;
- evaluate false-positive and false-negative examples against human labels;
- never use `match => false_positive`, `match => close`, or `match => skip Runtime`.

This is not per-alert memory. One compiled dataset version is queried only when
EDR path evidence warrants it, and its match is returned as bounded investigation
context. Current output is permanently marked `candidate_only=true`,
`automation_eligible=false`, and cannot affect a decision. A future governed
promotion requires a separate contract and evaluation; changing this catalog alone
must never grant decision impact.

## 6. Current Execution Pointer / 当前落点

```text
Code/config ready outside intranet:
  - DeerFlow local model profile
  - self-contained ZEUS signer
  - D12-B no-network preflight
  - direct asset Provider smoke entry

Still requires internal DEV:
  - Agent Platform run_workflow import and dependencies
  - approved asset hit/not-found/fallback/error test values
  - direct ZEUS and MCP mocked=false evidence
  - persisted InvestigationEvidence and UI/TUI/Lead Agent readback
```
