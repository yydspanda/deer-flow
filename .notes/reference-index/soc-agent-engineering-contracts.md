# SOC Agent 工程契约方案

> 目的：为 SOC Agent 后续扩展成 DeerFlow-aligned 多入口、多 Agent、Web UI、后台 ingestion、攻击模拟/防御综合平台时，提前固定代码风格、架构边界、API、通信协议和质量门禁。
>
> 参考来源：DeerFlow `RunManager/run_agent/RunJournal` 生命周期，Hermes ACP `SessionManager` 持久化恢复，Claude Code `buildTool/checkPermissions/PermissionDecision/SendMessageTool` 权限和结构化消息设计。
>
> 文档边界：本文件只规定工程契约；产品方向、阶段优先级和入口取舍以 `.notes/ai_soc/soc-agent-solution.md` 为准。

## 一、核心原则

SOC Agent 不是“LLM 自主系统”，而是“生产级 Runtime + 受控 LLM 节点”。工程契约必须优先保证：

1. **可扩展**：Headless CLI、TUI、Gateway API、Web UI、Channels、Kafka adapter 都调用同一套 core service。
2. **可验证**：所有外部输入、LLM 输出、工具参数都必须 schema 校验 + domain 校验。
3. **可审计**：每次 run、step、tool action、permission decision、memory update 都可追踪。
4. **可恢复**：run 有状态机，失败不能半写入；replay 能比较旧结果和新结果。
5. **可隔离**：SOC、防御工程、威胁狩猎、攻击模拟共享 core，但 memory scope、权限和工具能力隔离。

## 二、代码风格与质量门禁

### 后端 Python

| 项 | 约定 |
|---|---|
| Python | 3.12+ |
| 包管理 | `uv` |
| 格式化/Lint | `ruff format` + `ruff check` |
| 类型 | 所有 core/domain/protocol 层必须有类型标注 |
| 数据模型 | Pydantic v2 用于 API/Kafka/LLM/配置边界；dataclass 可用于纯内部轻量状态 |
| 时间 | 全部使用 timezone-aware UTC，落库 `TIMESTAMPTZ` |
| ID | 外部可见对象用 `run_id` / `alert_id` / `case_id` / `fact_id`；内部事件带 `event_id` |
| 错误 | 不吞异常；业务错误转成结构化 error code；原始异常写 trace/audit |

### 推荐门禁

后端基础门禁：

```bash
uv run ruff format --check .
uv run ruff check .
uv run pytest
```

架构/契约门禁：

```bash
uv run pyright
uv run pytest tests/contracts
uv run pytest tests/architecture
```

Release-level local Alpha 还必须从仓库根目录运行：

```bash
./scripts/soc-alpha-acceptance.sh all
```

该命令的 fixture/mock/local/data-gated 边界以
`.notes/ai_soc/alpha-acceptance-runbook.md` 为准，不能把聚合 `passed` 解释为 production-ready。

架构测试必须覆盖：

- `cli.py`、`tui/`、`daemon/`、Lead Agent bridge 和 app-layer Gateway routers 可以 import `core`。
- `core` 不 import app-layer Gateway router、Typer/TUI 或 concrete Kafka consumer。
- `pipeline` 不直接 import FastAPI/Kafka/Typer。
- `memory` 不能绕过 `soc_facts` 状态机直接注入 prompt。
- `tools` 的执行必须经过 `policy`。

## 三、项目分层

当前目录（新增模块应延续这一布局，不再按旧草案新建平行 `api/cli/ingestion/tools/queue` 业务层）：

```text
soc_agent/
├── contracts/          # 所有跨边界 schema：API/Kafka/Event/LLM/Tool
├── normalizers/        # 外部厂商/flat payload -> canonical contracts
├── pipeline/           # 十步固定 Runtime 的纯处理节点（含 reference_catalog）
├── core/               # Runtime、稳定 service、UoW、validator 和业务编排
├── domain/             # correlation、domain triage 等内部稳定领域逻辑
├── actions/            # action proposal、adapter registry、MCP/HTTP/vendor action adapters
├── memory/             # candidate、confirmed record、bounded retrieval
├── authorization/      # authorized-activity query/matcher
├── governed_context/   # typed operational fact support
├── disposition/        # shadow proposal/evaluation support
├── external_disposition/ # external status/reason mapping support
├── daemon/             # Kafka mapper/worker/consumer/long-running runner
├── llm/ + prompts/     # DeerFlow model port、bounded prompt/parser
├── db/                 # repository + migrations
├── demo/               # 可重复产品演示编排；只调用 core/repository/actions，不写业务决策
├── eval/               # 离线评估与 fixture runner；只做回归验证，不作为生产路径
├── tui/                # DeerFlow-style terminal workbench，只做 presentation/session
├── cli.py              # Headless transport；只调用 public service
├── lead_agent*.py      # DeerFlow profile/chat/context bridge
└── protocols.py        # 稳定 repository/provider/service ports
```

Gateway HTTP routers 位于 `backend/app/gateway/routers/soc_*.py`，属于 DeerFlow app 层；它们不应
复制到 `soc_agent/api/`。当前没有独立 SOC channel transport、通用 SSE event store 或 PostgreSQL
task queue；这些能力若立项，仍须通过 core service/protocol 边界接入。

依赖方向：

```text
api / cli / tui / channels / ingestion
        ↓
      core
        ↓
pipeline / memory / policy / tools / db / queue / normalizers
        ↓
contracts
```

`contracts/` 是最低层，避免 API、Kafka、LLM、Web UI 各写一套字段。

### 模块边界规则

- `contracts/` 只定义跨边界 schema、枚举和错误模型，不 import `core/pipeline/db/api/cli/tui/channels/ingestion`。
- `normalizers/` 是唯一允许接收 loose vendor payload、flat JSON、字段 alias 的层。核心 `AlertInput` 必须保持 canonical 且 strict。
- `core/` 是唯一 orchestration 层。Headless CLI、TUI、API、Web UI、Channels、Kafka adapter 都只能调用 core service，不能直接拼 pipeline。
- `pipeline/` 只做纯业务步骤，不直接 import FastAPI、Kafka、Typer、SQLAlchemy、psycopg、具体 LLM SDK。
- `db/` 只实现 repository，不承载业务决策；SQL row 和 domain/contract model 需要显式转换。
- `demo/` 只编排现有 service/repository/actions 或提供只读演示导航元数据，不直接拼接业务 view、不绕过状态机、不冒充真实集成完成。固定语料演示目标必须在服务端校验 `alert_id -> expected group_id`；缺失或重分组时显式 drift/fail closed。演示清单不得预写 verdict、Candidate、Memory 或动作结果。
- `eval/` 只用于 fixture / replay / regression，不写生产状态，不作为 Web/TUI/Kafka runtime 入口。
- `memory/` 不能绕过事实状态机写 prompt；只能通过 `MemoryStore`/`LessonStore` 协议读写。
- `actions/` 是 action proposal、adapter registry、MCP/HTTP/vendor adapter 的唯一归属目录；根目录不保留 `action_adapters.py`、`action_proposals.py`、`mcp_adapters.py` 兼容入口，新代码必须 import `soc_agent.actions.*`。
- `tools/` 不能直接执行高风险动作；必须经过 `policy`。
- 每个包的 `__init__.py` 只 export 稳定 public API。未 export 的类/函数默认内部实现，跨包不直接调用。

### Raw message parsing / 原始消息解析约束

- Vendor raw-message parsing belongs in the source normalizer/adapter. PingAn formats must remain
  under `soc_agent.normalizers`; core runtime, public skills, and Lead Agent prompts must not contain
  PingAn field aliases.
- Raw input is immutable evidence. Parsing adds `ParsedRawMessageEvidence`; it never replaces or
  deletes `AlertInput.raw` or `AnalysisRun.input_payload`.
- PingAn evidence selection is mutually exclusive, not a weighted merge. If any raw message parses,
  only `raw_message/high` fields may enter canonical mapping, role/scenario facts, conflicts, and LLM
  evidence; Zeus sibling/processed fields remain immutable raw-only audit data. If zero messages
  parse, the first structured event may enter `raw_structured/low` fallback. The current sole trust
  override is exact topic `T_GBD_zeus_data`, whose structured fallback is `high`; source type,
  message absence, similar names, and topic prefixes must not grant that trust.
- `FieldTrust.source_trust` must describe the actual source provenance. A fallback structured field
  must never inherit the selected raw message's high trust merely because both live in the same
  `zeusRawLogs[]` item. Reasoning exclusion is represented separately and must not lower an otherwise
  high-trust source.
- All parseable messages are retained. One message is selected as primary evidence and up to four
  paths are full supplementary evidence; supplementary selection uses deterministic canonical
  observation profiles, never raw source order. The dominant profile and rare profiles receive
  representative slots before repeated copies. Adapter-owned, exact-path high-value values outside that budget may enter bounded
  `BoundedEvidenceHighlight` records. A grouped highlight retains occurrence count and at most five
  representative paths; complete covered paths stay in `EvidenceCoverageReport`. Highlight paths
  count as model-visible coverage, obey the same sensitive-evidence mode, and cannot contain
  structured fallback values.
- Network/HTTP/process observations from different raw messages must keep stable evidence paths.
  Fact reconstruction may report contradiction only among claims in the same observation;
  different requests, sessions, HTTP transactions, proxy hops or process executions must not be
  collapsed into one synthetic conflict.
- For PingAn NIDS, `sip/sport/dip/dport/proto` are the observed wire five-tuple. Nested
  `alert.source/target` are rule-relative sensor endpoints and must remain separately named
  observation fields; adapters must not silently reinterpret them as wire source/destination or
  attacker/victim. `query` is not DNS/domain evidence without an explicit protocol contract.
- For PingAn NDR/APT, each parsed `sip/dip` message is an independent wire observation; HTTP and
  network-content file metadata must retain the exact message evidence path. In the reviewed source
  contract, message-first `sip/dip` are the provider-reported session initiator/responder for that
  observation. This scoped upstream fact does not need duplicate SYN/flow/PCAP confirmation unless the
  current alert explicitly marks direction unknown, proxy/NAT/forwarding changes the relevant leg, or
  same-observation evidence conflicts. It does not assign attacker/victim roles, prove compromise, or
  authorize a response. Generic Runtime must not infer this contract from aliases; every other source
  adapter must opt in explicitly. `ioc` carries vendor rule/detection descriptors and must not be
  promoted to a typed IOC by value shape. `file_name/file_md5` may create `observed_artifact` evidence
  but cannot prove an endpoint write, exploit success, or compromise. Reviewed `rule_name`,
  `rule_desc`, `attack_type`, `host_state`, and `rule_labels` fields are adapter-owned provider detection
  assertions. Their exact values may support classification/effect-stage reasoning only when selected
  high-trust bounded evidence contains the declared path; the adapter still cannot set the Runtime
  verdict.
- For PingAn HIDS, `internal_ip/agent_ip` are endpoint identity and provisional impacted-asset
  evidence, not packet source. `external_ip=1.1.1.1` is a typed non-reasoning placeholder. Process
  trees, users and artifacts stay per-message; an observed ppid is retained as generic
  `parent_process_id` even without a parent name. Only reviewed event contracts (`bounce_shell`,
  `honeypot`, `malic_opera`) may create event-scoped network observations; canonical
  source/destination remain empty. Unknown event types with network-shaped fields must surface a
  mapping issue rather than inherit direction from a known event.
- For PingAn EDR, endpoint identity, security role, and wire direction are separate contracts.
  `str_source_ip`, `device__ip`, and `iplist` identify the observed endpoint/impacted-asset candidate;
  they are not packet sources. `str_attack_ip` may emit only validated non-endpoint vendor
  attacker/peer candidates and typed IOCs, never a canonical destination. "Non-endpoint" is
  evaluated across parsed-message and structured-fallback identities in the same raw-event
  observation scope, not against only the current field dictionary. Polymorphic
  `str_threat_value` and `str_activity_id` remain typed source semantics and cannot become IP/hash
  entities by shape alone. When the source provides no explicit directional connection fields,
  canonical EDR source/destination and network observations must remain empty.
- For PingAn Threat Intel, nested `net.src_ip/dest_ip/src_port/dest_port/proto` describes the
  observed wire session. Provider `attacker` / `victim` values are independent
  `VENDOR_ASSERTION` claims and must not overwrite the session endpoints. The monitored `machine`
  may form an impacted-host candidate; `external_ip` or an explicit, shape-valid IOC may form threat
  indicators. `assets.ip` is an asset-scope expression that may contain CIDR/range syntax and must
  never become a host IP. Provider `result=success`, `is_black_ip`, threat severity/level and scores
  are typed source semantics, not exploit outcome, detection truth, or calibrated Runtime confidence.
- For exact-topic PingAn SIEM structured fallback, high trust means faithful evidence provenance,
  not that an upstream model is correct. `suspicious_email` may project message ID, sender,
  recipients, subject, links and attachment names into the generic email contract; body text and
  upstream model narrative remain bounded evidence. `standard_machine_copy` may project the
  aggregate computer name/IP candidates but cannot create network source/destination or attacker.
  Pipeline identities such as `User=system` are not event actors. Unknown subtypes retain bounded
  evidence and surface high-value mapping gaps instead of receiving guessed entities.
- PingAn NIDS `alert.action`, `alert.attack_res`, and HTTP status must carry typed field semantics.
  Sensor `allowed`, a vendor result code, or HTTP 2xx cannot prove attack/exploit success or set the
  Runtime verdict. NIDS `files[]` describes transaction/file-extraction metadata and must not be
  promoted to endpoint file-write evidence without an explicit source contract and outcome artifact.
- Vendor placeholder/default/non-observation fields must be emitted by the adapter as typed
  `SourceFieldSemantic`. `participates_in_entities=false` and `participates_in_reasoning=false` are
  hard guards: model projection omits the exact path/container descendants with
  `adapter_excluded_from_reasoning`; core Runtime and prompts must not recover the value through a
  different alias. Raw/parsed evidence remains immutable for audit. Core code must not contain the
  vendor's placeholder value.
- Field-importance matching considers only non-empty source leaves. Empty strings, nulls and empty
  containers cannot create false high-value mapping gaps; non-empty unsupported fields still must
  surface an explicit gap.
- Corpus acceptance for multi-message adapters is instance-level. Nested paths are matched by their
  reviewed leaf semantics, and each non-empty value must be typed, model-visible through bounded
  evidence/highlight, or explicitly excluded from reasoning. Path-level aggregate counts alone are
  insufficient because they can hide later-message and nested-field omissions.
- `LLMAnalysisRequest.v5` may include only `BoundedAnalysisEvidence`,
  `EvidenceCompactionReport`, and `BoundedEvidenceHighlight`: per-field and total-size bounded,
  parser/provenance annotated, and separated into primary/supplementary/group/profile/highlight
  content. It must not dump the unbounded vendor payload into the prompt.
- Long encoding-shaped spans are compacted through
  `soc_agent.pipeline.encoded_context.compact_encoded_spans()` only after sensitive-mode projection
  and before leaf-budget selection. This shared model-boundary rule applies to every selected
  primary/supplementary evidence item regardless of vendor, source type, or topic; PingAn topics
  must not opt in separately. The implementation does not decode values or mutate raw/parsed
  evidence. Each marker retains kind, original character count, and a 12-character SHA-256 prefix;
  the typed sidecar retains exact path and the complete SHA-256. Sidecar details stay in request/run
  audit and are omitted from the prompt projection. An exact marker-bearing scalar that is visible
  in bounded evidence may ground only source-field presence, encoding shape and model-boundary
  omission. It cannot ground hidden bytes, token validity/identity/privileges, security outcome, or
  the private complete sidecar hash.
- Production owns the algorithm under `backend/soc_agent/`. Code in that tree must never import
  `validation.*`; validation tools may import production modules to exercise the exact deployed
  behavior. Architecture tests enforce this dependency direction.
- Prompting and post-analysis evidence validation must share the same bounded projection function.
  `AnalysisResult.evidence[].source` must name an approved section, exact projection path, or a
  bounded evidence `source_path#parsed.field.path`; its scalar value must be present under that
  declared bounded source. Every item produces an
  `AnalysisEvidenceGroundingItem`; an ungrounded item adds a deterministic review reason only when it
  is cited by the decision core. Optional item failure remains auditable and blocks only dependent
  capabilities through `AnalysisMaterialityReport.v1`.
- One analyzer evidence item may cite only one exact source path. Composite source strings are
  invalid. Exact bounded `#parsed`, `#decoded`, and `#repaired` paths are separate provenance
  surfaces; descriptions may not introduce facts from uncited sibling paths.
- HTTP status, tool/API success, workflow/ticket state, `is_blocked`, or `is_banned` must retain the
  exact scoped meaning declared by its reviewed source/Adapter contract. Generic Grounding code must
  not infer or reject security outcome semantics from field names or prose keywords; the bounded
  analyzer decides attempt/effect/impact from cited evidence, while Automation Policy remains the
  independent action-authority boundary.
- Parser failure is explicit: preserve raw text, emit a warning, expose only bounded text to the
  analysis node, and keep structured fallback candidates at the trust selected by the source
  policy. PingAn defaults that trust to `low` except for its exact reviewed topic allowlist.
- Every selected raw message must emit `MessageSchemaObservation`. `recognized` means the outer parser
  grammar succeeded, including when a nested allowlisted field has a separately recorded decode/repair
  warning; `degraded` is reserved for an explicitly incomplete outer parse, and `unsupported` means no
  deterministic parser output exists. Nested damage is expressed by the preserved source string,
  `NestedJsonRepairObservation` and warnings; none of these statuses is a verdict or probability.
- `schema_fingerprint` hashes parser name/version plus structural field paths and types, never field
  values. Novelty is evaluated only against an explicitly accepted baseline; a first observation
  cannot call itself novel.
- `soc normalize drift ... --schema-baseline PRIOR_REPORT.json` must flag baseline-missing
  fingerprints in `novel_schema_fingerprint_counts` and include their samples in
  `suspicious_samples`. Novel/degraded/unsupported signals create parser/adapter maintenance work;
  they do not directly change a security verdict.
- Platform `ruleCode` remains the alert-platform detection identity. Sensor-internal `rule_id` or
  equivalent parsed values stay in parsed evidence unless a future typed alias contract explicitly
  maps their namespace.

## 四、模块接口与协议设计

随着代码量增长，SOC Agent 不能靠“大家自觉”维持边界。每个模块必须有明确 public interface、输入输出 schema、失败语义和依赖方向。

### Public API 原则

每个模块只暴露少量稳定入口：

```python
# soc_agent/core/service.py
class SocAnalysisService:
    def analyze(
        self,
        payload: Mapping[str, Any],
        *,
        context: ServiceRequestContext | None = None,
    ) -> AnalysisRun: ...
    def replay(self, run_id: str) -> AnalysisRun: ...


class SocReviewService:
    def correct(self, command: CorrectionCommand) -> AnalysisRun: ...
```

Headless CLI、TUI、API、Web UI、Channels、Kafka adapter 只能调用 `SocAnalysisService` 或同等级 core service；不能直接调用 `pipeline.extract_entities()`、DB repository、LLM adapter 来绕过 runtime。

每次 service 调用都应带 `ServiceRequestContext`，至少包含：

| 字段 | 说明 |
|---|---|
| `request_id` | 本次入口请求 ID |
| `actor` | 发起者：用户、系统、service |
| `actor.surface` | `cli/api/tui/web/channel/daemon/test`；其中 `daemon` 只表示后台系统 actor，不是用户产品入口 |
| `trace_id` | 跨服务/事件追踪 ID |
| `idempotency_key` | 写操作幂等键 |

### Protocol 优先于具体实现

可替换依赖先定义 `Protocol`，再写实现：

```python
class AlertRepository(Protocol):
    def save_run(self, run: AnalysisRun) -> None: ...
    def get_run(self, run_id: str) -> AnalysisRun | None: ...
    def find_recent_similar(self, query: SimilarAlertQuery) -> list[AlertSummary]: ...


class MemoryStore(Protocol):
    def find_confirmed_facts(self, query: MemoryQuery) -> list[MemoryFact]: ...
    def propose_fact(self, candidate: MemoryCandidate) -> None: ...


class LLMAnalyzer(Protocol):
    def analyze(self, request: LLMAnalysisRequest) -> AnalysisResult: ...


class SocEventSink(Protocol):
    def emit(self, event: SocEvent) -> None: ...
```

业务代码依赖协议，不依赖 PostgreSQL、Kafka、具体 LLM SDK、具体 vector DB。这样测试、替换供应商、本地模拟和后续多 Agent 扩展才不会牵一发动全身。

Replay 约束：

- `AnalysisRun` 必须保存 `input_payload` 和 `input_hash`，repository 不能只保存最终 verdict。
- `SocAnalysisService.replay(run_id)` 必须通过 `AlertRepository.get_run()` 取回旧 run 的输入快照，生成新的 run。
- replay 不能覆盖历史 run；新 run 必须记录 `replay_of_run_id`。
- 普通 replay 不允许复制仍处于 `running` 的 run；进程丢失恢复必须走
  `SocAnalysisService.recover(AnalysisRunRecoveryCommand)`，经过 stale window 后先把旧 run 标成
  `interrupted`，再创建带 `replay_of_run_id` 的新 run。SQL repository 必须以 expected `running`
  status 做单赢家条件更新；已认领但未产出 replay 的 interruption 仍受 stale lease 保护。默认 recovery
  idempotency key 由旧 run ID 稳定派生。
- 若旧 run 不存在，service 返回 not-found 语义；若旧 run 没有可 replay 输入，必须 fail-fast，不允许猜测输入。

Correction 约束：

- correction 是人工覆盖当前 operational decision，不删除或覆盖原始 `AnalysisResult`。
- 每次 correction 必须追加 `CorrectionRecord`，记录 previous verdict、corrected verdict、actor、reason、evidence 和时间。
- correction 只能把候选知识标记为 `pending_review`；不能直接生成 confirmed fact、lesson 或自动处置规则。
- correction 后仍保持 `automation_allowed=False`。
- 人工入口只能生成 `confidence_source=human_confirmation`；只有已通过
  `SocExternalDispositionService` trust/mapping/target gate 的内部调用可以使用
  `SocReviewService.correct_external()` 并生成 `external_disposition`。入口不能自行提交 provenance。
- correction confidence 是未校准的 confirmation strength，不是概率。未显式输入时使用
  `soc.correction_policy.v1` 的 categorical default；无论是否显式输入，都必须保留
  `confidence_source`、`confidence_was_explicit`、policy version 和 explanation，且
  `confidence_is_calibrated=false`、`calibrated_probability=null`。

Decision audit 约束：

- `DecisionAuditRecord` 是 analyze/replay/correct 的结构化审计摘要，不替代完整 `AnalysisRun.run_payload`。
- `DecisionAuditRepository.save_audit_record()` 必须在 service 边界调用，入口层不能绕过 service 自己写审计。
- `soc_decision_audit_log` 必须至少记录 `run_id`、`alert_id`、`actor`、`action`、`input_hash`、previous/final verdict、confidence 和可扩展 payload。
- replay/correction 必须生成新的审计记录，不覆盖历史审计记录。
- 审计写入失败必须暴露为执行失败或明确错误，不允许假装成功。
- analyzer decision audit payload 必须记录 `decision_policy_version`、`confidence_source`、
  `confidence_is_calibrated`、`calibrated_probability`、`calibration_profile_version`、
  `evidence_state` 和完整 `review_reasons`；不能只保存一个 raw confidence。

Analysis persistence / 分析持久化约束：

- 持久化分析必须使用支持 `analyze_journaled()` 的 Runtime。在进入 analyzer/provider 前，service 将
  同一个 `AnalysisRun` 以 `status=running` 写入 `soc_analysis_runs`，并附加
  `AnalysisRequestJournal(soc.analysis_request_journal.v1)`。
- request journal 只保存 request hash/schema、source/detection 元数据、model/prompt/step、证据计数、
  selected skill、request/trace/actor 和哈希后的 idempotency key；不得保存渲染后的 prompt、证据值、
  provider header/response、credential 或 token。原始 source replay snapshot 仍按既有治理边界保存在
  `AnalysisRun.input_payload`，两者不能混为一类数据。
- final bundle 把 journal 原子更新为 `completed` 或 `failed`；provider timeout 必须保存 typed retryable
  failure。若进程在 provider 中消失，或 final bundle 回滚，pre-call row 保持 `running` 可发现，由
  `soc recover RUN_ID --reason ...` 在 stale window 后转为 `interrupted` 并 replay。
- 一次 analyze/replay 的主业务结果必须通过 `AnalysisPersistence.save_analysis_bundle()` 原子写入
  `AnalysisRun`、`AlertSummary`、可选 `ReviewQueueItem` 和 `DecisionAuditRecord`；生产 SQL repository
  不得在 service 中逐表 commit 后假装为完整成功。
- 任一 bundle row 写入失败必须回滚全部四类写入。Normalization maintenance 是成功主写入后的
  fail-open side path，可以单独更新 run 的 monitoring result，但不能破坏已提交业务事务。
- `AnalysisRun.status=failed` 必须带 `RuntimeFailure`，至少包含 failed step、稳定 kind、retryable、
  sanitized error type/message。Provider 原始响应、header、secret 和未裁剪异常不得写入 run/audit。
- 不可重试失败进入 summary + ReviewQueue + audit；可重试失败保留 failed run/summary/audit，但不立即
  创建人工工单，Kafka 不 commit offset，并允许同一 idempotency key 重新执行。

Mutation unit of work and audit / 业务变更事务与审计约束：

- `AnalysisPersistence` 只拥有 analyze/replay 主 bundle；correction、review close/note、memory review、
  approval request/resolve/dry-run/execute 和 external disposition 使用独立
  `SocMutationUnitOfWork.mutation_transaction()`，一个 service command 对应一个数据库事务。
- service 是事务、RBAC、幂等和审计 owner。CLI/API/TUI/Kafka/adapter 入口不能开启局部事务、直接写表，
  或自行伪造 mutation audit。
- SQL transaction repository 的内部 `commit()` 只能 flush；外层 context manager 统一 commit。任一写入、
  decision audit 或 mutation audit 失败必须回滚本命令的全部状态。
- `SocMutationAuditRecord` 是所有 Alpha L3 mutation 的追加式命令审计，至少记录 operation、target、
  actor、`auth_source`、request、reason、idempotency key、command hash、result status/ref 和有界 payload。
  migration `0018_mutation_audit` 将其保存到 `soc_mutation_audit_log`。
- `DecisionAuditRecord` 负责 verdict/policy/evidence lineage，`SocMutationAuditRecord` 负责通用命令
  actor/idempotency/result lineage；两者不可相互替代。一次 correction/external command 可以在同一事务
  同时写两者。
- exact retry 的身份是 `(operation, idempotency_key)`，并校验 target 与稳定 command hash；完全相同的
  retry 返回已有逻辑结果，复用 key 提交不同内容必须 conflict。
- 原始 action payload、alert payload、HTTP headers、token、cookie、password、credential 和 provider
  response 不得进入 mutation audit。只保存显式 allowlist 的有界投影；reason 和嵌套字符串必须脱敏限长。
- `SocEvent` 是进程内通知，不是事务事实。服务必须先缓冲事件，数据库 commit 成功后再 flush；rollback
  时不得发出“已成功”的事件。
- 每个新增 multi-write mutation 必须增加逐写入 fault injection：在第 1..N 次写入后失败，断言所有业务
  表、两类 audit 与事件均保持命令前状态；另测 exact retry 只产生一个逻辑结果。

Alert summary 约束：

- `AlertSummary` 是面向告警列表、review queue、dedup、correlation 和 Web/TUI 查询的读模型，不替代完整 `AnalysisRun`。
- `AlertSummaryRepository.save_alert_summary()` 必须在 service 边界调用；CLI/API/TUI/daemon 入口不能自己拼 summary。
- `soc_alert_summaries` 保存扁平索引字段和完整 `summary_payload`，字段应优先服务高频查询：`alert_id`、`tenant_id`、`source_type`、`detection_key`、`rule_code`、`verdict`、`needs_review`、`updated_at`。
- correction 后必须更新同一个 run 的 summary，让 operational verdict 和 review 列表保持一致；原始 AI verdict 仍保留在 `AnalysisRun.analysis` 和 `soc_analysis_runs.run_payload`。
- replay 必须生成新的 summary，记录 `replay_of_run_id`，不能覆盖原 run summary。
- 方案文档中泛称的 `alert_summaries` 在当前实现里使用 SOC 前缀表名 `soc_alert_summaries`。
- Runtime analyzer decision 的 `review_reasons` 必须从 `Decision` 复制到 `AlertSummary` 和
  `ReviewQueueItem`；`reason` 可用于列表主原因，但不能丢弃其余结构化 guard。

Correlation service 约束：

- `SocCorrelationService` 是相似告警、历史关联和可复用证据的只读业务入口；CLI/API/TUI/Web/Lead Agent 都不能绕过 service 直接拼 correlation result。
- `CorrelationQuery` / `CorrelationResult` / `CorrelationMatch` 是 source handler、security scenario recognizer 和 unified investigation report 的稳定输入；不得让每个 EDR/APT/HIDS/WAF/F5 handler 或反弹 shell/webshell/横向移动识别器自己发明相似告警结构。
- MVP correlation 只能依赖 `AlertSummaryRepository` 和 `InvestigationEvidenceRepository`，不调用 LLM、不调用 MCP、不执行 action、不修改 run/summary/review/memory。
- correlation match 必须携带结构化 `matched_reasons`，当前稳定前缀为 `detection_key:`、
  `rule_code:`、`source_type:`、`category:`、`entity_key:`；不能只给自然语言解释。
- correlation 结果可以进入 `InvestigationContext`、Lead Agent bounded artifact、Web/TUI 展示和后续 domain triage request，但不能自动改 `AnalysisRun.decision`、不能自动关闭 review queue、不能直接生成 confirmed memory。
- Correlation bridge 必须把完整 typed `CorrelationResult` 放入 `UnifiedInvestigationReport.correlation_result`
  和 `SocDomainTriageRequest.correlation_result`；`similar_alert_count` / `correlation_match_count` 只允许作为
  展示 projection，不能替代结构化结果或伪造历史证据。
- `SocMainOrchestratorService` 不直接读写 repository。`SocAnalysisService` 与
  `SocCorrelationService` 必须共享同一个 `AlertSummaryRepository`；本地/eval 可用
  `InMemoryAlertSummaryRepository`；生产 wiring 必须注入共享 PostgreSQL repository 的完整 service pair，
  不能只传 repository 形成 summary-only 非原子持久化。
- 相似评分只能由 `soc_agent.domain.correlation.score_similar_alert()` 维护，SQL 和内存实现不得复制两套
  scoring semantics。历史 reusable evidence 必须按 matched historical `run_id` 精确加载；当前 repository
  多引用过滤为 union 语义，不能同时传复用的 `alert_id`，以免当前 run evidence 泄漏进历史 match。
- 每版 scorer 必须有显式 `CORRELATION_SCORING_POLICY_VERSION`，并进入 `CorrelationResult` 和离线
  eval report。修改权重、reason、候选召回或排序语义时必须升级 policy version，并用旧/新报告 replay diff；
  不能只改代码而让旧指标继续冒充当前规则。
- Correlation eval 标签固定分三类：`same_incident`、`related_distinct`、`unrelated`。检索任务把前两类
  视为 relevant；duplicate identity 任务只把 `same_incident` 视为 positive。严禁把
  `related_distinct` 计成检索 false positive，或因为高 score 把它静默合并。
- `soc eval correlation [FIXTURE] [--baseline-json PRIOR]` 必须保持只读，输出两套 confusion matrix、
  precision/recall/F1、reason prefix 分布、candidate fan-out、pair snapshot diff、
  `evidence_lineage_leakage_count` 和 `unrelated_evidence_exposure_count`。前者是跨 run 引用 bug，后者是
  检索噪声，两者不能合并统计。
- eval fixture 必须是 vendor-neutral、版本化、带人工 rationale 的受控标签集。默认 query/candidate limit
  必须覆盖每个 case 的全部标签候选，避免 top-k 截断被误解为 score=0；更大真实样本应按 cohort 另行扩展。
- 离线 dedup threshold 只用于测量 identity precision/recall；报告固定
  `shadow_dedup_allowed=false`、`decision_impact=none`。当前没有自动 suppression、merge、close queue 或
  memory confirmation 的授权路径。
- Correlation 是否命中只能改变调查上下文、finding evidence profile 和人工复核提示；不得直接提高
  Runtime detection confidence、做 dedup/suppression、关闭 queue、确认 memory 或执行 response。
- 后续若引入 LLM rerank，只能作为 bounded rerank node 消费候选 `CorrelationMatch`，输出仍必须经过 schema/domain validation；LLM 不得直接发起 DB 查询或扩大检索范围。

Domain triage 约束：

- `SocDomainTriageRequest` / `SocDomainTriageResult` / `SocDomainFinding` 是 source handler 和通用安全场景识别的稳定输出协议；不得让每个 handler 自己返回自由 JSON。APT/EDR/HIDS/WAF/F5 是输入来源或来源视角，反弹 shell、webshell、横向移动、命令执行、恶意外联、提权、凭证滥用等是可跨来源识别的安全场景。
- Domain handler 只能消费 `AnalysisRun`、bounded `SocSkillContext`、`InvestigationEvidence` refs、capability card refs 和后续 correlation refs；不能直接读 DB、不能调用 MCP/tool、不能写 review queue、不能写 confirmed memory、不能修改 `AnalysisRun.decision`。
- `SocDomainTriageService` 是 PA-10 domain handler 路由入口；entry adapter、TUI/Web、eval 和后续 Main Orchestrator 不能绕过 service 直接调用某个 handler。
- PA-10 handler 输出只允许包含 finding、scenario hints、evidence profile、current conclusion、evidence refs、capability card refs、recommendations、limitations、human checklist 和 metadata；任何处置动作必须转成 action proposal 并回到 policy/approval boundary。
- 每个 `SocDomainFinding` 必须给出当前结论：`current_conclusion.summary`、risk/certainty、recommended action/queue、`automation_allowed=false`。证据不足不能输出“无法判断然后停止”；必须给出当前偏向判断、证据缺口和人工核查清单。
- Evidence Fusion First 是 domain/scenario triage 默认策略：raw/canonical alert、历史相似预警、外部处置反馈、confirmed memory、read-only evidence 和可用 tool evidence 都是常规输入；工具证据缺失只能降低 certainty 并进入 `evidence_profile.gaps`，不得阻塞 finding 输出。
- deterministic scenario taxonomy 必须通过 `soc_agent.domain.scenarios` 暴露稳定 `SCENARIO_TAXONOMY_VERSION`、`scenario_taxonomy_keys()` 和 `scenario_taxonomy_snapshot()`；eval/replay diff 只能消费这些稳定快照，不能读取 `_SCENARIO_RULES` 私有结构。
- 如果厂商/上游提供了场景提示但内部 deterministic scenario taxonomy 未命中，domain triage 必须输出 `scenario_key=vendor.unmapped` 的低/中置信候选 finding，保留 `vendor_scenarios`、证据缺口和人工核查清单；它不能替代内部已识别场景，不能改 verdict，也不能直接写 confirmed memory。
- Domain finding 是分析证据，不是 operational verdict。它可以进入 unified investigation report、ReviewQueue/Lead Agent bounded context 和 pending memory candidate source，但不能自动关闭工单或自动确认 memory。分析师对 domain/scenario finding 的反馈只能经 `SocMemoryCandidateSourceBridge` 生成 `pending_review` candidate；feedback 可以进入 candidate content/facets/metadata，但仍不得绕过 `SocMemoryService.review_candidate()`。
- Domain/scenario finding 不得实现第二套 Review Policy。`run.decision.needs_review=false` 时必须使用
  `recommended_action=continue_policy_evaluation` 且不填 `recommended_queue`；只有 Base Decision 已有
  hard guard，或无 Decision 且 run 为 failed/interrupted/needs_review 时，finding 才能指向现有人工队列。
  taxonomy 未映射、可选 Provider 未配置或普通 evidence gap 本身不能创建复核。
- `soc eval scenarios PATH` 是 vendor-neutral deterministic scenario eval 入口；它直接消费 alert JSON 文件/目录，输出 taxonomy version、taxonomy keys、covered keys、missing keys、`vendor.unmapped` 计数和 per-sample findings。`--baseline-json` 只生成 replay diff 报告，不自动失败、不写业务库、不生成 memory。
- domain/scenario eval report 必须输出 taxonomy version、taxonomy keys、covered keys、missing keys 和 `vendor.unmapped` 计数，作为 replay diff 基线；eval 仍只读样本，不写业务库、不生成 confirmed memory。

Main orchestrator 约束：

- `SocMainOrchestratorService` 是 PA-11 unified investigation report 的 core service 入口；CLI/API/TUI/Web/Lead Agent 后续展示统一报告时不得绕过它自己拼 analyze/action/domain/review 链路。
- `SocMainOrchestratorRequest` / `UnifiedInvestigationReport` / `SocOrchestratorRouteStep` / `SocOrchestratorReviewContextSummary` 是主控报告的稳定 contract；前端和 eval 只能消费这些结构，不能消费 handler 内部私有对象。
- Main orchestrator 只能调用已有 core service、router、policy/dispatcher、adapter registry 和 domain triage service；不能直接读写 repository、不能直接调用 MCP/tool、不能直接执行高风险动作、不能确认 memory。
- PA-11 report 中的 read-only action result 必须先写 `InvestigationEvidence`，再通过 evidence refs 进入 domain finding 和 review context；不能让 route step payload 直接改变 verdict。
- report metadata 必须显式标记 `handler_output_only`、`writes_db`、`executes_high_risk_actions` 等边界语义；eval 必须验证这些字段，防止 demo 链路被误当生产处置链路。
- `PA-12` 真实 PingAn MCP/API 替换只能替换 action adapter/provider/config，不能改变 Main Orchestrator contract；DEV profile、portable signer、preflight/direct smoke code ready 只表示 `In Progress`，在内网保存 `mocked=false` direct/MCP/persisted-evidence 证据前不得标记 Done，也不允许用本地 mock 冒充完成。

PI-01D automatic enrichment 约束：

- `SocEnrichmentPlanner` 是 application/core service port，不是固定 Runtime node。Normalizer、entity extractor、fact reconstruction、LLM analyzer 和 `SocDecisionPolicy` 都不得调用 Provider/MCP。
- `SocEnrichmentPolicy(soc.enrichment_policy.v1)` 是 tenant-owned explicit allowlist。默认 `enabled_routes=[]`；v1 只接受 `asset.lookup`、`asset.locate`、`threat_intel.ip_reputation.lookup`、`security_tag.lookup`，并要求 `asset.lookup` / `asset.locate` 最多选择一个。
- `SocEnrichmentPlan(soc.enrichment_plan.v1)` 必须保存 policy version、run/alert/tenant/thread lineage、稳定 input hash/plan ID、planned action、structured skip 和硬边界 `decision_immutable=true`、`high_risk_actions_allowed=false`。
- v1 planner 只能读取 provenance-backed `EntityMention`、`RoleResolution`、completed run status 和显式 tenant policy。不得读取 raw vendor alias、自然语言 recommendation、模型自由文本 evidence gap 或从 scenario name 猜 tool/payload。
- 若未来使用 scenario/gap 触发 route，必须先新增版本化 typed trigger contract、allowlist 和离线 replay；不能直接解析 LLM 文本。
- Planner 只生成计划，不调用 Provider、不写 repository、不修改 `AnalysisRun`、不选择 response target。invalid entity、tenant mismatch、缺 network scope、内部/特殊 IP、无候选和预算耗尽必须成为结构化 skip，而不是异常或静默丢弃。
- TI 自动查询默认要求 tenant internal CIDR 配置；已配置内部地址和语言运行库判定的 non-global/special 地址不得发送给 reputation Provider。关闭该门槛必须是显式、版本化 tenant policy 决策。
- 每个 route 有 entity kind/role allowlist、per-route budget 和 total budget；计划 action 按规范化实体去重。相同 run + policy 的 plan/action identity 不得因 Web/TUI/Kafka `thread_id` 不同而改变。
- 角色冲突不阻止无副作用查询，但必须保留在 rationale 中；任何 planned action 都不得据此选择封禁、隔离、抑制或其他 response target。
- Main Orchestrator 合并 explicit 和 planned action 时，完全相同的 explicit action 优先；所有 action 仍必须经过 exact Capability Router、Action Policy、Dispatcher 和 Adapter Registry。Planner 不得拿到 MCP client 或 Provider object。
- Action result 只通过 injected `InvestigationEvidenceRepository` 保存；基础 run model dump 必须前后相同。测试中的 in-memory adapter/repository 只证明 contract，不证明真实 Provider 或生产 persistence。
- `SocEnrichmentCompositionConfig(soc.enrichment_composition.v1)` 默认 `enabled=false`。启用时必须指定 tenant、`required_result_mode`、至少一个 policy route，以及与 enabled routes 完全相等且 route 唯一的 `SocEnrichmentAdapterBinding`；不得靠目录扫描、模糊匹配或 MCP inventory 自动启用工具。
- Composition binding 必须锁定 exact `route/action/adapter_id/adapter_kind`。`SocActionAdapterRegistryPort` 必须暴露只读 `list_descriptors()`；startup validation 不得调用 Adapter、Provider 或 MCP tool。
- 启动校验必须要求 Adapter `risk_level=read_only`、`external_side_effect=read`、`execute_supported=true`，并确认其 `required_payload_fields` / `required_context_refs` 都是 Planner + Orchestrator 对该 route 保证提供的字段。任一漂移都必须 fail closed。
- Adapter descriptor 的 `metadata.result_provenance_contract` 只允许 `mock_only`、`runtime_declared`、`real_only`。real composition 拒绝 `mock_only`，mock composition 拒绝 `real_only`；`runtime_declared` 还必须声明 `metadata.result_mode_field=mocked`，MCP descriptor 的 allowlisted `output_fields` 必须实际保留该字段。通过该静态校验不等于真实调用成功，持久化 workflow 必须逐次检查实际 result mode。
- 启用 enrichment 的 composition root 必须显式注入 `InvestigationEvidenceRepository`，不能静默退回 process-local repository。In-memory repository 只用于测试；Kafka/internal batch 必须复用持久化 SOC repository。
- `PI-01D1/D2/D3/D4` 已完成 contract/planner、可选 Main Orchestrator bridge、显式配置、policy-route/registry startup validation、asset route consolidation、durable investigation workflow 和 recomputable read-only reporting。`SocEnrichmentExecution` 必须绑定 existing run、immutable plan、composition hash、trigger、result mode、request/trace/actor 和 stable idempotency key；`SocEnrichmentActionAttempt` 必须绑定 execution/action/attempt number、exact adapter、result/evidence hash和 retry state。SQL 状态由 migration `0019_enrichment_executions` 持久化并使用 optimistic CAS。
- 同一个 Kafka topic/partition/offset 或 batch source/payload identity 重试时必须找到同一 execution。`completed|no_actions|blocked|failed` 不得隐式重新调用 Provider；`retryable_failed` 只重试尚未成功的 action。非重试失败只能通过显式 linked replay 重新执行，replay 必须有新 idempotency key、reason 和 `replay_of_execution_id`。
- Provider 调用后、attempt finalize 前发生进程丢失时，stale recovery 必须先查确定性 evidence ID；已有 evidence 就完成旧 attempt，不得重复外部查询。没有 evidence 才记录 interrupted 并在 retry budget 内新建下一 attempt。stale window 必须大于受控 Provider timeout。
- `runtime_declared` Adapter 的实际 result 必须在 evidence 写入前暴露 boolean `mocked`；与 composition `required_result_mode` 不一致时进入 non-retryable contract failure，不能保存 evidence。`success` 与 route-specific normal `not_found` 都可保存只读 evidence；Provider failure、denied、contract failure 和 interrupted 不得伪装为 miss。
- Kafka daemon 和 PKL batch 只能通过显式 composition + 一个或多个 action-adapter config + 一个显式 MCP extensions config opt in；配置省略时固定 Runtime 路径必须保持可独立运行。batch 调查模式必须要求 persistence、显式 Provider confirmation，并把 composition/action/extensions-config SHA-256 写入 manifest/resume guard。基础分析成功而调查失败时仍须保留完整 `AnalysisRun` artifact。
- 任何依赖内网的已确认 Provider/源系统必须先通过外网 simulation package：复用相同 production Provider/MCP/action 代码，只替换为显式 fake transport，并覆盖配置、成功/查无/失败、持久化、回放、报告与零副作用。`external_simulation` 只能接受 `mocked=true`，`internal_real` 只能接受 `mocked=false`；仿真通过不得关闭真实 gate。尚无稳定 wire/source contract 的能力继续 data-gated，不得发明 mock Provider。
- live investigation batch 必须在首个 LLM 调用前通过实际 MCP `list_tools()` 精确发现每个启用 action config 的 `(server, tool)`；仅解析 extensions/action 文件不构成 runtime 可用性证明。缺 command/env、server 或 tool 时 fail fast，且 `--plan-only` 仍保持无 MCP discovery/Provider 调用。
- `run_pingan_internal_shadow.py` 是 PI-01E validation orchestration，不是生产 Runtime 或第二套业务服务。默认只能运行两组静态 plan；live 必须同时要求 `--execute --confirm-live --confirm-investigation`，并固定按 environment preflight -> MCP inventory -> isolated SQLite migration -> Runtime-only batch -> persisted investigation batch -> paired evaluator 顺序执行。任一步非零立即停止，gate failed 不得写 completed；orchestration report 只记录命令边界、状态和 gate 摘要，不采集环境值或替代已有真值表。
- internal batch 对已持久化 failed `AnalysisRun` 的显式 `--resume` 必须调用公共 `SocAnalysisService.replay()` 创建 linked replay，并记录旧 run lineage；不得复用原 idempotency key 返回同一失败 run，不得覆盖失败审计。已完成 investigation execution 仍按 durable identity 复用，不得重复 Provider 调用或 evidence 写入。
- 离线批次可用显式 `--default-tenant-id` 补充源导出缺失的可信 ingress tenant；若源 payload 已声明不同 tenant 必须 fail closed。Vendor Adapter 必须把该 generic tenant metadata 传入 canonical `AlertInput` 与 `LLMAnalysisRequest`，不能只留在 raw payload。
- 持久化 workflow 必须返回准确的 execution/attempt/evidence/provider-invocation metadata，不能沿用 PA-11 demo 固定的 `writes_db=false`。`soc investigation get|report|replay` 是当前 operator surface；三者都复用 service/repository，report 只读重建，CLI 不得直接调用 Provider。

Unified investigation view 约束：

- `UnifiedInvestigationView` / `InvestigationTimelineItem` 是 ReviewQueue 打开单个工单时的只读分析师视图 contract，不是新的 source of truth。
- `SocReviewService.get_investigation_context()` 是生成 `InvestigationContext.correlation_result`、`InvestigationContext.domain_triage_results` 和 `InvestigationContext.investigation_view` 的唯一 service 边界；API/Web/TUI/Lead Agent 不能绕过 service 自己拼等价结构。
- `UnifiedInvestigationView` 只能消费已有 read model 和只读 handler output：`AnalysisRun`、`AlertSummary`、`DecisionAuditRecord`、`CorrelationResult`、`SocDomainTriageResult`、`InvestigationEvidence`、`SocInvestigationAddendum`、`SocExternalDispositionRecord`、`SocMemoryCandidate`、`SocMemoryRetrievalResult`。
- `evidence_timeline` 只是投影；不能替代 `soc_analysis_runs`、`soc_decision_audit_log`、`soc_investigation_evidence`、`soc_external_dispositions`、`soc_memory_candidates` 或 `soc_memory_records`。
- 生成 unified view 不能写 DB、不能执行 action、不能发起 MCP/tool、不能确认 memory、不能修改 `AnalysisRun.decision` 或 `ReviewQueueItem.status`。
- Web/TUI/Lead Agent bounded artifact 可以展示 unified view 的计数、Top 关联、domain finding 和 timeline；任何 close/correct/approve/memory review 仍必须调用对应 core service。

PingAn SOC capability onboarding 约束：

- 平安 SOC 工具、MCP、skill、研判经验和处置经验进入项目之前，必须先整理成 capability card；来源、适用场景、输入字段、输出结构、风险等级、失败模式和脱敏验收样例必须明确。
- capability card 只能分类落到以下稳定 artifact：domain skill、normalizer/field trust rule、read-only action adapter、high-risk action adapter、domain handler、eval fixture、memory candidate、authorized-activity fact proposal。不得直接把一段经验文本粘进生产 prompt 后生效。
- 内部系统 endpoint、账号、token、cookie、真实敏感样本不得写入仓库；真实连接只能通过本地 config、environment secret 或部署 secret 注入。
- read-only 工具经验必须通过 `SocActionAdapterRegistry` / MCP-backed adapter 落地，结果写 `InvestigationEvidence`；不能让 Lead Agent 直接用自然语言调用内部系统。
- 处置类经验必须走独立 authorization / dry-run / execute boundary：默认使用 approval request / grant；只有经过评审的服务端 playbook policy 才可在精确范围内自动授权。未经过 staging smoke 和 adapter-level audit 前，生产 execute 只能保持 no external side effect。
- 经验记忆必须先进入 `pending_review` 或 eval fixture；只有人工确认、版本化和可回滚后才允许作为 confirmed memory 或 active lesson 影响后续判断。
- `.notes/ai_soc/capabilities/pingan/source-docs/` 中的历史 prompt 原文必须先按 `.notes/ai_soc/capabilities/pingan/knowledge-decomposition.md` 拆解；不得整体复制进 Lead Agent prompt、analysis node prompt 或 public skill。
- `skills/public/soc-*` 只能包含跨客户通用研判方法；平安内部域名、部门、账号、BU/PA code、路径、白名单、具体 `rule_code`、模板 ID、策略 ID、operateType 等必须进入 tenant memory、adapter mapping、policy/config 或 eval fixture。
- 已评审且稳定的租户网络、应用、平台、命名约定和首见行为 Playbook 可进入 versioned `TenantKnowledgeProfile`，仅按 canonical typed selector 投影为 bounded `C-*`。主机前缀、进程名、direct-parent、路径前缀、命令行 term、单个 file observation、账号 full-match pattern 和 URI 前缀不得通过 raw evidence 或 rule text 全文包含匹配；同一 selector group 为 OR，不同非空 group 为 AND。进程组合必须在一个 canonical observation 内成立，或仅在相同 `event_scope_id` 内沿“相同规范化进程名 + 相同非空 PID”的连通片段合并，禁止跨独立日志拼接；文件 relation/name/path prefix/path suffix 必须在同一个 canonical file observation 上成立，禁止从全局路径集合拼接；只读命令与变更命令存在语义边界时必须使用 normalized exact-command gate，不能使用子串。行为 Playbook 必须采用多组 typed signal，写明当前证据要求和失效条件，禁止只靠 rule name、单个宽泛文本或租户身份直接输出结论。每个投影必须保留 profile/fact/version/source/review/hash 且 `decision_authority=none`，应用、产品或 Playbook 命中不能直接形成 Memory Decision、operational ignore/transfer 或 action authority。
- 平安环境知识进入 memory 时必须带 tenant scope、source doc/section、status、validity 和 evidence refs；默认 `pending_review`，不能直接 confirmed。扫描、渗透测试、运维窗口、自动化服务等“当前是否被授权”的动态事实不得用 memory 代替，必须进入 authorized-activity fact lifecycle。
- 平安字段名和字段别名只能出现在 adapter/normalizer/mapping tests 或脱敏 fixture 中；core contract、public skill 和 Lead Agent prompt 必须消费 canonical fields。
- 平安处置经验如果需要外部事实查询，必须先建 read-only MCP/action adapter；如果会改变外部状态，必须是 high-risk/analyst-write action，并经过人工 approval 或明确的服务端 playbook authorization。

External disposition sync 约束：

- 外部预警/工单/处置系统的状态和理由同步必须走 vendor-neutral `SocExternalDispositionEvent`，Zeus 只是第一个 adapter；core service 不能出现 Zeus 专属分支。
- `SocExternalDispositionEvent` schema version 固定为 `soc.external_disposition.v1`，至少包含 `external_system`、`external_case_id`、`external_status`、`updated_at`、`raw_payload_hash`，可选包含 `tenant_id`、`source_event_id`、`source_version`、`external_alert_ref`、`soc_alert_id`、`soc_run_id`、`soc_queue_id`、`external_reason`、`external_tags`、`operator`；多租户部署时 `tenant_id` 必须由认证上下文或 adapter 配置补齐。
- 外部系统 adapter 只负责认证、解码、字段映射、redaction、幂等键生成和调用 `SocExternalDispositionService`；adapter 不得直接写 repository、不得直接调用 `SocReviewService.correct()`、不得直接写 memory 或 skill。
- `SocExternalDispositionService` 是外部反馈写入本地 audit、review/correction 和 external disposition record 的唯一 source service 边界。它只有在 server-owned mapping/classifier 已生成完整 typed Skill feedback 时，才允许组合 `SocSkillImprovementService`；原始 reason 不得直接聚类或创建 Skill candidate。
- 当前 External Disposition Contract MVP 已实现 contract/mapper/service/repository 边界；`SocExternalDispositionService.apply_event()` 对 high-trust、mapped、唯一定位且可映射 verdict 的事件复用 `SocReviewService.correct()` 同步 operational correction / review close。该 correction 会反馈给本次 run 实际使用的 confirmed Memory，但不会为每个外部工单事件创建一条 Memory candidate；需要形成新经验时，必须走显式人工 promotion 或 repeated-pattern quality gate。
- `soc_external_dispositions` 是当前 `SocExternalDispositionRecord` 的 SOC business store 表；`SqlAlchemyAlertRepository` 实现 `SocExternalDispositionRepository` 方法。生产和本地持久化都必须通过 migration `0009_external_dispositions` 或 `create_soc_tables()` 创建该表。
- 外部处置历史必须通过 `SocReviewService.get_investigation_context()` 聚合到 `InvestigationContext.external_dispositions`；ReviewQueue API/TUI/Web/Lead Agent bounded context 只能消费该字段，不能直接查 `soc_external_dispositions`。
- 幂等键固定形态为 `external_disposition:{tenant_id|default}:{external_system}:{external_case_id}:{source_event_id|source_version|updated_at_hash}`；重复 webhook、Kafka offset 回放或 polling 重扫不能重复关闭 review queue、重复改判或重复生成 Memory feedback。
- 目标定位顺序必须是明确本地引用优先：`soc_queue_id` -> `soc_run_id` -> `soc_alert_id` -> 已绑定 `external_system + external_case_id` -> 弱关联；弱关联不能唯一命中时只能保存 unmatched record，不得自动改判。
- 外部状态必须通过可配置 mapping 转换为 canonical status，例如 `closed_true_positive`、`closed_false_positive`、`closed_benign_true_positive`、`suppressed`、`escalated`、`ignored`、`duplicate`、`unknown`；未映射状态只能进入 `unknown/unmatched`，不能自动更新 operational decision。
- 外部 free-text reason 默认只是 case feedback；它可作为已使用 Memory 的 typed outcome feedback，或经 server-owned classifier 形成 `SkillImprovementCandidate(status=pending_review)`，但不得自动新建 Memory candidate、成为 confirmed memory/active lesson/active skill 或修改 prompt。
- 外部处置同步必须记录 source surface、operator、mapping version、apply status、target refs、idempotency key 和 audit event，支持 replay diff、撤销和客户审计。
- Webhook、Kafka、polling 和 manual import 都是 transport adapter；进入 core service 前必须归一成同一 `SocExternalDispositionEvent`，不能为每种 transport 复制业务状态机。

PI-03C Skill improvement governance 约束：

- `SkillFeedbackObservation` 必须明确 tenant、`simulation|desensitized_real`、source ID、精确 Skill
  package/guidance hash、scenario、typed failure facet、代表样本和 replay refs。LLM/free-text 不能决定两个
  case 是否属于同一 cohort。
- 聚合键必须包含完整版本化 policy 参数；只有同一键下达到 distinct-source threshold 才创建
  `SkillImprovementCandidate(status=pending_review)`。重复 webhook/重试和同一 source ID 不得重复计数。
- simulation 只能使用 `simulation_fixture`，真实数据不能使用该 source lane；两类 observation/candidate
  不得聚合或 supersede。simulation 永远 `mocked=true` 且不能产生真实质量声明。
- `SocSkillImprovementService` 是 ingest/list/get/review/replay 的唯一业务边界；repository 只保存 typed
  observation/candidate。candidate review 需要可信身份、`soc_skill_reviewer|soc_engineer|soc_admin`、
  idempotency、expected version 和 mutation audit。
- `approve_for_change` 只允许进入人工修改与评测流程。所有 observation/candidate 固定禁止 Skill 写入、
  activation、memory write、Runtime decision 和 real-quality claim；任何代码不得根据状态直接编辑
  `skills/public`。
- reviewed candidate 冻结；后续同 package cohort 反馈只用于 replay。`supersede` 必须指向同 tenant、同
  data class、同 Skill/scenario/facet 的显式 replacement candidate。
- 当前 replay 只重算 aggregation/source/replay-set integrity，并明确
  `skill_behavior_replay_executed=false`。真正修改 Skill 后仍必须运行绑定正例/反例的 behavior replay，才可
  进入独立 package promotion review。

Governed context fact / 受治理上下文事实约束：

- `GovernedContextFact` 是 typed operational fact 的共享信封，不是无类型 KV、自然语言知识条目或通用白名单。共享字段至少包含 fact id/type/schema version、tenant/environment、`valid_from/valid_until`、source type/ref/version/freshness、status、owner/reviewer/reason、evidence refs、content hash 和 audit time。
- 首批 subtype 包含 `AuthorizedActivityFact`、`SecurityExerciseCampaignFact` 和 `ExerciseParticipantFact`；后续可增加 asset/identity/change-window/network-topology/service-relationship/risk-acceptance fact，但每种类型都必须有 discriminated Pydantic payload、validator 和专用 matcher/resolver。
- 禁止实现一个依赖自然语言、embedding 或 LLM 的万能 fact matcher。共享代码只能处理 tenant/environment、事件时间、source freshness、status/version 等公共 applicability；subject/target/behavior、participant attribution、network topology 等语义由强类型 matcher 负责。
- `SocGovernedContextService` 是 propose/activate/suspend/revoke/expire/version/query 的唯一公共生命周期边界；typed domain service 只能组合该 service 与 matcher，不得复制状态机。
- `GovernedContextFactRepository` 是 PostgreSQL source-of-truth 边界。允许使用带 `fact_type` discriminator、公共索引列和 typed JSONB payload 的单一 envelope 表，但 repository 返回前必须恢复并验证具体 subtype，不能把任意 JSON 当作 active fact。
- fact 与 evidence、policy、memory 必须分离：MCP/action 返回 `InvestigationEvidence`；governed fact 描述在某时点成立的业务上下文；deterministic policy 决定 disposition；memory 保存可复用研判经验。
- GF-01 当前合同位于 `soc_agent.contracts.governed_context`。稳定 `fact_id` 下的每个
  `fact_version_id` 都是追加式历史版本；`fact_id + version` 唯一，`current_key` 唯一保证一个 logical
  fact 只有一个 latest。Repository 写入必须携带 `expected_latest_version` 并在同一事务中 supersede
  previous latest + append next version；并发冲突必须 fail-fast，禁止 last-write-wins。
- `SocGovernedContextService` 当前实现 `propose/revise/activate/suspend/revoke/expire/get/list/list_versions`。
  revision 总是回到 `proposed` 并清空 reviewer，需要重新激活；从 active revision 时 latest 立即
  fail closed，不能继续参与后续 matcher。`revoked/expired` 是终态；尚未到 `valid_until` 时不能用
  expire 提前结束，必须明确 revoke。
- propose/revise 角色为 `soc_analyst|soc_engineer|soc_admin|soc_context_source`；
  activate/suspend/revoke 为 `soc_context_approver|soc_admin`；expire 额外允许
  `soc_context_service`。生产 transport 必须从认证上下文构造这些角色，不能接受 client-supplied role。
- Active gate 至少要求：明确 expiry、非空 evidence refs、可激活 source type、source 当前未 stale、
  governance reviewer role。`authoritative_system/adapter_sync` source 必须提供 source version、
  `fresh_until` 和 `authoritative=true`。
- `0013_governed_context_facts` / `soc_governed_context_facts` 是当前持久化版本；
  `SqlAlchemyAlertRepository` 与 in-memory adapter 实现同一 protocol。JSON payload 读取时必须恢复
  typed subtype 并与索引列核对，不能只信 JSON 或只信 discriminator。
- governed-context contracts 必须 `extra=forbid`；未知或拼错字段直接 validation failure，不能被 Pydantic
  静默忽略。GF-01 的 `valid_at` 只过滤 envelope business validity，不得被 transport/service 当成
  authorization 成立；event-time state/source/scope applicability 仍由 AA-01 matcher 统一裁决。
- `soc context propose|revise|activate|suspend|revoke|expire|list|get` 只调用公共 service；CLI 的本地角色
  装配不等于生产认证。GF-01 不得注入 Runtime prompt、修改 detection decision、更新 ReviewQueue 或关单。

Authorized activity / 授权活动事实约束：

- `AuthorizedActivityFact` 表示某项扫描、渗透测试、运维、自动化或业务服务行为在明确范围和时间内获得授权。它不是 `SocMemoryRecord`、IP 白名单、`SocAgentApprovalGrant` 或 response action permission；四者不得复用同一状态机。
- contract 必须 vendor-neutral，至少包含 stable fact id/schema version、tenant/environment、activity type、subject selectors、target selectors、behavior selectors、`valid_from/valid_until`、source type/ref/version、status、owner/reviewer/reason、evidence refs 和 audit time。
- `subject/target/behavior` scope 必须支持 stable asset/service/account id、security tag、application/domain/CIDR、canonical scenario/behavior signature 和 optional detection alias。具体 IP 可以作为窄范围 selector，但不得默认形成跨环境、无期限的全局白名单。
- 任一 active fact 必须具有 tenant scope、authoritative source 或具备授权角色的人工确认、明确 expiry。范围过宽、无 expiry、source 不可追溯或仅有 free-text note 的事实只能保持 `proposed`，不得参与 disposition。
- fact status 至少区分 `proposed/active/suspended/expired/revoked`；suspend/revoke/expire 必须立即阻止新匹配，历史 replay 仍按 alert event time 和当时有效版本计算。
- `SocAuthorizedActivityService` 只负责 authorization query/match 和 disposition eligibility，生命周期必须复用 `SocGovernedContextService`；CLI/API/Web/TUI/Kafka/Lead Agent、source adapter 和 normalizer 不得直接写 fact repository 或自行判断授权成立。
- 外部 change/scanner/maintenance/CMDB/security-tag 系统仍是业务来源时，本地 governed fact 必须保存 external source ref/version/freshness；缓存失效不能伪装成有效授权。
- query-time MCP/action 和 webhook/Kafka/polling sync 都只能通过 source adapter 转换成 canonical fact/source observation。`security_tag.lookup` 是普通调查证据；只有授权事实 service 经显式 mapping 后才能把它用于授权匹配。
- `AuthorizationQuery` 只能从 canonical alert/entity/fact/scenario、alert event time 和 tenant context 构造；generic matcher 禁止识别 PingAn/Zeus 字段名。vendor alias 必须止于 normalizer/source adapter。
- `AuthorizationMatchResult` 必须由确定性 matcher 产生，至少记录 fact refs、`exact/partial/conflict/expired/not_found/unavailable`、matched/missing/out-of-scope dimensions、event time、source freshness、policy version 和 evidence refs。LLM 可以解释结果，但不得生成或改写 match status。
- exact match 必须同时满足 tenant/environment、event-time validity、subject、target、behavior 和 source freshness；任何必需维度缺失、冲突、过期、撤销或 source unavailable 都必须 fail closed 到 analyst review。
- 授权匹配不能修改 detection truth。真实利用、RemoteRegistry 启动或扫描行为仍可为 `actual_verdict=true_positive`，但可由确定性 disposition policy 提议 `closed_benign_true_positive`；不得改成 `false_positive`。
- Base Runtime 的 `SocDecisionPolicy` 只负责 grounded detection decision。授权 enrichment 在 persisted run 之后通过显式 orchestration/review service 执行；后续 disposition reconciliation 必须生成新 audit/version，不得覆盖原始 `AnalysisRun`。
- 初始版本只能 shadow：展示 matched fact、建议 canonical disposition 和解释，但仍进入人工关单。只有 exact authoritative match 在 replay precision、analyst override、source freshness、随机抽样复核和 rollback gate 达标后，才允许策略化自动关闭；任何 response action 仍走独立 approval boundary。
- 一次人工确认应先生成 scoped/expiring fact proposal；经授权角色激活后，后续 exact matches 可以复用。只有新行为签名、scope mismatch、partial/conflict、expired/revoked 或 source unavailable 才重新进人工，从而避免每条告警重复确认。
- 置信度评测必须区分 detection label 和 operational disposition label。`ConfidenceCalibrationSample` 后续应增加 `actual_disposition`、calibration eligibility、missing decisive context 和 authorization fact refs；若模型的 exact bounded input 不含决定性授权事实，业务真值可以保留，但 analyzer calibration 必须标记 `excluded_missing_decisive_context`。
- 被 calibration 排除的已知真值样本不得丢弃；它们进入 authorization-enrichment coverage、context availability 和 end-to-end disposition eval，不进入 analyzer Brier/ECE/threshold fitting。
- 需要持续观测 authorization match coverage、exact/partial/new-pattern 比例、analyst override rate、expired/stale fact rate、shadow disposition precision、抽样复核命中率和每条 active fact 的 alert fan-out。
- AA-01 当前合同位于 `soc_agent.contracts.authorization`，纯 query/matcher 位于
  `soc_agent.authorization`，公共入口为 `SocAuthorizedActivityService`。CLI/API/Kafka/Lead Agent 不得
  复制 selector 或 event-time 逻辑；只读 CLI 为 `soc context match`。
- query builder 只能消费 canonical `AlertInput`、`ExtractedEntities`、`FactReconstructionResult` 和显式
  tenant/environment/timezone context。`RoleResolution.selected_value` 必须先判型；不能把任意 asset/account
  字符串强制解释为 IP。无时区 event time 缺少显式 IANA timezone 时返回 `unavailable`。
- 历史匹配必须从 append-only fact versions 中选择 `state_changed_at <= alert.event_time` 的最后版本；
  事后创建的 proposed/active fact 不能反向授权旧告警。后续 revoke/suspend 不能改写撤销前的历史 replay。
- selector 语义固定为不同 `kind@namespace` group AND、同 group 多值 OR；CIDR 可包含 canonical IP。
  fact 声明 namespace 后 query 必须同 namespace。缺 group 为 `partial`，有兼容值但超 scope/recurrence 为
  `conflict`，lifecycle/business validity/source stale 为 `expired`，source/repository/history 不可用为
  `unavailable`。
- Repository error、candidate truncation、blocking `ConflictReport` 都必须 fail closed；不得因为找到某个
  selector 值就忽略其他 required group。AA-01 结果必须带 fact version/content hash、policy version、
  selector evidence paths 和 `shadow_only=true`。
- AA-01 只计算匹配，不持久化、不改 `AnalysisRun`/`Decision`/ReviewQueue、不提出 disposition。
  EX-01 通过 `AuthorizationEnrichmentRepository` 和 `SocAuthorizationEnrichmentService` 写入独立
  append-only `AuthorizationEnrichmentRecord`；记录必须保存 canonical query、排除 query id 的 semantic
  hash、完整 typed result、matcher policy、fact-version refs、actor、唯一 idempotency key 和 replay lineage。
- EX-01 只能关联已存在且 alert id 一致的 `AnalysisRun`；带 queue id 时必须校验 queue/run/alert lineage。
  同一 idempotency key 的重试只允许返回语义相同的原记录，不同输入复用必须 fail-fast。Replay 新增记录，
  不覆盖原记录，并通过 `replay_of_enrichment_id` 连接来源。
- EX-01 通过 `InvestigationContext.authorization_enrichments`、统一调查 timeline/counts、Web/TUI 和 bounded
  Lead Agent artifact 只读投影。它不得修改 `AnalysisRun`/`Decision`/ReviewQueue/memory/disposition，必须保留
  `shadow_only=true`、`decision_impact=none`。
- CLI 统一使用 `soc context enrich` 和 `soc context enrichment list|get|replay`；入口层不得复制 query/match
  或 repository 逻辑。事件为 `authorization.enrichment_recorded|replayed`。
- DP-01 使用 generic `SocOperationalDisposition`，并通过
  `SocDispositionProposalCommand/Record/ApplyResult`、`SocDetectionTruthSnapshot` 和
  `SocDispositionProposalService` 生成独立运营建议。不得复用 `Decision` 或 external disposition
  feedback record 伪装 proposal。
- DP-01 只能消费已持久化且 run/alert/queue lineage 一致的 enrichment；ReviewQueue 必须存在且为 open。
  只有 `status=exact`、存在 matched fact refs、enrichment 保持 shadow/no-impact，并且当前 persisted run
  detection truth 为 `true_positive` 时，才可生成
  `closed_benign_true_positive + authorized_activity_exact_match`。其他状态全部 fail closed。
- Proposal 必须同时保存 source enrichment/query hash/matcher policy/fact-version refs、detection truth
  snapshot、proposal policy、actor、idempotency key 和 semantic proposal key。相同 retry key 只返回原记录；
  相同 semantic proposal 使用不同 retry key 必须显式冲突，不能假装绑定一个未持久化的新 key。
  Repository 只允许 append；
  migration `0015_disposition_proposals` / table `soc_disposition_proposals` 是 source of truth。
- Proposal 固定 `proposal_mode=shadow`、`application_status=not_applied`、
  `requires_human_review=true`、`auto_close_allowed=false`，detection truth 和 ReviewQueue impact 均为
  `none`。Service 不得改 `AnalysisRun`、summary、ReviewQueue、memory、approval 或执行 action。
- CLI 为 `soc disposition propose|list|get`；InvestigationContext、timeline/counts、Web/TUI 和 bounded
  Lead Agent artifact 只能只读投影。事件为 `disposition.proposal_recorded`。
- EV-01 使用 `SocDispositionEvaluationScope` 固定 tenant/environment/time window/proposal policy/matcher
  policy cohort；跨租户、跨环境或跨版本结果不得混算。所有时间窗必须 timezone-aware。
- 随机抽样必须生成 append-only `SocDispositionSampleManifest`：对完整 cohort 使用可复现
  `sha256_rank_v1`，保存 population hash、selected proposal ids、sample size、seed hash 和 actor；不得保存原始
  seed。Population 查询触达 limit、proposal 缺 enrichment 或 lineage 破损时不得创建 manifest。
- Outcome 必须通过 `SocDispositionOutcomeCommand/Record` 和 `SocDispositionEvaluationService` 写入，且只允许
  绑定 lineage 一致、已经 closed 且有 closed_at/closed_by 的 ReviewQueue。不能从 `close_reason`、LLM 文本、
  Lead Agent summary 或 memory 推断 outcome。
- `analyst_resolution` 与 `sampled_quality_review` 是独立 label lane。Sampled review 必须引用持久化 manifest，
  proposal 必须属于 selected ids；已有 primary label 时 sampled reviewer 必须独立。`unknown` 记为
  `inconclusive`，不进入 precision 分母；不同 terminal disposition 记为 override。
- Outcome 为 append-only。更正必须显式 `supersedes_outcome_id=latest` 且 observed_at 不倒退；相同
  idempotency key 只返回同语义记录。服务端生成的 observed_at 不属于 retry 输入语义。唯一
  `lineage_key=hash(proposal, review_kind, supersedes-or-root)` 必须阻止并发写出两个 root 或两个相同后继。
- migration `0016_disposition_evaluation` / tables `soc_disposition_sample_manifests`、
  `soc_disposition_outcomes` 是 source of truth；indexed columns 与 typed JSON payload 恢复时必须一致。
- EV-01 gate 同时检查 proposal/resolved count、resolution rate、shadow precision、override rate、sampled
  count/precision/coverage/agreement、source freshness 和 fact-version fan-out。任何 dataset truncation 或
  source-enrichment lineage gap 必须返回 `insufficient_data`。Policy 必须显式 allowlist primary/sample
  outcome source；不在 allowlist 的 replay/external/analyst label 不得进入该次指标。
- `passed_shadow_evaluation` 只允许标记 `eligible_for_governed_rollout_review`。Policy/report 固定
  `auto_close_allowed=false`；EV-01 不得修改 run、summary、ReviewQueue、memory、approval、proposal 或 action。
- CLI 为 `soc disposition sample create|list|get`、`soc disposition outcome record|list|get` 和
  `soc disposition evaluate`；InvestigationContext/Web/TUI/Lead Agent 对 outcome 只读投影。EV-02 才接
  Web/TUI/API 与 trusted external disposition 的结构化写入，仍不得绕过 service。
- EV-02 的唯一 HTTP 写入口是 authenticated `POST /api/soc/review/disposition-outcomes`。API 固定
  `source=analyst`，要求 `Idempotency-Key` header，并把 actor/surface 交给
  `SocDispositionEvaluationService.record_outcome()`；客户端不得伪造 external/replay source。
- Web 必须把 ReviewQueue close 与 outcome capture 展示为两个独立动作。只有 closed queue 才允许提交，
  且必须显式选择 proposal、observed operational disposition、primary/sample lane 和 reason；修订必须显示并
  传递 latest `supersedes_outcome_id`。不得把 `close_reason`、verdict 或按钮文案转换成 outcome。
- Review TUI 使用 `/outcome DPROP disposition idempotency-key reason` 和
  `/sample-outcome DSAMPLE DPROP disposition idempotency-key reason`；两者固定 analyst source，并通过同一
  service 校验 closed queue、sample membership、independent reviewer、幂等和 supersession。启动 TUI 时用
  `--actor-id` 绑定稳定审阅身份，独立抽样复核必须使用不同于 primary analyst 的 actor。
- trusted external disposition bridge 只接受 `mapping_trust_level=high`、mapped canonical status、verified
  target（queue/run/case binding）以及唯一 lineage-matching proposal。External record 必须先持久化；bridge
  成功/幂等/跳过原因进入 apply result、audit 和 event。重复回放可补写缺失 outcome。
- External bridge 只能自动 supersede 先前的 external-source primary outcome；若 latest primary 来自 analyst
  或 replay，必须跳过并要求显式人工 supersession。外部 reason 只作为显式 event 的标签理由，不能用于猜
  canonical status。Outcome 写入本身仍是 `review_queue_impact=none`、`auto_close_allowed=false`；既有
  high-trust external correction/queue sync 是独立边界，不能被误写成 EV rollout controller。
- EV-03 sample-review campaign 不新增 mutable campaign table。`SocDispositionSampleReviewInbox` 必须由
  immutable `SocDispositionSampleManifest.selected_proposal_ids`、当前 proposal、ReviewQueue、latest primary
  outcome 和该 manifest 的 latest sampled outcome 派生；manifest 仍是防挑样 source of truth。
- Repository 通过 `list_latest_disposition_outcomes_for_proposals()` 批量返回每个 proposal/lane 的最新记录；
  SQL 实现必须按 `observed_at, created_at, outcome_id` 确定顺序并对大型 id 集合分块，不能在 Web 请求中对
  整个 manifest 执行逐 outcome N+1 查询。
- 只有 latest sampled outcome 与 latest primary reviewer 独立时才计入 campaign completion。当前 reviewer
  与 primary actor 相同必须显示冲突并禁止提交；未关闭 queue、lineage 缺失/不一致必须显示明确 readiness，
  不能被算作完成。
- Gateway 只读入口为 `GET /api/soc/review/disposition-samples` 和
  `GET /api/soc/review/disposition-samples/{sample_id}/inbox`；reviewer actor 必须来自认证 request context，
  不能由 query/body 伪造。Inbox 必须分页且只返回 manifest-selected proposal。
- Web `抽样复核` 视图只负责 campaign/inbox 导航。点击条目后把服务端返回的
  `sample_id + proposal_id + queue` 交给 EV-02 capture form；不得建立第二个 outcome 写 API，不得允许手工
  换成 manifest 外 proposal，也不得根据 UI 状态关闭工单或开启 auto-close。

Tenant disposition policy / 租户级处置策略约束：

- `dev/local/staging`、测试资产、租户自定义免处置范围等属于 tenant operational policy，不是通用
  detection truth、LLM 推断、confirmed memory 或永久白名单。默认通用策略不得假设 `stg == safe`。
- source adapter 只能输出带 exact provenance 的 vendor-neutral environment/context candidate；不得输出
  `safe`、`skip_analysis`、canonical disposition 或关闭决定。PingAn/Zeus 字段名只能停留在 PingAn adapter、
  reviewed tenant mapping 或 fixture。
- environment/context candidate 必须由 CMDB、authoritative source 或经治理的 tenant mapping 确认后才可参与
  policy。仅凭 hostname、自由文本或 LLM 识别出的 `stg` 只能保留为 hint，不能触发免处置。
- 单一 hostname/environment hint 只能触发 `manual_validation_required` 或 no-match，不得独自生成
  exempt/benign/false-positive 结论。只有 reviewed deterministic rule，或引用 exact `E-*` 且通过完整
  environment/effect/evidence-quality 条件的 bounded policy Skill，才可形成 operational disposition。
- Base Runtime 不得因 tenant/environment policy 跳过 normalization、fact reconstruction、bounded analyzer、
  Grounding 或 `SocDecisionPolicy`。Tenant disposition reconciliation 发生在 detection decision 之后，并写入
  独立、可审计的 policy decision/proposal。
- 通用 evaluator 只能消费 typed governed context、detection truth、versioned tenant policy 和显式注入的
  `TenantPolicySignalResolution`；不得包含
  `if tenant == pingan`、`if "stg" in hostname` 或厂商字段分支。租户差异必须是 data/config/plugin policy，
  不能是 core code path。
- `TenantPolicySignal` 必须保存 stable signal id/key/value、provider id/version、source ref/hash、bounded
  evidence paths 和有限 typed attributes。`TenantPolicySignalResolution` 必须明确
  `completed|not_applicable|failed_closed`；provider 抛错时只能生成无 signal 的 failed-closed resolution，
  不能让规则误命中或失败主 Runtime。Decision key/content hash 必须包含完整 resolution lineage。
- signal provider 只读 completed canonical run 和自己的 tenant source，不得回写 run、repository、Memory，
  不得授权动作。generic evaluator 只做 exact key/value 匹配；不得解释 subject、attributes 或厂商来源。
- policy 至少记录 `tenant_id`、stable policy id/version、typed conditions、environment/asset scope、
  `valid_from/valid_until`、authoritative source、owner/reviewer/reason、rollout mode、content hash 和 audit
  metadata。历史 replay 必须使用 alert event time 当时有效的 policy/context version。
- detection truth 与 operational disposition 必须并存。例如真实命中可保持
  `actual_verdict=true_positive`，同时由 PingAn 非生产策略建议
  `operational_disposition=nonproduction_exempt`；不得仅因环境免处置改成 `false_positive`。
- non-production exemption 与 authorized activity 是不同 policy input。前者描述租户对已确认环境的运营规则；
  后者证明特定主体、目标、行为和时间范围内的活动获得授权，二者不得互相冒充。
- 带 `authorization_statuses` 条件并设置 disposition 的 tenant rule 只允许 `exact`。匹配输入必须来自
  event-time、scope、source freshness 都通过的 Governed Context matcher；ambiguous/conflict/expired/
  unavailable 不得形成 disposition。技术 verdict 必须保留为 true positive/suspicious，不得改成 false positive。
- 租户层默认关闭。`shadow` 只记录 proposal；owner/reviewer/time 完整的 `enforced` policy 可在 post-Runtime
  effective stage 改变 review requirement 和 operational disposition。它永远不能授权/执行动作；自动关单、
  封禁、隔离或抑制仍需独立 `SocAutomationPolicy`/Grant、adapter、rollout 和 rollback gate。
- contract v1 实现固定为 `TenantDispositionPolicy` -> `TenantPolicyDecision`，由 `SocAnalysisService` 在主分析事务提交
  后通过通用 `PostAnalysisObserver` 调用。结果写入 migration `0022` 的 append-only
  `soc_tenant_policy_decisions`；observer 失败不得回滚主分析，幂等重试按 `run_id + policy id/version/hash`
  去重。`shadow_only` 必须与 policy mode 一致；no-match/shadow 不可 apply，reviewed enforced match 才可
  `auto_apply_allowed=true`。所有模式的 detection/action/memory impact 固定为 none。
- policy resolver 必须按 alert event time 选择有效版本。naive timestamp 只有在 operator 显式配置 IANA
  timezone 后才可本地化，并在 decision 中标记 `alert_event_time_timezone_assumed`；不得按主机本地时区猜测。
  带有效期的 policy 在 event time 缺失时 fail closed。每条 decision 必须保存 exact policy content hash、
  policy time/source、selected rule 和逐条件 evidence path。
- 通用 composition 认识 `SOC_TENANT_POLICY_ENABLED`（默认 false）、policy path、环境、时区、可选
  advisor mode/Skill path/model。只配置 path 但未显式 enable 必须 fail startup；advisor 只在 deterministic
  no-match 后执行。其 strict output 必须引用 exact `E-*`，可选引用现有 `R-*` 和 `S/A/M/C/T-*`，并保存
  model/Prompt/Skill/response hash；调用、schema 或引用校验失败固定持久化 fail-closed no-match。
- 一个 `SocAnalysisService` 实例内的 confirmed Memory、tenant policy 和 automation observer 必须共享同一
  server-owned runtime environment。普通入口继续严格校验进程级环境变量一致；显式隔离的 DEV/eval service
  可由 composition root 注入实例级 `runtime_environment`，但必须同时覆盖这三层，禁止只改 Memory scope。
- PingAn v2 位于 `integrations/pingan/policies/tenant-disposition-v2.json`，组合策略位于
  `integrations/pingan/policy_skills/disposition/SKILL.md`。generic evaluator 不得 import PingAn module 或
  出现 PingAn 字段、网段、规则码、主机模式；application composition 只可在显式 PingAn provider 开关打开
  后注入 integration port，默认关闭时不得加载它。PingAn 当前确认 canonical `status=200` 只证明请求
  成功，单独出现不得升级或忽略。确定性非 `200` 规则只读取 canonical HTTP `100..599` 状态，要求至少一条
  HTTP 事务且所有事务均非 `200`；工单、Workflow、转发、规则、抑制和处置状态不参与。强制转交
  rule_code 优先于该忽略规则。明确攻击成功/失陷使该规则弃权，但不能确定性升级；明确成功、
  `企图/尝试` 和响应效果必须交给 Runtime/Policy Skill 组合判断。明确失败仍可按审阅规则忽略。
- PingAn EDR 安全路径快速策略是独立默认关闭的 signal provider。catalog 保留全部 exact path，只允许从
  `safe_paths` 中至少两个不同成员、动态段和来源告警推导一个变量目录段 family；`other_paths` 不得建族。
  当前告警所有 canonical process/executable path 完整命中 exact safe path 或 safe family 后才可发
  `all_relevant_paths_safe`，两种 match 对 `ignored` 具有同等直接效力。任一未知/非法/超预算路径、
  `other_paths`-only、多个 hash 或 hash mismatch 都必须 fail closed。MCP action 仍永久
  `decision_impact=none`；只有受审阅 enforced Tenant Policy 可消费该 signal，且必须保留 Runtime truth。

Security exercise / 护网与红蓝对抗事实约束：

- `SecurityExerciseCampaignFact` 必须记录 campaign ref、tenant/environment、时间窗、target scope、allowed/forbidden behavior、Rules of Engagement ref/version、authoritative source 和治理状态。
- `ExerciseParticipantFact` 必须记录 campaign ref、participant role（red/blue/white/referee/other）、team/actor ref、time-bounded identifiers 和 authoritative roster source。identifier 可以是 IP/CIDR/domain/account/certificate/agent id，不得只支持 IP。
- `ParticipantAttributionMatcher` 必须按 alert event time 匹配 identifier，并显式返回 `exact/ambiguous/conflict/expired/not_found/unavailable`。动态 IP、NAT、共享跳板机、代理和地址重新分配不能被强制归属于单一参与者。
- 参与者归属只回答“当时是谁”，不能回答“这次行为是否被授权”。`SocSecurityExerciseContextService` 必须依次组合 participant attribution、campaign applicability、target scope、behavior scope 和 forbidden behavior；任一步非 exact 都进入 analyst review。
- 只有 participant/campaign/authorization/source freshness 全部 exact 时，才可提议 `actual_verdict=true_positive` + `operational_disposition=closed_benign_true_positive` + `reason_code=authorized_security_exercise`。不得把演练行为改成 `false_positive`，不得为每种演练创建新的 canonical status。
- 红队/蓝队/白队基础设施不得进入全局 benign IOC 或永久 suppression list；IOC/行为历史必须保留，并附 campaign-scoped context。
- 普通分析师上下文默认只暴露 role/team ref 和必要 match explanation；个人身份、官方名单和敏感联系方式必须使用更严格的 field-level access control，并记录访问审计。
- 护网事实的 replay/eval 必须覆盖：合法 exact match、超出目标范围、超出演练时间、使用禁止技术、一个 identifier 对应多参与者、identifier 变更和 source unavailable。

SOC memory tracking 约束：

- 业务记忆必须实现为 typed memory record + facets + retrieval policy，不得实现为 `topic/rule_code/scenario` 等字段的联合等值主键。
- `rule_code` 只是 vendor alias 的一种；平安 `rule_code`、EDR `signature_id`、SIEM `analytic_id`、Sigma id、Splunk analytic id 等都只能进入 `facets.detection.vendor_aliases`，不能成为跨公司必填字段。
- `facets.detection.canonical_key` 是推荐的跨供应商检测标识；缺失时必须能通过 `source_type/product/category/rule_name/MITRE/raw fingerprint` 生成弱 key，或退化到 topic/scenario 检索。
- topic、canonical detection、vendor aliases、scenario、entity、environment 都是可选检索 facets；缺失任意一个 facet 时系统仍必须能工作，只是召回分数降低。
- 具体 IP、UM、host、URL、file hash、process hash 等实体默认只能作为 evidence refs、query dimensions 或 case memory，不得默认成为长期全局 memory 主键。
- 授权活动、护网 campaign/participant、变更窗口、资产状态等 governed fact 不是 reusable memory；即使来自 correction/review note，也只能生成对应 typed fact proposal，不能通过 `SocMemoryService.confirm` 变成 runtime operational fact。
- TUI/Web/Kafka/Lead Agent/domain handler/external disposition sync 只能生成 `SocMemoryCandidate`；不得直接写 `confirmed` fact 或 active lesson。
- 所有 memory candidate 必须包含 source surface、source run/review/evidence refs、idempotency key、status、confidence、proposed content、facets、evidence refs 和 reviewer/audit fields。
- 当前已实现 DB-first candidate persistence、confirmed-memory boundary、governed retrieval activation 和 retrieval policy MVP：`SocMemoryService.propose_candidate()` 必须强制写 `pending_review`，并保持 `runtime_decision_allowed=false`；`SocMemoryService.list_candidates()` / `get_candidate()` 是 API/CLI/Web/TUI/Lead Agent 查询候选记忆的 service 边界；`SocMemoryService.review_candidate()` 是 confirm/reject/deprecate/expire 的唯一候选状态机边界；`SocMemoryService.set_retrieval_activation()` 是 confirmed record retrieval enable/disable 的唯一状态迁移边界；`SocMemoryService.find_relevant_records(SocMemoryQuery)` 是 confirmed memory 检索的唯一 service 边界。
- Memory 人工修订只能调用 `SocMemoryService.propose_revision_candidate()` / `POST /api/soc/memory/records/{memory_id}/revision-candidates`，并显式区分 `observed_use` 与 `operator_direct`。前者必须携带实际 `source_run_id` 并核验持久化 `SocMemoryUseRecord` 及使用时 content/facet hash；后者不允许伪造 use，而是冻结当前 predecessor version/hash，并尽可能沿用 predecessor source run/alert。两者都必须携带 `expected_record_version`、typed issue、充分理由、可信 actor 和幂等键，在一个 mutation transaction 中 CAS 暂停旧 retrieval、创建带 immutable lineage 的 `memory_revision` pending candidate 并写 mutation audit；不得由 React、router 或 repository 直接覆盖旧 Lesson。同一 Memory 同时只允许一个 open revision。
- `issue_type=applicability_too_broad` 必须从可追踪 source run 通过当前 `SocMemoryProfile` 重新投影 facets/applicability；不得复制 predecessor 的过宽 scope。Run 缺失、alert lineage 不一致或 canonical facets 不足时整个 mutation fail closed。新 scope/profile identity 必须冻结在 revision candidate，future-match directive 仍由后续人工审核决定。
- 修订候选继续复用普通 Candidate 的 Business Lesson、applicability 和人工确认流程。修订开放期间，旧 record 必须保留 `revision_pending=true`，`SocMemoryService.set_retrieval_activation(ENABLE)` 必须拒绝重新启用。确认后创建新的 `SocMemoryRecord`，并将旧 record 标记 `deprecated`、旧 candidate 标记 `superseded`，保存前后 Memory/Candidate ID、版本、actor、reason 和时间；旧内容保持原样。新 record 是否启用 retrieval 仍是独立治理动作。修订候选被 reject/expire 时必须以 CAS 结束 pending 标记但保持旧 retrieval 关闭，之后只能由显式 activation mutation 恢复；被 reject 的修订候选不得以 stale lineage reopen，必须发起新的受治理修订。
- 候选记忆来源桥接固定在 `soc_agent.memory.sources.SocMemoryCandidateSourceBridge`：新增来源必须先构造 `SocMemoryCandidateCreateCommand`，再经 `SocMemoryService.propose_candidate()` 写入，不得在 Web/TUI/Kafka/Lead Agent/domain handler 内直接拼 repository row。
- `SocReviewService.correct()` 是 correction、Memory outcome feedback 和可选 candidate promotion 的 service 边界。普通 correction 默认 `observed_only`；只有 typed `promote_to_memory=true` 通过 Admission 后才把 candidate id 回写到 `CorrectionRecord`、audit 和 event。外部反馈复用 correction 链路但不携带 promotion，不能逐事件创建 candidate。
- `SocReviewService.add_note()` 是 ReviewQueue review note -> pending memory candidate 的 service 边界；`soc review note`、Web/TUI note action 和后续 Lead Agent/Kafka note source 都必须通过它或同级 service 方法进入 `SocMemoryCandidateSourceBridge`，不得直接写 `soc_memory_candidates`。Review note source type 固定为 `review_note`，幂等键必须至少覆盖 queue/run/alert/note，并可附加 scenario/domain/finding refs。
- `SocReviewService.promote_run_to_memory()` 是“已完成研判 -> 人工提炼候选”的唯一 service 边界；
  Gateway 固定使用 `POST /api/soc/memory/runs/{run_id}/promote`，要求可信登录身份、
  `soc_analyst|soc_admin` 和 `Idempotency-Key`。显式操作及精确 run/alert lineage 是 promotion signal；
  `note` 为可选审核提示，不是准入、授权或最终业务判断字段。历史客户端的 `reason` 仅作为 `note` 兼容别名。
  该入口只绕过自动 Pattern 的 support/distinct-source 门槛，不绕过 `MemoryAdmissionService`、候选审核、
  Business Lesson 和 retrieval activation；输出固定为 `manual_note` 来源的 `pending_review` candidate，
  不得修改当前 run decision、ReviewQueue、confirmed Memory、tenant policy 或 action authority。同一
  run/alert 的 Candidate identity 必须稳定，补充备注或重复点击复用既有审核任务，不得制造重复候选。
  最终 verdict、business fact、applicability、handling guidance 和 governance reason 只能在 Candidate 审核
  边界填写。无可选备注时，mutation audit 必须用系统操作描述记录 actor/time/run/action，不得伪造业务理由。
- 分析师采纳 Lead Agent 结论必须是显式 human mutation，不是模型回调或 assistant message 自动写入。当前
  `ReviewNoteOrigin.ACCEPTED_LEAD_AGENT_CONCLUSION` 要求 queue、thread、message 和 acceptance reason，保留
  actor surface，并仍生成 `pending_review` 的 `review_note` source；非 Lead Agent TUI 不得暴露采纳命令。
  CLI/TUI lineage 属于人工声明/当前 stream provenance，不能冒充服务端消息真实性。Gateway/Web 固定使用
  `POST /api/soc/review/items/{queue_id}/lead-agent-threads/{thread_id}/accept`：请求体只允许 message ID 和
  acceptance reason，要求 authenticated thread read/ownership 与 `Idempotency-Key`，不得接收 assistant 文本。
  Gateway 必须从 thread metadata + 当前 materialized checkpoint branch 解析 `soc-triage` 的最后一条可见
  terminal assistant message，拒绝 stale/superseded、hidden/summary、tool-call、empty、oversized、ambiguous
  和不可用状态，并保存 checkpoint ID/text hash provenance 后调用 `SocReviewService.add_note()`。只有 open
  ReviewQueue 可创建新来源；idempotent retry 仍可返回已存在结果。
- Direct Web `soc-triage` 的 `context.soc_review_queue_id` 只是客户端 identity hint，不是可信 context。
  Gateway 必须要求 authenticated `lead_agent`、校验线程 ownership 与 agent identity，通过
  `SocReviewService.get_investigation_context()` 重建 bounded artifact，并验证 queue/run/alert/summary/tenant
  lineage。首次 run 使用 owner-scoped atomic get-or-create；线程 metadata 中的 queue/run/alert binding 是
  server-reserved、write-once，已有绑定只能复用，切换 queue 必须创建新线程。
- Web bounded artifact 只能放入 server runtime context。`SocLeadAgentReviewContextMiddleware` 在每次 model
  call 中临时注入 System authority contract + hidden Human data，不得把 artifact 写入 checkpoint/history；
  rendered projection 超过 48,000 characters 必须在 run admission 前 fail closed。每轮都重新读取当前
  InvestigationContext，不允许只复用首次 artifact。
- 每条由该 context 生成的 terminal AI message 必须保存
  `SocLeadAgentReviewContextProvenance`，至少包括 artifact/schema/hash、queue/run/alert、chat thread/run、
  skill hash、rendered size 与 context creation time。Web acceptance 必须同时匹配 route queue、immutable
  thread binding 和 message provenance，再保存被采纳的 exact snapshot hash。不得在 acceptance 写入 note/
  candidate 后重算 current context hash，否则合法幂等 retry 会因自身 mutation 失效。该桥不替代 TUI
  `SocLeadAgentChatService`，也不授予 verdict、memory、close 或 action authority。
- Client-facing `POST /api/threads/{thread_id}/state` 必须剥离提交消息中的保留 SOC review-context
  provenance；人工 checkpoint rewrite 只能使 acceptance fail closed，不能伪造 middleware 生成的可信
  lineage。服务端 graph/middleware 的正常 checkpoint 写入不经过该 ingress，不受此规则影响。
- Kafka/批处理不得逐 alert、run、finding 或 offset 创建 memory candidate。PI-03F3 固定使用
  `MemoryPatternObservationCreateCommand -> SocMemoryPatternService`：每个 completed Runtime result 最多写
  一条 immutable observation，且 Kafka/batch composition 均默认关闭；sidecar 的 ineligible/error 只进入
  结果状态，不得让基础 Runtime 失败或改变 Kafka commit 语义。
- `soc.memory_pattern_aggregation.v3` 由 generic kernel 执行固定窗口、质量门和候选生命周期；same-class、
  duplicate occurrence 和 candidate applicability 由 server-owned `SocMemoryProfile` 提供。generic fallback
  选择最强可用通用维度；PingAn profile v3 使用 detection key + normalized detector signature + behavior
  fingerprint 建立 compound cohort，detection-only/weak-only 降为 rule context，behavior-only strong
  pattern 作为 ruleless fallback，并拒绝
  category-only cohort。`rule_code` 不是必填项；禁止在 generic Runtime 中拼接供应商字段。
  aggregation key 必须包含 policy/profile/feature schema、lineage 和固定 window，lineage 必须隔离 tenant、
  environment 与 `simulation|operational` data class。
- fixed window 只能使用从原 Runtime input 重建出的 canonical timezone-aware
  `AlertInput.event.event_time`，不能使用 replay/run started time。缺失或 naive source time 必须
  `skipped_ineligible`，不得在 generic layer 猜测时区。默认 window=86400s、minimum support=5、minimum
  distinct sources=5；达到 recurrence 门槛后还必须有 minimum conclusive support=5、risk/benign consistency
  >=0.8，以及至少一个与 candidate type 对应的 consensus exact strong anchor。
- 每个 v3 observation 必须携带 bounded lesson snapshot，包括 verdict/risk class、review/evidence state、
  summary/reason/recommendation、primary scenario/stage 与 direction。质量不足、结论冲突、未决过多或缺少
  strong anchor 的 cohort 只保留 observation 和 reason codes，不得创建 candidate、不得进入专家队列。
- 通过全部门槛后必须通过已有 `SocMemoryService.propose_candidate()` 创建 exactly one frozen
  `pending_review` pattern-level candidate；candidate 正文必须包含适用范围、verdict 分布、一致率、代表性
  结论、少数/未决数量和复核边界，不得复述单个 alert。snapshot 保存 policy/window/scope、observation/source
  IDs、cohort quality 和 evidence-set hash。后续 observation 只能由 replay 报告为 added，candidate 不自动
  修改或 supersede，supersession 固定 `manual_only`。
- Candidate 幂等 identity 必须使用 window-independent lesson fingerprint：policy/lineage、dominant risk class
  和 consensus strong anchors。后续 fixed window 得到相同 fingerprint 时，只记 reinforcement observation 并
  指向既有 governed candidate，不得创建新的专家任务；risk class 或 strong-anchor scope 实质变化时才是新的
  lesson candidate，且仍不得自动 supersede 旧记录。
- `occurrence_key` 必须在 `(aggregation_key, occurrence_key)` 内唯一。PingAn 以原始 payload 中稳定、运营可见的
  ZEUS `alertId|alertCode` 为第一 occurrence identity；缺失时依次使用 canonical sensor event ID、exact input
  hash 和 bounded time/entity scope。同一 ZEUS 预警即使 Kafka offset 或可变 payload 字段变化也不得增加
  support；相同 IP/规则在不同时间生成新的 ZEUS alert ID 时属于新的真实 occurrence。PingAn legacy
  timestamp 缺少 offset 时由 Adapter 按 `Asia/Shanghai` 类型化并在 `event_time_policy` 留下 assumption，
  通用 Memory Kernel 仍只接受 timezone-aware canonical event time，不自行猜测租户时区。
- Pattern candidate 必须携带 profile-owned `SocMemoryApplicabilitySpec`。它包含 profile/version/feature-schema
  identity 以及 exact required/optional/excluded facets；query 只能由 composition root 选择同一 profile，调用方
  不得通过请求参数选择 profile。Applicability 与 ranking 独立，非 `applicable` 结果不能触发 typed directive。
- `soc_memory_pattern_observations` 是 observation store，migration 为
  `0021_memory_pattern_observations`；它不是 confirmed memory 表。`soc memory patterns list|replay` 只能调用
  `SocMemoryPatternService` 做只读 inspection/recomputation，replay 固定
  `candidate_mutation_performed=false`。
- `SocDomainTriageResult/SocDomainFinding` 可以通过 source bridge 生成 domain finding candidate，但必须由显式 service/entry 调用；只读 investigation context assembly 不能在渲染/读取过程中写 candidate。
- `soc_memory_candidates` 是当前 `SocMemoryCandidate` 的 SOC business store 表；`SqlAlchemyAlertRepository` 实现 `MemoryCandidateRepository` 方法。生产和本地持久化都必须通过 migration `0010_memory_candidates` 或 `create_soc_tables()` 创建该表。
- `soc_memory_records` 是 `SocMemoryRecord` 的 SOC business store 表；`confirm` decision 会从 candidate 派生一条 `SocMemoryRecord(status=confirmed, retrieval_enabled=false)`，生产和本地持久化都必须通过 migration `0011_memory_records` 或 `create_soc_tables()` 创建该表。
- retrieval activation 固定使用 `SocMemoryRetrievalActivationCommand` 和 policy version `soc.memory_retrieval_activation_policy.v1`。enable 必须携带 `memory_id`、`expected_record_version`、reason、timezone-aware `activation_valid_until`、`review_after_days` 和 request-context idempotency key；disable 必须携带 expected version/reason/idempotency key，且清除 active validity/review fields。
- activation 角色只能是 `soc_memory_reviewer` 或 `soc_admin`，并且必须有可信 `ActorContext.auth_source`。Gateway/Web/CLI 的入口角色装配不能替代 core service gate；普通 `soc_analyst` 必须被拒绝。
- `MemoryRecordRepository.compare_and_set_memory_record()` 是 activation/deactivation 与 linked record deprecate/expire 的并发边界。stale expected version 必须 conflict；每次成功迁移 bump `SocMemoryRecord.version`。不得用 read-then-unconditional-save 模拟 CAS。
- memory record 变更与 `SocMutationAuditRecord(operation=memory_retrieval_activation)` 必须在同一个 `SocMutationUnitOfWork` 中提交，event 只在 commit 后发出；任一写失败必须同时回滚 record/audit。完全相同 idempotency key + command hash 返回既有逻辑结果，同 key 改内容必须 conflict。
- 直接把 `retrieval_enabled=true` 写进 repository、fixture 或 migration 不构成合法 activation。`find_relevant_records()` 必须同时校验 confirmed status、固定 policy version、activation validity、review due、record/source validity；无治理 metadata、activation expired 和 review overdue 分别计数并排除。
- `SocMemoryCandidate.idempotency_key` 是候选记忆重复抑制边界；同 key 重放必须返回既有 candidate，不得重复写入或重复发出 memory update event。
- `SocMemoryCandidate.status=pending_review` 只能表示待评审建议；Web/TUI/Lead Agent 可以展示它，但不得展示为 confirmed fact、active lesson 或已生效策略。
- `confirm_candidate` 只表示候选通过初审，不创建 `SocMemoryRecord`；`confirm` 才创建 confirmed record。`reject` 只更新 candidate 状态，不创建 record；`deprecate` / `expire` 必须同步更新 linked record 状态和 deprecation metadata；非法状态迁移必须 fail-fast。
- Gateway memory candidate API 路径固定在 `/api/soc/memory/*`：
  - `GET /api/soc/memory/candidates`
  - `GET /api/soc/memory/candidates/{candidate_id}`
  - `POST /api/soc/memory/candidates/{candidate_id}/review`
  - `GET /api/soc/memory/records`
  - `GET /api/soc/memory/records/{memory_id}`
  - `POST /api/soc/memory/records/{memory_id}/retrieval`
  - `POST /api/soc/memory/search`
- `soc memory list/get/review/search`、`soc memory records list/get/retrieval` 和
  `soc memory patterns list/replay` 是本地/运维查询、评审、治理式启停、检索记忆和重复模式审计的
  headless CLI；它们只能调用相应 core service，不能直接查 repository row。`soc memory search
  --baseline-json` 与 pattern replay 都是 deterministic read-only diff/recomputation，不写业务状态。
- Kafka daemon 生成 memory candidate 时，幂等键必须包含 `topic/partition/offset` 或 run id；重复消费不能增加重复 fact 或污染 evidence count。
- `pending_review`、`confirmed_candidate`、`confirmed` candidate 和 `SocMemoryRecord(retrieval_enabled=false)` 默认都不进入全局 prompt 注入；只有经治理激活、activation/review/source validity 均有效且检索评分/预算通过的 confirmed record 才可以进入 `InvestigationContext.relevant_memories`。PromptBuilder 注入仍是后续独立切片。当前 `InvestigationContext.memory_candidates` 只用于展示和人工评审，不参与 runtime verdict。
- Memory 检索必须返回 match reason、score、memory id、version/hash、token estimate 和 skipped counters，支持后续 replay diff 和回滚。
- `InvestigationContext.relevant_memories` 只能由 `SocReviewService.get_investigation_context()` 通过 `SocMemoryService.find_relevant_records()` 生成；ReviewQueue API/TUI/Web/Lead Agent bounded context 不能直接查 `soc_memory_records` 或自己计算 score。
- `SocMemoryService` 是 memory 写入、确认、驳回、过期、检索和注入前筛选的唯一 service 边界；CLI/TUI/API/Web/daemon/Lead Agent 不能直接写 memory repository。
- PostgreSQL memory store 是唯一 source of truth；wiki/OKF 只能作为 DB 导出的 read model / review projection / portable export。wiki 反向修改必须生成 change proposal，经 review 后通过 `SocMemoryService` 写回新版本，不能直接覆盖 DB。

Review queue 约束：

- `ReviewQueueItem` 是人工复核队列读模型，由 `AlertSummary` 派生，不替代完整 `AnalysisRun`。
- `SocAnalysisService.analyze/replay()` 是唯一允许自动创建或更新 review queue item 的入口；CLI/API/TUI/daemon 不能自己拼 queue item。
- 入队原因必须是结构化 reason，例如 `summary.needs_review`、`low_confidence`、`uncertain_verdict`、`high_severity`。
- 同一个 run 同时最多保留一个 open review item；重新分析同一 run 的派生 summary 时更新 open item，而不是制造重复待办。
- `SocReviewService.correct()` 记录人工 correction 后，必须关闭该 run 的 open review item；关闭队列不能删除原始 run、summary 或审计记录。
- `SocReviewService.close_queue_item()` 只表示复核待办已处理，不等价于修改 verdict；需要改判必须走 `CorrectionCommand`。
- `SocReviewService.add_note()` 只表示记录分析师观察并提出候选记忆，不等价于关闭 queue、修改 verdict 或确认 memory。
- `soc_review_queue` 保存扁平索引字段和完整 `item_payload`，字段优先服务列表、筛选和复核入口：`status`、`priority`、`alert_id`、`run_id`、`source_type`、`rule_code`、`verdict`、`updated_at`。
- `InvestigationEvidence` 是资产定位、威胁情报、安全标签、软件路径等只读 investigation action 的结果证据，不是原始告警证据、不是 confirmed memory、不是 operational verdict。当前不存在外部 EDR process-tree/HIDS-context action。
- `InvestigationEvidenceRepository.save_evidence()` 只能在 service/action boundary 调用；CLI/API/TUI/Web/daemon 入口不能自己拼 evidence 绕过 dispatcher。
- Dispatcher 生成的 `InvestigationEvidence` 必须复制当前 `ServiceRequestContext.request_id/trace_id`，使 Action -> MCP -> Provider -> persisted evidence 可关联；旧记录允许为空，但新执行不得仅依赖 thread/queue ID 代替调用链 provenance。
- `SocReviewService.get_investigation_context()` 是聚合 action evidence、external disposition feedback 和 memory candidates 的边界；ReviewQueue API/TUI/Web/Lead Agent context bridge 都从 `InvestigationContext.action_evidence` / `InvestigationContext.external_dispositions` / `InvestigationContext.memory_candidates` 读取，不直接查 repository。
- read-only action evidence 可以帮助后续分析和人工复核，但不得自动修改 `AnalysisRun.decision`、不得自动关闭 review queue、不得直接写 confirmed memory。
- `soc_investigation_evidence` 是当前 `InvestigationEvidence` 的 SOC business store 表；`SqlAlchemyAlertRepository` 实现 evidence repository 方法。生产和本地持久化都必须通过 migration `0008_investigation_evidence` 或 `create_soc_tables()` 创建该表。
- `soc_external_dispositions` 是当前外部状态/理由回流的 SOC business store 表；`SqlAlchemyAlertRepository` 实现 external disposition repository 方法。Web/TUI/Lead Agent 只能把它作为外部人工反馈展示，不得把 `memory_candidate_id` 展示为已确认知识。
- `soc_memory_candidates` 是当前候选记忆的 SOC business store 表；`SqlAlchemyAlertRepository` 实现 memory candidate repository 方法。Web/TUI/Lead Agent 只能把它作为待评审候选展示，不得把 `pending_review` candidate 展示为已确认知识。
- Gateway `SocReviewService`、`soc review context/tui` 和 `soc chat tui --lead-agent` 必须使用同一 repository 作为 `evidence_repository`，确保 Web/TUI/Lead Agent 可以跨进程看到 read-only action evidence。
- Gateway `SocReviewService`、`soc review context/tui` 和 `soc chat tui --lead-agent` 必须使用同一 repository 作为 `external_disposition_repository`，确保外部反馈在 API/Web/TUI/Lead Agent 上下文中一致可见。
- Gateway `SocReviewService`、`soc review context/tui` 和 `soc chat tui --lead-agent` 必须使用同一 repository 作为 `memory_candidate_repository`，确保候选记忆在 API/Web/TUI/Lead Agent 上下文中一致可见。
- `soc review context --summary` 和 `soc demo alert` 只能调用 `SocReviewService.get_investigation_context()` 生成只读 compact view；它们不得在读取 context 时写 memory、写 evidence、执行 action 或修改 review status。
- Gateway ReviewQueue API 路径固定在 `/api/soc/review/*`：
  - `GET /api/soc/review/items`
  - `GET /api/soc/review/items/{queue_id}/context`
  - `POST /api/soc/review/items/{queue_id}/close`
  - `POST /api/soc/review/runs/{run_id}/correct`
- ReviewQueue API/TUI/Web 只能调用 `SocReviewService`，不能直接读写 repository 或组装 queue item。
- API/TUI/Web close/correct 必须构造 `ServiceRequestContext`；`ActorContext.surface` 必须准确标识 `api` / `tui` / `web`，`ActorContext.auth_source` 必须标识建立身份的真实信任边界。
- L3 状态变更不能只依赖 Gateway/router 检查。`SocReviewService`、`SocMemoryService`、normalization maintenance、governed-context lifecycle 和 `SocAgentApprovalService` 必须在 core service 内调用共享 `require_actor_roles()`；`actor_id=anonymous`、`auth_source=unknown` 或缺少命令所需 role 均 fail closed。
- Gateway 已认证普通用户映射为 `soc_analyst`，管理员映射为 `soc_admin`；CLI/TUI/daemon/external adapter 必须使用显式且受控的 local/daemon/adapter auth source，不得伪装成 session 用户。
- ReviewQueue Web thin page 通过 Gateway `/api/soc/review/*` 调用，不允许前端绕过 API 直接写库。
- Web 请求必须携带 `x-soc-surface=web`、`x-trace-id`；状态变更请求必须携带 `idempotency-key`。
- Web 前端可以携带 `x-soc-actor-id` 作为显式上下文，但 Gateway 侧必须优先使用认证中间件写入的 `request.state.user.id`，不能信任可伪造 header 覆盖已认证用户。
- Gateway 只接受白名单 SOC surface header；非法 `x-soc-surface` 必须降级为 `api`，不能把任意 header 值写入审计记录。
- `soc review tui` 必须保持 DeerFlow-aligned：
  - 可以复用 DeerFlow TUI 的 Textual app、theme、composer、command palette、stream 设计思想和组件。
  - 当前 ReviewQueue TUI 是 thin client，不接 DeerFlow agent stream，不写业务判断。
  - 后续 SOC Lead Agent TUI/chat 才接 DeerFlow messages/artifacts/streaming/clarification。
  - TUI 不应把 review queue 结构化业务数据塞进 `ThreadState.artifacts`；artifacts 只保存用户可打开/下载的生成文件路径。

SOC Agent chat stream 约束：

- `SocAgentChatService.stream()` 是 TUI/Web/Channels 的统一交互流入口；`send_message()` 只能 materialize 同一条 stream，不能定义第二套 headless 协议。
- `SocAgentStreamEvent.type` 与 DeerFlow embedded client/TUI 对齐，只允许 `values`、`messages-tuple`、`custom`、`end`。
- `values.data` 可以携带 `title`、`messages`、`artifacts`、`thread_id`；`artifacts` 仍只表示用户可打开/下载的生成文件路径。
- SOC 结构化上下文通过 `custom` event 暴露，例如 `{"kind": "soc.review_context", ...}`；不要塞进 `ThreadState.artifacts`。
- `SocAgentChatService` 可以调用 `SocReviewService`、`SocAnalysisService`、`SocMemoryService` 等 core services，但不能直接读写 repository、直接改 verdict、直接写 memory。
- `SocAgentChatService` 是 deterministic shell/context loader；真实 DeerFlow 对话由独立的
  `SocLeadAgentChatService(agent_name=soc-triage)` 承担。两条入口都必须保留 Runtime 固定控制流、
  core service 和人工审批边界，不能因为接入 LLM/skills/MCP 而放宽权限。
- `SocAgentCapabilityRouter` 是 SOC Lead Agent route 白名单：
  - 当前默认只允许 `chat.freeform` 和 `review.open_context`。
  - 未知 slash command 必须映射到 `command.unknown` 并拒绝。
  - `SocAgentChatRequest.allowed_routes` 只能收窄单次请求可用 route，不能扩大全局白名单。
  - route allowed 只代表可以进入下一层 service/action boundary，不代表允许执行处置、改判、写 memory 或调任意 MCP。
  - 每次 route decision 必须通过 `custom kind=soc.route_decision` 出现在 stream 中，便于 TUI/Web/Channels 和 replay 观察。
- `SocAgentActionDispatcher` 是 route -> service/action 边界：
  - dispatcher 可以调用 core service，例如 `SocReviewService.get_investigation_context()`。
  - dispatcher 不能直接 import repository、normalizer、runtime pipeline、Gateway router、TUI app 或 MCP client。
  - 每次 dispatch 必须返回 `SocAgentActionResult`，并通过 `custom kind=soc.action_result` 出现在 stream 中。
  - `custom kind=soc.action_result` 必须携带 `SocAgentActionResult.payload`，让 TUI/Web/Channels 可观察 read-only adapter 输出、approval boundary result 和后续 tool result。
  - 当前允许的真实 service/action path 包括 `review.open_context` 和显式 read-only adapter route；`chat.ready_message` 只是 deterministic shell message。
  - 显式 read-only adapter route 只能来自受控 tool/gateway metadata，例如 `SocAgentChatRequest.metadata["soc_route"]` 和 `metadata["action_payload"]`；不得从自然语言消息里猜测 route 或 payload。
  - dispatcher 调用 read-only adapter 前必须同时满足 capability router allowlist、permission policy 和 action adapter registry 精确匹配；缺少 registry、payload 或 adapter 时必须 fail-fast。
  - read-only adapter 成功执行后可以通过注入的 `InvestigationEvidenceRepository` 写入 `InvestigationEvidence`，复制 `request_id/trace_id`，并把 `evidence_id` 回填到 `SocAgentActionResult.payload`；没有 evidence repository 时 action result 仍然有效，但不会跨上下文复用。
  - `InvestigationEvidence.result_payload` 保存 adapter 输出快照；进入 Lead Agent bounded context 时必须限量，不允许无限制塞入所有历史 tool result。
  - 后续 `review.correct`、`analysis.replay`、封禁/隔离/下发规则等 action 必须先补 permission/human approval，再接 service command。
- `SocAgentActionPolicy` 是 action 执行前权限闸门：
  - 每次 allowed route 进入 dispatcher 前必须先得到 `SocAgentPermissionDecision`，并通过 `custom kind=soc.permission_decision` 出现在 stream 中。
  - `read_only` action 可以直接执行，例如 `chat.ready_message`、`review.open_context`。
  - `analyst_write` action 必须要求 actor 具备 `analyst` role，例如未来的 `review.correct`、`analysis.replay`。
  - `high_risk` action 必须返回 `requires_human_approval=True` 且不执行，例如未来的封禁 IP、隔离终端、任意 MCP 调用。
  - `high_risk` action 被拒绝时必须生成 `SocAgentApprovalRequest`，并通过 `custom kind=soc.approval_request` 暴露 `approval_request_id`、`permission_decision_id`、`action`、`risk_level`、`requested_by`、`status=pending`。
  - `SocAgentApprovalRequest` 只是审批请求，不是执行授权；后续执行必须另有 approval token、audit record、idempotency key。
  - `SocAgentApprovalRequestRepository` 是多入口 approval inbox 边界；Kafka daemon、Agent middleware、API/Web/TUI 产生的 pending request 都必须写入同一 repository contract。
  - Agent/TUI chat path 如果注入 `SocAgentApprovalService`，必须通过 `SocAgentApprovalService.submit_request()` 持久化高风险 `SocAgentApprovalRequest`，然后再把同一个 request 作为 `custom kind=soc.approval_request` 发给 stream 消费端；不允许 stream event 和 inbox record 分叉。
  - Agent/TUI chat path 未注入 `SocAgentApprovalService` 时，只允许作为 headless/test shell 输出 approval request event，不得隐式创建临时 repository 或直接写 DB。
  - Daemon path 的写入边界是 `SocDaemonService.submit_approval_request()`，内部只能调用 `SocAgentApprovalService.submit_request()`；Kafka 生命周期由 `SocKafkaConsumerRunner` / CLI runner 管理，`SocDaemonService.start()` 不作为第二套 runner 入口。
  - 真实 Kafka consumer、DeerFlow Lead Agent middleware、API router、Web/TUI 操作入口都不能直接 insert `soc_approval_requests`，也不能绕过 `SocAgentApprovalService` 自行构造 request/grant 状态流。
  - `soc_approval_requests` 表必须保存扁平索引字段和完整 `request_payload`；索引至少覆盖 `permission_decision_id`、`route`、`action`、`risk_level`、`status`、`requested_by_actor_id`、`created_at`，终态还要保存 resolution time/actor/reason/idempotency 和可选 grant reference。
  - request lifecycle 只允许 `pending -> approved|rejected|expired`。Pending 不能携带 resolution 字段；terminal request 必须携带处理时间、处理人、理由和 idempotency key；rejected/expired 不能引用 grant。
  - request creation 必须是 insert-only。状态转换只能调用 repository `resolve_approval_request(expected_status=pending)`；禁止重新引入 generic save/upsert request 入口绕过状态机。
  - approve API/service command 只接受 `approval_request_id`、reason 和 grant expiry。Service 必须从 repository 加载原 pending request；客户端提交的 route/action/payload/requested_by 不能参与 grant 构造。
  - approve 必须在同一 repository transaction 内把 request 转为 approved 并插入 grant；`soc_approval_grants.approval_request_id` 必须唯一，保证一个 request 最多产生一个 grant。只有 `soc_approver` 或 `soc_admin` role 可以 approve/reject/expire。
  - resolution 必须携带 idempotency key。完全相同的终态命令可返回既有 request/grant；不同 actor、reason、expiry、idempotency key 或目标终态必须返回 conflict，不能覆盖既有终态。
  - `SocAgentApprovalGrant.execution_token_id` 是一次性执行授权标识，不是 action result；生成 grant 仍不得执行封禁、隔离、MCP 调用等外部副作用。
  - `SocAgentApprovalGrantRepository` 是 approval grant 的持久化边界；必须提供按 request ID 查询，供 exact approve retry 返回同一 grant。
  - SQLAlchemy repository 必须持久化 `SocAgentApprovalGrant` 的 approve/consume 全量 payload，并提供按 `approval_grant_id`、`approval_request_id` 和 `execution_token_id` 查询。
  - `soc_approval_grants` 表必须保存扁平索引字段和完整 `grant_payload`；索引至少覆盖 `execution_token_id`、`approval_request_id`、`permission_decision_id`、`route`、`action`、`risk_level`、`status`、`expires_at`、`consumed_at`、`consume_idempotency_key`、`execution_result_id`。
  - `SocAgentActionCommand` 是 action adapter 的基础执行 contract；必须包含 `route`、`action`、`dry_run` 和 `payload`，供 read-only adapter、dry-run 和 execute preflight 共用。
  - `SocAgentApprovedActionCommand` 是审批后执行入口的显式 contract，继承 `SocAgentActionCommand` 并额外要求 `execution_token_id`；必须显式区分 `dry_run=True/False`。
  - `SocAgentApprovalService.dry_run_approved_action()` 必须先做 token 存在性、过期时间、route/action 一致性校验；没有 action adapter registry 时保持 token-only dry-run 兼容，有 registry 时必须继续调用 registry dry-run 校验 allowlist、payload 和 context refs；不得调用外部工具、不得修改业务状态、不得把 dry-run 结果当作真实处置完成。
  - `SocAgentApprovalService.execute_approved_action()` 是真实执行前的稳定边界：必须要求 `dry_run=False` 和 `idempotency_key`；有 action adapter registry 时，必须在消费 token 前完成 adapter execute preflight；preflight 成功后才能消费一次性 token，并把 `consumed_at`、`consumed_by`、`consume_idempotency_key`、`execution_result_id`、`execution_result_payload` 写回 grant。
  - `execute_approved_action()` 当前只做 execute preflight、消费 token 和记录 execution boundary audit，不调用外部工具、不封禁 IP、不隔离终端、不改生产系统；真实外部副作用只能在后续 action adapter execute 接入后打开。
  - 已消费 token 遇到相同 `idempotency_key` 必须返回原 `execution_result_payload`；不同 key 或缺少记录必须拒绝，避免重复执行。
  - `SocAgentActionAdapterDescriptor` 是真实 action adapter 的能力声明，必须固定 `adapter_id`、`route`、`action`、`risk_level`、`adapter_kind`、`external_side_effect`、`dry_run_supported`、`execute_supported`、`required_payload_fields` 和 `required_context_refs`。
  - `SocActionAdapterRegistry` 是 approved action 后续接 EDR/F5/SOAR/MCP 的唯一 allowlist；只能精确匹配 `route/action`，没有注册 adapter 时必须 fail-fast，不能 fallback 到自然语言、模糊匹配或任意 MCP tool。
  - `SocActionAdapter` 具体实现只能暴露 `dry_run()` / `execute()`；真实 SDK、HTTP client、MCP client 类型只能留在 adapter module，不能扩散到 core、Gateway、TUI、Web 或 contracts。
  - `DryRunOnlySocActionAdapter` 只能用于规划、测试和未上线动作；它可以校验 payload/context refs，但 execute 必须返回 failed 且 `external_side_effect=not_executed`。
  - `SocAgentApprovalService` 接 registry 时，必须先完成 approval grant 校验，再进行 adapter dry-run 或 execute preflight；payload 可以合并 approval request 的 `action_payload/context_refs` 和 command payload，其中 command payload 是显式覆盖。execute preflight 必须在消费 token 前完成 adapter 存在性、execute 支持度和参数校验，失败时 grant 必须保持 `approved`，避免 token 被消费后才发现 adapter 不可执行。
  - `asset.lookup` 是第一个具体 read-only adapter action，`risk_level=read_only`、`external_side_effect=read`、`execute_supported=True`，当前实现为 `InMemoryAssetLookupActionAdapter`，只服务本地开发/测试和 contract 验证；生产资产系统必须后续通过独立 adapter/MCP bridge 接入。
  - `asset.lookup` 可以登记为 read-only policy action，但不能默认加入 chat router 白名单；运行态调用只能通过显式 `soc_route=asset.lookup`、显式 `action_payload.asset_key`、显式 router allowlist 和注入的 action adapter registry 打开。
  - `asset.locate` 是 read-only business ownership / BU location action，用于把已提取资产定位到公司、业务组、处置归属或 mock 远程查询结果；它和 `asset.lookup` 一样不能默认加入 chat router 白名单，只能通过显式 proposal、router allowlist 和注入的 MCP-backed action adapter registry 打开。
  - PingAn `asset.locate` provider 只能位于 `soc_agent.integrations.pingan`；`actions/contracts/core/domain/pipeline` 不得 import 该 vendor package。provider 输入必须是已提取的 `asset_key`、类型、可选角色/UM 和 bounded context refs，不能重新执行资产抽取、攻击/受害角色裁决或处置目标选择。
  - D12 PingAn provider 保留经审阅的 ZEUS `/public/searchAssetInfo` 请求体与 `isec_sign(data, app_id, app_key)` 鉴权边界，以及 `searchAssetInfo -> asset_to_bu -> UM` 降级语义。Portable signer 固定为 PingAn-owned `soc_agent.integrations.pingan.zeus_signing:isec_sign`，不得恢复旧模块的默认 App Key 或 import 整个 `util.util_tools`；endpoint、secret、workflow runner/ID、operator 和 tenant ownership override 只能来自显式环境/配置。真实值可写入已验证 Git-ignored 且权限受限的 `*.local` 文件，不能进入通用 Runtime 或 commit。
  - D12-A `fake` 和 D12-B `internal` 模式必须互斥。fake transport/result 必须声明 `mocked=true`、`provider_mode=fake`、`decision_impact=none`；internal 配置缺失或 import/provider 失败必须 fail closed，禁止静默回退 fake。只有真实 `mocked=false` smoke 才能作为 PA-12/PI-01 real-provider 证据。
  - 多个有效资产归属必须返回 bounded candidates 与 `ambiguous=true`，不能默认选择第一条；原始 provider response、签名 header 和 secret 不得进入 `SocAgentActionResult`、`InvestigationEvidence` 或 smoke 报告。每个 attempt 至少记录 stage、lookup kind、`found|not_found|failed`、candidate count、mock provenance、sanitized error 和 `duration_ms`。只有明确 `not_found` 才可进入下一层；authentication/network/timeout/schema failure 必须立即 fail closed，不能伪装成查无。provider 不得直接修改 verdict、ReviewQueue、memory 或 action authority。
  - PingAn `threat_intel.ip_reputation.lookup` provider 只能位于 `soc_agent.integrations.pingan`；generic contracts/core/domain 只消费 `SocThreatIntelReputationRecord` 和 typed MCP result。内部模式必须复用 portable `isec_sign`、共享 ZEUS credential、HTTPS 和显式 host allowlist，配置/HTTP/timeout/schema failure 必须与正常 not-found 分离且禁止 fake fallback。
  - `/public/indicatorSearch` 只允许投影已审阅的 `ipAnalyseReport` / `ipReputationReport` 标签、scene/carrier/location 和 update time。每个 label 保留 exact source path；完整响应只保留 hash，未审阅字段只能暴露 bounded 字段名 warning，不能把值传给 LLM。旧硬编码风险公式、地理乘数、白名单和封禁规则不得迁移；没有稳定字段契约时 `score`、`confidence`、`last_seen` 必须留空。
  - TI freshness 由显式 tenant 配置和 provider update time计算；无法解析时间时返回 `unknown` 并按 stale-like evidence 处理，不能默认新鲜。所有结果固定 `evidence_boundary=investigation_only`、`decision_impact=none`、`automation_eligible=false`、`raw_response_included=false`。
  - PingAn `security_tag.lookup` provider 只能位于 `soc_agent.integrations.pingan`；generic contracts/core/domain 只消费 `SocSecurityTagRecord` 和 typed MCP result。内部模式复用 portable `isec_sign`、共享 ZEUS credential、HTTPS 与显式 host allowlist，配置/HTTP/timeout/schema failure 必须与正常 `not_found` 分离且禁止 fake fallback。
  - `/public/searchTagContent` 只允许投影审阅过的 `tagValue`、`tagType`、`tagCode`、`isValid`、`expireTime` 和 labels；完整响应只保留 SHA-256，未审阅字段只能暴露 bounded 字段名 warning。响应 hash 是 observation provenance，不得冒充 provider business version；没有稳定 update/version 字段时 `provider_version=null`、`source_freshness=unknown`。
  - security-tag validity 必须区分 `active/expired/inactive/conflicted/unknown/out_of_scope/unusable/not_found`。过期、失效和矛盾记录不得预过滤成查无；缺失/非法 `expireTime` 默认 `unknown`，只有经 source owner 审阅并显式配置后才能接受 open-ended validity。任一结果固定 `decision_impact=none`、`authorization_fact_created=false`、`automation_eligible=false`；它不能直接判安全、关单、授权动作或替代 PI-01B2 governed fact source。
  - SOC Runtime 不提供 `endpoint.process_tree.lookup` 或 `host.event_context.lookup`。进程树、父子进程、命令行、登录上下文和主机事件只能来自告警自身经过 normalizer/bounded evidence 处理后的原生证据；不存在外部 Provider 不能被建模为缺工具降级，更不能用 mock 结果补齐。
  - SOC Lead Agent 可以用 `<soc_action_proposal>...</soc_action_proposal>` 提出 `asset.lookup` / `asset.locate` 这类 read-only proposal；`SocLeadAgentActionProposalBoundary` 只能在注入 read-only router/dispatcher 时把它转成同一条 router/policy/dispatcher/registry 链路。
  - SOC Lead Agent 不得提出不存在的进程树或主机上下文查询。只有显式注册且经过租户配置治理的 `asset.locate`、`threat_intel.ip_reputation.lookup`、`security_tag.lookup` 等真实/待替换边界才能形成 read-only proposal。
  - `soc-asset-extraction` skill 只负责资产抽取、角色标注、disposal target 建议和 `asset.lookup` / `asset.locate` proposal 生成约束；skill 不得直接查询 Zeus/CMDB/EDR/SOAR，不得宣称 company code、BU、owner 或处置结果。
  - Lead Agent 不得直接调用 adapter、MCP 或资产系统；普通自然语言、Markdown 建议、模型自称“已查询”都不能触发 read-only lookup。
  - MCP-backed SOC action 必须实现为 `SocActionAdapter`，并先注册 `SocAgentActionAdapterDescriptor`；SOC route/action 到 MCP server/tool 的映射只能存在于 adapter/config 层，不能暴露给 Lead Agent 作为自由 tool 选择。
  - SOC MCP bridge 可以复用 DeerFlow `get_cached_mcp_tools()` / MCP session cache，但真实 LangChain/MCP tool 类型只能出现在 adapter module；`core/service.py`、Gateway、TUI、Web、contracts 不得 import MCP SDK 或 DeerFlow MCP cache。
  - `tool_search` 适合 DeerFlow 通用 agent 的 deferred tool discovery，不是 SOC action execution boundary；生产 SOC action 不允许由 Lead Agent 直接通过 `tool_search` 找到并调用任意 MCP tool。
  - `backend/soc_agent/actions/mcp.py` 是当前 SOC MCP adapter skeleton 边界；根目录不保留 `backend/soc_agent/mcp_adapters.py` 兼容入口。`SocMcpToolProviderPort` 只暴露 `list_tools()` / `invoke()`，`SocMcpToolActionAdapter` 先只支持 `read_only + external_side_effect=read`，provider exception 必须映射为 `SocAgentActionResult(status="failed")`，dry-run 不得调用 provider `invoke()`。
  - `SocMcpActionAdapterConfig` / `SocMcpToolBindingConfig` 是当前 MCP-backed read-only adapter 的显式配置边界：SOC action 字段和 `mcp.server/tool/timeout/input_mapping/output_fields` 必须由配置映射到 adapter registry，不能由 Lead Agent、dispatcher 或自然语言推断。
  - `mcp.server` 是 MCP-backed action 的执行路由绑定，不只是展示字段；adapter execute / smoke execute 必须把显式 server 传给 provider。只有配置没有提供 server 时，才允许 provider 在受控内部路径里按最长 server 前缀做兼容性推断。
  - `build_mcp_action_adapter_registry()` 只能注册 `enabled=true` 的配置；重复 `route/action` 必须 fail-fast；当前 config builder 只接受 `risk_level=read_only`、`external_side_effect=read`、`execute_supported=true`，write/destructive MCP 另走后续 high-risk preflight 设计。
  - `load_mcp_action_adapter_configs()` 是本地显式 MCP adapter allowlist 加载入口，只接受 `.json/.yaml/.yml` 且顶层为 list 或 `adapters: [...]`；不允许目录扫描、隐式发现、自然语言推断或从 Lead Agent profile 读取 MCP 绑定。
  - `build_mcp_action_adapter_registry_from_file()` 只能用于显式 smoke/dev/staging wiring；chat TUI、daemon 或 production runtime 默认不得自动加载本地 MCP adapter config，除非后续有独立配置治理和启动参数。
  - `DeerFlowCachedMcpToolProvider` 是唯一允许复用 DeerFlow `get_cached_mcp_tools()` 的 SOC provider 实现；它必须把 LangChain `BaseTool` 归一为 `SocMcpToolDescriptor` / `Mapping`，不能把 BaseTool、ToolMessage、content block 或 MCP SDK 类型传出 `actions/mcp.py`。
  - `DeerFlowCachedMcpToolProvider.invoke()` 必须按 exact tool name 调用，不做 fuzzy match；tool 缺失、cache 加载失败、调用失败、timeout 都必须转成 `SocMcpToolProviderError` / `SocMcpToolNotFoundError`，再由 adapter 映射为可审计 action result。
  - `DeerFlowCachedMcpToolProvider` 的 smoke execute 路径可以使用 one-shot MCP session 调用已 allowlist 的 read-only tool，避免 DeerFlow stdio session wrapper 为文件/浏览器类 MCP 做 workspace snapshot 时阻塞 SOC 只读数据查询；该 one-shot 路径仍必须先通过 DeerFlow cached MCP inventory 验证 exact tool 可见，且不能跳过 `SocMcpActionAdapterConfig` / `mcp.server` 显式路由绑定。
  - MCP tool 返回的 `structuredContent`、LangChain content/artifact、model dump 或普通 dict 都必须在 `actions/mcp.py` 内归一化；adapter 的 `output_fields` 是最终进入 `SocAgentActionResult.payload.mcp_result` 的字段裁剪边界。
  - MCP result 的 `isError=true` / `is_error=true` 必须在 `output_fields` 裁剪前映射为 `SocMcpToolProviderError` 和 failed action；不得因错误字段未在 allowlist 中而把 `{}` 当作成功结果。
  - `InvestigationEvidence.result_payload` 可来自 direct adapter，也可包含 MCP adapter 的 typed `mcp_result` envelope。通用 domain/evidence 消费者必须通过同一 `evidence_result_payload()` 解包；不得因 MCP envelope 忽略已持久化的真实 evidence，也不得为某个 vendor 单独解析。
  - `soc mcp smoke CONFIG --route ... --json ...` 是 dev/staging read-only MCP path 的显式 smoke 入口；默认使用 `DeerFlowCachedMcpToolProvider`，`--dry-run` 只验证 adapter/tool availability，execute smoke 输出 `SocMcpActionSmokeReport`。该命令不是生产 daemon，也不是 Lead Agent 自主 tool runtime。
  - `SocMcpActionSmokeReport` 是 dev/staging smoke 的版本化报告 contract，必须记录 `duration_ms`、payload/result byte size、adapter/tool/config metadata、`output_fields` 裁剪状态、`mcp_result_keys`、失败类型和内嵌 `SocAgentActionResult`；config/load/registry/tool failure 也应输出结构化 report，方便脚本归档和接入评估。
  - `soc mcp tools` 是 read-only MCP smoke 的前置 readiness 命令，只允许列出 DeerFlow cached MCP tool inventory，默认不输出 input schema；`--include-schema` 才输出 schema，`--report-path` 可落盘。它不得调用 MCP tool，也不得输出 secret。
  - `soc mcp smoke --report-path` 和 `soc mcp tools --report-path` 只能写调用者显式指定的报告文件；报告文件可能包含业务 payload/result，默认不应提交到 git。
  - `backend/scripts/soc_dev_mcp_server.py` 和 `backend/samples/mcp/soc_dev_*.json` 只用于本地真实 stdio MCP smoke，不是生产配置。样例 `extensions_config` 使用 `$SOC_DEV_MCP_PYTHON` / `$SOC_DEV_MCP_SERVER` 环境变量传绝对路径，避免 DeerFlow stdio tool 执行时切换 cwd 后找不到相对路径。
  - `backend/scripts/soc_dev_mcp_server.py` 当前提供两个本地 mock read-only tools：`asset_lookup` 返回静态资产记录，`asset_locate` 模拟 Zeus/CMDB/asset_to_bu 远程归属定位并返回 `mocked=true`；这两个工具只能用于开发 smoke 和 proposal bridge 验证。
  - `soc chat tui --lead-agent --mcp-action-config PATH` 是显式 dev/staging 注入入口；不传配置时保持本地 in-memory read-only adapter，不得隐式扫描或自动启用任意 MCP action config。
  - `soc chat tui --lead-agent` 当前使用 SOC repository 写入 read-only action evidence；不传 `--mcp-action-config` 时本地 registry 只包含 `asset.lookup`、`threat_intel.ip_reputation.lookup` 和 `security_tag.lookup` mock adapter；`InMemoryInvestigationEvidenceRepository` 只用于单元测试和无数据库的局部 service wiring。
  - PingAn 内网 DEV 的 SOC 业务库固定使用独立本地 SQLite。`resolve_database_url()` 在没有显式参数和 `SOC_DATABASE_URL` 时，若 DeerFlow 为 `database.backend: sqlite`，必须自动使用 `{database.sqlite_dir}/soc_agent_dev.db`，不能复用 `deerflow.db`；`soc db upgrade` 必须创建缺失的 SQLite 父目录。当前不收集 PostgreSQL、Kafka 或 K8s DEV 参数，也不能用 SQLite 结果声明准生产/生产基础设施已验收。生产/准生产 PostgreSQL 目标保持不变。
  - PingAn Host DEV 重部署只替换代码和 release-owned private overlay；安装器必须在全部进程停止后，把 allowlist 内的 `backend/.deer-flow/data`、JWT、用户/Agent/线程状态、受管集成与内网验收证据从 rollback checkout 复制到新 checkout。整个 SQLite data 目录必须连同 `-wal`/`-shm`/`-journal` sidecar 一起保存，任何恢复失败都回滚旧 checkout。`pingan-context`、配置和凭证由新 private overlay 覆盖；PID、日志、技能投影、临时缓存和旧交付包不得继承。常规安装/升级不得删除、清空或隐式重建已有 `deerflow.db` / `soc_agent_dev.db`；首次初始化残库也必须先隔离备份。SOC schema 只能通过 `soc_alembic_version` 验证，不能查询 DeerFlow 的 `alembic_version`。
  - 当前 `PI-01A` 的 PingAn TI Provider/MCP 已完成 production-shaped code 与 fake/persistence 回归；真实 DEV `mocked=false` hit/not-found/error/timeout、实际字段 coverage 和 evidence readback 仍是退出门槛。`D12-B` 按产品决定暂存，但其资产 Provider 门槛没有关闭。
  - `PI-01D4` reporting contract 已完成。`SocInvestigationReportingService` 只能从
    `SocEnrichmentExecutionRepository`、`SocEnrichmentActionAttempt` 和
    `InvestigationEvidenceRepository` 读取一个一致快照；不得调用 Provider、Dispatcher、Planner、LLM，
    不得修改 execution、base run、ReviewQueue、memory、approval 或 action state，也不得新建第二张报告
    真值表。
  - `soc.investigation_shadow_report.v1` 只能公开 secret-free plan/action/result、retry/provider-call、
    mock/real、evidence coverage 和 action-attempt latency 聚合。未采集的 Provider 网络耗时、tool cost、
    token/算力 cost 必须明确 `not_measured`，不得填 0 或从日志估算。
  - `soc.investigation_addendum.v1` 是确定性执行摘要，不是新模型结论；必须保持
    `reasoning_status=not_requested`、`new_conclusion_produced=false`、`decision_impact=none` 和
    `projection_persisted=false`。它可以进入 Review/Web/TUI/Lead Agent bounded context，但不得覆盖
    Runtime verdict、自动关单、确认 memory 或授权高风险动作。
  - reporting service 必须核验 attempt 的 evidence reference 与 execution 的
    run/alert/thread/route/action/plan-action 完全一致；evidence content hash 必须进入 projection source
    hash。报告与 addendum 应从同一 snapshot 成对生成，避免跨读取状态不一致。CLI 操作入口为
    `soc investigation report EXECUTION_ID`；内部 batch 只能聚合同一 contract，不得重新实现统计语义。
  - 当前执行指针为 Real Integration Debt evidence preparation；下一次有批准的内网配置和凭证时，使用 fresh internal-real stage-5 cohort 一并恢复 `D12-B / PI-01A / PI-01B1`。D1-D4 planner/composition/durable workflow/reporting 已完成，外网 5-row 与 50-row paired simulation 均已通过；50 条中的 157 个 fake Provider 结果全部为正常 not-found，因此真实 hit mapping 未被证明。PingAn asset、TI 与 security-tag 仍分别保留真实 DEV hit/not-found/error、对象/字段/有效期语义和 `mocked=false` evidence readback 门槛；paired report 本身固定 `closes_real_provider_gate=false`。PI-01B2/C 因真实 source contract 缺失标为 data-gated；完成 B1 不得冒充 `PI-01B2` 权威授权事实来源。
  - Gateway approved action API 路径固定在 `/api/soc/approvals/*`：
    - `POST /api/soc/approvals/grants`
    - `POST /api/soc/approvals/actions/dry-run`
    - `POST /api/soc/approvals/actions/execute`
    - `POST /api/soc/approvals/requests`
    - `GET /api/soc/approvals/requests`
    - `GET /api/soc/approvals/requests/{approval_request_id}`
    - `POST /api/soc/approvals/requests/{approval_request_id}/reject`
    - `POST /api/soc/approvals/requests/{approval_request_id}/expire`
  - approved action API/TUI/Web 只能调用 `SocAgentApprovalService`，不能直接读写 repository 或绕过 token consume 边界。
  - Web approved action workbench 只能通过 `frontend/src/core/soc/api.ts` 调用 Gateway `/api/soc/approvals/*`；React component 不得直接拼后端 repository/DB 行为，也不得把 dry-run 展示成真实处置完成。
  - Web approval inbox consumption 只能读取 Gateway `/api/soc/approvals/requests*`，并通过 request-ID-only `/api/soc/approvals/grants` 或 reject/expire endpoint 处理 pending request；前端不得提交可变 request JSON 或直接修改 `SocAgentApprovalRequest.status`。
  - `soc review tui` approval inbox consumption 只能调用 `SocAgentApprovalService`：`/approvals` list pending request、`/approval APR-...` get detail、`/approve APR-... reason` create grant、`/reject APR-... reason` 和 `/expire APR-... reason` 进入无 grant 终态。TUI 不能直接访问 repository，不能把 approve 当作执行完成。
  - `BG-P0-01` 完成 request/grant 状态完整性和 L3 authorization；`BG-P0-02` 已让 submit/approve/reject/expire/dry-run/execute 通过 `SocMutationUnitOfWork` 与 `SocMutationAuditRecord` 原子提交。request payload 或 grant execution payload 仍不能替代统一审计链，原始 action payload 不得写入 mutation audit。
  - TUI 本地 MVP approver actor 可以临时使用 `soc-review-tui` + `soc_approver`，但后续接真实用户体系后必须由认证/角色配置提供 approver role。
  - TUI dry-run / execute 必须作为单独命令显式触发：`/dry-run SAT-... route action` 只能调用 `SocAgentApprovalService.dry_run_approved_action()`；`/execute SAT-... route action idempotency-key` 必须提供 idempotency key，并继续走 `SocAgentApprovalService.execute_approved_action()`。
  - TUI execute 只能展示 `SocAgentActionResult` 和 execution boundary 状态；不得把 `external_side_effect=not_executed` 展示成真实封禁/隔离/处置完成。
  - Gateway 入口当前将 DeerFlow `system_role=admin` 映射为 SOC `soc_admin`；后续细粒度 SOC role 体系落地后必须替换为独立授权策略。
  - 未注册 action 默认拒绝，不能因为 route allowed 就执行。
- `soc_agent.tui.chat_runtime` 是纯翻译层：
  - 可以复用 DeerFlow TUI 的 `Action`、`RunStarted`、`RunEnded`、`AssistantDelta`、`SystemMessage`、`reduce()` 语义。
  - 可以把 `custom kind=soc.review_context` / `custom kind=soc.route_decision` / `custom kind=soc.permission_decision` / `custom kind=soc.approval_request` / `custom kind=soc.action_result` 转成可读系统提示。
  - 不能 import repository、normalizer、runtime pipeline、Gateway router 或 Textual app。
  - 不能执行 close/correct/analyze/response action；这些只能由明确命令或 service 调用触发。
- `soc chat tui` 是主 SOC Agent 的 terminal workbench shell：
  - 可以复用 DeerFlow Textual app、`ComposerInput`、`ViewState/reduce()`、`render_transcript()`、`render_status()`。
  - 只能通过 `SocAgentChatService.stream()` 获取内容。
  - `/open REV-...` 只能加载 review context，不能隐式关闭队列或修改 verdict。
  - CLI 构造 repository/service 是入口组装；TUI app 内部不能直接 import repository。
  - 后续 skills/MCP/tool route 必须走 capability router 白名单和 service 层，不能让 TUI 或 LLM 直接调任意工具。

Kafka daemon / consumer adapter 约束：

- Kafka consumer 是后台 ingestion adapter，只能把 broker record 映射成 `SocDaemonMessage`，再调用 `SocDaemonService.process_message()`；不能直接调用 runtime pipeline、repository、normalizer、LLM、approval repository 或 action adapter。
- `KafkaRecord -> SocDaemonMessage` 映射必须先经过 `soc_agent.daemon.kafka_mapper`；unknown topic、invalid JSON、non-object payload、非法 key 等错误不能进入 core service。
- `SocKafkaConsumerRunner` 固定提交语义：
  - 成功：`poll -> map -> process_message -> commit`。
  - mapper failure / service failure：先 `send_dead_letter()`，dead-letter 成功后才 `commit` 原 offset。
  - dead-letter 写失败不得 commit 原 offset，必须暴露异常，避免静默丢消息。
  - bounded loop 必须通过 `SocKafkaConsumerRunner.run()` 聚合结果；CLI/API/daemon wrapper 不应重新实现 poll loop。
  - `KafkaRunnerLoopResult` counters 是后续 metrics/readiness 的最小来源：processed、dead_lettered、idle、committed。
- `KafkaConsumerPort` 是真实 broker client 的唯一 port；真实 `aiokafka` / `confluent-kafka` adapter 只能实现该协议，不能把具体 SDK 类型扩散到 core、pipeline、repository、API、TUI 或 Web。
- 当前真实 broker adapter 选择 `confluent-kafka`：
  - 依赖必须放在 optional extra `backend[kafka]`，普通本地开发和 CI 不强制安装。
  - `confluent_kafka.Consumer`、`Producer`、`TopicPartition` 只能出现在 `soc_agent.daemon.kafka_adapter` 或同层 adapter module。
  - SDK message 必须立即转换为 `KafkaRecord`，不能传入 mapper、runner、core service 或 tests 之外的业务层。
  - consumer error、empty/tombstone value 必须在 adapter 层 fail-fast，不进入 `SocDaemonService`。
  - manual commit 使用 consumed offset + 1；禁止开启 automatic commit。
- `KafkaConsumerSettings` 是 broker adapter 配置 contract：
  - 默认 `enabled=False`，本地和 CI 不要求 Kafka broker。
  - 默认 broker 为 `localhost:9092`，只作为本地 Redpanda/Kafka 约定。
  - 默认 input topics 为 `soc.alerts.raw.v1` 和 `soc.approvals.requests.v1`。
  - 默认 dead-letter topic 为 `soc.alerts.dead_letter.v1`。
  - `security_protocol` 只允许 `PLAINTEXT`、`SSL`、`SASL_PLAINTEXT`、`SASL_SSL`。
  - SASL/TLS secret 只能通过环境变量引用传入，例如 `sasl_password_env`；不得把 secret 写入 notes、config、DB、run payload、step trace 或 dead-letter payload。
- `NullKafkaConsumerPort` 是 disabled-by-default 本地/测试 adapter：
  - `enabled=False` 时 `poll()` 返回 `None`。
  - `enabled=True` 但没有真实 broker adapter 时必须 fail-fast，不能伪装成已经连接 Kafka。
- `soc daemon consume` 是 broker consumer 的 CLI shell：
  - 默认必须有限次 poll，例如 `--max-records 1`，不能在本地/CI 默认长驻阻塞。
  - disabled idle path 不应要求数据库连接；只有真实 broker adapter 启用并可能处理消息时，才需要 repository-backed `SocDaemonService` wiring。
  - enabled path 必须先校验/构造 repository-backed `SocDaemonService`，再构造真实 Kafka client；配置错误不能先触发 broker 连接。
  - 输出必须是结构化 JSON 摘要，不能只写自然语言日志，方便测试和后续 supervisor/readiness 集成。
- `soc daemon run` 是长驻 daemon CLI shell：
  - 长驻控制流必须集中在 `SocKafkaDaemonRunner`，不能在 CLI、Docker entrypoint 或 supervisor 脚本里重新实现 poll loop。
  - `SocKafkaDaemonRunner` 只能调用 `SocKafkaConsumerRunner.process_next()`；不能绕过 runner 直接 poll/commit/dead-letter，也不能直接调用 `SocDaemonService.process_message()`。
  - `--max-loops` 只用于测试、本地验收和 smoke；生产默认不设置 loop cap。
  - idle poll 后必须有可配置 sleep/backoff，避免 broker 空闲时热循环；测试可设为 `--idle-sleep-ms 0`。
  - adapter/runtime error 后必须有可配置 backoff；测试可设为 `--error-backoff-ms 0`，生产不应为 0。
  - 连续错误必须有明确停止策略；默认 `--max-consecutive-errors 3`，`0` 只表示外部 supervisor 接管重试策略。
  - `SIGINT` / `SIGTERM` handler 只能设置 stop flag，不得在 signal handler 内做 DB/Kafka/IO 操作。
  - 停止时必须 close consumer port；异常路径也必须释放 consumer。
  - 输出 schema 固定为 `soc.kafka_daemon_run_result.v1`；默认只输出 counters 和 stop reason，只有显式 `--include-results` 才输出每轮结果。
  - 输出必须包含 `metrics`：`started_at`、`stopped_at`、`error_count`、`consecutive_error_count`、`last_success_at`、`last_error_at`、`last_error_type`、`last_error_message`。
  - daemon controller 只能记录 loop-level error metrics；mapper/service failure 的 dead-letter + commit 语义仍归 `SocKafkaConsumerRunner`。
  - `--metric-jsonl stdout|stderr` 是运行中 metric event sink：
    - 默认关闭，不能改变现有 CLI/smoke stdout summary。
    - event schema 固定为 `soc.kafka_daemon_metric.v1`。
    - event 只允许 `start`、`result`、`error`、`stop`。
    - result event 只能输出 record metadata、status、commit/dead-letter 状态和 daemon_result 摘要；不得输出完整 alert payload、raw_message、secret 或 DB URL。
    - 生产推荐输出到 stderr，由日志系统采集；stdout 保留最终 `soc.kafka_daemon_run_result.v1` summary。
- `backend/scripts/soc_daemon_entrypoint.sh` 是生产 daemon 的稳定 shell entrypoint：
  - 默认要求 `SOC_KAFKA_ENABLED=true`，避免生产容器运行在 null adapter。
  - `SOC_DAEMON_ALLOW_DISABLED=true` 只允许测试/本地验证。
  - 可选 `SOC_DAEMON_UPGRADE_DB=true` 只能作为便利模式；生产更推荐独立 migration job。
  - 可选 `SOC_DAEMON_PRESTART_STATUS_CHECK=true` 可以在启动前调用 healthcheck。
  - `SOC_DAEMON_METRIC_JSONL=stdout|stderr` 可以打开同一套 metric sink。
  - entrypoint 只能组装 CLI 参数和执行 preflight，不得实现业务逻辑、poll loop、offset commit 或 dead-letter。
- `backend/scripts/soc_daemon_healthcheck.sh` 是生产 daemon 的稳定 readiness shell：
  - 默认执行 `soc daemon status --check-broker`。
  - healthcheck 不处理业务消息、不提交 offset、不写 dead-letter、不写业务 DB。
  - `SOC_DAEMON_HEALTHCHECK_DATABASE=false` 只用于配置排障，不应作为生产 readiness。
  - Docker/K8s healthcheck 应调用该脚本，而不是自己拼 readiness 逻辑。
- SOC daemon 不直接进入 DeerFlow 主 docker-compose 默认服务；它是业务扩展进程，应通过独立 overlay、生产 compose 或 K8s deployment 模板接入。
- `docker/docker-compose.soc-daemon.yaml` 是 SOC daemon 的显式 opt-in compose overlay：
  - 不得被默认 `scripts/docker.sh` / `make docker-start` 自动加载。
  - service 必须使用 `backend/scripts/soc_daemon_entrypoint.sh`。
  - healthcheck 必须使用 `backend/scripts/soc_daemon_healthcheck.sh`。
  - 默认 metric sink 应使用 `SOC_DAEMON_METRIC_JSONL=stderr`。
  - overlay 不能保存生产 secret；Kafka/DB secret 必须来自 env file、secret manager 或部署平台。
  - 默认 build extra 是 `postgres,kafka`；`backend/Dockerfile` 必须把 comma/whitespace 分隔的 `UV_EXTRAS` 展开成多个 `--extra` flag。
  - 本地 SQLite + Kafka 验证可以显式设置 `SOC_DAEMON_UV_EXTRAS=kafka`。
- `docker/k8s/soc-daemon.yaml` 是 SOC daemon 的显式 opt-in K8s 示例模板：
  - 不得被默认部署脚本自动加载。
  - 只能作为 deployment contract 示例，应用前必须替换 image、namespace、broker、topic、Secret 和资源限制。
  - `ConfigMap` 只能保存非敏感配置；`SOC_DATABASE_URL`、Kafka password、CA 等必须来自 `Secret` 或 secret volume。
  - container command 必须使用 `backend/scripts/soc_daemon_entrypoint.sh`。
  - readiness/liveness probes 必须调用 `backend/scripts/soc_daemon_healthcheck.sh`。
  - 不应创建 Service；当前 daemon 无 HTTP listener，metric 最小面是 stderr JSONL。
  - 必须显式设置 resource requests/limits，避免后台消费进程无边界占用资源。
- `soc daemon status` 是 Kafka daemon readiness/status contract：
  - 输出 schema 固定为 `soc.kafka_daemon_status.v1`，供 CLI、supervisor、Docker/K8s readiness 和人工验收复用。
  - 默认只检查 database readiness 和 Kafka adapter 配置状态；不能 poll 或处理业务消息。
  - broker 连通性检查必须显式传 `--check-broker`，只允许做轻量 `poll()`；不能调用 `SocDaemonService.process_message()`、不能 commit offset、不能写 dead-letter、不能写业务 DB。
  - `--skip-database-check` 只允许用于配置检查或本地排障；生产 readiness 不应跳过 DB 检查。
  - status 输出中的 database URL 必须 redacted，不得泄露 password、SASL secret 或 TLS secret。
  - exit code 必须和 `ready` 对齐：ready 返回 `0`，unready 返回非零。
- `SocOperationsService` 是跨 Runtime/Review/Approval/Normalization/Memory/Kafka 的只读运营聚合边界：
  - contract 固定为 `soc.operations_snapshot.v1`；CLI 使用 `soc ops snapshot`，Gateway 使用
    `GET /api/soc/operations/snapshot`，入口不得自行查询多张表或拼接近似计数。
  - persisted counters 必须使用无分页上限的 SQL aggregate；禁止从 `list(limit=...)`、Web 当前页或
    daemon 进程内计数推断 lifetime backlog。
  - Gateway snapshot 必须是 passive read，不得 poll Kafka、处理 message、commit offset、写 DLQ 或改
    业务状态。只有 CLI 显式 `--check-broker` 时才允许复用轻量 broker checker。
  - 输出只允许 database backend、配置项数量、可用性和稳定 error code；database URL、broker address、
    username、credential、raw exception/diagnostic 不得进入 contract、API 或 CLI JSON。
  - snapshot 不输出总体 `healthy` 分数。未采集的 consumer lag、模型/算力和 production SLO 必须标记
    `not_measured`，不能用 `0`、`true` 或默认阈值冒充正常。
  - PI-04-A 不改变 Runtime、Kafka consumer、ReviewQueue、approval 或 memory 主流程；完整 Web、
    Prometheus、SLO threshold/alerting 属于后续 PI-04 切片。
  - PI-04-B Web 固定为 `core/soc` typed API/hook 的薄消费者：不得直接查表、主动 broker probe、复制
    aggregate 或推导 overall health；必须原样区分 `available|unavailable|not_configured|not_measured`。
    SQLite/Playwright 只能标为 local/test evidence，不能关闭 deployed Gateway/auth、真实 lag/算力、
    Prometheus 或 production SLO gate。
- PI-05A rollout rehearsal 是 vendor-neutral、纯内存、零副作用的控制流验证：
  - contracts 固定在 `soc_agent.contracts.rollout`，入口必须经 `SocRolloutRehearsalService`；CLI 只负责
    读取 `soc.rollout_rehearsal_request.v1`、可选 baseline 和输出报告，不能自行修改 gate 或 stage。
  - V1 plan 必须完整包含 5 类 owner、7 个不可削弱 stage scope 的 real gate、bounded cohort/feature-flag
    描述和有序 6 步 rollback；simulation owner 不能声明 real confirmation，simulation evidence 不能把
    real gate 标为 `passed`。
  - `soc.rollout_rehearsal_report.v1` 的 engineering pass 只证明虚拟
    `shadow -> limited_pilot -> controlled_rollout -> rollback` 可复跑；必须固定
    `current_real_stage=not_started`、`real_stage_transition_count=0`、`external_effect_count=0`，并保持
    production approval、auto-close、external mutation、high-risk action 和 real-rollout claim 全部 false。
  - service 不得调用 Provider、Kafka、repository mutation、feature-flag service、Zeus 或 response adapter。
    相同 semantic input 必须产生稳定 ID/hash 和 replay diff；真实 rollout controller/cohort enforcement
    只能在 PI-05C 以 fresh evidence、独立 owner approval、审计和可执行回滚另行实现。
- PI-05B simulation completion 是 `soc_agent.eval.completion` 下的离线只读聚合边界：
  - `soc.simulation_completion_request.v1` 必须显式列出 PI-01E、PI-03B、PI-03C ingest/replay、PI-04 和
    PI-05A artifact；路径只用于本地读取，不得进入输出报告。CLI 入口是 `soc rollout completion`。
  - 输出 `soc.simulation_completion_report.v1` 必须恰有五个 component，保存 artifact schema/id/SHA-256、
    stable semantic hash、逐项 check 与限制；缺失、坏 JSON/typed contract 或仿真越权 claim 必须形成 failed
    component 和非零 CLI 退出，不得用手工说明替代。
  - completion pass 只能表示产品仿真轨完整可复跑。报告必须固定 `mocked=true`、real stage
    `not_started`、real transition/effect=0、Pilot/Production/real rollout/auto-close/external mutation/high-risk
    action 全部 false，并逐项保留全部 7 个 `SocRolloutGateId` 为 open Real Integration Debt。
  - stable replay 的语义 identity 忽略生成时间；原始 artifact byte hash 单独保留并在 diff 中独立报告。
    evaluator 不得调用 Runtime、LLM、Provider、broker、数据库 mutation、feature flag、Zeus 或 action。
- `SocDaemonMessage` 的 Kafka metadata 必须保留 `topic`、`partition`、`offset`、`key`；daemon idempotency key 固定为 `kafka:{topic}:{partition}:{offset}`。
- dead-letter payload 必须使用 `soc.kafka_dead_letter.v1`，至少包含 failed_at、topic、partition、offset、key、headers、value、error_type、error_message；payload 不得包含 secret。
- 真实 consumer CLI/daemon 入口只能做配置读取、adapter 构造、runner loop 和 graceful shutdown；业务处理仍归 `SocDaemonService`。
- 后续并发、重试阈值、lag metrics、readiness 和 worker pool 只能在 runner/adapter 层扩展，不得改变 core service 的单条 message contract。
- Kafka worker pool / concurrency 约束：
  - 默认生产安全模式必须保持 `worker_concurrency=1`，等价当前串行 runner。
  - `PartitionCommitTracker` 是后续并发 controller 的 commit 推进原语；它只能做内存状态计算，不能 poll、commit、dead-letter 或调用 core service。
  - Kafka consumer poll/commit/pause/resume ownership 必须留在 poller/controller；worker 不得直接调用 Kafka consumer。
  - worker 只能调用 `SocDaemonService.process_message()` 或后续等价 core service，不得直接写 repository、commit offset 或写 dead-letter。
  - `KafkaWorkerResult` 是 worker -> controller 的唯一返回 contract；允许状态只有 `processed`、`dead_letter_required`、`retryable_error`、`fatal_error`。
  - `KafkaWorkerResult` 不得包含 `committed`、`dead_lettered` 或任何 broker side-effect 状态；这些只能由 poller/controller 在 dead-letter/commit 实际完成后记录。
  - `SocKafkaWorker` 可以被当前串行 `SocKafkaConsumerRunner` 复用，但 runner/controller 仍是 commit 和 dead-letter owner。
  - offset commit 必须 partition-aware，只能推进到同一 partition 已连续完成的最大 offset + 1。
  - mapper/service failure 必须先 dead-letter 成功，再把该 offset 标记为 completed；dead-letter 失败时不得 commit 或越过该 offset。
  - 并发前必须补幂等写入边界，确保同一 `kafka:{topic}:{partition}:{offset}` 重放不会重复污染 summary、approval inbox、audit 或 memory。
  - `SocAnalysisService` 的 idempotency hardening 以 `DecisionAuditRecord.payload["idempotency_key"]` 和 `soc_decision_audit_log.idempotency_key` 为索引；同 key、同 action 命中已完成或不可重试的既有 audit/run 时必须复用旧 run。仅当既有 run 明确 `failed && failure.retryable=true` 时允许同 key 重新执行。
  - `soc_decision_audit_log.idempotency_key` 是请求幂等索引字段，不保存 secret，不替代 Kafka metadata；Kafka metadata 仍来自 `SocDaemonMessage`。
  - worker pool 必须 bounded；必须有 max in-flight、queue depth、shutdown timeout 和 backpressure 语义。
  - Kafka worker concurrency 不等于 LLM concurrency；LLM analyzer 必须有独立限流。

Investigation context 约束：

- `InvestigationContext` 是分析师打开 review queue item 时的只读上下文，不产生新判断，也不修改 run/summary/audit。
- `SocReviewService.get_investigation_context(queue_id)` 是 API/TUI/Web/CLI 打开复核详情的统一 service 入口。
- context 至少包含 `queue_item` 和完整 `AnalysisRun`；如果注入了 summary/audit repository，则同时返回 `AlertSummary` 和 `DecisionAuditRecord[]`。
- context 中的 `similar_alerts` 必须来自 `AlertSummaryRepository.find_similar_alert_summaries()`，不能让入口层或 LLM 直接全库检索。
- context 中的 `memory_candidates` 必须来自 `MemoryCandidateRepository.list_memory_candidates(queue_id/run_id/alert_id)`，只能包含当前 review item 相关候选；入口层、前端和 Lead Agent 不能直接全表查询后自行关联。
- Lead Agent bounded context 必须明确标记 `memory_candidates` 为 reviewable proposals only；不能把它们当作 confirmed facts、active lessons 或处置依据。
- 入口层不能自己分别查 queue/run/summary/audit 再拼响应，避免 Web/TUI/CLI 对“详情页上下文”理解不一致。
- 后续相似告警、confirmed facts、lessons、threat intel 都应作为 context 的增量字段接入，不能绕过 service 直接塞进 prompt。

Similar alert 约束：

- `SimilarAlertQuery` 从当前 `AlertSummary` 派生，查询字段优先使用 `detection_key`、`rule_code`、`source_type`、`category`、`entity_keys`。
- `SimilarAlertMatch` 必须包含匹配到的 `AlertSummary`、数值 `score` 和结构化 `matched_reasons`，便于分析师和后续 LLM rerank 解释。
- 当前实现允许 repository 先用 SQL 读取最近候选窗口，再用 Python 规则打分；正式 PostgreSQL 优化时可在同一协议下改成 JSONB/GIN 实体交集查询。
- 相似查询必须排除当前 `run_id`，并受 `limit` / `candidate_limit` 限制，避免把全库塞进上下文。
- LLM 后续只允许对 `SimilarAlertMatch[]` 候选集合进行排序、解释或提出补查建议，不直接决定数据库检索范围。

SOC repository 实现约束：

- SOC 业务表放在 `backend/soc_agent/db/`，不塞进 DeerFlow harness persistence。
- repository 可以依赖 SQLAlchemy 和 `soc_agent.contracts`，不能 import `soc_agent.core`、`pipeline`、CLI/API/TUI/ingestion。
- `soc_analysis_runs.run_payload` 保存完整 `AnalysisRun`，索引列只服务查询和筛选，不作为唯一事实来源。
- SOC schema migrations 放在 `backend/soc_agent/db/migrations/`，使用独立版本表 `soc_alembic_version`。
- 正式 schema 变更走 `soc db upgrade` / Alembic revision；`create_soc_tables()` 和 `soc db init` 只作为本地开发、测试和验收辅助。
- SOC 当前持久化表包括 `soc_analysis_runs`、`soc_decision_audit_log`、`soc_alert_summaries`、
  `soc_review_queue`、`soc_approval_requests`、`soc_approval_grants`、`soc_investigation_evidence`、
  `soc_external_dispositions`、`soc_memory_candidates`、`soc_memory_records`、normalization baseline/issues、
  `soc_governed_context_facts`、`soc_authorization_enrichments`、`soc_disposition_proposals`、
  `soc_enrichment_executions` 和 `soc_enrichment_action_attempts`。
- 单元测试可以用 SQLite in-memory 验证 SQLAlchemy 映射。
- 本地开发/人工验收默认跟随 DeerFlow `database.backend: sqlite`，自动使用独立的 `{database.sqlite_dir}/soc_agent_dev.db`；显式 `--database-url` / `SOC_DATABASE_URL` 仅用于隔离测试文件或覆盖默认路径。
- 准生产、生产和长期联调环境必须指向 PostgreSQL；不得把本地 SQLite 例外扩大成生产架构。

### 三类模型必须分清

| 类型 | 用途 | 示例 | 约束 |
|---|---|---|---|
| Contract Model | 跨 API/Kafka/CLI/LLM/Tool 边界 | `AlertInput`, `AnalysisResult`, API response | Pydantic v2，版本化，严格校验 |
| Domain Model | 内部稳定业务对象 | `AlertSummary`, `MemoryFact`, `LessonRule` | 不被外部协议字段名污染 |
| Persistence Model | DB row / migration / repository DTO | `alert_summaries` row | 不直接暴露给 API 或 pipeline |

禁止：

- API 直接返回 DB row。
- pipeline 直接消费 Kafka message。
- DB 直接存未校验的 LLM 输出。
- LLM 输出绕过 `AnalysisResult` / `MemoryCandidate` 直接影响决策或 memory。

### Normalizer 约束

外部输入流程固定为：

```text
flat/vendor payload
      ↓
normalizers/
      ↓
canonical contract model
      ↓
runtime / pipeline / DB / memory / API response
```

`AlertInput` 不负责兼容所有厂商字段。平安、F5、EDR、NIDS、HIDS、云安全等 source-specific mapping 应放在：

```text
normalizers/pingan.py
normalizers/f5.py
normalizers/edr.py
normalizers/nids.py
normalizers/hids.py
```

字段别名约束：

- 原始字段别名、大小写差异、header 命名差异必须在 `normalizers/` 层归一化，例如 `x-forwarded-for`、`X-Forwarded-For`、`xForwardedFor` -> `entities.http.x_forwarded_for`。
- `pipeline/extractor.py` 只读取 canonical `AlertInput` 字段，不直接识别厂商原始字段名、HTTP header 原名或平台私有字段名。
- 如果 extractor 需要新增实体来源，先确认 canonical schema 是否已有字段；没有字段时先扩展 contract/normalizer，再提取实体。

#### PingAn EDR nested-detail contract

- `backend/soc_agent/normalizers/pingan_edr.py` owns PingAn EDR aliases and the historical
  `process_mame` typo. Generic Runtime、fact reconstruction 和 extractor 禁止识别这些名字。
- `detailsN` 必须按数字下标排序。Adapter 可以选择第一条有效 detail 形成单值 canonical
  process/file 摘要，但每条可用 detail 都必须形成带精确 `evidence_path` 的
  `ProcessObservationRef`；child process 作为同一 observation 的独立 node，不能覆盖父进程。
- Nested endpoint file action 的 `file_name/file_path` 可形成 `FileObservationRef`；平铺 EDR 的
  `str_suspicious_file` 映射为 `endpoint_action_target`。多态 `str_ioc_value` 只有在 Adapter 确认其为
  absolute file-shaped value 时才可另行投影为带原字段 provenance 的 `observed_artifact`，不得覆盖
  process image/action target，也不得把 IP、hash 或其他 IOC 伪造成 file。registry/task 字段是
  reasoning context，不得伪装成 file entity；file target、IOC artifact、`is_exist` 和 child process
  observation 均不自动证明恶意或执行成功。
- `iplist` 是 endpoint/impacted-host 证据，只能形成 host IP 与 provisional
  `victim`/`impacted_asset` claims；不得由此生成 network `source`/`destination` 或 attacker。
- `process_md5` 仅接受 32 位十六进制，`process_sha256` 仅接受 64 位十六进制。非法值保留在
  parsed/bounded evidence，并输出 `invalid_process_hash` semantic，但不得进入 canonical entity、
  extracted hash mention 或 provenance。
- `attck_id/attack_id` 可映射 MITRE tactic/technique classification，并必须标注为 vendor
  classification context；它不是 technique 已执行或攻击成功的真值。
- Adapter 必须同时输出 observation-level canonical provenance、EDR field-importance rules 和
  source-field semantics。新增/漂移的高价值 detail 字段必须能通过 `EvidenceCoverageReport` 或
  schema baseline issue 暴露，不能因完整 raw payload 仍存在就静默忽略。

#### PingAn Threat Intel / SIEM contract

- `backend/soc_agent/normalizers/pingan_threat_intel.py` owns ThreatBook nested aliases,
  session/role separation, IOC/MITRE projection, provenance, source semantics and field-importance
  rules. `pingan_platform.py` only dispatches by source type; generic pipeline code must not know
  ThreatBook names.
- `backend/soc_agent/normalizers/pingan_siem.py` owns reviewed SIEM subtype parsing. Structured
  string collections may use bounded `json.loads` with conservative `ast.literal_eval` fallback;
  arbitrary evaluation is forbidden and malformed values remain in raw/bounded evidence.
- Generic `EmailEntityRef` contains bounded message metadata only. Raw body, headers, upstream model
  chain-of-thought/narrative and attachment contents are not duplicated into canonical entities.
- `EvidenceFieldImportanceRegistry` must inspect both parsed message views and the selected
  `raw_structured` fallback view. Structured source patterns use the `structured.*` namespace;
  unknown or changed subtype fields can therefore create normalization-maintenance issues without
  changing verdict or auto-applying a mapping.

### Normalization / extraction report 约束

- `AnalysisRun.normalization_report` 记录 deterministic normalizer 的质量信号，不参与 verdict 决策。
- `AnalysisRun.extraction_report` 记录 deterministic entity extraction 的质量信号，不替代 `ExtractedEntities.mentions`。
- `SocNormalizationService.inspect()` 是 CLI/API/TUI 做样本归一化检查的统一 service 入口；入口层不能直接 import runtime 或 normalizer 拼结果。
- `SocNormalizationService.inspect(..., mapping_path=...)` 是 mapping 文件归一化检查入口；CLI/API/TUI 不直接读取 normalizer 产物。
- `SocNormalizationService.drift(...)` 是批量样本漂移聚合入口；入口层只枚举/读取样本，不实现聚合规则。
- `SocNormalizationService.drift_recent(...)` 是最近持久化 run 的漂移聚合入口；入口层只注入 repository 和 limit。
- report 的主要用途是字段漂移检测、供应商 mapping 维护、离线 LLM 辅助分析和 replay 对比。
- report 可以包含 missing fields、normalized fields、entity counts、warnings；不要塞完整 raw payload 或长解释。
- LLM 可以读取 report 生成 mapping 建议，但不能直接基于 report 自动修改生产 mapping。

### Evidence input policy 约束

`EvidenceInputPolicy` 表达“后续事实重建/LLM 研判应该优先看哪份输入”，不是最终事实结论。

- source-specific normalizer 可以在 `AlertInput.extensions["evidence_input_policy"]` 写入该策略；干净供应商可以省略。
- `raw_message_first` 表示原始 message 是首选证据；`structured_fallback` 表示 raw message 缺失，只能退回原始结构化日志对象。
- fallback 必须显式记录 `fallback_reason` 和 source-policy `trust_level`，不能因为缺少
  `message`、source type、相似 topic 名称或前缀而伪装成 raw message 同等可信。默认必须是
  `low`；只有经过评审的 exact-topic allowlist 可以覆写。
- `ignore_processed_fields_for_reasoning=True` 只表示研判主输入不读加工字段；加工字段仍可保存在 `extensions` 中供审计、对比和冲突检测。
- `EvidenceLayer` 当前至少区分 `raw_message`、`raw_structured`、`processed_field`、`agent_inference`、`human_confirmed`。
- 平安 ZEUS/天眼 adapter 使用 `raw_message_first + structured_fallback`：
  - 优先读取 `alert.hitLog[].zeusRawLogs[].message`。
  - raw message 缺失时只选择第一条 `zeusRawLogs[]` 对象形成有界 structured projection；
    完整数组继续保存在原始 payload，后续对象不进入当前模型输入。
  - structured fallback 默认标记 `fallback_reason=raw_message_missing`、`trust_level=low`。
  - 当前唯一高可信例外是 exact topic `T_GBD_zeus_data`；仅该 topic 使用
    `trust_level=high`。`siem` source type、相似名称和前缀都不能命中。
  - `zeusRawLogs=[]` 是上游证据缺口；不能生成 synthetic bounded evidence。
- 后续 `FieldTrust` / `RoleClaim` / `RoleResolution` / `ConflictReport` 建立在该 policy 之后：先决定主证据输入，再重建方向、角色和资产候选；不能在 normalizer 层直接下最终攻击方向或处置目标结论。

### Fact reconstruction 约束

`FactReconstructionResult` 是 LLM 分析前的事实层，不是最终研判结论。它解决的是“哪些字段可信、哪些角色候选互相冲突、后续分析应该带着什么不确定性进入”。

- runtime 固定在 `entity_extract` 后、`analyze_stub` / 后续 `llm_analyze` 前执行 `fact_reconstruct`。
- `FactReconstructionResult` 必须保存到 `AnalysisRun.fact_reconstruction`，随 run payload 一起持久化、replay、审计。
- `FieldTrust` 必须分离来源可信度与推理准入：`source_trust` 只表达 provenance reliability；
  `reasoning_status` 与 `participates` 只表达该路径能否作为独立事实来源。它们不能直接改变 verdict。
- policy 实际选中的输入使用 `selected_evidence`；显式 supplementary message 使用
  `supplementary_evidence`，两者可以参与事实重建。Clean canonical/structured adapter 只有在 policy
  允许 processed fields 时才可使用 `included_canonical_projection`。
- `raw_message_first` 已成功选择 message 时，`fallback_input_path` 只能保留为
  `source_trust=unknown`、`reasoning_status=excluded_unselected_fallback`、`participates=false` 的
  审计记录；不能因为路径存在就让 Zeus sibling structured fields 重新进入事实层。Structured
  fallback 被实际选中时，复用 selected-input trust，不重复生成第二条相同路径记录。
- 从已选 raw evidence 标准化得到的 canonical 副本必须从 `CanonicalFieldProvenance` 继承
  `source_trust`，同时标记为 `excluded_duplicate_projection`、`participates=false`。排除是为了避免
  同一证据重复投票，不能把原本 high-trust 的值错误降为 low；缺少 provenance 时使用 unknown，
  不猜测来源可信度。
- source-specific adapter 负责把厂商别名转换成通用 `RoleClaim`；generic fact reconstructor 禁止识别 `attack_sip`、`alarm_sip`、`str_attack_ip` 等厂商字段。
- `RoleClaim` 必须分开 `evidence_trust` 与 `semantic_confidence`。原始 message 解析成功只能提高前者，不能自动证明厂商的 attacker/victim 语义正确。
- `RoleResolution` 当前角色包括 `source`、`destination`、`attacker`、`victim`、`impacted_asset`；状态包括 `observed`、`tentative`、`conflicted`、`confirmed`、`unresolved`。
- `response_target` 不属于事实重建结果。它由 action type、policy、调查证据、approval 和 adapter preflight 在动作边界确定。
- `ConflictReport` 必须结构化表达冲突类型、涉及字段和值，例如：
  - 同一角色多个候选值：`source_candidate_conflict`、`victim_candidate_conflict`。
  - 场景化角色不一致：`reverse_connection_attacker_destination_mismatch`、`reverse_connection_victim_source_mismatch`。
  - 源和目的重叠：`source_destination_overlap`。
- 禁止使用全局 `attacker == source` / `victim == destination` 约束。正向攻击、反弹 shell、恶意外联、C2、横向移动、代理/NAT/XFF 的角色关系不同；未知场景不得伪造跨角色冲突。
- 主 message 和 supplementary messages 必须作为独立 claim source 参与冲突检查；supplementary
  不能只进入 Prompt。Structured fallback 只有在没有可解析 message、且 policy 实际选择它时才能
  成为 claim source；未选中的 fallback 只供 raw 审计。
- 冲突裁决必须输出暂定值或 unresolved、支持/反对 claim IDs、语义置信度、证据缺口、人工核查清单和 automation guard；不能一边报告冲突，一边把值伪装成 confirmed。
- fact layer 的 `automation_allowed` 始终为 false；即使角色由人工确认，也必须再经过 action policy/approval。
- 事实重建只做 deterministic 规则；LLM 只能读取 fact layer 和冻结的 reference catalogs，随后在
  `RoleAdjudicationResult` 中输出独立的语义角色裁决。它不能绕过 fact layer 直接相信上游加工字段，
  也不能把模型角色回写成 observed/confirmed telemetry。
- raw message 存在时，canonical processed fields 默认低可信且不作为主推理输入；raw message
  缺失时 structured fallback 必须保留 fallback warning，并沿用 source policy 的显式 trust。
  PingAn 默认 `low`，仅 exact topic `T_GBD_zeus_data` 为 `high`。

### Nested message decoding / 嵌套 message 解码约束

- JSON parser 递归保留真实 object/array；JSON-in-string、HTTP headers、XFF chain 只通过 allowlisted decoder 处理，禁止无界递归猜测所有字符串。
- PingAn NIDS 的 `request_header_str`、`response_header_str` 和历史拼写
  `response_hqeader_str` 只允许按受限 JSON object 解码；strict decode 结果可作为 HTTP metadata
  fallback，但 cookie/auth/token 等值仍由模型边界脱敏，header/body 不得被解释为执行成功事实。
- nested decoder 必须限制字段名、最大长度和解析深度；失败写 parser warning，不中断告警。
- `ParsedRawMessageEvidence.fields` 保留第一层解析结果，`decoded_fields` 保存受控二次解码结果；
  parser 层必须保持解析所得原始值，不在这里改写 password/token/cookie/header/body。完整原文仍以
  `AlertInput.raw` / `AnalysisRun.input_payload` 为审计来源。
- nested JSON 严格解析失败后必须保留 `fields` 原始字符串和 parser warning，并可尝试保守 repair。
  repair 结果必须按字段策略验证根容器类型、非空结构、最大深度、最大节点数、key 长度、key source
  evidence 和 string value source evidence；accepted 结果只写入独立
  `repaired_fields`，不得写入 `decoded_fields` 或覆盖原文。rejected/error repair 继续保留原始
  string 和 observation；模型边界再依据 evidence mode 决定原样投影或脱敏 fallback。
- 每次 repair attempt 必须生成 `NestedJsonRepairObservation`，至少记录 field path、
  accepted/rejected/error、strategy、repair log count 和不含敏感原文的 reason。
- 本地逐步验证产物必须保存对应 Runtime 节点的原始 contract `model_dump(mode="json")`；允许增加步骤、源文件 hash 和上一步引用等最小 envelope 元数据，但不得用审阅聚合、翻译字段或人工结论替换真实节点输出。
- body/header/token/cookie/password 等内容由模型边界的 `SensitiveEvidenceMode` 控制：
  - 通用默认 `redact`，进入 `BoundedAnalysisEvidence` 时脱敏并记录 sanitized paths。
  - 仅经过明确批准的模型环境可设置 `full`；已选字段的值必须保持原始、不改写，mode 必须进入证据
    contract 与审计，超预算字段必须作为 omission 记录。
  - `full` 不能成为通用部署默认，parser 也不能根据部署 mode 改变自身输出。
- `CanonicalFieldProvenance` 必须展示 canonical path、selected value、selected source path/layer/trust、selection reason 和 alternatives，让 `raw_message_first` 可从运行产物直接验证。
- `CanonicalFieldProvenance.selected_from` 必须能真实解释 `selected_value`。仅仅发现一个名称相似的
  source field 不足以建立 provenance；若 canonical 值来自平台 metadata，而 message 中的近似字段
  值不同，禁止把该 message 字段伪装成 selected source。

### Cross-message observation compaction / 跨 Message 观测压缩约束

- 通用压缩器只消费 canonical typed observations，不读取或判断 `sip`、`dip`、`detailsN` 等供应商
  字段名。供应商字段到 typed observation 的映射仍由 Adapter 独占。
- 压缩只改变模型投影，不改变 `AlertInput.raw`、`AnalysisRun.input_payload`、parsed messages、完整
  `SourceFieldSemantic`、`CanonicalFieldProvenance`、`RoleClaim` 或 replay 数据。
- `EvidenceCompactionReport.v1` 必须区分：共享 `stable_facts`、单字段 `varying_facts` 频次和保持字段
  组合关系的 `profiles`。模型不得把多个独立频次分布重新组合成一个从未发生的事件。
- 分组 fingerprint 只能使用 canonical observation kind 和字段形状；行为 profile 使用 typed values，
  但排除时间、observation ID、临时源端口和 PID 等纯实例噪声。任何被截断的长值必须带长度/hash
  标记，不能伪装成完整值。
- 代表证据固定为 primary 加 dominant/rare profile representatives，总数不得超过既有 `1+4` full
  message 预算。后出现的稀有 profile 必须能替换重复副本进入 supplementary evidence，禁止 first-N
  截断吞掉异常。
- 报告至少记录 source/typed observation/group/profile/duplicate/non-dominant counts、first/last seen、
  selected paths、represented/unrepresented sources 和 `high_value_omission_count`。不支持解析且无法被
  typed summary 表达的未选中变体必须产生 omission/warning，不能静默视为已压缩。
- `EvidenceCoverageReport.llm_projected_paths` 必须包含被 typed compaction 确实表达的 exact source
  paths；完整路径留在审计对象，Prompt 只接收计数。压缩后的 scalar facts 必须进入 `E-*` catalog，
  继续接受相同的 Grounding 校验。
- `occurrence_count` 只证明重复观测次数，不会提高来源语义正确性、模型置信度或动作权限。

### Evidence coverage 约束

- `build_analysis_input` 必须生成 `EvidenceCoverageReport`，至少记录 message schema observations、
  parsed/decoded/repaired paths、canonical/fact/scenario source paths、LLM projection、sanitization、
  encoded compaction、truncation、omissions 和 high-value gaps。
- structured fallback 必须记录实际投影的 leaf paths；`full` 只表示已选值保持原始，不表示绕过
  总预算，也不表示完整 `zeusRawLogs[]` 数组进入模型。
- `full` mode 下 `BoundedAnalysisEvidence.sanitized_field_paths`、
  `EvidenceCoverageReport.llm_sanitized_paths` 与 `llm_sanitized_count` 必须为空/零；不能继续把
  decoded replacement 或 sensitive-name path 误记为脱敏。源数据已有掩码保持原样，不视为 Runtime
  sanitization。encoded-span compaction 独立计入 `llm_compacted_encoded_paths`，不能混入 sanitized
  统计。
- high-value mapping expectations apply to the selected structured fallback as well as parsed
  messages. Structured checks use exact selected-input provenance and must not scan later unselected
  `zeusRawLogs[]` entries as though they entered canonical analysis.
- coverage report 是审计/漂移产物，不是 verdict。一个字段被解析但没有 canonical mapping 时不得
  静默消失：它必须仍可在 parsed evidence 中回放，并通过全路径清单或已定义 high-value gap 暴露。
- `llm_projected_paths` 表示该字段属于 bounded projection 的候选内容；若 evidence 整体被截断，必须
  同时记录 `llm_truncated_evidence_paths`，不能声称 leaf-level 完整送达。实现必须以
  `BoundedAnalysisEvidence.projected_field_paths/sanitized_field_paths/omitted_field_paths` 的实际结果
  生成 coverage，不能根据“曾参与候选排序”推断已送达。
- `llm_compacted_encoded_paths` 必须来自实际
  `BoundedAnalysisEvidence.encoded_span_omissions`，不能通过扫描 raw payload 猜测。字段仍可标记为
  projected，但其 `<ENCODED:type:length:sha256=short-hash:OMITTED>` 占位符与完整
  hash/path/length/kind 侧车不得成为 grounded `AnalysisResult.evidence`。
- Adapter 可通过 typed `canonical_field_provenance` extension 描述 canonical 与 observation
  字段的来源。Generic fact reconstructor 只校验/合并通用 contract，不识别供应商 aliases。
- accepted repair 进入 bounded analysis 时，原字段 replacement reason 必须标为
  `replaced_by_repaired_projection`；rejected/error repair 标为 `sanitized_string_fallback`。repair 结果
  只进入 repaired paths，不得进入 decoded paths。
- Prompt Builder 不得原样 dump 完整 coverage path 清单。模型只接收业务化的 analysis readiness、parser
  status/字段计数、documented omission 汇总、high-value target/reason 和 bounded projection 数量；schema
  fingerprint、parser version 和完整 vendor paths 只用于 Runtime 审计。结构化 evidence 在模型投影中必须
  保持 JSON object/array，不得再次编码成需要模型自行反转义的字符串。
- 结构化 JSON evidence 不允许按字符直接截断后伪装成 JSON。投影器应按 leaf/子树选择并重新序列化，
  在总预算内跨 primary/supplementary observations 优先保留高价值字段；所有省略必须有精确 path/reason。
- high-value gap 规则必须通过 `EvidenceFieldImportanceRule` / `EvidenceFieldImportanceRegistry` 声明。
  Core 默认规则只能使用 vendor-neutral/标准协议语义；供应商字段规则由 adapter 写入 typed extension。
  无效 extension 规则忽略并保留现有 deterministic defaults，不能让一条坏配置中断告警。
- 如果没有 bounded raw evidence/highlight，且 canonical provenance、role facts、scenario facts
  也全部为空，Core 必须生成 critical `analysis_evidence.unavailable` gap；不得仅以低置信度或
  `unknown` verdict 隐式表达上游输入缺失。该 gap 必须触发 `HIGH_VALUE_EVIDENCE_GAP`、degraded
  evidence、human review 和 `automation_allowed=false`，但不能改写 detection verdict。存在
  provenance-backed canonical/fact/scenario evidence 的合法通用输入不能仅因没有 raw-message
  object 而触发该 gap。
- Evidence quality 必须分层：`llm_compacted_encoded_paths` 单独存在是正常 Token 优化，不改变
  `DecisionEvidenceState`；普通 omission/truncation 且无 high-value gap、以及可选区块 reference failure
  时最多为 `partial`，不得产生 `truncated_analysis_evidence` 硬复核原因；high-value gap、decision-core
  reference failure 或 unusable core 才进入 `degraded`；critical unresolved fact conflict 优先进入
  `conflicted`。Outer schema degradation 本身进入 maintenance/partial，只有造成 high-value gap 才复核。
- `DecisionReviewReason.TRUNCATED_ANALYSIS_EVIDENCE` 仅为历史持久化兼容保留。当前
  `soc.decision_policy.v7` 不为 routine bounded budget pressure 生成该 reason；关键字段是否丢失必须由
  typed `EvidenceFieldImportanceRegistry` / `high_value_gaps` 判断，不能从 `truncated=true` 猜测。

### Normalization maintenance / 归一化维护约束

- `NormalizationSchemaBaseline` 是人工批准、版本化、可 supersede 的生产基线；scope 至少包含 tenant、
  source system、adapter、parser name/version。首次观察不能自动成为 accepted baseline。
- 只有 `soc_engineer` / `soc_admin` 可接受基线。接受新版本必须 supersede 同 scope 旧 active baseline，
  并可关闭被新基线覆盖的 missing/novel issue。
- `SocNormalizationMaintenanceService.monitor_run()` 在业务 run/summary/review/audit 持久化之后执行。
  监控失败不得把原告警改成 failed；失败类型只能进入 `NormalizationMonitoringResult.warnings`。
- missing baseline、novel/degraded/unsupported schema、high-value gap、evidence truncation 必须形成
  独立 `NormalizationMaintenanceIssue`，不能混入告警 `ReviewQueueItem`。Issue 必须有稳定 dedupe key、
  occurrence count、first/last seen、状态和处理理由；resolved/ignored 后复发必须 reopen。
  Routine truncation issue 用于预算/映射运维观察，不等于 Decision degraded；encoded compaction 不得
  冒充 truncation issue。
- `NORMALIZATION_BASELINE_ACCEPTED`、`NORMALIZATION_DRIFT_DETECTED`、
  `NORMALIZATION_ISSUE_UPDATED` 是通知事件，不是 verdict/memory/action 事件。
- Gateway `/api/soc/normalization/*`、CLI、Review TUI 和 Web 只能调用 maintenance service，不得直接
  写 repository。Kafka 单条结果/JSONL metrics 可携带 issue count/IDs/warnings，不携带 raw message。
- `soc normalize suggest` 只能在离线/replay 场景运行：prompt 只包含字段路径和 target whitelist，不含
  raw values；响应必须 schema validate，并验证 source path 已观察、target 在 canonical whitelist。
  输出永远是 candidate/rejected 且 `auto_apply_allowed=false`；不得动态修改 adapter、baseline 或 runtime。

### Confidence semantics / 置信度语义约束

- `EvidenceTrustLevel` 是来源/证据质量的有序标签，不是 0..1 概率。
- `MessageSchemaStatus` 是解析完整性状态，不是置信度。
- `ScenarioHypothesis.confidence` 与 `RoleClaim/RoleResolution.semantic_confidence` 当前是可回放、带版本
  的 deterministic heuristic score；在标注集完成 calibration 前，不得描述成真实概率。
- `AnalysisResult.confidence` 是 analyzer/LLM 对 verdict 的自评，只可用于展示、排序和离线评测；不能
  绕过 schema/domain validation、conflict guard、policy 或动作授权，也不能清除已存在的 human-review
  guard。
- `SocDecisionPolicy` 是 Runtime 中唯一允许把已校验 `AnalysisResult` 转换为 operational `Decision`
  的边界；CLI/API/TUI/Kafka/Lead Agent 不得自行按 confidence 拼 `needs_review`。
- `Decision` 必须显式携带 `confidence_source`、`confidence_is_calibrated`、可空的
  `calibrated_probability` / `calibration_profile_version`、`evidence_state`、结构化
  `review_reasons` 和 `policy_version`。raw confidence 不得冒充 calibrated probability。
- 当前 stub heuristic 与 LLM self-report 都未校准；`confidence_is_calibrated=false` 必须保留，但
  `soc.decision_policy.v7` 不再仅因未校准或 raw score 低而创建 ReviewQueue。Stub 仍以
  `stub_analyzer` 进入复核，因为它不是生产推理节点。未来校准 profile 只能通过人工标注、离线校准、
  版本审批和 replay 验证接入；`soc eval confidence` 的输出不会自动改变 Runtime。
- `true_positive`、`false_positive`、`suspicious` 都是完整当前结论，不能仅由标签本身生成复核原因；
  显式 `unknown|needs_review` 才产生 `uncertain_verdict`。critical unresolved fact conflict、high-value
  evidence gap、decision-core reference failure、unusable core、challenged verifier 和 stub 等 hard guard
  独立于 raw confidence，高分不能清除它们。可选区块损坏、outer schema warning、verifier unresolved/
  unavailable 和 routine truncation/omission 只表达 `partial` 或 capability block，encoded compaction
  单独存在不改变 evidence state。
- 告警进入 Runtime 代表受配置治理的上游 rule/detector/model 确实命中并产出告警；这是可信的
  detection provenance。Analyzer 必须在本轮给出当前 scenario、direction、roles、attempt/effect/impact、
  verdict 与 recommendation。Adapter 已评审字段按精确语义范围可信；缺少 CMDB/PCAP/TI/endpoint/
  history/memory 等可选增强本身不能擦除命中、阻止当前结论或强制复核。Detection Decision 仍不等于
  action authority，Base Runtime 固定不授权动作，后置 Automation Policy 独立裁决。
- memory confidence 只在 confirmed/retrieval-enabled memory 内参与排序；不能让 pending candidate
  自动生效。
- 不同层的 confidence/trust/status 禁止直接平均、相乘或折算成一个总分。任何聚合都必须先定义
  标注集、校准方法、版本化阈值和 replay 指标，并保留原始分层信号。
- 置信度评测必须分为 `soc eval labels prepare`、人工审阅、`soc eval labels validate`、
  `soc eval confidence` 四步。标签必须绑定 `run_id`、`input_hash`、model/prompt/pipeline version、
  reviewer、reviewed_at 和理由；标签文件不得复制 raw payload。
- Offline eval 的 `llm_success_count` 只统计模型 core 输出实际通过 Parser 且 Runtime 完成的样本。
  `deterministic_fallback` 可使 Runtime 以 needs-review 结果继续，但必须 `parse_success=false`、计入
  `failed_count` 并带 sanitized fallback error，不能冒充模型成功。默认 `stub-replay` 只验证 Parser/
  Grounding 回放；其模型名和来源必须显式，不能用于生产模型质量声明。
- `pending_review` 不能参与 calibration；无法确定真实结论的样本应标为 `excluded`，不得把
  `unknown/needs_review` 冒充 accepted ground truth。同一 `input_hash` 的 replay 不能重复计权，
  不同 model/prompt/pipeline scope 不能混入同一个 profile。
- `soc eval confidence` 必须同时消费完成治理校验的 label set 和与其 exact hash/identity 匹配的 corpus
  manifest，输出 manifest verification 加 accuracy、Brier score、ECE、non-empty bins、dataset hash 和
  versioned `review_below` profile。样本不足、实际 verdict 单一或无满足支持度的阈值必须 warning；
  当前 profile 的 `auto_action_allowed`、`profile_publish_allowed` 固定为 false，不自动写生产配置。
- `PI-03A` label set 必须再由 `soc.confidence_label_corpus_manifest.v1` 封存：记录 exact payload
  SHA-256、sample identity SHA-256、tenant/environment、`simulation|desensitized_real` data class、
  source refs、creator/rationale、review summary 和显式 supersession lineage。`seal` 不等于 review，
  `verify` 只证明 integrity，不等于 calibration readiness；二者必须分别报告。
- `simulation` corpus 必须为 `mocked=true`，并且 manifest/verification 都固定
  `real_quality_claim_allowed=false`。simulation 与 real corpus 禁止共用 supersession chain；仿真通过
  可以推进产品流程，但不能关闭真实标签 gate、发布 calibration profile、声明模型准确率或开放自动化。
- Reviewed label 必须显式携带 `review_source=human_review|simulation_fixture`；pending label 不得带 review
  source，真实 corpus 不得包含 simulation-fixture label。不得用伪造 reviewer id 把 synthetic truth
  包装成人工真值。
- `soc eval quality` 只组合现有 offline/scenario/correlation/confidence evaluator，输出
  `soc.quality_evaluation_report.v1`。component pass 只表示工程链可执行；aggregate 必须固定
  `real_quality_claim_allowed=false`、`profile_publish_allowed=false`、`rollout_allowed=false`、
  `automation_allowed=false`，并保留 Grounding、taxonomy coverage、correlation false positive 和小样本
  limitation。Replay diff 使用去除 generated run/finding ID 和 timestamp 的稳定语义 hash。

### Investigation evidence eligibility / 调查证据采信约束

- `InvestigationEvidence.mocked` 是稳定的顶层 provenance 标记；读取历史 payload 时可以兼容检测嵌套
  `mocked=true`，但新写入必须同步设置顶层字段。
- 只有 `status=success` 且 `mocked=false` 的 read-only action evidence 可以满足场景所需 route、参与
  domain/scenario semantic calculation 或提高 finding confidence。
- 成功 mock evidence 可以保留在 evidence refs、调查时间线和 demo/eval 中，但必须明确标记为 mock；
  denied/failed evidence 只能保留在完整调查审计中，不得进入 finding 的已采信证据集合。
- mock/failed/denied evidence 不得改变 Runtime verdict、关闭 ReviewQueue、写 confirmed memory 或允许自动处置。

### LLM analysis request 约束

`LLMAnalysisRequest` 是 stub analyzer 和真实 LLM analyzer 的唯一输入 contract。它的目的不是扩大上下文，而是把脏输入收敛成可验证、可审计、可替换的分析请求。

- runtime 固定在 `fact_reconstruct` 后执行 `build_analysis_input`，产出 `AnalysisRun.llm_analysis_request`。
- `analyze_stub` 和真实 `llm_analyze` 只能消费 `LLMAnalysisRequest`，不能直接消费 raw payload 或自行重新解析 vendor 字段。
- `LLMAnalysisRequest` 必须包含：
  - canonical source / detection / classification / entities。
  - `ExtractedEntities`。
  - `FactReconstructionResult`。
  - `primary_evidence_path`、`conflict_count`、`conflict_types`、`warnings`。
  - `EvidenceCompactionReport.v1`，包括 stable/varying/profile 结构和完整性计数。
- analyzer 输出的 `AnalysisResult.evidence` 必须能引用 fact layer 中的关键不确定性，例如低可信 fallback 和字段冲突。
- 新 LLM 输出必须使用 `soc.analysis_result.v4`，并显式包含：
  - open-vocabulary `scenario_assessments`；不得要求场景预先存在于固定 taxonomy。
  - `origin=upstream_hint|inferred|hybrid`、唯一 primary、未校准 scenario confidence。
  - `activity_stage=detection_hit|attempt_observed|effect_observed|impact_confirmed|indeterminate`。
  - `NetworkDirectionAssessment`：分别表达 wire flow、boundary direction、semantic direction、initiator、
    intermediary、证据缺口和 `E/R/context` 引用。
  - `RoleAdjudicationResult`：typed attacker/victim/impacted/proxy/relay/scanner/C2 等角色；模型不直接
    输出 action-specific target，Runtime 只能从已接受 typed role 派生候选目标。
  - 可选 `competing_explanations`、`evidence_gaps`、`manual_checks`。
- 上游 `ScenarioHypothesis` 是输入提示，不是 LLM 输出真值。模型可以细化或拒绝它，但必须用 bounded
  evidence 解释；不能把未知场景只埋在自然语言 `reason`。
- Runtime 必须在调用前冻结 replay-stable `E-*` 当前事实目录和 `S/A/M/C/T-*` 上下文目录。Prompt
  只向模型暴露请求内短别名（`E-001`、`S/A/M/C/T-001` 等）；模型核心返回
  verdict/confidence/summary/reason/action 及这些短别名。Runtime 必须通过冻结的一一映射恢复稳定 ID，
  再补全 exact evidence tuple，并生成稳定核心推理 `R-00`。稳定 ID 才能进入 Grounding、持久化和
  replay；精确别名恢复属于 hydration，不计 repair，未知别名不得模糊匹配。
- `reference_catalogs.role_entities` 只暴露 Runtime 已类型化的 canonical/extracted 实体；raw vendor
  字段名、端口、计数器、时间戳和事件 ID 仍只是普通证据，不能因为名字像实体就成为角色目标。
- `soc-analysis-v38` 将稳定的信任、分析方法和引用规则放在 system message；bounded alert context 位于
  user message 前部，任务、精确响应结构和 final checklist 位于尾部。scenario/direction/role 使用精确
  key 契约，角色只能把 `reference_catalogs.role_entities` 中选中项的 `evidence_ref` 复制为
  `entity_ref`。Prompt Builder 按 `conflicted -> reviewed Memory（覆盖 exact/context-only/directive use mode，并按 reviewed_verdict 平衡示例） -> typed network evidence/network source -> non-network`
  选择且只注入一个完整、
  机器校验的 synthetic Golden Demo，并把 `prompt_example_id` 写入 trace；示例专用 `EX-*` 绝不能进入
  模型输出，示例 verdict/scenario/direction/role/confidence/action 也只能用于 shape guidance，不得复制为
  当前结论。另提供 compact decision calibration，必须对称覆盖无 Memory 的 false-positive/true-positive、
  context-only 可迁移/不可迁移，以及技术真阳性但后续 Tenant Policy 可忽略；它不构成第二套 output shape。
- `soc-analysis-v38` / `soc-analysis-json-parser-v24` 只允许有日志、无安全语义的机械恢复；仅当完整字段
  集合可无歧义判定为紧凑模型输出时，允许恢复缺失的顶层 `soc.analysis_model_output.v4` 版本。允许把
  严格十进制 confidence 字符串转为数值；core/optional 引用只能按冻结目录过滤、去重并保持原顺序截到
  契约上限 20；可用显式 `scenario_key` 补缺失展示名；可用同一可选对象已有字段生成缺失 rationale，
  缺失/非法 scenario provenance 只能保守记为 `inferred`；当前 compact v4 仅缺失/空 `summary` 时，
  若已有非空且不超过 4000 字符的 `reason`，可将该字符串原样复制为 display summary 并记录
  `materialize_summary_from_reason`；或在其 cited typed facts 只收敛到一个唯一
  实体时补全 role entity。通用事件/
  alert/finding ID 不是 attacker/victim 实体，多个候选不得猜测；不得根据自然语言新增攻击者、受害者、
  场景、成功状态或上下文来源。
- `soc.analysis_evidence_grounding.v3` 先逐条校验 `E-*` reference/path/typed scalar，再校验 `R-*` 和
  `S/A/M/C/T-*` 引用完整性。Grounding 证明引用闭合，不证明模型推理已校准或可以执行动作。
- 精确可见的 `<ENCODED:...:OMITTED>` marker-bearing scalar 只能证明字段存在、encoding shape 和模型边界
  省略，不能证明隐藏字节、token 身份/权限、安全结果或私有 sidecar hash。
- Grounding 不修复或重新裁判模型安全语义。任何 reference failure 必须进入
  `AnalysisMaterialityReport.v1`：若命中 `decision_evidence_refs/decision_reasoning_refs` 则阻断 core
  decision 并复核；若只属于可选区块则保留 verdict，只阻断对应 capability。
- deterministic stub 用于 request 结构、trace、replay、golden test 和低成本降级；它不是生产模型质量证明。
- 真实模型通过 `DeerFlowLLMChatClient` 复用 `deerflow.models.create_chat_model()`；SOC 代码不得再实现一套
  provider SDK 或 API key 读取。主分析结构纠错可通过 `SOC_LLM_OUTPUT_FALLBACK_MODEL` 选择另一个
  已注册 DeerFlow 模型，但不能静默切 provider，也不能形成开放式 fallback 链。
- prompt builder 只能从 `LLMAnalysisRequest` 生成 prompt；不能把完整 `AlertInput.raw` 自动塞入上下文。
- analyzer public output 必须是 `AnalysisNodeOutput`：
  - `analysis` 必须先经过 parser、Pydantic schema validation 和 domain validation。
  - `output_quality` 必须由 Runtime 写入，记录 `accepted|repaired|degraded|deterministic_fallback`、
    accepted/degraded section 和脱敏 issue；模型不得输出或覆盖该字段。
  - parser 只允许有 repair log 的 bounded、lossless 白名单修复。模型把
    `AnalysisResult.evidence[].value` 偶发返回为 object/array 时，可在严格字符上限内序列化为紧凑
    JSON scalar；超限或有损变换必须 schema failure，不能截断后伪装成功。
  - `model_name`、`prompt_version`、`parser_version` 必须进入 run/step trace。
  - `PipelineStepTrace.metadata` 必须记录 `prompt_hash`、`candidate_hash`、`repair_applied`、usage/response metadata 等审计信息。
  - step metadata 不保存完整 prompt、完整 raw LLM output 或完整 vendor payload；需要复盘时通过 replay 输入和版本重新生成。
- 默认 runtime 必须继续使用 deterministic `StubLLMAnalyzer`；真实 LLM analyzer 只能通过显式 flag/config/client 注入。
- 统一配置为 `SOC_ANALYZER_MODE=stub|llm`、`SOC_LLM_MODEL`、`SOC_LLM_THINKING_ENABLED`、
  `SOC_LLM_JSON_MODE_ENABLED`、
  `SOC_LLM_ATTACH_TRACING`、`SOC_LLM_MAX_CONCURRENCY`、`SOC_LLM_REQUESTS_PER_MINUTE`、
  `SOC_LLM_ADMISSION_TIMEOUT_SECONDS`、`SOC_LLM_CALL_TIMEOUT_SECONDS`、
  `SOC_LLM_OUTPUT_RETRY_ATTEMPTS`、`SOC_LLM_OUTPUT_FALLBACK_MODEL`。CLI 可用 `--analyzer-mode` /
  `--model-name` 覆盖；未知模型必须 fail-fast，
  禁止静默换到默认 provider。
- `SOC_LLM_JSON_MODE_ENABLED` 默认 `false`，只在目标 Provider 已通过能力探测后开启。它只请求合法
  JSON object，不等于 JSON Schema enforcement；每次成功/失败调用都必须记录
  `json_mode_requested`，本地 parser/schema/domain validation 与唯一一次 bounded core repair 仍不可省略。
- 条件式角色复核配置为 `SOC_ROLE_VERIFIER_ENABLED`（默认 `false`）、
  `SOC_ROLE_VERIFIER_MODEL`（缺省复用主模型）和 `SOC_ROLE_VERIFIER_MIN_CONFIDENCE`（`0..1`）。stub
  analyzer 下禁止启用 verifier；未知 verifier model 必须在启动组装时 fail-fast。
- `SOC_LLM_OUTPUT_RETRY_ATTEMPTS` 只能为 `0|1`，主分析与 verifier 各自最多做一次结构纠错。主分析
  必须先独立校验 compact required core 与 `reasoning / scenario_assessments / network_direction /
  role_adjudication / guidance`。core 有效时，本地保留合法 item/section、丢弃坏 item 或注入 inert
  default，并标记局部 degraded；不得只为可选区块再次调用 Provider。只有 core 无效时，纠错请求才
  接收 invalid candidate、validation error、允许目录和响应 Schema，并写 `primary_analysis_retry`
  journal。core 最终无效时使用 deterministic stub 形成显式 fallback 结果并强制 review；不得把模型
  坏输出伪装成正常 stub。Provider timeout/capacity/auth/transport 仍是 retryable Runtime failure，不得
  用 stub 隐藏。verifier/full-core 纠错分别写 `role_verification_retry|primary_analysis_retry`，所有路径
  都不得形成开放式 self-reflection loop。
- LLM admission 必须独立于 Kafka worker concurrency，使用进程内 bounded semaphore 和可选 RPM
  预算。准入饱和是 retryable `analyzer_capacity`，不得调用 provider 后再伪装成本地限流。
- admission timeout 只限制等待本地并发名额；call timeout 独立限制一次 provider invocation。后者
  超时必须形成 retryable `analyzer_timeout`，Kafka 不得提交 offset；后台调用可能无法强制中断时，
  executor worker 数仍必须有界，防止超时请求无限创建线程。
- parser semantic repair 只允许当前 `soc-analysis-json-parser-v24` 明确列出的可证明无损关系修复；
  类型猜测、内容拼接、歧义引用和安全语义补全必须被拒。可恢复 optional section 按上述 degraded
  路径处理；required core 无法恢复时按 deterministic fallback 处理。
- 新模型响应不得带未声明的顶层、scenario、direction 或 role 字段；不得用字符串 confidence、重复
  reference、无 `E/R` 支撑的 assessed direction/role、重复角色/target 或零/多个 primary 绕过
  `AnalysisResult.v4`。
- Prompt compact JSON、模型响应、`AnalysisResult` 文本字段、evidence 数量/值长度、knowledge candidate
  数量/长度都必须有硬上限；超限必须在 Runtime 中形成 typed failure，不能进入 repair 无限消耗。
- `DeerFlowLLMChatClient` 只可保存 allowlisted response metadata 和 token usage；provider headers、凭证、
  原始 response object 不得进入 `AnalysisRun`。

### Analysis materiality / 局部影响约束

- Runtime 必须在 `evidence_grounding` 和可选 `role_verification` 之后、`decide` 之前执行独立
  `analysis_materiality` 步骤；结果固定写入 `AnalysisRun.analysis_materiality`、step trace、analysis
  audit、Lead Agent review context 和固定 cohort 报告。
- `AnalysisMaterialityReport.v1` 必须分别记录 `core_usable`、`decision_usable`、结构化 review reason、
  section impact、每个 immutable `ConflictReport` 的 disposition，以及 scenario/direction/source/
  destination/attacker/victim/impacted-asset/user/response-action capability guard。禁止只保存一个
  笼统 `degraded=true`。
- impact 只有 `none / action_only / decision_review`。optional section 缺失或损坏、非 critical 未解决
  target conflict、verifier unresolved/unavailable 默认属于 capability 级问题；不得擦除已通过 core
  Grounding 的 verdict，也不得仅因此创建 ReviewQueue。
- core fallback、core section 损坏、decision core 的 E/R reference failure、critical unresolved current-
  fact conflict 和 verifier challenged 属于 `decision_review`。Decision review 同时阻断通用 response
  action，不能被 `needs_review=true` 的租户自动化 override 绕过。
- `ConflictReport` 是 pre-analysis immutable observation；materiality 只能追加
  `resolved / accepted_variance / unresolved` disposition 和 resolution source，不得原地删除或改写冲突。
  已由 FactReconstruction、Runtime 场景语义或 grounded model role adjudication 解决的冲突只保留审计。
- `SocDecisionPolicy.v7` 只消费 materiality 的 decision-level结果；`SocAutomationService` 必须再次检查
  对应 capability guard。这样 verdict 可用不等于精确动作目标可用，动作被阻断也不等于整条研判失败。

### Network direction / role adjudication / tenant knowledge 约束

- 方向必须拆成三层：observed wire flow、organization-boundary direction、security semantic roles。
  禁止在通用 Runtime 建立 `source == attacker` 或 `destination == victim` 的全局等式。
- `FactReconstructionResult` 继续是 deterministic pre-LLM fact layer；模型最终语义角色必须写入独立
  `AnalysisResult.role_adjudication`，不得回写或伪装成原始 `RoleResolution`。
- assessed `NetworkDirectionAssessment` 必须同时有 `E-*` 和 `R-*`；每个 `AdjudicatedRole` 必须有
  exact `E-*`、`R-*`，使用知识时还要带对应 `S/A/M/C/T-*`。`R-00` 可作为 top-level core reasoning。
- `AdjudicatedRole.status=unresolved` 表示当前证据不能把该语义角色绑定到具体实体，必须使用
  `value=null`；`tentative|resolved_from_evidence|resolved_from_context|conflicted` 仍必须携带非空 typed
  entity。不得使用虚构的 `unknown` 字符串绕过实体约束。
- direction/role 的 `context_refs` 只需精确存在于本次 request catalog，不要求在引用的 `R-*` 中机械
  重复；不存在或歧义引用必须拒绝。Runtime 派生 response target 时必须找到同类型、同值且
  `resolved_from_evidence` 的 typed role，并同时通过 `AnalysisMaterialityReport.capability_guards`；缺少
  精确实体或 capability 被阻断时必须 deny，不得回退到松散 host/IP 猜测。
- 派生 response target 必须绑定具体 `action_kind`。它不是 tenant disposition、Approval、automatic
  authorization 或 adapter execution input；最终权限仍只来自受治理 Automation Policy。
- 第二阶段角色复核必须是默认关闭、确定性触发的可选 Runtime 节点；不得由第一轮模型自行决定是否
  调用。v2 gate 只复核最多四个原子网络方向字段与非占位 attacker/victim；显式 core conflict/indeterminate、
  upstream role conflict 或这些核心引用的 Grounding failure 才能触发。`inferred`、`tentative`、一般
  evidence gap、intermediary、response target 和 confidence 单独出现都不能触发；confidence 只能在已
  触发后作为诊断原因。不能按供应商字段名或场景关键词硬编码。
- 复核输入必须把第一轮结论原子化为稳定 `RC-*` claims；`RC-ND-01..04` 分别只承载 observed flow、
  boundary direction、semantic direction 和 connection initiator 中实际存在的一项。禁止投影第一轮
  rationale、reasoning prose 或 confidence，避免锚定。复核只读取同一冻结 catalog 中与 claim、核心
  role 及类型化 network context 相关的子集；没有新 Provider 证据时必须承认它只是独立反证检查，
  不得宣称完成事实增强。
- `soc-role-verification-v4` 必须遵守 Adapter 的精确字段语义：已评审的 provider-reported session
  initiator/responder 声明本身就是该会话角色的上游证据，不能只因没有独立 SYN/PCAP 而降为
  unresolved。只有本告警中的 direction-unknown、proxy/NAT/forwarding leg 或同 observation 反证可
  挑战该 scoped claim；攻击者/受害者仍必须独立复核，response target 不属于本节点的复核范围。
- 新模型输出 `RoleVerificationCandidate.v2` 必须完整且仅完整覆盖触发时的 claims。每条只能为
  `supported|challenged|unresolved`，所有事实/上下文引用必须解析并区分 supporting/contradicting
  极性；challenged 必须有 contradicting `E-*` 或类型化 `S/A/M/C/T-*`，unresolved 必须有 evidence
  gap，alternative 必须保持原 claim key 集合。每条必须有非空 `counterevidence_assessment`，不能用
  单纯“同意第一轮”代替反证搜索。v1 仅为已持久化历史记录读取兼容，live parser 不接受 v1 输出。
- Provider GeoIP/address-location enrichment 只能作为 Adapter 保留的审计字段，不能进入方向复核事实
  目录，也不能覆盖经审核且匹配当前实体的 `network_scope`。租户私有网段和 GeoIP 已知误标必须留在
  tenant Profile/Adapter，通用 Runtime 不按 `30/8` 或任何供应商字段名硬编码。
- `network_scope` Fact 必须显式声明类型化 `network_scope_membership`（当前枚举为
  `organization_controlled / organization_external / shared_or_ambiguous`），不得靠解析 label/summary
  推断，其他 Fact kind 禁止携带该字段。两个 canonical endpoint 均被
  `organization_controlled` 覆盖时，Runtime 可生成只约束
  organization boundary 的 `internal_to_internal` invariant；它不得外推 attacker/victim、compromise、
  verdict 或 action authority。Verifier status/alternative/contradicting refs 与该 invariant 矛盾时必须
  schema-fail 并进入唯一一次受限 correction，不能因自然语言 rationale 写对就接受错误 status。
- `RoleAdjudicationVerificationResult` 不得重写 `AnalysisResult.v4`。confirmed 不移除既有复核原因；
  challenged 使 `soc.decision_policy.v7` 进入 conflicted 并要求人工复核；unresolved/unavailable 保留
  可用第一轮结论，但由 materiality 阻断 direction/attacker/victim/impacted-asset targeting。任何复核
  状态都不能直接授权动作。
- 每个主分析/角色复核 Provider 调用必须分别写入有序、上限为 8 的
  `provider_request_journals`，新记录使用 `soc.analysis_request_journal.v2` 并保存
  purpose/model/prompt/parser/step/status，而不保存 prompt、证据值、
  credential 或 provider response。兼容 `request_journal` 只作为当前/最后一次调用及中断恢复指针；
  新调用开始前，上一条 running journal 必须完成，进程丢失时只能有最后一条保持 running。
- verifier 被配置时，`AnalysisRun.pipeline_version` 必须固定为 `soc-runtime-v8`，不以本条告警是否触发
  第二次调用为条件；verifier-free 流程使用 `soc-runtime-v7`。质量评测不得混合两个版本。
- 固定十告警对照必须报告触发率、claim status、fail-closed、每个 Runtime step、Runtime total、E2E
  total、主/复核调用耗时与 token。2026-08-12 的 10/10 live gate 是 v1 历史基线；v2 在该冻结结果上
  离线重算为 3/10。随后同一输入 hash 的 v2 live 运行实际触发 5/10，完成 5 次逻辑复核、14 个实际
  Claim 和 5 次 Provider invocation；离线与 live 差异来自主模型重采样后的 typed 方向/角色输出，不能
  把离线 replay 伪装成 live 结论。在独立人工方向/角色真值建立前，不得把 challenge 率当准确率，也
  不得默认启用 verifier。
- 评测必须区分“一条告警的一次逻辑复核”“该复核中的原子 `RC-*` 数量”和“实际 Provider invocation
  数量”。一次逻辑复核最多有 1 个整体方向 claim 与 attacker/victim claims，并可因 bounded contract
  correction 产生第二次 Provider 调用，但不能记成第二条告警复核。Gate 为未触发样本稳定投影的候选
  Claim 只能计入 `projected_candidate_claim_count`，不得混入实际 `atomic_claim_count`。Token 必须按 primary analysis、
  role verifier、tenant-policy advisor 三条 lane 分开汇总；Provider usage 完整时标 `reported`，内网
  缺失时按实际可见 request/response 做 `estimated`，部分上报加估算为 `mixed`，失败且无可见内容为
  `unavailable` 并使总量成为 lower bound。没有经审核的模型单价表时金额成本必须为
  `not_measured`；没有独立人工真值时 precision/recall/F1 与 accuracy 必须为 `not_measured`。
- 固定十告警 E2E 默认只读取数据库中已经存在的正式 Memory。模型 `K-*`、每 case 的
  `11-knowledge-candidates.json` 和根 `knowledge-review/` 都只是离线人工审核材料，不等于
  `soc_memory_candidates`，更不等于 `soc_memory_records`。只有 admission、人工 confirm 和独立 retrieval
  activation 才能形成可检索 Memory；`--replace` 重建输出目录时也会重建隔离的 `soc-e2e.sqlite`。
- 人工确认只能通过 `SocReviewService.confirm_role_adjudication()` 或其 Gateway command 进入。命令要求
  trusted actor、角色权限、expected revision 和非空理由，结果追加 `RoleAdjudicationRevisionRecord`，
  保留 base model adjudication hash 和 previous effective hash；不得覆盖模型输出。
- 人工确认的 response target 必须引用同一命令中确认的精确实体，action-specific `target_role` 可与该
  实体的语义角色不同，且 `automation_allowed=false`。后续动作仍
  必须通过 Policy/Approval/Authorization/Adapter preflight。
- 租户静态知识使用严格 `TenantKnowledgeProfile.v1`，只按 canonical current-request selector 匹配并投影
  bounded、hashed、source-linked `C-*`。profile loader 不执行租户代码；每项固定
  `decision_authority=none`。
- 首见行为 Playbook 仍属于租户静态知识，而不是伪造的 confirmed Memory。它必须由已审核来源迁移，
  使用 canonical typed 多信号 selector，并把适用条件和当前恶意反证写在同一 Fact 中；模型可引用该
  `C-*` 参与 Base Decision，但后续 Memory/Tenant/Automation authority 层保持不变。
- Process Playbook 的同事件聚合必须由 canonical `event_scope_id` 限定，并只合并“相同规范化进程名 +
  相同非空 PID”的连通 observation；只有同名但 PID 缺失不构成跨 observation identity，未连通片段
  即使来自同一告警也不得凑成匹配。需要区分读取与变更的
  命令必须使用 exact-command selector，例如 `net share` 不得匹配 `net share d$ /delete`。
- 需要 parent 语义时必须读取 canonical direct-parent 字段；需要文件对象语义时，relation、name、path
  prefix/suffix 必须共同命中同一个 `FileObservationRef`。不得从全局进程名或路径集合猜父子关系，也不得
  把进程文件、IOC 和动作目标跨 observation 拼成一个 Playbook。
- 通用方法进 public Skill `S-*`，字段/采集语义进 Adapter `A-*`，人工历史经验进 Memory `M-*`，实时
  MCP 查询进 `T-*`，运营规则进 Tenant/Automation Policy。不得用一个长租户 Prompt 混合这些权限层。
- 动态授权测试、护网参与者、变更窗口和当前资产状态不是静态 profile；必须继续走 typed Governed
  Context Fact 生命周期和事件时间匹配。

### Mapping config 约束

- mapping config 只用于确定性字段搬运：`canonical.target.path: $.source.path`。
- mapping target 必须是 canonical `AlertInput` 字段路径，不能写厂商别名字段。
- source path 当前只承诺最小 `$.a.b.c` 语法；带 `.` 的复杂 key 或需要条件解析的供应商格式，升级为 Python adapter。
- mapping 文件可以声明 `name` 和 canonical `source` 默认值；report adapter 必须输出为 `mapping:<name>`。
- 缺失 source path 必须进入 `NormalizationReport.warnings` / `unmapped_fields`，用于漂移检测。
- mapping 文件变更需要测试样本覆盖，不能靠线上每条告警动态 LLM 解析。

### Entity extraction 约束

- `ExtractedEntities` 保留 `ips/domains/urls/processes/users/hosts/rule_codes/rule_names/rules` 兼容字段，但新能力应优先读写 `mentions`。
- `EntityMention` 是确定性 extractor 和后续 LLM enrichment 的统一输出 contract，必须包含 `kind`、`value`、规范化 `key`、可选 `role`、`confidence`、`source`、`evidence_path`。
- `EntityMention.source` 必须标记来源：`deterministic`、`llm`、`normalizer`、`analyst`。LLM 输出不能伪装成确定性实体。
- 企业身份字段如 UM 账号应进入 `UserEntityRef.um_account`，提取为 `kind=user, role=um_account, key=user:<value>`；不要新增独立 `EntityKind.UM_ACCOUNT`。
- `user_id`、Windows SID、IAM subject、UM 账号、登录名都属于 user identity，但必须用 `role` 区分，不能把 SID/资产用户 ID 冒充成 UM。
- 处置人、审批人、分析师账号默认留在 `extensions` 或审计上下文，不进入核心 user 实体，除非它们是告警主体。
- `AlertSummary.entity_keys` 必须从 `ExtractedEntities.mentions[].key` 派生；只有旧 run 没有 mentions 时才允许 fallback 到旧列表字段。
- LLM entity extraction 只能补充或建议 `EntityMention`，不能直接写 `AlertSummary`、review queue、memory fact 或 verdict。
- LLM 生成的实体必须经过 schema validate、domain validate 和去重后，才允许进入 `AnalysisRun.entities.mentions`。
- `entity_keys` 是相似告警召回索引，不保存大段解释；完整实体上下文和来源留在 `AnalysisRun.entities.mentions`。

### 新模块设计检查表

每新增一个模块、类或 service 前，先写清楚：

| 问题 | 必须回答 |
|---|---|
| 模块职责 | 它解决哪个具体问题？ |
| 调用方 | 谁允许调用它？CLI/API/Daemon/Core/Pipeline？ |
| 依赖方 | 它允许 import 哪些层？ |
| 输入 | 使用哪个 contract/domain model？ |
| 输出 | 返回哪个 contract/domain model？ |
| 失败语义 | 抛异常、返回 error object、还是写入 run failed？ |
| 审计 | 是否产生 run/step/tool/memory event？ |
| 持久化 | 是否落库？通过哪个 repository/protocol？ |
| 可重放 | replay 时如何复现输入、输出和版本？ |
| Memory 影响 | 是否读写 facts/lessons？是否需要 human confirmation？ |

如果这些问题说不清楚，先不要写实现。

### 架构测试门禁

后续新增：

```text
backend/tests/architecture/
├── test_import_boundaries.py
├── test_public_api_exports.py
├── test_contracts_are_strict.py
├── test_pipeline_has_no_transport_imports.py
└── test_tools_require_policy.py
```

当前架构门禁集中落地为：

```text
backend/tests/architecture/test_soc_agent_boundaries.py
```

必须覆盖：

- `contracts` 不 import `core/pipeline/db/daemon`、Gateway/Typer 或具体 provider。
- `core` 不 import app-layer Gateway routers、`cli.py`/TUI 或 concrete Kafka consumer。
- `pipeline` 不 import FastAPI/Kafka/Typer/具体 DB client/具体 LLM SDK。
- Gateway router、CLI、TUI、daemon worker 和 Lead Agent bridge 只能通过 public core service 进入业务逻辑。
- `AlertInput` 保持 canonical strict schema；flat/vendor payload 只能在 `normalizers` 出现。
- public package exports 与文档一致，避免跨包调用内部函数。

## 五、Runtime 状态机

参考 DeerFlow `RunManager`：run 必须有明确状态，状态迁移可持久化。枚举保留未来状态不表示当前
runner 会写出所有状态；普通 replay 创建新 run 并设置 `replay_of_run_id`，不会把任一 run 改成
`replayed`。

### AnalysisRunStatus

```text
pending
running
needs_review
success
failed
interrupted
rolled_back
replayed
```

### PipelineStepStatus

```text
pending
running
skipped
success
failed
retrying
```

当前 `PipelineStepTrace` 嵌套在 `AnalysisRun` 中，因此不重复保存 `run_id/alert_id`。稳定字段为：

| 字段 | 说明 |
|---|---|
| `step_name` | `normalize/entity_extract/fact_reconstruct/...` |
| `status` | step 状态 |
| `input_hash` | 输入摘要 hash |
| `output_hash` | 输出摘要 hash |
| `started_at/ended_at` | 时间 |
| `duration_ms` | 耗时 |
| `error` | 脱敏后的失败摘要 |
| `warnings` | 本节点的结构化可读警告 |
| `metadata` | 节点专属有界 metadata；LLM model/token/parser 等只在相关节点出现 |

独立 transport 如果需要扁平 trace，可以投影 `run_id/alert_id`，但不得反向扩散到内部 step contract。
当前固定 runner 主要产生 `running -> success|failed` step；`pending/skipped/retrying` 是受支持的扩展
状态，不是每次运行必经状态。

开发期执行监控必须从持久化 `AnalysisRun.steps`、active provider request journal 和后续 Pattern
Observation 写入状态构造只读投影。前端只轮询当前选中且正在运行的 alert-scoped 轻量 endpoint，不得轮询
全量语料 state，也不得在浏览器推测 phase。投影可包含 step/phase 状态、起止时间、耗时、warning/error
摘要、模型名、token、Schema/Grounding 计数、Decision 和 Observation/Candidate ID；不得返回原始 payload、
Evidence/Context 内容、Prompt、模型原文、Provider response 或 secret。

DEV 告警演练的提交并发采用 `alert_id` 粒度的服务端原子占用：不同告警可并行，同一告警的第二个请求
必须立即 `409` 且不得再次进入 Runtime/LLM。跨浏览器状态只轮询轻量 `/activity` 投影；完整 corpus state
只在活动集合变化或运行完成后重新读取。并发完成的 POST 响应内嵌快照不具备覆盖其他 Run 的权威性，
前端不得用它直接替换全局缓存。该占用仅服务单进程 DEV 演练；生产 API/Kafka 和多副本部署必须继续使用
持久化 source identity、idempotency 与 lease/worker 契约，不能把进程内占用冒充为生产防重。

完整 DEV 审计不得扩大上述轮询契约，而使用独立、显式加载、认证 `soc_admin` 且受
`SOC_DEV_CORPUS_WORKBENCH_ENABLED` 与隔离 SQLite 约束的 audit endpoint。其只读 bundle 按固定顺序投影
Run manifest、原始输入、canonical normalization、实体、事实、bounded LLM request、结构化模型结果、
quality/grounding/materiality、Decision lineage 和 Pattern/Memory write；可包含完整持久化业务证据与模型
上下文，但不得读取环境变量/credential、重新调用 Adapter/Runtime/LLM、修改状态或作为 STG/生产证据。
新 run 必须持久化 normalize step 当时产生的 exact `AlertInput`；旧 run 缺失时只能标记 partial 并显示
frozen `LLMAnalysisRequest` 的 canonical projection，不得用新版本 Adapter 在读取时静默重算历史结果。

## 六、数据模型边界

### Pydantic 用在边界

必须使用 Pydantic schema 的边界：

- CLI 输入文件解析后的 `AlertInput`
- FastAPI request/response
- Kafka message payload
- LLM structured output
- Tool input/output
- Config yaml

### Domain model 用在内部

内部领域模型应稳定，不被外部协议污染：

```text
SecurityEntitySet
SecurityFinding
AnalysisRun
PipelineStep
Evidence
PermissionDecision
ToolAction
MemoryFact
LessonRule
```

原则：外部协议可以 version bump，domain model 不跟着频繁改名。

## 七、API 接口规范

`BG-P1-02` 冻结以下兼容契约。现有 Web/API 已广泛使用 `/api/soc/*`，因此不新增一套重复
`/api/soc/v1/*` route，也不把所有 success body 强行包成 `{data, meta}`：

| Contract | Current rule |
|---|---|
| Base path | 保持 `/api/soc/{capability}/...`；breaking transport 才建立新 major path |
| Version | 每个 operation 的 OpenAPI 带 `x-soc-api-version=1`；每个 route response 带 `X-SOC-API-Version: 1` |
| Success body | 保持 endpoint 声明的直接 typed JSON；list endpoint 自己使用明确的 `{items: [...]}` response model |
| Error body | route/dependency/validation error 使用 `application/problem+json` 和 `SocProblemDetails(soc.api.problem.v1)` |
| Request ID | 可传 `X-Request-Id`；未传则生成；进入 `ServiceRequestContext.request_id` 并在 response 回显 |
| Trace ID | 可传 `X-Trace-Id`；复用 DeerFlow trace context 或生成；进入 service context 并在 response 回显 |
| Idempotency | `Idempotency-Key` 是公共 optional header；只有明确声明幂等语义的 mutation 才要求并消费它 |
| Actor | 权威 actor 来自 Gateway authenticated user/service principal；`X-Actor` 不属于协议，legacy `X-SOC-Actor-Id` 不能覆盖 authenticated identity |

`SocProblemDetails` 至少包含：

```json
{
  "schema_version": "soc.api.problem.v1",
  "type": "urn:deerflow:soc:problem:soc.conflict",
  "title": "Conflict",
  "status": 409,
  "detail": "The command conflicts with current state",
  "instance": "/api/soc/...",
  "code": "soc.conflict",
  "request_id": "req_...",
  "trace_id": "trace_...",
  "retryable": false,
  "errors": []
}
```

约束：

- `backend/app/gateway/routers/soc_transport.py` 是唯一 SOC HTTP transport helper；所有 SOC router
  通过 `create_soc_router()` 获得相同 headers、Problem Details 和 OpenAPI metadata。
- validation `errors` 只返回 location/message/type，不回显 request input、secret 或 raw alert。
- pre-router authentication/CSRF failure 继续属于 DeerFlow Gateway 全局安全边界；SOC route 不复制或
  绕过 Auth/CSRF middleware。进入 SOC route 后的业务、dependency 和 schema error 使用上述 problem。
- Frontend `core/soc/api.ts` 保留 direct typed success 解析，识别 Problem Details 为 `SocApiError`，
  传递 `X-Request-Id`，并拒绝已声明但不受支持的 API version。
- `contracts/soc_api/openapi-v1.snapshot.json` 锁定所有已发布 SOC path/method、公共 request headers、
  response headers 和 error statuses。新增/删除/改路径必须显式 review snapshot diff。
- PingAn 私有 `8090` 兼容入口不属于通用 `/api/soc/*`。它保留旧 ZEUS
  `POST /workflow/task` 与 `GET /task/task_status` wire shape：Bearer/`app-key` 只需命中 operator 配置的
  legacy allowed-key set，`app_code` 是业务路由/持久元数据/幂等字段，不是 credential selector。
  `alert_data` 必须保留旧调用方发送的完整对象，不能转换成 `message` 字符串；鉴权必须先于任务存在性
  查询，端口仍受 Host DEV/private deployment 与来源网络限制。
- 旧协议 live acceptance 的私有请求不得由操作员复制示例后手工替换 JSON。PingAn 请求准备器只接收
  人工批准的 pending `alert_id`，验证 Workbench index/payload-store 的 schema、SHA-256、大小、
  source index、payload hash、快照状态与内外层 ID，自动生成 fresh session 和 mode-`0600`
  `.local.json`；其报告禁止包含业务正文。Lifecycle/callback mode 必须由同一配置命令成对切换，
  任一 key 缺失、重复或不可解析时 fail closed。
- 旧协议 live acceptance 必须显式使用 checkout 解析出的绝对 `SOC_DATABASE_URL`，并在首次连接
  `8090` 前验证 SQLite 文件、`soc_alembic_version` 及 Processing Job/Callback 表；证据库不可用时
  不得产生外部副作用。若 durable submit 后客户端在状态、事件或报告读取阶段失败，只允许保留完全
  相同的 mode-`0600` 请求并显式 `--resume-existing`：相同幂等 identity 必须返回原 Job，已完成的
  Runtime 与 callback 不得重跑。只有首次响应已离开 `PENDING` 才能确认是既有任务；否则保持原
  请求并稍后重试，不能用 resume flag 冒充恢复证据。恢复报告以 `resumed_existing_confirmed=true` 代替 fresh gate，
  但同 Job replay、真实 lifecycle、Runtime lineage、delivered non-mocked callback 仍全部必需。

## 八、事件与通信规范

### 内部事件

`SocEvent` 当前是结构化的进程内通知，事务化 L3 命令只在 commit 后发出；默认 sink 可以是 no-op。
持久审计由 `decision_audit_log` 和 `soc_mutation_audit_log` 承担。下面的通用 durable event stream/SSE
是 `AC-46` 的 Stage 4 target，当前不得把 event type 清单或 schema 当成已落库/已推送能力：

```json
{
  "schema_version": "soc.event.v1",
  "event_id": "evt_...",
  "event_type": "pipeline.step.completed",
  "run_id": "run_...",
  "alert_id": "ALT-0001",
  "trace_id": "trace_...",
  "occurred_at": "2026-06-28T10:00:00Z",
  "payload": {}
}
```

推荐事件类型：

```text
analysis.run.created
analysis.run.started
pipeline.step.started
pipeline.step.completed
pipeline.step.failed
llm.call.started
llm.call.completed
tool.action.proposed
tool.action.approved
tool.action.executed
memory.fact.proposed
memory.fact.confirmed
memory.fact.rejected
analysis.run.completed
analysis.run.failed
```

### Web/CLI 流式输出（target / Deferred）

参考 DeerFlow StreamBridge/SSE 思路：

- API/Web UI 未来可用 SSE 或 WebSocket 订阅 run events；当前 SOC Review Web 使用普通 Gateway API。
- CLI 当前直接调用 core service；它不宣称已经有 durable event stream。
- event payload 不放超大原始日志，只放摘要和引用 ID。

## 九、Kafka 协议

Kafka/Redpanda ingestion 已实现。Kafka message 必须 versioned，不直接把厂商原始字段当作内部模型。

### 输入 topic

```text
soc.alerts.raw.v1
```

payload：

```json
{
  "schema_version": "soc.alert.raw.v1",
  "source": "edr",
  "alert_id": "ALT-0001",
  "dedup_key": "rule:exe:src",
  "occurred_at": "2026-06-28T10:00:00Z",
  "severity": "medium",
  "raw": {},
  "entities_hint": {}
}
```

`soc.alerts.raw.v1` 是 topic 名，`soc.alert.raw.v1` 是 payload schema version，二者不要混用。
当前 input contract 由 `SocAlertRawEnvelope` 执行以下强约束：

- Pydantic `extra=forbid`；不再兼容直接投递 vendor alert object。
- `source`、`alert_id`、`dedup_key`、`occurred_at`、`severity`、`raw` 必填。
- `raw` 最大 900,000 UTF-8 JSON bytes，`entities_hint` 最大 64,000 bytes，且都必须 JSON 可序列化。
- `_soc_ingress` 是 transport provenance 保留键，source `raw` 不得占用。
- mapper 完整保留 source `raw`，只用 `setdefault` 补通用 fallback，并把 envelope metadata 放入
  `_soc_ingress`；`entities_hint` 不是已确认事实。
- validation error 只返回字段路径和约束，不包含 input/raw 值。非法版本、裸对象、超限和保留键
  冲突按 poison message 进入 DLQ，成功写 DLQ 后才 commit source offset。
- contract tests 必须至少覆盖 APT、EDR、HIDS 三类脱敏 source payload 的完整保留，以及 malformed、
  bad version、oversize、reserved-key collision 和错误信息不泄漏 raw。

### 输出 topics（target，当前未生产）

```text
soc.analysis.results.v1
soc.analysis.review_required.v1
soc.analysis.events.v1
```

Kafka consumer 约定：

- 至少一次投递，必须靠 `alert_id + run_mode + pipeline_version` 做幂等。
- Kafka callback 解码后必须先生成 `SocDaemonMessage`，再调用 `SocDaemonService.process_message()`；callback 不能直接写 repository、不能直接调用 runtime pipeline。
- `SocDaemonMessage.kind=alert` 只能进入 `SocAnalysisService.analyze()`；`kind=approval_request` 只能进入 `SocAgentApprovalService.submit_request()`；后续 `kind=external_disposition` 只能进入 `SocExternalDispositionService.apply_event()`。
- Kafka metadata 必须保留为 daemon message metadata；`topic + partition + offset` 派生 `idempotency_key=kafka:{topic}:{partition}:{offset}`。
- DB 写入成功后再 commit offset。
- poller/controller 永远拥有 poll、DLQ 和 commit；当前 runner 可同步调用单条 worker，后续 worker
  pool 也不得把 consumer side effect 下放给 worker。
- poison message 进入 dead-letter topic：`soc.alerts.dead_letter.v1`。

当前分析结果的 source of truth 是 DB read model/Gateway；broker 侧仅实现 input 和 dead-letter。
`soc.analysis.*` producer 属于后续明确立项，不得把 topic 名字本身当成已接通能力。

### 外部处置反馈 application ingress

- Generic ingress 是 authenticated `POST /api/soc/external-dispositions`，请求必须是
  `SocExternalDispositionIngressCommand(soc.external_disposition_ingress.v1)` 包裹 canonical
  `SocExternalDispositionEvent`。
- `event.source_event_id` 必填，用于稳定语义幂等；完全相同的 retry 返回已有结果，相同 identity
  的 changed retry 必须 `409 Conflict`。
- Core service 只允许 `soc_admin` / `external_disposition_adapter`，router authentication 不能替代
  service-level authorization。
- 调用方不能提交 status/trust mapping config。vendor payload -> canonical event 的映射、租户、签名、
  replay protection 和 trust policy 由 server-side source adapter/config 持有。
- 真实 Zeus/ITSM/SOAR source adapter 可以采用 webhook、Kafka、polling 或受控 import，但必须进入
  同一个 canonical ingress/service，不能直接写 repository、ReviewQueue、memory 或 outcome。

## 十、工具与动作协议

参考 Claude Code `buildTool + validateInput + checkPermissions + isReadOnly`，SOC 工具必须统一声明能力。

```python
class ToolSpec(BaseModel):
    name: str
    description: str
    input_schema: dict
    output_schema: dict
    permission_level: Literal["L0", "L1", "L2", "L3", "L4", "L5"]
    read_only: bool
    idempotent: bool
    timeout_seconds: int
    side_effects: list[str]
```

执行前必须得到 `PermissionDecision`：

```json
{
  "behavior": "allow | ask | deny",
  "reason_type": "policy | rule | classifier | human | safety_check",
  "reason": "L4 action requires human approval",
  "approved_by": null
}
```

当前 Alpha 能力边界：

- L0：通过 service 读取本地日志、告警和业务数据。
- L1：通过受控 read-only adapter/MCP 查询外部数据，并保留 evidence/audit。
- L2：生成明确标记的建议、review item 和 candidate knowledge。
- L3：只允许可信 `auth_source`、命令级角色、core service、幂等和 mutation audit 完整成立时改变内部状态。
- L4：只验证 approval/grant、adapter preflight、dry-run 和一次性 token 消费边界；真实外部副作用仍是 data-gated。
- L5：Alpha 明确拒绝，攻击模拟必须另行定义范围、审批、隔离环境和审计。

## 十一、多 Agent 通信协议

当前 Alpha 不做复杂多 Agent 通信。长期如果引入 Detection/Hunting/IR/Attack Simulation Agent，必须使用结构化消息，不用自由文本当协议。

```json
{
  "schema_version": "soc.agent.message.v1",
  "message_id": "msg_...",
  "conversation_id": "case_...",
  "from_agent": "soc_triage_agent",
  "to_agent": "detection_engineering_agent",
  "message_type": "request | response | broadcast | approval_request",
  "summary": "Need rule tuning suggestion for repeated false positive",
  "content": {},
  "requires_response": true,
  "expires_at": "2026-06-28T10:10:00Z"
}
```

禁止：

- 跨 Agent 直接共享所有 memory。
- 用自然语言消息触发高风险动作。
- 子 Agent 绕过 orchestrator 直接操作生产系统。

## 十二、配置规范

配置分三类：

| 类型 | 示例 | 管理方式 |
|---|---|---|
| 静态配置 | 模型、阈值、队列、超时 | `config.yaml` + Pydantic 校验 |
| 密钥配置 | API key、DB URL、Kafka password | `.env` / secret manager，不进 git |
| 运行时策略 | fact 状态、lesson 启用、自动动作审批 | PostgreSQL，有审计版本 |

所有配置变更必须能回答：

- 谁改的？
- 什么时候改的？
- 改了什么？
- 影响哪些 run？

## 十三、身份、认证与授权

本地 CLI 可以使用显式本机 actor 和配置文件；API、Web UI、Daemon 必须在服务边界区分 actor、surface 和 `auth_source`。

### Actor 模型

| Actor | 说明 | 默认权限 |
|---|---|---|
| `system` | Runtime/daemon 内部动作 | 只能按 policy 执行 |
| `analyst` | 一线分析师 | 分析、纠正、提交 review |
| `shift_lead` | 值班负责人 | 批量确认/驳回、批准部分 L3 |
| `admin` | 平台管理员 | 配置、数据源、模型、策略 |
| `agent:<name>` | 子 Agent / 专职 Agent | 只能使用分配的 tool scope |
| `service:<name>` | Kafka consumer / scheduler | 只能写入指定队列和事件 |

所有写操作必须带：

```text
actor_id
actor_type
auth_source
request_id
```

### 授权原则

- API 入口做认证，core/policy 再做授权，不能只靠入口保护。
- `PermissionDecision` 需要记录 actor、policy version、decision reason。
- 自动动作即使由 daemon 触发，也必须能追踪到 policy 和候选证据。
- 多 Agent 场景下，子 Agent 不继承用户全部权限，只继承本任务明确授予的 capability。

## 十四、数据安全、脱敏与留存

SOC 数据通常包含内网 IP、用户名、主机名、进程命令行、文件路径、hash、可能的业务系统名称。默认按敏感数据处理。

### 数据分类

| 级别 | 内容 | 处理 |
|---|---|---|
| S0 | 指标、计数、耗时、token usage | 可长期保留 |
| S1 | 告警摘要、规则名、verdict、confidence | 可保留，注意访问控制 |
| S2 | IP、主机名、用户名、进程路径、命令行 | 存储和日志需要脱敏策略 |
| S3 | 原始日志、样本路径、凭证片段、业务数据 | 默认不进 prompt，不进普通日志 |
| S4 | 密钥、token、密码、cookie、私钥 | 必须拦截、脱敏、拒绝进入 LLM |

### Prompt 数据原则

- 进入 LLM 的内容必须经过 `PromptSanitizer`。
- 原始日志默认只截取必要片段，保留 evidence reference。
- prompt 全文是否落库必须可配置；生产默认存 hash + injected ids + 摘要。
- golden samples 必须脱敏后提交仓库。

### 留存策略

| 数据 | 默认留存 |
|---|---|
| `decision_audit_log` | 180-365 天，按磁盘和合规调整 |
| `pipeline_step_trace` | 30-90 天，长期保留摘要 |
| 原始 alert payload | 默认 30 天或只存引用 |
| confirmed facts / lessons | 长期保留，但需要老化和复查 |
| rejected facts | 保留摘要和 hash，用于抑制重复错误 |

## 十五、Schema 版本与兼容策略

所有跨边界协议都必须带 `schema_version`：

```text
soc.api.v1
soc.event.v1
soc.alert.raw.v1
soc.analysis.result.v1
soc.agent.message.v1
soc.llm.triage_output.v1
```

兼容规则：

- 小版本只允许新增 optional 字段。
- 删除字段、改语义、改枚举含义必须升大版本。
- API/Kafka/LLM schema 都要有 contract tests。
- migrations 必须支持从前一个 release 升级，不允许只支持空库。
- replay 时必须记录 `pipeline_version`、`schema_version`、`prompt_version`、`model_name`。

建议维护：

```text
contracts/schemas/
├── api/
├── kafka/
├── events/
├── llm/
└── tools/
```

当前已提交 SOC OpenAPI v1 snapshot 并执行兼容性测试；在生产集成冻结 Kafka 外部契约前，必须补齐 AsyncAPI/schema 文档及兼容性门禁。

## 十六、模型、Prompt 与评测治理

模型和 prompt 不是代码外的“黑盒配置”，它们会直接影响判定结果，必须版本化。

### 必须记录

| 字段 | 说明 |
|---|---|
| `model_provider` | OpenAI / DeepSeek / vLLM / ... |
| `model_name` | 实际调用模型 |
| `model_parameters` | temperature、max tokens、reasoning 等 |
| `prompt_version` | prompt 模板版本 |
| `pipeline_version` | pipeline 版本 |
| `parser_version` | JSON parser / repair 策略版本 |
| `eval_set_version` | 使用的 golden set 版本 |

### Prompt 约定

- prompt 模板集中放在 `prompts/` 或 `pipeline/prompt_builder.py`，不要散落在节点里。
- prompt 输出必须对应 `contracts/llm/*.py` 的 Pydantic schema。
- prompt 修改必须跑 golden alert set。
- 高风险 prompt 变更需要 replay 一批历史样本，比较 override rate、needs_review rate、parse rate。

### Prompt / Skill / Tool 分层约定

SOC Agent 后续会同时存在 DeerFlow-style lead agent、domain skills、MCP/tool 调用和 runtime node prompts。工程上必须区分这些层，不能把所有能力塞进一个 prompt。

| 类型 | 所属层 | 负责什么 | 禁止什么 |
|---|---|---|---|
| Lead Agent prompt | DeerFlow `lead_agent` custom agent / `SocAgentChatService` / TUI / Web / Channels | 交互、任务理解、调查计划、选择 skill/tool、提出澄清问题 | 直接改 DB、memory、decision，绕过 core service 执行动作 |
| Domain skill | DeerFlow `skills/public/soc-*` 或后续 SOC custom skill | 提供 endpoint、network/APT、web application、email、资产归属、攻击方向等领域指导 | 自己执行工具、自己写 memory、把候选知识当 confirmed fact |
| Node prompt | `soc_agent/prompts/` | 固定 pipeline 节点内的结构化推理，例如 `llm_analyze` | 自主改变主流程、直接调用 MCP/tool、输出未校验自然语言进入决策层 |
| MCP/tool adapter | `soc_agent/tools/` / DeerFlow MCP bridge | 查询或执行外部能力 | 绕过 policy、审计、人类审批执行高风险动作 |

当前 `soc-analysis-v38` 是 **analysis node prompt**，不是 SOC Lead Agent 的总控 prompt。它只能消费
`LLMAnalysisRequest.v6` 和受控 context catalogs，输出 compact `soc.analysis_model_output.v4`；Runtime
将其 hydration 为 `AnalysisResult.v4`，再依次经过 schema/domain validation、evidence grounding、
analysis materiality 和 Decision Policy v7。模型不能决定后续状态或动作权限。

当前 `SocSkillResolver` 已作为薄层落地在 `backend/soc_agent/skills.py`，只输出 DeerFlow skill 名称和结构化 reason。后续 `build_soc_skill_context()` 通过 DeerFlow parser 校验真实 public Skill package，并只投影包内受审阅、受预算约束的 `references/runtime-guidance.md`；它不把完整 `SKILL.md` 常驻 Runtime prompt，也不绕过 DeerFlow skill system。当前 DeerFlow 可加载的 SOC domain skills 是：

- `soc-alert-triage`
- `soc-endpoint-triage`
- `soc-network-apt-triage`
- `soc-web-application-triage`
- `soc-email-phishing-triage`
- `soc-asset-direction`
- `soc-asset-extraction`

`SocSkillResolver` 遵循：

- 输入来自 `LLMAnalysisRequest`、`AlertSummary`、confirmed facts 或 analyst-selected context，不读取松散 raw vendor payload。
- 当前用 deterministic 规则选择 skill，例如 `source_type=edr/hids` -> `soc-endpoint-triage`，typed HTTP 或 `source_type=f5/waf` -> `soc-web-application-triage`，typed email -> `soc-email-phishing-triage`，明确方向冲突 -> `soc-asset-direction`。普通 `tentative/unresolved` 角色属于预期不确定性，不得让方向 Skill 对所有告警常驻。
- 路由信号分级：source type 与 canonical typed evidence 是强信号；明确 domain 文本只是 fallback；`恶意`、`命令执行`、`文件读取/上传` 等跨域行为词只能强化已由来源或 typed evidence 建立的兼容路由，不能单独让已知 endpoint/network/web 来源跨域。typed cross-domain evidence 不得因该约束被裁掉。D6 v2 必须验证 `keyword_only_cross_domain_misroutes=[]`。
- `ExtractedEntities.hosts` 只允许 `EntityKind.HOST`；业务资产、资产 ID 和资产组进入 `assets`。IP/domain/url 的存在本身不证明是 network session，文件元数据本身也不证明存在 endpoint 行为；优先根据 canonical typed observations 路由。
- LLM 可以在白名单 skill 候选中 rerank 或建议补充 skill，但不能动态加载未知 skill 后直接影响决策。
- 选中的 Skill 作为 bounded context 注入 prompt；必须记录 skill name、选择原因、命中特征、package hash、guidance source/hash、估算 token 数和预算。
- Skill 只能产生指导、候选解释、候选查询或 action proposal；写 DB、写 memory、执行 tool 必须回到 service/policy 层。

当前实现：`SocSkillContext.v2` 已接入 `LLMAnalysisRequest.skill_context`、`build_analysis_prompt()`、`JsonLLMAnalyzer.metadata`、`SocAgentChatService` 的 `soc.skill_context` stream event 和 TUI translate。Runtime 只注入 Skill package 内的 bounded runtime guidance 及其双 hash/token metadata；完整 `SKILL.md` 和场景 references 仍由 DeerFlow Lead Agent 按需动态读取。`validation/compact_zeus/checkpoint_d` 的 D5 单样本产物验证该边界，D6 v2 对全语料做 typed route、keyword-only cross-domain 和 package coverage，D7 只验证真实 Analyzer 的 `AnalysisResult.v2` 结构；D6/D7 都不是 Runtime 新节点，D7 通过也不能替代 D8 Grounding。

SOC Lead Agent profile 安装必须使用 DeerFlow per-user custom-agent storage。当前 `SocLeadAgentProfileInstaller` / `soc agent install-profile` 写入 `.deer-flow/users/{user_id}/agents/soc-triage/config.yaml` 和 `SOUL.md`，profile v2 还写入 operator-owned `middlewares`。默认不覆盖，只有显式 `--overwrite` 才更新；因此旧安装必须由 operator 明确覆盖后才同时获得 ReviewQueue context 与 approval middleware。legacy shared 同名 agent 存在时跳过，避免 shadow。不要为 SOC 自建第二套 agent profile storage。

SOC specialist subagent 必须复用 DeerFlow root `config.yaml -> subagents.custom_agents`、
`CustomSubagentConfig`、registry、`task` executor、model inheritance 和原生 task events。
当前 definitions/installer 只允许位于 `soc_agent.subagents`；`soc agent install-subagents` 默认 dry-run，
只有 `--apply` 写 root config，同名不同配置默认原子失败，`--overwrite` 也只能替换受管 SOC names 并
保留其他 operator config。Profile 必须 capability-oriented：network、endpoint（EDR/HIDS）、web、email；
不得按 vendor/topic/rule 无限生成 Agent。当前 profile 必须 `tools=[]` 且 `skills=[]`；只能使用
`SocLeadAgentDelegationMiddleware` 从 trusted ReviewQueue artifact 投影的 case evidence 和与该专家匹配的
`SocSkillContext.v2` `runtime-guidance.md`。它不得继承 Provider/MCP/action、shell、file-read/write、
递归 task 或 approval 权限。`max_turns=32` 是当前 middleware graph 的 recursion budget，不是 32 次
自主行动额度。Specialist output 是 advisory artifact，
不是 `InvestigationEvidence`、`AnalysisResult`、`Decision`、memory candidate、approval request 或 action
result。Lead Agent operator middleware 必须校验 specialist allowlist、trusted case context、单任务 1200
字符、server projection 32K 字符、同一 chat run 最多两个不同专家、context/task/projection
lineage 和输出 marker；任何 stopped/capped result 必须标记 `execution_failed`。不得修改
DeerFlow 通用 executor 来硬编码 SOC 规则。`PI-01G1..G3` 已用原生 task event/replay 回归和
NIDS/EDR `deepseek-v4-flash` 代表样本关闭 `AC-30`；两次报告均保持
`provider_acceptance_claimed=false`，不关闭任何 Real Integration Debt。

SOC Lead Agent chat entry 必须复用 DeerFlow embedded client / gateway runtime。当前 `SocLeadAgentChatService` 通过 `DeerFlowClient(agent_name="soc-triage")` 转发 stream，并发出 `soc.lead_agent_entry` marker；它不是 SOC action executor。入口治理分两条但共用同一 proposal/service boundary：标准 Web/Gateway custom-agent 运行由 per-agent `SocLeadAgentApprovalMiddleware` 在 `after_model` 处理完整结构化 marker；`soc chat tui --lead-agent` 保留 `SocLeadAgentChatService` 的外层 proposal bridge，避免在 embedded client 内重复处理。ReviewQueue context 已通过 `backend/soc_agent/context_bridge.py` 以 bounded `SocLeadAgentReviewContextArtifact` 接入：只能由 `SocReviewService.get_investigation_context()` 取数，必须记录 context hash / skill context hash，不能把完整 raw payload 或 repository 访问权交给 Lead Agent。

SOC Lead Agent action proposal 必须走 `backend/soc_agent/actions/proposals.py`。根目录不保留 `backend/soc_agent/action_proposals.py` 兼容入口。只有 `<soc_action_proposal>...</soc_action_proposal>` 显式 JSON marker 会被解析成 `SocAgentActionProposal`；普通自然语言、Markdown 建议、模型自称“已执行”的文本都不能触发动作。每条模型消息最多接收 5 个有效 proposal，模型只可提交 `route/action/reason/payload/confidence`；`proposal_id`、source、actor、thread/run/queue/alert/context 引用、request ID 与 idempotency key 都由 server 注入。proposal、permission decision、approval request 和 mutation request identity 必须由同一 server seed 稳定派生，使 graph replay 对相同意图幂等；相同 identity 下语义变化仍必须冲突，不能用忽略 `created_at` 掩盖内容变化。`SocLeadAgentActionProposalBoundary` 只能调用 `SocAgentActionPolicy`，并在高风险时生成 pending `SocAgentApprovalRequest`；approval request 必须携带 `source_proposal_id`、`action_payload`、`context_refs`。本边界和 middleware 都不直接执行高风险 MCP/tool、不调用外部处置 adapter、不修改业务状态；失败必须 fail closed 并输出 secret-light error/event。

Approval inbox 客户端必须展示 proposal 溯源字段。Web/TUI 至少要让审批人看到 `source_proposal_id`、`action_payload`、`context_refs`；展示层不能改写这些字段，不能绕过 approval grant / dry-run / execute boundary。

MCP/tool 调用、审批和处置必须继续通过 SOC service/action/policy boundary 逐步接入。

后续 MCP/tool 调用遵循：

- 查询类工具默认仍需通过 allowlist、rate limit、audit，例如资产归属、威胁情报、安全标签和历史告警查询；不存在的进程树/主机上下文 Provider 不进入 allowlist。
- 处置类工具默认高风险，例如 IP 封禁、EDR 隔离、禁用账号、下发阻断，必须有人类审批或明确 playbook 授权。
- LLM 输出只能是 `ToolActionProposal` / `ActionProposal` 一类结构化候选，不能直接调用 adapter。
- 真实 adapter 必须注册到 `SocActionAdapterRegistry`，并通过 `SocAgentActionAdapterDescriptor` 声明 side-effect、必需参数和 dry-run/execute 能力。
- Tool result 必须作为 evidence 写回 run trace / audit，不允许只进入 prompt 后丢失。

### Profile / Skill / MCP 开放配置治理

SOC Lead Agent、Domain Sub Agent、Skill 和 MCP/tool group 的开放配置以 `.notes/ai_soc/governance/agent-profile-governance.md` 为产品治理源头；工程实现必须满足：

- Profile、skill、MCP 绑定必须有 `draft -> validated -> staging -> active -> archived` 生命周期。
- `draft` / `staging` 不能影响生产决策；`active` 必须记录审批人、评测集版本、profile hash、skill hash、tool group hash。
- Middleware preset 只能由代码/operator-owned profile 定义，不能由模型、普通用户或 agent HTTP update 自由新增/删除。DeerFlow `AgentConfig.middlewares` 是通用可信扩展点，不是业务用户可编辑字段；per-agent 列表先于 global `extensions.middlewares` 加载，精确重复项只实例化一次。
- 用户可配置 readonly MCP 候选；`high_risk` tool group 必须由管理员/审批流程启用，并继续走 human approval。
- SOC Lead Agent custom-agent profile 只写 DeerFlow 支持的 `name/description/model/tool_groups/skills/middlewares/SOUL.md` 语义；其中 `middlewares` 只能由可信安装器/operator 管理。不得向 profile 增加自定义 `mcp` 字段作为生产执行绑定。MCP server 连接属于 DeerFlow `extensions_config.json` / `mcp_config.json`，SOC action 到 MCP tool 的业务绑定属于 action adapter allowlist。高风险 adapter 不得作为 unrestricted DeerFlow/MCP tool 直接暴露给模型。
- 固定 SOC Runtime 不选择或执行交互式 subagent profile。当前只有服务端绑定 ReviewQueue 的
  `soc-triage` 可在 `SocLeadAgentDelegationMiddleware` 白名单内选择 managed specialist；LLM
  不能动态加载未知 profile/skill/tool。未来若实现 active profile registry，其他路由也必须经该
  治理状态和白名单，不得反向改变 Runtime 主流程。
- 所有 profile 选择、skill 注入、tool proposal、tool result 都必须进入 trace/audit，支持 replay diff 和 rollback。

### Model fallback

允许 fallback，但必须显式记录：

```text
requested_model
actual_model
fallback_reason
```

禁止静默换模型后仍把结果当成同一评测基线。

## 十七、成本、限流与背压

每天 1 万条告警不算大吞吐，但 LLM 调用会造成成本和速率瓶颈。成本控制是 Runtime 责任，不是后期优化。

### 预算维度

| 维度 | 示例 |
|---|---|
| per alert | 单条告警最大 LLM 调用次数、最大 token |
| per run | replay / correction 的最大调用次数 |
| per minute | provider rate limit |
| per day | 日预算和告警降级策略 |
| per tenant/env | 后续多环境或多团队隔离 |

### 背压策略

以下是 production target。当前 Alpha Kafka runner 有串行 poll/worker/commit/DLQ、bounded LLM
admission 和 JSONL metrics，但没有 priority queue、delayed queue、duplicate merge worker 或 partition-aware
worker pool；这些属于 `AC-06/AC-47/AC-48`，不能按本节文字冒充已实现：

- 队列满时优先保留高 severity、低置信、未处理告警。
- 重复告警优先 merge，不排队完整分析。
- provider 限流时，低风险告警进入 delayed queue 或 review queue。
- daemon 不允许无限并发；所有 LLM/tool 调用必须走 semaphore/rate limiter。
- 超预算时明确产出 `needs_review`，不能假装分析成功。

## 十八、部署、运维与恢复

### 环境分层

```text
local       # 本地开发：SOC SQLite；需要协议验证时可启 Redpanda/PostgreSQL
dev         # 开发共享环境
staging     # 接近生产数据结构，脱敏数据
production  # 真实告警
```

### 健康检查

Current Alpha provides Kafka daemon status/health scripts, readiness summaries, JSONL process
metrics and normalization metrics. A unified SOC HTTP health/Prometheus surface is a Stage 4 target：

```text
/healthz       # 进程是否存活
/readyz        # DB/Kafka/model provider 是否可用
/metrics       # Prometheus 指标
```

### 关键指标

```text
analysis_success_total
analysis_failed_total
analysis_needs_review_total
pipeline_step_duration_ms
llm_call_total
llm_token_total
llm_parse_failure_total
tool_permission_denied_total
queue_depth
queue_lag_seconds
kafka_consumer_lag
```

### 备份恢复

- PostgreSQL 必须有备份策略。
- migrations 先在 staging 跑。
- fact/lesson 变更可回滚。
- replay 不覆盖历史结论，只生成新 run。

## 十九、扩展点与插件边界

长期要服务 SOC、防御工程、威胁狩猎、IR、WAF/F5、攻击模拟，所以扩展点要提前固定，但不要过早做动态插件系统。

### 稳定扩展点

| 扩展点 | Protocol |
|---|---|
| 模型调用 | `LLMClient` |
| 告警接入 | `AlertSource` |
| 工具执行 | `ToolExecutor` |
| 知识检索 | `KnowledgeRetriever` |
| 记忆存储 | `MemoryStore` |
| 队列 | `TaskQueue` |
| 策略 | `PolicyEngine` |
| 事件输出 | `EventSink` |

当前使用 Python `Protocol` + 显式 registry，不做热插拔 marketplace。

### 禁止的扩展方式

- 插件直接拿 DB connection 任意写。
- 插件直接拼 prompt 注入 LLM。
- 插件绕过 `PolicyEngine` 执行动作。
- 插件返回自由文本作为结构化事实。

## 二十、并发、一致性与幂等

必须提前定义哪些操作可以重复执行。

| 操作 | 幂等键 |
|---|---|
| analyze alert | `alert_id + pipeline_version + mode` |
| replay run | `source_run_id + replay_config_hash` |
| confirm fact | `fact_id + target_status + actor_id` |
| Kafka consume | `topic + partition + offset` 或 `alert_id + source` |
| tool action | `action_id` |

一致性原则：

- `alert_summaries`、`decision_audit_log`、`pipeline_step_trace` 要么同事务写入关键结果，要么能通过 run 状态判断失败。
- Alpha L3 业务命令必须通过 `SocMutationUnitOfWork` 同时提交业务状态和
  `soc_mutation_audit_log`；进程事件只能在 commit 后发出。
- LLM 调用不可回滚，所以必须先提交 bounded `AnalysisRequestJournal`，再调用 provider，最后通过
  analysis bundle 写 final decision；只发进程内 `analysis.requested` event 不满足该约束。
- 外部副作用动作必须先写 `automation_actions(proposed)`，批准后再执行。

## 二十一、测试与评测

### 测试层级

| 层 | 覆盖内容 |
|---|---|
| unit | extractor、validator、policy、dedup、lesson match |
| contract | API schema、Kafka schema、LLM output schema |
| integration | PostgreSQL repository、migration、replay |
| golden | 固定样例告警的期望 verdict/evidence |
| architecture | import 边界、工具必须经过 policy、memory 注入只读 confirmed |

### Golden alert set

Alpha regression set 最少维护：

- 1 条明确误报
- 1 条明确真阳性
- 1 条低置信未知
- 1 条字段缺失
- 1 条坏 JSON 模拟

指标：

```text
LLM JSON parse rate
domain validation pass rate
decision audit coverage
replay diff rate
analyst override rate
duplicate merge rate
review queue precision
tool permission denial rate
```

### Offline eval 约束

- `soc eval offline` 是真实 LLM 默认上线前的评测入口；它必须可重复运行、默认不调用外部模型。
- 显式 `--live-llm --model-name NAME` 时允许逐样本调用 DeerFlow 注册模型；它与
  `--llm-response-jsonl` 互斥，失败样本必须留在 report，不能中断后伪装成 stub 成功。
- replay response 使用 JSONL，按 `sample_id` 绑定样本；`content` 可以是字符串或 JSON object，但进入 analyzer 前必须走同一套 `JsonLLMAnalyzer`、parser、schema/domain validation。
- 默认未提供 replay response 时，只允许把 stub 结果序列化后再走一遍 LLM parser/runtime 链路，用于 smoke-test 工程路径；不能把该结果解释为真实模型质量。
- eval report 必须至少包含 parse success、repair count、failed count、verdict diff、needs_review diff、confidence delta。
- eval 只读样本，不写业务库、不生成 memory、不入 review queue；需要持久化评测历史时另建 eval repository/schema。
- Memory 使用独立的 `soc.memory_heldout_eval_fixture.v1` / `soc.memory_heldout_eval_report.v1`，但仍遵守
  相同只读边界。fixture 必须冻结 reviewed record 及其 source alert lineage、held-out request/base decision、
  prediction snapshot 与独立人工 truth；source alert ID 与 held-out alert ID 不得重叠。
  `prepare` 可用 operator-owned tenant/environment 补齐请求中的缺失值，但已有值不一致时必须拒绝，不能
  为了让 Profile 命中而静默改写冻结运行的 scope。
- 每个 accepted held-out case 必须对全部 frozen Memory 标注
  `decision_applicable|context_only|unrelated`，并提供 reviewer/source/time/reason。pending case 不得进入
  accuracy 分母；simulation case 只能使用 simulation truth，且 report 必须固定
  `real_quality_metrics_available=false`、`rollout_authorized=false`。
- Memory eval 必须调用生产 `SocMemoryProfile`、`SocMemoryService`、`M-*` enricher 和
  `SocAutomationService`；禁止复制匹配或 directive 算法。retrieval relevance、Profile lesson
  applicability 和实际 directive eligibility 必须独立计量，不能把“召回”当作“改判已获准”。
- 任何新携带 decision directive 的 confirmed Memory 必须保存 `soc.memory_business_lesson.v2`；v1
  保持只读兼容。v2 脱离 source alert 仍可独立理解，分别表达检测场景、实际业务事件、审核结论、
  判断依据、精确适用条件、允许泛化范围、失效/反证条件和处置建议。
  `SocMemoryService.review_candidate()` 是唯一确认边界：决策型确认必须显式
  提交 reviewer-owned `record_lesson`；服务只负责契约校验、确定性渲染和持久化，不得从自由文本
  review reason、candidate caption 或测试夹具猜出业务经验。缺少 Lesson 必须在 candidate 状态迁移前拒绝；
  `Reviewed simulation lesson for ...` 这类看似够长但没有业务含义的占位语句同样不能获得改判权。
  Lesson 渲染结果拥有最终 record `summary/content`；禁止同时提交结构化 Lesson 与自由文本覆盖，避免
  split-brain。审核者可通过
  `record_applicability` 将候选已有的 canonical optional facet 收紧为 required facet；不得新增候选中
  不存在的值，也不得把 vendor raw field 名写入通用 Runtime contract。
  人工可读的 applicability 文案必须使用本地化标签并同时保留原 facet key/value，例如
  `必须匹配「行为强度（behavior_strength）」：强特征（strong）`；机器判断始终读取 typed
  `SocMemoryApplicabilitySpec`，历史英文文案只能在 UI read projection 中转换，禁止回写改造旧记录。
- `SocMemoryRecord.business_lesson` 存入已有 JSON `record_payload`，不新增平行表或迁移；旧的无 Lesson
  record 仍可作为非权威上下文读取，但新决策型确认必须经过上述边界。`M-*` 投影继续读取同一 record，
  并记录 Lesson schema provenance；评测 fixture 可以提供人工真值输入，但不能绕过生产服务来证明
  确认、渲染、持久化或检索能力已经实现。
- 候选级 AI 辅助使用独立的 `soc.memory_business_lesson_draft.v1`，只允许在已有
  `SocMemoryApplicabilitySpec` 的可评审候选上调用。`SocMemoryLessonDraftService` 从服务端候选构建
  有界 `D-*` 目录，并要求当前审核人显式选择最终 verdict；历史 candidate/cohort verdict 只是模型观察，
  不得覆盖该人工选择。模型只能返回检测场景、实际事件、业务结论、逐条引用的判断依据、泛化边界、
  失效条件、处置建议和 uncertainties；每条草稿依据必须保留到精确 `D-*` 来源的映射，供审核人逐条核验。机器适用条件由服务端从
  applicability 确定性补齐。未知引用、超长上下文或 schema
  不合格必须拒绝，普通 JSON 解析失败只允许记录在 provenance 中的保守 `json_repair`。
  Provider JSON-object mode 不是前置条件；Prompt 尾部必须提供带 `required`/
  `additionalProperties=false` 的完整 JSON Schema。草稿中的 URI、域名和 namespaced identifier 必须逐字
  存在于引用的有界来源目录；结论不得包含处置动作语言。Runtime 必须确定性添加必需 facet 不匹配和
  当前反证/攻击影响失效底线。专家收窄草稿范围时只提交 candidate optional facet 键名，服务端重建其
  exact value；任何未知键拒绝。候选级调用可以有最多一次有界 output repair，其 prompt hash、调用数、
  token 和 repair action 必须记入 draft provenance；该策略不得扩展为每条告警默认重试。
  草稿固定 `decision_impact=none`、`review_required=true`、`persistence_performed=false`，不得自动确认、
  激活、改判或授权动作。严格 JSON 是模型输出协议，不要求供应商支持 JSON-object mode；后者仍是
  可选传输能力。当前使用版本化专用 Prompt，不把租户事实硬编码进 Prompt，也不为固定表单生成另建
  Skill；租户业务事实必须来自候选/cohort 或当前 reviewer context，并由最终 reviewer 明确确认。页面应
  默认只读展示完整 Lesson，只有显式编辑才暴露文本输入；Applicability 始终由服务端拥有。

### Checkpoint D cross-source/full-corpus contract

- D10 是显式付费的真实模型代表样本回放：每个已知 topic 一条代表样本并包含全部 D0 known input
  gap；必须记录 model/Prompt/Parser、usage、Grounding 和 Decision，禁止静默回退 stub。没有人工标签时
  不能把 D10 当准确率或自动化上线依据。
- D11 是无模型的全量兼容性 Gate：每个 D0 payload 通过公开 `SocAnalysisService` 十步控制流执行两
  次，必须覆盖全部唯一 corpus 行、保持 input payload、使用 `analyze_stub`，并证明 Decision
  fail-closed。它不是持久化 `replay(run_id)`，不得访问 DB、MCP、租户处置或 action。
- D11 semantic projection 必须纳入下游 step output、normalization/extraction report 和 failure contract；
  必须排除 `run_id`、时间戳、耗时、重复 step input hash，以及源缺失时由摄入生成的
  `AlertEventRef.received_at` 所污染的 normalize raw output hash。原始 trace 差异仍保留为信息字段。
- D11 主矩阵只保存紧凑摘要，真实输入不得复制进 212 行报告；仅失败或不稳定行可在 gitignored
  `diagnostics/` 保存完整双运行结果。Stub verdict 只表示路径覆盖，不能作为模型质量。
- D11 必须显式统计 parser warning、schema status、encoded compaction、omission reason、routine
  truncation、high-value gap、conflict、Grounding 和 Decision review reason，并把 evidence-quality
  分层规则做成 acceptance checks，而不是只输出人工阅读数字。

### Release-level Alpha acceptance contract

- 唯一入口是仓库根目录 `./scripts/soc-alpha-acceptance.sh all`；组件命令只用于定位失败，不能用若干
  手工成功日志替代最终 `finalize` gate。
- 聚合 schema 固定为 `soc.alpha_acceptance_report.v1`，至少覆盖 representative APT/EDR/HIDS、CLI、
  Kafka consume/commit/DLQ、SQL、registered Gateway handlers/services、Review Web、feedback、decision+
  mutation audit 和 replay lineage。
- Report 必须写 component status、failure reasons、mock/data-gated disclosures、known failure semantics
  和 SHA-256 artifact manifest；缺少任何必需 component 或 source coverage 时必须失败。
- Deterministic analyzer、SQLite、mock investigation provider、local Redpanda、mocked browser transport
  必须逐项标记；这些局部边界不能因 aggregate pass 被提升为 production evidence。
- 验收输出位于 `backend/.deer-flow/soc-alpha-acceptance/`，可能含告警衍生数据，必须 gitignored，且
  每个 release candidate 重新生成。

### Stage-exit Alpha readiness contract

- Stage 3 技术退出入口是仓库根目录 `./scripts/soc-alpha-readiness.sh all`。它必须复用上面的 acceptance
  report，不得复制一套 APT/EDR/HIDS 业务验收逻辑。
- 聚合 schema 固定为 `soc.alpha_readiness_report.v1`，必须记录 source commit/branch、acceptance report
  hash/component status、完整 SOC pytest gate、architecture/migration gate、完整性矩阵 hash/counts 和
  Stage 4 roadmap hash/work-package IDs。
- pytest gate 不能只相信 exit code；还必须从日志解析大于零的 passed count，并拒绝 failed/error。
- readiness finalizer 必须从权威 Markdown 读取 completeness counts、Data-gated/Deferred capability IDs 和
  `PI-01..05`，只保存引用/hash，不建立第二份能力状态台账。
- technical pass 只允许设置 `alpha_candidate_ready=true`；必须始终保持
  `release_decision=pending_owner_review`、`stage_transition_allowed=false`、
  `production_ready=false`。人类审批不能由本地脚本推断。
- 报告可以记录 dirty worktree 供开发评审，但正式 release archive 必须在 reviewed commit 的 clean
  checkout 重跑；输出位于 `backend/.deer-flow/soc-alpha-readiness/`，必须 gitignored。
- 部署、停止/回滚、签字角色和 Stage 4 外部输入只由
  `.notes/ai_soc/alpha-readiness-package.md` 解释；真实 provider/基础设施/标签/响应动作不能用更多 mock
  关闭。

## 二十二、交付阶段与契约成熟度

执行顺序只由 `.notes/ai_soc/delivery-roadmap.md` 决定：`BD -> AA -> BG -> PI`。旧的技术
`Phase 1..5` 已不再作为当前状态，因为 Kafka、API、memory 和评测能力已经跨原草案落地，继续沿用会
把“已实现”和“后续目标”混在一起。

| Maturity / 成熟度 | Current scope / 当前范围 |
|---|---|
| Local Alpha complete | 十步 Runtime、CLI/Kafka ingress、SQL、Review Web/TUI、Lead Agent、API v1 transport、audit/replay、memory/governed context 的代码可控门禁 |
| Mock / fixture | 本地 CMDB/EDR/HIDS/TI/tag facts、脱敏样本、browser HTTP fixture；只验证 contract/flow |
| Deferred | Kafka result topics/worker pool、通用 durable event/SSE、Threat Hunting/Detection Engineering/Attack Simulation 自治 Agent、Knowledge RAG、Prometheus 全局态势；network/endpoint/web/email specialist 已由 PI-01G 完成 |
| Data-gated PI | 真实 provider/source feed、PostgreSQL/Kafka/K8s capacity/recovery、生产标签与校准、真实响应动作 |

任何 maturity 变化都先更新唯一完整性矩阵，再同步本工程契约；不得在本节新增平行 backlog。

## 二十三、Effective Decision 与受治理响应自动化

### 23.1 七层状态必须分离

- `AnalysisRun.decision` 是 immutable base detection decision。`SocDecisionPolicy` 仍是固定 Runtime
  中唯一生成它的组件；post-Runtime service 不得回写该对象。
- `SocDecisionTransitionRecord.stages` 必须严格按 `Base -> Memory -> Tenant Policy -> Effective` 排列，
  每一阶段的 `before` 必须等于上一阶段的 `after`。Memory reinforcement/override、租户策略 Decision 和
  最终 disposition 都必须保存 source/version/hash、selected rule 和 bounded contributors。
- `TenantPolicyDecision` 是独立运营判断。它可以在 reviewed `enforced` 模式改变 effective review/disposition，
  但不能改 `AnalysisRun.decision` 的 detection truth/confidence，也不能授予动作权限。
- `SocDecisionTransitionRecord.after` 与 `effective_disposition` 是四阶段解析后的最终有效结果；原 base 决策
  仍可独立统计和回放。
- `SocDispositionTransitionRecord` 是 operational disposition，不是 detection truth。
- `SocActionAuthorizationRecord` 是某个 exact route/action/target/adapter 的权限决定，不是 verdict、
  Memory、模型建议或 Approval Request。
- `SocActionExecutionRecord` 是外部调用事实；授权成功不等于执行成功，必须单独保存 attempt/result/error。

### 23.2 Memory 检索与改判

- 单条 correction/review note/domain finding 等 workflow signal 必须先经过
  `MemoryAdmissionService(soc.memory_admission_policy.v1)`。准入至少要求：明确人工 promotion/acceptance、
  足够理由、一个可复用 facet；未准入返回 `observed_only`，不得创建候选。Kafka/batch repeated pattern
  继续走独立聚合门槛，但最终仍只创建 pending candidate。
- Same-alert Memory validation may deliberately seed one record per alert only when the source/candidate/target are
  all explicit `eval_fixture`, `decision_impact=none`, data class is `simulation`, the database is isolated, and the
  command requires an explicit in-sample confirmation flag. Such records validate wiring only and must never be
  counted as production lessons, Memory quality, generalization evidence, or the expected alert-to-Memory ratio.
- Production-quality claims must use the held-out Memory eval contract. Historical `agent_response` is prediction
  material, not human truth. A directive override is counted only when the actual post-Runtime transition lists the
  exact `M-*` Memory contributor; retrieval metadata or `applicable` status alone is insufficient.
- Operational Kafka/batch learning uses `soc.memory_pattern_aggregation.v3`: one alert may create one immutable
  observation, but only a repeated, conclusive, >=80% outcome-consistent cohort with a consensus strong anchor may
  create one pattern-level candidate. Candidate summary/content must state whether the cohort is risk or benign,
  its verdict distribution, applicability, representative reasons and exceptions; a generic recurrence sentence
  or copied alert body is insufficient for confirmation.
- v1/v2 are historical deserialization values,
  not defaults for new observations. Tenant-specific semantics live behind `SocMemoryProfileRegistry`; generic Core,
  contracts and persistence must not import PingAn raw aliases.
- Every projected confirmed Memory must create one idempotent `SocMemoryUseRecord` after effective-decision
  reconciliation. It freezes exact memory version/content/facets hash, retrieval/applicability result, base/effective
  verdict and transition effect. Final analyst correction or canonical external disposition creates append-only
  `SocMemoryFeedbackEvent`; it must not rewrite either the source run or Memory record.
- `SocMemoryHealthRecord` is a CAS-updated read model derived from uses/feedback. Every high-trust contradiction
  creates `SocMemoryRevisionProposal`. When the active directive targets benign/false-positive but final high-trust
  truth is risk, the disable-only `soc_memory_safety_monitor` must disable retrieval immediately; it cannot enable a
  record. Revision/deprecation remains a reviewer action. Memory still never grants action authority.
- Explicit run promotion and correction must resolve the current `SocMemoryProfile`, project the exact persisted
  `AnalysisRun`, and call the Profile applicability builder. A persisted Pattern environment is server-owned and
  must override caller-supplied metadata. Manual entry must not silently fall back to broad generic facets.
- 普通 `ReviewNoteCommand` 的显式提升只能使用类型化 `promote_to_memory=true`；不得从自由格式
  metadata 暗示提升。Lead Agent acceptance 的“足够理由”必须检查人工 `acceptance_reason`，不得用
  模型回复正文长度代替人工判断。
- candidate 和 Runtime query 必须复用 `memory/facets.py` 的 vendor-neutral facet 语义。`alert_id/run_id`
  只能作为 lineage metadata；不得作为 match facet。`rule_code`、environment、scenario、role 和 behavior
  均可缺失；generic Memory Kernel 不得规定一个所有厂商必填的多维联合硬键。Tenant Profile 可以基于
  已存在的 canonical facets 定义版本化 compound cohort/applicability，但必须保留 ruleless fallback 和
  context-only/decision-authority 边界。
- PingAn Profile v7（feature schema v5）把稳定 `rule_code`（无 code 时可用稳定 `rule_name`）投影为 canonical
  `detection_key`，但该 key 只表示规则大类，不得单独复制历史 verdict。Profile 必须从 canonical
  rule name 生成版本化 `detection_signature`；不得用 `alert_id/run_id` 合成任一检测身份。
  `detection_key`、`detection_signature` 与 deterministic `behavior_fingerprint` 同时存在时，必须使用三者的
  compound signature 隔离 cohort；同 key、不同 detector name 或 behavior、相反结论必须形成独立候选。
  detection-only cohort 只能产生 rule-context `REVIEW_HINT`，service 必须拒绝其 future-match directive；
  behavior-only cohort 仍可服务没有稳定规则身份的告警。二者都缺失时 observation 不准入。
- Generic Profile v2 / feature schema v2 可从 canonical destination transport/port 投影 `network_service`，
  并从 bounded current-alert evidence 提取标准 CVE 为 `vulnerability_id`；不得把 source/destination IP 或
  ephemeral source port 编入 behavior fingerprint。Pattern signature 必须冻结有界 facet 白名单，不能因新
  facet 超过 `MemoryPatternSignature` 的 20-group contract。
- PingAn Profile v7（feature schema v5）必须从 canonical typed entities 生成 core/detail 两层 behavior component。Core 可包含
  process image/path、稳定 command module/switch 名、parent service 和 typed target class；detail 可保留
  `target_file:SAM|SYSTEM` 等精确观察；network service、CVE 和版本化 `attack_behavior_family` 可参与
  同类拆分。`IP/host/account`、alert/run lineage 和 ClassId 等随机参数不得进入
  fingerprint。平铺 EDR `str_suspicious_file` 必须先由 PingAn Adapter 转成带 provenance 的
  `endpoint_action_target`；path-shaped `str_ioc_value` 必须保留为独立 `observed_artifact`，Profile
  不得读取供应商原始别名或把两者合并。Profile 必须把 `protocol:*`、`http_method:*`、
  `network_service:*`、来源 classification family 和 generic `scenario:web_attack` 标记为 weak behavior；
  CVE、MITRE、process 和其他当前 reviewed component 才可投影为 strong。决策型 compound applicability 必须精确匹配
  environment、detection key、detection signature、behavior fingerprint 和 `behavior_strength=strong`。
  weak-only compound 最多产生 rule-context candidate，不能附加 future-match directive。
- `SocMemoryApplicabilitySpec` 可声明严格受限的 context-only lane：compound record 的 exact
  detection/signature/environment/strong-class 匹配、exact behavior fingerprint 缺失且至少一个
  `behavior_component_strong` 重叠时，可以 `partial` 方式进入 `M-*` 推理上下文。协议或 HTTP 方法等
  weak-only overlap 不得召回。该投影必须显式标记
  `context_only_allowed=true`，在 token 排序中晚于 exact match，并且不得应用
  `SocMemoryDecisionDirective`。没有 component overlap 的同-rule 记录仍为 not applicable。
- 每个投影给分析模型的 confirmed Memory 必须使用 typed
  `AnalysisMemoryContextComparison` 明示 `use_mode`、`shared_facets`、`current_only_facets`、
  `memory_only_facets`、required-facet 命中/缺失、exclusion 命中和 applicability reason codes。
  比较只能由 Runtime 根据当前 query 与冻结 record facets 确定性生成；模型和客户端不得自报、扩大或
  删除这些匹配事实。
- `context_only` 是 deterministic directive authority 的否定，不是 semantic relevance 的否定。模型可在
  检查 Business Lesson 的 applicability/generalization/invalidation 后，让该 `M-*` 影响 Base Decision，
  且必须在 `decision_context_refs`/`R-*` 中留痕；但它不得形成 Memory Decision、授权动作或跳过 Tenant
  Policy。一次 Run 中同一 Memory/version 仍只能记录一种最终 use effect。
- 禁止仅因为 Memory 无 Directive、只有 partial/context-only 命中、IP/host/account 不同，或缺少可选
  CMDB/TI/EDR enrichment 而默认输出 `suspicious`。同样禁止仅因同 rule/detection 命中就复用历史结论。
  服务、漏洞、行为族、执行结果、授权范围或显式失效条件等实质差异必须阻止结论迁移；无 Memory 时仍
  必须从当前 `E-*`、通用 `S-*`、Adapter `A-*` 和 tenant knowledge `C-*` 给出最佳受支持 Base verdict。
- Before accepting either exact or context-only applicability, the resolved tenant Profile may reject substantive
  canonical scope conflicts such as different network service, CVE, or attack-behavior family. Compatibility with
  older records may derive these scopes from canonical `behavior_component*` prefixes, but never from tenant raw
  aliases. A same-rule cross-behavior record rejected here must count as `skipped_not_applicable`.
- 没有 typed applicability 的 legacy record 最多作为 bounded `M-*` 背景存在，即使历史上携带 directive
  也不得改判。确定性 Memory Decision 必须同时满足 record `decision_impact=detection_decision`、typed
  applicability 和当前 projection `status=applicable`；客户端 metadata 不能恢复该权限。
- Reviewer 可在 confirm 时通过 `record_applicability` 收窄候选 scope，只能保留/缩小原 required 值域，
  或把候选 optional facet 提升为 required；不得移除强锚点、切换 profile/feature schema、降低阈值、
  扩大值域或扩大 context-only missing/similarity 集。生成或显式提交的 directive 必须包含最终所有
  required facet keys。
- Profile/feature schema 升级必须 fail closed。PingAn v2-v5 typed records 不得被 v6 query 静默解释、迁移或
  继承 directive；必须重新聚合、审核和激活。Profile 投影若同一 IP 同时存在于 generic `entity=ip:*` 与
  typed `role_entity`，只移除重复 generic facet；不得把 IP 变成 required facet，跨 IP 行为泛化必须保留。
- Memory 的 fixed window 只定义重复 observation 的候选聚合范围，不是 Memory 生命周期。Generic Profile
  默认 24h；tenant profile 可声明版本化 bounded default，PingAn Profile v7 默认 30d。显式 operator/eval
  policy 可覆盖 Profile 默认值，但必须冻结进 observation；Profile 变更不得静默重写旧窗口。当前
  `window_start/window_end` 只保存在 source metadata；pattern candidate 的 90 天治理有效期从候选生成
  时开始，人工确认后 repeated-pattern record 再从确认时间获得独立的 90 天有效期，review interval
  默认 30 天。确认时 retrieval activation 另有显式截止时间和复审周期，且不能超出 record validity。
  旧实现产生的 repeated-pattern record 只有在 `valid_until <= created_at`、即“记录创建时已经过期”时，
  才可由 `set_retrieval_activation()` 在同一版本迁移和 mutation audit 中执行一次
  `soc.memory_pattern_legacy_validity_repair.v1`；正常生命周期到期的 Memory 仍必须 fail closed，禁止
  借 activation 静默续期。环境是服务端配置的 `dev|stg|prd` 运行
  边界，不从 topic、IP 或供应商自由字段推断。Runtime 在 Profile/query 前绑定显式 batch/daemon
  environment，或一致的 `SOC_MEMORY_ENVIRONMENT` / tenant-policy / automation environment；冲突配置
  必须 fail closed。
- 正式 Memory Center 的一级对象是跨 fixed window 稳定的 `lineage_key` Pattern，不是
  `aggregation_key` cohort。列表/搜索/详情必须由 Repository + Core Service 生成：分别显示 lineage
  observation 总数、distinct source 数、Profile fixed-window 数、candidate 冻结快照数和后续 reinforcement 数。
  例如同一 Sliver 模式跨窗口形成 `6 + 1 + 1` 时，运营视图必须显示 1 个 Pattern、8 条 observation、
  3 个 window；原始 observation 和 window lineage 仍完整保留用于 replay/audit。
- 人工提前提炼不得成为 Memory Center 之外的孤立治理对象。若 source run 已存在 Pattern observation，
  新 Candidate 必须持久化该 observation 的 stable lineage；对旧 Candidate，Repository read model 只允许按
  exact `source_run_id`、alert、tenant、Profile/schema 和 environment 兼容性做只读补链。禁止用 rule code、
  文本或相似度猜测 lineage；补链不得修改 observation/candidate/record，也不得增加 support/distinct-source 数。
- Memory Center 默认查询必须在 Repository 层排除 `terminal_history` candidate-only lineage，并返回当前
  筛选条件下的历史数量；显式 `include_terminal_history=true` 才参与列表分页。禁止先分页再由 React
  隐藏历史项，否则历史数据会挤占活跃 Pattern 页面。历史详情仍可通过稳定 lineage 直接审计。
- Memory Center 根页面不得自动选中第一条 Pattern，也不得把第一条 Pattern 的关联告警研判绑定到导航
  请求。Pattern 详情先用 `include_observations=false` 获取治理摘要；source observations 由运营人员
  显式加载并按 20 条分页。审核中心各 route 只允许启动本视图需要的 Query，Candidate 列表不得旁路加载
  ReviewQueue、Approval Inbox 或全部 Memory records；详情只按 `source_candidate_id` 查询关联记录。
- Profile/schema 升级不得直接删除旧候选或重写 confirmed Memory。只有仍处于 `pending_review` /
  `confirmed_candidate` 的 repeated-pattern candidate，才可通过 typed supersession command 转为
  `superseded`；必须验证 same tenant、same source alert、same Profile ID、新版本/新 schema 与 newer
  successor，并原子保存 successor link、mutation audit 和 process event。confirmed record 继续按显式
  revalidation/deprecation 流程处理。
- SQL repository 必须在完整 eligible corpus 上分别执行 `soc_memory_record_facets` exact-facet lane 和
  text lane，与 scoped fallback 合并后再用共享 scorer 应用 candidate limit 和最终 top-K/token budget。
  禁止恢复“先取最新 200 条再评分”的实现；大量新但宽泛匹配的记录不能遮蔽旧的强相关 Memory。
- 固定 Runtime 查询必须使用 `soc.memory_retrieval_policy.v2`。多 lane 只负责 recall；每个 record 在返回
  前必须按 memory type 命中至少一个 exact strong anchor。Detection/benign/response lesson 可用
  detection key、rule code、scenario、behavior fingerprint、role/entity；procedure/negative memory 还可用
  skill/conflict；environment/identity facts 要求实体类 anchor；adapter mapping 要求 integration/product/source。
- `environment`、`source_type`、`category` 和普通 text 可排序或限定 scope，但不能作为 detection lesson、
  benign pattern 或 response hint 的唯一放行依据。未命中强锚点计入
  `skipped_missing_strong_anchor`，并保留 `anchor_match_reasons/matched_anchor_facets` 供 replay diff。
- `M-*` 自由文本只是 reasoning context，永远不是 `E-*` 当前告警事实，也不能直接产生 deterministic
  decision effect 或 action authorization。
- 只有 `SocMemoryCandidateReviewCommand(decision=confirm)` 可携带
  `SocMemoryDecisionDirective`。该指令不得从 candidate/record 自由文本、LLM 输出或标签自动推断。
- `effect=override` 必须至少声明一个 `required_facet_key`；`target_verdict=unknown` 不得 clear review。
  confirm 前 service 必须验证 required facet key 在 candidate 中存在非空值。
- 应用 directive 前必须重新加载 exact `memory_id/version/content_hash/facets_hash`，并验证 confirmed status、受治理 retrieval
  activation policy、actor/reason、record validity、activation validity、review due、minimum match score
  和全部 required matched facets。任一失败时只保留普通 `M-*` 上下文。
- 多条 override 指向不同 verdict 时 transition 固定为 `conflicted`、`needs_review=true`，并停止本轮
  disposition/action rule selection；禁止按最新时间、最高分或 list 顺序选第一条。
- Memory directive 可以改变 effective decision，但永不直接授权、执行动作或关闭 ReviewQueue。

### 23.3 自动策略与人工审批

- `SocAutomationPolicy` 是 server-owned、strict `extra=forbid`、tenant/environment/version/validity-bound
  动作策略。缺 `SOC_AUTOMATION_POLICY_PATH` 时不得选择 action/disposition automation rule；但如果租户
  policy 已启用，仍必须构建 effective-decision resolver 以保存四阶段 lineage。`shadow` 只能留痕；
  `enforced` 必须有 `reviewed_by/reviewed_at`。
- Policy selection 使用 effective decision，并不要求任何 Memory contributor。当前告警自身的
  evidence/model/Skill/context 可以独立匹配规则并获得 automatic authorization。
- Automation rule 可显式匹配 exact `rule_code`、`detection_key` 或已持久化
  `tenant_policy_rule_id`。若 tenant disposition 已是 ignored/closed/duplicate，带 action 的规则必须显式
  绑定该 tenant rule，否则禁止隐式覆盖关闭类 disposition。
- automatic action rule 必须显式声明 verdicts、evidence states、model names、Prompt versions、Decision
  Policy versions、minimum confidence 和 `needs_review`。模型、Prompt 或基础 Decision Policy 版本变化时，
  旧规则不得继续自动授权。
  若匹配 `needs_review=true`，还必须提供非空 `review_required_override_reason`；该例外只允许精确动作
  先执行，不删除 ReviewQueue、不清除 base/effective review state、不伪造 human contributor。
- `human_approval` 与 `automatic_policy` 是并列 authorization modes。Lead Agent proposal 默认仍走前者；
  模型不得通过 proposal 声称某条 automatic rule 已匹配。
- automatic authorization 只接受 exact registered descriptor：route/action/adapter ID 全等，
  `execute_supported=true`，`external_side_effect=write|destructive`，`idempotency_required=true`。目标必须
  由 canonical Runtime facts/role resolution 确定性解析，不能由模型自由文本或 provider response 选择。
- 实际执行还要求 `SOC_AUTOMATION_EXECUTE_AUTHORIZED_ACTIONS=true` 和 composition root 注入 registry。
  仅有 policy 文件不得触发调用。
- replay run 可以重算 transition 与 policy match，但 automatic external action 固定拒绝；不得因历史回放
  生成新的外部副作用或新的外部 idempotency identity。
- 当前 `SocAgentApprovalService.execute_approved_action()` 仍是 Alpha grant/preflight/token boundary；不能
  把它描述为已完成真实外部副作用。后续收敛必须让 human Grant 和 automatic Authorization 共用一个
  external execution service，不得复制 adapter 业务逻辑。

### 23.4 幂等、失败与审计

- decision/disposition/authorization identity 必须包含 source transition、selected rule、policy version/hash、
  exact action/target 和模式；重复 observer 运行返回已保存记录。
- external idempotency key 固定从 authorization key 派生，同一授权的 retryable attempts 必须复用它。
  默认最多 3 次；attempt 递增。`succeeded|failed_terminal|skipped` 不得再次执行。
- 授权过期后只写 `skipped` execution。target 缺失、adapter 缺失/不匹配、不支持 execute、非 write/
  destructive 或不要求 idempotency 均 fail closed。
- Post-analysis observer failure 不回滚已提交 base Runtime bundle，但必须写 sanitized operator log；不得因
  automation failure 把已完成分析伪装成失败或重复提交 Kafka offset。
- Migration `0023_governed_automation_and_memory_index` 拥有
  `soc_memory_record_facets`、`soc_decision_transitions`、`soc_disposition_transitions`、
  `soc_action_authorizations`、`soc_action_executions`。旧 Memory JSON 的 facet backfill 必须兼容 SQLite
  driver 返回 string/bytes 的情况。
- Migration `0024_decision_stages` 增加 tenant policy mode/review/application 索引，以及 Memory/Tenant stage、
  tenant decision ID 和 effective disposition 索引。完整四阶段对象仍保存在 transition payload 中。
- `soc automation lineage --run-id|--alert-id` 是当前 read-only operator surface。输出必须同时暴露
  decision before/after、disposition、authorization reason/mode 和每次 execution；不得输出 credential、
  provider header、rendered prompt 或完整敏感 response。

### 23.5 效能遥测、最终真值与规则建议

- `SocEffectivenessService` 是质量、自动化、规则和算力指标的唯一 Core read service。API/Web 不得直接
  扫描 JSON payload、在浏览器拼分母或复制聚合算法。SQL Repository 只读取索引列与既有 append-only
  lineage，返回 `SocRuleEffectivenessAggregate[]`；Core 再生成 `soc.effectiveness_snapshot.v1`。
- 统计窗口内同一 `alert_id` 只选择最新 `AnalysisRun`；旧 Run 计入 `superseded_run_count`，不得重复扩大
  告警量。aggregation mode 固定记录为 `latest_run_per_alert_sql_v1`。
- 质量真值只能来自高可信人工 correction/outcome、通过 mapping/trust/target gate 的外部 disposition，或
  sealed independent sample label。模型自报 verdict/confidence、自由文本 reason、close status 和无来源
  fixture 不得进入准确率、漏报率或规则误报率分母。分母为零时必须返回 `not_measured + value=null`，
  禁止返回看似优秀的 `0%` 或 `100%`。
- Detection truth 与 operational disposition 分开。`true_positive + ignored` 可以表达已授权测试；
  applied ignore 不能反向把技术真值改成 false-positive。技术漏报和错误自动忽略必须分别统计。
- Rule/detection group 使用 tenant、source type/system 与 canonical detection identity；`rule_code`、
  `rule_name` 均可缺失，只是供应商 alias。每组必须分别暴露 confirmed-risk share、rule false-positive
  share、AI triage accuracy/miss、自动忽略与错误忽略、模型用量/质量及 Memory use/contradiction。
  Confirmed-risk share 不是 detector recall；缺少未告警的攻击总体时不得宣称规则召回率。
- migration `0026_effectiveness_telemetry` 为 Run 增加 nullable verdict、duration、provider usage、Token 和
  output-quality 索引。它不回写历史 payload；旧数据 coverage 为空是合法状态。Provider 不返回 usage 时
  `usage_measurement_status=unavailable`，禁止按字符数估算并冒充 Token。
- `SocRuleOptimizationPolicy` 必须版本化。`SocRuleImprovementRecommendation.authority` 固定为
  `advisory`；推荐可以指向补标签、修 Adapter/Prompt、拆场景、调上游规则、保留完整研判或评估快速路径，
  但不得自动修改 Flink rule、Prompt、Memory、Tenant Policy、Automation Policy 或动作权限。
- 任何快速路径都不得仅匹配 `rule_code`。至少需要高量稳定 outcome、足够标签、实际模型消耗、精确
  behavior/applicability、已审核 Memory/Policy、无已知 wrong-auto-ignore 和抽样完整研判。新行为、反证、
  schema/Profile/模型版本变化或 Memory suspension 必须 fail back to full analysis。
- 规则改进的效果声明必须绑定旧/新版本、冻结 cohort、数据/配置/模型 hash 和 before/after 或 A/B 指标；
  必须同时验证误报下降、漏报不升、错误自动忽略不升。仅观察调整后的单周期相关性不能声称因果收益。
