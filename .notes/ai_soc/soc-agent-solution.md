# SOC Agent 预警分析系统 — 完整方案 v4

> 基于 DeerFlow 架构学习 + Claude Code 源码研究 + 业界调研（Dropzone AI、AgentSOC、Vigil SOC、Radiant Security、D3 Security）
> 整理：2026-06-17 · v4（新增主 Agent + 子 Agent 编排层，吸收 Claude Code 设计模式）
> 参考：`.notes/research/hermes-vs-deerflow-agent-patterns.md`（Claude Code 可借鉴设计模式清单）
> 工程契约：`.notes/reference-index/soc-agent-engineering-contracts.md`（代码风格、API、通信协议、工具权限、测试门禁）

---

## 一、整体架构

### 0.1 当前执行决策

当前方向固定为：**先做 DeerFlow-aligned SOC Agent，不另起一套独立产品入口**。

执行优先级：

1. Phase 1 先完成 headless CLI + `SocAnalysisService` + PostgreSQL 持久化 + `show/replay/correct` 可靠闭环。
2. Web UI、TUI、Gateway API、Channels 后续都复用同一套 core services，不复制 pipeline。
3. Kafka/Redpanda 是后台 ingestion adapter，用来消费预警流并调用 core service，不是替代 Web/TUI 的用户入口。
4. LLM 不掌握主控制流；runtime 固定流程，LLM 只在受控节点内做结构化建议。
5. 记忆和知识写入必须可审计、可回滚、可人工确认，不能让 LLM 自发现结果直接变成事实。

文档关系：本文件决定“做什么和先后顺序”；`.notes/reference-index/soc-agent-engineering-contracts.md` 决定“代码接口、协议、边界和测试怎么约束”。如果两者措辞冲突，以本文件为方向源头，并同步修正工程契约。

### 1.0 产品目标与目标用户

SOC Agent 的第一目标不是替代分析师，也不是一上来做全自动 SOC，而是：

> 帮 SOC 分析师更快、更一致地完成告警初筛、误报降噪、证据整理和经验沉淀，并保证每一次判断可审计、可纠正、可回滚。

因此产品路径遵循“先可信单条研判，再关联降噪，再学习，再 7×24 自动化”的顺序：

```text
Phase 1：单条告警可信研判
Phase 2：历史关联与重复告警降噪
Phase 3：分析师反馈驱动的学习
Phase 4：Daemon 自动处理 + 人工复查
Phase 5：知识增强、威胁情报、复查大屏
```

目标用户：

| 用户 | 核心任务 | 产品必须满足 |
|---|---|---|
| 一线 SOC 分析师 | 看告警、判断真假、写结论 | 快速获得 verdict、confidence、证据、解释；判断错了能马上纠正 |
| 值班负责人 | 看低置信/高风险队列，做批量复查 | 能批量审核、发现误判模式、追踪分析质量 |
| 安全平台管理员 | 配数据源、阈值、模型、知识和规则 | 可配置、可回滚、可审计，不因知识污染影响全局判断 |
| 开发者/维护者 | 调试 pipeline、回放样例、验证效果 | CLI、样例数据、可重复测试、明确日志和错误 |

Phase 1 的主用户是开发者/维护者 + 一线 SOC 分析师；Phase 4 以后再重点服务值班负责人和团队运营。

### 1.1 架构总图

```mermaid
graph TB
    subgraph 数据源
        EDR["EDR / SIEM"]
        KAFKA["Kafka 预警流"]
    end

    subgraph 入口层["DeerFlow-aligned 入口层"]
        TUI_MODE["TUI / Terminal Workbench<br/>（值班运营/交互调查）"]
        CLI_MODE["Headless CLI<br/>（--print/--json 风格，开发/调试/单条研判）"]
        API_MODE["Gateway API<br/>（Web UI / 外部系统 / LangGraph-compatible API）"]
        WEB_UI["Web UI<br/>（批量复查/可视化）"]
        CHANNEL_MODE["Channels<br/>（Slack/Telegram/Feishu 等 IM 接入）"]
        INGEST_MODE["Kafka Ingestion Adapter<br/>（后台消费，不是独立用户入口）"]
    end

    subgraph SERVICES["Core Services（统一业务入口）"]
        ANALYSIS_SERVICE["SocAnalysisService<br/>analyze / replay"]
        REVIEW_SERVICE["SocReviewService<br/>review / correct"]
        MEMORY_SERVICE["SocMemoryService<br/>facts / lessons"]
        CHAT_SERVICE["SocAgentChatService<br/>interactive investigation"]
    end

    subgraph SOC_Agent["SOC Agent（Main Agent + Sub Agent 编排）"]
        MAIN["<b>Main Agent</b>（持久）<br/>────────────────<br/>· 滑动窗口（最近200条摘要）<br/>· 倒排索引（实体→alert_id）<br/>· 活跃子Agent管理<br/>· 去重判定（不调LLM）"]
        
        SUB["<b>Sub Agent</b>（per-alert，短命）<br/>─────────────<br/>① 实体提取<br/>② 经验快查<br/>③ 关联查询<br/>④ 漏斗关联<br/>⑤ LLM 综合分析<br/>⑥ 置信度决策<br/>⑦ 写入存储"]
        
        MAIN -->|"去重未命中·派发"| SUB
        MAIN -->|"去重命中·merge"| SKIP_MERGE["直接引用已有结论<br/>不调 LLM"]
        SUB -->|"返回结论+context_modifier"| MAIN
    end

    subgraph 存储层
        SOUL["soc_soul.md<br/>（部署时写一次）<br/>环境知识+分析原则"]
        MEMORY["soc_facts<br/>（PostgreSQL 动态知识）<br/>模式级知识+纠正经验"]
        DB[("PostgreSQL<br/>─────────<br/>alert_summaries<br/>（每条预警摘要）<br/><br/>lessons_learned<br/>（条件→动作规则）<br/><br/>review_queue<br/>（待复查记录）")]
    end

    subgraph 反馈闭环
        CORRECT["纠正对话<br/>LLM 追问→提取知识"]
        REVIEW["大屏复查<br/>分析师批量确认/驳回"]
    end

    EDR --> KAFKA
    KAFKA --> INGEST_MODE
    EDR --> CLI_MODE

    CLI_MODE --> ANALYSIS_SERVICE
    TUI_MODE --> ANALYSIS_SERVICE
    TUI_MODE --> REVIEW_SERVICE
    TUI_MODE --> CHAT_SERVICE
    API_MODE --> ANALYSIS_SERVICE
    API_MODE --> REVIEW_SERVICE
    API_MODE --> MEMORY_SERVICE
    CHANNEL_MODE --> CHAT_SERVICE
    INGEST_MODE --> ANALYSIS_SERVICE
    WEB_UI --> API_MODE

    ANALYSIS_SERVICE --> MAIN
    REVIEW_SERVICE --> DB
    MEMORY_SERVICE --> MEMORY
    CHAT_SERVICE --> MAIN

    SUB -->|读| SOUL
    SUB -->|读| MEMORY
    SUB -->|读| DB
    SUB -->|写| DB

    CLI_MODE -->|纠正触发| CORRECT
    TUI_MODE -->|交互纠正| CORRECT
    CORRECT -->|提取知识| MEMORY
    CORRECT -->|提取规则| DB
    CORRECT -->|同步更新| MAIN

    DB -->|复查队列| REVIEW
    REVIEW -->|确认/驳回| DB
    REVIEW -->|提取规则| DB
    REVIEW -->|同步更新| MAIN
```

### 1.2 DeerFlow-aligned 入口 + 统一 Core Services

SOC Agent 的产品入口应和 DeerFlow 保持同构，而不是另起一套产品形态。DeerFlow 当前提供 Web UI、Gateway/FastAPI API、LangGraph-compatible run/thread API、`deerflow` 终端 TUI，并且 TUI console script 同时支持 headless `--print` / `--json` / `--cli` 模式；此外还有 Slack、Telegram、Feishu、Discord、DingTalk、WeCom/WeChat 等 channel 接入。

因此 SOC 对外也按这些入口演进：SOC Web UI、SOC Gateway API、SOC TUI/Terminal Workbench、SOC headless CLI、SOC Channels。Kafka consumer 是后台 ingestion adapter，用来把预警流送进 core services，不应被当成和 Web/TUI 并列的用户产品入口。

| 维度 | DeerFlow 已有形态 | SOC 对齐形态 | SOC 用途 |
|---|---|---|---|
| Web UI | Next.js 前端 | SOC 专用 Web 视图 | 告警队列、case/evidence、复查、可视化 |
| Gateway API | FastAPI routers + LangGraph-compatible API | SOC routers / external API | Web UI、外部系统、自动化集成 |
| TUI | `deerflow` terminal workbench，Textual 可选 | `soc` terminal workbench | 值班运营、交互调查、run trace、review queue |
| Headless CLI | `deerflow --print/--json/--cli` | `soc analyze/correct/replay --json` | 开发调试、样例回放、CI、单条研判 |
| Channels | Slack/Telegram/Feishu 等 | SOC 通知/轻量交互 channel | 通知、确认、补充上下文、低频交互 |
| Background ingestion | DeerFlow 无等价用户入口 | Kafka/Redpanda adapter | 7x24 消费预警流，调用 core service |

**入口边界**：

- Phase 1 先做 headless CLI + `SocAnalysisService`，等价于 DeerFlow 的 headless console script 能力，不急着做完整 TUI/Web UI。
- TUI 是和 DeerFlow 对齐的 terminal workbench，可作为 Phase 3/4 的 operator console 引入，定位是交互入口和运行观测面板，不重新实现业务逻辑。
- Web UI 等 `review_queue`、置信度、批量复查模型稳定后再做，避免早期 UI 返工。
- Web UI 和 Channels 都通过 Gateway/API 或对应 channel adapter 进入 service，不直接拼业务逻辑。
- Kafka ingestion adapter 只负责消费、反压、offset/lease、失败重试和调用 service，不拥有分析逻辑。
- Headless CLI、TUI、API、Web UI、Channels、Kafka adapter 都只做 transport / presentation / session 编排，业务逻辑全部进入 `core services`。
- Core services 是稳定 public API：后续入口不直接调用 pipeline、DB repository、memory store 或 LLM adapter。

建议 service layer：

| Service | 职责 | 典型调用方 |
|---|---|---|
| `SocAnalysisService` | 单条/批量告警分析、replay、run 查询 | Headless CLI、API、Kafka adapter、TUI、Web UI |
| `SocReviewService` | review queue、人工确认/驳回、纠正流程 | TUI、Web UI、API、Channels |
| `SocMemoryService` | facts/lessons 查询、确认、驳回、回滚 | Headless CLI、TUI、Web UI |
| `SocIngestionService` / `SocDaemonService` | Kafka worker orchestration、offset/lease/backpressure | Kafka adapter |
| `SocAgentChatService` | 类 DeerFlow 的交互式调查、澄清、case 内对话 | TUI、Web UI、Channels |

当前已落地第一步：`backend/soc_agent/core/service.py` 中的 `SocAnalysisService.analyze()` 包装 deterministic runtime。后续入口都应优先接这个 service，而不是直接拼 pipeline。

### 1.2.0 SOC Lead Agent、Skill、MCP 与 Node Prompt 分层

SOC Agent 的长期目标不是一个传统告警分类器，而是一个 DeerFlow-aligned 的安全运营智能体平台。必须区分“总控 Agent prompt”和“固定 Runtime 节点 prompt”，否则后续接 EDR skill、APT skill、资产归属、攻击方向判断、封禁 MCP 时会互相污染。

分层原则：

```text
SOC Lead Agent / Operator Agent
  -> 负责交互、任务理解、选择 domain skill、选择 MCP/tool、提出调查计划

SOC Runtime / Core Services
  -> 负责固定流水线、状态机、schema/domain validation、审计、replay、权限和失败处理

Domain Skills
  -> 负责提供 EDR / APT / F5-WAF / HIDS / NIDS / 资产归属 / 攻击方向 / 处置剧本等领域知识

MCP / Tool Gateway
  -> 负责调用 EDR、资产系统、SOAR、防火墙、Kafka、PostgreSQL 等外部能力

Node Prompts
  -> 负责固定节点内的结构化推理，例如 llm_analyze、correlation_rerank、knowledge_candidate_extract
```

这意味着：

- `soc-analysis-v1` 是 **analysis node prompt**，不是 SOC Lead Agent 的总控 prompt。
- Node prompt 只能在 Runtime 指定节点内运行，输出必须进入 schema/domain validation。
- Domain skill 可以动态选择，但选择结果必须以受控 skill context 注入节点或交互 agent，不能绕过 Runtime 直接改 DB、memory 或执行处置。
- MCP/tool 可以动态调用，但必须经过 Tool Gateway、权限策略、审计和必要的人类审批。
- SOC Lead Agent 可以像 DeerFlow `lead_agent` 一样使用 skills、MCP、tool search、subagent，但它不能取代 core service 的状态机和安全边界。

典型流程：

```text
EDR 告警进入
  -> normalize / entity_extract / fact_reconstruct / build_analysis_input
  -> SkillResolver 选择 edr-triage / lateral-movement / asset-ownership 等 skill
  -> PromptBuilder 注入 bounded request + selected skill context
  -> LLM 输出 AnalysisResult / action proposal
  -> Policy 判定是否允许查询或处置
  -> 低风险查询调用 EDR/资产 MCP；高风险封禁/隔离必须人工审批
  -> audit / summary / review queue / replay
```

早期实现顺序：

1. Phase 1 先把 node prompt、JSON parser、真实 LLM analyzer behind flag 做稳。
2. Phase 2/3 再引入 `SocSkillResolver`，先用 deterministic 规则按 `source_type`、`detection_key`、category、entity kind 选择 skill。
3. Phase 3/4 再让 SOC Lead Agent 在 TUI/Web/Chat 场景中做交互式调查和受限软路由。
4. MCP/tool 处置能力最后接入；查询类工具先接，封禁/隔离/禁用账号必须默认需要审批。

### 1.2.1 Phase 1 当前实际 Runtime Pipeline

截至 2026-07-01，Phase 1 已落地的实际 runtime 不是早期草案里的简单 `normalize -> entity_extract -> analyze_stub -> validate -> decide`，而是已经补上了 ZEUS/天眼输入可信度治理和 LLM-ready 分析输入层：

```text
vendor/raw payload
  -> normalize
  -> entity_extract
  -> fact_reconstruct
  -> build_analysis_input
  -> analyze_stub / later llm_analyze
  -> schema_validate
  -> decide
  -> audit / summary / review queue
```

各步骤职责：

| Step | 当前实现 | 输入 | 输出 | 职责边界 |
|---|---|---|---|---|
| `normalize` | Done | flat/vendor/raw payload | `AlertInput` + `NormalizationReport` | 只做确定性归一化；不判定攻击方向、受害方、处置目标 |
| `entity_extract` | Done | `AlertInput` | `ExtractedEntities` + `ExtractionReport` | 从 canonical 字段提取实体 mention；不直接读取厂商字段 |
| `fact_reconstruct` | Done | `AlertInput` | `FactReconstructionResult` | 根据证据策略生成字段可信度、角色候选、冲突报告；不直接下 verdict |
| `build_analysis_input` | Done | `AlertInput` + `ExtractedEntities` + `FactReconstructionResult` | `LLMAnalysisRequest` | 收敛成 stub/LLM 唯一可消费的有界分析上下文 |
| `analyze_stub` | Done | `LLMAnalysisRequest` | `AnalysisResult` | Phase 1 deterministic stub；后续可替换为真实 LLM analyzer |
| `schema_validate` | Done | `AnalysisResult` | validated `AnalysisResult` | 校验 LLM/stub 输出结构，防止自然语言直接进入决策层 |
| `decide` | Done | validated `AnalysisResult` | `Decision` | 只给建议和 review 分流；Phase 1 固定 `automation_allowed=False` |

ZEUS/天眼输入可信度相关结构状态：

| Contract | 状态 | 目的 |
|---|---|---|
| `EvidenceLayer` | Done | 区分 `raw_message`、`raw_structured`、`processed_field`、`agent_inference`、`human_confirmed` |
| `EvidenceInputPolicy` | Done | 指定事实重建和后续分析优先看哪份输入；平安优先 `zeusRawLogs[].message`，缺失时 fallback 到 `zeusRawLogs[]` 并降级可信度 |
| `FieldTrust` | Done | 标注字段来源、可信度、是否参与事实重建 |
| `RoleAssignment` | Done | 记录 source、destination、attacker、victim、impacted_asset、response_target 等候选角色 |
| `ConflictReport` | Done | 显式记录 attacker/source、victim/destination、同角色多候选等冲突 |
| `FactReconstructionResult` | Done | LLM 分析前的事实层，保存 evidence policy、field trust、role assignment、conflict report 和 warning |
| `LLMAnalysisRequest` | Done | stub/LLM analyzer 的唯一输入 contract，避免模型直接吃 raw payload 或未筛选加工字段 |

这条链路的核心原则：

- `normalize` 解决格式统一，不解决事实可信度。
- `EvidenceInputPolicy` 解决主证据选择，不直接输出结论。
- `fact_reconstruct` 解决字段可信度、角色候选和冲突显式化，不直接判定真假。
- `LLMAnalysisRequest` 解决 LLM 上下文边界，不允许后续模型绕过它直接读取完整 raw payload。
- 后续 `PromptBuilder` 必须只从 `LLMAnalysisRequest` 生成 prompt/messages。

#### Phase 1 当前下一步固定顺序

截至 2026-07-01，`ReviewQueue` 的基础 service 和 `InvestigationContext` 已经具备雏形；继续优先做 UI 的产品验证价值有限，因为队列里仍主要展示 deterministic stub 结论。当前阶段应先把 LLM 分析节点做成可控、可测试、可回放的工程节点，再放大到复核界面或 Kafka 后台流。

固定顺序：

1. **Prompt Builder + SOC analysis prompt golden tests**
   - `PromptBuilder` 只能消费 `LLMAnalysisRequest`。
   - 不允许直接把完整 `AlertInput.raw` 或 vendor payload 无脑塞进 prompt。
   - Prompt 必须显式呈现主证据来源、字段可信度、角色候选、冲突报告、warnings 和 JSON 输出 schema。
   - Golden tests 至少覆盖 PingAn APT/EDR、raw message 缺失 fallback、字段冲突场景。
   - 当前状态：Done，已落地 `backend/soc_agent/prompts/analysis.py` 和 `backend/tests/test_soc_agent_prompts.py`。
2. **LLM JSON output parser + schema validation + bad JSON repair golden sample**
   - 先严格 JSON parse，再 repair，再 Pydantic / domain validation。
   - repair 仍失败时必须进入明确失败态或 `needs_review`，不能假装成功。
   - 当前状态：Done，已落地 `backend/soc_agent/llm/json_parser.py` 和 `backend/tests/test_soc_agent_llm_json_parser.py`。
3. **真实 LLM analyzer behind flag**
   - 默认继续使用 deterministic stub。
   - 只有显式配置开启时才调用真实模型。
   - 必须记录 `prompt_version`、`prompt_hash`、`parser_version`、`model_name` 和必要 token/cost 信息。
   - 当前状态：Done，已落地 `backend/soc_agent/llm/analyzer.py`、`AnalysisNodeOutput`、runtime analyzer 注入和 fake client 测试；默认 runtime 仍走 stub。
4. **Offline eval：stub / llm / replay diff**
   - 同一批样本比较 verdict、confidence、needs_review、parse success、冲突字段处理质量。
   - 评估结果决定真实 LLM 是否进入默认链路。
   - 当前状态：Done，已落地 `backend/soc_agent/eval/offline.py`、`soc eval offline`、JSONL replay response 和差异报告。
5. **ReviewQueue UI 或 Kafka daemon**
   - UI 依赖稳定可解释的研判结果。
   - Kafka daemon 依赖稳定的 parse/repair/fallback/rate-limit 策略。
   - 当前状态：In progress，已选择先服务分析师复核闭环；ReviewQueue API MVP 已落地，下一步做 TUI thin client 或 Web thin page。
6. **ReviewQueue API / TUI / Web**
   - API 是 Web/TUI/外部系统统一入口，必须只调用 `SocReviewService`。
   - TUI 是 Phase 1/2 更合适的薄操作台，用于 open queue、context、close、correct、trace 调试。
   - Web UI 后续基于同一套 API 增量做列表和详情页，不复制业务逻辑。
   - 当前状态：API Done，TUI Done，SOC Agent chat stream contract Done，TUI chat runtime adapter Done，`soc chat tui` workbench shell Done；下一步做 ReviewQueue Web thin page 或 capability router。

7. **SOC Agent chat stream contract**
   - `SocAgentChatService.stream()` 是后续 SOC Lead Agent TUI/Web/Channels 的统一流式入口。
   - 事件类型与 DeerFlow TUI 兼容：`values`、`messages-tuple`、`custom`、`end`。
   - `send_message()` 只 materialize 同一条 stream，供 headless/API 使用，不定义第二套 chat 协议。
   - 当前 Phase 1 实现只做 deterministic shell 和 ReviewQueue context loading；不调用 LLM、不执行处置、不绕过 core service。
   - 当前已落地 `soc_agent.tui.chat_runtime`，把 `SocAgentStreamEvent` 转成 DeerFlow TUI reducer actions；`soc.review_context` custom event 转为 TUI `SystemMessage`。
   - 当前已落地 `soc chat tui`，复用 DeerFlow Textual/ComposerInput/ViewState/reducer/render 语义，支持普通消息和 `/open REV-...` 加载 review context。

ReviewQueue TUI 实现边界：

- `soc review tui` 是 DeerFlow-aligned thin client。
- 它复用 DeerFlow 的 Textual/THEME/ComposerInput/slash command 体验，但业务动作只走 `SocReviewService`。
- 它不是 SOC Lead Agent 主聊天入口；后续主 Agent TUI/chat 才复用 DeerFlow 的 stream/messages/artifacts/clarification。
- ReviewQueue TUI 不把 verdict/evidence 等业务结构塞进 `ThreadState.artifacts`；artifacts 只用于后续报告、IOC 文件、攻击链图等生成产物。

### 1.2.2 生产级 Runtime 原则：硬骨架 + 软路由

SOC Agent 的工程定位不是“让 LLM 自主完成一切”，而是：

> 确定性 SOC Runtime 掌握控制流，LLM 处理局部不确定判断，人类接管高风险动作。

主流程必须由系统掌握，不能让 LLM 自由决定是否跳过必要检查、是否结束任务、是否执行动作。LLM 的主动性只放在受限扩展点里，且每次选择必须可验证、可审计、可回放。

```text
Deterministic Backbone
  normalize
  -> entity_extract
  -> fact_reconstruct
  -> build_analysis_input
  -> llm_analyze
  -> schema_validate
  -> domain_validate
  -> decide
  -> audit
  -> review/replay

LLM Advisory Router（受限软路由）
  - 从候选历史里选择最相关证据
  - 判断是否需要补充查询
  - 生成需要问人的澄清问题
  - 提出新知识候选
  - 总结证据和不确定性
```

控制权边界：

| 层 | 谁掌握 | 允许做什么 | 不允许做什么 |
|---|---|---|---|
| 主流程 | Runtime 代码 | 固定步骤、状态流转、失败处理、审计写入 | 让 LLM 自由决定跳步、结束或重试 |
| 模糊判断 | LLM 节点 | 在给定输入和候选集合内推理、排序、解释、提出建议 | 直接执行工具、修改数据库、改变权限策略 |
| 动作执行 | Policy + Tool Registry | 按权限等级执行 L0-L2 低风险动作，记录审计 | 绕过审批执行 L3+ 或破坏性动作 |
| 高风险决策 | 分析师/负责人 | 审批、驳回、纠正、升级规则 | 交给模型自动确认 |

Phase 节奏：

- Phase 1：流程定死，当前先用 deterministic `analyze_stub`；真实 LLM 接入前必须先经过 `LLMAnalysisRequest` 和后续 PromptBuilder。
- Phase 2：在固定主流程中补 dedup、lesson_match、history_query、correlation 等节点；LLM 仍只处理受控分析或候选排序。
- Phase 3：引入受限 `LLM Advisory Router`，只允许从白名单 next steps 中选择，例如 `ask_human`、`fetch_more_history`、`mark_needs_review`。
- Phase 4+：软路由必须具备 step trace、成本估算、replay diff 和评测指标后，才允许进入 daemon。

### 1.2.3 Fork 维护原则：上游最小侵入 + SOC 增量扩展

当前仓库是基于 DeerFlow 上游开源项目 fork 后的二次开发，因此 SOC Agent 的工程边界必须优先考虑长期同步 upstream 的成本。默认原则是：

> DeerFlow 保持为通用 Agent 框架底座，SOC Agent 作为业务扩展层增量接入；除非确实需要修复框架缺陷或补通用扩展点，否则不改动上游原有核心代码。

落地约束：

| 类型 | 默认做法 | 说明 |
|---|---|---|
| SOC 业务逻辑 | 新增独立模块/包/目录 | pipeline、memory、db、policy、daemon、CLI 等放在 SOC 自己的边界内 |
| 上游通用能力 | 优先 import / adapter / wrapper | 复用模型工厂、配置、LangGraph、middleware 思路，不直接改原实现 |
| 必须改上游代码 | 只做小型、通用、可解释的扩展点 | 例如增加 hook、interface、配置项；避免写入 SOC 专有分支逻辑 |
| API/Schema | 新增 SOC namespace | 避免污染 DeerFlow 原有 chat/thread/gateway 协议 |
| 数据库 | SOC 独立 PostgreSQL schema / migrations | 不复用或改造 DeerFlow 原 persistence 表承载 SOC 告警 |
| 前端 | 后期新增 SOC 专用页面/路由 | 不把 SOC 告警队列硬塞进通用聊天 UI 主流程 |
| 上游同步 | 保持 rebase/merge 可控 | 每次改动应能解释“这是 SOC 增量”还是“这是通用框架修复” |

判断标准：

- 如果需求只服务 SOC 预警研判，优先放到 SOC 模块。
- 如果能力对 DeerFlow 通用 Agent 也有价值，才考虑抽成上游风格的通用扩展点。
- 如果需要改已有 DeerFlow 文件，先确认是否可以通过配置、adapter、hook、router 注册或新增入口绕开。
- 如果必须改原文件，改动应小而稳定，并在 PR/提交说明里标明原因，降低未来同步 upstream 的冲突概率。

### 1.3 设计决策与依据

| 决策 | 选择 | 依据 |
|---|---|---|
| Fork 维护 | **上游最小侵入 + SOC 增量扩展** | 当前仓库是 DeerFlow fork；SOC Agent 应作为业务层接入，尽量不改上游核心，降低后续同步 upstream 的冲突成本 |
| Agent 架构 | **Main Agent (持久) + Sub Agent (per-alert)** | 主Agent维护短期上下文（滑动窗口+倒排索引）实现免LLM去重；子Agent独立流水线互不干扰。参考 Claude Code 的 Leader + Teammate 模式（`QueryEngine.ts:184-207`） |
| 控制流 | **Runtime 固定主流程，LLM 受限软路由** | 生产系统需要可执行、可验证、可恢复、可追踪；LLM 只处理候选选择、解释归因、澄清建议等模糊环节 |
| 去重策略 | 主Agent倒排索引 + 基于证据重叠度判定 | 同 `detection_key` + 同 `process_name` + 同 `source_ip` 在短窗口内重复 → 直接 merge，不调 LLM。参考 Claude Code `bashPermissions.ts:1483-1587` 的 speculative classifier |
| 子Agent生命周期 | 独立 AbortController + 超时 kill | 单个子Agent不响应 → kill，其他子Agent不受影响。参考 Claude Code `Task.ts:6-76` |
| 知识存储 | PostgreSQL `soc_facts` + 可选 JSON 快照 | 业务记忆会影响安全判断，必须可审计、可确认/驳回、可回滚；复用 DeerFlow LLM 抽取和合并思路 |
| 预警摘要 | PostgreSQL | 需要按实体/时间窗口查询，文件做不到；生产环境同构 |
| 经验规则 | PostgreSQL | 需要按 pattern 高效匹配；后续可加 trigram/JSONB 索引 |
| 反馈方式 | 追问对话(CLI) + 批量复查(daemon) | CLI 可交互；daemon 只能事后复查 |
| soul 维护 | 部署时写一次 | 运营分析师不会主动维护，后续学习走 memory |
| memory 粒度 | 模式级，不存具体 IP/用户 | 具体实体的靠 alert_summaries 关联查询覆盖 |
| 主Agent重启恢复 | pattern_index 从 alert_summaries 重建最近 N 条 | daemon 重启后滑动窗口短期失忆，但索引可重建 |

### 1.4 和 DeerFlow 的架构映射

| DeerFlow | SOC Agent |
|---|---|
| SOUL.md | soc_soul.md（分析原则+环境知识） |
| memory.json | `soc_facts` 表 + `soc_memory_snapshot.json`（可选导出） |
| `_apply_updates()` | 合并新旧 facts 的策略复用，但落库到 PostgreSQL |
| `format_memory_for_injection()` | 注入 prompt（同逻辑） |
| `detect_correction()` | 纠正对话 → confidence 覆盖 |
| middleware 链 | 处理流水线（7 步） |
| config.yaml | config.yaml（模型/阈值/API） |

### 1.5 核心设计：Main Agent + Sub Agent 编排

> 参考 Claude Code 的 Leader + Teammate 模式（`QueryEngine.ts:184-207`、`Task.ts:6-76`）。

#### 为什么需要这个层次

v3 的单引擎方案有一个瓶颈：每条预警独立走完整流水线，即使与前一条几乎完全相同。批量扫描触发 20 条同类预警时，会重复调用 LLM 20 次。

Main Agent + Sub Agent 的核心价值：
1. **去重免算**：主 Agent 的倒排索引在 LLM 调用前拦截重复预警
2. **短期上下文**：主 Agent 维护最近 N 条预警的趋势，子 Agent 不只是看历史 DB，还能看到「刚才发生了什么」
3. **并行安全**：子 Agent 独立运行，超时 kill 不影响其他

#### Main Agent 维护的状态

```python
@dataclass
class MainAgentContext:
    # === 静资产（从存储加载） ===
    soul: str                              # soc_soul.md 全文
    
    # === 准静资产（从存储加载，按需更新） ===
    memory_facts: list[Fact]               # soc_facts 中 status=confirmed 的 facts
    
    # === 动资产（内存维护，重启时从 DB 重建） ===
    recent_window: deque[AlertDigest]      # 最近 200 条预警摘要（环形缓冲区）
    pattern_index: dict[PatternKey, list[str]]  # (detection_key, process_name, source_ip) → alert_ids
    active_subs: dict[str, SubAgentHandle] # 当前正在运行的子 Agent
    
    # === 实时上下文（不持久化，重启清零） ===
    recent_trends: dict[str, TrendSignal]  # 最近 5 分钟的趋势信号
                                           # 例如: "10.0.3.5 在 3 分钟内触发了 5 条不同规则"
```

#### 去重判定逻辑（主 Agent，纯代码，不调 LLM）

```python
class MainAgent:
    def check_duplicate(self, alert: AlertInput) -> AlertDigest | None:
        """查 pattern_index：同 detection_key + 同 process + 同 source_ip 的近 N 条"""
        key = PatternKey(
            detection_key=alert.detection.detection_key,
            process_name=alert.entities.process.process_name,
            source_ip=alert.entities.network.source_ip,
        )
        candidates = self.ctx.pattern_index.get(key, [])
        
        for alert_id in reversed(candidates[-5:]):  # 只看最近 5 条
            digest = self.ctx.recent_window.get(alert_id)
            if digest is None:
                continue
            # 证据重叠度 > 90% → 判定为重复
            if digest.evidence_overlap(alert) > 0.9:
                return digest
        return None

    async def on_alert(self, alert: dict) -> AlertResult:
        # 步骤 0（新增）：去重判定
        match = self.check_duplicate(alert)
        if match is not None:
            if match.confidence > 0.9:
                # 完全同类型 → 跳过所有 LLM 调用
                return AlertResult(
                    alert_id=alert["id"],
                    verdict=match.verdict,
                    confidence=match.confidence,
                    merged_from=match.alert_id,
                    skipped_llm=True
                )
            else:
                # 可能相关但不确定 → 标记 context_ref 供子 Agent 参考
                alert["_context_ref"] = match.alert_id
        
        # 步骤 1: 派发子 Agent
        sub = SubAgent(
            alert=alert,
            context_ref=alert.get("_context_ref"),
            main_ctx=self.ctx
        )
        self.ctx.active_subs[alert["id"]] = sub
        
        # 步骤 2: 异步执行（带超时）
        try:
            result = await asyncio.wait_for(sub.run(), timeout=120)
        except asyncio.TimeoutError:
            result = AlertResult(
                alert_id=alert["id"],
                verdict="uncertain",
                confidence=0.3,
                summary="子Agent超时"
            )
            sub.kill()
        
        # 步骤 3: 结果回写主 Agent 上下文
        if result.context_modifier:
            self.ctx = result.context_modifier(self.ctx)
        
        # 步骤 4: 更新滑动窗口
        self.ctx.recent_window.append(result.to_digest())
        self.ctx.pattern_index[key].append(alert["id"])
        del self.ctx.active_subs[alert["id"]]
        
        return result
```

#### 子 Agent 启动时注入上下文

```python
class SubAgent:
    def __init__(self, alert, context_ref, main_ctx):
        self.alert = alert
        self.context_ref = context_ref  # ← 如果命中相似预警，带上
        self.main_ctx = main_ctx        # 共享 soul + memory + 实时趋势
        self.abort = asyncio.Event()
    
    async def run(self) -> AlertResult:
        # 构建 prompt 时注入主 Agent 的实时上下文
        context = {
            "alert": self.alert,
            "context_ref": self._get_context_ref(),  # 相关历史的完整摘要
            "recent_trends": self.main_ctx.recent_trends,  # 最近 5 分钟趋势
            "soul": self.main_ctx.soul,
            "memory_facts": self.main_ctx.memory_facts,
        }
        
        # 走 7 步流水线...
        result = await self.pipeline(context)
        return result
    
    def _get_context_ref(self) -> dict | None:
        if not self.context_ref:
            return None
        prior = self.main_ctx.recent_window.get(self.context_ref)
        if prior:
            return {
                "alert_id": prior.alert_id,
                "verdict": prior.verdict,
                "summary": prior.summary,
                "relation_hint": f"同类预警 {prior.alert_id} 已判定为 {prior.verdict}"
            }
        return None
    
    def kill(self):
        self.abort.set()
```

#### 效率提升预估

| 场景 | v3（单引擎） | v4（主+子） |
|---|---|---|
| 扫描器 5 秒触发 20 条同类预警 | 20 次 LLM 调用 | 第 1 次走 Sub Agent，后 19 次主 Agent 直接 merge（**0 次 LLM**） |
| 同 IP 同进程再次触发（距上次 30s） | 重新研判 | 索引命中 → 复用结论 |
| 同 IP 不同进程（距上次 30s） | 无所知 | 子 Agent 拿到「此 IP 刚触发过其他规则」的实时上下文 |

#### 重启恢复策略

```python
def rebuild_main_agent_from_db() -> MainAgentContext:
    """daemon 重启时从 alert_summaries 重建滑动窗口"""
    ctx = MainAgentContext()
    recent = db.query("""
        SELECT alert_id, detection_key, process_name, source_ip, verdict,
               confidence, summary, entities, alert_time
        FROM alert_summaries
        ORDER BY analyzed_at DESC
        LIMIT 200
    """)
    for row in recent:
        digest = AlertDigest.from_row(row)
        ctx.recent_window.append(digest)
        key = PatternKey(row["detection_key"], row["process_name"], row["source_ip"])
        ctx.pattern_index[key].append(row["alert_id"])
    return ctx
```

重启后滑动窗口短期可用（最多丢失重启间隔内的预警上下文，但 index 和 recent_window 都能从 DB 重建）。

### 1.6 MVP 成功指标

Phase 1 不以“自动化率”作为核心目标，而以“单条研判可信、可纠正、可审计”为目标。先记录 baseline，再逐步优化。

| 指标 | Phase 1 目标 | 为什么重要 |
|---|---|---|
| 单条样例告警分析成功率 | > 90% | 证明 pipeline 能稳定跑完，不被字段缺失、模型输出、DB 写入打断 |
| LLM JSON 可解析率 | > 95%（含 repair 后） | 结构化输出是落库、审计和后续自动化的基础 |
| 每条结论 evidence 完整率 | > 90% | 分析师必须能理解为什么这么判 |
| 审计可追踪率 | 100% | 每个 verdict 都能回看输入、注入知识、模型输出和最终决策 |
| CLI 端到端耗时 | 记录 p50/p95，先不设硬目标 | Phase 1 先要可信；性能用于后续优化 |
| 分析师纠正率 / override rate | 记录 baseline | 后续判断模型、规则和知识质量 |
| `soc_facts` 候选生成率 | 纠正后能稳定生成候选知识 | 验证反馈闭环是否真的沉淀经验 |

Phase 2 以后再关注重复告警 merge 率、LLM 调用节省率、confirmed facts 命中次数；Phase 4 以后再关注自动关闭准确率和 review_queue 采纳率。

### 1.7 Phase 1 Non-goals

Phase 1 的边界必须明确，避免把 MVP 做成不可验收的平台项目。

- 不接生产 Kafka，只处理本地样例、手工 JSON 和可控测试数据。
- 不做 Web UI，只做可脚本化 CLI。
- 不做自动封禁、隔离主机、禁用账号等破坏性处置。
- 不做 7×24 daemon，不承诺生产吞吐。
- 不做 Knowledge RAG，不引入 PageIndex / GraphRAG。
- 不让 LLM 自发现知识直接影响后续判断；自发现知识只能进入 `pending_review`。
- 不做复杂多租户、权限系统、团队管理。
- 不把单次分析师纠正直接升级为高风险自动处置规则。

---

## 二、处理流水线（7 步详解）

每条预警进入后，依次走过 7 个步骤。大部分步骤是纯代码，只在④⑤两步调用 LLM。

### 2.0 LLM 责任边界

LLM 不是流程控制器，而是流水线中的可替换推理节点。每个节点必须声明输入、输出 schema、失败策略和是否允许软路由。

| 步骤 | 类型 | 是否允许 LLM 主动路由 | 原则 |
|---|---|---|---|
| ① 实体提取 | 代码优先 | 否 | Phase 1 用规则/解析器；复杂日志后续可加 LLM extractor，但输出仍进 schema 校验 |
| ② 经验快查 | 代码/DB | 否 | 命中 confirmed lesson 才能短路；LLM 不能自行创建可执行 lesson |
| ③ 关联查询 | 代码/DB | 否 | 系统按实体和时间窗口查候选，LLM 不能决定“不查历史” |
| ④ 漏斗关联 | LLM 节点 | 是，受限 | LLM 只在候选集合内挑相关历史、解释相关性、提出是否需要补充信息 |
| ⑤ 综合分析 | LLM 节点 | 是，受限 | 输出 verdict/confidence/evidence/recommended_action/knowledge_candidates，不能直接执行动作 |
| ⑥ 置信度决策 | 代码/Policy | 否 | Runtime 根据阈值、权限、证据、黑名单和 review 规则做最终分流 |
| ⑦ 写入存储 | 代码/DB | 否 | 所有写入必须带来源、状态、审计信息；LLM 只能提供候选内容 |

受限软路由的 next steps 必须来自白名单，例如：

```text
ask_human
fetch_more_history
fetch_process_tree
mark_needs_review
propose_knowledge_candidate
```

每个 soft route 输出必须包含：

```json
{
  "next_step": "fetch_more_history",
  "reason": "同一 src_ip 在 10 分钟内触发多条不同规则，需要确认是否同一攻击链",
  "expected_information_gain": "high",
  "risk_level": "L0",
  "cost_estimate": "low"
}
```

Runtime 负责决定是否采纳该建议。未通过白名单、权限、成本或状态校验的 soft route，一律转为 `needs_review` 或忽略并记录审计。

### 2.1 流程总图

```mermaid
flowchart TD
    START["新预警进入"] --> STEP1["① 实体提取<br/>纯代码·不调LLM"]

    STEP1 --> STEP2{"② 经验快查<br/>查 lessons_learned"}
    STEP2 -->|"命中 auto_close"| SKIP["直接返回 FP·结束<br/>不调 LLM"]
    STEP2 -->|"命中 lower_priority"| LOWER["降优先级·继续"]
    STEP2 -->|"未命中"| STEP3

    LOWER --> STEP3["③ 关联查询<br/>查 alert_summaries<br/>按实体 OR 匹配·top30<br/>纯代码·不调LLM"]

    STEP3 -->|"无历史"| STEP5["⑤ LLM 综合分析"]
    STEP3 -->|"有历史（≤30条摘要）"| STEP4A["④ Phase 1 粗筛<br/>摘要注入 LLM → 挑 3-5 条 ID<br/>LLM 调用 #1"]

    STEP4A --> STEP4B["④ Phase 2 精筛<br/>拉完整日志 → LLM 精确对比<br/>LLM 调用 #2"]
    STEP4B --> STEP5

    STEP5 --> STEP6{"⑥ 置信度决策<br/>纯代码"}
    STEP6 -->|"≥ 0.85 FP/benign"| CLOSE["auto_close"]
    STEP6 -->|"≥ 0.85 TP"| INCIDENT["auto_incident"]
    STEP6 -->|"0.5~0.85"| ESCALATE["escalate·附分析"]
    STEP6 -->|"< 0.5"| HUMAN["必须人工"]

    CLOSE --> STEP7["⑦ 写入存储"]
    INCIDENT --> STEP7
    ESCALATE --> STEP7
    HUMAN --> STEP7
    STEP7 --> END["结束"]
```

### 2.2 ① 实体提取（纯代码）

从预警原始数据中提取结构化实体，供后续关联查询使用。不调 LLM。

**提取维度**：

| 实体类型 | 来源 | 过滤规则 |
|---|---|---|
| IP 地址 | `AlertInput.entities.network` + process/http 文本正则补充 | 排除 127.x / 0.x / 169.254.x / 224.x |
| 域名 | `entities.network.domain` / `entities.http.host` + 正则补充 | 排除无点号的单段主机名 |
| 进程名 | `entities.process.process_name` / `parent_process_name` | 可按 basename 归一 |
| 文件哈希 | 正则（32-64 位十六进制） | 排除 < 32 位 |
| 注册表路径 | 正则（HK 开头路径） | — |
| URL | `entities.network.url` / `entities.http.url` + 正则补充 | — |
| 用户名 | `entities.user` | — |
| 检测规则 | `alert.detection.rule_code/rule_name/detection_key` | `rule_code` 可选，`detection_key` 必有 |

**提取优先级**：canonical `AlertInput` 结构化字段优先，正则补充。EDR、NIDS、F5/WAF、HIDS 等外部平铺/厂商字段必须先经过 `normalizers/` 转换，不直接进入 runtime。

```python
def extract_entities(alert: AlertInput) -> AlertEntities:
    e = AlertEntities()

    # 结构化字段优先
    for f in [
        alert.entities.network.source_ip,
        alert.entities.network.destination_ip,
    ]:
        if f and is_real_ip(f):
            e.ips.append(f)

    proc = alert.entities.process.process_name or ""
    if proc:
        e.exe_names.append(PureWindowsPath(proc).name.lower())

    # 正则补充（从 canonical 文本字段和 raw 中补）
    blob = " ".join([
        alert.entities.process.command_line or "",
        json.dumps(alert.raw, ensure_ascii=False),
    ])
    e.ips.extend(ip for ip in re.findall(r'\b(?:\d{1,3}\.){3}\d{1,3}\b', blob)
                  if is_real_ip(ip) and ip not in e.ips)
    e.file_hashes.extend(re.findall(r'\b[A-Fa-f0-9]{32,64}\b', blob))

    return e

def is_real_ip(ip: str) -> bool:
    return not ip.startswith(("127.", "0.", "169.254.", "224."))
```

### 2.3 ② 经验快查（纯代码）

查询 lessons_learned 表，看是否命中已知 pattern。命中则跳过后续所有 LLM 调用。

**匹配逻辑**：

```python
def check_lessons(alert: AlertInput, entities: AlertEntities) -> LessonMatch | None:
    conditions = []

    # 构建 pattern 片段
    for exe in entities.exe_names:
        conditions.append(("process", exe))
    if alert.detection.detection_key:
        conditions.append(("detection_key", alert.detection.detection_key))
    if alert.detection.rule_code:
        conditions.append(("rule_code", alert.detection.rule_code))
    for ip in entities.ips:
        conditions.append(("ip", ip))

    for key, value in conditions:
        rows = db.query(
            "SELECT * FROM lessons_learned WHERE pattern LIKE ?",
            [f"%{key}:{value}%"]
        )
        if rows:
            return LessonMatch(row=rows[0], matched_on=f"{key}:{value}")

    return None
```

**三种命中结果**：

| 命中 action | 后续动作 | LLM 调用 |
|---|---|---|
| `auto_close` | 直接返回 FP，结束 | 0 次 |
| `lower_priority` | 降低优先级，继续走流水线 | 正常调用 |
| `flag` | 打标记，继续走流水线 | 正常调用 |

### 2.4 ③ 关联查询（纯代码）

查询 alert_summaries 表，按实体 OR 匹配，取 top 30 条历史摘要。宁可多召回不漏。

**查询 SQL**：

```sql
SELECT alert_id, alert_time, detection_key, process_name, source_ip, destination_ip,
       verdict, confidence, summary, alert_type
FROM alert_summaries
WHERE (detection_key = ?)
   OR (rule_code = ?)
   OR (process_name = ?)
   OR (source_ip = ? OR destination_ip = ?)
   OR (domain = ?)
   OR (user_name = ?)
ORDER BY alert_time DESC
LIMIT 30
```

**参数按实体填充**：每个维度独立匹配，OR 取并集。交给 Phase 1 LLM 去筛。

**返回结果**：
- 0 条 → 无历史，跳到⑤直接分析
- 1~30 条 → 进入④漏斗关联

### 2.5 ④ 漏斗关联（两轮 LLM）

将关联查询拿到的摘要经过两轮筛选，从 30 条缩到 3-5 条完整日志，用于精确对比。

#### Phase 1：粗筛（30 条摘要 → 挑 3-5 条 ID）

```python
PHASE1_PROMPT = """<alert_correlation_phase1>
当前新预警: {current_alert}

以下是可能有关系的历史预警摘要，请挑出最相关的 3-5 条（返回 ID 列表）。
判断标准：同类行为、同程序、同攻击模式、同目标。不相关的不要选。

{formatted_summaries}
</alert_correlation_phase1>

只返回 JSON: {"related_ids": ["ALT-001", ...], "reason": "一句话关联理由"}"""
```

**输入**：30 条摘要，每条约 50 tokens → 共 1.5K tokens
**输出**：3-5 个 alert_id
**目的**：只挑出最相关的，避免拉无关的完整日志浪费 token

#### Phase 2：精筛（拉完整日志 → 精确对比）

```python
PHASE2_PROMPT = """<alert_correlation_phase2>
当前新预警完整日志:
{current_alert_full}

以下是最相关的历史预警完整日志:
{full_logs}

请精确对比：
1. 当前预警是否与某条历史预警属于同一类事件？
2. 如果是，引用历史调查结论
3. 如果有新发现或差异，指出
</alert_correlation_phase2>"""
```

**输入**：3-5 条完整日志，每条约 500 tokens → 共 1.5-2.5K tokens
**输出**：关联分析结论（结构化文本）
**目的**：用完整日志做精确判断，不是只看摘要

#### Token 消耗分析

| 方案 | 总 Token | 准确率 |
|---|---|---|
| 暴力全塞原始日志（30 条） | 20K+ | 高但贵 |
| 只用摘要，不拉完整日志 | 1.5K | 快但缺精确对比 |
| **漏斗式（本方案）** | **~6K** | **高且省** |

### 2.6 ⑤ LLM 综合分析

这是核心步骤。将所有上下文组装后交给 LLM，产出最终的判断和摘要。

#### 输入组装

参考 Claude Code 的 prompt 组装方式（`QueryEngine.ts:316-325`、`api.ts:119-266`），将 prompt 拆分为可缓存和不可缓存两部分：

```
LLM 收到的输入：
├── [cacheable] System Prompt（含分析原则 + 输出格式要求）
├── [cacheable] soc_soul.md（全文注入，几乎不变 → 可缓存）
├── [volatile]  soc_facts（status=confirmed 的 facts，按实体相关性筛选）
├── [volatile]  主Agent实时上下文（近5分钟趋势·context_ref）
├── [volatile]  关联结果（④的 Phase 2 产出，或空）
└── [volatile]  当前预警完整数据
```

> 使用 Anthropic API 的 `cache_control: {type: "ephemeral"}` 标记 cacheable block。soul 不变时每次省 ~2K tokens。参考 Claude Code `api.ts:1373-1379`。

#### System Prompt

```
你是一个安全分析师 AI 助手，负责分析安全预警。

<soul>
{soc_soul.md 全文}
</soul>

<memory>
{soc_facts 中 status=confirmed 的 facts}
</memory>

## 判定标准

- true_positive：确认的恶意行为，有明确恶意证据
- false_positive：已知合法工具/行为被误拦，能解释为什么是误报
- benign_positive：行为确实发生了，但属于正常业务

## 置信度
- ≥ 0.85: 高置信，可自动化处理
- 0.5-0.85: 中置信，建议人工复核
- < 0.5: 低置信，必须人工

## 关联分析规则
- 历史有多条同类型 FP → 当前大概率也是 FP（但要检查差异）
- 同 IP/域名在多台机器出现 → 提高警惕
- 首次出现的模式 → 默认中低置信

## 输出格式（严格 JSON）
{
    "verdict": "true_positive | false_positive | benign_positive",
    "confidence": 0.0-1.0,
    "summary": "一句话总结（≤80字，含关键程序/IP/行为/结论）",
    "investigation": "详细分析（3-5句）",
    "alert_type": "malware | lateral_movement | exfiltration | scanning | credential_access | persistence | other",
    "severity": "critical | high | medium | low | info",
    "recommendation": "建议动作",
    "iocs": ["提取的 IOC 列表"],
    "new_knowledge": null | { "content": "...", "category": "..." }
}
```

#### 输出处理

```python
async def analyze(alert, soul, memory_facts, correlation_result):
    result = await llm.call(system=SYSTEM_PROMPT, user=build_user_prompt(...))

    # 如果 LLM 主动发现了新知识 → 写入 memory（pending 状态）
    if result.get("new_knowledge"):
        add_fact(
            content=result["new_knowledge"]["content"],
            category=result["new_knowledge"]["category"],
            confidence=0.55,
            source="llm_discovery",
            status="pending_review"
        )

    return result
```

### 2.7 ⑥ 置信度决策（纯代码）

参考 Claude Code 的多级权限决策管道（`types/permissions.ts:24-44`、`useCanUseTool.tsx:28-191`），决策不只是看 confidence 阈值，而是多层漏斗：

```
经验规则(lessons_learned) → 运行模式(mode) → 分类器(相似度) → 置信度阈值 → 人工确认
         ↓                       ↓               ↓               ↓            ↓
    auto_close / flag        daemon=less    similarity>0.9    ≥0.85 auto    <0.5 human
                            aggressive     直接复用
```

决策实现：

```python
def decide(analysis: dict, mode: str, 
           lesson_match: LessonMatch | None = None,
           similarity_score: float = 0.0) -> Decision:
    """
    五层决策链：
    L1: 经验规则（② 已检查）
    L2: 运行模式
    L3: 分类器相似度（新增）
    L4: LLM 置信度阈值
    L5: 人工确认
    """
    c = analysis["confidence"]
    v = analysis["verdict"]
    
    # L2: 运行模式覆盖
    if mode == "daemon" and c < 0.85:
        return Decision(action="queue_for_review", needs_human=True)
    
    # L3: 分类器相似度预判（新增）—— 历史有高相似的同类型，直接复用
    if similarity_score > 0.9 and lesson_match is None:
        return Decision(action="inherit_prior", needs_human=False)
    
    # L4: LLM 置信度阈值
    if c >= 0.85:
        if v in ("false_positive", "benign_positive"):
            return Decision(action="auto_close", needs_human=False)
        else:
            return Decision(action="auto_incident", needs_human=False)
    elif c >= 0.5:
        return Decision(action="escalate", needs_human=True)
    else:
        # L5: 必须人工
        return Decision(action="human_required", needs_human=True)
```

### 2.8 ⑦ 写入存储

分析完成后，将结果写入各存储。

| 写入目标 | 写什么 | 条件 |
|---|---|---|
| `alert_summaries` | 实体 + verdict + summary + alert_type | 每条预警都写 |
| `review_queue` | alert_id + AI 判断 + 详细分析 | 仅 daemon 模式 + 低置信 |
| `soc_facts` | new_knowledge（pending 状态） | 仅 LLM 主动发现时 |

```python
def write_results(alert: AlertInput, entities, analysis, decision):
    db.insert("alert_summaries", {
        "alert_id": alert.alert_id,
        "detection_key": alert.detection.detection_key,
        "rule_code": alert.detection.rule_code,
        "rule_name": alert.detection.rule_name,
        "source_type": alert.source.source_type,
        "source_system": alert.source.source_system,
        "alert_time": alert.event.event_time,
        "process_name": alert.entities.process.process_name,
        "source_ip": alert.entities.network.source_ip,
        "destination_ip": alert.entities.network.destination_ip,
        "domain": next(iter(entities.domains), ""),
        "user_name": alert.get("user_name", ""),
        "entities": json.dumps(entities.to_dict()),
        "verdict": analysis["verdict"],
        "confidence": analysis["confidence"],
        "summary": analysis["summary"],
        "alert_type": analysis["alert_type"],
        "severity": analysis["severity"],
        "mode": decision.mode,
    })

    if decision.action == "queue_for_review":
        db.insert("review_queue", {
            "alert_id": alert["id"],
            "ai_verdict": analysis["verdict"],
            "ai_confidence": analysis["confidence"],
            "ai_summary": analysis["summary"],
            "ai_investigation": analysis["investigation"],
        })
```

### 2.9 LLM 调用统计

| 场景 | LLM 调用次数 | Token 消耗 | 说明 |
|---|---|---|---|
| 主Agent去重命中 | **0** | 0 | 纯内存匹配，不调 LLM |
| 分类器预判高置信命中 | **0** | 0 | 异步分类器直接判定（新增） |
| 命中经验规则（auto_close） | **0** | 0 | lessons_learned 命中 |
| 无历史预警 | **1**（⑤综合分析） | ~3K | — |
| 带 context_ref（相似预警） | **1**（⑤综合分析，简化版） | ~2K | 子Agent拿到历史结论，简化 prompt |
| 有历史 + 漏斗关联 | **3**（④粗筛 + ④精筛 + ⑤综合分析） | ~6K | — |

### 2.10 预警去重与预判（v4 新增）

v3 的去抖动方案（60s 窗口查 DB）有一个问题：第 2 条预警到达时第 1 条可能还没写入 DB。

v4 方案：

**1. 主 Agent 内存去重（主路径）**

见 1.5 节：主 Agent 维护 `pattern_index` + `recent_window`，在派发子 Agent 之前拦截重复预警。纯内存操作，零延迟。

**2. 异步分类器预判（辅助路径）**

参考 Claude Code 的 speculative classifier（`bashPermissions.ts:1483-1587`）：

```python
async def handle_alert_with_prejudge(alert):
    # 步骤 ②③ 同步进行的同时，后台启动分类器
    speculative = asyncio.create_task(classifier.prejudge(alert))
    
    lesson = check_lessons(alert)        # ② 同步查 DB
    history = correlate(alert)           # ③ 同步查 DB
    
    classifier_result = await speculative  # 等待分类器完成
    
    # 分类器高置信命中 → 直接跳过 LLM
    if (classifier_result.confidence == "high" 
        and classifier_result.action == "auto_close"):
        return AlertResult(verdict="false_positive", 
                          confidence=0.92,
                          source="classifier",
                          skipped_llm=True)
    
    # 继续走 ④⑤ 正常流水线
```

效果：很多低风险预警在 LLM 调用前就被两步过滤（主Agent去重 + 分类器预判）。

**3. DB 去重（兜底）**

如果主 Agent 刚重启、窗口还空的场景，降级查 DB：

```
条件：同 detection_key + 同 source_ip + 同 process_name
窗口：60 秒
实现：处理前查 alert_summaries 是否有 60s 内同条件记录
      有 → 跳过，返回已有结论
      无 → 正常处理
```

---

## 三、存储设计

理解了流水线后，再看存储设计。存储是流水线的"仓库"：流水线读它、写它。

### 3.1 三层存储定位

```
                    权威性高
                      ↑
   soc_soul.md        │     ← 部署时写一次，几乎不动
   （静态环境知识）     │        分析原则、网段说明、已知 IOC
                      │
   PostgreSQL         │     ← 动态知识主存储
   ├ soc_facts        │        模式级知识：纠正、环境、批准工具、威胁观察
   ├ fact_evidence    │        fact 的来源证据，关联 alert / review / CLI 纠正
   ├ lessons_learned  │        条件→动作规则 → ②经验快查读
   ├ alert_summaries  │        每条预警的摘要 → ③关联查询读
   └ review_queue     │        daemon 待复查 → 大屏复查读
                      │
   soc_memory_snapshot.json  ← 可选导出/备份/离线可读，不作为生产主存储
                      │
                    查询频率高
```

**流水线读写关系**：

| 流水线步骤 | 读 | 写 |
|---|---|---|
| ① 实体提取 | — | — |
| ② 经验快查 | lessons_learned + soc_facts | — |
| ③ 关联查询 | alert_summaries | — |
| ④ 漏斗关联 | alert_summaries（拉完整日志） | — |
| ⑤ LLM 综合分析 | soc_soul.md + soc_facts | soc_facts（LLM 发现时，pending_review） |
| ⑥ 置信度决策 | — | — |
| ⑦ 写入存储 | — | alert_summaries + review_queue + fact_evidence |

### 3.2 soc_soul.md — 部署时写一次

安全架构师/lead 在项目上线时写好，以后几乎不动。

```markdown
# SOC Agent Soul

## 分析原则
- 同 exe + 同规则，历史全 FP 的，先检查有没有细微差异再跟判
- 跨 ≥3 台机器出现同一可疑 IP，提高警惕
- 首次出现的模式，默认中低置信，不自动处理
- 高置信自动处理只做关闭和建事件，不做隔离/封禁

## 已批准工具
- SecurityScan v2.3 — 漏洞扫描，每周三凌晨 2 点
- AngryIPScanner — 安全团队资产盘点

## 网段说明
- 10.0.1.0/24  开发网段
- 10.0.5.0/24  财务网段，月末有异常流量属正常
- 10.0.8.0/24  DMZ，公网暴露

## 已知恶意 IOC
- 185.220.101.35 — Cobalt Strike C2（微步 2026-04）

## 运维窗口
- 2026-06 AD 迁移，异常认证事件预期增多
```

**注入方式**：⑤ LLM 综合分析时全文注入（通常几百行，~2-3K tokens）。

### 3.3 soc_facts — SOC 动态知识主表

SOC memory 采用 PostgreSQL 主存储，不直接引入 mem0 / agentmemory / memU 作为 MVP 运行时依赖。外部 memory 框架可参考其抽取、检索、实体链接设计，但 SOC 业务记忆必须可审计、可确认/驳回、可回滚。

```sql
CREATE TABLE soc_facts (
    id BIGSERIAL PRIMARY KEY,
    content TEXT NOT NULL,
    category TEXT NOT NULL,       -- environment / correction / approved / threat / observation
    status TEXT NOT NULL DEFAULT 'pending_review',
    confidence DOUBLE PRECISION NOT NULL DEFAULT 0.5,
    source_type TEXT NOT NULL,    -- cli_correction / daemon_review / llm_discovery / manual_seed
    source_alert_id TEXT,
    entities JSONB NOT NULL DEFAULT '{}'::jsonb,
    valid_from TIMESTAMPTZ,
    valid_until TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_facts_status_category ON soc_facts(status, category);
CREATE INDEX idx_facts_confidence ON soc_facts(confidence DESC);
CREATE INDEX idx_facts_entities_gin ON soc_facts USING GIN (entities);
CREATE INDEX idx_facts_content_fts ON soc_facts USING GIN (to_tsvector('simple', content));

CREATE TABLE fact_evidence (
    id BIGSERIAL PRIMARY KEY,
    fact_id BIGINT NOT NULL REFERENCES soc_facts(id) ON DELETE CASCADE,
    alert_id TEXT,
    evidence_type TEXT NOT NULL,  -- alert / analyst_note / llm_reason / review
    evidence_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_fact_evidence_fact ON fact_evidence(fact_id);
CREATE INDEX idx_fact_evidence_alert ON fact_evidence(alert_id);
```

示例 fact：

```json
{
  "content": "ProcExec-SuspiciousScanner 规则主要由 SecurityScan 触发，历史基本为 FP",
  "category": "correction",
  "status": "confirmed",
  "confidence": 0.92,
  "source_type": "cli_correction",
  "source_alert_id": "ALT-0042",
  "entities": {
    "rule_names": ["ProcExec-SuspiciousScanner"],
    "tools": ["SecurityScan"]
  }
}
```

**四个 status**：

| status | 含义 | 注入 prompt？ | 来源 |
|---|---|---|---|
| `confirmed` | 已确认 | **是** | 纠正对话提取 / 分析师确认 |
| `confirmed_candidate` | CLI 深度纠正产生的高可信候选 | 默认否；可由配置决定是否在当前 CLI 会话引用 | CLI 纠正对话 |
| `pending_review` | 待确认 | **否**（避免误导 LLM） | LLM 自发现 |
| `rejected` | 已驳回 | **否** | 分析师驳回 |

**粒度控制**：memory 只存模式级知识，不存具体 IP/用户。
- ✅ "SecurityScan 是公司漏扫工具"（适用所有机器）
- ✅ "财务网段月末对账有异常流量"（适用整个网段）
- ❌ "张三的 PC 有 USB 许可"（太具体，放 alert_summaries 关联查询即可）

**注入方式**：⑤ LLM 综合分析时，只注入 `status=confirmed` 且未过期的 facts，按实体相关性、confidence、时间新鲜度排序。

**检索策略**：

| 阶段 | 策略 |
|---|---|
| Phase 1 | `status=confirmed` + rule/exe/ip/domain/user 精确过滤 |
| Phase 2 | PostgreSQL full-text search + JSONB 实体索引 |
| Phase 3 | `pg_trgm` 模糊匹配，可选 `pgvector` 语义召回 |
| Phase 5 | 评估 mem0/memU 作为外部 memory engine 或知识库导入器 |

**外部 memory 框架定位**：

| 项目 | 定位 | SOC Agent 决策 |
|---|---|---|
| mem0 | 通用 AI Agent 长期记忆 | 参考检索/抽取思路，MVP 不引入运行时依赖 |
| agentmemory | Codex/Claude/OpenCode 等编码 Agent 记忆 | 适合辅助开发，不作为 SOC 业务 memory |
| LMCache | LLM KV cache / 推理加速 | 不是业务记忆；自建 vLLM/SGLang 后再评估 |
| memU | workspace/source 到结构化 agent memory | 参考 typed memory / workspace ingestion，Phase 5 再评估 |

### 3.4 alert_summaries — 每条预警的摘要

写入：⑦ 写入存储。读取：③ 关联查询 + ④ 漏斗关联（Phase 2 拉完整日志时）。

当前 Phase 1 实现已先落地 SOC 前缀表 `soc_alert_summaries`，由 `AlertSummary` contract 和 `AlertSummaryRepository` 维护。它是告警列表、review queue、dedup、correlation、Web/TUI 查询的读模型，不替代完整 run；完整事实仍在 `soc_analysis_runs.run_payload`。`analyze` / `replay` / `correct` 都通过 core service 更新 summary，入口层不能自行组装 summary。

下面的 `alert_summaries` DDL 是长期草案；实际实现以 `backend/soc_agent/db/migrations/versions/0003_alert_summaries.py` 为准。

```sql
CREATE TABLE alert_summaries (
    alert_id TEXT PRIMARY KEY,
    detection_key TEXT NOT NULL,
    rule_code TEXT,
    rule_name TEXT,
    source_type TEXT NOT NULL,
    source_system TEXT,
    alert_time TIMESTAMPTZ NOT NULL,

    process_name TEXT,
    source_ip TEXT,
    destination_ip TEXT,
    dst_port TEXT,
    domain TEXT,
    url TEXT,
    file_hash TEXT,
    user_name TEXT,
    entities JSONB NOT NULL DEFAULT '{}'::jsonb,
    raw JSONB NOT NULL DEFAULT '{}'::jsonb,

    verdict TEXT NOT NULL,
    confidence DOUBLE PRECISION NOT NULL,
    summary TEXT NOT NULL,
    alert_type TEXT,
    severity TEXT,

    analyzed_at TIMESTAMPTZ,
    corrected_at TIMESTAMPTZ,
    original_verdict TEXT,
    mode TEXT NOT NULL DEFAULT 'cli'
);

CREATE INDEX idx_sum_detection_key ON alert_summaries(detection_key);
CREATE INDEX idx_sum_rule_code ON alert_summaries(rule_code);
CREATE INDEX idx_sum_rule_name ON alert_summaries(rule_name);
CREATE INDEX idx_sum_process ON alert_summaries(process_name);
CREATE INDEX idx_sum_ip ON alert_summaries(source_ip, destination_ip);
CREATE INDEX idx_sum_domain ON alert_summaries(domain);
CREATE INDEX idx_sum_user ON alert_summaries(user_name);
CREATE INDEX idx_sum_time ON alert_summaries(alert_time DESC);
CREATE INDEX idx_sum_entities_gin ON alert_summaries USING GIN (entities);
```

### 3.5 lessons_learned — 条件→动作规则

写入：纠正对话后提取。读取：② 经验快查。

存的是**可自动执行的规则**，不是知识描述：

```sql
CREATE TABLE lessons_learned (
    id BIGSERIAL PRIMARY KEY,
    pattern TEXT NOT NULL,
    action TEXT NOT NULL,       -- auto_close / lower_priority / flag
    lesson TEXT NOT NULL,
    source_alert_id TEXT,
    hit_count INTEGER NOT NULL DEFAULT 0,
    confidence DOUBLE PRECISION NOT NULL DEFAULT 0.8,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_lessons_pattern ON lessons_learned(pattern);
CREATE INDEX idx_lessons_confidence ON lessons_learned(confidence DESC);
```

| pattern | action | lesson |
|---|---|---|
| `process:scanner.exe + detection_key:pingan-edr:rule_code:edr-scan-001` | auto_close | SecurityScan v2.3 漏扫工具触发 |
| `process:ngrok.exe` | lower_priority | 开发团队偶尔做调试用 |
| `source_ip:10.0.1.100 + detection_key:vpn:rule_name:anomalous_location` | auto_close | VPN 网关，出差员工从各地登录 |

### 3.6 review_queue — Daemon 模式待复查

写入：⑦ 写入存储（仅 daemon 模式 + 低置信）。读取：大屏复查。

```sql
CREATE TABLE review_queue (
    id BIGSERIAL PRIMARY KEY,
    alert_id TEXT NOT NULL,
    alert_time TIMESTAMPTZ NOT NULL,
    ai_verdict TEXT NOT NULL,
    ai_confidence DOUBLE PRECISION NOT NULL,
    ai_summary TEXT NOT NULL,
    ai_investigation TEXT,
    status TEXT NOT NULL DEFAULT 'pending',
    reviewed_at TIMESTAMPTZ,
    reviewer_note TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_review_status ON review_queue(status, created_at);
```

### 3.7 Knowledge RAG Layer — Phase 5 外部知识增强

Knowledge RAG Layer 不属于 runtime memory，不参与 Phase 1-3 的核心告警闭环。它用于给 LLM 分析提供可引用的外部知识证据，例如安全制度、EDR/SIEM 手册、告警规则文档、威胁情报报告、MITRE ATT&CK 映射和历史复盘报告。

边界：

| 层 | 存什么 | 是否影响实时判定 | 存储/实现 |
|---|---|---|---|
| `soc_facts` | 已确认的模式级业务知识 | 是，直接注入分析 prompt | PostgreSQL |
| `lessons_learned` | 条件→动作的可执行经验规则 | 是，可自动降噪/关闭 | PostgreSQL |
| `alert_summaries` | 历史告警摘要 | 是，做实体/时间窗口关联 | PostgreSQL |
| Knowledge RAG | 文档、报告、SOP、威胁知识图谱 | 否，作为证据补充和引用来源 | Phase 5 评估 PageIndex / GraphRAG |

候选技术：

| 项目 | 适合内容 | SOC 用法 | 引入时机 |
|---|---|---|---|
| VectifyAI PageIndex | 长 PDF、SOP、产品手册、规则说明、合规文档 | 把长文档转成树状索引，让 LLM 按章节/页码可解释检索 | Phase 5：安全制度/手册/报告问答 |
| Microsoft GraphRAG | 威胁情报、MITRE ATT&CK、资产/业务/账号关系、历史事件复盘 | 构建实体-关系图，回答多跳关系问题，例如 IOC→APT→TTP→资产影响 | Phase 5：威胁知识图谱和复杂关联 |

优先级：

1. **GraphRAG 优先**：更贴近 SOC 的实体关系、攻击链、资产依赖和威胁情报关联。
2. **PageIndex 次之**：适合长文档和报告的可解释章节检索。

Phase 5 之前只预留接口，不引入运行时依赖：

```python
class KnowledgeRetriever(Protocol):
    async def retrieve(self, query: str, entities: EntitySet) -> list[KnowledgeEvidence]:
        ...
```

⑤ LLM 综合分析时，Knowledge RAG 只作为“外部证据”注入，不自动写入 `soc_facts`。只有分析师确认或纠正后，才通过反馈闭环转化为 confirmed fact 或 lesson。

### 3.8 审计与回滚设计

SOC Agent 的每次判断都必须能回答三个问题：当时看到了什么、为什么这么判、后来如果知识被撤销哪些结论受影响。因此审计不是日志附属品，而是产品可信度的一部分。

#### soc_decision_audit_log — 每次判定的证据链

当前实现已先落地 `soc_decision_audit_log`，用于记录 analyze / replay / correct 的结构化审计摘要。完整 run 快照仍保存在 `soc_analysis_runs.run_payload`，审计表服务快速查询、质量指标和后续 Web/TUI 展示。

```sql
CREATE TABLE soc_decision_audit_log (
    id BIGSERIAL PRIMARY KEY,
    alert_id TEXT NOT NULL,
    pipeline_version TEXT NOT NULL,
    mode TEXT NOT NULL,              -- cli / daemon / replay
    model_name TEXT,
    input_hash TEXT NOT NULL,
    prompt_hash TEXT,
    injected_fact_ids BIGINT[] NOT NULL DEFAULT '{}',
    matched_lesson_ids BIGINT[] NOT NULL DEFAULT '{}',
    llm_raw_output TEXT,
    repaired_output JSONB,
    final_verdict TEXT NOT NULL,
    final_confidence DOUBLE PRECISION NOT NULL,
    decision_reason TEXT NOT NULL,
    error_message TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_audit_alert ON decision_audit_log(alert_id);
CREATE INDEX idx_audit_created ON decision_audit_log(created_at DESC);
CREATE INDEX idx_audit_facts_gin ON decision_audit_log USING GIN (injected_fact_ids);
```

写入规则：

- `soc analyze` 每次都写审计记录，即使 LLM 输出解析失败也记录 raw output 和错误。
- `replay` 写新的审计记录，不覆盖历史记录。
- prompt 全文可按环境配置保存或只保存 hash；但必须保存 injected fact ids、matched lesson ids 和最终决策原因。

#### soc_fact_versions — 知识变更版本

```sql
CREATE TABLE soc_fact_versions (
    id BIGSERIAL PRIMARY KEY,
    fact_id BIGINT NOT NULL REFERENCES soc_facts(id),
    old_status TEXT,
    new_status TEXT,
    old_content TEXT,
    new_content TEXT,
    changed_by TEXT NOT NULL,        -- cli / daemon_review / analyst / migration
    reason TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_fact_versions_fact ON soc_fact_versions(fact_id, created_at DESC);
```

回滚规则：

- fact 从 `confirmed` 改为 `rejected` 或内容发生实质变化时，查 `decision_audit_log.injected_fact_ids`，标记受影响历史结论。
- 回滚不静默改写历史 verdict；只生成“受影响”标记，必要时通过 `soc replay ALERT_ID` 重新研判。
- 自动关闭类 lesson 被撤销后，后续不再命中；历史自动关闭记录进入待抽样复查。

#### automation_actions — 自动处置预留审计

Phase 1 不做自动处置，但先预留自动动作审计模型，避免 Phase 4 后补时缺证据链。

```sql
CREATE TABLE automation_actions (
    id BIGSERIAL PRIMARY KEY,
    alert_id TEXT NOT NULL,
    action_type TEXT NOT NULL,       -- auto_close / lower_priority / create_case / isolate_host
    proposed_by TEXT NOT NULL,       -- lesson / classifier / analyst / llm
    approved_by TEXT,
    status TEXT NOT NULL DEFAULT 'proposed',
    reason TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    executed_at TIMESTAMPTZ
);

CREATE INDEX idx_actions_alert ON automation_actions(alert_id);
CREATE INDEX idx_actions_status ON automation_actions(status, created_at);
```

Phase 4 之前，只允许写 `proposed` 或 `dry_run`，不执行破坏性动作。

---

## 四、反馈闭环

流水线只"读"和"写"存储，但存储的**内容质量**靠反馈闭环持续提升。

### 4.1 三条知识入口

```mermaid
flowchart TD
    subgraph 入口1["入口 1：部署时注入"]
        D["安全架构师编写<br/>soc_soul.md"] --> SOUL["soc_soul.md"]
    end

    subgraph 入口2_CLI["入口 2a：CLI 纠正对话"]
        C1["分析师纠正"] --> C2["LLM 追问对话"]
        C2 --> C3["提取知识"]
        C3 --> MEM["soc_facts<br/>status: confirmed"]
        C3 --> LESSON["lessons_learned<br/>条件→动作规则"]
    end

    subgraph 入口2_DAEMON["入口 2b：Daemon 复查"]
        R1["分析师大屏复查"] --> R2["确认/驳回/纠正"]
        R2 -->|"确认·提取模式知识"| MEM
        R2 -->|"纠正·提取规则"| LESSON
        R2 -->|"驳回"| REJECT["status: rejected"]
    end

    subgraph 入口3["入口 3：LLM 自发现"]
        L1["⑤ LLM 分析中<br/>new_knowledge 非空"] --> L2["写入 soc_facts"]
        L2 --> PENDING["status: pending_review<br/>confidence: 0.5-0.6<br/>不注入 prompt"]
        PENDING -->|"分析师 review"| MEM
    end
```

### 4.2 CLI 纠正对话流程

```bash
$ soc-agent correct ALT-0042 --verdict false_positive

💭 我把 SecurityScan 误判为恶意扫描工具，
   可能是因为不了解公司环境中有这个已批准工具

我有几个问题想确认：
  1. scanner.exe 是你们批准的安全工具吗？叫什么名字？
  2. 它定期运行还是偶尔运行？

❓ scanner.exe 是你们批准的安全工具吗？叫什么名字？
  → SecurityScan v2.3，公司买的漏扫工具

❓ 它定期运行还是偶尔运行？
  → 每周三凌晨定时跑

还有什么要补充的吗？（回车跳过）
  → 

✅ 已保存 1 条知识到 soc_facts（status: confirmed）
✅ 已保存 1 条规则到 lessons_learned（以后同类自动关闭）
✅ 已更新 ALT-0042 verdict → false_positive
```

**纠正涉及三个 Prompt**：
1. 反思+追问生成（LLM 调用 #1）
2. 知识提取（LLM 调用 #2）
3. 规则提取（同一次调用，一起输出）

### 4.3 Daemon 复查流程

```
第二天，分析师打开复查大屏：

┌─────────────────────────────────────────────────────┐
│ 待复查: 23 条  │  已确认: 156 条  │  已纠正: 8 条   │
├──────────┬──────────┬──────────┬────────────────────┤
│ 预警ID    │ AI判断    │ 置信度    │ 一句话摘要          │
├──────────┼──────────┼──────────┼────────────────────┤
│ ALT-0150 │ TP       │ 0.72     │ 10.0.3.5 异常PS... │
│ ALT-0149 │ FP       │ 0.68     │ scanner.exe 触发... │
│ ...      │          │          │                    │
└──────────┴──────────┴──────────┴────────────────────┘

操作：
  [确认] → AI 判断正确 → 提取模式知识（如果有价值）
  [纠正] → 选正确 verdict → 可选填原因 → 自动提取知识+规则
  [批量确认] → 全部正确 → 不提取知识（太多了，没有新信息）
```

**Daemon 复查 vs CLI 纠正**：
- CLI：深度追问 2-3 个问题，提取详细知识
- Daemon 复查：快速确认/纠正，只提取高价值知识（避免每条都追问）

### 4.4 pending_review 知识的触达路径

pending 状态的知识**不注入 prompt**（避免误导 LLM），但需要让分析师看到并确认。三条路径：

#### 路径 1：CLI 专有命令

```bash
$ soc-agent knowledge review

📋 待确认知识（3 条）：

  #1 [observation] confidence: 0.55
     "10.0.3.0/24 网段近期频繁触发 PowerShell 编码执行规则"
     来源: LLM 自发现 · 2026-05-17
     → [c]确认 [r]驳回 [s]跳过: c
     ✅ 已确认

  #2 [environment] confidence: 0.58
     "10.0.1.55 的 nmap 扫描行为可能属于开发网段月度扫描"
     → [c]确认 [r]驳回 [s]跳过: r
     "驳回原因（可选）: 这是攻击者在做内网探测"
     ✅ 已驳回
```

#### 路径 2：纠正对话末尾顺带问

```
✅ 已保存 1 条知识到 soc_facts
✅ 已保存 1 条经验规则

💡 顺便问一下，我之前有一个观察想确认：
   "10.0.3.0/24 网段近期频繁触发 PowerShell 编码执行规则"
   这个观察对吗？
   → [y]对 [n]不对 [回车跳过]: y
   ✅ 已确认
```

逻辑：纠正对话结束后，查有没有和当前预警实体相关的 pending_review 条目，有则顺带问。利用分析师已在思考相关上下文的时机。

#### 路径 3：Daemon 复查大屏

```
┌─ 待确认知识（3 条）─────────────────────────┐
│ #1 10.0.3.0/24 频繁PS执行  → [确认] [驳回]  │
│ #2 nmap 可能是月度扫描      → [确认] [驳回]  │
│ #3 svchost 非标准路径常见    → [确认] [驳回]  │
└───────────────────────────────────────────────┘
```

### 4.5 知识老化

- soc_facts 超过 180 天未命中 → 降低注入优先级或标记待复查
- lessons_learned 的 hit_count 连续 90 天为 0 → 标记待 review
- confirmed facts 数量超过阈值 → 触发 dream/整理任务，合并重复知识并导出快照

### 4.6 知识污染防线

SOC 业务记忆会直接影响后续判断，因此知识写入必须比普通聊天记忆更保守。核心原则：LLM 可以提出候选知识，但不能自行把候选知识变成真理。

| 来源 | 默认状态 | 是否注入 prompt | 晋级条件 |
|---|---|---|---|
| `soc_soul.md` 部署时手写 | 静态权威 | 是 | 安全负责人手工维护 |
| CLI 深度纠正提取的 fact | `confirmed_candidate` 或 `confirmed` | 仅 `confirmed` 注入 | 分析师明确确认，且包含来源 alert/evidence |
| Daemon 批量复查提取的 fact | `pending_review` | 否 | 二次确认或多次独立命中后确认 |
| LLM 自发现 fact | `pending_review` | 否 | 分析师 review 确认 |
| 自动关闭 lesson | `pending_review` | 否 | 命中多次、无人纠正、分析师确认后启用 |
| 被驳回知识 | `rejected` | 否 | 默认进入抑制列表，避免反复提出 |

为了支持 `confirmed_candidate`，`soc_facts.status` 可采用四态：

| status | 含义 |
|---|---|
| `confirmed` | 已确认，可注入 prompt |
| `confirmed_candidate` | CLI 纠正中高可信候选，默认不自动注入；可由配置决定是否在 CLI 单次会话内引用 |
| `pending_review` | 待人工确认，不注入 |
| `rejected` | 已驳回，不注入，并用于抑制重复建议 |

自动关闭规则必须额外保守：

- 单次纠正不能直接生成长期 `auto_close` 规则，只能生成候选 lesson。
- 候选 lesson 需要至少命中多次，且没有被纠正，才允许转为启用。
- 首次出现的模式、跨主机扩散、命中高危 IOC、置信度低于阈值时，一律不自动关闭。
- 自动关闭只允许关闭或降级，不允许隔离、封禁、删除文件等破坏性动作。
- 每条自动关闭都必须写 `automation_actions` 和 `decision_audit_log`。

被驳回知识的抑制策略：

- 同一 content + entities 的 LLM 自发现如果已被驳回，短期内不再重复提示。
- 如果新 evidence 明显不同，可以重新进入 `pending_review`，但必须展示“曾被驳回”的历史。
- rejected fact 不删除，保留为负样本和审计证据。

---

## 五、入口设计：对齐 DeerFlow 的 Web / Gateway / TUI / Headless / Channels

### 5.1 Phase 1 先做 Headless CLI

Headless CLI 是 MVP 的最短闭环入口，用于开发调试、单条研判、知识纠正和后台 ingestion 运维。它对齐 DeerFlow `deerflow --print/--json/--cli` 的定位：终端入口可以无界面运行、输出可脚本化结果，但不代表 SOC 要做一套独立于 DeerFlow 的产品入口。

第一版命令：

```bash
export SOC_DATABASE_URL="postgresql://user:password@localhost:5432/soc_agent"
soc db upgrade
soc analyze ./samples/alert.json
soc analyze ./samples/alert.json --persist
soc analyze --json '{"source":{"source_type":"edr","source_system":"pingan-edr"},"detection":{"rule_code":"EDR-SCAN-001","rule_name":"ProcExec-SuspiciousScanner"},"entities":{"network":{"source_ip":"10.0.1.10"},"process":{"process_name":"SecurityScan"}}}'
soc show RUN-0001
soc replay RUN-0001
soc correct RUN-0001 --verdict false_positive --reason "approved scanner maintenance window"
soc memory list
```

说明：CLI/API/Kafka 入口可以为了接入便利接受 flat/simple/vendor-like payload，但必须先经过 `normalizers/` 转换成 canonical `AlertInput`。核心 runtime、pipeline、DB、memory、API response 不直接依赖 flat 字段。

Phase 2/4 扩展：

```bash
soc replay ALT-0001
soc lessons list
soc ingest start
soc ingest status
soc review list --status pending
```

### 5.2 Phase 1 验收标准

Phase 1 完成的定义不是“CLI 命令能跑”，而是“单条告警能可信研判、能纠正、能审计、失败可解释”。

| 场景 | Given | When | Then |
|---|---|---|---|
| 正常分析 | 一条字段完整的样例 EDR/SIEM 告警 JSON | 执行 `soc analyze alert.json` | 输出 verdict、confidence、summary、evidence、reason、suggested_action，并写入 `alert_summaries` 和 `decision_audit_log` |
| 字段缺失 | 告警缺少非关键字段，如 `dst_port` 或 `domain` | 执行 `soc analyze` | 实体提取降级，输出 warning，但不中断整体分析 |
| LLM JSON 损坏 | 模型返回带代码块、尾逗号或半截 JSON | 解析分析结果 | 先严格解析，失败后用 `json_repair` 修复；仍失败则返回明确错误并记录 raw output |
| 数据库不可用 | PostgreSQL 连接失败 | 执行 `soc analyze` 或 `soc correct` | CLI 返回可读错误，不假装成功，不产生半写入状态 |
| 分析师纠正 | 已有一条分析结果 | 执行 `soc correct RUN_ID --verdict ... --reason ...` | 更新当前 operational decision，追加 correction record，标记候选知识为 `pending_review`，不自动注入 memory |
| 审计追踪 | 任意一次分析或纠正 | 查询审计记录 | 能看到输入 hash、注入 fact ids、命中 lesson ids、模型原始输出/修复输出和最终决策 |
| Step trace | 任意一次分析 | 查询 run trace | 能看到每个 pipeline step 的开始/结束时间、输入摘要、输出摘要、错误、重试次数和耗时 |
| Domain validation | LLM 输出结构化结果 | 进入 `decide` 前校验 | verdict、confidence、evidence、recommended_action 必须通过 schema + 业务规则校验；不合法则进入 `needs_review` 或失败态 |
| 不自动处置 | 任意 Phase 1 分析结果 | verdict 为高置信 FP 或 TP | 只输出建议动作，不执行隔离、封禁、删除、关闭生产告警等动作 |
| replay 一致性 | 同一条 alert 和相同配置 | 执行 `soc replay ALERT_ID` | 生成新的审计记录，不覆盖历史记录，差异可比较 |
| LLM 不掌握主流程 | 任意 Phase 1/2 分析 | 检查 trace 和配置 | pipeline 必经步骤不能被 LLM 跳过；LLM 只能在固定节点内输出结构化建议 |

Phase 1 最小样例集：

- 1 条明确误报：公司批准扫描器触发规则。
- 1 条明确真阳性：命中已知恶意 IOC 或异常进程链。
- 1 条低置信未知：缺少上下文，只能进入人工判断。
- 1 条字段缺失/格式异常样例。
- 1 条模型输出坏 JSON 的模拟样例。

### 5.3 入口分层原则

```
soc_agent/core/service.py        # core services：统一业务入口
soc_agent/core/runtime.py        # deterministic runtime：固定 pipeline 和状态迁移
soc_agent/cli.py                 # Headless CLI，只调用 core services
soc_agent/api/                   # Gateway/API routers，只调用 core services
soc_agent/ingestion/             # Kafka/Redpanda consumer adapter，只调用 core services
soc_agent/tui/                   # 后续 Terminal Workbench，只调用 core services
soc_agent/channels/              # 后续 IM channel adapter，只调用 core services
```

约束：

- Headless CLI、API、TUI、Web UI、Channels 都只是入口，不写业务判断逻辑。
- Kafka/Redpanda ingestion 是后台 adapter，不是用户产品入口，也不写业务判断逻辑。
- 单条研判、Kafka 消费、TUI 交互、Web UI 复查、Channel 确认都复用同一套 core services。
- Phase 1 已先落地 `SocAnalysisService`，后续逐步补 `SocReviewService`、`SocMemoryService`、`SocDaemonService`、`SocAgentChatService`。
- Phase 1 不做复杂 TUI；终端输出只用于表格、颜色、进度条和 JSON 输出。
- Phase 3/4 可以做后端 TUI / Operator Console，用于实时运行状态、review queue、run trace、replay、纠正、memory 确认、agent 对话。
- Web UI 放到 review_queue 和批量复查模型稳定后再做，避免早期返工。

入口调用关系固定为：

```text
Headless CLI ─────┐
TUI ──────────────┤
Gateway API ──────┤
Web UI ───────────┼──> Core Services ──> Runtime / Agent Orchestrator ──> pipeline/db/memory/policy/tools
Channels ─────────┤
Kafka Adapter ────┘
```

不允许：

- Kafka consumer 直接调用 pipeline 节点。
- TUI 直接写 DB 或 memory。
- Web UI 绕过 API/core service 直接拼业务判断。
- Headless CLI 为了方便复制 runtime 逻辑。
- 任一入口绕过 `policy` 执行高风险动作。

### 5.4 TUI / Operator Console 的位置

TUI 不是 Phase 1 必做，但应作为后续重要入口保留。它面向值班运营、安全分析师、检测工程师和授权攻防人员，定位类似“后端操作台 + 交互式 Agent Console”。

Headless CLI 和 TUI 的区别：

| 入口 | 交互形态 | 适合场景 | SOC 约束 |
|---|---|---|---|
| Headless CLI | 一次性命令或脚本化命令，例如 `soc analyze alert.json`，对齐 DeerFlow `--print/--json` | 开发调试、CI、单条 replay、运维脚本 | 输出结构化结果，不维护复杂屏幕状态 |
| TUI | 持续运行的终端界面，有列表、面板、键盘交互、实时刷新 | 值班运营、队列浏览、run trace、交互纠正、agent 对话 | 只调用 core services，不直接实现业务逻辑 |

DeerFlow 已提供 TUI 入口：`deerflow.tui` 基于 optional `textual`，入口层负责启动界面；底层仍通过 runtime / run manager / stream bridge 处理 run 生命周期和事件流。SOC Agent 应保持同样风格：TUI 是 operator experience，不是另一套分析引擎。

TUI 可以提供：

- 实时 Kafka/daemon 处理状态、队列深度、失败率、LLM 成本。
- review queue 浏览、过滤、批量确认/驳回。
- 单条 alert 的 run trace、证据、实体、命中 lessons、注入 memory 查看。
- replay / diff / 纠正 verdict。
- facts/lessons 的确认、驳回、回滚。
- 类 DeerFlow 的 case 内对话式调查：让 agent 总结、追问、生成调查步骤、解释不确定性。
- 高风险动作审批面板：显示 actor、scope、reason、可回滚性和审计记录。

TUI 不能做：

- 自己实现分析 pipeline。
- 自己生成或确认 memory fact，必须调用 `SocMemoryService`。
- 自己执行工具动作，必须走 `policy` 和 tool registry。
- 绕过 service 直接写 PostgreSQL。

### 5.5 Web UI 的位置

当前 DeerFlow 前端已有通用 Web UI，但 SOC 场景需要专用视图：告警队列、研判详情、证据时间线、实体关联、复查队列、批量确认/驳回。Web UI 不是 Phase 1 重点，应在 Phase 3/4 基于稳定的 `review_queue` 和 API 增量构建。

### 5.6 任务队列选型：不上来不用 Celery

预计规模是每天最多约 1 万条告警，平均吞吐约 7 条/分钟。瓶颈主要是 LLM/API 调用的并发、限流、重试和突发峰值吸收，不是队列框架调度性能。因此不在 MVP 引入 Celery。

分阶段策略：

| 阶段 | 队列方案 | 用途 | 理由 |
|---|---|---|---|
| Phase 1 | 进程内 `asyncio.Queue` / `PriorityQueue` + `Semaphore` | CLI 单条/少量分析、LLM 并发限流 | 零外部依赖，最容易调试 |
| Phase 2 | 进程内优先级队列 + 去重 key | 主 Agent 去重、子 Agent 并行调度 | 支持高优/低优告警，避免重复 LLM 调用 |
| Phase 4 | PostgreSQL-backed queue（优先 PgQueuer；备选 Procrastinate） | Daemon 消费 Kafka 后持久化派发子 Agent | 已经有 PostgreSQL，不额外引入 Redis/RabbitMQ；重启不丢任务 |
| Phase 5+ | 再评估 Dramatiq/Celery | 多机高吞吐、复杂路由、统一监控 | 只有规模明显上升或需要复杂队列路由时再引入 |

不选 Celery 的原因：

- 对日 1 万告警是过重依赖，需要 Redis/RabbitMQ、worker、beat/flower 等运维面。
- SOC Agent 已经依赖 PostgreSQL，PG-backed queue 可以复用同一基础设施。
- 真实耗时主要在 LLM/API 调用，队列框架 no-op benchmark 的差异不是主要矛盾。

队列抽象先自研一层接口，便于后续替换：

```python
class TaskQueue(Protocol):
    async def enqueue(self, task: QueueTask) -> str: ...
    async def claim(self, worker_id: str) -> QueueTask | None: ...
    async def ack(self, task_id: str) -> None: ...
    async def retry(self, task_id: str, reason: str) -> None: ...
    async def fail(self, task_id: str, reason: str) -> None: ...
```

Phase 1 的进程内实现不保证持久化；Phase 4 的 PostgreSQL 实现必须支持任务状态、重试次数、优先级、租约超时和 worker 心跳。

### 5.6 工具参考落地建议

`/home/yydspei/projects/system-prompts-and-models-of-ai-tools/tools` 中的材料按“是否进入 SOC Agent 运行链路”分三类处理。

| 工具/材料 | SOC Agent 价值 | 采用阶段 | 落地方式 |
|---|---|---|---|
| `json_repair` | 修复 LLM 输出中的尾逗号、代码块、半截 JSON 等问题 | Phase 1 | 放在 `llm/json_parser.py`：先严格解析 + Pydantic 校验，失败后 repair，再记录修复日志 |
| LiteLLM | 统一不同模型提供商、token 计数、参数容错、异步批量调用 | Phase 1/2 评估 | 如果 DeerFlow `models/factory.py` 已满足则不强行替换；若要接 DeepSeek/自建 OpenAI-compatible 服务，可做 `LLMClient` 适配层 |
| LangExtract | 对非结构化日志、告警描述、报告文本做高召回结构化提取，并保留字符级溯源 | Phase 2/5 | Phase 1 仍以纯代码实体提取为主；复杂原始文本再引入 `EntityExtractor` 的 LangExtract 实现 |
| PageIndex | 长 PDF、SOP、产品手册、规则说明的可解释检索 | Phase 5 | 作为 Knowledge RAG Layer 的文档索引后端，不进入实时告警主路径 |
| Python `ContextVar` / `Protocol` | 异步请求上下文隔离、可替换接口设计 | Phase 1 | `RequestContext` / `TaskQueue` / `MemoryStore` / `KnowledgeRetriever` 优先用 `Protocol` 定义边界 |
| 代码质量材料 | ruff、pre-commit、CI 门禁，降低后期重构成本 | Phase 1 | 开发脚手架阶段就补 `ruff`、测试命令和 CI 检查 |
| Makefile / Git 工作流 | 统一本地命令和 fork/upstream 同步流程 | 开发流程 | 作为工程协作规范，不放进运行时依赖 |

关键原则：工具必须服务于流水线中的明确问题。MVP 不追求“工具全接入”，而是先把 `json_repair`、类型边界、代码质量和轻量队列打牢；长文档 RAG、多模型统一网关、复杂 LLM 提取放到有真实数据后再评估。

---

## 六、Prompt 设计汇总

### 6.1 纠正追问 Prompt

```
你判断错了。原始判断: {ai_verdict}，分析师纠正: {analyst_verdict}。
分析师备注: {analyst_note}
预警详情: {alert_summary}

请反思为什么判错，然后提 2-3 个追问帮您理解环境。
不要问预警里已有的信息。

输出 JSON:
{
    "self_reflection": "一句话反思",
    "questions": ["问题1", "问题2", "问题3"]
}
```

### 6.2 知识提取 Prompt

```
以下是纠正对话记录：
{conversation}

提取可复用的模式级知识（不要提取具体 IP/用户级别的）。
区分"一次性事实"和"可复用知识"：
- "这台机器在重装" → 一次性，不提取
- "SecurityScan 是我们的漏扫工具" → 可复用，提取

输出 JSON:
{
    "facts": [
        { "content": "...", "category": "correction|environment|approved|threat", "confidence": 0.0-1.0 }
    ],
    "lesson": {
        "pattern": "exe:xxx + rule:yyy",
        "action": "auto_close | lower_priority",
        "lesson": "人能读的解释"
    }
}
```

---

## 七、项目结构与开发路线图

### 7.1 项目结构

```
soc-agent/
├── config.yaml              # 模型/API/阈值配置
├── soc_soul.md              # 静态环境知识（部署时写一次）
├── soc_memory_snapshot.json # 可选导出快照（生产主存储在 PostgreSQL soc_facts）
├── docker-compose.yml       # 本地 PostgreSQL + Kafka/Redpanda 开发环境
├── .env.example             # DATABASE_URL / KAFKA_BROKERS 等本地配置示例
│
├── cli.py                   # CLI 入口，只调用 core services
├── api/                     # 后续 Web UI/Gateway 调用入口，只调用 core services
├── daemon/                  # Daemon 入口（Kafka 消费 + 优雅关闭），只调用 core services
├── tui/                     # 后续 Operator Console / Agent Console，只调用 core services
│
├── contracts/               # API/Kafka/Event/LLM/Tool schema，所有跨边界协议先落这里
│   ├── api.py
│   ├── events.py
│   ├── kafka.py
│   ├── llm.py
│   └── tools.py
│
├── core/
│   ├── service.py           # SocAnalysisService：analyze/replay/run 查询
│   ├── review_service.py    # SocReviewService：review queue / correction
│   ├── memory_service.py    # SocMemoryService：facts / lessons / rollback
│   ├── daemon_service.py    # SocDaemonService：worker orchestration
│   ├── chat_service.py      # SocAgentChatService：交互式调查 / case 对话
│   ├── runtime.py           # SOC Runtime：固定控制流、状态迁移、错误恢复
│   ├── state.py             # AnalysisRun / PipelineStep / RunStatus
│   ├── validator.py         # schema_validate + domain_validate
│   ├── router.py            # Phase 3+：受限 LLM Advisory Router（白名单 next steps）
│   └── types.py             # Alert / Entity / Analysis / Decision 数据结构
│
├── queue/
│   ├── base.py              # TaskQueue 协议
│   ├── memory_queue.py      # Phase 1：进程内 asyncio/PriorityQueue
│   └── pg_queue.py          # Phase 4：PostgreSQL-backed queue（PgQueuer/自研适配）
│
├── agent/
│   ├── main_agent.py        # Main Agent（持久·去重·编排）
│   ├── main_context.py      # MainAgentContext 数据类
│   ├── sub_agent.py         # Sub Agent（per-alert·7步流水线）
│   └── agent_mailbox.py     # 主-子 Agent 通信
│
├── policy/
│   ├── engine.py            # PermissionDecision / 动作权限 / risk gate
│   ├── levels.py            # L0-L5 权限等级
│   └── audit.py             # 权限决策审计
│
├── prompts/
│   ├── versions.py          # prompt_version 管理
│   ├── sanitizer.py         # PromptSanitizer：脱敏和敏感字段拦截
│   └── templates/           # prompt 模板
│
├── pipeline/
│   ├── extractor.py         # ① 实体提取
│   ├── lesson_check.py      # ② 经验快查
│   ├── correlator.py        # ③ 关联查询
│   ├── funnel.py            # ④ 漏斗关联（Phase1+Phase2）
│   ├── classifier.py        # 异步分类器预判（新增·参考 Claude Code bashPermissions）
│   ├── analyzer.py          # ⑤ LLM 综合分析
│   ├── prompt_builder.py    # ⑤ prompt 组装（含缓存感知·新增）
│   ├── decider.py           # ⑥ 置信度决策（5 层决策链）
│   ├── writer.py            # ⑦ 写入存储
│   └── trace.py             # step-level trace 采集
│
├── events/
│   ├── signal.py            # Signal 响应式事件（参考 Claude Code signal.ts）
│   ├── hook_executor.py     # Hook 生命周期管理（参考 hookEvents.ts）
│   └── hooks/               # 内置 hooks
│       ├── progress_hook.py # 流水线进度推送
│       └── audit_hook.py    # 审计日志
│
├── memory/
│   ├── store.py             # PostgreSQL SocMemoryStore（soc_facts/fact_evidence）
│   ├── extractor.py         # 从纠正对话/LLM发现提取候选 facts
│   ├── apply_updates.py     # 合并新旧 facts（复用 DeerFlow 思路）
│   ├── inject.py            # 格式化注入 prompt
│   ├── snapshot.py          # 导入/导出 soc_memory_snapshot.json
│   └── dream.py             # 后台知识整合去重（参考 Claude Code autoDream.ts）
│
├── knowledge/               # Phase 5：外部知识增强，不参与 MVP 核心闭环
│   ├── retriever.py         # KnowledgeRetriever 协议
│   ├── pageindex_adapter.py # 长文档/SOP/报告树状检索（可选）
│   └── graphrag_adapter.py  # 威胁情报/ATT&CK/资产关系图检索（可选）
│
├── feedback/
│   ├── correct_dialog.py    # CLI 纠正追问对话
│   ├── knowledge_extract.py # 知识提取
│   └── review_processor.py  # Daemon 复查处理
│
├── db/
│   ├── migrations/          # PostgreSQL schema 迁移
│   ├── schema.sql           # 当前 PostgreSQL schema 快照
│   ├── queries.py           # 查询函数
│   └── models.py            # 数据模型
│
├── observability/
│   ├── events.py            # event sink / SSE / CLI progress 统一事件
│   ├── metrics.py           # Prometheus 指标
│   └── replay_diff.py       # replay 结果比较
│
└── utils/
    ├── accumulator.py       # EndTruncatingAccumulator（安全截断·参考 Claude Code）
    └── shutdown.py          # 多级优雅关闭（参考 gracefulShutdown.ts）
```

### 7.2 开发路线图

| Phase | 目标 | 周期 | 核心交付 |
|---|---|---|---|
| **Phase 1** | MVP — CLI + Runtime 可靠性闭环 | 2 周 | contracts schema + 固定 pipeline + ①实体提取 + ⑤LLM分析（无关联）+ PromptSanitizer + schema/domain validation + step trace + 审计落库 + rate limit + `analyze`/`correct`/`replay` |
| **Phase 2** | 关联能力 + 主Agent去重 | 2 周 | ③关联查询 + ④漏斗关联 + ②经验快查 + MainAgent去重 + pattern_index |
| **Phase 3** | 学习能力 + 受限软路由 | 2 周 | PostgreSQL SocMemoryStore + 纠正对话 + facts status 管理 + 异步分类器 + LLM Advisory Router 白名单 next steps + prompt/model replay evaluation |
| **Phase 4** | Daemon 模式 + 子Agent并行 | 2 周 | Kafka 消费 + SubAgent并行 + review_queue + 优雅关闭 + 去重预判 + AsyncAPI/Kafka schema + metrics/readiness + replay diff / router 评测 |
| **Phase 5** | 增强 | 按需 | Knowledge RAG Layer（GraphRAG 优先，PageIndex 次之）/ 威胁情报集成 / 复查大屏 UI / MITRE ATT&CK / 知识老化 / Dream整合 |

---

## 八、业界调研摘要

| 来源 | 关键启示 |
|---|---|
| [Dropzone AI - Context Memory](https://www.dropzone.ai/blog/context-memory-ai-soc-analyst) | 三阶段 Collect→Comprehend→Conclude；四类上下文；70% FP 降低 |
| [Dropzone AI - 300+ 部署经验](https://www.dropzone.ai/blog/ai-soc-analyst-lessons-learned-at-scale) | 先降噪再 AI；SOAR 处理确定性预警；分阶段上线 |
| [AgentSOC 论文](https://arxiv.org/html/2604.20134v1) | 四层架构；闭环 Sense→Reason→Act；506ms 延迟 |
| [Vigil SOC](https://github.com/Vigil-SOC/vigil) | 12 Agent + Workflow；confidence 0.90 自动批准 |
| [Radiant Security](https://thehackernews.com/expert-insights/2025/11/continuous-feedback-loops-why-training.html) | Day 90: 70-80% FP 降低；静态 AI 会退化 |
| [D3 Security](https://d3security.com/resources/5-architectural-flaws-agentic-ai-soc/) | 多 Agent 5 个结构性缺陷；统一推理引擎更优 |

---

## 九、技术实现：在 DeerFlow Fork 上增量扩展

### 路线：业务模块增量接入，不侵入上游核心

当前仓库本身是 DeerFlow fork，因此实现路线不是重写一个新框架，也不是把 SOC 逻辑散落改进 DeerFlow 原有主流程，而是在 fork 内新增 SOC 业务模块：

1. 用 LangGraph 画 SOC Agent 自己的 7 步流水线。
2. 通过 import、adapter、wrapper 复用 DeerFlow 已有模型、配置、middleware、工具桥接等能力。
3. SOC 专有 CLI、daemon、PostgreSQL schema、Kafka 消费、审计和复查流程放在 SOC 模块内。
4. 只有当现有 DeerFlow 缺少通用扩展点时，才对上游原文件做小范围、通用化改动。

这样做的目标是同时满足两个条件：SOC Agent 能快速落地；未来同步 upstream 时不会因为大量业务侵入而难以 rebase。

### 复用的组件

| DeerFlow 组件 | 怎么用 | 改动量 |
|---|---|---|
| `models/factory.py` | 直接 import `create_chat_model()` | 零 |
| `memory/updater.py` | 搬过来，改 MEMORY_UPDATE_PROMPT | 小 |
| `memory/storage.py` + `queue.py` | 搬过来（原子写入 + 30s 去抖动） | 零 |
| `middleware/` 部分 | 挑着搬（summarization, error handling） | 小 |
| `config/app_config.py` | 搬过来（热重载 + YAML） | 小 |
| `tools/sync.py` + `types.py` | 直接 import（async→sync 桥接） | 零 |

### 不搬的东西

| 组件 | 不搬的原因 |
|---|---|
| DeerFlowClient | SOC 走 Kafka 消费，不走 chat |
| Gateway/Auth | 部署环境自己管 |
| IM Channels | SOC 走 Kafka/大屏 |
| Skills | 7 步流水线固定，不需要动态加载 |
| Persistence | 用自己的 PostgreSQL schema 存 alert |
| Sandbox | 除非要执行脚本分析日志 |

### SOC Agent 图（两层）

**主 Agent 图（去重 + 派发）**：

```python
# main_agent.py
g = StateGraph(MainAgentState)
g.add_node("dedup", dedup_check)              # 去重判定（纯代码）
g.add_node("dispatch", dispatch_to_subagent)  # 派发子Agent
g.add_node("merge", merge_with_prior)         # 合并到已有预警
g.add_node("update_ctx", update_main_context) # 更新主Agent滑动窗口+索引

g.set_entry_point("dedup")
g.add_conditional_edges("dedup", route, {
    "duplicate": "merge",
    "new": "dispatch"
})
g.add_edge("dispatch", "update_ctx")
g.add_edge("merge", "update_ctx")
g.add_edge("update_ctx", END)
```

**子 Agent 图（7 步流水线 + 分类器预判）**：

```python
# sub_agent.py
g = StateGraph(SubAgentState)
g.add_node("extract", extract_entities)              # ① 纯代码
g.add_node("check_exp", check_experience)             # ② 查 DB
g.add_node("classify_bg", async_classifier_prejudge)  # 分类器预判（后台并行·新增）
g.add_node("correlate", correlate_alerts)             # ③ 查 DB
g.add_node("funnel_phase1", funnel_phase1)            # ④ Phase 1 LLM
g.add_node("funnel_phase2", funnel_phase2)            # ④ Phase 2 LLM
g.add_node("analyze", llm_analysis)                   # ⑤ LLM
g.add_node("decide", confidence_decision)             # ⑥ 5层决策
g.add_node("write", write_results)                    # ⑦ 写 DB

g.set_entry_point("extract")
g.add_edge("extract", "check_exp")
# 分类器与 ②③ 并行执行
g.add_edge("extract", "classify_bg")
g.add_conditional_edges("check_exp", route, {"skip": "write", "continue": "correlate"})
g.add_edge("correlate", "funnel_phase1")
g.add_edge("funnel_phase1", "funnel_phase2")
g.add_edge("funnel_phase2", "analyze")

# 在 analyze 之前等待分类器结果（join）
# 如果分类器已高置信命中 → 跳过 LLM 直接 decide
g.add_conditional_edges("analyze", route_classifier, {
    "classifier_hit": "decide",  # 分类器结果作为参考
    "normal": "decide"
})
g.add_conditional_edges("decide", route, {
    "auto": "write", "escalate": "write", "retry": "analyze"
})
g.add_edge("write", END)
```

分类器预判的关键设计（参考 Claude Code `bashPermissions.ts:1555-1587`）：

```python
async def async_classifier_prejudge(state: SubAgentState) -> SubAgentState:
    """后台运行，与步骤②③并行。结果缓存到 state，步骤⑤使用。"""
    command = f"{state.exe_name} {state.rule_name}"
    descriptions = [state.exe_name, state.rule_name]
    
    result = await classifier.classify(command, descriptions, mode='allow')
    
    state.classifier_result = result
    if result.matches and result.confidence == 'high':
        state.classifier_hit = True
        state.classifier_action = result.action  # auto_close / lower_priority
    
    return state
```

---

## 十、长期演进：Security Agent Platform

当前项目仍以 SOC 告警研判为第一个落地场景，但架构上应保留演进为综合安全智能体平台的空间。长期目标不是把所有能力塞进一个“万能 Agent”，而是：

> 一个安全智能体平台 + 一个总控 Orchestrator + 多个专职 Agent + 共享 core 能力 + 严格权限和审计。

这样既能复用实体、证据、审计、memory、工具和队列等基础设施，也能避免 SOC 防御判断、攻击模拟、检测工程、威胁狩猎之间互相污染。

### 10.1 为什么不是单一 Agent

不建议用一个超大 Agent 同时完成 SOC 研判、防御、渗透测试、F5/WAF 攻防、终端攻击分析和红蓝对抗。

原因：

- **目标冲突**：SOC 研判要保守、可解释、减少误报；攻击模拟要探索路径、寻找绕过，两者 prompt 目标不同。
- **memory 污染**：攻击模拟经验不能直接变成 SOC 自动关闭规则；防御误报知识也不能影响攻击链规划。
- **权限混乱**：只读日志分析和执行攻击/处置动作不是同一风险等级。
- **审计困难**：出了误判或误操作时，必须知道是哪条 agent 链、哪类知识、哪个工具动作造成的。
- **安全边界不足**：红队/攻击模拟属于高风险能力，必须独立授权、限定 scope、强审计。

因此产品形态应是“综合入口 + 专职 Agent”，而不是一个全能 Agent。

这里的“综合入口”指统一的 operator experience，而不是把所有业务逻辑塞进一个入口或一个 Agent。长期可以同时存在 TUI、Web UI、API、CLI、Daemon，但它们都进入 service/orchestrator 层：

```text
TUI / Web UI / API / CLI / Daemon
        ↓
Core Services + Orchestrator
        ↓
SOC / Detection / Hunting / IR / Attack Simulation Agents
        ↓
Shared contracts / evidence / policy / audit / memory / tools
```

也就是说，TUI 可以让运营、安全、检测工程和授权攻防人员使用同一套平台体验，但不同任务必须路由到不同 service/agent，并受到 memory scope、tool permission 和 audit 约束。

### 10.2 多 Agent 角色划分

建议长期保留以下 Agent 角色，但不要在 Phase 1 一次性实现。SOC Agent 是第一个垂直 Agent。

| Agent | 主要职责 | 引入时机 | 默认动作权限 |
|---|---|---|---|
| `soc_triage_agent` | 告警研判、误报降噪、证据整理、反馈学习 | Phase 1 起 | L0-L2，Phase 4 可有限 L3 |
| `detection_engineering_agent` | 分析误报模式、建议规则调优、生成 Sigma/YARA/EDR 查询草案 | Phase 3/4 | L0-L2 |
| `threat_hunting_agent` | 基于 IOC/TTP/实体关系做历史回溯和威胁狩猎 | Phase 4/5 | L0-L2 |
| `incident_response_agent` | 事件响应 runbook、调查步骤、case 汇总和处置建议 | Phase 4/5 | L0-L3，破坏性动作审批 |
| `threat_intel_agent` | 威胁情报、IOC、APT/TTP、MITRE ATT&CK 映射 | Phase 5 | L0-L1 |
| `endpoint_security_agent` | 终端进程链、EDR 事件、文件/注册表/账号行为分析 | Phase 5 | L0-L2 |
| `network_waf_agent` | F5/WAF/网络边界日志分析、攻击特征归因、规则建议 | Phase 5 | L0-L2 |
| `attack_simulation_agent` | 授权范围内的攻击路径建模、靶场验证、检测验证 | Phase 5+ | 默认 L0-L1；执行需审批和 scope |

各 Agent 必须共享实体和证据模型，但不能默认共享所有 memory。

### 10.3 Orchestrator 与共享 Core

长期项目结构可从 `soc-agent` 演进为 `security-agent-platform`：

```text
security-agent-platform/
├── core/
│   ├── entity/          # IP、domain、host、user、process、hash、rule、asset
│   ├── evidence/        # 证据结构、引用、证据链渲染
│   ├── case/            # alert / incident / finding 生命周期
│   ├── audit/           # decision_audit_log / automation_actions
│   ├── policy/          # 动作权限、安全边界、审批策略
│   ├── memory/          # facts / lessons / memory scope / 版本回滚
│   ├── knowledge/       # 文档、威胁情报、ATT&CK、SOP
│   ├── queue/           # TaskQueue / lease / retry / heartbeat
│   ├── llm/             # model gateway / json parser / prompt builder
│   └── tools/           # 工具注册、权限声明、执行审计
│
├── agents/
│   ├── soc_triage_agent/
│   ├── detection_engineering_agent/
│   ├── threat_hunting_agent/
│   ├── incident_response_agent/
│   ├── threat_intel_agent/
│   ├── endpoint_security_agent/
│   ├── network_waf_agent/
│   └── attack_simulation_agent/
│
└── orchestrator/
    ├── router.py        # 根据任务类型、风险等级、数据源路由到 Agent
    ├── supervisor.py    # 多 Agent 编排、join、冲突处理
    └── safety_gate.py   # 高风险动作审批、scope 校验、审计落库
```

Phase 1 不需要建立完整平台目录，但写代码时应把通用能力放在可迁移边界：

- 实体抽取不要绑定 SOC pipeline。
- evidence 和 audit 设计要能服务所有 Agent。
- memory 必须带 scope/type/source，不能只有一张无边界 facts 表。
- 工具执行必须走统一 tool registry 和 action policy。

### 10.4 Memory 隔离策略

综合安全智能体最容易出问题的是 memory 混用。必须从设计上把“环境事实、防御经验、攻击知识、case 上下文”分开。

建议长期采用以下 memory scope：

| Scope | 内容 | 默认可被谁使用 | 是否可影响自动决策 |
|---|---|---|---|
| `environment_facts` | 网段、资产、业务系统、批准工具、运维窗口 | 所有防御类 Agent | 是，但需 confirmed |
| `detection_facts` | 告警规则、误报模式、检测经验、SOC 纠正知识 | SOC / Detection / Hunting | 是，但需 confirmed |
| `attack_knowledge` | TTP、攻击链、验证方法、靶场经验 | Attack Simulation / Detection / Threat Intel | 默认否；转化后才能影响防御决策 |
| `case_memory` | 某一次事件、告警、演练的临时上下文 | 当前 case 相关 Agent | 只影响当前 case |
| `global_security_knowledge` | MITRE、CVE、厂商文档、F5/EDR/SIEM 手册 | 所有 Agent | 作为外部证据，不直接变成事实 |
| `negative_memory` | 被驳回的 LLM 发现、错误规则、误判模式 | 所有相关 Agent | 用于抑制重复错误 |

SOC 自动研判默认只能注入：

- `environment_facts`
- `detection_facts`
- 当前 `case_memory`
- 已确认的外部证据引用

不能默认注入 `attack_knowledge`。如果攻击模拟结果要帮助 SOC，需要经过“检测知识转化”流程，进入 `detection_facts` 或 `lessons_learned`，并保留来源和审计。

### 10.5 动作权限分级

所有 Agent 的工具和动作必须按风险分级。权限等级不是技术细节，而是产品安全边界。

| 等级 | 动作类型 | 示例 | 默认策略 |
|---|---|---|---|
| L0 | 只读分析 | 查日志、读告警、读知识库、生成解释 | 默认允许，记录审计 |
| L1 | 生成建议 | 建议关闭、建议调查、建议检测规则、生成 runbook | 默认允许，需标注不执行 |
| L2 | 低风险写入 | 写 `review_queue`、写 `pending_review` fact、创建草稿 case | 允许，但必须审计 |
| L3 | 受控操作 | auto_close、lower_priority、创建正式 case | 需要策略命中和可回滚 |
| L4 | 高风险生产操作 | 封禁账号、隔离主机、修改生产规则、阻断流量 | 默认人工审批 |
| L5 | 攻击模拟执行 | 在靶场运行 PoC、扫描授权资产、模拟攻击链 | 仅授权 scope + 强审批 + 隔离环境 |

阶段约束：

- Phase 1：只允许 L0-L2，不执行自动处置。
- Phase 4：SOC Agent 可在严格条件下执行有限 L3，例如高置信重复误报 auto_close。
- Phase 5+：攻击模拟 Agent 默认只做 L0-L1；任何执行都必须绑定 scope、目标、时间窗、审批人和审计记录。

红队/攻击模拟能力只能定位为“授权环境下的模拟、验证和检测评估”：

- 允许：攻击路径建模、漏洞影响分析、检测规则验证建议、靶场验证计划。
- 受控：在本地靶场/授权测试环境运行验证。
- 默认禁止：对真实资产自动攻击、横向移动、破坏、持久化、绕过检测的实操自动化。

### 10.6 路线图：SOC → Detection → Hunting → IR → Attack Simulation

长期路线应保持从低风险、高复用能力开始：

| 阶段 | 能力 | 原因 |
|---|---|---|
| Step 1 | SOC Triage Agent | 当前 MVP，先跑通可信研判、纠正、审计 |
| Step 2 | Detection Engineering Agent | 与 SOC 数据最接近，直接复用误报、规则、证据和 lessons |
| Step 3 | Threat Hunting Agent | 复用实体索引、历史摘要和 IOC/TTP 查询 |
| Step 4 | Incident Response Agent | 复用 case、证据链、runbook 和处置建议 |
| Step 5 | Threat Intel Agent | 接入外部知识、MITRE、CVE、厂商文档 |
| Step 6 | Endpoint / Network / WAF Agents | 面向终端、F5/WAF、网络边界做领域化分析 |
| Step 7 | Attack Simulation Agent | 最后引入，必须限定授权范围和执行审批 |

近期最推荐的扩展顺序：

1. 先完成 SOC Phase 1-3，把单条研判、反馈学习、审计做稳。
2. 再做 Detection Engineering Agent，专门处理规则噪音、误报模式、检测建议。
3. 然后做 Threat Hunting Agent，用 SOC 已沉淀的实体和历史数据做回溯。
4. 最后再考虑攻击模拟和红蓝对抗能力。

### 10.7 对当前方案的落地要求

为了不在未来推倒重来，当前 SOC Agent 实现时必须保留这些扩展点：

- `AlertEntities` 应升级为通用 `SecurityEntitySet`，覆盖 asset、network、identity、process、file、rule、vulnerability。
- `AlertResult` 应抽象为 `SecurityFinding` 或保留可迁移字段：verdict、confidence、evidence、recommended_action、audit_id。
- `soc_facts` 应预留 `scope` 字段，区分 `environment_facts`、`detection_facts`、`attack_knowledge` 等。
- `decision_audit_log` 和 `automation_actions` 不要只服务 SOC，要能记录任何 Agent 的决策和动作。
- 所有工具调用必须声明权限等级、作用域、是否可回滚。
- prompt builder 必须支持按 Agent 类型注入不同 memory scope。
- Web UI 后续应以 case/evidence/review 为核心，而不是只做 SOC 告警列表。

最终形态：

```text
Security Agent Platform
  = Orchestrator
  + SOC Triage Agent
  + Detection Engineering Agent
  + Threat Hunting Agent
  + Incident Response Agent
  + Threat Intel Agent
  + Endpoint / Network / WAF Agents
  + Authorized Attack Simulation Agent
  + Shared Core: entity / evidence / audit / policy / memory / tools
```
