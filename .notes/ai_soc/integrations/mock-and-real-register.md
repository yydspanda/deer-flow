# SOC Agent Mock 与真实接入台账

> Updated: 2026-08-09
>
> 目的：集中记录当前 SOC Agent 里哪些能力只是 mock、fixture、in-memory 或本地 smoke，用来验证工程链路；后续接入真实 PingAn / 客户环境时，必须按本台账替换、复测和重新验收。

## 1. 总原则

- Mock 只能证明协议、服务边界、展示链路和回归测试能跑通，不能证明生产系统已接入。
- `PA-12` 真实 PingAn MCP/API 替换当前为 `In Progress / internal smoke`：DEV profile、portable signer、preflight 和 direct smoke entry 已实现，但在产生内网 `mocked=false` 证据前仍不能算完成；本地 mock、fake fixture、in-memory adapter 不能冒充完成。
- Read-only mock 的成功结果可以写入 `InvestigationEvidence` 用于 demo/eval，但必须带 `mocked=true`、fixture source 或 adapter id，不能当作生产事实，不能满足场景证据要求，也不能提高 domain/scenario confidence。
- 外部 free-text reason、LLM 总结、分析师备注、mock tool result 都只能生成 `SocMemoryCandidate(status=pending_review)`；不能直接写 confirmed memory 或 active lesson。
- 真实替换时只能替换 adapter/provider/config，不得改变 core service contract、Main Orchestrator contract、ReviewQueue contract 或 Lead Agent bounded context contract。
- 每个 mock 替换成真实系统前，必须至少补齐：endpoint/config、认证方式、字段裁剪、敏感信息处理、超时/重试、失败返回、latency、payload/result size、smoke report、rollback 方案。
- **外网仿真先行**：后续任何需要进入内网才能完成的已确认能力，必须先在外网使用同一 production Provider/MCP/action 代码与显式 fake transport 跑通配置校验、成功/查无/失败矩阵、持久化、回放、报告和零副作用门禁。进入内网只允许注入 endpoint/secret/批准样本并切换为 `internal`/`mocked=false`，不能现场补核心代码或临时发明验收规则。
- 外网仿真与内网真实报告是两个不能互换的 evidence class。仿真报告必须写 `mocked=true`、`evidence_class=simulated`、`real_provider_evidence=false`；它是内网执行的前置门槛，但永远不能关闭真实 Provider gate。
- 产品完成状态和真实接入状态是两个独立轴。一个已冻结 contract 的 external simulation 通过后，相关产品流程可以继续到评测、运营和治理切片；对应 `mocked=false` gate 继续登记为 `Real Integration Debt`，不得把整个项目卡在等待内网，也不得把 simulation 状态重命名为 real。
- 如果能力本身、wire contract 或权威来源尚未确认，则继续标记 `data-gated`，只能仿真已经冻结的 vendor-neutral ingress/contract，不能为了满足“先 mock”而虚构不存在的 endpoint、字段或 Provider。已删除的 EDR 进程树/HIDS 上下文查询不得恢复。

## 2. 当前 Mock / Fixture 清单

| 能力 | 当前实现 | 位置 | 当前用途 | 后续真实替换 |
|---|---|---|---|---|
| `asset.lookup` | in-memory read-only adapter，部分 smoke 可走 MCP-backed config；PingAn PI-01E 已禁用 | `backend/soc_agent/actions/adapters.py`、`backend/soc_agent/actions/mcp.py` | 验证“简单资产记录查询”的 action contract、policy、approval preflight、evidence 写入；它不等同于 ownership-oriented `asset.locate` | PingAn real-only 示例选择 `asset.locate`，paired evaluator 将 `asset.lookup` 视为 blocking failure；其他 tenant 若保留，必须提供独立真实 adapter/result schema |
| `asset.locate` | 本地 stdio MCP mock tool | `backend/scripts/soc_dev_mcp_server.py`、`backend/samples/mcp/` | 模拟 Zeus/CMDB/asset_to_bu 归属定位，验证 Lead Agent proposal -> MCP adapter -> evidence | 替换为真实资产归属/BU/owner/处置归属服务；保存 `soc.mcp_action_smoke_report.v1` |
| `D12-A` PingAn asset provider | **Implemented production-shaped code with fake transport; still mock** | `backend/soc_agent/integrations/pingan/`、`backend/scripts/soc_pingan_asset_mcp_server.py`、`backend/samples/mcp/pingan_asset/` | 外网验证 ZEUS 签名、Agent Platform auth/create/poll、`searchAssetInfo -> asset_to_bu -> UM` 降级编排、MCP/action 映射和 fail-closed；产物明确 `mocked=true` | 只有 `D12-B` 内网注入真实 endpoint/secret/approved cases 并产生 `mocked=false` smoke 证据后才算 real；D12-A 不能关闭 `PA-12` / `PI-01` |
| `D12-B` PingAn internal validation | **Code/config/offline toolchain/acceptance runners prepared; real evidence pending** | `backend/samples/pingan_dev/`、`backend/scripts/soc_pingan_dev_preflight.py`、`backend/scripts/soc_pingan_asset_direct_smoke.py`、`backend/scripts/soc_pingan_d12b_matrix.py`、`backend/scripts/soc_pingan_d12b_evidence.py` | 真实值保存在 Git-ignored local profile/`0600` case file；Agent Platform client 不依赖旧 Python 包；Apple Silicon backend 可离线安装；plan 不发请求，live 必须显式确认；aggregate report 不含 raw query/UM/Provider body/override value；evidence runner 经 MCP/Dispatcher 持久化并回读共享 Review/Lead Agent context，检查基础 Run/Review 不变 | 内网提供环境对应的 ZEUS/Agent Platform endpoint、credential、operator 和 approved cases，通过 direct + MCP + persisted evidence/readback；至少一项 `mocked=false` 且 deployed Web/TUI smoke 通过后才改变 real-provider 状态 |
| Automatic read-only investigation orchestration | **D1-D4 deterministic production code; not a mock** | `backend/soc_agent/contracts/enrichment.py`、`backend/soc_agent/core/enrichment.py`、`backend/soc_agent/core/investigation_workflow.py`、`backend/soc_agent/contracts/investigation_reporting.py`、`backend/soc_agent/core/investigation_reporting.py`、`backend/soc_agent/application/enrichment.py`、migration `0019_enrichment_executions` | 默认关闭；typed plan 经同一 Dispatcher/Registry；execution/attempt/evidence 可持久化；实际 `mocked` 在 evidence 前校验；Kafka/internal batch 仅显式 opt-in；幂等、failure/not-found、bounded retry、stale recovery、linked replay 和无 Provider 调用的 report/addendum 投影已实现 | PI-01E 用真实内网配置收集 shadow 证据。真实 Provider 仍须各自 `mocked=false` 验收；orchestration/reporting 完成不等于 PingAn asset/TI/tag 已真实接通，且任何阶段都不改基础 verdict/close/memory/automation |
| PI-01E external simulated shadow | **Simulation Done at 5 and 50 rows; Real Debt Open** | `backend/samples/enrichment/pingan-external-simulation.yaml`、`backend/samples/mcp/pingan_shadow/extensions.simulated.json`、Git-ignored `backend/.deer-flow/soc-internal-validation/external-simulation/pi-01e-20260805-50-v2/` | 50 条同 cohort live DeepSeek Runtime-only 与 persisted investigation 均完成；`asset.locate`/`security_tag.lookup` 共 157 次 fake MCP 调用并持久化 157 条 `mocked=true` evidence，0 failure/missing evidence/越权副作用，paired gate passed。结果全部为正常 not-found，报告显式警告未覆盖 Provider hit path。该 simulation gate 已允许产品轨继续 PI-03 | `real_provider_evidence=false`、`closes_real_provider_gate=false`。内网 `internal_real` 5 及 D12-B/PI-01A/B1 的真实 hit/not-found/error gate 作为独立债务保留 |
| PI-01E paired shadow evaluator | **Deterministic dual-mode validation code; not a Provider** | `validation/compact_zeus/internal_batch/evaluate_pingan_shadow.py` | `external_simulation` 严格要求 mock composition/fake MCP/全部 `mocked=true`；`internal_real` 严格要求 real composition/internal MCP/全部 `mocked=false`。两者共同核对 cohort、tenant、composition/action/extensions 指纹、pre-LLM compatibility、P95/review/schema/measurement gaps 与零越权副作用 | 仍需内网真实 5/50/all 产物；仿真 pass 不证明模型准确、不自动扩容、不关闭 D12-B/PI-01A/B1 gate |
| `threat_intel.ip_reputation.lookup` | **PingAn production-shaped Provider/MCP 已实现；默认 registry 仍有 in-memory fallback，真实内网证据待补** | `backend/soc_agent/integrations/pingan/threat_intel.py`、`backend/scripts/soc_pingan_threat_intel_mcp_server.py`、`backend/samples/mcp/pingan_threat_intel/` | 外网验证 ZEUS wire contract、bounded mapping、freshness/lineage、MCP/action/persistence；fake 明确 `mocked=true` | `PI-01A` 在 DEV 跑 hit/not-found/error/timeout 和实际字段 coverage，保存 `mocked=false` evidence；PI-01D/PI-01E 配置该 MCP adapter 后禁用默认 in-memory fallback |
| `security_tag.lookup` | **PingAn production-shaped Provider/MCP 已实现；默认 registry 仍有 in-memory fallback，真实内网证据待补** | `backend/soc_agent/integrations/pingan/security_tag.py`、`backend/scripts/soc_pingan_security_tag_mcp_server.py`、`backend/samples/mcp/pingan_security_tag/` | 外网验证 ZEUS wire contract、validity/scope/status、MCP/action/persistence；过期/失效/冲突不丢失，fake 明确 `mocked=true` | `PI-01B1` 在 DEV 核对对象类型、`expireTime`/永久有效语义并跑 exact-hit/expired/not-found/error，保存 `mocked=false` evidence；标签查询本身不等于 PI-01B2 权威授权事实已同步 |
| Authorized-activity source facts | GF-01 lifecycle/DB 与 AA-01 matcher 是真实确定性实现；当前 HIDS/EDR shadow facts 由已确认业务真值构造为本地 in-memory fixture | `backend/soc_agent/contracts/governed_context.py`、`backend/soc_agent/authorization/`、gitignored `step-12-authorization-shadow/` | 验证 event-time lifecycle/scope/freshness/recurrence 和 exact explanation；不代表已接变更/扫描器/维护系统 | `PI-01B2` 接真实 change/scanner/maintenance/exercise-roster/CMDB source adapter，同步 source ref/version/scope/freshness 后重跑 shadow；不得把 B1 标签或 validation fixture 当生产 active fact |
| PingAn eval fixtures | 脱敏/伪造 APT、EDR、HIDS 回归样本 | `backend/samples/eval/pingan/`、`backend/samples/alerts/pingan_legacy_hids.json` | 验证 normalizer、read-only action、domain triage、main orchestrator demo | 补充经批准的脱敏真实样本、schema drift 样本、反例和边界样本 |
| PI-03A label corpus | **Simulation governance implemented; labels remain pending** | `backend/soc_agent/eval/labels.py`、`soc eval labels prepare|seal|verify`、Git-ignored `backend/.deer-flow/soc-internal-validation/pi-03a-simulation/` | 以 5 条 live-LLM Runtime 产物验证 immutable manifest、payload/sample hash、review summary、来源、理由和 supersession；报告固定 `mocked=true`、`real_quality_claim_allowed=false` | 真实质量评测需经批准的脱敏真实数据、具名 reviewer/rationale 和完成审阅的 label set；simulation corpus 不得与真实 corpus 共用 supersession chain |
| PI-03B quality evaluation | **Simulation Done / real quality debt open** | `backend/soc_agent/eval/quality.py`、`soc eval quality`、`backend/samples/eval/confidence/`、Git-ignored `backend/.deer-flow/soc-internal-validation/pi-03b-simulation/` | 用 4 条明确 `simulation_fixture` label 组合 offline Runtime、scenario、correlation 和 manifest-bound calibration；8 alert 工程 gate passed，二次 replay `changed=false` | Synthetic accuracy/Brier、fixture taxonomy coverage 和 correlation metrics 不是生产质量。真实 corpus 需 `human_review` 来源；当前固定禁止 profile publish、rollout、automation 和 real-quality claim |
| PI-03C Skill improvement backlog | **Simulation Done / real feedback classifier debt open** | `backend/soc_agent/contracts/skill_improvement.py`、`backend/soc_agent/core/skill_improvement.py`、migration `0020_skill_improvement_backlog`、`soc skill-improvement *`、`backend/samples/eval/skill_improvement/` | 4 条 synthetic typed feedback 验证 distinct-source threshold、Skill package hash linkage、SQL/RBAC/audit/state machine/freeze 和 aggregation replay；报告固定 `mocked=true` 且所有 Skill mutation/activation/memory/runtime/quality 权限为 false | 真实 correction/external disposition reason 必须经 server-owned classifier 产生 tenant、目标 Skill/version、scenario 和 failure facet；不能按 reason 文本或 LLM embedding 自动聚类，也不能以 simulation 关闭真实反馈 gate |
| PI-04B Operations Web | **Real thin consumer / local fixture evidence** | `frontend/src/app/workspace/soc/operations/`、`frontend/src/components/workspace/soc/soc-operations-overview.tsx`、`frontend/src/core/soc/`、`frontend/tests/e2e/soc-review.spec.ts` | 只读消费 `soc.operations_snapshot.v1`，展示 SQLite local/test nature、server-owned exact counts 和 `not_measured`；30 秒刷新、错误态、桌面/移动无溢出与截图已通过 | Playwright fixture 不是 deployed Gateway/auth/production telemetry。真实 Kafka lag、模型算力、Provider telemetry、Prometheus/SLO 与环境 owner 审批仍开放 |
| PI-04C Effectiveness and Rule guidance | **Product-shaped read model implemented / real labels and telemetry open** | `backend/soc_agent/contracts/effectiveness.py`、`backend/soc_agent/core/effectiveness.py`、`backend/soc_agent/db/effectiveness.py`、migration `0026`、`/api/soc/effectiveness/snapshot`、Operations Web | 最新 Run 去重、最终真值分母、准确率/漏报/转交/自动忽略、检测族质量、模型 usage 与 Memory 反例聚合均由服务端完成；无标签显示 `not_measured`，Rule 建议固定 `advisory` | 当前 SQLite/fixture 只能验证计算和交互。真实 Zeus/运营最终状态、历史 usage 覆盖、生产 Prometheus/SLO 和规则版本 before/after 或 A/B 证据归 `PI-04D` |
| PI-05A rollout rehearsal | **Simulation Done / real rollout gate open** | `backend/soc_agent/contracts/rollout.py`、`backend/soc_agent/core/rollout.py`、`soc rollout rehearse`、`backend/samples/rollout/`、Git-ignored `backend/.deer-flow/soc-internal-validation/pi-05a-simulation/` | 16-step virtual flow、5 owner roles、7 real gates、6-step rollback 与 stable semantic replay 通过；报告固定 `mocked=true`、0 real transition、0 external effect，且不调用 Provider/broker/DB mutation/feature flag/Zeus/action | 不证明真实 owner、deployed cohort enforcement、PI-01 Provider、PI-02 infrastructure、PI-03 quality、PI-04 SLO 或 rollback。真实 rollout controller/approval/audit 和环境演练归 PI-05C；simulation 永远不能关闭这些 gate |
| PI-05B simulation completion | **Simulation Done / all real gates open** | `backend/soc_agent/eval/completion.py`、`soc rollout completion`、`backend/samples/rollout/pi05b_local_simulation.json`、Git-ignored `backend/.deer-flow/soc-internal-validation/pi-05b-simulation/` | 六个现有 artifact 汇总为五个 typed component；校验 provenance/hash/replay/claim boundary，缺失/坏 artifact 或仿真越权声明 fail closed。本地 `SCG-6EEDC5DC3417` 五组件 passed、replay unchanged | 只允许声明本地产品仿真轨完整；固定 `mocked=true`、real stage `not_started`、`pilot_ready=false`、`production_ready=false`。PI-01..05 七项真实 gate 均登记为 open Real Integration Debt |
| External Disposition Zeus fixture | Zeus 状态/理由 mock payload；canonical Gateway ingress 和 SQL/service 是真实实现 | `backend/samples/external_disposition/zeus_status_update.json`、`backend/app/gateway/routers/soc_external_dispositions.py` | fixture 验证 field-path mapper；authenticated ingress 验证 canonical command、RBAC、idempotency、review/correction | 替换 fixture/source side 为真实 webhook/Kafka/poll adapter；补签名、租户、重放和脱敏；继续复用现有 ingress/service |
| Kafka local smoke | 本地 Redpanda/Kafka topic、strict `SocAlertRawEnvelope` sample、dead-letter smoke | `backend/scripts/soc_kafka_smoke.py`、`backend/soc_agent/daemon/` | 验证 strict envelope、raw preservation、consumer runner、commit、dead-letter、status/readiness | 替换为真实 topic、ACL、consumer group、DLQ、监控、容量与失败演练 |
| Alpha Review Web browser fixture | Chromium 渲染真实 SOC React 页面，HTTP transport 由 deterministic Playwright route fixture 提供 | `frontend/tests/e2e/soc-review.spec.ts`、`frontend/tests/e2e/utils/mock-soc-api.ts` | 回归 queue/context、close/correct、approval、memory、sample outcome 和 normalization 交互及 request contract | 部署环境验收必须再走真实 Gateway/auth/network；浏览器 fixture 不能证明后端或生产链路 |
| 高风险响应动作 | 当前只有 proposal、policy、approval、一次性 grant、dry-run/execute preflight；`external_side_effect=not_executed` | `backend/soc_agent/actions/adapters.py`、`backend/soc_agent/core/service.py` | 验证封禁 IP、隔离主机等动作在执行前的权限、审批、幂等和审计边界 | 接入真实 EDR/F5/SOAR/防火墙 adapter；必须补回滚/补偿、执行结果核验和失败重试，默认仍需人工审批 |
| LLM analyzer | **真实路径已完成**：默认 deterministic stub；显式模式复用 DeerFlow `create_chat_model` | `backend/soc_agent/llm/`、`backend/soc_agent/core/runtime.py` | stub 保证回归/回放；`SOC_ANALYZER_MODE=llm` 或 CLI flag 调用已注册模型；有独立 concurrency/RPM admission、输出上限、evidence grounding、typed failure；raw confidence 当前均标记为 uncalibrated 并进入复核 | 持续补人工标注集、离线校准和成本预算；真实输出仍需 JSON/schema/domain/grounding validation 和 `SocDecisionPolicy` |
| Normalization suggestion | **真实路径已完成**：deterministic/replay/live LLM 三种离线模式 | `backend/soc_agent/normalizers/suggestions.py` | 发现 mapping 候选并严格校验 observed source path / canonical whitelist | 所有建议仍需工程师复核，`auto_apply_allowed=false` |
| SQL/in-memory repositories in tests | in-memory repository 或 SQLite 单元测试 | `backend/soc_agent/*/repository.py`、`backend/tests/` | 单元测试和无 DB 局部 wiring；PingAn 内网 DEV 在 DeerFlow `database.backend: sqlite` 下自动使用 `{database.sqlite_dir}/soc_agent_dev.db` | 生产/准生产必须走 PostgreSQL migration + SQLAlchemy repository；当前 DEV 不收集 PostgreSQL 参数 |

### 2.1 容易被误判为 Mock、但已经是真实实现的部分

| 能力 | 当前性质 | 说明 |
|---|---|---|
| Normalizer / message parser / entity extraction / fact reconstruction | deterministic production code | 它们是 Runtime 的确定性处理节点，不调用模型不等于 mock；格式漂移通过 monitoring/maintenance issue 暴露 |
| `StubLLMAnalyzer` | 显式 fallback/test mode | 用于回归、replay 基线和无网络运行；`llm` 模式已经能调用真实 DeerFlow 模型，两者共享同一 Runtime contract |
| `NullKafkaConsumerPort` | disabled-mode adapter | `SOC_KAFKA_ENABLED=false` 时明确不连接 broker；启用后使用 `ConfluentKafkaConsumerPort`，不是用 null adapter 冒充消费成功 |
| SOC SQLite | 本地真实持久化 | 本地开发可以真实保存 SOC 数据；生产/准生产目标仍是 PostgreSQL，不应把 SQLite 测试结果当生产验收 |
| SOC Lead Agent | DeerFlow 真实 agent path | 复用 DeerFlow `lead_agent`、profile、skills 和 MCP；mock 的是部分外部查询结果，不是 Lead Agent 运行时本身 |
| SOC specialist subagents | DeerFlow 真实 custom-subagent path | `PI-01G` 复用原生 registry/`task`/model/task events；profile 本身 `tools=[]`/`skills=[]`，只接收服务端投影的 bounded case 和已评审 Skill guidance。专家文本是 advisory reasoning，不是 mock tool result，也不是 evidence/verdict。NIDS/EDR `deepseek-v4-flash` smoke 已通过，但其 fake Provider evidence 仍保留 `mocked=true` 且 `provider_acceptance_claimed=false` |
| GF-01 / AA-01 | deterministic production contracts/services | Fact lifecycle、历史版本选择和 matcher 不是 mock；EX/DP/EV persistence/evaluation 已实现，当前缺口是权威事实来源同步和 governed rollout |
| External disposition canonical ingress | authenticated application boundary | Gateway route、SQL repository、transactional service、RBAC 和 exact-retry/conflict 语义是真实实现；mock/data-gated 的是 Zeus/ITSM/SOAR source feed、签名和凭证 |
| PingAn historical software-path catalog | deterministic local compiler + read-only MCP/action | 真实编译旧 XLSX 并精确查询版本化 SQLite；不是 mock，也不是权威 allowlist。catalog/MCP 输出固定为 investigation-only、decision impact none；只有另行显式开启的 server-owned PingAn fast-disposition policy 可以基于 exact/受控 family signal 形成 `ignored`，两条权限边界不得混用 |
| Alpha acceptance orchestrator | real local/test acceptance code | `scripts/soc-alpha-acceptance.sh` 调真实 CLI/service/SQL/Kafka/browser test 并生成版本化报告；其中 analyzer/provider/browser transport/基础设施性质由报告逐项披露，不因总状态 passed 而变成 production real |

### 2.2 Runtime heuristic / LLM replacement audit

| Runtime component | Current nature | 是否用 LLM 替换 | 结论 |
|---|---|---|---|
| Normalizer / message parser | 确定性生产代码 | No | LLM 只可离线建议 mapping；生产解析必须可回放、可监控 |
| Entity extraction | 确定性基础抽取 | Not as replacement | 后续可增加 bounded LLM enrichment，但原始 extractor 保留为基线和 provenance 来源 |
| Fact reconstruction / scenario hypothesis | 版本化 deterministic heuristic，当前未校准 | Not as controller | LLM 可补候选场景/解释，不能覆盖 role conflict、evidence trust 或 response-target guard |
| `StubLLMAnalyzer` | 显式 fallback/test/replay baseline | Production uses `llm` explicitly | 不删除；生产入口必须显式配置 `SOC_ANALYZER_MODE=llm`，且 trace 标明 analyzer |
| `JsonLLMAnalyzer` | 真实 DeerFlow model path | Already real | 模型自报 confidence 仍不是 calibrated probability |
| `SocDecisionPolicy` | 确定性生产决策策略 | No | LLM 不决定 `needs_review`；策略统一处理 provenance、evidence guard、review reasons 和 version |
| Evidence grounding / Runtime failure / atomic bundle | 确定性生产防护 | No | 模型证据必须回指 bounded context；run/summary/review/audit 同事务；retryable failure 不 commit Kafka offset |
| Domain/scenario finding confidence | 可回放 heuristic score | No direct replacement | 只用于 finding 排序/解释；mock/failed evidence 不得抬分，后续用标注集评测版本常量 |
| Correlation / memory retrieval score | 确定性检索分数 | No direct replacement | 后续 LLM 只能 bounded rerank，不能扩大查询或直接生效 memory |
| Asset/TI/security-tag results | 当前部分为 mock external facts | **Must replace with real provider** | 这是当前真实缺口；不能让 LLM 生成或猜测外部事实；进程/主机上下文直接使用告警原生证据 |
| Main orchestrator demo | 可重复 MVP 编排 | Evolve through services/Lead Agent | demo 不是生产自主 Agent；生产入口继续复用同一 Runtime/service/policy 边界 |

## 3. PA-12 的真实完成标准

`PA-12` 不等于“mock adapter 都写完”。只有满足下面条件，才能从 `In Progress` 改成 `Done`：

1. 拿到真实 dev/staging endpoint、MCP server 或 API adapter 配置，不把 secret 写入仓库。
2. 每个真实 provider 都通过 `SocActionAdapterRegistry` 或 MCP-backed adapter 显式注册，不能让 Lead Agent 自由调用任意 MCP tool。
3. 跑 `soc mcp tools` / `soc mcp smoke` 或对应 adapter smoke，保存结构化报告，报告不提交敏感 payload。
4. 记录 latency、failure rate、timeout、payload size、result size、字段裁剪、敏感字段、空结果、权限失败和限流行为。
5. 真实结果只作为 `InvestigationEvidence` 进入 ReviewQueue / Lead Agent context；不能直接改 verdict、memory 或处置状态。
6. 对每个真实接入补至少一条脱敏回归样本，覆盖成功、查不到、权限失败或超时中的至少两类情况。

### 3.1 当前不能由 LLM 替代的外部事实能力

下列能力仍是 credential-gated，不属于本轮 LLM 接入遗漏：

- `asset.lookup` / `asset.locate`：需要 CMDB、Zeus 或资产服务 endpoint。
- `threat_intel.ip_reputation.lookup`：需要企业或外部 TI provider。
- `security_tag.lookup`：需要白名单、演练、变更、维护窗口等权威数据源。
- `authorized_activity` source sync：需要 change/scanner/maintenance/CMDB 的事实来源、版本和 freshness；
  当前 step-12 人工构造事实只用于 shadow replay。
- External Disposition Zeus fixture：需要 webhook、Kafka 或 polling 接入参数。
- 高风险响应动作：需要 EDR/F5/SOAR/防火墙 staging endpoint、审批策略和回滚/补偿能力。

大模型可以建议“应查询什么”，但不能虚构这些系统的查询结果。真实 endpoint/凭证到位后，只替换
adapter/provider/config，并继续将结果作为 `InvestigationEvidence` 回流。

还要区分 **provider readiness** 和 **workflow reachability**：D12-B、PI-01A、PI-01B1 分别证明真实
能力源可调用；只有 `PI-01D` 才证明 Kafka/批处理能够根据 typed context 受控选择并保存这些只读
调查结果。内网 PKL Runtime 批跑继续默认不调用 MCP；调查批跑必须是显式模式和独立报告。

### 3.2 已删除的未确认能力

`endpoint.process_tree.lookup` 和 `host.event_context.lookup` 已从 action contract、默认 registry、Lead Agent prompt、domain/scenario routing、fixture 和测试中删除。平安当前没有这两个外部查询能力，且逐告警调用成本不可接受；进程树、命令行、登录上下文和主机事件继续从原始告警的 bounded native evidence 获取。未来只有确认存在真实 Provider 并重新完成产品/工程评审后，才允许以新的显式 contract 接入，不能恢复旧 mock。

## 4. External Disposition 不是 MCP

当前 `external disposition` 是外部预警/工单/处置系统状态和理由回流的协议与服务边界，不是 MCP tool。

现有实现：

- `SocExternalDispositionEvent`：vendor-neutral 输入事件。
- `SocExternalDispositionIngressCommand` + `POST /api/soc/external-dispositions`：当前真实的 authenticated canonical application ingress。
- `SocExternalDispositionAdapterConfig`：把 Zeus/ITSM/SIEM/SOAR payload 映射到 canonical event 的配置。
- `SocExternalDispositionService.apply_event()`：唯一写入 external disposition record、audit、review/correction
  和 exact used-Memory feedback 的 service 边界；不逐外部事件创建 Memory candidate。
- `SqlAlchemyAlertRepository` + migration `0009`：当前真实持久化；`InMemoryExternalDispositionRepository` 只用于 tests/smoke。

当前未完成的是“真实外部系统 -> canonical command”的 source adapter。真实 feed 接入后仍调用上述
Gateway/service 边界，不新建第二套状态同步逻辑。

未来如果 Zeus、ITSM、SOAR 或客户自研系统通过 MCP 暴露“读取工单状态/回写状态/订阅更新”，可以新增 MCP-backed external disposition adapter；但 adapter 仍必须把结果转成 `SocExternalDispositionEvent`，再进入 `SocExternalDispositionService.apply_event()`，不能绕过 service 直接改 ReviewQueue 或 memory。

## 5. 新增 Mock 时必须更新本文件

以后新增以下任一内容，都要同步更新本台账：

- 新的 `InMemory*ActionAdapter`、fake provider、local stdio MCP server tool。
- 新的 `backend/samples/**` fixture，用于模拟真实系统。
- 新的 `--dry-run`、stub、behind-flag LLM 或本地 smoke 流程。
- 暂时替代真实 PingAn / 客户系统的配置、mapper 或 provider。
- 将 mock 替换为真实系统的 smoke 结果、风险评估和剩余差距。

## 6. Alpha 验收中的组合边界

`./scripts/soc-alpha-acceptance.sh all` 是真实的验收编排器，但它组合了不同性质的证据：

| Component | Current nature | Production claim |
|---|---|---|
| Core Runtime/service/SQL/replay/audit | 真实代码 + 脱敏 fixture + deterministic analyzer + local SQLite | 不证明 live LLM 质量或 PostgreSQL 生产行为 |
| Kafka | ephemeral Redpanda 上的真实 Kafka protocol/offset/DLQ | 不证明生产 ACL/TLS/容量/恢复 |
| Review Web | 真实 React/Chromium + mocked HTTP transport | 不证明部署后的 Gateway/auth/network |
| Read-only investigation actions | 真实 policy/adapter/evidence contract + mock facts | 不证明 CMDB/EDR/TI/security-tag 接入 |
| External feedback | 真实 canonical Gateway handler/service/UoW/audit + fixture source event | 不证明 Zeus/ITSM/SOAR source feed、签名或凭证 |

Aggregate `passed` 只表示上述边界内的本地 Alpha 门禁全部成立。详细命令、artifact 和失败语义见
`../alpha-acceptance-runbook.md`。

## 7. 内网迁移实现与真实验收矩阵

这张表是外网完成产品流后迁移内网时的唯一 replacement checklist。每项只允许从现有
production-shaped adapter/ingress/runner 注入真实配置，不允许到内网再临时改 core contract。

| Debt ID | 外网当前状态 | 内网需要提供/实现 | 必跑验证与证据 | 关闭条件 |
|---|---|---|---|---|
| `RID-01 D12-B asset.locate` | 同一 Provider/MCP/action 代码 + fake transport，`mocked=true`；自包含 workflow HTTP client、旧源码 YHSYS PRD private-profile preparer 与离线 Mac toolchain 已准备；`message.by` 固定为旧值 | ZEUS DEV endpoint/App ID/Key、批准的 hit/not-found/UM/ambiguous cases；Agent Platform PRD profile 由 ignored env 注入 | preflight、direct matrix、MCP smoke、Dispatcher persistence/readback、Web/TUI readback | 至少一个真实 hit，完整失败矩阵，所有成功 evidence `mocked=false`，基础 verdict/Review 不变 |
| `RID-02 PI-01A threat intelligence` | production-shaped Provider/MCP + fake | `/public/indicatorSearch` DEV 响应与批准 IP cases，共用 ZEUS 鉴权 | hit/not-found/error/timeout/freshness/trim/lineage/persistence | 真实字段映射复核通过且 evidence `mocked=false` |
| `RID-03 PI-01B1 security tag` | production-shaped Provider/MCP + fake | `/public/searchTagContent` DEV 对象类型、expiry/永久语义与批准 entity cases | active/expired/inactive/conflict/out-of-scope/not-found/error + persistence | 真实响应语义冻结且 evidence `mocked=false`；仍不冒充 B2 权威事实同步 |
| `RID-04 PI-01B2 governed facts` | lifecycle/matcher 已实现；source contract data-gated | change/scanner/maintenance/exercise roster 权威源、version、scope、validity、owner、privacy/RBAC | source adapter replay、乱序/修订/过期/冲突、event-time match | 真实 source contract 冻结并写 append-only fact；fixture 不再承担生产事实 |
| `RID-05 PI-01C external disposition` | canonical authenticated ingress/service 已实现；source feed data-gated | Zeus/ITSM/SOAR 稳定 event ID、status/reason/version/ordering、签名与 tenant mapping | webhook/Kafka/poll adapter 的幂等、乱序、重放、更正、unknown status、trust mapping | 真实 source adapter 只生成 canonical command，SQL/UoW/audit 通过 |
| `RID-06 PI-01E internal shadow` | external 5/50 simulation passed | 同 cohort approved PKL、real composition/action/extensions config、isolated DEV SQLite | Runtime-only + persisted investigation 5 -> 50 -> all、paired evaluator | all Provider results符合 `internal_real`/`mocked=false`；不得以 external report 替代 |
| `RID-07 PI-02 infrastructure` | SQLite + local Redpanda flow passed | 生产前另行提供 Kafka ACL/TLS/topic/group/DLQ、PostgreSQL、K8s/worker 参数 | throughput/lag/backpressure/restart/replay/idempotency/migration/connection-pool/rollback | 约一万告警/日目标和 SLO 证据通过；DEV 继续 SQLite 不阻塞产品开发 |
| `RID-08 PI-03 real quality` | simulation manifest/eval/calibration flow passed；PI-01G 专家执行链路已通，但 NIDS 结果仍暴露 upstream role/direction assertion 可能过度加权 | 批准脱敏 corpus、具名 reviewer、labels/rationale、correlation pairs，包含 network tuple / TCP initiator / attacker-victim 反例 | seal/verify/quality/confidence replay，按 source/scenario/specialist 分层 | 只能由 `human_review` real corpus 产生质量声明或 profile promotion；不用 Runtime 硬编码伪造语义校准 |
| `RID-09 PI-04 telemetry` | local Snapshot/Web passed | deployed Kafka/model/Provider metrics、Prometheus scrape、owner 和 SLO | lag/throughput/latency/error/cost/schema drift dashboards and alerts | `not_measured` 被真实指标替换并通过值班/留存评审 |
| `RID-10 PI-05 rollout/actions` | virtual rollout + approval boundary passed，0 external effect | deployed cohort enforcement、具名 owner、feature flag、真实 rollback、EDR/F5/SOAR adapter | Shadow -> Limited Pilot rehearsal、approval-gated dry-run/execute、compensation | fresh gates 全部通过；`pilot_ready` 才可变 true，高风险动作仍默认人工审批 |
| `RID-11 PI-01H legacy compatibility` | durable Job/lease/event、旧 API、Runtime worker、legacy projection、Callback Outbox/attempt、项目模型网关、Host sidecar、fake E2E、只读 lifecycle/signature smoke 与脱敏 live acceptance runner 已完成 | EAGW/ZEUS/callback 私有配置、真实旧客户端、批准样本与容量参数 | model-gateway completion smoke；只读 precheck 先证明签名与 pending；local runner 验证 fresh submit/幂等 replay/Runtime/真实 callback transport；ZEUS 上游真实发起和旧页面回读；告警量 `1 -> 5 -> 50 -> 200/5000+` 与模型并发 `1 -> 2 -> 4 -> 6` 分开测试 | lifecycle smoke `code=200/status=1`；local live 报告通过；ZEUS-originated Job 在旧页面可回读；无重复 LLM/丢任务；P95/吞吐/租约/并发参数有证据；Fake E2E 或仅 local self-submit 不关闭本项 |

### 7.1 External freeze audit / 外网冻结审计

2026-08-09 已完成一次不访问内网的交付冻结审计：

| RID group | 外网可迁移产物 | 冻结结论 | 进入内网后仍需完成 |
|---|---|---|---|
| `RID-01..03` | production-shaped PingAn Provider、stdio MCP、action/evidence、fake matrix 与 local profile | 代码/配置/runner 均进入迁移源码必需清单；外网结果继续固定 `mocked=true` | approved cases + real endpoint/secret，逐项取得 `mocked=false` direct/MCP/persistence/readback 证据 |
| `RID-04..05` | vendor-neutral Governed Context 与 External Disposition service/ingress | 通用生命周期可迁移；权威 source contract 仍 data-gated，未虚构 PingAn Provider | 冻结真实 change/roster/status/reason/version/ordering contract 后再写 source adapter |
| `RID-06` | external 5/50、internal runner、paired evaluator、real-only composition | runner/config/验收逻辑随源码迁移；external report 不关闭 internal gate | 在隔离 SQLite 上按 `5 -> 50 -> all` 重跑 approved PKL 和 internal-real evidence |
| `RID-07` | SQLite DEV、local Kafka/Redpanda 协议链 | DEV 继续 SQLite；未制造不存在的 Kafka/K8s/PostgreSQL 参数 | 真实基础设施输入到位后单独验收，不阻塞当前内网 DEV 模型/Provider 测试 |
| `RID-08..10` | label/quality/Skill backlog、Operations Web、rollout/approval 的 simulation contracts | 产品流程可迁移且 claim boundary 已冻结；所有 real gates 保持 open | 真实 labels/telemetry/owners/cohort/rollback/action adapters 到位后逐项关闭 |
| `RID-11` | 旧协议兼容 API、durable execution、项目模型网关、Host DEV sidecar、hermetic fake acceptance 与 live acceptance runner | 外网已证明协议/恢复/回调审计可复跑；fake 报告固定 `simulated=true`，live runner 尚无内网证据 | 注入内网 private overlay 后完成 EAGW、ZEUS lifecycle/callback、旧页面和容量验收 |

`scripts/build_pingan_internal_transfer.py` 现在默认拒绝 dirty worktree，并核对 30 个关键 source/sample/
runner/doc 文件。只有 clean commit 构建的报告可以给出 `final_handoff_eligible=true`；显式
`--allow-dirty` 只生成开发验包，固定为不可交付。PingAn DEV 配置模板与根配置版本均为 `v33`。
专项冻结回归为 `142 passed`；它验证本地 Provider/fake、mock/real 配置边界、D12 evidence、enrichment
composition 和 internal-batch runner，不产生任何真实内网 evidence，因此 `RID-01..10` 状态均未被误关。

`PI-01G` 专家子智能体本身不是内网 Provider debt：只要配置的模型可用，它就是 DeerFlow 真实执行
路径。当前外网 NIDS network 与 EDR endpoint 代表报告分别保存为 Git-ignored
`backend/.deer-flow/soc-lead-agent-validation/SOC-PI01G-SMOKE-20260807T091947Z.json` 和
`SOC-PI01G-SMOKE-20260807T083748Z.json`；它们证明真实 model/task/event 链路，明确不声称
Provider 验收。内网迁移只需安装相同 profiles 并重跑 registry/Web/TUI smoke；专家看到的
RID-01..05 结果必须继续按各自 `mocked` provenance 解释，不能由模型文本替代真实验收。

## 8. 当前下一步

当前交付顺序只以 `.notes/ai_soc/delivery-roadmap.md` 和 `.notes/ai_soc/progress.md` 为准；本台账不再维护平行的 next-step 列表。`PI-01G1..G3` 外网产品完整性切片已于 2026-08-07 完成，RID-01..10 保持独立开放。外网阶段即使已知部分接口细节，也只实现契约一致且明确 `mocked=true` 的完整产品流，不直接关闭任何平安内网 RID；外网代码、测试、台账和迁移材料冻结后，才在平安 DEV 按 RID 逐项进入 real acceptance。尚无稳定 source contract 的能力继续 data-gated。任何 mock、专家文本或 LLM 输出都不得伪装成真实外部事实。
