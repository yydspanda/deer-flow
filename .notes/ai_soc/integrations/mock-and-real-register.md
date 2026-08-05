# SOC Agent Mock 与真实接入台账

> Updated: 2026-08-04
>
> 目的：集中记录当前 SOC Agent 里哪些能力只是 mock、fixture、in-memory 或本地 smoke，用来验证工程链路；后续接入真实 PingAn / 客户环境时，必须按本台账替换、复测和重新验收。

## 1. 总原则

- Mock 只能证明协议、服务边界、展示链路和回归测试能跑通，不能证明生产系统已接入。
- `PA-12` 真实 PingAn MCP/API 替换当前为 `In Progress / internal smoke`：DEV profile、portable signer、preflight 和 direct smoke entry 已实现，但在产生内网 `mocked=false` 证据前仍不能算完成；本地 mock、fake fixture、in-memory adapter 不能冒充完成。
- Read-only mock 的成功结果可以写入 `InvestigationEvidence` 用于 demo/eval，但必须带 `mocked=true`、fixture source 或 adapter id，不能当作生产事实，不能满足场景证据要求，也不能提高 domain/scenario confidence。
- 外部 free-text reason、LLM 总结、分析师备注、mock tool result 都只能生成 `SocMemoryCandidate(status=pending_review)`；不能直接写 confirmed memory 或 active lesson。
- 真实替换时只能替换 adapter/provider/config，不得改变 core service contract、Main Orchestrator contract、ReviewQueue contract 或 Lead Agent bounded context contract。
- 每个 mock 替换成真实系统前，必须至少补齐：endpoint/config、认证方式、字段裁剪、敏感信息处理、超时/重试、失败返回、latency、payload/result size、smoke report、rollback 方案。

## 2. 当前 Mock / Fixture 清单

| 能力 | 当前实现 | 位置 | 当前用途 | 后续真实替换 |
|---|---|---|---|---|
| `asset.lookup` | in-memory read-only adapter，部分 smoke 可走 MCP-backed config | `backend/soc_agent/actions/adapters.py`、`backend/soc_agent/actions/mcp.py` | 验证“简单资产记录查询”的 action contract、policy、approval preflight、evidence 写入；它不等同于 ownership-oriented `asset.locate` | `PI-01D` 必须为 tenant 选择真实 adapter/显式 provider mapping，或从 allowlist 禁用；PI-01E 不得保留默认 in-memory mock |
| `asset.locate` | 本地 stdio MCP mock tool | `backend/scripts/soc_dev_mcp_server.py`、`backend/samples/mcp/` | 模拟 Zeus/CMDB/asset_to_bu 归属定位，验证 Lead Agent proposal -> MCP adapter -> evidence | 替换为真实资产归属/BU/owner/处置归属服务；保存 `soc.mcp_action_smoke_report.v1` |
| `D12-A` PingAn asset provider | **Implemented production-shaped code with fake transport; still mock** | `backend/soc_agent/integrations/pingan/`、`backend/scripts/soc_pingan_asset_mcp_server.py`、`backend/samples/mcp/pingan_asset/` | 外网验证 ZEUS 签名调用边界、`searchAssetInfo -> asset_to_bu -> UM` 降级编排、MCP/action 映射和 fail-closed；产物明确 `mocked=true` | 只有 `D12-B` 内网注入真实 endpoint/secret/signer/workflow runner 并产生 `mocked=false` smoke 证据后才算 real；D12-A 不能关闭 `PA-12` / `PI-01` |
| `D12-B` PingAn internal validation | **Code/config/acceptance runners prepared; real evidence pending** | `backend/samples/pingan_dev/`、`backend/scripts/soc_pingan_dev_preflight.py`、`backend/scripts/soc_pingan_asset_direct_smoke.py`、`backend/scripts/soc_pingan_d12b_matrix.py`、`backend/scripts/soc_pingan_d12b_evidence.py` | 真实值保存在 Git-ignored local profile/`0600` case file；plan 不发请求，live 必须显式确认；aggregate report 不含 raw query/UM/Provider body/override value；evidence runner 经 MCP/Dispatcher 持久化并回读共享 Review/Lead Agent context，检查基础 Run/Review 不变 | 内网提供 Agent Platform `run_workflow` 依赖和 approved cases，通过 direct + MCP + persisted evidence/readback；至少一项 `mocked=false` 且 deployed Web/TUI smoke 通过后才改变 real-provider 状态 |
| Automatic read-only investigation orchestration | **D1-D4 deterministic production code; not a mock** | `backend/soc_agent/contracts/enrichment.py`、`backend/soc_agent/core/enrichment.py`、`backend/soc_agent/core/investigation_workflow.py`、`backend/soc_agent/contracts/investigation_reporting.py`、`backend/soc_agent/core/investigation_reporting.py`、`backend/soc_agent/application/enrichment.py`、migration `0019_enrichment_executions` | 默认关闭；typed plan 经同一 Dispatcher/Registry；execution/attempt/evidence 可持久化；实际 `mocked` 在 evidence 前校验；Kafka/internal batch 仅显式 opt-in；幂等、failure/not-found、bounded retry、stale recovery、linked replay 和无 Provider 调用的 report/addendum 投影已实现 | PI-01E 用真实内网配置收集 shadow 证据。真实 Provider 仍须各自 `mocked=false` 验收；orchestration/reporting 完成不等于 PingAn asset/TI/tag 已真实接通，且任何阶段都不改基础 verdict/close/memory/automation |
| `threat_intel.ip_reputation.lookup` | **PingAn production-shaped Provider/MCP 已实现；默认 registry 仍有 in-memory fallback，真实内网证据待补** | `backend/soc_agent/integrations/pingan/threat_intel.py`、`backend/scripts/soc_pingan_threat_intel_mcp_server.py`、`backend/samples/mcp/pingan_threat_intel/` | 外网验证 ZEUS wire contract、bounded mapping、freshness/lineage、MCP/action/persistence；fake 明确 `mocked=true` | `PI-01A` 在 DEV 跑 hit/not-found/error/timeout 和实际字段 coverage，保存 `mocked=false` evidence；PI-01D/PI-01E 配置该 MCP adapter 后禁用默认 in-memory fallback |
| `security_tag.lookup` | **PingAn production-shaped Provider/MCP 已实现；默认 registry 仍有 in-memory fallback，真实内网证据待补** | `backend/soc_agent/integrations/pingan/security_tag.py`、`backend/scripts/soc_pingan_security_tag_mcp_server.py`、`backend/samples/mcp/pingan_security_tag/` | 外网验证 ZEUS wire contract、validity/scope/status、MCP/action/persistence；过期/失效/冲突不丢失，fake 明确 `mocked=true` | `PI-01B1` 在 DEV 核对对象类型、`expireTime`/永久有效语义并跑 exact-hit/expired/not-found/error，保存 `mocked=false` evidence；标签查询本身不等于 PI-01B2 权威授权事实已同步 |
| Authorized-activity source facts | GF-01 lifecycle/DB 与 AA-01 matcher 是真实确定性实现；当前 HIDS/EDR shadow facts 由已确认业务真值构造为本地 in-memory fixture | `backend/soc_agent/contracts/governed_context.py`、`backend/soc_agent/authorization/`、gitignored `step-12-authorization-shadow/` | 验证 event-time lifecycle/scope/freshness/recurrence 和 exact explanation；不代表已接变更/扫描器/维护系统 | `PI-01B2` 接真实 change/scanner/maintenance/exercise-roster/CMDB source adapter，同步 source ref/version/scope/freshness 后重跑 shadow；不得把 B1 标签或 validation fixture 当生产 active fact |
| PingAn eval fixtures | 脱敏/伪造 APT、EDR、HIDS 回归样本 | `backend/samples/eval/pingan/`、`backend/samples/alerts/pingan_legacy_hids.json` | 验证 normalizer、read-only action、domain triage、main orchestrator demo | 补充经批准的脱敏真实样本、schema drift 样本、反例和边界样本 |
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
| GF-01 / AA-01 | deterministic production contracts/services | Fact lifecycle、历史版本选择和 matcher 不是 mock；EX/DP/EV persistence/evaluation 已实现，当前缺口是权威事实来源同步和 governed rollout |
| External disposition canonical ingress | authenticated application boundary | Gateway route、SQL repository、transactional service、RBAC 和 exact-retry/conflict 语义是真实实现；mock/data-gated 的是 Zeus/ITSM/SOAR source feed、签名和凭证 |
| PingAn historical software-path catalog | deterministic local compiler + read-only MCP/action | 真实编译旧 XLSX 并精确查询版本化 SQLite；不是 mock，也不是权威 allowlist。输出固定为 investigation-only、decision impact none；源数据缺少人工 reviewer/scope/validity，因此不能用于自动判良、跳过 Runtime 或关闭告警 |
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
- `SocExternalDispositionService.apply_event()`：唯一写入 external disposition record、audit、review/correction、后续 memory candidate 的 service 边界。
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

## 7. 当前下一步

当前交付顺序只以 `.notes/ai_soc/delivery-roadmap.md` 和 `.notes/ai_soc/progress.md` 为准；本台账不再维护平行的 next-step 列表。`PA-12` 的外网代码准备已完成，真实 provider 仍由内网 `run_workflow` 依赖、网络和 approved cases gated；external source feed 也仍 data-gated。不得用更多 mock 或 LLM 伪造事实冒充接入。
