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

Phase 1 就建立：

```bash
uv run ruff format --check .
uv run ruff check .
uv run pytest
```

Phase 2+ 增加：

```bash
uv run pyright
uv run pytest tests/contracts
uv run pytest tests/architecture
```

架构测试必须覆盖：

- `api/cli/tui/channels/ingestion` 可以 import `core`。
- `core` 不 import `api/cli/tui/channels/ingestion`。
- `pipeline` 不直接 import FastAPI/Kafka/Typer。
- `memory` 不能绕过 `soc_facts` 状态机直接注入 prompt。
- `tools` 的执行必须经过 `policy`。

## 三、项目分层

建议目录：

```text
soc_agent/
├── contracts/          # 所有跨边界 schema：API/Kafka/Event/LLM/Tool
├── normalizers/        # 外部厂商/flat payload -> canonical contracts
├── domain/             # 内部稳定领域对象；不暴露外部协议细节
├── protocols.py        # Phase 1 可替换依赖协议；复杂后再拆 protocols/
├── core/               # Runtime、状态机、service、validator、router
├── pipeline/           # 7 步流水线节点
├── policy/             # 权限等级、动作审批、risk gate
├── actions/            # action proposal、adapter registry、MCP/HTTP/vendor action adapters
├── tools/              # 工具注册和执行适配器
├── memory/             # soc_facts / lessons / prompt 注入
├── db/                 # repository + migrations
├── demo/               # 可重复产品演示编排；只调用 core/repository/actions，不写业务决策
├── eval/               # 离线评估与 fixture runner；只做回归验证，不作为生产路径
├── queue/              # Phase 1 memory queue；Phase 4 PG queue
├── api/                # Gateway/FastAPI 入口，只做 transport
├── cli/                # Headless CLI 入口，只做 transport
├── tui/                # DeerFlow-style terminal workbench，只做 presentation/session
├── channels/           # IM channel adapter，只做 transport/session
├── ingestion/          # Kafka/Redpanda consumer，只做后台 ingestion adapter
└── observability/      # trace、metrics、audit writer
```

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
- `demo/` 只编排现有 service/repository/actions 生成可演示数据，不直接拼接业务 view、不绕过状态机、不冒充真实集成完成。
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
- Evidence priority for PingAn observable facts is fixed as: deterministically parsed raw message
  (`raw_message/high`) > Zeus structured fallback (`raw_structured/medium` or `low`) > canonical
  processed field (`processed_field/low` when raw-first policy is active).
- `FieldTrust` must describe the field actually used. A fallback structured field must never inherit
  the selected raw message's high trust merely because both live in the same `zeusRawLogs[]` item.
- All parseable messages are retained. One message is selected as primary evidence and the remaining
  paths are supplementary evidence; selection and ordering must be deterministic and replayable.
- `LLMAnalysisRequest` may include only `BoundedAnalysisEvidence`: per-field and total-size bounded,
  parser/provenance annotated, and separated into primary/supplementary content. It must not dump the
  unbounded vendor payload into the prompt.
- Prompting and post-analysis evidence validation must share the same bounded projection function.
  `AnalysisResult.evidence[].source` must name an approved section, exact projection path, or a
  bounded evidence `source_path#parsed.field.path`; its scalar value must be present under that
  declared bounded source. Every item produces an
  `AnalysisEvidenceGroundingItem`; any ungrounded item adds
  `ungrounded_analysis_evidence` to deterministic review reasons.
- Parser failure is explicit: preserve raw text, emit a warning, expose only bounded text to the
  analysis node, and keep structured fallback candidates at reduced trust.
- Every selected raw message must emit `MessageSchemaObservation`. `recognized` means parser grammar
  success, `degraded` means partial/nested decode warnings, and `unsupported` means no deterministic
  parser output exists; none of these statuses is a verdict or probability.
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
- 若旧 run 不存在，service 返回 not-found 语义；若旧 run 没有可 replay 输入，必须 fail-fast，不允许猜测输入。

Correction 约束：

- correction 是人工覆盖当前 operational decision，不删除或覆盖原始 `AnalysisResult`。
- 每次 correction 必须追加 `CorrectionRecord`，记录 previous verdict、corrected verdict、actor、reason、evidence 和时间。
- correction 只能把候选知识标记为 `pending_review`；不能直接生成 confirmed fact、lesson 或自动处置规则。
- correction 后仍保持 `automation_allowed=False`。

Decision audit 约束：

- `DecisionAuditRecord` 是 analyze/replay/correct 的结构化审计摘要，不替代完整 `AnalysisRun.run_payload`。
- `DecisionAuditRepository.save_audit_record()` 必须在 service 边界调用，入口层不能绕过 service 自己写审计。
- `soc_decision_audit_log` 必须至少记录 `run_id`、`alert_id`、`actor`、`action`、`input_hash`、previous/final verdict、confidence 和可扩展 payload。
- replay/correction 必须生成新的审计记录，不覆盖历史审计记录。
- 审计写入失败在 Phase 1 应暴露为执行失败或明确错误，不允许假装成功。
- analyzer decision audit payload 必须记录 `decision_policy_version`、`confidence_source`、
  `confidence_is_calibrated`、`calibrated_probability`、`calibration_profile_version`、
  `evidence_state` 和完整 `review_reasons`；不能只保存一个 raw confidence。

Analysis persistence / 分析持久化约束：

- 一次 analyze/replay 的主业务结果必须通过 `AnalysisPersistence.save_analysis_bundle()` 原子写入
  `AnalysisRun`、`AlertSummary`、可选 `ReviewQueueItem` 和 `DecisionAuditRecord`；生产 SQL repository
  不得在 service 中逐表 commit 后假装为完整成功。
- 任一 bundle row 写入失败必须回滚全部四类写入。Normalization maintenance 是成功主写入后的
  fail-open side path，可以单独更新 run 的 monitoring result，但不能破坏已提交业务事务。
- `AnalysisRun.status=failed` 必须带 `RuntimeFailure`，至少包含 failed step、稳定 kind、retryable、
  sanitized error type/message。Provider 原始响应、header、secret 和未裁剪异常不得写入 run/audit。
- 不可重试失败进入 summary + ReviewQueue + audit；可重试失败保留 failed run/summary/audit，但不立即
  创建人工工单，Kafka 不 commit offset，并允许同一 idempotency key 重新执行。

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

- `SocCorrelationService` 是 Phase 2 相似告警、历史关联和可复用证据的只读业务入口；CLI/API/TUI/Web/Lead Agent 都不能绕过 service 直接拼 correlation result。
- `CorrelationQuery` / `CorrelationResult` / `CorrelationMatch` 是 source handler、security scenario recognizer 和 unified investigation report 的稳定输入；不得让每个 EDR/APT/HIDS/WAF/F5 handler 或反弹 shell/webshell/横向移动识别器自己发明相似告警结构。
- MVP correlation 只能依赖 `AlertSummaryRepository` 和 `InvestigationEvidenceRepository`，不调用 LLM、不调用 MCP、不执行 action、不修改 run/summary/review/memory。
- correlation match 必须携带结构化 `match_reasons`，例如 `same_detection_key`、`same_rule_code`、`shared_ip`、`shared_user`、`same_asset`、`same_source_type`、`reusable_evidence`；不能只给自然语言解释。
- correlation 结果可以进入 `InvestigationContext`、Lead Agent bounded artifact、Web/TUI 展示和后续 domain triage request，但不能自动改 `AnalysisRun.decision`、不能自动关闭 review queue、不能直接生成 confirmed memory。
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
- `soc eval scenarios PATH` 是 vendor-neutral deterministic scenario eval 入口；它直接消费 alert JSON 文件/目录，输出 taxonomy version、taxonomy keys、covered keys、missing keys、`vendor.unmapped` 计数和 per-sample findings。`--baseline-json` 只生成 replay diff 报告，不自动失败、不写业务库、不生成 memory。
- domain/scenario eval report 必须输出 taxonomy version、taxonomy keys、covered keys、missing keys 和 `vendor.unmapped` 计数，作为 replay diff 基线；eval 仍只读样本，不写业务库、不生成 confirmed memory。

Main orchestrator 约束：

- `SocMainOrchestratorService` 是 PA-11 unified investigation report 的 core service 入口；CLI/API/TUI/Web/Lead Agent 后续展示统一报告时不得绕过它自己拼 analyze/action/domain/review 链路。
- `SocMainOrchestratorRequest` / `UnifiedInvestigationReport` / `SocOrchestratorRouteStep` / `SocOrchestratorReviewContextSummary` 是主控报告的稳定 contract；前端和 eval 只能消费这些结构，不能消费 handler 内部私有对象。
- Main orchestrator 只能调用已有 core service、router、policy/dispatcher、adapter registry 和 domain triage service；不能直接读写 repository、不能直接调用 MCP/tool、不能直接执行高风险动作、不能确认 memory。
- PA-11 report 中的 read-only action result 必须先写 `InvestigationEvidence`，再通过 evidence refs 进入 domain finding 和 review context；不能让 route step payload 直接改变 verdict。
- report metadata 必须显式标记 `handler_output_only`、`writes_db`、`executes_high_risk_actions` 等边界语义；eval 必须验证这些字段，防止 demo 链路被误当生产处置链路。
- `PA-12` 真实 PingAn MCP/API 替换只能替换 action adapter/provider/config，不能改变 Main Orchestrator contract；真实 endpoint/凭证缺失时状态为 Waiting，不允许用本地 mock 冒充完成。

Unified investigation view 约束：

- `UnifiedInvestigationView` / `InvestigationTimelineItem` 是 ReviewQueue 打开单个工单时的只读分析师视图 contract，不是新的 source of truth。
- `SocReviewService.get_investigation_context()` 是生成 `InvestigationContext.correlation_result`、`InvestigationContext.domain_triage_results` 和 `InvestigationContext.investigation_view` 的唯一 service 边界；API/Web/TUI/Lead Agent 不能绕过 service 自己拼等价结构。
- `UnifiedInvestigationView` 只能消费已有 read model 和只读 handler output：`AnalysisRun`、`AlertSummary`、`DecisionAuditRecord`、`CorrelationResult`、`SocDomainTriageResult`、`InvestigationEvidence`、`SocExternalDispositionRecord`、`SocMemoryCandidate`、`SocMemoryRetrievalResult`。
- `evidence_timeline` 只是投影；不能替代 `soc_analysis_runs`、`soc_decision_audit_log`、`soc_investigation_evidence`、`soc_external_dispositions`、`soc_memory_candidates` 或 `soc_memory_records`。
- 生成 unified view 不能写 DB、不能执行 action、不能发起 MCP/tool、不能确认 memory、不能修改 `AnalysisRun.decision` 或 `ReviewQueueItem.status`。
- Web/TUI/Lead Agent bounded artifact 可以展示 unified view 的计数、Top 关联、domain finding 和 timeline；任何 close/correct/approve/memory review 仍必须调用对应 core service。

PingAn SOC capability onboarding 约束：

- 平安 SOC 工具、MCP、skill、研判经验和处置经验进入项目之前，必须先整理成 capability card；来源、适用场景、输入字段、输出结构、风险等级、失败模式和脱敏验收样例必须明确。
- capability card 只能分类落到以下稳定 artifact：domain skill、normalizer/field trust rule、read-only action adapter、high-risk action adapter、domain handler、eval fixture、memory candidate。不得直接把一段经验文本粘进生产 prompt 后生效。
- 内部系统 endpoint、账号、token、cookie、真实敏感样本不得写入仓库；真实连接只能通过本地 config、environment secret 或部署 secret 注入。
- read-only 工具经验必须通过 `SocActionAdapterRegistry` / MCP-backed adapter 落地，结果写 `InvestigationEvidence`；不能让 Lead Agent 直接用自然语言调用内部系统。
- 处置类经验必须走 approval request / approval grant / dry-run / execute boundary；未经过 staging smoke 和 adapter-level audit 前，生产 execute 只能保持 no external side effect。
- 经验记忆必须先进入 `pending_review` 或 eval fixture；只有人工确认、版本化和可回滚后才允许作为 confirmed memory 或 active lesson 影响后续判断。
- `.notes/ai_soc/capabilities/pingan/source-docs/` 中的历史 prompt 原文必须先按 `.notes/ai_soc/capabilities/pingan/knowledge-decomposition.md` 拆解；不得整体复制进 Lead Agent prompt、analysis node prompt 或 public skill。
- `skills/public/soc-*` 只能包含跨客户通用研判方法；平安内部域名、部门、账号、BU/PA code、路径、白名单、具体 `rule_code`、模板 ID、策略 ID、operateType 等必须进入 tenant memory、adapter mapping、policy/config 或 eval fixture。
- 平安环境知识进入 memory 时必须带 tenant scope、source doc/section、status、validity 和 evidence refs；默认 `pending_review`，不能直接 confirmed。
- 平安字段名和字段别名只能出现在 adapter/normalizer/mapping tests 或脱敏 fixture 中；core contract、public skill 和 Lead Agent prompt 必须消费 canonical fields。
- 平安处置经验如果需要外部事实查询，必须先建 read-only MCP/action adapter；如果会改变外部状态，必须是 high-risk/analyst-write action 并走 approval。

External disposition sync 约束：

- 外部预警/工单/处置系统的状态和理由同步必须走 vendor-neutral `SocExternalDispositionEvent`，Zeus 只是第一个 adapter；core service 不能出现 Zeus 专属分支。
- `SocExternalDispositionEvent` schema version 固定为 `soc.external_disposition.v1`，至少包含 `external_system`、`external_case_id`、`external_status`、`updated_at`、`raw_payload_hash`，可选包含 `tenant_id`、`source_event_id`、`source_version`、`external_alert_ref`、`soc_alert_id`、`soc_run_id`、`soc_queue_id`、`external_reason`、`external_tags`、`operator`；多租户部署时 `tenant_id` 必须由认证上下文或 adapter 配置补齐。
- 外部系统 adapter 只负责认证、解码、字段映射、redaction、幂等键生成和调用 `SocExternalDispositionService`；adapter 不得直接写 repository、不得直接调用 `SocReviewService.correct()`、不得直接写 memory 或 skill。
- `SocExternalDispositionService` 是外部反馈写入本地 audit、review/correction、external disposition record、memory candidate 和 skill improvement candidate 的唯一 service 边界。
- 当前 External Disposition Contract MVP 已实现 contract/mapper/service/repository 边界；`SocExternalDispositionService.apply_event()` 对 high-trust、mapped、唯一定位且可映射 verdict 的事件复用 `SocReviewService.correct()` 同步 operational correction / review close。mapped、可定位且带 reason 的事件可以通过注入的 `SocMemoryService.propose_candidate()` 生成 pending memory candidate；低可信只能生成候选不能改判，未知状态、无法定位、无 reason 或非可学习状态不能生成候选。
- `soc_external_dispositions` 是当前 `SocExternalDispositionRecord` 的 SOC business store 表；`SqlAlchemyAlertRepository` 实现 `SocExternalDispositionRepository` 方法。生产和本地持久化都必须通过 migration `0009_external_dispositions` 或 `create_soc_tables()` 创建该表。
- 外部处置历史必须通过 `SocReviewService.get_investigation_context()` 聚合到 `InvestigationContext.external_dispositions`；ReviewQueue API/TUI/Web/Lead Agent bounded context 只能消费该字段，不能直接查 `soc_external_dispositions`。
- 幂等键固定形态为 `external_disposition:{tenant_id|default}:{external_system}:{external_case_id}:{source_event_id|source_version|updated_at_hash}`；重复 webhook、Kafka offset 回放或 polling 重扫不能重复关闭 review queue、重复改判或重复生成 memory candidate。
- 目标定位顺序必须是明确本地引用优先：`soc_queue_id` -> `soc_run_id` -> `soc_alert_id` -> 已绑定 `external_system + external_case_id` -> 弱关联；弱关联不能唯一命中时只能保存 unmatched record，不得自动改判。
- 外部状态必须通过可配置 mapping 转换为 canonical status，例如 `closed_true_positive`、`closed_false_positive`、`closed_benign_true_positive`、`suppressed`、`escalated`、`ignored`、`duplicate`、`unknown`；未映射状态只能进入 `unknown/unmatched`，不能自动更新 operational decision。
- 外部 free-text reason 默认只是 case feedback；只能生成 `SocMemoryCandidate(status=pending_review)` 或 `SkillImprovementCandidate(status=pending_review)`，不得直接成为 confirmed memory、active lesson、active skill 或 prompt 修改。
- 外部处置同步必须记录 source surface、operator、mapping version、apply status、target refs、idempotency key 和 audit event，支持 replay diff、撤销和客户审计。
- Webhook、Kafka、polling 和 manual import 都是 transport adapter；进入 core service 前必须归一成同一 `SocExternalDispositionEvent`，不能为每种 transport 复制业务状态机。

SOC memory tracking 约束：

- 业务记忆必须实现为 typed memory record + facets + retrieval policy，不得实现为 `topic/rule_code/scenario` 等字段的联合等值主键。
- `rule_code` 只是 vendor alias 的一种；平安 `rule_code`、EDR `signature_id`、SIEM `analytic_id`、Sigma id、Splunk analytic id 等都只能进入 `facets.detection.vendor_aliases`，不能成为跨公司必填字段。
- `facets.detection.canonical_key` 是推荐的跨供应商检测标识；缺失时必须能通过 `source_type/product/category/rule_name/MITRE/raw fingerprint` 生成弱 key，或退化到 topic/scenario 检索。
- topic、canonical detection、vendor aliases、scenario、entity、environment 都是可选检索 facets；缺失任意一个 facet 时系统仍必须能工作，只是召回分数降低。
- 具体 IP、UM、host、URL、file hash、process hash 等实体默认只能作为 evidence refs、query dimensions 或 case memory，不得默认成为长期全局 memory 主键。
- TUI/Web/Kafka/Lead Agent/domain handler/external disposition sync 只能生成 `SocMemoryCandidate`；不得直接写 `confirmed` fact 或 active lesson。
- 所有 memory candidate 必须包含 source surface、source run/review/evidence refs、idempotency key、status、confidence、proposed content、facets、evidence refs 和 reviewer/audit fields。
- 当前已实现 DB-first candidate persistence、confirmed-memory boundary 和 retrieval policy MVP：`SocMemoryService.propose_candidate()` 必须强制写 `pending_review`，并保持 `runtime_decision_allowed=false`；`SocMemoryService.list_candidates()` / `get_candidate()` 是 API/CLI/Web/TUI/Lead Agent 查询候选记忆的 service 边界；`SocMemoryService.review_candidate()` 是 confirm/reject/deprecate/expire 的唯一状态机边界；`SocMemoryService.find_relevant_records(SocMemoryQuery)` 是 confirmed memory 检索的唯一 service 边界。
- 候选记忆来源桥接固定在 `soc_agent.memory.sources.SocMemoryCandidateSourceBridge`：新增来源必须先构造 `SocMemoryCandidateCreateCommand`，再经 `SocMemoryService.propose_candidate()` 写入，不得在 Web/TUI/Kafka/Lead Agent/domain handler 内直接拼 repository row。
- `SocReviewService.correct()` 是 correction -> pending memory candidate 的 service 边界；当注入 `MemoryCandidateRepository` 时，它会把 candidate id 回写到 `CorrectionRecord.memory_candidate_id`、audit payload 和 event payload。外部反馈如果复用 correction 链路，不得再重复创建第二条 correction candidate。
- `SocReviewService.add_note()` 是 ReviewQueue review note -> pending memory candidate 的 service 边界；`soc review note`、Web/TUI note action 和后续 Lead Agent/Kafka note source 都必须通过它或同级 service 方法进入 `SocMemoryCandidateSourceBridge`，不得直接写 `soc_memory_candidates`。Review note source type 固定为 `review_note`，幂等键必须至少覆盖 queue/run/alert/note，并可附加 scenario/domain/finding refs。
- `SocDomainTriageResult/SocDomainFinding` 可以通过 source bridge 生成 domain finding candidate，但必须由显式 service/entry 调用；只读 investigation context assembly 不能在渲染/读取过程中写 candidate。
- `soc_memory_candidates` 是当前 `SocMemoryCandidate` 的 SOC business store 表；`SqlAlchemyAlertRepository` 实现 `MemoryCandidateRepository` 方法。生产和本地持久化都必须通过 migration `0010_memory_candidates` 或 `create_soc_tables()` 创建该表。
- `soc_memory_records` 是 `SocMemoryRecord` 的 SOC business store 表；`confirm` decision 会从 candidate 派生一条 `SocMemoryRecord(status=confirmed, retrieval_enabled=false)`，生产和本地持久化都必须通过 migration `0011_memory_records` 或 `create_soc_tables()` 创建该表。
- `SocMemoryCandidate.idempotency_key` 是候选记忆重复抑制边界；同 key 重放必须返回既有 candidate，不得重复写入或重复发出 memory update event。
- `SocMemoryCandidate.status=pending_review` 只能表示待评审建议；Web/TUI/Lead Agent 可以展示它，但不得展示为 confirmed fact、active lesson 或已生效策略。
- `confirm_candidate` 只表示候选通过初审，不创建 `SocMemoryRecord`；`confirm` 才创建 confirmed record。`reject` 只更新 candidate 状态，不创建 record；`deprecate` / `expire` 必须同步更新 linked record 状态和 deprecation metadata；非法状态迁移必须 fail-fast。
- Gateway memory candidate API 路径固定在 `/api/soc/memory/*`：
  - `GET /api/soc/memory/candidates`
  - `GET /api/soc/memory/candidates/{candidate_id}`
  - `POST /api/soc/memory/candidates/{candidate_id}/review`
  - `GET /api/soc/memory/records`
  - `GET /api/soc/memory/records/{memory_id}`
  - `POST /api/soc/memory/search`
- `soc memory list/get/review/search` 和 `soc memory records list/get` 是本地/运维查询、评审和检索记忆的 headless CLI；它只能调用 `SocMemoryService`，不能直接查 repository row。
- Kafka daemon 生成 memory candidate 时，幂等键必须包含 `topic/partition/offset` 或 run id；重复消费不能增加重复 fact 或污染 evidence count。
- `pending_review`、`confirmed_candidate`、`confirmed` candidate 和 `SocMemoryRecord(retrieval_enabled=false)` 默认都不进入全局 prompt 注入；只有 retrieval policy 显式允许、未过期且 retrieval-enabled 的 memory record 才可以进入 `InvestigationContext.relevant_memories`。PromptBuilder 注入仍是后续独立切片。当前 `InvestigationContext.memory_candidates` 只用于展示和人工评审，不参与 runtime verdict。
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
- `InvestigationEvidence` 是只读查询、定位、EDR process tree 等 investigation action 的结果证据，不是原始告警证据、不是 confirmed memory、不是 operational verdict。
- `InvestigationEvidenceRepository.save_evidence()` 只能在 service/action boundary 调用；CLI/API/TUI/Web/daemon 入口不能自己拼 evidence 绕过 dispatcher。
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
- API/TUI/Web close/correct 必须构造 `ServiceRequestContext`；`ActorContext.surface` 必须准确标识 `api` / `tui` / `web`。
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
- Phase 1 的 chat stream 是 deterministic shell/context loader，不调用真实 SOC Lead Agent；后续接 LLM/skills/MCP 时必须保留 Runtime 固定控制流和人工审批边界。
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
  - read-only adapter 成功执行后可以通过注入的 `InvestigationEvidenceRepository` 写入 `InvestigationEvidence`，并把 `evidence_id` 回填到 `SocAgentActionResult.payload`；没有 evidence repository 时 action result 仍然有效，但不会跨上下文复用。
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
  - Daemon path 的写入边界是 `SocDaemonService.submit_approval_request()`，内部只能调用 `SocAgentApprovalService.submit_request()`；`SocDaemonService.start()` 在 Phase 4 Kafka consumer 落地前仍保持未实现。
  - 真实 Kafka consumer、DeerFlow Lead Agent middleware、API router、Web/TUI 操作入口都不能直接 insert `soc_approval_requests`，也不能绕过 `SocAgentApprovalService` 自行构造 request/grant 状态流。
  - `soc_approval_requests` 表必须保存扁平索引字段和完整 `request_payload`；索引至少覆盖 `permission_decision_id`、`route`、`action`、`risk_level`、`status`、`requested_by_actor_id`、`created_at`。
  - `SocAgentApprovalService` 只能把 pending request 转成 `SocAgentApprovalGrant`；只有 `soc_approver` 或 `soc_admin` role 可以批准。
  - `SocAgentApprovalGrant.execution_token_id` 是一次性执行授权标识，不是 action result；生成 grant 仍不得执行封禁、隔离、MCP 调用等外部副作用。
  - `SocAgentApprovalGrantRepository` 是 approval grant 的持久化边界；`approve()` 在 repository 存在时必须保存 grant。
  - SQLAlchemy repository 必须持久化 `SocAgentApprovalGrant` 的 approve/consume 全量 payload，并提供按 `approval_grant_id` 和 `execution_token_id` 查询。
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
  - `endpoint.process_tree.lookup` 是 read-only endpoint investigation action，用于在没有真实 EDR MCP 时验证进程树查询、证据沉淀和 Lead Agent proposal bridge；当前实现为 `InMemoryEndpointProcessTreeLookupActionAdapter`，默认返回 mock EDR process tree，不代表真实 EDR 接入。
  - SOC Lead Agent 可以用 `<soc_action_proposal>...</soc_action_proposal>` 提出 `asset.lookup` / `asset.locate` 这类 read-only proposal；`SocLeadAgentActionProposalBoundary` 只能在注入 read-only router/dispatcher 时把它转成同一条 router/policy/dispatcher/registry 链路。
  - SOC Lead Agent 可以提出 `endpoint.process_tree.lookup` proposal，但必须先检查 `InvestigationContext.action_evidence` / bounded artifact `action_evidence`，复用已有新鲜结果，避免重复查询。
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
  - `soc mcp smoke CONFIG --route ... --json ...` 是 dev/staging read-only MCP path 的显式 smoke 入口；默认使用 `DeerFlowCachedMcpToolProvider`，`--dry-run` 只验证 adapter/tool availability，execute smoke 输出 `SocMcpActionSmokeReport`。该命令不是生产 daemon，也不是 Lead Agent 自主 tool runtime。
  - `SocMcpActionSmokeReport` 是 dev/staging smoke 的版本化报告 contract，必须记录 `duration_ms`、payload/result byte size、adapter/tool/config metadata、`output_fields` 裁剪状态、`mcp_result_keys`、失败类型和内嵌 `SocAgentActionResult`；config/load/registry/tool failure 也应输出结构化 report，方便脚本归档和接入评估。
  - `soc mcp tools` 是 read-only MCP smoke 的前置 readiness 命令，只允许列出 DeerFlow cached MCP tool inventory，默认不输出 input schema；`--include-schema` 才输出 schema，`--report-path` 可落盘。它不得调用 MCP tool，也不得输出 secret。
  - `soc mcp smoke --report-path` 和 `soc mcp tools --report-path` 只能写调用者显式指定的报告文件；报告文件可能包含业务 payload/result，默认不应提交到 git。
  - `backend/scripts/soc_dev_mcp_server.py` 和 `backend/samples/mcp/soc_dev_*.json` 只用于本地真实 stdio MCP smoke，不是生产配置。样例 `extensions_config` 使用 `$SOC_DEV_MCP_PYTHON` / `$SOC_DEV_MCP_SERVER` 环境变量传绝对路径，避免 DeerFlow stdio tool 执行时切换 cwd 后找不到相对路径。
  - `backend/scripts/soc_dev_mcp_server.py` 当前提供两个本地 mock read-only tools：`asset_lookup` 返回静态资产记录，`asset_locate` 模拟 Zeus/CMDB/asset_to_bu 远程归属定位并返回 `mocked=true`；这两个工具只能用于开发 smoke 和 proposal bridge 验证。
  - `soc chat tui --lead-agent --mcp-action-config PATH` 是显式 dev/staging 注入入口；不传配置时保持本地 in-memory read-only adapter，不得隐式扫描或自动启用任意 MCP action config。
  - `soc chat tui --lead-agent` 当前使用 SOC repository 写入 read-only action evidence；不传 `--mcp-action-config` 时默认包含本地 `asset.lookup` 与 `endpoint.process_tree.lookup` mock adapter；`InMemoryInvestigationEvidenceRepository` 只用于单元测试和无数据库的局部 service wiring。
  - 真实 dev/staging MCP smoke 不是当前阻塞项；只有拿到真实 endpoint/凭证后才替换本地 fixture 并保存 `soc.mcp_action_smoke_report.v1`。
  - Gateway approved action API 路径固定在 `/api/soc/approvals/*`：
    - `POST /api/soc/approvals/grants`
    - `POST /api/soc/approvals/actions/dry-run`
    - `POST /api/soc/approvals/actions/execute`
    - `POST /api/soc/approvals/requests`
    - `GET /api/soc/approvals/requests`
    - `GET /api/soc/approvals/requests/{approval_request_id}`
  - approved action API/TUI/Web 只能调用 `SocAgentApprovalService`，不能直接读写 repository 或绕过 token consume 边界。
  - Web approved action workbench 只能通过 `frontend/src/core/soc/api.ts` 调用 Gateway `/api/soc/approvals/*`；React component 不得直接拼后端 repository/DB 行为，也不得把 dry-run 展示成真实处置完成。
  - Web approval inbox consumption 只能读取 Gateway `/api/soc/approvals/requests*`，并通过 `/api/soc/approvals/grants` 把 pending request 转成 grant；前端不得直接修改 `SocAgentApprovalRequest.status`。
  - `soc review tui` approval inbox consumption 只能调用 `SocAgentApprovalService`：`/approvals` list pending request、`/approval APR-...` get detail、`/approve APR-... reason` create grant。TUI 不能直接访问 repository，不能把 approve 当作执行完成。
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
- Phase 1 实现允许 repository 先用 SQL 读取最近候选窗口，再用 Python 规则打分；正式 PostgreSQL 优化时可在同一协议下改成 JSONB/GIN 实体交集查询。
- 相似查询必须排除当前 `run_id`，并受 `limit` / `candidate_limit` 限制，避免把全库塞进上下文。
- LLM 后续只允许对 `SimilarAlertMatch[]` 候选集合进行排序、解释或提出补查建议，不直接决定数据库检索范围。

SOC repository 实现约束：

- SOC 业务表放在 `backend/soc_agent/db/`，不塞进 DeerFlow harness persistence。
- repository 可以依赖 SQLAlchemy 和 `soc_agent.contracts`，不能 import `soc_agent.core`、`pipeline`、CLI/API/TUI/ingestion。
- `soc_analysis_runs.run_payload` 保存完整 `AnalysisRun`，索引列只服务查询和筛选，不作为唯一事实来源。
- SOC schema migrations 放在 `backend/soc_agent/db/migrations/`，使用独立版本表 `soc_alembic_version`。
- 正式 schema 变更走 `soc db upgrade` / Alembic revision；`create_soc_tables()` 和 `soc db init` 只作为 Phase 1 本地开发辅助。
- SOC 当前持久化表包括 `soc_analysis_runs`、`soc_decision_audit_log`、`soc_alert_summaries`、`soc_review_queue`、`soc_approval_requests`、`soc_approval_grants`、`soc_investigation_evidence`、`soc_external_dispositions` 和 `soc_memory_candidates`。
- 单元测试可以用 SQLite in-memory 验证 SQLAlchemy 映射。
- 本地开发/人工验收可以用独立 SOC SQLite 文件，例如 `backend/.deer-flow/data/soc_agent_dev.db`，并通过 `SOC_DATABASE_URL=sqlite:////.../soc_agent_dev.db` 显式启用。
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
- fallback 必须显式记录 `fallback_reason` 和较低 `trust_level`，不能伪装成 raw message 同等可信。
- `ignore_processed_fields_for_reasoning=True` 只表示研判主输入不读加工字段；加工字段仍可保存在 `extensions` 中供审计、对比和冲突检测。
- `EvidenceLayer` 当前至少区分 `raw_message`、`raw_structured`、`processed_field`、`agent_inference`、`human_confirmed`。
- 平安 ZEUS/天眼 adapter 使用 `raw_message_first + structured_fallback`：
  - 优先读取 `alert.hitLog[].zeusRawLogs[].message`。
  - raw message 缺失时 fallback 到完整 `zeusRawLogs[]` 对象，并标记 `fallback_reason=raw_message_missing`、`trust_level=low`。
- 后续 `FieldTrust` / `RoleClaim` / `RoleResolution` / `ConflictReport` 建立在该 policy 之后：先决定主证据输入，再重建方向、角色和资产候选；不能在 normalizer 层直接下最终攻击方向或处置目标结论。

### Fact reconstruction 约束

`FactReconstructionResult` 是 LLM 分析前的事实层，不是最终研判结论。它解决的是“哪些字段可信、哪些角色候选互相冲突、后续分析应该带着什么不确定性进入”。

- runtime 固定在 `entity_extract` 后、`analyze_stub` / 后续 `llm_analyze` 前执行 `fact_reconstruct`。
- `FactReconstructionResult` 必须保存到 `AnalysisRun.fact_reconstruction`，随 run payload 一起持久化、replay、审计。
- `FieldTrust` 只表达字段可信度和是否参与事实重建；不能直接改变 verdict。
- source-specific adapter 负责把厂商别名转换成通用 `RoleClaim`；generic fact reconstructor 禁止识别 `attack_sip`、`alarm_sip`、`str_attack_ip` 等厂商字段。
- `RoleClaim` 必须分开 `evidence_trust` 与 `semantic_confidence`。原始 message 解析成功只能提高前者，不能自动证明厂商的 attacker/victim 语义正确。
- `RoleResolution` 当前角色包括 `source`、`destination`、`attacker`、`victim`、`impacted_asset`；状态包括 `observed`、`tentative`、`conflicted`、`confirmed`、`unresolved`。
- `response_target` 不属于事实重建结果。它由 action type、policy、调查证据、approval 和 adapter preflight 在动作边界确定。
- `ConflictReport` 必须结构化表达冲突类型、涉及字段和值，例如：
  - 同一角色多个候选值：`source_candidate_conflict`、`victim_candidate_conflict`。
  - 场景化角色不一致：`reverse_connection_attacker_destination_mismatch`、`reverse_connection_victim_source_mismatch`。
  - 源和目的重叠：`source_destination_overlap`。
- 禁止使用全局 `attacker == source` / `victim == destination` 约束。正向攻击、反弹 shell、恶意外联、C2、横向移动、代理/NAT/XFF 的角色关系不同；未知场景不得伪造跨角色冲突。
- 主 message、supplementary messages、structured fallback 都必须作为独立 claim source 参与冲突检查；supplementary 不能只进入 Prompt。
- 冲突裁决必须输出暂定值或 unresolved、支持/反对 claim IDs、语义置信度、证据缺口、人工核查清单和 automation guard；不能一边报告冲突，一边把值伪装成 confirmed。
- fact layer 的 `automation_allowed` 始终为 false；即使角色由人工确认，也必须再经过 action policy/approval。
- Phase 1 的事实重建只做 deterministic 规则；LLM 后续只能读取 fact layer 进行解释、补充候选或提出复核问题，不能绕过该层直接相信上游加工字段。
- raw message 存在时，canonical processed fields 默认低可信且不作为主推理输入；raw message 缺失时 structured fallback 必须保留低可信 warning。

### Nested message decoding / 嵌套 message 解码约束

- JSON parser 递归保留真实 object/array；JSON-in-string、HTTP headers、XFF chain 只通过 allowlisted decoder 处理，禁止无界递归猜测所有字符串。
- nested decoder 必须限制字段名、最大长度和解析深度；失败写 parser warning，不中断告警。
- `ParsedRawMessageEvidence.fields` 保留第一层解析结果，`decoded_fields` 保存受控二次解码结果；完整原文仍只以 `AlertInput.raw` / `AnalysisRun.input_payload` 为审计来源。
- nested JSON 严格解析失败后必须保留 `fields` 原始字符串和 parser warning，并可尝试保守 repair。
  repair 结果必须按字段策略验证根容器类型、非空结构、最大深度、最大节点数、key 长度、key source
  evidence 和 string value source evidence；accepted 结果只写入独立
  `repaired_fields`，不得写入 `decoded_fields` 或覆盖原文。rejected/error repair 使用脱敏、限长字符串
  fallback。
- 每次 repair attempt 必须生成 `NestedJsonRepairObservation`，至少记录 field path、
  accepted/rejected/error、strategy、repair log count 和不含敏感原文的 reason。
- 本地逐步验证产物必须保存对应 Runtime 节点的原始 contract `model_dump(mode="json")`；允许增加步骤、源文件 hash 和上一步引用等最小 envelope 元数据，但不得用审阅聚合、翻译字段或人工结论替换真实节点输出。
- body/header/token/cookie/password 等内容进入 `BoundedAnalysisEvidence` 前必须脱敏或以 decoded projection 替换；不能因为字段来自 raw message 就绕过敏感信息边界。
- `CanonicalFieldProvenance` 必须展示 canonical path、selected value、selected source path/layer/trust、selection reason 和 alternatives，让 `raw_message_first` 可从运行产物直接验证。

### Evidence coverage 约束

- `build_analysis_input` 必须生成 `EvidenceCoverageReport`，至少记录 message schema observations、
  parsed/decoded/repaired paths、canonical/fact/scenario source paths、LLM projection、sanitization、truncation、
  omissions 和 high-value gaps。
- coverage report 是审计/漂移产物，不是 verdict。一个字段被解析但没有 canonical mapping 时不得
  静默消失：它必须仍可在 parsed evidence 中回放，并通过全路径清单或已定义 high-value gap 暴露。
- `llm_projected_paths` 表示该字段属于 bounded projection 的候选内容；若 evidence 整体被截断，必须
  同时记录 `llm_truncated_evidence_paths`，不能声称 leaf-level 完整送达。
- accepted repair 进入 bounded analysis 时，原字段 replacement reason 必须标为
  `replaced_by_repaired_projection`；rejected/error repair 标为 `sanitized_string_fallback`。repair 结果
  只进入 repaired paths，不得进入 decoded paths。
- Prompt Builder 不得原样 dump 完整 coverage path 清单。模型只接收 parser status/fingerprint、计数、
  omission reason 汇总、high-value target/reason 和 truncation 数量；完整 vendor paths 只用于审计。
- high-value gap 规则必须通过 `EvidenceFieldImportanceRule` / `EvidenceFieldImportanceRegistry` 声明。
  Core 默认规则只能使用 vendor-neutral/标准协议语义；供应商字段规则由 adapter 写入 typed extension。
  无效 extension 规则忽略并保留现有 deterministic defaults，不能让一条坏配置中断告警。

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
  绕过 schema/domain validation、conflict guard、policy、approval 或 human review。
- `SocDecisionPolicy` 是 Runtime 中唯一允许把已校验 `AnalysisResult` 转换为 operational `Decision`
  的边界；CLI/API/TUI/Kafka/Lead Agent 不得自行按 confidence 拼 `needs_review`。
- `Decision` 必须显式携带 `confidence_source`、`confidence_is_calibrated`、可空的
  `calibrated_probability` / `calibration_profile_version`、`evidence_state`、结构化
  `review_reasons` 和 `policy_version`。raw confidence 不得冒充 calibrated probability。
- 当前 stub heuristic 与 LLM self-report 都未校准，必须包含 `confidence_not_calibrated` 并进入
  human review。未来只有经过人工标注、离线校准、版本审批和 replay 验证的 profile 才能改变该策略；
  `soc eval confidence` 的输出不会自动接入 Runtime。
- `false_positive` 必须要求人工确认。fact conflict、degraded/unsupported message schema、high-value
  evidence gap、LLM evidence truncation 等 guard 独立于 raw confidence，高分不能清除它们。
- memory confidence 只在 confirmed/retrieval-enabled memory 内参与排序；不能让 pending candidate
  自动生效。
- 不同层的 confidence/trust/status 禁止直接平均、相乘或折算成一个总分。任何聚合都必须先定义
  标注集、校准方法、版本化阈值和 replay 指标，并保留原始分层信号。
- 置信度评测必须分为 `soc eval labels prepare`、人工审阅、`soc eval labels validate`、
  `soc eval confidence` 四步。标签必须绑定 `run_id`、`input_hash`、model/prompt/pipeline version、
  reviewer、reviewed_at 和理由；标签文件不得复制 raw payload。
- `pending_review` 不能参与 calibration；无法确定真实结论的样本应标为 `excluded`，不得把
  `unknown/needs_review` 冒充 accepted ground truth。同一 `input_hash` 的 replay 不能重复计权，
  不同 model/prompt/pipeline scope 不能混入同一个 profile。
- `soc eval confidence` 只消费完成治理校验的 label set，输出 accuracy、Brier score、ECE、non-empty
  bins、dataset hash 和 versioned `review_below` profile。样本不足、实际 verdict 单一或无满足支持度的
  阈值必须 warning；当前 profile 的 `auto_action_allowed` 固定为 false，不自动写生产配置。

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
- analyzer 输出的 `AnalysisResult.evidence` 必须能引用 fact layer 中的关键不确定性，例如低可信 fallback 和字段冲突。
- deterministic stub 用于 request 结构、trace、replay、golden test 和低成本降级；它不是生产模型质量证明。
- 真实模型通过 `DeerFlowLLMChatClient` 复用 `deerflow.models.create_chat_model()`；SOC 代码不得再实现一套
  provider SDK、API key 读取或模型 fallback。
- prompt builder 只能从 `LLMAnalysisRequest` 生成 prompt；不能把完整 `AlertInput.raw` 自动塞入上下文。
- analyzer public output 必须是 `AnalysisNodeOutput`：
  - `analysis` 必须先经过 parser、Pydantic schema validation 和 domain validation。
  - `model_name`、`prompt_version`、`parser_version` 必须进入 run/step trace。
  - `PipelineStepTrace.metadata` 必须记录 `prompt_hash`、`candidate_hash`、`repair_applied`、usage/response metadata 等审计信息。
  - step metadata 不保存完整 prompt、完整 raw LLM output 或完整 vendor payload；需要复盘时通过 replay 输入和版本重新生成。
- 默认 runtime 必须继续使用 deterministic `StubLLMAnalyzer`；真实 LLM analyzer 只能通过显式 flag/config/client 注入。
- 统一配置为 `SOC_ANALYZER_MODE=stub|llm`、`SOC_LLM_MODEL`、`SOC_LLM_THINKING_ENABLED`、
  `SOC_LLM_ATTACH_TRACING`、`SOC_LLM_MAX_CONCURRENCY`、`SOC_LLM_REQUESTS_PER_MINUTE`、
  `SOC_LLM_ADMISSION_TIMEOUT_SECONDS`、`SOC_LLM_CALL_TIMEOUT_SECONDS`。CLI 可用 `--analyzer-mode` /
  `--model-name` 覆盖；未知模型必须 fail-fast，
  禁止静默换到默认 provider。
- LLM admission 必须独立于 Kafka worker concurrency，使用进程内 bounded semaphore 和可选 RPM
  预算。准入饱和是 retryable `analyzer_capacity`，不得调用 provider 后再伪装成本地限流。
- admission timeout 只限制等待本地并发名额；call timeout 独立限制一次 provider invocation。后者
  超时必须形成 retryable `analyzer_timeout`，Kafka 不得提交 offset；后台调用可能无法强制中断时，
  executor worker 数仍必须有界，防止超时请求无限创建线程。
- parser semantic repair 只允许可证明无损的白名单形状转换并记录精确 repair log。目前只允许
  `verdict: [one_string] -> one_string` 与 `evidence[i].value: [one_scalar] -> one_scalar`；多元素数组、
  类型猜测和内容拼接必须失败并进入 typed Runtime failure。
- Prompt compact JSON、模型响应、`AnalysisResult` 文本字段、evidence 数量/值长度、knowledge candidate
  数量/长度都必须有硬上限；超限必须在 Runtime 中形成 typed failure，不能进入 repair 无限消耗。
- `DeerFlowLLMChatClient` 只可保存 allowlisted response metadata 和 token usage；provider headers、凭证、
  原始 response object 不得进入 `AnalysisRun`。

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

Phase 1 当前先落地为：

```text
backend/tests/architecture/test_soc_agent_boundaries.py
```

必须覆盖：

- `contracts` 不 import `core/pipeline/db/api/daemon/cli`。
- `core` 不 import `api/cli/daemon`。
- `pipeline` 不 import FastAPI/Kafka/Typer/具体 DB client/具体 LLM SDK。
- `api/cli/daemon` 只能通过 core service 进入业务逻辑。
- `AlertInput` 保持 canonical strict schema；flat/vendor payload 只能在 `normalizers` 出现。
- public package exports 与文档一致，避免跨包调用内部函数。

## 五、Runtime 状态机

参考 DeerFlow `RunManager`：run 必须有明确状态，状态迁移可持久化。

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

每个 step trace 至少包含：

| 字段 | 说明 |
|---|---|
| `run_id` | 本次分析 ID |
| `alert_id` | 告警 ID |
| `step_name` | `normalize/entity_extract/dedup/...` |
| `status` | step 状态 |
| `input_hash` | 输入摘要 hash |
| `output_hash` | 输出摘要 hash |
| `started_at/ended_at` | 时间 |
| `duration_ms` | 耗时 |
| `error_code/error_message` | 失败原因 |
| `retry_count` | 重试次数 |
| `model_name/token_usage` | LLM 节点才有 |

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

API 从第一天就加版本：

```text
/api/soc/v1/...
```

### Phase 1 API 草案

| 方法 | 路径 | 用途 |
|---|---|---|
| `POST` | `/api/soc/v1/alerts/analyze` | 提交单条告警分析 |
| `GET` | `/api/soc/v1/runs/{run_id}` | 查询 run 状态和摘要 |
| `GET` | `/api/soc/v1/runs/{run_id}/steps` | 查询 step trace |
| `POST` | `/api/soc/v1/runs/{run_id}/replay` | 回放分析 |
| `GET` | `/api/soc/v1/alerts/{alert_id}` | 查看告警分析结果 |
| `POST` | `/api/soc/v1/runs/{run_id}/corrections` | 提交人工纠正 |
| `GET` | `/api/soc/v1/facts` | 查询 facts |
| `PATCH` | `/api/soc/v1/facts/{fact_id}` | 确认/驳回/回滚 fact |

### 响应格式

业务成功：

```json
{
  "data": {},
  "meta": {
    "request_id": "req_...",
    "schema_version": "soc.api.v1"
  }
}
```

业务失败采用 Problem Details 风格：

```json
{
  "error": {
    "code": "LLM_OUTPUT_INVALID",
    "message": "LLM output failed schema validation",
    "details": {},
    "retryable": false
  },
  "meta": {
    "request_id": "req_...",
    "run_id": "run_..."
  }
}
```

所有写接口支持：

- `Idempotency-Key`
- `X-Request-Id`
- `X-Actor`

## 八、事件与通信规范

### 内部事件

内部事件用于 CLI 进度、Web UI SSE、Daemon 观测、审计落库。事件必须结构化：

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

### Web/CLI 流式输出

参考 DeerFlow StreamBridge/SSE 思路：

- API/Web UI 用 SSE 或 WebSocket 订阅 run events。
- CLI Phase 1 可以直接消费 core event stream，不必绕 HTTP。
- event payload 不放超大原始日志，只放摘要和引用 ID。

## 九、Kafka 协议

Phase 4 引入 Kafka/Redpanda。Kafka message 必须 versioned，不直接透传厂商原始字段作为内部模型。

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

### 输出 topics

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
- 不在 Kafka callback 内执行长逻辑；只入队并由 Runtime worker 处理。
- poison message 进入 dead-letter topic：`soc.alerts.dead_letter.v1`。

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

Phase 1 只允许：

- L0：读日志、读告警、读 DB
- L1：生成建议
- L2：写 `review_queue`、写 candidate fact、写 audit

## 十一、多 Agent 通信协议

Phase 1 不做复杂多 Agent 通信。长期如果引入 Detection/Hunting/IR/Attack Simulation Agent，必须使用结构化消息，不用自由文本当协议。

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

Phase 1 CLI 可以先用本机用户和配置文件，不做完整用户体系；但 API、Web UI、Daemon 从设计上必须区分 actor。

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

Phase 2 起生成并提交 OpenAPI snapshot；Phase 4 起维护 AsyncAPI/Kafka schema 文档。

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
| Domain skill | DeerFlow `skills/public/soc-*` 或后续 SOC custom skill | 提供 EDR、APT、F5/WAF、资产归属、攻击方向、处置剧本等领域指导 | 自己执行工具、自己写 memory、把候选知识当 confirmed fact |
| Node prompt | `soc_agent/prompts/` | 固定 pipeline 节点内的结构化推理，例如 `llm_analyze` | 自主改变主流程、直接调用 MCP/tool、输出未校验自然语言进入决策层 |
| MCP/tool adapter | `soc_agent/tools/` / DeerFlow MCP bridge | 查询或执行外部能力 | 绕过 policy、审计、人类审批执行高风险动作 |

当前 `soc-analysis-v2` 是 **analysis node prompt**，不是 SOC Lead Agent 的总控 prompt。它只能消费 `LLMAnalysisRequest` 和后续受控 skill context，输出必须进入 `AnalysisResult` parser、schema validation、domain validation、evidence grounding，再由 Runtime 决定后续状态。

当前 `SocSkillResolver` 已作为薄层落地在 `backend/soc_agent/skills.py`，只输出 DeerFlow skill 名称和结构化 reason，不加载 `SKILL.md` 内容，不绕过 DeerFlow skill system。当前 DeerFlow 可加载的 SOC domain skills 是：

- `soc-alert-triage`
- `soc-endpoint-triage`
- `soc-network-apt-triage`
- `soc-waf-f5-triage`
- `soc-asset-direction`

`SocSkillResolver` 遵循：

- 输入来自 `LLMAnalysisRequest`、`AlertSummary`、confirmed facts 或 analyst-selected context，不读取松散 raw vendor payload。
- Phase 2/3 先用 deterministic 规则选择 skill，例如 `source_type=edr/hids` -> `soc-endpoint-triage`，`source_type=f5/waf` -> `soc-waf-f5-triage`，存在方向冲突 -> `soc-asset-direction`。
- LLM 可以在白名单 skill 候选中 rerank 或建议补充 skill，但不能动态加载未知 skill 后直接影响决策。
- 选中的 skill 作为 bounded context 注入 prompt；必须记录 skill name、skill version/hash、注入摘要和 token 预算。
- Skill 只能产生指导、候选解释、候选查询或 action proposal；写 DB、写 memory、执行 tool 必须回到 service/policy 层。

当前实现：`SocSkillContext` 已接入 `LLMAnalysisRequest.skill_context`、`build_analysis_prompt()`、`JsonLLMAnalyzer.metadata`、`SocAgentChatService` 的 `soc.skill_context` stream event 和 TUI translate。实现只注入 compact summary + sha256 content hash + token budget，不把完整 `SKILL.md` 作为 analysis node prompt 上下文。

SOC Lead Agent profile 安装必须使用 DeerFlow per-user custom-agent storage。当前 `SocLeadAgentProfileInstaller` / `soc agent install-profile` 写入 `.deer-flow/users/{user_id}/agents/soc-triage/config.yaml` 和 `SOUL.md`，默认不覆盖，只有显式 `--overwrite` 才更新；legacy shared 同名 agent 存在时跳过，避免 shadow。不要为 SOC 自建第二套 agent profile storage。

SOC Lead Agent chat entry 必须复用 DeerFlow embedded client / gateway runtime。当前 `SocLeadAgentChatService` 通过 `DeerFlowClient(agent_name="soc-triage")` 转发 stream，并发出 `soc.lead_agent_entry` marker；它不是 SOC action executor。ReviewQueue context 已通过 `backend/soc_agent/context_bridge.py` 以 bounded `SocLeadAgentReviewContextArtifact` 接入：只能由 `SocReviewService.get_investigation_context()` 取数，必须记录 context hash / skill context hash，不能把完整 raw payload 或 repository 访问权交给 Lead Agent。

SOC Lead Agent action proposal 必须走 `backend/soc_agent/actions/proposals.py`。根目录不保留 `backend/soc_agent/action_proposals.py` 兼容入口。只有 `<soc_action_proposal>...</soc_action_proposal>` 显式 JSON marker 会被解析成 `SocAgentActionProposal`；普通自然语言、Markdown 建议、模型自称“已执行”的文本都不能触发动作。`SocLeadAgentActionProposalBoundary` 只能调用 `SocAgentActionPolicy`，并在高风险时生成 pending `SocAgentApprovalRequest`；approval request 必须携带 `source_proposal_id`、`action_payload`、`context_refs`。本边界不执行 MCP/tool，不调用外部处置 adapter，不修改业务状态。

Approval inbox 客户端必须展示 proposal 溯源字段。Web/TUI 至少要让审批人看到 `source_proposal_id`、`action_payload`、`context_refs`；展示层不能改写这些字段，不能绕过 approval grant / dry-run / execute boundary。

MCP/tool 调用、审批和处置必须继续通过 SOC service/action/policy boundary 逐步接入。

后续 MCP/tool 调用遵循：

- 查询类工具默认仍需通过 allowlist、rate limit、audit，例如资产归属查询、EDR 进程树查询、历史告警查询。
- 处置类工具默认高风险，例如 IP 封禁、EDR 隔离、禁用账号、下发阻断，必须有人类审批或明确 playbook 授权。
- LLM 输出只能是 `ToolActionProposal` / `ActionProposal` 一类结构化候选，不能直接调用 adapter。
- 真实 adapter 必须注册到 `SocActionAdapterRegistry`，并通过 `SocAgentActionAdapterDescriptor` 声明 side-effect、必需参数和 dry-run/execute 能力。
- Tool result 必须作为 evidence 写回 run trace / audit，不允许只进入 prompt 后丢失。

### Profile / Skill / MCP 开放配置治理

SOC Lead Agent、Domain Sub Agent、Skill 和 MCP/tool group 的开放配置以 `.notes/ai_soc/governance/agent-profile-governance.md` 为产品治理源头；工程实现必须满足：

- Profile、skill、MCP 绑定必须有 `draft -> validated -> staging -> active -> archived` 生命周期。
- `draft` / `staging` 不能影响生产决策；`active` 必须记录审批人、评测集版本、profile hash、skill hash、tool group hash。
- Middleware preset 只能由代码定义，不能由用户自由新增/删除。
- 用户可配置 readonly MCP 候选；`high_risk` tool group 必须由管理员/审批流程启用，并继续走 human approval。
- SOC Lead Agent custom-agent profile 只写 DeerFlow 支持的 `name/description/model/tool_groups/skills/SOUL.md` 语义；不得向 profile 增加自定义 `mcp` 字段作为生产执行绑定。MCP server 连接属于 DeerFlow `extensions_config.json` / `mcp_config.json`，SOC action 到 MCP tool 的业务绑定属于 action adapter allowlist。
- Runtime 只能从 active profile registry 选择 profile；LLM 只能在白名单候选内建议 rerank，不能动态加载未知 profile/skill/tool。
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

- 队列满时优先保留高 severity、低置信、未处理告警。
- 重复告警优先 merge，不排队完整分析。
- provider 限流时，低风险告警进入 delayed queue 或 review queue。
- daemon 不允许无限并发；所有 LLM/tool 调用必须走 semaphore/rate limiter。
- 超预算时明确产出 `needs_review`，不能假装分析成功。

## 十八、部署、运维与恢复

### 环境分层

```text
local       # 本地开发：PostgreSQL + Redpanda 可选
dev         # 开发共享环境
staging     # 接近生产数据结构，脱敏数据
production  # 真实告警
```

### 健康检查

至少提供：

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
| 记忆存储 | `MemoryStore` |protocal
| 队列 | `TaskQueue` |
| 策略 | `PolicyEngine` |
| 事件输出 | `EventSink` |

Phase 1 用 Python `Protocol` + 显式 registry 即可，不做热插拔 marketplace。

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
- LLM 调用不可回滚，所以必须先记录 request metadata，再写 final decision。
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

Phase 1 最少维护：

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

## 二十二、Phase 切分

### Phase 1 必须做

- `contracts/` schema
- `core/runtime.py` 固定状态机
- `core/validator.py` schema/domain validation
- `pipeline/trace.py` step trace
- `decision_audit_log`
- `PromptSanitizer` 基础脱敏
- `prompt_version/model_name/pipeline_version` 审计字段
- 基础 rate limiter / semaphore
- CLI 调 core service
- API schema 草案可先不暴露，但 contracts 要先定
- 架构测试和 golden alert set

### Phase 2 做

- API v1 初版
- history correlation contracts
- dedup idempotency
- OpenAPI snapshot test
- 运行环境配置分层

### Phase 3 做

- LLM Advisory Router 白名单
- router decision trace
- memory/fact 版本回滚
- prompt/model replay evaluation

### Phase 4 做

- Kafka topic schema
- AsyncAPI/Kafka schema 文档
- PostgreSQL-backed queue / lease / heartbeat
- SSE/Web event stream
- replay diff / router 评测
- readiness/metrics/consumer lag 监控

### Phase 5 做

- 多 Agent message protocol
- Knowledge RAG contracts
- Attack Simulation Agent 的 L5 scope/approval protocol
