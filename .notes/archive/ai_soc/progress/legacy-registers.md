# Legacy SOC Progress Registers

> Superseded planning and capability tables removed from the active progress pointer by `PI-06`. They are preserved only for historical traceability; current status comes from `delivery-roadmap.md` and `progress.md`.

## 阶段交付主线

> 这张表只反映权威阶段顺序。每个阶段的详细 task 和 Gate 以 `delivery-roadmap.md` 为准；下面的能力长表仅用于追踪历史能力，不决定当前下一刀。

| 阶段 | 交付物 | 状态 | 当前边界 | 退出条件 |
|---|---|---|---|---|
| `BD` | Boss Demo v0.1 | **Done / BD Gate Passed** | 已交付浏览器优先 golden path、可重置数据和演示验收 | `BD-01..03` 和 BD Gate 已全部通过 |
| `AA` | SOC Alpha Completeness Audit | **Done / AA Gate Passed** | 50 项唯一矩阵、13 个 Gap 和 7 个冻结工作包已确认 | AA Gate 已于 2026-07-18 通过 |
| `BG` | Close Blocking Gaps | **Done / Alpha Gate Passed** | P0/P1、readiness technical gate、独立评审与具名范围批准已完成 | 2026-07-20 批准进入 Stage 4 integration preparation |
| `PI` | Real Data & Production Integration | **Current / External Product Complete + Real Debt Open** | 既有 simulation、PI-01F/F2 和 PI-01G 专家子智能体产品链已完成；7 个真实 gate 保持 open | 外网产品完整性缺口已关闭；fresh real evidence、具名 owner approval、cohort enforcement 和可执行 rollback 到位后才能进入 Pilot readiness review |

## 能力与历史切片台账

> 下表保留能力状态和历史切片。TODO 不能越过 `delivery-roadmap.md` 插入当前 Stage。

| 顺序 | 待办 | 状态 | 做什么 | 验收标准 |
|---|---|---|---|---|
| 0 | PingAn knowledge decomposition | Partial / internal-smoke-gated | 已完成 `PA-01..PA-11`；`PA-12` 的 DEV profile/signer/preflight 已就绪，等待内网真实 smoke | 通用 skill 不含平安内部知识；每条平安经验都有 target artifact、tenant scope、来源和验收方式；PA-12 不能用 mock 冒充完成 |
| 0.1 | `PA-01` PingAn capability card register | Done | 已新增 `.notes/ai_soc/capabilities/pingan/capability-cards.md`，从 APT/EDR/HIDS 三份源文档抽出 P0/P1/P2 cards | P0 card 已明确 source、场景、输入、输出、落点、风险等级、失败模式和验收要求；mock MCP 必须等 card 明确后再做 |
| 0.2 | `PA-02` APT source decomposition | Done | 已扩展 `PA-APT-001..005`：攻击方向、场景化研判、威胁情报、security tag、IP 封堵高风险边界；拆出 skill/domain handler/eval/memory/action 边界 | APT 通用方法进 public skill/domain handler；平安字段、URI 例外、内部环境、策略和阈值只进 tenant artifact |
| 0.3 | `PA-03` EDR source decomposition | Done | 已扩展 EDR cards：进程树、路径/命令行、LoginData/System、提权、UM/账号、终端处置候选 | 通用 endpoint 方法进 skill/domain handler；平安路径、账号、部门、BU 和封禁/隔离策略只进 tenant artifact 或 approval-gated action |
| 0.4 | `PA-04` HIDS source decomposition | Done | 已扩展 HIDS cards：主机事件上下文、event_type 场景化研判、误报/授权运维模式、服务器隔离候选 | 通用 host/endpoint 方法进 skill/domain handler；平安组名、账号、路径、机房、域名、隔离模板只进 tenant artifact 或 approval-gated action |
| 0.5 | `PA-05` PingAnKnowledgeCandidate register | Done | 已新增 `.notes/ai_soc/capabilities/pingan/knowledge-candidates.md`，从 APT/EDR/HIDS expanded cards 抽候选知识清单，标注 target_artifact、tenant_scope、source_doc、source_section、status、validity、review owner | 每条平安专属经验都能回答“放哪里、为什么、是否过期、由谁确认”，且默认 pending_review，不直接影响 runtime decision |
| 0.6 | `PA-06` public skills minimal revisions | Done | 已对 `skills/public/soc-*` 做最小增量修订，只吸收跨客户通用的 APT/EDR/HIDS/WAF/asset 研判方法 | `rg` 检查 public skills 不出现平安字段、内部环境、账号/组织、白名单、模板 ID、策略 ID 或处置阈值 |
| 0.7 | `PA-07` P0 read-only mock action adapters | Done / revised | 保留 `threat_intel.ip_reputation.lookup`、`security_tag.lookup` mock adapters；未经证实的进程树/主机上下文查询 mock 已删除 | 外部查询通过 `SocActionAdapterRegistry`；进程/主机上下文使用告警原生证据；不改 verdict/memory |
| 0.8 | `PA-08` PingAn eval fixtures | Done | 已新增 `backend/samples/eval/pingan/` 三条 fixture、`backend/samples/alerts/pingan_legacy_hids.json`、`backend/soc_agent/eval/pingan.py` 和 `soc eval pingan` | APT/EDR/HIDS 各 1 条脱敏 fixture；覆盖字段冲突、查不到外部事实、误报/授权标签；read-only success 写 `InvestigationEvidence` |
| 0.9 | `PA-09` PingAn memory candidate entry | Done | 已新增 `SocMemoryCandidate` contracts、`MemoryCandidateRepository` protocol、in-memory repository 和 `SocMemoryService.propose_candidate()` | 候选默认 `pending_review`，携带 source/evidence/validity/idempotency/facets/review 信息；不自动 confirmed，不影响 runtime decision |
| 0.10 | `PA-10` PingAn domain triage MVP | Done | 已新增 `SocDomainTriageRequest/Result/Finding` contract、`SocDomainTriageService`、APT/EDR/HIDS deterministic handlers 和 `soc eval pingan-domain` | 子研判只输出 finding/evidence/recommendation；消费 skill context 和 read-only evidence refs；不写 DB、不执行 action、不改 verdict |
| 0.11 | `PA-11` PingAn main orchestrator demo | Done | `SocMainOrchestratorService`、`UnifiedInvestigationReport`、`soc eval pingan-main` 已覆盖 APT/EDR/HIDS analyze -> correlation -> read-only evidence -> domain finding -> review summary | 每条当前告警命中 seeded historical run，并只复用该 historical `run_id` 的 evidence；不写 DB、不执行高风险动作 |
| 0.12 | `PA-12` real PingAn MCP/API replacement | In Progress / internal smoke | DEV profile、portable signer、preflight/direct smoke entry 已完成；内网补 Agent Platform import、approved cases 和 `mocked=false` 证据 | 评估 latency、failure、payload/result size、字段裁剪和敏感信息风险；不能用本地 mock 假装完成 |
| 0.13 | `D12-A` PingAn asset provider implementation | Done / fake-only | 已建立可移植 `asset.locate` provider、ZEUS HTTP/signing port、workflow port、stdio MCP server、显式 config 与 fake smoke | fake 输出始终 `mocked=true`；internal 缺配置 fail closed；该状态不标记 PA-12/PI-01 real provider Done |
| 0.14 | `D12-B` PingAn asset provider internal smoke | Parked / internal evidence pending | root config/模型/签名已审计，preflight、direct matrix 和 MCP evidence acceptance 已实现；按产品决定暂存，恢复时直接在内网补真实 case matrix | 成功、查无、鉴权失败、超时和 InvestigationEvidence 全部留证；真实响应为 `mocked=false`；暂存不等于完成 |
| 0.15 | `PI-01A` PingAn threat-intelligence provider | In Progress / internal smoke pending | production-shaped Provider、stdio MCP、action config、bounded field mapping、freshness/lineage 和 fake/persistence 回归已完成 | 真实 DEV hit/not-found/error/timeout；实际字段 coverage 经复核；`mocked=false` evidence 可持久化回读；不迁移旧评分/封禁规则 |
| 1 | Correlation Service MVP | Done | `SocCorrelationService` 基于 summary/evidence 输出相似告警、匹配原因和可复用证据；typed result 已进入 main report/domain/review summary | 不调用 LLM、不依赖真实 MCP、不改 decision；demo 当前告警可看到历史 run + reusable evidence |
| 1.1 | Correlation -> Unified Investigation bridge | Done | 共享 summary repository、统一 deterministic scorer、`SocDomainTriageRequest.correlation_result`、`UnifiedInvestigationReport.correlation_result` 和 review counts 已接通 | metadata count 不是证据源；historical evidence 只按 matched `run_id` 加载；移除两个无效 mock 后 APT/EDR/HIDS eval 为 3 matches / 4 evidence / 0 failure |
| 1.2 | Correlation quality baseline | Done | 已建 vendor-neutral same-incident / related-but-distinct / unrelated corpus；`soc eval correlation` 输出双任务指标、reason 分布、fan-out、evidence lineage/unrelated exposure，并支持 `--baseline-json` replay diff | scorer/report/fixture 版本显式；当前 8-pair baseline 暴露 retrieval/dedup precision 均约 0.667；`shadow_dedup_allowed=false` |
| 1.3 | [Correlation label corpus expansion](../archive/ai_soc/deferred/correlation-label-corpus-expansion.md) | Deferred / `PI-03` data-dependent | 从脱敏真实告警准备 analyst-reviewed pairs，覆盖来源、时间窗口、跨规则同事件和同规则不同事件 cohort | 不以 8 条受控 pair 代表生产分布；标签来源/rationale/version 可审计；扩充后再比较 scorer v2，不直接切换生产规则；不阻塞当前 `PI-01` |
| 2 | External Disposition Sync Contract | Done | 已新增 vendor-neutral event/status/mapping/record/result contract、generic mapper、Zeus mock fixture、`SocExternalDispositionService`、repository protocol、in-memory repository、PostgreSQL persistence、ReviewQueue context API/Web/TUI/Lead Agent visibility；已接 high-trust mapped review/correction 和 pending memory candidate | 不在 core service 写死 Zeus；未知状态/无法定位只保存 unmatched；重复事件幂等；free-text reason 只能进 pending candidate，不能进 confirmed memory |
| 3 | Memory Tracking Contract | Done for DB/Runtime v2; Wiki projection deferred | DB-first candidate/review/retrieval governance、Memory Admission、shared facet builder、full-corpus facet index、type-aware strong-anchor Retrieval v2、fixed Runtime `M-*` injection 和 typed effective-decision directive 已完成 | 不使用四维硬 key；缺 rule_code/topic/vendor alias 时仍可工作；单条信号先准入且每条告警不创建 memory；wiki/OKF 只作为后期 projection |
| 3.1 | Memory candidate DB/API/ReviewQueue visibility | Done | 已新增 `soc_memory_candidates`、repository、CLI `soc memory list/get`、Gateway `/api/soc/memory/candidates`、ReviewQueue context/Web/TUI/Lead Agent bounded visibility | candidate 仍为 `pending_review` 且 `runtime_decision_allowed=false`；不注入 prompt，不影响 verdict |
| 3.2 | Memory candidate review workflow / confirmed-memory boundary | Done | `SocMemoryService`/CLI/Gateway 支持 confirm/reject/deprecate/expire；confirm 可选择附加审核后的 `SocMemoryDecisionDirective` | 默认 record retrieval-disabled；自由文本无改判权限；typed directive 不能从文本推断，override 必须有 required facets |
| 3.3 | Confirmed memory retrieval policy / unified visibility MVP | Done / v2 | `SocMemoryQuery.v2`、full-corpus facet index、score/reason/budget、memory-type exact anchor gate、CLI/API/ReviewQueue/Web/TUI/Lead Agent 和 fixed Runtime `M-*` injection 已接通 | 只返回 governed active + strong-anchor records；top-K 是投影预算而非最新-N扫描；普通 `M-*` 只作 reasoning context |
| 3.4 | Governed confirmed-memory retrieval activation | Done | `SocMemoryRetrievalActivationCommand` 和 `SocMemoryService.set_retrieval_activation()` 统一 role/reason/expected-version/validity/review/audit 语义；CLI/API/Web/Boss Demo 均复用该入口，search 支持 baseline diff | 直接写布尔值、过期 activation、逾期 review 或无治理 metadata 的 record 均不能进入 bounded retrieval；事务失败不留下 record/audit 半写状态 |
| 3.5 | Effective decision + governed response automation | Done for code/simulation; real gate open | `SocAutomationService`、strict policy、four lineage records/tables、CLI lineage、Memory directive、no-Memory automatic action、idempotent retry 已实现 | 默认关闭；shadow 不授权；Memory conflict 停止 rule selection；真实 adapter/owner/rollback/labels 未验收前不得启用生产 enforced execution |
| 4 | Domain Sub-Agent Contract | Done for PA-10 | 已固定 `SocDomainTriageRequest`、`SocDomainTriageResult`、`SocDomainFinding` 结构 | EDR/APT/HIDS 已共用同一 schema；子研判不能直接改 decision 或写 DB |
| 4.1 | Network direction + role adjudication | Done for contract/runtime; live quality gate next | `AnalysisResult.v4` 输出三层方向、typed security roles 和 action-specific target proposal；PingAn reviewed direction knowledge 通过 bounded `C-*`；人工确认追加 revision | 不把 source/destination 写死为 attacker/victim；模型 target 无动作权限；下一步用人工方向真值评测并补 Web/TUI 确认 UI |
| 5 | Generic security scenario recognition | Partial | deterministic MVP 已完成：第一批场景包括反弹 shell、webshell、横向移动、命令/代码执行、恶意外联、提权、凭证滥用；未命中内部 taxonomy 但存在上游场景提示时输出 `vendor.unmapped` 候选 finding；已暴露 `SCENARIO_TAXONOMY_VERSION`/keys/snapshot；PingAn domain eval 和 vendor-neutral `soc eval scenarios` 都输出 covered/missing/unmapped 计数，`--baseline-json` 可生成 replay diff | 任何来源的告警都通过统一 `SocDomainTriageResult/Finding` 输出场景化 finding；Evidence Fusion First；未映射厂商场景不阻断研判、不改 verdict、不写 confirmed memory；eval 能作为 replay diff 基线；LLM 后续只能在 bounded context 中识别场景，不能直接改 verdict 或写 confirmed memory |
| 6 | Main SOC Agent Orchestrator MVP | Done for Phase 2 bridge | 已串起 analyze、skill context、correlation、read-only action evidence、domain triage、review summary，输出 `UnifiedInvestigationReport` | APT/EDR/HIDS demo 能看到主控用了哪些 skill、历史 match/reasons/evidence、route、finding 和 review context |
| 7 | Web/TUI visible investigation | Done for MVP | 已新增 `UnifiedInvestigationView`、`InvestigationTimelineItem`，`InvestigationContext` 聚合 correlation result、domain triage results、evidence timeline、external feedback、memory candidates 和 relevant memories；Web/TUI/Lead Agent bounded artifact 可见 | 分析师能区分 runtime decision、domain findings、read-only evidence、外部人工反馈、人工 correction、retrieval-enabled memory；视图只读，不改 verdict |
| 8 | Demo / Eval Script | Done for APT/EDR/HIDS + single-alert MVP | `soc demo run`/`soc demo alert` 保持持久化调查演示；`soc eval pingan-main` 额外验证无 DB 的 current + historical correlation 主编排链 | 可分别演示持久化 Web/TUI context 与 bounded orchestrator report；mock action evidence 明确标记，不冒充真实 PA-12 |
| 9 | Memory candidate source integration | Done for PI-03F + Admission v1 | `SocMemoryCandidateSourceBridge` 已接 correction/domain finding/feedback/review note 并统一经过 Memory Admission；PI-03F1/F2 已接人工采纳 Lead Agent message；PI-03F3 以 immutable observation + 24h UTC source-event-time cohort + 5/5 双门槛接 Kafka/batch，只创建一个 frozen pending candidate | 模型输出、普通点击/note、每条 alert/finding/offset 均不能直接写 candidate；重复出现不证明 verdict/authorization/impact/action；confirmed/retrieval gate 仍由 `SocMemoryService` 控制 |
| 10 | Normalization maintenance loop | Done for MVP | 持久化 schema baseline、主动 monitor、去重/reopen issue、SocEvent、CLI/API/Web/TUI、Kafka metric 摘要；字段重要性 registry、离线 suggestion、confidence calibration 和 repair domain guard 已落地 | 新 schema/解析降级/关键映射缺口不静默；首次观察不自批 baseline；suggestion 不自动改代码；calibration profile 不自动放行动作 |
| 11 | DeerFlow-backed live Runtime LLM | Done for MVP | 新增 `DeerFlowLLMChatClient`、`SocLLMSettings`，统一装配 analyze/replay/demo/Kafka；offline eval 和 normalize suggest 支持 live model | 显式选择模型；未知模型 fail-fast；输出过 JSON/schema/domain validation；trace 记录安全 metadata/usage；模型不能执行动作 |
| 11.1 | Deterministic decision policy / confidence guard | Done for uncalibrated MVP | 新增 `SocDecisionPolicy`，把 raw analyzer score、来源、校准状态、证据状态、结构化 review reasons 和 policy version 分开；mock/failed evidence 不参与 domain/scenario 置信度 | stub/LLM self-report 当前全部进入复核；误报、冲突、schema 降级/不支持、关键证据缺口、截断等 guard 不会被高分覆盖；summary/queue/audit 保留原因 |
| 11.2 | Runtime production hardening | Done | 显式 `skill_context` trace、共享 bounded projection、analysis evidence grounding、prompt/output/schema hard bounds、LLM concurrency/RPM admission、typed sanitized failure、Kafka retry/dead-letter 语义、run/summary/review/audit 原子 bundle | 未落地证据强制复核；可重试失败不 commit offset/不制造工单噪声；不可重试失败进入 ReviewQueue/DLQ；SQL 故障回归证明四类主写入全部回滚 |
| 11.3 | Governed confidence label set | Done for initial 5-sample baseline / 3 accepted, 2 excluded | 已完成 5 条 DeepSeek 同 scope 标签审阅：2 false positive、1 exploit-attempt true positive accepted；2 条决定性授权/内部业务上下文缺失样本保留业务真值但 excluded；validator 返回 `calibratable=true` | 标签集无 pending/重复 input hash/混合 model-prompt-pipeline scope；accepted 同时包含正负类；仅 3 条 accepted，仍只允许离线 smoke，不能生成生产阈值或自动放行 |
| 11.4 | Governed context fact lifecycle | GF-01 Done | 已完成 typed `AuthorizedActivityPayload`、append-only fact versions、role-gated lifecycle、repository protocol、in-memory/SQLAlchemy persistence、`0013` migration、CLI 和 sample | Fact 与 evidence/memory/approval/detection truth 分离；revision fail closed 并重新审批 |
| 11.5 | Authorized activity event-time matcher | AA-01 Done | 已完成 `AuthorizationQuery/AuthorizationMatchResult`、canonical query builder、历史 lifecycle version selection、source freshness/recurrence/scope matcher、`soc context match`；真实 HIDS/EDR shadow replay 均为 exact | matcher 不识别 vendor aliases、不调用 LLM、不持久化、不改 verdict/ReviewQueue/disposition；naive event time 必须显式传 IANA timezone |
| 11.6 | Authorization enrichment persistence/projection | EX-01 Done | 已完成 strict command/record/result contracts、append-only in-memory/SQL repository、`0014` migration、幂等写入、replay lineage、CLI 和 InvestigationContext/Web/TUI/Lead Agent 投影 | enrichment 保存 query hash/policy/fact refs/actor；`shadow_only=true`、`decision_impact=none`；不修改 run decision/queue/memory/disposition |
| 11.7 | Shadow disposition proposal | DP-01 Done | 已完成 generic operational disposition、detection truth snapshot、append-only proposal repository、`0015` migration、CLI 和 InvestigationContext/Web/TUI/Lead Agent 投影 | 仅 open-queue persisted exact enrichment + current true-positive 可提议 benign-TP；proposal 永远 shadow/not-applied，人工复核，不改 run/queue，不自动关单 |
| 11.8 | Shadow disposition evaluation gate | EV-01 Done | 已完成 explicit evaluation scope、hash-ranked sample manifest、append-only superseding outcome、`0016` migration、CLI、gate report 和 InvestigationContext/Web/TUI/Lead Agent 只读投影 | 指标覆盖 resolution/precision/override/sample coverage+agreement/freshness/fact fan-out；passed 只代表可进入治理评审，`auto_close_allowed=false` |
| 11.9 | Structured disposition outcome capture | EV-02 Done | authenticated API/Web、Review TUI primary/sample command 和 trusted external disposition bridge 全部复用 `SocDispositionEvaluationService` | 不从 `close_reason` 猜标签；幂等、sample membership、独立 reviewer、supersession 和 closed queue 仍由 service 校验；不启用 auto-close |
| 11.10 | Sample review campaign inbox | EV-03 Done | 新增 derived reviewer inbox、latest-outcome batch query、Gateway read API 和 Web `抽样复核` view；selected work 回到 EV-02 capture form | manifest 仍是唯一抽样真相；禁止挑样、第二写入口和 mutable campaign table；`auto_close_allowed=false` |
| W1 | Real dev/staging CMDB/EDR MCP replacement | Waiting | 等 endpoint/凭证后替换本地 fixture，运行 `soc mcp tools/smoke` 并保存 report | 评估 latency、failure、payload/result size、字段裁剪和敏感信息风险 |
| D1 | [Wiki/OKF export projection](../archive/ai_soc/deferred/wiki-okf-memory-projection.md) | Deferred | DB memory store、retrieval、review workflow 稳定后，再做 DB -> wiki/OKF export | PostgreSQL 仍是 source of truth；wiki 反向修改只能生成 proposal |
| D2 | [Production telemetry / Prometheus / SLO](../archive/ai_soc/deferred/production-observability-and-slo.md) | Deferred / real-telemetry-dependent | PI-04A Snapshot CLI/API 与 PI-04B 薄 Web 已完成；仅真实 Kafka/Runtime/LLM/Provider/算力 telemetry、Prometheus、SLO 和告警治理仍后置 | 不重做 Operations Web；真实 baseline、低基数指标、owner 和 runbook 到位后再排期 |
| D3 | High-risk real execute | Deferred | 等真实 staging adapter、审批策略、补偿和 adapter audit 成熟后再打开 | 生产 execute 前必须有 approval、dry-run、idempotency、回滚/补偿策略 |
| D4 | [Adaptive normalization/parser evolution](../archive/ai_soc/deferred/adaptive-normalization-parser-evolution.md) | Deferred / production-data-dependent | 按真实 drift cohort 离线生成 parser/mapping/test 候选并治理发布 | 当前 deterministic parser/monitoring 已工作；不得逐告警调用 LLM 或自动改 Runtime |
| D5 | [Native Agent Tool Call + trusted target binding](../archive/ai_soc/deferred/native-agent-tool-call-and-target-binding.md) | Deferred / architecture hardening | 用 DeerFlow native structured tool 替换 Lead Agent 自定义 marker，并用 frozen-context typed refs 解析动作目标 | 当前 policy/approval 继续有效；在可信目标绑定完成前，不得仅凭模型自由 payload 开放无人值守高风险执行 |
| D6 | [Kafka worker pool / concurrency](../archive/ai_soc/deferred/kafka-worker-pool-concurrency-plan.md) | Deferred / production-metrics-dependent | commit tracker、worker result 和串行 runner 已完成；真实吞吐证明需要后再实现 bounded controller | poll/commit 仍归 controller；partition 顺序、背压、优雅退出和 LLM/DB 独立限流必须同时验收 |

## Phase 1 切片计划

| 序号 | 切片 | 状态 | 验收标准 |
|---|---|---|---|
| 1 | SOC Agent 代码落点确认与骨架创建 | Done | 明确包目录、CLI 接入方式、测试目录；新增空骨架不破坏现有测试 |
| 2 | contracts + core state | Done | 定义 `AlertInput`、`AnalysisResult`、`Decision`、`AnalysisRun`、`PipelineStepTrace` 等 schema/状态 |
| 3 | fixed Runtime pipeline | Done | `normalize -> entity_extract -> fact_reconstruct -> build_analysis_input -> skill_context -> analyze -> schema_validate -> evidence_grounding -> decide` 固定执行，LLM 不能跳步 |
| 4 | CLI `soc analyze` | Done | 能读取 JSON 文件/字符串，输出结构化 JSON 结果和 step trace |
| 5 | golden alert samples | Done for Phase 1 | 覆盖批准扫描器误报、恶意 IOC、低置信未知、字段缺失、嵌套坏 JSON accepted/rejected repair 和 schema drift |
| 6 | Phase 1 最小测试 | Done | 字段缺失不崩、输出过 schema/domain validation、每步有 trace、不执行自动处置；坏 JSON repair 有字段策略/domain guard 回归 |
| 7 | replay contract | Done | `AnalysisRun` 记录 input payload/hash；`SocAnalysisService.replay()` 通过 repository 生成新 run，不覆盖旧 run |
| 8 | PostgreSQL run repository | Done | SOC ORM row + SQLAlchemy repository + Alembic migration + headless CLI `show/replay` 已完成 |
| 9 | manual correction loop | Done | `soc correct RUN_ID` 更新 operational decision，保留原 AI verdict，追加 correction record，不自动写 confirmed memory |
| 10 | decision audit log | Done | `soc_decision_audit_log` 独立表记录 analyze/replay/correct 的结构化审计记录 |
| 11 | alert summary read model | Done | `soc_alert_summaries` 保存可查询摘要，analyze/replay/correct 通过 service 维护 summary |
| 12 | legacy platform normalizer | Done | 平安旧预警平台 envelope 转 canonical `AlertInput`；APT/EDR/HIDS raw message parser registry 可提取核心实体，完整 raw payload 保留 |
| 13 | CLI summary list | Done | `soc list` 输出持久化 `AlertSummary`，用于验证 Web/TUI 列表字段 |
| 14 | ZEUS evidence input policy | Done | 平安 ZEUS/天眼 parsed raw message 最高优先级；多 message 分 primary/supplementary；nested decode 失败保留原文，保守验收的 repair 进入独立 repaired projection，拒绝/失败时使用脱敏字符串 fallback |
| 15 | fact reconstruction layer | Done | `entity_extract` 后生成 `FactReconstructionResult`，记录字段可信度、角色候选和冲突报告 |
| 16 | LLM-ready analysis request | Done | `fact_reconstruct` 后生成 `LLMAnalysisRequest`，analyzer 消费有界 primary/supplementary evidence 内容和 compact coverage summary，不接收完整 vendor payload；完整 `EvidenceCoverageReport` 留作审计 |
| 17 | Prompt Builder + SOC prompt golden tests | Done | Prompt 只能从 `LLMAnalysisRequest` 生成；覆盖 PingAn APT/EDR、raw message 缺失 fallback、字段冲突；不把完整 raw payload 无脑塞进 prompt |
| 18 | LLM JSON parser + bad JSON repair | Done | 先严格 JSON parse，再 repair，再 Pydantic/domain validation；覆盖代码块、尾逗号、半截 JSON、字段类型错误 |
| 19 | 真实 LLM analyzer behind flag | Done | 已复用 DeerFlow `create_chat_model`；默认继续走 `analyze_stub`，显式 `SOC_ANALYZER_MODE=llm` 或 CLI flag 才调用模型；输出经过 prompt builder、JSON parser、schema/domain validation |
| 20 | Offline eval：stub / llm / replay diff | Done | 同一批样本比较 verdict、confidence、needs_review、parse success、冲突字段处理质量 |
| 21 | ReviewQueue API | Done | Gateway 暴露 review queue 列表、调查上下文、关闭、纠正接口；业务动作仍走 `SocReviewService` |
| 22 | ReviewQueue TUI thin client | Done | 基于 service/API 展示 open queue、打开 context、关闭 item、发起 correction；不复制业务逻辑 |
| 23 | SOC Agent chat stream contract | Done | `SocAgentChatService` 输出 DeerFlow-compatible stream event；可加载 ReviewQueue context；不调用 LLM、不替代 core service |
| 24 | SOC TUI chat runtime adapter | Done | 将 `SocAgentStreamEvent` 翻译成 DeerFlow TUI reducer actions；支持 `soc.review_context` custom event；保持纯函数、无 Textual/DB 依赖 |
| 25 | SOC Agent chat TUI workbench shell | Done | `soc chat tui` 启动 DeerFlow-aligned Textual chat workbench；支持普通消息和 `/open REV-...` context loading；业务仍走 `SocAgentChatService` |
| 26 | SOC Agent capability router MVP | Done | `SocAgentCapabilityRouter` 对 chat request 生成白名单 route decision；stream 发出 `soc.route_decision`；TUI 显示 allowed/denied |
| 27 | SOC Agent route -> service/action dispatcher | Done | `SocAgentActionDispatcher` 将 allowed route 映射为显式 service action result；stream 发出 `soc.action_result`；`review.open_context` 通过 `SocReviewService` 执行 |
| 28 | SOC Agent action permission / human approval | Done | `SocAgentActionPolicy` 在 action dispatch 前输出 permission decision；read-only 允许、analyst-write 需 analyst 角色、高风险要求人工审批且不执行 |
| 29 | SOC Agent approval request event | Done | 高风险 action 被拒绝时生成 `SocAgentApprovalRequest`；stream 发出 `soc.approval_request`；TUI 显示 pending approval request |
| 30 | SOC Agent approval grant token | Done | `SocAgentApprovalService` 将 pending approval request 转成一次性 `SocAgentApprovalGrant`；仅 `soc_approver`/`soc_admin` 可批准；仍不执行真实动作 |
| 31 | SOC Agent approval grant persistence / dry-run | Done | `approve()` 可保存 grant；`dry_run_approved_action()` 用 execution token 校验 route/action/expiry，只返回 dry-run result，不执行外部副作用 |
| 32 | ReviewQueue Web thin page | Done | Next.js 工作台新增 `/workspace/soc/review`，通过 Gateway ReviewQueue API 展示队列/上下文并提交 close/correct；前端不复制业务逻辑 |
| 33 | ReviewQueue Web actor/context headers | Done | Web 请求携带 surface/trace/idempotency；Gateway 用认证用户覆盖可伪造 actor header，并把 `surface=web` 写入 service context |
| 34 | approved-action consume/audit boundary | Done | `execute_approved_action()` 要求 `dry_run=False` + idempotency，消费一次性 token，记录 consumed/execution result payload；仍不执行外部副作用 |
| 35 | approval grant repository persistence | Done | 新增 `soc_approval_grants` 表和 SQLAlchemy repository 方法，持久化 approval grant approve/consume 状态 |
| 36 | approved action Gateway API | Done | 新增 `/api/soc/approvals/*`，支持 create grant、dry-run、execute；Gateway admin 映射为 `soc_admin` |
| 37 | approved action Web workbench | Done | ReviewQueue Web 页面新增审批动作面板，复用 Gateway API 完成 create grant、dry-run、execute 边界验证 |
| 38 | approval request inbox API | Done | 新增 `soc_approval_requests` 持久化表和 `/api/soc/approvals/requests` inbox API，供 Kafka daemon、Agent middleware、Web/TUI 共用 |
| 39 | approval inbox Web consumption | Done | Web 审批动作面板从 approval inbox 拉取 pending request，支持列表、详情、approve、dry-run、execute |
| 40 | Agent/daemon approval inbox write boundary | Done | `SocAgentChatService` 可持久化高风险 approval request；`SocDaemonService` 暴露同一 approval inbox 写入边界；真实 Kafka consumer / DeerFlow middleware 仍后续接入 |
| 41 | approval inbox TUI consumption | Done | `soc review tui` 展示 pending approval request，支持打开详情并 approve 生成 execution token；不执行真实动作 |
| 42 | TUI approved-action dry-run / execute command | Done | `soc review tui` 支持 dry-run token 校验和 execute boundary token 消费；execute 要求显式 idempotency key；仍不执行外部副作用 |
| 43 | Kafka daemon scaffold / approval request ingestion | Done | 新增 versioned daemon message contract、`SocDaemonService.process_message()` 和 `soc daemon process` 本地入口；支持 alert 分析与 approval_request 入箱；尚未连接 Kafka broker |
| 44 | SOC Lead Agent approval middleware | Done (`PI-01F`) | Web/Gateway `soc-triage` 通过 operator-owned per-agent middleware 进入统一 approval boundary；SOC TUI 保留既有外层 bridge；稳定 server IDs、最多 5 proposals、replay 幂等，高风险不自动执行 |
| 45 | Kafka consumer adapter planning | Done | 新增并归档 `.notes/archive/ai_soc/implementation-plans/kafka-consumer-adapter-plan.md`，明确 mapper/runner/offset/dead-letter/metrics 方案和下一刀 |
| 46 | Kafka record -> daemon message mapper | Done | 新增 `soc_agent.daemon.kafka_mapper`，纯 stdlib + contracts；支持 alert/approval topics、custom topic set、坏 JSON/未知 topic 错误 |
| 47 | Kafka consumer runner skeleton | Done | 新增 `SocKafkaConsumerRunner` 和 `KafkaConsumerPort`，串行 map -> process -> commit；mapper/service failure 进 dead-letter，仍不接真实 broker |
| 48 | Kafka consumer settings + null adapter | Done | 新增 `KafkaConsumerSettings` 环境变量配置 contract 和 `NullKafkaConsumerPort`；默认禁用、启用但无真实 adapter 时 fail-fast |
| 49 | `soc daemon consume` disabled wiring | Done | CLI 读取 `KafkaConsumerSettings` 并运行有限次 runner poll；默认 idle 输出 JSON，disabled path 不要求 DB/Kafka |
| 50 | Confluent Kafka broker adapter | Done | 新增 `backend[kafka]` optional extra 和 `ConfluentKafkaConsumerPort`；支持 subscribe/poll/manual commit/dead-letter produce+flush |
| 51 | Kafka smoke runner + live Redpanda smoke | Done | 新增 `backend/scripts/soc_kafka_smoke.py`，真实 Redpanda smoke 已验证 sample publish、daemon consume、summary、dead-letter、post-commit idle |
| 52 | Kafka bounded runner loop counters | Done | `SocKafkaConsumerRunner.run()` 下沉有限循环，返回 processed/dead_lettered/idle/committed counters；CLI 输出 counters，为后续 metrics/readiness 铺路 |
| 53 | Kafka daemon status/readiness contract | Done | 新增 `soc daemon status`，输出 versioned JSON；检查 database readiness，支持显式 `--check-broker` 轻量 broker poll |
| 54 | Kafka daemon long-running run loop | Done | 新增 `SocKafkaDaemonRunner` 和 `soc daemon run`；支持 SIGINT/SIGTERM graceful stop、idle sleep、bounded local validation 和结构化 run result |
| 55 | Kafka daemon metrics/backoff | Done | `soc daemon run` 输出 run metrics；adapter/runtime error 会 backoff，可配置连续错误阈值，避免故障热循环 |
| 56 | Kafka daemon production entrypoint / healthcheck | Done | 新增 `soc_daemon_entrypoint.sh`、`soc_daemon_healthcheck.sh` 和 production runbook；固定 env、healthcheck、日志采集和 Docker overlay 约定 |
| 57 | Kafka isolated run-mode smoke | Done | `soc_kafka_smoke.py --mode run` 使用隔离 topic 验证 `soc daemon run` 真实 broker 消费、commit、summary 和 dead-letter |
| 58 | Kafka daemon JSONL metric sink | Done | `soc daemon run --metric-jsonl stderr|stdout` 可持续输出 start/result/error/stop JSONL 事件；entrypoint 支持 `SOC_DAEMON_METRIC_JSONL` |
| 59 | Kafka daemon production compose overlay | Done | 新增 `docker-compose.soc-daemon.yaml`，显式 opt-in 启动 SOC daemon；默认不进入 DeerFlow 主 docker 流程 |
| 60 | Kafka daemon Dockerfile multi-extra support | Done | `backend/Dockerfile` 支持 comma/whitespace 分隔 `UV_EXTRAS`；SOC daemon overlay 默认 `postgres,kafka` |
| 61 | Kafka daemon K8s deployment contract | Done | 新增 opt-in K8s template，固定 ConfigMap/Secret/probes/resources/logging 标签；Compose 与 K8s 配置等价关系写入 runbook |
| 62 | Kafka worker pool / concurrency planning | Done | 新增并发规划文档，明确 poller ownership、partition-aware commit、bounded in-flight、幂等前置和 LLM 独立限流 |
| 63 | Kafka partition commit tracker | Done | 新增纯内存 `PartitionCommitTracker`，锁定乱序完成、dead-letter pending、多 partition 和已提交边界的 commit 推进规则 |
| 64 | Kafka daemon idempotency hardening | Done | `SocAnalysisService` 通过 audit idempotency key 复用既有 run，避免同一 Kafka offset 重放重复写 summary/review/audit |
| 65 | Kafka WorkerPoolResult contract | Done | 新增 `KafkaWorkerResult` / `SocKafkaWorker`，worker 只返回 processed/dead_letter_required/retryable/fatal 结构化结果；不 commit、不 dead-letter、不启动并发 |
| 66 | SocSkillResolver + SOC Lead Agent MVP | Done | 复用 DeerFlow custom-agent/profile/skills 机制；按 source/detection/entities/conflict 选择 SOC domain skills；新增只读 CLI `soc agent profile` / `soc agent resolve-skills` |
| 67 | Skill-selected bounded context for analysis/chat | Done | `LLMAnalysisRequest.skill_context`、PromptBuilder、LLM metadata、ReviewContext chat stream 和 TUI translate 已接入 compact skill context；记录 skill/hash/token budget；不让 LLM 动态加载未知 skill |
| 68 | SOC Lead Agent DeerFlow profile installation path | Done | 新增 `soc agent install-profile`，把推荐 profile 写入 DeerFlow per-user custom-agent storage；默认 dry-run/skip 安全语义，`--overwrite` 才覆盖 |
| 69 | SOC Lead Agent chat entry wiring | Done | 新增 `SocLeadAgentChatService`，通过 DeerFlowClient `agent_name=soc-triage` 进入现有 lead_agent；`soc chat tui --lead-agent` 可选启用 |
| 70 | SOC Lead Agent review context bridge | Done | 将 ReviewQueue context 以 bounded context/artifact 形式提供给 DeerFlow SOC Lead Agent；不让 Lead Agent 直接读 repository 或执行处置 |
| 71 | SOC Lead Agent action proposal boundary | Done | 约束 Lead Agent 后续如何输出结构化 action proposal；仍不直接执行 MCP/tool/处置动作，必须回到 policy/approval/service 边界 |
| 72 | Approval inbox proposal payload rendering | Done | Web/TUI 审批入口展示 `source_proposal_id`、`action_payload`、`context_refs`，让分析师审批前能看见 Lead Agent 候选动作来源和参数 |
| 73 | Action adapter registry contract planning | Done | 规划真实 `response.block_ip` / `endpoint.isolate_host` / MCP tool adapter registry 的 contract、幂等、审计和 dry-run 要求；新增 registry/descriptor/protocol/dry-run-only adapter，不直接接生产动作 |
| 74 | Approval service adapter dry-run integration | Done | `SocAgentApprovalService.dry_run_approved_action()` 在 token 校验后可选调用 action adapter registry dry-run，校验 allowlist、payload 和 context refs；默认仍兼容无 registry 的 token-only dry-run |
| 75 | Execute adapter preflight before token consume | Done | `execute_approved_action()` 在消费 token 前可选校验 adapter 存在性、execute 支持度、payload 和 context refs；仍不接生产副作用 |
| 76 | First concrete safe read-only adapter | Done | 先接资产归属只读 adapter，验证 descriptor、dry-run、execute preflight 与审计 payload；不接封禁/隔离等写动作 |
| 77 | Read-only adapter dispatcher / tool gateway wiring | Done | 明确 `asset.lookup` 如何通过受控 route/tool gateway 进入运行态；默认不加入 chat router 白名单；结果必须写入 action result / audit payload |
| 78 | SOC Lead Agent read-only tool proposal bridge | Done | Lead Agent 只能通过结构化 envelope 请求 `asset.lookup` 等只读能力；bridge 转成同一条 router/policy/dispatcher/registry 链路；不直接调用 adapter/MCP |
| 79 | MCP adapter bridge / real read-only data source planning | Done | 规划真实资产、TI、安全标签或其他已确认 read-only tool 如何通过 adapter descriptor 接入；write/destructive 仍走 approval |
| 80 | MCP tool provider port + fake provider adapter tests | Done | 定义 SOC MCP provider port、fake provider 和 read-only MCP adapter skeleton；不接真实 MCP server |
| 81 | MCP-backed read-only `asset.lookup` adapter config builder | Done | 用 fake provider 固定显式配置到 MCP-backed `asset.lookup` adapter registry 的构造方式；不接真实 MCP server |
| 82 | SOC action package structure hygiene | Done | 将 action adapter、proposal、MCP adapter 收口到 `backend/soc_agent/actions/`，删除根目录旧入口；架构测试防止继续往根目录新增 action-like 模块 |
| 83 | DeerFlow cached MCP provider implementation | Done | 复用 DeerFlow MCP cache/session 生命周期，实现 `SocMcpToolProviderPort`；仍不让 Lead Agent 直接调用任意 MCP tool |
| 84 | Read-only config smoke wiring | Done | 支持 JSON/YAML 显式 adapter config 加载，`soc mcp smoke` 可验证 config -> registry -> DeerFlow cached provider -> action result |
| 85 | Dev/staging read-only MCP smoke report contract | Done | `soc mcp smoke` 输出 versioned report，记录 latency、failure、payload size、result size、tool/config 和 output_fields 裁剪信息 |
| 86 | MCP smoke readiness inventory | Done | `soc mcp tools` 可安全列出 DeerFlow cached MCP tools，`soc mcp smoke/tools --report-path` 可落盘报告；无 MCP config 时 tool_count=0 |
| 87 | Local real MCP fixture and read-only smoke | Done | 本地 stdio MCP server + sample extensions/action config 可验证 `soc_dev_asset_lookup` discovery 和 `asset.lookup` execute smoke |
| 88 | Real dev/staging MCP replacement | In Progress / PI-01 | 先接真实 PingAn 资产、TI、安全标签 DEV Provider，再替换对应 fixture、保存 smoke report 并评估延迟、失败率、字段裁剪和接入风险 |
| 89 | Upstream MCP sync compatibility retest | Done | 同步 upstream/main 后，SOC MCP adapter 显式传递 `mcp.server`，并重新验证 DeerFlow MCP 前缀重叠路由、local stdio discovery 和 `asset.lookup` execute smoke |
| 90 | Asset extraction skill + asset.locate MCP mock | Done | 根据资产提取/定位原型，新增 `soc-asset-extraction` skill、`asset.locate` read-only policy、mock MCP tool/config，并让 Lead Agent proposal bridge 可通过 MCP-backed adapter 执行只读定位 |
| 91 | Read-only action result evidence bridge | Done | 新增 `InvestigationEvidence` contract、repository protocol、in-memory store；read-only action success 后可记录 evidence，ReviewQueue context / Lead Agent artifact / Web/TUI 可展示 |
| 92 | InvestigationEvidence PostgreSQL persistence / Gateway wiring | Done | 新增 `soc_investigation_evidence` migration、ORM row、SQLAlchemy repository 方法；Gateway/CLI ReviewService 和 Lead Agent read-only dispatcher 使用同一 repository 共享 evidence |
| 93 | Lead Agent evidence reuse + endpoint process-tree mock adapter | Removed / superseded 2026-08-03 | evidence reuse 保留；未经证实且高耗时的进程树查询 mock、policy 和 proposal 已删除，改用告警原生 bounded evidence |
| 94 | PingAn SOC capability onboarding | Done | 新增 `.notes/ai_soc/capabilities/pingan/onboarding.md`，固定经验 -> capability card -> skill/MCP/normalizer/domain/eval/memory 的转化流程 |
| 95 | Correlation Service + Unified Report Bridge | Done | 结构化 correlation service/CLI 基于 summary + evidence 找历史 match；typed result 已进入 main report/domain/review summary；不调用 LLM、不改 Runtime decision |
| 96 | External Disposition Sync Contract MVP | Done | 固定外部预警/工单/处置系统状态与理由同步协议；新增 mapper/service/repository MVP，Zeus 只是 mock fixture |
| 100 | External Disposition Review/Correction Integration | Done | 高可信 mapped external disposition 在唯一定位本地 target 后复用 `SocReviewService.correct()`，同步 operational correction 并关闭 review queue；低可信/未知/无法定位不改判 |
| 97 | Memory Tracking Contract | Partial | `SocMemoryCandidate` 已完成 DB/API/ReviewQueue visibility、review workflow 与 governed retrieval；PI-03F1/F2 只在分析师显式采纳后保存 review-note candidate；PI-03F3 已完成 Kafka/batch typed aggregate source；PI-01F2 已完成 Web queue-grounded Lead Agent context。固定 Runtime analyzer 的 memory PromptBuilder injection 未开启；wiki/OKF 后期只做 projection |
| 98 | PingAn Domain Triage MVP | Done | 新增 `SocDomainTriageService` 和 APT/EDR/HIDS deterministic handlers；`soc eval pingan-domain` 可验证三类样本输出 domain findings、capability card refs 和 evidence refs |
| 99 | PingAn Main Orchestrator Demo | Done | `soc eval pingan-main` 验证 APT/EDR/HIDS historical + current analyze -> correlation -> skill -> read-only evidence -> domain finding -> review summary |
| 101 | Phase 2 Correlation Eval Baseline | Done | 新增版本化 scorer ID、same/related/unrelated pair corpus、双任务 precision/recall、reason/fan-out/evidence 报告和 replay diff；不启用 dedup suppression |
