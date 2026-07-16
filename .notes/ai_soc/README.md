# SOC Agent Notes Index

这个目录只放 SOC Agent 当前会反复使用的文档。根目录只保留主入口；专项资料按主题放入子目录，避免再次变成资料堆。

## Start Here

| 你要做什么 | 先看 | 再看 |
|---|---|---|
| 判断产品方向、阶段和优先级 | `soc-agent-solution.md` | `progress.md` 的当前待办 |
| 向老板/管理层解释为什么 Runtime 控制流程、LLM 只做受控推理 | `runtime-and-llm-control-strategy.md` | `soc-agent-solution.md` 的核心原则 |
| 开始下一刀开发 | `progress.md` | `.notes/reference-index/soc-agent-engineering-contracts.md` |
| 理解一条预警从进入到复核的完整过程 | `alert-lifecycle-flow.md` | `soc-agent-solution.md` 的服务章节 |
| 排查 message 新结构、字段遗漏、决策置信度和复核原因 | `soc-agent-solution.md` 的 Normalizer / Confidence 章节 | `.notes/reference-index/soc-agent-engineering-contracts.md` 的对应契约 |
| 查看平安经验如何进入系统 | `capabilities/pingan/onboarding.md` | `capabilities/pingan/capability-cards.md` |
| 拆分平安历史 prompt / 经验 / 工具 | `capabilities/pingan/knowledge-decomposition.md` | `memory/memory-tracking.md` |
| 查看平安专属知识候选 | `capabilities/pingan/knowledge-candidates.md` | `capabilities/pingan/source-docs/` |
| 查看当前哪些能力仍是 mock | `integrations/mock-and-real-register.md` | `capabilities/pingan/onboarding.md` 的 PA-12 |
| 设计外部工单/处置状态回流 | `integrations/external-disposition-sync.md` | 工程契约 external disposition 章节 |
| 设计记忆和经验沉淀 | `memory/memory-tracking.md` | `alert-lifecycle-flow.md` 的 memory flow |
| 管理授权活动、查看/回放授权匹配增强、规划变更窗口与护网身份 | `governance/governed-context-facts.md` | `soc-agent-solution.md` Section 7.4 |
| 设计 Lead/Sub Agent、skill、MCP 开放配置 | `governance/agent-profile-governance.md` | 工程契约 Profile / Skill / MCP 章节 |

## Directory Map

```text
ai_soc/
├── README.md
├── soc-agent-solution.md              # 权威产品/系统方案
├── runtime-and-llm-control-strategy.md # 管理层说明：Runtime-first + bounded LLM
├── progress.md                        # 长期进度台账和当前待办
├── alert-lifecycle-flow.md            # 当前端到端流程图谱
├── capabilities/
│   └── pingan/
│       ├── onboarding.md              # 平安经验输入与能力卡流程
│       ├── knowledge-decomposition.md # 平安经验拆分规则
│       ├── capability-cards.md        # PingAn capability card 台账
│       ├── knowledge-candidates.md    # PingAn tenant memory/policy/eval 候选
│       └── source-docs/               # 平安 APT/EDR/HIDS 原始经验资料
├── integrations/
│   ├── external-disposition-sync.md   # 外部工单/状态/理由同步协议
│   └── mock-and-real-register.md      # mock/fixture/真实替换台账
├── memory/
│   └── memory-tracking.md             # DB-first memory 与 retrieval policy
└── governance/
    ├── agent-profile-governance.md    # Lead/Sub Agent、Skill、MCP 配置治理
    └── governed-context-facts.md      # 授权/演练/变更等 typed fact 生命周期和 CLI
```

## Document Roles

| 文档 | 角色 | 更新规则 |
|---|---|---|
| `soc-agent-solution.md` | 当前权威方案；决定做什么、为什么做、先后顺序 | 产品方向、阶段范围、入口取舍变化时更新 |
| `runtime-and-llm-control-strategy.md` | 管理层说明；解释为什么不用全自主 Agent，而采用 Runtime-first + bounded LLM | 管理层沟通口径、LLM 使用策略或控制流哲学变化时更新 |
| `progress.md` | 开发进度台账；聊天记录不算进度 | 每个可验证切片完成后更新 |
| `alert-lifecycle-flow.md` | 当前系统完整过程说明；只写 as-is flow | 服务边界、状态流转、数据写入、命令入口变化时更新 |
| `capabilities/pingan/*` | 平安经验、能力卡、专属知识候选、源资料 | 新增/拆分/实现/废弃 PingAn card 或候选时更新 |
| `integrations/*` | 外部系统接入、mock 与真实替换边界 | 新增 mock、真实 provider、外部反馈协议变化时更新 |
| `memory/memory-tracking.md` | typed memory、candidate、confirmed memory、retrieval policy | memory contract、状态机、检索、projection 变化时更新 |
| `governance/governed-context-facts.md` | typed operational fact、版本、来源、有效期和授权匹配边界 | fact contract、生命周期、matcher 或 disposition policy 变化时更新 |
| `governance/agent-profile-governance.md` | agent profile、skill、MCP 开放配置治理 | profile 生命周期、权限和用户可配置范围变化时更新 |

## Maintenance Rules

- 不新增平行版“完整方案”；方向变化先改 `soc-agent-solution.md`。
- 不把 `progress.md` 当方案读；它只是状态和下一步台账。
- 不从 `archive/` 推导当前路线；归档只用于追溯。
- 不把平安 `source-docs/` 原文整体复制进 public skill、Lead Agent prompt 或 node prompt。
- 专项文档如果改变实现顺序，必须同步更新 `soc-agent-solution.md` 和 `progress.md`。
- 工程边界、API、事件、权限、测试规则必须同步到 `.notes/reference-index/soc-agent-engineering-contracts.md`。
