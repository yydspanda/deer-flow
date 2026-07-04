# SOC Action Adapter Registry Plan

> 状态：Phase 1 收口规划已开始。当前已固定 contract、本地 dry-run-only adapter、approval service dry-run / execute preflight、第一个具体只读 `asset.lookup` adapter，以及显式 read-only adapter dispatcher/tool gateway wiring；仍不接生产 EDR/F5/MCP 副作用。

## 背景

SOC Lead Agent 已经可以提出结构化 action proposal，并通过 policy 生成 approval request。Web/TUI 审批人可以看到 proposal 来源、payload 和 context refs。下一步如果直接把 `response.block_ip`、`endpoint.isolate_host` 这类动作接到 MCP 或厂商 API，会有三个风险：

- action 名称靠字符串猜测，容易绕过 allowlist。
- dry-run 只校验 token，不校验厂商 adapter 是否存在、参数是否齐全。
- execute token 被消费后才发现 adapter 不支持执行，审计和补偿语义会变复杂。

因此先建立 action adapter registry，把真实外部动作接入前的 contract 固定住。

## 设计决策

Action adapter registry 是 `SocAgentApprovalService` 后面的执行能力注册表，不是新的审批系统。

```text
Lead Agent / Skill / Daemon
  -> SocAgentActionProposal
  -> SocAgentActionPolicy
  -> SocAgentApprovalRequest
  -> SocAgentApprovalGrant.execution_token_id
  -> SocAgentApprovalService.dry_run_approved_action / execute_approved_action
  -> SocActionAdapterRegistry
  -> EDR / F5 / SOAR / MCP adapter
```

当前代码已落地到 registry contract + approval service dry-run / execute preflight integration：

- `SocAgentActionAdapterDescriptor`：声明 action adapter 能力、风险、side-effect 等级、必需 payload/context 字段。
- `SocActionAdapter` protocol：所有真实 adapter 必须实现 `dry_run()` 和 `execute()`。
- `SocActionAdapterRegistry`：只按精确 `route/action` allowlist 解析 adapter，不提供 fallback。
- `DryRunOnlySocActionAdapter`：用于 Phase 1/测试/尚未上线的动作，只验证参数并返回 `external_side_effect=not_executed`。
- `SocAgentApprovalService.dry_run_approved_action()`：有 registry 时先校验 grant，再把 approval request 中的 `action_payload/context_refs` 与 command payload 合并后交给 registry dry-run；没有 registry 时保持 token-only dry-run 兼容。
- `SocAgentApprovalService.execute_approved_action()`：有 registry 时，在消费 token 前先调用 registry execute preflight，确认 adapter 存在、支持 execute、payload/context refs 齐全；当前仍不调用 adapter.execute，不产生外部副作用。
- `InMemoryAssetLookupActionAdapter`：第一个具体 read-only adapter，route/action 固定为 `asset.lookup`，只读查询 in-memory/static inventory，用于验证 descriptor、dry-run、execute preflight 和 result payload；不是生产资产系统接入。
- `SocAgentActionDispatcher` read-only adapter path：显式 `metadata.soc_route=asset.lookup` 且 router allowlist 打开时，dispatcher 可通过注入的 `SocActionAdapterRegistry` 执行只读 adapter，并把 adapter result payload 通过 `soc.action_result` stream event 暴露；默认 chat router 仍不开放 `asset.lookup`。

## Contract 约束

每个 adapter descriptor 至少声明：

| 字段 | 用途 |
|---|---|
| `adapter_id` | 审计和运维定位 |
| `route` / `action` | 精确匹配 approval command，禁止模糊匹配 |
| `risk_level` | 必须和 policy 风险等级对齐 |
| `adapter_kind` | `noop/service/mcp/http/script`，后续用于配置治理 |
| `external_side_effect` | `none/read/write/destructive`，用于审批 UI 和审计 |
| `dry_run_supported` / `execute_supported` | 明确 adapter 当前能力 |
| `required_payload_fields` | 例如 `ip`、`duration_seconds`、`host_id` |
| `required_context_refs` | 例如 `queue_id`、`run_id`、`alert_id` |
| `idempotency_required` | 高风险动作默认必须有幂等键 |

## 不变量

- 没有注册 adapter 的 action 必须 fail-fast，不能 fallback 到“让 LLM 再解释一下”。
- dry-run 永远不能产生外部副作用。
- execute 必须在 approval grant token、idempotency key、adapter descriptor、payload/context 校验都通过后才允许调用真实 adapter。
- adapter result 必须进入 `SocAgentActionResult.payload`，并保留 `adapter_id`、`external_side_effect`、幂等键和执行者。
- read-only adapter 运行态调用必须来自显式 tool/gateway metadata，例如 `SocAgentChatRequest.metadata["soc_route"]` 和 `metadata["action_payload"]`；不能从自然语言消息里猜测 route 或 payload。
- read-only adapter 也必须同时满足 router allowlist、permission policy 和 registry 精确匹配；缺少任一层都必须 fail-fast。
- `soc.action_result` stream event 必须携带 result payload，供 TUI/Web/Channels 可观测 adapter 输出。
- 真实 MCP/HTTP/厂商 SDK 类型只能出现在具体 adapter module，不能扩散到 core、API、TUI、Web。
- Web/TUI 只能展示 `SocAgentActionResult`，不能自行调用 adapter。

## 后续切片

1. **Approval service adapter dry-run integration**（Done）
   - 给 `SocAgentApprovalService` 注入可选 `SocActionAdapterRegistry`。
   - `dry_run_approved_action()` 继续先校验 approval grant，再调用 registry dry-run 校验 payload/context。
   - registry 不存在时保持当前 token-only dry-run，兼容本地最小闭环。

2. **Execute preflight before token consume**（Done）
   - `execute_approved_action()` 在消费 token 前检查 adapter 是否存在、是否支持 execute、payload 是否满足必需字段。
   - 只有真实 adapter 调用结果确定后，才把 execution result 写回 grant。
   - 当前仍不调用 `adapter.execute()`；dry-run-only adapter / 未注册 adapter 会 fail-fast，token 保持 unconsumed。

3. **First concrete safe adapter**（Done）
   - 先接只读查询类 adapter，例如资产归属查询或 EDR 进程树查询。
   - 封禁 IP、隔离终端、禁用账号等 write/destructive adapter 等 staging eval、审计和补偿策略稳定后再接。

4. **Read-only adapter dispatcher / tool gateway wiring**（Done）
   - `asset.lookup` 通过显式 chat/tool gateway metadata 进入 action dispatcher：`metadata.soc_route=asset.lookup` + `metadata.action_payload`。
   - 默认不加入 chat router 白名单；必须显式 route / skill / tool policy 打开。
   - read-only adapter execute 结果进入 `SocAgentActionResult.payload` 和 `soc.action_result.payload`，不能只把结果塞回 prompt。

5. **SOC Lead Agent read-only tool proposal bridge**
   - 让 SOC Lead Agent 只能通过结构化 tool/proposal envelope 请求 `asset.lookup` 等只读 action。
   - bridge 负责把 proposal 转成显式 `metadata.soc_route/action_payload`，再走同一条 router/policy/dispatcher/registry 链路。
   - 不允许 Lead Agent 直接 import adapter 或直接调用 MCP/资产系统。

6. **MCP adapter bridge**
   - 复用 DeerFlow MCP/tool 能力，但通过 SOC adapter descriptor 限定 action 名称、参数 schema、风险等级和审计字段。
   - 用户可配置 readonly MCP 候选；高风险 MCP group 只允许管理员启用，并继续走 approval。
