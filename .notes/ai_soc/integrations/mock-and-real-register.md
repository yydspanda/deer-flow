# SOC Agent Mock 与真实接入台账

> Updated: 2026-07-20
>
> 目的：集中记录当前 SOC Agent 里哪些能力只是 mock、fixture、in-memory 或本地 smoke，用来验证工程链路；后续接入真实 PingAn / 客户环境时，必须按本台账替换、复测和重新验收。

## 1. 总原则

- Mock 只能证明协议、服务边界、展示链路和回归测试能跑通，不能证明生产系统已接入。
- `PA-12` 真实 PingAn MCP/API 替换当前仍是 `Waiting`；本地 mock、fake fixture、in-memory adapter 不能冒充完成。
- Read-only mock 的成功结果可以写入 `InvestigationEvidence` 用于 demo/eval，但必须带 `mocked=true`、fixture source 或 adapter id，不能当作生产事实，不能满足场景证据要求，也不能提高 domain/scenario confidence。
- 外部 free-text reason、LLM 总结、分析师备注、mock tool result 都只能生成 `SocMemoryCandidate(status=pending_review)`；不能直接写 confirmed memory 或 active lesson。
- 真实替换时只能替换 adapter/provider/config，不得改变 core service contract、Main Orchestrator contract、ReviewQueue contract 或 Lead Agent bounded context contract。
- 每个 mock 替换成真实系统前，必须至少补齐：endpoint/config、认证方式、字段裁剪、敏感信息处理、超时/重试、失败返回、latency、payload/result size、smoke report、rollback 方案。

## 2. 当前 Mock / Fixture 清单

| 能力 | 当前实现 | 位置 | 当前用途 | 后续真实替换 |
|---|---|---|---|---|
| `asset.lookup` | in-memory read-only adapter，部分 smoke 可走 MCP-backed config | `backend/soc_agent/actions/adapters.py`、`backend/soc_agent/actions/mcp.py` | 验证资产查询 action contract、policy、approval preflight、evidence 写入 | 替换为 CMDB / 资产系统 / 客户资产服务 read-only adapter 或 MCP-backed adapter |
| `asset.locate` | 本地 stdio MCP mock tool | `backend/scripts/soc_dev_mcp_server.py`、`backend/samples/mcp/` | 模拟 Zeus/CMDB/asset_to_bu 归属定位，验证 Lead Agent proposal -> MCP adapter -> evidence | 替换为真实资产归属/BU/owner/处置归属服务；保存 `soc.mcp_action_smoke_report.v1` |
| `endpoint.process_tree.lookup` | in-memory EDR process-tree mock adapter | `backend/soc_agent/actions/adapters.py` | 验证 EDR 进程树 evidence、Lead Agent proposal、Review context 复用 | 替换为真实 EDR read-only 查询 API/MCP；补字段裁剪和进程树大小上限 |
| `host.event_context.lookup` | in-memory host event-context mock adapter | `backend/soc_agent/actions/adapters.py` | 验证 HIDS/主机上下文只读查询和 PingAn PA-07 P0 能力 | 替换为 HIDS / 主机日志 / EDR host telemetry 查询服务 |
| `threat_intel.ip_reputation.lookup` | in-memory 威胁情报 mock adapter | `backend/soc_agent/actions/adapters.py` | 验证 APT 情报查询 evidence 形态，避免 domain handler 自己假设情报 | 替换为企业威胁情报、TI 平台或外部情报 provider 的 read-only adapter |
| `security_tag.lookup` | in-memory 标签/授权/白名单 mock adapter | `backend/soc_agent/actions/adapters.py` | 验证授权扫描、演练、维护窗口、白名单等标签 evidence 形态 | 替换为安全标签、变更、演练、白名单、维护窗口等真实数据源 |
| Authorized-activity source facts | GF-01 lifecycle/DB 与 AA-01 matcher 是真实确定性实现；当前 HIDS/EDR shadow facts 由已确认业务真值构造为本地 in-memory fixture | `backend/soc_agent/contracts/governed_context.py`、`backend/soc_agent/authorization/`、gitignored `step-12-authorization-shadow/` | 验证 event-time lifecycle/scope/freshness/recurrence 和 exact explanation；不代表已接变更/扫描器/维护系统 | 接真实 change/scanner/maintenance/CMDB source adapter，同步 source ref/version/freshness 后重新跑 shadow replay；不得把 validation fixture 当生产 active fact |
| PingAn eval fixtures | 脱敏/伪造 APT、EDR、HIDS 回归样本 | `backend/samples/eval/pingan/`、`backend/samples/alerts/pingan_legacy_hids.json` | 验证 normalizer、read-only action、domain triage、main orchestrator demo | 补充经批准的脱敏真实样本、schema drift 样本、反例和边界样本 |
| External Disposition Zeus fixture | Zeus 状态/理由 mock payload；canonical Gateway ingress 和 SQL/service 是真实实现 | `backend/samples/external_disposition/zeus_status_update.json`、`backend/app/gateway/routers/soc_external_dispositions.py` | fixture 验证 field-path mapper；authenticated ingress 验证 canonical command、RBAC、idempotency、review/correction | 替换 fixture/source side 为真实 webhook/Kafka/poll adapter；补签名、租户、重放和脱敏；继续复用现有 ingress/service |
| Kafka local smoke | 本地 Redpanda/Kafka topic、strict `SocAlertRawEnvelope` sample、dead-letter smoke | `backend/scripts/soc_kafka_smoke.py`、`backend/soc_agent/daemon/` | 验证 strict envelope、raw preservation、consumer runner、commit、dead-letter、status/readiness | 替换为真实 topic、ACL、consumer group、DLQ、监控、容量与失败演练 |
| Alpha Review Web browser fixture | Chromium 渲染真实 SOC React 页面，HTTP transport 由 deterministic Playwright route fixture 提供 | `frontend/tests/e2e/soc-review.spec.ts`、`frontend/tests/e2e/utils/mock-soc-api.ts` | 回归 queue/context、close/correct、approval、memory、sample outcome 和 normalization 交互及 request contract | 部署环境验收必须再走真实 Gateway/auth/network；浏览器 fixture 不能证明后端或生产链路 |
| 高风险响应动作 | 当前只有 proposal、policy、approval、一次性 grant、dry-run/execute preflight；`external_side_effect=not_executed` | `backend/soc_agent/actions/adapters.py`、`backend/soc_agent/core/service.py` | 验证封禁 IP、隔离主机等动作在执行前的权限、审批、幂等和审计边界 | 接入真实 EDR/F5/SOAR/防火墙 adapter；必须补回滚/补偿、执行结果核验和失败重试，默认仍需人工审批 |
| LLM analyzer | **真实路径已完成**：默认 deterministic stub；显式模式复用 DeerFlow `create_chat_model` | `backend/soc_agent/llm/`、`backend/soc_agent/core/runtime.py` | stub 保证回归/回放；`SOC_ANALYZER_MODE=llm` 或 CLI flag 调用已注册模型；有独立 concurrency/RPM admission、输出上限、evidence grounding、typed failure；raw confidence 当前均标记为 uncalibrated 并进入复核 | 持续补人工标注集、离线校准和成本预算；真实输出仍需 JSON/schema/domain/grounding validation 和 `SocDecisionPolicy` |
| Normalization suggestion | **真实路径已完成**：deterministic/replay/live LLM 三种离线模式 | `backend/soc_agent/normalizers/suggestions.py` | 发现 mapping 候选并严格校验 observed source path / canonical whitelist | 所有建议仍需工程师复核，`auto_apply_allowed=false` |
| SQL/in-memory repositories in tests | in-memory repository 或 SQLite 单元测试 | `backend/soc_agent/*/repository.py`、`backend/tests/` | 单元测试和无 DB 局部 wiring | 生产/准生产必须走 PostgreSQL migration + SQLAlchemy repository；本地开发可用 SOC SQLite 测试库 |

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
| CMDB/EDR/HIDS/TI/security-tag results | 当前部分为 mock external facts | **Must replace with real provider** | 这是当前真实缺口；不能让 LLM 生成或猜测外部事实 |
| Main orchestrator demo | 可重复 MVP 编排 | Evolve through services/Lead Agent | demo 不是生产自主 Agent；生产入口继续复用同一 Runtime/service/policy 边界 |

## 3. PA-12 的真实完成标准

`PA-12` 不等于“mock adapter 都写完”。只有满足下面条件，才能从 `Waiting` 改成 `Done`：

1. 拿到真实 dev/staging endpoint、MCP server 或 API adapter 配置，不把 secret 写入仓库。
2. 每个真实 provider 都通过 `SocActionAdapterRegistry` 或 MCP-backed adapter 显式注册，不能让 Lead Agent 自由调用任意 MCP tool。
3. 跑 `soc mcp tools` / `soc mcp smoke` 或对应 adapter smoke，保存结构化报告，报告不提交敏感 payload。
4. 记录 latency、failure rate、timeout、payload size、result size、字段裁剪、敏感字段、空结果、权限失败和限流行为。
5. 真实结果只作为 `InvestigationEvidence` 进入 ReviewQueue / Lead Agent context；不能直接改 verdict、memory 或处置状态。
6. 对每个真实接入补至少一条脱敏回归样本，覆盖成功、查不到、权限失败或超时中的至少两类情况。

### 3.1 当前不能由 LLM 替代的外部事实能力

下列能力仍是 credential-gated，不属于本轮 LLM 接入遗漏：

- `asset.lookup` / `asset.locate`：需要 CMDB、Zeus 或资产服务 endpoint。
- `endpoint.process_tree.lookup`：需要真实 EDR 查询接口。
- `host.event_context.lookup`：需要 HIDS/EDR/日志平台查询接口。
- `threat_intel.ip_reputation.lookup`：需要企业或外部 TI provider。
- `security_tag.lookup`：需要白名单、演练、变更、维护窗口等权威数据源。
- `authorized_activity` source sync：需要 change/scanner/maintenance/CMDB 的事实来源、版本和 freshness；
  当前 step-12 人工构造事实只用于 shadow replay。
- External Disposition Zeus fixture：需要 webhook、Kafka 或 polling 接入参数。
- 高风险响应动作：需要 EDR/F5/SOAR/防火墙 staging endpoint、审批策略和回滚/补偿能力。

大模型可以建议“应查询什么”，但不能虚构这些系统的查询结果。真实 endpoint/凭证到位后，只替换
adapter/provider/config，并继续将结果作为 `InvestigationEvidence` 回流。

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

当前交付顺序只以 `.notes/ai_soc/delivery-roadmap.md` 和 `.notes/ai_soc/progress.md` 为准；本台账不再维护平行的 next-step 列表。`PA-12` 真实 provider 与 external source feed 继续保持 data-gated，在 endpoint/凭证/允许测试数据到位前不得用更多 mock 或 LLM 伪造事实冒充接入。
