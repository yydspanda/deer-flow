# SOC Agent 文档入口

这个目录只保留当前 SOC Agent 开发会反复使用的文档。低频实现计划、历史会议纪要、已完成切片和暂缓项都放到 `.notes/archive/ai_soc/`，避免顶层再次变成资料堆。

## 怎么用

| 你要做什么 | 先看 | 再看 |
|---|---|---|
| 判断产品方向、阶段和优先级 | `soc-agent-solution.md` | `progress.md` 的当前待办 |
| 开始下一刀开发 | `progress.md` | `alert-lifecycle-flow.md` 和 `.notes/reference-index/soc-agent-engineering-contracts.md` |
| 理解当前告警从进入到复核的完整流转 | `alert-lifecycle-flow.md` | `soc-agent-solution.md` 的入口/服务章节 |
| 输入平安 SOC 经验、工具、MCP、skill 想法 | `pingan-soc-capability-onboarding.md` | 需要落工程时再看工程契约 |
| 拆分平安 prompt/经验/工具/记忆 | `pingan-knowledge-decomposition-plan.md` | `soc-memory-tracking-plan.md` 和相关 `skills/public/soc-*` |
| 查看平安 APT/EDR/HIDS capability cards | `pingan-capability-cards.md` | `pingan-soc-capability-onboarding.md` 的 TODO |
| 查看平安知识候选/记忆候选 | `pingan-knowledge-candidates.md` | `soc-memory-tracking-plan.md` 和后续 DB memory contract |
| 查看当前哪些能力仍是 mock / fixture / in-memory | `mock-and-real-integration-register.md` | `pingan-soc-capability-onboarding.md` 的 PA-12 |
| 设计外部系统状态/理由同步 | `external-disposition-sync-plan.md` | `soc-agent-solution.md` 和工程契约中的 external disposition 约束 |
| 设计记忆和经验沉淀 | `soc-memory-tracking-plan.md` | external disposition / review / domain triage 的来源约束 |
| 设计 Lead/Sub Agent、skill、MCP 开放配置 | `soc-agent-profile-governance.md` | 工程契约中的 Profile / Skill / MCP 章节 |

## 当前保留文档

| 文档 | 角色 | 更新规则 |
|---|---|---|
| `README.md` | 本目录使用说明 | 目录结构变化时更新 |
| `soc-agent-solution.md` | 权威产品/系统方案，决定做什么和先后顺序 | 产品方向、阶段路线、入口取舍变化时必须更新 |
| `progress.md` | 长期开发进度台账和当前待办 | 每个可验证切片完成后更新；聊天记录不算进度 |
| `alert-lifecycle-flow.md` | 当前 As-Is 生命周期和下一阶段 To-Be 研判链路 | 状态机、服务边界、下一阶段演示链路变化时更新 |
| `pingan-soc-capability-onboarding.md` | 平安 SOC 经验输入模板 | 新增业务经验、tool/MCP/skill/SOP card 时更新 |
| `pingan-knowledge-decomposition-plan.md` | 平安 APT/EDR/HIDS 文档到 skill、memory、MCP、policy、eval 的拆解矩阵 | 拆解规则、artifact 边界或第一批 capability cards 变化时更新 |
| `pingan-capability-cards.md` | 平安 APT/EDR/HIDS capability card 台账 | 新增、拆分、实现、废弃平安 card 时更新 |
| `pingan-knowledge-candidates.md` | 平安专属知识候选清单 | 新增、确认、拒绝、过期或迁移 PingAn memory/policy/adapter/eval candidate 时更新 |
| `mock-and-real-integration-register.md` | mock、fixture、in-memory、本地 MCP smoke 与真实接入替换台账 | 新增 mock、替换真实 provider、完成 smoke/eval 或调整 PA-12 状态时更新 |
| `external-disposition-sync-plan.md` | 外部预警/工单/处置系统状态与理由同步方案 | 实现 external feedback contract 前后更新 |
| `soc-memory-tracking-plan.md` | DB-first typed memory 与候选记忆方案 | memory contract、检索、确认、wiki/OKF projection 变化时更新 |
| `soc-agent-profile-governance.md` | SOC Lead/Sub Agent、Skill、MCP 开放配置治理 | 配置开放、profile 生命周期、skill/MCP 治理变化时更新 |

## 不要怎么用

- 不要把 `progress.md` 当方案读；它是台账，只负责记录状态和下一步。
- 不要从归档文档直接推导当前路线；归档只用于追溯。
- 不要新增平行版“完整方案”。方向变化先改 `soc-agent-solution.md`。
- 不要为每个小讨论都新增新文档；能放进现有文档的，优先合并。
- 不要让专项方案脱离主线。如果专项方案会影响实现顺序，必须同步更新 `progress.md` 和 `soc-agent-solution.md`。

## 归档位置

| 目录 | 内容 |
|---|---|
| `.notes/archive/ai_soc/implementation-plans/` | 已完成或低频的 Kafka/MCP/action adapter 实现计划 |
| `.notes/archive/ai_soc/deferred/` | 当前不进入 Alpha 的暂缓项 |
| `.notes/archive/ai_soc/reference/` | 已被主方案或工程契约吸收的背景参考 |
| `.notes/archive/ai_soc/runbooks/` | 部署/运行 runbook，等进入对应阶段再提升为 active |
