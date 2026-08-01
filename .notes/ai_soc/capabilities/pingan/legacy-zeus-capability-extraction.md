# Legacy Zeus Triage Capability Extraction / 旧 Zeus 研判能力提炼

> Status: active reference. Execution order remains
> `.notes/ai_soc/delivery-roadmap.md`.

## 1. Purpose / 目的

`validation/original_works/zeus/flows/` 和它直接引用的 `prompt/*.py` 是旧系统的研判经验来源，
不是待迁移框架。当前工作只回答一个问题：

> 旧 APT、NIDS、HIDS、EDR 流程里，哪些告警研判方法值得进入新的
> deterministic SOC Runtime + public Skills + bounded LLM Analyzer？

不复制 LlamaIndex Workflow，不恢复一 topic 一 Agent，也不把旧 Prompt 整段塞进模型。

## 2. Current Scope / 当前范围

### In scope / 现在做

- APT/NDR、NIDS、HIDS、EDR 的场景识别方法；
- 请求、响应、网络方向、进程、文件、用户、认证等证据检查；
- 区分 detection hit、attempt、observed effect、confirmed impact；
- 识别竞争性良性解释；
- 输出当前结论、证据缺口和人工核查清单；
- 上游场景提示与模型推断场景的分离；
- 平安字段/topic 只通过 PingAn Adapter 进入通用 Runtime。

### Explicit non-goals / 当前明确不做

- 旧攻击链和时间线展示；
- 后续动作闭环、自动封堵、隔离、账号禁用、分单或关单；
- CMDB、EDR、TI、黑白标签等真实服务接通；
- EML/二维码深度解析和邮件 Agent；
- NL2SQL、Chat BI；
- 把旧 `related_history` 直接塞回首轮 Runtime Analyzer。

这些能力未来若需要，继续复用现有 correlation、memory、MCP/action、approval 和 audit 边界，
不从旧 Flow 复制实现。

## 3. Extraction Rules / 提炼规则

| Legacy material / 旧材料 | New owner / 新归属 | Rule / 规则 |
|---|---|---|
| 通用证据检查与场景判断方法 | `skills/public/soc-*` references + bounded runtime guidance | 去掉客户字段、固定 rule code、硬编码白名单和绝对阈值 |
| 平安 topic、字段、message 解析 | `backend/soc_agent/normalizers/pingan_*` | 供应商别名止于 Adapter |
| 首轮 LLM 研判 | `AnalysisResult.v2` + Prompt + Parser | 只消费 `LLMAnalysisRequest.v2` |
| 模型证据引用 | `EvidenceItem` + D8 Grounding | 引用 exact bounded path/value；描述不得夹带 sibling facts |
| 最终运营决策 | `SocDecisionPolicy` | 模型不拥有控制流、最终状态或执行权限 |
| 历史结论与环境经验 | correlation/governed memory/context | 只在明确契约中进入，不伪装成本次原始证据 |

旧规则中以下模式不能迁移为通用真值：

- HTTP 非 200 就忽略，HTTP 200 就攻击成功；
- 字段缺失就忽略；
- 内网来源、安全路径、熟悉域名或工具名就默认良性；
- `sip=attacker`、`dip=victim`；
- 上游 `host_state`、`attack_result` 或模型分数等于事实；
- 固定主机、路径、UA、网段或组织名单直接改 verdict。

## 4. Direct Flow And Prompt Audit / 直接流程与 Prompt 审计

| Entry flow | Direct prompt source | Reusable triage capability | New implementation |
|---|---|---|---|
| `apt_alert_assess.py` | `prompt/apt_prompts.py` | Web 攻击、弱口令、命令/代码执行、文件读写、WebShell、信息泄露、代理/工具、挖矿/后门等场景；请求与响应分开；成功证据分级 | `soc-network-apt-triage`, `soc-web-application-triage`, `AnalysisResult.v2` |
| `nids_alert_assess.py` | `prompt/nids_prompts.py` | TCP/UDP、HTTP、XXE、路径/文件、认证、DNS/DNSLog、代理/C2、工具特征；wire direction 与安全角色分开 | Network/Web Skills + PingAn NDR Adapter |
| `hids_alert_assess.py` | `prompt/hids_prompts.py` | 反弹 shell、Web 命令、提权、持久化、暴力破解、病毒、蜜罐、WebShell；主机/进程证据和授权上下文 | Endpoint Skill + PingAn HIDS Adapter |
| `edr_alert_assess.py` | `prompt/edr_prompts.py` | 进程链、路径、命令、用户、时间、提权和程序合法性；安全路径只是上下文，不是良性证明 | Endpoint Skill + PingAn EDR Adapter |
| `other_topic_assess.py` | generic fallback | 未识别来源/场景时仍给当前判断、缺口和复核项 | generic normalizer + ReviewQueue |
| `wb_alert_assess.py` | source-specific TI logic | TI 结论作为带来源和时效的证据，不是 verdict | PingAn TI Adapter + InvestigationEvidence |

旧 Flow 只用于定位直接引用的研判规则。未直接服务当前告警研判的 controller、schema、
description generation、disposition tools 和辅助业务 Flow 不再逐文件宣称“已迁移”。

## 5. Implemented D7 Contract / 已实现的 D7 契约

`AnalysisResult.v2` 新增：

| Field | Meaning / 含义 |
|---|---|
| `scenario_assessments` | 开放词表场景列表，不受固定 taxonomy 限制 |
| `scenario_name` / `scenario_key` | 人类可读场景与可选稳定通用键 |
| `origin` | `upstream_hint`、`inferred` 或 `hybrid` |
| `is_primary` | 非空场景列表必须且只能有一个主场景 |
| `activity_stage` | `detection_hit`、`attempt_observed`、`effect_observed`、`impact_confirmed`、`indeterminate` |
| `evidence_indices` | 回指同一 `AnalysisResult.evidence` 的零基索引 |
| `competing_explanations` | 竞争性良性或替代解释 |
| `evidence_gaps` | 会影响结论的缺失证据 |
| `manual_checks` | 运营人员可执行的核查项 |

Parser 只接受 `soc.analysis_result.v2`，拒绝缺失字段、未知顶层/场景字段、字符串 confidence、
非法 evidence index、重复场景和多主场景。旧持久化对象仍可用默认空字段读取，但新 LLM 输出
必须显式满足 D7 契约。

## 6. Real D7 Result / 真实 D7 结果

重放命令：

```bash
./scripts/soc-runtime-validation.sh checkpoint-d
./scripts/soc-runtime-validation.sh checkpoint-d-live
./scripts/soc-runtime-validation.sh checkpoint-d-grounding
```

本地 gitignored 产物：

```text
backend/.deer-flow/soc-runtime-validation/checkpoint-d/
├── step-d7-analyzer-output/1965449.analyzer-output.json
└── step-d8-evidence-grounding/1965449.grounding.json
```

2026-08-01 authoritative 运行使用 `deepseek-v4-pro`、`soc-analysis-v8` 和
`soc-analysis-json-parser-v5` 得到：

- structure status: `passed`;
- verdict: `suspicious`;
- primary scenario: `弱口令成功登录`;
- stage: `effect_observed`;
- evidence: 10;
- evidence gaps: 4;
- manual checks: 4;
- JSON repair: false.

这只证明 Prompt、真实模型、Parser 和 typed contract 能协作，不证明证据已经落地或最终判定
正确。D7 明确没有运行 grounding、Decision、correlation/memory、MCP/tool、持久化和处置。

## 7. D8 Grounding Result / D8 证据落地结果

D8 已对 D7 的每条 evidence 做 deterministic Grounding：

1. source/value 是否能回指 bounded context；
2. composite value 是否由全部组成部分支持；
3. evidence description 是否夹带该 source/value 之外的 sibling facts；
4. `effect_observed` / `impact_confirmed` 是否有对应结果证据；
5. 未落地证据是否强制进入 degraded evidence、human review 和
   `automation_allowed=false`。

最终结果：

- execution status: `passed`;
- quality status: `blocked`;
- 10 条 evidence 中 8 grounded、2 `description_context_leakage`；
- trusted, high-trust bounded provider outcome assertion 消除了旧的 false
  `unproven_outcome_claim`；精确可见 encoded marker 只 ground 值存在/编码形态/边界省略；
- 两条拒绝分别把 destination IP 混入 source-IP evidence，以及把弱口令分类混入 request-body
  evidence；
- primary scenario 仍引用被拒绝 evidence，不能描述为“研判正确”或“可自动处置”。

下一边界是单样本 Decision Policy 审阅，验证 D8 报告实际产生 degraded evidence、human review
和 `automation_allowed=false`，而不是继续扩充旧 Flow 或 Prompt。

## 8. Source Hygiene / 源文件卫生

`validation/original_works/zeus/` 保持只读参考。`*:Zone.Identifier`、`__pycache__`、`*.pyc`
和 `.DS_Store` 由 `.gitignore` 排除。提炼后的能力必须重新实现、测试并进入本台账；旧代码
不成为生产依赖。
