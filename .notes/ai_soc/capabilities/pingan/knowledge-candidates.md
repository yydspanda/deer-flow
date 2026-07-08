# PingAn Knowledge Candidates

> Updated: 2026-07-07
>
> 目的：把 `capabilities/pingan/capability-cards.md` 中已经拆清楚的平安 APT / EDR / HIDS 专属知识，进一步整理成可审阅、可过期、可拒绝、可迁移到 DB memory / policy / adapter / eval 的候选清单。本文档是 `PA-05` 的产物。

## 1. 结论

这里的 candidate 不是 confirmed memory。所有条目默认：

```text
tenant_scope = pingan
status = pending_review
can_affect_decision = false
```

只有经过分析师/负责人确认、补充 evidence refs、评测通过后，才允许进入 confirmed memory、tenant policy/config 或 runtime adapter mapping。

## 2. Candidate Schema

```yaml
candidate_id: PA-KC-...
title: short name
candidate_type: procedure | detection_lesson | benign_pattern | environment_fact | identity_pattern | response_policy_hint | negative_memory | adapter_mapping | eval_fixture
target_artifact: tenant_memory | tenant_policy_config | adapter_mapping | eval_fixture | read_only_action_contract | high_risk_action_policy
tenant_scope: pingan
source_doc: PA-APT-SRC | PA-EDR-SRC | PA-HIDS-SRC | PA-COMMON
source_section: section id / card id
status: pending_review
validity: stable | expires | source_versioned | needs_runtime_evidence
review_owner: soc_analyst | soc_lead | platform_admin | security_ops_owner
decision_impact: none | review_hint | retrieval_hint | policy_candidate
evidence_required:
  - run
  - review_item
  - investigation_evidence
  - eval_fixture
```

## 3. Candidate Register

| Candidate ID | Type | Target artifact | Source | Status | Review owner |
|---|---|---|---|---|---|
| `PA-KC-COM-001` | `response_policy_hint` | tenant policy/config | `PA-COM-001` | pending_review | soc_lead |
| `PA-KC-COM-002` | `adapter_mapping` | adapter mapping/eval | `PA-COM-002` | pending_review | platform_admin |
| `PA-KC-COM-003` | `response_policy_hint` | tenant policy/config | `PA-RESP-001` | pending_review | security_ops_owner |
| `PA-KC-APT-001` | `negative_memory` | tenant memory/eval | `PA-APT-001` | pending_review | soc_lead |
| `PA-KC-APT-002` | `detection_lesson` | tenant memory/eval | `PA-APT-002` | pending_review | soc_analyst |
| `PA-KC-APT-003` | `benign_pattern` | tenant memory/eval | `PA-APT-002` | pending_review | soc_analyst |
| `PA-KC-APT-004` | `response_policy_hint` | tenant policy/config | `PA-APT-003` | pending_review | soc_lead |
| `PA-KC-APT-005` | `response_policy_hint` | high-risk action policy | `PA-APT-005` | pending_review | security_ops_owner |
| `PA-KC-EDR-001` | `environment_fact` | tenant memory/config | `PA-EDR-001` | pending_review | platform_admin |
| `PA-KC-EDR-002` | `identity_pattern` | tenant memory/eval | `PA-EDR-004` | pending_review | soc_lead |
| `PA-KC-EDR-003` | `detection_lesson` | tenant memory/eval | `PA-EDR-002` | pending_review | soc_analyst |
| `PA-KC-EDR-004` | `detection_lesson` | tenant memory/eval | `PA-EDR-003` | pending_review | soc_analyst |
| `PA-KC-EDR-005` | `negative_memory` | tenant memory/eval | `PA-EDR-001` | pending_review | soc_lead |
| `PA-KC-EDR-006` | `response_policy_hint` | high-risk action policy | `PA-EDR-005` | pending_review | security_ops_owner |
| `PA-KC-HIDS-001` | `environment_fact` | tenant memory/config | `PA-HIDS-001` | pending_review | platform_admin |
| `PA-KC-HIDS-002` | `benign_pattern` | tenant memory/eval | `PA-HIDS-003` | pending_review | soc_analyst |
| `PA-KC-HIDS-003` | `detection_lesson` | tenant memory/eval | `PA-HIDS-002` | pending_review | soc_analyst |
| `PA-KC-HIDS-004` | `negative_memory` | tenant memory/eval | `PA-HIDS-003` | pending_review | soc_lead |
| `PA-KC-HIDS-005` | `response_policy_hint` | high-risk action policy | `PA-HIDS-004` | pending_review | security_ops_owner |
| `PA-KC-EVAL-001` | `eval_fixture` | eval fixture | APT/EDR/HIDS cards | pending_review | platform_admin |

## 4. Candidate Details

### PA-KC-COM-001 — 历史处置状态阈值

- `candidate_type`: `response_policy_hint`
- `target_artifact`: tenant policy/config
- `source_doc`: `PA-COMMON`
- `source_section`: `PA-COM-001`
- `validity`: `source_versioned`
- `decision_impact`: `policy_candidate`

内容：旧流程中按历史关联预警状态、忽略次数、忽略理由分类来给出“可忽略/应转交”的提示。该能力可作为 correlation/policy hint，但不能直接决定 verdict。

验收：

- 阈值必须配置化，不写死在 public skill。
- 外部系统状态名必须先映射到 vendor-neutral status。
- 历史理由文本只能生成 candidate，不能自动 confirmed。

### PA-KC-COM-002 — 资产角色与归属字段映射

- `candidate_type`: `adapter_mapping`
- `target_artifact`: adapter mapping/eval
- `source_doc`: `PA-COMMON`
- `source_section`: `PA-COM-002`
- `validity`: `source_versioned`
- `decision_impact`: `review_hint`

内容：平安旧平台里资产、BU、PA code、owner、host/ip/domain/web 等字段需要映射到 canonical entity 和 `asset.locate` evidence。

验收：

- 字段名只进入 PingAn adapter 或 mapping tests。
- canonical SOC core 不直接读平安字段名。
- 查不到归属时必须返回 unknown/ambiguous，不编造。

### PA-KC-COM-003 — FollowUp / BU / PA code 处置归属

- `candidate_type`: `response_policy_hint`
- `target_artifact`: tenant policy/config
- `source_doc`: `PA-COMMON`
- `source_section`: `PA-RESP-001`
- `validity`: `source_versioned`
- `decision_impact`: `policy_candidate`

内容：转 BU、pa_code、bu_name、兜底分单等属于平安处置归属策略，不属于通用 skill。

验收：

- 只能作为 external disposition / policy candidate。
- 不能让 Lead Agent 直接生成外部工单写操作。
- 生产回写必须走 analyst-write / approval boundary。

### PA-KC-APT-001 — 加工方向字段不可信

- `candidate_type`: `negative_memory`
- `target_artifact`: tenant memory/eval
- `source_doc`: `PA-APT-SRC`
- `source_section`: `PA-APT-001`
- `validity`: `stable`
- `decision_impact`: `retrieval_hint`

内容：APT/天眼方向字段可能与 raw message、五元组、实际攻击方向冲突。遇到方向冲突时，应优先 raw message、五元组、HTTP/request-response、资产角色重建。

验收：

- 必须有 conflict report / eval fixture 支撑。
- 不能把该结论写成所有供应商通用事实。
- 只能降低加工字段权重，不能自动反转 verdict。

### PA-KC-APT-002 — APT 攻击成功证据分层

- `candidate_type`: `detection_lesson`
- `target_artifact`: tenant memory/eval
- `source_doc`: `PA-APT-SRC`
- `source_section`: `PA-APT-002`
- `validity`: `source_versioned`
- `decision_impact`: `retrieval_hint`

内容：APT 场景应区分攻击尝试、攻击命中、攻击成功和影响已发生。HTTP payload 只有和 response status/body、系统信息回显、敏感内容返回等证据结合，才更接近成功证据。

验收：

- 通用方法可反馈给 public skill；平安例外仍保留 tenant scope。
- fixture 必须覆盖 success / attempt / failed 三类。

### PA-KC-APT-003 — 平安业务路径/内部系统误报候选

- `candidate_type`: `benign_pattern`
- `target_artifact`: tenant memory/eval
- `source_doc`: `PA-APT-SRC`
- `source_section`: `PA-APT-002`
- `validity`: `expires`
- `decision_impact`: `review_hint`

内容：某些内部业务路径、内部系统 host、健康检查或内部工具触发 APT/Web 攻击特征时，可能是误报或授权行为。

验收：

- 不保存真实 host/path 明文到 public skill。
- 必须有有效期和来源。
- 命中后只作为 review hint，不自动关闭。

### PA-KC-APT-004 — IP 情报评分策略候选

- `candidate_type`: `response_policy_hint`
- `target_artifact`: tenant policy/config
- `source_doc`: `PA-APT-SRC`
- `source_section`: `PA-APT-003`
- `validity`: `source_versioned`
- `decision_impact`: `policy_candidate`

内容：IP 情报标签、地理、时效、白名单/CDN/移动网络等因素可形成风险解释和处置建议，但不能单独触发封堵。

验收：

- 情报结果必须来自 `threat_intel.ip_reputation.lookup` evidence。
- stale 情报必须降权。
- 策略阈值必须 tenant config 化。

### PA-KC-APT-005 — IP 封堵前置条件

- `candidate_type`: `response_policy_hint`
- `target_artifact`: high-risk action policy
- `source_doc`: `PA-APT-SRC`
- `source_section`: `PA-APT-005`
- `validity`: `source_versioned`
- `decision_impact`: `policy_candidate`

内容：IP 封堵只能作为 high-risk action proposal，需要方向、攻击方 IP、威胁情报、security tag 或明确 skip reason、审批人和 idempotency key。

验收：

- 不写真实策略 ID。
- 不自动执行。
- 未定位 attacker 或 security tag 冲突时不得生成 executable proposal。

### PA-KC-EDR-001 — 平安安全路径/工具环境事实

- `candidate_type`: `environment_fact`
- `target_artifact`: tenant memory/config
- `source_doc`: `PA-EDR-SRC`
- `source_section`: `PA-EDR-001`
- `validity`: `expires`
- `decision_impact`: `review_hint`

内容：平安安全软件路径、内部安全工具、工作时间习惯等只能作为平安环境事实，用于解释 EDR 风险，不是全局安全常识。

验收：

- 不进入 public skill。
- 必须带有效期。
- path safe 只能降低风险，不能单独忽略。

### PA-KC-EDR-002 — 平安 UM/账号格式

- `candidate_type`: `identity_pattern`
- `target_artifact`: tenant memory/eval
- `source_doc`: `PA-EDR-SRC`
- `source_section`: `PA-EDR-004`
- `validity`: `source_versioned`
- `decision_impact`: `retrieval_hint`

内容：平安普通域用户、外包账号、UM-like account 的格式可以辅助账号抽取和身份解释，但不能作为跨客户通用规则。

验收：

- 只用于 entity extraction candidate / identity explanation。
- 不因账号格式正常就判定行为正常。
- 账号封禁仍走 high-risk approval。

### PA-KC-EDR-003 — LoginData/System 分支经验

- `candidate_type`: `detection_lesson`
- `target_artifact`: tenant memory/eval
- `source_doc`: `PA-EDR-SRC`
- `source_section`: `PA-EDR-002`
- `validity`: `source_versioned`
- `decision_impact`: `retrieval_hint`

内容：LoginData/System 文件读取类场景需要结合路径、进程、命令、用户和上下文判断，不应只依赖 rule_code。

验收：

- rule_code 只作为 vendor alias。
- 没有 rule_code 时仍能通过 canonical detection/entity 工作。
- 必须保留原始字段 evidence。

### PA-KC-EDR-004 — 提权行为授权与风险区分

- `candidate_type`: `detection_lesson`
- `target_artifact`: tenant memory/eval
- `source_doc`: `PA-EDR-SRC`
- `source_section`: `PA-EDR-003`
- `validity`: `source_versioned`
- `decision_impact`: `retrieval_hint`

内容：提权类行为要区分授权运维、管理员操作、异常账号提权和可疑命令上下文。平安管理员组/账号例外只能作为 tenant candidate。

验收：

- public skill 只保留提权研判方法。
- 平安管理员组名/账号不进 public skill。
- fixture 覆盖 authorized candidate 与 risky escalation。

### PA-KC-EDR-005 — 路径安全不是关闭依据

- `candidate_type`: `negative_memory`
- `target_artifact`: tenant memory/eval
- `source_doc`: `PA-EDR-SRC`
- `source_section`: `PA-EDR-001`
- `validity`: `stable`
- `decision_impact`: `retrieval_hint`

内容：路径看似安全只能降低风险，不能替代命令行、父进程、用户权限和行为目的分析。

验收：

- 作为 negative memory 可用于抑制“只看路径安全”的错误建议。
- 必须有 eval fixture 覆盖 safe path + risky cmd。

### PA-KC-EDR-006 — UM 封禁/终端隔离前置条件

- `candidate_type`: `response_policy_hint`
- `target_artifact`: high-risk action policy
- `source_doc`: `PA-EDR-SRC`
- `source_section`: `PA-EDR-005`
- `validity`: `source_versioned`
- `decision_impact`: `policy_candidate`

内容：UM 封禁、主机/IP 隔离只能作为 approval-gated proposal，需要 account extraction、endpoint finding、asset locate、security tag 或 explicit skip reason。

验收：

- 不写平安账号系统细节到 public skill。
- `disposal_target` 不清时不得 fallback 执行。
- proposal 必须带 idempotency key。

### PA-KC-HIDS-001 — 平安 HIDS 环境事实

- `candidate_type`: `environment_fact`
- `target_artifact`: tenant memory/config
- `source_doc`: `PA-HIDS-SRC`
- `source_section`: `PA-HIDS-001`
- `validity`: `expires`
- `decision_impact`: `review_hint`

内容：平安机房、内部域名、内部网段、主机环境、内部安全工具等只用于 PingAn tenant context。

验收：

- 不进入 public skill。
- 必须有有效期和来源。
- 不能单独导致忽略或关闭。

### PA-KC-HIDS-002 — HIDS 授权运维/安全测试误报候选

- `candidate_type`: `benign_pattern`
- `target_artifact`: tenant memory/eval
- `source_doc`: `PA-HIDS-SRC`
- `source_section`: `PA-HIDS-003`
- `validity`: `expires`
- `decision_impact`: `review_hint`

内容：某些运维脚本、安全组测试、健康检查、固定路径/工具可能触发 HIDS 告警，但只能作为误报候选。

验收：

- 命中后不自动关闭。
- 需要 security tag 或人工确认才能升级状态。
- 必须避免写入真实账号/路径明文到 public skill。

### PA-KC-HIDS-003 — HIDS event_type 研判经验

- `candidate_type`: `detection_lesson`
- `target_artifact`: tenant memory/eval
- `source_doc`: `PA-HIDS-SRC`
- `source_section`: `PA-HIDS-002`
- `validity`: `source_versioned`
- `decision_impact`: `retrieval_hint`

内容：不同 event_type 需要不同关注点，但 event_type 只是 vendor alias / scenario facet；通用方法应看进程链、命令、用户、来源 IP、主机角色和上下文。

验收：

- 不把 event_type 到 verdict 的映射写死。
- finding 必须同时列 malicious indicators 和 benign indicators。
- fixture 覆盖反弹 shell、web command、内网暴破等。

### PA-KC-HIDS-004 — 不要全局化平安误报规则

- `candidate_type`: `negative_memory`
- `target_artifact`: tenant memory/eval
- `source_doc`: `PA-HIDS-SRC`
- `source_section`: `PA-HIDS-003`
- `validity`: `stable`
- `decision_impact`: `retrieval_hint`

内容：类似“内网暴破常见为运维”这类结论只能在 PingAn tenant 内作为候选，不得全局化；其他客户可能是高风险。

验收：

- 不进入 public skill。
- 必须 tenant scoped。
- 命中后仍需 evidence/review。

### PA-KC-HIDS-005 — 服务器隔离前置条件

- `candidate_type`: `response_policy_hint`
- `target_artifact`: high-risk action policy
- `source_doc`: `PA-HIDS-SRC`
- `source_section`: `PA-HIDS-004`
- `validity`: `source_versioned`
- `decision_impact`: `policy_candidate`

内容：服务器隔离只能作为 high-risk proposal，需要 host finding、disposal target、asset locate、host event context、security tag 或 explicit skip reason。

验收：

- 不写真实 templateId / operateType 到 public skill。
- HOST 未定位、资产归属不明、security tag 冲突时不得生成 executable proposal。
- 真实执行前必须有审批、dry-run、idempotency 和回滚/补偿策略。

### PA-KC-EVAL-001 — 第一批 PingAn 脱敏 eval fixture

- `candidate_type`: `eval_fixture`
- `target_artifact`: eval fixture
- `source_doc`: `PA-COMMON`
- `source_section`: APT/EDR/HIDS expanded cards
- `validity`: `source_versioned`
- `decision_impact`: `none`

内容：需要建立 APT/EDR/HIDS 每类至少一条脱敏 fixture，覆盖方向冲突、路径安全但命令风险、授权标签命中、host context 为空、高风险 action 不可执行等情况。

验收：

- fixture 不包含生产 secret、真实白名单、真实账号或敏感业务字段。
- fixture 能验证 public skill 不被 PingAn 知识污染。
- fixture 能验证 action 只生成 evidence/proposal，不直接执行。

## 5. Promotion Rules

候选升级规则：

| From | To | 条件 |
|---|---|---|
| `pending_review` | `confirmed_candidate` | 至少一个分析师确认 + 有 source doc + 有脱敏 eval 或 evidence ref |
| `confirmed_candidate` | `confirmed` | 多个真实 review/run 命中且无反例，或负责人批准 |
| `pending_review` | `rejected` | 证据不足、容易误导、只适合一次性 case |
| `confirmed` | `deprecated` | 环境变化、策略过期、反例增多 |

确认后仍不能绕过边界：

- `environment_fact` / `benign_pattern` 只能影响 retrieval/review hint。
- `response_policy_hint` 只能影响 proposal 推荐，不执行 action。
- `adapter_mapping` 只进入 PingAn adapter/mapping tests，不进入 core。
- `eval_fixture` 只用于测试，不进入 runtime prompt。

## 6. PA-05 Done Definition

- 每条 PingAn 专属经验都有 candidate ID、type、target artifact、tenant scope、source、status、validity 和 review owner。
- 所有 candidate 默认 `pending_review`。
- 没有 candidate 直接写入 confirmed memory。
- 没有真实内部白名单、账号、secret、系统地址或生产策略 ID 写入本文档。
- 下一步可以安全进入 `PA-06` public skill 最小修订，或 `PA-07` mock read-only adapters。
