# SOC Agent Notes Index

这个目录只放 SOC Agent 当前会反复使用的文档。根目录只保留主入口；专项资料按主题放入子目录，避免再次变成资料堆。

## Start Here

| 你要做什么 | 先看 | 再看 |
|---|---|---|
| 与颜耀明复盘人工交接、AI自动化机会和数据治理前置要求 | `briefings/yan-yaoming-automation-review-20260814/README.md` | `audits/alpha-journey-inventory.md` 与 `integrations/mock-and-real-register.md` |
| 判断当前在哪个交付阶段、正在做什么 | `progress.md` 的唯一当前指针 | `delivery-roadmap.md` 的 Stage、task 与 Gate |
| 审阅当前代码实际接通的完整 SOC 旅程 | `audits/alpha-journey-inventory.md` | `alert-lifecycle-flow.md` |
| 核对代码、方案、生命周期、工程契约和 Mock 台账是否一致 | `audits/alpha-consistency-audit.md` | `audits/alpha-journey-inventory.md` |
| 查看唯一完整性矩阵、P0/P1 阻塞项和冻结实施顺序 | `audits/alpha-completeness-matrix.md` | `delivery-roadmap.md` 的 Stage 3 |
| 准备当前能力演示、技术介绍、话术和答疑 | `reporting/README.md` | `reporting/capability-demo-runbook.md` |
| 追溯早期单告警 Boss Demo 验收 | `boss-demo-v0.1-runbook.md` | `delivery-roadmap.md` 的 Stage 1 Gate |
| 重跑 APT/EDR/HIDS Alpha 一键验收并审阅版本化报告 | `alpha-acceptance-runbook.md` | `audits/alpha-completeness-matrix.md` 的 `AC-23/24/49` |
| 准备 Alpha Gate 评审、部署/回滚和 Stage 4 交接 | `alpha-readiness-package.md` | 本地 `backend/.deer-flow/soc-alpha-readiness/alpha-readiness-report.json` |
| 查看 Alpha Gate 四方评审、具名批准范围与剩余部署门禁 | `alpha-gate-review.md` | `alpha-readiness-package.md` 的 Owner Review |
| 重跑并逐步审阅 Runtime/LLM/correlation/governance 或 Checkpoint D0-D11 产物 | `runtime-validation-runbook.md` | 本地 `backend/.deer-flow/soc-runtime-validation/` |
| 用 10 条完整告警从原始输入一直审阅到最终结论 | `validation/compact_zeus/e2e/README.md` | 本地 `backend/.deer-flow/soc-validation/e2e-ten-current/SUMMARY.md` |
| 审阅紧凑模型契约在冻结 20 条跨来源真实告警上的结构质量、局部降级、Token 和延迟 | 本地 `backend/.deer-flow/soc-validation/materiality-v4-alias-twenty-20260814-r1/QUALITY-REPORT.md` | 同目录 `quality-summary.json` 和 `items/` |
| 审阅模型坏 JSON、局部 Schema 错误、一次纠错和 deterministic fallback 是否可靠 | `validation/compact_zeus/e2e/README.md` 的 `analysis_output_quality` | 本地 `backend/.deer-flow/soc-validation/e2e-ten-output-resilience-20260813/SUMMARY.md` |
| 审核本轮 LLM 萃取的知识建议并决定落点 | `validation/compact_zeus/e2e/README.md` 的 Authority Boundary | 本地 `backend/.deer-flow/soc-validation/e2e-ten-current/knowledge-review/REVIEW.md` |
| 判断产品方向和系统设计 | `soc-agent-solution.md` | `delivery-roadmap.md` 的阶段边界 |
| 理解 SOC Analysis Runtime、DeerFlow/Codex 类 Agent Runtime 与 Agent Graph 的区别 | `architecture/runtime-and-agent-architecture.md` | `soc-agent-solution.md` Sections 1、5.3、6 |
| 面向管理或技术评审解释为什么 Runtime 控制流程、LLM 只做受控推理 | `reporting/project-brief.md` | `reporting/technical-solution.md` |
| 开始下一刀开发 | `progress.md` | `.notes/reference-index/soc-agent-engineering-contracts.md` |
| 理解一条预警从进入到复核的完整过程 | `alert-lifecycle-flow.md` | `soc-agent-solution.md` 的服务章节 |
| 排查 message 新结构、字段遗漏、决策置信度和复核原因 | `soc-agent-solution.md` 的 Normalizer / Confidence 章节 | `.notes/reference-index/soc-agent-engineering-contracts.md` 的对应契约 |
| 查看平安经验如何进入系统 | `capabilities/pingan/onboarding.md` | `capabilities/pingan/capability-cards.md` |
| 拆分平安历史 prompt / 经验 / 工具 | `capabilities/pingan/knowledge-decomposition.md` | `memory/memory-tracking.md` |
| 审阅旧方向 Prompt 如何迁成 `S/A/C/M/T/Policy`，以及 Runtime 如何裁决 attacker/victim | `capabilities/pingan/network-direction-knowledge-migration.md` | `soc-agent-solution.md` Section 5.6 |
| 审阅主分析后的条件式方向/角色反证、触发条件和失败关闭语义 | `alert-lifecycle-flow.md` 的 `role_verification_gate` | `soc-agent-solution.md` 的 AnalysisResult.v4 / conditional verification 章节 |
| 审阅同事提供的 PingAn `security-log-analysis` Skill Demo | `capabilities/pingan/security-log-analysis-skill-audit.md` | `capabilities/pingan/onboarding.md` 的 PA-13 |
| 核对旧 Zeus / Security Log Analysis 经验哪些已迁移、拒绝迁移或仍待业务确认 | `capabilities/pingan/legacy-knowledge-migration-matrix.md` | `capabilities/pingan/tenant-static-knowledge-migration.md` |
| 审阅 PingAn 处置策略来源、200/非200语义、确定性规则与 Policy Skill 分工 | `capabilities/pingan/disposition-policy-extraction.md` | `soc-agent-solution.md` Section 7.4.3 |
| 重跑 PingAn 租户处置策略与四阶段端到端验证 | `validation/compact_zeus/e2e/README.md` | 本地 `e2e-ten-pingan-policy-current/SUMMARY.md` |
| 核对旧 Zeus `flows/` 的研判能力迁到哪里、哪些明确不迁 | `capabilities/pingan/legacy-zeus-capability-extraction.md` | `capabilities/pingan/knowledge-decomposition.md` |
| 查看平安专属知识候选 | `capabilities/pingan/knowledge-candidates.md` | `capabilities/pingan/source-docs/` |
| 查看当前哪些能力仍是 mock | `integrations/mock-and-real-register.md` | `capabilities/pingan/onboarding.md` 的 PA-12 |
| 盘点所有外部接入、未完成项及其权威任务归属 | `integrations/README.md` | `delivery-roadmap.md` 的 PI 阶段 |
| 重跑已完成的 PI-05A/B rollout/completion 仿真，或恢复已停放的真实接入验收 | `backend/samples/rollout/README.md` | `delivery-roadmap.md` 的 PI-05 分解；真实接入再看 `integrations/pingan-internal-continuation-handoff.md` |
| 查看旧 sec-model/ZEUS 状态/safe-path 审计结论 | `integrations/pingan-legacy-source-audit.md` | `integrations/pingan-internal-continuation-handoff.md` |
| 设计外部工单/处置状态回流 | `integrations/external-disposition-sync.md` | 工程契约 external disposition 章节 |
| 设计通用记忆和经验沉淀 | `memory/memory-tracking.md` | `alert-lifecycle-flow.md` 的 memory flow |
| 审阅 PingAn Memory 的同类关联、候选质量、改判、反馈和失效闭环 | `memory/pingan-soc-memory-design.md` | `governance/decision-disposition-action-automation.md` |
| 理解 Memory 改判、无 Memory 自动处置和完整动作留痕 | `governance/decision-disposition-action-automation.md` | `soc-agent-solution.md` Section 7.2 / 10 |
| 管理授权活动、影子处置建议、抽样 outcome 与评测 gate，规划变更窗口与护网身份 | `governance/governed-context-facts.md` | `soc-agent-solution.md` Section 7.4 |
| 设计 Lead/Sub Agent、skill、MCP 开放配置 | `governance/agent-profile-governance.md` | 工程契约 Profile / Skill / MCP 章节 |

## Directory Map

```text
ai_soc/
├── README.md
├── delivery-roadmap.md               # 唯一阶段顺序、Gate 和防跑偏规则
├── boss-demo-v0.1-runbook.md          # Stage 1 演示命令、话术、边界与验收记录
├── alpha-acceptance-runbook.md        # Stage 3 Alpha 单命令验收、证据包和结论边界
├── alpha-readiness-package.md         # BG-03 Alpha Gate 评审、部署/回滚与 Stage 4 handoff
├── alpha-gate-review.md               # 产品/SOC/安全/平台独立评审意见和正式签字表
├── runtime-validation-runbook.md      # Step 01-12 重跑命令、产物与最新审阅结论
├── soc-agent-solution.md              # 权威产品/系统方案
├── progress.md                        # 唯一当前指针和最多 10 条近期完成记录
├── alert-lifecycle-flow.md            # 当前端到端流程图谱
├── architecture/
│   └── runtime-and-agent-architecture.md # SOC Runtime、Agent Graph、DeerFlow/Codex 对照与分享材料
├── reporting/
│   ├── README.md                       # 当前汇报材料入口与真实性口径
│   ├── capability-demo-runbook.md      # 4,343 条语料的能力演示路线与现场话术
│   ├── project-brief.md                 # 一页介绍、成熟度、八页汇报结构与确认事项
│   ├── technical-solution.md           # Runtime/Agent/Memory/Decision 技术方案
│   └── reporting-faq.md                # 常见追问与建议回答
├── audits/
│   ├── alpha-journey-inventory.md      # AUD-01 as-is 入口/服务/状态/表/可见产物盘点
│   ├── alpha-consistency-audit.md      # AUD-02 代码/契约/文档/Mock 事实差异审计
│   └── alpha-completeness-matrix.md    # AUD-03 唯一完整性矩阵、阻塞台账与冻结工作包
├── capabilities/
│   └── pingan/
│       ├── onboarding.md              # 平安经验输入与能力卡流程
│       ├── knowledge-decomposition.md # 平安经验拆分规则
│       ├── legacy-zeus-capability-extraction.md # 旧 Zeus flows 研判能力去向与 D7/D8 验证边界
│       ├── legacy-knowledge-migration-matrix.md # 旧经验逐项迁移状态、语料证据和人工确认项
│       ├── disposition-policy-extraction.md # 平安处置经验来源、分层归属、配置与回放边界
│       ├── network-direction-knowledge-migration.md # 旧方向知识的 S/A/C/M/T/Policy 迁移与角色裁决边界
│       ├── capability-cards.md        # PingAn capability card 台账
│       ├── knowledge-candidates.md    # PingAn tenant memory/policy/eval 候选
│       ├── security-log-analysis-skill-audit.md # 同事 Skill Demo 的拆解、路由修复与迁移边界
│       └── source-docs/               # 平安 APT/EDR/HIDS 原始经验资料
├── integrations/
│   ├── README.md                       # 外部接入完成/未完成/deferred 全量交叉索引
│   ├── external-disposition-sync.md   # 外部工单/状态/理由同步协议
│   ├── pingan-dev-information-collection.md # 内网 DEV 配置/契约/样本收集清单
│   ├── pingan-legacy-source-audit.md  # 旧模型/签名/状态/safe-path 源码审计
│   ├── pingan-internal-continuation-handoff.md # 内网剩余步骤和命令
│   └── mock-and-real-register.md      # mock/fixture/真实替换台账
├── memory/
│   ├── memory-tracking.md             # DB-first generic Memory kernel 与 retrieval policy
│   └── pingan-soc-memory-design.md     # PingAn profile、模式候选、命中改判与反馈演化
└── governance/
    ├── agent-profile-governance.md    # Lead/Sub Agent、Skill、MCP 配置治理
    ├── decision-disposition-action-automation.md # 改判、处置、授权、执行和 Memory 影响边界
    └── governed-context-facts.md      # 授权/演练/变更等 typed fact 生命周期和 CLI
```

## Document Roles

| 文档 | 角色 | 更新规则 |
|---|---|---|
| `delivery-roadmap.md` | 唯一阶段性交付路线；决定当前阶段、后续顺序和 Gate | 阶段范围、顺序、退出条件或 Parking Lot 归属变化时更新 |
| `boss-demo-v0.1-runbook.md` | Stage 1 可复跑演示手册和汇报证据 | 每完成 BD task、命令/入口/结果变化或彩排后更新 |
| `alpha-acceptance-runbook.md` | Stage 3 release-level Alpha 验收命令、证据结构、失败语义和 mock/data-gated 声明 | 验收命令、报告 schema、覆盖面或结论边界变化时更新 |
| `alpha-readiness-package.md` | Stage 3 退出评审包；汇总技术门禁、部署/回滚和 Stage 4 外部输入，不拥有能力状态 | Alpha Gate 证据、部署/回滚边界、签字要求或 Stage 4 handoff 变化时更新 |
| `alpha-gate-review.md` | Stage 3 四方评审意见、风险分级、PI 责任角色与具名签字记录；不拥有阶段/能力状态 | 评审建议、具名 decision、PI owner 或 release/change record 变化时更新 |
| `runtime-validation-runbook.md` | Runtime/eval/governance 本地逐步验证命令、产物契约和审阅结果 | 验证脚本、步骤分类、样本结果或门禁语义变化时更新 |
| `soc-agent-solution.md` | 当前权威产品/系统方案；决定做什么、为什么做 | 产品方向、架构、服务边界或入口取舍变化时更新 |
| `progress.md` | 唯一活动执行指针；聊天记录不算进度，历史不在此累积 | task 切换或切片完成后更新；超出 10 条的完成记录按月移入 `.notes/archive/ai_soc/progress/` |
| `alert-lifecycle-flow.md` | 当前系统完整过程说明；只写 as-is flow | 服务边界、状态流转、数据写入、命令入口变化时更新 |
| `architecture/runtime-and-agent-architecture.md` | 解释 SOC Analysis Runtime、Agent Runtime、Agent Graph 的差异和组合方式；不拥有路线图或实现状态 | 控制流、Runtime/Agent 权限边界或 DeerFlow 装配方式发生实质变化时更新 |
| `reporting/*` | 当前能力演示、项目介绍、技术摘要和 FAQ；只引用权威状态，不另建路线图 | 演示入口、固定案例、对外口径或能力真实性边界变化时更新 |
| `audits/alpha-journey-inventory.md` | AUD-01 代码现状证据清单；为一致性审计和唯一缺口矩阵提供输入 | 入口、service、状态机、持久化表或用户可见 surface 发生实质变化时更新 |
| `audits/alpha-consistency-audit.md` | AUD-02 代码、权威文档和 Mock/real 性质差异清单；为 AUD-03 提供事实输入 | 被审文档或对应实现发生变化，并且差异已重新核实时更新 |
| `audits/alpha-completeness-matrix.md` | AUD-03 唯一能力分类、阻塞台账和 Stage 3 冻结输入；禁止从聊天另起任务清单 | Gap 被关闭、外部条件到位或用户明确调整阶段范围时更新 |
| `capabilities/pingan/*` | 平安经验、能力卡、专属知识候选、源资料 | 新增/拆分/实现/废弃 PingAn card 或候选时更新 |
| `integrations/*` | 外部系统接入、mock 与真实替换边界 | 新增 mock、真实 provider、外部反馈协议变化时更新 |
| `memory/memory-tracking.md` | typed memory、candidate、confirmed memory、retrieval policy | memory contract、状态机、检索、projection 变化时更新 |
| `memory/pingan-soc-memory-design.md` | PingAn same-class profile、模式候选、适用范围、反馈/健康/修订闭环 | PingAn profile、Memory evolution、审核或失效规则变化时更新 |
| `governance/decision-disposition-action-automation.md` | base/effective decision、disposition、authorization、execution 的唯一专项说明 | Memory decision directive、automation policy、动作执行或 lineage contract 变化时更新 |
| `governance/governed-context-facts.md` | typed operational fact、版本、来源、有效期和授权匹配边界 | fact contract、生命周期、matcher 或 disposition policy 变化时更新 |
| `governance/agent-profile-governance.md` | agent profile、skill、MCP 开放配置治理 | profile 生命周期、权限和用户可配置范围变化时更新 |

## Maintenance Rules

- 不新增平行版路线图；阶段顺序只改 `delivery-roadmap.md`，产品/架构方向只改 `soc-agent-solution.md`。
- `progress.md` 必须且只能声明一个 Current Stage 和一个 In Progress Task；task 必须存在于 `delivery-roadmap.md`，未通过 Gate 不切换阶段。
- 不把 `progress.md` 当方案读；它只是状态和下一步台账。
- 不从 `archive/` 推导当前路线；月度进度归档只用于追溯，迁移前 legacy 记录不补造实验元数据。
- 模型、Prompt/config、语料、性能或对比实验必须使用 `soc-experiment` manifest，记录 upstream commit、模型、config/data hash、硬件、命令与指标。
- 修改 progress/Roadmap/归档后运行 `python scripts/check_soc_progress.py`；每周 upstream 漂移由 `soc-project-governance` CI 检查。
- 不把平安 `source-docs/` 原文整体复制进 public skill、Lead Agent prompt 或 node prompt。
- 专项文档如果改变实现顺序，必须同步更新 `soc-agent-solution.md` 和 `progress.md`。
- 工程边界、API、事件、权限、测试规则必须同步到 `.notes/reference-index/soc-agent-engineering-contracts.md`。
