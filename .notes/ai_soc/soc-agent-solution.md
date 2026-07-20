# SOC Agent Solution / SOC Agent 权威方案

Status: Active review baseline

Last updated: 2026-07-16

Primary audience: product review, architecture review, engineering review, security review

This document is the authoritative review entry for the SOC Agent work in this DeerFlow fork.
It explains what the system is, how the modules fit together, which contracts must stay stable,
and how PingAn-specific experience is injected without hard-coding the whole product to PingAn.

本文是后续 review 的主文档。它不再承担流水账职责：具体进度看
`.notes/ai_soc/progress.md`，端到端生命周期细节看
`.notes/ai_soc/alert-lifecycle-flow.md`，工程接口规范看
`.notes/reference-index/soc-agent-engineering-contracts.md`。

---

## 0. Review Guide / 如何评审

Review should answer these questions first:

| Review question / 评审问题 | Where to inspect / 看哪里 |
| --- | --- |
| The product flow is clear? / 产品流转是否清楚 | Sections 3, 4, and `.notes/ai_soc/alert-lifecycle-flow.md` |
| The architecture is DeerFlow-aligned? / 是否复用 DeerFlow | Sections 2, 5, 7 |
| The agent is not a fragile prompt wrapper? / 是否避免 prompt-only agent | Sections 1, 4, 6, 10 |
| Data contracts are stable? / 数据协议是否稳定 | Sections 8, 9 |
| PingAn knowledge is reusable and not hard-coded? / 平安经验是否可迁移 | Section 11 |
| Memory is useful but not polluting decisions? / 记忆是否可控 | Section 10 |
| Governed context is typed, scoped and auditable? / 运营事实是否强类型、有范围、时效和审计 | Section 7.4 |
| Approval and side effects are safe? / 审批和副作用是否安全 | Sections 7, 12 |

Non-goals of this document:

- It is not a full backlog. Use `.notes/ai_soc/progress.md` for completed slices and next steps.
- It is not a replacement for code contracts. Use `backend/soc_agent/contracts/` and
  `.notes/reference-index/soc-agent-engineering-contracts.md` for exact schemas.
- It does not document every old draft. Historical or low-frequency notes live under
  `.notes/archive/` or focused subdirectories.

---

## 1. Core Principle / 核心原则

The SOC Agent is not a pure traditional alert analysis system, and it is not a fully autonomous
LLM that drives production operations by itself.

SOC Agent 是一个在 DeerFlow 上增量构建的安全运营智能体系统：

- Deterministic runtime owns the main control flow / 确定性 Runtime 掌握主流程。
- LLM handles bounded reasoning inside controlled nodes / LLM 只在受控节点内做研判。
- Skills provide reusable investigation playbooks / Skill 承载可复用研判方法。
- MCP or action adapters provide external capabilities / MCP 或 Action Adapter 承载外部系统能力。
- Human approval gates high-risk actions / 高风险动作必须人工审批。
- Memory is candidate-first and review-gated / 记忆先进入候选，确认后才可检索使用。
- Operational context uses typed governed facts, not memory or permanent whitelists / 授权活动、
  护网参与者、变更窗口等运营上下文使用强类型受治理事实，不是记忆，也不是永久白名单。
- PingAn and other tenants use adapters, capability cards, policy, and scoped memory, not
  hard-coded core logic / 平安或其他客户能力通过适配器、能力卡、策略和租户记忆接入，不写死到核心。

### 1.1 Control Philosophy / 控制流哲学

```mermaid
flowchart LR
    A["🧭 Runtime<br/>固定主流程"] --> B["🧩 Deterministic Code<br/>解析、校验、状态、审计"]
    A --> C["🤖 LLM Node<br/>受控研判、结构化输出"]
    A --> D["🛠️ Tool/MCP<br/>只读查询或审批后动作"]
    A --> E["👤 Human Review<br/>复核、纠正、确认记忆"]

    C -. "may suggest / 可建议" .-> A
    C -. "cannot skip / 不可跳过" .-> B
    D -. "evidence only by default / 默认只产证据" .-> B
    E -. "authoritative correction / 人类纠正权威" .-> B
```

LLM can recommend routes from a whitelist, but it cannot rewrite the pipeline, skip validation,
write confirmed memory, or execute side-effect actions.

---

## 2. Terminology / 中英文术语对照

| 中文术语 | English term | Code / contract | Meaning / 含义 |
| --- | --- | --- | --- |
| 预警 | Alert | `AlertInput` | SOC Agent 的标准输入，不直接绑定某一家厂商字段 |
| 原始日志 | Raw message | `raw_message`, `source.raw` | 供应商或平台原始文本，优先作为事实重建依据 |
| 标准化 | Normalization | `normalizers/` | 将 PingAn/EDR/APT/HIDS/F5 等输入转成 canonical alert |
| 证据层 | Evidence Layer | `EvidenceLayer` | 区分 raw、canonical、enriched、derived 等证据来源 |
| 字段可信度 | Field Trust | `FieldTrust` | 表示字段来源可信程度和冲突情况 |
| 角色声明 | Role Claim | `RoleClaim` | 区分网络观测、厂商角色断言、场景推导、外部证据和人工确认 |
| 场景假设 | Scenario Hypothesis | `ScenarioHypothesis` | 反弹 shell、C2、横向移动等带证据的暂定场景，不是最终 verdict |
| 角色裁决 | Role Resolution | `RoleResolution` | 给出 observed/tentative/conflicted/confirmed/unresolved 状态和暂定值 |
| 冲突报告 | Conflict Report | `ConflictReport` | 记录上游字段、加工字段、模型结论之间的冲突 |
| 受限分析证据 | Bounded Analysis Evidence | `BoundedAnalysisEvidence` | 允许进入模型的限长、脱敏、带来源证据，不等于完整 raw payload |
| Skill 选择上下文 | Skill Context | `SocSkillContext` | 当前选择清单、原因、摘要和 hash；不是完整 `SKILL.md` 正文 |
| 分析运行 | Analysis Run | `AnalysisRun` | 一次 alert 分析的完整记录、trace、result |
| 决策审计 | Decision Audit | `DecisionAuditRecord` | analyze/replay/correct 的判定沿革和证据策略摘要，不替代完整 run |
| 业务变更审计 | Mutation Audit | `SocMutationAuditRecord` | L3 服务命令的追加式审计；记录 actor、来源、原因、幂等和有界结果，不保存原始敏感 payload |
| 预警摘要 | Alert Summary | `AlertSummary` | 轻量读模型，用于列表、关联、复核和 demo |
| 复核队列 | Review Queue | `ReviewQueueItem` | 需要分析师看的工作项 |
| 调查上下文 | Investigation Context | `InvestigationContext` | Review/Lead Agent/TUI/Web 共享的受控上下文 |
| 统一调查视图 | Unified Investigation View | `UnifiedInvestigationView` | 将分析、证据、相似预警、记忆、外部反馈拼成可读视图 |
| 调查证据 | Investigation Evidence | `InvestigationEvidence` | 只读工具/MCP 查询结果，不直接改变 verdict |
| 受治理上下文事实 | Governed Context Fact | `GovernedContextFact` | 共享租户、时效、来源、状态和审计信封的强类型运营事实 |
| 授权活动事实 | Authorized Activity Fact | `AuthorizedActivityFact` | 某项扫描、运维、测试或服务行为在限定范围和时间内已获授权；不是永久白名单 |
| 授权匹配结果 | Authorization Match Result | `AuthorizationMatchResult` | 当前告警与有效授权事实的确定性匹配、缺口、冲突和来源说明 |
| 安全演练事实 | Security Exercise Fact | `SecurityExerciseCampaignFact` | 护网/红蓝对抗的时间、目标、交战规则和权威来源 |
| 演练参与者事实 | Exercise Participant Fact | `ExerciseParticipantFact` | 某个时段内团队角色与 IP/账号/证书等标识的可审计关联 |
| 参与者归属结果 | Participant Attribution Result | `ParticipantAttributionResult` | 根据事件时间和标识判断红队/蓝队/白队等身份，允许 ambiguous/conflict |
| 检测真值 | Detection Truth | `actual_verdict` | 告警描述的攻击/异常行为是否真实发生 |
| 运营处置 | Operational Disposition | `SocOperationalDisposition` | 真实行为最终应升级、复核、抑制或按授权良性真阳关闭；与 detection truth 分离 |
| 影子处置建议 | Shadow Disposition Proposal | `SocDispositionProposalRecord` | 基于确定性上下文提出运营结论，但不应用、不关单 |
| 领域研判 | Domain Triage | `SocDomainTriageResult` | 面向 APT/EDR/HIDS/网络/账号等领域的研判结果 |
| 场景发现 | Scenario Finding | `SocDomainFinding` | 反弹 shell、横向移动、恶意外联等场景化发现 |
| 主控智能体 | SOC Lead Agent | `soc-triage` profile | DeerFlow lead_agent 派生的 SOC 对话/编排入口 |
| 子能力 | Skill | `skills/public/soc-*` | 可复用的研判方法，不直接调用生产系统 |
| 动作适配器 | Action Adapter | `SocActionAdapter` | 工具能力边界，可是 in-memory/mock/MCP-backed |
| MCP 工具 | MCP Tool | MCP server tool | 外部系统能力的标准工具接口，例如 CMDB/EDR/TI |
| 动作建议 | Action Proposal | `SocAgentActionProposal` | Agent 建议下一步查什么或做什么 |
| 审批请求 | Approval Request | `SocAgentApprovalRequest` | 高风险动作进入统一审批 inbox |
| 审批授权 | Approval Grant | `SocAgentApprovalGrant` | 一次性 execution token，不等于自动执行 |
| 处置反馈 | External Disposition | `SocExternalDispositionEvent` | Zeus/老平台/工单系统同步回来的状态和理由 |
| 候选记忆 | Memory Candidate | `SocMemoryCandidate` | 待复核的经验、事实、模式、反馈 |
| 确认记忆 | Confirmed Memory | `SocMemoryRecord` | 人类确认后的可检索记忆，默认仍受 retrieval policy 约束 |
| 能力卡 | Capability Card | PingAn capability docs | 描述一个业务能力应落到 skill、MCP、adapter、memory 还是 eval |

---

## 3. Product Shape / 产品形态

SOC Agent exposes several entry surfaces, but all of them must call the same core services.

SOC Agent 有多个入口，但不能各写一套业务逻辑：

| Entry / 入口 | User / 用户 | Purpose / 用途 | Rule / 约束 |
| --- | --- | --- | --- |
| CLI | Developer, maintainer | Demo, smoke, replay, correction | Thin wrapper over services |
| TUI / terminal workbench | Analyst, operator | Queue review, context view, agent chat | DeerFlow-aligned, no independent business logic |
| Web UI | Analyst, team lead | Review inbox, approval inbox, investigation view | Reads Gateway APIs backed by services |
| Kafka daemon | Background ingestion | Consume strict versioned alert envelopes and create review items | Validate/unwrap only; raw source payload remains intact |
| DeerFlow Lead Agent | Analyst chat | Ask questions around a review item, propose next steps | Uses bounded review context |
| External systems | Zeus, old SOC platform, ticketing | Push status/reason back into SOC Agent | Source adapter maps to canonical command, then authenticated Gateway/service boundary |

Gateway compatibility contract:

- Existing `/api/soc/*` paths and direct typed success bodies are stable for current Web clients.
- Every SOC route uses `create_soc_router()` to return `X-SOC-API-Version: 1`, correlated
  `X-Request-Id` / `X-Trace-Id`, and sanitized RFC Problem Details for route-level failures.
- The OpenAPI summary snapshot under `contracts/soc_api/` is the review gate for path/method/header/error
  changes. Authenticated Gateway identity is authoritative; a caller header cannot replace it.

```mermaid
flowchart TB
    subgraph Entry["🚪 Entry Surfaces / 入口层"]
        CLI["⌨️ CLI"]
        TUI["🖥️ TUI"]
        WEB["🌐 Web UI"]
        KAFKA["📨 Kafka Daemon"]
        LEAD["🤖 DeerFlow Lead Agent"]
        EXT["🔁 External Systems<br/>Zeus / Ticket / Old SOC"]
    end

    subgraph Core["🧠 SOC Core Services / 核心服务层"]
        ANALYSIS["SocAnalysisService"]
        REVIEW["SocReviewService"]
        MEMORY["SocMemoryService"]
        DAEMON["SocDaemonService"]
        CHAT["SocAgentChatService"]
        DISPOSITION["SocExternalDispositionService"]
        APPROVAL["SocAgentApprovalService"]
    end

    subgraph Runtime["🧭 Runtime / 固定运行时"]
        PIPELINE["Pipeline Nodes<br/>normalize / evidence / triage / decide"]
        TRACE["Step Trace<br/>Audit / Replay"]
    end

    subgraph Data["🗄️ Data / 数据层"]
        DB["PostgreSQL in prod<br/>SQLite only for local smoke"]
        EVENTS["Event Log"]
        MEMORY_DB["Memory Tables"]
    end

    CLI --> ANALYSIS
    TUI --> REVIEW
    WEB --> REVIEW
    KAFKA --> DAEMON
    LEAD --> CHAT
    EXT --> DISPOSITION

    DAEMON --> ANALYSIS
    CHAT --> REVIEW
    CHAT --> APPROVAL
    DISPOSITION --> REVIEW
    REVIEW --> MEMORY
    ANALYSIS --> PIPELINE
    PIPELINE --> TRACE
    TRACE --> DB
    REVIEW --> DB
    MEMORY --> MEMORY_DB
    APPROVAL --> EVENTS
```

Product conclusion:

- Build a full SOC work system, not only a CLI parser.
- Keep the first usable path narrow: one alert in, reviewable investigation out.
- Keep all user-facing surfaces thin: CLI/Web/TUI/daemon call the same service layer.
- Keep DeerFlow reusable: SOC Lead Agent should use DeerFlow `lead_agent` profile/skills/MCP path, not a separate LangGraph clone unless proven necessary.

---

## 4. End-to-End Flow / 端到端流程

The currently implemented lifecycle is documented in detail in
`.notes/ai_soc/alert-lifecycle-flow.md`. The summary below is the review-level target flow. Steps 7
and 9 are implemented for authorized-activity enrichment plus shadow disposition; campaign,
participant and additional governed-fact types remain planned.

```mermaid
flowchart TD
    A["📥 1. Alert Ingest<br/>Kafka / CLI / API / demo"] --> B["🧾 2. Normalize<br/>vendor adapter -> AlertInput"]
    B --> C["🔍 3. Evidence Policy<br/>raw first / field trust / conflict report"]
    C --> D["🧠 4. Runtime Triage<br/>deterministic + bounded LLM"]
    D --> E["🧩 5. Domain Findings<br/>scenario / entities / evidence gaps"]
    E --> F["🛠️ 6. Investigation Actions<br/>read-only adapter or MCP"]
    F --> A1["🪪 7. Governed Context Enrichment<br/>campaign / participant / authorization"]
    A1 --> G["📚 8. Context Assembly<br/>similar alerts / evidence / memory / external feedback"]
    G --> D1["⚖️ 9. Disposition Reconciliation<br/>detection truth != operational disposition"]
    D1 --> H{"👤 10. Review Routing<br/>new / partial / conflict / expired?"}
    H -->|"yes"| H1["Review Queue<br/>conclusion + gaps + checklist"]
    H -->|"exact governed match"| H2["Shadow / policy-gated disposition<br/>closed_benign_true_positive candidate"]
    H1 --> I{"✅ Analyst action<br/>复核动作"}
    H2 --> I
    I -->|"correct / close / note"| J["📝 11. Audit + State Update<br/>status / reason / trace"]
    I -->|"high-risk action"| K["🛂 Approval Inbox<br/>request -> grant -> dry-run/execute boundary"]
    J --> L["🧬 12. Memory Candidate<br/>pending_review only"]
    K --> J
    L --> M{"👤 Memory Review<br/>人工确认"}
    M -->|"confirm"| N["📖 Confirmed Memory<br/>retrieval-enabled by policy"]
    M -->|"reject / expire"| O["🗃️ Archive / no runtime effect"]
```

Important behavior:

- A result must still be produced even when tools are unavailable.
- Missing evidence should become explicit evidence gaps and human checklist items.
- Historical similar alerts, external feedback, and confirmed memory are not fallback-only;
  they are part of the normal investigation context.
- Tool results are evidence. They do not silently mutate verdict, memory, or status.
- Authorization never rewrites detection truth. A real exploit or prohibited operation can remain
  `true_positive` while the operational disposition is `closed_benign_true_positive` because it was
  explicitly authorized.
- Only a complete governed-context chain that matches event time, tenant/environment, participant,
  subject, target and behavior scope may reduce repetitive review. Identity attribution alone is not
  authorization. Partial, expired, conflicting or new patterns still go to humans.
- High-risk actions go to approval inbox. Approval grant is still not automatic execution.

---

## 5. Module Responsibilities / 模块职责

### 5.1 Entry Layer / 入口层

| Module / 模块 | Responsibility / 职责 | Must not do / 禁止 |
| --- | --- | --- |
| `backend/soc_agent/cli.py` | CLI commands for demo, review, memory, daemon smoke | No direct DB business mutation except through services |
| Gateway API routes | Web/TUI/API access to review, memory, approval through the shared v1 transport helper | No duplicate runtime logic, ad hoc error shape or trusted caller actor header |
| Kafka consumer / daemon | Validate `SocAlertRawEnvelope`, preserve raw payload, map to daemon messages, call `SocDaemonService` | No bare alert object, vendor parsing or direct alert analysis logic |
| DeerFlow Lead Agent bridge | Chat around bounded investigation context | No unbounded raw secret/context injection |
| External disposition adapter | Map old-platform status/reason to canonical event and call the authenticated canonical ingress | No direct DB, verdict, queue or confirmed-memory write |

### 5.2 Core Service Layer / 核心服务层

| Service / 服务 | Public role / 对外角色 | Review focus / 评审重点 |
| --- | --- | --- |
| `SocAnalysisService` | Analyze alert, replay run, update summary | Runtime determinism, trace, validation |
| `SocReviewService` | Review queue, correction, notes, investigation context | State transition, audit, memory candidate bridge |
| `SocMemoryService` | Candidate review, confirmed memory retrieval | Human confirmation boundary |
| `SocDaemonService` | Background ingestion orchestration | Idempotency, backoff, worker result |
| `SocAgentChatService` | SOC chat event stream and proposal handling | Bounded context and approval proposal |
| `SocAgentApprovalService` | Approval request/grant/dry-run/execute boundary | Permission, token, audit, no silent execute |
| `SocExternalDispositionService` | External status/reason sync | Mapping, target resolution, idempotency |
| `SocGovernedContextService` | Govern typed fact lifecycle and source/version history | GF-01 implemented: proposal, activation, validity, revocation, audit |
| `SocAuthorizedActivityService` | Read-only authorized-activity matching over governed facts | AA-01 implemented: canonical query, historical version selection, scope/time/freshness explanation |
| `SocAuthorizationEnrichmentService` | Persist/replay one authorization match as investigation context | EX-01 implemented: append-only, idempotent, read-only projection; no disposition |
| `SocDispositionProposalService` | Produce an auditable shadow operational proposal from persisted context | DP-01 implemented: exact + true-positive gate; no apply/close/action |
| `SocDispositionEvaluationService` | Persist explicit labels, create reproducible samples, derive reviewer inboxes, compute read-only gate reports | EV-01..EV-03 implemented: CLI/API/Web/TUI/trusted external capture share one append-only service; passed report still cannot auto-close |
| `SocSecurityExerciseContextService` (planned) | Compose campaign, participant attribution and authorization | Red/blue/white-team identity is not authorization by itself |
| `SocCorrelationService` | Deterministic similar-alert and historical-evidence lookup | Shared summary repository, structured reasons, no LLM/decision mutation |
| `SocMainOrchestratorService` | Read-only orchestration for analysis/correlation/selected actions/domain report | Typed `CorrelationResult` bridge; no direct repository/tool/high-risk side effects |

### 5.3 Runtime Pipeline / 固定运行时

```mermaid
flowchart TD
    I["🧾 Raw Alert Payload"] --> N["1. normalize"]
    N --> X["2. entity_extract<br/>code-first"]
    X --> F["3. fact_reconstruct<br/>RoleClaim + Scenario + Resolution"]
    F --> B["4. build_analysis_input<br/>bounded evidence + coverage"]
    B --> S["5. skill_context<br/>allowlisted compact guidance"]
    S --> L["6. analyze_stub / analyze_llm<br/>DeerFlow model in explicit mode"]
    L --> V["7. schema_validate<br/>JSON + Pydantic + domain"]
    V --> G["8. evidence_grounding<br/>claim value -> bounded context path"]
    G --> R["9. SocDecisionPolicy<br/>detection decision guards"]
    R --> P["🔒 Atomic analysis bundle<br/>run + summary + review + audit"]
    L -->|failure| E["⚠️ RuntimeFailure<br/>typed + sanitized + retryable"]
    E --> P
    P --> M["🛠️ normalization_monitor<br/>fail-open maintenance side path"]
```

`SocCorrelationService`, `SocDomainTriageService`, investigation actions, governed-context matching,
memory retrieval, and the DeerFlow SOC Lead Agent are **not hidden nodes inside this base
Runtime**. They consume the persisted run through explicit orchestration/review services. The base
Runtime produces the detection assessment; a later deterministic disposition reconciliation may
combine it with governed authorization facts without rewriting the immutable original run. This
keeps one-alert execution replayable while allowing richer investigation workflows to evolve
independently.

The primary analysis bundle has its own `AnalysisPersistence` transaction. L3 service commands use
the separate `SocMutationUnitOfWork`: one correction, review close/note, memory review, approval
transition/action boundary, or external-disposition apply either commits all SQL writes plus one
`SocMutationAuditRecord`, or rolls them all back. Process-local `SocEvent` emission is buffered until
commit. `DecisionAuditRecord` remains the decision-lineage record; mutation audit is the
cross-command actor/idempotency/result record, so neither table replaces the other.

Phase 2 correlation bridge / 历史关联桥接：

```mermaid
flowchart LR
    A["🧾 Current Alert<br/>当前告警"] --> B["⚙️ SocAnalysisService<br/>fixed Runtime + AlertSummary"]
    B --> C["🔎 SocCorrelationService<br/>shared summary repository"]
    C --> D["🗃️ Historical AlertSummary<br/>历史摘要"]
    C --> E["🧰 Historical Evidence<br/>matched run_id only"]
    D --> F["📦 CorrelationResult<br/>score + reasons + reusable evidence"]
    E --> F
    B --> G["🧩 SocDomainTriageService"]
    F --> G
    B --> H["📋 UnifiedInvestigationReport"]
    F --> H
    G --> H
    H --> I["🧑‍💻 Analyst / Lead Agent<br/>bounded review context"]
    F -. "🚫 no mutation" .-> J["Decision / Queue / Memory / Action"]
```

The analysis and correlation services share one `AlertSummaryRepository`, but the orchestrator does
not query it directly. Local/eval sessions use an in-memory implementation; production must inject a
fully configured service pair backed by the SOC PostgreSQL repository, preserving atomic analysis
persistence. Structured correlation is authoritative; metadata counts are display-only
projections. Reusable evidence is selected by the matched historical `run_id`, preventing a repeated
`alert_id` from mixing current and historical action results. A match can enrich domain findings and
human review, but it cannot by itself suppress/deduplicate an alert or alter the Runtime decision.

Correlation evaluation / 关联质量评测：

```mermaid
flowchart LR
    A["🏷️ Versioned Pair Labels<br/>same / related / unrelated"] --> B["⚙️ Scoring Policy<br/>soc.correlation.scoring.v1"]
    B --> C["🔎 Retrieval Task<br/>same + related = relevant"]
    B --> D["🧬 Identity Task<br/>same only = duplicate"]
    C --> E["📊 Precision / Recall<br/>Reason Distribution / Fan-out"]
    D --> E
    B --> F["🧰 Evidence Check<br/>run lineage + unrelated exposure"]
    F --> E
    E --> G["🔁 Replay Diff<br/>pair + metric deltas"]
    G -. "measurement only" .-> H["🚫 No Suppression<br/>shadow_dedup_allowed=false"]
```

`soc eval correlation` consumes a versioned, vendor-neutral label set. `same_incident` and
`related_distinct` are both useful historical retrievals, but only `same_incident` is a possible
duplicate identity. The current controlled baseline has 8 pairs: retrieval precision `0.667`, recall
`1.0`; an offline score threshold of `130` also yields duplicate precision `0.667`, recall `1.0`
because one related-but-distinct endpoint occurrence crosses the threshold. Historical evidence has
zero cross-run lineage leaks, but two unrelated retrieved candidates expose irrelevant evidence.
These numbers are diagnostic, not rollout gates. The current scorer has no incident-identity proof,
so automatic dedup/suppression remains forbidden. `--baseline-json` compares metric, pair, reason,
fan-out and evidence changes while ignoring report timestamps.

Runtime rules:

- Every node has typed input/output.
- LLM output must pass parser and schema validation.
- Analyzer evidence must pass deterministic grounding against the exact bounded prompt projection.
  Ungrounded values or invalid source paths cannot disappear behind a high confidence score; they
  become structured review reasons.
- The live analyzer reuses DeerFlow `create_chat_model()` and configured model names. Entry surfaces
  select it explicitly with `--analyzer-mode llm` or `SOC_ANALYZER_MODE=llm`; direct service tests
  remain deterministic by default.
- Model calls record actual model name, prompt/parser versions, duration, bounded token usage and
  safe provider metadata. They never persist API keys, request headers, full prompts or raw responses.
- Process-local model admission is bounded independently from Kafka workers with
  `SOC_LLM_MAX_CONCURRENCY`, optional `SOC_LLM_REQUESTS_PER_MINUTE`, and
  `SOC_LLM_ADMISSION_TIMEOUT_SECONDS`. One provider invocation is separately bounded by
  `SOC_LLM_CALL_TIMEOUT_SECONDS`; timeout is a retryable `analyzer_timeout`, not a silent hang.
- Bad JSON repair is allowed only as a logged parser step. Narrow schema-shape repair may unwrap a
  single-item verdict or evidence-value array, but multi-item or lossy coercion still fails schema
  validation.
- Prompt context, model response, analysis text, evidence count/value size, knowledge candidates,
  and projection depth/list sizes all have hard bounds.
- Replay must be possible from stored run payload and deterministic settings.
- Runtime failures are stored as `RuntimeFailure(kind, step_name, retryable, safe message)`. Retryable
  Kafka failures do not commit the offset or open a duplicate analyst queue; non-retryable failures
  are dead-lettered and enter ReviewQueue.
- The primary business write is one transaction through `AnalysisPersistence.save_analysis_bundle()`:
  `AnalysisRun`, `AlertSummary`, optional `ReviewQueueItem`, and `DecisionAuditRecord` either all
  commit or all roll back. Normalization maintenance remains a fail-open post-write side path.
- Runtime never calls production side-effect tools directly.

### 5.4 Normalizer and Evidence Layer / 标准化与证据层

The system must handle vendor differences without turning the core schema into a vendor schema.

处理不同供应商日志的方式：

1. Vendor adapter maps raw input to canonical `AlertInput`.
2. Evidence policy records which fields are trusted, weak, conflicting, or derived.
3. PingAn deterministically parses all available `zeusRawLogs[].message` values through a
   source-scoped parser registry. Supported MVP formats are delimited JSON, quoted KV,
   comma-delimited KV, and loose KV.
   Supported nested JSON/HTTP fields are decoded by an allowlisted decoder with size limits;
   decoded request/response bodies and headers are redacted before entering bounded model context.
4. Fields parsed from the raw message are the highest-priority observable facts. Conflicting
   `zeusRawLogs[]` structured fields remain visible as medium/low-trust fallback candidates;
   canonical processed fields remain lower-trust derived values.
5. The original payload is never replaced: `AlertInput.raw` and `AnalysisRun.input_payload`
   retain every hit log, raw event, message, and platform field for replay and audit.
6. The first successfully parsed message becomes primary evidence; additional messages become
   bounded supplementary evidence. Network and process facts remain per-message observations with
   stable `observation_scope`; different requests or process executions are not collapsed into one
   session conflict. Analysis nodes receive size-bounded parsed content, not only a source path and
   not the unbounded vendor payload.
7. If raw message parsing fails, Runtime preserves the raw text, emits a warning, and keeps the
   structured fallback at reduced trust. If raw message is absent, PingAn falls back to
   `zeusRawLogs[]` with explicit low trust.
8. Strict nested JSON failure does not discard the field. Runtime attempts a conservative repair:
   accepted structures enter a separately labeled `repaired_fields` projection, while rejected or
   failed repair uses a redacted string fallback. Repair is field-policy aware and validates root
   type, depth, node count, key length, and source-evidenced keys/string values. The original string
   always stays in `fields`, and repaired content never masquerades as strict-decoded source fact.
9. Every selected message emits `MessageSchemaObservation`: `recognized` means the deterministic
   parser handled the structure, `degraded` means partial/nested decoding failed, and `unsupported`
   means no parser handled the selected message. A structural fingerprint supports baseline diff.
10. `EvidenceCoverageReport` records parsed/decoded/repaired paths, canonical/fact/scenario consumers,
    exact bounded LLM projection, redaction/replacement, omission reasons, truncation, and known
    high-value gaps. A candidate path is not reported as projected unless its value is present in the
    exact prompt projection. High-value expectations come from `EvidenceFieldImportanceRegistry`:
    core provides vendor-neutral defaults, while source adapters may add typed rules in
    `AlertInput.extensions`. It is persisted for audit; the prompt receives only a compact coverage
    summary without vendor paths.
11. Clean vendors may bypass heavy conflict handling, but still produce canonical evidence metadata.
12. Vendor aliases stop at the source adapter. PingAn fields such as `attack_sip`, `alarm_sip`,
   `str_source_ip`, and `str_attack_ip` are converted into vendor-neutral `RoleClaim` objects;
   the generic fact reconstructor does not interpret those aliases directly.
13. Evidence trust and semantic confidence are separate. A value parsed faithfully from raw
    message may still be a wrong attacker/victim assertion from the source product.
14. Vendor-known placeholders or non-observation fields are emitted as `SourceFieldSemantic` with
    explicit reasoning/entity permissions. For example, a vendor default external IP may remain in
    raw/parsed evidence for audit while being forbidden from canonical entities, IOC extraction and
    network-peer reasoning. Core Runtime does not know vendor aliases or placeholder values.
15. External SOAR/asset/related-alert context remains separated from event facts. Asset owner or
    logged-on account is not automatically the event actor; historical automated dispositions are
    not independent human evidence. Deferred external context is visible in coverage and is later
    admitted through typed investigation/correlation services.

Schema drift workflow / 结构漂移流程：

1. Offline onboarding still uses `soc normalize drift` and `--schema-baseline` to compare a reviewed
   sample corpus before deployment.
2. An engineer accepts production baselines with `soc normalize baseline-accept` or
   `POST /api/soc/normalization/baselines`. Baselines are versioned, scoped by tenant/source/adapter/
   parser/version, persisted in `soc_normalization_schema_baselines`, and never self-approved.
3. Every persisted CLI/Kafka analysis calls `SocNormalizationMaintenanceService.monitor_run()` after
   the normal run/summary/queue/audit writes. Missing baseline, novel fingerprint, degraded or
   unsupported schema, high-value mapping gap, and bounded-evidence truncation become deduplicated
   `NormalizationMaintenanceIssue` records. Monitoring failure is fail-open for alert analysis and is
   recorded in `NormalizationMonitoringResult.warnings`.
4. Accepting a baseline supersedes the prior active version and resolves covered `baseline_missing` /
   `novel_schema` issues. Recurrence increments `occurrence_count`; a resolved/ignored issue that
   recurs is reopened.
5. Operators use `soc normalize issues`, Review TUI `/normalization` and `/norm-update`, or the Web
   workbench at `/workspace/soc/normalization`. Gateway metrics expose bounded type/severity/source
   counts; Kafka JSONL metrics include per-message issue count/IDs/warnings.
6. `soc normalize suggest` is an offline-only assistant. It emits a value-free path prompt and can
   either replay a recorded response or call a DeerFlow-configured model with `--live-llm`. Both paths
   validate suggestions against observed source paths and a canonical target whitelist, keep invalid
   proposals as rejected, and always return `auto_apply_allowed=false`.
7. A new fingerprint only means the structure changed. It is maintenance evidence, never a verdict,
   memory fact, suppression decision, or automatic parser patch.

Conflict adjudication / 冲突裁决：

- `source` / `destination` are observed network roles; they are not globally equivalent to
  `attacker` / `victim`.
- Scenario hypotheses determine which semantic constraints are valid. For reverse connection,
  `source=victim` and `destination=attacker` is consistent rather than contradictory.
- `RoleResolution` always exposes status, provisional value, evidence gaps, manual checks, and
  supporting/contradicting claim IDs. A conflicted resolution may provide a current conclusion,
  but it remains visibly provisional.
- Supplementary raw messages participate as independent claim sources. They are not only appended
  to an LLM prompt.
- Fact reconstruction never determines `response_target`. Action type, policy, asset evidence,
  approval, and the action adapter jointly determine an operational target later.
- No role resolution alone enables an action. `automation_allowed` remains false at this layer,
  including for human-confirmed facts.

```mermaid
flowchart TB
    RAW["📦 Vendor Raw<br/>PingAn / EDR / APT / HIDS / F5 / Other"] --> ADAPTER["🔌 Source Adapter<br/>normalizers/*"]
    ADAPTER --> PARSER["🧾 Raw Message Parser<br/>primary + supplementary"]
    PARSER --> CANON["📄 Canonical AlertInput"]
    PARSER --> SCHEMA["🔎 MessageSchemaObservation<br/>status + fingerprint"]
    PARSER --> BOUNDED["✂️ Bounded Analysis Evidence"]
    PARSER --> COVER["📊 EvidenceCoverageReport<br/>used / sanitized / omitted / gap"]
    SCHEMA --> MONITOR["🛠️ Maintenance Monitor<br/>baseline / drift / coverage"]
    COVER --> MONITOR
    MONITOR --> MAINTDB["🗃️ Baseline + Issue Store"]
    MAINTDB --> OPS["🧑‍💻 CLI / TUI / Web / Metrics"]
    ADAPTER --> CLAIM["🧾 RoleClaim<br/>vendor-neutral claims"]
    ADAPTER --> TRUST["🧪 FieldTrust"]
    CLAIM --> SCENARIO["🧠 ScenarioHypothesis"]
    SCENARIO --> RESOLVE["⚖️ RoleResolution"]
    TRUST --> RESOLVE
    RESOLVE --> CONFLICT["⚠️ ConflictReport<br/>provisional resolution + guard"]
    CANON --> RUNTIME["🧭 Runtime"]
    BOUNDED --> RUNTIME
    SCHEMA --> RUNTIME
    COVER --> RUNTIME
    RESOLVE --> RUNTIME
    CONFLICT --> RUNTIME
```

### 5.5 Confidence Semantics / 置信度语义

The system has several confidence-like values, but they are not interchangeable probabilities.

| Signal / 信号 | Meaning / 含义 | Current use / 当前用途 |
| --- | --- | --- |
| `EvidenceTrustLevel` | Provenance quality: raw, fallback, processed, inferred, or human-confirmed | Evidence ordering and warnings; never averaged |
| `MessageSchemaStatus` | Parser completeness: recognized/degraded/unsupported | Drift and maintenance alert; not a probability |
| `ScenarioHypothesis.confidence` | Versioned deterministic heuristic score | Scenario ordering and review context; currently uncalibrated |
| `RoleClaim/RoleResolution.semantic_confidence` | Strength of one role interpretation under a scenario | Provisional role explanation and automation guard |
| `AnalysisResult.confidence` | Analyzer/LLM assessment for the verdict | Review display and eval only; cannot bypass validation or approval |
| `Decision.confidence_source` | Provenance of the raw decision score | Distinguishes stub heuristic, LLM self-report, human confirmation, and external disposition |
| `Decision.calibrated_probability` | Versioned calibrated probability, when an approved profile exists | Currently `null`; it must never be fabricated from raw analyzer confidence |
| `Decision.evidence_state` / `review_reasons` | Operational evidence guard and structured review causes | Drives ReviewQueue/audit explanations independently of the numeric score |
| `AnalysisEvidenceGroundingReport` | Whether each analyzer evidence value exists at its declared bounded-context source | Ungrounded claims force review and remain auditable; never auto-rewrite model output |
| `AuthorizationMatchResult` | Deterministic applicability of governed authorization facts | Drives disposition eligibility; it is not an LLM probability |
| Calibration eligibility | Whether decisive facts were present in the evaluated model input | Excludes context-missing samples from analyzer calibration without discarding their business truth |
| Memory confidence | Strength of a reviewed reusable lesson | Retrieval ranking after confirmation; never promotes a candidate by itself |

Rules:

- Never average or multiply these values across layers.
- A high-trust raw field can still have low semantic confidence when the sensor assigns attacker and
  victim incorrectly.
- Current scenario/role scores are deterministic heuristics, not calibrated likelihoods. Their
  constants and taxonomy version must be replayable.
- LLM self-reported confidence is advisory. Production thresholds require labeled replay sets,
  calibration metrics, versioned thresholds, and comparison against analyst outcomes.
- Offline calibration is a governed two-stage boundary. `soc eval labels prepare` extracts a compact,
  raw-payload-free review bundle from complete live-LLM `AnalysisRun` artifacts; analysts then set
  `actual_verdict`, `review_status`, reviewer, time, and reason. `soc eval labels validate` blocks
  pending labels, duplicate input hashes, and mixed model/prompt/pipeline scopes. Only a validated
  label set may enter `soc eval confidence`, which reports accuracy, Brier score, expected calibration
  error and non-empty bins and emits a provenance-bound `review_below` profile. Small or single-class
  sets are warned; the profile remains offline and `auto_action_allowed` is always false.
- Calibration must separate detection truth from operational disposition. A sample can have
  `actual_verdict=true_positive` and `actual_disposition=closed_benign_true_positive` at the same
  time. If authorization was not present in the exact bounded input used by the model, record the
  known business truth but mark the analyzer sample `excluded_missing_decisive_context`; do not use
  it to punish or calibrate the analyzer. Retain it for authorization-enrichment coverage metrics.
- Missing coverage, degraded schemas, conflicts, and truncation can lower or cap an operational
  conclusion, but no single score may silently erase those warnings.
- Analyzer evidence uses one exact source path per item. Composite paths and descriptions that add
  uncited sibling facts are invalid. `#parsed`, `#decoded`, and `#repaired` citations are distinct
  provenance surfaces and must resolve to the exact bounded projection used for that model call.
- HTTP status, tool transport success, workflow state, ticket transition, `is_blocked`, or
  `is_banned` does not by itself prove exploit execution, command success, file creation, compromise,
  or completed response. A positive outcome claim without outcome-specific evidence forces review as
  `unproven_outcome_claim`; explicit uncertainty such as "cannot confirm success" is not such a claim.
- `SocDecisionPolicy` is the only Runtime component allowed to translate validated analysis into a
  detection `Decision`. The current policy deliberately marks stub and live-LLM confidence as
  uncalibrated and sends every such decision to human review until a labeled, approved calibration
  profile is explicitly integrated and replay-tested.
- Final lifecycle disposition is a separate deterministic reconciliation boundary. It may consume
  the detection decision plus `AuthorizationMatchResult`, but neither an LLM statement nor a memory
  match may directly close or suppress an alert.
- False-positive decisions require confirmation even when the raw score is high. Review reasons are
  structured (`confidence_not_calibrated`, `fact_conflict`, `high_value_evidence_gap`, and so on),
  persisted in the summary/queue/audit trail, and must not be replaced by one free-text reason.
- Successful mock evidence is visible for demo and audit only. Mock, denied, or failed action results
  cannot satisfy scenario tool requirements, raise domain/scenario confidence, or change verdict.

---

## 6. SOC Lead Agent, Skills, MCP, and Tools / 智能体、技能、MCP、工具

The SOC Lead Agent should feel like DeerFlow, but operate inside SOC service boundaries.

SOC Lead Agent 的定位：

- It is the analyst-facing conversational agent.
- It can inspect a review item through `InvestigationContext`.
- It can select SOC skills and propose actions.
- It can call read-only adapters or MCP-backed tools through controlled bridges.
- It cannot bypass services, approval, memory review, or audit.

```mermaid
flowchart TD
    Analyst["👤 Analyst"] --> Lead["🤖 SOC Lead Agent<br/>DeerFlow lead_agent profile"]
    Lead --> Context["📚 Bounded Review Context<br/>queue item / evidence / memory / external feedback"]
    Lead --> Skill["🧩 SOC Skills<br/>APT / EDR / HIDS / Asset / Endpoint"]
    Lead --> Proposal["📌 Action Proposal<br/>what to check / who should review"]
    Proposal --> Router{"🛡️ Action Boundary"}
    Router -->|"read-only"| Adapter["🛠️ Action Adapter / MCP<br/>asset.lookup / process_tree.lookup / threat_intel.lookup"]
    Router -->|"high-risk"| Approval["🛂 Approval Inbox"]
    Adapter --> Evidence["🔎 InvestigationEvidence"]
    Evidence --> Context
    Approval --> Audit["🧾 Audit/Event"]
```

### 6.1 Skill vs MCP vs Memory / Skill、MCP、Memory 怎么分

| Content type / 内容 | Belongs to / 应放在 | Example / 例子 |
| --- | --- | --- |
| Reusable investigation method / 通用研判方法 | Public SOC skill | How to reason about reverse shell, malicious outbound, process tree |
| External system query / 外部系统查询 | MCP or action adapter | CMDB lookup, EDR process tree, threat intel reputation |
| Governed operational fact / 有治理的运营事实 | Governed context registry + typed source adapter | Exercise participant, approved scanner campaign, maintenance window, asset state |
| Tenant-specific descriptive fact / 租户描述性事实 | Scoped memory or policy/config | Internal domain meaning, investigation note, special business-system context |
| Vendor field mapping / 字段映射 | Normalizer adapter | PingAn `zeusRawLogs[].message` mapping |
| Repeated operational conclusion / 历史处置经验 | Memory candidate then confirmed memory | This rule often flips attacker/victim direction under condition X |
| Eval sample / 验证样本 | Eval fixture | Desensitized APT/EDR/HIDS examples |
| Prompt fragment / 提示词片段 | Only if it is stable role/task instruction | Output requirements, evidence discipline |

### 6.2 Sub Agent Strategy / 子智能体策略

Long-term, APT/EDR/HIDS/network/endpoint/domain-specific agents should be sub-agents or
specialized profiles under a SOC orchestrator, not independent uncontrolled agents.

长期结构：

| Role / 角色 | Purpose / 用途 | Timing / 时机 |
| --- | --- | --- |
| SOC Lead Agent | Main analyst-facing orchestrator | Current direction |
| APT triage sub-agent | Network/APT alert reasoning | After domain flow stabilizes |
| EDR triage sub-agent | Endpoint/process/file/account reasoning | After process-tree evidence flow stabilizes |
| HIDS triage sub-agent | Host intrusion and integrity reasoning | After generic scenario taxonomy stabilizes |
| Threat hunting agent | Cross-alert hunting and IOC/TTP expansion | Later |
| Detection engineering agent | Rule tuning, false-positive pattern analysis | Later |
| Attack simulation agent | Authorized red-team or validation workflows | Later, strict scope and approval |

All agents share SOC contracts, service boundaries, approval, and memory policy.

---

## 7. Review, Approval, and External Feedback / 复核、审批、外部反馈

### 7.1 Review Queue / 复核队列

Review queue is the analyst's primary work surface.

It should show:

- Current conclusion with confidence.
- Evidence used and evidence gaps.
- Scenario findings and domain triage.
- Similar historical alerts.
- Relevant confirmed memory.
- Pending memory candidates.
- External disposition history.
- Read-only investigation evidence.
- Human checklist and suggested next steps.

### 7.2 Approval Boundary / 审批边界

High-risk actions must follow this path:

```mermaid
sequenceDiagram
    participant Agent as 🤖 SOC Agent
    participant Approval as 🛂 Approval Service
    participant Inbox as 📬 Approval Inbox
    participant Human as 👤 Approver
    participant Adapter as 🛠️ Action Adapter
    participant Audit as 🧾 Audit

    Agent->>Approval: submit SocAgentApprovalRequest
    Approval->>Inbox: persist pending request
    Human->>Approval: approve/reject/expire by request ID
    Approval->>Inbox: atomic pending -> terminal transition
    Approval->>Approval: approved only: create one-time grant in same transaction
    Human->>Approval: dry-run / execute boundary
    Approval->>Adapter: preflight allowed adapter/payload/context
    Adapter-->>Approval: dry-run result or execution boundary result
    Approval->>Audit: commit durable mutation audit
```

Current safety posture:

- Read-only investigation actions can produce `InvestigationEvidence`.
- High-risk actions create approval requests.
- Request lifecycle is `pending -> approved/rejected/expired`; approve loads the persisted request by ID and one request can create at most one grant.
- Exact resolution retries are idempotent; stale, forged, or semantically changed retries are rejected.
- Execute boundary exists, but real production side effects must wait for real adapter review.
- Approval grant is single-use. Request/grant/result changes and their secret-safe mutation audit commit in one command transaction through migration `0018_mutation_audit`.

### 7.3 External Disposition Sync / 外部处置反馈同步

Users may still work in Zeus or another old SOC system. Their status/reason updates must
feed SOC Agent rather than being lost.

```mermaid
flowchart LR
    OLD["🏢 Old SOC / Zeus<br/>status + reason"] --> MAP["🔁 ExternalDisposition Adapter<br/>mapping + target resolve"]
    MAP --> EVENT["SocExternalDispositionEvent"]
    EVENT --> TX["🔒 SocMutationUnitOfWork<br/>one external event / one transaction"]
    TX --> REVIEW["ReviewQueue state/reason sync"]
    TX --> CAND["Memory Candidate<br/>pending_review"]
    TX --> AUDIT["Decision + Mutation Audit"]
    TX --> EXT["ExternalDispositionRecord"]
```

Rules:

- The implemented application ingress is authenticated
  `POST /api/soc/external-dispositions` with
  `SocExternalDispositionIngressCommand(schema_version=soc.external_disposition_ingress.v1)`.
- The ingress requires a stable `event.source_event_id`; only `soc_admin` or
  `external_disposition_adapter` service roles may apply it. Source-specific field mapping and trust
  configuration remain server-owned and cannot be supplied by the caller.
- An exact source-event retry returns the existing logical result. Reusing the same semantic identity
  with changed content returns a conflict instead of silently overwriting history.
- This closes the generic application boundary only. Real Zeus/ITSM/SOAR webhook, Kafka or polling
  feeds still require customer endpoint, authentication/signature, tenant and replay configuration.
- External reason may update local review state if target resolution is unique and trusted.
- External reason can generate memory candidates.
- External reason cannot become confirmed memory without review.
- Mapping must be configurable per external system.
- Local correction, summary/queue, candidate, external record, eligible outcome and both audit
  records commit atomically; events are emitted only after commit. An exact retry returns the one
  existing logical result, while reuse of the idempotency key for changed content conflicts.

### 7.4 Governed Context Facts / 受治理上下文事实（GF-01 + AA-01 + EX-01 + DP-01 + EV-01..EV-03 Implemented）

Authorization is the first implementation, but it is not the only operational context that needs
source, scope, time and revocation. Use a shared typed envelope instead of a universal untyped KV
store or one matcher for every fact.

```text
GovernedContextFact
├── AuthorizedActivityFact
├── SecurityExerciseCampaignFact
├── ExerciseParticipantFact
├── AssetContextFact
├── IdentityContextFact
├── ChangeWindowFact
├── NetworkTopologyFact
├── ServiceRelationshipFact
└── RiskAcceptanceFact
```

`GovernedContextFact` owns common metadata: fact id/type/schema version, tenant/environment,
`valid_from/valid_until`, source type/ref/version/freshness, status, owner/reviewer/reason,
evidence refs, content hash and audit timestamps. Every subtype has a discriminated typed payload and
its own matcher/resolver. The system must not implement a generic natural-language fact matcher.

Shared lifecycle belongs to `SocGovernedContextService`; typed domain services compose it with
deterministic matchers. PostgreSQL may use one common fact envelope table with typed JSONB payloads
and indexed common columns, but Pydantic contracts and subtype validators remain mandatory.

Current GF-01 + AA-01 + EX-01 + DP-01 + EV-01..EV-03 implementation:

- Governed lifecycle contracts live in `soc_agent.contracts.governed_context`; the first instantiable
  subtype is vendor-neutral `AuthorizedActivityPayload`. Matching contracts live separately in
  `soc_agent.contracts.authorization` so persistence does not imply authorization.
- `SocGovernedContextService` owns propose/revise/activate/suspend/revoke/expire/get/list. A stable
  `fact_id` has append-only immutable versions identified by `fact_version_id`; every writer supplies
  `expected_latest_version`, and stale writers fail rather than overwrite a newer decision.
- Revision always creates a new `proposed` version and requires re-approval. Revising an active fact
  deliberately fails closed: the latest version is no longer active until an approver activates it.
- `GovernedContextFactRepository` is implemented by the in-memory test adapter and
  `SqlAlchemyAlertRepository`. Migration `0013_governed_context_facts` creates
  `soc_governed_context_facts`; one unique `current_key` per fact and unique `(fact_id, version)`
  enforce version-stream integrity.
- `AuthorizationQueryBuilder` consumes only canonical alert/entity/fact/scenario contracts. It does
  not recognize vendor aliases. Naive event times require an explicit tenant/integration IANA
  timezone and record that assumption.
- `AuthorizedActivityMatcher` selects the fact lifecycle version that existed at alert event time,
  then checks lifecycle, validity, source freshness, recurrence and typed scope. Different selector
  kinds are ANDed; values inside one kind are ORed. It emits
  `exact/partial/conflict/expired/not_found/unavailable` without an LLM.
- CLI provides lifecycle commands plus read-only `soc context match`. HIDS `java -> chattr` and EDR
  RemoteRegistry sample replay both produce explainable exact matches in step-12 validation.
- `SocAuthorizationEnrichmentService` attaches AA-01 results to existing runs through strict
  `AuthorizationEnrichmentCommand/Record/ApplyResult` contracts. Migration
  `0014_authorization_enrichments` stores append-only records with query hash, matcher policy, exact
  fact-version refs, actor, idempotency key and replay lineage.
- `SocReviewService.get_investigation_context()` projects enrichments into the shared API/Web/TUI read
  model and the bounded Lead Agent artifact. Timeline/counts label them as `shadow_only` and
  `decision_impact=none`.
- `SocDispositionProposalService` consumes one persisted exact enrichment linked to an open
  ReviewQueue and snapshots the current detection truth. Only `true_positive` may produce
  `closed_benign_true_positive`; missing/closed/mismatched queue, all other match states or verdicts
  fail closed. Migration `0015_disposition_proposals` persists immutable proposal,
  source enrichment/query/matcher/fact refs, detection snapshot, policy, actor and idempotency data.
- `soc disposition propose|list|get` and InvestigationContext/Web/TUI/Lead Agent expose the proposal.
  It is always `shadow`, `not_applied`, `requires_human_review=true`, `auto_close_allowed=false`, with
  no detection-truth or ReviewQueue impact.
- `SocDispositionEvaluationService` records explicit append-only outcomes only after the linked
  ReviewQueue is closed. It never infers labels from close-reason text. Corrections must explicitly
  supersede the latest outcome; analyst-resolution and independent sampled-quality-review lanes remain
  distinguishable.
- EV-01 creates reproducible `sha256_rank_v1` sample manifests over one exact
  tenant/environment/time/proposal-policy/matcher-policy cohort. Migration `0016_disposition_evaluation`
  stores manifests and outcomes in `soc_disposition_sample_manifests` and `soc_disposition_outcomes`.
- `SocDispositionEvaluationGateReport` measures resolution, shadow precision, override, sampled coverage/
  precision/agreement, source freshness and fact-version fan-out. Truncated datasets or broken enrichment
  lineage are insufficient data. Gate policy explicitly allowlists primary/sample outcome sources, and a unique
  outcome lineage key prevents concurrent root/successor labels. Passing means only
  `eligible_for_governed_rollout_review`; the report and policy both keep `auto_close_allowed=false`.
- EV-02 connects authenticated API/Web capture, Review TUI primary/sample commands, and a guarded trusted
  external-disposition bridge to `SocDispositionEvaluationService.record_outcome()`. Web close and outcome
  remain separate actions; API/TUI require explicit idempotency; no surface infers a label from close reason.
  Review TUI accepts a stable `--actor-id` so the service can enforce independent sampled-review identity.
  The external bridge requires a high-trust mapped event, verified target and exactly one matching proposal,
  reports deterministic skip reasons, and never silently replaces a newer analyst/replay label.
- EV-03 derives `SocDispositionSampleReviewInbox` from each immutable manifest and current proposal,
  ReviewQueue, primary outcome and sampled outcome records. It computes independent-review completion,
  reviewer conflicts and explicit readiness without adding a second campaign source of truth. Read-only
  Gateway endpoints feed the Web `抽样复核` view; opening an item preselects the server-returned
  `sample_id/proposal_id/queue` in the EV-02 form, so reviewers cannot cherry-pick outside the manifest.
- GF-01/AA-01/EX-01 do not inject facts into `LLMAnalysisRequest` or change `SocDecisionPolicy`.
  DP-01 generates a separate operational proposal but still cannot update the run, close ReviewQueue,
  write memory, authorize an action, or execute a response. EV-01 evaluates that proposal, EV-02 captures
  explicit labels and EV-03 organizes independent QA work; all retain the same no-mutation boundary.

#### 7.4.1 Authorized Activity Facts / 授权活动事实

The system must not ask an analyst to reconfirm the same known authorized activity for every alert,
but it also must not turn one confirmation into a permanent IP whitelist. Use a dedicated governed
fact lifecycle instead of memory or prompt text.

`AuthorizedActivityFact` minimum semantics:

| Dimension / 维度 | Required meaning / 必须表达 |
| --- | --- |
| Identity | Stable fact id, schema version, tenant and environment |
| Activity | `vulnerability_scan`, `penetration_test`, `maintenance`, `automation`, `service_traffic`, or an extensible tenant-scoped type |
| Subject scope | Scanner/service/asset/account selectors; prefer stable asset/service IDs and tags over an IP alone |
| Target scope | Asset IDs, tags, applications, domains or bounded CIDRs the activity may touch |
| Behavior scope | Scenario keys, normalized behavior signatures, service/process constraints, and optional detection aliases |
| Validity | `valid_from`, `valid_until`, optional recurrence/window, and evaluation against alert event time |
| Source | Authoritative system/type, source reference or ticket/campaign id, source version, evidence refs |
| Governance | `proposed/active/suspended/expired/revoked`, owner, reviewer, reason, created/updated time |

`AuthorizationMatchResult` is produced by deterministic code, not by the LLM. It records matched fact
ids, `exact/partial/conflict/expired/not_found/unavailable`, matched and missing dimensions, event time,
source freshness, policy version and evidence refs.

```mermaid
flowchart TD
    H["👤 Analyst or authoritative system<br/>确认授权活动"] --> P["📝 Proposed Fact<br/>scope + validity + source"]
    P --> G{"🛂 Governance Review<br/>owner / role / expiry"}
    G -->|"approve"| A["🪪 Active AuthorizedActivityFact"]
    G -->|"reject"| X["🗃️ Rejected"]
    A --> Q["🔍 AuthorizationQuery<br/>canonical entities + scenario + event time"]
    Q --> M["⚙️ Deterministic Matcher"]
    M -->|"exact + fresh + no conflict"| B["✅ Benign-TP disposition eligible<br/>先 shadow，后策略化自动关闭"]
    M -->|"partial / new / expired / conflict / unavailable"| R["👤 ReviewQueue<br/>只复核差异与新模式"]
    A -->|"expiry / revoke / source change"| E["⏳ Expired or Revoked<br/>立即停止匹配"]
```

Source and rollout rules:

- Preferred sources are change-management, scanner, maintenance, CMDB/security-tag or other
  authoritative systems exposed through read-only adapters/MCP. PostgreSQL stores the governed fact
  and source snapshot/cache; it must retain the external source reference and freshness.
- Analyst confirmation may create a `proposed` fact. Activation requires an authorized role, explicit
  scope and expiry. A free-text review note alone cannot activate it.
- `security_tag.lookup` may provide one evidence input, but a generic future
  `authorized_activity.lookup`/source-sync adapter owns authorization-specific records. Vendor names
  stop at adapters; core matching consumes canonical selectors.
- Exact matching can only make `closed_benign_true_positive` eligible. It must never rewrite a real
  detection to `false_positive`, and it never authorizes a response action.
- Initial rollout is shadow-only: show proposed disposition and match explanation while humans still
  close the case. Auto-close is enabled later only for exact, authoritative, fresh matches after
  replay precision, override rate and sampled-review gates pass.
- Historical alerts are evaluated using the authorization fact version valid at alert event time.
  Current state cannot silently rewrite old cases.

Rollout slices are deliberately separate:

| Slice | Responsibility | Must not do |
| --- | --- | --- |
| `GF-01` | Govern typed fact lifecycle and append-only versions | Match alerts |
| `AA-01` | Build canonical query and return deterministic match explanation | Persist enrichment or propose disposition |
| `EX-01` | Implemented: persist/version match enrichment and project it into investigation context/audit | Change detection truth or generate disposition |
| `DP-01` | Implemented: persist `closed_benign_true_positive` shadow proposal from open-queue exact enrichment + true-positive detection snapshot | Apply proposal or auto-close during shadow phase |
| `EV-01` | Implemented: explicit outcomes, reproducible samples, precision/override/freshness/fan-out gate | Apply proposal or enable auto-close |
| `EV-02` | Implemented: capture structured outcomes through Web/TUI/API and trusted external disposition bridge | Infer labels from free text, silently replace analyst labels, or bypass evaluation service |
| `EV-03` | Implemented: derived paginated sample-review inbox plus Web campaign navigation over persisted manifests and selected proposals | Enable auto-close or let reviewers cherry-pick outside a manifest |

#### 7.4.2 Security Exercise Context / 护网与红蓝对抗上下文

A security exercise requires three facts, not one IP whitelist:

| Fact / 事实 | What it proves / 能证明什么 |
| --- | --- |
| `SecurityExerciseCampaignFact` | Campaign time, environment, target scope, allowed/forbidden behaviors and versioned Rules of Engagement |
| `ExerciseParticipantFact` | At event time, an IP/CIDR/domain/account/certificate/agent id belonged to a red/blue/white team or other exercise role |
| `AuthorizedActivityFact` | That participant was allowed to perform this behavior against this target during this campaign |

```mermaid
flowchart LR
    E["🧾 Alert event<br/>time + peers + behavior + target"] --> P["🔍 ParticipantAttributionMatcher"]
    P -->|"exact"| R["Participant role<br/>red / blue / white / referee"]
    P -->|"ambiguous / conflict / expired"| H["👤 Human review"]
    R --> C["🔍 Campaign applicability<br/>time + environment + RoE version"]
    C --> A["🔍 AuthorizedActivityMatcher<br/>subject + target + behavior"]
    A -->|"all exact + fresh"| D["⚖️ detection=true_positive<br/>disposition=closed_benign_true_positive<br/>reason=authorized_security_exercise"]
    A -->|"out of scope / forbidden / missing"| H
```

Rules:

- Seeing a registered red-team IP only establishes participant attribution. It cannot prove that the
  current target or technique was authorized.
- Participant identifiers are time-bounded and multi-valued. Dynamic IP, NAT, shared jump hosts,
  proxying and identifier reassignment must yield `ambiguous/conflict` rather than forced identity.
- Ordinary analyst context should expose role/team refs; personal identity and official roster details
  require stricter access control and remain auditable.
- Do not mark red-team infrastructure as globally benign or erase its IOC history. Preserve the
  detection and attach campaign-scoped operational context.
- The canonical disposition remains `closed_benign_true_positive`; use a reason code such as
  `authorized_security_exercise` instead of creating one status per campaign type.

---

## 8. Data Contracts / 数据契约

The contracts are the product boundary. Adding a UI, daemon, agent, or vendor adapter should
not require rewriting core contracts.

| Contract / 契约 | Role / 作用 | Stability expectation / 稳定性 |
| --- | --- | --- |
| `AlertInput` | Canonical alert input | Stable, extensible through typed optional sections and metadata |
| `EvidenceLayer` | Evidence source/layer metadata | Stable |
| `FieldTrust` | Field trust and provenance | Stable |
| `CanonicalFieldProvenance` | Selected canonical source path and alternatives | Stable |
| `RoleClaim` | Observable/asserted/derived role evidence | Stable |
| `ScenarioHypothesis` | Evidence-backed scenario hypothesis | Stable |
| `RoleResolution` | Conflict-aware role result and evidence gaps | Stable |
| `ConflictReport` | Conflict and ambiguity report | Stable |
| `AnalysisRun` | Full runtime execution record | Stable for replay/audit |
| `AlertSummary` | Lightweight read model | Stable for queue/correlation/list |
| `CorrelationResult` | Structured historical similarity and reusable-evidence result | Stable read-only bridge into domain/report/context |
| `CorrelationEvalFixtureSet` | Versioned same/related/unrelated pair labels | Offline-only; must name scoring policy and preserve human rationale |
| `CorrelationEvalReport` | Retrieval, identity, fan-out, reason and evidence baseline | Read-only; `shadow_dedup_allowed=false` |
| `ReviewQueueItem` | Analyst work item | Stable state machine |
| `InvestigationContext` | Shared context for Web/TUI/Lead Agent | Stable but may gain new sections |
| `UnifiedInvestigationView` | Read-optimized investigation projection | Stable as display/read model |
| `UnifiedInvestigationReport` | Main-orchestrator analysis/correlation/evidence/domain report | Stable bounded report; no direct state mutation |
| `InvestigationEvidence` | Tool/MCP evidence record | Stable |
| `GovernedContextFact` | Shared typed fact envelope and lifecycle | GF-01 implemented stable contract |
| `AuthorizedActivityPayload` | Time-, scope- and source-bounded authorized activity definition | GF-01 storage + AA-01 deterministic matcher implemented |
| `SecurityExerciseCampaignFact` | Campaign scope and Rules of Engagement | Planned typed fact |
| `ExerciseParticipantFact` | Event-time participant role and identifier mapping | Planned typed fact |
| `ParticipantAttributionResult` | Deterministic participant identity resolution | Planned typed result |
| `AuthorizationQuery` | Vendor-neutral event-time matching input | AA-01 implemented stable contract |
| `AuthorizationMatchResult` | Explainable deterministic match result | AA-01 implemented; read-only/shadow, not a disposition |
| `AuthorizationEnrichmentRecord` | Append-only query/result/policy/fact-ref snapshot attached to a run | EX-01 implemented; replayable, `decision_impact=none` |
| `SocDispositionProposalRecord` | Append-only operational proposal with detection snapshot and authorization lineage | DP-01 implemented; shadow/not-applied, human review required |
| `SocDispositionSampleManifest` | Reproducible hash-ranked quality-review sample over one exact cohort | EV-01 implemented; append-only, seed hash only |
| `SocDispositionSampleReviewInbox` | Derived reviewer-specific progress/readiness over one immutable manifest | EV-03 implemented; paginated, no mutable campaign state, no auto-close |
| `SocDispositionOutcomeRecord` | Explicit append-only analyst/trusted-system label with supersession lineage | EV-01 implemented; no decision or queue impact |
| `SocDispositionEvaluationReport` | Read-only precision/override/sample/freshness/fan-out gate result | EV-01 implemented; passed means rollout review only, never auto-close |
| `SocDomainTriageResult` | Domain-level triage result | Stable |
| `SocDomainFinding` | Scenario-level finding | Stable taxonomy version required |
| `SocAgentActionProposal` | Agent proposal | Stable |
| `SocAgentApprovalRequest` | Approval inbox item | Stable |
| `SocAgentApprovalGrant` | One-time approval grant | Stable |
| `SocExternalDispositionEvent` | External status/reason update | Stable |
| `SocMemoryCandidate` | Pending memory proposal | Stable |
| `SocMemoryRecord` | Confirmed memory | Stable retrieval policy |

Contract rules:

- Prefer additive changes.
- Keep vendor-specific fields under adapter metadata, aliases, or scoped extensions.
- Never make `rule_code`, vendor scene name, or PingAn-only fields required for the system to work.
- Every external write path must include actor, source, reason, and audit metadata.

---

## 9. Persistence and Runtime Data / 存储与运行数据

Production and staging should use PostgreSQL as the SOC business store. Local SQLite can be
used only for smoke/demo convenience when explicitly configured.

Main persistence categories:

| Data / 数据 | Purpose / 用途 | Notes / 备注 |
| --- | --- | --- |
| Analysis runs | Replay and audit | Full payload, trace, result |
| Alert summaries | Review/correlation/list | Lightweight projection |
| Review queue | Human workflow | State, owner, reason, correction |
| Investigation evidence | Read-only tool/MCP evidence | Reusable in context, not memory by default |
| Approval requests/grants | High-risk action boundary | Terminal request lifecycle and at most one one-time grant per approved request |
| External dispositions | Old-platform status/reason sync | Idempotent by external event key |
| Governed context facts | Typed operational facts | Versioned, expiring, revocable, source-referenced |
| Context match audit | Authorization/attribution/applicability result | Replayable against event time and policy version |
| Memory candidates | Pending learning | Human review required |
| Confirmed memory | Reviewed experience | Retrieval-enabled by policy |
| Decision audit | Verdict lineage | analyze/replay/correct/external decision metadata and policy provenance |
| Mutation audit | L3 command lineage | Append-only actor/auth source/reason/idempotency/command hash/bounded result; no raw payload or secrets |
| Process events | Local signaling | Buffered until SQL commit; generic durable event streaming remains a later capability |

Service/repository rule:

- Services depend on repository protocols from `backend/soc_agent/protocols.py`.
- Entry layers must not write tables directly.
- Multi-write L3 commands must use `SocMutationUnitOfWork`; service code owns audit creation and
  emits process events only after the transaction commits.
- Migrations live under `backend/soc_agent/db/migrations/`.
- SOC tables stay separate from DeerFlow harness persistence unless a generic upstream extension is required.

---

## 10. Memory System / 记忆系统

Memory must help the agent learn from operations, but it must not become an uncontrolled
prompt-stuffing system.

### 10.1 Memory Granularity / 记忆粒度

Use layered and optional keys, not one over-specific composite key.

| Layer / 层级 | Example / 示例 | Required? / 必须吗 |
| --- | --- | --- |
| Tenant / 租户 | PingAn, customer A | Optional but recommended in multi-tenant |
| Source family / 来源族 | EDR, APT, HIDS, NIDS, SIEM, WAF | Optional |
| Detection alias / 检测别名 | rule_code, signature_id, analytic_id | Optional |
| Scenario / 场景 | reverse_shell, webshell, lateral_movement | Optional |
| Entity features / 实体特征 | IP, domain, process, user, host | Optional |
| Disposition pattern / 处置模式 | false positive because asset is scanner | Optional |

Retrieval should work even when some dimensions are missing. For example, if a vendor has no
`rule_code`, the system can still match by source family, scenario, entity features, summary,
and confirmed memory text.

### 10.2 Memory Lifecycle / 记忆生命周期

```mermaid
flowchart TD
    S1["📝 Source<br/>correction / review note / external reason / domain finding / repeated pattern"] --> C["🧬 SocMemoryCandidate<br/>pending_review"]
    C --> R{"👤 Human review"}
    R -->|"confirm"| M["📖 SocMemoryRecord<br/>confirmed"]
    R -->|"reject"| X["🗃️ rejected"]
    R -->|"expire/deprecate"| E["⏳ expired/deprecated"]
    M --> P{"Retrieval policy<br/>检索策略"}
    P -->|"enabled + budget + match"| CTX["📚 InvestigationContext.relevant_memories"]
    P -->|"disabled or weak"| NO["🚫 no runtime injection"]
```

Rules:

- LLM-discovered knowledge is candidate knowledge only.
- Correction, review note, domain finding, external feedback, and repeated pattern can all create candidates.
- Confirmation requires explicit human action through `SocMemoryService`.
- Confirmed memory retrieval is budgeted and reasoned; it is not dumped blindly into prompts.
- Active operational facts are not confirmed memory. Memory may describe how a scanner or exercise
  team tends to behave, but only governed-context services and typed matchers can determine identity,
  campaign applicability and authorization for a specific event time.
- Wiki/OKF-style displays can be exported later from DB memory, but DB remains the source of truth.

---

## 11. PingAn Capability Layer / 平安能力接入层

PingAn experience is valuable, but the product must stay vendor-neutral.

平安能力接入不等于把平安字段和提示词写死到核心。正确做法：

```mermaid
flowchart TB
    DOCS["📚 PingAn source docs<br/>source-docs/"] --> DECOMP["🧹 Knowledge decomposition<br/>拆解为通用/专属/工具/记忆/eval"]
    DECOMP --> CARD["🧾 Capability cards<br/>能力卡"]
    CARD --> SKILL["🧩 Public SOC Skills<br/>通用研判方法"]
    CARD --> ADAPTER["🔌 PingAn Adapter<br/>字段/证据标准化"]
    CARD --> MCP["🛠️ Mock or real MCP/Action<br/>资产/威胁情报/标签/EDR"]
    CARD --> MEMORY["🧬 PingAn-scoped Memory Candidate"]
    CARD --> EVAL["🧪 Eval Fixtures<br/>脱敏样本"]
    SKILL --> CORE["🧠 SOC Core"]
    ADAPTER --> CORE
    MCP --> CORE
    MEMORY --> CORE
    EVAL --> CORE
```

Current PingAn docs live under:

- `.notes/ai_soc/capabilities/pingan/source-docs/`
- `.notes/ai_soc/capabilities/pingan/onboarding.md`
- `.notes/ai_soc/capabilities/pingan/knowledge-decomposition.md`
- `.notes/ai_soc/capabilities/pingan/capability-cards.md`
- `.notes/ai_soc/capabilities/pingan/knowledge-candidates.md`

### 11.1 What goes where / 平安内容怎么落位

| PingAn content / 平安内容 | Destination / 落位 | Reason / 原因 |
| --- | --- | --- |
| Raw field quirks, `zeusRawLogs[].message` preference | PingAn normalizer adapter | Vendor-specific parsing belongs at edge |
| General investigation reasoning | `skills/public/soc-*` | Reusable across customers |
| PingAn internal asset/tag/business knowledge | Scoped memory or config | Not public prompt |
| Remote lookup capability | Mock now, real MCP/action later | External capability boundary |
| Historical false-positive/true-positive lessons | Memory candidate | Must be reviewed |
| Desensitized examples | Eval fixtures | Regression tests and replay |
| Prompt-like notes that are actually experience | Capability card or memory candidate | Avoid stuffing main prompt |

### 11.2 PingAn PA Track / 平安能力路线

The PA track is a capability onboarding method, not a separate product architecture.

| Step / 步骤 | Meaning / 含义 |
| --- | --- |
| PA-01 | Capability register |
| PA-02..PA-04 | APT/EDR/HIDS source decomposition |
| PA-05 | PingAn knowledge candidate register |
| PA-06 | Public skill minimal revisions |
| PA-07 | Read-only mock action adapters |
| PA-08 | Eval fixtures |
| PA-09 | Memory candidate entry |
| PA-10 | Domain triage MVP |
| PA-11 | Main orchestrator demo |
| PA-12 | Replace mock with real PingAn dev/staging MCP/API, credential-gated |

PA-12 must not be marked complete by adding more mock behavior. It requires real endpoint,
credentials, smoke report, and payload/latency/error evaluation.

---

## 12. Security and Permission Model / 安全与权限模型

| Level / 等级 | Meaning / 含义 | SOC Agent behavior / 行为 |
| --- | --- | --- |
| L0 | Read local context | Allowed through services |
| L1 | Read external data | Allowed through read-only adapter/MCP with audit |
| L2 | Generate recommendation | Allowed, must be labeled recommendation |
| L3 | Change internal SOC state | Requires trusted `auth_source`, command-specific role and service method |
| L4 | Execute external side effect | Approval required, adapter reviewed |
| L5 | Destructive or attack simulation | Explicit scope, approval, audit, later phase only |

Security invariants:

- No production secret in docs, skills, fixtures, or committed config.
- No side-effect tool call from LLM directly.
- No confirmed memory without human review.
- No tenant-specific knowledge in public generic skills unless sanitized and generalized.
- No unbounded raw alert dump into DeerFlow Lead Agent context.
- No bypass of `SocReviewService`, `SocMemoryService`, or `SocAgentApprovalService`.
- No L3 mutation may rely only on entry-layer authorization; core services must reject unknown provenance and missing roles.
- No Alpha L3 mutation may commit business state without its `SocMutationAuditRecord`; audit
  projections must be bounded and secret-safe, and exact retry semantics are keyed by operation,
  idempotency key and command hash.

---

## 13. Evaluation and Demo / 评测与演示

The system should be reviewable from a single alert before scaling to Kafka.

Useful command surfaces:

```bash
# Run one arbitrary alert through the service chain and create review context
soc demo alert alert_demo/apt-2026494.json --pretty

# Seed repeatable demo chains
soc demo run all

# Inspect review context
soc review context <queue-id> --summary --pretty

# Add analyst note and optionally create memory candidate
soc review note <queue-id> --note "..." --memory-candidate

# Review memory candidates
soc memory list --status pending_review
soc memory review <candidate-id> --confirm
soc memory search --query "reverse shell internal host"

# Chat through DeerFlow-aligned SOC Lead Agent
soc chat tui --queue-id <queue-id> --lead-agent

# Process daemon message locally
soc daemon process --message-json '{"kind":"alert",...}'

# Inspect model resolution without exposing credentials
soc llm status --analyzer-mode llm --model-name deepseek-v4-pro --pretty

# Run one alert through the real bounded model node
soc analyze alert.json --analyzer-mode llm --model-name deepseek-v4-pro --pretty

# Compare stub and live model over an offline sample set
soc eval offline samples/ --live-llm --model-name deepseek-v4-pro --pretty

# Reproduce the complete local Runtime/evaluation/governance review package
cd ..
./scripts/soc-runtime-validation.sh all
```

The generated Step 01-12 directories are review tracks, not twelve hidden Runtime nodes. The fixed
Runtime remains `normalize -> entity_extract -> fact_reconstruct -> build_analysis_input ->
skill_context -> analyze -> schema_validate -> evidence_grounding -> decide`; normalization
suggestions, human labels, correlation evaluation, and governed authorization are explicit offline or
sidecar tracks. Exact commands, artifact contracts, and the latest local findings are documented in
[`runtime-validation-runbook.md`](runtime-validation-runbook.md).

Acceptance criteria for the first complete demo:

- One alert can be normalized into `AlertInput`.
- Runtime creates an `AnalysisRun` and `AlertSummary`.
- Review queue item is visible.
- Investigation context includes conclusion, evidence, scenario findings, similar alerts if any,
  memory if any, and explicit evidence gaps.
- Lead Agent can answer around the review item using bounded context.
- Read-only action/MCP result becomes `InvestigationEvidence`.
- Analyst note/correction can create pending memory candidate.
- External disposition can sync status/reason into review context.
- No high-risk action is executed without approval boundary.

Current delivery priority / 当前交付顺序：

1. **Boss Demo v0.1**: first expose one repeatable, browser-first golden path so management can see
   the current product value without mistaking mock or shadow-only behavior for production.
2. **SOC Alpha Completeness Audit**: inspect the complete alert journey and classify every
   capability as `Complete`, `Gap`, `Mock`, `Data-gated`, or `Deferred`.
3. **Close Blocking Gaps**: close only the code-controlled P0/P1 blockers identified by the audit,
   then rerun the APT/EDR/HIDS end-to-end acceptance path.
4. **Real Data & Production Integration**: connect real providers and infrastructure, collect real
   labels, establish operations/SLO evidence, and progress through governed shadow/pilot gates.

The authoritative work packages, gates, Parking Lot and anti-drift rules live in
[`delivery-roadmap.md`](delivery-roadmap.md). The current implementation pointer lives only in
[`progress.md`](progress.md).

“Alpha complete” means the local/test product journey is repeatable and every mock or external-data
dependency is explicit. It does not mean `production-ready` while real CMDB/EDR/Zeus credentials,
production labels, operational SLOs, and high-risk response adapters are still unavailable.

---

## 14. Current Architecture Decisions / 当前架构决策

| Decision / 决策 | Rationale / 理由 |
| --- | --- |
| Use DeerFlow lead_agent for SOC conversational agent | Reuses existing agent/profile/skill/MCP infrastructure |
| Keep SOC code under `backend/soc_agent/` | Avoid invasive upstream fork changes |
| Keep entry surfaces thin | Prevent CLI/Web/TUI/Kafka logic divergence |
| Reuse DeerFlow `create_chat_model` for Runtime LLM | One provider/config/tracing implementation; no SOC-specific SDK client |
| Centralize detection decisions in `SocDecisionPolicy` | Keep confidence provenance, grounding guards and detection review reasons deterministic and auditable |
| Reconcile operational disposition separately and deterministically | Preserve `detection truth != operational disposition`; authorization may make a true positive benign but cannot make the behavior disappear |
| Use a typed `GovernedContextFact` envelope | Reuse tenant, event-time validity, source, status, revocation and audit without creating an untyped universal fact matcher |
| Compose exercise attribution and authorization | A red/blue/white-team identity match does not by itself authorize the observed target or behavior |
| Use canonical `AlertInput` | Vendor-neutral core |
| Use adapters for PingAn and future vendors | Extensible source integration |
| Use read-only investigation evidence first | Makes tools useful before risky automation |
| Use approval inbox for high-risk actions | Production-safe execution boundary |
| Use candidate-first memory | Prevent memory pollution |
| Treat Kafka as ingestion adapter | It is not the product UX itself |
| Keep PA-12 credential-gated | Mock cannot prove real integration |

---

## 15. Review Checklist / Review 清单

### Product Review / 产品评审

- Does the analyst know what happened, why, and what to do next?
- Can the system give a conclusion even when tools are unavailable?
- Are evidence gaps explicit and actionable?
- Does the flow support old-platform feedback and human notes?
- Is the demo path clear enough to show value on a single alert?

### Architecture Review / 架构评审

- Are CLI/Web/TUI/Kafka/Lead Agent all calling core services?
- Is DeerFlow core changed only where a generic extension point is justified?
- Are SOC modules cohesive and not becoming a flat pile of Python files?
- Are protocol boundaries represented by typed contracts?
- Can future APT/EDR/HIDS sub-agents reuse the same contracts?

### Data Contract Review / 数据契约评审

- Does the new field belong in canonical schema, vendor adapter metadata, memory, or evidence?
- Is the field required only when truly universal?
- Is `rule_code` optional and vendor-neutral through aliases?
- Does every state-changing operation carry actor, source, reason, and audit metadata?

### Memory Review / 记忆评审

- Is the source a correction, feedback, note, domain finding, repeated pattern, or external reason?
- Does it enter `pending_review` first?
- Is tenant/vendor specificity represented safely?
- Is retrieval explainable and budgeted?
- Can the memory be rejected, expired, or deprecated?

### Governed Context Fact Review / 受治理上下文事实评审

- Is this an operational fact rather than evidence, policy or a reusable investigation lesson?
- Is the subtype contract explicit, with a deterministic matcher/resolver?
- Are tenant/environment, subject, target, behavior and event-time scope explicit?
- Is the source authoritative, versioned, fresh and auditable?
- Will partial, conflicting, expired or out-of-scope matches still reach a human?
- Does the result preserve detection truth and only propose the canonical operational disposition?
- Can the fact be suspended, revoked or expired without editing prompts or code?
- For exercises, are campaign, participant attribution and authorized activity independently proven?

### Tool/MCP Review / 工具评审

- Is the capability read-only or side-effecting?
- Does the adapter validate payload and context refs?
- Does the result become evidence rather than verdict mutation?
- Is there a mock provider for tests and a real provider gated by config/credentials?
- Is sensitive data redacted before Lead Agent context?

### PingAn Review / 平安能力评审

- Is PingAn-specific parsing in `normalizers/pingan_platform.py` or equivalent adapter?
- Is general investigation reasoning promoted to public SOC skill only after sanitization?
- Are PingAn environment facts stored as scoped memory/config, not public prompt?
- Are capability cards updated before implementing a new PingAn integration?
- Does PA-12 use real dev/staging credentials and smoke report, not more mock?

---

## 16. Pointers / 相关文档

| Document / 文档 | Purpose / 用途 |
| --- | --- |
| `.notes/ai_soc/alert-lifecycle-flow.md` | Detailed lifecycle diagrams and state/data changes |
| `.notes/ai_soc/progress.md` | Durable progress ledger and next step |
| `.notes/reference-index/soc-agent-engineering-contracts.md` | API, protocol, code style, events, tests |
| `.notes/ai_soc/capabilities/pingan/onboarding.md` | PingAn capability onboarding plan |
| `.notes/ai_soc/integrations/mock-and-real-register.md` | Mock vs real integration register |
| `.notes/ai_soc/integrations/external-disposition-sync.md` | External status/reason sync plan |
| `.notes/ai_soc/memory/memory-tracking.md` | Memory tracking and retrieval plan |
| `.notes/ai_soc/governance/agent-profile-governance.md` | SOC agent/profile/skill governance |

The review source of truth is this document plus the contracts document. Historical chat context
should not be treated as durable project state.
