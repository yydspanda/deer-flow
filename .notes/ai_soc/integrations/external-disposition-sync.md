# External Disposition Sync

> Updated: 2026-07-16
>
> 本文档定义 SOC Agent 与外部预警/工单/处置系统同步人工状态和处置理由的产品与工程边界。Zeus 是第一个接入场景，但协议不能写死 Zeus；未来要能接客户自研 SOC、SIEM/SOAR、ServiceNow、Jira、ITSM 或其他工单系统。

## 1. 结论

必须做，但它是独立的 **external disposition feedback lane**，不是 Kafka 原始告警 ingestion lane。

```text
外部系统状态/理由更新
  -> ExternalDispositionAdapter
  -> SocExternalDispositionService
  -> audit / review / correction sync
  -> guarded disposition evaluation outcome
  -> memory candidate
  -> skill improvement candidate
```

外部系统仍可以是分析师当前主操作界面。SOC Agent 不要求第一阶段替换 Zeus 页面，而是把 Zeus 中的人工处置状态、理由、标签和复核结果同步回来，形成可审计、可回放、可学习的反馈闭环。

当前实现状态：

- Done：`SocExternalDispositionEvent`、canonical status、adapter/mapping config、`SocExternalDispositionRecord`、`SocExternalDispositionApplyResult`。
- Done：通用 field-path mapper，可用 Zeus mock fixture 转成 vendor-neutral event，不在 core 写死 Zeus。
- Done：`SocExternalDispositionService.apply_event()` + repository protocol + in-memory repository，支持状态映射、目标定位、幂等、unmatched 和 audit。
- Done：高可信 mapped event 在唯一定位本地 target 后复用 `SocReviewService.correct()`，同步 operational correction 并关闭 review queue；低可信、未知状态、无法定位仍不改判。
- Done：mapped 且可定位的外部 reason 可通过 `SocMemoryService.propose_candidate()` 生成 `SocMemoryCandidate(status=pending_review)`；未知/无法定位/无 reason 不生成候选。
- Done：external disposition PostgreSQL persistence、ReviewQueue context API visibility、Web/TUI display、Lead Agent bounded context display。
- Done：EV-02 guarded bridge。只有 high-trust mapped event、verified target 和唯一 matching shadow proposal
  才通过 `SocDispositionEvaluationService` 写 external-source outcome；apply result/audit/event 暴露 outcome id 或
  skip reason，重复 event 可幂等补写。
- Done：DB-first memory candidate persistence、review workflow、confirmed-memory/retrieval boundary。
- Not yet：skill improvement candidate aggregation、真实外部 endpoint/credential integration。

## 2. 产品目标

| 目标 | 说明 |
|---|---|
| 同步人工结论 | 分析师在外部系统更新状态和理由后，SOC Agent 能收到并记录 |
| 保留审计链 | 谁在什么系统、什么时间、把哪个 case 改成什么状态，必须可追踪 |
| 驱动本地状态 | 可置信映射后更新 ReviewQueue / Correction / operational disposition |
| 形成学习候选 | 人工理由进入 `SocMemoryCandidate`，必要时生成 skill improvement candidate |
| 可扩展接入 | Zeus 只是 adapter；核心协议不依赖 Zeus 字段、状态名或 ID 体系 |

## 3. 非目标

- 不把外部 free-text reason 直接变成 confirmed memory。
- 不让外部状态更新直接修改 skill、prompt、domain handler 或 action policy。
- 不把 SOC Agent 做成 Zeus 的强耦合插件。
- 不要求第一阶段双向同步所有字段；先做外部处置结果单向进入 SOC Agent。
- 不在 webhook / Kafka callback / polling callback 里写复杂业务逻辑。

## 4. 核心协议

建议核心输入事件命名为 `SocExternalDispositionEvent`，版本为 `soc.external_disposition.v1`。

| 字段 | 必填 | 说明 |
|---|---|---|
| `schema_version` | 是 | 固定 `soc.external_disposition.v1` |
| `tenant_id` | 否 | 多租户部署时必填；单租户/本地开发可由配置补默认值 |
| `external_system` | 是 | 例如 `zeus`、`servicenow`、`jira`、`custom_soc` |
| `external_case_id` | 是 | 外部预警单、工单或 case id |
| `source_event_id` | 否 | 外部系统自己的事件 id；没有时由 adapter 用 payload hash 生成 |
| `source_version` | 否 | 外部工单版本、更新时间游标或 sequence |
| `external_alert_ref` | 否 | 外部告警 id、rule id、ticket key 等原始引用 |
| `soc_alert_id` | 否 | 已知本地 alert id，存在时优先精确定位 |
| `soc_run_id` | 否 | 已知本地 run id |
| `soc_queue_id` | 否 | 已知本地 review queue id |
| `external_status` | 是 | 外部原始状态，不要求枚举统一 |
| `external_reason` | 否 | 分析师填写的处置理由或备注 |
| `external_tags` | 否 | 外部标签、分类、处置动作摘要 |
| `operator` | 否 | 外部操作人、角色、团队 |
| `updated_at` | 是 | 外部系统记录的更新时间 |
| `raw_payload_hash` | 是 | 原始 payload 稳定 hash；原文是否保存由部署策略决定 |

### Canonical Mapping

外部状态必须经过可配置映射后才进入本地语义。核心枚举建议从少量稳定状态开始：

| Canonical status | 用途 |
|---|---|
| `closed_true_positive` | 人工确认有风险或真实攻击 |
| `closed_false_positive` | 人工确认误报 |
| `closed_benign_true_positive` | 行为真实但业务可接受 |
| `suppressed` | 已抑制、降噪或不再触发处置 |
| `escalated` | 升级到其他团队或更高优先级 |
| `ignored` | 资产/测试/非管辖范围忽略 |
| `duplicate` | 合并到已有 case |
| `unknown` | 无法可靠映射，进入待复核 |

映射规则由客户/系统 adapter 配置，不写死在 runtime。未映射状态只能生成 `unknown` disposition record 和 review note，不能自动改判。

## 5. 服务边界

```text
Webhook / Kafka / Polling / Manual import
  -> ExternalDispositionAdapterPort
  -> SocExternalDispositionEvent
  -> SocExternalDispositionService.apply_event()
  -> repositories through service only
```

| 层 | 职责 | 禁止 |
|---|---|---|
| Adapter | 认证、解码、字段映射、幂等键生成、调用 service | 直接写 repository、直接改 review/correction/memory |
| Service | schema validation、状态映射、目标定位、审计、状态同步、候选记忆生成 | 写 confirmed memory、修改 skill、执行高风险 action |
| Repository | 保存 disposition record、audit、memory candidate 等 | 承载业务判断 |
| Idle jobs | 聚类理由、提出 skill/memory 优化候选 | 自动激活优化 |

## 6. 幂等与目标定位

幂等键建议：

```text
external_disposition:{tenant_id|default}:{external_system}:{external_case_id}:{source_event_id|source_version|updated_at_hash}
```

目标定位顺序：

1. 精确引用：`soc_queue_id`、`soc_run_id`、`soc_alert_id`。
2. 外部引用：`external_system + external_case_id` 已建立过绑定。
3. 弱关联：`external_alert_ref`、source topic、rule/detection alias、asset/entity、时间窗口。
4. 无法唯一定位：写 unmatched disposition record，进入待复核，不更新本地状态。

## 7. 本地状态影响

外部处置事件通过 service 后可以产生这些结果：

| 结果 | 条件 | 说明 |
|---|---|---|
| `ExternalDispositionRecord` | 所有合法事件 | 保存原始状态、映射状态、reason、operator、target refs、trust level |
| `DecisionAuditLog` | 所有合法事件 | 记录来源、幂等键、映射结果、是否应用 |
| `CorrectionRecord` | 高置信映射且目标唯一 | 把外部人工结论作为 external correction，同步 operational decision |
| `ReviewQueueItem` close/update | 已映射为 closed 类状态 | 关闭或标记本地待复核项，但保留原始 run |
| `SocMemoryCandidate` | reason 有复用价值 | 只进入 pending review，不进入 confirmed memory |
| `SocDispositionOutcomeRecord` | high-trust mapped + verified target + 唯一 matching proposal + closed queue | 通过 evaluation service 写显式 external-source label；不从 reason 猜 status，不应用 proposal |
| `SkillImprovementCandidate` | 多次相似 reason 指向 skill 缺陷 | 生成优化候选，不自动改 skill |

## 8. 学习闭环

人工 reason 是高价值反馈，但也最容易污染知识库。处理规则：

- 单条 reason 默认只是 case feedback，不是长期知识。
- 多条相似 reason 命中同一 detection/topic/scenario/facet 后，才建议聚合成 memory candidate。
- 只有人工确认、版本化、可回滚后，candidate 才能成为 confirmed memory。
- skill 优化只能生成候选任务：说明受影响 skill、证据样本、失败模式、建议修改点、评测样本。
- 任何自动聚类或 LLM 总结都必须保留 source event refs，支持回放和撤销。

## 9. 实现切片

| 顺序 | 状态 | 切片 | 验收 |
|---|---|---|---|
| 1 | Done | `SocExternalDispositionEvent` contract + mapper tests | Zeus/通用样例都能转成 canonical event |
| 2 | Done | `SocExternalDispositionService` + repository protocol | 幂等、状态映射、unmatched、audit 都有测试 |
| 3 | Done | Zeus adapter mock fixture | 用 fixture 模拟 Zeus 状态/理由更新，不接真实 endpoint |
| 4 | Done | Review/Correction integration | 高置信外部结论能同步本地 review/correction |
| 5 | Done | Memory candidate integration | reason 生成 pending candidate，不写 confirmed memory |
| 6 | Done | External disposition DB/API visibility | external disposition record 和 memory candidate id 能进入 ReviewQueue context |
| 7 | Deferred / `PI-03C` | Skill improvement candidate backlog | 重复 reason/correction 可聚合成可追溯、可回放、只读的待评审优化项；不得自动改 Skill |
| 8 | Done | Web/TUI visibility | ReviewQueue context 显示外部处置历史和理由 |
| 9 | Done | EV-02 structured outcome bridge | 符合 gate 的 external event 通过 evaluation service 幂等写 outcome；不覆盖 analyst primary，skip reason 可审计 |

### 9.1 PI-03C Skill improvement candidate / Skill 改进候选

这一项没有遗漏，但在真实反馈形成重复 cohort 前保持 Deferred。第一版实现边界：

- `SkillImprovementCandidate` 必须记录 tenant、目标 Skill/package version、scenario/failure facet、聚合
  policy version、source disposition/correction refs、代表样本、建议修改和 replay set refs。
- 聚合键只能使用版本化、可解释的 typed facet；LLM 可离线总结候选，但不能丢失 source refs，也不能
  自己决定多个 case 属于同一缺陷。
- 单条 reason 不创建 Skill 修改任务；达到策略阈值也只生成 pending backlog，由 Skill owner/分析师确认。
- confirm 只批准进入 Skill 修改与评测流程，不直接编辑 `skills/public/`，不激活新版本，也不写
  confirmed memory。
- 候选必须支持 reject、supersede、expire 和 replay；Skill 修改后用绑定样本和反例回放，防止只修一个
  租户表达而破坏通用能力。

退出门槛：幂等聚合、来源追溯、人工状态机、权限/审计、Skill version linkage 和 replay diff 均有
测试；在此之前，重复 reason 仍只作为 external disposition 与 memory candidate 输入保存。

## 10. 市场化扩展要求

- 所有外部系统接入必须通过 adapter + mapping config，不允许在 core service 中判断 `if zeus`。
- Canonical status 保持小而稳定；客户差异放在映射层。
- event schema 版本化，新增字段必须向后兼容。
- 支持多租户/多系统共存：`tenant_id`、`external_system`、`external_case_id` 共同定义外部身份域。
- 对外 API/Kafka topic 的 payload 不暴露内部 DB 结构。
- reason、operator、raw payload 可能包含敏感信息，默认做 hash/redaction/可配置保存。
