# SOC Agent Investigation from Analysis Gaps

Status: Deferred

## Intent

未来实现 SOC Lead Agent 动态调查时，将 `AnalysisResult.analysis.evidence_gaps` 作为调查目标，
将 `AnalysisResult.analysis.manual_checks` 作为候选检查方法。Agent 还必须同时消费服务端构建的
`InvestigationContext`、canonical entities、`E-*`/`R-*` 引用、确认 Memory、租户知识和当前可用
能力清单，不能只读取两个自由文本数组。

## Required Boundary

```text
immutable AnalysisRun
  -> server-built InvestigationContext
  -> typed, versioned InvestigationTask planning
  -> allowlisted Skill / read-only action proposal
  -> governed dispatcher / MCP adapter
  -> persisted InvestigationEvidence
  -> versioned Investigation Addendum
```

- `evidence_gaps` 表达“为什么继续查”，不是 Provider 调用指令。
- `manual_checks` 表达“可以怎么查”，不是可信命令或已存在能力证明。
- Agent 必须把自由文本建议绑定到 canonical entity/evidence refs，并映射到服务端 allowlist 后才能
  提出工具调用。
- 每个任务至少区分 `pending`、`running`、`resolved`、`partially_resolved`、`unavailable` 和
  `not_applicable`，并保留 attempt、结果证据和停止原因。
- 新调查结果不得覆盖 immutable Base Decision；需要重新判断时生成有版本和引用的 investigation
  addendum，再进入独立 Effective Decision/Policy 流程。

## Activation Conditions

- selected-case SOC Lead Agent 调查进入正式排期；
- 明确交互式人工触发与自动触发的边界；
- 冻结 `InvestigationTask` 版本化契约、任务预算、停止条件和幂等规则；
- 具备至少一个可验证的只读 Provider，并能持久化 `InvestigationEvidence`；
- 使用代表性告警验证任务映射准确率、无效调用率、延迟、Token 和人工接管率。

## Non-goals

- 当前不修改 Runtime、Lead Agent prompt、Enrichment Planner 或 MCP 路由。
- 不因任意非空 `evidence_gaps` 自动启动 Agent；并非所有告警都需要动态调查。
- 不允许自由文本绕过 capability router、租户策略、权限、审批或审计。
- 不把 Agent 输出直接确认为事实、Memory、最终结论或自动处置授权。

## First Slice When Activated

先实现 provider-free planning replay：从冻结的 `AnalysisRun` 生成 typed task candidates，展示每条
gap/manual check 如何绑定实体、证据和候选 route；不实际调用工具。通过人工样本审阅后，再接现有
`SocAgentCapabilityRouter -> SocAgentActionDispatcher -> SocActionAdapterRegistry` 路径。
