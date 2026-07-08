# SOC Alert Lifecycle Flow

> Updated: 2026-07-08
>
> 本文档描述两件事：
>
> 1. 当前代码已经实现的预警生命周期、状态变化和数据写入边界。
> 2. 下一阶段如何演进到可演示的 Main SOC Agent + 通用安全场景识别研判链路。
>
> 原则：LLM 和 Lead Agent 可以参与研判、提出调查动作和生成解释，但主流程、状态机、权限、审计、持久化仍由 SOC Runtime / Core Services 掌握。

## 1. 当前结论

当前系统已经具备一条可信的 SOC 预警闭环底座：

```text
alert in
  -> deterministic runtime analyze
  -> run / summary / review / audit persistence
  -> ReviewQueue Web/TUI
  -> SOC Lead Agent bounded context
  -> read-only action proposal
  -> investigation evidence persistence
  -> approval inbox / grant / execute boundary
```

但它还不是最终形态的“完整 SOC Agent + 多个子研判 Agent”。当前缺口在这里：

- `SocCorrelationService` MVP 已存在，能通过 CLI/core service 输出结构化相似告警、匹配原因和可复用 evidence；后续还需要接入 ReviewQueue/Web/TUI 可视化面板。
- External Disposition 已有 vendor-neutral event/mapping/service MVP，并且 high-trust mapped 外部结论可同步为本地 correction / review close；mapped 且可定位的外部 reason 可生成 pending memory candidate；DB/API 和 Web/TUI visibility 已完成。
- `SocMemoryCandidate` 已有 DB-first candidate store、API/CLI、ReviewQueue Web/TUI/Lead Agent visibility 和 confirm/reject/deprecate/expire review workflow；`confirm` 会生成 `SocMemoryRecord(retrieval_enabled=false)`；confirmed memory retrieval policy MVP 已能按 `retrieval_enabled=true` gate 返回 `relevant_memories`，但 prompt injection / runtime decision 仍未开启。
- APT/EDR/HIDS 已有统一 `SocDomainTriageRequest` / `SocDomainTriageResult` / `SocDomainFinding`。后续不按 F5/WAF 这类单一来源固定排期，而是按反弹 shell、webshell、横向移动、命令执行、恶意外联、提权、凭证滥用等通用安全场景补识别能力。
- PA-11 已有只读 Main Orchestrator demo：`SocMainOrchestratorService` 能把 deterministic analyze、selected skills、read-only action evidence、domain findings 和 review context 合成 `UnifiedInvestigationReport`。
- 这条链路已经通过 `UnifiedInvestigationView` 接入 ReviewQueue Web/TUI/Lead Agent bounded context 的统一调查视图；还没有替换为真实 PingAn MCP/API，PA-12 等真实 endpoint/凭证。
- `soc demo run [all|apt|edr|hids]` 已能把 APT/EDR/HIDS 脱敏样例持久化成可打开的 investigation chain：ReviewQueue item、read-only evidence、domain finding、confirmed/retrieval memory 和 unified investigation view。

因此当前 Alpha 主线不是继续堆更多 mock tool，而是把已经跑通的只读研判链路、外部反馈链路和候选记忆链路变成分析师可见、可审计、可复盘的产品闭环：

```text
[Done] PingAn knowledge decomposition + capability cards + pending candidates
  -> [Done] Correlation Service MVP
  -> [Done] PingAn PA-08 eval fixtures
  -> [Done] PingAn PA-09 memory candidate entry
  -> [Done] PingAn PA-10 domain triage MVP
  -> [Done] PingAn PA-11 Main Orchestrator demo
  -> [Done] External Disposition contract + review/correction + memory candidate
  -> [Done] External Disposition PostgreSQL/API/ReviewQueue visibility
  -> [Done] Memory candidate DB/API/ReviewQueue visibility
  -> [Done] Memory candidate review workflow / confirmed-memory boundary
  -> [Done] Confirmed memory retrieval policy / unified investigation visibility MVP
  -> [Partial] APT/EDR/HIDS source handlers done; generic scenario recognition pending
  -> [Done] Web/TUI visible investigation MVP
  -> [Done] Demo / Eval Script MVP
  -> [Partial] Memory candidate source integration: correction + domain finding bridge done
  -> [Partial] Generic security scenario recognition deterministic MVP
```

暂缓项不作为当前 Alpha 前置条件：

- Real dev/staging CMDB/EDR MCP replacement：等待 endpoint/凭证。
- PingAn PA-12 real MCP/API replacement：等待真实 PingAn dev/staging endpoint/凭证，不能用本地 mock 冒充完成。
- Wiki/OKF export projection：等 PostgreSQL memory store、retrieval 和 review workflow 稳定后再做，且只能作为 DB 的 projection。
- Prometheus / operations overview：等 Kafka/review/approval/runtime 数据流稳定后再做。
- High-risk real execute：等真实 staging adapter、审批策略、补偿和 adapter audit 成熟后再打开。

PingAn APT/EDR/HIDS 专属经验当前已经先落到 `.notes/ai_soc/pingan-capability-cards.md` 和 `.notes/ai_soc/pingan-knowledge-candidates.md`，并且已有 `SocMemoryService.propose_candidate()` 作为代码入口。它只生成 `pending_review` candidate，不能直接影响 runtime verdict；`SocMemoryService.review_candidate()` 是确认、驳回、废弃和过期的唯一边界。确认后的 `SocMemoryRecord` 默认 `retrieval_enabled=false`，只有后续显式打开 retrieval gate 的 confirmed record 才能通过 `SocMemoryService.find_relevant_records()` 返回到 `InvestigationContext.relevant_memories`；即便命中，也仍只是调查上下文，不直接改 verdict。

## 2. 当前已实现服务边界

| Service / Component | 当前职责 | 状态 |
|---|---|---|
| `SocAnalysisService` | 预警分析入口；调用固定 runtime，保存 run/summary/review/audit | Done |
| `SocCorrelationService` | 基于 alert summaries 和 investigation evidence 输出结构化相似告警、匹配原因和可复用 evidence | Done |
| `SocReviewService` | review queue、调查上下文、关闭、人工纠正；聚合 similar alerts、correlation result、domain triage、action evidence、external disposition feedback、memory candidates、relevant memories 和 `UnifiedInvestigationView` | Done |
| `SocAgentApprovalService` | approval request inbox、approval grant、dry-run、execute boundary | Done |
| `SocSkillResolver` | 从 canonical alert / review context 选择白名单 SOC domain skills，生成 compact bounded context | Done |
| `SocMainOrchestratorService` | 串起 analyze -> read-only route/action/evidence -> domain triage -> bounded review summary，输出 `UnifiedInvestigationReport` | Done for PA-11 |
| `SocLeadAgentChatService` | 通过 DeerFlow `DeerFlowClient(agent_name="soc-triage")` 进入现有 `lead_agent` | Done |
| `SocLeadAgentActionProposalBoundary` | 只处理显式 `<soc_action_proposal>`；read-only proposal 走 router/policy/dispatcher/registry，高风险写入 approval inbox | Done |
| `SocActionAdapterRegistry` | action adapter allowlist；当前支持 `asset.lookup`、`asset.locate`、`endpoint.process_tree.lookup` 等只读能力 | Done |
| `InvestigationEvidenceRepository` | 保存只读 action/tool 结果，供 ReviewQueue、Web/TUI、Lead Agent 后续复用 | Done |
| `SocMemoryService` | 生成并查询 pending review memory candidate；通过 service review workflow 确认/驳回/废弃/过期 candidate；`confirm` 生成 retrieval-disabled `SocMemoryRecord`；`find_relevant_records()` 只返回 retrieval-enabled confirmed records 并给出 score/match reason/token budget | Partial |
| `SocDomainTriageService` | APT/EDR/HIDS deterministic domain handlers；消费 skill context 和 read-only evidence refs，只输出 findings | Done for PA-10 |
| `SocKafkaDaemonRunner` / `SocKafkaConsumerRunner` | opt-in Kafka daemon run loop、mapper、dead-letter、manual commit、metrics JSONL | Done, production params waiting |
| `SqlAlchemyAlertRepository` | 当前统一实现 run、summary、review queue、audit、approval request、approval grant、investigation evidence、external disposition、memory candidate 和 memory record 持久化 | Done |
| `soc_agent.demo` / `soc demo run` | 可重复生成本地持久化 APT/EDR/HIDS investigation chain，方便 Web/TUI/CLI 打开统一调查视图 | Done for MVP |

## 3. 当前 As-Is 生命周期

```mermaid
flowchart TD
    subgraph Inputs["入口层"]
        CLI["CLI / API\nsoc analyze"]
        Kafka["Kafka daemon\nopt-in"]
        Web["Web ReviewQueue"]
        TUI["SOC TUI"]
        Lead["SOC Lead Agent\nsoc-triage"]
        External["External systems\nZeus / ITSM / SOAR"]
    end

    subgraph Core["Core Services"]
        Analysis["SocAnalysisService"]
        Review["SocReviewService"]
        Chat["SocAgentChatService / SocLeadAgentChatService"]
        Approval["SocAgentApprovalService"]
        ExternalSync["SocExternalDispositionService"]
    end

    subgraph Runtime["Fixed Runtime"]
        Normalize["normalize"]
        Entity["entity_extract"]
        Fact["fact_reconstruct"]
        BuildInput["build_analysis_input"]
        Skill["skill_context"]
        Analyze["analyze_stub / LLM node"]
        Validate["schema_validate"]
        Decide["decide"]
    end

    subgraph Stores["SOC Business Store"]
        Runs["soc_analysis_runs"]
        Summary["soc_alert_summaries"]
        Queue["soc_review_queue"]
        Audit["soc_decision_audit_log"]
        ApprovalReq["soc_approval_requests"]
        ApprovalGrant["soc_approval_grants"]
        Evidence["soc_investigation_evidence"]
        Disposition["soc_external_dispositions"]
        MemoryCandidate["soc_memory_candidates\npending / reviewed"]
        MemoryRecord["soc_memory_records\nretrieval_enabled=false"]
    end

    CLI --> Analysis
    Kafka --> Analysis
    Web --> Review
    TUI --> Review
    TUI --> Chat
    Lead --> Chat
    External --> ExternalSync

    Analysis --> Normalize --> Entity --> Fact --> BuildInput --> Skill --> Analyze --> Validate --> Decide

    Decide --> Runs
    Decide --> Summary
    Decide --> Queue
    Decide --> Audit

    Review --> Queue
    Review --> Runs
    Review --> Summary
    Review --> Audit
    Review --> Evidence

    Chat --> Review
    Chat --> Approval
    Chat --> Evidence
    Approval --> ApprovalReq
    Approval --> ApprovalGrant
    ExternalSync --> Review
    ExternalSync --> Audit
    ExternalSync --> Disposition
    ExternalSync --> MemoryCandidate
    Review --> MemoryCandidate
    Review --> MemoryRecord
```

### 3.1 数据状态变化

| 对象 | 状态字段 | 流转 | 说明 |
|---|---|---|---|
| `AnalysisRun` | `status` | `running -> success / needs_review / failed` | 完整 run payload 和输入快照保留，用于 replay |
| `AlertSummary` | `verdict / confidence / needs_review` | analyze/replay/correct 更新 | 面向列表、检索、关联和 review queue 的读模型 |
| `ReviewQueueItem` | `status` | `open -> closed` | close 不等于改判；改判必须走 correction |
| `CorrectionRecord` | `candidate_knowledge_status` | `pending_review` | 人工纠正不会直接写 confirmed memory |
| `ExternalDispositionRecord` | `mapped_status / apply_status` | `received -> mapped / unmatched -> applied / ignored` | 外部系统状态/理由同步记录；Zeus 只是 adapter，reason 只生成候选记忆 |
| `SocMemoryCandidate` | `status / runtime_decision_allowed` | `pending_review -> confirmed_candidate / confirmed / rejected / deprecated / expired` | 候选经验、外部 reason、domain finding 或 correction 先进入评审；任何状态都不会直接改 verdict |
| `SocMemoryRecord` | `status / retrieval_enabled` | `confirmed -> deprecated / expired`，默认 `retrieval_enabled=false` | 由 confirmed candidate 派生；当前只是 confirmed-memory 边界，不进入 prompt 或 runtime decision |
| `InvestigationEvidence` | `status` | `success / failed` | 只读调查证据，不自动改 verdict，不写 confirmed memory |
| `SocAgentApprovalRequest` | `status` | `pending` | 审批请求，不是执行授权 |
| `SocAgentApprovalGrant` | `status` | `approved -> consumed` | 一次性 execution token，当前 execute 仍不产生外部副作用 |

### 3.2 外部处置反馈流

外部处置反馈流用于接 Zeus、ITSM、SIEM/SOAR、客户自研工单系统等外部产品中的人工状态和理由。Zeus 只是第一个 adapter，不允许把 Zeus 字段、状态名或 ID 体系写死到 core service。

```mermaid
flowchart TD
    Zeus["Zeus / external ticket system\nstatus + reason update"] --> Adapter["ExternalDispositionAdapter\nwebhook / Kafka / polling"]
    Adapter --> Event["SocExternalDispositionEvent\nvendor-neutral + versioned"]
    Event --> Service["SocExternalDispositionService.apply_event"]

    Service --> Map["status mapping config\nexternal_status -> canonical_status"]
    Map --> Locate["target locate\nqueue_id / run_id / alert_id / external_case_id / weak correlation"]

    Locate -->|unique + trusted| Apply["apply external correction\nsync review/correction"]
    Locate -->|ambiguous / unmapped| Unmatched["unmatched disposition\nneeds review"]

    Apply --> Audit["soc_decision_audit_log"]
    Apply --> Record["soc_external_dispositions"]
    Apply --> Review["soc_review_queue update/close"]
    Apply --> Candidate["SocMemoryCandidate\npending_review"]
    Candidate --> SkillCandidate["SkillImprovementCandidate\npending review"]

    Unmatched --> Record
    Unmatched --> Audit
```

实现约束：

- Adapter 只做传输、认证、解码、字段映射和幂等键生成；不能直接写 repository。
- `SocExternalDispositionService` 是唯一允许同步外部状态到本地 review/correction/audit 的边界。
- 外部 free-text reason 默认只是 case feedback；只能生成 pending memory / skill improvement candidate，不能直接成为 confirmed memory 或 active skill。
- 状态映射必须可配置，未映射状态进入 `unknown/unmatched`，不自动改判。
- 幂等键必须包含 `tenant_id`、`external_system`、`external_case_id` 和外部事件版本/更新时间/hash，重复回放不能重复关闭队列或重复生成候选记忆。

### 3.3 Memory Candidate Review Flow

候选记忆评审流已经作为 confirmed-memory boundary MVP 落地。它解决“分析师能确认/驳回经验”的问题，但仍不解决“哪些 confirmed memory 可以进入提示词、检索和自动决策”的问题。

```mermaid
flowchart TD
    Source["Correction / External reason / Domain finding / Lead Agent summary"] --> Candidate["SocMemoryCandidate\nstatus=pending_review\nruntime_decision_allowed=false"]
    Candidate --> Service["SocMemoryService.review_candidate"]
    Service -->|confirm_candidate| ConfirmCandidate["candidate.status=confirmed_candidate\n仍不生效"]
    Service -->|confirm| Confirm["candidate.status=confirmed"]
    Confirm --> Record["SocMemoryRecord\nstatus=confirmed\nretrieval_enabled=false"]
    Service -->|reject| Reject["candidate.status=rejected"]
    Service -->|deprecate / expire| Retire["candidate.status=deprecated / expired\nlinked record deprecated / expired"]
    Service --> Event["SocEventType.MEMORY_UPDATED"]
```

实现约束：

- ReviewQueue Web、TUI、CLI、Gateway API 和 Lead Agent 后续只能调用 `SocMemoryService.review_candidate()`，不能直接写 `soc_memory_candidates` 或 `soc_memory_records`。
- `confirm_candidate` 只表示候选通过初审；`confirm` 才会创建 `SocMemoryRecord`。
- `SocMemoryRecord.retrieval_enabled=false` 是当前硬边界；retrieval policy、score、token budget 和 match reason 已有 MVP；prompt injection、retrieval enablement workflow 和 replay diff 后续单独实现。
- `rejected`、`deprecated`、`expired` 只能作为审计状态或后续过滤条件，不删除原候选和证据来源。

### 3.4 Confirmed Memory Retrieval Flow

Confirmed-memory retrieval policy MVP 已落地。它只解决“哪些 confirmed memory 可以作为调查上下文被看见”的问题，不解决自动决策和 prompt 注入。

```mermaid
flowchart TD
    Context["InvestigationContext input\nsummary + entities + skills + evidence"] --> Query["SocMemoryQuery\nfacets + text_terms + evidence_refs"]
    Query --> Service["SocMemoryService.find_relevant_records"]
    Service --> Gate["retrieval_enabled=true\nstatus=confirmed\nnot expired"]
    Gate --> Score["score + match_reasons\nmatched_facets + token_estimate"]
    Score --> Result["SocMemoryRetrievalResult"]
    Result --> Review["InvestigationContext.relevant_memories\nWeb/TUI/Lead Agent visible"]
    Review --> NoDecision["no verdict mutation\nno prompt injection yet"]
```

实现约束：

- `SocMemoryQuery` 不要求 topic、rule_code、canonical detection、scenario 或 entity 全部存在；缺任意 facet 只降低召回和分数。
- `SocMemoryRetrievalResult` 必须返回 `memory_id`、`version`、`content_hash`、`facets_hash`、`score`、`match_reasons` 和 `token_estimate`，便于 replay diff。
- `retrieval_enabled=false` 的 confirmed record 必须被计入 `skipped_retrieval_disabled`，不能返回为 match。
- ReviewQueue Web/TUI/Lead Agent 只能展示 `relevant_memories`，不能把它当成 active lesson 自动改判。

### 3.5 Unified Investigation View Flow

Web/TUI visible investigation MVP 已落地。它解决“分析师在一个地方看清本次调查材料”的问题，不解决自动决策、自动处置或新推理。

```mermaid
flowchart TD
    Queue["ReviewQueue queue_id"] --> ReviewService["SocReviewService.get_investigation_context"]
    ReviewService --> Base["run + summary + audit + similar_alerts\n+ action_evidence + external_dispositions\n+ memory_candidates + relevant_memories"]
    Base --> Correlation["SocCorrelationService\nCorrelationResult"]
    Base --> Domain["SocDomainTriageService\nSocDomainTriageResult"]
    Base --> Timeline["InvestigationTimelineItem[]\nanalysis / decision / evidence / external / memory / audit"]
    Correlation --> View["UnifiedInvestigationView"]
    Domain --> View
    Timeline --> View
    View --> Web["ReviewQueue Web\n统一调查视图"]
    View --> TUI["SOC Review TUI\nview counters + timeline"]
    View --> Lead["Lead Agent bounded artifact\ncompact view payload"]
    View --> NoWrite["read-only projection\nno DB write / no action / no verdict mutation"]
```

实现约束：

- `UnifiedInvestigationView` 只能由 `SocReviewService.get_investigation_context()` 聚合，不允许 Web/TUI/Lead Agent 自己拼同义结构。
- `CorrelationResult`、`SocDomainTriageResult`、`SocMemoryRetrievalResult`、`InvestigationEvidence`、`SocExternalDispositionRecord` 仍是各自来源的 source of truth；`evidence_timeline` 只是展示投影。
- domain finding、correlation match 和 relevant memory 都不能自动改 `AnalysisRun.decision`，也不能自动关闭 review item。
- Web/TUI 可展示统一计数、Top 关联告警、领域发现和时间线；更深的复盘仍应回到原始 run/evidence/audit/memory record。

## 4. 预警分析主链路

主控制流由 runtime 固定掌握。LLM 或 deterministic stub 只能作为固定节点，不决定是否跳过必要步骤。

```mermaid
flowchart TD
    Input["原始预警 JSON"] --> Service["SocAnalysisService.analyze(payload)"]
    Service --> EventStart["emit ANALYSIS_REQUESTED"]
    EventStart --> Runtime["DeterministicAnalysisRuntime.analyze"]

    Runtime --> S1["normalize\nAlertInput"]
    S1 --> S2["entity_extract\nExtractedEntities"]
    S2 --> S3["fact_reconstruct\nFactReconstructionResult"]
    S3 --> S4["build_analysis_input\nLLMAnalysisRequest"]
    S4 --> Skill["SocSkillResolver\nbounded skill context"]
    Skill --> S5["analyze_stub / LLM analyzer\nAnalysisNodeOutput"]
    S5 --> S6["schema_validate\nAnalysisResult"]
    S6 --> S7["decide\nDecision"]

    S7 --> Status{"Decision.needs_review?"}
    Status -->|true| NeedsReview["AnalysisRun.status = needs_review"]
    Status -->|false| Success["AnalysisRun.status = success"]
    Runtime -->|exception| Failed["AnalysisRun.status = failed"]

    NeedsReview --> Persist["持久化/读模型更新"]
    Success --> Persist
    Failed --> Persist

    Persist --> SaveRun["soc_analysis_runs"]
    Persist --> SaveSummary["soc_alert_summaries"]
    Persist --> UpsertReview["upsert soc_review_queue"]
    Persist --> SaveAudit["soc_decision_audit_log"]
    Persist --> EventEnd["emit ANALYSIS_COMPLETED / ANALYSIS_FAILED"]
```

### Runtime Step Trace

每个 runtime step 都写入 `PipelineStepTrace`：

| 字段 | 含义 |
|---|---|
| `step_name` | `normalize`、`entity_extract`、`fact_reconstruct`、`build_analysis_input`、`analyze_stub`/LLM step、`schema_validate`、`decide` |
| `status` | `running -> success` 或 `failed` |
| `input_hash` / `output_hash` | 当前 step 输入/输出稳定 hash |
| `warnings` | entity/fact/analysis request 中产生的警告 |
| `metadata` | analyzer model、prompt、parser、skill context 等元信息 |

## 5. ReviewQueue、Lead Agent 与 Evidence

ReviewQueue 是当前人工复核入口；SOC Lead Agent 只能拿 bounded context，不能直接读 repository、改 verdict 或执行处置。

```mermaid
flowchart TD
    QueueId["ReviewQueue queue_id"] --> ReviewService["SocReviewService.get_investigation_context"]
    ReviewService --> Context["InvestigationContext\nrun + summary + audit + correlation_result\n+ domain_triage_results + action_evidence\n+ external_dispositions + memory_candidates\n+ relevant_memories + investigation_view"]
    Context --> Bridge["SocLeadAgentReviewContextArtifact\nredacted + bounded + hashed"]
    Bridge --> LeadEntry["SocLeadAgentChatService\nagent_name=soc-triage"]
    LeadEntry --> DeerFlowLead["DeerFlow lead_agent stream"]

    DeerFlowLead --> Proposal["explicit <soc_action_proposal> JSON"]
    Proposal --> Boundary["SocLeadAgentActionProposalBoundary"]
    Boundary --> Policy["SocAgentActionPolicy"]

    Policy -->|read_only| ReadOnly["router + dispatcher + adapter registry"]
    ReadOnly --> ActionResult["soc.action_result"]
    ActionResult --> Evidence["soc_investigation_evidence"]

    Policy -->|high_risk| Approval["SocAgentApprovalService.submit_request"]
    Approval --> Inbox["soc_approval_requests"]
```

当前 read-only action 能力：

| Action | 当前实现 | 用途 |
|---|---|---|
| `asset.lookup` | in-memory / MCP-backed config | 查静态资产记录，验证资产查询 contract |
| `asset.locate` | local stdio MCP mock | 模拟 Zeus/CMDB/asset_to_bu 归属定位 |
| `endpoint.process_tree.lookup` | in-memory mock | 模拟 EDR 进程树和网络连接调查 |

所有 read-only action result 都只是 investigation evidence：

- 可以进入 ReviewQueue context。
- 可以进入 Lead Agent bounded artifact。
- 可以展示在 Web/TUI。
- 不能自动改判。
- 不能自动关闭 review item。
- 不能直接写 confirmed memory。

候选记忆当前可见但不生效：

- `SocMemoryCandidate(status=pending_review)` 可以进入 ReviewQueue context、Web/TUI 和 Lead Agent bounded artifact。
- Lead Agent 必须把 `memory_candidates` 当成待评审建议，不能当成 confirmed fact、active lesson 或处置依据。
- confirm/reject/deprecate/expire 状态机已由 `SocMemoryService.review_candidate()` 提供；`confirm` 生成的 `SocMemoryRecord` 默认 `retrieval_enabled=false`。
- confirmed memory retrieval policy MVP 已由 `SocMemoryService.find_relevant_records()` 提供；只返回 `retrieval_enabled=true`、confirmed、未过期 record，并带 score / match reason / token budget。
- prompt injection、runtime decision 影响和 memory replay diff 后续单独实现。

## 6. 审批与高风险动作

当前审批链路已经拆成 request inbox 和 grant token 两层。

```mermaid
flowchart TD
    HighRisk["高风险 action\nAgent / daemon / API"] --> Request["SocAgentApprovalRequest\nstatus=pending"]
    Request --> Inbox["soc_approval_requests"]
    Inbox --> Human["Web/TUI/后台人工审批"]
    Human --> Approve["create approval grant"]
    Approve --> Grant["SocAgentApprovalGrant\nstatus=approved\nexecution_token_id"]

    Grant --> DryRun["dry-run"]
    DryRun --> DryResult["校验 token/route/action/expiry\n不消费 token\n不执行外部副作用"]

    Grant --> Execute["execute boundary"]
    Execute --> Consume["grant.status=consumed\nconsumed_at / consumed_by\nexecution_result_payload"]
    Consume --> NoSideEffect["当前 external_side_effect=not_executed"]
```

当前 execute 只消费 token 并记录 execution boundary，不会封禁 IP、隔离终端、下发 F5 策略或调用生产 MCP。真实外部副作用必须等 adapter-level audit、补偿、失败重试和真实 staging 验证后打开。

## 7. To-Be：完整 SOC Agent 可见链路

目标是让分析师能看到一个预警被 Main SOC Agent 调度多个 domain sub-agent 研判，并形成统一报告。PA-11 已先完成 headless/eval demo；后续要把同一份 `UnifiedInvestigationReport` 接入 ReviewQueue Web/TUI 和 Lead Agent bounded context：

```mermaid
flowchart TD
    Alert["AlertInput / ReviewQueue Context"] --> Main["Main SOC Agent Orchestrator"]

    Main --> Corr["SocCorrelationService\nsimilar alerts + reusable evidence"]
    Main --> Router["Domain Router\nsource_type / detection / entities / skills"]

    Router --> Network["Network / APT Source Handler\nnetwork direction + IOC + attack chain"]
    Router --> Endpoint["Endpoint Source Handler\nprocess tree + account + lateral movement"]
    Router --> Host["Host Source Handler\nfile/process/login behavior"]
    Router --> Scenario["Generic Scenario Recognizer\nreverse shell + webshell + lateral movement + command execution"]

    Corr --> Network
    Corr --> Endpoint
    Corr --> Host
    Corr --> Scenario

    Network --> Merge["Unified Investigation Report"]
    Endpoint --> Merge
    Host --> Merge
    Scenario --> Merge

    Merge --> Decision["Runtime Decision\nverdict/confidence/needs_review"]
    Merge --> Evidence["Investigation Evidence Timeline"]
    Merge --> Review["ReviewQueue / Web / TUI / Lead Agent"]
```

这里的 sub-agent 可以先不是独立进程，也不一定马上是完整 DeerFlow custom agent。MVP 更合理的实现方式是：

- 先定义统一 `DomainTriageRequest` / `DomainTriageResult` contract（PA-10 已完成 APT/EDR/HIDS）。
- 每个 domain handler 可以是 deterministic + skill/prompt context 的受控节点。
- 后续再把稳定的 domain handler 升级为 DeerFlow-derived domain agent/profile。
- Main Agent 负责路由、并发策略、证据合并、冲突标记和审计，不让子 agent 直接写 DB 或执行工具。

## 8. 下一阶段实现路线

### Slice 0：PingAn Knowledge Decomposition + Capability Onboarding

目的：把用户掌握的平安 SOC 工具、MCP、skill、研判经验和处置经验结构化嵌入项目，并先区分通用 skill、平安 tenant memory、adapter/mapping、MCP/action、policy/config 和 eval fixture，避免把历史 prompt 原文直接塞进通用 skill 或主 Agent prompt。

范围：

- 新增并维护 `.notes/ai_soc/pingan-soc-capability-onboarding.md`。
- 新增并维护 `.notes/ai_soc/pingan-knowledge-decomposition-plan.md`。
- 新增并维护 `.notes/ai_soc/pingan-capability-cards.md`，作为 PingAn APT / EDR / HIDS capability card 台账。
- 每个经验点先整理成 capability card，再分类到 skill、tenant memory、MCP/action adapter、normalizer、policy/config、domain handler、eval case 或 memory candidate。
- 第一批已登记 APT 方向判断、APT 场景化研判、威胁情报、security tag、EDR 进程树、资产归属、HIDS 主机事件、HIDS event_type 研判等 P0 cards；F5/WAF 后续有源文档或样例后再补。
- 不把生产账号、token、内部系统地址或敏感数据写入仓库；真实 endpoint/凭证只通过本地配置或 secret 注入。

验收：

- 每个即将实现的平安 SOC 经验都有 capability card。
- 能明确它落到通用 skill、tenant memory、adapter、policy/config、domain handler、eval case 还是 memory candidate。
- 通用 `skills/public/soc-*` 不包含平安内部域名、账号、部门、规则码、模板 ID、策略 ID、字段别名或误报白名单。
- 每个能力都有至少一个脱敏样例或 fake fixture。
- read-only / high-risk / memory candidate 的安全边界明确。

### Slice 1：Correlation Service MVP

目的：让 SOC Agent 先具备“看历史、找相似、复用证据”的能力。

范围：

- 新增 `SocCorrelationService`。
- 新增 `CorrelationQuery`、`CorrelationMatch`、`CorrelationResult`。
- 基于 `soc_alert_summaries` 查询相似告警。
- 合并 `InvestigationEvidence` 中同 run / alert / entity 相关的只读证据。
- 接入 `SocReviewService.get_investigation_context()`，保留旧 `similar_alerts` 兼容展示，但新增更结构化的 `correlation_result`。
- 加 CLI 验证入口，例如 `soc correlate RUN_ID --pretty`。

验收：

- 不调用 LLM。
- 不依赖真实 MCP。
- 不改 DeerFlow core。
- 对同一批 demo alert 能看到相似告警、匹配原因和可复用 evidence。

### Slice 2：External Disposition Sync Contract

目的：让 Zeus、ITSM、SIEM/SOAR、客户自研工单系统中的人工状态和处置理由能回流 SOC Agent，同时保持协议可扩展、可插拔、可审计。

范围：

- 新增并维护 `.notes/ai_soc/external-disposition-sync-plan.md`。
- 固定 `SocExternalDispositionEvent` vendor-neutral schema，Zeus 只是第一个 adapter。
- 已有 `SocExternalDispositionService`、adapter port、状态映射、幂等键、unmatched record、audit、review/correction 同步和 pending memory candidate；skill improvement candidate 后续补。
- 支持 webhook、Kafka、polling、manual import 作为 transport adapter，但进入 service 前必须归一成同一 event。
- 明确外部 free-text reason 只能进入 pending memory / skill improvement candidate，不能直接写 confirmed memory 或 active skill。

验收：

- 不在 core service 中写死 Zeus 字段、状态名或 ID 体系。
- 未映射状态或无法唯一定位的 case 只能保存 unmatched record，不自动改判。
- 重复外部事件不会重复关闭 queue、重复改判或重复生成候选记忆。
- Web/TUI/Review context 后续能展示外部 disposition history 和 reason。

### Slice 3：Memory Tracking Contract

目的：固定 typed memory record + facets + retrieval policy，让 TUI、Kafka daemon、ReviewQueue、Lead Agent 和 domain triage 的重要结论后续能转成候选记忆。

当前状态：DB-first memory candidate persistence、review workflow 和 retrieval policy MVP 已完成；`SocMemoryCandidate` 现在有 `soc_memory_candidates` 表、repository、`soc memory list/get/review`、Gateway `/api/soc/memory/candidates`、ReviewQueue context/Web/TUI/Lead Agent bounded context。`confirm` 会创建 `SocMemoryRecord(retrieval_enabled=false)`；`soc memory search`、`/api/soc/memory/search` 和 `InvestigationContext.relevant_memories` 只返回 retrieval-enabled confirmed records，仍不能影响 runtime decision。

范围：

- 后续新增并维护 `.notes/ai_soc/soc-memory-tracking-plan.md`。
- 固定 DB-first memory contract：PostgreSQL 是 source of truth，wiki/OKF 只是后期展示、审阅和迁移 projection。
- 固定 typed record：`memory_type`、`status`、`content`、`facets`、`evidence_refs`、`version/hash`。
- 固定 facets：topics、canonical detection key、vendor aliases、scenario、entities、environment；缺失任意 facet 时系统仍要能工作。
- 明确具体 IP、UM、host、URL、hash 默认只作为 evidence / query dimension，不直接成为长期 memory 主粒度。
- 已新增 `SocMemoryRecord` / `SocMemoryRecordStatus`、`SocMemoryQuery`、`SocMemoryRetrievalResult` 和 `soc_memory_records`；后续继续规划 prompt injection、memory replay diff 和 retrieval enablement policy。
- 明确 TUI/Web correction、Kafka daemon repeated pattern、Lead Agent summary、DomainTriageResult 和 InvestigationEvidence 如何生成 memory candidate。
- candidate review workflow 已实现：confirm / reject / deprecate / expire 只能走 `SocMemoryService`。

验收：

- 不自动把 candidate 写成生效 memory；只有人工 `confirm` 才创建 `SocMemoryRecord`，且默认 retrieval disabled；只有显式 retrieval-enabled confirmed record 才能被检索返回。
- `pending_review` 不注入 prompt。
- 所有 candidate 都有 facets、来源、evidence refs、幂等键和 reviewer/audit fields。
- 后续代码只能通过 `SocMemoryService` 写 memory，入口层不能直接写 repository。

### Slice 4：Domain / Scenario Triage Contract

目的：先固定 source handler 和安全场景识别单元的输入输出，避免 EDR、APT、HIDS、WAF、云日志、反弹 shell、webshell、横向移动等各写一套。

范围：

- 新增 `DomainTriageRequest`。
- 新增 `DomainTriageResult`。
- 新增 `DomainTriageFinding`、`DomainTriageEvidenceRef`、`DomainTriageRecommendation`。
- 结果必须包含 `domain`、`confidence`、`findings`、`evidence_refs`、`evidence_profile`、`current_conclusion`、`human_checklist`。

验收：

- EDR/APT/HIDS/WAF/F5/云日志等来源，以及反弹 shell、webshell、横向移动、命令执行等安全场景都能用同一 schema。
- 子研判结果不能直接改 `AnalysisRun.decision`。
- 子研判结果可被统一 report 合并。

### Slice 5：Generic Security Scenario Recognition

目的：让 demo alert 不只按来源分支，还能识别跨来源安全场景。F5/WAF 是未来 source/adapter 示例，不是当前固定专项。

范围：

- 已有 APT/EDR/HIDS source handler 继续保留，用于提供来源视角 evidence。
- 新增通用场景识别：反弹 shell、webshell、横向移动、命令执行、恶意外联、提权、凭证滥用等。
- 场景识别可使用 LLM bounded reasoning，但输入必须来自 canonical alert、raw evidence、实体、read-only evidence、skill context 和 confirmed/retrieval memory。
- 场景识别输出仍是 `SocDomainFinding`，不直接改 verdict，不写 confirmed memory。
- Evidence Fusion First：raw/canonical alert、历史相似预警、外部处置反馈、confirmed memory、read-only action evidence 和可用工具证据都是常规输入；工具证据缺失只进入 evidence gaps 并降低 certainty，不能让 Agent 停止输出当前结论。

验收：

- deterministic MVP 已输出 `scenario_key`、vendor scenario hints、`evidence_profile`、`current_conclusion` 和 `human_checklist`。
- PingAn APT/EDR/HIDS eval 能识别命令/代码执行、恶意外联、横向移动等场景。
- 每个 scenario finding 都必须给出当前结论，即使证据不足也要明确证据缺口和人工核查清单。
- 后续再补 bounded LLM recognizer、自定义 taxonomy、scenario replay/eval 指标和 analyst feedback -> candidate memory。

### Slice 6：Main SOC Agent Orchestrator MVP

目的：把 correlation、domain routing、domain triage 和 report merge 串起来。

当前状态：PA-11 已完成只读 headless/eval 版本，入口为 `SocMainOrchestratorService` 和 `soc eval pingan-main`。

范围：

- 新增 `SocMainOrchestratorService` 或在 `SocAnalysisService` 后增加受控 investigation stage。
- 输入一个 run/review context。
- 自动选择 1-N 个 domain handlers。
- 合并 `CorrelationResult` 和 `DomainTriageResult`。
- 输出 `UnifiedInvestigationReport`。

验收：

- 单条 APT demo 能触发 APT + threat intel / security-tag evidence。
- 单条 EDR demo 能触发 EDR + endpoint process-tree evidence。
- 单条 HIDS demo 能触发 host-event context + security-tag evidence。
- 多 domain 冲突要显式展示，不能静默覆盖。

遗留：

- 当前 PA-11 使用 fixture action specs 和 mock adapters；PA-12 需要真实 PingAn MCP/API endpoint/凭证后替换 provider。
- correlation 尚未并入 PA-11 report；下一版 report merge 需要加入 `CorrelationResult`。
- Web/TUI visible investigation MVP 已通过 `UnifiedInvestigationView` 展示 correlation、domain finding 和 timeline；`UnifiedInvestigationReport` 仍是 PA-11 headless/eval report，后续 demo/eval slice 再决定是否持久化或单独展示。

### Slice 7：Web/TUI 可见化

目的：让你和同事能直观看到“谁参与了研判、用了什么证据、给了什么结论”。

当前状态：MVP 已完成。ReviewQueue Web/TUI 和 Lead Agent bounded context 已能消费 `InvestigationContext.investigation_view`，展示统一计数、领域发现、调查时间线、Top 关联告警、外部反馈和记忆上下文。

范围：

- ReviewQueue context 页面增加：
  - correlation panel：已通过 unified view + 原有相似告警区块展示
  - domain triage panel：已通过 unified view 展示
  - evidence timeline：已通过 `InvestigationTimelineItem[]` 展示
  - action proposal panel：已通过 approval inbox/proposal 区块展示
- TUI 增加对应文本视图。
- Lead Agent bounded context 带入 report summary，不塞完整 raw payload。

验收：

- 打开一个 review item，能看到完整研判链路。
- 能区分 runtime decision、domain findings、read-only evidence 和人工 correction。
- 统一视图只读，不写 DB、不执行 action、不改 verdict。

### Slice 8：Demo / Eval Script（Done for APT/EDR/HIDS MVP）

目的：形成可重复演示和回归验证。

范围：

- 已用 PingAn 脱敏样本生成 APT/EDR/HIDS demo run。
- 已提供 `soc demo run [all|apt|edr|hids]` 跑持久化链路。
- 已输出 JSON report + Web/TUI/CLI 可打开的 review item。

验收：

```text
soc demo run all --database-url ... --init-db --pretty
soc review context REV-... --database-url ... --pretty
soc review tui --database-url ...
soc chat tui --lead-agent --queue-id REV-... --database-url ...
```

能稳定看到同一条预警的 runtime、domain triage、read-only evidence、relevant memory 和 review 状态。本 MVP 暂不自动种 external disposition，避免 demo queue 被 high-trust 外部反馈自动关闭。

### Slice 9：Memory candidate source integration（Partial）

目的：把系统里会产生经验沉淀价值的路径统一收敛到 pending memory candidate，而不是散落在各自模块里。

范围：

- TUI/Web correction、external disposition、domain finding、Lead Agent proposal、Kafka/daemon 处理结论都通过统一 command 生成 candidate。
- 每个 candidate 必须带 source/evidence/validity/idempotency/facets/review owner。
- 不直接写 confirmed memory；不启用 prompt injection；不改 runtime verdict。

当前已完成：

- `SocMemoryCandidateSourceBridge` 作为统一来源桥接层，所有新增来源应先构造 `SocMemoryCandidateCreateCommand`，再通过 `SocMemoryService.propose_candidate()` 写入。
- `SocReviewService.correct()` 在配置 `MemoryCandidateRepository` 时会自动把人工/外部复用 correction 生成 pending candidate，并把 `memory_candidate_id` 写回 `CorrectionRecord`、audit payload 和 review corrected event。
- `DomainTriageResult/SocDomainFinding` 已有 bridge/factory，可按 finding 稳定幂等生成 pending candidate；当前仍需由后续 entry/service 显式调用。
- `InvestigationEvidence` 仍只是候选记忆的 evidence ref，不直接触发 memory 写入。

剩余接入：

- ReviewQueue 关闭备注、Lead Agent proposal、Kafka/daemon repeated pattern 和 domain finding 持久化路径还需要逐个接入同一个 bridge。
- 每个新来源必须补幂等测试，证明重放不会重复写 candidate。

验收：

- 每类来源都能生成可查询 pending candidate。
- ReviewQueue context / Web / TUI 能看到来源和证据。
- 重放同一来源不会重复写 candidate。

## 9. 暂缓项

这些需求有价值，但不是看到完整 SOC Agent Alpha 的前置条件：

- 真实 dev/staging CMDB/EDR MCP replacement：等待 endpoint/凭证。
- 真实 high-risk execute：等待 staging adapter、补偿、失败审计和审批策略成熟。
- Kafka bounded worker pool：等待真实吞吐、DB/K8s 参数和 LLM 限流策略。
- Prometheus `/metrics` exporter 和运行态势看板：已记录在 `.notes/archive/ai_soc/deferred/operations-overview-deferred.md`。
- 外部 Knowledge RAG / 威胁情报大屏：Phase 5。

## 10. 当前可见 Alpha 目标

Alpha 的定义不是“完全自动化 SOC”，而是：

```text
给一条 APT/EDR/HIDS/WAF/F5/云日志等来源的预警
  -> 系统能稳定跑完整 runtime
  -> 找出历史相似告警
  -> 选择对应 source handler 和通用安全场景识别
  -> 生成结构化 findings（例如反弹 shell / webshell / 横向移动 / 命令执行）
  -> 复用 read-only evidence
  -> 产出统一 Investigation Report
  -> 在 Web/TUI/Lead Agent context 里可审阅
  -> 所有状态、证据、审批边界可追踪
```

这条路线优先保证“看得见、查得清、改得了、可回放”，再考虑自动关闭、自动处置和大规模并发。
