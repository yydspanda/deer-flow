# SOC Agent Profile Governance

> 目的：定义 SOC Lead Agent、Domain Sub Agent、Skill 和 MCP/tool group 的开放配置边界。结论是允许同事参与配置和沉淀经验，但生产启用必须经过校验、评测、审批和审计。

## 1. Decision

SOC Agent 可以复用 DeerFlow `lead_agent` 的自定义 agent/profile 思路，但只能把它作为 **profile 生成和交互编排机制**，不能让它替代 SOC Runtime。

最终边界：

```text
DeerFlow lead_agent
  -> 辅助生成/编辑 profile 草稿
  -> 作为 SOC Lead Agent / Domain Sub Agent 的交互外壳

SOC Runtime / Core Services
  -> 固定控制流、schema/domain validation、审计、replay、review queue、权限和审批

Policy / Approval / Audit
  -> 决定 profile 能否启用、tool 能否调用、memory 能否确认、action 能否执行
```

主控和 sub agent 都可以由 lead agent 辅助生成 profile 草稿，但不能直接生产生效。生成结果必须进入 `draft -> validated -> staging -> active -> archived` 生命周期。

## 2. Concepts

| 概念 | 说明 | 例子 |
|---|---|---|
| SOC Lead Agent Profile | 面向分析师的主控交互/编排 profile，负责理解任务、规划调查、选择 domain specialist / skill / action proposal | `soc-triage` |
| Domain Specialist Profile | capability-oriented 的专项第二视角，负责局部证据分析和建议，不负责最终状态流转 | `soc-network-specialist`、`soc-endpoint-specialist`、`soc-web-specialist`、`soc-email-specialist` |
| Domain Skill | 领域知识、SOP、字段解释、研判方法、提示词片段 | `edr-triage`、`asset-context`、`attack-direction` |
| MCP/tool group | 查询或执行外部系统能力的受控工具组 | `asset-readonly`、`edr-readonly`、`firewall-response` |
| Middleware Preset | 代码/operator 定义的 middleware 组合，不允许普通用户或模型自由拼装 | 当前 `SocLeadAgentApprovalMiddleware`；后续可形成命名 preset |
| Profile Registry | 未来开放自定义 profile 时才需要的治理注册表；当前四个 specialist 是代码/operator-owned managed config | Target: `SocAgentProfileRegistry` |

## 3. Lifecycle

Profile、skill 和 MCP 绑定都必须具备版本状态。

```text
draft
  -> validated
  -> staging
  -> active
  -> archived
```

| 状态 | 含义 | 可做什么 | 不允许 |
|---|---|---|---|
| `draft` | 同事或 lead agent 生成的草稿 | 编辑 SOUL、skill 文案、适用条件、readonly MCP 候选 | 参与生产研判 |
| `validated` | 通过 schema 和静态安全检查 | 进入样例评测 | 调用真实外部工具 |
| `staging` | 通过 golden sample / replay eval，可在测试环境或小流量灰度 | 对样例、测试库、dry-run 工具运行 | 默认影响生产决策 |
| `active` | 审批通过的生产版本 | 被 SOC Runtime 选择和调用 | 绕过 policy / audit |
| `archived` | 历史版本 | 可回溯、可 rollback source | 新调用 |

启用规则：

- `draft` 不能直接变 `active`。
- `active` 必须记录审批人、审批原因、评测集版本、profile hash、skill hash、tool group hash。
- 每次启用新版本必须保留旧版本，可 rollback。
- LLM 生成的内容默认是 candidate，不是 trusted configuration。

## 4. Permission Model

| 角色 | 能做什么 | 不能做什么 |
|---|---|---|
| `soc_analyst` | 创建/编辑 draft profile、skill 草稿、readonly MCP 候选，提交评测 | 发布 active、绑定高风险工具、改 policy |
| `detection_engineer` | 维护 source_type/rule_code/vendor 适用条件，补样例集，运行 eval | 绕过审批启用生产 |
| `shift_lead` | 审核 staging 结果，批准低风险 profile active | 批准高风险处置工具 |
| `platform_admin` | 管理 registry、middleware preset、tool group、回滚版本 | 绕过审计 |
| `soc_approver` | 批准高风险 action grant / response tool 使用 | 直接改 Runtime pipeline |

## 5. Configurable By Users

这些内容可以开放给同事配置，但默认进入 draft：

- profile 名称、描述、适用范围。
- `SOUL.md` 草稿。
- domain skill 文案、SOP、字段解释。
- skill 适用条件，例如 `source_type=edr`、`vendor=xxx`、`rule_code in (...)`。
- readonly MCP/tool group 候选绑定。
- golden sample / eval set 绑定。
- prompt 示例和期望输出说明。

允许 lead agent 辅助生成：

- SOC Lead Agent / Domain Sub Agent 的 `SOUL.md` 初稿。
- skill 草稿。
- profile 的 skill/tool group 推荐。
- eval case 初步分类建议。

## 6. Not User Configurable

这些内容必须由代码、管理员或审批控制：

- SOC Runtime pipeline 主流程。
- `SocAnalysisService`、`SocReviewService`、`SocMemoryService` 的业务语义。
- middleware preset 的定义和组合。
- DeerFlow custom-agent `middlewares` 路径；该字段只允许可信 profile installer/operator 写入，普通
  agent update API 必须原样保留而不能新增、删除或替换。
- high-risk MCP/tool group 的生产启用。
- approval policy、audit policy、rate limit、cost budget。
- confirmed memory / confirmed lesson 的直接写入。
- profile 从 `staging` 到 `active` 的发布。
- 自动处置动作，例如封禁 IP、隔离终端、禁用账号、下发阻断规则。

## 7. MCP Risk Levels

| 风险等级 | 例子 | 默认策略 |
|---|---|---|
| `readonly` | 资产查询、威胁情报查询、安全标签、历史告警查询、PostgreSQL readonly 查询 | 可开放 draft 绑定，active 仍需 allowlist 和 audit；只能绑定真实存在且已评审的 Provider |
| `analyst_write` | review correction、case note、ticket update、memory propose fact | 需要用户身份和 service command，不能直接写 confirmed knowledge |
| `high_risk` | block IP、isolate endpoint、disable account、push firewall rule、suppress alert rule、任意生产变更 MCP | 必须 human approval，默认 dry-run，不允许 profile 自行启用，也不得作为 unrestricted DeerFlow/MCP tool 暴露给模型 |

MCP/tool result 必须写入 run trace / audit，不能只进入 prompt 后丢失。

## 8. Runtime Selection

Runtime 不选择或运行 specialist；它仍固定完成告警处理。只有绑定 ReviewQueue 的
`soc-triage` Lead Agent 可在需要专项第二视角时提议 DeerFlow `task` 委派。LLM 可选择的专家
仍由 `SocLeadAgentDelegationMiddleware` 强制白名单：

| 问题类型 | 允许的 specialist |
|---|---|
| APT、NDR/NIDS、C2、方向/网络角色 | `soc-network-specialist` |
| EDR、HIDS、进程、文件、账号、终端影响 | `soc-endpoint-specialist` |
| HTTP、WAF/F5、反向代理、webshell、认证 | `soc-web-specialist` |
| phishing、邮件身份、链接/附件/QR、收件人影响 | `soc-email-specialist` |

它不允许 `general-purpose`/`bash`，不按 `source_type` 自动多跑模型，也不将专家文本回写为事实。

## 9. Current Code Shape

```text
backend/soc_agent/
  lead_agent.py            # soc-triage profile template + SOUL + trusted middleware path
  agent_profile.py         # DeerFlow per-user profile installer
  lead_agent_chat.py       # embedded SOC TUI outer proposal bridge
  middlewares/
    lead_agent_review_context.py # transient Web context injection + message provenance
    lead_agent_delegation.py     # bounded native task policy + stable advisory lineage
    lead_agent_approval.py # standard Web/Gateway after_model approval bridge
  subagents.py             # four managed DeerFlow CustomSubagentConfig profiles + installer
  actions/
    proposals.py           # shared proposal parser/policy/approval boundary

backend/app/gateway/
  soc_lead_agent_context.py  # authenticated queue/thread binding + artifact construction
  soc_lead_agent_messages.py # current-checkpoint assistant message resolver
  routers/soc_review.py      # authenticated human acceptance -> pending candidate
```

DeerFlow generic extension point is `AgentConfig.middlewares`; per-agent paths load before global
`extensions.middlewares` and exact duplicates are removed. SOC does not create a second LangGraph
runtime or a second profile store. A future profile registry/preset lifecycle remains governed work,
not a reason to replace these current boundaries.

Action proposal governance and memory-source acceptance are separate boundaries. The profile
middleware may turn an explicit proposal marker into a read-only result or Approval Inbox request;
it never writes memory. Conversely, the Web acceptance route can submit only a server-resolved
terminal assistant message to `SocReviewService.add_note()` after explicit human acceptance; it never
executes a proposal. Direct Web chat treats the queue ID only as an identity hint; Gateway builds the
bounded artifact through `SocReviewService`, persists a server-owned immutable thread binding, and the
profile middleware injects it transiently on every model call. The terminal assistant message carries
exact context provenance, which acceptance must match with the route queue and thread binding. This
Web bridge and the TUI outer bridge share contracts but remain separate entry adapters.

Specialist profiles are intentionally tool-free (`tools=[]`, `skills=[]`). The delegation middleware
filters the current `SocSkillContext.v2` by specialist and injects only reviewed runtime guidance with
the server-built bounded case. It enforces at most two distinct specialists per chat run, a 1,200
character Lead Agent question, a 32,000 character total projection, stable hashes, and fail-closed
handling for stopped/capped output or action markers. `max_turns=32` is the graph recursion budget
needed by the current middleware chain, not a user-configurable autonomous turn allowance.

Profile contract 草案：

```python
class SocAgentProfile(BaseModel):
    name: str
    role: Literal["lead", "subagent", "skill_agent"]
    status: Literal["draft", "validated", "staging", "active", "archived"]
    soul_path: str
    skills: list[str]
    mcp_groups: list[str]
    middleware_preset: str
    model: str | None = None
    risk_level: Literal["readonly", "analyst_write", "high_risk"]
    version: str
    profile_hash: str
```

## 10. Phase Plan

| Phase | 做什么 | 不做什么 |
|---|---|---|
| Phase 1 | 代码内置 profile 模板；不开放前端生成；固定 Runtime 和 ReviewQueue 闭环 | Profile Studio、生产自动处置 |
| Phase 2 | 绑定 ReviewQueue 的 Lead Agent 在 middleware 白名单内选择内置 capability specialist；记录 case/task/projection/profile lineage | Runtime 按 `source_type` 自动多跑模型；LLM 自由加载未知 skill/agent |
| Phase 3 | draft profile API/CLI、schema validate、offline eval、staging 状态 | draft 直接生产生效 |
| Phase 4 | Web Profile Studio：同事可创建 draft、跑 eval、提交审批；daemon 可灰度 active profile | 高风险工具无审批执行 |
| Phase 5 | profile marketplace/governance、跨团队复用、知识/RAG 联动、stale profile 检测 | 无版本审计的 prompt 管理 |

## 11. Acceptance Criteria

任何开放配置功能必须满足：

- 能看到谁创建、谁修改、谁审批、何时启用。
- 能看到 profile/skill/tool group 的版本和 hash。
- 能用 golden samples / replay eval 比较启用前后结果。
- 能 rollback 到上一 active 版本。
- 能禁止 draft/staging 影响生产决策。
- 能证明 high-risk MCP/tool 没有绕过 approval。
