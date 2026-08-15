# Native Agent Tool Calls and Trusted Target Binding

> 状态：Deferred / architecture hardening。当前 Lead Agent 的动作建议已经经过 JSON 解析、
> Pydantic 校验、白名单路由、服务端策略和审批边界；本项要替换的是模型到动作建议之间的自定义文本协议，
> 不是重写固定 SOC Runtime，也不是立即开放高风险自动执行。

## 为什么后置

当前动作建议使用：

```text
Lead Agent text
  -> <soc_action_proposal>{...}</soc_action_proposal>
  -> JSON/Pydantic parser
  -> SocAgentActionPolicy
  -> Approval Inbox or read-only dispatcher
```

它已经能拒绝坏 JSON、未知动作和未授权高风险动作，因此当前没有必须立刻重构的阻塞缺陷。但
`SocAgentActionProposal.payload` 仍是通用字典，合法字符串不等于可信目标：模型可能生成格式正确、却没有
绑定到当前告警实体的 IP、主机或路径。将这条链迁移为 DeerFlow 原生工具调用会跨越 Lead Agent profile、
工具 Schema、ReviewQueue context、动作服务、流式事件、Web/TUI、审计和 replay，属于中大型边界迁移，适合
独立排期。

## 目标设计

```text
DeerFlow SOC Lead Agent
  -> native propose_soc_action tool call
       action
       target_ref / governed argument refs
       reason
       confidence
  -> trusted context resolver
       validates context_hash, entity type, role and freshness
       materializes exact IP / host / path from Runtime-owned catalogs
  -> existing proposal service
  -> existing server-owned policy / approval / automation
  -> typed adapter or MCP
  -> append-only action lineage
```

核心原则：模型选择一个 Runtime 已知的目标引用，不重新抄写会被机器执行的 IP、主机名、账号或路径。
Schema 只保证结构；目标解析、权限和执行授权继续由服务端负责。

## 实施切片

### NT-01 Native proposal tool

- 在 SOC 扩展层增加 `propose_soc_action` 的 LangChain/DeerFlow structured tool。
- 使用 action-specific discriminated input，不再接收无边界 `payload: dict`。
- 通过现有 SOC profile/中间件扩展点装配，不修改 DeerFlow 通用 Lead Agent 核心。
- 工具只创建 proposal，绝不直接调用外部响应动作。

### NT-02 Trusted target resolver

- 从当前 ReviewQueue/Runtime bounded context 生成稳定、类型化的 `target_ref`。
- 绑定 `queue_id`、`run_id`、`context_hash`、实体类型、角色和证据 lineage。
- unknown、ambiguous、expired、wrong-type 或跨 context 引用必须 fail closed。
- `response.block_ip` 等动作的最终参数只能由 resolver materialize，不能采用模型自由填写值。

### NT-03 Compact machine contracts

- Role Verifier 以 `claim_id + status + evidence/context refs` 为机器权威输出；替代角色只能引用已有
  typed entity，不重复生成 IP/host。
- Tenant Policy Advisor 返回候选 clause/rule ID、匹配状态和引用；实际 disposition/review effect 仍由
  deterministic evaluator 映射。
- 分析摘要和 rationale 保持自然语言。叙述中的实体拼写不一致只形成质量告警，不因可选文本缺陷废弃
  已通过的核心 verdict。

### NT-04 Migration and removal

- 先做 native proposal 与旧 marker 的离线 replay diff，不能双执行。
- Web/TUI 使用 DeerFlow 原生 tool-call/progress 事件展示 proposal 状态。
- 迁移已保存 transcript/replay reader 后，停止生成并删除
  `<soc_action_proposal>` 文本协议；历史记录保持只读可解释。
- 更新协议版本、契约快照、架构测试和回滚开关。

## 固定边界

- 固定 SOC Runtime 仍拥有 mandatory pipeline；不能把整个 `AnalysisResult` 改造成自主 Agent 工具循环。
- Skill 负责方法、知识和路由提示，不承担机器执行协议，也不需要全部改成 JSON 输出。
- 原生 Tool Call 和严格 Schema 都不能授予动作权限；Memory、Skill、模型、MCP 和工具结果仍不能绕过
  server-owned policy、RBAC、审批、幂等和审计。
- read-only lookup 可以在受控策略下自动执行；写入或破坏性动作继续遵守既有 automation/approval policy。
- 在 NT-02 或等价的可信目标绑定完成前，不得仅凭 Lead Agent 自由填写的 payload 开放无人值守高风险执行。

## 重新启动条件

满足任一条件即可提议重新排期，但仍需经过路线图评审：

- 准备让 Lead Agent proposal 进入无人值守写入或高风险执行链。
- replay/线上指标证明 marker 解析失败或目标值漂移已成为显著质量问题。
- Web/TUI 需要完整复用 DeerFlow 原生 tool-call lifecycle，而自定义文本 marker 成为阻塞点。
- 当前配置模型和内网网关已稳定支持 DeerFlow/LangChain structured tool calling。

## 验收标准

- 模型不能通过自由字符串创造 `block_ip`、`isolate_host` 等动作目标。
- 同一个 tool call 可追溯到 frozen context、typed target、proposal、policy decision、approval、execution 和结果。
- unknown/ambiguous/stale/cross-context target ref 均拒绝，且拒绝不会产生外部副作用。
- malformed optional rationale 只降低展示质量，不删除已验证的核心结论或触发额外模型调用。
- native/legacy replay diff 可解释；切换期间任何 proposal 最多进入一次服务端治理链。
- focused contract/service/middleware tests、SOC architecture tests、Web/TUI stream tests 和代表样本 replay 全部通过。

