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
├── tools/              # 工具注册和执行适配器
├── memory/             # soc_facts / lessons / prompt 注入
├── db/                 # repository + migrations
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
- `memory/` 不能绕过事实状态机写 prompt；只能通过 `MemoryStore`/`LessonStore` 协议读写。
- `tools/` 不能直接执行高风险动作；必须经过 `policy`。
- 每个包的 `__init__.py` 只 export 稳定 public API。未 export 的类/函数默认内部实现，跨包不直接调用。

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

Alert summary 约束：

- `AlertSummary` 是面向告警列表、review queue、dedup、correlation 和 Web/TUI 查询的读模型，不替代完整 `AnalysisRun`。
- `AlertSummaryRepository.save_alert_summary()` 必须在 service 边界调用；CLI/API/TUI/daemon 入口不能自己拼 summary。
- `soc_alert_summaries` 保存扁平索引字段和完整 `summary_payload`，字段应优先服务高频查询：`alert_id`、`tenant_id`、`source_type`、`detection_key`、`rule_code`、`verdict`、`needs_review`、`updated_at`。
- correction 后必须更新同一个 run 的 summary，让 operational verdict 和 review 列表保持一致；原始 AI verdict 仍保留在 `AnalysisRun.analysis` 和 `soc_analysis_runs.run_payload`。
- replay 必须生成新的 summary，记录 `replay_of_run_id`，不能覆盖原 run summary。
- 方案文档中泛称的 `alert_summaries` 在当前实现里使用 SOC 前缀表名 `soc_alert_summaries`。

Review queue 约束：

- `ReviewQueueItem` 是人工复核队列读模型，由 `AlertSummary` 派生，不替代完整 `AnalysisRun`。
- `SocAnalysisService.analyze/replay()` 是唯一允许自动创建或更新 review queue item 的入口；CLI/API/TUI/daemon 不能自己拼 queue item。
- 入队原因必须是结构化 reason，例如 `summary.needs_review`、`low_confidence`、`uncertain_verdict`、`high_severity`。
- 同一个 run 同时最多保留一个 open review item；重新分析同一 run 的派生 summary 时更新 open item，而不是制造重复待办。
- `SocReviewService.correct()` 记录人工 correction 后，必须关闭该 run 的 open review item；关闭队列不能删除原始 run、summary 或审计记录。
- `SocReviewService.close_queue_item()` 只表示复核待办已处理，不等价于修改 verdict；需要改判必须走 `CorrectionCommand`。
- `soc_review_queue` 保存扁平索引字段和完整 `item_payload`，字段优先服务列表、筛选和复核入口：`status`、`priority`、`alert_id`、`run_id`、`source_type`、`rule_code`、`verdict`、`updated_at`。

Investigation context 约束：

- `InvestigationContext` 是分析师打开 review queue item 时的只读上下文，不产生新判断，也不修改 run/summary/audit。
- `SocReviewService.get_investigation_context(queue_id)` 是 API/TUI/Web/CLI 打开复核详情的统一 service 入口。
- context 至少包含 `queue_item` 和完整 `AnalysisRun`；如果注入了 summary/audit repository，则同时返回 `AlertSummary` 和 `DecisionAuditRecord[]`。
- context 中的 `similar_alerts` 必须来自 `AlertSummaryRepository.find_similar_alert_summaries()`，不能让入口层或 LLM 直接全库检索。
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
- SOC 当前持久化表包括 `soc_analysis_runs`、`soc_decision_audit_log`、`soc_alert_summaries` 和 `soc_review_queue`。
- 单元测试可以用 SQLite in-memory 验证 SQLAlchemy 映射；运行时配置和正式部署必须指向 PostgreSQL。

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
- 后续 `FieldTrust` / `ConflictReport` 应建立在该 policy 之后：先决定主证据输入，再重建方向、角色、资产、处置目标，并记录字段冲突；不能在 normalizer 层直接下最终攻击方向结论。

### Fact reconstruction 约束

`FactReconstructionResult` 是 LLM 分析前的事实层，不是最终研判结论。它解决的是“哪些字段可信、哪些角色候选互相冲突、后续分析应该带着什么不确定性进入”。

- runtime 固定在 `entity_extract` 后、`analyze_stub` / 后续 `llm_analyze` 前执行 `fact_reconstruct`。
- `FactReconstructionResult` 必须保存到 `AnalysisRun.fact_reconstruction`，随 run payload 一起持久化、replay、审计。
- `FieldTrust` 只表达字段可信度和是否参与事实重建；不能直接改变 verdict。
- `RoleAssignment` 是候选角色分配，当前允许的角色包括 `source`、`destination`、`attacker`、`victim`、`impacted_asset`、`response_target`。
- `ConflictReport` 必须结构化表达冲突类型、涉及字段和值，例如：
  - 同一角色多个候选值：`source_candidate_conflict`、`victim_candidate_conflict`。
  - 跨角色不一致：`attacker_source_mismatch`、`victim_destination_mismatch`。
  - 源和目的重叠：`source_destination_overlap`。
- Phase 1 的事实重建只做 deterministic 规则；LLM 后续只能读取 fact layer 进行解释、补充候选或提出复核问题，不能绕过该层直接相信上游加工字段。
- raw message 存在时，canonical processed fields 默认低可信且不作为主推理输入；raw message 缺失时 structured fallback 必须保留低可信 warning。

### LLM analysis request 约束

`LLMAnalysisRequest` 是 stub analyzer 和后续真实 LLM analyzer 的唯一输入 contract。它的目的不是扩大上下文，而是把脏输入收敛成可验证、可审计、可替换的分析请求。

- runtime 固定在 `fact_reconstruct` 后执行 `build_analysis_input`，产出 `AnalysisRun.llm_analysis_request`。
- `analyze_stub` 和后续真实 `llm_analyze` 只能消费 `LLMAnalysisRequest`，不能直接消费 raw payload 或自行重新解析 vendor 字段。
- `LLMAnalysisRequest` 必须包含：
  - canonical source / detection / classification / entities。
  - `ExtractedEntities`。
  - `FactReconstructionResult`。
  - `primary_evidence_path`、`conflict_count`、`conflict_types`、`warnings`。
- analyzer 输出的 `AnalysisResult.evidence` 必须能引用 fact layer 中的关键不确定性，例如低可信 fallback 和字段冲突。
- 真实 LLM 接入前，先以 deterministic stub 验证 request 结构、trace、持久化、replay 和 review queue 不受影响。
- 后续接模型时，prompt builder 只能从 `LLMAnalysisRequest` 生成 prompt；不能把完整 `AlertInput.raw` 自动塞入上下文。

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
| 记忆存储 | `MemoryStore` |
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
