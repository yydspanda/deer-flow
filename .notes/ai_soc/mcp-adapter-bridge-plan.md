# SOC MCP Adapter Bridge Plan

> 状态：Phase 1 后续规划。当前已完成 SOC MCP provider port、fake provider tests、read-only MCP adapter skeleton、MCP-backed read-only `asset.lookup` explicit config builder、DeerFlow cached MCP provider implementation、本地显式 config smoke wiring，以及 dev/staging smoke report contract；尚未做真实 dev/staging MCP live smoke。目标是把真实资产系统、EDR 只读查询、F5/SOAR/MCP 能力接进 SOC action adapter registry，但不让 SOC Lead Agent 直接调用任意 MCP tool。

## 背景

当前 SOC 主链路已经具备：

- `SocActionAdapterRegistry`：按精确 `route/action` 注册 adapter。
- `InMemoryAssetLookupActionAdapter`：第一个 read-only adapter，验证 `asset.lookup` contract。
- `SocAgentActionDispatcher`：显式 `metadata.soc_route/action_payload` 才能进入 read-only adapter。
- `SocLeadAgentActionProposalBoundary`：Lead Agent 只能通过 `<soc_action_proposal>...</soc_action_proposal>` 提出候选动作；read-only proposal 也必须经过 router/policy/dispatcher/registry。

下一步要接真实数据源，不能把 DeerFlow MCP tool 直接交给 SOC Lead Agent 自主调用。原因：

- MCP tool 名称和参数属于外部扩展面，不能作为 SOC 稳定业务 action contract。
- tool_search/deferred tool 适合通用 agent 探索工具，不适合作为生产处置边界。
- SOC 需要稳定审计字段、风险等级、payload schema、超时、重试、脱敏和 approval boundary。

## DeerFlow 现有事实

基于本仓库 CodeGraph/源码确认：

- `deerflow.tools.tools.get_available_tools()` 会加载 config tools、built-ins、cached MCP tools、ACP tools，并按 tool name 去重。
- MCP tools 来自 `deerflow.mcp.cache.get_cached_mcp_tools()`。
- `get_cached_mcp_tools()` 会按 `extensions_config.json` / `mcp_config.json` 变更自动 reset/lazy initialize。
- `ExtensionsConfig.from_file()` 读取 root `extensions_config.json` 或 `mcp_config.json`，并通过 `get_enabled_mcp_servers()` 获取启用的 MCP server。
- DeerFlow 的 deferred tool/search 体系通过 `deerflow.tools.mcp_metadata.tag_mcp_tool()` 标记 MCP tools，再由 `tool_search` 延迟暴露 schema。

因此 SOC 侧应复用 DeerFlow MCP cache/session 生命周期，但在 SOC adapter 层重新包一层稳定业务 contract。

## 决策

SOC MCP bridge 是 `SocActionAdapter` 的一种具体实现，不是新的 agent tool runtime。

```text
SOC Lead Agent / Skill / TUI / Daemon
  -> SocAgentActionProposal or explicit SocAgentChatRequest metadata
  -> SocAgentCapabilityRouter
  -> SocAgentActionPolicy
  -> SocAgentActionDispatcher
  -> SocActionAdapterRegistry
  -> SocMcpToolActionAdapter
  -> DeerFlow cached MCP tool
  -> SocAgentActionResult.payload
```

关键原则：

- Lead Agent 只知道 SOC action：例如 `asset.lookup`、`endpoint.process_tree.lookup`、`response.block_ip`。
- Adapter 才知道 MCP server/tool 名称：例如 `cmdb_asset_lookup`、`edr_process_tree`、`f5_block_ip`。
- MCP tool 输出必须被 adapter 转换为 `SocAgentActionResult.payload`，不能把原始 tool result 不经筛选地塞回 prompt。
- read-only MCP 可以先接；write/destructive MCP 必须继续走 approval grant + execute preflight + idempotency。
- SOC Lead Agent profile 不增加 `mcp` 字段。DeerFlow custom-agent config 只支持 `skills/tool_groups` 等 profile 字段；MCP server 连接仍由 DeerFlow `extensions_config.json` / `mcp_config.json` 管理，SOC route/action 到 MCP tool 的绑定由本文件定义的 action adapter allowlist 管理。

## Adapter Descriptor Mapping

每个 MCP-backed adapter 仍必须先声明 `SocAgentActionAdapterDescriptor`。

示例：

```yaml
adapter_id: asset-lookup-cmdb-mcp
route: asset.lookup
action: asset.lookup
risk_level: read_only
adapter_kind: mcp
external_side_effect: read
dry_run_supported: true
execute_supported: true
idempotency_required: false
required_payload_fields:
  - asset_key
required_context_refs:
  - thread_id
mcp:
  server: cmdb
  tool: cmdb_asset_lookup
  timeout_seconds: 5
  input_mapping:
    asset_key: asset_key
  output_contract: soc.asset_lookup_result.v1
```

写动作示例必须保持 disabled，直到 staging/eval/approval 稳定：

```yaml
adapter_id: response-block-ip-f5-mcp
route: response.block_ip
action: response.block_ip
risk_level: high_risk
adapter_kind: mcp
external_side_effect: write
dry_run_supported: true
execute_supported: false
idempotency_required: true
required_payload_fields:
  - ip
  - duration_seconds
required_context_refs:
  - queue_id
  - run_id
  - approval_request_id
mcp:
  server: f5
  tool: f5_block_ip
  timeout_seconds: 10
```

## Port 设计

先在 SOC 层定义很窄的 provider port，避免 core/service 依赖 LangChain/MCP 类型。

```python
class SocMcpToolProviderPort(Protocol):
    def list_tools(self) -> list[SocMcpToolDescriptor]: ...
    def invoke(self, tool_name: str, payload: Mapping[str, Any], *, timeout_seconds: int) -> Mapping[str, Any]: ...
```

约束：

- 真实 provider 可以用 DeerFlow `get_cached_mcp_tools()` 查找 tool 并调用。
- 测试 provider 用 fake dict/tool callable，不能要求真实 MCP server。
- provider 类型不能进入 `core/service.py`、Gateway router、TUI、Web。
- adapter module 负责把 MCP exception 映射为 `SocAgentActionResult(status="failed")` 或 `SocActionAdapterRegistryError`。

## Payload 与输出

输入侧：

- adapter 只接受 `SocAgentActionCommand.payload`。
- adapter 必须校验 descriptor 的 `required_payload_fields` 和 `required_context_refs`。
- adapter 可做字段映射，但不能让 MCP tool 自己解释 SOC payload。
- secrets 只能通过环境变量或 MCP server config 进入，不允许出现在 command payload、notes、dead-letter、audit payload。

输出侧：

- read-only adapter output 必须包含：
  - `adapter_id`
  - `adapter_kind=mcp`
  - `external_side_effect=read`
  - `tool_name`
  - `result_schema_version`
  - 经过脱敏/裁剪的业务字段
- write/destructive output 必须额外包含：
  - `execution_token_id`
  - `idempotency_key`
  - provider request id / operation id
  - rollback/compensation hint（如果 provider 支持）

## Runtime 边界

禁止：

- Lead Agent 直接 `tool_search` 后调用生产 MCP tool 来完成 SOC action。
- 从自然语言推断 MCP tool name 或 payload。
- 将 MCP tool result 原样返回给前端或 prompt。
- 未注册 `SocAgentActionAdapterDescriptor` 的 MCP tool 被执行。
- high-risk MCP tool 在没有 approval grant 的情况下执行。

允许：

- Lead Agent 输出 `asset.lookup` read-only proposal。
- Bridge 把 proposal 转成显式 `soc_route/action_payload`。
- Registry 找到 `asset.lookup` MCP-backed adapter。
- Adapter 调 DeerFlow cached MCP tool 查询资产系统。
- 结果以 `SocAgentActionResult.payload` 出现在 stream/API/TUI/Web。

## 配置治理

MCP bridge 配置不应该直接复用全部 `extensions_config.json`。

推荐分层：

```text
extensions_config.json
  -> MCP server 连接、OAuth、transport、secret env

soc_action_adapters.yaml / db managed config
  -> SOC route/action 到 MCP server/tool 的 allowlist mapping
  -> risk_level / side_effect / schema / timeout / owner / enabled
```

早期可以先放本地 YAML，后续进入 Web 管理：

| 字段 | 说明 |
|---|---|
| `enabled` | 默认 false；必须显式打开 |
| `owner` | 配置责任人或团队 |
| `environment` | `dev/staging/prod` |
| `route/action` | SOC 稳定 action 名称 |
| `mcp.server/tool` | DeerFlow MCP 连接和 tool 名 |
| `risk_level` | 必须和 `SocAgentActionPolicy` 对齐 |
| `external_side_effect` | `read/write/destructive` |
| `payload_schema` | 后续可升级为 JSON Schema |
| `timeout_seconds` | 防止 tool hang 住 agent runtime |

## 接入顺序

1. **MCP tool provider port + fake provider adapter tests**（Done）
   - 不接真实 MCP。
   - 固定 provider port、tool descriptor、timeout/error mapping、result payload。
   - 当前实现：`backend/soc_agent/actions/mcp.py`
     - `SocMcpToolDescriptor`
     - `SocMcpToolProviderPort`
     - `SocMcpToolActionAdapter`
     - `mcp_read_only_adapter_descriptor()`
   - 当前测试：`backend/tests/test_soc_mcp_adapters.py`

2. **MCP-backed read-only `asset.lookup` adapter behind explicit config**（Done）
   - 先用 fake provider 或本地 stub 验证 registry builder。
   - `execute_supported=True`，`external_side_effect=read`。
   - 无配置时继续使用 in-memory adapter 或 fail-fast，不影响本地。
   - 当前实现：`backend/soc_agent/actions/mcp.py`
     - `SocMcpActionAdapterConfig`
     - `SocMcpToolBindingConfig`
     - `build_mcp_action_adapter()`
     - `build_mcp_action_adapter_registry()`
   - 当前测试：`backend/tests/test_soc_mcp_adapters.py`
     - explicit config -> registry -> `asset.lookup` execute。
     - disabled config skip。
     - duplicate route/action fail-fast。
     - non-read-only config reject。

3. **DeerFlow cached MCP provider implementation**（Done）
   - 只在 adapter module import `deerflow.mcp.cache.get_cached_mcp_tools()`。
   - 按 tool name 精确查找，不做 fuzzy match。
   - 真实 MCP server 缺失时返回明确 adapter failure。
   - 当前实现：`DeerFlowCachedMcpToolProvider` in `backend/soc_agent/actions/mcp.py`。
   - provider 对外仍只暴露 `SocMcpToolProviderPort`：
     - `list_tools()` 返回 SOC `SocMcpToolDescriptor`，不会把 LangChain `BaseTool` 传出 adapter module。
     - `invoke()` 按 exact tool name 调 `BaseTool.invoke()`，执行 timeout，并把 dict / content+artifact / model dump / text 结果归一为 `Mapping`。
   - 当前测试使用 fake cached tool 和 monkeypatched `deerflow.mcp.cache.get_cached_mcp_tools()`，不要求真实 MCP server。

4. **Read-only config smoke wiring**（Done）
   - 固定本地显式 config 加载方式，支持 `.json/.yaml/.yml`。
   - 只接受顶层 list 或 `adapters: [...]`，不做目录扫描、不做自然语言推断。
   - 新增 `soc mcp smoke CONFIG --route asset.lookup --json ...`：
     - 默认使用 `DeerFlowCachedMcpToolProvider`。
     - `--dry-run` 只验证 adapter/tool 可用性，不调用 MCP tool。
     - execute smoke 输出 `SocMcpActionSmokeReport`，内含 `SocAgentActionResult`，用于检查 latency、payload size、payload 裁剪和 error mapping。
   - 当前测试仍用 fake cached tool，不要求真实 MCP server。

5. **Dev/staging read-only MCP smoke report contract**（Done）
   - `soc mcp smoke` 输出 `soc.mcp_action_smoke_report.v1`。
   - report 固定记录：
     - `duration_ms`
     - `action_payload_bytes`
     - `action_result_bytes`
     - `mcp_result_bytes`
     - `adapter_id / adapter_kind / mcp_server / tool_name / timeout_seconds`
     - `output_fields / output_filter_applied / mcp_result_keys`
     - `error_type / error_message`
     - `action_result`
   - config/load/registry/tool failure 也输出结构化 report，便于 CI 或本地 smoke 脚本归档。
   - 当前测试仍用 fake cached tool，不要求真实 MCP server。

6. **Real dev/staging read-only MCP live smoke**（Next）
   - 用 dev/staging MCP server 验证资产查询或 EDR process tree 查询。
   - 保存 `soc.mcp_action_smoke_report.v1`，评估延迟、失败率、payload size、敏感字段脱敏/裁剪情况。

7. **High-risk MCP preflight only**
   - `response.block_ip`、`endpoint.isolate_host`、F5 规则等先只接 dry-run / execute preflight。
   - execute_supported 默认 false。
   - 真实 execute 等 staging eval、审批、幂等、回滚策略稳定后再打开。

## 下一刀

建议做 **Real dev/staging read-only MCP smoke run**：

- 先不要接生产系统；用 dev/staging MCP server 验证 `asset.lookup` / EDR process tree 一类 read-only tool。
- 沿用已固定的本地显式 adapter config，后续再升级为 DB/Web managed config。
- smoke 验证 read-only path：config -> registry -> `DeerFlowCachedMcpToolProvider` -> `SocMcpActionSmokeReport.action_result.payload`。
- 保存 report，记录 latency、failure、payload size 和敏感字段裁剪情况；不开放 high-risk execute。
