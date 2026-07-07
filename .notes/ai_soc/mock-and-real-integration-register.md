# SOC Agent Mock 与真实接入台账

> Updated: 2026-07-07
>
> 目的：集中记录当前 SOC Agent 里哪些能力只是 mock、fixture、in-memory 或本地 smoke，用来验证工程链路；后续接入真实 PingAn / 客户环境时，必须按本台账替换、复测和重新验收。

## 1. 总原则

- Mock 只能证明协议、服务边界、展示链路和回归测试能跑通，不能证明生产系统已接入。
- `PA-12` 真实 PingAn MCP/API 替换当前仍是 `Waiting`；本地 mock、fake fixture、in-memory adapter 不能冒充完成。
- Read-only mock 的成功结果可以写入 `InvestigationEvidence` 用于 demo/eval，但必须带 `mocked=true`、fixture source 或 adapter id，不能当作生产事实。
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
| PingAn eval fixtures | 脱敏/伪造 APT、EDR、HIDS 回归样本 | `backend/samples/eval/pingan/`、`backend/samples/alerts/pingan_legacy_hids.json` | 验证 normalizer、read-only action、domain triage、main orchestrator demo | 补充经批准的脱敏真实样本、schema drift 样本、反例和边界样本 |
| External Disposition Zeus fixture | Zeus 状态/理由 mock payload | `backend/samples/external_disposition/zeus_status_update.json` | 验证 field-path mapper、status mapping、idempotency、review/correction | 替换为真实 webhook/Kafka/poll/manual import adapter；补认证、签名、租户、重放和脱敏 |
| Kafka local smoke | 本地 Redpanda/Kafka topic、sample payload、dead-letter smoke | `backend/scripts/soc_kafka_smoke.py`、`backend/soc_agent/daemon/` | 验证 consumer runner、mapper、commit、dead-letter、status/readiness | 替换为真实 topic、ACL、consumer group、DLQ、监控、容量与失败演练 |
| LLM analyzer | 默认 deterministic stub；真实 LLM behind flag | `backend/soc_agent/llm/`、`backend/soc_agent/core/runtime.py` | 保证 Phase 1/2 默认可重复、可回放、低成本 | 真实 LLM 只能在显式配置和 offline eval 通过后启用，输出仍需 JSON/schema/domain validation |
| SQL/in-memory repositories in tests | in-memory repository 或 SQLite 单元测试 | `backend/soc_agent/*/repository.py`、`backend/tests/` | 单元测试和无 DB 局部 wiring | 生产/准生产必须走 PostgreSQL migration + SQLAlchemy repository；本地开发可用 SOC SQLite 测试库 |

## 3. PA-12 的真实完成标准

`PA-12` 不等于“mock adapter 都写完”。只有满足下面条件，才能从 `Waiting` 改成 `Done`：

1. 拿到真实 dev/staging endpoint、MCP server 或 API adapter 配置，不把 secret 写入仓库。
2. 每个真实 provider 都通过 `SocActionAdapterRegistry` 或 MCP-backed adapter 显式注册，不能让 Lead Agent 自由调用任意 MCP tool。
3. 跑 `soc mcp tools` / `soc mcp smoke` 或对应 adapter smoke，保存结构化报告，报告不提交敏感 payload。
4. 记录 latency、failure rate、timeout、payload size、result size、字段裁剪、敏感字段、空结果、权限失败和限流行为。
5. 真实结果只作为 `InvestigationEvidence` 进入 ReviewQueue / Lead Agent context；不能直接改 verdict、memory 或处置状态。
6. 对每个真实接入补至少一条脱敏回归样本，覆盖成功、查不到、权限失败或超时中的至少两类情况。

## 4. External Disposition 不是 MCP

当前 `external disposition` 是外部预警/工单/处置系统状态和理由回流的协议与服务边界，不是 MCP tool。

现有实现：

- `SocExternalDispositionEvent`：vendor-neutral 输入事件。
- `SocExternalDispositionAdapterConfig`：把 Zeus/ITSM/SIEM/SOAR payload 映射到 canonical event 的配置。
- `SocExternalDispositionService.apply_event()`：唯一写入 external disposition record、audit、review/correction、后续 memory candidate 的 service 边界。
- `InMemoryExternalDispositionRepository`：当前只用于 service tests 和本地 smoke。

未来如果 Zeus、ITSM、SOAR 或客户自研系统通过 MCP 暴露“读取工单状态/回写状态/订阅更新”，可以新增 MCP-backed external disposition adapter；但 adapter 仍必须把结果转成 `SocExternalDispositionEvent`，再进入 `SocExternalDispositionService.apply_event()`，不能绕过 service 直接改 ReviewQueue 或 memory。

## 5. 新增 Mock 时必须更新本文件

以后新增以下任一内容，都要同步更新本台账：

- 新的 `InMemory*ActionAdapter`、fake provider、local stdio MCP server tool。
- 新的 `backend/samples/**` fixture，用于模拟真实系统。
- 新的 `--dry-run`、stub、behind-flag LLM 或本地 smoke 流程。
- 暂时替代真实 PingAn / 客户系统的配置、mapper 或 provider。
- 将 mock 替换为真实系统的 smoke 结果、风险评估和剩余差距。

## 6. 当前下一步

- External Disposition 已完成 contract、mapper、record、audit、high-trust review/correction 和 `SocMemoryCandidate(status=pending_review)`；下一刀建议接 PG/API/ReviewQueue visibility。
- `PA-12` 继续等待真实 PingAn dev/staging endpoint、凭证和允许测试的数据源。
- 在真实接口未就绪前，不继续堆更多 mock；优先补 Memory candidate、Web/TUI 可见化、correlation/main orchestrator 整合和 demo/eval 链路。
